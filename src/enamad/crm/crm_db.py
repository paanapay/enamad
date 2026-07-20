"""CRM database schema and data access."""
from __future__ import annotations

import json
import os
import re
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

PROJECT_OWNER = "owner"
PROJECT_ADMIN = "admin"
PROJECT_MEMBER = "member"
PROJECT_ROLES = (PROJECT_OWNER, PROJECT_ADMIN, PROJECT_MEMBER)
PROJECT_ADMIN_ROLES = frozenset({PROJECT_OWNER, PROJECT_ADMIN})

DEFAULT_PROJECT_SLUG = "default"
DEFAULT_PROJECT_NAME = "پروژه اصلی"

CRM_SETTINGS_KEYS = (
    "kavenegar_api_key",
    "smtp_host",
    "smtp_port",
    "smtp_username",
    "smtp_password",
    "smtp_from",
    "smtp_tls",
    # "no" = accept self-signed certificates (skip TLS verification).
    "smtp_ssl_verify",
    # "yes" = test/dry-run mode: nothing is really sent; messages are only logged.
    "dry_run",
)


def is_dry_run(settings: dict[str, str] | None) -> bool:
    """True when CRM test mode is on (no real SMS/email leaves the system)."""
    return bool(settings) and str(settings.get("dry_run") or "").lower() == "yes"

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


def _column_exists(cursor, table: str, column: str) -> bool:
    cursor.execute(
        """
        SELECT 1 FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s AND COLUMN_NAME = %s
        LIMIT 1
        """,
        (table, column),
    )
    return cursor.fetchone() is not None


def _index_exists(cursor, table: str, index_name: str) -> bool:
    cursor.execute(
        """
        SELECT 1 FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s AND INDEX_NAME = %s
        LIMIT 1
        """,
        (table, index_name),
    )
    return cursor.fetchone() is not None


