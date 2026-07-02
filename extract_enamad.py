#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Enamad domain list scraper.
Stores results in MySQL. Each page requires a fresh captcha (auto OCR via ddddocr).

Setup:
  copy config.example.ini to config.ini
  python extract_enamad.py --init-db
  python extract_enamad.py --pages 2
  python extract_enamad.py --all
  python extract_enamad.py --search digikala.com
  python extract_enamad.py --search-file domains.txt
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys
import time
import threading
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from html import unescape
from multiprocessing import Manager
from pathlib import Path

import certifi
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from typing import TYPE_CHECKING

from console_ui import (
    ScrapeConsole,
    ScrapeStats,
    WorkerConsole,
    ParallelDashboard,
    enable_colors,
    fmt_int,
    paint,
    C,
)
from db import (
    commit_connection,
    finish_scrape_run,
    fix_encoded_domains,
    refresh_domain_services,
    refresh_stale_domains,
    refresh_stale_domains_parallel,
    get_scrape_state,
    init_database,
    load_config,
    mysql_connection,
    normalize_domain,
    reset_scrape_state,
    save_domains,
    start_scrape_run,
    update_scrape_progress,
    get_worker_progress,
    update_worker_progress,
)

if TYPE_CHECKING:
    import numpy as np
    from PIL import Image

    from captcha_learn import CaptchaLearner

CAPTCHA_LEN = 5  # اینماد تقریباً همیشه ۵ کاراکتر

BASE_URL = "https://enamad.ir/"
TRUSTSEAL_URL = "https://trustseal.enamad.ir/"
PAGE_SIZE = 30
SCRIPT_DIR = Path(__file__).resolve().parent
CAPTCHA_LEARN_PATH = SCRIPT_DIR / "captcha_learn.json"


def configure_console_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def safe_print(text: str = "") -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        data = (text + "\n").encode(encoding, errors="replace")
        sys.stdout.buffer.write(data)
        sys.stdout.flush()

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


def _ocr_libs():
    """Lazy-load OpenCV / NumPy / Pillow (not needed for --init-db or --refresh-stale)."""
    import cv2
    import numpy as np
    from PIL import Image, ImageEnhance, ImageOps

    return cv2, np, Image, ImageEnhance, ImageOps


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
    def _to_bytes(image: "Image.Image") -> bytes:
        _, _, Image, _, _ = _ocr_libs()
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=95)
        return buffer.getvalue()

    @staticmethod
    def _to_bytes_cv(gray: "np.ndarray") -> bytes:
        cv2, _, _, _, _ = _ocr_libs()
        ok, encoded = cv2.imencode(".jpg", gray)
        if not ok:
            raise RuntimeError("encode تصویر کپچا ناموفق بود.")
        return encoded.tobytes()

    @staticmethod
    def _focus_crops(base: "Image.Image") -> list["Image.Image"]:
        _, _, Image, _, _ = _ocr_libs()
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
    def _remove_small_components(gray: "np.ndarray") -> "np.ndarray":
        cv2, np, _, _, _ = _ocr_libs()
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

    def _enhance_crop(self, crop: "Image.Image", scale: int = 3) -> list[bytes]:
        cv2, np, Image, ImageEnhance, ImageOps = _ocr_libs()
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

    def _preprocess_variants(
        self, image_bytes: bytes, fast: bool = False
    ) -> list[tuple[bytes, int]]:
        """(bytes, weight) — وزن بیشتر = برش دقیق‌تر روی متن اصلی."""
        _, _, Image, _, _ = _ocr_libs()
        weighted: list[tuple[bytes, int]] = []
        base = Image.open(io.BytesIO(image_bytes)).convert("RGB")

        if fast:
            crop = self._focus_crops(base)[0]
            for variant in self._enhance_crop(crop, scale=3)[:2]:
                weighted.append((variant, 4))
            return weighted

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

    def read_candidates(
        self,
        image_bytes: bytes,
        learner: CaptchaLearner | None = None,
        fast: bool = False,
    ) -> list[str]:
        votes: Counter[str] = Counter()

        for variant, weight in self._preprocess_variants(image_bytes, fast=fast):
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
        ordered = ordered[:10]

        if learner is not None and learner.enabled:
            ordered = learner.expand_candidates(ordered, image_bytes)

        return unique_captcha_guesses(ordered, max_guesses=8)


def captcha_submit_variants(code: str) -> list[str]:
    if len(code) != CAPTCHA_LEN:
        return []
    return [code.lower()]


