from __future__ import annotations

from typing import Any

DOMAIN_FIELDS = """
    id, enamad_id, code, domain, business_name, owner_name,
    business_address, phone, email, work_hours,
    province, city, rating, approve_date, expire_date,
    trustseal_url, phone_type, mobile_phone, email_normalized,
    enamad_status, updated_at, created_at
"""

ENAMAD_STATUS_NOT_FOUND = "not_found"


def get_stats(conn) -> dict[str, Any]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(rating >= 1) AS rated,
                MAX(updated_at) AS last_update,
                MAX(source_page) AS max_page,
                COUNT(DISTINCT source_page) AS distinct_pages
            FROM enamad_domains
            """
        )
        summary = cursor.fetchone() or {}
        total = int(summary.get("total") or 0)
        rated = int(summary.get("rated") or 0)
        last_update = summary.get("last_update")
        page_coverage = {
            "max_page": summary.get("max_page"),
            "distinct_pages": summary.get("distinct_pages"),
        }

        cursor.execute(
            """
            SELECT state_key, state_value
            FROM scraper_state
            """
        )
        all_state = {row["state_key"]: row["state_value"] for row in cursor.fetchall()}

        worker_pages: list[int] = []
        for key, value in all_state.items():
            if key.startswith("parallel_w") and key.endswith("_last"):
                try:
                    worker_pages.append(int(value))
                except (TypeError, ValueError):
                    continue

        total_pages_raw = all_state.get("total_pages")
        total_pages = int(total_pages_raw) if total_pages_raw else None
        global_last = int(all_state.get("last_completed_page") or 0)
        worker_max = max(worker_pages) if worker_pages else 0
        max_source = int(page_coverage.get("max_page") or 0)
        distinct_pages = int(page_coverage.get("distinct_pages") or 0)
        effective_last = max(global_last, worker_max, max_source)

        scrape_rows = {
            "last_completed_page": str(global_last),
            "total_pages": total_pages_raw,
            "effective_last_page": effective_last,
            "distinct_pages_in_db": distinct_pages,
            "worker_count": len(worker_pages),
        }

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
            SELECT pages_fetched, records_saved, status, started_at, finished_at
            FROM scrape_runs
            WHERE pages_fetched >= 10
            ORDER BY id DESC
            LIMIT 1
            """
        )
        last_major_run = cursor.fetchone()

    return {
        "total": total,
        "rated": rated,
        "scrape": scrape_rows,
        "last_run": last_run,
        "last_major_run": last_major_run,
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


def get_sample_domain(conn) -> dict | None:
    """A real domain with the most complete contact info, for previews."""
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT {DOMAIN_FIELDS}
            FROM enamad_domains
            WHERE business_name IS NOT NULL AND business_name != ''
              AND owner_name IS NOT NULL AND owner_name != ''
              AND mobile_phone IS NOT NULL AND mobile_phone != ''
              AND email_normalized IS NOT NULL AND email_normalized != ''
            ORDER BY id ASC
            LIMIT 1
            """
        )
        row = cursor.fetchone()
    if row:
        return row
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT {DOMAIN_FIELDS}
            FROM enamad_domains
            WHERE business_name IS NOT NULL AND business_name != ''
              AND mobile_phone IS NOT NULL AND mobile_phone != ''
            ORDER BY id ASC
            LIMIT 1
            """
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


DOMAIN_SORTS = {
    "latest": "source_page ASC, source_row ASC, id ASC",
    "approve": "approve_date DESC, id DESC",
    "top": "rating DESC, updated_at DESC",
    "newest": "created_at DESC, id DESC",
}


def _build_domain_where(
    *,
    province: str = "",
    city: str = "",
    phone_type: str = "",
    category: str = "",
    approve_from: str = "",
    approve_to: str = "",
    created_from: str = "",
    created_to: str = "",
) -> tuple[list[str], list[Any]]:
    """Build a list of WHERE conditions + params from optional filters."""
    where: list[str] = []
    params: list[Any] = []
    if province:
        where.append("province = %s")
        params.append(province)
    # category = business service/permit title from enamad_domain_services.
    if category:
        where.append(
            "EXISTS (SELECT 1 FROM enamad_domain_services s "
            "WHERE s.enamad_id = enamad_domains.enamad_id "
            "AND s.code = enamad_domains.code AND s.service_title = %s)"
        )
        params.append(category)
    if city:
        where.append("city = %s")
        params.append(city)
    if phone_type == "mobile":
        where.append("phone_type IN ('mobile', 'mixed')")
    elif phone_type:
        where.append("phone_type = %s")
        params.append(phone_type)
    # approve_date is stored as a zero-padded ascii Jalali string (YYYY/MM/DD),
    # so lexicographic comparison matches chronological order.
    if approve_from:
        where.append("approve_date >= %s")
        params.append(approve_from)
    if approve_to:
        where.append("approve_date <= %s")
        params.append(approve_to)
    # created_at is a gregorian TIMESTAMP; compare against ISO date bounds.
    if created_from:
        where.append("created_at >= %s")
        params.append(created_from)
    if created_to:
        where.append("created_at < DATE_ADD(%s, INTERVAL 1 DAY)")
        params.append(created_to)
    return where, params


def get_domains_filtered(
    conn, *, filters: dict, sort: str = "latest", offset: int = 0, limit: int = 50
) -> list[dict]:
    where, params = _build_domain_where(**filters)
    if sort == "approve":
        where.append("approve_date IS NOT NULL AND approve_date <> ''")
    if sort == "top":
        where.append("rating >= 4")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    order = DOMAIN_SORTS.get(sort, DOMAIN_SORTS["latest"])
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT {DOMAIN_FIELDS}
            FROM enamad_domains
            {clause}
            ORDER BY {order}
            LIMIT %s OFFSET %s
            """,
            (*params, limit, offset),
        )
        return list(cursor.fetchall())


def count_domains_filtered(conn, *, filters: dict, sort: str = "latest") -> int:
    where, params = _build_domain_where(**filters)
    if sort == "approve":
        where.append("approve_date IS NOT NULL AND approve_date <> ''")
    if sort == "top":
        where.append("rating >= 4")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    with conn.cursor() as cursor:
        cursor.execute(
            f"SELECT COUNT(*) AS c FROM enamad_domains {clause}",
            tuple(params),
        )
        return int(cursor.fetchone()["c"])


def get_all_provinces(conn) -> list[str]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT province
            FROM enamad_domains
            WHERE province IS NOT NULL AND province <> ''
            GROUP BY province
            ORDER BY province ASC
            """
        )
        return [row["province"] for row in cursor.fetchall()]


