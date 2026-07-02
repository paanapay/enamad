from __future__ import annotations

from typing import Any

DOMAIN_FIELDS = """
    id, enamad_id, code, domain, business_name, owner_name,
    business_address, phone, email, work_hours,
    province, city, rating, approve_date, expire_date,
    trustseal_url, updated_at, created_at
"""


def get_stats(conn) -> dict[str, Any]:
    with conn.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) AS total FROM enamad_domains")
        total = int(cursor.fetchone()["total"])

        cursor.execute(
            """
            SELECT COUNT(*) AS rated
            FROM enamad_domains
            WHERE rating >= 1
            """
        )
        rated = int(cursor.fetchone()["rated"])

        cursor.execute(
            """
            SELECT state_key, state_value
            FROM scraper_state
            WHERE state_key IN ('last_completed_page', 'total_pages')
            """
        )
        scrape_rows = {row["state_key"]: row["state_value"] for row in cursor.fetchall()}

        cursor.execute(
            """
            SELECT pages_fetched, records_saved, status, started_at, finished_at
            FROM scrape_runs
            ORDER BY id DESC
            LIMIT 1
            """
        )
        last_run = cursor.fetchone()

        cursor.execute(
            """
            SELECT MAX(updated_at) AS last_update
            FROM enamad_domains
            """
        )
        last_update = cursor.fetchone().get("last_update")

    return {
        "total": total,
        "rated": rated,
        "scrape": scrape_rows,
        "last_run": last_run,
        "last_update": last_update,
    }


def get_domain_exact(conn, domain: str) -> dict | None:
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT {DOMAIN_FIELDS}
            FROM enamad_domains
            WHERE domain = %s
            LIMIT 1
            """,
            (domain,),
        )
        return cursor.fetchone()


def search_domains(conn, query: str, *, limit: int = 8) -> list[dict]:
    pattern = f"%{query}%"
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT {DOMAIN_FIELDS}
            FROM enamad_domains
            WHERE domain LIKE %s
               OR business_name LIKE %s
               OR owner_name LIKE %s
            ORDER BY
                CASE WHEN domain = %s THEN 0
                     WHEN domain LIKE %s THEN 1
                     ELSE 2 END,
                updated_at DESC
            LIMIT %s
            """,
            (pattern, pattern, pattern, query, f"{query}%", limit),
        )
        return list(cursor.fetchall())


def get_latest_domains(conn, *, offset: int, limit: int) -> list[dict]:
    """Domains in the same order as the enamad.ir site list (source_page/row)."""
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT {DOMAIN_FIELDS}
            FROM enamad_domains
            ORDER BY source_page ASC, source_row ASC, id ASC
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
        )
        return list(cursor.fetchall())


def get_newest_by_approve(conn, *, offset: int, limit: int) -> list[dict]:
    """Newest domains strictly by Enamad issue date (approve_date DESC)."""
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT {DOMAIN_FIELDS}
            FROM enamad_domains
            WHERE approve_date IS NOT NULL AND approve_date != ''
            ORDER BY approve_date DESC, id DESC
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
        )
        return list(cursor.fetchall())


def get_top_rated(conn, *, offset: int, limit: int, min_rating: int = 4) -> list[dict]:
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT {DOMAIN_FIELDS}
            FROM enamad_domains
            WHERE rating >= %s
            ORDER BY rating DESC, updated_at DESC
            LIMIT %s OFFSET %s
            """,
            (min_rating, limit, offset),
        )
        return list(cursor.fetchall())


def count_with_approve(conn) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT COUNT(*) AS c
            FROM enamad_domains
            WHERE approve_date IS NOT NULL AND approve_date != ''
            """
        )
        return int(cursor.fetchone()["c"])


def count_top_rated(conn, min_rating: int = 4) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) AS c FROM enamad_domains WHERE rating >= %s",
            (min_rating,),
        )
        return int(cursor.fetchone()["c"])


def get_domain_by_id(conn, domain_id: int) -> dict | None:
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT {DOMAIN_FIELDS}
            FROM enamad_domains
            WHERE id = %s
            """,
            (domain_id,),
        )
        return cursor.fetchone()


def count_domains(conn) -> int:
    with conn.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) AS c FROM enamad_domains")
        return int(cursor.fetchone()["c"])


def count_search(conn, query: str) -> int:
    pattern = f"%{query}%"
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT COUNT(*) AS c
            FROM enamad_domains
            WHERE domain LIKE %s OR business_name LIKE %s OR owner_name LIKE %s
            """,
            (pattern, pattern, pattern),
        )
        return int(cursor.fetchone()["c"])


def get_provinces(conn, *, limit: int = 20) -> list[dict]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT province, COUNT(*) AS cnt
            FROM enamad_domains
            WHERE province IS NOT NULL AND province != ''
            GROUP BY province
            ORDER BY cnt DESC, province ASC
            LIMIT %s
            """,
            (limit,),
        )
        return list(cursor.fetchall())


def get_domains_by_province(
    conn, province: str, *, offset: int, limit: int
) -> list[dict]:
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT {DOMAIN_FIELDS}
            FROM enamad_domains
            WHERE province = %s
            ORDER BY updated_at DESC
            LIMIT %s OFFSET %s
            """,
            (province, limit, offset),
        )
        return list(cursor.fetchall())


def count_by_province(conn, province: str) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) AS c FROM enamad_domains WHERE province = %s",
            (province,),
        )
        return int(cursor.fetchone()["c"])


def get_domain_services(conn, enamad_id: str, code: str) -> list[dict]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT service_title, license_issuer, status, valid_from, valid_to
            FROM enamad_domain_services
            WHERE enamad_id = %s AND code = %s
            ORDER BY row_num ASC
            LIMIT 50
            """,
            (enamad_id, code),
        )
        return list(cursor.fetchall())


def get_bot_users(conn, *, offset: int, limit: int) -> list[dict]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT user_id, username, first_name, last_name,
                   interaction_count, last_action, first_seen, last_seen
            FROM bot_users
            ORDER BY last_seen DESC
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
        )
        return list(cursor.fetchall())


def count_bot_users(conn) -> int:
    with conn.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) AS c FROM bot_users")
        return int(cursor.fetchone()["c"])


def get_bot_user_stats(conn) -> dict:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(interaction_count), 0) AS interactions,
                SUM(last_seen >= (NOW() - INTERVAL 1 DAY)) AS active_1d,
                SUM(last_seen >= (NOW() - INTERVAL 7 DAY)) AS active_7d
            FROM bot_users
            """
        )
        row = cursor.fetchone() or {}
    return {
        "total": int(row.get("total") or 0),
        "interactions": int(row.get("interactions") or 0),
        "active_1d": int(row.get("active_1d") or 0),
        "active_7d": int(row.get("active_7d") or 0),
    }
