"""CRM admin panel routes (Flask Blueprint)."""
from __future__ import annotations

import os
from functools import wraps

from cache_utils import cached, invalidate
from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

import bot_queries as q
from contact_utils import (
    KAVENEGAR_TOKENS,
    TEMPLATE_VARIABLES,
    build_template_context,
)
from crm_db import (
    CALL_OUTCOMES,
    CRM_SETTINGS_KEYS,
    PROJECT_ADMIN,
    PROJECT_MEMBER,
    PROJECT_OWNER,
    PROJECT_ROLES,
    ROLE_ADMIN,
    ROLE_SUPER,
    add_project_member,
    change_admin_password,
    create_admin,
    create_call_log,
    create_campaign,
    crm_stats,
    count_call_logs,
    delete_automation_rule,
    delete_template,
    find_admin_by_username,
    get_admin_by_id,
    get_all_settings,
    get_automation_rule,
    get_call_log,
    get_campaign,
    get_template,
    is_dry_run,
    list_admins,
    list_automation_rules,
    list_call_logs,
    list_campaigns,
    list_message_logs,
    list_project_members,
    list_templates,
    preview_template,
    remove_project_member,
    save_automation_rule,
    save_settings,
    save_template,
    update_admin,
    update_campaign_counts,
    update_project_member_role,
    verify_admin_password,
    count_message_logs,
    message_log_stats,
    iter_message_logs_for_export,
    call_stats,
)
from crm_service import (
    is_campaign_running,
    send_to_domain,
    start_campaign_async,
)
from db import mysql_connection
from email_presets import list_email_presets
from project_access import (
    can_manage_project,
    current_project_id,
    is_platform_super,
)

crm_bp = Blueprint("crm", __name__, url_prefix="/crm")

# Sample data used for live preview when no real domain is selected.
_SAMPLE_CONTEXT = {
    "domain": "shop-nemune.ir",
    "business_name": "فروشگاه نمونه",
    "owner_name": "علی رضایی",
    "province": "تهران",
    "city": "تهران",
    "approve_date": "1403/05/01",
    "expire_date": "1404/05/01",
    "phone": "02112345678",
    "email": "info@shop-nemune.ir",
    "mobile_phone": "09121234567",
    "email_normalized": "info@shop-nemune.ir",
    "phone_type": "mobile",
}


def _config():
    # Prefer the real Flask module; root webapp.py is only a thin shim.
    try:
        from enamad.web.webapp import app_config
    except ImportError:
        from webapp import app_config  # type: ignore

    return app_config()


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("admin_id"):
            return redirect(url_for("login", next=request.path))
        if not session.get("project_id"):
            try:
                with mysql_connection(_config().mysql) as conn:
                    from project_access import activate_user_project

                    activate_user_project(conn, int(session["admin_id"]))
            except Exception:  # noqa: BLE001
                pass
            if not session.get("project_id"):
                return redirect(url_for("projects_home"))
        return view(*args, **kwargs)

    return wrapper


def super_admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_platform_super():
            abort(403)
        return view(*args, **kwargs)

    return wrapper


def project_admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not can_manage_project():
            abort(403)
        return view(*args, **kwargs)

    return wrapper


def _project_id() -> int:
    pid = current_project_id()
    if not pid:
        abort(403)
    return pid


def _current_admin() -> dict:
    return {
        "id": session.get("admin_id"),
        "username": session.get("admin_username"),
        "display_name": session.get("admin_display_name"),
        "role": session.get("admin_role"),
        "is_super": is_platform_super(),
        "can_manage_project": can_manage_project(),
    }


def _test_mode_enabled() -> bool:
    project_id = current_project_id()
    if not project_id:
        return False

    def produce():
        with mysql_connection(_config().mysql) as conn:
            return is_dry_run(get_all_settings(conn, project_id=project_id))

    try:
        return bool(cached(f"crm_dry_run:{project_id}", 30, produce))
    except Exception:  # noqa: BLE001
        return False


@crm_bp.app_context_processor
def inject_admin():
    if session.get("admin_id"):
        project = None
        if session.get("project_id"):
            project = {
                "id": session.get("project_id"),
                "name": session.get("project_name"),
                "role": session.get("project_role"),
            }
        return {
            "current_admin": _current_admin(),
            "current_project": project,
            "crm_test_mode": _test_mode_enabled(),
        }
    return {}


@crm_bp.route("/")
@login_required
def dashboard():
    project_id = _project_id()

    def produce():
        with mysql_connection(_config().mysql) as conn:
            return {
                "stats": crm_stats(conn, project_id=project_id),
                "campaigns": list_campaigns(conn, project_id=project_id, limit=5),
            }

    ttl = int(os.environ.get("WEB_STATS_CACHE_TTL", "60"))
    data = cached(f"crm_dashboard:{project_id}", ttl, produce)
    return render_template("crm/dashboard.html", **data)