def _add_column_if_missing(cursor, table: str, column: str, ddl: str) -> None:
    if not _column_exists(cursor, table, column):
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _add_index_if_missing(cursor, table: str, index_name: str, ddl: str) -> None:
    if not _index_exists(cursor, table, index_name):
        cursor.execute(f"ALTER TABLE {table} ADD {ddl}")


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
            CREATE TABLE IF NOT EXISTS projects (
              id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
              name VARCHAR(128) NOT NULL,
              slug VARCHAR(64) NOT NULL,
              is_active TINYINT(1) NOT NULL DEFAULT 1,
              created_by BIGINT UNSIGNED NULL,
              created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                ON UPDATE CURRENT_TIMESTAMP,
              PRIMARY KEY (id),
              UNIQUE KEY uk_projects_slug (slug),
              KEY idx_projects_active (is_active),
              CONSTRAINT fk_projects_creator
                FOREIGN KEY (created_by) REFERENCES admin_users (id)
                ON DELETE SET NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS project_members (
              id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
              project_id BIGINT UNSIGNED NOT NULL,
              user_id BIGINT UNSIGNED NOT NULL,
              role VARCHAR(32) NOT NULL DEFAULT 'admin',
              created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY (id),
              UNIQUE KEY uk_project_member (project_id, user_id),
              KEY idx_members_user (user_id),
              CONSTRAINT fk_members_project
                FOREIGN KEY (project_id) REFERENCES projects (id)
                ON DELETE CASCADE,
              CONSTRAINT fk_members_user
                FOREIGN KEY (user_id) REFERENCES admin_users (id)
                ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        # Legacy-compatible create (project_id added by migration below).
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

    _migrate_projects(conn)
    ensure_default_super_admin(conn)


def _migrate_projects(conn) -> None:
    """Add project_id columns and move legacy rows into the default project."""
    with conn.cursor() as cursor:
        default_id = _ensure_default_project_row(cursor)

        # crm_settings: legacy PK(setting_key) → PK(project_id, setting_key)
        if not _column_exists(cursor, "crm_settings", "project_id"):
            cursor.execute(
                "ALTER TABLE crm_settings ADD COLUMN project_id BIGINT UNSIGNED NULL"
            )
            cursor.execute(
                "UPDATE crm_settings SET project_id = %s WHERE project_id IS NULL",
                (default_id,),
            )
            cursor.execute("ALTER TABLE crm_settings DROP PRIMARY KEY")
            cursor.execute(
                "ALTER TABLE crm_settings MODIFY project_id BIGINT UNSIGNED NOT NULL"
            )
            cursor.execute(
                "ALTER TABLE crm_settings ADD PRIMARY KEY (project_id, setting_key)"
            )
        else:
            cursor.execute(
                "UPDATE crm_settings SET project_id = %s WHERE project_id IS NULL",
                (default_id,),
            )

        scoped_tables = (
            ("message_templates", "idx_templates_project"),
            ("automation_rules", "idx_rules_project"),
            ("message_campaigns", "idx_campaigns_project"),
            ("message_logs", "idx_logs_project"),
            ("crm_call_logs", "idx_calls_project"),
        )
        for table, index_name in scoped_tables:
            _add_column_if_missing(
                cursor, table, "project_id", "BIGINT UNSIGNED NULL"
            )
            cursor.execute(
                f"UPDATE {table} SET project_id = %s WHERE project_id IS NULL",
                (default_id,),
            )
            _add_index_if_missing(
                cursor, table, index_name, f"KEY {index_name} (project_id)"
            )

        # Attach existing admins to the default project (once).
        cursor.execute(
            """
            INSERT IGNORE INTO project_members (project_id, user_id, role)
            SELECT %s, a.id,
              CASE WHEN a.role = %s THEN %s ELSE %s END
            FROM admin_users a
            """,
            (default_id, ROLE_SUPER, PROJECT_OWNER, PROJECT_ADMIN),
        )


def _ensure_default_project_row(cursor) -> int:
    cursor.execute(
        "SELECT id FROM projects WHERE slug = %s LIMIT 1",
        (DEFAULT_PROJECT_SLUG,),
    )
    row = cursor.fetchone()
    if row:
        return int(row["id"])
    cursor.execute(
        """
        INSERT INTO projects (name, slug, is_active)
        VALUES (%s, %s, 1)
        """,
        (DEFAULT_PROJECT_NAME, DEFAULT_PROJECT_SLUG),
    )
    return int(cursor.lastrowid)


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
        admin_id = int(cursor.lastrowid)
        project_id = _ensure_default_project_row(cursor)
        cursor.execute(
            """
            INSERT IGNORE INTO project_members (project_id, user_id, role)
            VALUES (%s, %s, %s)
            """,
            (project_id, admin_id, PROJECT_OWNER),
        )


def _slugify_project_name(name: str) -> str:
    raw = re.sub(r"[^\w\-]+", "-", (name or "").strip().lower(), flags=re.UNICODE)
    raw = re.sub(r"-+", "-", raw).strip("-")
    if not raw or not re.search(r"[a-z0-9]", raw):
        raw = "project"
    return raw[:48]


def _unique_project_slug(cursor, base: str) -> str:
    slug = base[:48] or "project"
    candidate = slug
    for _ in range(12):
        cursor.execute(
            "SELECT 1 FROM projects WHERE slug = %s LIMIT 1", (candidate,)
        )
        if not cursor.fetchone():
            return candidate
        candidate = f"{slug[:40]}-{secrets.token_hex(2)}"
    return f"project-{secrets.token_hex(4)}"


def create_project(
    conn,
    *,
    name: str,
    owner_id: int,
    slug: str | None = None,
) -> int:
    name = (name or "").strip()
    if not name:
        raise ValueError("نام پروژه الزامی است")
    with conn.cursor() as cursor:
        base = _slugify_project_name(slug or name)
        final_slug = _unique_project_slug(cursor, base)
        cursor.execute(
            """
            INSERT INTO projects (name, slug, is_active, created_by)
            VALUES (%s, %s, 1, %s)
            """,
            (name, final_slug, owner_id),
        )
        project_id = int(cursor.lastrowid)
        cursor.execute(
            """
            INSERT INTO project_members (project_id, user_id, role)
            VALUES (%s, %s, %s)
            """,
            (project_id, owner_id, PROJECT_OWNER),
        )
    return project_id


def get_project(conn, project_id: int) -> dict | None:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT * FROM projects WHERE id = %s",
            (project_id,),
        )
        return cursor.fetchone()


def list_user_projects(conn, user_id: int) -> list[dict]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT p.*, m.role AS member_role
            FROM projects p
            JOIN project_members m ON m.project_id = p.id
            WHERE m.user_id = %s AND p.is_active = 1
            ORDER BY p.id ASC
            """,
            (user_id,),
        )
        return list(cursor.fetchall())


def get_membership(conn, project_id: int, user_id: int) -> dict | None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT m.*, p.name AS project_name, p.slug AS project_slug, p.is_active
            FROM project_members m
            JOIN projects p ON p.id = m.project_id
            WHERE m.project_id = %s AND m.user_id = %s
            LIMIT 1
            """,
            (project_id, user_id),
        )
        return cursor.fetchone()


