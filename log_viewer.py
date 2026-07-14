"""Read application log files for the admin panel (safe path checks)."""
from __future__ import annotations

import os
import re
from typing import Any

from logging_setup import LOG_DIR, LOG_FILE

LOG_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) "
    r"\[(?P<level>[A-Z]+)\] (?P<logger>[^:]+): (?P<message>.*)$"
)

LEVELS = ("ERROR", "WARNING", "INFO", "DEBUG", "CRITICAL")


def _safe_log_path(filename: str) -> str | None:
    """Resolve a log filename inside LOG_DIR only (blocks path traversal)."""
    name = (filename or "enamad.log").strip()
    if not name.startswith("enamad.log"):
        return None
    if ".." in name or "/" in name or "\\" in name:
        return None
    full = os.path.abspath(os.path.join(LOG_DIR, name))
    if not full.startswith(os.path.abspath(LOG_DIR) + os.sep):
        return None
    if not os.path.isfile(full):
        return None
    return full


def list_log_files() -> list[dict[str, Any]]:
    """Return enamad.log and rotated backups newest-first."""
    root = os.path.abspath(LOG_DIR)
    if not os.path.isdir(root):
        return []
    names = sorted(
        (n for n in os.listdir(root) if n.startswith("enamad.log")),
        reverse=True,
    )
    files: list[dict[str, Any]] = []
    for name in names:
        path = os.path.join(root, name)
        try:
            size = os.path.getsize(path)
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        files.append({"name": name, "size": size, "mtime": mtime})
    return files


def _tail_raw_lines(path: str, *, max_lines: int = 500) -> list[str]:
    """Read the last N lines from a (possibly large) text file."""
    max_lines = max(1, min(max_lines, 5000))
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            pos = fh.tell()
            if pos == 0:
                return []
            block = 8192
            chunks: list[bytes] = []
            line_count = 0
            while pos > 0 and line_count <= max_lines:
                read_size = min(block, pos)
                pos -= read_size
                fh.seek(pos)
                chunk = fh.read(read_size)
                chunks.insert(0, chunk)
                line_count = b"".join(chunks).count(b"\n")
            text = b"".join(chunks).decode("utf-8", errors="replace")
            lines = text.splitlines()
            return lines[-max_lines:]
    except OSError:
        return []


def parse_log_lines(raw_lines: list[str]) -> list[dict[str, Any]]:
    """Group multiline tracebacks with their log record."""
    entries: list[dict[str, Any]] = []
    for line in raw_lines:
        match = LOG_LINE_RE.match(line)
        if match:
            entries.append(
                {
                    "ts": match.group("ts"),
                    "level": match.group("level"),
                    "logger": match.group("logger"),
                    "message": match.group("message"),
                    "raw": line,
                    "extra": [],
                }
            )
            continue
        if entries:
            entries[-1]["extra"].append(line)
            entries[-1]["message"] += "\n" + line
        else:
            entries.append(
                {
                    "ts": "",
                    "level": "",
                    "logger": "",
                    "message": line,
                    "raw": line,
                    "extra": [],
                }
            )
    return entries


def read_log_entries(
    *,
    filename: str = "enamad.log",
    max_lines: int = 500,
    level: str = "",
    search: str = "",
) -> dict[str, Any]:
    path = _safe_log_path(filename)
    if not path:
        return {
            "path": None,
            "filename": filename,
            "entries": [],
            "total_raw": 0,
            "error": "فایل لاگ یافت نشد.",
        }

    raw = _tail_raw_lines(path, max_lines=max_lines)
    entries = parse_log_lines(raw)

    level = (level or "").strip().upper()
    if level and level in LEVELS:
        entries = [e for e in entries if e.get("level") == level]

    needle = (search or "").strip().lower()
    if needle:
        entries = [
            e
            for e in entries
            if needle in (e.get("message") or "").lower()
            or needle in (e.get("logger") or "").lower()
            or needle in (e.get("raw") or "").lower()
        ]

    entries.reverse()  # newest first in the UI
    return {
        "path": path,
        "filename": os.path.basename(path),
        "entries": entries,
        "total_raw": len(raw),
        "error": None,
    }
