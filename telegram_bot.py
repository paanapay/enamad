#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram bot for browsing Enamad domain database.

Setup:
  1. Create a bot via @BotFather and copy the token
  2. Add [telegram] section to config.ini
  3. python telegram_bot.py

Usage:
  python telegram_bot.py
  python telegram_bot.py --config path/to/config.ini
"""

from __future__ import annotations

import argparse
import asyncio
import configparser
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from telegram import Update
from telegram.constants import ParseMode
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import bot_queries as queries
from bot_ui import (
    PAGE_SIZE,
    back_home_keyboard,
    domain_detail_keyboard,
    domain_detail_text,
    domain_list_keyboard,
    domain_list_text,
    help_text,
    main_menu_keyboard,
    main_menu_text,
    provinces_keyboard,
    provinces_text,
    search_prompt_text,
    search_results_keyboard,
    search_results_text,
    search_detail_header,
    search_other_results_text,
    stats_text,
)
from db import (
    commit_connection,
    load_config,
    mysql_connection,
    normalize_domain,
    refresh_domain_trustseal,
)

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR / "config.ini"

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("enamad-bot")


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    allowed_user_ids: frozenset[int]
    live_search: bool
    proxy_url: str | None
    api_base_url: str | None
    connect_timeout: float
    read_timeout: float


def _env(*keys: str) -> str | None:
    for key in keys:
        value = os.environ.get(key)
        if value is not None and value.strip() != "":
            return value.strip()
    return None


def load_telegram_config(path: Path) -> TelegramConfig:
    parser = configparser.ConfigParser()
    if path.is_file():
        parser.read(path, encoding="utf-8")

    env_token = _env("BOT_TOKEN", "TELEGRAM_BOT_TOKEN")
    if not parser.has_section("telegram") and env_token is None:
        raise ValueError(
            "بخش [telegram] در config.ini نیست و BOT_TOKEN هم ست نشده.\n"
            "نمونه:\n"
            "[telegram]\n"
            "bot_token = YOUR_TOKEN\n"
            "allowed_users = \n"
            "live_search = yes"
        )

    token = env_token or parser.get("telegram", "bot_token", fallback="").strip()
    if not token or token.upper() == "YOUR_TOKEN":
        raise ValueError("bot_token تنظیم نشده (config.ini یا BOT_TOKEN).")

    raw_users = _env("TELEGRAM_ALLOWED_USERS") or parser.get(
        "telegram", "allowed_users", fallback=""
    ).strip()
    allowed: set[int] = set()
    if raw_users:
        for part in raw_users.replace(";", ",").split(","):
            part = part.strip()
            if part.isdigit():
                allowed.add(int(part))

    live = (
        _env("TELEGRAM_LIVE_SEARCH")
        or parser.get("telegram", "live_search", fallback="yes")
    ).strip().lower()
    live_search = live in ("1", "true", "yes", "on")

    proxy = _env("TELEGRAM_PROXY") or parser.get("telegram", "proxy", fallback="").strip()
    proxy_url = proxy or None

    api_base = _env("TELEGRAM_API_BASE_URL") or parser.get(
        "telegram", "api_base_url", fallback=""
    ).strip()
    api_base_url = api_base or None

    connect_timeout = float(
        _env("TELEGRAM_CONNECT_TIMEOUT")
        or parser.getfloat("telegram", "connect_timeout", fallback=30.0)
    )
    read_timeout = float(
        _env("TELEGRAM_READ_TIMEOUT")
        or parser.getfloat("telegram", "read_timeout", fallback=30.0)
    )

    return TelegramConfig(
        bot_token=token,
        allowed_user_ids=frozenset(allowed),
        live_search=live_search,
        proxy_url=proxy_url,
        api_base_url=api_base_url,
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
    )


def is_allowed(update: Update, cfg: TelegramConfig) -> bool:
    if not cfg.allowed_user_ids:
        return True
    user = update.effective_user
    return bool(user and user.id in cfg.allowed_user_ids)


async def deny_access(update: Update) -> None:
    message = "⛔️ دسترسی به این ربات برای شما مجاز نیست."
    if update.callback_query:
        await update.callback_query.answer(message, show_alert=True)
    elif update.message:
        await update.message.reply_text(message)


def get_app_config(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data["app_config"]


def get_tg_config(context: ContextTypes.DEFAULT_TYPE) -> TelegramConfig:
    return context.application.bot_data["tg_config"]


def get_provinces_cache(context: ContextTypes.DEFAULT_TYPE) -> list[dict]:
    return context.application.bot_data.setdefault("provinces_cache", [])


async def send_main_menu(message, *, edit: bool = False) -> None:
    text = main_menu_text()
    markup = main_menu_keyboard()
    if edit:
        await message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
    else:
        await message.reply_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update, get_tg_config(context)):
        await deny_access(update)
        return
    context.user_data.pop("awaiting_search", None)
    if update.message:
        await send_main_menu(update.message)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    if not is_allowed(update, get_tg_config(context)):
        await deny_access(update)
        return

    await query.answer()
    data = query.data
    app_config = get_app_config(context)

    if data == "m:home":
        context.user_data.pop("awaiting_search", None)
        await send_main_menu(query.message, edit=True)
        return

    if data == "m:help":
        await query.message.edit_text(
            help_text(),
            reply_markup=back_home_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "m:search":
        context.user_data["awaiting_search"] = True
        await query.message.edit_text(
            search_prompt_text(),
            reply_markup=back_home_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "m:stats":
        with mysql_connection(app_config.mysql) as conn:
            stats = queries.get_stats(conn)
        await query.message.edit_text(
            stats_text(stats),
            reply_markup=back_home_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "m:provinces":
        with mysql_connection(app_config.mysql) as conn:
            provinces = queries.get_provinces(conn)
        get_provinces_cache(context)[:] = provinces
        await query.message.edit_text(
            provinces_text(provinces),
            reply_markup=provinces_keyboard(provinces),
            parse_mode=ParseMode.HTML,
        )
        return

    if data.startswith("m:latest:"):
        page = int(data.split(":")[-1])
        await show_domain_list(
            query.message,
            app_config,
            title="🆕 تازه‌ترین دامنه‌ها",
            nav_prefix="m:latest",
            page=page,
            fetch=lambda conn, offset, limit: queries.get_latest_domains(
                conn, offset=offset, limit=limit
            ),
            count_fn=queries.count_domains,
        )
        return

    if data.startswith("m:approve:"):
        page = int(data.split(":")[-1])
        await show_domain_list(
            query.message,
            app_config,
            title="📅 جدیدترین صدور اینماد",
            nav_prefix="m:approve",
            page=page,
            fetch=lambda conn, offset, limit: queries.get_newest_by_approve(
                conn, offset=offset, limit=limit
            ),
            count_fn=queries.count_with_approve,
        )
        return

    if data.startswith("m:top:"):
        page = int(data.split(":")[-1])
        await show_domain_list(
            query.message,
            app_config,
            title="⭐ دامنه‌های امتیاز بالا",
            nav_prefix="m:top",
            page=page,
            fetch=lambda conn, offset, limit: queries.get_top_rated(
                conn, offset=offset, limit=limit
            ),
            count_fn=queries.count_top_rated,
        )
        return

    if data.startswith("m:prov:"):
        parts = data.split(":")
        prov_index = int(parts[2])
        page = int(parts[3])
        provinces = get_provinces_cache(context)
        if not provinces:
            with mysql_connection(app_config.mysql) as conn:
                provinces = queries.get_provinces(conn)
            get_provinces_cache(context)[:] = provinces
        if prov_index >= len(provinces):
            await query.message.edit_text(
                "استان یافت نشد.",
                reply_markup=back_home_keyboard(),
            )
            return
        province = provinces[prov_index]["province"]

        def fetch(conn, offset, limit):
            return queries.get_domains_by_province(
                conn, province, offset=offset, limit=limit
            )

        def count_fn(conn):
            return queries.count_by_province(conn, province)

        await show_domain_list(
            query.message,
            app_config,
            title=f"🗺 استان {province}",
            nav_prefix=f"m:prov:{prov_index}",
            page=page,
            fetch=fetch,
            count_fn=count_fn,
        )
        return

    if data.startswith("m:dr:"):
        domain_id = int(data.split(":")[-1])
        await query.message.edit_text(
            "⏳ در حال دریافت مجوزها از enamad.ir …",
            parse_mode=ParseMode.HTML,
        )
        try:
            with mysql_connection(app_config.mysql) as conn:
                refreshed = await asyncio.to_thread(
                    refresh_domain_trustseal, conn, domain_id
                )
                commit_connection(conn)
            if not refreshed:
                await query.message.edit_text(
                    "❌ بروزرسانی ناموفق بود.",
                    reply_markup=back_home_keyboard(),
                    parse_mode=ParseMode.HTML,
                )
                return
            row, services = refreshed
            text = domain_detail_text(row, services, header="🔄 <b>بروزرسانی شد</b>")
            await query.message.edit_text(
                text,
                reply_markup=domain_detail_keyboard(domain_id),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Exception as exc:
            log.warning("Trust seal refresh failed for id=%s: %s", domain_id, exc)
            await query.message.edit_text(
                f"❌ خطا در بروزرسانی: {esc(str(exc))}",
                reply_markup=domain_detail_keyboard(domain_id),
                parse_mode=ParseMode.HTML,
            )
        return

    if data.startswith("m:d:"):
        domain_id = int(data.split(":")[-1])
        await show_domain_detail(query.message, app_config, domain_id)
        return


async def show_domain_list(
    message,
    app_config,
    *,
    title: str,
    nav_prefix: str,
    page: int,
    fetch,
    count_fn,
) -> None:
    offset = page * PAGE_SIZE
    with mysql_connection(app_config.mysql) as conn:
        rows = fetch(conn, offset, PAGE_SIZE)
        total = count_fn(conn)

    text = domain_list_text(title, rows, page, total)
    markup = domain_list_keyboard(rows, nav_prefix=nav_prefix, page=page, total=total)
    await message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)


async def show_domain_detail(message, app_config, domain_id: int) -> None:
    with mysql_connection(app_config.mysql) as conn:
        row = queries.get_domain_by_id(conn, domain_id)
        if not row:
            await message.edit_text(
                "❌ رکورد یافت نشد.",
                reply_markup=back_home_keyboard(),
                parse_mode=ParseMode.HTML,
            )
            return
        services = queries.get_domain_services(
            conn, str(row["enamad_id"]), str(row["code"])
        )

    text = domain_detail_text(row, services)
    await message.edit_text(
        text,
        reply_markup=domain_detail_keyboard(domain_id),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


def load_row_services(conn, row: dict) -> list[dict]:
    enamad_id = str(row.get("enamad_id") or "")
    code = str(row.get("code") or "")
    if not enamad_id or not code:
        return []
    return queries.get_domain_services(conn, enamad_id, code)


def build_search_reply(
    row: dict,
    services: list[dict],
    query: str,
    *,
    total: int,
    others: list[dict],
) -> str:
    header = search_detail_header(query, total=total, showing_best=total > 1)
    text = domain_detail_text(row, services, header=header)
    if others:
        extra = search_other_results_text(others, total)
        if extra and len(text) + len(extra) < 3900:
            text = f"{text}\n\n{extra}"
    return text


async def reply_search_result(
    message,
    row: dict,
    services: list[dict],
    query: str,
    *,
    total: int,
    others: list[dict],
) -> None:
    text = build_search_reply(row, services, query, total=total, others=others)
    domain_id = row.get("id")
    if others:
        markup = search_results_keyboard([row, *others])
    elif domain_id:
        markup = domain_detail_keyboard(int(domain_id))
    else:
        markup = back_home_keyboard()
    await message.reply_text(
        text,
        reply_markup=markup,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    if others and len(text) >= 3900:
        compact = search_other_results_text(others, total)
        if compact:
            await message.reply_text(
                compact,
                reply_markup=search_results_keyboard([row, *others]),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )


def _live_search_domain_sync(domain: str) -> dict | None:
    from extract_enamad import EnamadClient, maybe_enrich_row, normalize_search_row

    client = EnamadClient()
    data = client.search_domain(domain)
    if not data:
        return None
    row = normalize_search_row(data, domain)
    return maybe_enrich_row(client, row, True)


async def live_search_domain(domain: str) -> dict | None:
    return await asyncio.to_thread(_live_search_domain_sync, domain)


def format_live_result(row: dict) -> str:
    header = "🌐 <b>نتیجه زنده از enamad.ir</b>\n<i>(در دیتابیس محلی ذخیره نشده)</i>"
    services = row.get("services") or []
    return domain_detail_text(row, services, header=header)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    if not is_allowed(update, get_tg_config(context)):
        await deny_access(update)
        return

    text = update.message.text.strip()
    if not text or text.startswith("/"):
        return

    app_config = get_app_config(context)
    tg_config = get_tg_config(context)

    query = normalize_domain(text)
    if len(query) < 2:
        await update.message.reply_text(
            "⚠️ عبارت جستجو خیلی کوتاه است.",
            reply_markup=main_menu_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return

    with mysql_connection(app_config.mysql) as conn:
        row = queries.get_domain_exact(conn, query)
        if row:
            services = load_row_services(conn, row)
            await reply_search_result(
                update.message,
                row,
                services,
                query,
                total=1,
                others=[],
            )
            return

        rows = queries.search_domains(conn, query, limit=8)
        total = queries.count_search(conn, query)

    if rows:
        best = rows[0]
        others = rows[1:]
        with mysql_connection(app_config.mysql) as conn:
            services = load_row_services(conn, best)
        await reply_search_result(
            update.message,
            best,
            services,
            query,
            total=total,
            others=others,
        )
        return

    if tg_config.live_search:
        await update.message.reply_chat_action("typing")
        try:
            row = await live_search_domain(query)
        except Exception as exc:
            log.warning("Live search failed for %s: %s", query, exc)
            row = None

        if row:
            await update.message.reply_text(
                format_live_result(row),
                reply_markup=back_home_keyboard(),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            return

    await update.message.reply_text(
        f"🔍 نتیجه‌ای برای <code>{query}</code> پیدا نشد.",
        reply_markup=main_menu_keyboard(),
        parse_mode=ParseMode.HTML,
    )


def build_application(config_path: Path) -> Application:
    app_config = load_config(config_path)
    tg_config = load_telegram_config(config_path)

    request = HTTPXRequest(
        connect_timeout=tg_config.connect_timeout,
        read_timeout=tg_config.read_timeout,
        write_timeout=tg_config.read_timeout,
        pool_timeout=tg_config.connect_timeout,
        proxy=tg_config.proxy_url,
    )
    polling_request = HTTPXRequest(
        connect_timeout=tg_config.connect_timeout,
        read_timeout=tg_config.read_timeout,
        write_timeout=tg_config.read_timeout,
        pool_timeout=tg_config.connect_timeout,
        proxy=tg_config.proxy_url,
    )

    builder = (
        Application.builder()
        .token(tg_config.bot_token)
        .request(request)
        .get_updates_request(polling_request)
    )
    if tg_config.api_base_url:
        builder = builder.base_url(tg_config.api_base_url)
        log.info("Using custom Telegram API: %s", tg_config.api_base_url)

    application = builder.build()
    application.bot_data["app_config"] = app_config
    application.bot_data["tg_config"] = tg_config
    application.bot_data["provinces_cache"] = []

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_start))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    if tg_config.proxy_url:
        log.info("Telegram proxy enabled: %s", tg_config.proxy_url.split("@")[-1])

    return application


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enamad Telegram bot")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help="Path to config.ini (default: config.ini)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = SCRIPT_DIR / config_path

    try:
        app = build_application(config_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    log.info("Enamad Telegram bot started (polling mode — no webhook needed).")
    try:
        app.run_polling(
            allowed_updates=Update.ALL_TYPES,
            bootstrap_retries=5,
            drop_pending_updates=True,
        )
    except Exception as exc:
        if "TimedOut" in type(exc).__name__ or "Connect" in str(exc):
            print(
                "\n❌ اتصال به Telegram API برقرار نشد (Timed out).\n"
                "   این مشکل webhook نیست — سرور api.telegram.org از شبکه شما در دسترس نیست.\n\n"
                "   راه‌حل‌ها:\n"
                "   1. VPN / پروکسی سیستم را روشن کنید\n"
                "   2. در config.ini پروکسی محلی را تنظیم کنید، مثلاً:\n"
                "      proxy = http://127.0.0.1:10809\n"
                "      ; یا socks5://127.0.0.1:10808\n"
                "   3. connect_timeout = 60\n\n"
                "   Webhook لازم نیست — polling روی localhost کافی است.\n",
                file=sys.stderr,
            )
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
