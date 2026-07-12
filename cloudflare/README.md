# Telegram Bot API proxy (Cloudflare Worker)

Because `api.telegram.org` is filtered on some servers, the Telegram bot can
route its API calls through a Cloudflare Worker deployed on a subdomain of your
own domain (examples below use `example.com`). Cloudflare's edge reaches
Telegram directly, so no VPN/proxy is needed on the server.

```
bot (server)  ->  https://tgapi.example.com/bot...   ->  Worker (Cloudflare edge)  ->  https://api.telegram.org/bot...
```

## Deploy — Dashboard (no tooling needed)

1. Cloudflare dashboard → **Workers & Pages** → **Create** → **Create Worker**.
2. Name it `enamad-telegram-proxy`, click **Deploy**, then **Edit code**.
3. Paste the contents of [`worker.js`](./worker.js) and **Deploy**.
4. Open the Worker → **Settings** → **Domains & Routes** → **Add** → **Custom Domain**
   → enter `tgapi.example.com` → **Add domain**. Cloudflare creates the DNS
   record automatically (proxied).
5. Verify: opening `https://tgapi.example.com/` returns `ok`.

## Deploy — one command with an API token (recommended)

`deploy.sh` uses the Cloudflare REST API directly (only needs `curl` + `jq`, no
Node/wrangler). It uploads the Worker, creates the `tgapi.<your-domain>` custom
domain, verifies it, and can also reconfigure the bot on the server.

Create an API token (Cloudflare → My Profile → API Tokens → "Edit Cloudflare
Workers" template, scoped to your domain's zone), then set your domain and run:

```bash
export CLOUDFLARE_API_TOKEN=xxxxxxxx
export ZONE_DOMAIN=example.com
export WORKER_HOSTNAME=tgapi.example.com
bash cloudflare/deploy.sh                 # deploy Worker + custom domain only
bash cloudflare/deploy.sh --configure-bot # + set TELEGRAM_API_BASE_URL and restart the bot
```

Required token permissions: **Account → Workers Scripts: Edit**,
**Zone → Workers Routes: Edit**, **Zone → Zone: Read**.

## Deploy — Wrangler (alternative)

```bash
npm i -g wrangler
cd cloudflare
wrangler login          # or: export CLOUDFLARE_API_TOKEN=...
wrangler deploy
```

## Point the bot at the Worker

On the server, in `/srv/enamad/.env`:

```
TELEGRAM_API_BASE_URL=https://tgapi.example.com/bot
```

(Remove any `TELEGRAM_PROXY=...` line — the proxy is no longer used.)

Then restart the Telegram bot:

```bash
cd /srv/enamad && docker compose up -d --force-recreate bot
```

The bot derives the file endpoint (`/file/bot`) automatically from this base URL.
