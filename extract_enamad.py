#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
استخراج فهرست دارندگان اینماد
هر صفحه نیاز به کپچای جدید دارد — با ddddocr خودکار حل می‌شود.

نصب:
  C:\\laragon\\bin\\python\\python-3.13\\python.exe -m pip install -r requirements.txt

اجرا:
  python extract_enamad.py --pages 2
  python extract_enamad.py --pages 5 --output domains.csv
  python extract_enamad.py --pages 2 --manual
"""

from __future__ import annotations

import argparse
import base64
import csv
import io
import json
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

CAPTCHA_MIN_LEN = 4
CAPTCHA_MAX_LEN = 6

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
    def _is_plausible(code: str) -> bool:
        return CAPTCHA_MIN_LEN <= len(code) <= CAPTCHA_MAX_LEN

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

    def _preprocess_variants(self, image_bytes: bytes) -> list[bytes]:
        variants: list[bytes] = [image_bytes]
        base = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        width, height = base.size

        scales = (2, 3)
        for scale in scales:
            big = base.resize((width * scale, height * scale), Image.Resampling.LANCZOS)
            variants.append(self._to_bytes(big))

        cropped = base.crop((0, 0, width, int(height * 0.72)))
        variants.append(self._to_bytes(cropped.resize((width * 3, int(height * 0.72 * 3)), Image.Resampling.LANCZOS)))

        gray = ImageOps.grayscale(base)
        gray = ImageEnhance.Contrast(gray).enhance(2.5)
        gray = gray.point(lambda px: 255 if px > 145 else 0)
        variants.append(self._to_bytes(gray.resize((width * 3, height * 3), Image.Resampling.NEAREST)))

        arr = np.array(base)
        cv_gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        cv_gray = cv2.resize(cv_gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        _, otsu = cv2.threshold(cv_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        variants.append(self._to_bytes_cv(otsu))

        adaptive = cv2.adaptiveThreshold(
            cv_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 8
        )
        variants.append(self._to_bytes_cv(adaptive))

        return variants

    def read(self, image_bytes: bytes) -> str:
        candidates = self.read_candidates(image_bytes)
        return candidates[0] if candidates else ""

    def read_candidates(self, image_bytes: bytes) -> list[str]:
        votes: Counter[str] = Counter()

        for variant in self._preprocess_variants(image_bytes):
            raw = CaptchaOcr._engine.classification(variant)
            code = self._sanitize(raw)
            if not self._is_plausible(code):
                continue
            votes[code] += 2
            votes[code.lower()] += 1
            votes[code.upper()] += 1

        if not votes:
            return []

        ranked = sorted(votes.items(), key=lambda item: (-item[1], -len(item[0])))
        ordered: list[str] = []
        for code, _score in ranked:
            for variant in (code.lower(), code, code.upper()):
                if self._is_plausible(variant) and variant not in ordered:
                    ordered.append(variant)
        return ordered


def captcha_submit_variants(code: str) -> list[str]:
    ordered: list[str] = []
    for variant in (code.lower(), code, code.upper()):
        if CAPTCHA_MIN_LEN <= len(variant) <= CAPTCHA_MAX_LEN and variant not in ordered:
            ordered.append(variant)
    return ordered


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


def normalize_row(item: dict, row_number: int) -> dict:
    domain_id = item.get("id", "")
    code = item.get("code", "")
    trustseal = ""
    if domain_id and code:
        trustseal = f"https://trustseal.enamad.ir/?id={domain_id}&code={code}"

    return {
        "row": row_number,
        "id": domain_id,
        "code": code,
        "domain": item.get("domain_address", ""),
        "business_name": item.get("persian_name", "") or "",
        "province": item.get("province", "") or "",
        "city": item.get("city", "") or "",
        "rating": item.get("rating", 0),
        "approve_date": item.get("approve_date", "") or "",
        "expire_date": item.get("expire_date", "") or "",
        "trustseal_url": trustseal,
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
                        normalize_row(item, ((page - 1) * PAGE_SIZE) + index + 1)
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


def save_csv(rows: list[dict], path: Path) -> None:
    if not rows:
        raise RuntimeError("داده‌ای برای ذخیره نیست.")
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def save_json(rows: list[dict], path: Path) -> None:
    path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="استخراج فهرست دارندگان اینماد")
    parser.add_argument(
        "--pages",
        type=int,
        default=1,
        help="تعداد صفحه برای دریافت (پیش‌فرض: 1)",
    )
    parser.add_argument(
        "--start-page",
        type=int,
        default=1,
        help="شروع از صفحه N (پیش‌فرض: 1)",
    )
    parser.add_argument(
        "--output",
        default="enamad_domains.csv",
        help="فایل خروجی",
    )
    parser.add_argument(
        "--format",
        choices=("csv", "json"),
        default="csv",
        help="فرمت خروجی",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="تأخیر بین صفحات (ثانیه)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=5,
        help="حداکثر تلاش کپچای جدید برای هر صفحه (پیش‌فرض: 5)",
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="ورود دستی کپچا به‌جای OCR",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="ذخیره تصاویر کپچا در پوشه debug_captcha",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.pages < 1:
        print("تعداد صفحات باید حداقل 1 باشد.", file=sys.stderr)
        return 1

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = SCRIPT_DIR / output_path

    if args.format == "json" and output_path.suffix.lower() != ".json":
        output_path = output_path.with_suffix(".json")

    debug_dir = SCRIPT_DIR / "debug_captcha" if args.debug else None

    if args.manual:
        ocr = None
        print("حالت دستی: کپچا را خودتان وارد می‌کنید.")
    else:
        print("در حال بارگذاری OCR (ddddocr)...")
        ocr = CaptchaOcr()
        print("OCR آماده است.")

    client = EnamadClient()

    all_rows: list[dict] = []
    total_pages = None
    end_page = args.start_page + args.pages - 1

    print(f"شروع استخراج: صفحه {args.start_page} تا {end_page}")

    for page in range(args.start_page, end_page + 1):
        print(f"صفحه {page}...")
        rows, total_pages = fetch_page(
            client,
            page,
            ocr,
            args.manual,
            args.retries,
            debug_dir,
        )
        all_rows.extend(rows)
        print(f"  → {len(rows)} رکورد (جمع: {len(all_rows)})")

        if total_pages is not None and page >= total_pages:
            print(f"به آخرین صفحه ({total_pages}) رسیدیم.")
            break

        if page < end_page:
            time.sleep(max(0.0, args.delay))

    if args.format == "json":
        save_json(all_rows, output_path)
    else:
        save_csv(all_rows, output_path)

    print(f"انجام شد. {len(all_rows)} رکورد در {output_path} ذخیره شد.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
