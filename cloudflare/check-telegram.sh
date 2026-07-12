#!/bin/bash
ENV_FILE="${ENAMAD_ENV:-/srv/enamad/.env}"
HOSTN="${WORKER_HOSTNAME:-tgapi.paanapay.com}"
TOK=$(grep -E '^(TELEGRAM_BOT_TOKEN|BOT_TOKEN)=' "$ENV_FILE" | head -1 | cut -d= -f2-)
if [ -z "$TOK" ]; then echo "no telegram token in $ENV_FILE"; exit 1; fi
echo "=== getMe via worker ==="
curl -s --max-time 20 "https://$HOSTN/bot$TOK/getMe" | jq '{ok, id: .result.id, username: .result.username}'
echo "=== recent bot logs ==="
docker logs --tail 40 enamad-bot-1 2>&1 | tail -20
