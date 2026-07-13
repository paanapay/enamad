"""CRM database schema and data access."""
from __future__ import annotations

import json
import os
import secrets
from typing import Any

from werkzeug.security import check_password_hash, generate_password_hash

from contact_utils import (
    KAVENEGAR_TOKENS,
    TEMPLATE_VARIABLES,
    build_template_context,
    classify_phone,
    normalize_email,
    render_text_template,
)

ROLE_SUPER = "super_admin"
ROLE_ADMIN = "admin"

CRM_SETTINGS_KEYS = (
    "kavenegar_api_key",
    "smtp_host",
    "smtp_port",
    "smtp_username",
    "smtp_password",
    "smtp_from",
    "smtp_tls",
)

CALL_OUTCOMES = {
    "not_called": "تماس نگرفته",
    "no_answer": "پاسخ نداد",
    "wrong_number": "شماره اشتباه",
    "interested": "علاقه‌مند",
    "not_interested": "عدم تمایل",
    "callback": "تماس مجدد",
    "converted": "تبدیل شده",
}

CALL_OUTCOME_POSITIVE = frozenset({"interested", "callback", "converted"})


def ensure_crm_tables(conn) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_users (
              id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
              username VARCHAR(64) NOT NULL,
              password_hash VARCHAR(255) NOT NULL,
              display_name VARCHAR(128) NULL,
              role VARCHAR(32) NOT NULL DEFAULT 'admin',
              is_active TINYINT(1) NOT NULL DEFAULT 1,
              created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              last_login TIMESTAMP NULL DEFAULT NULL,
              PRIMARY KEY (id),
              UNIQUE KEY uk_admin_username (username)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS crm_settings (
              setting_key VARCHAR(64) NOT NULL,
              setting_value TEXT NULL,
              updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                ON UPDATE CURRENT_TIMESTAMP,
              PRIMARY KEY (setting_key)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS message_templates (
              id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
              name VARCHAR(128) NOT NULL,
              channel VARCHAR(16) NOT NULL,
              provider VARCHAR(32) NOT NULL DEFAULT 'kavenegar',
              description TEXT NULL,
              kavenegar_template VARCHAR(128) NULL,
              token_mapping JSON NULL,
              sms_preview_text TEXT NULL,
              email_subject VARCHAR(512) NULL,
              email_body TEXT NULL,
              is_active TINYINT(1) NOT NULL DEFAULT 1,
              created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                ON UPDATE CURRENT_TIMESTAMP,
              PRIMARY KEY (id),
              KEY idx_templates_channel (channel),
              KEY idx_templates_active (is_active)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS automation_rules (
              id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
              name VARCHAR(128) NOT NULL,
              trigger_type VARCHAR(32) NOT NULL DEFAULT 'new_domain',
              template_id BIGINT UNSIGNED NOT NULL,
              channel VARCHAR(16) NOT NULL,
              mobile_only TINYINT(1) NOT NULL DEFAULT 1,
              is_active TINYINT(1) NOT NULL DEFAULT 1,
              created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                ON UPDATE CURRENT_TIMESTAMP,
              PRIMARY KEY (id),
              KEY idx_rules_active (is_active),
              CONSTRAINT fk_rules_template
                FOREIGN KEY (template_id) REFERENCES message_templates (id)
                ON DELETE RESTRICT
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS message_campaigns (
              id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
              name VARCHAR(128) NOT NULL,
              channel VARCHAR(16) NOT NULL,
              template_id BIGINT UNSIGNED NOT NULL,
              status VARCHAR(32) NOT NULL DEFAULT 'draft',
              target_type VARCHAR(32) NOT NULL DEFAULT 'manual',
              target_domain_ids JSON NULL,
              mobile_only TINYINT(1) NOT NULL DEFAULT 1,
              created_by BIGINT UNSIGNED NULL,
              total_count INT UNSIGNED NOT NULL DEFAULT 0,
              sent_count INT UNSIGNED NOT NULL DEFAULT 0,
              failed_count INT UNSIGNED NOT NULL DEFAULT 0,
              skipped_count INT UNSIGNED NOT NULL DEFAULT 0,
              created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              started_at TIMESTAMP NULL DEFAULT NULL,
              finished_at TIMESTAMP NULL DEFAULT NULL,
              PRIMARY KEY (id),
              KEY idx_campaigns_status (status),
              CONSTRAINT fk_campaigns_template
                FOREIGN KEY (template_id) REFERENCES message_templates (id)
                ON DELETE RESTRICT,
              CONSTRAINT fk_campaigns_admin
                FOREIGN KEY (created_by) REFERENCES admin_users (id)
                ON DELETE SET NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS message_logs (
              id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
              campaign_id BIGINT UNSIGNED NULL,
              automation_rule_id BIGINT UNSIGNED NULL,
              domain_id BIGINT UNSIGNED NULL,
              channel VARCHAR(16) NOT NULL,
              recipient VARCHAR(255) NULL,
              recipient_type VARCHAR(32) NULL,
              status VARCHAR(32) NOT NULL DEFAULT 'pending',
              provider_message_id VARCHAR(64) NULL,
              error_message TEXT NULL,
              template_id BIGINT UNSIGNED NULL,
              sent_at TIMESTAMP NULL DEFAULT NULL,
              created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY (id),
              KEY idx_logs_campaign (campaign_id),
              KEY idx_logs_domain (domain_id),
              KEY idx_logs_status (status),
              KEY idx_logs_created (created_at),
              CONSTRAINT fk_logs_campaign
                FOREIGN KEY (campaign_id) REFERENCES message_campaigns (id)
                ON DELETE SET NULL,
              CONSTRAINT fk_logs_rule
                FOREIGN KEY (automation_rule_id) REFERENCES automation_rules (id)
                ON DELETE SET NULL,
              CONSTRAINT fk_logs_domain
                FOREIGN KEY (domain_id) REFERENCES enamad_domains (id)
                ON DELETE SET NULL,
              CONSTRAINT fk_logs_template
                FOREIGN KEY (template_id) REFERENCES message_templates (id)
                ON DELETE SET NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS crm_call_logs (
              id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
              domain_id BIGINT UNSIGNED NOT NULL,
              created_by BIGINT UNSIGNED NULL,
              phone_used VARCHAR(64) NULL,
              outcome VARCHAR(32) NOT NULL,
              notes TEXT NULL,
              called_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              next_follow_up_at DATE NULL,
              created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY (id),
              KEY idx_calls_domain (domain_id),
              KEY idx_calls_outcome (outcome),
              KEY idx_calls_follow_up (next_follow_up_at),
              KEY idx_calls_called_at (called_at),
              CONSTRAINT fk_calls_domain
                FOREIGN KEY (domain_id) REFERENCES enamad_domains (id)
                ON DELETE CASCADE,
              CONSTRAINT fk_calls_admin
                FOREIGN KEY (created_by) REFERENCES admin_users (id)
                ON DELETE SET NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )

        cursor.execute("SHOW COLUMNS FROM enamad_domains")
        existing = {row["Field"] for row in cursor.fetchall()}
        contact_cols = {
            "phone_type": "VARCHAR(16) NULL",
            "mobile_phone": "VARCHAR(16) NULL",
            "email_normalized": "VARCHAR(255) NULL",
        }
        for name, ddl in contact_cols.items():
            if name not in existing:
                cursor.execute(f"ALTER TABLE enamad_domains ADD COLUMN {name} {ddl}")

        cursor.execute("SHOW COLUMNS FROM message_templates")
        tpl_existing = {row["Field"] for row in cursor.fetchall()}
        if "sms_preview_text" not in tpl_existing:
            cursor.execute(
                "ALTER TABLE message_templates ADD COLUMN sms_preview_text TEXT NULL "
                "AFTER token_mapping"
            )

    ensure_default_super_admin(conn)


def backfill_contact_fields(conn, *, limit: int = 2000) -> int:
    """Classify phone/email for existing rows missing phone_type."""
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, phone, email FROM enamad_domains
            WHERE phone_type IS NULL
            LIMIT %s
            """,
            (limit,),
        )
        rows = list(cursor.fetchall())
    if not rows:
        return 0
    updated = 0
    with conn.cursor() as cursor:
        for row in rows:
            enriched = enrich_contact_fields(row)
            cursor.execute(
                """
                UPDATE enamad_domains
                SET phone_type = %s, mobile_phone = %s, email_normalized = %s
                WHERE id = %s
                """,
                (
                    enriched["phone_type"],
                    enriched["mobile_phone"],
                    enriched["email_normalized"],
                    row["id"],
                ),
            )
            updated += 1
    return updated


def ensure_default_super_admin(conn) -> None:
    """Bootstrap super admin from WEB_ADMIN_PASSWORD if no admins exist."""
    with conn.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) AS c FROM admin_users")
        if int((cursor.fetchone() or {}).get("c") or 0) > 0:
            return
        password = os.environ.get("WEB_ADMIN_PASSWORD", "").strip()
        if not password:
            return
        cursor.execute(
            """
            INSERT INTO admin_users (username, password_hash, display_name, role)
            VALUES (%s, %s, %s, %s)
            """,
            ("admin", generate_password_hash(password), "مدیر اصلی", ROLE_SUPER),
        )


def enrich_contact_fields(row: dict) -> dict:
    phone_type, mobile = classify_phone(row.get("phone"))
    email_norm = normalize_email(row.get("email"))
    enriched = dict(row)
    enriched["phone_type"] = phone_type
    enriched["mobile_phone"] = mobile
    enriched["email_normalized"] = email_norm
    return enriched


def get_setting(conn, key: str) -> str:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT setting_value FROM crm_settings WHERE setting_key = %s",
            (key,),
        )
        row = cursor.fetchone()
    return str((row or {}).get("setting_value") or "")


def get_all_settings(conn) -> dict[str, str]:
    with conn.cursor() as cursor:
        cursor.execute("SELECT setting_key, setting_value FROM crm_settings")
        rows = cursor.fetchall()
    return {row["setting_key"]: row["setting_value"] or "" for row in rows}


def save_settings(conn, settings: dict[str, str]) -> None:
    with conn.cursor() as cursor:
        for key, value in settings.items():
            if key not in CRM_SETTINGS_KEYS:
                continue
            cursor.execute(
                """
                INSERT INTO crm_settings (setting_key, setting_value)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value)
                """,
                (key, value),
            )


def authenticate_admin(conn, username: str, password: str) -> dict | None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, username, password_hash, display_name, role, is_active
            FROM admin_users
            WHERE username = %s
            LIMIT 1
            """,
            (username.strip(),),
        )
        row = cursor.fetchone()
    if not row or not row.get("is_active"):
        return None
    if not check_password_hash(row["password_hash"], password):
        return None
    with conn.cursor() as cursor:
        cursor.execute(
            "UPDATE admin_users SET last_login = CURRENT_TIMESTAMP WHERE id = %s",
            (row["id"],),
        )
    return row


def list_admins(conn) -> list[dict]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, username, display_name, role, is_active, created_at, last_login
            FROM admin_users
            ORDER BY id ASC
            """
        )
        return list(cursor.fetchall())


def create_admin(
    conn,
    *,
    username: str,
    password: str,
    display_name: str | None,
    role: str,
) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO admin_users (username, password_hash, display_name, role)
            VALUES (%s, %s, %s, %s)
            """,
            (
                username.strip(),
                generate_password_hash(password),
                display_name or None,
                role,
            ),
        )
        return int(cursor.lastrowid)


def update_admin(
    conn,
    admin_id: int,
    *,
    display_name: str | None = None,
    role: str | None = None,
    is_active: bool | None = None,
    password: str | None = None,
) -> None:
    fields: list[str] = []
    values: list[Any] = []
    if display_name is not None:
        fields.append("display_name = %s")
        values.append(display_name or None)
    if role is not None:
        fields.append("role = %s")
        values.append(role)
    if is_active is not None:
        fields.append("is_active = %s")
        values.append(1 if is_active else 0)
    if password:
        fields.append("password_hash = %s")
        values.append(generate_password_hash(password))
    if not fields:
        return
    values.append(admin_id)
    with conn.cursor() as cursor:
        cursor.execute(
            f"UPDATE admin_users SET {', '.join(fields)} WHERE id = %s",
            values,
        )


def get_admin_by_id(conn, admin_id: int) -> dict | None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, username, display_name, role, is_active, created_at, last_login
            FROM admin_users WHERE id = %s
            """,
            (admin_id,),
        )
        return cursor.fetchone()


def verify_admin_password(conn, admin_id: int, password: str) -> bool:
    """Check a plaintext password against the stored hash for one admin."""
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT password_hash FROM admin_users WHERE id = %s AND is_active = 1",
            (admin_id,),
        )
        row = cursor.fetchone()
    if not row or not row.get("password_hash"):
        return False
    return check_password_hash(row["password_hash"], password)


def change_admin_password(conn, admin_id: int, new_password: str) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            "UPDATE admin_users SET password_hash = %s WHERE id = %s",
            (generate_password_hash(new_password), admin_id),
        )


def _parse_json_field(value) -> dict:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}


def list_templates(conn, *, channel: str | None = None) -> list[dict]:
    sql = "SELECT * FROM message_templates"
    params: list[Any] = []
    if channel:
        sql += " WHERE channel = %s"
        params.append(channel)
    sql += " ORDER BY id DESC"
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        rows = list(cursor.fetchall())
    for row in rows:
        row["token_mapping"] = _parse_json_field(row.get("token_mapping"))
    return rows


def get_template(conn, template_id: int) -> dict | None:
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM message_templates WHERE id = %s", (template_id,))
        row = cursor.fetchone()
    if row:
        row["token_mapping"] = _parse_json_field(row.get("token_mapping"))
    return row


def save_template(conn, data: dict, *, template_id: int | None = None) -> int:
    token_mapping = json.dumps(data.get("token_mapping") or {}, ensure_ascii=False)
    payload = (
        data["name"],
        data["channel"],
        data.get("provider") or "kavenegar",
        data.get("description") or None,
        data.get("kavenegar_template") or None,
        token_mapping,
        data.get("sms_preview_text") or None,
        data.get("email_subject") or None,
        data.get("email_body") or None,
        1 if data.get("is_active", True) else 0,
    )
    with conn.cursor() as cursor:
        if template_id:
            cursor.execute(
                """
                UPDATE message_templates SET
                  name=%s, channel=%s, provider=%s, description=%s,
                  kavenegar_template=%s, token_mapping=%s, sms_preview_text=%s,
                  email_subject=%s, email_body=%s, is_active=%s
                WHERE id=%s
                """,
                (*payload, template_id),
            )
            return template_id
        cursor.execute(
            """
            INSERT INTO message_templates (
              name, channel, provider, description, kavenegar_template,
              token_mapping, sms_preview_text, email_subject, email_body, is_active
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            payload,
        )
        return int(cursor.lastrowid)


def delete_template(conn, template_id: int) -> None:
    with conn.cursor() as cursor:
        cursor.execute("DELETE FROM message_templates WHERE id = %s", (template_id,))


def list_automation_rules(conn) -> list[dict]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT r.*, t.name AS template_name
            FROM automation_rules r
            JOIN message_templates t ON t.id = r.template_id
            ORDER BY r.id DESC
            """
        )
        return list(cursor.fetchall())


def get_automation_rule(conn, rule_id: int) -> dict | None:
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM automation_rules WHERE id = %s", (rule_id,))
        return cursor.fetchone()


def save_automation_rule(conn, data: dict, *, rule_id: int | None = None) -> int:
    payload = (
        data["name"],
        data.get("trigger_type") or "new_domain",
        int(data["template_id"]),
        data["channel"],
        1 if data.get("mobile_only", True) else 0,
        1 if data.get("is_active", True) else 0,
    )
    with conn.cursor() as cursor:
        if rule_id:
            cursor.execute(
                """
                UPDATE automation_rules SET
                  name=%s, trigger_type=%s, template_id=%s, channel=%s,
                  mobile_only=%s, is_active=%s
                WHERE id=%s
                """,
                (*payload, rule_id),
            )
            return rule_id
        cursor.execute(
            """
            INSERT INTO automation_rules (
              name, trigger_type, template_id, channel, mobile_only, is_active
            ) VALUES (%s,%s,%s,%s,%s,%s)
            """,
            payload,
        )
        return int(cursor.lastrowid)


def delete_automation_rule(conn, rule_id: int) -> None:
    with conn.cursor() as cursor:
        cursor.execute("DELETE FROM automation_rules WHERE id = %s", (rule_id,))


def list_campaigns(conn, *, limit: int = 50) -> list[dict]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.*, t.name AS template_name, a.username AS created_by_name
            FROM message_campaigns c
            JOIN message_templates t ON t.id = c.template_id
            LEFT JOIN admin_users a ON a.id = c.created_by
            ORDER BY c.id DESC
            LIMIT %s
            """,
            (limit,),
        )
        return list(cursor.fetchall())


def get_campaign(conn, campaign_id: int) -> dict | None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.*, t.name AS template_name
            FROM message_campaigns c
            JOIN message_templates t ON t.id = c.template_id
            WHERE c.id = %s
            """,
            (campaign_id,),
        )
        row = cursor.fetchone()
    if row:
        row["target_domain_ids"] = _parse_json_field(row.get("target_domain_ids"))
        if isinstance(row["target_domain_ids"], list):
            pass
        elif row["target_domain_ids"]:
            row["target_domain_ids"] = list(row["target_domain_ids"].values())
        else:
            row["target_domain_ids"] = []
    return row


def create_campaign(conn, data: dict) -> int:
    domain_ids = data.get("target_domain_ids") or []
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO message_campaigns (
              name, channel, template_id, status, target_type,
              target_domain_ids, mobile_only, created_by, total_count
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                data["name"],
                data["channel"],
                int(data["template_id"]),
                data.get("status") or "draft",
                data.get("target_type") or "manual",
                json.dumps(domain_ids),
                1 if data.get("mobile_only", True) else 0,
                data.get("created_by"),
                len(domain_ids),
            ),
        )
        return int(cursor.lastrowid)


def update_campaign_counts(
    conn,
    campaign_id: int,
    *,
    status: str | None = None,
    sent: int = 0,
    failed: int = 0,
    skipped: int = 0,
    started: bool = False,
    finished: bool = False,
) -> None:
    fields = [
        "sent_count = sent_count + %s",
        "failed_count = failed_count + %s",
        "skipped_count = skipped_count + %s",
    ]
    values: list[Any] = [sent, failed, skipped]
    if status:
        fields.append("status = %s")
        values.append(status)
    if started:
        fields.append("started_at = COALESCE(started_at, CURRENT_TIMESTAMP)")
    if finished:
        fields.append("finished_at = CURRENT_TIMESTAMP")
    values.append(campaign_id)
    with conn.cursor() as cursor:
        cursor.execute(
            f"UPDATE message_campaigns SET {', '.join(fields)} WHERE id = %s",
            values,
        )


def insert_message_log(conn, data: dict) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO message_logs (
              campaign_id, automation_rule_id, domain_id, channel,
              recipient, recipient_type, status, provider_message_id,
              error_message, template_id, sent_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                data.get("campaign_id"),
                data.get("automation_rule_id"),
                data.get("domain_id"),
                data["channel"],
                data.get("recipient"),
                data.get("recipient_type"),
                data.get("status") or "pending",
                data.get("provider_message_id"),
                data.get("error_message"),
                data.get("template_id"),
                data.get("sent_at"),
            ),
        )
        return int(cursor.lastrowid)


def _message_log_filters(
    *,
    campaign_id: int | None = None,
    channel: str = "",
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    search: str = "",
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if campaign_id:
        clauses.append("l.campaign_id = %s")
        params.append(campaign_id)
    if channel:
        clauses.append("l.channel = %s")
        params.append(channel)
    if status:
        clauses.append("l.status = %s")
        params.append(status)
    if date_from:
        clauses.append("COALESCE(l.sent_at, l.created_at) >= %s")
        params.append(f"{date_from} 00:00:00")
    if date_to:
        clauses.append("COALESCE(l.sent_at, l.created_at) <= %s")
        params.append(f"{date_to} 23:59:59")
    if search:
        pattern = f"%{search}%"
        clauses.append(
            "(d.domain LIKE %s OR d.business_name LIKE %s OR l.recipient LIKE %s)"
        )
        params.extend([pattern, pattern, pattern])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def list_message_logs(
    conn,
    *,
    campaign_id: int | None = None,
    channel: str = "",
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    search: str = "",
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    where, params = _message_log_filters(
        campaign_id=campaign_id,
        channel=channel,
        status=status,
        date_from=date_from,
        date_to=date_to,
        search=search,
    )
    sql = f"""
        SELECT l.*, d.domain, d.business_name,
               c.name AS campaign_name,
               t.name AS template_name
        FROM message_logs l
        LEFT JOIN enamad_domains d ON d.id = l.domain_id
        LEFT JOIN message_campaigns c ON c.id = l.campaign_id
        LEFT JOIN message_templates t ON t.id = l.template_id
        {where}
        ORDER BY l.id DESC
        LIMIT %s OFFSET %s
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, [*params, limit, offset])
        return list(cursor.fetchall())


def count_message_logs(
    conn,
    *,
    campaign_id: int | None = None,
    channel: str = "",
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    search: str = "",
) -> int:
    where, params = _message_log_filters(
        campaign_id=campaign_id,
        channel=channel,
        status=status,
        date_from=date_from,
        date_to=date_to,
        search=search,
    )
    sql = f"""
        SELECT COUNT(*) AS c
        FROM message_logs l
        LEFT JOIN enamad_domains d ON d.id = l.domain_id
        {where}
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        return int((cursor.fetchone() or {}).get("c") or 0)


def message_log_stats(
    conn,
    *,
    channel: str = "",
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    search: str = "",
) -> dict[str, Any]:
    """Aggregate message-log counts grouped by channel and status."""
    where, params = _message_log_filters(
        channel=channel,
        status=status,
        date_from=date_from,
        date_to=date_to,
        search=search,
    )
    sql = f"""
        SELECT l.channel, l.status, COUNT(*) AS c
        FROM message_logs l
        LEFT JOIN enamad_domains d ON d.id = l.domain_id
        {where}
        GROUP BY l.channel, l.status
    """
    stats = {
        "total": 0,
        "sms": {"total": 0, "sent": 0, "failed": 0, "skipped": 0, "pending": 0},
        "email": {"total": 0, "sent": 0, "failed": 0, "skipped": 0, "pending": 0},
    }
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        for row in cursor.fetchall():
            ch = row["channel"] if row["channel"] in stats else None
            st = row["status"] or "pending"
            count = int(row["c"] or 0)
            stats["total"] += count
            if ch:
                stats[ch]["total"] += count
                stats[ch][st] = stats[ch].get(st, 0) + count
    return stats


def iter_message_logs_for_export(
    conn,
    *,
    channel: str = "",
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    search: str = "",
    batch: int = 1000,
):
    """Yield message-log rows for CSV export in batches (streaming-friendly)."""
    offset = 0
    while True:
        rows = list_message_logs(
            conn,
            channel=channel,
            status=status,
            date_from=date_from,
            date_to=date_to,
            search=search,
            limit=batch,
            offset=offset,
        )
        if not rows:
            break
        yield from rows
        if len(rows) < batch:
            break
        offset += batch


_DOMAIN_OUTREACH_FIELDS = """
    d.id, d.domain, d.business_name, d.owner_name, d.phone, d.email,
    d.phone_type, d.mobile_phone, d.email_normalized,
    d.province, d.city, d.approve_date, d.expire_date, d.rating,
    EXISTS(
        SELECT 1 FROM message_logs ml
        WHERE ml.domain_id = d.id AND ml.channel = 'sms' AND ml.status = 'sent'
    ) AS sms_sent,
    EXISTS(
        SELECT 1 FROM message_logs ml
        WHERE ml.domain_id = d.id AND ml.channel = 'email' AND ml.status = 'sent'
    ) AS email_sent,
    EXISTS(
        SELECT 1 FROM crm_call_logs cl WHERE cl.domain_id = d.id
    ) AS has_call
"""


def _campaign_domain_filters(
    query: str, phone_type: str
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if query:
        pattern = f"%{query}%"
        clauses.append(
            "(d.domain LIKE %s OR d.business_name LIKE %s OR d.owner_name LIKE %s)"
        )
        params.extend([pattern, pattern, pattern])
    if phone_type == "mobile":
        clauses.append("d.phone_type IN ('mobile', 'mixed')")
    elif phone_type:
        clauses.append("d.phone_type = %s")
        params.append(phone_type)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def list_domains_for_campaign(
    conn,
    *,
    query: str = "",
    phone_type: str = "",
    offset: int = 0,
    limit: int = 50,
) -> list[dict]:
    where, params = _campaign_domain_filters(query, phone_type)
    if query:
        order = """
            ORDER BY
                CASE WHEN d.domain = %s THEN 0
                     WHEN d.domain LIKE %s THEN 1
                     ELSE 2 END,
                d.updated_at DESC, d.id ASC
        """
        order_params = [query, f"{query}%"]
    else:
        order = "ORDER BY d.source_page ASC, d.source_row ASC, d.id ASC"
        order_params = []
    sql = f"""
        SELECT {_DOMAIN_OUTREACH_FIELDS}
        FROM enamad_domains d
        {where}
        {order}
        LIMIT %s OFFSET %s
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, [*params, *order_params, limit, offset])
        return list(cursor.fetchall())


