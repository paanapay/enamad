"""Central logging: console (Docker) + rotating file for the admin log viewer."""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DIR = os.environ.get("LOG_DIR", "logs")
LOG_FILE = os.path.join(LOG_DIR, "enamad.log")
MAX_BYTES = int(os.environ.get("LOG_MAX_BYTES", str(5 * 1024 * 1024)))
BACKUP_COUNT = int(os.environ.get("LOG_BACKUP_COUNT", "5"))
# Only warnings/errors go to the log file (the admin panel viewer);
# the console keeps INFO for docker logs. Override with LOG_FILE_LEVEL=INFO.
FILE_LEVEL = os.environ.get("LOG_FILE_LEVEL", "WARNING")

# Chatty third-party loggers: polling bots log every getUpdates request at
# INFO via httpx. Cap them at WARNING everywhere.
NOISY_LOGGERS = ("httpx", "httpcore", "telegram", "apscheduler", "urllib3")

_configured = False


def setup_logging(level: int | str = logging.INFO) -> str:
    """Attach stream + rotating file handlers to the root logger (idempotent)."""
    global _configured
    os.makedirs(LOG_DIR, exist_ok=True)

    root = logging.getLogger()
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)
    root.setLevel(level)

    formatter = logging.Formatter(LOG_FORMAT)
    abs_log = os.path.abspath(LOG_FILE)

    has_stream = any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in root.handlers
    )
    has_file = any(
        isinstance(h, RotatingFileHandler)
        and getattr(h, "baseFilename", "") == abs_log
        for h in root.handlers
    )

    if not has_stream:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        root.addHandler(stream)

    if not has_file:
        file_handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(getattr(logging, FILE_LEVEL.upper(), logging.WARNING))
        root.addHandler(file_handler)

    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    _configured = True
    return abs_log
