"""Flask app factory scaffold.

For now we return the existing app object to avoid behavioral changes while
incrementally moving to package-based modules.
"""
from __future__ import annotations

from flask import Flask


def create_app() -> Flask:
    from enamad.web.webapp import app as legacy_app

    return legacy_app
