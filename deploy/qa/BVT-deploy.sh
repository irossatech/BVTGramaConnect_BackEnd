#!/usr/bin/env bash
# BVT Grama Connect qa server: get the latest code and (re)start it. No root or sudo needed.
#
#   SERVER_IP=147.93.102.247 PORT=9501 bash BVT-deploy.sh    the first time
#   bash BVT-deploy.sh                                       every later release
#
# Production on a hosting account of the same kind (deploy/qa/README.md, "Production on a
# hosting panel"), the first time, as that site's own user:
#   MODE=production DOMAIN=www.gramaconnect.com EXTRA_DOMAINS=gramaconnect.com PORT=9601 bash BVT-deploy.sh
# It writes a production .env (DEBUG=False, grama.settings.prod, Gunicorn), deploys BRANCH (default main
# there), checks Django's security settings, collects the static files and refuses SEED=1.
#
# Pulls the backend repo and the web repo (cloned next to it), uses the website and console
# build committed in the web repo's dist/ (or builds it, when the repo has none), installs the Python packages, writes the backend's .env
# on the first run, migrates the database and restarts with BVT-start.sh. deploy/qa/README.md
# has the whole setup, the database line for .env and the data copy.
#
# Options, as environment variables:
#   SERVER_IP   the address people type in the browser; needed on the first run
#   DOMAIN      also allowed on the first run, e.g. dev.bvtgramaconnect.com
#   PORT        the port; on later runs the port already in use is kept
#   BRANCH      the branch of both repos, default dev
#   PYTHON_BIN  the Python 3.12+ to build the virtual environment with, default python3
#   QA_SQLITE=1 run on a SQLite file instead of DATABASE_URL (a throwaway server only)
#   SEED=1      load the sample villages, posts and officials: refused once villages exist,
#               because it would write its sample places over the real list
set -euo pipefail
# shellcheck source=qa-lib.sh
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/qa-lib.sh"

main() {
  local branch python_bin production=0
  if is_production || [[ "${MODE:-}" == "production" ]]; then
    production=1
    [[ "${SEED:-0}" != "1" ]] || fail "SEED=1 is refused in production: it writes sample places, posts and officials."
    [[ "${QA_SQLITE:-0}" != "1" ]] || fail "QA_SQLITE=1 is refused in production: it needs its own MySQL database."
  fi
  if (( production )); then branch="${BRANCH:-main}"; else branch="${BRANCH:-dev}"; fi
  python_bin="${PYTHON_BIN:-python3}"

  step "Checking tools and folders"
  [[ -f "$BACKEND_DIR/manage.py" ]] || fail "No backend repo at $BACKEND_DIR."
  [[ -d "$WEB_DIR/.git" ]] || fail "No web repo at $WEB_DIR. Clone BVTGramaConnect next to the backend."
  command -v git >/dev/null || fail "git is not installed."
  command -v curl >/dev/null || fail "curl is not installed."
  command -v "$python_bin" >/dev/null || fail "$python_bin is not installed."
  "$python_bin" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' \
    || fail "Python 3.12 or newer is needed; $python_bin is $("$python_bin" --version 2>&1). Set PYTHON_BIN=python3.12."

  step "Web repo: $WEB_DIR ($branch)"
  drop_untracked_build "$WEB_DIR"
  update_repo "$WEB_DIR" "$branch"
  build_web "$WEB_DIR"

  step "Backend repo: $BACKEND_DIR ($branch)"
  update_repo "$BACKEND_DIR" "$branch"

  step "Python packages"
  [[ -x "$PYTHON" ]] || "$python_bin" -m venv "$BACKEND_DIR/.venv"
  "$PYTHON" -m pip install --quiet --upgrade pip
  "$PYTHON" -m pip install --quiet -r "$BACKEND_DIR/requirements.txt"

  step "Settings (.env)"
  if (( production )); then write_production_env; else write_env; fi
  load_settings_module
  check_database_setting

  step "Database"
  cd "$BACKEND_DIR"
  "$PYTHON" manage.py check
  if (( production )); then
    is_production || fail "$BACKEND_DIR/.env does not name grama.settings.prod; this is not a production .env."
    # Before anything changes, as deploy/deploy.sh does: a weak key or a missing https setting stops here.
    "$PYTHON" manage.py check --deploy --tag security --fail-level WARNING \
      || fail "Django's security check failed (above). Fix .env, then deploy again."
  fi
  "$PYTHON" manage.py migrate --noinput
  "$PYTHON" manage.py ensure_schedules
  # Shared posts' category pictures, Django admin and API docs styles: the panel's nginx serves
  # .jpg and .css files through the site folder's static link, never through Django.
  "$PYTHON" manage.py collectstatic --noinput >/dev/null
  echo "Static files collected (link the site folder's static to them: deploy/qa/README.md)."
  if (( production )); then
    # App Links: the panel's nginx serves *.json from the site folder, never from Django.
    "$PYTHON" manage.py write_assetlinks "$(dirname "$BACKEND_DIR")/.well-known/assetlinks.json"
  fi
  if [[ "${SEED:-0}" == "1" ]]; then
    local villages
    villages="$("$PYTHON" manage.py shell -c 'from apps.geography.models import Village; print(Village.objects.count())' | tail -n 1)"
    [[ "$villages" == "0" ]] || fail "SEED=1 refused: the database already has $villages villages, and the sample data would write its own places and coordinates over them. On a server with the real list, use: manage.py load_demo_area"
    echo "Loading sample data. Copy the temporary passwords below: they are shown only once."
    "$PYTHON" manage.py seed_sample_data
  fi

  step "Starting"
  bash "$QA_DIR/BVT-start.sh"
}

