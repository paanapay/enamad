# Enamad Domain Scraper

Scrapes the official Enamad domain-holder list (`enamad.ir/DomainListForMIMT`)
into **MySQL**, solving captchas automatically with `ddddocr`. Includes
**Telegram** and **Bale** bots to browse the data and a **scheduler** to keep it fresh.

Each list page returns 30 domains and needs a fresh captcha. Domain *details*
(address, phone, licenses…) come from the public trust-seal page and need **no
captcha**.

> Full command reference and advanced options: see [DOCS.md](DOCS.md).

---

## Requirements

- Python 3.10+
- MySQL 5.7+ / MariaDB 10.3+
- Internet access to `https://enamad.ir`

(Docker users don't need to install these locally — see below.)

---

## Quick start (local)

```bash
# 1. install dependencies
pip install -r requirements.txt

# 2. create your config
cp config.example.ini config.ini      # then edit MySQL + Telegram settings

# 3. create the database tables
python extract_enamad.py --init-db

# 4. scrape the full list (parallel, resumable)
python extract_enamad.py --all --workers 4 --chunk-pages 10

# 5. run a bot
python telegram_bot.py
# or Bale:
python bale_bot.py
```

`--all` auto-resumes after interruptions — just run it again.

---

## Keeping data fresh

A full scrape takes hours; do it once, then keep it current cheaply:

```bash
# new domains (needs captcha, but only a few tail pages)
python extract_enamad.py --update

# refresh address/phone/email/licenses of existing domains (NO captcha)
python extract_enamad.py --refresh-stale --stale-days 30 --refresh-limit 500

# refresh one specific domain + all its licenses
python extract_enamad.py --refresh-services digikala.com
```

To automate these on a schedule, run `python scheduler.py` (cron-like, config in
the `[scheduler]` section). Details in [DOCS.md](DOCS.md).

---

## Run with Docker

Brings up MySQL + bot + scheduler together. No local Python/MySQL needed.

```bash
cp .env.example .env          # set MYSQL_PASSWORD, BOT_TOKEN, admin IDs, ...
docker compose up -d --build  # starts mysql, creates schema, runs bot + scheduler
```

Then run the one-time full scrape inside the container:

```bash
docker compose run --rm bot python extract_enamad.py --all --workers 4 --chunk-pages 10
```

| Service | Role |
|---------|------|
| `mysql` | Database with a persistent volume |
| `init` | One-shot: creates the schema, then exits |
| `bot` | Telegram bot (long-polling, always on) |
| `bale-bot` | Bale bot (long-polling, always on) |
| `scheduler` | Recurring `--update` + `--refresh-stale` |

Config inside containers comes from environment variables (see `.env.example`),
so no `config.ini` is required. More detail in [DOCS.md](DOCS.md).

---

## Bots (Telegram & Bale)

Both bots share the same features:

- Search by domain / business name / owner (local DB + optional live lookup)
- Browse latest domains (same order as the site), top-rated, by province
- Admins get a user list + admin panel; set `admin_users` in config

**Telegram:** `python telegram_bot.py` — config in `[telegram]`, token from @BotFather.

**Bale:** `python bale_bot.py` — config in `[bale]`, token from [@botfather](https://ble.ir/botfather) on Bale. Uses the [Bale Bot API](https://docs.bale.ai/) (`https://tapi.bale.ai`) and usually needs no proxy inside Iran.

Uses long polling — no public URL or webhook needed. Setup details in [DOCS.md](DOCS.md).

---

## Notes

- Be respectful: avoid aggressive parallel load against enamad.ir.
- `config.ini`, `.env`, cookies and captcha temp files are git-ignored.
- Use at your own responsibility; respect enamad.ir terms of service.
