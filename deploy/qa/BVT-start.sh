#!/usr/bin/env bash
# BVT Grama Connect qa server: start (or restart) the backend. No root or sudo needed.
#
#   PORT=9501 bash BVT-start.sh    the first time: the port is remembered from then on
#   bash BVT-start.sh              later: the port used last time
#   bash BVT-start.sh --if-down    the keep-alive: start only when not running (cron, every minute)
#
# Runs two background processes as the current user:
#   web     Django on 0.0.0.0:PORT: website (/), console (/console/), API (/api/v1/), photos (/media/)
#   worker  background jobs (push notifications, review escalation, translation)
# They do not belong to the terminal: closing it or logging out leaves them running. Some hosting
# setups still end every process of a login at logout, so a crontab line runs the keep-alive every
# minute: cron is not part of any login, and it also brings them back after a crash or a reboot.
# BVT-stop.sh leaves a "stopped" mark that the keep-alive respects until the next BVT-start.sh.
# Settings come from the backend's .env: the dev settings with DEBUG=False and the database in
# DATABASE_URL (deploy/qa/README.md). PID files, the port and the logs are in ~/.bvt-grama.
set -euo pipefail
# shellcheck source=qa-lib.sh
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/qa-lib.sh"

case "${1:-}" in
  "") keepalive=0 ;;
  --if-down) keepalive=1 ;;
  *) echo "Usage: bash BVT-start.sh [--if-down]" >&2; exit 2 ;;
esac

# Start one manage.py command in the background, in its own session, logging to LOG_DIR.
start_one() {
  local name="$1" log="$LOG_DIR/$1.log"
  shift
  # Keep logs small: move a log over 20 MB aside, replacing the previous one.
  if [[ -f "$log" ]] && (( $(stat -c %s "$log") > 20 * 1024 * 1024 )); then
    mv -f "$log" "$log.1"
  fi
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') starting $name: manage.py $*" >>"$log"
  (
    cd "$BACKEND_DIR"
    # 9>&-: the processes must not inherit the start lock, or they would hold it for ever.
    PYTHONUNBUFFERED=1 setsid nohup "$PYTHON" manage.py "$@" >>"$log" 2>&1 </dev/null 9>&- &
    echo $! >"$STATE_DIR/$name.pid"
  )
}

# --- Checks ------------------------------------------------------------------

mkdir -p "$LOG_DIR"
chmod 700 "$STATE_DIR"

# One start at a time: a deploy's start and the keep-alive never overlap.
exec 9>"$STATE_DIR/start.lock"
if (( keepalive )); then
  flock -n 9 || exit 0  # a start is already under way
  [[ -f "$STOPPED_MARK" ]] && exit 0  # stopped on purpose with BVT-stop.sh
  [[ -f "$STATE_DIR/port" ]] || exit 0  # never started yet
  if is_ours "$(cat "$STATE_DIR/web.pid" 2>/dev/null || true)" && is_ours "$(cat "$STATE_DIR/worker.pid" 2>/dev/null || true)"; then
    exit 0  # running: nothing to do
  fi
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') keep-alive: web or worker is not running, starting both"
else
  flock -w 120 9 || fail "Another start has not finished after 2 minutes ($STATE_DIR/start.lock)."
  rm -f "$STOPPED_MARK"
fi

PORT="${PORT:-$(cat "$STATE_DIR/port" 2>/dev/null || true)}"
[[ -n "$PORT" ]] || fail "No port yet. The first time: PORT=9501 bash BVT-start.sh"
[[ "$PORT" =~ ^[0-9]+$ ]] && (( PORT >= 1024 && PORT <= 65535 )) \
  || fail "PORT must be a number from 1024 to 65535 (lower ports need root), not '$PORT'."
[[ -f "$BACKEND_DIR/manage.py" ]] || fail "No backend at $BACKEND_DIR."
[[ -x "$PYTHON" ]] || fail "No virtual environment at $BACKEND_DIR/.venv. Run BVT-deploy.sh first."
[[ -f "$BACKEND_DIR/.env" ]] || fail "No $BACKEND_DIR/.env. Run BVT-deploy.sh first."
for tool in setsid curl ss flock; do
  command -v "$tool" >/dev/null || fail "$tool is not installed."
done

# --- Restart -----------------------------------------------------------------

stop_one web
stop_one worker

if ss -ltnH "sport = :$PORT" 2>/dev/null | grep -q .; then
  ss -ltnpH "sport = :$PORT" 2>/dev/null >&2 || true
  fail "Port $PORT is already used by another program (above). Pick another: PORT=<port> bash BVT-start.sh"
fi

echo "$PORT" >"$STATE_DIR/port"
start_one web runserver "0.0.0.0:$PORT" --noreload
start_one worker qcluster

# --- Keep it running: after logout, a crash or a reboot ------------------------------

if (( ! keepalive )); then
  if set_cron_line "$AUTOSTART_TAG" "* * * * * bash $(readlink -f "${BASH_SOURCE[0]}") --if-down >>$LOG_DIR/keepalive.log 2>&1 $AUTOSTART_TAG"; then
    echo "Keep-alive: on (crontab, every minute). Closing the terminal or logging out does not stop it."
  else
    echo "WARNING: could not write your crontab: after a logout, crash or reboot run BVT-start.sh again." >&2
  fi
fi

# --- Wait for it -----------------------------------------------------------------

echo -n "Waiting for the backend on port $PORT"
web_pid="$(cat "$STATE_DIR/web.pid")"
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:$PORT/api/v1/health/" >/dev/null 2>&1; then
    echo " ok."
    ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
    echo
    echo "Running. Open, with your server's public IP address:"
    echo "  Website  http://${ip:-<server IP>}:$PORT/"
    echo "  Console  http://${ip:-<server IP>}:$PORT/console/"
    echo "  API      http://${ip:-<server IP>}:$PORT/api/v1/health/"
    echo
    echo "Logs:  tail -f $LOG_DIR/web.log     tail -f $LOG_DIR/worker.log"
    exit 0
  fi
  if ! is_ours "$web_pid"; then
    echo " stopped."
    tail -n 25 "$LOG_DIR/web.log" >&2
    fail "The backend exited (log above, full log: $LOG_DIR/web.log)."
  fi
  echo -n "."
  sleep 1
done

echo " no answer after 30 seconds."
tail -n 25 "$LOG_DIR/web.log" >&2
fail "The backend did not answer. Full log: $LOG_DIR/web.log"
