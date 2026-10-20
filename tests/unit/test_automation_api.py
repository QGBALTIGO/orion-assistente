from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app import automation_api as api
from app.catalog_api import Actor, authenticate


@pytest.mark.parametrize("values", [{"expected_version": True}, {"enabled": "true"}, {"daily_limit": 0},
    {"daily_limit": 101}, {"acknowledged": "yes"}])
def test_execution_contract_is_strict(values):
    with pytest.raises(ValidationError):
        api.ExecutionSave(**{"expected_version": 0, "idempotency_key": "synthetic-key", "enabled": False, **values})


@pytest.mark.asyncio
async def test_invalid_consent_contact_is_422_not_server_error():
    app = FastAPI(); app.include_router(api.router)
    app.dependency_overrides[authenticate] = lambda: Actor("member", account_id=uuid4())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
        response = await client.get(f"/api/v1/tenants/{uuid4()}/management/marketing/consent", params={"contact_e164": "group@g.us"})
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("route,payload", [("execution", {"expected_version": 0, "idempotency_key": "synthetic-key", "enabled": True, "acknowledged": True}),
    ("consent", {"contact_e164": "+5567999999901", "marketing_opt_in": True, "operational_opt_in": False, "evidence": "Evidência sintética"}),
    ("contact-pause", {"contact_e164": "+5567999999901", "paused": True})])
async def test_flag_off_never_mutates_or_initializes_execution(route, payload, monkeypatch):
    monkeypatch.delenv("MARKETING_EXECUTION_ENABLED", raising=False)
    monkeypatch.setattr(api, "support_session_factory", lambda: pytest.fail("disabled write accessed account storage"))
    app = FastAPI(); app.include_router(api.router)
    app.dependency_overrides[authenticate] = lambda: Actor("member", account_id=uuid4())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
        response = await client.put(f"/api/v1/tenants/{uuid4()}/management/marketing/{route}", json=payload)
    assert response.status_code == 423
