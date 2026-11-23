#!/usr/bin/env bash
# BVT Grama Connect: deploy the latest code. Run on the server:
#
#   sudo bash /srv/gramaconnect/backend/deploy/deploy.sh      (or as the deploy user)
#
# Pulls the backend, public website and console repos, installs Python
# packages, migrates, collects static files, builds and publishes the web
# parts, makes sure the background schedules exist, restarts the services,
# then runs Django's deploy checks and a health check. Each deploy that passes the
# health check is recorded in $APP_ROOT/deploy-history.log (backend and web commit).
#
#   deploy.sh --rollback                         back to the previous recorded backend + web pair
#   deploy.sh --rollback <backend> <web>         back to these two commits
#
# A rollback puts back code, packages, static files and the web build, restarts and checks
# the API. It never undoes migrations: when the database has migrations the older code does
# not know, it stops before changing anything (restore the backup taken before that deploy).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$HERE/lib.sh"
# shellcheck source=history-lib.sh
source "$HERE/history-lib.sh"
load_config "$GC_DEPLOY_ENV"

MODE=deploy
ROLLBACK_BACKEND=""
ROLLBACK_WEB=""
case "${1:-}" in
  "") ;;
  --rollback)
    MODE=rollback
    ROLLBACK_BACKEND="${2:-}"
    ROLLBACK_WEB="${3:-}"
    if [[ -n "$ROLLBACK_BACKEND" && -z "$ROLLBACK_WEB" ]]; then
      die "Give both commits: deploy.sh --rollback <backend commit> <web commit>, or neither."
    fi
    ;;
  *) die "Unknown option '$1'. Use: deploy.sh, or deploy.sh --rollback [<backend commit> <web commit>]" ;;
esac
DEPLOY_HISTORY="$APP_ROOT/deploy-history.log"

# A backup before anything changes (audit finding BE-DB-013): on MySQL a migration that fails part-way
# stays half applied, and the nightly backup can be a day old. backup.sh needs root, so it runs here,
# before root hands over to the deploy user. A failed backup stops the deploy with nothing changed.
if [[ "$BACKUP_BEFORE_DEPLOY" == "1" ]]; then
  if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
    log "Backup before deploying"
    bash "$HERE/backup.sh" || die "The backup before deploying failed; nothing was changed. Fix it, or set BACKUP_BEFORE_DEPLOY=0 to deploy without one."
  elif [[ -z "${GC_BACKUP_DONE:-}" ]]; then
    warn "Started as $(id -un), not root: no backup was taken before deploying. Run it with sudo to get one."
  fi
fi

# Everything below runs as the deploy user; root re-runs this script as that user.
if [[ "$(id -un)" != "$APP_USER" ]]; then
  [[ ${EUID:-$(id -u)} -eq 0 ]] || die "Run as $APP_USER or root."
  exec sudo -u "$APP_USER" -H env GC_BACKUP_DONE=1 bash "$0" "$@"
fi

[[ -r "$GC_APP_ENV" ]] || die "$GC_APP_ENV is missing or unreadable; run setup.sh first."
set -a
# shellcheck disable=SC1090
source "$GC_APP_ENV"
set +a
export LANG=C.UTF-8 PYTHONUTF8=1
PY="$VENV/bin/python"

# --- Steps shared by a deploy and a rollback ---------------------------------------------------

# Is the website and console build committed in the web repo (dist/ in git)?
build_is_committed() {
  git -C "$WEB_REPO_DIR" ls-files --error-unmatch dist/index.html dist/console/index.html >/dev/null 2>&1
}

# One repo holds the website and the Super Admin console. Its build writes
# dist/ (website) and dist/console/ (console). They are published to the same
# folders Nginx already serves: www/public at / and www/console at /console/.
# The web repo commits dist/, built by the web developer: it is used as it is, so the server never
# changes files git tracks (the next merge would stop). That build calls the API at /api/v1 on the
# same domain. Without a committed dist/, it is built here as before.
publish_web() {
  if build_is_committed; then
    log "Using the committed website and console build"
    [[ "$WEB_API_BASE" == "/api/v1" ]] \
      || warn "WEB_API_BASE=$WEB_API_BASE is ignored: the committed build calls /api/v1 on the same domain."
  else
    log "Building the website and console"
    (
      cd "$WEB_REPO_DIR"
      npm ci --no-audit --no-fund --loglevel=error
      GC_API_BASE="$WEB_API_BASE" npm run build
    )
  fi
  local page
  for page in "$WEB_REPO_DIR/dist/index.html" "$WEB_REPO_DIR/dist/console/index.html"; do
    [[ -f "$page" ]] || die "The web build did not produce $page."
  done
  # Byte for byte: the Design Canvas runtime re-reads index.html on boot.
  rsync -a --delete --exclude '/console/' --exclude '*.md' "$WEB_REPO_DIR/dist/" "$WWW_DIR/public/"
  rsync -a --delete --exclude '*.md' "$WEB_REPO_DIR/dist/console/" "$WWW_DIR/console/"
}