def list_project_members(conn, project_id: int) -> list[dict]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT m.id AS membership_id, m.role AS member_role, m.created_at AS joined_at,
                   a.id, a.username, a.display_name, a.role AS platform_role,
                   a.is_active, a.last_login
            FROM project_members m
            JOIN admin_users a ON a.id = m.user_id
            WHERE m.project_id = %s
            ORDER BY FIELD(m.role, 'owner', 'admin', 'member'), a.id ASC
            """,
            (project_id,),
        )
        return list(cursor.fetchall())


def add_project_member(
    conn,
    *,
    project_id: int,
    user_id: int,
    role: str = PROJECT_ADMIN,
) -> None:
    if role not in PROJECT_ROLES:
        role = PROJECT_ADMIN
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO project_members (project_id, user_id, role)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE role = VALUES(role)
            """,
            (project_id, user_id, role),
        )


def update_project_member_role(
    conn, *, project_id: int, user_id: int, role: str
) -> None:
    if role not in PROJECT_ROLES:
        raise ValueError("نقش نامعتبر است")
    with conn.cursor() as cursor:
        if role != PROJECT_OWNER:
            cursor.execute(
                """
                SELECT COUNT(*) AS c FROM project_members
                WHERE project_id = %s AND role = %s AND user_id != %s
                """,
                (project_id, PROJECT_OWNER, user_id),
            )
            owners_left = int((cursor.fetchone() or {}).get("c") or 0)
            cursor.execute(
                """
                SELECT role FROM project_members
                WHERE project_id = %s AND user_id = %s
                """,
                (project_id, user_id),
            )
            current = cursor.fetchone()
            if (
                current
                and current.get("role") == PROJECT_OWNER
                and owners_left < 1
            ):
                raise ValueError("حداقل یک مالک برای پروژه لازم است")
        cursor.execute(
            """
            UPDATE project_members SET role = %s
            WHERE project_id = %s AND user_id = %s
            """,
            (role, project_id, user_id),
        )


def remove_project_member(conn, *, project_id: int, user_id: int) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT role FROM project_members
            WHERE project_id = %s AND user_id = %s
            """,
            (project_id, user_id),
        )
        current = cursor.fetchone()
        if not current:
            return
        if current.get("role") == PROJECT_OWNER:
            cursor.execute(
                """
                SELECT COUNT(*) AS c FROM project_members
                WHERE project_id = %s AND role = %s AND user_id != %s
                """,
                (project_id, PROJECT_OWNER, user_id),
            )
            if int((cursor.fetchone() or {}).get("c") or 0) < 1:
                raise ValueError("حداقل یک مالک برای پروژه لازم است")
        cursor.execute(
            """
            DELETE FROM project_members
            WHERE project_id = %s AND user_id = %s
            """,
            (project_id, user_id),
        )


def find_admin_by_username(conn, username: str) -> dict | None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, username, display_name, role, is_active
            FROM admin_users WHERE username = %s LIMIT 1
            """,
            (username.strip(),),
        )
        return cursor.fetchone()


