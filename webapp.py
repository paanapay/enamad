#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Admin web panel for browsing the Enamad domain database.

Lets an admin (behind a password login) browse the scraped list, inspect a
domain's details/services, look up bot users, and run a live استعلام against
enamad.ir for a domain that may not be in the local DB yet.

Run locally:
    export WEB_ADMIN_PASSWORD=secret
    python webapp.py                 # dev server on :8095

Production (Docker) uses gunicorn (see docker-compose.yml `web` service).
"""
from __future__ import annotations

import os
import secrets
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

import bot_queries as q
from db import load_config, mysql_connection

app = Flask(__name__)
app.secret_key = os.environ.get("WEB_SECRET_KEY") or secrets.token_hex(32)

ADMIN_PASSWORD = os.environ.get("WEB_ADMIN_PASSWORD", "")
LIVE_SEARCH = (os.environ.get("WEB_LIVE_SEARCH", "yes").strip().lower()
               in ("1", "true", "yes", "on"))
PAGE_SIZE = int(os.environ.get("WEB_PAGE_SIZE", "25"))

_APP_CONFIG = None


def app_config():
    global _APP_CONFIG
    if _APP_CONFIG is None:
        _APP_CONFIG = load_config()
    return _APP_CONFIG


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("auth"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapper


@app.template_filter("group")
def group_filter(value):
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return value


@app.template_filter("stars")
def stars_filter(value):
    try:
        n = int(value or 0)
    except (TypeError, ValueError):
        n = 0
    n = max(0, min(5, n))
    return "★" * n + "☆" * (5 - n)


@app.route("/healthz")
def healthz():
    return "ok", 200


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if ADMIN_PASSWORD and secrets.compare_digest(password, ADMIN_PASSWORD):
            session["auth"] = True
            session.permanent = True
            nxt = request.args.get("next") or url_for("dashboard")
            return redirect(nxt)
        flash("رمز عبور نادرست است.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    with mysql_connection(app_config().mysql) as conn:
        stats = q.get_stats(conn)
        users = q.get_bot_user_stats(conn)
    return render_template("dashboard.html", stats=stats, users=users)


@app.route("/domains")
@login_required
def domains():
    query = (request.args.get("q") or "").strip()
    sort = request.args.get("sort", "latest")
    try:
        page = max(0, int(request.args.get("page", 0)))
    except ValueError:
        page = 0
    offset = page * PAGE_SIZE

    with mysql_connection(app_config().mysql) as conn:
        if query:
            rows = q.search_domains(conn, query, limit=PAGE_SIZE)
            total = q.count_search(conn, query)
            # search returns best matches (no offset paging); keep single page
            page = 0
        elif sort == "top":
            rows = q.get_top_rated(conn, offset=offset, limit=PAGE_SIZE)
            total = q.count_top_rated(conn)
        elif sort == "approve":
            rows = q.get_newest_by_approve(conn, offset=offset, limit=PAGE_SIZE)
            total = q.count_with_approve(conn)
        else:
            sort = "latest"
            rows = q.get_latest_domains(conn, offset=offset, limit=PAGE_SIZE)
            total = q.count_domains(conn)

    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    return render_template(
        "domains.html",
        rows=rows,
        total=total,
        page=page,
        pages=pages,
        sort=sort,
        query=query,
        page_size=PAGE_SIZE,
    )


@app.route("/domain/<int:domain_id>")
@login_required
def domain_detail(domain_id: int):
    with mysql_connection(app_config().mysql) as conn:
        row = q.get_domain_by_id(conn, domain_id)
        if not row:
            abort(404)
        services = q.get_domain_services(
            conn, row.get("enamad_id"), row.get("code")
        )
    return render_template("domain_detail.html", d=row, services=services)


@app.route("/estelam", methods=["GET", "POST"])
@login_required
def estelam():
    domain = (request.form.get("domain") or request.args.get("domain") or "").strip()
    result = None
    error = None
    local = None
    if domain:
        with mysql_connection(app_config().mysql) as conn:
            local = q.get_domain_exact(conn, _clean_domain(domain))
        if LIVE_SEARCH:
            try:
                result = _live_lookup(domain)
            except Exception as exc:  # noqa: BLE001
                error = f"استعلام زنده ناموفق بود: {exc}"
        else:
            error = "استعلام زنده غیرفعال است (WEB_LIVE_SEARCH=no)."
    return render_template(
        "estelam.html", domain=domain, result=result, local=local, error=error
    )


@app.route("/users")
@login_required
def users():
    try:
        page = max(0, int(request.args.get("page", 0)))
    except ValueError:
        page = 0
    offset = page * PAGE_SIZE
    with mysql_connection(app_config().mysql) as conn:
        rows = q.get_bot_users(conn, offset=offset, limit=PAGE_SIZE)
        total = q.count_bot_users(conn)
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    return render_template(
        "users.html", rows=rows, total=total, page=page, pages=pages
    )


def _clean_domain(domain: str) -> str:
    try:
        from extract_enamad import clean_domain

        return clean_domain(domain)
    except Exception:  # noqa: BLE001
        return domain.strip().lower()


def _live_lookup(domain: str) -> dict | None:
    """Live استعلام against enamad.ir, reusing the scraper client."""
    from extract_enamad import (
        EnamadClient,
        maybe_enrich_row,
        normalize_search_row,
    )

    client = EnamadClient(quiet=True)
    data = client.search_domain(domain)
    if not data:
        return None
    row = normalize_search_row(data, domain)
    return maybe_enrich_row(client, row, True)


if __name__ == "__main__":
    if not ADMIN_PASSWORD:
        print("WARNING: WEB_ADMIN_PASSWORD not set — login will always fail.")
    app.run(host="127.0.0.1", port=int(os.environ.get("WEB_PORT", "8095")))