restart_services() {
  log "Restarting services"
  sudo -n /usr/bin/systemctl restart gramaconnect-gunicorn.service
  sudo -n /usr/bin/systemctl restart gramaconnect-qcluster.service
}

health_check() {
  log "Health check"
  local _
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
      return 0
    fi
    sleep 2
  done
  die "The API did not answer on $GC_SOCKET. See: journalctl -u gramaconnect-gunicorn -n 100. To go back to the previous version: deploy.sh --rollback"
}

# --- Rollback ------------------------------------------------------------------------------------

if [[ "$MODE" == "rollback" ]]; then
  [[ -d "$BACKEND_DIR/.git" && -d "$WEB_REPO_DIR/.git" ]] || die "No deployed code to roll back."
  current_backend="$(git -C "$BACKEND_DIR" rev-parse HEAD)"
  current_web="$(git -C "$WEB_REPO_DIR" rev-parse HEAD)"
  if [[ -z "$ROLLBACK_BACKEND" ]]; then
    target="$(history_target "$DEPLOY_HISTORY" "$current_backend" "$current_web")" \
      || die "No earlier deploy is recorded in $DEPLOY_HISTORY. Give the commits: deploy.sh --rollback <backend commit> <web commit>"
    read -r ROLLBACK_BACKEND ROLLBACK_WEB <<<"$target"
  fi
  git -C "$BACKEND_DIR" fetch -q --prune origin || warn "Could not fetch the backend repo; using the commits on disk."
  git -C "$WEB_REPO_DIR" fetch -q --prune origin || warn "Could not fetch the web repo; using the commits on disk."
  ROLLBACK_BACKEND="$(resolve_commit "$BACKEND_DIR" "$ROLLBACK_BACKEND")" \
    || die "Backend commit not found: $ROLLBACK_BACKEND"
  ROLLBACK_WEB="$(resolve_commit "$WEB_REPO_DIR" "$ROLLBACK_WEB")" \
    || die "Web commit not found: $ROLLBACK_WEB"

  log "Checking the database against backend ${ROLLBACK_BACKEND:0:12}"
  unknown="$(migrations_unknown_to "$PY" "$BACKEND_DIR" "$ROLLBACK_BACKEND")" || die "Could not read the applied migrations; nothing was changed."
  if [[ -n "$unknown" ]]; then
    printf '%s\n' "$unknown" >&2
    die "The database has the migrations above, which backend ${ROLLBACK_BACKEND:0:12} does not have; nothing was changed. Restore the backup taken before that deploy (restore.sh, see RUNBOOK.md), then run deploy.sh --rollback again."
  fi

  log "Rolling back: backend ${current_backend:0:12} -> ${ROLLBACK_BACKEND:0:12}, web ${current_web:0:12} -> ${ROLLBACK_WEB:0:12}"
  git -C "$BACKEND_DIR" reset -q --hard "$ROLLBACK_BACKEND"
  log "Installing the Python packages of that commit"
  "$PY" -m pip install --quiet -r "$BACKEND_DIR/requirements.txt"
  (cd "$BACKEND_DIR" && "$PY" manage.py collectstatic --noinput >/dev/null)
  git -C "$WEB_REPO_DIR" reset -q --hard "$ROLLBACK_WEB"
  publish_web
  restart_services
  health_check
  history_record "$DEPLOY_HISTORY" rolled-back "$BACKEND_DIR" "$WEB_REPO_DIR"
  log "Rolled back to backend ${ROLLBACK_BACKEND:0:12} and web ${ROLLBACK_WEB:0:12} (recorded in $DEPLOY_HISTORY). The next deploy.sh brings the branch's latest code back."
  exit 0
