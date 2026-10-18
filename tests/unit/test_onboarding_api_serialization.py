"""HTTP serialization parity; service stubs do not claim real PostgreSQL proof."""
from copy import deepcopy
from datetime import date, datetime, time, timezone
from decimal import Decimal
from enum import Enum
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
import fastapi.routing
import httpx
import pytest

from app import onboarding_api as api
from app.catalog_api import Actor, authenticate

TENANT = UUID("00000000-0000-4000-8000-000000000101")
ACTOR = Actor("member", account_id=UUID("00000000-0000-4000-8000-000000000102"))
BASE = f"/api/v1/tenants/{TENANT}/onboarding"
CREATE = {"name": "Empresa sintética Árvore", "timezone": "America/Cuiaba", "niche": "barbearia",
    "activity": "both", "idempotency_key": "synthetic-create-key"}
COMPLETE = {"expected_version": 7, "expected_snapshot": "a" * 64, "idempotency_key": "synthetic-complete-key",
    "activate_catalog": True, "acknowledged": True}


class Status(str, Enum):
    COMPLETE = "complete"


def encoded_state(count=3):
    """Keep the existing encoder as the oracle, including non-native SQL types."""
    raw = {"tenant_id": TENANT, "status": Status.COMPLETE, "version": 7, "required": False,
        "pending": [], "snapshot": "a" * 64, "last_operation_key": "synthetic-key",
        "configuration": {"private_address": None, "niche": "barbearia", "activity": "both"},
        "catalog": {"professionals": [{"id": UUID(int=200 + index), "name": "Pessoa Árvore <teste>",
            "commission_percent": Decimal("12.50"), "active": index % 2 == 0, "image_url": None,
            "updated_at": datetime(2026, 10, 18, 12, 30, tzinfo=timezone.utc)} for index in range(count)],
            "services": [{"id": UUID(int=9001), "price_cents": 3500, "duration_min": 40, "prep_min": 5,
                "cleanup_min": 10, "professional_ids": [UUID(int=200)]}],
            "hours": [{"id": UUID(int=9002), "weekday": 0, "start_time": time(9, 15), "end_time": time(18),
                "day": date(2026, 10, 19), "breaks": [{"start_time": time(12), "end_time": time(13)}]}],
            "products": [{"variants": [{"price_kind": "on_request", "unit_price_cents": None}]}]},
        "channels": {"assistant": {"generation": 2, "profile": {"faqs": [{"question": "Preço?", "answer": "Depende do serviço."}],
            "style_rules": {"tone": "warm"}}}, "google": {"status": "not_configured"}},
        "operations": [{"key": "synthetic-key", "version": 7, "result": {"ids": [str(UUID(int=9002))]}}]}
    return jsonable_encoder(raw)


def app_with_actor():
    app = FastAPI(); app.include_router(api.router)
    app.dependency_overrides[authenticate] = lambda: ACTOR
    return app


SUCCESS = [
    ("entry", "GET", "/api/v1/onboarding/entry", None, 200),
    ("create_business", "POST", "/api/v1/onboarding/businesses", CREATE, 201),
    ("get_state", "GET", BASE, None, 200),
    ("complete", "POST", BASE + "/complete", COMPLETE, 200),
    *[("save_step", "PUT", BASE + "/steps/" + step,
        {"expected_version": 7, "idempotency_key": "synthetic-save-key", "data": {}}, 200)
      for step in ("business", "location", "professionals", "catalog", "hours", "channels", "review")],
]


@pytest.mark.parametrize("service_name,method,path,body,status", SUCCESS)
@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.asyncio
async def test_each_success_and_replay_preserves_legacy_json_bytes_status_and_headers(
        monkeypatch, service_name, method, path, body, status, replay):
    expected = encoded_state()
    if service_name == "save_step":
        # These fields are appended after _state's encoder, just as in save_step.
        expected["result"] = {"id": str(UUID(int=9001)), "ids": [str(UUID(int=9002))], "generation": 2}
    if replay: expected["operations"][0]["result"] = {"id": str(UUID(int=9001))}
    calls = []
    async def service(*args):
        calls.append(args); return deepcopy(expected)
    monkeypatch.setattr(api.service, service_name, service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_with_actor()), base_url="https://test") as client:
        response = await client.request(method, path, json=body)
    legacy = JSONResponse(jsonable_encoder(expected), status_code=status)
    assert response.status_code == status and response.json() == expected
    assert response.content == legacy.body
    assert sorted(response.headers.raw) == sorted(legacy.raw_headers)
    assert len(calls) == 1 and calls[0][0] == ACTOR
    assert response.json()["catalog"]["hours"][0]["start_time"] == "09:15:00"
    assert response.json()["catalog"]["professionals"][0]["commission_percent"] == 12.5
    assert response.json()["catalog"]["products"][0]["variants"][0]["unit_price_cents"] is None


@pytest.mark.parametrize("status,detail,headers", [
    (401, "Sessão expirada", {"WWW-Authenticate": "Bearer"}), (403, "Sem permissão", {}),
    (404, "Etapa desconhecida", {}), (409, {"message": "Estado mudou", "pending": [{"code": "hours"}]}, {}),
    (422, "Valor inválido", {}), (423, "Configuração desabilitada", {}),
    (503, "Sessões indisponíveis", {"Retry-After": "2"}),
])
@pytest.mark.asyncio
async def test_service_exceptions_keep_exact_http_semantics(monkeypatch, status, detail, headers):
    async def fail(*args): raise HTTPException(status, detail, headers=headers)
    monkeypatch.setattr(api.service, "create_business", fail)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_with_actor()), base_url="https://test") as client:
        response = await client.post("/api/v1/onboarding/businesses", json=CREATE)
    legacy = JSONResponse({"detail": detail}, status_code=status, headers=headers)
    assert response.status_code == status and response.content == legacy.body
    assert sorted(response.headers.raw) == sorted(legacy.raw_headers)


@pytest.mark.asyncio
async def test_authentication_challenge_is_unchanged_and_never_enters_service(monkeypatch):
    monkeypatch.setenv("SUPPORT_API_TOKEN", "synthetic-only-" + "x" * 40)
    async def forbidden(*args): pytest.fail("Unauthorized request reached onboarding service")
    monkeypatch.setattr(api.service, "get_state", forbidden)
    app = FastAPI(); app.include_router(api.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
        response = await client.get(BASE)
    assert response.status_code == 401 and response.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
async def test_large_real_http_shape_has_identical_bytes_without_second_encoder(monkeypatch):
    snapshot = encoded_state(281)
    async def service(*args): return deepcopy(snapshot)
    monkeypatch.setattr(api.service, "get_state", service)
    optimized = app_with_actor()
    legacy = FastAPI()
    @legacy.get(BASE)
    async def original(actor=Depends(authenticate)):
        return await service(actor, TENANT)
    legacy.dependency_overrides[authenticate] = lambda: ACTOR
    original_encoder = fastapi.routing.jsonable_encoder; calls = []
    def count(value, *args, **kwargs):
        calls.append(1); return original_encoder(value, *args, **kwargs)
    monkeypatch.setattr(fastapi.routing, "jsonable_encoder", count)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=legacy), base_url="https://test") as client:
        before = await client.get(BASE)
    assert len(calls) == 1
    calls.clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=optimized), base_url="https://test") as client:
        after = await client.get(BASE)
    assert calls == [] and after.content == before.content and after.headers == before.headers
    assert after.status_code == before.status_code == 200