@crm_bp.route("/settings", methods=["GET", "POST"])
@login_required
@project_admin_required
def settings():
    project_id = _project_id()
    if request.method == "POST":
        data = {key: request.form.get(key, "") for key in CRM_SETTINGS_KEYS}
        data["smtp_tls"] = "yes" if request.form.get("smtp_tls") == "on" else "no"
        data["smtp_ssl_verify"] = (
            "yes" if request.form.get("smtp_ssl_verify") == "on" else "no"
        )
        data["dry_run"] = "yes" if request.form.get("dry_run") == "on" else "no"
        with mysql_connection(_config().mysql) as conn:
            save_settings(conn, data, project_id=project_id)
            conn.commit()
        invalidate(f"crm_dry_run:{project_id}")
        flash("تنظیمات ذخیره شد.", "ok")
        return redirect(url_for("crm.settings"))

    with mysql_connection(_config().mysql) as conn:
        stored = get_all_settings(conn, project_id=project_id)
    return render_template("crm/settings.html", settings=stored)


@crm_bp.route("/settings/test-email", methods=["POST"])
@login_required
@project_admin_required
def settings_test_email():
    """Send a test email with the saved SMTP settings (bypasses dry-run)."""
    from email_sender import EmailConfig, EmailSendError, send_email

    to_addr = (request.form.get("test_email") or "").strip()
    if not to_addr or "@" not in to_addr:
        flash("آدرس ایمیل معتبر وارد کنید.", "error")
        return redirect(url_for("crm.settings"))

    project_id = _project_id()
    with mysql_connection(_config().mysql) as conn:
        stored = get_all_settings(conn, project_id=project_id)

    config = EmailConfig.from_settings(stored)
    if not config.is_configured():
        flash(
            "تنظیمات SMTP کامل نیست (سرور و آدرس فرستنده لازم است). "
            "اول تنظیمات را ذخیره کنید.",
            "error",
        )
        return redirect(url_for("crm.settings"))

    try:
        send_email(
            config,
            to_addr=to_addr,
            subject="ایمیل تستی — تنظیمات SMTP",
            body=(
                "این یک ایمیل تستی است.\n\n"
                "اگر این پیام را دریافت کرده‌اید، تنظیمات SMTP پنل به‌درستی "
                f"کار می‌کند.\n\nسرور: {config.host}:{config.port}\n"
                f"فرستنده: {config.from_addr}"
            ),
        )
    except EmailSendError as exc:
        flash(f"ارسال ایمیل تستی ناموفق بود: {exc}", "error")
        return redirect(url_for("crm.settings"))

    flash(f"ایمیل تستی با موفقیت به {to_addr} ارسال شد.", "ok")
    return redirect(url_for("crm.settings"))


@crm_bp.route("/templates")
@login_required
def templates_list():
    channel = request.args.get("channel")
    project_id = _project_id()
    with mysql_connection(_config().mysql) as conn:
        rows = list_templates(conn, project_id=project_id, channel=channel or None)
    return render_template("crm/templates_list.html", rows=rows, channel=channel)


@crm_bp.route("/templates/new", methods=["GET", "POST"])
@crm_bp.route("/templates/<int:template_id>/edit", methods=["GET", "POST"])
@login_required
def template_form(template_id: int | None = None):
    project_id = _project_id()
    if request.method == "POST":
        token_mapping = {}
        for kn_token in KAVENEGAR_TOKENS:
            var_name = (request.form.get(f"map_{kn_token}") or "").strip()
            if var_name:
                token_mapping[kn_token] = var_name
        data = {
            "name": request.form.get("name", "").strip(),
            "channel": request.form.get("channel", "sms"),
            "provider": request.form.get("provider", "kavenegar"),
            "description": request.form.get("description", "").strip(),
            "kavenegar_template": request.form.get("kavenegar_template", "").strip(),
            "token_mapping": token_mapping,
            "sms_preview_text": request.form.get("sms_preview_text", "").strip(),
            "email_subject": request.form.get("email_subject", "").strip(),
            "email_body": request.form.get("email_body", "").strip(),
            "is_active": request.form.get("is_active") == "on",
        }
        if not data["name"]:
            flash("نام قالب الزامی است.", "error")
        else:
            with mysql_connection(_config().mysql) as conn:
                save_template(
                    conn, data, project_id=project_id, template_id=template_id
                )
                conn.commit()
            flash("قالب ذخیره شد.", "ok")
            return redirect(url_for("crm.templates_list"))

    row = None
    if template_id:
        with mysql_connection(_config().mysql) as conn:
            row = get_template(conn, template_id, project_id=project_id)
        if not row:
            abort(404)
    return render_template(
        "crm/template_form.html",
        row=row,
        variables=TEMPLATE_VARIABLES,
        kavenegar_tokens=KAVENEGAR_TOKENS,
        sample_context=_SAMPLE_CONTEXT,
        email_presets=list_email_presets(),
    )