def count_domains_for_campaign(
    conn, *, query: str = "", phone_type: str = ""
) -> int:
    where, params = _campaign_domain_filters(query, phone_type)
    with conn.cursor() as cursor:
        cursor.execute(
            f"SELECT COUNT(*) AS c FROM enamad_domains d {where}",
            params,
        )
        return int((cursor.fetchone() or {}).get("c") or 0)


def get_domains_by_ids(conn, domain_ids: list[int]) -> list[dict]:
    if not domain_ids:
        return []
    placeholders = ",".join(["%s"] * len(domain_ids))
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT id, domain, business_name, owner_name, phone, email,
                   phone_type, mobile_phone, email_normalized,
                   province, city, approve_date, expire_date
            FROM enamad_domains
            WHERE id IN ({placeholders})
            """,
            domain_ids,
        )
        return list(cursor.fetchall())


def get_active_rules_for_trigger(conn, trigger_type: str) -> list[dict]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT r.*, t.*
            FROM automation_rules r
            JOIN message_templates t ON t.id = r.template_id
            WHERE r.is_active = 1 AND t.is_active = 1
              AND r.trigger_type = %s
            """,
            (trigger_type,),
        )
        rows = list(cursor.fetchall())
    for row in rows:
        row["token_mapping"] = _parse_json_field(row.get("token_mapping"))
    return rows


