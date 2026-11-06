"""Synthetic endpoint tests: no real WhatsApp numbers are queried or messages sent."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID
import json

import pytest
from fastapi import HTTPException, Response

from app import subscription_number_check as api

TENANT = UUID("00000000-0000-4000-8000-000000000118")
DEVICE = UUID("00000000-0000-4000-8000-000000000119")
GEN = UUID("00000000-0000-4000-8000-000000000120")
ACTOR = SimpleNamespace(account_id=UUID("00000000-0000-4000-8000-000000000121"))
NUMBER = "+5567999999901"


class SyntheticRequest:
    def __init__(self, data):
        self.data = data
        self.reads = 0

    async def stream(self):
        self.reads += 1
        yield json.dumps(self.data).encode("utf-8")


def request(**changes):
    return SyntheticRequest({"device_id": str(DEVICE), "phones": [NUMBER],
                             "confirmed_authorization": True} | changes)


def install(monkeypatch, *, changed=False, reject_owner=False):
    state = SimpleNamespace(owners=[], calls=[], quotas=[], version_reads=0, effects=[])
    row = {"id": DEVICE, "generation": GEN, "revision": 2,
           "channel_id": UUID("00000000-0000-4000-8000-000000000122")}
    transport = {"channel_id": str(row["channel_id"]), "enabled": True}

    @asynccontextmanager
    async def owner(tenant, actor):
        state.owners.append((tenant, actor))
        if reject_owner:
            raise HTTPException(403, "owner_only")
        yield SimpleNamespace()

    async def device_scope(db, tenant, device, actor, require_connected=False):
        assert tenant == TENANT and device == DEVICE and require_connected
        return row, transport

    def version(device):
        state.version_reads += 1
        return "new-version" if changed and state.version_reads >= 2 else "original-version"

    async def bridge(transport_value, path, method, data):
        assert transport_value is transport and method == "POST"
        assert path == "device/number-check/check"
        assert set(data) == {"device_id", "generation", "settings_revision", "phones"}
        state.calls.append(data)
        return {"device_id": str(DEVICE), "generation": str(GEN),
                "settings_revision": 2, "items": [
                {"phone": NUMBER, "status": "unknown"}]}

    async def reserve(req, tenant, device, n):
        assert (tenant, device, n) == (TENANT, DEVICE, 1)
        state.quotas.append(n)

    monkeypatch.setattr(api, "owner_transaction", owner)
    monkeypatch.setattr(api.devices, "scope", device_scope)
    monkeypatch.setattr(api.devices, "device_version", version)
    monkeypatch.setattr(api.devices, "transport_scope",
                        lambda row: {"device_id": str(DEVICE),
                        "generation": str(GEN), "settings_revision": 2})
    monkeypatch.setattr(api.channels, "_bridge_control", bridge)
    monkeypatch.setattr(api, "reserve_quota", reserve)
    return state


@pytest.mark.asyncio
async def test_check_one_number_after_owner_scope_and_quota(monkeypatch):
    state = install(monkeypatch)
    req = request()
    res = Response()
    value = await api.check(TENANT, req, res, ACTOR)
    assert value["total"] == 1
    assert value["items"] == [{"phone": NUMBER, "status": "unknown"}]
    assert len(state.owners) == 3
    assert req.reads == 1 and len(state.calls) == 1 and state.quotas == [1]
    assert res.headers["Cache-Control"] == "no-store"
    assert not state.effects


@pytest.mark.asyncio
async def test_owner_denied_before_reading_body_or_provider(monkeypatch):
    state = install(monkeypatch, reject_owner=True)
    req = request()
    with pytest.raises(HTTPException) as exc:
        await api.check(TENANT, req, Response(), ACTOR)
    assert exc.value.status_code == 403
    assert req.reads == 0 and not state.calls and not state.quotas


@pytest.mark.asyncio
async def test_invalid_input_does_not_reserve_or_query(monkeypatch):
    state = install(monkeypatch)
    req = request(phones=[NUMBER, NUMBER])
    with pytest.raises(HTTPException) as exc:
        await api.check(TENANT, req, Response(), ACTOR)
    assert exc.value.status_code == 422
    assert not state.calls and not state.quotas


@pytest.mark.asyncio
async def test_device_replacement_after_provider_call_refuses_stale_result(monkeypatch):
    state = install(monkeypatch, changed=True)
    with pytest.raises(HTTPException) as exc:
        await api.check(TENANT, request(), Response(), ACTOR)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "NUMBER_CHECK_CONTEXT_CHANGED"
    assert len(state.calls) == 1 and state.quotas == [1]


@pytest.mark.asyncio
async def test_network_failure_is_generic_and_quota_not_refunded(monkeypatch):
    state = install(monkeypatch)
    async def error(*args, **kwargs):
        raise HTTPException(502, {"sensitive": "not-for-browser"})
    monkeypatch.setattr(api.channels, "_bridge_control", error)
    with pytest.raises(HTTPException) as exc:
        await api.check(TENANT, request(), Response(), ACTOR)
    assert exc.value.status_code == 503
    assert "sensitive" not in str(exc.value.detail)
    assert state.quotas == [1]