@crm_bp.route("/templates/<int:template_id>/delete", methods=["POST"])
@login_required
def template_delete(template_id: int):
    project_id = _project_id()
    with mysql_connection(_config().mysql) as conn:
        delete_template(conn, template_id, project_id=project_id)
        conn.commit()
    flash("قالب حذف شد.", "ok")
    return redirect(url_for("crm.templates_list"))


@crm_bp.route("/automations")
@login_required
def automations_list():
    project_id = _project_id()
    with mysql_connection(_config().mysql) as conn:
        rows = list_automation_rules(conn, project_id=project_id)
        templates = list_templates(conn, project_id=project_id)
    return render_template("crm/automations.html", rows=rows, templates=templates)


@crm_bp.route("/automations/new", methods=["GET", "POST"])
@crm_bp.route("/automations/<int:rule_id>/edit", methods=["GET", "POST"])
@login_required
def automation_form(rule_id: int | None = None):
    project_id = _project_id()
    if request.method == "POST":
        data = {
            "name": request.form.get("name", "").strip(),
            "trigger_type": "new_domain",
            "template_id": request.form.get("template_id"),
            "channel": request.form.get("channel", "sms"),
            "mobile_only": request.form.get("mobile_only") == "on",
            "is_active": request.form.get("is_active") == "on",
            "sms_schedule_enabled": request.form.get("sms_schedule_enabled") == "on",
            "sms_window_start": request.form.get("sms_window_start"),
            "sms_window_end": request.form.get("sms_window_end"),
        }
        if not data["name"] or not data["template_id"]:
            flash("نام و قالب الزامی است.", "error")
        else:
            try:
                with mysql_connection(_config().mysql) as conn:
                    save_automation_rule(
                        conn, data, project_id=project_id, rule_id=rule_id
                    )
                    conn.commit()
                flash("قانون خودکار ذخیره شد.", "ok")
                return redirect(url_for("crm.automations_list"))
            except ValueError as exc:
                flash(str(exc), "error")

    row = None
    with mysql_connection(_config().mysql) as conn:
        templates = list_templates(conn, project_id=project_id)
        if rule_id:
            row = get_automation_rule(conn, rule_id, project_id=project_id)
            if not row:
                abort(404)
    return render_template(
        "crm/automation_form.html",
        row=row,
        templates=templates,
        hour_options=list(range(24)),
    )


@crm_bp.route("/automations/<int:rule_id>/delete", methods=["POST"])
@login_required
def automation_delete(rule_id: int):
    project_id = _project_id()
    with mysql_connection(_config().mysql) as conn:
        delete_automation_rule(conn, rule_id, project_id=project_id)
        conn.commit()
    flash("قانون حذف شد.", "ok")
    return redirect(url_for("crm.automations_list"))


@crm_bp.route("/campaigns")
@login_required
def campaigns_list():
    project_id = _project_id()
    with mysql_connection(_config().mysql) as conn:
        rows = list_campaigns(conn, project_id=project_id)
    return render_template("crm/campaigns.html", rows=rows)


def _parse_selected_domain_ids(form_or_args) -> list[int]:
    ids: list[int] = []
    for raw in form_or_args.getlist("domain_id"):
        if str(raw).isdigit():
            ids.append(int(raw))
    if not ids:
        for part in (form_or_args.get("domain_ids") or "").replace("\n", ",").split(","):
            part = part.strip()
            if part.isdigit():
                ids.append(int(part))
    if not ids:
        for part in (form_or_args.get("selected") or "").split(","):
            part = part.strip()
            if part.isdigit():
                ids.append(int(part))
    seen: set[int] = set()
    unique: list[int] = []
    for domain_id in ids:
        if domain_id not in seen:
            seen.add(domain_id)
            unique.append(domain_id)
    return unique


