from __future__ import annotations

import configparser
import os
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import unquote

import pymysql
from pymysql.cursors import DictCursor

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "config.ini"
SCHEMA_PATH = SCRIPT_DIR / "schema.sql"


@dataclass(frozen=True)
class MySQLConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


@dataclass(frozen=True)
class ScraperConfig:
    delay: float
    retries: int


@dataclass(frozen=True)
class AppConfig:
    mysql: MySQLConfig
    scraper: ScraperConfig


def normalize_domain(domain: str) -> str:
    """Decode URL-encoded IDN labels and strip common URL prefixes."""
    value = (domain or "").strip()
    if not value:
        return ""

    for _ in range(2):
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded

    value = value.lower()
    for prefix in ("https://www.", "http://www.", "https://", "http://", "www."):
        if value.startswith(prefix):
            value = value[len(prefix) :]
    return value.split("/")[0].split("?")[0].split("#")[0].strip()


def _merge_search_metadata(row: dict, fresh: dict) -> dict:
    """Apply live search fields (fresh enamad_id/code) onto an existing DB row."""
    merged = dict(row)
    for key in (
        "enamad_id",
        "code",
        "domain",
        "business_name",
        "province",
        "city",
        "rating",
        "approve_date",
        "expire_date",
        "trustseal_url",
    ):
        value = fresh.get(key)
        if value not in (None, ""):
            merged[key] = value
    return merged


def update_domain_by_id(conn, domain_id: int, row: dict, *, old_enamad_id: str, old_code: str) -> None:
    """Update one domain row in place (avoids duplicate inserts when id/code rotate)."""
    from crm_db import enrich_contact_fields

    enriched = enrich_contact_fields(row)
    new_enamad_id = str(enriched.get("enamad_id") or "")
    new_code = str(enriched.get("code") or "")
    if not new_enamad_id or not new_code:
        raise ValueError("enamad_id and code are required to update a domain")

    with conn.cursor() as cursor:
        if (old_enamad_id, old_code) != (new_enamad_id, new_code):
            cursor.execute(
                "DELETE FROM enamad_domain_services WHERE enamad_id = %s AND code = %s",
                (old_enamad_id, old_code),
            )
        cursor.execute(
            """
            UPDATE enamad_domains SET
                enamad_id = %s,
                code = %s,
                domain = %s,
                business_name = %s,
                owner_name = %s,
                business_address = %s,
                phone = %s,
                email = %s,
                work_hours = %s,
                province = %s,
                city = %s,
                rating = %s,
                approve_date = %s,
                expire_date = %s,
                trustseal_url = %s,
                phone_type = %s,
                mobile_phone = %s,
                email_normalized = %s,
                enamad_status = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (
                new_enamad_id,
                new_code,
                normalize_domain(str(enriched.get("domain") or "")),
                enriched.get("business_name") or enriched.get("persian_name") or None,
                enriched.get("owner_name") or None,
                enriched.get("business_address") or None,
                enriched.get("phone") or None,
                enriched.get("email") or None,
                enriched.get("work_hours") or None,
                enriched.get("province") or None,
                enriched.get("city") or None,
                int(enriched.get("rating") or 0),
                enriched.get("approve_date") or None,
                enriched.get("expire_date") or None,
                enriched.get("trustseal_url") or None,
                enriched.get("phone_type"),
                enriched.get("mobile_phone"),
                enriched.get("email_normalized"),
                domain_id,
            ),
        )

    save_domain_services(conn, [enriched])


def refresh_domain_trustseal(conn, domain_id: int, client=None) -> tuple[dict, list[dict]] | None:
    """Fetch trust seal page and refresh contact info + all services in DB.

    Resolves a fresh enamad_id/code via live search (same path as estelam/bot)
    because stored trust-seal tokens expire and stale codes time out.

    Pass a reused `client` (EnamadClient) to enable HTTP keep-alive across calls.
    """
    from extract_enamad import EnamadClient, TRUSTSEAL_LABELS, normalize_search_row

    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, enamad_id, code, domain, business_name, owner_name,
                   business_address, phone, email, work_hours, province, city,
                   rating, approve_date, expire_date, trustseal_url,
                   source_page, source_row, updated_at, created_at
            FROM enamad_domains
            WHERE id = %s
            """,
            (domain_id,),
        )
        row = cursor.fetchone()
    if not row or not row.get("domain"):
        return None

    old_enamad_id = str(row.get("enamad_id") or "")
    old_code = str(row.get("code") or "")

    if client is None:
        client = EnamadClient(quiet=True, timeout=25, retries=1)

    search_data = client.search_domain(str(row["domain"]))
    if not search_data:
        set_domain_enamad_status(conn, domain_id, "not_found")
        return None

    enriched = _merge_search_metadata(row, normalize_search_row(search_data, str(row["domain"])))

    try:
        details = client.fetch_trustseal_details(enriched["enamad_id"], enriched["code"])
    except Exception:
        return None

    for field in TRUSTSEAL_LABELS.values():
        value = details.get(field, "")
        if value:
            enriched[field] = value
    if not enriched.get("business_name") and details.get("shop_name"):
        enriched["business_name"] = details["shop_name"]
    enriched["services"] = details.get("services") or []

    update_domain_by_id(
        conn,
        domain_id,
        enriched,
        old_enamad_id=old_enamad_id,
        old_code=old_code,
    )
    services = [
        {
            "service_title": item.get("service_title"),
            "license_issuer": item.get("license_issuer"),
            "status": item.get("status"),
            "valid_from": item.get("valid_from"),
            "valid_to": item.get("valid_to"),
        }
        for item in enriched["services"]
    ]
    return enriched, services