def build_kavenegar_tokens(template: dict, domain_row: dict) -> dict[str, str]:
    mapping = _parse_json_field(template.get("token_mapping"))
    context = build_template_context(domain_row)
    tokens: dict[str, str] = {}
    for kn_token in KAVENEGAR_TOKENS:
        var_name = mapping.get(kn_token, "")
        if var_name:
            tokens[kn_token] = str(context.get(var_name) or "-")[:100]
    if not tokens.get("token"):
        tokens["token"] = str(context.get("domain") or "-")[:100]
    return tokens


def preview_template(template: dict, domain_row: dict) -> str:
    context = build_template_context(domain_row)
    if template.get("channel") == "email":
        subject = render_text_template(template.get("email_subject") or "", context)
        body = render_text_template(template.get("email_body") or "", context)
        return f"موضوع: {subject}\n\n{body}"
    mapping = _parse_json_field(template.get("token_mapping"))
    parts = [f"الگوی کاوه‌نگار: {template.get('kavenegar_template') or '—'}"]
    for kn_token in KAVENEGAR_TOKENS:
        var_name = mapping.get(kn_token)
        if var_name:
            parts.append(f"{kn_token} ← {TEMPLATE_VARIABLES.get(var_name, var_name)}: {context.get(var_name, '')}")
    return "\n".join(parts)


