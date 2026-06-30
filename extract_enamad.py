#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Enamad domain list scraper.
Stores results in MySQL. Each page requires a fresh captcha (auto OCR via ddddocr).

Setup:
  copy config.example.ini to config.ini
  python extract_enamad.py --init-db
  python extract_enamad.py --pages 2
"""

from __future__ import annotations

import argparse
import base64
import io
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

import certifi
import cv2
import numpy as np
import requests
from PIL import Image, ImageEnhance, ImageOps
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from db import (
    finish_scrape_run,
    init_database,
    load_config,
    mysql_connection,
    save_domains,
    start_scrape_run,
)

CAPTCHA_LEN = 5  # اینماد تقریباً همیشه ۵ کاراکتر

BASE_URL = "https://enamad.ir/"
PAGE_SIZE = 30
SCRIPT_DIR = Path(__file__).resolve().parent

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
    "Origin": "https://enamad.ir",
    "Referer": "https://enamad.ir/DomainListForMIMT",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


class CaptchaOcr:
    _engine = None

    def __init__(self) -> None:
        if CaptchaOcr._engine is None:
            try:
                import ddddocr
            except ImportError as exc:
                raise RuntimeError(
                    "ddddocr نصب نیست. اجرا کنید:\n"
                    "  python -m pip install ddddocr"
                ) from exc
            engine = ddddocr.DdddOcr(show_ad=False)
            try:
                engine.set_ranges(
                    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
                )
            except Exception:
                pass
            CaptchaOcr._engine = engine

    @staticmethod
    def _sanitize(text: str) -> str:
        return re.sub(r"[^a-zA-Z0-9]", "", text or "").strip()

    @staticmethod
    def _extract_five_char_slices(code: str) -> list[str]:
        if len(code) == CAPTCHA_LEN:
            return [code]
        if len(code) < CAPTCHA_LEN:
            return []
        slices: list[str] = []
        for start in range(0, len(code) - CAPTCHA_LEN + 1):
            part = code[start : start + CAPTCHA_LEN]
            if part not in slices:
                slices.append(part)
        return slices

    @staticmethod
    def _is_plausible(code: str) -> bool:
        return len(code) == CAPTCHA_LEN

    @staticmethod
    def _to_bytes(image: Image.Image) -> bytes:
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=95)
        return buffer.getvalue()

    @staticmethod
    def _to_bytes_cv(gray: np.ndarray) -> bytes:
        ok, encoded = cv2.imencode(".jpg", gray)
        if not ok:
            raise RuntimeError("encode تصویر کپچا ناموفق بود.")
        return encoded.tobytes()

    @staticmethod
    def _focus_crops(base: Image.Image) -> list[Image.Image]:
        """برش نواحی اصلی؛ حذف واترمارک پایین و متن گمراه‌کننده راست."""
        width, height = base.size
        return [
            # بدون پایین (واترمارک) و بدون راست (متن اضافی)
            base.crop((0, 0, int(width * 0.70), int(height * 0.65))),
            # ناحیه مرکزی
            base.crop((
                int(width * 0.05),
                int(height * 0.05),
                int(width * 0.68),
                int(height * 0.62),
            )),
            # سمت چپ-بالا (متن اصلی معمولاً اینجاست)
            base.crop((0, 0, int(width * 0.62), int(height * 0.58))),
        ]

    @staticmethod
    def _remove_small_components(gray: np.ndarray) -> np.ndarray:
        """حذف نوشته‌های ریز (واترمارک پایین و کنار)."""
        inverted = cv2.bitwise_not(gray)
        _, binary = cv2.threshold(inverted, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)

        if num_labels <= 1:
            return gray

        areas = stats[1:, cv2.CC_STAT_AREA]
        heights = stats[1:, cv2.CC_STAT_HEIGHT]
        max_area = int(areas.max()) if len(areas) else 0
        max_height = int(heights.max()) if len(heights) else 0

        mask = np.zeros_like(gray)
        for label in range(1, num_labels):
            area = stats[label, cv2.CC_STAT_AREA]
            height = stats[label, cv2.CC_STAT_HEIGHT]
            if area >= max(25, max_area * 0.18) and height >= max(8, max_height * 0.45):
                mask[labels == label] = 255

        if mask.max() == 0:
            return gray
        return cv2.bitwise_not(mask)

    def _enhance_crop(self, crop: Image.Image, scale: int = 3) -> list[bytes]:
        outputs: list[bytes] = []
        width, height = crop.size
        big = crop.resize((width * scale, height * scale), Image.Resampling.LANCZOS)
        outputs.append(self._to_bytes(big))

        gray = ImageOps.grayscale(big)
        gray = ImageEnhance.Contrast(gray).enhance(2.8)
        gray = gray.point(lambda px: 255 if px > 140 else 0)
        outputs.append(self._to_bytes(gray))

        arr = np.array(big.convert("RGB"))
        cv_gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        cleaned = self._remove_small_components(cv_gray)
        outputs.append(self._to_bytes_cv(cleaned))
        _, otsu = cv2.threshold(cleaned, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        outputs.append(self._to_bytes_cv(otsu))
        return outputs

    def _preprocess_variants(self, image_bytes: bytes) -> list[tuple[bytes, int]]:
        """(bytes, weight) — وزن بیشتر = برش دقیق‌تر روی متن اصلی."""
        weighted: list[tuple[bytes, int]] = []
        base = Image.open(io.BytesIO(image_bytes)).convert("RGB")

        for crop in self._focus_crops(base):
            for variant in self._enhance_crop(crop, scale=3):
                weighted.append((variant, 4))

        for crop in self._focus_crops(base):
            for variant in self._enhance_crop(crop, scale=2):
                weighted.append((variant, 3))

        width, height = base.size
        bottom_cut = base.crop((0, 0, width, int(height * 0.72)))
        weighted.append((self._to_bytes(bottom_cut.resize((width * 3, int(height * 0.72 * 3)), Image.Resampling.LANCZOS)), 1))

        return weighted

    def read(self, image_bytes: bytes) -> str:
        candidates = self.read_candidates(image_bytes)
        return candidates[0] if candidates else ""

    def read_candidates(self, image_bytes: bytes) -> list[str]:
        votes: Counter[str] = Counter()

        for variant, weight in self._preprocess_variants(image_bytes):
            raw = CaptchaOcr._engine.classification(variant)
            code = self._sanitize(raw)
            for guess in self._extract_five_char_slices(code):
                if not self._is_plausible(guess):
                    continue
                votes[guess.lower()] += weight * 3
                votes[guess] += weight * 2
                votes[guess.upper()] += weight

        if not votes:
            return []

        ranked = sorted(votes.items(), key=lambda item: (-item[1], item[0]))
        ordered: list[str] = []
        for code, _score in ranked:
            if code not in ordered:
                ordered.append(code)
        return ordered[:10]


def captcha_submit_variants(code: str) -> list[str]:
    if len(code) != CAPTCHA_LEN:
        return []
    return [code.lower()]


def resolve_ca_bundle() -> str | bool:
    candidates = [
        certifi.where(),
        r"C:\laragon\etc\ssl\cacert.pem",
        r"D:\laragon\etc\ssl\cacert.pem",
        str(SCRIPT_DIR / "cacert.pem"),
    ]
    for path in candidates:
        if path and Path(path).is_file():
            return path
    return True


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    session.verify = resolve_ca_bundle()

    retry = Retry(
        total=4,
        connect=4,
        read=4,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class EnamadClient:
    def __init__(self) -> None:
        self.session = create_session()
        self._warmed = False

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{BASE_URL}{path.lstrip('/')}"
        last_error: Exception | None = None

        for attempt in range(1, 4):
            try:
                response = self.session.request(method, url, timeout=90, **kwargs)
                response.raise_for_status()
                return response
            except (requests.exceptions.SSLError, requests.exceptions.ConnectionError) as exc:
                last_error = exc
                wait = attempt * 2
                print(f"  خطای اتصال (تلاش {attempt}/3)، {wait} ثانیه صبر...")
                time.sleep(wait)

        raise RuntimeError(f"اتصال به enamad.ir برقرار نشد: {last_error}") from last_error

    def warm_session(self) -> None:
        if self._warmed:
            return
        try:
            self._request("GET", "DomainListForMIMT")
            self._warmed = True
        except Exception as exc:
            print(f"  هشدار: بارگذاری اولیه صفحه ناموفق ({exc})")

    def refresh_captcha(self) -> tuple[str, bytes]:
        self.warm_session()
        response = self._request(
            "POST",
            "refreshCapt",
            json={},
            headers={"Content-Type": "application/json; charset=UTF-8"},
        )
        data = response.json()
        token = data.get("cptToken", "")
        image_b64 = data.get("captha") or data.get("captcha") or ""
        if not token or not image_b64:
            raise RuntimeError("دریافت کپچا ناموفق بود.")
        return token, base64.b64decode(image_b64)

    def get_domain_list(self, page: int, token: str, captcha: str) -> dict:
        payload = {
            "s#ms-domain-address": "",
            "s#ms-persian-name": "",
            "s#ms-product-service-id-enc": "",
            "s#mi-rating": "-1",
            "s#ms-province-id-enc": "",
            "s#ms-city-id-enc": "",
            "Capt": captcha,
            "Csearch": "",
            "page": str(page),
            "token": token,
            "cptToken": token,
            "checkcapga": "0",
        }
        response = self._request(
            "POST",
            "getDomainList",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
        )
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            snippet = response.text[:200].replace("\n", " ")
            raise RuntimeError(f"پاسخ نامعتبر از سرور: {snippet}") from exc


def needs_captcha(message: str) -> bool:
    keywords = ("کپچا", "کد امنیتی", "captcha", "امنیتی", "وارد نمایید")
    lower = message.lower()
    return any(k in message or k in lower for k in keywords)


def normalize_row(item: dict, row_number: int, page: int) -> dict:
    domain_id = item.get("id", "")
    code = item.get("code", "")
    trustseal = ""
    if domain_id and code:
        trustseal = f"https://trustseal.enamad.ir/?id={domain_id}&code={code}"

    return {
        "enamad_id": str(domain_id),
        "code": str(code),
        "domain": item.get("domain_address", ""),
        "business_name": item.get("persian_name", "") or "",
        "province": item.get("province", "") or "",
        "city": item.get("city", "") or "",
        "rating": item.get("rating", 0),
        "approve_date": item.get("approve_date", "") or "",
        "expire_date": item.get("expire_date", "") or "",
        "trustseal_url": trustseal,
        "source_page": page,
        "source_row": row_number,
    }


def save_captcha_image(image_bytes: bytes, page: int, attempt: int, debug_dir: Path | None) -> Path:
    save_dir = debug_dir or (SCRIPT_DIR / "captcha_tmp")
    save_dir.mkdir(parents=True, exist_ok=True)
    img_path = save_dir / f"captcha_page_{page}_try{attempt}.jpg"
    img_path.write_bytes(image_bytes)
    return img_path


def fetch_page(
    client: EnamadClient,
    page: int,
    ocr: CaptchaOcr | None,
    manual: bool,
    max_retries: int,
    debug_dir: Path | None,
) -> tuple[list[dict], int]:
    last_error = "خطای نامشخص"

    for attempt in range(1, max_retries + 1):
        token, image_bytes = client.refresh_captcha()
        img_path = save_captcha_image(image_bytes, page, attempt, debug_dir)

        if manual:
            print(f"  تصویر کپچا: {img_path}")
            if sys.platform == "win32":
                os.startfile(img_path)
            if not sys.stdin.isatty():
                raise RuntimeError("حالت manual فقط در ترمینال کار می‌کند.")
            guesses = [input("  کد کپچا را وارد کنید: ").strip()]
        elif ocr is None:
            raise RuntimeError("OCR فعال نیست.")
        else:
            guesses = ocr.read_candidates(image_bytes)
            if guesses:
                preview = ", ".join(guesses[:6])
                if len(guesses) > 6:
                    preview += ", ..."
                print(f"  حدس‌ها: {preview}")
            else:
                last_error = "OCR کپچا را تشخیص نداد"
                print(f"  تلاش {attempt}/{max_retries}: {last_error}")
                continue

        tried_any = False
        for guess in guesses:
            if not guess:
                continue
            for submit_code in captcha_submit_variants(guess):
                tried_any = True
                result = client.get_domain_list(page, token, submit_code)

                if int(result.get("result", 0)) == 1:
                    if submit_code != guess:
                        print(f"  ✓ کپچا با '{submit_code}' قبول شد")
                    domains = result.get("applicantDomainsList") or []
                    total_pages = max(1, int(result.get("page", 1)))
                    rows = [
                        normalize_row(item, ((page - 1) * PAGE_SIZE) + index + 1, page)
                        for index, item in enumerate(domains)
                    ]
                    return rows, total_pages

                last_error = str(result.get("result_msg", "خطای نامشخص"))
                if not needs_captcha(last_error):
                    raise RuntimeError(f"صفحه {page}: {last_error}")

        if not tried_any:
            last_error = "کد کپچا خالی است"
        print(f"  تلاش {attempt}/{max_retries}: کپچا اشتباه ({last_error})")

    raise RuntimeError(f"صفحه {page} بعد از {max_retries} تلاش ناموفق: {last_error}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape Enamad domain holders into MySQL")
    parser.add_argument(
        "--config",
        default="config.ini",
        help="Path to config.ini (default: config.ini)",
    )
    parser.add_argument(
        "--init-db",
        action="store_true",
        help="Create database and tables from schema.sql",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=1,
        help="Number of pages to fetch (default: 1)",
    )
    parser.add_argument(
        "--start-page",
        type=int,
        default=1,
        help="Start from page N (default: 1)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=None,
        help="Delay between pages in seconds (overrides config.ini)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=None,
        help="Max captcha retries per page (overrides config.ini)",
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="Enter captcha manually instead of OCR",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Save captcha images to debug_captcha/",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = SCRIPT_DIR / config_path

    if args.init_db:
        app_config = load_config(config_path)
        print("Initializing database...")
        init_database(app_config.mysql)
        print(f"Database ready: {app_config.mysql.database}")
        return 0

    app_config = load_config(config_path)
    delay = args.delay if args.delay is not None else app_config.scraper.delay
    retries = args.retries if args.retries is not None else app_config.scraper.retries

    if args.pages < 1:
        print("Page count must be at least 1.", file=sys.stderr)
        return 1

    debug_dir = SCRIPT_DIR / "debug_captcha" if args.debug else None

    if args.manual:
        ocr = None
        print("Manual captcha mode.")
    else:
        print("Loading OCR (ddddocr)...")
        ocr = CaptchaOcr()
        print("OCR ready.")

    client = EnamadClient()
    end_page = args.start_page + args.pages - 1
    total_saved = 0
    pages_fetched = 0
    run_id: int | None = None

    print(f"Scraping pages {args.start_page} to {end_page} -> MySQL ({app_config.mysql.database})")

    try:
        with mysql_connection(app_config.mysql) as conn:
            run_id = start_scrape_run(
                conn,
                start_page=args.start_page,
                pages_requested=args.pages,
                notes="manual" if args.manual else "ddddocr",
            )

            for page in range(args.start_page, end_page + 1):
                print(f"Page {page}...")
                rows, total_pages = fetch_page(
                    client,
                    page,
                    ocr,
                    args.manual,
                    retries,
                    debug_dir,
                )
                saved = save_domains(conn, rows, scrape_run_id=run_id)
                total_saved += saved
                pages_fetched += 1
                print(f"  -> {len(rows)} records saved (total: {total_saved})")

                if total_pages is not None and page >= total_pages:
                    print(f"Reached last available page ({total_pages}).")
                    break

                if page < end_page:
                    time.sleep(max(0.0, delay))

            finish_scrape_run(
                conn,
                run_id=run_id,
                pages_fetched=pages_fetched,
                records_saved=total_saved,
                status="completed",
            )
    except Exception as exc:
        if run_id is not None:
            try:
                with mysql_connection(app_config.mysql) as conn:
                    finish_scrape_run(
                        conn,
                        run_id=run_id,
                        pages_fetched=pages_fetched,
                        records_saved=total_saved,
                        status="failed",
                        notes=str(exc),
                    )
            except Exception:
                pass
        raise

    print(f"Done. {total_saved} records stored in MySQL (run_id={run_id}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
