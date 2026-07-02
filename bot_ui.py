from __future__ import annotations

import html
from datetime import datetime
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

PAGE_SIZE = 10


def esc(value: Any) -> str:
    if value is None:
        return ""
    return html.escape(str(value))


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
    return (
        "🛡 <b>ربات اینماد</b>\n\n"
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
    return (
        "🛠 <b>پنل مدیریت</b>\n\n"
        f"👥 کل کاربران: <b>{user_stats.get('total', 0):,}</b>\n"
        f"🟢 فعال ۲۴ ساعت اخیر: <b>{user_stats.get('active_1d', 0):,}</b>\n"
        f"📅 فعال ۷ روز اخیر: <b>{user_stats.get('active_7d', 0):,}</b>\n"
        f"💬 مجموع تعاملات: <b>{user_stats.get('interactions', 0):,}</b>\n\n"
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


def users_list_text(rows: list[dict], page: int, total: int) -> str:
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    header = (
        "👥 <b>کاربران ربات</b>\n"
        f"صفحه {page + 1} از {pages}  •  {total:,} کاربر\n"
    )
    if not rows:
        return header + "\n<i>هنوز کاربری ثبت نشده.</i>"

    parts = [header, ""]
    for index, row in enumerate(rows, start=1):
        num = page * PAGE_SIZE + index
        name = esc(_user_display_name(row))
        username = row.get("username")
        uname = f" (@{esc(username)})" if username else ""
        line = (
            f"<b>{num}.</b> {name}{uname}\n"
            f"🆔 <code>{row.get('user_id')}</code> · "
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
    return (
        "🔍 <b>جستجوی دامنه</b>\n\n"
        "نام دامنه یا عنوان کسب‌وکار را بنویسید:\n"
        "<i>مثال: digikala.com یا دیجی‌کالا</i>\n\n"
        "برای لغو، /start را بزنید."
    )


def help_text() -> str:
    return (
        "❓ <b>راهنما</b>\n\n"
        "🔍 <b>جستجو</b> — دامنه، نام فارسی یا صاحب امتیاز\n"
        "🆕 <b>تازه‌ترین‌ها</b> — بر اساس ترتیب سایت اینماد\n"
        "⭐ <b>امتیاز بالا</b> — دامنه‌های ۴ و ۵ ستاره\n"
        "🗺 <b>استان</b> — فیلتر بر اساس استان\n\n"
        "💡 اگر دامنه در دیتابیس نبود، جستجوی زنده از enamad.ir هم انجام می‌شود."
    )


def stats_text(stats: dict) -> str:
    scrape = stats.get("scrape") or {}
    last_run = stats.get("last_run")
    last_major = stats.get("last_major_run")
    lines = [
        "📊 <b>آمار دیتابیس</b>",
        f"📦 کل دامنه‌ها: <b>{stats.get('total', 0):,}</b>",
        f"⭐ دارای امتیاز: <b>{stats.get('rated', 0):,}</b>",
    ]

    total_pages = scrape.get("total_pages")
    effective_last = int(scrape.get("effective_last_page") or 0)
    distinct_pages = int(scrape.get("distinct_pages_in_db") or 0)

    if total_pages:
        total_pages_int = int(total_pages)
        pct = effective_last * 100 // max(1, total_pages_int)
        lines.append(
            f"📄 پیشرفت اسکرپ: <b>{effective_last:,}</b> / {total_pages_int:,} ({pct}%)"
        )
        if distinct_pages and distinct_pages != effective_last:
            cover = distinct_pages * 100 // max(1, total_pages_int)
            lines.append(
                f"📑 صفحات پوشش‌داده‌شده در DB: <b>{distinct_pages:,}</b> ({cover}%)"
            )
    elif effective_last:
        lines.append(f"📄 آخرین صفحه اسکرپ‌شده: <b>{effective_last:,}</b>")

    if stats.get("last_update"):
        lines.append(f"🕐 آخرین به‌روزرسانی: {fmt_date(stats['last_update'])}")

    display_run = last_major or last_run
    if display_run:
        lines.extend(
            [
                "",
                "<b>آخرین اجرای اسکرپر</b>",
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
                f"<i>آخرین اجرای کوچک: {last_run.get('pages_fetched', 0):,} صفحه، "
                f"{last_run.get('records_saved', 0):,} رکورد "
                f"({fmt_date(last_run.get('finished_at'))})</i>",
            ]
        )

    return "\n".join(lines)


def domain_card(row: dict, *, compact: bool = False) -> str:
    domain = esc(row.get("domain") or "—")
    name = esc(row.get("business_name") or "—")
    rating = int(row.get("rating") or 0)
    province = esc(row.get("province") or "—")
    city = esc(row.get("city") or "—")

    if compact:
        return (
            f"🌐 <code>{domain}</code>\n"
            f"🏪 {name}\n"
            f"{stars(rating)}  📍 {province} / {city}"
        )

    lines = [
        f"🌐 <b>{domain}</b>",
        f"🏪 {name}",
        f"{stars(rating)} ({rating}/5)",
        f"📍 {province} — {city}",
    ]
    if row.get("owner_name"):
        lines.append(f"👤 {esc(row['owner_name'])}")
    if row.get("business_address"):
        lines.append(f"📫 {esc(row['business_address'])}")
    if row.get("phone"):
        lines.append(f"📞 <code>{esc(row['phone'])}</code>")
    if row.get("email"):
        lines.append(f"✉️ {esc(row['email'])}")
    if row.get("work_hours"):
        lines.append(f"🕒 {esc(row['work_hours'])}")
    if row.get("approve_date"):
        lines.append(f"📅 صدور: {esc(row['approve_date'])}")
    if row.get("expire_date"):
        lines.append(f"⏳ انقضا: {esc(row['expire_date'])}")
    if row.get("trustseal_url"):
        lines.append(f'🔗 <a href="{esc(row["trustseal_url"])}">نماد اعتماد</a>')
    if row.get("updated_at"):
        lines.append(f"🕐 به‌روز: {fmt_date(row.get('updated_at'))}")
    return "\n".join(lines)


def domain_detail_text(row: dict, services: list[dict], *, header: str | None = None) -> str:
    parts: list[str] = []
    if header:
        parts.append(header)
    parts.append(domain_card(row, compact=False))
    if services:
        parts.append("")
        parts.append("<b>📋 خدمات / مجوزها</b>")
        for index, service in enumerate(services[:12], start=1):
            title = esc(service.get("service_title") or "—")
            status = esc(service.get("status") or "")
            issuer = esc(service.get("license_issuer") or "")
            line = f"{index}. {title}"
            if status:
                line += f" <i>[{status}]</i>"
            if issuer:
                line += f"\n   ↳ {issuer}"
            parts.append(line)
        if len(services) > 12:
            parts.append(f"<i>+ {len(services) - 12} مورد دیگر</i>")
    return "\n".join(parts)


def search_detail_header(query: str, *, total: int, showing_best: bool = False) -> str:
    title = f"🔍 <b>جستجو:</b> <code>{esc(query)}</code>"
    if showing_best and total > 1:
        return f"{title}\n<i>بهترین نتیجه از {total:,} مورد:</i>"
    return title


def search_other_results_text(rows: list[dict], total: int) -> str:
    if not rows:
        return ""
    lines = [
        "",
        f"<b>نتایج مشابه دیگر</b> ({total - 1:,} مورد)",
        "",
    ]
    for index, row in enumerate(rows, start=2):
        lines.append(f"<b>{index}.</b>")
        lines.append(domain_card(row, compact=True))
        lines.append("")
    if total > len(rows) + 1:
        lines.append(f"<i>+ {total - len(rows) - 1:,} نتیجه دیگر…</i>")
    return "\n".join(lines).strip()


def list_header(title: str, page: int, total: int) -> str:
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    return (
        f"{title}\n"
        f"صفحه {page + 1} از {pages}  •  {total:,} مورد\n"
    )


def domain_list_line(num: int, row: dict) -> str:
    """Compact list entry: number + name + domain + approve date."""
    domain = esc(row.get("domain") or "—")
    name = esc(row.get("business_name") or "—")
    approve = row.get("approve_date")
    second = f"🌐 <code>{domain}</code>"
    if approve:
        second += f" · 📅 {esc(approve)}"
    return f"<b>{num}.</b> {name}\n{second}"


def domain_list_text(title: str, rows: list[dict], page: int, total: int) -> str:
    if not rows:
        return f"{title}\n\n<i>موردی یافت نشد.</i>"

    parts = [list_header(title, page, total), ""]
    for index, row in enumerate(rows, start=1):
        num = page * PAGE_SIZE + index
        parts.append(domain_list_line(num, row))
        parts.append("")
    return "\n".join(parts).strip()


def search_results_text(query: str, rows: list[dict], total: int) -> str:
    title = f"🔍 نتایج برای <code>{esc(query)}</code>"
    if not rows:
        return f"{title}\n\n<i>در دیتابیس چیزی پیدا نشد.</i>"
    text = list_header(title, 0, total)
    for index, row in enumerate(rows, start=1):
        text += f"\n<b>{index}.</b>\n{domain_card(row, compact=True)}\n"
    if total > len(rows):
        text += f"\n<i>نمایش {len(rows)} از {total:,} نتیجه</i>"
    return text


def provinces_text(provinces: list[dict]) -> str:
    lines = [
        "🗺 <b>انتخاب استان</b>",
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