def unique_captcha_guesses(candidates: list[str], max_guesses: int = 8) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for item in candidates:
        code = re.sub(r"[^a-zA-Z0-9]", "", item or "").lower()
        if len(code) != CAPTCHA_LEN or code in seen:
            continue
        seen.add(code)
        unique.append(code)
        if len(unique) >= max_guesses:
            break
    return unique


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
    def __init__(self, *, quiet: bool = False, timeout: int = 90, retries: int = 3) -> None:
        self.session = create_session()
        self._warmed = False
        self.quiet = quiet
        self.timeout = timeout
        self.retries = retries

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
            print(f"  Warning: initial page load failed ({exc})")

    def warm_home(self) -> None:
        self._request("GET", "")

    def search_domain(self, domain: str) -> dict | None:
        """Lookup a domain via enamad.ir/Home/GetData (site search box API)."""
        self.warm_home()
        cleaned = clean_domain(domain)
        response = self._request(
            "POST",
            "Home/GetData",
            data={"domain": cleaned},
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        text = response.text.strip()
        if not text or text == "null":
            return None
        data = response.json()
        if not isinstance(data, dict):
            return None
        domain_id = int(data.get("id") or 0)
        if domain_id <= 0:
            return None
        return data

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

    def fetch_trustseal_html(self, domain_id: str | int, code: str) -> str:
        """Fetch trust seal page (POST, same as enamad.ir search modal)."""
        last_error: Exception | None = None
        attempts = max(1, self.retries)
        for attempt in range(1, attempts + 1):
            try:
                response = self.session.post(
                    TRUSTSEAL_URL,
                    data={"id": str(domain_id), "Code": code},
                    headers={"Referer": BASE_URL},
                    timeout=self.timeout,
                )
                response.raise_for_status()
                return response.text
            except (
                requests.exceptions.SSLError,
                requests.exceptions.ConnectionError,
                requests.exceptions.HTTPError,
                requests.exceptions.Timeout,
            ) as exc:
                last_error = exc
                if attempt >= attempts:
                    break
                wait = attempt * 2
                if not self.quiet:
                    print(f"  trustseal error (attempt {attempt}/{attempts}), waiting {wait}s...")
                time.sleep(wait)

        raise RuntimeError(f"Could not fetch trust seal page: {last_error}") from last_error

    def fetch_trustseal_details(self, domain_id: str | int, code: str) -> dict:
        return parse_trustseal_html(self.fetch_trustseal_html(domain_id, code))


TRUSTSEAL_LABELS = {
    "صاحب امتیاز :": "owner_name",
    "آدرس:": "business_address",
    "تلفن:": "phone",
    "پست الكترونیكی:": "email",
    "ساعت پاسخگویی:": "work_hours",
}


def _clean_html_text(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_table_cell(value: str) -> str:
    value = value.strip()
    if value in ("-----------------", "—", "-", "–"):
        return ""
    return value


def parse_services_table(html: str) -> list[dict]:
    heading = "خدمات و مجوزهای کسب و کار"
    start = html.find(heading)
    if start < 0:
        return []

    table_match = re.search(
        r"<table[^>]*\btable\b[^>]*>.*?<tbody>(.*?)</tbody>",
        html[start:],
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not table_match:
        return []

    services: list[dict] = []
    for row_match in re.finditer(
        r"<tr>\s*(.*?)\s*</tr>",
        table_match.group(1),
        flags=re.DOTALL | re.IGNORECASE,
    ):
        cells = re.findall(
            r"<td[^>]*>(.*?)</td>",
            row_match.group(1),
            flags=re.DOTALL | re.IGNORECASE,
        )
        if len(cells) < 7:
            continue

        cleaned = [_normalize_table_cell(_clean_html_text(cell)) for cell in cells[:7]]
        title = cleaned[1].strip()
        if not title:
            continue
        services.append(
            {
                "row_num": len(services) + 1,
                "service_title": title,
                "license_issuer": cleaned[2],
                "license_number": cleaned[3],
                "valid_from": cleaned[4],
                "valid_to": cleaned[5],
                "status": cleaned[6],
            }
        )

    return services


def parse_trustseal_html(html: str) -> dict:
    details = {field: "" for field in TRUSTSEAL_LABELS.values()}
    for label, field in TRUSTSEAL_LABELS.items():
        pattern = (
            rf"{re.escape(label)}\s*</div>\s*"
            r'<div[^>]*\bcontentinformation\b[^>]*>\s*(.*?)\s*</div>'
        )
        match = re.search(pattern, html, flags=re.DOTALL | re.IGNORECASE)
        if match:
            details[field] = _clean_html_text(match.group(1))

    shop_match = re.search(r'id="shopLink"[^>]*>(.*?)</a>', html, flags=re.DOTALL | re.IGNORECASE)
    if shop_match:
        details["shop_name"] = _clean_html_text(shop_match.group(1))

    details["services"] = parse_services_table(html)
    return details


def enrich_row_with_trustseal(client: EnamadClient, row: dict) -> dict:
    if not row.get("enamad_id") or not row.get("code"):
        return row

    details = client.fetch_trustseal_details(row["enamad_id"], row["code"])
    enriched = dict(row)
    for key in TRUSTSEAL_LABELS.values():
        value = details.get(key, "")
        if value:
            enriched[key] = value

    if not enriched.get("persian_name") and details.get("shop_name"):
        enriched["persian_name"] = details["shop_name"]
        enriched["business_name"] = details["shop_name"]

    enriched["services"] = details.get("services") or []
    return enriched


def maybe_enrich_row(client: EnamadClient, row: dict, enabled: bool) -> dict:
    if not enabled:
        return row
    try:
        return enrich_row_with_trustseal(client, row)
    except Exception as exc:
        print(f"  Warning: trust seal details failed ({exc})")
        return row


def clean_domain(domain: str) -> str:
    return normalize_domain(domain)


def needs_captcha(message: str) -> bool:
    keywords = ("کپچا", "کد امنیتی", "captcha", "امنیتی", "وارد نمایید")
    lower = message.lower()
    return any(k in message or k in lower for k in keywords)


def normalize_search_row(data: dict, queried_domain: str) -> dict:
    domain_id = data.get("id", "")
    code = data.get("code", "")
    trustseal = ""
    if domain_id and code:
        trustseal = f"https://trustseal.enamad.ir/?id={domain_id}&code={code}"

    province = data.get("statename") or data.get("stateName") or data.get("province") or ""
    city = data.get("cityname") or data.get("cityName") or data.get("city") or ""
    approve = data.get("approvedate") or data.get("approve_date") or ""
    expire = data.get("expdate") or data.get("expire_date") or ""
    rating = data.get("rating")
    if rating is None:
        rating = data.get("logolevel", 0)

    return {
        "enamad_id": str(domain_id),
        "code": str(code),
        "domain": normalize_domain(str(data.get("domain_address") or queried_domain)),
        "business_name": data.get("persian_name") or data.get("nameper") or "",
        "province": province,
        "city": city,
        "rating": rating or 0,
        "approve_date": approve,
        "expire_date": expire,
        "trustseal_url": trustseal,
        "source_page": None,
        "source_row": None,
    }


def print_search_result(row: dict) -> None:
    safe_print(f"  Domain:        {row['domain']}")
    safe_print(f"  Persian name:  {row.get('persian_name') or row.get('business_name', '')}")
    if row.get("owner_name"):
        safe_print(f"  Owner:         {row['owner_name']}")
    if row.get("business_address"):
        safe_print(f"  Address:       {row['business_address']}")
    if row.get("phone"):
        safe_print(f"  Phone:         {row['phone']}")
    if row.get("email"):
        safe_print(f"  Email:         {row['email']}")
    if row.get("work_hours"):
        safe_print(f"  Work hours:    {row['work_hours']}")
    safe_print(f"  Province/City: {row['province']} / {row['city']}")
    safe_print(f"  Rating:        {row['rating']}")
    safe_print(f"  Approve:       {row['approve_date']}")
    safe_print(f"  Expire:        {row['expire_date']}")
    safe_print(f"  Trust seal:    {row['trustseal_url']}")
    services = row.get("services") or []
    if services:
        safe_print(f"  Services ({len(services)}):")
        for service in services:
            title = service.get("service_title") or "?"
            status = service.get("status") or ""
            issuer = service.get("license_issuer") or ""
            line = f"    - {title}"
            if status:
                line += f" [{status}]"
            if issuer:
                line += f" ({issuer})"
            safe_print(line)


def run_search(args, app_config) -> int:
    domains: list[str] = []
    if args.search:
        domains.append(args.search)
    if args.search_file:
        path = Path(args.search_file)
        if not path.is_absolute():
            path = SCRIPT_DIR / path
        domains.extend(
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        )

    if not domains:
        print("No domains to search.", file=sys.stderr)
        return 1

    delay = args.delay if args.delay is not None else app_config.scraper.delay
    client = EnamadClient()
    found = 0
    saved = 0
    fetch_details = not args.no_details

    if args.no_save:
        for raw in domains:
            query = clean_domain(raw)
            print(f"Searching: {query}")
            data = client.search_domain(query)
            if not data:
                print("  -> Not found in Enamad")
                time.sleep(max(0.0, delay))
                continue

            row = normalize_search_row(data, query)
            if fetch_details:
                print("  Fetching trust seal details...")
                row = maybe_enrich_row(client, row, True)
            found += 1
            if args.json:
                print(json.dumps(row, ensure_ascii=False, indent=2))
            else:
                print_search_result(row)
            time.sleep(max(0.0, delay))

        print(f"Done. Found {found}/{len(domains)}.")
        return 0 if found > 0 else 1

    with mysql_connection(app_config.mysql) as conn:
        run_id = start_scrape_run(
            conn,
            start_page=0,
            pages_requested=0,
            notes=f"search: {len(domains)} domain(s)",
        )
        commit_connection(conn)

        for raw in domains:
            query = clean_domain(raw)
            print(f"Searching: {query}")
            data = client.search_domain(query)
            if not data:
                print("  -> Not found in Enamad")
                time.sleep(max(0.0, delay))
                continue

            row = normalize_search_row(data, query)
            if fetch_details:
                print("  Fetching trust seal details...")
                row = maybe_enrich_row(client, row, True)
            found += 1

            if args.json:
                print(json.dumps(row, ensure_ascii=False, indent=2))
            else:
                print_search_result(row)

            save_domains(conn, [row], scrape_run_id=run_id)
            commit_connection(conn)
            saved += 1
            print("  -> Saved to MySQL")

            time.sleep(max(0.0, delay))

        finish_scrape_run(
            conn,
            run_id=run_id,
            pages_fetched=0,
            records_saved=saved,
            status="completed",
            notes=f"search found {found}/{len(domains)}",
        )
        commit_connection(conn)

    print(f"Done. Found {found}/{len(domains)}, saved {saved}.")
    return 0 if found > 0 or args.no_save else 1


def normalize_row(item: dict, row_number: int, page: int) -> dict:
    domain_id = item.get("id", "")
    code = item.get("code", "")
    trustseal = ""
    if domain_id and code:
        trustseal = f"https://trustseal.enamad.ir/?id={domain_id}&code={code}"

    return {
        "enamad_id": str(domain_id),
        "code": str(code),
        "domain": normalize_domain(str(item.get("domain_address", ""))),
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


def save_captcha_image(
    image_bytes: bytes, page: int, attempt: int, debug_dir: Path | None, *, force: bool = False
) -> Path | None:
    if debug_dir is None and not force:
        return None
    save_dir = debug_dir or (SCRIPT_DIR / "captcha_tmp")
    save_dir.mkdir(parents=True, exist_ok=True)
    img_path = save_dir / f"captcha_page_{page}_try{attempt}.jpg"
    img_path.write_bytes(image_bytes)
    return img_path


def cleanup_captcha_images() -> None:
    removed = 0
    for directory in (SCRIPT_DIR / "captcha_tmp", SCRIPT_DIR / "debug_captcha"):
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            if path.is_file():
                path.unlink(missing_ok=True)
                removed += 1
        try:
            directory.rmdir()
        except OSError:
            pass
    if removed:
        print(f"Removed {removed} captcha image(s).")


def _rows_from_result(result: dict, page: int) -> tuple[list[dict], int]:
    domains = result.get("applicantDomainsList") or []
    total_pages = max(1, int(result.get("page", 1)))
    rows = [
        normalize_row(item, ((page - 1) * PAGE_SIZE) + index + 1, page)
        for index, item in enumerate(domains)
    ]
    return rows, total_pages


def _solve_captcha_for_page(
    client: EnamadClient,
    page: int,
    ocr: CaptchaOcr | None,
    manual: bool,
    max_retries: int,
    debug_dir: Path | None,
    learner: CaptchaLearner | None,
    ui: ScrapeConsole | None = None,
    stats: ScrapeStats | None = None,
    fast_ocr: bool = False,
    wcon: WorkerConsole | None = None,
) -> tuple[str, str, dict]:
    last_error = "خطای نامشخص"

    for attempt in range(1, max_retries + 1):
        token, image_bytes = client.refresh_captcha()
        img_path = save_captcha_image(
            image_bytes, page, attempt, debug_dir, force=manual
        )
        failed_this_image: list[str] = []

        if manual:
            if img_path is None:
                raise RuntimeError("ذخیره تصویر کپچا برای حالت manual ناموفق بود.")
            print(f"  تصویر کپچا: {img_path}")
            if sys.platform == "win32":
                os.startfile(img_path)
            if not sys.stdin.isatty():
                raise RuntimeError("حالت manual فقط در ترمینال کار می‌کند.")
            guesses = [input("  کد کپچا را وارد کنید: ").strip()]
            ocr_guesses: list[str] = []
        elif ocr is None:
            raise RuntimeError("OCR فعال نیست.")
        else:
            ocr_guesses = ocr.read_candidates(image_bytes, learner=learner, fast=fast_ocr)
            guesses = ocr_guesses
            if guesses:
                preview = ", ".join(guesses[:6])
                if len(guesses) > 6:
                    preview += ", ..."
                learned = ""
                if (
                    learner is not None
                    and learner.enabled
                    and learner.lookup_solution(image_bytes)
                ):
                    learned = " (cache)"
                if ui:
                    ui.captcha_guesses(preview, learned)
                elif wcon:
                    wcon.captcha_guesses(preview, learned)
                else:
                    print(f"  حدس‌ها{learned}: {preview}")
            else:
                last_error = "OCR کپچا را تشخیص نداد"
                if ui:
                    ui.captcha_fail(attempt, max_retries, last_error)
                elif wcon:
                    wcon.captcha_fail(attempt, max_retries, last_error)
                else:
                    print(f"  تلاش {attempt}/{max_retries}: {last_error}")
                continue

        tried_any = False
        for index, submit_code in enumerate(unique_captcha_guesses(guesses, max_guesses=8), start=1):
            tried_any = True
            if ui:
                ui.captcha_try(index, submit_code)
            elif wcon:
                wcon.captcha_try(index, submit_code)
            else:
                print(f"  try {index}: {submit_code}...", flush=True)
            result = client.get_domain_list(page, token, submit_code)

            if int(result.get("result", 0)) == 1:
                if learner is not None and learner.enabled:
                    learner.record_success(
                        image_bytes,
                        submit_code,
                        failed_this_image,
                        ocr_guesses,
                    )
                if stats:
                    stats.note_captcha_solved(len(failed_this_image))
                return token, submit_code, result

            last_error = str(result.get("result_msg", "خطای نامشخص"))
            if needs_captcha(last_error):
                failed_this_image.append(submit_code)
            else:
                raise RuntimeError(f"صفحه {page}: {last_error}")

        if not tried_any:
            last_error = "کد کپچا خالی است"
        if stats:
            stats.note_captcha_round_failed()
        if ui:
            ui.captcha_fail(attempt, max_retries, last_error)
        elif wcon:
            wcon.captcha_fail(attempt, max_retries, last_error)
        else:
            print(f"  تلاش {attempt}/{max_retries}: کپچا اشتباه ({last_error})")

    raise RuntimeError(f"صفحه {page} بعد از {max_retries} تلاش ناموفق: {last_error}")


DEFAULT_CHUNK_PAGES = 5


def fetch_page_chunk(
    client: EnamadClient,
    start_page: int,
    end_page: int | None,
    ocr: CaptchaOcr | None,
    manual: bool,
    max_retries: int,
    debug_dir: Path | None,
    learner: CaptchaLearner | None = None,
    reuse_captcha: bool = True,
    ui: ScrapeConsole | None = None,
    stats: ScrapeStats | None = None,
    max_chunk_pages: int = DEFAULT_CHUNK_PAGES,
    fast_ocr: bool = False,
    wcon: WorkerConsole | None = None,
) -> tuple[list[tuple[int, list[dict]]], int | None]:
    """Solve captcha once, then fetch consecutive pages while the API accepts reuse."""
    token, submit_code, first_result = _solve_captcha_for_page(
        client,
        start_page,
        ocr,
        manual,
        max_retries,
        debug_dir,
        learner,
        ui=ui,
        stats=stats,
        fast_ocr=fast_ocr,
        wcon=wcon,
    )

    chunk: list[tuple[int, list[dict]]] = []
    total_pages_api: int | None = None
    page = start_page
    result = first_result
    pages_in_session = 0

    while True:
        if end_page is not None and page > end_page:
            break
        if reuse_captcha and pages_in_session >= max(1, max_chunk_pages):
            break

        if page > start_page:
            if ui:
                ui.captcha_reuse(page)
            elif wcon:
                wcon.captcha_reuse(page)
            else:
                print(f"  reuse captcha -> page {page}...", flush=True)
            result = client.get_domain_list(page, token, submit_code)
            if int(result.get("result", 0)) != 1:
                message = str(result.get("result_msg", ""))
                if needs_captcha(message):
                    msg = f"کپچا برای صفحه {page} منقضی شد — کپچای جدید لازم است"
                    if wcon:
                        wcon.warn(msg)
                    else:
                        print(f"  {msg}")
                else:
                    raise RuntimeError(f"صفحه {page}: {message}")
                break

        rows, total_pages_api = _rows_from_result(result, page)
        chunk.append((page, rows))
        pages_in_session += 1

        if not reuse_captcha:
            break

        if total_pages_api is not None and page >= total_pages_api:
            break

        page += 1

    return chunk, total_pages_api


def worker_log(worker_id: int | None, message: str, wcon: WorkerConsole | None = None) -> None:
    if wcon is not None:
        wcon.info(message)
        return
    prefix = f"[W{worker_id}] " if worker_id is not None else ""
    safe_print(f"{prefix}{message}")


def report_worker_progress(
    shared,
    lock,
    worker_id: int | None,
    *,
    last_page: int | None = None,
    pages_done: int | None = None,
    records: int | None = None,
    status: str | None = None,
    activity: str | None = None,
    pid: int | None = None,
) -> None:
    if shared is None or lock is None or worker_id is None:
        return
    key = str(worker_id)
    with lock:
        current = dict(shared.get(key, {}))
        if last_page is not None:
            current["last_page"] = last_page
        if pages_done is not None:
            current["pages_done"] = pages_done
        if records is not None:
            current["records"] = records
        if status is not None:
            current["status"] = status
        if activity is not None:
            current["activity"] = activity
        if pid is not None:
            current["pid"] = pid
        shared[key] = current


def learn_path_for_worker(worker_id: int | None) -> Path:
    if worker_id is None:
        return CAPTCHA_LEARN_PATH
    return SCRIPT_DIR / f"captcha_learn_w{worker_id}.json"


def split_page_ranges(start: int, end: int, workers: int) -> list[tuple[int, int]]:
    if start > end or workers < 1:
        return []
    total = end - start + 1
    workers = min(workers, total)
    base, extra = divmod(total, workers)
    ranges: list[tuple[int, int]] = []
    page = start
    for index in range(workers):
        size = base + (1 if index < extra else 0)
        ranges.append((page, page + size - 1))
        page += size
    return ranges


@dataclass(frozen=True)
class ListScrapeOptions:
    config_path: str
    start_page: int | None
    end_page: int | None
    all_mode: bool
    delay: float
    retries: int
    max_pages_per_chunk: int
    manual: bool
    fast_ocr: bool
    no_chunk: bool
    with_details: bool
    no_learn: bool
    debug: bool
    worker_id: int | None = None
    use_pretty: bool = True
    live_ui: bool = True
    resume: bool = True
    reset: bool = False
    start_delay: float = 0.0
    verbose_worker: bool = False


def resolve_list_scrape_start(
    conn,
    options: ListScrapeOptions,
) -> tuple[int, int | None]:
    """Return (start_page, total_pages_api)."""
    total_pages_api: int | None = None

    if options.worker_id is not None:
        range_start = options.start_page or 1
        range_end = options.end_page
        last_done = get_worker_progress(conn, options.worker_id)
        if last_done > 0 and range_end is not None and last_done >= range_end:
            return range_end + 1, total_pages_api
        if last_done > 0:
            return max(range_start, last_done + 1), total_pages_api
        return range_start, total_pages_api

    if not options.all_mode:
        return options.start_page or 1, total_pages_api

    if options.reset:
        reset_scrape_state(conn)
        return options.start_page or 1, None

    if options.start_page is not None:
        return options.start_page, None

    if not options.resume:
        return 1, None

    state = get_scrape_state(conn)
    last_done = state.get("last_completed_page", 0)
    total_known = state.get("total_pages")
    if last_done > 0 and total_known and last_done >= total_known:
        return total_known + 1, total_known
    if last_done > 0:
        print(
            f"Resuming from page {last_done + 1} "
            f"(last completed: {last_done}"
            f"{f' / {total_known}' if total_known else ''})."
        )
        return last_done + 1, total_known
    return 1, total_known


def run_list_scrape(
    options: ListScrapeOptions,
    *,
    shared_progress=None,
    log_lock=None,
) -> dict:
    config_path = Path(options.config_path)
    app_config = load_config(config_path)
    use_pretty = options.use_pretty and options.worker_id is None
    is_worker = options.worker_id is not None
    enable_colors(True)
    ui = ScrapeConsole(enabled=use_pretty, live=use_pretty and options.live_ui)
    wcon: WorkerConsole | None = None

    if options.start_delay > 0:
        time.sleep(options.start_delay)

    debug_dir = SCRIPT_DIR / "debug_captcha" if options.debug else None
    worker_id = options.worker_id

    report_worker_progress(
        shared_progress,
        log_lock,
        worker_id,
        status="starting",
        activity="loading OCR" if not options.manual else "manual",
    )

    if options.manual:
        ocr = None
        if use_pretty:
            ui.line(paint("Manual captcha mode.", C.YELLOW))
        else:
            worker_log(worker_id, "Manual captcha mode.", wcon)
    else:
        if use_pretty:
            ui.line(paint("Loading OCR (ddddocr)...", C.DIM))
        ocr = CaptchaOcr()
        if use_pretty:
            ui.line(paint("OCR ready.", C.GREEN))

    from captcha_learn import CaptchaLearner

    learner = CaptchaLearner(
        learn_path_for_worker(worker_id),
        enabled=not options.no_learn,
    )

    client = EnamadClient()
    total_saved = 0
    pages_fetched = 0
    run_id: int | None = None
    total_pages_api: int | None = None
    stats = ScrapeStats(start_page=options.start_page or 1)

    try:
        with mysql_connection(app_config.mysql) as conn:
            start_page, total_pages_api = resolve_list_scrape_start(conn, options)
            end_page = options.end_page
            if options.all_mode and end_page is None:
                bounded = False
            else:
                bounded = True
                if end_page is None:
                    end_page = start_page

            if options.all_mode and total_pages_api and start_page > total_pages_api:
                worker_log(
                    options.worker_id,
                    f"Already complete ({total_pages_api}/{total_pages_api} pages).",
                )
                return {
                    "worker_id": options.worker_id,
                    "status": "completed",
                    "pages_fetched": 0,
                    "records_saved": 0,
                    "run_id": None,
                    "last_page": total_pages_api,
                }

            if start_page > (end_page if bounded else start_page):
                if wcon:
                    wcon.info("Range already complete.")
                else:
                    worker_log(worker_id, "Range already complete.", wcon)
                return {
                    "worker_id": worker_id,
                    "status": "completed",
                    "pages_fetched": 0,
                    "records_saved": 0,
                    "run_id": None,
                    "last_page": end_page if bounded else start_page - 1,
                }

            if is_worker and bounded and end_page is not None:
                wcon = WorkerConsole(
                    worker_id,
                    log_lock,
                    quiet=not options.verbose_worker,
                    silent=shared_progress is not None and not options.verbose_worker,
                    range_end=end_page,
                )
                if shared_progress is None:
                    wcon.info(f"range {start_page}-{end_page}")
            elif use_pretty and options.all_mode:
                ui.banner("Enamad Scraper — full run")
                ui.line(paint(f"MySQL: {app_config.mysql.database}", C.DIM))
            elif bounded and end_page is not None:
                msg = (
                    f"Scraping pages {start_page} to {end_page} "
                    f"-> MySQL ({app_config.mysql.database})"
                )
                if use_pretty:
                    ui.line(paint(msg, C.CYAN))
                else:
                    worker_log(worker_id, msg)

            page = start_page
            stats.start_page = start_page
            stats.total_pages = total_pages_api

            pages_requested = 0
            if bounded and end_page is not None:
                pages_requested = max(0, end_page - start_page + 1)

            notes = "all pages" if options.all_mode else ("manual" if options.manual else "ddddocr")
            if worker_id is not None:
                notes = f"worker {worker_id}: {notes}"

            run_id = start_scrape_run(
                conn,
                start_page=start_page,
                pages_requested=pages_requested,
                notes=notes,
            )
            commit_connection(conn)

            if use_pretty:
                ui.refresh_sticky(stats, learner.summary() if learner.enabled else "")

            report_worker_progress(
                shared_progress,
                log_lock,
                worker_id,
                status="running",
                activity="scraping",
                last_page=start_page - 1,
                pages_done=0,
                records=0,
            )

            while True:
                if total_pages_api is not None and page > total_pages_api:
                    break
                if bounded and end_page is not None and page > end_page:
                    break

                chunk_end = end_page if bounded else None
                if use_pretty:
                    ui.begin_chunk(page, total_pages_api, stats)
                elif wcon:
                    report_worker_progress(
                        shared_progress,
                        log_lock,
                        worker_id,
                        activity="captcha",
                        last_page=page,
                    )

                chunk, total_pages_api = fetch_page_chunk(
                    client,
                    page,
                    chunk_end,
                    ocr,
                    options.manual,
                    options.retries,
                    debug_dir,
                    learner=learner,
                    reuse_captcha=not options.no_chunk,
                    ui=ui if use_pretty else None,
                    stats=stats if use_pretty else None,
                    max_chunk_pages=options.max_pages_per_chunk,
                    fast_ocr=options.fast_ocr,
                    wcon=wcon,
                )
                if not chunk:
                    raise RuntimeError(f"صفحه {page}: هیچ رکوردی دریافت نشد")

                chunk_pages = [p for p, _ in chunk]
                chunk_records = 0
                last_chunk_page = chunk[-1][0]
                for chunk_page, rows in chunk:
                    if options.with_details:
                        enriched_rows = []
                        for row in rows:
                            enriched_rows.append(maybe_enrich_row(client, row, True))
                            time.sleep(max(0.0, options.delay * 0.3))
                        rows = enriched_rows

                    saved = save_domains(conn, rows, scrape_run_id=run_id)
                    total_saved += saved
                    pages_fetched += 1
                    chunk_records += len(rows)

                    if total_pages_api is not None and chunk_page >= total_pages_api:
                        if wcon:
                            wcon.info(f"Reached last available page ({total_pages_api}).")
                        page = chunk_page + 1
                        break

                    if bounded and end_page is not None and chunk_page >= end_page:
                        page = chunk_page + 1
                        break
                else:
                    page = last_chunk_page + 1

                if options.worker_id is not None:
                    update_worker_progress(conn, options.worker_id, last_chunk_page)
                elif options.all_mode:
                    update_scrape_progress(conn, last_chunk_page, total_pages_api)
                commit_connection(conn)

                if wcon and shared_progress is None:
                    wcon.chunk_done(chunk_pages, chunk_records, total_saved)
                lo, hi = min(chunk_pages), max(chunk_pages)
                report_worker_progress(
                    shared_progress,
                    log_lock,
                    worker_id,
                    last_page=last_chunk_page,
                    pages_done=pages_fetched,
                    records=total_saved,
                    status="running",
                    activity=f"chunk {lo}-{hi}",
                )

                stats.total_pages = total_pages_api
                stats.note_chunk(chunk_pages, chunk_records)
                if use_pretty:
                    learner_line = learner.summary() if learner.enabled else ""
                    ui.end_chunk(chunk_pages, chunk_records, total_saved, stats, learner_line)

                if total_pages_api is not None and page > total_pages_api:
                    break
                if bounded and end_page is not None and page > end_page:
                    break

                time.sleep(max(0.0, options.delay))

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
        if worker_id is not None:
            report_worker_progress(
                shared_progress,
                log_lock,
                worker_id,
                status="failed",
                activity="failed",
            )
            if wcon:
                wcon.error(str(exc))
            return {
                "worker_id": worker_id,
                "status": "failed",
                "pages_fetched": pages_fetched,
                "records_saved": total_saved,
                "run_id": run_id,
                "last_page": page if "page" in locals() else options.start_page,
                "error": str(exc),
                "pid": os.getpid(),
            }
        raise
    finally:
        cleanup_captcha_images()
        if learner.enabled:
            learner.save()

    if use_pretty:
        ui.done(total_saved, run_id)
        if learner.enabled:
            ui.line(paint(f"[learn] {learner.summary()}", C.MAGENTA, C.DIM))
    else:
        if wcon:
            wcon.success(f"Done — {total_saved} records (run_id={run_id})")
        else:
            print(f"Done. {total_saved} records stored in MySQL (run_id={run_id}).")
        if learner.enabled and not is_worker:
            print(learner.summary())

    report_worker_progress(
        shared_progress,
        log_lock,
        worker_id,
        status="completed",
        activity="done",
        pages_done=pages_fetched,
        records=total_saved,
    )

    return {
        "worker_id": worker_id,
        "status": "completed",
        "pages_fetched": pages_fetched,
        "records_saved": total_saved,
        "run_id": run_id,
        "last_page": last_chunk_page if "last_chunk_page" in locals() else start_page,
        "pid": os.getpid(),
    }


def _parallel_worker(payload: dict) -> dict:
    configure_console_encoding()
    shared_progress = payload.pop("shared_progress", None)
    log_lock = payload.pop("log_lock", None)
    options = ListScrapeOptions(**payload)
    worker_pid = os.getpid()
    report_worker_progress(
        shared_progress,
        log_lock,
        options.worker_id,
        status="spawned",
        activity="loading OCR",
        pid=worker_pid,
    )
    result = run_list_scrape(
        options,
        shared_progress=shared_progress,
        log_lock=log_lock,
    )
    result["pid"] = worker_pid
    return result


def run_parallel_scrape(
    args: argparse.Namespace,
    app_config,
    config_path: Path,
    max_pages_per_chunk: int,
) -> int:
    if args.manual:
        print("Parallel mode does not support --manual.", file=sys.stderr)
        return 1
    if args.with_details:
        print("Parallel mode does not support --with-details.", file=sys.stderr)
        return 1
    if not args.all and args.end_page is None:
        print("Parallel mode requires --all or --end-page.", file=sys.stderr)
        return 1

    delay = args.delay if args.delay is not None else app_config.scraper.delay
    retries = args.retries if args.retries is not None else app_config.scraper.retries
    workers = args.workers

    with mysql_connection(app_config.mysql) as conn:
        if args.reset:
            reset_scrape_state(conn)
            commit_connection(conn)
            print("Progress reset. Starting parallel scrape from page 1.")

        state = get_scrape_state(conn)
        total_pages = state.get("total_pages")
        start_page = args.start_page or 1

        if args.all:
            if args.start_page is None and not args.reset:
                last_done = state.get("last_completed_page", 0)
                if last_done > 0:
                    start_page = last_done + 1
            if total_pages and start_page > total_pages:
                print(f"Already complete ({total_pages}/{total_pages} pages).")
                return 0
            end_page = total_pages
            if end_page is None:
                print(
                    "Total page count unknown. Run a single-worker scrape first "
                    "to discover total_pages, or pass --end-page.",
                    file=sys.stderr,
                )
                return 1
        else:
            end_page = args.end_page
            if end_page is None or end_page < start_page:
                print("Invalid page range for parallel scrape.", file=sys.stderr)
                return 1

    ranges = split_page_ranges(start_page, end_page, workers)
    if not ranges:
        print("Nothing to scrape.", file=sys.stderr)
        return 1

    enable_colors(True)
    worker_count = len(ranges)
    ParallelDashboard.print_plan(
        start_page,
        end_page,
        ranges,
        worker_count=worker_count,
    )

    manager = Manager()
    shared = manager.dict()
    log_lock = manager.Lock()
    active_ranges: list[tuple[int, int, int]] = []
    payloads: list[dict] = []

    for index, (lo, hi) in enumerate(ranges):
        worker_last = 0
        with mysql_connection(app_config.mysql) as conn:
            worker_last = get_worker_progress(conn, index)
        worker_start = max(lo, worker_last + 1) if worker_last > 0 else lo
        if worker_start > hi:
            print(paint(f"  W{index} already complete ({lo}-{hi})", C.DIM))
            continue

        active_ranges.append((index, lo, hi))
        shared[str(index)] = {
            "range_lo": lo,
            "range_hi": hi,
            "last_page": worker_start - 1,
            "pages_done": 0,
            "records": 0,
            "status": "pending",
            "activity": "queued",
        }

        options = ListScrapeOptions(
            config_path=str(config_path),
            start_page=worker_start,
            end_page=hi,
            all_mode=False,
            delay=delay,
            retries=retries,
            max_pages_per_chunk=max_pages_per_chunk,
            manual=False,
            fast_ocr=args.fast_ocr,
            no_chunk=args.no_chunk,
            with_details=False,
            no_learn=args.no_learn,
            debug=args.debug,
            worker_id=index,
            use_pretty=False,
            live_ui=False,
            resume=False,
            reset=False,
            start_delay=index * max(0.0, args.worker_stagger),
            verbose_worker=args.verbose_workers,
        )
        payload = dict(options.__dict__)
        payload["shared_progress"] = shared
        payload["log_lock"] = log_lock
        payloads.append(payload)

    if not payloads:
        print("All worker ranges already complete.")
        if args.all and total_pages:
            with mysql_connection(app_config.mysql) as conn:
                update_scrape_progress(conn, total_pages, total_pages)
        return 0

    dashboard = ParallelDashboard(
        active_ranges,
        shared,
        log_lock,
        global_start=start_page,
        global_end=end_page,
    )
    stop_refresh = threading.Event()

    def refresh_dashboard() -> None:
        while not stop_refresh.wait(1.5):
            dashboard.render()

    refresh_thread = threading.Thread(target=refresh_dashboard, daemon=True)
    refresh_thread.start()
    dashboard.render()

    results: list[dict] = []
    failed = False
    try:
        with ProcessPoolExecutor(max_workers=len(payloads)) as pool:
            futures = {pool.submit(_parallel_worker, payload): payload for payload in payloads}
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                if result.get("status") != "completed":
                    failed = True
    finally:
        stop_refresh.set()
        refresh_thread.join(timeout=3.0)
        dashboard.finish(results)

    total_pages_fetched = sum(int(item.get("pages_fetched") or 0) for item in results)
    total_records = sum(int(item.get("records_saved") or 0) for item in results)

    if not failed and args.all and end_page is not None:
        with mysql_connection(app_config.mysql) as conn:
            update_scrape_progress(conn, end_page, end_page)

    print(
        paint(
            f"\nParallel done. {len(results)} workers, {total_pages_fetched} pages, "
            f"{total_records} records.",
            C.GREEN,
            C.BOLD,
        )
    )
    return 1 if failed else 0


def discover_total_pages(retries: int, fast_ocr: bool) -> int | None:
    """Solve one captcha on page 1 to read the current total page count."""
    from captcha_learn import CaptchaLearner

    ocr = CaptchaOcr()
    learner = CaptchaLearner(learn_path_for_worker(None), enabled=True)
    client = EnamadClient()
    try:
        _, _, result = _solve_captcha_for_page(
            client,
            1,
            ocr,
            False,
            retries,
            None,
            learner,
            fast_ocr=fast_ocr,
        )
    finally:
        if learner.enabled:
            learner.save()
        cleanup_captcha_images()
    _, total_pages = _rows_from_result(result, 1)
    return total_pages


def run_update(
    args: argparse.Namespace,
    app_config,
    config_path: Path,
    max_pages_per_chunk: int,
) -> int:
    """Incrementally fetch only newly-added pages at the tail of the list.

    New Enamad approvals are appended at the end of the list, so we only need to
    re-scrape the last known pages (plus an overlap) up to the new total.
    """
    delay = args.delay if args.delay is not None else app_config.scraper.delay
    retries = args.retries if args.retries is not None else app_config.scraper.retries
    overlap = max(0, args.update_overlap)

    with mysql_connection(app_config.mysql) as conn:
        state = get_scrape_state(conn)
    old_total = state.get("total_pages")

    print(paint("Discovering current total pages (1 captcha)...", C.CYAN))
    current_total = discover_total_pages(retries, args.fast_ocr)
    if not current_total or current_total < 1:
        print("Could not determine total pages from Enamad.", file=sys.stderr)
        return 1

    base = old_total if old_total else current_total
    start_page = max(1, min(base, current_total) - overlap + 1)
    end_page = current_total

    new_pages = max(0, current_total - old_total) if old_total else 0
    print(
        paint(
            f"Total pages: {fmt_int(old_total) if old_total else '?'} -> {fmt_int(current_total)}"
            f"  ({new_pages} new)  scanning pages {fmt_int(start_page)}-{fmt_int(end_page)}"
            f" (overlap {overlap})",
            C.WHITE,
        )
    )

    options = ListScrapeOptions(
        config_path=str(config_path),
        start_page=start_page,
        end_page=end_page,
        all_mode=False,
        delay=delay,
        retries=retries,
        max_pages_per_chunk=max_pages_per_chunk,
        manual=False,
        fast_ocr=args.fast_ocr,
        no_chunk=args.no_chunk,
        with_details=False,
        no_learn=args.no_learn,
        debug=args.debug,
        use_pretty=not args.plain,
        live_ui=not args.plain and not args.no_live,
        resume=False,
        reset=False,
    )
    result = run_list_scrape(options)

    with mysql_connection(app_config.mysql) as conn:
        update_scrape_progress(conn, current_total, current_total)
        commit_connection(conn)

    saved = int(result.get("records_saved") or 0) if isinstance(result, dict) else 0
    print(paint(f"Update done. {fmt_int(saved)} records touched.", C.GREEN, C.BOLD))
    return 0


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
        "--fix-domains",
        action="store_true",
        help="Decode URL-encoded domains already stored in MySQL (e.g. %%D9%%BE...)",
    )
    parser.add_argument(
        "--refresh-services",
        metavar="DOMAIN",
        nargs="?",
        const="__all__",
        help="Re-fetch trust seal and update all services in DB (optional: one domain)",
    )
    parser.add_argument(
        "--refresh-limit",
        type=int,
        default=None,
        help="With --refresh-services/--refresh-stale: max domains to refresh",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Incremental: fetch only newly-added tail pages up to the new total",
    )
    parser.add_argument(
        "--update-overlap",
        type=int,
        default=5,
        help="With --update: re-scan this many pages before the old total (default: 5)",
    )
    parser.add_argument(
        "--refresh-stale",
        action="store_true",
        help="Refresh domains not updated recently via trust seal (no captcha)",
    )
    parser.add_argument(
        "--stale-days",
        type=int,
        default=30,
        help="With --refresh-stale: refresh domains older than N days (default: 30)",
    )
    parser.add_argument(
        "--refresh-workers",
        type=int,
        default=1,
        help="With --refresh-stale: parallel worker threads (default: 1, no captcha)",
    )
    parser.add_argument(
        "--missing-only",
        action="store_true",
        help="With --refresh-stale: only refresh domains missing address/phone/email",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Fetch all pages until the end of the list",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=None,
        help="Number of pages to fetch (default: 1, ignored with --all)",
    )
    parser.add_argument(
        "--start-page",
        type=int,
        default=None,
        help="Start from page N (overrides auto-resume)",
    )
    parser.add_argument(
        "--end-page",
        type=int,
        default=None,
        help="Stop after page N (for parallel workers or bounded runs)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of parallel page-range workers (requires --all or --end-page)",
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
        "--reset",
        action="store_true",
        help="With --all: clear saved progress and start from page 1",
    )
    parser.add_argument(
        "--search",
        metavar="DOMAIN",
        help="Look up one domain via enamad.ir/Home/GetData (site search API)",
    )
    parser.add_argument(
        "--search-file",
        metavar="FILE",
        help="Text file with one domain per line to look up",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="With --search: print only, do not write to MySQL",
    )
    parser.add_argument(
        "--no-details",
        action="store_true",
        help="With --search: skip trust seal page (address, phone, email)",
    )
    parser.add_argument(
        "--with-details",
        action="store_true",
        help="With --pages/--all: fetch trust seal contact info per record (slow)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="With --search: output JSON",
    )
    parser.add_argument(
        "--no-live",
        action="store_true",
        help="Keep all log lines (disable clearing transient output)",
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        help="Plain log output without colors or stats panels",
    )
    parser.add_argument(
        "--no-chunk",
        action="store_true",
        help="Fetch one page per captcha (disable captcha reuse across pages)",
    )
    parser.add_argument(
        "--chunk-pages",
        type=int,
        default=None,
        help=f"Pages per captcha when reusing (default: {DEFAULT_CHUNK_PAGES})",
    )
    parser.add_argument(
        "--fast-ocr",
        action="store_true",
        help="Lighter captcha OCR (fewer image variants, faster but slightly less accurate)",
    )
    parser.add_argument(
        "--no-learn",
        action="store_true",
        help="Disable captcha learning cache (default: learn from solved captchas)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Save captcha images to debug_captcha/",
    )
    parser.add_argument(
        "--verbose-workers",
        action="store_true",
        help="With --workers: show captcha/reuse logs from each worker",
    )
    parser.add_argument(
        "--worker-stagger",
        type=float,
        default=0.0,
        help="Delay between worker starts in seconds (default: 0)",
    )
    return parser.parse_args()


def main() -> int:
    configure_console_encoding()
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

    if args.fix_domains:
        app_config = load_config(config_path)
        with mysql_connection(app_config.mysql) as conn:
            fixed = fix_encoded_domains(conn)
        print(f"Fixed {fixed} URL-encoded domain(s) in {app_config.mysql.database}.")
        return 0

    if args.refresh_services:
        app_config = load_config(config_path)
        domain = None if args.refresh_services == "__all__" else args.refresh_services
        delay = args.delay if args.delay is not None else app_config.scraper.delay
        with mysql_connection(app_config.mysql) as conn:
            ok, failed = refresh_domain_services(
                conn,
                domain=domain,
                limit=args.refresh_limit,
                delay=delay,
                progress=True,
            )
            commit_connection(conn)
        scope = domain or "all domains"
        print(f"Refreshed services for {scope}: {ok} ok, {failed} failed.")
        return 0

    if args.refresh_stale:
        app_config = load_config(config_path)
        limit = args.refresh_limit if args.refresh_limit is not None else 500
        refresh_workers = max(1, args.refresh_workers)
        if refresh_workers > 1:
            delay = args.delay if args.delay is not None else 0.0
            candidates, ok, failed = refresh_stale_domains_parallel(
                app_config.mysql,
                days=args.stale_days,
                limit=limit,
                workers=refresh_workers,
                delay=delay,
                progress=True,
                missing_only=args.missing_only,
            )
        else:
            delay = args.delay if args.delay is not None else 0.3
            with mysql_connection(app_config.mysql) as conn:
                candidates, ok, failed = refresh_stale_domains(
                    conn,
                    days=args.stale_days,
                    limit=limit,
                    delay=delay,
                    progress=True,
                    missing_only=args.missing_only,
                )
                commit_connection(conn)
        if args.missing_only:
            scope = "missing details"
        else:
            scope = "all domains" if args.stale_days <= 0 else f">{args.stale_days}d old"
        print(
            f"Done. Stale refresh ({scope}): {candidates:,} processed, "
            f"{ok:,} ok, {failed:,} failed."
        )
        return 0

    app_config = load_config(config_path)

    if args.search or args.search_file:
        return run_search(args, app_config)

    if args.workers < 1:
        print("workers must be at least 1.", file=sys.stderr)
        return 1

    chunk_pages = args.chunk_pages if args.chunk_pages is not None else DEFAULT_CHUNK_PAGES
    if chunk_pages < 1:
        print("chunk-pages must be at least 1.", file=sys.stderr)
        return 1
    max_pages_per_chunk = chunk_pages

    if args.update:
        return run_update(args, app_config, config_path, max_pages_per_chunk)

    if args.workers > 1:
        return run_parallel_scrape(args, app_config, config_path, max_pages_per_chunk)

    delay = args.delay if args.delay is not None else app_config.scraper.delay
    retries = args.retries if args.retries is not None else app_config.scraper.retries

    if args.all:
        pages_to_fetch = 0
        end_page = args.end_page
        all_mode = True
    else:
        pages_to_fetch = args.pages if args.pages is not None else 1
        if pages_to_fetch < 1:
            print("Page count must be at least 1.", file=sys.stderr)
            return 1
        start_page = args.start_page or 1
        end_page = args.end_page if args.end_page is not None else start_page + pages_to_fetch - 1
        all_mode = False

    options = ListScrapeOptions(
        config_path=str(config_path),
        start_page=args.start_page,
        end_page=end_page if not args.all else args.end_page,
        all_mode=all_mode,
        delay=delay,
        retries=retries,
        max_pages_per_chunk=max_pages_per_chunk,
        manual=args.manual,
        fast_ocr=args.fast_ocr,
        no_chunk=args.no_chunk,
        with_details=args.with_details,
        no_learn=args.no_learn,
        debug=args.debug,
        use_pretty=not args.plain,
        live_ui=not args.plain and not args.no_live,
        resume=True,
        reset=args.reset,
    )
    run_list_scrape(options)
    return 0


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    raise SystemExit(main())
