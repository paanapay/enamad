"""Configuration wrapper for modular imports.

This forwards to the current legacy config loader so runtime behavior stays intact.
"""
from __future__ import annotations

from enamad.data.db import AppConfig, MySQLConfig, ScraperConfig, load_config

__all__ = ["AppConfig", "MySQLConfig", "ScraperConfig", "load_config"]