@crm_bp.route("/campaigns/new", methods=["GET", "POST"])
@login_required
def campaign_new():
    page_size = 50
    project_id = _project_id()
    post_selected_ids: list[int] = []
    form_defaults = {
        "name": "",
        "channel": "sms",
        "template_id": "",
        "mobile_only": True,
        "send_now": True,
    }
    if request.method == "POST":
        domain_ids = _parse_selected_domain_ids(request.form)
        form_defaults = {
            "name": request.form.get("name", "").strip(),
            "channel": request.form.get("channel", "sms"),
            "template_id": request.form.get("template_id") or "",
            "mobile_only": request.form.get("mobile_only") == "on",
            "send_now": request.form.get("send_now") == "on",
        }
        data = {
            "name": form_defaults["name"],
            "channel": form_defaults["channel"],
            "template_id": form_defaults["template_id"],
            "target_domain_ids": domain_ids,
            "mobile_only": form_defaults["mobile_only"],
            "created_by": session.get("admin_id"),
        }
        if not data["name"] or not data["template_id"] or not domain_ids:
            flash("نام، قالب و حداقل یک دامنه الزامی است.", "error")
            post_selected_ids = domain_ids
        else:
            try:
                with mysql_connection(_config().mysql) as conn:
                    campaign_id = create_campaign(conn, data, project_id=project_id)
                    # Mark as queued before the worker picks it up so the detail
                    # page shows a meaningful status immediately.
                    if form_defaults["send_now"]:
                        update_campaign_counts(conn, campaign_id, status="queued")
                    conn.commit()
                if form_defaults["send_now"]:
                    start_campaign_async(_config().mysql, campaign_id)
                    flash(
                        f"کمپین در صف ارسال قرار گرفت ({len(domain_ids)} دامنه). "
                        "پیشرفت ارسال در همین صفحه به‌روز می‌شود.",
                        "ok",
                    )
                    return redirect(url_for("crm.campaign_detail", campaign_id=campaign_id))
                flash("کمپین ذخیره شد.", "ok")
                return redirect(url_for("crm.campaign_detail", campaign_id=campaign_id))
            except ValueError as exc:
                flash(str(exc), "error")
                post_selected_ids = domain_ids

    from jalali_utils import format_jdate, is_jalali_date

    def _greg_to_jalali(value: str) -> str:
        value = (value or "").strip()
        if not value:
            return ""
        jalali = format_jdate(value)
        return jalali if is_jalali_date(jalali) else ""

    query = (request.args.get("q") or "").strip()
    phone_filter = request.args.get("phone_type", "")
    province = (request.args.get("province") or "").strip()
    city = (request.args.get("city") or "").strip()
    categories = [
        c.strip() for c in request.args.getlist("category") if c and c.strip()
    ]
    sort = request.args.get("sort", "latest")
    if sort not in ("latest", "newest", "approve", "top"):
        sort = "latest"
    outreach = (request.args.get("outreach") or "").strip()
    if outreach not in ("no_sms", "no_email", "no_sms_email"):
        outreach = ""
    approve_from_raw = (request.args.get("approve_from") or "").strip()
    approve_to_raw = (request.args.get("approve_to") or "").strip()
    created_from = (request.args.get("created_from") or "").strip()
    created_to = (request.args.get("created_to") or "").strip()
    approve_from = _greg_to_jalali(approve_from_raw)
    approve_to = _greg_to_jalali(approve_to_raw)

    selected_ids = post_selected_ids or _parse_selected_domain_ids(request.args)
    try:
        page = max(0, int(request.args.get("page", 0)))
    except ValueError:
        page = 0

    filter_kwargs = {
        "project_id": project_id,
        "query": query,
        "phone_type": phone_filter,
        "province": province,
        "city": city,
        "categories": categories,
        "approve_from": approve_from,
        "approve_to": approve_to,
        "created_from": created_from,
        "created_to": created_to,
        "outreach": outreach,
        "sort": sort,
    }
    with mysql_connection(_config().mysql) as conn:
        templates = list_templates(conn, project_id=project_id)
        from crm_db import count_domains_for_campaign, list_domains_for_campaign

        total = count_domains_for_campaign(conn, **filter_kwargs)
        domains = list_domains_for_campaign(
            conn,
            **filter_kwargs,
            offset=page * page_size,
            limit=page_size,
        )
        provinces = q.get_all_provinces(conn)
        province_cities = q.get_province_cities(conn)
        category_options = q.get_service_categories(conn)

    pages = (total + page_size - 1) // page_size if total else 1
    has_filters = any(
        [
            query,
            phone_filter,
            province,
            city,
            categories,
            approve_from_raw,
            approve_to_raw,
            created_from,
            created_to,
            outreach,
            sort != "latest",
        ]
    )
    return render_template(
        "crm/campaign_new.html",
        templates=templates,
        domains=domains,
        query=query,
        phone_filter=phone_filter,
        province=province,
        city=city,
        categories=categories,
        sort=sort,
        outreach=outreach,
        provinces=provinces,
        province_cities=province_cities,
        category_options=category_options,
        approve_from=approve_from_raw,
        approve_to=approve_to_raw,
        approve_from_disp=_greg_to_jalali(approve_from_raw),
        approve_to_disp=_greg_to_jalali(approve_to_raw),
        created_from=created_from,
        created_to=created_to,
        created_from_disp=_greg_to_jalali(created_from),
        created_to_disp=_greg_to_jalali(created_to),
        has_filters=has_filters,
        form=form_defaults,
        selected_ids=selected_ids,
        page=page,
        pages=pages,
        total=total,
        page_size=page_size,
    )


@crm_bp.route("/campaigns/<int:campaign_id>")
@login_required
def campaign_detail(campaign_id: int):
    project_id = _project_id()
    try:
        page = max(0, int(request.args.get("page", 0)))
    except ValueError:
        page = 0
    page_size = 50
    with mysql_connection(_config().mysql) as conn:
        campaign = get_campaign(conn, campaign_id, project_id=project_id)
        if not campaign:
            abort(404)
        logs = list_message_logs(
            conn,
            project_id=project_id,
            campaign_id=campaign_id,
            limit=page_size,
            offset=page * page_size,
        )
        total_logs = count_message_logs(
            conn, project_id=project_id, campaign_id=campaign_id
        )
    pages = (total_logs + page_size - 1) // page_size
    return render_template(
        "crm/campaign_detail.html",
        campaign=campaign,
        logs=logs,
        page=page,
        pages=pages,
        total_logs=total_logs,
        log_statuses=_LOG_STATUSES,
    )


