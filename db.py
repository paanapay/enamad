from __future__ import annotations

import configparser
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

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


def load_config(path: Path | None = None) -> AppConfig:
    config_path = path or DEFAULT_CONFIG_PATH
    if not config_path.is_file():
        raise FileNotFoundError(
            f"Config file not found: {config_path}\n"
            f"Copy config.example.ini to config.ini and edit your MySQL settings."
        )

    parser = configparser.ConfigParser()
    parser.read(config_path, encoding="utf-8")

    mysql = MySQLConfig(
        host=parser.get("mysql", "host", fallback="127.0.0.1"),
        port=parser.getint("mysql", "port", fallback=3306),
        user=parser.get("mysql", "user", fallback="root"),
        password=parser.get("mysql", "password", fallback=""),
        database=parser.get("mysql", "database", fallback="enamad"),
    )
    scraper = ScraperConfig(
        delay=parser.getfloat("scraper", "delay", fallback=1.0),
        retries=parser.getint("scraper", "retries", fallback=5),
    )
    return AppConfig(mysql=mysql, scraper=scraper)


def connect(cfg: MySQLConfig, database: str | None = None):
    return pymysql.connect(
        host=cfg.host,
        port=cfg.port,
        user=cfg.user,
        password=cfg.password,
        database=database or cfg.database,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
    )


@contextmanager
def mysql_connection(cfg: MySQLConfig, database: str | None = None) -> Iterator[Any]:
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
        conn.commit()
    finally:
        conn.close()


DOMAIN_DETAIL_COLUMNS = {
    "owner_name": "VARCHAR(512) NULL",
    "business_address": "VARCHAR(1024) NULL",
    "phone": "VARCHAR(64) NULL",
    "email": "VARCHAR(255) NULL",
    "work_hours": "VARCHAR(128) NULL",
}


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

    sql = """
        INSERT INTO enamad_domains (
            enamad_id, code, domain, business_name, owner_name, business_address,
            phone, email, work_hours, province, city,
            rating, approve_date, expire_date, trustseal_url,
            source_page, source_row, scrape_run_id
        ) VALUES (
            %(enamad_id)s, %(code)s, %(domain)s, %(business_name)s, %(owner_name)s,
            %(business_address)s, %(phone)s, %(email)s, %(work_hours)s,
            %(province)s, %(city)s,
            %(rating)s, %(approve_date)s, %(expire_date)s, %(trustseal_url)s,
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
            source_page = VALUES(source_page),
            source_row = VALUES(source_row),
            scrape_run_id = VALUES(scrape_run_id),
            updated_at = CURRENT_TIMESTAMP
    """

    payload = []
    for row in rows:
        payload.append(
            {
                "enamad_id": str(row.get("enamad_id", "")),
                "code": str(row.get("code", "")),
                "domain": row.get("domain", ""),
                "business_name": row.get("business_name") or row.get("persian_name") or None,
                "owner_name": row.get("owner_name") or None,
                "business_address": row.get("business_address") or None,
                "phone": row.get("phone") or None,
                "email": row.get("email") or None,
                "work_hours": row.get("work_hours") or None,
                "province": row.get("province") or None,
                "city": row.get("city") or None,
                "rating": int(row.get("rating") or 0),
                "approve_date": row.get("approve_date") or None,
                "expire_date": row.get("expire_date") or None,
                "trustseal_url": row.get("trustseal_url") or None,
                "source_page": row.get("source_page"),
                "source_row": row.get("source_row"),
                "scrape_run_id": scrape_run_id,
            }
        )

    with conn.cursor() as cursor:
        cursor.executemany(sql, payload)

    save_domain_services(conn, rows, scrape_run_id=scrape_run_id)
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
            for service in services:
                title = (service.get("service_title") or "").strip()
                if not title:
                    continue
                batch.append(
                    {
                        "enamad_id": enamad_id,
                        "code": code,
                        "row_num": int(service.get("row_num") or 0) or len(batch) + 1,
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
