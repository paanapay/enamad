#!/bin/bash
set -euo pipefail
cd /var/www/enamad

read_env() {
  local key="$1"
  grep -E "^${key}=" .env | head -1 | cut -d= -f2- | tr -d '\r'
}

MYSQL_PASSWORD=$(read_env MYSQL_PASSWORD)
MYSQL_DATABASE=$(read_env MYSQL_DATABASE)
MYSQL_DATABASE=${MYSQL_DATABASE:-enamad}

OUT=/tmp/enamad-migration
rm -rf "$OUT"
mkdir -p "$OUT"

cp .env "$OUT/"
if [ -f config.ini ]; then
  cp config.ini "$OUT/"
fi

echo "Dumping database..."
docker exec enamad-mysql-1 mariadb-dump \
  -uroot -p"$MYSQL_PASSWORD" \
  --single-transaction \
  --routines \
  --triggers \
  "$MYSQL_DATABASE" > "$OUT/enamad.sql"

BYTES=$(wc -c < "$OUT/enamad.sql")
echo "SQL dump bytes: $BYTES"

tar -czf /tmp/enamad-migration.tar.gz -C /tmp enamad-migration
echo "Archive: /tmp/enamad-migration.tar.gz ($(wc -c < /tmp/enamad-migration.tar.gz) bytes)"
