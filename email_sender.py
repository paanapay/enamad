"""SMTP email sender for CRM campaigns."""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from typing import Any

from email_layout import prepare_email_html


class EmailConfig:
    def __init__(
        self,
        *,
        host: str,
        port: int = 587,
        username: str = "",
        password: str = "",
        from_addr: str = "",
        use_tls: bool = True,
        verify_ssl: bool = True,
    ):
        self.host = (host or "").strip()
        self.port = int(port or 587)
        self.username = (username or "").strip()
        self.password = password or ""
        self.from_addr = (from_addr or self.username or "").strip()
        self.use_tls = use_tls
        self.verify_ssl = verify_ssl

    @classmethod
    def from_settings(cls, settings: dict[str, str]) -> "EmailConfig":
        return cls(
            host=settings.get("smtp_host", ""),
            port=int(settings.get("smtp_port") or 587),
            username=settings.get("smtp_username", ""),
            password=settings.get("smtp_password", ""),
            from_addr=settings.get("smtp_from", ""),
            use_tls=(settings.get("smtp_tls", "yes").lower() in ("1", "true", "yes", "on")),
            verify_ssl=(
                settings.get("smtp_ssl_verify", "yes").lower()
                in ("1", "true", "yes", "on")
            ),
        )

    def is_configured(self) -> bool:
        return bool(self.host and self.from_addr)


class EmailSendError(Exception):
    pass


def send_email(
    config: EmailConfig,
    *,
    to_addr: str,
    subject: str,
    body: str,
) -> dict[str, Any]:
    if not config.is_configured():
        raise EmailSendError("تنظیمات SMTP کامل نیست")

    html_body = prepare_email_html(body)
    # quoted-printable keeps UTF-8 safe across SMTP relays and folds long lines
    # (8bit mega-lines get split mid-word/tag by some MTAs → "س ریع", "< strong>").
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.from_addr
    msg["To"] = to_addr
    msg.set_content(
        html_body, subtype="html", charset="utf-8", cte="quoted-printable"
    )

    try:
        if config.use_tls:
            context = ssl.create_default_context()
            if not config.verify_ssl:
                # Self-signed certificate on the mail server.
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            with smtplib.SMTP(config.host, config.port, timeout=30) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                if config.username:
                    server.login(config.username, config.password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(config.host, config.port, timeout=30) as server:
                if config.username:
                    server.login(config.username, config.password)
                server.send_message(msg)
    except Exception as exc:  # noqa: BLE001
        raise EmailSendError(str(exc)) from exc

    return {"to": to_addr, "subject": subject}
