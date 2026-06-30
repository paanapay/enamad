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
    finally:
        conn.close()


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
            enamad_id, code, domain, business_name, province, city,
            rating, approve_date, expire_date, trustseal_url,
            source_page, source_row, scrape_run_id
        ) VALUES (
            %(enamad_id)s, %(code)s, %(domain)s, %(business_name)s, %(province)s, %(city)s,
            %(rating)s, %(approve_date)s, %(expire_date)s, %(trustseal_url)s,
            %(source_page)s, %(source_row)s, %(scrape_run_id)s
        )
        ON DUPLICATE KEY UPDATE
            domain = VALUES(domain),
            business_name = VALUES(business_name),
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
                "business_name": row.get("business_name") or None,
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

    return len(payload)
