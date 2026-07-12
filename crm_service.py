"""CRM message sending orchestration."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from contact_utils import build_template_context, normalize_email, render_text_template
from crm_db import (
    build_kavenegar_tokens,
    get_active_rules_for_trigger,
    get_all_settings,
    get_campaign,
    get_domains_by_ids,
    get_template,
    insert_message_log,
    update_campaign_counts,
)
from email_sender import EmailConfig, EmailSendError, send_email
from sms_kavenegar import KavenegarClient, KavenegarError

log = logging.getLogger("enamad-crm")


def _now():
    return datetime.now()


def send_sms_to_domain(
    conn,
    *,
    domain_row: dict,
    template: dict,
    settings: dict[str, str] | None = None,
    mobile_only: bool = True,
    campaign_id: int | None = None,
    automation_rule_id: int | None = None,
) -> dict[str, Any]:
    settings = settings or get_all_settings(conn)
    context = build_template_context(domain_row)
    phone_type = context.get("phone_type")
    mobile = context.get("mobile_phone")

    base_log = {
        "campaign_id": campaign_id,
        "automation_rule_id": automation_rule_id,
        "domain_id": domain_row.get("id"),
        "channel": "sms",
        "template_id": template.get("id"),
    }

    if mobile_only and phone_type == "landline":
        insert_message_log(
            conn,
            {
                **base_log,
                "recipient": domain_row.get("phone"),
                "recipient_type": "landline",
                "status": "skipped",
                "error_message": "شماره ثابت — پیامک ارسال نشد",
            },
        )
        return {"status": "skipped", "reason": "landline"}

    if not mobile:
        insert_message_log(
            conn,
            {
                **base_log,
                "recipient": domain_row.get("phone"),
                "recipient_type": phone_type or "unknown",
                "status": "skipped",
                "error_message": "شماره موبایل معتبر یافت نشد",
            },
        )
        return {"status": "skipped", "reason": "no_mobile"}

    if not template.get("kavenegar_template"):
        insert_message_log(
            conn,
            {
                **base_log,
                "recipient": mobile,
                "recipient_type": "mobile",
                "status": "failed",
                "error_message": "نام الگوی کاوه‌نگار تنظیم نشده",
            },
        )
        return {"status": "failed", "reason": "no_template_name"}

    tokens = build_kavenegar_tokens(template, domain_row)
    client = KavenegarClient(settings.get("kavenegar_api_key", ""))
    try:
        result = client.lookup(
            mobile,
            template["kavenegar_template"],
            token=tokens.get("token", "-"),
            token2=tokens.get("token2"),
            token3=tokens.get("token3"),
            token10=tokens.get("token10"),
            token20=tokens.get("token20"),
        )
        insert_message_log(
            conn,
            {
                **base_log,
                "recipient": mobile,
                "recipient_type": "mobile",
                "status": "sent",
                "provider_message_id": str(result.get("messageid") or ""),
                "sent_at": _now(),
            },
        )
        return {"status": "sent", "messageid": result.get("messageid")}
    except KavenegarError as exc:
        insert_message_log(
            conn,
            {
                **base_log,
                "recipient": mobile,
                "recipient_type": "mobile",
                "status": "failed",
                "error_message": f"[{exc.status}] {exc.message}",
            },
        )
        return {"status": "failed", "error": exc.message}


def send_email_to_domain(
    conn,
    *,
    domain_row: dict,
    template: dict,
    settings: dict[str, str] | None = None,
    campaign_id: int | None = None,
    automation_rule_id: int | None = None,
) -> dict[str, Any]:
    settings = settings or get_all_settings(conn)
    context = build_template_context(domain_row)
    email = context.get("email_normalized") or normalize_email(domain_row.get("email"))

    base_log = {
        "campaign_id": campaign_id,
        "automation_rule_id": automation_rule_id,
        "domain_id": domain_row.get("id"),
        "channel": "email",
        "template_id": template.get("id"),
    }

    if not email:
        insert_message_log(
            conn,
            {
                **base_log,
                "recipient": domain_row.get("email"),
                "recipient_type": "email",
                "status": "skipped",
                "error_message": "ایمیل معتبر یافت نشد",
            },
        )
        return {"status": "skipped", "reason": "no_email"}

    subject = render_text_template(template.get("email_subject") or "", context)
    body = render_text_template(template.get("email_body") or "", context)
    config = EmailConfig.from_settings(settings)

    try:
        send_email(config, to_addr=email, subject=subject, body=body)
        insert_message_log(
            conn,
            {
                **base_log,
                "recipient": email,
                "recipient_type": "email",
                "status": "sent",
                "sent_at": _now(),
            },
        )
        return {"status": "sent"}
    except EmailSendError as exc:
        insert_message_log(
            conn,
            {
                **base_log,
                "recipient": email,
                "recipient_type": "email",
                "status": "failed",
                "error_message": str(exc),
            },
        )
        return {"status": "failed", "error": str(exc)}


def send_to_domain(
    conn,
    *,
    domain_row: dict,
    template: dict,
    channel: str,
    mobile_only: bool = True,
    campaign_id: int | None = None,
    automation_rule_id: int | None = None,
    settings: dict[str, str] | None = None,
) -> dict[str, Any]:
    if channel == "sms":
        return send_sms_to_domain(
            conn,
            domain_row=domain_row,
            template=template,
            settings=settings,
            mobile_only=mobile_only,
            campaign_id=campaign_id,
            automation_rule_id=automation_rule_id,
        )
    if channel == "email":
        return send_email_to_domain(
            conn,
            domain_row=domain_row,
            template=template,
            settings=settings,
            campaign_id=campaign_id,
            automation_rule_id=automation_rule_id,
        )
    return {"status": "failed", "error": "کانال نامعتبر"}


def run_campaign(conn, campaign_id: int) -> dict[str, int]:
    campaign = get_campaign(conn, campaign_id)
    if not campaign:
        raise ValueError("کمپین یافت نشد")

    template = get_template(conn, campaign["template_id"])
    if not template or not template.get("is_active"):
        raise ValueError("قالب فعال یافت نشد")

    domain_ids = campaign.get("target_domain_ids") or []
    domains = get_domains_by_ids(conn, domain_ids)
    settings = get_all_settings(conn)
    mobile_only = bool(campaign.get("mobile_only", 1))

    update_campaign_counts(conn, campaign_id, status="running", started=True)
    counts = {"sent": 0, "failed": 0, "skipped": 0}

    for domain_row in domains:
        result = send_to_domain(
            conn,
            domain_row=domain_row,
            template=template,
            channel=campaign["channel"],
            mobile_only=mobile_only,
            campaign_id=campaign_id,
            settings=settings,
        )
        status = result.get("status")
        if status == "sent":
            counts["sent"] += 1
        elif status == "skipped":
            counts["skipped"] += 1
        else:
            counts["failed"] += 1

    update_campaign_counts(
        conn,
        campaign_id,
        status="completed",
        sent=counts["sent"],
        failed=counts["failed"],
        skipped=counts["skipped"],
        finished=True,
    )
    conn.commit()
    return counts


def process_new_domains(conn, domain_ids: list[int]) -> None:
    """Run automation rules for newly inserted domains."""
    if not domain_ids:
        return

    rules = get_active_rules_for_trigger(conn, "new_domain")
    if not rules:
        return

    domains = get_domains_by_ids(conn, domain_ids)
    settings = get_all_settings(conn)

    for domain_row in domains:
        for rule in rules:
            template = {
                "id": rule["template_id"],
                "channel": rule["channel"],
                "kavenegar_template": rule.get("kavenegar_template"),
                "token_mapping": rule.get("token_mapping"),
                "email_subject": rule.get("email_subject"),
                "email_body": rule.get("email_body"),
            }
            try:
                send_to_domain(
                    conn,
                    domain_row=domain_row,
                    template=template,
                    channel=rule["channel"],
                    mobile_only=bool(rule.get("mobile_only", 1)),
                    automation_rule_id=rule["id"],
                    settings=settings,
                )
            except Exception as exc:  # noqa: BLE001
                log.exception("automation failed domain=%s rule=%s", domain_row.get("id"), rule.get("id"))
                insert_message_log(
                    conn,
                    {
                        "automation_rule_id": rule["id"],
                        "domain_id": domain_row.get("id"),
                        "channel": rule["channel"],
                        "status": "failed",
                        "error_message": str(exc),
                        "template_id": rule["template_id"],
                    },
                )

    conn.commit()
