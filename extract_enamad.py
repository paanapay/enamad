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
from collections import Counter
from html import unescape
from pathlib import Path

import certifi
import cv2
import numpy as np
import requests
from PIL import Image, ImageEnhance, ImageOps
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from db import (
    commit_connection,
    finish_scrape_run,
    get_scrape_state,
    init_database,
    load_config,
    mysql_connection,
    reset_scrape_state,
    save_domains,
    start_scrape_run,
    update_scrape_progress,
)

CAPTCHA_LEN = 5  # اینماد تقریباً همیشه ۵ کاراکتر

BASE_URL = "https://enamad.ir/"
TRUSTSEAL_URL = "https://trustseal.enamad.ir/"
PAGE_SIZE = 30
SCRIPT_DIR = Path(__file__).resolve().parent


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
        for attempt in range(1, 4):
            try:
                response = self.session.post(
                    TRUSTSEAL_URL,
                    data={"id": str(domain_id), "Code": code},
                    headers={"Referer": BASE_URL},
                    timeout=90,
                )
                response.raise_for_status()
                return response.text
            except (
                requests.exceptions.SSLError,
                requests.exceptions.ConnectionError,
                requests.exceptions.HTTPError,
            ) as exc:
                last_error = exc
                wait = attempt * 2
                print(f"  trustseal error (attempt {attempt}/3), waiting {wait}s...")
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
        row_num = int(cleaned[0]) if cleaned[0].isdigit() else len(services) + 1
        services.append(
            {
                "row_num": row_num,
                "service_title": cleaned[1],
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
    value = domain.strip().lower()
    for prefix in ("https://www.", "http://www.", "https://", "http://", "www."):
        if value.startswith(prefix):
            value = value[len(prefix) :]
    return value.split("/")[0].split("?")[0].split("#")[0]


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
        "domain": data.get("domain_address") or queried_domain,
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
        "--debug",
        action="store_true",
        help="Save captcha images to debug_captcha/",
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

    app_config = load_config(config_path)

    if args.search or args.search_file:
        return run_search(args, app_config)

    delay = args.delay if args.delay is not None else app_config.scraper.delay
    retries = args.retries if args.retries is not None else app_config.scraper.retries

    if args.all:
        pages_to_fetch = 0
    else:
        pages_to_fetch = args.pages if args.pages is not None else 1
        if pages_to_fetch < 1:
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
    total_saved = 0
    pages_fetched = 0
    run_id: int | None = None
    total_pages_api: int | None = None
    start_page = args.start_page or 1

    try:
        with mysql_connection(app_config.mysql) as conn:
            if args.all:
                if args.reset:
                    reset_scrape_state(conn)
                    commit_connection(conn)
                    print("Progress reset. Starting from page 1.")
                    start_page = args.start_page or 1
                elif args.start_page is not None:
                    start_page = args.start_page
                    print(f"Starting from page {start_page} (--start-page).")
                else:
                    state = get_scrape_state(conn)
                    last_done = state.get("last_completed_page", 0)
                    total_known = state.get("total_pages")
                    if last_done > 0 and total_known and last_done >= total_known:
                        print(
                            f"Already complete ({last_done}/{total_known} pages). "
                            "Use --reset to scrape from the beginning."
                        )
                        return 0
                    if last_done > 0:
                        start_page = last_done + 1
                        total_pages_api = total_known
                        print(
                            f"Resuming from page {start_page} "
                            f"(last completed: {last_done}"
                            f"{f' / {total_known}' if total_known else ''})."
                        )
                    else:
                        start_page = 1
                        print("No saved progress. Starting from page 1.")

            end_page = None if args.all else start_page + pages_to_fetch - 1

            if args.all:
                print(f"Scraping ALL pages from {start_page} -> MySQL ({app_config.mysql.database})")
            else:
                print(f"Scraping pages {start_page} to {end_page} -> MySQL ({app_config.mysql.database})")

            page = start_page

            run_id = start_scrape_run(
                conn,
                start_page=start_page,
                pages_requested=pages_to_fetch,
                notes="all pages" if args.all else ("manual" if args.manual else "ddddocr"),
            )
            commit_connection(conn)

            while True:
                if total_pages_api is not None and page > total_pages_api:
                    break
                if not args.all and end_page is not None and page > end_page:
                    break

                label = f"Page {page}"
                if total_pages_api:
                    label += f" / {total_pages_api}"
                print(f"{label}...")

                rows, total_pages_api = fetch_page(
                    client,
                    page,
                    ocr,
                    args.manual,
                    retries,
                    debug_dir,
                )
                if args.with_details:
                    enriched_rows = []
                    for row in rows:
                        enriched_rows.append(maybe_enrich_row(client, row, True))
                        time.sleep(max(0.0, delay * 0.3))
                    rows = enriched_rows
                saved = save_domains(conn, rows, scrape_run_id=run_id)
                total_saved += saved
                pages_fetched += 1

                if args.all:
                    update_scrape_progress(conn, page, total_pages_api)

                commit_connection(conn)
                print(f"  -> {len(rows)} records saved (total: {total_saved}, committed)")

                if total_pages_api is not None and page >= total_pages_api:
                    print(f"Reached last available page ({total_pages_api}).")
                    break

                if not args.all and end_page is not None and page >= end_page:
                    break

                page += 1
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
    finally:
        cleanup_captcha_images()

    print(f"Done. {total_saved} records stored in MySQL (run_id={run_id}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
