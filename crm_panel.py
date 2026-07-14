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
    ROLE_ADMIN,
    ROLE_SUPER,
    change_admin_password,
    create_admin,
    create_call_log,
    create_campaign,
    crm_stats,
    count_call_logs,
    delete_automation_rule,
    delete_template,
    get_admin_by_id,
    get_all_settings,
    get_automation_rule,
    get_call_log,
    get_campaign,
    get_call_logs_for_domain,
    get_latest_call_for_domain,
    get_template,
    is_dry_run,
    list_admins,
    list_automation_rules,
    list_call_logs,
    list_campaigns,
    list_message_logs,
    list_templates,
    preview_template,
    save_automation_rule,
    save_settings,
    save_template,
    update_admin,
    verify_admin_password,
    count_message_logs,
    message_log_stats,
    iter_message_logs_for_export,
    call_stats,
)
from crm_service import run_campaign, send_to_domain
from db import mysql_connection

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
    from webapp import app_config

    return app_config()


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


def _current_admin() -> dict:
    return {
        "id": session.get("admin_id"),
        "username": session.get("admin_username"),
        "display_name": session.get("admin_display_name"),
        "role": session.get("admin_role"),
        "is_super": session.get("admin_role") == ROLE_SUPER,
    }


def _test_mode_enabled() -> bool:
    def produce():
        with mysql_connection(_config().mysql) as conn:
            return is_dry_run(get_all_settings(conn))

    try:
        return bool(cached("crm_dry_run", 30, produce))
    except Exception:  # noqa: BLE001
        return False


@crm_bp.app_context_processor
def inject_admin():
    if session.get("admin_id"):
        return {
            "current_admin": _current_admin(),
            "crm_test_mode": _test_mode_enabled(),
        }
    return {}


@crm_bp.route("/")
@login_required
def dashboard():
    def produce():
        with mysql_connection(_config().mysql) as conn:
            return {
                "stats": crm_stats(conn),
                "campaigns": list_campaigns(conn, limit=5),
            }

    ttl = int(os.environ.get("WEB_STATS_CACHE_TTL", "60"))
    data = cached("crm_dashboard", ttl, produce)
    return render_template("crm/dashboard.html", **data)


@crm_bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        data = {key: request.form.get(key, "") for key in CRM_SETTINGS_KEYS}
        data["smtp_tls"] = "yes" if request.form.get("smtp_tls") == "on" else "no"
        data["dry_run"] = "yes" if request.form.get("dry_run") == "on" else "no"
        with mysql_connection(_config().mysql) as conn:
            save_settings(conn, data)
            conn.commit()
        invalidate("crm_dry_run")
        flash("تنظیمات ذخیره شد.", "ok")
        return redirect(url_for("crm.settings"))

    with mysql_connection(_config().mysql) as conn:
        stored = get_all_settings(conn)
    return render_template("crm/settings.html", settings=stored)


@crm_bp.route("/templates")
@login_required
def templates_list():
    channel = request.args.get("channel")
    with mysql_connection(_config().mysql) as conn:
        rows = list_templates(conn, channel=channel or None)
    return render_template("crm/templates_list.html", rows=rows, channel=channel)


@crm_bp.route("/templates/new", methods=["GET", "POST"])
@crm_bp.route("/templates/<int:template_id>/edit", methods=["GET", "POST"])
@login_required
def template_form(template_id: int | None = None):
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
                save_template(conn, data, template_id=template_id)
                conn.commit()
            flash("قالب ذخیره شد.", "ok")
            return redirect(url_for("crm.templates_list"))

    row = None
    if template_id:
        with mysql_connection(_config().mysql) as conn:
            row = get_template(conn, template_id)
        if not row:
            abort(404)
    return render_template(
        "crm/template_form.html",
        row=row,
        variables=TEMPLATE_VARIABLES,
        kavenegar_tokens=KAVENEGAR_TOKENS,
        sample_context=_SAMPLE_CONTEXT,
    )


@crm_bp.route("/templates/<int:template_id>/delete", methods=["POST"])
@login_required
def template_delete(template_id: int):
    with mysql_connection(_config().mysql) as conn:
        delete_template(conn, template_id)
        conn.commit()
    flash("قالب حذف شد.", "ok")
    return redirect(url_for("crm.templates_list"))


@crm_bp.route("/automations")
@login_required
def automations_list():
    with mysql_connection(_config().mysql) as conn:
        rows = list_automation_rules(conn)
        templates = list_templates(conn)
    return render_template("crm/automations.html", rows=rows, templates=templates)


@crm_bp.route("/automations/new", methods=["GET", "POST"])
@crm_bp.route("/automations/<int:rule_id>/edit", methods=["GET", "POST"])
@login_required
def automation_form(rule_id: int | None = None):
    if request.method == "POST":
        data = {
            "name": request.form.get("name", "").strip(),
            "trigger_type": "new_domain",
            "template_id": request.form.get("template_id"),
            "channel": request.form.get("channel", "sms"),
            "mobile_only": request.form.get("mobile_only") == "on",
            "is_active": request.form.get("is_active") == "on",
        }
        if not data["name"] or not data["template_id"]:
            flash("نام و قالب الزامی است.", "error")
        else:
            with mysql_connection(_config().mysql) as conn:
                save_automation_rule(conn, data, rule_id=rule_id)
                conn.commit()
            flash("قانون خودکار ذخیره شد.", "ok")
            return redirect(url_for("crm.automations_list"))

    row = None
    with mysql_connection(_config().mysql) as conn:
        templates = list_templates(conn)
        if rule_id:
            row = get_automation_rule(conn, rule_id)
            if not row:
                abort(404)
    return render_template("crm/automation_form.html", row=row, templates=templates)


