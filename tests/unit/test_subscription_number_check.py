"""Exact provider results, tenant isolation and lossless bounded number inputs."""
from contextlib import asynccontextmanager
from copy import deepcopy
from uuid import uuid4

from fastapi import FastAPI, HTTPException
import httpx
from pydantic import ValidationError
import pytest

from app import subscription_number_check as api
from app import subscription_number_check_schemas as schemas
from app.catalog_api import Actor, authenticate

TENANT, OWNER, DEVICE, GENERATION, CHANNEL = (uuid4() for _ in range(5))
PHONE = "+5567999999901"
ROW = {"id": DEVICE, "tenant_id": TENANT, "owner_account_id": OWNER, "channel_id": CHANNEL,
       "generation": GENERATION, "revision": 2, "status": "connected", "name": "Teste"}


@pytest.mark.parametrize("value,expected", [
    ("+55 (67) 99999-9901", PHONE), ("5567999999901", PHONE),
    ("0055 67 99999-9901", PHONE), ("+595 981 234567", "+595981234567"),
    ("+1 (202) 555-0123", "+12025550123"), ("+351.912.345.678", "+351912345678"),
])
def test_accepts_formatted_international_numbers_without_losing_digits(value, expected):
    assert schemas.normalize_phone(value) == expected


@pytest.mark.parametrize("value", ["+012345678", "+１２３４５６７８９", "12345", "1e12", "123@lid", "true", "https://wa.me/12345678", "55/67999999901"])
def test_rejects_ambiguous_and_nonphone_values(value):
    with pytest.raises(ValueError):
        schemas.normalize_phone(value)


def test_mixed_input_keeps_invalid_lines_and_deduplicates_only_normalized_valid_numbers():
    numbers, rows, duplicates = schemas.prepare_numbers([PHONE, "+55 (67) 99999-9901", "not-a-number", "+595981234567"])
    assert numbers == [PHONE, "+595981234567"]
    assert duplicates == 1
    assert rows == [{"number": PHONE}, {"number": "not-a-number", "status": "error", "reason": "invalid_number"}, {"number": "+595981234567"}]


def test_exact_limit_and_exclusive_sources():
    assert len(schemas.CheckRequest(device_id=DEVICE, numbers=[PHONE] * 1000).numbers) == 1000
    assert schemas.CheckRequest(device_id=DEVICE, group_id=uuid4()).numbers == []
    for change in ({"numbers": [PHONE] * 1001}, {"numbers": []}, {"numbers": [PHONE], "group_id": uuid4()}, {"numbers": [True]}, {"numbers": [PHONE], "tenant_id": TENANT}):
        with pytest.raises(ValidationError):
            schemas.CheckRequest.model_validate({"device_id": DEVICE, **change})


@pytest.fixture
def case(monkeypatch):
    actor = Actor("member", account_id=OWNER)
    calls, guards, changes = [], [], {"revision": 2, "deny": False, "remote": None}
    @asynccontextmanager
    async def owner(tenant, current):
        guards.append(tenant)
        if tenant != TENANT or current != actor or changes["deny"]:
            raise HTTPException(403, "Denied")
        yield object()
    async def scope(db, tenant, identity, current, require_connected=False):
        assert tenant == TENANT and identity == DEVICE and current == actor and require_connected
        return {**ROW, "revision": changes["revision"]}, {"id": CHANNEL, "tenant_id": TENANT}
    async def transport(channel, action, method, payload):
        assert channel["id"] == CHANNEL and channel["tenant_id"] == TENANT
        assert action == "device/number-check/check" and method == "POST"
        calls.append(deepcopy(payload))
        if changes["remote"]:
            return await changes["remote"](payload)
        return {**{k: payload[k] for k in ("device_id", "generation", "settings_revision")},
                "results": [{"number": n, "status": "registered", "reason": None} for n in payload["numbers"]], "total": len(payload["numbers"])}
    monkeypatch.setattr(api, "owner_transaction", owner)
    monkeypatch.setattr(api.devices, "scope", scope)
    monkeypatch.setattr(api.channels, "_bridge_control", transport)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[authenticate] = lambda: actor
    return app, calls, guards, changes


async def post(case, values, tenant=TENANT):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=case[0]), base_url="https://orion.test") as client:
        return await client.post(api.BASE.format(tenant_id=tenant) + "/check", json={"device_id": str(DEVICE), **values})


async def test_check_normalizes_mixed_input_and_never_sends_a_message(case):
    response = await post(case, {"numbers": [PHONE, "+55 (67) 99999-9901", "bad", "+595981234567"]})
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["input_total"] == 4 and value["total"] == 3 and value["duplicates"] == 1
    assert value["counts"] == {"registered": 2, "not_registered": 0, "inconclusive": 0, "error": 1}
    assert case[1][0]["numbers"] == [PHONE, "+595981234567"]
    assert response.headers["cache-control"] == "no-store"


async def test_actual_negative_and_timeout_are_distinct(case):
    async def remote(payload):
        return {**{k: payload[k] for k in ("device_id", "generation", "settings_revision")}, "total": 2,
            "results": [{"number": payload["numbers"][0], "status": "not_registered", "reason": None},
                        {"number": payload["numbers"][1], "status": "inconclusive", "reason": "provider_timeout"}]}
    case[3]["remote"] = remote
    value = (await post(case, {"numbers": [PHONE, "+595981234567"]})).json()
    assert value["counts"]["not_registered"] == value["counts"]["inconclusive"] == 1
    assert value["items"][1]["status"] == "inconclusive"


async def test_all_invalid_lines_do_not_call_whatsapp(case):
    response = await post(case, {"numbers": ["bad", "01234"]})
    assert response.status_code == 200
    assert response.json()["counts"]["error"] == 2
    assert case[1] == []


async def test_cross_tenant_denied_before_provider_or_payload_processing(case):
    response = await post(case, {"numbers": [PHONE]}, tenant=uuid4())
    assert response.status_code == 403 and case[1] == []


async def test_provider_timeout_does_not_produce_a_negative_result(case):
    async def remote(payload):
        raise TimeoutError()
    case[3]["remote"] = remote
    response = await post(case, {"numbers": [PHONE]})
    assert response.status_code == 503 and "items" not in response.json()


@pytest.mark.parametrize("change", ["number", "generation", "count", "invalid_status", "late_revision"])
async def test_unverified_or_stale_provider_results_are_rejected(case, change):
    async def remote(payload):
        result = {**{k: payload[k] for k in ("device_id", "generation", "settings_revision")},
                  "total": 1, "results": [{"number": PHONE, "status": "registered", "reason": None}]}
        if change == "number": result["results"][0]["number"] = "+595981234567"
        elif change == "generation": result["generation"] = str(uuid4())
        elif change == "count": result["total"] = 2
        elif change == "invalid_status": result["results"][0]["status"] = "maybe"
        else: case[3]["revision"] = 3
        return result
    case[3]["remote"] = remote
    response = await post(case, {"numbers": [PHONE]})
    assert response.status_code in {409, 503} and "items" not in response.json()


async def test_group_selection_uses_server_members_not_browser_phones(case, monkeypatch):
    group = uuid4()
    async def members(db, tenant, identity):
        assert tenant == TENANT and identity == group
        return [PHONE, "+595981234567"]
    monkeypatch.setattr(api, "group_numbers", members)
    response = await post(case, {"group_id": str(group)})
    assert response.status_code == 200 and response.json()["total"] == 2
    assert case[1][0]["numbers"] == [PHONE, "+595981234567"]