def get_province_cities(conn) -> list[dict]:
    """Distinct (province, city) pairs, for a dependent city dropdown."""
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT province, city
            FROM enamad_domains
            WHERE city IS NOT NULL AND city <> ''
              AND province IS NOT NULL AND province <> ''
            GROUP BY province, city
            ORDER BY province ASC, city ASC
            """
        )
        return list(cursor.fetchall())


def get_service_categories(conn, *, limit: int = 300) -> list[str]:
    """Distinct service/permit titles, most common first, for a category filter."""
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT service_title
            FROM enamad_domain_services
            WHERE service_title IS NOT NULL AND service_title <> ''
            GROUP BY service_title
            ORDER BY COUNT(*) DESC, service_title ASC
            LIMIT %s
            """,
            (limit,),
        )
        return [row["service_title"] for row in cursor.fetchall()]


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
            SELECT platform, user_id, username, first_name, last_name,
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


def get_all_bot_user_targets(conn, *, platform: str = "") -> list[tuple[str, int]]:
    """All (platform, user_id) pairs for broadcast, optionally one platform."""
    sql = "SELECT platform, user_id FROM bot_users"
    params: list = []
    if platform:
        sql += " WHERE platform = %s"
        params.append(platform)
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        return [(row["platform"], int(row["user_id"])) for row in cursor.fetchall()]


def get_domains_by_phone_type(
    conn, phone_type: str, *, offset: int = 0, limit: int = 50
) -> list[dict]:
    with conn.cursor() as cursor:
        if phone_type == "mobile":
            cursor.execute(
                f"""
                SELECT {DOMAIN_FIELDS}
                FROM enamad_domains
                WHERE phone_type IN ('mobile', 'mixed')
                ORDER BY updated_at DESC
                LIMIT %s OFFSET %s
                """,
                (limit, offset),
            )
        else:
            cursor.execute(
                f"""
                SELECT {DOMAIN_FIELDS}
                FROM enamad_domains
                WHERE phone_type = %s
                ORDER BY updated_at DESC
                LIMIT %s OFFSET %s
                """,
                (phone_type, limit, offset),
            )
        return list(cursor.fetchall())


def count_by_phone_type(conn, phone_type: str) -> int:
    with conn.cursor() as cursor:
        if phone_type == "mobile":
            cursor.execute(
                """
                SELECT COUNT(*) AS c FROM enamad_domains
                WHERE phone_type IN ('mobile', 'mixed')
                """
            )
        else:
            cursor.execute(
                "SELECT COUNT(*) AS c FROM enamad_domains WHERE phone_type = %s",
                (phone_type,),
            )
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
