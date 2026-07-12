from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

PAGE_SIZE = 10


@dataclass(frozen=True)
class TextFormat:
    """Message text formatting.

    - "html": Telegram HTML tags (<b>, <i>, <code>, <a>).
    - "plain": Bale. Bale always interprets messages as Markdown and its bold
      syntax needs padding spaces (which look ugly), while backslash-escaping
      shows the backslashes literally. So we emit clean plain text with no
      emphasis markers and no escaping. Inline `*`/`_` inside words are safe on
      Bale because it only treats them as markup when surrounded by spaces.
      Links still render, so we keep the `[label](url)` form.
    """

    name: str

    def esc(self, value: Any) -> str:
        if value is None:
            return ""
        text = str(value)
        if self.name == "html":
            return html.escape(text)
        return text

    def bold(self, value: Any) -> str:
        text = self.esc(value)
        if self.name == "html":
            return f"<b>{text}</b>"
        return text

    def code(self, value: Any) -> str:
        text = self.esc(value)
        if self.name == "html":
            return f"<code>{text}</code>"
        return text

    def italic(self, value: Any) -> str:
        text = self.esc(value)
        if self.name == "html":
            return f"<i>{text}</i>"
        return text

    def link(self, label: Any, url: str) -> str:
        text = self.esc(label)
        if self.name == "html":
            safe_url = html.escape(url, quote=True)
            return f'<a href="{safe_url}">{text}</a>'
        return f"[{text}]({url})"


HTML_FMT = TextFormat("html")
MD_FMT = TextFormat("plain")

_fmt: TextFormat = HTML_FMT


def set_text_format(fmt: TextFormat) -> None:
    global _fmt
    _fmt = fmt


def get_text_format() -> TextFormat:
    return _fmt


def esc(value: Any) -> str:
    return _fmt.esc(value)


def stars(rating: int) -> str:
    rating = max(0, min(5, int(rating or 0)))
    return "⭐" * rating + "☆" * (5 - rating)


def fmt_date(value: Any) -> str:
    if not value:
        return "—"
    if isinstance(value, datetime):
        return value.strftime("%Y/%m/%d %H:%M")
    return esc(value)


def main_menu_text() -> str:
    f = _fmt
    return (
        f"🛡 {f.bold('ربات اینماد')}\n\n"
        "جستجو و مرور دامنه‌های دارای نماد اعتماد الکترونیکی\n\n"
        "یک گزینه از منو انتخاب کنید 👇"
    )


def main_menu_keyboard(*, is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("🔍 جستجوی دامنه", callback_data="m:search"),
            InlineKeyboardButton("🆕 تازه‌ترین‌ها", callback_data="m:latest:0"),
        ],
        [
            InlineKeyboardButton("⭐ امتیاز بالا", callback_data="m:top:0"),
            InlineKeyboardButton("🗺 بر اساس استان", callback_data="m:provinces"),
        ],
        [
            InlineKeyboardButton("❓ راهنما", callback_data="m:help"),
        ],
    ]
    if is_admin:
        rows.append(
            [
                InlineKeyboardButton("📊 آمار دیتابیس", callback_data="m:stats"),
                InlineKeyboardButton("🛠 پنل مدیریت", callback_data="m:admin"),
            ]
        )
    return InlineKeyboardMarkup(rows)


def admin_panel_text(user_stats: dict) -> str:
    f = _fmt
    return (
        f"🛠 {f.bold('پنل مدیریت')}\n\n"
        f"👥 کل کاربران: {f.bold(f'{user_stats.get('total', 0):,}')}\n"
        f"🟢 فعال ۲۴ ساعت اخیر: {f.bold(f'{user_stats.get('active_1d', 0):,}')}\n"
        f"📅 فعال ۷ روز اخیر: {f.bold(f'{user_stats.get('active_7d', 0):,}')}\n"
        f"💬 مجموع تعاملات: {f.bold(f'{user_stats.get('interactions', 0):,}')}\n\n"
        "یک گزینه را انتخاب کنید 👇"
    )


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("👥 لیست کاربران", callback_data="m:users:0")],
            [InlineKeyboardButton("🏠 منوی اصلی", callback_data="m:home")],
        ]
    )


def _user_display_name(row: dict) -> str:
    parts = [row.get("first_name") or "", row.get("last_name") or ""]
    name = " ".join(p for p in parts if p).strip()
    return name or "—"


