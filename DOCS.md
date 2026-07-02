# Enamad Scraper — Full Documentation

Detailed reference for the scraper, updater, scheduler, Telegram bot, and Docker
setup. For a quick start, see [README.md](README.md).

---

## Project layout

```
enamad/
├── extract_enamad.py   # Scraper + updater CLI
├── telegram_bot.py     # Telegram bot
├── scheduler.py        # Recurring update/refresh scheduler (APScheduler)
├── db.py               # MySQL helpers (config via file or ENV)
├── console_ui.py       # Live console / parallel dashboard
├── bot_ui.py           # Bot text + keyboards
├── bot_queries.py      # Bot DB queries
├── schema.sql          # Database schema
├── config.example.ini  # Config template
├── Dockerfile
├── docker-compose.yml
├── .env.example        # Docker env template
├── requirements.txt
├── README.md           # Quick start
└── DOCS.md             # This file
```

---

## Configuration

Copy `config.example.ini` to `config.ini` and edit:

```ini
[mysql]
host = 127.0.0.1
port = 3306
user = root
password =
database = enamad

[scraper]
delay = 0            ; seconds between captcha chunks (0 = fastest)
retries = 5          ; captcha retries per page

[telegram]
bot_token = 123456:ABC...
allowed_users =      ; comma-separated user IDs (empty = public bot)
admin_users =        ; admin user IDs: user list + admin panel
live_search = yes    ; query enamad.ir live if a domain isn't in the DB
proxy =              ; http://host:port or socks5://host:port (leave empty if not needed)
connect_timeout = 30
read_timeout = 30

[scheduler]
timezone = Asia/Tehran
update_cron = 0 3 * * *      ; new domains daily at 03:00
update_overlap = 5
update_workers = 1
update_chunk_pages = 10
refresh_cron = 0 */6 * * *   ; refresh stale domains every 6 hours
refresh_days = 30
refresh_limit = 500
run_on_start = no
enable_update = yes
enable_refresh = yes
```

Every value can also be provided via environment variables (used by Docker):
`MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE`,
`SCRAPER_DELAY`, `SCRAPER_RETRIES`, `BOT_TOKEN`, `TELEGRAM_ALLOWED_USERS`,
`TELEGRAM_ADMIN_USERS`, `TELEGRAM_LIVE_SEARCH`, `TELEGRAM_PROXY`,
`SCHED_UPDATE_CRON`, `SCHED_REFRESH_CRON`, `SCHED_REFRESH_DAYS`,
`SCHED_REFRESH_LIMIT`, `SCHED_RUN_ON_START`, `TZ`.

---

## CLI reference (`extract_enamad.py`)

| Option | Description |
|--------|-------------|
| `--init-db` | Create database and tables, then exit |
| `--all` | Fetch every page until the end (auto-resume) |
| `--reset` | With `--all`: clear saved progress, start from page 1 |
| `--pages N` | Number of pages to fetch (default 1, ignored with `--all`) |
| `--start-page N` | Start from page N (overrides auto-resume) |
| `--end-page N` | Stop after page N (bounded / parallel runs) |
| `--workers N` | Parallel page-range workers (needs `--all` or `--end-page`) |
| `--chunk-pages N` | Pages reused per captcha (default 5) |
| `--delay SEC` | Pause between chunks (overrides config) |
| `--retries N` | Max captcha attempts per page |
| `--fast-ocr` | Lighter OCR — faster but less accurate |
| `--manual` | Type captcha manually (single-worker only) |
| `--debug` | Save captcha images to `debug_captcha/` |
| `--update` | Incremental: fetch only newly-added tail pages |
| `--update-overlap N` | With `--update`: re-scan N pages before the old total (default 5) |
| `--refresh-stale` | Refresh existing domains via trust seal (no captcha) |
| `--stale-days N` | With `--refresh-stale`: refresh domains older than N days (default 30; `0` = all) |
| `--refresh-workers N` | With `--refresh-stale`: parallel worker threads (default 1). No captcha, so scales well |
| `--refresh-services [DOMAIN]` | Re-fetch trust seal + all licenses (one domain, or all) |
| `--refresh-limit N` | Cap domains per refresh run |
| `--search DOMAIN` | Look up one domain via `/Home/GetData` |
| `--search-file FILE` | Look up many domains from a text file |
| `--no-save` | With search: don't write to MySQL |
| `--json` | With search: print JSON |
| `--fix-domains` | Decode URL-encoded domains stored in MySQL |
| `--config FILE` | Config file path (default `config.ini`) |

### Common examples

```bash
# full scrape, 4 parallel workers, resumable
python extract_enamad.py --all --workers 4 --chunk-pages 10

# resume after Ctrl+C / crash
python extract_enamad.py --all --workers 4 --chunk-pages 10

# start over from page 1
python extract_enamad.py --all --reset

# look up a single domain (no DB write)
python extract_enamad.py --search digikala.com --no-save
```

---

## Keeping data fresh

A full scrape is expensive (captcha per page). Do it once, then:

### New domains — `--update`

Reads the current total page count (one captcha) and scrapes only the newest
pages plus a small overlap.

```bash
python extract_enamad.py --update
```

### Existing domains — `--refresh-stale` (no captcha)

Rating, expiry, address, phone, email and licenses come from the public
trust-seal page, which needs no captcha. Oldest `updated_at` is refreshed first,
so you can run it repeatedly to cycle through the whole DB.

