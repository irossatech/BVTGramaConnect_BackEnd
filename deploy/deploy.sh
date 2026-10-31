#!/usr/bin/env bash
# BVT Grama Connect: deploy the latest code. Run on the server:
#
#   sudo bash /srv/gramaconnect/backend/deploy/deploy.sh      (or as the deploy user)
#
# Pulls the backend, public website and console repos, installs Python
# packages, migrates, collects static files, builds and publishes the web
# parts, makes sure the background schedules exist, restarts the services,
# then runs Django's deploy checks and a health check.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$HERE/lib.sh"
load_config "$GC_DEPLOY_ENV"

# Everything below runs as the deploy user; root re-runs this script as that user.
if [[ "$(id -un)" != "$APP_USER" ]]; then
  [[ ${EUID:-$(id -u)} -eq 0 ]] || die "Run as $APP_USER or root."
  exec sudo -u "$APP_USER" -H bash "$0" "$@"
fi

[[ -r "$GC_APP_ENV" ]] || die "$GC_APP_ENV is missing or unreadable; run setup.sh first."
set -a
# shellcheck disable=SC1090
source "$GC_APP_ENV"
set +a
export LANG=C.UTF-8 PYTHONUTF8=1

# --- Code ----------------------------------------------------------------------------

update_repo() {
  local url="$1" dir="$2"
  if [[ ! -d "$dir/.git" ]]; then
    [[ -n "$url" ]] || die "No repository URL for $dir."
    log "Cloning $url"
    git clone --branch "$GIT_BRANCH" "$url" "$dir"
    return
  fi
  log "Updating $(basename "$dir") ($GIT_BRANCH)"
  git -C "$dir" fetch --prune origin
  git -C "$dir" checkout -q "$GIT_BRANCH"
  git -C "$dir" merge --ff-only "origin/$GIT_BRANCH"
}
update_repo "${REPO_BACKEND:-}" "$BACKEND_DIR"
update_repo "${REPO_WEB:-}" "$WEB_REPO_DIR"

# --- Python packages --------------------------------------------------------------------

[[ -x "$VENV/bin/python" ]] || python3 -m venv "$VENV"
PY="$VENV/bin/python"
log "Installing Python packages"
"$PY" -m pip install --quiet --upgrade pip wheel
# One requirements file for every environment: it includes Gunicorn and the
# drivers for both MySQL and PostgreSQL.
"$PY" -m pip install --quiet -r "$BACKEND_DIR/requirements.txt"

# --- Django ----------------------------------------------------------------------------------

cd "$BACKEND_DIR"
# Before anything changes, so a failed check leaves the running version as it was. Security
# checks only (--tag security): drf-spectacular's schema-naming warnings are not a reason to
# stop a deploy. STRICT_CHECKS=1 (the default) stops on any security warning.
log "Django security checks"
if ! "$PY" manage.py check --deploy --tag security --fail-level WARNING; then
  if [[ "$STRICT_CHECKS" == "1" ]]; then
    die "manage.py check --deploy reported security warnings (STRICT_CHECKS=1). Fix them, or set STRICT_CHECKS=0 to deploy anyway."
  fi
  warn "check --deploy reported security warnings; see above."
fi

# Known vulnerabilities in the pinned packages (G10). pip-audit lives in a venv of its own,
# so it never mixes with the app's packages. AUDIT_DEPENDENCIES=0 skips it for an emergency.
if [[ "$AUDIT_DEPENDENCIES" == "1" ]]; then
  log "Checking Python packages for known vulnerabilities"
  AUDIT_VENV="$APP_ROOT/audit-venv"
  [[ -x "$AUDIT_VENV/bin/pip-audit" ]] || python3 -m venv "$AUDIT_VENV"
  "$AUDIT_VENV/bin/python" -m pip install --quiet "pip-audit==$PIP_AUDIT_VERSION"
  "$AUDIT_VENV/bin/pip-audit" --requirement "$BACKEND_DIR/requirements.txt" --progress-spinner off \
    || die "pip-audit found a known vulnerability (above) or could not run. Upgrade the package in requirements.txt, or set AUDIT_DEPENDENCIES=0 to deploy anyway."
fi

log "Migrating the database"
"$PY" manage.py migrate --noinput
log "Collecting static files"
"$PY" manage.py collectstatic --noinput >/dev/null
if "$PY" manage.py help ensure_schedules >/dev/null 2>&1; then
  log "Background schedules"
  "$PY" manage.py ensure_schedules
fi

# --- Web parts ------------------------------------------------------------------------------------

# One repo holds the website and the Super Admin console. Its build writes
# dist/ (website) and dist/console/ (console). They are published to the same
# folders Nginx already serves: www/public at / and www/console at /console/.
log "Building the website and console"
(
  cd "$WEB_REPO_DIR"
  npm ci --no-audit --no-fund --loglevel=error
  GC_API_BASE="$WEB_API_BASE" npm run build
)
for page in "$WEB_REPO_DIR/dist/index.html" "$WEB_REPO_DIR/dist/console/index.html"; do
  [[ -f "$page" ]] || die "The web build did not produce $page."
done
# Byte for byte: the Design Canvas runtime re-reads index.html on boot.
rsync -a --delete --exclude '/console/' --exclude '*.md' "$WEB_REPO_DIR/dist/" "$WWW_DIR/public/"
rsync -a --delete --exclude '*.md' "$WEB_REPO_DIR/dist/console/" "$WWW_DIR/console/"

# --- Restart ---------------------------------------------------------------------------------------

log "Restarting services"
sudo -n /usr/bin/systemctl restart gramaconnect-gunicorn.service
sudo -n /usr/bin/systemctl restart gramaconnect-qcluster.service

# --- Checks ----------------------------------------------------------------------------------------

log "Health check"
for _ in $(seq 1 15); do
  if curl -fsS --unix-socket "$GC_SOCKET" -H "Host: $DOMAIN" -H "X-Forwarded-Proto: https" \
       http://localhost/api/v1/health/ >/dev/null 2>&1; then
    log "API is up: $(curl -fsS --unix-socket "$GC_SOCKET" -H "Host: $DOMAIN" -H "X-Forwarded-Proto: https" http://localhost/api/v1/health/)"
    if [[ -s "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]]; then
      if curl -fsS -o /dev/null "https://$DOMAIN/api/v1/health/"; then
        log "https://$DOMAIN answers."
      else
        warn "The API is up but https://$DOMAIN did not answer; check Nginx and DNS."
      fi
    fi
    exit 0
  fi
  sleep 2
done
die "The API did not answer on $GC_SOCKET. See: journalctl -u gramaconnect-gunicorn -n 100"