def register_user_with_project(
    conn,
    *,
    username: str,
    password: str,
    display_name: str | None,
    project_name: str,
) -> tuple[dict, int]:
    """Create a new user + their first project. Returns (admin_row, project_id)."""
    username = (username or "").strip()
    project_name = (project_name or "").strip()
    if not username or not password:
        raise ValueError("نام کاربری و رمز عبور الزامی است")
    if len(password) < 6:
        raise ValueError("رمز عبور باید حداقل ۶ کاراکتر باشد")
    if not project_name:
        raise ValueError("نام پروژه الزامی است")
    if find_admin_by_username(conn, username):
        raise ValueError("این نام کاربری قبلاً ثبت شده است")
    admin_id = create_admin(
        conn,
        username=username,
        password=password,
        display_name=display_name,
        role=ROLE_ADMIN,
    )
    project_id = create_project(conn, name=project_name, owner_id=admin_id)
    admin = get_admin_by_id(conn, admin_id)
    if not admin:
        raise RuntimeError("ساخت کاربر ناموفق بود")
    return admin, project_id


def enrich_contact_fields(row: dict) -> dict:
    phone_type, mobile = classify_phone(row.get("phone"))
    email_norm = normalize_email(row.get("email"))
    enriched = dict(row)
    enriched["phone_type"] = phone_type
    enriched["mobile_phone"] = mobile
    enriched["email_normalized"] = email_norm
    return enriched


def get_setting(conn, key: str, *, project_id: int) -> str:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT setting_value FROM crm_settings
            WHERE project_id = %s AND setting_key = %s
            """,
            (project_id, key),
        )
        row = cursor.fetchone()
    return str((row or {}).get("setting_value") or "")


def get_all_settings(conn, *, project_id: int) -> dict[str, str]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT setting_key, setting_value FROM crm_settings
            WHERE project_id = %s
            """,
            (project_id,),
        )
        rows = cursor.fetchall()
    return {row["setting_key"]: row["setting_value"] or "" for row in rows}


def save_settings(conn, settings: dict[str, str], *, project_id: int) -> None:
    with conn.cursor() as cursor:
        for key, value in settings.items():
            if key not in CRM_SETTINGS_KEYS:
                continue
            cursor.execute(
                """
                INSERT INTO crm_settings (project_id, setting_key, setting_value)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value)
                """,
                (project_id, key, value),
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


def list_templates(
    conn, *, project_id: int, channel: str | None = None
) -> list[dict]:
    sql = "SELECT * FROM message_templates WHERE project_id = %s"
    params: list[Any] = [project_id]
    if channel:
        sql += " AND channel = %s"
        params.append(channel)
    sql += " ORDER BY id DESC"
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        rows = list(cursor.fetchall())
    for row in rows:
        row["token_mapping"] = _parse_json_field(row.get("token_mapping"))
    return rows


def get_template(
    conn, template_id: int, *, project_id: int | None = None
) -> dict | None:
    sql = "SELECT * FROM message_templates WHERE id = %s"
    params: list[Any] = [template_id]
    if project_id is not None:
        sql += " AND project_id = %s"
        params.append(project_id)
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        row = cursor.fetchone()
    if row:
        row["token_mapping"] = _parse_json_field(row.get("token_mapping"))
    return row


def save_template(
    conn, data: dict, *, project_id: int, template_id: int | None = None
) -> int:
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
                WHERE id=%s AND project_id=%s
                """,
                (*payload, template_id, project_id),
            )
            return template_id
        cursor.execute(
            """
            INSERT INTO message_templates (
              project_id, name, channel, provider, description, kavenegar_template,
              token_mapping, sms_preview_text, email_subject, email_body, is_active
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (project_id, *payload),
        )
        return int(cursor.lastrowid)


def delete_template(conn, template_id: int, *, project_id: int) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            "DELETE FROM message_templates WHERE id = %s AND project_id = %s",
            (template_id, project_id),
        )


