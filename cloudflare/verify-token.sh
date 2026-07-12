#!/bin/bash
T="${CLOUDFLARE_API_TOKEN:?set token}"
ACC="${CLOUDFLARE_ACCOUNT_ID:?set account id}"
A=(-H "Authorization: Bearer $T")
echo "=== token verify ==="
curl -s "${A[@]}" https://api.cloudflare.com/client/v4/user/tokens/verify | jq .
echo "=== accounts (account-level access?) ==="
curl -s "${A[@]}" https://api.cloudflare.com/client/v4/accounts | jq '.success, [.result[]?|{id,name}]'
ZONE_DOMAIN="${ZONE_DOMAIN:-example.com}"
echo "=== zone $ZONE_DOMAIN ==="
curl -s "${A[@]}" "https://api.cloudflare.com/client/v4/zones?name=$ZONE_DOMAIN" | jq '.success, (.result[0].id // "none")'
echo "=== list workers scripts (needs Workers Scripts:Read/Edit) ==="
curl -s "${A[@]}" "https://api.cloudflare.com/client/v4/accounts/$ACC/workers/scripts" | jq '.success, (.errors // [])'
