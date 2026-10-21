#!/usr/bin/env bash
# BVT Grama Connect: restore a backup made by backup.sh.
#
#   sudo bash deploy/restore.sh [--db-only | --media-only] [--yes] BACKUP
#
# BACKUP is a local .tar.gpg file, or just its file name, which is then taken
# from /var/backups/gramaconnect or fetched from BACKUP_TARGET.
#
# This REPLACES the live database and/or /var/media. The current media folder
# is kept as /var/media.before-restore-<time>. Take a fresh backup first if the
# current data might still be needed: sudo bash deploy/backup.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$HERE/lib.sh"
require_root
load_config "$GC_DEPLOY_ENV"

DO_DB=1
DO_MEDIA=1
ASSUME_YES=0
BACKUP=""
while (( $# )); do
  case "$1" in
    --db-only) DO_MEDIA=0 ;;
    --media-only) DO_DB=0 ;;
    --yes) ASSUME_YES=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    -*) die "Unknown option: $1" ;;
    *) BACKUP="$1" ;;
  esac
  shift
done
[[ -n "$BACKUP" ]] || die "Name the backup to restore. Latest local backups: $(find "$GC_BACKUP_DIR" -maxdepth 1 -name 'gramaconnect-*.tar.gpg' -printf '%f ' 2>/dev/null | tr ' ' '\n' | sort | tail -n 5 | tr '\n' ' ')"

WORK="$(mktemp -d /var/tmp/gramaconnect-restore.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

# --- Find the file ------------------------------------------------------------------------

if [[ -f "$BACKUP" ]]; then
  FILE="$BACKUP"
elif [[ -f "$GC_BACKUP_DIR/$BACKUP" ]]; then
  FILE="$GC_BACKUP_DIR/$BACKUP"
elif backup_target_configured; then
  kind="${BACKUP_TARGET%%:*}"
  dest="${BACKUP_TARGET#*:}"
  log "Fetching $BACKUP from $BACKUP_TARGET"
  case "$kind" in
    rclone) rclone copy "$dest/$BACKUP" "$WORK/" ;;
    rsync) rsync -a -e "ssh -o BatchMode=yes" "$dest/$BACKUP" "$WORK/" ;;
    local) cp "$dest/$BACKUP" "$WORK/" ;;
    *) die "Unknown BACKUP_TARGET kind: $kind" ;;
  esac
  FILE="$WORK/$BACKUP"
else
  die "Backup not found: $BACKUP"
fi
[[ -s "$FILE" ]] || die "Backup file is empty or missing: $FILE"

log "Decrypting $FILE"
mkdir -p "$WORK/backup"
gpg --batch --quiet --pinentry-mode loopback --passphrase-file "$BACKUP_PASSPHRASE_FILE" -d "$FILE" \
  | tar -C "$WORK/backup" -xf -
(cd "$WORK/backup" && sha256sum -c --quiet SHA256SUMS) || die "Checksums do not match; the backup is damaged."
created="" db_engine="" db_name="" dump="" domain=""
# shellcheck disable=SC1090
source <(grep -E '^(created|db_engine|db_name|dump|domain)=' "$WORK/backup/MANIFEST")
[[ -n "$dump" && -f "$WORK/backup/$dump" ]] || die "The backup has no database dump."
[[ "${db_engine:-}" == "$DB_ENGINE" ]] || die "This backup is from a $db_engine database; this server uses $DB_ENGINE."

echo
echo "  Backup:   $(basename "$FILE")  (made $created on $domain from database $db_name)"
echo "  Restores: $( (( DO_DB )) && echo "database $DB_NAME ")$( (( DO_MEDIA )) && echo "media $GC_MEDIA")"
echo "  The current data will be REPLACED."
if (( ! ASSUME_YES )); then
  read -r -p "  Type the domain name ($DOMAIN) to continue: " answer
  [[ "$answer" == "$DOMAIN" ]] || die "Cancelled."
fi

log "Stopping the app"
systemctl stop gramaconnect-gunicorn.service gramaconnect-qcluster.service

restart_app() {
  systemctl start gramaconnect-gunicorn.service gramaconnect-qcluster.service
}
trap 'restart_app; rm -rf "$WORK"' EXIT

# --- Database ----------------------------------------------------------------------------------

if (( DO_DB )); then
  log "Restoring database $DB_NAME"
  if [[ "$DB_ENGINE" == "mysql" ]]; then
    mysql --protocol=socket -uroot -e "DROP DATABASE IF EXISTS \`$DB_NAME\`; CREATE DATABASE \`$DB_NAME\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
    mysql --defaults-extra-file="$GC_ETC/mysql-backup.cnf" --default-character-set=utf8mb4 "$DB_NAME" \
      < "$WORK/backup/$dump"
  else
    sudo -u postgres dropdb --if-exists "$DB_NAME"
    sudo -u postgres createdb -E UTF8 -T template0 --locale=C.UTF-8 -O "$DB_USER" "$DB_NAME"
    sudo -u postgres psql -v ON_ERROR_STOP=1 -q -d "$DB_NAME" \
      -c "REVOKE ALL ON DATABASE \"$DB_NAME\" FROM PUBLIC; GRANT CONNECT, TEMPORARY ON DATABASE \"$DB_NAME\" TO \"$DB_USER\"; ALTER SCHEMA public OWNER TO \"$DB_USER\";"
    # Objects are created as the app's role so it owns them, as after migrate.
    cp "$WORK/backup/$dump" "$WORK/pg.dump"
    chmod 644 "$WORK/pg.dump"
    chmod 755 "$WORK"
    sudo -u postgres pg_restore --no-owner --role="$DB_USER" --exit-on-error -d "$DB_NAME" "$WORK/pg.dump"
  fi
fi

# --- Media --------------------------------------------------------------------------------------------

if (( DO_MEDIA )); then
  log "Restoring $GC_MEDIA"
  if [[ -d "$GC_MEDIA" ]]; then
    mv "$GC_MEDIA" "$GC_MEDIA.before-restore-$(date +%Y%m%d-%H%M%S)"
  fi
  tar -C "$(dirname "$GC_MEDIA")" -xzf "$WORK/backup/media.tar.gz"
  chown -R "$APP_USER:www-data" "$GC_MEDIA"
  find "$GC_MEDIA" -type d -exec chmod 2750 {} +
  find "$GC_MEDIA" -type f -exec chmod 640 {} +
fi

log "Starting the app"
restart_app
trap 'rm -rf "$WORK"' EXIT
sleep 3
if curl -fsS --unix-socket "$GC_SOCKET" -H "Host: $DOMAIN" -H "X-Forwarded-Proto: https" http://localhost/api/v1/health/ >/dev/null; then
  log "Restore finished and the API answers."
else
  warn "Restore finished but the API does not answer yet: journalctl -u gramaconnect-gunicorn -n 100"
fi