def create_call_log(conn, data: dict) -> int:
    outcome = data.get("outcome") or "not_called"
    if outcome not in CALL_OUTCOMES:
        raise ValueError("نتیجه تماس نامعتبر است")
    called_at = data.get("called_at")
    with conn.cursor() as cursor:
        if called_at:
            cursor.execute(
                """
                INSERT INTO crm_call_logs (
                  domain_id, created_by, phone_used, outcome, notes,
                  called_at, next_follow_up_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    int(data["domain_id"]),
                    data.get("created_by"),
                    data.get("phone_used") or None,
                    outcome,
                    data.get("notes") or None,
                    called_at,
                    data.get("next_follow_up_at") or None,
                ),
            )
        else:
            cursor.execute(
                """
                INSERT INTO crm_call_logs (
                  domain_id, created_by, phone_used, outcome, notes,
                  next_follow_up_at
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    int(data["domain_id"]),
                    data.get("created_by"),
                    data.get("phone_used") or None,
                    outcome,
                    data.get("notes") or None,
                    data.get("next_follow_up_at") or None,
                ),
            )
        return int(cursor.lastrowid)


def get_call_log(conn, call_id: int) -> dict | None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.*, d.domain, d.business_name, d.owner_name,
                   a.username AS created_by_name, a.display_name AS created_by_display
            FROM crm_call_logs c
            JOIN enamad_domains d ON d.id = c.domain_id
            LEFT JOIN admin_users a ON a.id = c.created_by
            WHERE c.id = %s
            """,
            (call_id,),
        )
        return cursor.fetchone()


def list_call_logs(
    conn,
    *,
    filter_type: str = "all",
    outcome: str | None = None,
    domain_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    sql = """
        SELECT c.*, d.domain, d.business_name, d.owner_name, d.mobile_phone,
               a.username AS created_by_name, a.display_name AS created_by_display
        FROM crm_call_logs c
        JOIN enamad_domains d ON d.id = c.domain_id
        LEFT JOIN admin_users a ON a.id = c.created_by
        WHERE 1=1
    """
    params: list[Any] = []

    if domain_id:
        sql += " AND c.domain_id = %s"
        params.append(domain_id)

    if outcome:
        sql += " AND c.outcome = %s"
        params.append(outcome)
    elif filter_type == "today":
        sql += """
          AND c.next_follow_up_at IS NOT NULL
          AND c.next_follow_up_at <= CURDATE()
          AND c.outcome IN ('interested', 'callback')
        """
    elif filter_type == "interested":
        sql += " AND c.outcome IN ('interested', 'callback')"

    sql += " ORDER BY c.called_at DESC, c.id DESC LIMIT %s OFFSET %s"
    params.extend([limit, offset])

    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        return list(cursor.fetchall())


def count_call_logs(
    conn,
    *,
    filter_type: str = "all",
    outcome: str | None = None,
    domain_id: int | None = None,
) -> int:
    sql = "SELECT COUNT(*) AS c FROM crm_call_logs c WHERE 1=1"
    params: list[Any] = []

    if domain_id:
        sql += " AND c.domain_id = %s"
        params.append(domain_id)
    if outcome:
        sql += " AND c.outcome = %s"
        params.append(outcome)
    elif filter_type == "today":
        sql += """
          AND c.next_follow_up_at IS NOT NULL
          AND c.next_follow_up_at <= CURDATE()
          AND c.outcome IN ('interested', 'callback')
        """
    elif filter_type == "interested":
        sql += " AND c.outcome IN ('interested', 'callback')"

    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        return int((cursor.fetchone() or {}).get("c") or 0)


def get_latest_call_for_domain(conn, domain_id: int) -> dict | None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.*, a.username AS created_by_name, a.display_name AS created_by_display
            FROM crm_call_logs c
            LEFT JOIN admin_users a ON a.id = c.created_by
            WHERE c.domain_id = %s
            ORDER BY c.called_at DESC, c.id DESC
            LIMIT 1
            """,
            (domain_id,),
        )
        return cursor.fetchone()