def _format_duration(seconds: float) -> str:
    if seconds < 0 or seconds == float("inf"):
        return "-"
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _stdout_tty() -> bool:
    return sys.stdout.isatty()


def _ansi(text: str, *codes: str) -> str:
    if not _stdout_tty() or not codes:
        return text
    return "".join(codes) + text + "\033[0m"


def _progress_bar(ratio: float, width: int = 22) -> str:
    ratio = max(0.0, min(1.0, ratio))
    filled = int(width * ratio)
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def _progress_step(total: int) -> int:
    if total <= 50:
        return 1
    if total <= 500:
        return 5
    return 10


def _truncate_domain(domain: str | None, width: int = 28) -> str:
    if not domain:
        return ""
    if len(domain) <= width:
        return domain
    return domain[: max(0, width - 3)] + "..."


def _print_refresh_progress(
    index: int,
    total: int,
    ok: int,
    failed: int,
    started: float,
    *,
    current_domain: str | None = None,
    every: int | None = None,
) -> None:
    if total <= 0:
        return
    step = every if every is not None else _progress_step(total)
    # Always show the very first item so the user sees activity immediately.
    if index != 1 and index != total and index % step != 0:
        return

    elapsed = max(0.001, time.time() - started)
    rate = index / elapsed
    remaining = total - index
    eta = remaining / rate if rate > 0 else float("inf")
    ratio = index / total
    pct = int(ratio * 100)

    bar = _ansi(_progress_bar(ratio), "\033[36m")
    counts = (
        _ansi(f"ok {ok:,}", "\033[32m")
        + _ansi(" | ", "\033[2m")
        + (
            _ansi(f"fail {failed:,}", "\033[31m")
            if failed
            else _ansi("fail 0", "\033[2m")
        )
    )
    meta = _ansi(
        f"{rate:.1f}/s | ETA {_format_duration(eta)}",
        "\033[2m",
    )
    domain_label = _truncate_domain(current_domain)
    domain_part = (
        _ansi(f" | {domain_label}", "\033[33m") if domain_label else ""
    )

    line = (
        f"{bar}  "
        f"{index:,}/{total:,}  {pct:>3}%  "
        f"{counts}  "
        f"{meta}"
        f"{domain_part}"
    )

    # Clear full line first — fixes leftover chars when the new domain is shorter.
    sys.stdout.write("\033[2K\r")
    sys.stdout.write(line)
    sys.stdout.flush()
    if index == total:
        sys.stdout.write("\n")
        sys.stdout.flush()


