#!/bin/bash
# One-shot: pull latest, ensure web-panel env vars, build & start the web service.
set -euo pipefail
cd /srv/enamad

echo "=== discard local edits to tracked files, pull ==="
git checkout -- bot_ui.py telegram_bot.py 2>/dev/null || true
git pull origin master

echo "=== ensure web env in .env ==="
if ! grep -q '^WEB_ADMIN_PASSWORD=' .env; then
  PW=$(openssl rand -base64 12 | tr -dc 'A-Za-z0-9' | cut -c1-14)
  echo "WEB_ADMIN_PASSWORD=$PW" >> .env
fi
if ! grep -q '^WEB_SECRET_KEY=' .env; then
  echo "WEB_SECRET_KEY=$(openssl rand -hex 32)" >> .env
fi
grep -q '^WEB_LIVE_SEARCH=' .env || echo 'WEB_LIVE_SEARCH=yes' >> .env

echo "=== build web image ==="
docker compose build web

echo "=== start web service ==="
docker compose up -d web

sleep 4
echo "=== health check ==="
curl -s -o /dev/null -w 'local_health=%{http_code}\n' --max-time 10 http://127.0.0.1:8095/healthz || true
echo "=== web env (password shown once) ==="
grep '^WEB_ADMIN_PASSWORD=' .env
docker compose ps web
