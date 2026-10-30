#!/usr/bin/env bash
# Shared helpers for the deploy scripts. Sourced by setup.sh, deploy.sh,
# backup.sh and restore.sh; not meant to be run on its own.

# The paths below are used by the scripts that source this file and by the
# templates (through render), which shellcheck cannot follow.
# shellcheck disable=SC2034

# Bash 5.2 treats "&" in ${var//pattern/replacement} as "the match". Templates
# must be copied literally.
shopt -u patsub_replacement 2>/dev/null || true


GC_ETC=/etc/gramaconnect
GC_DEPLOY_ENV="$GC_ETC/deploy.env"        # copy of config.env, read by every script
GC_APP_ENV="$GC_ETC/gramaconnect.env"     # Django's environment (secrets live here)
GC_BACKUP_DIR=/var/backups/gramaconnect
GC_SOCKET_DIR=/run/gramaconnect
GC_SOCKET="$GC_SOCKET_DIR/gunicorn.sock"
GC_MEDIA=/var/media

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWARN:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

require_root() {
  [[ ${EUID:-$(id -u)} -eq 0 ]] || die "Run this as root, for example: sudo bash $0"
}

# load_config [file]: read deploy settings and fill in defaults.
load_config() {
  local file="${1:-$GC_DEPLOY_ENV}"
  [[ -r "$file" ]] || die "Config not found or not readable: $file. Copy deploy/config.env.example, edit it, and pass its path to setup.sh."
  set -a
  # shellcheck disable=SC1090
  source "$file"
  set +a

  : "${DOMAIN:?DOMAIN is required in $file}"
  : "${ADMIN_EMAIL:?ADMIN_EMAIL is required in $file}"
  APP_USER="${APP_USER:-deploy}"
  APP_ROOT="${APP_ROOT:-/srv/gramaconnect}"
  DB_ENGINE="${DB_ENGINE:-mysql}"
  DB_NAME="${DB_NAME:-grama}"
  DB_USER="${DB_USER:-grama}"
  GIT_BRANCH="${GIT_BRANCH:-main}"
  EXTRA_DOMAINS="${EXTRA_DOMAINS:-}"
  SSH_PORT="${SSH_PORT:-22}"
  SUDO_NOPASSWD="${SUDO_NOPASSWD:-1}"
  HARDEN_SSH="${HARDEN_SSH:-1}"
  CERTBOT_STAGING="${CERTBOT_STAGING:-0}"
  BACKUP_TARGET="${BACKUP_TARGET:-[BACKUP TARGET]}"
  BACKUP_PASSPHRASE_FILE="${BACKUP_PASSPHRASE_FILE:-$GC_ETC/backup.passphrase}"
  BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
  WEB_API_BASE="${WEB_API_BASE:-/api/v1}"
  CSP_SCRIPT_EXTRA="${CSP_SCRIPT_EXTRA:-}"
  STRICT_CHECKS="${STRICT_CHECKS:-1}"
  OFFICE_IPS="${OFFICE_IPS:-}"
  django_admin_access

  case "$DB_ENGINE" in
    mysql|postgresql) ;;
    *) die "DB_ENGINE must be mysql or postgresql, not '$DB_ENGINE'." ;;
  esac
  [[ $DB_NAME =~ ^[a-z_][a-z0-9_]{0,62}$ ]] || die "DB_NAME may use only lowercase letters, digits and underscores."
  [[ $DB_USER =~ ^[a-z_][a-z0-9_]{0,31}$ ]] || die "DB_USER may use only lowercase letters, digits and underscores."
  [[ $APP_USER =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || die "APP_USER is not a valid user name."

  ALL_DOMAINS="$DOMAIN${EXTRA_DOMAINS:+ $EXTRA_DOMAINS}"
  SERVER_NAMES="$ALL_DOMAINS"
  BACKEND_DIR="$APP_ROOT/backend"
  WEB_REPO_DIR="$APP_ROOT/web"  # website and Super Admin console: one repo, one build
  WWW_DIR="$APP_ROOT/www"
  VENV="$APP_ROOT/venv"
  STATIC_ROOT="$APP_ROOT/static"
}

# set_env FILE KEY VALUE [--if-missing]: set KEY=VALUE, replacing an existing
# line. With --if-missing an existing value is kept (used for generated secrets).
set_env() {
  local file="$1" key="$2" value="$3" mode="${4:-}"
  [[ -e "$file" ]] || : > "$file"
  if grep -q "^${key}=" "$file"; then
    [[ "$mode" == "--if-missing" ]] && return 0
    local tmp
    tmp="$(mktemp)"
    K="$key" V="$value" awk 'index($0, ENVIRON["K"] "=") == 1 { print ENVIRON["K"] "=" ENVIRON["V"]; next } { print }' "$file" > "$tmp"
    cat "$tmp" > "$file"
    rm -f "$tmp"
  else
    printf '%s=%s\n' "$key" "$value" >> "$file"
  fi
}

get_env() {
  local file="$1" key="$2"
  grep -m1 "^${key}=" "$file" 2>/dev/null | cut -d= -f2- || true
}

# django_admin_access: turn OFFICE_IPS into the Nginx lines that guard /django-admin/
# (owner decision: office IPs only). Empty OFFICE_IPS leaves only "deny all".
# Sets DJANGO_ADMIN_ACCESS, which nginx-locations.conf reads through render.
django_admin_access() {
  local ip ipv4 ipv6
  ipv4='^([0-9]{1,3}\.){3}[0-9]{1,3}(/[0-9]{1,2})?$'
  ipv6='^[0-9A-Fa-f]*:[0-9A-Fa-f:.]*(/[0-9]{1,3})?$'
  DJANGO_ADMIN_ACCESS=""
  for ip in ${OFFICE_IPS//,/ }; do
    [[ $ip =~ $ipv4 || $ip =~ $ipv6 ]] || die "OFFICE_IPS: '$ip' is not an IP address or CIDR range."
    DJANGO_ADMIN_ACCESS+="    allow $ip;"$'\n'
  done
  DJANGO_ADMIN_ACCESS+="    deny all;"
}

# render TEMPLATE DEST: copy TEMPLATE to DEST, replacing every @NAME@ with the
# value of shell variable NAME. An unset variable is an error (set -u).
render() {
  local src="$1" dest="$2" content var
  content="$(<"$src")"
  for var in $(grep -o '@[A-Z_][A-Z0-9_]*@' "$src" | sort -u | tr -d '@'); do
    content="${content//@${var}@/${!var}}"
  done
  printf '%s\n' "$content" > "$dest.tmp"
  mv "$dest.tmp" "$dest"
}

# install_if_changed SRC DEST: replace DEST only when SRC differs.
# Returns 0 when DEST changed, 1 when it was already identical.
install_if_changed() {
  local src="$1" dest="$2"
  if [[ -f "$dest" ]] && cmp -s "$src" "$dest"; then
    rm -f "$src"
    return 1
  fi
  mv "$src" "$dest"
  return 0
}

urlencode() {
  python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$1"
}

backup_target_configured() {
  [[ -n "$BACKUP_TARGET" && "$BACKUP_TARGET" != "[BACKUP TARGET]" ]]
}