def refresh_domain_services(
    conn,
    *,
    domain: str | None = None,
    limit: int | None = None,
    delay: float = 0.5,
    progress: bool = False,
    commit_every: int = 100,
) -> tuple[int, int]:
    """Re-fetch trust seal pages and update services for stored domains."""
    where = "WHERE enamad_id IS NOT NULL AND enamad_id != '' AND code IS NOT NULL AND code != ''"
    params: list = []
    if domain:
        where += " AND domain = %s"
        params.append(normalize_domain(domain))

    limit_sql = ""
    if limit is not None and limit > 0:
        limit_sql = " LIMIT %s"
        params.append(limit)

    with conn.cursor() as cursor:
        cursor.execute(
            f"SELECT id, domain FROM enamad_domains {where} ORDER BY id ASC{limit_sql}",
            params,
        )
        rows = cursor.fetchall()

    ok = 0
    failed = 0
    total = len(rows)
    started = time.time()
    if progress and total:
        print(
            _ansi(
                f"Refreshing {total:,} domain(s) from trust seal ...",
                "\033[1m",
                "\033[36m",
            ),
            flush=True,
        )

    for index, row in enumerate(rows, start=1):
        try:
            result = refresh_domain_trustseal(conn, row["id"])
            if result:
                ok += 1
            else:
                failed += 1
        except Exception:
            failed += 1

        if progress:
            _print_refresh_progress(
                index, total, ok, failed, started,
                current_domain=row.get("domain"),
            )
        if commit_every > 0 and index % commit_every == 0:
            conn.commit()

        if delay > 0 and index < total:
            time.sleep(delay)

    if commit_every > 0:
        conn.commit()

    return ok, failed


def refresh_stale_domains(
    conn,
    *,
    days: int = 30,
    limit: int = 500,
    delay: float = 0.3,
    progress: bool = False,
    commit_every: int = 100,
    missing_only: bool = False,
    newest_first: bool = False,
) -> tuple[int, int, int]:
    """Refresh domains not updated in the last `days` days (no captcha needed).

    Returns (candidates, ok, failed). By default oldest `updated_at` is refreshed first.
    With `missing_only`, only rows lacking address/phone/email are picked.
    With `newest_first`, domains at the front of the enamad site list are picked first.
    """
    rows = _select_stale_ids(
        conn, days, limit, missing_only=missing_only, newest_first=newest_first
    )

    ok = 0
    failed = 0
    total = len(rows)
    started = time.time()
    if progress and total:
        if missing_only:
            age_label = "missing details"
        else:
            age_label = f"older than {days}d" if days > 0 else "all domains"
        print(
            _ansi(
                f"Refreshing {total:,} domain(s) from trust seal ({age_label}) ...",
                "\033[1m",
                "\033[36m",
            ),
            flush=True,
        )

    for index, row in enumerate(rows, start=1):
        try:
            if refresh_domain_trustseal(conn, row["id"]):
                ok += 1
            else:
                failed += 1
        except Exception:
            failed += 1

        if progress:
            _print_refresh_progress(
                index, total, ok, failed, started,
                current_domain=row.get("domain"),
            )
        if commit_every > 0 and index % commit_every == 0:
            conn.commit()

        if delay > 0 and index < total:
            time.sleep(delay)

    if commit_every > 0:
        conn.commit()

    return total, ok, failed