def list_automation_rules(conn, *, project_id: int) -> list[dict]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT r.*, t.name AS template_name
            FROM automation_rules r
            JOIN message_templates t ON t.id = r.template_id
            WHERE r.project_id = %s
            ORDER BY r.id DESC
            """,
            (project_id,),
        )
        return list(cursor.fetchall())


def get_automation_rule(
    conn, rule_id: int, *, project_id: int | None = None
) -> dict | None:
    sql = "SELECT * FROM automation_rules WHERE id = %s"
    params: list[Any] = [rule_id]
    if project_id is not None:
        sql += " AND project_id = %s"
        params.append(project_id)
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchone()


def save_automation_rule(
    conn, data: dict, *, project_id: int, rule_id: int | None = None
) -> int:
    template_id = int(data["template_id"])
    if not get_template(conn, template_id, project_id=project_id):
        raise ValueError("قالب انتخاب‌شده متعلق به این پروژه نیست")
    payload = (
        data["name"],
        data.get("trigger_type") or "new_domain",
        template_id,
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
                WHERE id=%s AND project_id=%s
                """,
                (*payload, rule_id, project_id),
            )
            return rule_id
        cursor.execute(
            """
            INSERT INTO automation_rules (
              project_id, name, trigger_type, template_id, channel,
              mobile_only, is_active
            ) VALUES (%s,%s,%s,%s,%s,%s,%s)
            """,
            (project_id, *payload),
        )
        return int(cursor.lastrowid)


def delete_automation_rule(conn, rule_id: int, *, project_id: int) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            "DELETE FROM automation_rules WHERE id = %s AND project_id = %s",
            (rule_id, project_id),
        )


