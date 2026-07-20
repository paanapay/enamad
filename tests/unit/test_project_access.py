from __future__ import annotations

from flask import Flask

import project_access


def test_current_project_id_parses_int():
    app = Flask(__name__)
    app.secret_key = "test"
    with app.test_request_context("/"):
        project_access.set_project_session({"id": "12", "name": "A"})
        assert project_access.current_project_id() == 12


def test_activate_user_project_uses_matching_membership(monkeypatch):
    app = Flask(__name__)
    app.secret_key = "test"

    rows = [
        {"id": 1, "name": "P1", "member_role": "member"},
        {"id": 2, "name": "P2", "member_role": "admin"},
    ]

    monkeypatch.setattr(project_access, "list_user_projects", lambda conn, uid: rows)

    with app.test_request_context("/"):
        ok = project_access.activate_user_project(object(), 9, project_id=2)
        assert ok is True
        assert project_access.current_project_id() == 2
        assert project_access.can_manage_project() is True