_LOG_CHANNELS = {"sms": "پیامک", "email": "ایمیل"}
_LOG_STATUSES = {
    "sent": "ارسال موفق",
    "test": "آزمایشی",
    "failed": "ناموفق",
    "skipped": "رد شده",
    "pending": "در انتظار",
}


def _log_filter_args():
    channel = request.args.get("channel", "").strip()
    status = request.args.get("status", "").strip()
    date_from = request.args.get("from", "").strip()
    date_to = request.args.get("to", "").strip()
    search = (request.args.get("q") or "").strip()
    if channel not in _LOG_CHANNELS:
        channel = ""
    if status not in _LOG_STATUSES:
        status = ""
    return channel, status, date_from, date_to, search


@crm_bp.route("/logs")
@login_required
def message_logs():
    project_id = _project_id()
    channel, status, date_from, date_to, search = _log_filter_args()
    try:
        page = max(0, int(request.args.get("page", 0)))
    except ValueError:
        page = 0
    page_size = 50
    with mysql_connection(_config().mysql) as conn:
        stats = message_log_stats(
            conn,
            project_id=project_id,
            channel=channel,
            status=status,
            date_from=date_from,
            date_to=date_to,
            search=search,
        )
        total = count_message_logs(
            conn,
            project_id=project_id,
            channel=channel,
            status=status,
            date_from=date_from,
            date_to=date_to,
            search=search,
        )
        logs = list_message_logs(
            conn,
            project_id=project_id,
            channel=channel,
            status=status,
            date_from=date_from,
            date_to=date_to,
            search=search,
            limit=page_size,
            offset=page * page_size,
        )
    pages = (total + page_size - 1) // page_size if total else 1
    return render_template(
        "crm/logs.html",
        logs=logs,
        stats=stats,
        channels=_LOG_CHANNELS,
        statuses=_LOG_STATUSES,
        channel=channel,
        status=status,
        date_from=date_from,
        date_to=date_to,
        query=search,
        page=page,
        pages=pages,
        total=total,
        page_size=page_size,
    )


