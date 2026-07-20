"""Send admin messages to bot users (Telegram / Bale) from the web panel.

Uses the plain Bot API over HTTP (sendMessage), independent of the running
bot processes, so the web container only needs the tokens.
"""
from __future__ import annotations

import configparser
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.ini"

_DEFAULT_BASE = {
    "telegram": "https://api.telegram.org/bot",
    "bale": "https://tapi.bale.ai/bot",
}
_ENV_TOKEN_KEYS = {
    "telegram": ("BOT_TOKEN", "TELEGRAM_BOT_TOKEN"),
    "bale": ("BALE_BOT_TOKEN",),
}
_ENV_BASE_KEYS = {
    "telegram": ("TELEGRAM_API_BASE_URL",),
    "bale": ("BALE_API_BASE_URL",),
}
_CONFIG_SECTION = {"telegram": "telegram", "bale": "bale"}

PLATFORMS = ("telegram", "bale")


def _env(*keys: str) -> str:
    for key in keys:
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    return ""


def _config_parser() -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    if CONFIG_PATH.is_file():
        parser.read(CONFIG_PATH, encoding="utf-8")
    return parser


def resolve_platform(platform: str) -> tuple[str, str] | None:
    """Return (token, api_base_url) for a platform, or None if unconfigured."""
    if platform not in PLATFORMS:
        return None
    parser = _config_parser()
    section = _CONFIG_SECTION[platform]

    token = _env(*_ENV_TOKEN_KEYS[platform]) or parser.get(
        section, "bot_token", fallback=""
    ).strip()
    if not token or token.upper() == "YOUR_TOKEN":
        return None

    base = _env(*_ENV_BASE_KEYS[platform]) or parser.get(
        section, "api_base_url", fallback=""
    ).strip() or _DEFAULT_BASE[platform]
    if not base.endswith("/bot"):
        base = base.rstrip("/") + "/bot"
    return token, base


def configured_platforms() -> list[str]:
    return [p for p in PLATFORMS if resolve_platform(p)]


def send_bot_message(
    platform: str, user_id: int, text: str, *, timeout: float = 20.0
) -> dict[str, Any]:
    resolved = resolve_platform(platform)
    if not resolved:
        return {"ok": False, "error": f"توکن {platform} تنظیم نشده"}
    token, base = resolved
    try:
        resp = requests.post(
            f"{base}{token}/sendMessage",
            data={"chat_id": user_id, "text": text},
            timeout=timeout,
        )
        data = resp.json()
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc)}
    except ValueError:
        return {"ok": False, "error": f"پاسخ نامعتبر ({resp.status_code})"}
    if data.get("ok"):
        return {"ok": True}
    return {"ok": False, "error": str(data.get("description") or data)}


def broadcast(
    targets: list[tuple[str, int]], text: str, *, max_workers: int = 6
) -> dict[str, Any]:
    """Send `text` to (platform, user_id) targets. Returns counts + errors."""
    results = {"sent": 0, "failed": 0, "errors": []}
    if not targets:
        return results

    def _send(target: tuple[str, int]) -> tuple[tuple[str, int], dict]:
        return target, send_bot_message(target[0], target[1], text)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_send, t) for t in targets]
        for future in as_completed(futures):
            (platform, user_id), result = future.result()
            if result.get("ok"):
                results["sent"] += 1
            else:
                results["failed"] += 1
                if len(results["errors"]) < 15:
                    results["errors"].append(
                        f"{platform}:{user_id} — {result.get('error')}"
                    )
    return results
