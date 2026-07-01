# Enamad Domain Scraper

Python scraper for the official Enamad domain holder list (`enamad.ir/DomainListForMIMT`).

It fetches paginated results, solves captchas automatically with `ddddocr`, and stores records in **MySQL**.

Each API page returns **30 domains**. Every page requires a **new captcha**.

---

## Requirements

- **Python 3.10+** (tested with 3.13)
- **MySQL 5.7+** or **MariaDB 10.3+**
- Internet access to `https://enamad.ir`

Recommended on Windows: [Laragon](https://laragon.org/) with Python + MySQL enabled.

---

## Project layout

```
enamad/
├── extract_enamad.py      # Main scraper
├── db.py                  # MySQL helpers
├── schema.sql             # Database schema
├── config.example.ini     # Config template
├── config.ini             # Your local config (not in git)
├── requirements.txt
└── README.md
```

---

## Installation

### 1. Clone / open the project

```bash
cd D:\WORK\enamad
```

### 2. Install Python dependencies

```bash
C:\laragon\bin\python\python-3.13\python.exe -m pip install -r requirements.txt
```

First install of `ddddocr` downloads ~140 MB (includes ONNX runtime). This is normal.

### 3. Create MySQL database config

Copy the example config:

```bash
copy config.example.ini config.ini
```

Edit `config.ini`:

```ini
[mysql]
host = 127.0.0.1
port = 3306
user = root
password =
database = enamad

[scraper]
delay = 1.0
retries = 5
```

Adjust `user`, `password`, and `database` for your environment.

### 4. Initialize the database

Make sure MySQL is running, then:

```bash
python extract_enamad.py --init-db
```

This runs `schema.sql` and creates:

- `scrape_runs` — scrape session log
- `enamad_domains` — domain records (upsert on `enamad_id + code`)

---

## Usage

### Basic scrape (1 page = 30 records)

```bash
python extract_enamad.py --pages 1
```

### Scrape multiple pages

```bash
python extract_enamad.py --pages 10
```

### Search a single domain (site search API)

Same API used by the search box on [enamad.ir](https://enamad.ir/) (`POST /Home/GetData`):

```bash
python extract_enamad.py --search digikala.com
```

Print only (no MySQL):

```bash
python extract_enamad.py --search digikala.com --no-save
```

Bulk lookup from a text file (one domain per line):

```bash
python extract_enamad.py --search-file domains.txt
```

### Scrape **all** pages (full database)

```bash
python extract_enamad.py --all
```

Runs until the last page. Progress is saved in MySQL after each page.

**Resume** after interrupt (Ctrl+C) or crash — run the same command again:

```bash
python extract_enamad.py --all
```

It continues from the next page automatically.

**Start over** from page 1:

```bash
python extract_enamad.py --all --reset
```

**Force a specific page** (ignores saved progress):

```bash
python extract_enamad.py --all --start-page 50
```

Check saved progress in MySQL:

```sql
SELECT * FROM scraper_state;
```

### Start from a specific page

```bash
python extract_enamad.py --start-page 5 --pages 3
```

### Manual captcha (fallback if OCR fails)

```bash
python extract_enamad.py --pages 2 --manual
```

### Save captcha images for debugging

```bash
python extract_enamad.py --pages 1 --debug
```

### Custom config path

```bash
python extract_enamad.py --config D:\path\to\config.ini --pages 5
```

---

## CLI options

| Option | Description |
|--------|-------------|
| `--init-db` | Create database and tables, then exit |
| `--search DOMAIN` | Look up one domain via `/Home/GetData` |
| `--search-file FILE` | Look up many domains from a text file |
| `--no-save` | With search: do not write to MySQL |
| `--json` | With search: print JSON |
| `--all` | Fetch every page until the end (auto-resume) |
| `--reset` | Clear saved progress and start from page 1 (with `--all`) |
| `--pages N` | Number of pages to fetch (default: 1, ignored with `--all`) |
| `--start-page N` | Start from page N (overrides auto-resume) |
| `--delay SEC` | Pause between pages (overrides config) |
| `--retries N` | Max captcha attempts per page (overrides config) |
| `--manual` | Type captcha manually |
| `--debug` | Save captcha images to `debug_captcha/` |
| `--config FILE` | Config file path (default: `config.ini`) |

---

## Database schema

### `enamad_domains`

| Column | Description |
|--------|-------------|
| `enamad_id` | Enamad record ID |
| `code` | Enamad verification code |
| `domain` | Domain name |
| `business_name` | Persian business title |
| `province` | Province |
| `city` | City |
| `rating` | Star rating (0–5) |
| `approve_date` | Issue date |
| `expire_date` | Expiry date |
| `trustseal_url` | Trust seal link |
| `source_page` | Page number when scraped |
| `source_row` | Row index on that page |
| `scrape_run_id` | FK to `scrape_runs` |

Duplicate records are updated (`ON DUPLICATE KEY UPDATE`).

### Example queries

```sql
SELECT domain, business_name, province, city, rating
FROM enamad_domains
ORDER BY updated_at DESC
LIMIT 20;

SELECT status, pages_fetched, records_saved, started_at, finished_at
FROM scrape_runs
ORDER BY id DESC;
```

---

## Troubleshooting

### `Config file not found`

Copy `config.example.ini` to `config.ini`.

### SSL / connection errors to enamad.ir

- Check internet connection
- Retry later (site may be slow)
- The scraper retries failed connections automatically

### Captcha keeps failing

- Use `--manual` to enter captcha by hand
- Use `--debug` to inspect captcha images
- Increase retries: `--retries 8`

### MySQL access denied

- Verify credentials in `config.ini`
- Ensure MySQL service is running (Laragon: Start All)

---

## Telegram bot

Browse the scraped database from Telegram.

### Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token.
2. Add to `config.ini`:

```ini
[telegram]
bot_token = 123456:ABC...
allowed_users =          ; optional: your Telegram user ID(s), comma-separated
live_search = yes        ; query enamad.ir if not found locally
```

3. Install dependencies and run:

```bash
pip install -r requirements.txt
python telegram_bot.py
```

### Features

| Feature | Description |
|---------|-------------|
| 🔍 Search | Domain, business name, or owner (local DB + optional live API) |
| 🆕 Latest | Recently updated records in MySQL |
| 📅 New approvals | Sorted by approve date |
| ⭐ Top rated | 4–5 star domains |
| 🗺 By province | Browse by province |
| 📊 Stats | DB size, scrape progress, last run |

Send any domain name as text to search directly.

---

## Notes

- Scraping many pages takes time (captcha + delay per page).
- Be respectful: do not run aggressive parallel jobs against enamad.ir.
- `config.ini`, cookies, CSV exports, and captcha temp folders are git-ignored.

---

## License

Use at your own responsibility. Respect enamad.ir terms of service.