def _select_stale_ids(
    conn, days: int, limit: int, *, missing_only: bool = False, newest_first: bool = False
) -> list[dict]:
    """Pick domains to refresh.

    - Normal: those not updated in the last `days` days (oldest first).
    - missing_only: only rows lacking contact details (address/phone/email),
      regardless of `updated_at`.
    - newest_first: same order as panel sort=latest (source_page/row, front of enamad list).
    """
    if missing_only:
        where = (
            "enamad_id IS NOT NULL AND enamad_id != '' "
            "AND code IS NOT NULL AND code != '' "
            "AND (enamad_status IS NULL OR enamad_status != 'not_found') "
            "AND ("
            "  business_address IS NULL OR business_address = '' "
            "  OR phone IS NULL OR phone = '' "
            "  OR email IS NULL OR email = ''"
            ")"
        )
        params: tuple = (limit,)
    else:
        where = (
            "enamad_id IS NOT NULL AND enamad_id != '' "
            "AND code IS NOT NULL AND code != '' "
            "AND (updated_at IS NULL OR updated_at < (NOW() - INTERVAL %s DAY))"
        )
        params = (days, limit)

    if newest_first:
        order = (
            "CASE WHEN source_page IS NULL OR source_row IS NULL THEN 1 ELSE 0 END ASC, "
            "source_page ASC, source_row ASC, id ASC"
        )
    else:
        order = "updated_at ASC"

    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT id, domain
            FROM enamad_domains
            WHERE {where}
            ORDER BY {order}
            LIMIT %s
            """,
            params,
        )
        return list(cursor.fetchall())


def refresh_stale_domains_parallel(
    cfg: "MySQLConfig",
    *,
    days: int = 30,
    limit: int = 500,
    workers: int = 4,
    delay: float = 0.0,
    progress: bool = False,
    commit_every: int = 50,
    missing_only: bool = False,
    newest_first: bool = False,
) -> tuple[int, int, int]:
    """Parallel version of refresh_stale_domains using threads.

    Each worker owns its own DB connection and HTTP client (keep-alive).
    Refresh is I/O-bound (no captcha), so threads scale well.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import wait as _futures_wait_impl
    from concurrent.futures import FIRST_COMPLETED
    from extract_enamad import EnamadClient

    def _futures_wait(pending, timeout):
        result = _futures_wait_impl(
            pending, timeout=timeout, return_when=FIRST_COMPLETED
        )
        return result.done, result.not_done

    with mysql_connection(cfg) as conn:
        rows = _select_stale_ids(
            conn, days, limit, missing_only=missing_only, newest_first=newest_first
        )

    total = len(rows)
    if total == 0:
        return 0, 0, 0

    workers = max(1, min(workers, total))
    # enamad.ir throttles concurrent connections per IP. Beyond ~6 the server
    # returns 429 and every worker stalls in backoff, so throughput DROPS.
    SAFE_WORKERS = 6
    capped = False
    if workers > SAFE_WORKERS:
        capped = True
        workers = SAFE_WORKERS
    started = time.time()
    if progress:
        if missing_only:
            age_label = "missing details"
        else:
            age_label = f"older than {days}d" if days > 0 else "all domains"
        if capped:
            print(
                _ansi(
                    f"Note: capping workers to {SAFE_WORKERS} — enamad.ir throttles "
                    f"a single IP; more workers only trigger 429s and slow down.",
                    "\033[33m",
                ),
                flush=True,
            )
        print(
            _ansi(
                f"Refreshing {total:,} domain(s) from trust seal "
                f"({age_label}) with {workers} workers ...",
                "\033[1m",
                "\033[36m",
            ),
            flush=True,
        )

    lock = threading.Lock()
    counters = {"done": 0, "ok": 0, "failed": 0}
    stop_event = threading.Event()

    # Round-robin split keeps oldest-first order roughly balanced per worker.
    chunks: list[list[dict]] = [rows[i::workers] for i in range(workers)]

    def worker(chunk: list[dict]) -> None:
        # Quiet client with a tighter timeout so one slow request can't stall a
        # worker for 90s*3; keeps the shared progress bar clean.
        client = EnamadClient(quiet=True, timeout=25, retries=2)
        conn = connect(cfg)
        processed = 0
        try:
            for row in chunk:
                if stop_event.is_set():
                    break
                try:
                    result = refresh_domain_trustseal(conn, row["id"], client=client)
                    success = bool(result)
                except Exception:
                    success = False
                processed += 1
                if commit_every > 0 and processed % commit_every == 0:
                    conn.commit()

                with lock:
                    counters["done"] += 1
                    if success:
                        counters["ok"] += 1
                    else:
                        counters["failed"] += 1
                    if progress:
                        _print_refresh_progress(
                            counters["done"], total,
                            counters["ok"], counters["failed"], started,
                            current_domain=row.get("domain"),
                        )
                if delay > 0 and not stop_event.is_set():
                    time.sleep(delay)
            conn.commit()
        finally:
            conn.close()

    pool = ThreadPoolExecutor(max_workers=workers)
    futures = [pool.submit(worker, chunk) for chunk in chunks if chunk]
    interrupted = False
    try:
        # Poll instead of a plain result() join so Ctrl+C reaches the main thread.
        pending = set(futures)
        while pending:
            done, pending = _futures_wait(pending, timeout=0.5)
    except KeyboardInterrupt:
        interrupted = True
        stop_event.set()
        if progress:
            print(
                _ansi(
                    "\nStopping... waiting for in-flight requests to finish.",
                    "\033[33m",
                ),
                flush=True,
            )
    finally:
        # Workers exit soon after stop_event; don't cancel so their commits land.
        pool.shutdown(wait=True)

    if interrupted and progress:
        print(
            _ansi(
                f"Interrupted. {counters['done']:,}/{total:,} processed "
                f"({counters['ok']:,} ok, {counters['failed']:,} failed).",
                "\033[33m",
            ),
            flush=True,
        )

    return counters["done"], counters["ok"], counters["failed"]


