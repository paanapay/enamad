#!/bin/bash
set -euo pipefail

APP_DIR=/srv/enamad
ARCHIVE=/tmp/enamad-migration.tar.gz

read_env() {
  local key="$1"
  grep -E "^${key}=" "$APP_DIR/.env" | head -1 | cut -d= -f2- | tr -d '\r'
}

if [ ! -f "$ARCHIVE" ]; then
  echo "Missing $ARCHIVE" >&2
  exit 1
fi

rm -rf /tmp/enamad-migration
tar -xzf "$ARCHIVE" -C /tmp

mkdir -p "$APP_DIR"
if [ ! -d "$APP_DIR/.git" ]; then
  git clone https://github.com/paanapay/enamad.git "$APP_DIR"
fi

cp /tmp/enamad-migration/.env "$APP_DIR/.env"
if [ -f /tmp/enamad-migration/config.ini ]; then
  cp /tmp/enamad-migration/config.ini "$APP_DIR/config.ini"
fi

cd "$APP_DIR"

docker compose up -d mysql
echo "Waiting for MySQL..."
for i in $(seq 1 60); do
  if docker compose exec -T mysql healthcheck.sh --connect --innodb_initialized >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

MYSQL_PASSWORD=$(read_env MYSQL_PASSWORD)
MYSQL_DATABASE=$(read_env MYSQL_DATABASE)
MYSQL_DATABASE=${MYSQL_DATABASE:-enamad}

echo "Importing SQL dump..."
docker compose exec -T mysql mariadb -uroot -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE" < /tmp/enamad-migration/enamad.sql

SERVICES=(bot scheduler)
if grep -qE '^BALE_BOT_TOKEN=.+$' .env 2>/dev/null; then
  SERVICES+=(bale-bot)
fi

docker compose up -d --build "${SERVICES[@]}"
docker compose ps