# Fast-forward a repo to origin/<branch>. Stops if someone changed tracked files on the server.
update_repo() {
  local dir="$1" branch="$2"
  if [[ -n "$(git -C "$dir" status --porcelain --untracked-files=no)" ]]; then
    git -C "$dir" status --short --untracked-files=no >&2
    fail "$dir has local changes (above). Commit or undo them, then deploy again."
  fi
  git -C "$dir" fetch --quiet origin
  git -C "$dir" checkout --quiet "$branch"
  git -C "$dir" pull --quiet --ff-only origin "$branch"
  echo "Now at $(git -C "$dir" log --oneline -1)"
}

# Is the website and console build committed in the web repo (dist/ in git)?
build_is_committed() {
  git -C "$1" ls-files --error-unmatch dist/index.html dist/console/index.html >/dev/null 2>&1
}

# A dist/ built on this server before the web repo committed its own would stop the pull
# ("untracked files would be overwritten"). It is only build output, so it goes.
drop_untracked_build() {
  local dir="$1"
  if [[ -d "$dir/dist" ]] && ! build_is_committed "$dir"; then
    rm -rf "$dir/dist"
  fi
}

# The web repo commits dist/ (website and console, built by the web developer): use it as it is,
# so the server never changes files git tracks. Without a committed dist/, build it here as before:
# npm packages (again only when package-lock.json changed), then the Next.js static export.
build_web() {
  local dir="$1"
  if build_is_committed "$dir"; then
    echo "Using the committed build: $dir/dist (website) and $dir/dist/console (console)"
    return
  fi
  [[ -f "$dir/package.json" ]] || fail "No package.json in $dir."
  command -v npm >/dev/null || fail "Node.js and npm are not installed."
  node -e 'const [a, b] = process.versions.node.split(".").map(Number); process.exit(a > 20 || (a === 20 && b >= 9) ? 0 : 1)' \
    || fail "Node.js 20.9 or newer is needed for the Next.js build; this is $(node --version)."
  cd "$dir"
  if [[ ! -f node_modules/.package-lock.json || package-lock.json -nt node_modules/.package-lock.json ]]; then
    npm ci --no-audit --no-fund --loglevel=error
  fi
  npm run build
  [[ -f dist/index.html && -f dist/console/index.html ]] \
    || fail "The build did not write dist/index.html and dist/console/index.html."
  echo "Built: $dir/dist (website) and $dir/dist/console (console)"
}

# Write the backend's .env on the first run only; later runs leave it as it is.
write_env() {
  local env_file="$BACKEND_DIR/.env" secret hosts
  if [[ -f "$env_file" ]]; then
    echo "Keeping the existing $env_file."
    if [[ -n "${SERVER_IP:-}" ]] && ! grep -q "^ALLOWED_HOSTS=.*${SERVER_IP}" "$env_file"; then
      echo "NOTE: SERVER_IP=$SERVER_IP is not in ALLOWED_HOSTS in $env_file. Add it there, or the site answers 400." >&2
    fi
    return
  fi
  [[ -n "${SERVER_IP:-}" ]] || fail "First run: give the address people will use, e.g.  SERVER_IP=147.93.102.247 PORT=9501 bash BVT-deploy.sh"
  secret="$("$PYTHON" -c 'import secrets; print(secrets.token_urlsafe(50))')"
  hosts="$SERVER_IP${DOMAIN:+,$DOMAIN},localhost,127.0.0.1"
  (
    umask 077
    cat >"$env_file" <<EOF
# BVT Grama Connect qa server. Written by deploy/qa/BVT-deploy.sh.
# DEBUG=False runs grama/settings.py in production mode: no debug pages, secure cookies, and the
# lines below are required. Add the database line yourself (deploy/qa/README.md, "The database"):
# DATABASE_URL=mysql://...
SECRET_KEY=$secret
DEBUG=False
ALLOWED_HOSTS=$hosts
WEB_PUBLIC_DIR=$WEB_DIR/dist
WEB_ADMIN_DIR=$WEB_DIR/dist/console
MEDIA_ROOT=$BACKEND_DIR/media
STATIC_ROOT=$BACKEND_DIR/staticfiles
# No Nginx of our own: Django serves the website, the console and the photos.
SERVE_FILES=True
# Debug builds of the app cannot pass Google Play Integrity.
CITIZEN_INTEGRITY_REQUIRED=False
# Swagger (/api/docs/) open for the client teams.
API_DOCS_PUBLIC=True
EOF
    if [[ "${QA_SQLITE:-0}" == "1" ]]; then
      echo "DATABASE_URL=sqlite:///$BACKEND_DIR/db.sqlite3" >>"$env_file"
    fi
  )
  echo "Wrote $env_file (readable by you only)."
}