def fix_encoded_domains(conn) -> int:
    """Repair domains stored with percent-encoding (e.g. Persian IDN from API)."""
    fixed = 0
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, domain
            FROM enamad_domains
            WHERE domain LIKE '%\\%%' ESCAPE '\\\\'
            """
        )
        rows = cursor.fetchall()

        for row in rows:
            old_domain = row["domain"] or ""
            new_domain = normalize_domain(old_domain)
            if not new_domain or new_domain == old_domain:
                continue
            cursor.execute(
                "UPDATE enamad_domains SET domain = %s WHERE id = %s",
                (new_domain, row["id"]),
            )
            fixed += 1

    return fixed


def _env(*keys: str) -> str | None:
    """Return the first non-empty environment variable among keys."""
    for key in keys:
        value = os.environ.get(key)
        if value is not None and value.strip() != "":
            return value.strip()
    return None


def load_config(path: Path | None = None) -> AppConfig:
    config_path = path or DEFAULT_CONFIG_PATH

    parser = configparser.ConfigParser()
    if config_path.is_file():
        parser.read(config_path, encoding="utf-8")
    elif _env("MYSQL_HOST") is None:
        raise FileNotFoundError(
            f"Config file not found: {config_path}\n"
            f"Copy config.example.ini to config.ini and edit your MySQL settings, "
            f"or provide MYSQL_HOST/MYSQL_USER/... environment variables (Docker)."
        )

    mysql = MySQLConfig(
        host=_env("MYSQL_HOST") or parser.get("mysql", "host", fallback="127.0.0.1"),
        port=int(_env("MYSQL_PORT") or parser.getint("mysql", "port", fallback=3306)),
        user=_env("MYSQL_USER") or parser.get("mysql", "user", fallback="root"),
        password=_env("MYSQL_PASSWORD") or parser.get("mysql", "password", fallback=""),
        database=_env("MYSQL_DATABASE") or parser.get("mysql", "database", fallback="enamad"),
    )
    scraper = ScraperConfig(
        delay=float(_env("SCRAPER_DELAY") or parser.getfloat("scraper", "delay", fallback=1.0)),
        retries=int(_env("SCRAPER_RETRIES") or parser.getint("scraper", "retries", fallback=5)),
    )
    return AppConfig(mysql=mysql, scraper=scraper)


_NO_DATABASE = object()


def connect(cfg: MySQLConfig, database=_NO_DATABASE):
    # Pass database=None explicitly to connect WITHOUT selecting a schema
    # (needed by init_database before the DB exists). Omit it to use cfg.database.
    if database is _NO_DATABASE:
        db_name = cfg.database
    else:
        db_name = database
    return pymysql.connect(
        host=cfg.host,
        port=cfg.port,
        user=cfg.user,
        password=cfg.password,
        database=db_name,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
    )


@contextmanager
def mysql_connection(cfg: MySQLConfig, database=_NO_DATABASE) -> Iterator[Any]:
    conn = connect(cfg, database=database)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def commit_connection(conn) -> None:
    conn.commit()


def init_database(cfg: MySQLConfig) -> None:
    if not SCHEMA_PATH.is_file():
        raise FileNotFoundError(f"Schema file not found: {SCHEMA_PATH}")

    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    statements = [part.strip() for part in sql.split(";") if part.strip()]

    conn = connect(cfg, database=None)
    try:
        with conn.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
        conn.commit()
        ensure_domain_detail_columns(conn)
        ensure_services_table(conn)
        ensure_bot_users_table(conn)
        from crm_db import ensure_crm_tables

        ensure_crm_tables(conn)
        conn.commit()
    finally:
        conn.close()


DOMAIN_DETAIL_COLUMNS = {
    "owner_name": "VARCHAR(512) NULL",
    "business_address": "VARCHAR(1024) NULL",
    "phone": "VARCHAR(64) NULL",
    "email": "VARCHAR(255) NULL",
    "work_hours": "VARCHAR(128) NULL",
    "enamad_status": "VARCHAR(16) NULL",
}


def set_domain_enamad_status(conn, domain_id: int, status: str | None) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            "UPDATE enamad_domains SET enamad_status = %s WHERE id = %s",
            (status, domain_id),
        )


def ensure_domain_detail_columns(conn) -> None:
    with conn.cursor() as cursor:
        cursor.execute("SHOW COLUMNS FROM enamad_domains")
        existing = {row["Field"] for row in cursor.fetchall()}
        for name, ddl in DOMAIN_DETAIL_COLUMNS.items():
            if name not in existing:
                cursor.execute(f"ALTER TABLE enamad_domains ADD COLUMN {name} {ddl}")


def ensure_services_table(conn) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS enamad_domain_services (
              id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
              enamad_id VARCHAR(64) NOT NULL,
              code VARCHAR(128) NOT NULL,
              row_num INT UNSIGNED NOT NULL,
              service_title VARCHAR(512) NOT NULL,
              license_issuer VARCHAR(512) NULL,
              license_number VARCHAR(128) NULL,
              valid_from VARCHAR(32) NULL,
              valid_to VARCHAR(32) NULL,
              status VARCHAR(64) NULL,
              scrape_run_id BIGINT UNSIGNED NULL,
              created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
              PRIMARY KEY (id),
              UNIQUE KEY uk_service_row (enamad_id, code, row_num),
              KEY idx_service_title (service_title(191)),
              KEY idx_service_status (status),
              CONSTRAINT fk_services_domain
                FOREIGN KEY (enamad_id, code) REFERENCES enamad_domains (enamad_id, code)
                ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )


def ensure_bot_users_table(conn) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_users (
              platform VARCHAR(16) NOT NULL DEFAULT 'telegram',
              user_id BIGINT NOT NULL,
              username VARCHAR(255) NULL,
              first_name VARCHAR(255) NULL,
              last_name VARCHAR(255) NULL,
              interaction_count INT UNSIGNED NOT NULL DEFAULT 0,
              last_action VARCHAR(64) NULL,
              first_seen TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              last_seen TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
              PRIMARY KEY (platform, user_id),
              KEY idx_bot_users_last_seen (last_seen)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        cursor.execute(
            """
            SELECT COUNT(*) AS c
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'bot_users'
              AND COLUMN_NAME = 'platform'
            """
        )
        if int((cursor.fetchone() or {}).get("c") or 0) == 0:
            cursor.execute(
                """
                ALTER TABLE bot_users
                  ADD COLUMN platform VARCHAR(16) NOT NULL DEFAULT 'telegram' FIRST
                """
            )
            cursor.execute("ALTER TABLE bot_users DROP PRIMARY KEY")
            cursor.execute(
                "ALTER TABLE bot_users ADD PRIMARY KEY (platform, user_id)"
            )


def record_bot_user(
    conn,
    *,
    platform: str = "telegram",
    user_id: int,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    action: str | None = None,
) -> None:
    """Upsert a bot user, bumping interaction count and last_seen."""
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO bot_users
                (platform, user_id, username, first_name, last_name, last_action, interaction_count)
            VALUES (%s, %s, %s, %s, %s, %s, 1)
            ON DUPLICATE KEY UPDATE
                username = VALUES(username),
                first_name = VALUES(first_name),
                last_name = VALUES(last_name),
                last_action = VALUES(last_action),
                interaction_count = interaction_count + 1,
                last_seen = CURRENT_TIMESTAMP
            """,
            (platform, user_id, username, first_name, last_name, action),
        )


