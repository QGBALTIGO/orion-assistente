"""Tenant-bound analytics and honest degradation without any external calls."""
from dataclasses import replace
import json
from uuid import UUID

from cryptography.fernet import Fernet
from fastapi import FastAPI
import httpx
import pytest

from app import affiliate_workspace as workspace
from app.catalog_api import Actor, authenticate


TENANT = UUID("10000000-0000-4000-8000-000000000001")
OTHER_TENANT = UUID("10000000-0000-4000-8000-000000000002")
OWNER = Actor("member", tenant_id=TENANT,
              account_id=UUID("20000000-0000-4000-8000-000000000001"),
              membership_role="owner")


@pytest.fixture
def context(monkeypatch):
    key = Fernet.generate_key()
    monkeypatch.setenv("ORION_AFFILIATE_WORKSPACE_KEY", key.decode())
    ctx = {"actor": OWNER, "calls": [], "database": [], "key": Fernet(key),
           "report_status": 404, "report": {}, "state_status": 200,
           "state": {"company": "Ofertas sintéticas", "jobs": [], "rule": [],
                     "destination": [], "connection": []}}

    class Result:
        def __init__(self, value): self.value = value
        def scalar_one_or_none(self): return self.value

    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def execute(self, query, params):
            ctx["database"].append((str(query), params))
            assert params["tenant"] == TENANT
            return Result("America/Campo_Grande" if "SELECT timezone" in str(query)
                          else "Ofertas sintéticas")

    async def fetch(method, path, *, headers=None, params=None, body=None):
        claim = json.loads(ctx["key"].decrypt(headers["X-Orion-Affiliate-Access"].encode()))
        ctx["calls"].append({"method": method, "path": path, "params": params, "claim": claim})
        assert claim == {"tenant": TENANT.hex, "name": "Ofertas sintéticas",
                         "method": "GET", "path": path}
        assert method == "GET" and body is None
        kind = "report" if path == "/api/analytics" else "state"
        return ctx[f"{kind}_status"], "application/json", json.dumps(ctx[kind]).encode()

    monkeypatch.setattr(workspace, "support_session_factory", lambda: Session)
    monkeypatch.setattr(workspace, "fetch_upstream", fetch)
    app = FastAPI()
    app.include_router(workspace.router, prefix="/api/v1")
    app.dependency_overrides[authenticate] = lambda: ctx["actor"]
    ctx["app"] = app
    return ctx


async def get(ctx, tenant=TENANT, query=""):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ctx["app"]),
                                 base_url="https://orion.test") as client:
        return await client.get(f"/api/v1/afiliados/{tenant}/api/analytics{query}")


async def test_missing_report_uses_scoped_snapshot_without_inventing_revenue(context):
    response = await get(context, query=f"?period=7d&tenant={OTHER_TENANT}")
    assert response.status_code == 200
    report = response.json()
    assert response.headers["cache-control"] == "no-store"
    assert report["period"]["key"] == "7d"
    assert report["period"]["timezone"] == "America/Campo_Grande"
    assert report["summary"]["commission_cents"] is None
    assert report["summary"]["sales_cents"] is None
    assert report["summary"]["clicks"] is None
    assert report["summary"]["published"] is None
    assert report["sources"]["publications"]["complete"] is False
    assert [call["path"] for call in context["calls"]] == ["/api/analytics", "/api/state"]
    assert context["calls"][0]["params"] == {"period": "7d"}


async def test_confirmed_report_passes_through_without_loading_snapshot(context):
    report = workspace.build_analytics_fallback(context["state"])
    report["sources"]["sales"]["status"] = "ready"
    report["summary"].update(commission_cents=0, sales_cents=0, orders=0)
    context.update(report_status=200, report=report)
    response = await get(context)
    assert response.status_code == 200
    assert response.json() == context["report"]
    assert len(context["calls"]) == 1


@pytest.mark.parametrize("actor,tenant", [
    (replace(OWNER, membership_role="editor"), TENANT),
    (replace(OWNER, maintenance=True), TENANT),
    (replace(OWNER, account_id=None), TENANT),
    (Actor("support"), TENANT),
    (OWNER, OTHER_TENANT),
])
async def test_forbidden_actor_never_reaches_provider(context, actor, tenant):
    context["actor"] = actor
    response = await get(context, tenant)
    assert response.status_code == 403
    assert context["calls"] == []
    assert context["database"] == []


@pytest.mark.parametrize("status,expected", [(401, 401), (403, 403), (429, 429), (500, 502)])
async def test_report_failure_does_not_replace_confirmed_numbers_with_snapshot(context, status, expected):
    context["report_status"] = status
    response = await get(context)
    assert response.status_code == expected
    assert len(context["calls"]) == 1
    assert "summary" not in response.json()


@pytest.mark.parametrize("report", [{}, {"period": {"key": "7d"}, "sources": {},
                                       "summary": {}, "series": []}, None])
async def test_malformed_or_wrong_period_report_is_not_presented(context, report):
    context.update(report_status=200, report=report)
    assert (await get(context)).status_code == 502
    assert len(context["calls"]) == 1


async def test_invalid_period_rejected_before_external_read(context):
    assert (await get(context, query="?period=all-time")).status_code == 422
    assert context["calls"] == []


async def test_snapshot_failure_is_not_a_zero_activity_report(context):
    context["state_status"] = 500
    assert (await get(context)).status_code == 502


@pytest.mark.parametrize("state", [{"detail": "Invalid provider envelope"},
                                   {"jobs": [], "rule": [], "destination": [], "connection": None}])
async def test_malformed_snapshot_cannot_become_an_empty_operation(context, state):
    context["state"] = state
    assert (await get(context)).status_code == 502


async def test_provider_timeout_is_reported_without_fabricating_data(context, monkeypatch):
    async def timeout(*args, **kwargs):
        raise TimeoutError
    monkeypatch.setattr(workspace, "fetch_upstream", timeout)
    assert (await get(context)).status_code == 504