@crm_bp.route("/logs/export.csv")
@login_required
def message_logs_export():
    from flask import Response

    project_id = _project_id()
    channel, status, date_from, date_to, search = _log_filter_args()

    def generate():
        import csv
        import io

        header = [
            "id",
            "زمان",
            "کانال",
            "وضعیت",
            "گیرنده",
            "نوع گیرنده",
            "دامنه",
            "کسب‌وکار",
            "کمپین",
            "قالب",
            "شناسه پیام",
            "خطا",
        ]
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(header)
        yield "\ufeff" + buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)

        from jalali_utils import format_jdatetime

        with mysql_connection(_config().mysql) as conn:
            for row in iter_message_logs_for_export(
                conn,
                project_id=project_id,
                channel=channel,
                status=status,
                date_from=date_from,
                date_to=date_to,
                search=search,
            ):
                writer.writerow(
                    [
                        row.get("id"),
                        format_jdatetime(row.get("sent_at") or row.get("created_at")),
                        _LOG_CHANNELS.get(row.get("channel"), row.get("channel") or ""),
                        _LOG_STATUSES.get(row.get("status"), row.get("status") or ""),
                        row.get("recipient") or "",
                        row.get("recipient_type") or "",
                        row.get("domain") or "",
                        row.get("business_name") or "",
                        row.get("campaign_name") or "",
                        row.get("template_name") or "",
                        row.get("provider_message_id") or "",
                        row.get("error_message") or "",
                    ]
                )
                yield buffer.getvalue()
                buffer.seek(0)
                buffer.truncate(0)

    filename = "message_logs.csv"
    return Response(
        generate(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@crm_bp.route("/campaigns/<int:campaign_id>/send", methods=["POST"])
@login_required
def campaign_send(campaign_id: int):
    project_id = _project_id()
    with mysql_connection(_config().mysql) as conn:
        campaign = get_campaign(conn, campaign_id, project_id=project_id)
        if not campaign:
            abort(404)
        if campaign["status"] in ("queued", "running") or is_campaign_running(campaign_id):
            flash("این کمپین هم‌اکنون در حال ارسال است.", "error")
            return redirect(url_for("crm.campaign_detail", campaign_id=campaign_id))
        update_campaign_counts(conn, campaign_id, status="queued")
        conn.commit()
    if start_campaign_async(_config().mysql, campaign_id):
        flash("ارسال در صف قرار گرفت؛ پیشرفت در همین صفحه به‌روز می‌شود.", "ok")
    else:
        flash("این کمپین هم‌اکنون در حال ارسال است.", "error")
    return redirect(url_for("crm.campaign_detail", campaign_id=campaign_id))


@crm_bp.route("/campaigns/<int:campaign_id>/status")
@login_required
def campaign_status(campaign_id: int):
    project_id = _project_id()
    with mysql_connection(_config().mysql) as conn:
        campaign = get_campaign(conn, campaign_id, project_id=project_id)
    if not campaign:
        abort(404)
    total = int(campaign.get("total_count") or 0)
    processed = (
        int(campaign.get("sent_count") or 0)
        + int(campaign.get("failed_count") or 0)
        + int(campaign.get("skipped_count") or 0)
    )
    return jsonify(
        {
            "status": campaign["status"],
            "total": total,
            "sent": int(campaign.get("sent_count") or 0),
            "failed": int(campaign.get("failed_count") or 0),
            "skipped": int(campaign.get("skipped_count") or 0),
            "pending": max(0, total - processed),
            "running": campaign["status"] in ("queued", "running"),
        }
    )


@crm_bp.route("/admins")
@login_required
@super_admin_required
def admins_list():
    with mysql_connection(_config().mysql) as conn:
        rows = list_admins(conn)
    return render_template("crm/admins.html", rows=rows)


@crm_bp.route("/admins/new", methods=["GET", "POST"])
@crm_bp.route("/admins/<int:admin_id>/edit", methods=["GET", "POST"])
@login_required
@super_admin_required
def admin_form(admin_id: int | None = None):
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        display_name = request.form.get("display_name", "").strip()
        role = request.form.get("role", ROLE_ADMIN)
        is_active = request.form.get("is_active") == "on"
        if role not in (ROLE_SUPER, ROLE_ADMIN):
            role = ROLE_ADMIN
        with mysql_connection(_config().mysql) as conn:
            if admin_id:
                update_admin(
                    conn,
                    admin_id,
                    display_name=display_name,
                    role=role,
                    is_active=is_active,
                    password=password or None,
                )
            else:
                if not username or not password:
                    flash("نام کاربری و رمز عبور الزامی است.", "error")
                    return redirect(url_for("crm.admin_form"))
                create_admin(
                    conn,
                    username=username,
                    password=password,
                    display_name=display_name,
                    role=role,
                )
            conn.commit()
        flash("مدیر ذخیره شد.", "ok")
        return redirect(url_for("crm.admins_list"))

    row = None
    if admin_id:
        with mysql_connection(_config().mysql) as conn:
            row = get_admin_by_id(conn, admin_id)
        if not row:
            abort(404)
    return render_template("crm/admin_form.html", row=row, roles=[ROLE_SUPER, ROLE_ADMIN])


@crm_bp.route("/account/password", methods=["GET", "POST"])
@login_required
def change_password():
    admin_id = session.get("admin_id")
    if not admin_id:
        abort(403)
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if len(new) < 6:
            flash("رمز عبور جدید باید حداقل ۶ کاراکتر باشد.", "error")
        elif new != confirm:
            flash("رمز عبور جدید و تکرار آن یکسان نیستند.", "error")
        else:
            with mysql_connection(_config().mysql) as conn:
                if not verify_admin_password(conn, admin_id, current):
                    flash("رمز عبور فعلی نادرست است.", "error")
                else:
                    change_admin_password(conn, admin_id, new)
                    conn.commit()
                    flash("رمز عبور با موفقیت تغییر کرد.", "ok")
                    return redirect(url_for("crm.change_password"))
    return render_template("crm/change_password.html")


@crm_bp.route("/preview", methods=["POST"])
@login_required
def preview():
    project_id = _project_id()
    template_id = int(request.form.get("template_id", 0))
    domain_id = int(request.form.get("domain_id", 0))
    with mysql_connection(_config().mysql) as conn:
        template = get_template(conn, template_id, project_id=project_id)
        domain = q.get_domain_by_id(conn, domain_id)
    if not template or not domain:
        return "قالب یا دامنه یافت نشد", 404
    return preview_template(template, domain)


@crm_bp.route("/preview-email-html", methods=["POST"])
@login_required
def preview_email_html():
    from email_layout import prepare_email_html

    data = request.get_json(silent=True) or {}
    body = data.get("body", "")
    return jsonify({"html": prepare_email_html(body)})


@crm_bp.route("/preview-context")
@login_required
def preview_context():
    """Return template variables for live preview (sample or a real domain)."""
    domain_query = (request.args.get("domain") or "").strip()

    with mysql_connection(_config().mysql) as conn:
        row = None
        if not domain_query:
            # No query: auto-pick a real domain with complete info.
            row = q.get_sample_domain(conn)
            if row:
                context = build_template_context(row)
                return jsonify(
                    {"source": "domain", "domain": row.get("domain"),
                     "default": True, "context": context}
                )
            return jsonify({"source": "sample", "context": _SAMPLE_CONTEXT})

        if domain_query.isdigit():
            row = q.get_domain_by_id(conn, int(domain_query))
        if not row:
            matches = q.search_domains(conn, domain_query, limit=1)
            row = matches[0] if matches else None

    if not row:
        return jsonify({"source": "sample", "context": _SAMPLE_CONTEXT, "notfound": True})

    context = build_template_context(row)
    return jsonify({"source": "domain", "domain": row.get("domain"), "context": context})


@crm_bp.route("/templates/test-send", methods=["POST"])
@login_required
def template_test_send():
    """Send a real SMS/email using the current form values + a selected domain.

    Always bypasses dry-run so the admin can receive the real message on their
    own phone/email (pick a domain that has that contact, or override recipient).
    """
    data = request.get_json(silent=True) or {}
    channel = (data.get("channel") or "sms").strip()
    if channel not in ("sms", "email"):
        return jsonify({"ok": False, "error": "کانال نامعتبر"}), 400

    domain_query = (data.get("domain") or "").strip()
    if not domain_query:
        return jsonify({"ok": False, "error": "دامنه را بارگذاری کنید"}), 400

    override = (data.get("override_recipient") or "").strip() or None

    token_mapping = {}
    for kn_token in KAVENEGAR_TOKENS:
        var_name = (data.get(f"map_{kn_token}") or "").strip()
        if var_name:
            token_mapping[kn_token] = var_name

    template = {
        "id": int(data["template_id"]) if data.get("template_id") else None,
        "channel": channel,
        "kavenegar_template": (data.get("kavenegar_template") or "").strip(),
        "token_mapping": token_mapping,
        "email_subject": data.get("email_subject") or "",
        "email_body": data.get("email_body") or "",
    }

    if channel == "sms" and not template["kavenegar_template"]:
        return jsonify({"ok": False, "error": "نام الگوی کاوه‌نگار را وارد کنید"}), 400
    if channel == "email" and not (template["email_subject"] or template["email_body"]):
        return jsonify({"ok": False, "error": "موضوع یا متن ایمیل خالی است"}), 400

    with mysql_connection(_config().mysql) as conn:
        row = None
        if domain_query.isdigit():
            row = q.get_domain_by_id(conn, int(domain_query))
        if not row:
            matches = q.search_domains(conn, domain_query, limit=1)
            row = matches[0] if matches else None
        if not row:
            return jsonify({"ok": False, "error": "دامنه یافت نشد"}), 404

        result = send_to_domain(
            conn,
            domain_row=row,
            template=template,
            channel=channel,
            project_id=_project_id(),
            mobile_only=not bool(override),
            force_send=True,
            override_recipient=override,
        )
        conn.commit()

    status = result.get("status")
    recipient = result.get("recipient") or override or ""
    if status == "sent":
        return jsonify(
            {
                "ok": True,
                "status": status,
                "recipient": recipient,
                "domain": row.get("domain"),
                "message": f"ارسال شد به {recipient}",
            }
        )
    if status == "skipped":
        reasons = {
            "landline": "شماره این دامنه ثابت است — موبایل ندارد",
            "no_mobile": "موبایل معتبری برای این دامنه ثبت نشده",
            "no_email": "ایمیل معتبری برای این دامنه ثبت نشده",
        }
        return jsonify(
            {
                "ok": False,
                "status": status,
                "error": reasons.get(result.get("reason"), "ارسال رد شد"),
            }
        ), 400
    return jsonify(
        {
            "ok": False,
            "status": status,
            "error": result.get("error") or "ارسال ناموفق",
        }
    ), 400


@crm_bp.route("/calls")
@login_required
def calls_list():
    project_id = _project_id()
    filter_type = request.args.get("filter", "all")
    if filter_type not in ("all", "today", "interested"):
        filter_type = "all"
    try:
        page = max(0, int(request.args.get("page", 0)))
    except ValueError:
        page = 0
    page_size = 30
    offset = page * page_size

    with mysql_connection(_config().mysql) as conn:
        rows = list_call_logs(
            conn,
            project_id=project_id,
            filter_type=filter_type,
            limit=page_size,
            offset=offset,
        )
        total = count_call_logs(
            conn, project_id=project_id, filter_type=filter_type
        )
        stats = call_stats(conn, project_id=project_id)

    pages = (total + page_size - 1) // page_size
    return render_template(
        "crm/calls.html",
        rows=rows,
        filter_type=filter_type,
        stats=stats,
        outcomes=CALL_OUTCOMES,
        page=page,
        pages=pages,
        total=total,
    )


@crm_bp.route("/calls/new", methods=["GET", "POST"])
@login_required
def call_new():
    project_id = _project_id()
    domain_id = request.args.get("domain_id") or request.form.get("domain_id")
    domain_row = None
    search_results = []

    if request.method == "POST":
        domain_id_raw = request.form.get("domain_id", "").strip()
        outcome = request.form.get("outcome", "").strip()
        notes = request.form.get("notes", "").strip()
        phone_used = request.form.get("phone_used", "").strip()
        next_follow_up = request.form.get("next_follow_up_at", "").strip() or None

        if not domain_id_raw or not domain_id_raw.isdigit():
            flash("دامنه را انتخاب کنید.", "error")
        elif outcome not in CALL_OUTCOMES:
            flash("نتیجه تماس را انتخاب کنید.", "error")
        else:
            with mysql_connection(_config().mysql) as conn:
                create_call_log(
                    conn,
                    {
                        "domain_id": int(domain_id_raw),
                        "created_by": session.get("admin_id"),
                        "phone_used": phone_used,
                        "outcome": outcome,
                        "notes": notes,
                        "next_follow_up_at": next_follow_up,
                    },
                    project_id=project_id,
                )
                conn.commit()
            flash("تماس ثبت شد.", "ok")
            return redirect(
                url_for("crm.calls_list", filter="today" if outcome == "callback" else "all")
            )

    query = (request.args.get("q") or "").strip()
    with mysql_connection(_config().mysql) as conn:
        if domain_id and str(domain_id).isdigit():
            domain_row = q.get_domain_by_id(conn, int(domain_id))
        if query:
            search_results = q.search_domains(conn, query, limit=20)

    default_phone = ""
    if domain_row:
        default_phone = (
            domain_row.get("mobile_phone")
            or domain_row.get("phone")
            or ""
        )

    return render_template(
        "crm/call_form.html",
        domain=domain_row,
        search_results=search_results,
        query=query,
        outcomes=CALL_OUTCOMES,
        default_phone=default_phone,
    )


@crm_bp.route("/calls/<int:call_id>")
@login_required
def call_detail(call_id: int):
    project_id = _project_id()
    with mysql_connection(_config().mysql) as conn:
        row = get_call_log(conn, call_id, project_id=project_id)
    if not row:
        abort(404)
    return render_template(
        "crm/call_detail.html", row=row, outcomes=CALL_OUTCOMES
    )


_MEMBER_ROLE_LABELS = {
    PROJECT_OWNER: "مالک",
    PROJECT_ADMIN: "مدیر",
    PROJECT_MEMBER: "عضو",
}


@crm_bp.route("/members")
@login_required
@project_admin_required
def members_list():
    project_id = _project_id()
    with mysql_connection(_config().mysql) as conn:
        rows = list_project_members(conn, project_id)
    return render_template(
        "crm/members.html",
        rows=rows,
        role_labels=_MEMBER_ROLE_LABELS,
    )


@crm_bp.route("/members/add", methods=["GET", "POST"])
@login_required
@project_admin_required
def members_add():
    project_id = _project_id()
    if request.method == "POST":
        mode = request.form.get("mode", "existing")
        role = request.form.get("role", PROJECT_ADMIN)
        if role not in PROJECT_ROLES:
            role = PROJECT_ADMIN
        try:
            with mysql_connection(_config().mysql) as conn:
                if mode == "new":
                    username = (request.form.get("username") or "").strip()
                    password = request.form.get("password", "")
                    display_name = (request.form.get("display_name") or "").strip()
                    if not username or not password:
                        raise ValueError("نام کاربری و رمز عبور الزامی است")
                    if len(password) < 6:
                        raise ValueError("رمز عبور باید حداقل ۶ کاراکتر باشد")
                    if find_admin_by_username(conn, username):
                        raise ValueError("این نام کاربری قبلاً ثبت شده است")
                    user_id = create_admin(
                        conn,
                        username=username,
                        password=password,
                        display_name=display_name or username,
                        role=ROLE_ADMIN,
                    )
                else:
                    username = (request.form.get("username") or "").strip()
                    existing = find_admin_by_username(conn, username)
                    if not existing or not existing.get("is_active"):
                        raise ValueError("کاربری با این نام کاربری یافت نشد")
                    user_id = int(existing["id"])
                add_project_member(
                    conn, project_id=project_id, user_id=user_id, role=role
                )
                conn.commit()
            flash("عضو به پروژه اضافه شد.", "ok")
            return redirect(url_for("crm.members_list"))
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template(
        "crm/member_form.html",
        roles=PROJECT_ROLES,
        role_labels=_MEMBER_ROLE_LABELS,
    )


@crm_bp.route("/members/<int:user_id>/role", methods=["POST"])
@login_required
@project_admin_required
def members_role(user_id: int):
    project_id = _project_id()
    role = request.form.get("role", PROJECT_ADMIN)
    try:
        with mysql_connection(_config().mysql) as conn:
            update_project_member_role(
                conn, project_id=project_id, user_id=user_id, role=role
            )
            conn.commit()
        flash("نقش عضو به‌روز شد.", "ok")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("crm.members_list"))


@crm_bp.route("/members/<int:user_id>/remove", methods=["POST"])
@login_required
@project_admin_required
def members_remove(user_id: int):
    project_id = _project_id()
    try:
        with mysql_connection(_config().mysql) as conn:
            remove_project_member(
                conn, project_id=project_id, user_id=user_id
            )
            conn.commit()
        flash("عضو از پروژه حذف شد.", "ok")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("crm.members_list"))