def start_scrape_run(
    conn,
    start_page: int,
    pages_requested: int,
    notes: str | None = None,
) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO scrape_runs (start_page, pages_requested, notes)
            VALUES (%s, %s, %s)
            """,
            (start_page, pages_requested, notes),
        )
        return int(cursor.lastrowid)


def finish_scrape_run(
    conn,
    run_id: int,
    pages_fetched: int,
    records_saved: int,
    status: str = "completed",
    notes: str | None = None,
) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE scrape_runs
            SET finished_at = CURRENT_TIMESTAMP,
                pages_fetched = %s,
                records_saved = %s,
                status = %s,
                notes = %s
            WHERE id = %s
            """,
            (pages_fetched, records_saved, status, notes, run_id),
        )


def save_domains(conn, rows: list[dict], scrape_run_id: int | None = None) -> int:
    if not rows:
        return 0

    from crm_db import enrich_contact_fields
    from crm_service import process_new_domains

    existing_keys: set[tuple[str, str]] = set()
    keys_to_check = [
        (str(r.get("enamad_id", "")), str(r.get("code", "")))
        for r in rows
        if r.get("enamad_id") and r.get("code")
    ]
    if keys_to_check:
        placeholders = ",".join(["(%s,%s)"] * len(keys_to_check))
        flat = [item for pair in keys_to_check for item in pair]
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT enamad_id, code FROM enamad_domains
                WHERE (enamad_id, code) IN ({placeholders})
                """,
                flat,
            )
            for row in cursor.fetchall():
                existing_keys.add((row["enamad_id"], row["code"]))

    sql = """
        INSERT INTO enamad_domains (
            enamad_id, code, domain, business_name, owner_name, business_address,
            phone, email, work_hours, province, city,
            rating, approve_date, expire_date, trustseal_url,
            phone_type, mobile_phone, email_normalized,
            source_page, source_row, scrape_run_id
        ) VALUES (
            %(enamad_id)s, %(code)s, %(domain)s, %(business_name)s, %(owner_name)s,
            %(business_address)s, %(phone)s, %(email)s, %(work_hours)s,
            %(province)s, %(city)s,
            %(rating)s, %(approve_date)s, %(expire_date)s, %(trustseal_url)s,
            %(phone_type)s, %(mobile_phone)s, %(email_normalized)s,
            %(source_page)s, %(source_row)s, %(scrape_run_id)s
        )
        ON DUPLICATE KEY UPDATE
            domain = VALUES(domain),
            business_name = VALUES(business_name),
            owner_name = VALUES(owner_name),
            business_address = VALUES(business_address),
            phone = VALUES(phone),
            email = VALUES(email),
            work_hours = VALUES(work_hours),
            province = VALUES(province),
            city = VALUES(city),
            rating = VALUES(rating),
            approve_date = VALUES(approve_date),
            expire_date = VALUES(expire_date),
            trustseal_url = VALUES(trustseal_url),
            phone_type = VALUES(phone_type),
            mobile_phone = VALUES(mobile_phone),
            email_normalized = VALUES(email_normalized),
            source_page = VALUES(source_page),
            source_row = VALUES(source_row),
            scrape_run_id = VALUES(scrape_run_id),
            updated_at = CURRENT_TIMESTAMP
    """

    payload = []
    new_keys: list[tuple[str, str]] = []
    for row in rows:
        enriched = enrich_contact_fields(row)
        eid = str(enriched.get("enamad_id", ""))
        code = str(enriched.get("code", ""))
        key = (eid, code)
        if key not in existing_keys and eid and code:
            new_keys.append(key)
        payload.append(
            {
                "enamad_id": eid,
                "code": code,
                "domain": normalize_domain(str(enriched.get("domain", ""))),
                "business_name": enriched.get("business_name") or enriched.get("persian_name") or None,
                "owner_name": enriched.get("owner_name") or None,
                "business_address": enriched.get("business_address") or None,
                "phone": enriched.get("phone") or None,
                "email": enriched.get("email") or None,
                "work_hours": enriched.get("work_hours") or None,
                "province": enriched.get("province") or None,
                "city": enriched.get("city") or None,
                "rating": int(enriched.get("rating") or 0),
                "approve_date": enriched.get("approve_date") or None,
                "expire_date": enriched.get("expire_date") or None,
                "trustseal_url": enriched.get("trustseal_url") or None,
                "phone_type": enriched.get("phone_type"),
                "mobile_phone": enriched.get("mobile_phone"),
                "email_normalized": enriched.get("email_normalized"),
                "source_page": enriched.get("source_page"),
                "source_row": enriched.get("source_row"),
                "scrape_run_id": scrape_run_id,
            }
        )

    with conn.cursor() as cursor:
        cursor.executemany(sql, payload)

    save_domain_services(conn, rows, scrape_run_id=scrape_run_id)

    if new_keys:
        placeholders = ",".join(["(%s,%s)"] * len(new_keys))
        flat = [item for pair in new_keys for item in pair]
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT id FROM enamad_domains
                WHERE (enamad_id, code) IN ({placeholders})
                """,
                flat,
            )
            new_ids = [int(r["id"]) for r in cursor.fetchall()]
        if new_ids:
            try:
                process_new_domains(conn, new_ids)
            except Exception:  # noqa: BLE001
                import logging
                logging.getLogger("enamad-db").exception("CRM automation failed")

    return len(payload)