def list_campaigns(conn, *, project_id: int, limit: int = 50) -> list[dict]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.*, t.name AS template_name, a.username AS created_by_name
            FROM message_campaigns c
            JOIN message_templates t ON t.id = c.template_id
            LEFT JOIN admin_users a ON a.id = c.created_by
            WHERE c.project_id = %s
            ORDER BY c.id DESC
            LIMIT %s
            """,
            (project_id, limit),
        )
        return list(cursor.fetchall())


def get_campaign(
    conn, campaign_id: int, *, project_id: int | None = None
) -> dict | None:
    sql = """
        SELECT c.*, t.name AS template_name
        FROM message_campaigns c
        JOIN message_templates t ON t.id = c.template_id
        WHERE c.id = %s
    """
    params: list[Any] = [campaign_id]
    if project_id is not None:
        sql += " AND c.project_id = %s"
        params.append(project_id)
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
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


def create_campaign(conn, data: dict, *, project_id: int) -> int:
    domain_ids = data.get("target_domain_ids") or []
    template_id = int(data["template_id"])
    if not get_template(conn, template_id, project_id=project_id):
        raise ValueError("قالب انتخاب‌شده متعلق به این پروژه نیست")
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO message_campaigns (
              project_id, name, channel, template_id, status, target_type,
              target_domain_ids, mobile_only, created_by, total_count
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                project_id,
                data["name"],
                data["channel"],
                template_id,
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
              project_id, campaign_id, automation_rule_id, domain_id, channel,
              recipient, recipient_type, status, provider_message_id,
              error_message, template_id, sent_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                data.get("project_id"),
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
    project_id: int,
    campaign_id: int | None = None,
    channel: str = "",
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    search: str = "",
) -> tuple[str, list[Any]]:
    clauses: list[str] = ["l.project_id = %s"]
    params: list[Any] = [project_id]
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
    where = f"WHERE {' AND '.join(clauses)}"
    return where, params


def list_message_logs(
    conn,
    *,
    project_id: int,
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
        project_id=project_id,
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
    project_id: int,
    campaign_id: int | None = None,
    channel: str = "",
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    search: str = "",
) -> int:
    where, params = _message_log_filters(
        project_id=project_id,
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
    project_id: int,
    channel: str = "",
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    search: str = "",
) -> dict[str, Any]:
    """Aggregate message-log counts grouped by channel and status."""
    where, params = _message_log_filters(
        project_id=project_id,
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
        "sms": {"total": 0, "sent": 0, "failed": 0, "skipped": 0, "pending": 0, "test": 0},
        "email": {"total": 0, "sent": 0, "failed": 0, "skipped": 0, "pending": 0, "test": 0},
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
    project_id: int,
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
            project_id=project_id,
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


def _domain_outreach_select(project_id: int) -> tuple[str, list[Any]]:
    sql = """
    d.id, d.domain, d.business_name, d.owner_name, d.phone, d.email,
    d.phone_type, d.mobile_phone, d.email_normalized,
    d.province, d.city, d.approve_date, d.expire_date, d.rating,
    EXISTS(
        SELECT 1 FROM message_logs ml
        WHERE ml.domain_id = d.id AND ml.channel = 'sms' AND ml.status = 'sent'
          AND ml.project_id = %s
    ) AS sms_sent,
    EXISTS(
        SELECT 1 FROM message_logs ml
        WHERE ml.domain_id = d.id AND ml.channel = 'email' AND ml.status = 'sent'
          AND ml.project_id = %s
    ) AS email_sent,
    EXISTS(
        SELECT 1 FROM crm_call_logs cl
        WHERE cl.domain_id = d.id AND cl.project_id = %s
    ) AS has_call
    """
    return sql, [project_id, project_id, project_id]


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
    project_id: int,
    query: str = "",
    phone_type: str = "",
    offset: int = 0,
    limit: int = 50,
) -> list[dict]:
    where, params = _campaign_domain_filters(query, phone_type)
    fields_sql, outreach_params = _domain_outreach_select(project_id)
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
        SELECT {fields_sql}
        FROM enamad_domains d
        {where}
        {order}
        LIMIT %s OFFSET %s
    """
    with conn.cursor() as cursor:
        cursor.execute(
            sql, [*outreach_params, *params, *order_params, limit, offset]
        )
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
                   province, city, approve_date, expire_date, created_at
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
            SELECT r.id, r.project_id, r.name, r.trigger_type, r.template_id,
                   r.channel, r.mobile_only, r.is_active, r.created_at,
                   t.kavenegar_template, t.token_mapping,
                   t.email_subject, t.email_body, t.is_active AS template_active
            FROM automation_rules r
            JOIN message_templates t ON t.id = r.template_id
            WHERE r.is_active = 1 AND t.is_active = 1
              AND r.trigger_type = %s
              AND r.project_id IS NOT NULL
            """,
            (trigger_type,),
        )
        rows = list(cursor.fetchall())
    for row in rows:
        row["token_mapping"] = _parse_json_field(row.get("token_mapping"))
    return rows


def automation_already_handled(
    conn,
    domain_id: int,
    *,
    project_id: int,
    rule_id: int | None = None,
) -> bool:
    """True if this domain already got a final automation attempt (sent/test/failed
    or a non-retryable skip such as landline). Missing-contact skips are NOT final
    so a later trustseal refresh can still trigger the rule.
    """
    sql = """
        SELECT status, error_message
        FROM message_logs
        WHERE domain_id = %s AND project_id = %s AND automation_rule_id IS NOT NULL
    """
    params: list[Any] = [domain_id, project_id]
    if rule_id is not None:
        sql += " AND automation_rule_id = %s"
        params.append(rule_id)
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        rows = list(cursor.fetchall())
    for row in rows:
        status = row.get("status") or ""
        if status in ("sent", "test", "failed"):
            return True
        if status == "skipped":
            err = str(row.get("error_message") or "")
            # Retryable: contact was empty at scrape time; refresh may fill it.
            if "موبایل معتبر" in err or "ایمیل معتبر" in err:
                continue
            return True
    return False


def _sanitize_kavenegar_token(value: str, *, allow_spaces: bool = False) -> str:
    """Kavenegar token/token2/token3 reject spaces, newlines and underscores.

    token10/token20 allow a few spaces. We normalize invalid characters so
    common values like owner names ("فرشاد محمدی") still send successfully.
    """
    text = str(value or "").strip()
    text = text.replace("\r", " ").replace("\n", " ").replace("_", "-")
    if allow_spaces:
        # Collapse repeated spaces; token10 allows a limited number.
        text = re.sub(r"\s+", " ", text).strip()
    else:
        text = re.sub(r"\s+", "", text)
    return (text or "-")[:100]


def build_kavenegar_tokens(template: dict, domain_row: dict) -> dict[str, str]:
    mapping = _parse_json_field(template.get("token_mapping"))
    context = build_template_context(domain_row)
    tokens: dict[str, str] = {}
    for kn_token in KAVENEGAR_TOKENS:
        var_name = mapping.get(kn_token, "")
        if var_name:
            raw = str(context.get(var_name) or "-")
            tokens[kn_token] = _sanitize_kavenegar_token(
                raw, allow_spaces=kn_token in ("token10", "token20")
            )
    if not tokens.get("token"):
        tokens["token"] = _sanitize_kavenegar_token(
            str(context.get("domain") or "-"), allow_spaces=False
        )
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


def create_call_log(conn, data: dict, *, project_id: int) -> int:
    outcome = data.get("outcome") or "not_called"
    if outcome not in CALL_OUTCOMES:
        raise ValueError("نتیجه تماس نامعتبر است")
    called_at = data.get("called_at")
    with conn.cursor() as cursor:
        if called_at:
            cursor.execute(
                """
                INSERT INTO crm_call_logs (
                  project_id, domain_id, created_by, phone_used, outcome, notes,
                  called_at, next_follow_up_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    project_id,
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
                  project_id, domain_id, created_by, phone_used, outcome, notes,
                  next_follow_up_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    project_id,
                    int(data["domain_id"]),
                    data.get("created_by"),
                    data.get("phone_used") or None,
                    outcome,
                    data.get("notes") or None,
                    data.get("next_follow_up_at") or None,
                ),
            )
        return int(cursor.lastrowid)


