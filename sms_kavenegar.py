"""Kavenegar SMS provider — Lookup (verify) API."""
from __future__ import annotations

from typing import Any

import requests


class KavenegarError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class KavenegarClient:
    BASE_URL = "https://api.kavenegar.com/v1"

    def __init__(self, api_key: str, *, timeout: float = 30.0):
        self.api_key = (api_key or "").strip()
        self.timeout = timeout

    def lookup(
        self,
        receptor: str,
        template: str,
        *,
        token: str,
        token2: str | None = None,
        token3: str | None = None,
        token10: str | None = None,
        token20: str | None = None,
        tag: str | None = None,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise KavenegarError(0, "API-KEY کاوه‌نگار تنظیم نشده است")

        url = f"{self.BASE_URL}/{self.api_key}/verify/lookup.json"
        payload: dict[str, str] = {
            "receptor": receptor,
            "template": template,
            "token": token,
        }
        for key, value in (
            ("token2", token2),
            ("token3", token3),
            ("token10", token10),
            ("token20", token20),
            ("tag", tag),
        ):
            if value:
                payload[key] = value

        resp = requests.post(url, data=payload, timeout=self.timeout)
        data = resp.json()
        ret = data.get("return") or {}
        status = int(ret.get("status") or 0)
        message = str(ret.get("message") or "")
        if status != 200:
            raise KavenegarError(status, message or "ارسال پیامک ناموفق بود")

        entries = data.get("entries") or []
        return entries[0] if entries else {}
