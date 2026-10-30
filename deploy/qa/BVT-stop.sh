#!/usr/bin/env bash
# BVT Grama Connect qa server: stop the backend and the background worker. No root or sudo needed.
#
#   bash BVT-stop.sh              stop now; they stay stopped until BVT-start.sh (also after a reboot)
#   bash BVT-stop.sh --disable    the same, and the keep-alive line leaves the crontab
#
# Leaves a "stopped" mark, so the keep-alive (BVT-start.sh --if-down, every minute) does not start
# them again behind your back, for example while data is loaded. BVT-start.sh removes the mark.
# The database, uploads and settings are not touched.
set -euo pipefail
# shellcheck source=qa-lib.sh
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/qa-lib.sh"

case "${1:-}" in
  "") disable=0 ;;
  --disable) disable=1 ;;
  *) echo "Usage: bash BVT-stop.sh [--disable]" >&2; exit 2 ;;
esac

mkdir -p "$STATE_DIR"
# The same lock as BVT-start.sh: never stop half-way through a keep-alive start.
exec 9>"$STATE_DIR/start.lock"
flock -w 120 9 || fail "A start has not finished after 2 minutes ($STATE_DIR/start.lock)."
touch "$STOPPED_MARK"

stop_one web
stop_one worker

if (( disable )) && set_cron_line "$AUTOSTART_TAG" ""; then
  echo "Keep-alive: off (removed from the crontab)."
fi

echo "Stopped. Start again with: bash BVT-start.sh"