# Production on a hosting panel: write .env on the first run only, with the production settings.
# The database line (DATABASE_URL) is added by hand, as on qa; the Firebase key and the office
# details too (deploy/qa/README.md, "Production on a hosting panel").
write_production_env() {
  local env_file="$BACKEND_DIR/.env" secret names hosts origins first
  if [[ -f "$env_file" ]]; then
    echo "Keeping the existing $env_file."
    return
  fi
  [[ -n "${DOMAIN:-}" ]] || fail "First production run: give the domain, e.g.  MODE=production DOMAIN=www.gramaconnect.com EXTRA_DOMAINS=gramaconnect.com PORT=9601 bash BVT-deploy.sh"
  names="$DOMAIN${EXTRA_DOMAINS:+ $EXTRA_DOMAINS}"
  first="$DOMAIN"
  hosts="$(echo "$names" | tr ' ' ','),localhost,127.0.0.1"
  # shellcheck disable=SC2086  # one origin per name
  origins="$(printf 'https://%s,' $names)"
  origins="${origins%,}"
  secret="$("$PYTHON" -c 'import secrets; print(secrets.token_urlsafe(50))')"
  (
    umask 077
    cat >"$env_file" <<EOF
# BVT Grama Connect production on a hosting panel. Written by deploy/qa/BVT-deploy.sh (MODE=production).
# Add DATABASE_URL yourself (deploy/qa/README.md, "Production on a hosting panel").
# The scripts read this name to know this is production; grama/settings.py itself goes by DEBUG.
DJANGO_SETTINGS_MODULE=grama.settings.prod
DEBUG=False
SECRET_KEY=$secret
ALLOWED_HOSTS=$hosts
CSRF_TRUSTED_ORIGINS=$origins
MEDIA_ROOT=$BACKEND_DIR/media
STATIC_ROOT=$BACKEND_DIR/staticfiles
WEB_PUBLIC_DIR=$WEB_DIR/dist
WEB_ADMIN_DIR=$WEB_DIR/dist/console
# The panel's nginx forwards every address without a file extension here, so Django serves the
# website and console pages; files with an extension come from the site folder's links.
SERVE_FILES=True
# The panel's nginx sets X-Real-IP; check it once (README) so lockouts count real addresses.
TRUST_X_REAL_IP=True
PUBLIC_BASE_URL=https://$first/
SITE_WEBSITE_URL=https://$first/
PLAY_INTEGRITY_PACKAGE=com.bvt.app
# Owner's decision of 2026-11-04: citizens may post from the website's APK until the app is on
# Google Play. Remove this line on the day it is.
CITIZEN_INTEGRITY_REQUIRED=False
FCM_PROJECT_ID=bvt-grama-connect
FCM_TOPIC_PREFIX=
EOF
  )
  echo "Wrote $env_file (readable by you only, production settings)."
}

# A qa server keeps its data in its own database. With DEBUG=False the settings refuse to start
# without DATABASE_URL; QA_SQLITE=1 writes a SQLite one on the first run.
check_database_setting() {
  if grep -qE '^DATABASE_URL=' "$BACKEND_DIR/.env"; then
    echo "Database: DATABASE_URL from .env."
  elif [[ "${QA_SQLITE:-0}" == "1" ]]; then
    echo "Database: a SQLite file in $BACKEND_DIR (QA_SQLITE=1)."
  else
    fail "No DATABASE_URL in $BACKEND_DIR/.env. Add it (deploy/qa/README.md, \"The database\"), or QA_SQLITE=1 for a throwaway SQLite server."
  fi
}

# All on one line: git pull may replace this file while it runs, and bash reads scripts as it goes.
main "$@"; exit $?
