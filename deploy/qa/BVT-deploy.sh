#!/usr/bin/env bash
# BVT Grama Connect qa server: get the latest code and (re)start it. No root or sudo needed.
#
#   SERVER_IP=147.93.102.247 PORT=9501 bash BVT-deploy.sh    the first time
#   bash BVT-deploy.sh                                       every later release
#
# Pulls the backend repo and the web repo (cloned next to it), builds the website and console
# (Next.js static export into dist/), installs the Python packages, writes the backend's .env
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
  local branch python_bin
  branch="${BRANCH:-dev}"
  python_bin="${PYTHON_BIN:-python3}"

  step "Checking tools and folders"
  [[ -f "$BACKEND_DIR/manage.py" ]] || fail "No backend repo at $BACKEND_DIR."
  [[ -d "$WEB_DIR/.git" ]] || fail "No web repo at $WEB_DIR. Clone BVTGramaConnect next to the backend."
  command -v git >/dev/null || fail "git is not installed."
  command -v curl >/dev/null || fail "curl is not installed."
  command -v npm >/dev/null || fail "Node.js and npm are not installed."
  node -e 'const [a, b] = process.versions.node.split(".").map(Number); process.exit(a > 20 || (a === 20 && b >= 9) ? 0 : 1)' \
    || fail "Node.js 20.9 or newer is needed for the Next.js build; this is $(node --version)."
  command -v "$python_bin" >/dev/null || fail "$python_bin is not installed."
  "$python_bin" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' \
    || fail "Python 3.12 or newer is needed; $python_bin is $("$python_bin" --version 2>&1). Set PYTHON_BIN=python3.12."

  step "Web repo: $WEB_DIR ($branch)"
  update_repo "$WEB_DIR" "$branch"
  build_web "$WEB_DIR"

  step "Backend repo: $BACKEND_DIR ($branch)"
  update_repo "$BACKEND_DIR" "$branch"

  step "Python packages"
  [[ -x "$PYTHON" ]] || "$python_bin" -m venv "$BACKEND_DIR/.venv"
  "$PYTHON" -m pip install --quiet --upgrade pip
  "$PYTHON" -m pip install --quiet -r "$BACKEND_DIR/requirements.txt"

  step "Settings (.env)"
  write_env
  check_database_setting

  step "Database"
  cd "$BACKEND_DIR"
  "$PYTHON" manage.py check
  "$PYTHON" manage.py migrate --noinput
  "$PYTHON" manage.py ensure_schedules
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

# Next.js static export: npm packages (again only when package-lock.json changed), then the build.
build_web() {
  local dir="$1"
  [[ -f "$dir/package.json" ]] || fail "No package.json in $dir."
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
# BVT Grama Connect qa server (dev settings, DEBUG off). Written by deploy/qa/BVT-deploy.sh.
# Add the database line yourself (deploy/qa/README.md, "The database"): DATABASE_URL=mysql://...
SECRET_KEY=$secret
DEBUG=False
ALLOWED_HOSTS=$hosts
WEB_PUBLIC_DIR=$WEB_DIR/dist
WEB_ADMIN_DIR=$WEB_DIR/dist/console
EOF
  )
  echo "Wrote $env_file (readable by you only)."
}

# A qa server keeps its data in its own database. Without DATABASE_URL, the dev settings would
# quietly create a SQLite file in the backend folder and migrate that instead.
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