def save_domain_services(conn, rows: list[dict], scrape_run_id: int | None = None) -> int:
    saved = 0
    insert_sql = """
        INSERT INTO enamad_domain_services (
            enamad_id, code, row_num, service_title, license_issuer,
            license_number, valid_from, valid_to, status, scrape_run_id
        ) VALUES (
            %(enamad_id)s, %(code)s, %(row_num)s, %(service_title)s, %(license_issuer)s,
            %(license_number)s, %(valid_from)s, %(valid_to)s, %(status)s, %(scrape_run_id)s
        )
        ON DUPLICATE KEY UPDATE
            service_title = VALUES(service_title),
            license_issuer = VALUES(license_issuer),
            license_number = VALUES(license_number),
            valid_from = VALUES(valid_from),
            valid_to = VALUES(valid_to),
            status = VALUES(status),
            scrape_run_id = VALUES(scrape_run_id),
            updated_at = CURRENT_TIMESTAMP
    """

    with conn.cursor() as cursor:
        for row in rows:
            if "services" not in row:
                continue

            enamad_id = str(row.get("enamad_id", ""))
            code = str(row.get("code", ""))
            if not enamad_id or not code:
                continue

            services = row.get("services") or []
            cursor.execute(
                "DELETE FROM enamad_domain_services WHERE enamad_id = %s AND code = %s",
                (enamad_id, code),
            )

            batch = []
            for row_num, service in enumerate(services, start=1):
                title = (service.get("service_title") or "").strip()
                if not title:
                    continue
                batch.append(
                    {
                        "enamad_id": enamad_id,
                        "code": code,
                        "row_num": row_num,
                        "service_title": title,
                        "license_issuer": service.get("license_issuer") or None,
                        "license_number": service.get("license_number") or None,
                        "valid_from": service.get("valid_from") or None,
                        "valid_to": service.get("valid_to") or None,
                        "status": service.get("status") or None,
                        "scrape_run_id": scrape_run_id,
                    }
                )

            if batch:
                cursor.executemany(insert_sql, batch)
                saved += len(batch)

    return saved


