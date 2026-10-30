#!/usr/bin/env bash
# BVT Grama Connect qa server: stop the backend and the background worker. No root or sudo needed.
#
#   bash BVT-stop.sh              stop now; they start again after the next reboot
#   bash BVT-stop.sh --disable    stop now and do not start after a reboot
#
# Start again with BVT-start.sh. The database, uploads and settings are not touched.
set -euo pipefail
# shellcheck source=qa-lib.sh
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/qa-lib.sh"

case "${1:-}" in
  "") disable=0 ;;
  --disable) disable=1 ;;
  *) echo "Usage: bash BVT-stop.sh [--disable]" >&2; exit 2 ;;
esac

stop_one web
stop_one worker

if (( disable )) && set_cron_line "$AUTOSTART_TAG" ""; then
  echo "Autostart after reboot: off."
fi

echo "Stopped. Start again with: bash BVT-start.sh"
