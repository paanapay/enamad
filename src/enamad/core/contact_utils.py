"""Parse and classify Enamad contact fields (phone, email)."""
from __future__ import annotations

import re
from typing import Literal

PhoneType = Literal["mobile", "landline", "mixed", "none", "unknown"]

_PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "0123456789" * 2)
_MOBILE_RE = re.compile(r"^09\d{9}$")
_LANDLINE_RE = re.compile(r"^0[1-9]\d{8,9}$")
_EMAIL_RE = re.compile(
    r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}",
    re.IGNORECASE,
)

# Variables available in SMS/email templates.
TEMPLATE_VARIABLES = {
    "domain": "دامنه",
    "business_name": "نام کسب‌وکار",
    "owner_name": "نام مالک",
    "province": "استان",
    "city": "شهر",
    "approve_date": "تاریخ صدور",
    "expire_date": "تاریخ انقضا",
    "phone": "تلفن (خام)",
    "email": "ایمیل (خام)",
    "mobile_phone": "موبایل (نرمال)",
    "email_normalized": "ایمیل (نرمال)",
}

KAVENEGAR_TOKENS = ("token", "token2", "token3", "token10", "token20")


def to_ascii_digits(value: str) -> str:
    return (value or "").translate(_PERSIAN_DIGITS)


def extract_phone_candidates(raw: str | None) -> list[str]:
    """Pull digit sequences that may represent Iranian phone numbers."""
    if not raw:
        return []
    text = to_ascii_digits(raw)
    parts = re.split(r"[/|,;،\n]+", text)
    candidates: list[str] = []
    for part in parts:
        digits = re.sub(r"[^\d+]", "", part.strip())
        if not digits:
            continue
        if digits.startswith("+98"):
            digits = "0" + digits[3:]
        elif digits.startswith("0098"):
            digits = "0" + digits[4:]
        elif digits.startswith("98") and len(digits) >= 12:
            digits = "0" + digits[2:]
        if len(digits) >= 10:
            candidates.append(digits)
    return candidates


def classify_phone(raw: str | None) -> tuple[PhoneType, str | None]:
    """Return phone type and normalized mobile (09xxxxxxxxx) if any."""
    candidates = extract_phone_candidates(raw)
    if not candidates:
        return "none", None

    mobiles: list[str] = []
    landlines: list[str] = []
    for digits in candidates:
        if _MOBILE_RE.match(digits):
            mobiles.append(digits)
        elif _LANDLINE_RE.match(digits):
            landlines.append(digits)

    if mobiles and landlines:
        return "mixed", mobiles[0]
    if mobiles:
        return "mobile", mobiles[0]
    if landlines:
        return "landline", None
    return "unknown", None


def normalize_email(raw: str | None) -> str | None:
    """Extract first valid email from Enamad free-text field."""
    if not raw:
        return None
    text = to_ascii_digits(raw).strip().lower()
    text = text.replace("[at]", "@").replace("(at)", "@")
    text = re.sub(r"\s+at\s+", "@", text, flags=re.IGNORECASE)
    text = text.replace(" ", "")
    match = _EMAIL_RE.search(text)
    if match:
        return match.group(0).lower()
    if "@" in text and "." in text.split("@", 1)[-1]:
        cleaned = re.sub(r"[^a-z0-9@._+\-]", "", text)
        if _EMAIL_RE.match(cleaned):
            return cleaned
    return None


def build_template_context(domain_row: dict) -> dict[str, str]:
    """Build variable map for template rendering."""
    phone_type, mobile = classify_phone(domain_row.get("phone"))
    email_norm = normalize_email(domain_row.get("email"))
    return {
        "domain": str(domain_row.get("domain") or ""),
        "business_name": str(domain_row.get("business_name") or ""),
        "owner_name": str(domain_row.get("owner_name") or ""),
        "province": str(domain_row.get("province") or ""),
        "city": str(domain_row.get("city") or ""),
        "approve_date": str(domain_row.get("approve_date") or ""),
        "expire_date": str(domain_row.get("expire_date") or ""),
        "phone": str(domain_row.get("phone") or ""),
        "email": str(domain_row.get("email") or ""),
        "mobile_phone": mobile or "",
        "email_normalized": email_norm or "",
        "phone_type": phone_type,
    }


def render_text_template(template: str, context: dict[str, str]) -> str:
    result = template or ""
    for key, value in context.items():
        result = result.replace("{{" + key + "}}", str(value or ""))
    return result