STATE_LAST_PAGE = "last_completed_page"
STATE_TOTAL_PAGES = "total_pages"


def get_scrape_state(conn) -> dict[str, int]:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT state_key, state_value FROM scraper_state "
            "WHERE state_key IN (%s, %s)",
            (STATE_LAST_PAGE, STATE_TOTAL_PAGES),
        )
        rows = cursor.fetchall()

    state: dict[str, int] = {}
    for row in rows:
        try:
            state[row["state_key"]] = int(row["state_value"])
        except (TypeError, ValueError):
            continue
    return state


def update_scrape_progress(conn, last_completed_page: int, total_pages: int | None) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO scraper_state (state_key, state_value)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE
                state_value = VALUES(state_value),
                updated_at = CURRENT_TIMESTAMP
            """,
            (STATE_LAST_PAGE, str(last_completed_page)),
        )
        if total_pages is not None:
            cursor.execute(
                """
                INSERT INTO scraper_state (state_key, state_value)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE
                    state_value = VALUES(state_value),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (STATE_TOTAL_PAGES, str(total_pages)),
            )


def reset_scrape_state(conn) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            "DELETE FROM scraper_state WHERE state_key IN (%s, %s)",
            (STATE_LAST_PAGE, STATE_TOTAL_PAGES),
        )
        cursor.execute("DELETE FROM scraper_state WHERE state_key LIKE 'parallel_w%%_last'")


def worker_progress_key(worker_id: int) -> str:
    return f"parallel_w{worker_id}_last"


def get_worker_progress(conn, worker_id: int) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT state_value FROM scraper_state WHERE state_key = %s",
            (worker_progress_key(worker_id),),
        )
        row = cursor.fetchone()
    if not row:
        return 0
    try:
        return int(row["state_value"])
    except (TypeError, ValueError):
        return 0


def update_worker_progress(conn, worker_id: int, last_page: int) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO scraper_state (state_key, state_value)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE
                state_value = VALUES(state_value),
                updated_at = CURRENT_TIMESTAMP
            """,
            (worker_progress_key(worker_id), str(last_page)),
        )
