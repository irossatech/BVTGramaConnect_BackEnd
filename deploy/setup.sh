#!/usr/bin/env bash
# BVT Grama Connect: server setup for Ubuntu 22.04 or 24.04 LTS.
#
#   sudo bash deploy/setup.sh /path/to/config.env
#
# Safe to run again: every step checks what is already there. Later runs can
# omit the path; the copy in /etc/gramaconnect/deploy.env is used.
#
# Steps: packages, deploy user, SSH hardening, firewall, fail2ban, automatic
# security updates, database (MySQL or PostgreSQL), Django environment file,
# code checkout, systemd services, first deploy, Nginx + Let's Encrypt,
# nightly encrypted backups.
# Variables such as DEPLOY_DIR and HSTS_HEADER are read by render() through
# ${!name}, which shellcheck cannot see.
# shellcheck disable=SC2034
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATES="$HERE/templates"
# shellcheck source=lib.sh
source "$HERE/lib.sh"
require_root

# --- Configuration --------------------------------------------------------------

CONFIG_SRC="${1:-$GC_DEPLOY_ENV}"
install -d -m 750 -o root -g root "$GC_ETC"
if [[ "$(readlink -f "$CONFIG_SRC")" != "$GC_DEPLOY_ENV" ]]; then
  [[ -r "$CONFIG_SRC" ]] || die "Config not found: $CONFIG_SRC"
  install -m 640 -o root -g root "$CONFIG_SRC" "$GC_DEPLOY_ENV"
fi
load_config "$GC_DEPLOY_ENV"
export DEBIAN_FRONTEND=noninteractive

# shellcheck disable=SC1091
source /etc/os-release
[[ "${ID:-}" == "ubuntu" ]] || die "This setup targets Ubuntu LTS; found ${PRETTY_NAME:-unknown}."
case "${VERSION_ID:-}" in
  22.04|24.04) ;;
  *) warn "Tested on Ubuntu 22.04 and 24.04; this is $VERSION_ID." ;;
esac

# --- Packages ---------------------------------------------------------------------

log "Installing packages"
apt-get update -y
apt-get install -y --no-install-recommends \
  ca-certificates curl git gnupg rsync rclone sudo jq openssl \
  ufw fail2ban unattended-upgrades \
  nginx certbot ffmpeg \
  python3 python3-venv python3-dev python3-pip build-essential pkg-config \
  default-libmysqlclient-dev
# ffmpeg gives ffprobe (video length) and the poster frame for video posts.
# default-libmysqlclient-dev is needed on every server: requirements.txt always
# builds mysqlclient, whichever database this server uses.
if [[ "$DB_ENGINE" == "mysql" ]]; then
  apt-get install -y --no-install-recommends mysql-server
else
  apt-get install -y --no-install-recommends postgresql postgresql-contrib
fi

ensure_node() {
  local have
  have="$(node -v 2>/dev/null | sed -E 's/^v([0-9]+).*/\1/' || true)"
  if [[ -n "$have" && "$have" -ge 18 ]]; then
    return
  fi
  log "Installing Node.js 20 from NodeSource (needed to build the web parts)"
  install -d -m 755 /etc/apt/keyrings
  curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
    | gpg --dearmor --yes -o /etc/apt/keyrings/nodesource.gpg
  echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" \
    > /etc/apt/sources.list.d/nodesource.list
  apt-get update -y
  apt-get install -y nodejs
}
ensure_node

# --- Clock and memory ---------------------------------------------------------------

timedatectl set-timezone Asia/Kolkata || warn "Could not set the time zone."
if ! swapon --show | grep -q . && [[ "$(awk '/MemTotal/ {print $2}' /proc/meminfo)" -lt 4000000 ]]; then
  if [[ ! -f /swapfile ]]; then
    log "Adding a 2 GB swap file (less than 4 GB of RAM)"
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
  fi
  swapon /swapfile || true
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# --- Deploy user --------------------------------------------------------------------

log "Deploy user: $APP_USER"
if ! id "$APP_USER" &>/dev/null; then
  adduser --disabled-password --gecos "" "$APP_USER"