def _platform_label(platform: str | None) -> str:
    if platform == "bale":
        return "بله"
    if platform == "telegram":
        return "تلگرام"
    return platform or "—"


def users_list_text(rows: list[dict], page: int, total: int) -> str:
    f = _fmt
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    header = (
        f"👥 {f.bold('کاربران ربات')}\n"
        f"صفحه {page + 1} از {pages}  •  {total:,} کاربر\n"
    )
    if not rows:
        return header + f"\n{f.italic('هنوز کاربری ثبت نشده.')}"

    parts = [header, ""]
    for index, row in enumerate(rows, start=1):
        num = page * PAGE_SIZE + index
        name = esc(_user_display_name(row))
        username = row.get("username")
        uname = f" (@{esc(username)})" if username else ""
        platform = row.get("platform")
        platform_tag = f" · {esc(_platform_label(platform))}" if platform else ""
        line = (
            f"{f.bold(f'{num}.')} {name}{uname}\n"
            f"🆔 {f.code(row.get('user_id'))}{platform_tag} · "
            f"💬 {row.get('interaction_count', 0):,} · "
            f"🕐 {fmt_date(row.get('last_seen'))}"
        )
        parts.append(line)
        parts.append("")
    return "\n".join(parts).strip()


def users_list_keyboard(page: int, total: int) -> InlineKeyboardMarkup:
    nav: list[InlineKeyboardButton] = []
    max_page = max(0, (total - 1) // PAGE_SIZE)
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ قبلی", callback_data=f"m:users:{page - 1}"))
    if page < max_page:
        nav.append(InlineKeyboardButton("بعدی ▶️", callback_data=f"m:users:{page + 1}"))
    buttons: list[list[InlineKeyboardButton]] = []
    if nav:
        buttons.append(nav)
    buttons.append(
        [
            InlineKeyboardButton("🛠 پنل مدیریت", callback_data="m:admin"),
            InlineKeyboardButton("🏠 منوی اصلی", callback_data="m:home"),
        ]
    )
    return InlineKeyboardMarkup(buttons)


def back_home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🏠 منوی اصلی", callback_data="m:home")]]
    )


def search_prompt_text() -> str:
    f = _fmt
    return (
        f"🔍 {f.bold('جستجوی دامنه')}\n\n"
        "نام دامنه یا عنوان کسب‌وکار را بنویسید:\n"
        f"{f.italic('مثال: digikala.com یا دیجی‌کالا')}\n\n"
        "برای لغو، /start را بزنید."
    )


def help_text() -> str:
    f = _fmt
    return (
        f"❓ {f.bold('راهنما')}\n\n"
        f"🔍 {f.bold('جستجو')} — دامنه، نام فارسی یا صاحب امتیاز\n"
        f"🆕 {f.bold('تازه‌ترین‌ها')} — بر اساس ترتیب سایت اینماد\n"
        f"⭐ {f.bold('امتیاز بالا')} — دامنه‌های ۴ و ۵ ستاره\n"
        f"🗺 {f.bold('استان')} — فیلتر بر اساس استان\n\n"
        "💡 اگر دامنه در دیتابیس نبود، جستجوی زنده از enamad.ir هم انجام می‌شود."
    )


