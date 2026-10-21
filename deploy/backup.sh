#!/usr/bin/env bash
# BVT Grama Connect: encrypted backup of the database and uploaded media.
# Runs nightly from gramaconnect-backup.timer; run by hand with:
#
#   sudo bash /srv/gramaconnect/backend/deploy/backup.sh
#
# Writes /var/backups/gramaconnect/gramaconnect-YYYYmmdd-HHMMSS.tar.gpg
# (database dump + /var/media, encrypted with the passphrase file), copies it
# to BACKUP_TARGET and deletes copies older than BACKUP_RETENTION_DAYS in both places.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$HERE/lib.sh"
require_root
load_config "$GC_DEPLOY_ENV"

[[ -s "$BACKUP_PASSPHRASE_FILE" ]] || die "Backup passphrase file $BACKUP_PASSPHRASE_FILE is missing."
install -d -m 700 "$GC_BACKUP_DIR"

STAMP="$(date +%Y%m%d-%H%M%S)"
NAME="gramaconnect-$STAMP.tar.gpg"
OUT="$GC_BACKUP_DIR/$NAME"
WORK="$(mktemp -d /var/tmp/gramaconnect-backup.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

log "Dumping the $DB_ENGINE database $DB_NAME"
if [[ "$DB_ENGINE" == "mysql" ]]; then
  mysqldump --defaults-extra-file="$GC_ETC/mysql-backup.cnf" \
    --single-transaction --quick --routines --triggers --no-tablespaces \
    --default-character-set=utf8mb4 --set-gtid-purged=OFF \
    "$DB_NAME" > "$WORK/database.sql"
  DUMP_FILE=database.sql
else
  # The redirect runs as root on purpose: postgres cannot write to the root-only work dir.
  # shellcheck disable=SC2024
  sudo -u postgres pg_dump --format=custom --no-owner --dbname="$DB_NAME" > "$WORK/database.dump"
  DUMP_FILE=database.dump
fi
[[ -s "$WORK/$DUMP_FILE" ]] || die "The database dump is empty."

log "Archiving $GC_MEDIA"
tar -C "$(dirname "$GC_MEDIA")" -czf "$WORK/media.tar.gz" "$(basename "$GC_MEDIA")"

cat > "$WORK/MANIFEST" <<EOF
created=$(date -Iseconds)
host=$(hostname -f 2>/dev/null || hostname)
domain=$DOMAIN
db_engine=$DB_ENGINE
db_name=$DB_NAME
dump=$DUMP_FILE
media=media.tar.gz
backend_commit=$(git -C "$BACKEND_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)
EOF
(cd "$WORK" && sha256sum "$DUMP_FILE" media.tar.gz > SHA256SUMS)

log "Encrypting to $OUT"
tar -C "$WORK" -cf - MANIFEST SHA256SUMS "$DUMP_FILE" media.tar.gz \
  | gpg --batch --yes --quiet --pinentry-mode loopback --passphrase-file "$BACKUP_PASSPHRASE_FILE" \
        --symmetric --cipher-algo AES256 --compress-algo none -o "$OUT.partial"
mv "$OUT.partial" "$OUT"
chmod 600 "$OUT"

# --- Off-server copy --------------------------------------------------------------------

RETENTION="$BACKUP_RETENTION_DAYS"
if backup_target_configured; then
  kind="${BACKUP_TARGET%%:*}"
  dest="${BACKUP_TARGET#*:}"
  case "$kind" in
    rclone)
      log "Uploading with rclone to $dest"
      rclone copy "$OUT" "$dest" --quiet
      rclone delete "$dest" --min-age "${RETENTION}d" --include 'gramaconnect-*.tar.gpg' --quiet
      ;;
    rsync)
      log "Uploading with rsync to $dest"
      remote_host="${dest%%:*}"
      remote_dir="${dest#*:}"
      ssh -o BatchMode=yes "$remote_host" "mkdir -p '$remote_dir'"
      rsync -a -e "ssh -o BatchMode=yes" "$OUT" "$remote_host:$remote_dir/"
      ssh -o BatchMode=yes "$remote_host" \
        "find '$remote_dir' -maxdepth 1 -name 'gramaconnect-*.tar.gpg' -mtime +$((RETENTION - 1)) -delete"
      ;;
    local)
      log "Copying to $dest"
      install -d -m 700 "$dest"
      cp "$OUT" "$dest/"
      find "$dest" -maxdepth 1 -name 'gramaconnect-*.tar.gpg' -mtime +$((RETENTION - 1)) -delete
      ;;
    *)
      die "BACKUP_TARGET must start with rclone:, rsync: or local: (got '$BACKUP_TARGET'). The local copy is kept."
      ;;
  esac
else
  warn "BACKUP_TARGET is not set; the backup stays on this server only."
fi

find "$GC_BACKUP_DIR" -maxdepth 1 -name 'gramaconnect-*.tar.gpg' -mtime +$((RETENTION - 1)) -delete
log "Backup done: $OUT ($(du -h "$OUT" | cut -f1))"