def get_call_log(
    conn, call_id: int, *, project_id: int | None = None
) -> dict | None:
    sql = """
        SELECT c.*, d.domain, d.business_name, d.owner_name,
               a.username AS created_by_name, a.display_name AS created_by_display
        FROM crm_call_logs c
        JOIN enamad_domains d ON d.id = c.domain_id
        LEFT JOIN admin_users a ON a.id = c.created_by
        WHERE c.id = %s
    """
    params: list[Any] = [call_id]
    if project_id is not None:
        sql += " AND c.project_id = %s"
        params.append(project_id)
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchone()


def list_call_logs(
    conn,
    *,
    project_id: int,
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
        WHERE c.project_id = %s
    """
    params: list[Any] = [project_id]

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
    project_id: int,
    filter_type: str = "all",
    outcome: str | None = None,
    domain_id: int | None = None,
) -> int:
    sql = "SELECT COUNT(*) AS c FROM crm_call_logs c WHERE c.project_id = %s"
    params: list[Any] = [project_id]

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


def get_latest_call_for_domain(
    conn, domain_id: int, *, project_id: int
) -> dict | None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.*, a.username AS created_by_name, a.display_name AS created_by_display
            FROM crm_call_logs c
            LEFT JOIN admin_users a ON a.id = c.created_by
            WHERE c.domain_id = %s AND c.project_id = %s
            ORDER BY c.called_at DESC, c.id DESC
            LIMIT 1
            """,
            (domain_id, project_id),
        )
        return cursor.fetchone()


def get_call_logs_for_domain(
    conn, domain_id: int, *, project_id: int, limit: int = 20
) -> list[dict]:
    return list_call_logs(
        conn, project_id=project_id, domain_id=domain_id, limit=limit, offset=0
    )


def call_stats(conn, *, project_id: int) -> dict[str, int]:
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
            WHERE project_id = %s
            """,
            (project_id,),
        )
        row = cursor.fetchone() or {}
    return {
        "total": int(row.get("total") or 0),
        "follow_up_today": int(row.get("follow_up_today") or 0),
        "interested": int(row.get("interested") or 0),
        "converted": int(row.get("converted") or 0),
    }


def crm_stats(conn, *, project_id: int) -> dict[str, int]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM message_templates
                 WHERE is_active = 1 AND project_id = %s) AS templates,
                (SELECT COUNT(*) FROM automation_rules
                 WHERE is_active = 1 AND project_id = %s) AS rules,
                (SELECT COUNT(*) FROM message_campaigns
                 WHERE project_id = %s) AS campaigns,
                SUM(phone_type IN ('mobile', 'mixed')) AS mobile_domains,
                SUM(phone_type = 'landline') AS landline_domains,
                SUM(
                    email_normalized IS NOT NULL AND email_normalized != ''
                ) AS email_domains
            FROM enamad_domains
            """,
            (project_id, project_id, project_id),
        )
        row = cursor.fetchone() or {}
        calls = call_stats(conn, project_id=project_id)
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
