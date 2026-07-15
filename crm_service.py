"""CRM message sending orchestration."""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any

from contact_utils import build_template_context, normalize_email, render_text_template
from crm_db import (
    build_kavenegar_tokens,
    get_active_rules_for_trigger,
    automation_already_handled,
    get_all_settings,
    get_campaign,
    get_domains_by_ids,
    get_template,
    insert_message_log,
    is_dry_run,
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
    force_send: bool = False,
    override_recipient: str | None = None,
) -> dict[str, Any]:
    settings = settings or get_all_settings(conn)
    context = build_template_context(domain_row)
    phone_type = context.get("phone_type")
    mobile = (override_recipient or "").strip() or context.get("mobile_phone")

    base_log = {
        "campaign_id": campaign_id,
        "automation_rule_id": automation_rule_id,
        "domain_id": domain_row.get("id"),
        "channel": "sms",
        "template_id": template.get("id"),
    }
    test_tag = "[ارسال تستی قالب] " if force_send else ""

    if not override_recipient and mobile_only and phone_type == "landline":
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

    if is_dry_run(settings) and not force_send:
        token_preview = "، ".join(
            f"{k}={v}" for k, v in tokens.items() if v
        )
        insert_message_log(
            conn,
            {
                **base_log,
                "recipient": mobile,
                "recipient_type": "mobile",
                "status": "test",
                "error_message": (
                    f"[آزمایشی] الگو: {template['kavenegar_template']}"
                    + (f" | {token_preview}" if token_preview else "")
                ),
                "sent_at": _now(),
            },
        )
        return {"status": "test"}

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
                "error_message": test_tag.strip() or None,
                "sent_at": _now(),
            },
        )
        return {"status": "sent", "messageid": result.get("messageid"), "recipient": mobile}
    except KavenegarError as exc:
        insert_message_log(
            conn,
            {
                **base_log,
                "recipient": mobile,
                "recipient_type": "mobile",
                "status": "failed",
                "error_message": f"{test_tag}[{exc.status}] {exc.message}",
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
    force_send: bool = False,
    override_recipient: str | None = None,
) -> dict[str, Any]:
    settings = settings or get_all_settings(conn)
    context = build_template_context(domain_row)
    email = (
        (override_recipient or "").strip()
        or context.get("email_normalized")
        or normalize_email(domain_row.get("email"))
    )

    base_log = {
        "campaign_id": campaign_id,
        "automation_rule_id": automation_rule_id,
        "domain_id": domain_row.get("id"),
        "channel": "email",
        "template_id": template.get("id"),
    }
    test_tag = "[ارسال تستی قالب] " if force_send else ""

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

    if is_dry_run(settings) and not force_send:
        preview = body.strip().replace("\n", " ")
        if len(preview) > 160:
            preview = preview[:160] + "…"
        insert_message_log(
            conn,
            {
                **base_log,
                "recipient": email,
                "recipient_type": "email",
                "status": "test",
                "error_message": f"[آزمایشی] موضوع: {subject} | {preview}",
                "sent_at": _now(),
            },
        )
        return {"status": "test"}

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
                "error_message": test_tag.strip() or None,
                "sent_at": _now(),
            },
        )
        return {"status": "sent", "recipient": email}
    except EmailSendError as exc:
        insert_message_log(
            conn,
            {
                **base_log,
                "recipient": email,
                "recipient_type": "email",
                "status": "failed",
                "error_message": f"{test_tag}{exc}",
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
    force_send: bool = False,
    override_recipient: str | None = None,
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
            force_send=force_send,
            override_recipient=override_recipient,
        )
    if channel == "email":
        return send_email_to_domain(
            conn,
            domain_row=domain_row,
            template=template,
            settings=settings,
            campaign_id=campaign_id,
            automation_rule_id=automation_rule_id,
            force_send=force_send,
            override_recipient=override_recipient,
        )
    return {"status": "failed", "error": "کانال نامعتبر"}


def run_campaign(conn, campaign_id: int) -> dict[str, int]:
    """Send a campaign sequentially (queue-style), committing progress after
    every message so the detail page can show live sent/failed/pending counts.
    """
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
    conn.commit()
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
        # In test mode messages get a "test" status; count them as processed
        # (sent bucket) so the campaign completes normally.
        if status in ("sent", "test"):
            counts["sent"] += 1
            update_campaign_counts(conn, campaign_id, sent=1)
        elif status == "skipped":
            counts["skipped"] += 1
            update_campaign_counts(conn, campaign_id, skipped=1)
        else:
            counts["failed"] += 1
            update_campaign_counts(conn, campaign_id, failed=1)
        # Commit per message: sending is network-bound anyway, and this makes
        # the log + counters visible to the detail page while running.
        conn.commit()

    update_campaign_counts(conn, campaign_id, status="completed", finished=True)
    conn.commit()
    return counts


# --- Background campaign queue -------------------------------------------
# One worker thread per campaign; a module-level registry prevents the same
# campaign from being queued twice (e.g. double-click on "start").
_running_campaigns: set[int] = set()
_running_lock = threading.Lock()


def is_campaign_running(campaign_id: int) -> bool:
    with _running_lock:
        return campaign_id in _running_campaigns


def start_campaign_async(mysql_config, campaign_id: int) -> bool:
    """Queue a campaign to send in a background thread.

    Returns False if this campaign is already being processed.
    """
    from db import mysql_connection

    with _running_lock:
        if campaign_id in _running_campaigns:
            return False
        _running_campaigns.add(campaign_id)

    def worker():
        try:
            with mysql_connection(mysql_config) as conn:
                run_campaign(conn, campaign_id)
        except Exception:
            log.exception("campaign %s failed in background worker", campaign_id)
            try:
                with mysql_connection(mysql_config) as conn:
                    update_campaign_counts(
                        conn, campaign_id, status="failed", finished=True
                    )
                    conn.commit()
            except Exception:
                log.exception("could not mark campaign %s as failed", campaign_id)
        finally:
            with _running_lock:
                _running_campaigns.discard(campaign_id)

    thread = threading.Thread(
        target=worker, name=f"campaign-{campaign_id}", daemon=True
    )
    thread.start()
    return True


def process_new_domains(conn, domain_ids: list[int]) -> None:
    """Run automation rules only for domains created *after* each rule existed.

    Timing:
      - Called from save_domains when a brand-new enamad_id+code is stored
      - Called from refresh_domain_trustseal after contact details arrive

    Old catalogue domains (created before the rule) are never messaged, even
    when a later trustseal refresh fills their phone/email.
    """
    if not domain_ids:
        return

    rules = get_active_rules_for_trigger(conn, "new_domain")
    if not rules:
        return

    domains = get_domains_by_ids(conn, domain_ids)
    settings = get_all_settings(conn)

    for domain_row in domains:
        domain_created = domain_row.get("created_at")
        context = build_template_context(domain_row)
        for rule in rules:
            rule_id = int(rule["id"])
            rule_created = rule.get("created_at")
            # Only domains that appeared after this rule was created.
            if domain_created and rule_created and domain_created < rule_created:
                continue

            if automation_already_handled(conn, int(domain_row["id"]), rule_id=rule_id):
                continue

            channel = rule["channel"]
            # Wait for contact info — do not write a skip log yet.
            if channel == "sms" and not context.get("mobile_phone"):
                phone_type = context.get("phone_type")
                if phone_type in ("", "none", "unknown", None) and not domain_row.get("phone"):
                    continue
            if channel == "email" and not (
                context.get("email_normalized") or domain_row.get("email")
            ):
                continue

            template = {
                "id": rule["template_id"],
                "channel": channel,
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
                    channel=channel,
                    mobile_only=bool(rule.get("mobile_only", 1)),
                    automation_rule_id=rule_id,
                    settings=settings,
                )
            except Exception as exc:  # noqa: BLE001
                log.exception(
                    "automation failed domain=%s rule=%s",
                    domain_row.get("id"),
                    rule_id,
                )
                insert_message_log(
                    conn,
                    {
                        "automation_rule_id": rule_id,
                        "domain_id": domain_row.get("id"),
                        "channel": channel,
                        "status": "failed",
                        "error_message": str(exc),
                        "template_id": rule["template_id"],
                    },
                )

    conn.commit()
