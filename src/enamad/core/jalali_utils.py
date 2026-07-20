"""Jalali (Shamsi) date formatting utilities."""
from __future__ import annotations

import re
from datetime import date, datetime

import jdatetime

_JALALI_RE = re.compile(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})")
_PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "0123456789" * 2)


def _to_ascii_digits(value: str) -> str:
    return (value or "").translate(_PERSIAN_DIGITS)


def is_jalali_date(value) -> bool:
    """Return True if value looks like a Jalali date string (year 1300-1499)."""
    if not value:
        return False
    s = _to_ascii_digits(str(value).strip())
    match = _JALALI_RE.match(s)
    if not match:
        return False
    year = int(match.group(1))
    return 1300 <= year < 1500


def _parse_gregorian(value) -> date | datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return value

    s = _to_ascii_digits(str(value).strip())
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            chunk = s[:19] if "H" in fmt else s[:10]
            parsed = datetime.strptime(chunk, fmt)
            return parsed if "H" in fmt else parsed.date()
        except ValueError:
            continue
    return None


def format_jdate(value, fmt: str = "%Y/%m/%d") -> str:
    """Format a date as Jalali. Pass-through and normalize if already Jalali."""
    if not value:
        return "—"
    if is_jalali_date(value):
        s = _to_ascii_digits(str(value).strip())
        match = _JALALI_RE.match(s)
        if match:
            y, mo, d = int(match.group(1)), int(match.group(2)), int(match.group(3))
            return jdatetime.date(y, mo, d).strftime(fmt)
        return str(value)

    parsed = _parse_gregorian(value)
    if parsed is None:
        return str(value)
    if isinstance(parsed, datetime):
        parsed = parsed.date()
    jd = jdatetime.date.fromgregorian(date=parsed)
    return jd.strftime(fmt)


def format_jdatetime(value, fmt: str = "%Y/%m/%d %H:%M") -> str:
    """Format a datetime as Jalali."""
    if not value:
        return "—"
    if is_jalali_date(value) and " " not in str(value) and "T" not in str(value):
        return format_jdate(value)

    parsed = _parse_gregorian(value)
    if parsed is None:
        return str(value)
    if isinstance(parsed, date) and not isinstance(parsed, datetime):
        parsed = datetime.combine(parsed, datetime.min.time())
    jd = jdatetime.datetime.fromgregorian(datetime=parsed)
    return jd.strftime(fmt)
