#!/bin/bash
# Create/ensure a DNS A record for a subdomain of paanapay.com on Cloudflare.
# Usage: CLOUDFLARE_API_TOKEN=... SUBDOMAIN=enamad.paanapay.com IP=45.149.76.142 PROXIED=false bash dns-setup.sh
set -euo pipefail
T="${CLOUDFLARE_API_TOKEN:?set token}"
ZONE_DOMAIN="${ZONE_DOMAIN:-paanapay.com}"
SUB="${SUBDOMAIN:?set SUBDOMAIN}"
IP="${IP:?set IP}"
PROXIED="${PROXIED:-false}"
API="https://api.cloudflare.com/client/v4"
A=(-H "Authorization: Bearer $T" -H "Content-Type: application/json")

zone_id=$(curl -s "${A[@]}" "$API/zones?name=$ZONE_DOMAIN" | jq -r '.result[0].id // empty')
[ -n "$zone_id" ] || { echo "zone not found"; exit 1; }
echo "zone_id=$zone_id"

rec_id=$(curl -s "${A[@]}" "$API/zones/$zone_id/dns_records?name=$SUB&type=A" | jq -r '.result[0].id // empty')
body=$(jq -nc --arg n "$SUB" --arg c "$IP" --argjson p "$PROXIED" \
  '{type:"A",name:$n,content:$c,ttl:1,proxied:$p}')

if [ -n "$rec_id" ]; then
  echo "updating existing record $rec_id"
  curl -s -X PUT "${A[@]}" "$API/zones/$zone_id/dns_records/$rec_id" --data "$body" \
    | jq '{success, name: .result.name, content: .result.content, proxied: .result.proxied}'
else
  echo "creating record"
  curl -s -X POST "${A[@]}" "$API/zones/$zone_id/dns_records" --data "$body" \
    | jq '{success, name: .result.name, content: .result.content, proxied: .result.proxied, errors}'
fi
