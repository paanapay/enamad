"""SMTP email sender for CRM campaigns."""
from __future__ import annotations

import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any


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
    ):
        self.host = (host or "").strip()
        self.port = int(port or 587)
        self.username = (username or "").strip()
        self.password = password or ""
        self.from_addr = (from_addr or self.username or "").strip()
        self.use_tls = use_tls

    @classmethod
    def from_settings(cls, settings: dict[str, str]) -> "EmailConfig":
        return cls(
            host=settings.get("smtp_host", ""),
            port=int(settings.get("smtp_port") or 587),
            username=settings.get("smtp_username", ""),
            password=settings.get("smtp_password", ""),
            from_addr=settings.get("smtp_from", ""),
            use_tls=(settings.get("smtp_tls", "yes").lower() in ("1", "true", "yes", "on")),
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

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = config.from_addr
    msg["To"] = to_addr
    msg.attach(MIMEText(body, "html" if "<" in body and ">" in body else "plain", "utf-8"))

    try:
        if config.use_tls:
            context = ssl.create_default_context()
            with smtplib.SMTP(config.host, config.port, timeout=30) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                if config.username:
                    server.login(config.username, config.password)
                server.sendmail(config.from_addr, [to_addr], msg.as_string())
        else:
            with smtplib.SMTP(config.host, config.port, timeout=30) as server:
                if config.username:
                    server.login(config.username, config.password)
                server.sendmail(config.from_addr, [to_addr], msg.as_string())
    except Exception as exc:  # noqa: BLE001
        raise EmailSendError(str(exc)) from exc

    return {"to": to_addr, "subject": subject}