fi
usermod -aG sudo,www-data "$APP_USER"
USER_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
install -d -m 700 -o "$APP_USER" -g "$APP_USER" "$USER_HOME/.ssh"
KEYS="$USER_HOME/.ssh/authorized_keys"
if [[ ! -s "$KEYS" && -s /root/.ssh/authorized_keys ]]; then
  log "Copying root's SSH keys to $APP_USER"
  cp /root/.ssh/authorized_keys "$KEYS"
fi
[[ -e "$KEYS" ]] && chown "$APP_USER:$APP_USER" "$KEYS" && chmod 600 "$KEYS"

SUDOERS=/etc/sudoers.d/gramaconnect
{
  echo "# Managed by BVT Grama Connect deploy/setup.sh"
  if [[ "$SUDO_NOPASSWD" == "1" ]]; then
    echo "$APP_USER ALL=(ALL) NOPASSWD:ALL"
  else
    echo "$APP_USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart gramaconnect-gunicorn.service, /usr/bin/systemctl restart gramaconnect-qcluster.service, /usr/bin/systemctl reload nginx.service"
  fi
} > "$SUDOERS.tmp"
visudo -cf "$SUDOERS.tmp" >/dev/null || die "Generated sudoers file is invalid."
chmod 440 "$SUDOERS.tmp"
mv "$SUDOERS.tmp" "$SUDOERS"

# --- SSH hardening --------------------------------------------------------------------

if [[ "$HARDEN_SSH" == "1" ]]; then
  can_sudo=0
  if [[ "$SUDO_NOPASSWD" == "1" ]]; then
    can_sudo=1
  elif passwd -S "$APP_USER" | awk '{exit !($2 == "P")}'; then
    can_sudo=1
  fi
  if [[ ! -s "$KEYS" ]]; then
    warn "SSH hardening skipped: $KEYS is empty. Add a public key for $APP_USER and run setup again, or you would be locked out."
  elif [[ "$can_sudo" != "1" ]]; then
    warn "SSH hardening skipped: $APP_USER has no password and SUDO_NOPASSWD=0, so nobody could become root after root login is disabled."
  else
    log "SSH: keys only, no root login"
    # Named 05-… so it wins over cloud-init's 50-cloud-init.conf (first value wins).
    cat > /etc/ssh/sshd_config.d/05-gramaconnect.conf <<EOF
# Managed by BVT Grama Connect deploy/setup.sh
Port $SSH_PORT
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
MaxAuthTries 4
EOF
    sshd -t || die "sshd rejected the new configuration; left SSH unchanged."
    systemctl reload ssh 2>/dev/null || systemctl reload sshd
  fi
fi

# --- Firewall, fail2ban, automatic updates ------------------------------------------------

log "Firewall: SSH, HTTP, HTTPS only"
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow "$SSH_PORT/tcp" >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

log "fail2ban: sshd and nginx rate limits"
render "$TEMPLATES/fail2ban.local" /etc/fail2ban/jail.d/gramaconnect.local
systemctl enable --now fail2ban >/dev/null
systemctl restart fail2ban

log "Automatic security updates"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF
cat > /etc/apt/apt.conf.d/52gramaconnect-unattended <<'EOF'
// Security updates install on their own. Reboots stay manual: see deploy/RUNBOOK.md.
Unattended-Upgrade::Automatic-Reboot "false";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
EOF
systemctl enable --now unattended-upgrades >/dev/null

# --- Folders ------------------------------------------------------------------------------

log "Folders under $APP_ROOT and $GC_MEDIA"
install -d -m 2750 -o "$APP_USER" -g www-data "$APP_ROOT" "$STATIC_ROOT" "$WWW_DIR" "$WWW_DIR/public" "$WWW_DIR/console"
install -d -m 2750 -o "$APP_USER" -g www-data "$GC_MEDIA"
install -d -m 700 -o root -g root "$GC_BACKUP_DIR"
install -d -m 755 /var/www/letsencrypt
chown root:"$APP_USER" "$GC_ETC"
chmod 750 "$GC_ETC"
chown root:"$APP_USER" "$GC_DEPLOY_ENV"
chmod 640 "$GC_DEPLOY_ENV"

# --- Database ------------------------------------------------------------------------------

DB_PASS_FILE="$GC_ETC/db.password"
if [[ ! -s "$DB_PASS_FILE" ]]; then
  (umask 077; openssl rand -hex 24 > "$DB_PASS_FILE")
