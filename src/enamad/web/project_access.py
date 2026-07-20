"""Session helpers for multi-project CRM access."""
from __future__ import annotations

from flask import session

from crm_db import (
    PROJECT_ADMIN_ROLES,
    PROJECT_OWNER,
    ROLE_SUPER,
    get_membership,
    list_user_projects,
)


def set_admin_session(admin: dict) -> None:
    session["admin_id"] = admin["id"]
    session["admin_username"] = admin["username"]
    session["admin_display_name"] = admin.get("display_name") or admin["username"]
    session["admin_role"] = admin["role"]
    session["auth"] = True
    session.permanent = True


def set_project_session(project: dict, *, member_role: str | None = None) -> None:
    session["project_id"] = int(project["id"])
    session["project_name"] = project.get("name") or ""
    session["project_role"] = member_role or project.get("member_role") or PROJECT_OWNER


def clear_project_session() -> None:
    session.pop("project_id", None)
    session.pop("project_name", None)
    session.pop("project_role", None)


def current_project_id() -> int | None:
    raw = session.get("project_id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def is_platform_super() -> bool:
    return session.get("admin_role") == ROLE_SUPER


def can_manage_project() -> bool:
    if is_platform_super():
        return True
    return session.get("project_role") in PROJECT_ADMIN_ROLES


def activate_user_project(conn, user_id: int, project_id: int | None = None) -> bool:
    """Set session project from membership. Returns False if user has none."""
    projects = list_user_projects(conn, user_id)
    if not projects:
        clear_project_session()
        return False
    chosen = None
    if project_id is not None:
        for row in projects:
            if int(row["id"]) == int(project_id):
                chosen = row
                break
    if chosen is None:
        # Prefer previously selected project when still valid.
        current = current_project_id()
        if current is not None:
            for row in projects:
                if int(row["id"]) == current:
                    chosen = row
                    break
    if chosen is None:
        chosen = projects[0]
    set_project_session(chosen, member_role=chosen.get("member_role"))
    return True


def ensure_membership(conn, project_id: int, user_id: int) -> dict | None:
    return get_membership(conn, project_id, user_id)
