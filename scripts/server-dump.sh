#!/bin/bash
# Dump the Enamad DB on the server to /tmp/enamad.sql (gzip: /tmp/enamad.sql.gz)
set -euo pipefail
cd /srv/enamad
PW=$(grep -E '^MYSQL_PASSWORD=' .env | head -1 | cut -d= -f2- | tr -d '\r')
DB=$(grep -E '^MYSQL_DATABASE=' .env | head -1 | cut -d= -f2- | tr -d '\r')
DB=${DB:-enamad}
echo "database=$DB"
docker exec enamad-mysql-1 mariadb -uroot -p"$PW" -N -e \
  "SELECT CONCAT('domains=', COUNT(*)) FROM enamad_domains;
   SELECT CONCAT('services=', COUNT(*)) FROM enamad_domain_services;
   SELECT CONCAT('bot_users=', COUNT(*)) FROM bot_users;" "$DB"
echo "dumping..."
docker exec enamad-mysql-1 mariadb-dump -uroot -p"$PW" \
  --single-transaction --routines --triggers "$DB" > /tmp/enamad.sql
gzip -f /tmp/enamad.sql
echo "done: /tmp/enamad.sql.gz ($(wc -c < /tmp/enamad.sql.gz) bytes)"