```bash
# 500 domains older than 30 days
python extract_enamad.py --refresh-stale --stale-days 30 --refresh-limit 500

# ignore age — refresh everything (large!)
python extract_enamad.py --refresh-stale --stale-days 0 --refresh-limit 500000

# much faster with parallel workers (no captcha, so safe to raise)
python extract_enamad.py --refresh-stale --stale-days 0 --refresh-limit 500000 --refresh-workers 8 --delay 0

# one specific domain + its licenses
python extract_enamad.py --refresh-services digikala.com
```

> Note on ordering: the site list is **not** sorted by date. New approvals can
> appear near the front. `--update` targets the tail as a cheap best-effort; run
> a full `--all` periodically if you need guaranteed completeness.

---

## Scheduling (`scheduler.py`)

Cron-like scheduler (APScheduler) that runs `--update` and `--refresh-stale`
automatically. Portable across OSes and Docker; frequencies come from the
`[scheduler]` config section or `SCHED_*` env vars.

```bash
python scheduler.py
```

The process must stay running. In Docker it restarts automatically. On a bare
server, run it under a process manager (systemd, supervisor, `docker compose`,
etc.). Cron syntax is standard: `min hour day month weekday`.

---

## Docker

`docker-compose.yml` runs MySQL, the bot, and the scheduler. Config comes from
environment variables — no `config.ini` needed inside containers.

```bash
cp .env.example .env          # set MYSQL_PASSWORD, BOT_TOKEN, admin IDs, ...
docker compose up -d --build
```

| Service | Role |
|---------|------|
| `mysql` | Database with a persistent volume (`mysql_data`) |
| `init` | One-shot: creates the schema, then exits |
| `bot` | Telegram bot (always on) |
| `scheduler` | Recurring `--update` + `--refresh-stale` |

Run the initial full scrape once:

```bash
docker compose run --rm bot python extract_enamad.py --all --workers 4 --chunk-pages 10
```

> The scraper must reach `enamad.ir`. `TELEGRAM_PROXY` is only needed if
> `api.telegram.org` is blocked on the host; on a foreign server leave it empty.

---

## Telegram bot

### Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token.
2. Put it in `config.ini` (`[telegram] bot_token = ...`) or `BOT_TOKEN` env var.
3. Run `python telegram_bot.py`.

On startup the bot auto-registers its menu commands and description on Telegram.

### Features

| Feature | Description |
|---------|-------------|
| 🔍 Search | Domain, business name, or owner (local DB + optional live API) |
| 🆕 Latest | Domains in the same order as the enamad.ir site list |
| ⭐ Top rated | 4–5 star domains |
| 🗺 By province | Browse by province |

Commands: `/search`, `/latest`, `/top`, `/provinces`, `/help`.
Send any domain name as plain text to search directly.

### Users & admin

Every interaction is recorded in the `bot_users` table (id, name, username,
interaction count, first/last seen). Set `admin_users` to your Telegram numeric
ID (get it from [@userinfobot](https://t.me/userinfobot)).

| Role | Access |
|------|--------|
| Regular user | Search, lists (latest / top / province), help |
| **Admin** | The above + 📊 stats, 🛠 admin panel, and 👥 user list (`/users`, `/stats`) |

Admin-only actions are rejected for non-admins. If `allowed_users` is empty the
bot is public; admins always have access regardless of the allow-list.

### Polling vs webhook

The bot uses long polling — no public URL, HTTPS cert, or open port, and it
works behind NAT or a proxy. A webhook only helps at very high message volume
and needs a public HTTPS endpoint; it does **not** speed up Enamad scraping.
Stick with polling unless you hit real scale.

### Proxy notes

`proxy` accepts **HTTP** or **SOCKS5** only (e.g. `http://127.0.0.1:10809` or
`socks5://127.0.0.1:10808`). Telegram **MTProto** proxies are not supported —
they work only for client apps, not the HTTPS Bot API. On an unfiltered server,
leave `proxy` empty.

---

## Database schema

### `enamad_domains`

| Column | Description |
|--------|-------------|
| `enamad_id`, `code` | Enamad record ID + verification code (unique key) |
| `domain` | Domain name |
| `business_name`, `owner_name` | Persian business / owner name |
| `business_address`, `phone`, `email`, `work_hours` | Contact info (trust seal) |
| `province`, `city` | Location |
| `rating` | Star rating (0–5) |
| `approve_date`, `expire_date` | Issue / expiry dates |
| `trustseal_url` | Trust seal link |
| `source_page`, `source_row` | Position in the site list |
| `scrape_run_id` | FK to `scrape_runs` |

Duplicates are upserted (`ON DUPLICATE KEY UPDATE`).

### Other tables

- `enamad_domain_services` — licenses/services per domain
- `scrape_runs` — scrape session log
- `scraper_state` — progress (`last_completed_page`, `total_pages`, per-worker)
- `bot_users` — Telegram users and interaction counts

### Example queries

```sql
SELECT domain, business_name, province, city, rating
FROM enamad_domains
ORDER BY updated_at DESC
LIMIT 20;

SELECT status, pages_fetched, records_saved, started_at, finished_at
FROM scrape_runs ORDER BY id DESC;
```

---

## Troubleshooting

**`Config file not found`** — copy `config.example.ini` to `config.ini`, or set
the `MYSQL_*` env vars.

**Connection / SSL errors to enamad.ir** — check connectivity and retry; the
scraper retries automatically.

**Captcha keeps failing** — drop `--fast-ocr`, increase `--retries 10`, lower
`--chunk-pages`, or use `--manual` (single worker).

**All workers fail together** — likely rate-limiting from enamad.ir; use fewer
workers and a small `--delay`, then resume.

**MySQL access denied** — verify credentials and that the server is running.

**Telegram `TimedOut`** — `api.telegram.org` is unreachable; set an HTTP/SOCKS5
`proxy` or enable a VPN (not needed on an unfiltered server).
