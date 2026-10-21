"""Single business context remains authoritative even when setup features are off."""
from uuid import uuid4

from fastapi import FastAPI, HTTPException
import httpx
import pytest

from app import account_business, business_profile_api, onboarding_schemas, onboarding_service
from app.catalog_api import Actor, authenticate


class Result:
    def __init__(self, value): self.value = value
    def mappings(self): return self
    def scalars(self): return self
    def all(self): return self.value
    def first(self): return self.value[0] if self.value else None
    def scalar_one(self): return self.value
    def scalar_one_or_none(self): return self.value


class DB:
    def __init__(self, *results): self.results, self.calls = list(results), []
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    def begin(self): return self
    async def execute(self, statement, parameters):
        self.calls.append((str(statement), parameters))
        assert self.results, "Unexpected database call while resolving the account business"
        return Result(self.results.pop(0))


def company(*, role="owner"):
    return {"id": uuid4(), "name": "Empresa sintética", "slug": "empresa-sintetica",
            "status": "active", "timezone": "America/Campo_Grande", "role": role}


@pytest.mark.asyncio
@pytest.mark.parametrize("feature", ["0", "1"])
async def test_account_endpoint_returns_its_only_business_without_global_operator_fallback(monkeypatch, feature):
    account, owned, configured = uuid4(), company(), uuid4()
    db = DB([owned])
    monkeypatch.setenv("BUSINESS_PROFILES_ENABLED", feature)
    monkeypatch.setenv("ONBOARDING_ENABLED", "0")
    monkeypatch.setenv("PANEL_OPERATOR_TENANT_ID", str(configured))
    monkeypatch.setattr(business_profile_api, "support_session_factory", lambda: lambda: db)
    app = FastAPI()
    app.include_router(business_profile_api.account_router)
    async def authenticated():
        return Actor("member", account_id=account)
    app.dependency_overrides[authenticate] = authenticated
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/account/businesses")
    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == [str(owned["id"])]
    assert len(db.calls) == 1 and db.calls[0][1] == {"account": account}


@pytest.mark.asyncio
@pytest.mark.parametrize("feature", ["0", "1"])
async def test_account_endpoint_returns_conflict_instead_of_selectable_legacy_businesses(monkeypatch, feature):
    db = DB([company(), company()])
    monkeypatch.setenv("BUSINESS_PROFILES_ENABLED", feature)
    monkeypatch.setenv("ONBOARDING_ENABLED", "0")
    monkeypatch.setattr(business_profile_api, "support_session_factory", lambda: lambda: db)
    app = FastAPI()
    app.include_router(business_profile_api.account_router)
    async def authenticated():
        return Actor("member", account_id=uuid4())
    app.dependency_overrides[authenticate] = authenticated
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/account/businesses")
    assert response.status_code == 409
    assert response.json() == {"detail": account_business.MULTIPLE_BUSINESSES_MESSAGE}
    assert len(db.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("history", [False, True])
async def test_unlinked_personal_account_distinguishes_first_setup_from_revoked_ownership(monkeypatch, history):
    monkeypatch.delenv("PANEL_OPERATOR_TENANT_ID", raising=False)
    db = DB([], history)
    actor = Actor("member", account_id=uuid4())
    if history:
        with pytest.raises(HTTPException) as denied:
            await account_business.panel_businesses(db, actor)
        assert denied.value.status_code == 409
        assert denied.value.detail == account_business.INACTIVE_BUSINESS_MESSAGE
    else:
        assert await account_business.panel_businesses(db, actor) == []
    assert len(db.calls) == 2
    assert all("UPDATE " not in sql and "INSERT " not in sql and "DELETE " not in sql for sql, _ in db.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 1, 2])
async def test_operator_without_config_only_resolves_an_unambiguous_global_business(monkeypatch, count):
    monkeypatch.delenv("PANEL_OPERATOR_TENANT_ID", raising=False)
    rows = [company(role="support") for _ in range(count)]
    db = DB(rows)
    if count == 1:
        assert await account_business.panel_businesses(db, Actor("support")) == rows
    else:
        with pytest.raises(HTTPException) as denied:
            await account_business.panel_businesses(db, Actor("support"))
        assert denied.value.status_code == 409
        assert denied.value.detail == account_business.OPERATOR_CONTEXT_MESSAGE


@pytest.mark.asyncio
async def test_operator_config_is_an_exact_context_and_missing_target_never_falls_back(monkeypatch):
    requested = uuid4()
    monkeypatch.setenv("PANEL_OPERATOR_TENANT_ID", str(requested))
    db = DB([])
    with pytest.raises(HTTPException) as denied:
        await account_business.panel_businesses(db, Actor("support"))
    assert denied.value.status_code == 409
    assert len(db.calls) == 1 and db.calls[0][1]["tenant"] == requested


@pytest.mark.asyncio
async def test_invalid_operator_config_does_not_enumerate_other_businesses(monkeypatch):
    monkeypatch.setenv("PANEL_OPERATOR_TENANT_ID", "not-a-business-uuid")
    db = DB()
    with pytest.raises(HTTPException) as denied:
        await account_business.panel_businesses(db, Actor("admin"))
    assert denied.value.status_code == 409 and not db.calls


@pytest.mark.asyncio
async def test_personal_admin_uses_its_membership_instead_of_another_configured_business(monkeypatch):
    owned = company()
    monkeypatch.setenv("PANEL_OPERATOR_TENANT_ID", str(uuid4()))
    db = DB([owned])
    assert await account_business.panel_businesses(db, Actor("admin", account_id=uuid4())) == [owned]
    assert len(db.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", [False, True])
async def test_administrative_identity_without_membership_requires_one_defined_context(monkeypatch, configured):
    account = uuid4()
    business = company(role="admin")
    if configured:
        monkeypatch.setenv("PANEL_OPERATOR_TENANT_ID", str(business["id"]))
        db = DB([], [business])
        assert await account_business.panel_businesses(db, Actor("admin", account_id=account)) == [business]
        assert db.calls[1][1]["tenant"] == business["id"]
    else:
        monkeypatch.delenv("PANEL_OPERATOR_TENANT_ID", raising=False)
        db = DB([], [business, company(role="admin")])
        with pytest.raises(HTTPException) as denied:
            await account_business.panel_businesses(db, Actor("admin", account_id=account))
        assert denied.value.status_code == 409
        assert denied.value.detail == account_business.OPERATOR_CONTEXT_MESSAGE
    assert len(db.calls) == 2 and db.calls[0][1] == {"account": account}


@pytest.mark.asyncio
async def test_administrative_identity_cannot_register_a_personal_business(monkeypatch):
    account = uuid4()
    db = DB(None, [{"id": account, "email": None, "verified_at": None, "personal_account": False}])
    monkeypatch.setenv("ONBOARDING_ENABLED", "1")
    monkeypatch.setenv("BUSINESS_PROFILES_ENABLED", "1")
    monkeypatch.setattr(onboarding_service, "support_session_factory", lambda: lambda: db)
    payload = onboarding_schemas.CreateBusiness(name="Empresa sintética", niche="barbearia", activity="services",
        timezone="America/Cuiaba", idempotency_key=str(uuid4()))
    with pytest.raises(HTTPException) as denied:
        await onboarding_service.create_business(Actor("admin", account_id=account), payload)
    assert denied.value.status_code == 403
    assert denied.value.detail == "Entre com sua conta pessoal para criar a empresa"
    assert len(db.calls) == 2 and not db.results
    assert all("INSERT " not in sql and "UPDATE " not in sql and "DELETE " not in sql for sql, _ in db.calls)
