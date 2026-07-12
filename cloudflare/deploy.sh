#!/bin/bash
# Deploy the Telegram Bot API proxy Worker to Cloudflare using the REST API.
#
# Usage:
#   export CLOUDFLARE_API_TOKEN=xxxxxxxx
#   bash cloudflare/deploy.sh                 # deploy worker + custom domain
#   bash cloudflare/deploy.sh --configure-bot # also point /srv/enamad bot at it
#
# Required API token permissions:
#   - Account  > Workers Scripts       : Edit
#   - Zone     > Workers Routes        : Edit
#   - Zone     > Zone                  : Read
#   (a token created from the "Edit Cloudflare Workers" template works)
#
# Optional env overrides:
#   WORKER_NAME (default enamad-telegram-proxy)
#   ZONE_DOMAIN (default paanapay.com)
#   WORKER_HOSTNAME (default tgapi.paanapay.com)
#   CLOUDFLARE_ACCOUNT_ID (skip account lookup)
#   ENAMAD_ENV (default /srv/enamad/.env)
set -euo pipefail

WORKER_NAME="${WORKER_NAME:-enamad-telegram-proxy}"
ZONE_DOMAIN="${ZONE_DOMAIN:-paanapay.com}"
WORKER_HOSTNAME="${WORKER_HOSTNAME:-tgapi.paanapay.com}"
COMPAT_DATE="2024-11-01"
API="https://api.cloudflare.com/client/v4"
HERE="$(cd "$(dirname "$0")" && pwd)"
WORKER_FILE="${WORKER_FILE:-$HERE/worker.js}"

: "${CLOUDFLARE_API_TOKEN:?Set CLOUDFLARE_API_TOKEN}"
[ -f "$WORKER_FILE" ] || { echo "worker file not found: $WORKER_FILE"; exit 1; }
AUTH=(-H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}")

if ! command -v jq >/dev/null 2>&1; then
  echo "installing jq..."
  apt-get update -qq && apt-get install -y -qq jq
fi

die() { echo "ERROR: $1"; echo "${2:-}" | jq . 2>/dev/null || echo "${2:-}"; exit 1; }

# 1) account id
account_id="${CLOUDFLARE_ACCOUNT_ID:-}"
if [ -z "$account_id" ]; then
  acct_json=$(curl -s "${AUTH[@]}" "$API/accounts")
  account_id=$(echo "$acct_json" | jq -r '.result[0].id // empty')
  [ -n "$account_id" ] || die "could not read account id (check token permissions)" "$acct_json"
fi
echo "account_id=$account_id"

# 2) upload worker (ES module)
metadata='{"main_module":"worker.js","compatibility_date":"'"$COMPAT_DATE"'"}'
up_json=$(curl -s -X PUT "${AUTH[@]}" \
  "$API/accounts/$account_id/workers/scripts/$WORKER_NAME" \
  -F "metadata=$metadata;type=application/json" \
  -F "worker.js=@$WORKER_FILE;type=application/javascript+module")
[ "$(echo "$up_json" | jq -r '.success')" = "true" ] || die "worker upload failed" "$up_json"
echo "worker uploaded: $WORKER_NAME"

# 3) zone id
zone_json=$(curl -s "${AUTH[@]}" "$API/zones?name=$ZONE_DOMAIN")
zone_id=$(echo "$zone_json" | jq -r '.result[0].id // empty')
[ -n "$zone_id" ] || die "could not find zone $ZONE_DOMAIN" "$zone_json"
echo "zone_id=$zone_id"

# 4) attach custom domain (idempotent)
dom_json=$(curl -s -X PUT "${AUTH[@]}" -H "Content-Type: application/json" \
  "$API/accounts/$account_id/workers/domains" \
  --data '{"environment":"production","hostname":"'"$WORKER_HOSTNAME"'","service":"'"$WORKER_NAME"'","zone_id":"'"$zone_id"'"}')
if [ "$(echo "$dom_json" | jq -r '.success')" = "true" ]; then
  echo "custom domain attached: $WORKER_HOSTNAME"
else
  echo "custom domain note: $(echo "$dom_json" | jq -c '.errors // .messages')"
fi

# 5) verify
echo "waiting for propagation..."
sleep 10
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 25 "https://$WORKER_HOSTNAME/" || echo 000)
echo "https://$WORKER_HOSTNAME/ => $code"
[ "$code" = "200" ] && echo ">>> Worker is live <<<" || echo "(if not 200 yet, give DNS/SSL a minute and retry the curl)"

# 6) optionally point the bot at the worker
if [ "${1:-}" = "--configure-bot" ]; then
  ENV_FILE="${ENAMAD_ENV:-/srv/enamad/.env}"
  if [ -f "$ENV_FILE" ]; then
    sed -i '/^TELEGRAM_API_BASE_URL=/d; /^TELEGRAM_PROXY=/d' "$ENV_FILE"
    echo "TELEGRAM_API_BASE_URL=https://$WORKER_HOSTNAME/bot" >> "$ENV_FILE"
    echo "updated $ENV_FILE -> TELEGRAM_API_BASE_URL=https://$WORKER_HOSTNAME/bot (removed TELEGRAM_PROXY)"
    ( cd "$(dirname "$ENV_FILE")" && docker compose up -d --force-recreate bot )
    echo "telegram bot recreated"
  else
    echo "env file $ENV_FILE not found; skipping bot config"
  fi
fi

echo "done."
