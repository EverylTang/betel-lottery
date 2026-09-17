#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <backup-directory>" >&2
  exit 2
fi

backup_dir=$1
timestamp=$(date +%Y%m%d-%H%M%S)
mkdir -p "$backup_dir"

: "${MYSQL_HOST:?Set MYSQL_HOST}"
: "${MYSQL_PORT:=3306}"
: "${MYSQL_DATABASE:=betel_lottery}"
: "${MYSQL_USER:?Set MYSQL_USER}"
: "${MYSQL_PASSWORD:?Set MYSQL_PASSWORD}"

output="$backup_dir/${MYSQL_DATABASE}-${timestamp}.sql.gz"
MYSQL_PWD="$MYSQL_PASSWORD" mysqldump \
  --host="$MYSQL_HOST" \
  --port="$MYSQL_PORT" \
  --user="$MYSQL_USER" \
  --single-transaction \
  --routines \
  --triggers \
  --set-gtid-purged=OFF \
  "$MYSQL_DATABASE" | gzip > "$output"

gzip -t "$output"
echo "Backup verified: $output"
