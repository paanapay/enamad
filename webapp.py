#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Admin web panel for browsing the Enamad domain database."""
from __future__ import annotations

import os
import secrets
from functools import wraps

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

import bot_queries as q
from cache_utils import cached
from crm_db import ROLE_SUPER, authenticate_admin, ensure_crm_tables, CALL_OUTCOMES
from jalali_utils import format_jdate, format_jdatetime, is_jalali_date
from crm_panel import crm_bp
from db import ensure_domain_detail_columns, ensure_domain_indexes, load_config, mysql_connection
from log_viewer import LEVELS, clear_logs, list_log_files, read_log_entries
from logging_setup import LOG_DIR, setup_logging

setup_logging()

app = Flask(__name__)
app.secret_key = os.environ.get("WEB_SECRET_KEY") or secrets.token_hex(32)
app.register_blueprint(crm_bp)

ADMIN_PASSWORD = os.environ.get("WEB_ADMIN_PASSWORD", "")
LIVE_SEARCH = (os.environ.get("WEB_LIVE_SEARCH", "yes").strip().lower()
               in ("1", "true", "yes", "on"))
PAGE_SIZE = int(os.environ.get("WEB_PAGE_SIZE", "25"))

_APP_CONFIG = None


def app_config():
    global _APP_CONFIG
    if _APP_CONFIG is None:
        _APP_CONFIG = load_config()
    return _APP_CONFIG


