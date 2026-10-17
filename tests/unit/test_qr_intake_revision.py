"""Ingress authority/guard contracts. Redis execution is tested separately in CI."""
import asyncio
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app import qr_intake_revision as revision


class Redis:
    def __init__(self, valid=1):
        self.valid = valid;self.calls = [];self.closed = False
    async def eval(self, *args):
        self.calls.append(args)
        if isinstance(self.valid, Exception):
            raise self.valid
        return self.valid
    async def aclose(self):
        self.closed = True


@pytest.fixture
def config(monkeypatch):
    tenant = uuid4()
    monkeypatch.setenv("QR_AI_INTAKE_REVISION_ENABLED", "1")
    monkeypatch.setenv("QR_AI_ENABLED", "1")
    monkeypatch.setenv("QR_AI_TENANT_ID", str(tenant))
    monkeypatch.setenv("QR_AI_ALLOWED_SENDERS", "5521997557364")
    monkeypatch.setenv("QR_BRIDGE_TOKEN", "synthetic-bridge-key-for-tests-only-12345678")
    return tenant


@pytest.mark.asyncio
async def test_notice_is_private_exact_phone_and_server_bound_tenant(config, monkeypatch):
    import redis.asyncio
    memory = Redis()
    monkeypatch.setattr(redis.asyncio, "from_url", lambda *a, **k: memory)
    app = FastAPI();app.include_router(revision.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        body = {"phone": "5521997557364", "message_id": "synthetic-one"}
        assert (await client.post("/internal/qr/ai/notice", json=body)).status_code == 401
        headers = {"X-Orion-Bridge-Key": "synthetic-bridge-key-for-tests-only-12345678"}
        assert (await client.post("/internal/qr/ai/notice", json={**body, "phone": "5521997557365"}, headers=headers)).status_code == 403
        assert (await client.post("/internal/qr/ai/notice", json={**body, "tenant_id": str(uuid4())}, headers=headers)).status_code == 422
        assert (await client.post("/internal/qr/ai/notice", json={**body, "phone": "123@lid"}, headers=headers)).status_code == 422
        result = await client.post("/internal/qr/ai/notice", json=body, headers=headers)
    assert result.status_code == 200 and result.json() == {"revision": 1}
    assert len(memory.calls) == 1 and str(config) in memory.calls[0][2]
    assert memory.closed


@pytest.mark.asyncio
async def test_feature_default_disabled_never_accesses_redis(config, monkeypatch):
    monkeypatch.delenv("QR_AI_INTAKE_REVISION_ENABLED")
    assert not revision.enabled()
    token = revision.bind(config, "5521997557364", ["synthetic"], 1, Redis(valid=AssertionError()))
    try:
        await revision.check_before_write()
    finally:
        revision.reset(token)


@pytest.mark.asyncio
async def test_changed_ingress_before_first_write_is_the_only_replayable_ack(config):
    redis = Redis(valid=0)
    token = revision.bind(config, "5521997557364", ["old"], 1, redis)
    try:
        with pytest.raises(revision.Superseded) as error:
            await revision.check_before_write()
        assert error.value.result() == {"answer": None, "step": "superseded", "not_committed": True}
        assert not isinstance(error.value, (HTTPException, ValueError))
    finally:
        revision.reset(token)


@pytest.mark.asyncio
async def test_after_a_write_starts_never_claim_non_commit_or_authorize_replay(config):
    redis = Redis()
    token = revision.bind(config, "5521997557364", ["old"], 1, redis)
    try:
        await revision.check_before_write()
        redis.valid = 0
        with pytest.raises(revision.Superseded) as error:
            await revision.check_before_write()
        assert error.value.result() == {"answer": None, "step": "superseded_after_write", "not_committed": False}
    finally:
        revision.reset(token)


@pytest.mark.asyncio
async def test_redis_failure_is_uncertain_not_a_zero_write_replay_ack(config):
    token = revision.bind(config, "5521997557364", ["old"], 1, Redis(RuntimeError("unavailable")))
    try:
        with pytest.raises(HTTPException) as error:
            await revision.check_before_write()
        assert error.value.status_code == 503
    finally:
        revision.reset(token)


@pytest.mark.asyncio
async def test_parallel_tenant_contact_guards_never_share_latch_or_expected_revision(config):
    async def turn(tenant, phone, valid):
        token = revision.bind(tenant, phone, ["same-message-id"], 1, Redis(valid))
        try:
            await asyncio.sleep(0)
            try:
                await revision.check_before_write();return "started"
            except revision.Superseded as error:
                return error.not_committed
        finally:
            revision.reset(token)
    assert await asyncio.gather(turn(config, "5521997557364", 1), turn(uuid4(), "5521997557365", 0)) == ["started", True]
    await revision.check_before_write()  # No context: unrelated API/Meta callers remain untouched.


@pytest.mark.parametrize("expected,ids", [(None, ["one"]), (0, ["one"]), (True, ["one"]),
    (1, []), (1, ["one", "one"]), (1, ["bad\nid"]), (1, [str(x) for x in range(7)])])
def test_missing_revision_or_invalid_group_cannot_bind(config, expected, ids):
    with pytest.raises(HTTPException):
        revision.bind(config, "5521997557364", ids, expected, Redis())
