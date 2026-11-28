# Shared by the BVT-*.sh scripts of a qa server, or of production, on a hosting account without root
# (deploy/qa/README.md). Sourced, not run.

# This file sits in <backend>/deploy/qa/, so the backend is two folders up; the web repo is cloned
# next to the backend. Both can be overridden from the environment.
QA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="${BACKEND_DIR:-$(cd "$QA_DIR/../.." && pwd)}"
WEB_DIR="${WEB_DIR:-$(cd "$BACKEND_DIR/.." && pwd)/BVTGramaConnect}"
PYTHON="$BACKEND_DIR/.venv/bin/python"

# PID files, the port and the logs, outside the web folders. One per Unix account.
STATE_DIR="$HOME/.bvt-grama"
LOG_DIR="$STATE_DIR/logs"
AUTOSTART_TAG="# bvt-grama-autostart"
# Left by BVT-stop.sh: the keep-alive does not start what was stopped on purpose.
STOPPED_MARK="$STATE_DIR/stopped"
BACKUP_TAG="# bvt-grama-backup"

step() { printf '\n== %s\n' "$*"; }
fail() { echo "ERROR: $*" >&2; exit 1; }

# Production on the same kind of hosting account (deploy/qa/README.md, "Production on a hosting
# panel"): the backend's .env names the production settings, and every manage.py, Gunicorn and
# backup run here uses them. A qa server's .env has no such line. Both load grama/settings.py,
# which goes by DEBUG; this name only tells the scripts which kind of server this is.
load_settings_module() {
  local module=""
  if [[ -f "$BACKEND_DIR/.env" ]]; then
    module="$(sed -n 's/^DJANGO_SETTINGS_MODULE=//p' "$BACKEND_DIR/.env" | tail -n 1 | tr -d '"'"'"' \r')"
  fi
  if [[ -n "$module" ]]; then
    export DJANGO_SETTINGS_MODULE="$module"
  fi
}
is_production() { [[ "${DJANGO_SETTINGS_MODULE:-}" == "grama.settings.prod" ]]; }
load_settings_module

# True when PID is alive and is one of our processes, manage.py or Gunicorn (not a reused PID).
is_ours() {
  local pid="$1"
  [[ -n "$pid" && -r "/proc/$pid/cmdline" ]] && tr '\0' ' ' <"/proc/$pid/cmdline" | grep -qE "manage\.py|gunicorn"
}

# Stop one process and everything it started (it runs in its own process group).
stop_one() {
  local name="$1" pidfile="$STATE_DIR/$1.pid" pid
  pid="$(cat "$pidfile" 2>/dev/null || true)"
  if is_ours "$pid"; then
    kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    for _ in $(seq 1 15); do
      is_ours "$pid" || break
      sleep 1
    done
    if is_ours "$pid"; then
      kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
    fi
    echo "Stopped $name (PID $pid)."
  else
    echo "$name was not running."
  fi
  rm -f "$pidfile"
}

# Rotate every log in LOG_DIR that has grown past LOG_MAX_MB (20): its lines move to <log>.1, older
# copies shift up to <log>.LOG_KEEP (5) and the oldest is dropped. Copy, then empty in place
# (logrotate's copytruncate): web and worker keep writing to the same open file, so nothing needs a
# restart and no root is needed. Run by the keep-alive every minute (finding BE-OBS-202), so the disk
# cannot fill between restarts. A line written between the copy and the emptying can be lost.
rotate_logs() {
  local max=$(( ${LOG_MAX_MB:-20} * 1024 * 1024 )) keep="${LOG_KEEP:-5}" log n
  for log in "$LOG_DIR"/*.log; do
    [[ -f "$log" ]] && (( $(stat -c %s "$log") > max )) || continue
    for (( n = keep - 1; n >= 1; n-- )); do
      [[ -f "$log.$n" ]] && mv -f "$log.$n" "$log.$(( n + 1 ))"
    done
    cp -p "$log" "$log.1" && : >"$log"
  done
  return 0
}

# Replace this account's crontab line carrying TAG with LINE (an empty LINE only removes it).
set_cron_line() {
  local tag="$1" line="$2"
  command -v crontab >/dev/null || { echo "WARNING: crontab is not available." >&2; return 1; }
  { crontab -l 2>/dev/null | grep -vF "$tag" || true; [[ -n "$line" ]] && echo "$line"; } | crontab -
}