def stats_text(stats: dict) -> str:
    f = _fmt
    scrape = stats.get("scrape") or {}
    last_run = stats.get("last_run")
    last_major = stats.get("last_major_run")
    lines = [
        f"📊 {f.bold('آمار دیتابیس')}",
        f"📦 کل دامنه‌ها: {f.bold(f'{stats.get('total', 0):,}')}",
        f"⭐ دارای امتیاز: {f.bold(f'{stats.get('rated', 0):,}')}",
    ]

    total_pages = scrape.get("total_pages")
    effective_last = int(scrape.get("effective_last_page") or 0)
    distinct_pages = int(scrape.get("distinct_pages_in_db") or 0)

    if total_pages:
        total_pages_int = int(total_pages)
        pct = effective_last * 100 // max(1, total_pages_int)
        lines.append(
            f"📄 پیشرفت اسکرپ: {f.bold(f'{effective_last:,}')} / {total_pages_int:,} ({pct}%)"
        )
        if distinct_pages and distinct_pages != effective_last:
            cover = distinct_pages * 100 // max(1, total_pages_int)
            lines.append(
                f"📑 صفحات پوشش‌داده‌شده در DB: {f.bold(f'{distinct_pages:,}')} ({cover}%)"
            )
    elif effective_last:
        lines.append(f"📄 آخرین صفحه اسکرپ‌شده: {f.bold(f'{effective_last:,}')}")

    if stats.get("last_update"):
        lines.append(f"🕐 آخرین به‌روزرسانی: {fmt_date(stats['last_update'])}")

    display_run = last_major or last_run
    if display_run:
        lines.extend(
            [
                "",
                f.bold("آخرین اجرای اسکرپر"),
                f"• وضعیت: {esc(display_run.get('status', '—'))}",
                f"• صفحات: {display_run.get('pages_fetched', 0):,}",
                f"• رکوردها: {display_run.get('records_saved', 0):,}",
            ]
        )
        if display_run.get("finished_at"):
            lines.append(f"• پایان: {fmt_date(display_run['finished_at'])}")

    if (
        last_run
        and last_major
        and last_run is not last_major
        and int(last_run.get("pages_fetched") or 0) < 10
    ):
        lines.extend(
            [
                "",
                f.italic(
                    f"آخرین اجرای کوچک: {last_run.get('pages_fetched', 0):,} صفحه، "
                    f"{last_run.get('records_saved', 0):,} رکورد "
                    f"({fmt_date(last_run.get('finished_at'))})"
                ),
            ]
        )

    return "\n".join(lines)


def domain_card(row: dict, *, compact: bool = False) -> str:
    f = _fmt
    domain = esc(row.get("domain") or "—")
    name = esc(row.get("business_name") or "—")
    rating = int(row.get("rating") or 0)
    province = esc(row.get("province") or "—")
    city = esc(row.get("city") or "—")

    if compact:
        return (
            f"🌐 {f.code(domain)}\n"
            f"🏪 {name}\n"
            f"{stars(rating)}  📍 {province} / {city}"
        )

    lines = [
        f"🌐 {f.bold(domain)}",
        f"🏪 {name}",
        f"{stars(rating)} ({rating}/5)",
        f"📍 {province} — {city}",
    ]
    if row.get("owner_name"):
        lines.append(f"👤 {esc(row['owner_name'])}")
    if row.get("business_address"):
        lines.append(f"📫 {esc(row['business_address'])}")
    if row.get("phone"):
        lines.append(f"📞 {f.code(row['phone'])}")
    if row.get("email"):
        lines.append(f"✉️ {esc(row['email'])}")
    if row.get("work_hours"):
        lines.append(f"🕒 {esc(row['work_hours'])}")
    if row.get("approve_date"):
        lines.append(f"📅 صدور: {esc(row['approve_date'])}")
    if row.get("expire_date"):
        lines.append(f"⏳ انقضا: {esc(row['expire_date'])}")
    if row.get("trustseal_url"):
        lines.append(f"🔗 {f.link('نماد اعتماد', row['trustseal_url'])}")
    if row.get("updated_at"):
        lines.append(f"🕐 به‌روز: {fmt_date(row.get('updated_at'))}")
    return "\n".join(lines)


def domain_detail_text(row: dict, services: list[dict], *, header: str | None = None) -> str:
    f = _fmt
    parts: list[str] = []
    if header:
        parts.append(header)
    parts.append(domain_card(row, compact=False))
    if services:
        parts.append("")
        parts.append(f.bold("📋 خدمات / مجوزها"))
        for index, service in enumerate(services[:12], start=1):
            title = esc(service.get("service_title") or "—")
            status = esc(service.get("status") or "")
            issuer = esc(service.get("license_issuer") or "")
            line = f"{index}. {title}"
            if status:
                line += f" {f.italic(f'[{status}]')}"
            if issuer:
                line += f"\n   ↳ {issuer}"
            parts.append(line)
        if len(services) > 12:
            parts.append(f.italic(f"+ {len(services) - 12} مورد دیگر"))
    return "\n".join(parts)


def search_detail_header(query: str, *, total: int, showing_best: bool = False) -> str:
    f = _fmt
    title = f"🔍 {f.bold('جستجو:')} {f.code(query)}"
    if showing_best and total > 1:
        return f"{title}\n{f.italic(f'بهترین نتیجه از {total:,} مورد:')}"
    return title


