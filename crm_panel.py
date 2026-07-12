"""CRM admin panel routes (Flask Blueprint)."""
from __future__ import annotations

from functools import wraps

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
    CRM_SETTINGS_KEYS,
    ROLE_ADMIN,
    ROLE_SUPER,
    create_admin,
    create_campaign,
    crm_stats,
    delete_automation_rule,
    delete_template,
    get_admin_by_id,
    get_all_settings,
    get_automation_rule,
    get_campaign,
    get_template,
    list_admins,
    list_automation_rules,
    list_campaigns,
    list_message_logs,
    list_templates,
    preview_template,
    save_automation_rule,
    save_settings,
    save_template,
    update_admin,
    count_message_logs,
)
from crm_service import run_campaign
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


@crm_bp.app_context_processor
def inject_admin():
    if session.get("admin_id"):
        return {"current_admin": _current_admin()}
    return {}


@crm_bp.route("/")
@login_required
def dashboard():
    with mysql_connection(_config().mysql) as conn:
        stats = crm_stats(conn)
        campaigns = list_campaigns(conn, limit=5)
    return render_template("crm/dashboard.html", stats=stats, campaigns=campaigns)


@crm_bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        data = {key: request.form.get(key, "") for key in CRM_SETTINGS_KEYS}
        data["smtp_tls"] = "yes" if request.form.get("smtp_tls") == "on" else "no"
        with mysql_connection(_config().mysql) as conn:
            save_settings(conn, data)
            conn.commit()
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


@crm_bp.route("/campaigns/new", methods=["GET", "POST"])
@login_required
def campaign_new():
    if request.method == "POST":
        domain_ids_raw = request.form.get("domain_ids", "")
        domain_ids = []
        for part in domain_ids_raw.replace("\n", ",").split(","):
            part = part.strip()
            if part.isdigit():
                domain_ids.append(int(part))
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
    selected_ids = request.args.getlist("domain_id")
    domains = []
    with mysql_connection(_config().mysql) as conn:
        templates = list_templates(conn)
        if query:
            domains = q.search_domains(conn, query, limit=50)
        elif phone_filter:
            domains = q.get_domains_by_phone_type(conn, phone_filter, limit=50)
    return render_template(
        "crm/campaign_new.html",
        templates=templates,
        domains=domains,
        query=query,
        phone_filter=phone_filter,
        selected_ids=[int(x) for x in selected_ids if x.isdigit()],
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
