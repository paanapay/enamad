from __future__ import annotations

from enamad.web.app_factory import create_app


def test_create_app_healthz():
    app = create_app()
    with app.test_client() as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.data.decode("utf-8") == "ok"