def search_other_results_text(rows: list[dict], total: int) -> str:
    f = _fmt
    if not rows:
        return ""
    lines = [
        "",
        f"{f.bold('نتایج مشابه دیگر')} ({total - 1:,} مورد)",
        "",
    ]
    for index, row in enumerate(rows, start=2):
        lines.append(f.bold(f"{index}."))
        lines.append(domain_card(row, compact=True))
        lines.append("")
    if total > len(rows) + 1:
        lines.append(f.italic(f"+ {total - len(rows) - 1:,} نتیجه دیگر…"))
    return "\n".join(lines).strip()


def list_header(title: str, page: int, total: int) -> str:
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    return (
        f"{title}\n"
        f"صفحه {page + 1} از {pages}  •  {total:,} مورد\n"
    )


def domain_list_line(num: int, row: dict) -> str:
    """Compact list entry: number + name + domain + approve date."""
    f = _fmt
    domain = esc(row.get("domain") or "—")
    name = esc(row.get("business_name") or "—")
    approve = row.get("approve_date")
    second = f"🌐 {f.code(domain)}"
    if approve:
        second += f" · 📅 {esc(approve)}"
    return f"{f.bold(f'{num}.')} {name}\n{second}"


def domain_list_text(title: str, rows: list[dict], page: int, total: int) -> str:
    f = _fmt
    if not rows:
        return f"{title}\n\n{f.italic('موردی یافت نشد.')}"

    parts = [list_header(title, page, total), ""]
    for index, row in enumerate(rows, start=1):
        num = page * PAGE_SIZE + index
        parts.append(domain_list_line(num, row))
        parts.append("")
    return "\n".join(parts).strip()


def search_results_text(query: str, rows: list[dict], total: int) -> str:
    f = _fmt
    title = f"🔍 نتایج برای {f.code(query)}"
    if not rows:
        return f"{title}\n\n{f.italic('در دیتابیس چیزی پیدا نشد.')}"
    text = list_header(title, 0, total)
    for index, row in enumerate(rows, start=1):
        text += f"\n{f.bold(f'{index}.')}\n{domain_card(row, compact=True)}\n"
    if total > len(rows):
        text += f"\n{f.italic(f'نمایش {len(rows)} از {total:,} نتیجه')}"
    return text


def provinces_text(provinces: list[dict]) -> str:
    f = _fmt
    lines = [
        f"🗺 {f.bold('انتخاب استان')}",
        "یک استان را انتخاب کنید:",
    ]
    return "\n".join(lines)


def provinces_keyboard(provinces: list[dict]) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for index, item in enumerate(provinces):
        name = item["province"]
        count = item["cnt"]
        label = f"{name} ({count:,})"
        if len(label) > 28:
            label = f"{name[:22]}… ({count:,})"
        row.append(InlineKeyboardButton(label, callback_data=f"m:prov:{index}:0"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("🏠 منوی اصلی", callback_data="m:home")])
    return InlineKeyboardMarkup(buttons)


def domain_list_keyboard(
    rows: list[dict],
    *,
    nav_prefix: str,
    page: int,
    total: int,
) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    detail_row: list[InlineKeyboardButton] = []
    for row in rows:
        domain = row.get("domain") or "?"
        label = domain if len(domain) <= 18 else domain[:16] + "…"
        detail_row.append(
            InlineKeyboardButton(f"📋 {label}", callback_data=f"m:d:{row['id']}")
        )
        if len(detail_row) == 2:
            buttons.append(detail_row)
            detail_row = []
    if detail_row:
        buttons.append(detail_row)

    nav: list[InlineKeyboardButton] = []
    max_page = max(0, (total - 1) // PAGE_SIZE)
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ قبلی", callback_data=f"{nav_prefix}:{page - 1}"))
    if page < max_page:
        nav.append(InlineKeyboardButton("بعدی ▶️", callback_data=f"{nav_prefix}:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("🏠 منوی اصلی", callback_data="m:home")])
    return InlineKeyboardMarkup(buttons)


def domain_detail_keyboard(domain_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔄 بروزرسانی از اینماد", callback_data=f"m:dr:{domain_id}")],
            [InlineKeyboardButton("🏠 منوی اصلی", callback_data="m:home")],
        ]
    )


def search_results_keyboard(rows: list[dict]) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    for row in rows[:6]:
        domain = row.get("domain") or "?"
        label = domain if len(domain) <= 24 else domain[:22] + "…"
        buttons.append(
            [InlineKeyboardButton(f"📋 {label}", callback_data=f"m:d:{row['id']}")]
        )
    buttons.append([InlineKeyboardButton("🏠 منوی اصلی", callback_data="m:home")])
    return InlineKeyboardMarkup(buttons)
