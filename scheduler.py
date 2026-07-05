#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Portable task scheduler for the Enamad project (Laravel-scheduler style).

Runs two recurring jobs by shelling out to extract_enamad.py:
  1. --update        : fetch newly-added domains (cheap, tail pages only)
  2. --refresh-stale : refresh existing domains via trust seal (no captcha)

Frequencies are configurable via config.ini ([scheduler] section) or env vars,
so the same file works on Windows (dev), Linux, and inside Docker.

Run:
  python scheduler.py
  python scheduler.py --config path/to/config.ini
"""

from __future__ import annotations

import argparse
import configparser
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR / "config.ini"

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] scheduler: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("enamad-scheduler")


def _env(*keys: str) -> str | None:
    for key in keys:
        value = os.environ.get(key)
        if value is not None and value.strip() != "":
            return value.strip()
    return None


@dataclass(frozen=True)
class SchedulerConfig:
    timezone: str
    update_cron: str
    update_pages: int
    update_workers: int
    update_chunk_pages: int
    refresh_cron: str
    refresh_days: int
    refresh_limit: int
    refresh_workers: int
    refresh_missing_only: bool
    run_on_start: bool
    enable_update: bool
    enable_refresh: bool


def load_scheduler_config(path: Path) -> SchedulerConfig:
    parser = configparser.ConfigParser()
    if path.is_file():
        parser.read(path, encoding="utf-8")

    def get(key: str, fallback: str) -> str:
        env_key = f"SCHED_{key.upper()}"
        return _env(env_key) or parser.get("scheduler", key, fallback=fallback)

    def get_int(key: str, fallback: int) -> int:
        return int(get(key, str(fallback)))

    def get_bool(key: str, fallback: bool) -> bool:
        return get(key, "yes" if fallback else "no").lower() in ("1", "true", "yes", "on")

    return SchedulerConfig(
        timezone=get("timezone", "Asia/Tehran"),
        update_cron=get("update_cron", "0 3 * * *"),
        update_pages=get_int("update_pages", 50),
        update_workers=get_int("update_workers", 1),
        update_chunk_pages=get_int("update_chunk_pages", 10),
        refresh_cron=get("refresh_cron", "0 */6 * * *"),
        refresh_days=get_int("refresh_days", 30),
        refresh_limit=get_int("refresh_limit", 500),
        refresh_workers=get_int("refresh_workers", 4),
        refresh_missing_only=get_bool("refresh_missing_only", False),
        run_on_start=get_bool("run_on_start", False),
        enable_update=get_bool("enable_update", True),
        enable_refresh=get_bool("enable_refresh", True),
    )


def _run(label: str, extra_args: list[str], config_path: Path) -> None:
    cmd = [sys.executable, str(SCRIPT_DIR / "extract_enamad.py"), *extra_args,
           "--config", str(config_path)]
    log.info("Running job '%s': %s", label, " ".join(extra_args))
    try:
        result = subprocess.run(cmd, cwd=str(SCRIPT_DIR))
        if result.returncode == 0:
            log.info("Job '%s' finished successfully.", label)
        else:
            log.warning("Job '%s' exited with code %s.", label, result.returncode)
    except Exception as exc:
        log.error("Job '%s' failed to run: %s", label, exc)


def make_update_job(cfg: SchedulerConfig, config_path: Path):
    def job() -> None:
        args = [
            "--update",
            "--update-pages", str(cfg.update_pages),
            "--workers", str(cfg.update_workers),
            "--chunk-pages", str(cfg.update_chunk_pages),
        ]
        _run("update", args, config_path)

    return job


def make_refresh_job(cfg: SchedulerConfig, config_path: Path):
    def job() -> None:
        args = [
            "--refresh-stale",
            "--stale-days", str(cfg.refresh_days),
            "--refresh-limit", str(cfg.refresh_limit),
            "--refresh-workers", str(cfg.refresh_workers),
            "--delay", "0",
        ]
        if cfg.refresh_missing_only:
            args.append("--missing-only")
        _run("refresh-stale", args, config_path)

    return job


def main() -> int:
    argp = argparse.ArgumentParser(description="Enamad recurring task scheduler")
    argp.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to config.ini")
    parsed = argp.parse_args()

    config_path = Path(parsed.config)
    if not config_path.is_absolute():
        config_path = SCRIPT_DIR / config_path

    cfg = load_scheduler_config(config_path)
    scheduler = BlockingScheduler(timezone=cfg.timezone)

    update_job = make_update_job(cfg, config_path)
    refresh_job = make_refresh_job(cfg, config_path)

    if cfg.enable_update:
        scheduler.add_job(
            update_job,
            CronTrigger.from_crontab(cfg.update_cron, timezone=cfg.timezone),
            id="update",
            name="Fetch new domains",
            max_instances=1,
            coalesce=True,
        )
        log.info("Scheduled 'update' with cron '%s' (%s).", cfg.update_cron, cfg.timezone)

    if cfg.enable_refresh:
        scheduler.add_job(
            refresh_job,
            CronTrigger.from_crontab(cfg.refresh_cron, timezone=cfg.timezone),
            id="refresh-stale",
            name="Refresh stale domains",
            max_instances=1,
            coalesce=True,
        )
        log.info(
            "Scheduled 'refresh-stale' with cron '%s' (%s)%s.",
            cfg.refresh_cron,
            cfg.timezone,
            " [missing-only]" if cfg.refresh_missing_only else "",
        )

    if not scheduler.get_jobs():
        log.error("No jobs enabled. Set enable_update/enable_refresh in [scheduler].")
        return 1

    if cfg.run_on_start:
        log.info("run_on_start enabled — running jobs once now.")
        if cfg.enable_refresh:
            refresh_job()
        if cfg.enable_update:
            update_job()

    log.info("Scheduler started. Press Ctrl+C to exit.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