@crm_bp.route("/automations/<int:rule_id>/delete", methods=["POST"])
@login_required
def automation_delete(rule_id: int):
    with mysql_connection(_config().mysql) as conn:
        delete_automation_rule(conn, rule_id)
        conn.commit()
    flash("قانون حذف شد.", "ok")
    return redirect(url_for("crm.automations_list"))


@crm_bp.route("/campaigns")
@login_required
def campaigns_list():
    with mysql_connection(_config().mysql) as conn:
        rows = list_campaigns(conn)
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
    post_selected_ids: list[int] = []
    if request.method == "POST":
        domain_ids = _parse_selected_domain_ids(request.form)
        data = {
            "name": request.form.get("name", "").strip(),
            "channel": request.form.get("channel", "sms"),
            "template_id": request.form.get("template_id"),
            "target_domain_ids": domain_ids,
            "mobile_only": request.form.get("mobile_only") == "on",
            "created_by": session.get("admin_id"),
        }
        if not data["name"] or not data["template_id"] or not domain_ids:
            flash("نام، قالب و حداقل یک دامنه الزامی است.", "error")
            post_selected_ids = domain_ids
        else:
            with mysql_connection(_config().mysql) as conn:
                campaign_id = create_campaign(conn, data)
                conn.commit()
            if request.form.get("send_now") == "on":
                try:
                    with mysql_connection(_config().mysql) as conn:
                        counts = run_campaign(conn, campaign_id)
                    flash(
                        f"کمپین ارسال شد: {counts['sent']} موفق، "
                        f"{counts['failed']} ناموفق، {counts['skipped']} رد شده.",
                        "ok",
                    )
                except Exception as exc:  # noqa: BLE001
                    flash(f"خطا در ارسال: {exc}", "error")
                return redirect(url_for("crm.campaign_detail", campaign_id=campaign_id))
            flash("کمپین ذخیره شد.", "ok")
            return redirect(url_for("crm.campaign_detail", campaign_id=campaign_id))

    query = (request.args.get("q") or "").strip()
    phone_filter = request.args.get("phone_type", "")
    selected_ids = post_selected_ids or _parse_selected_domain_ids(request.args)
    try:
        page = max(0, int(request.args.get("page", 0)))
    except ValueError:
        page = 0
    with mysql_connection(_config().mysql) as conn:
        templates = list_templates(conn)
        from crm_db import count_domains_for_campaign, list_domains_for_campaign

        total = count_domains_for_campaign(
            conn, query=query, phone_type=phone_filter
        )
        domains = list_domains_for_campaign(
            conn,
            query=query,
            phone_type=phone_filter,
            offset=page * page_size,
            limit=page_size,
        )
    pages = (total + page_size - 1) // page_size if total else 1
    return render_template(
        "crm/campaign_new.html",
        templates=templates,
        domains=domains,
        query=query,
        phone_filter=phone_filter,
        selected_ids=selected_ids,
        page=page,
        pages=pages,
        total=total,
        page_size=page_size,
    )


@crm_bp.route("/campaigns/<int:campaign_id>")
@login_required
def campaign_detail(campaign_id: int):
    try:
        page = max(0, int(request.args.get("page", 0)))
    except ValueError:
        page = 0
    page_size = 50
    with mysql_connection(_config().mysql) as conn:
        campaign = get_campaign(conn, campaign_id)
        if not campaign:
            abort(404)
        logs = list_message_logs(
            conn, campaign_id=campaign_id, limit=page_size, offset=page * page_size
        )
        total_logs = count_message_logs(conn, campaign_id=campaign_id)
    pages = (total_logs + page_size - 1) // page_size
    return render_template(
        "crm/campaign_detail.html",
        campaign=campaign,
        logs=logs,
        page=page,
        pages=pages,
        total_logs=total_logs,
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
    channel, status, date_from, date_to, search = _log_filter_args()
    try:
        page = max(0, int(request.args.get("page", 0)))
    except ValueError:
        page = 0
    page_size = 50
    with mysql_connection(_config().mysql) as conn:
        stats = message_log_stats(
            conn,
            channel=channel,
            status=status,
            date_from=date_from,
            date_to=date_to,
            search=search,
        )
        total = count_message_logs(
            conn,
            channel=channel,
            status=status,
            date_from=date_from,
            date_to=date_to,
            search=search,
        )
        logs = list_message_logs(
            conn,
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
    try:
        with mysql_connection(_config().mysql) as conn:
            counts = run_campaign(conn, campaign_id)
        flash(
            f"ارسال انجام شد: {counts['sent']} موفق، "
            f"{counts['failed']} ناموفق، {counts['skipped']} رد شده.",
            "ok",
        )
    except Exception as exc:  # noqa: BLE001
        flash(f"خطا: {exc}", "error")
    return redirect(url_for("crm.campaign_detail", campaign_id=campaign_id))


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
    template_id = int(request.form.get("template_id", 0))
    domain_id = int(request.form.get("domain_id", 0))
    with mysql_connection(_config().mysql) as conn:
        template = get_template(conn, template_id)
        domain = q.get_domain_by_id(conn, domain_id)
    if not template or not domain:
        return "قالب یا دامنه یافت نشد", 404
    return preview_template(template, domain)


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
            conn, filter_type=filter_type, limit=page_size, offset=offset
        )
        total = count_call_logs(conn, filter_type=filter_type)
        stats = call_stats(conn)

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
    with mysql_connection(_config().mysql) as conn:
        row = get_call_log(conn, call_id)
    if not row:
        abort(404)
    return render_template(
        "crm/call_detail.html", row=row, outcomes=CALL_OUTCOMES
    )