fi
chmod 600 "$DB_PASS_FILE"
DB_PASS="$(<"$DB_PASS_FILE")"
DB_PASS_URL="$(urlencode "$DB_PASS")"

if [[ "$DB_ENGINE" == "mysql" ]]; then
  log "MySQL: utf8mb4 database $DB_NAME, user $DB_USER limited to it"
  cat > /tmp/60-gramaconnect.cnf <<'EOF'
# Managed by BVT Grama Connect deploy/setup.sh. utf8mb4 stores Telugu correctly.
[mysqld]
bind-address = 127.0.0.1
mysqlx-bind-address = 127.0.0.1
character-set-server = utf8mb4
collation-server = utf8mb4_unicode_ci

[client]
default-character-set = utf8mb4
EOF
  systemctl enable --now mysql >/dev/null
  if install_if_changed /tmp/60-gramaconnect.cnf /etc/mysql/mysql.conf.d/60-gramaconnect.cnf; then
    systemctl restart mysql
  fi
  mysql --protocol=socket -uroot <<SQL
CREATE DATABASE IF NOT EXISTS \`$DB_NAME\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASS';
CREATE USER IF NOT EXISTS '$DB_USER'@'127.0.0.1' IDENTIFIED BY '$DB_PASS';
ALTER USER '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASS';
ALTER USER '$DB_USER'@'127.0.0.1' IDENTIFIED BY '$DB_PASS';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, DROP, REFERENCES,
      CREATE TEMPORARY TABLES, LOCK TABLES, SHOW VIEW, TRIGGER
  ON \`$DB_NAME\`.* TO '$DB_USER'@'localhost', '$DB_USER'@'127.0.0.1';
FLUSH PRIVILEGES;
SQL
  DATABASE_URL="mysql://$DB_USER:$DB_PASS_URL@127.0.0.1:3306/$DB_NAME"
  # Credentials for mysqldump in backup.sh, readable by root only.
  (umask 077; cat > "$GC_ETC/mysql-backup.cnf" <<EOF
[client]
user=$DB_USER
password=$DB_PASS
host=localhost
default-character-set=utf8mb4
EOF
  )
else
  log "PostgreSQL: UTF8 database $DB_NAME owned by $DB_USER"
  systemctl enable --now postgresql >/dev/null
  sudo -u postgres psql -v ON_ERROR_STOP=1 -q --set=role="$DB_USER" --set=pass="$DB_PASS" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN', :'role')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role') \gexec
SELECT format('ALTER ROLE %I WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L', :'role', :'pass') \gexec
SQL
  if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'" | grep -q 1; then
    sudo -u postgres createdb -E UTF8 -T template0 --locale=C.UTF-8 -O "$DB_USER" "$DB_NAME"
  fi
  sudo -u postgres psql -v ON_ERROR_STOP=1 -q -d "$DB_NAME" <<SQL
REVOKE ALL ON DATABASE "$DB_NAME" FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE "$DB_NAME" TO "$DB_USER";
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO "$DB_USER";
SQL
  DATABASE_URL="postgres://$DB_USER:$DB_PASS_URL@127.0.0.1:5432/$DB_NAME"
fi

# --- Django environment file ----------------------------------------------------------------

log "Writing $GC_APP_ENV"
if [[ ! -e "$GC_APP_ENV" ]]; then
  cat > "$GC_APP_ENV" <<'EOF'
# BVT Grama Connect production environment. Written by deploy/setup.sh.
# setup.sh rewrites the managed keys on every run and never touches the rest.
# Generated secrets (SECRET_KEY) are kept once created.
EOF
fi
ALLOWED_HOSTS="${ALL_DOMAINS// /,}"
# shellcheck disable=SC2086  # split ALL_DOMAINS into one origin per name
CSRF_TRUSTED_ORIGINS="$(printf 'https://%s,' $ALL_DOMAINS)"
CSRF_TRUSTED_ORIGINS="${CSRF_TRUSTED_ORIGINS%,}"
set_env "$GC_APP_ENV" DJANGO_SETTINGS_MODULE grama.settings.prod
set_env "$GC_APP_ENV" SECRET_KEY "$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')" --if-missing
set_env "$GC_APP_ENV" ALLOWED_HOSTS "$ALLOWED_HOSTS"
set_env "$GC_APP_ENV" CSRF_TRUSTED_ORIGINS "$CSRF_TRUSTED_ORIGINS"
set_env "$GC_APP_ENV" DATABASE_URL "$DATABASE_URL"
set_env "$GC_APP_ENV" MEDIA_ROOT "$GC_MEDIA"
set_env "$GC_APP_ENV" STATIC_ROOT "$STATIC_ROOT"
set_env "$GC_APP_ENV" WEB_PUBLIC_DIR "$WWW_DIR/public"
set_env "$GC_APP_ENV" WEB_ADMIN_DIR "$WWW_DIR/console"
set_env "$GC_APP_ENV" TRUST_X_REAL_IP True
set_env "$GC_APP_ENV" SECURE_SSL_REDIRECT False
set_env "$GC_APP_ENV" LANG C.UTF-8
# Email for invites and password resets (SMTP). With an empty SMTP_PASSWORD
# in config.env, the password already in the env file is kept.
set_env "$GC_APP_ENV" EMAIL_BACKEND django.core.mail.backends.smtp.EmailBackend
set_env "$GC_APP_ENV" EMAIL_HOST "${SMTP_HOST:-localhost}"
set_env "$GC_APP_ENV" EMAIL_PORT "${SMTP_PORT:-587}"
set_env "$GC_APP_ENV" EMAIL_HOST_USER "${SMTP_USER:-}"
if [[ -n "${SMTP_PASSWORD:-}" ]]; then
  set_env "$GC_APP_ENV" EMAIL_HOST_PASSWORD "$SMTP_PASSWORD"
else
  set_env "$GC_APP_ENV" EMAIL_HOST_PASSWORD "" --if-missing
fi
set_env "$GC_APP_ENV" EMAIL_USE_TLS "${SMTP_USE_TLS:-True}"
set_env "$GC_APP_ENV" DEFAULT_FROM_EMAIL "${MAIL_FROM:-BVT Grama Connect <no-reply@$DOMAIN>}"
set_env "$GC_APP_ENV" CONSOLE_LOGIN_URL "https://$DOMAIN/console/"
# Link added to shared posts and invites (owner decision Q17).
set_env "$GC_APP_ENV" SITE_WEBSITE_URL "https://$DOMAIN/" --if-missing
# Placeholder the owner replaces (kept once edited):
#   FCM_CREDENTIALS_FILE: Firebase service-account JSON. Missing file = pushes off.
set_env "$GC_APP_ENV" FCM_CREDENTIALS_FILE "$GC_ETC/fcm-service-account.json" --if-missing
set_env "$GC_APP_ENV" FCM_PROJECT_ID "" --if-missing
set_env "$GC_APP_ENV" LOG_LEVEL INFO --if-missing
chown root:"$APP_USER" "$GC_APP_ENV"
chmod 640 "$GC_APP_ENV"

# --- Code ------------------------------------------------------------------------------------

clone_repo() {
  local url="$1" dir="$2"
  [[ -d "$dir/.git" ]] && return 0
  [[ -n "$url" ]] || die "No repository URL for $dir."
  log "Cloning $url"
  if ! sudo -u "$APP_USER" -H git clone --branch "$GIT_BRANCH" "$url" "$dir"; then
    die "Could not clone $url. For private repositories add deploy keys first (deploy/RUNBOOK.md, 'Private repositories')."
  fi
}
clone_repo "${REPO_BACKEND:-}" "$BACKEND_DIR"
clone_repo "${REPO_WEB:-}" "$WEB_REPO_DIR"

# --- systemd services --------------------------------------------------------------------------

log "systemd services"
CPUS="$(nproc)"
GUNICORN_WORKERS=$(( CPUS * 2 + 1 ))
(( GUNICORN_WORKERS > 9 )) && GUNICORN_WORKERS=9
DEPLOY_DIR="$BACKEND_DIR/deploy"
units_changed=0
for unit in gramaconnect-gunicorn.service gramaconnect-qcluster.service gramaconnect-backup.service gramaconnect-backup.timer; do
  render "$TEMPLATES/$unit" "/tmp/$unit"
  if install_if_changed "/tmp/$unit" "/etc/systemd/system/$unit"; then
    units_changed=1
  fi
done
[[ "$units_changed" == "1" ]] && systemctl daemon-reload
systemctl enable gramaconnect-gunicorn.service gramaconnect-qcluster.service >/dev/null

# --- First deploy: venv, packages, migrate, collectstatic, web parts, start ----------------------

log "Deploying the code"
bash "$HERE/deploy.sh"

# --- Nginx and Let's Encrypt ------------------------------------------------------------------------

log "Nginx"
rm -f /etc/nginx/sites-enabled/default
HSTS_HEADER=""
render "$TEMPLATES/nginx-proxy.conf" /etc/nginx/snippets/gramaconnect-proxy.conf
render "$TEMPLATES/nginx-locations.conf" /etc/nginx/snippets/gramaconnect-locations.conf
CERT_DIR="/etc/letsencrypt/live/$DOMAIN"

write_site() {
  local mode="$1"
  if [[ "$mode" == "https" ]]; then
    HSTS_HEADER='add_header Strict-Transport-Security "max-age=31536000" always;'
    render "$TEMPLATES/nginx-site-https.conf" /etc/nginx/sites-available/gramaconnect.conf
  else
    HSTS_HEADER=""
    render "$TEMPLATES/nginx-site-http.conf" /etc/nginx/sites-available/gramaconnect.conf
  fi
  render "$TEMPLATES/nginx-security.conf" /etc/nginx/snippets/gramaconnect-security.conf
  ln -sf /etc/nginx/sites-available/gramaconnect.conf /etc/nginx/sites-enabled/gramaconnect.conf
  nginx -t || die "Nginx rejected the configuration."
  systemctl enable --now nginx >/dev/null
  systemctl reload nginx
}

if [[ ! -s "$CERT_DIR/fullchain.pem" ]]; then
  write_site http
  log "Requesting a Let's Encrypt certificate for: $ALL_DOMAINS"
  certbot_args=(certonly --webroot -w /var/www/letsencrypt --non-interactive --agree-tos
                -m "$ADMIN_EMAIL" --keep-until-expiring --expand --cert-name "$DOMAIN")
  [[ "$CERTBOT_STAGING" == "1" ]] && certbot_args+=(--staging)
  for name in $ALL_DOMAINS; do certbot_args+=(-d "$name"); done
  if ! certbot "${certbot_args[@]}"; then
    warn "No certificate yet. Check that every name in DOMAIN and EXTRA_DOMAINS points at this server, then run setup again. The site is served over plain HTTP until then."
  fi
fi
if [[ -s "$CERT_DIR/fullchain.pem" ]]; then
  write_site https
fi
install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/gramaconnect-reload-nginx <<'EOF'
#!/bin/sh
systemctl reload nginx
EOF
chmod 755 /etc/letsencrypt/renewal-hooks/deploy/gramaconnect-reload-nginx
systemctl enable --now certbot.timer >/dev/null 2>&1 || true

# --- Backups ------------------------------------------------------------------------------------------

log "Nightly encrypted backups"
if [[ ! -s "$BACKUP_PASSPHRASE_FILE" ]]; then
  (umask 077; openssl rand -base64 48 > "$BACKUP_PASSPHRASE_FILE")
  warn "New backup passphrase in $BACKUP_PASSPHRASE_FILE. Copy it somewhere safe and offline now."
fi
chmod 600 "$BACKUP_PASSPHRASE_FILE"
systemctl enable --now gramaconnect-backup.timer >/dev/null
backup_target_configured || warn "BACKUP_TARGET is still the placeholder; backups stay on this server only until you set it."

# --- Summary -----------------------------------------------------------------------------------------------

log "Done."
cat <<EOF

  Site:          https://$DOMAIN/            (console: /console/, API: /api/v1/health/)
  Code:          $APP_ROOT/{backend,public,admin}
  Environment:   $GC_APP_ENV
  Database:      $DB_ENGINE, database $DB_NAME, user $DB_USER
  Services:      gramaconnect-gunicorn, gramaconnect-qcluster, gramaconnect-backup.timer
  Next steps:    deploy/RUNBOOK.md, "After the first setup"
EOF
