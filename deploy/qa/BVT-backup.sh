#!/usr/bin/env bash
# BVT Grama Connect qa server: back up the data and the uploaded files. No root or sudo needed.
#
#   bash BVT-backup.sh              one backup now
#   bash BVT-backup.sh --install    also every night at 02:30 (this account's crontab)
#   bash BVT-backup.sh --uninstall  stop the nightly backup
#
# Each backup is a folder in ~/bvt-backups (readable by this account only):
#   data.json.gz   every data table, the same dump as a server copy (deploy/DATA_MOVE.md); it
#                  holds password hashes and personal data, so it never leaves the server
#   media.tgz      photos, videos, logos and banners
# The newest KEEP backups are kept (default 7). Django's own dump: no database password needed,
# and it restores into MySQL or SQLite alike (deploy/qa/README.md, "Restoring a backup").
set -euo pipefail
# shellcheck source=qa-lib.sh
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/qa-lib.sh"

BACKUP_ROOT="${BACKUP_ROOT:-$HOME/bvt-backups}"
KEEP="${KEEP:-7}"

case "${1:-}" in
  --install)
    mkdir -p "$LOG_DIR"
    set_cron_line "$BACKUP_TAG" "30 2 * * * bash $(readlink -f "${BASH_SOURCE[0]}") >>$LOG_DIR/backup.log 2>&1 $BACKUP_TAG"
    echo "Nightly backup at 02:30: on. Log: $LOG_DIR/backup.log"
    ;;
  --uninstall)
    set_cron_line "$BACKUP_TAG" ""
    echo "Nightly backup: off."
    exit 0
    ;;
  "") ;;
  *) echo "Usage: bash BVT-backup.sh [--install|--uninstall]" >&2; exit 2 ;;
esac

[[ -x "$PYTHON" ]] || fail "No virtual environment at $BACKEND_DIR/.venv. Run BVT-deploy.sh first."
[[ "$KEEP" =~ ^[1-9][0-9]*$ ]] || fail "KEEP must be a number of backups, not '$KEEP'."

umask 077
mkdir -p "$BACKUP_ROOT"
chmod 700 "$BACKUP_ROOT"
target="$BACKUP_ROOT/$(date '+%Y%m%d-%H%M%S')"
partial="$target.partial"
mkdir "$partial"

echo "=== $(date '+%Y-%m-%d %H:%M:%S') backup to $target"
cd "$BACKEND_DIR"
"$PYTHON" -X utf8 manage.py dumpdata --natural-foreign --natural-primary \
  --exclude contenttypes --exclude auth.permission --exclude sessions --exclude admin.logentry \
  --exclude django_q --exclude token_blacklist --exclude axes --exclude web_admin.ConsoleAccess \
  | gzip -9 >"$partial/data.json.gz"
if [[ -d media ]]; then
  tar -czf "$partial/media.tgz" media
fi
# Only a finished backup gets its real name, so a failed one is never taken for a good one.
mv "$partial" "$target"
du -sh "$target" | awk '{print "Backup done: " $1}'

# Keep the newest KEEP finished backups; leftovers of failed runs go too.
rm -rf "$BACKUP_ROOT"/*.partial 2>/dev/null || true
ls -1d "$BACKUP_ROOT"/2*/ 2>/dev/null | sort -r | tail -n +"$((KEEP + 1))" | xargs -r rm -rf
echo "Kept: $(ls -1d "$BACKUP_ROOT"/2*/ 2>/dev/null | wc -l) backup(s) in $BACKUP_ROOT"