def _ensure_schema():
    with mysql_connection(app_config().mysql) as conn:
        ensure_domain_detail_columns(conn)
        ensure_domain_indexes(conn)
        ensure_crm_tables(conn)
        conn.commit()


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("admin_id"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapper


def super_admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if session.get("admin_role") != ROLE_SUPER:
            abort(403)
        return view(*args, **kwargs)

    return wrapper


@app.before_request
def _bootstrap_crm():
    if not getattr(app, "_crm_ready", False):
        try:
            _ensure_schema()
            app._crm_ready = True
        except Exception:  # noqa: BLE001
            pass


@app.context_processor
def inject_globals():
    admin = None
    if session.get("admin_id"):
        admin = {
            "id": session.get("admin_id"),
            "username": session.get("admin_username"),
            "display_name": session.get("admin_display_name"),
            "role": session.get("admin_role"),
            "is_super": session.get("admin_role") == ROLE_SUPER,
        }
    return {"current_admin": admin}


@app.template_filter("group")
def group_filter(value):
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return value


@app.template_filter("stars")
def stars_filter(value):
    try:
        n = int(value or 0)
    except (TypeError, ValueError):
        n = 0
    n = max(0, min(5, n))
    return "★" * n + "☆" * (5 - n)


@app.template_filter("phone_label")
def phone_label_filter(value):
    labels = {
        "mobile": "موبایل",
        "landline": "ثابت",
        "mixed": "موبایل+ثابت",
        "unknown": "نامشخص",
        "none": "—",
    }
    return labels.get(value or "", value or "—")


@app.template_filter("enamad_status_label")
def enamad_status_label_filter(value):
    labels = {
        "not_found": "یافت نشد در اینماد",
    }
    return labels.get(value or "", "")


@app.template_filter("call_outcome")
def call_outcome_filter(value):
    return CALL_OUTCOMES.get(value or "", value or "—")


def _greg_to_jalali(value: str) -> str:
    """Convert a gregorian ISO date (yyyy-mm-dd) to an ascii Jalali string.

    Returns "" for empty or unparseable input so tampered values can't leak
    into the SQL filter.
    """
    value = (value or "").strip()
    if not value:
        return ""
    jalali = format_jdate(value)
    return jalali if is_jalali_date(jalali) else ""


@app.template_filter("jdate")
def jdate_filter(value):
    return format_jdate(value)


@app.template_filter("jdatetime")
def jdatetime_filter(value):
    return format_jdatetime(value)


@app.route("/healthz")
def healthz():
    return "ok", 200


@app.route("/crm")
def crm_redirect():
    return redirect(url_for("crm.dashboard"))


def _set_admin_session(admin: dict) -> None:
    session["admin_id"] = admin["id"]
    session["admin_username"] = admin["username"]
    session["admin_display_name"] = admin.get("display_name") or admin["username"]
    session["admin_role"] = admin["role"]
    session["auth"] = True
    session.permanent = True


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password", "")

        with mysql_connection(app_config().mysql) as conn:
            if username:
                admin = authenticate_admin(conn, username, password)
                if admin:
                    _set_admin_session(admin)
                    nxt = request.args.get("next") or url_for("dashboard")
                    return redirect(nxt)

            # Legacy: single password without username (migrates to admin user)
            if not username and ADMIN_PASSWORD and secrets.compare_digest(password, ADMIN_PASSWORD):
                ensure_crm_tables(conn)
                from crm_db import list_admins

                admins = list_admins(conn)
                if admins:
                    _set_admin_session(admins[0])
                else:
                    session["auth"] = True
                    session.permanent = True
                nxt = request.args.get("next") or url_for("dashboard")
                return redirect(nxt)

        flash("نام کاربری یا رمز عبور نادرست است.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


STATS_CACHE_TTL = int(os.environ.get("WEB_STATS_CACHE_TTL", "60"))


@app.route("/")
@login_required
def dashboard():
    def produce():
        from crm_db import crm_stats

        with mysql_connection(app_config().mysql) as conn:
            return {
                "stats": q.get_stats(conn),
                "users": q.get_bot_user_stats(conn),
                "crm": crm_stats(conn),
            }

    data = cached("web_dashboard", STATS_CACHE_TTL, produce)
    return render_template("dashboard.html", **data)


@app.route("/domains")
@login_required
def domains():
    query = (request.args.get("q") or "").strip()
    sort = request.args.get("sort", "latest")
    if sort not in q.DOMAIN_SORTS:
        sort = "latest"
    phone_type = (request.args.get("phone_type") or "").strip()
    province = (request.args.get("province") or "").strip()
    city = (request.args.get("city") or "").strip()
    category = (request.args.get("category") or "").strip()

    # Date inputs arrive as gregorian ISO (yyyy-mm-dd) from the Jalali picker.
    approve_from_raw = (request.args.get("approve_from") or "").strip()
    approve_to_raw = (request.args.get("approve_to") or "").strip()
    created_from = (request.args.get("created_from") or "").strip()
    created_to = (request.args.get("created_to") or "").strip()

    filters = {
        "province": province,
        "city": city,
        "phone_type": phone_type,
        "category": category,
        # approve_date is stored as Jalali, so convert bounds to Jalali strings.
        "approve_from": _greg_to_jalali(approve_from_raw),
        "approve_to": _greg_to_jalali(approve_to_raw),
        "created_from": created_from,
        "created_to": created_to,
    }

    try:
        page = max(0, int(request.args.get("page", 0)))
    except ValueError:
        page = 0
    offset = page * PAGE_SIZE

    with mysql_connection(app_config().mysql) as conn:
        provinces = q.get_all_provinces(conn)
        province_cities = q.get_province_cities(conn)
        categories = q.get_service_categories(conn)
        if query:
            rows = q.search_domains(conn, query, limit=PAGE_SIZE)
            total = q.count_search(conn, query)
            page = 0
        else:
            rows = q.get_domains_filtered(
                conn, filters=filters, sort=sort, offset=offset, limit=PAGE_SIZE
            )
            total = q.count_domains_filtered(conn, filters=filters, sort=sort)

    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE

    # Args to carry across pagination links (drop empties + page).
    filter_args = {
        k: v
        for k, v in {
            "q": query,
            "sort": sort,
            "phone_type": phone_type,
            "province": province,
            "city": city,
            "category": category,
            "approve_from": approve_from_raw,
            "approve_to": approve_to_raw,
            "created_from": created_from,
            "created_to": created_to,
        }.items()
        if v
    }
    has_filters = any(
        [phone_type, province, city, category, approve_from_raw, approve_to_raw,
         created_from, created_to]
    )
    return render_template(
        "domains.html",
        rows=rows,
        total=total,
        page=page,
        pages=pages,
        sort=sort,
        query=query,
        phone_type=phone_type,
        province=province,
        city=city,
        category=category,
        provinces=provinces,
        province_cities=province_cities,
        categories=categories,
        approve_from=approve_from_raw,
        approve_to=approve_to_raw,
        approve_from_disp=_greg_to_jalali(approve_from_raw),
        approve_to_disp=_greg_to_jalali(approve_to_raw),
        created_from=created_from,
        created_to=created_to,
        created_from_disp=_greg_to_jalali(created_from),
        created_to_disp=_greg_to_jalali(created_to),
        filter_args=filter_args,
        has_filters=has_filters,
        page_size=PAGE_SIZE,
    )


@app.route("/domain/<int:domain_id>")
@login_required
def domain_detail(domain_id: int):
    from crm_db import get_call_logs_for_domain, get_latest_call_for_domain

    with mysql_connection(app_config().mysql) as conn:
        row = q.get_domain_by_id(conn, domain_id)
        if not row:
            abort(404)
        services = q.get_domain_services(
            conn, row.get("enamad_id"), row.get("code")
        )
        latest_call = get_latest_call_for_domain(conn, domain_id)
        call_history = get_call_logs_for_domain(conn, domain_id, limit=15)
    return render_template(
        "domain_detail.html",
        d=row,
        services=services,
        latest_call=latest_call,
        call_history=call_history,
    )


@app.route("/estelam", methods=["GET", "POST"])
@login_required
def estelam():
    from db import set_domain_enamad_status

    domain = (request.form.get("domain") or request.args.get("domain") or "").strip()
    result = None
    error = None
    local = None
    if domain:
        with mysql_connection(app_config().mysql) as conn:
            local = q.get_domain_exact(conn, _clean_domain(domain))
        if LIVE_SEARCH:
            try:
                result = _live_lookup(domain)
            except Exception as exc:  # noqa: BLE001
                error = f"استعلام زنده ناموفق بود: {exc}"
        else:
            error = "استعلام زنده غیرفعال است (WEB_LIVE_SEARCH=no)."
        if local:
            with mysql_connection(app_config().mysql) as conn:
                if result:
                    set_domain_enamad_status(conn, local["id"], None)
                elif not error:
                    set_domain_enamad_status(conn, local["id"], "not_found")
                conn.commit()
                local = q.get_domain_exact(conn, _clean_domain(domain))
    return render_template(
        "estelam.html", domain=domain, result=result, local=local, error=error
    )


@app.route("/users")
@login_required
@super_admin_required
def users():
    from bot_broadcast import configured_platforms

    try:
        page = max(0, int(request.args.get("page", 0)))
    except ValueError:
        page = 0
    offset = page * PAGE_SIZE
    with mysql_connection(app_config().mysql) as conn:
        rows = q.get_bot_users(conn, offset=offset, limit=PAGE_SIZE)
        total = q.count_bot_users(conn)
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    return render_template(
        "users.html",
        rows=rows,
        total=total,
        page=page,
        pages=pages,
        send_platforms=configured_platforms(),
    )


@app.route("/users/send", methods=["POST"])
@login_required
@super_admin_required
def users_send():
    from bot_broadcast import PLATFORMS, broadcast

    text = (request.form.get("message") or "").strip()
    mode = (request.form.get("mode") or "selected").strip()
    platform_filter = (request.form.get("platform") or "").strip()
    page = request.form.get("page", 0)

    if not text:
        flash("متن پیام خالی است.", "error")
        return redirect(url_for("users", page=page))

    if mode == "all":
        with mysql_connection(app_config().mysql) as conn:
            targets = q.get_all_bot_user_targets(
                conn, platform=platform_filter if platform_filter in PLATFORMS else ""
            )
    else:
        targets = []
        for raw in request.form.getlist("targets"):
            # checkbox values look like "telegram:123456789"
            platform, _, user_id = raw.partition(":")
            if platform in PLATFORMS and user_id.strip().lstrip("-").isdigit():
                targets.append((platform, int(user_id)))

    if not targets:
        flash("هیچ کاربری برای ارسال انتخاب نشده.", "error")
        return redirect(url_for("users", page=page))

    results = broadcast(targets, text)
    if results["failed"]:
        detail = "؛ ".join(results["errors"][:5])
        flash(
            f"ارسال شد به {results['sent']} کاربر — {results['failed']} ناموفق. {detail}",
            "error" if not results["sent"] else "ok",
        )
    else:
        flash(f"پیام به {results['sent']} کاربر ارسال شد.", "ok")
    return redirect(url_for("users", page=page))


@app.route("/system/logs")
@login_required
@super_admin_required
def system_logs():
    filename = (request.args.get("file") or "enamad.log").strip()
    level = (request.args.get("level") or "").strip().upper()
    query = (request.args.get("q") or "").strip()
    try:
        lines = int(request.args.get("lines", 500))
    except ValueError:
        lines = 500
    if lines not in (200, 500, 1000, 2000):
        lines = 500

    data = read_log_entries(
        filename=filename,
        max_lines=lines,
        level=level,
        search=query,
    )
    return render_template(
        "system_logs.html",
        entries=data["entries"],
        filename=data["filename"],
        total_raw=data["total_raw"],
        error=data["error"],
        log_files=list_log_files(),
        log_dir=LOG_DIR,
        levels=LEVELS,
        level=level,
        query=query,
        lines=lines,
        line_options=(200, 500, 1000, 2000),
    )


@app.route("/system/logs/clear", methods=["POST"])
@login_required
@super_admin_required
def system_logs_clear():
    scope = (request.form.get("scope") or "current").strip()
    filename = (request.form.get("file") or "enamad.log").strip()
    all_files = scope == "all"

    cleared, errors = clear_logs(filename=filename, all_files=all_files)
    if errors:
        flash(f"برخی فایل‌ها پاک نشدند: {', '.join(errors)}", "error")
    elif cleared:
        if all_files:
            flash(f"{cleared} فایل لاگ خالی شد.", "ok")
        else:
            flash(f"فایل «{filename}» خالی شد.", "ok")
    else:
        flash("فایلی برای پاک کردن یافت نشد.", "error")

    return redirect(url_for("system_logs", file=filename if not all_files else "enamad.log"))


def _clean_domain(domain: str) -> str:
    try:
        from extract_enamad import clean_domain

        return clean_domain(domain)
    except Exception:  # noqa: BLE001
        return domain.strip().lower()


def _live_lookup(domain: str) -> dict | None:
    from extract_enamad import (
        EnamadClient,
        maybe_enrich_row,
        normalize_search_row,
    )

    client = EnamadClient(quiet=True)
    data = client.search_domain(domain)
    if not data:
        return None
    row = normalize_search_row(data, domain)
    return maybe_enrich_row(client, row, True)


if __name__ == "__main__":
    if not ADMIN_PASSWORD:
        print("WARNING: WEB_ADMIN_PASSWORD not set — create admin via CRM after first DB init.")
    app.run(host="127.0.0.1", port=int(os.environ.get("WEB_PORT", "8095")))