def get_call_logs_for_domain(conn, domain_id: int, *, limit: int = 20) -> list[dict]:
    return list_call_logs(conn, domain_id=domain_id, limit=limit, offset=0)


def call_stats(conn) -> dict[str, int]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(
                    next_follow_up_at IS NOT NULL
                    AND next_follow_up_at <= CURDATE()
                    AND outcome IN ('interested', 'callback')
                ) AS follow_up_today,
                SUM(outcome IN ('interested', 'callback')) AS interested,
                SUM(outcome = 'converted') AS converted
            FROM crm_call_logs
            """
        )
        row = cursor.fetchone() or {}
    return {
        "total": int(row.get("total") or 0),
        "follow_up_today": int(row.get("follow_up_today") or 0),
        "interested": int(row.get("interested") or 0),
        "converted": int(row.get("converted") or 0),
    }


def crm_stats(conn) -> dict[str, int]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM message_templates WHERE is_active = 1) AS templates,
                (SELECT COUNT(*) FROM automation_rules WHERE is_active = 1) AS rules,
                (SELECT COUNT(*) FROM message_campaigns) AS campaigns,
                SUM(phone_type IN ('mobile', 'mixed')) AS mobile_domains,
                SUM(phone_type = 'landline') AS landline_domains,
                SUM(
                    email_normalized IS NOT NULL AND email_normalized != ''
                ) AS email_domains
            FROM enamad_domains
            """
        )
        row = cursor.fetchone() or {}
        calls = call_stats(conn)
    return {
        "templates": int(row.get("templates") or 0),
        "rules": int(row.get("rules") or 0),
        "campaigns": int(row.get("campaigns") or 0),
        "mobile_domains": int(row.get("mobile_domains") or 0),
        "landline_domains": int(row.get("landline_domains") or 0),
        "email_domains": int(row.get("email_domains") or 0),
        "calls_total": calls["total"],
        "calls_follow_up_today": calls["follow_up_today"],
        "calls_interested": calls["interested"],
        "calls_converted": calls["converted"],
    }
