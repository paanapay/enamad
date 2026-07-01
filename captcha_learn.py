from __future__ import annotations

import io
import json
import re
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

CAPTCHA_LEN = 5
STORE_VERSION = 1
MAX_IMAGE_CACHE = 5000
MIN_CONFUSION_COUNT = 4
ROLLING_WINDOW = 50


def re_sub_alnum(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "", text or "").strip()


class CaptchaLearner:
    def __init__(self, store_path: Path, enabled: bool = True) -> None:
        self.store_path = store_path
        self.enabled = enabled
        self.data = self._load()
        self._dirty = False

    def _empty_store(self) -> dict:
        return {
            "version": STORE_VERSION,
            "stats": {
                "total_success": 0,
                "first_guess_success": 0,
                "recent_first_guess": [],
            },
            "char_confusions": {},
            "image_cache": {},
        }

    def _load(self) -> dict:
        if not self.enabled or not self.store_path.is_file():
            return self._empty_store()
        try:
            raw = json.loads(self.store_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._empty_store()
        if not isinstance(raw, dict):
            return self._empty_store()
        raw.setdefault("version", STORE_VERSION)
        raw.setdefault(
            "stats",
            {"total_success": 0, "first_guess_success": 0, "recent_first_guess": []},
        )
        raw.setdefault("char_confusions", {})
        raw.setdefault("image_cache", {})
        return raw

    def save(self) -> None:
        if not self.enabled or not self._dirty:
            return
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.store_path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._dirty = False

    @staticmethod
    def fingerprint(image_bytes: bytes) -> str:
        image = Image.open(io.BytesIO(image_bytes)).convert("L")
        small = image.resize((16, 16), Image.Resampling.LANCZOS)
        arr = np.array(small, dtype=np.float32)
        threshold = float(arr.mean())
        bits = (arr > threshold).astype(np.uint8).flatten()
        return "".join("1" if bit else "0" for bit in bits)

    def lookup_solution(self, image_bytes: bytes) -> str | None:
        """Only exact image match — fuzzy match caused wrong answers to be tried first."""
        if not self.enabled:
            return None

        fingerprint = self.fingerprint(image_bytes)
        entry = self.data.get("image_cache", {}).get(fingerprint)
        if not isinstance(entry, dict):
            return None
        solution = str(entry.get("solution", "")).strip().lower()
        if len(solution) == CAPTCHA_LEN:
            return solution
        return None

    def _confusion_map(self) -> dict[str, list[tuple[str, int]]]:
        by_from: dict[str, list[tuple[str, int]]] = defaultdict(list)
        for key, count in self.data.get("char_confusions", {}).items():
            if int(count) < MIN_CONFUSION_COUNT:
                continue
            if ":" not in key:
                continue
            src, dst = key.split(":", 1)
            if len(src) != 1 or len(dst) != 1:
                continue
            by_from[src].append((dst, int(count)))
        for src in by_from:
            by_from[src].sort(key=lambda item: (-item[1], item[0]))
        return by_from

    def expand_candidates(self, candidates: list[str], image_bytes: bytes) -> list[str]:
        if not self.enabled:
            return candidates

        ordered: list[str] = []
        seen: set[str] = set()

        def add(code: str) -> None:
            cleaned = re_sub_alnum(code)
            if len(cleaned) != CAPTCHA_LEN:
                return
            for variant in (cleaned.lower(), cleaned, cleaned.upper()):
                if variant not in seen:
                    seen.add(variant)
                    ordered.append(variant)

        # OCR ranking first — never let learning push a guess ahead of OCR top picks
        for candidate in candidates:
            add(candidate)

        cached = self.lookup_solution(image_bytes)
        if cached:
            add(cached)

        by_from = self._confusion_map()
        if by_from:
            seeds = [re_sub_alnum(c).lower() for c in candidates[:4]]
            for seed in seeds:
                if len(seed) != CAPTCHA_LEN:
                    continue
                for variant in self._mutate_by_confusion(seed, by_from):
                    add(variant)

        return ordered[:15]

    @staticmethod
    def _mutate_by_confusion(code: str, by_from: dict[str, list[tuple[str, int]]]) -> list[str]:
        variants: list[str] = []
        seen: set[str] = {code}
        for index, char in enumerate(code):
            for replacement, _count in by_from.get(char, [])[:4]:
                if replacement == char:
                    continue
                variant = code[:index] + replacement + code[index + 1 :]
                if variant not in seen:
                    seen.add(variant)
                    variants.append(variant)
        return variants

    def record_success(
        self,
        image_bytes: bytes,
        solution: str,
        failed_attempts: list[str],
        ocr_candidates: list[str],
    ) -> None:
        if not self.enabled:
            return

        answer = re_sub_alnum(solution).lower()
        if len(answer) != CAPTCHA_LEN:
            return

        stats = self.data.setdefault("stats", {})
        stats["total_success"] = int(stats.get("total_success", 0)) + 1
        first_try = not failed_attempts
        if first_try:
            stats["first_guess_success"] = int(stats.get("first_guess_success", 0)) + 1

        recent: list = stats.setdefault("recent_first_guess", [])
        recent.append(1 if first_try else 0)
        if len(recent) > ROLLING_WINDOW:
            del recent[: len(recent) - ROLLING_WINDOW]

        confusions = self.data.setdefault("char_confusions", {})
        for failed in failed_attempts:
            guess = re_sub_alnum(failed).lower()
            if len(guess) != CAPTCHA_LEN:
                continue
            if sum(a != b for a, b in zip(guess, answer)) != 1:
                continue
            for wrong, right in zip(guess, answer):
                if wrong != right:
                    key = f"{wrong}:{right}"
                    confusions[key] = int(confusions.get(key, 0)) + 1

        if answer not in {re_sub_alnum(c).lower() for c in ocr_candidates[:3]}:
            for candidate in ocr_candidates:
                guess = re_sub_alnum(candidate).lower()
                if len(guess) != CAPTCHA_LEN:
                    continue
                distance = sum(a != b for a, b in zip(guess, answer))
                if distance == 1:
                    for wrong, right in zip(guess, answer):
                        if wrong != right:
                            key = f"{wrong}:{right}"
                            confusions[key] = int(confusions.get(key, 0)) + 2

        fingerprint = self.fingerprint(image_bytes)
        cache = self.data.setdefault("image_cache", {})
        previous = cache.get(fingerprint, {})
        cache[fingerprint] = {
            "solution": answer,
            "hits": int(previous.get("hits", 0)) + 1 if isinstance(previous, dict) else 1,
        }
        if len(cache) > MAX_IMAGE_CACHE:
            trim = len(cache) - MAX_IMAGE_CACHE
            for old_key in list(cache.keys())[:trim]:
                cache.pop(old_key, None)

        self._dirty = True
        self.save()

    def summary(self) -> str:
        stats = self.data.get("stats", {})
        total = int(stats.get("total_success", 0))
        first = int(stats.get("first_guess_success", 0))
        confusions = sum(
            1 for count in self.data.get("char_confusions", {}).values()
            if int(count) >= MIN_CONFUSION_COUNT
        )
        cached = len(self.data.get("image_cache", {}))
        recent = stats.get("recent_first_guess", [])
        if total <= 0:
            return f"learning: {cached} cached images, {confusions} strong char rules"
        rate = round(first * 100 / total)
        if len(recent) >= 10:
            recent_rate = round(sum(recent) * 100 / len(recent))
            return (
                f"learning: {total} solved ({rate}% first-guess overall, "
                f"{recent_rate}% last-{len(recent)}), {confusions} strong rules"
            )
        return f"learning: {total} solved ({rate}% first-guess), {confusions} strong rules, {cached} images"