fi

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

# Known vulnerabilities in the packages the NEW code pins (G10), checked before anything changes on
# disk. pip-audit lives in a venv of its own, so it never mixes with the app's packages.
# AUDIT_DEPENDENCIES=0 skips it for an emergency.
if [[ "$AUDIT_DEPENDENCIES" == "1" && -d "$BACKEND_DIR/.git" ]]; then
  log "Checking the new code's Python packages for known vulnerabilities"
  git -C "$BACKEND_DIR" fetch --prune origin
  incoming="$(mktemp)"
  git -C "$BACKEND_DIR" show "origin/$GIT_BRANCH:requirements.txt" >"$incoming"
  AUDIT_VENV="$APP_ROOT/audit-venv"
  [[ -x "$AUDIT_VENV/bin/pip-audit" ]] || python3 -m venv "$AUDIT_VENV"
  "$AUDIT_VENV/bin/python" -m pip install --quiet "pip-audit==$PIP_AUDIT_VERSION"
  if ! "$AUDIT_VENV/bin/pip-audit" --requirement "$incoming" --progress-spinner off; then
    rm -f "$incoming"
    die "pip-audit found a known vulnerability (above) or could not run; nothing was changed. Upgrade the package in requirements.txt, or set AUDIT_DEPENDENCIES=0 to deploy anyway."
  fi
  rm -f "$incoming"
fi

# The commit running now, to go back to when the checks below fail.
previous_backend=""
[[ -d "$BACKEND_DIR/.git" ]] && previous_backend="$(git -C "$BACKEND_DIR" rev-parse HEAD)"
previous_web=""
[[ -d "$WEB_REPO_DIR/.git" ]] && previous_web="$(git -C "$WEB_REPO_DIR" rev-parse HEAD)"
update_repo "${REPO_BACKEND:-}" "$BACKEND_DIR"
# A dist/ built here before the web repo committed its own would stop the merge; it is only build output.
if [[ -d "$WEB_REPO_DIR/dist" ]] && ! build_is_committed; then
  rm -rf "$WEB_REPO_DIR/dist"
fi
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
# Security checks only (--tag security): drf-spectacular's schema-naming warnings are not a reason
# to stop a deploy. STRICT_CHECKS=1 (the default) stops on any security warning. The new code and
# its packages are on disk by now, and Gunicorn workers that restart on --max-requests would load
# them, so a failed check puts the previous commit and its packages back before stopping.
log "Django security checks"
if ! "$PY" manage.py check --deploy --tag security --fail-level WARNING; then
  if [[ "$STRICT_CHECKS" == "1" ]]; then
    if [[ -n "$previous_backend" ]]; then
      warn "Going back to the previous backend commit ${previous_backend:0:12} and its packages."
      git -C "$BACKEND_DIR" reset -q --hard "$previous_backend"
      "$PY" -m pip install --quiet -r "$BACKEND_DIR/requirements.txt" || warn "Could not reinstall the previous packages."
    fi
    # The web files Nginx serves were not touched yet; the web repo goes back too, so both repos match what is live.
    [[ -n "$previous_web" ]] && git -C "$WEB_REPO_DIR" reset -q --hard "$previous_web"
    die "manage.py check --deploy reported security warnings (STRICT_CHECKS=1); the previous version is back in place, and nothing was migrated. Fix them, or set STRICT_CHECKS=0 to deploy anyway."
  fi
  warn "check --deploy reported security warnings; see above."
fi

log "Migrating the database"
"$PY" manage.py migrate --noinput
log "Collecting static files"
"$PY" manage.py collectstatic --noinput >/dev/null
if "$PY" manage.py help ensure_schedules >/dev/null 2>&1; then
  log "Background schedules"
  "$PY" manage.py ensure_schedules
fi

# --- Web parts, restart, checks ---------------------------------------------------------------------

publish_web
restart_services
health_check
history_record "$DEPLOY_HISTORY" deployed "$BACKEND_DIR" "$WEB_REPO_DIR"
log "Deployed backend $(git -C "$BACKEND_DIR" rev-parse --short=12 HEAD) and web $(git -C "$WEB_REPO_DIR" rev-parse --short=12 HEAD) (recorded in $DEPLOY_HISTORY)."
