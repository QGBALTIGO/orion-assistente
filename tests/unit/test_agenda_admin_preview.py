"""The admin agenda preview never replaces or opens the normal panel."""
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.admin_accounts import require_platform_owner
from app.agenda_admin_preview import router


def make_client():
    app = FastAPI()
    app.include_router(router)
    return app, TestClient(app)


def test_public_or_member_without_platform_session_is_denied(monkeypatch):
    monkeypatch.setenv("ORION_AGENDA_ADMIN_PREVIEW_ENABLED", "1")
    _, client = make_client()
    assert client.get("/painel/agenda-teste").status_code == 401
    assert client.get("/painel/agenda-teste/agenda-preview.js").status_code == 401
    assert client.get("/painel/agenda-teste/agenda-preview.css").status_code == 401
    assert client.get("/painel/agenda-teste?role=admin").status_code == 401


def test_preview_off_by_default(monkeypatch):
    monkeypatch.delenv("ORION_AGENDA_ADMIN_PREVIEW_ENABLED", raising=False)
    app, client = make_client()
    app.dependency_overrides[require_platform_owner] = lambda: uuid.uuid4()
    assert client.get("/painel/agenda-teste").status_code == 404


def test_admin_enabled_gets_only_preview_files(monkeypatch):
    monkeypatch.setenv("ORION_AGENDA_ADMIN_PREVIEW_ENABLED", "1")
    app, client = make_client()
    app.dependency_overrides[require_platform_owner] = lambda: uuid.uuid4()
    response = client.get("/painel/agenda-teste")
    assert response.status_code == 200
    assert "Apenas para a conta administradora" in response.text
    assert 'src="/painel/agenda-teste/agenda-preview.js"' in response.text
    assert "no-store" in response.headers["cache-control"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    js = client.get("/painel/agenda-teste/agenda-preview.js")
    assert js.status_code == 200
    assert 'method:"GET"' in js.text
    assert client.get("/painel/agenda-teste/agenda-preview.css").status_code == 200
    assert client.get("/painel/agenda-teste/irrelevant.js").status_code == 404
    assert client.get("/painel/agenda-teste/panel.html").status_code == 404
