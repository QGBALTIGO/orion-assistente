from fastapi.testclient import TestClient

from app.main import app


def test_health_exposes_no_internal_information() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "DATABASE_URL" not in response.text
    assert "tenant" not in response.text


def test_no_tenant_endpoint_exposed_in_fatia_zero() -> None:
    response = TestClient(app).get("/tenants")
    assert response.status_code == 404
