#!/usr/bin/env bash
set -euo pipefail
# Run from the repo root. Keep encrypted copies off the VPS.
BACKUP_DIR="${BACKUP_DIR:-$PWD/var/backups}"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
umask 077
file="$BACKUP_DIR/opsverse-$(date -u +%Y%m%dT%H%M%SZ).dump"
partial="$file.partial"
trap 'rm -f "$partial"' EXIT
docker compose -f docker-compose.yml -f deploy/compose.live.yml exec -T db \
  pg_dump -U opsverse -d opsverse -Fc > "$partial"
docker compose -f docker-compose.yml -f deploy/compose.live.yml exec -T db \
  pg_restore -l < "$partial" > /dev/null
mv "$partial" "$file"
trap - EXIT
printf 'Backup written: %s\n' "$file"
