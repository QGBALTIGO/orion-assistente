"""Adopted legacy ingress with real control gates and local SQL/Redis fakes."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import HTTPException
import pytest

from app import inbox_control as control, qr_ai, qr_channel_scope as scope
from app import qr_intake_revision as revision
from test_qr_channels_scope import PHONE, ingress, request  # noqa: F401


class Result:
    def __init__(self, row=None):
        self.row = row

    def mappings(self):
        return self

    def one(self):
        assert self.row is not None
        return dict(self.row)

    def one_or_none(self):
        return dict(self.row) if self.row is not None else None

    def scalar_one_or_none(self):
        assert self.row is None or isinstance(self.row, int)
        return self.row


@pytest.fixture
def adopted(ingress, monkeypatch):
    original, _, identities, redis, calls, _, _, api = ingress
    identity = replace(original, legacy_transport=True, central_adopted=True)
    identities[str(identity.channel_id)] = identity
    monkeypatch.setenv("INBOX_CONTROL_ENABLED", "0")
    row = {"id": uuid4(), "mode": "ai", "revision": 7, "pending_operation": None}
    state = SimpleNamespace(identity=identity, row=row, sql=[], fail=False, resume_revision=None, identities=identities,
                            redis=redis, calls=calls, api=api)

    class DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def execute(self, statement, values):
            sql = " ".join(str(statement).split())
            state.sql.append(sql)
            if state.fail:
                raise HTTPException(503, "Controle sintético indisponível")
            assert values["t"] == identity.tenant_id
            if "FROM inbox_control_events" in sql:
                return Result(state.resume_revision)
            assert values["channel"] == identity.channel_id
            if "phone" in values:
                assert values["phone"] == "+" + PHONE
            if sql.startswith("INSERT INTO inbox_channel_links") or sql.startswith("INSERT INTO inbox_controls"):
                return Result()
            assert "FROM inbox_controls" in sql
            return Result(row)

    @asynccontextmanager
    async def transaction(db, tenant):
        if scope.current() is not None and tenant != scope.current().tenant_id:
            raise HTTPException(409, "Autorização sintética de outro estabelecimento")
        assert tenant == identity.tenant_id
        yield db

    monkeypatch.setattr(control, "get_session_factory", lambda: DB)
    monkeypatch.setattr(control, "tenant_transaction", transaction)
    return state


def assert_unbound():
    assert scope.current() is None
    assert control._assistant_scope.get() is None
    assert revision._turn.get() is None


@pytest.mark.parametrize("global_control", ["0", "1"])
async def test_adopted_ai_uses_persistent_revision_with_global_control_off(adopted, monkeypatch, global_control):
    monkeypatch.setenv("INBOX_CONTROL_ENABLED", global_control)
    response = await request(adopted.api, adopted.identity)
    assert response.status_code == 200, response.text
    assert response.json()["step"] == "confirmed"
    assert response.json()["control_revision"] == 7
    assert len(adopted.calls) == 1 and adopted.calls[0][2] == PHONE
    assert any("FROM inbox_controls" in sql for sql in adopted.sql)
    assert_unbound()


@pytest.mark.parametrize("mode,pending", [("human", None), ("paused", None), ("waiting", None),
                                        ("closed", None), ("ai", "synthetic-pending")])
async def test_adopted_human_pause_or_pending_slot_never_runs_ai(adopted, mode, pending):
    adopted.row.update(mode=mode, pending_operation=pending)
    response = await request(adopted.api, adopted.identity)
    assert response.status_code == 200 and response.json() == {"answer": None, "step": "human"}
    assert not adopted.calls and not adopted.redis.transactions
    assert adopted.row["mode"] == mode and adopted.row["pending_operation"] == pending
    assert_unbound()


async def test_adopted_control_failure_restores_request_context(adopted):
    adopted.fail = True
    response = await request(adopted.api, adopted.identity)
    assert response.status_code == 503
    assert not adopted.calls and not adopted.redis.transactions
    assert_unbound()


async def test_adopted_missing_intake_revision_does_not_create_consent(adopted):
    response = await request(adopted.api, adopted.identity, announce=False)
    assert response.status_code == 422
    assert not adopted.calls and not adopted.redis.events and not adopted.redis.transactions
    assert_unbound()


async def test_adopted_stale_notice_and_duplicate_keep_existing_receipts(adopted):
    response = await request(adopted.api, adopted.identity, intake_revision=99999)
    assert response.json() == {"answer": None, "step": "superseded", "not_committed": True,
                               "control_revision": 7}
    assert not adopted.calls and not adopted.redis.transactions
    response = await request(adopted.api, adopted.identity)
    assert response.status_code == 200 and response.json()["step"] == "confirmed"
    assert (await request(adopted.api, adopted.identity)).json()["step"] == "duplicate"
    assert len(adopted.calls) == 1 and len(adopted.redis.transactions) == 1
    assert_unbound()


@pytest.mark.parametrize("field", ["fence", "lease_owner", "owner_account_id", "tenant_id", "channel_id",
                                   "assistant_enabled", "allowed_sender"])
async def test_adopted_identity_change_before_first_write_remains_rejected(adopted, monkeypatch, field):
    async def turn(*_args, **_kwargs):
        value = 2 if field == "fence" else False if field in {"assistant_enabled", "allowed_sender"} else uuid4()
        adopted.identities[str(adopted.identity.channel_id)] = replace(adopted.identity, **{field: value})
        await revision.check_before_write()
        adopted.calls.append("must not write")
    monkeypatch.setattr(qr_ai, "turn", turn)
    response = await request(adopted.api, adopted.identity)
    assert response.status_code in {409, 423} and not adopted.calls and not adopted.redis.transactions
    assert_unbound()


async def test_adopted_handoff_during_inference_prevents_first_write(adopted, monkeypatch):
    async def turn(*_args, **_kwargs):
        adopted.row.update(mode="human", revision=8)
        await revision.check_before_write()
        adopted.calls.append("must not write")
    monkeypatch.setattr(qr_ai, "turn", turn)
    response = await request(adopted.api, adopted.identity)
    assert response.status_code == 409 and not adopted.calls and not adopted.redis.transactions
    assert adopted.row["mode"] == "human" and adopted.row["revision"] == 8
    assert_unbound()


async def test_adopted_cancelled_turn_restores_all_request_context(adopted, monkeypatch):
    async def cancelled(*_args, **_kwargs):
        assert scope.current() == adopted.identity
        assert control._assistant_scope.get()[3] == 7
        raise asyncio.CancelledError()
    monkeypatch.setattr(qr_ai, "turn", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await request(adopted.api, adopted.identity)
    assert not adopted.calls and not adopted.redis.transactions
    assert_unbound()


@pytest.mark.parametrize("operator_resume", [False, True])
async def test_adopted_ai_authority_clears_old_human_context_without_global_flag(adopted, monkeypatch, operator_resume):
    state_key = f"orion:qr:ai:{adopted.identity.tenant_id}:{PHONE}"
    adopted.redis.values[state_key] = json.dumps({"step": "human", "old": True})
    adopted.redis.values[state_key + ":commerce"] = json.dumps({"pending": {"old": True}})
    adopted.redis.values[state_key + ":birthday-invite"] = "old-invitation"
    adopted.redis.values[state_key + ":verified-receipt"] = "preserved-receipt"
    adopted.resume_revision = 7 if operator_resume else None
    seen = []

    async def turn(*_args, **_kwargs):
        raw = await adopted.redis.get(state_key)
        assert not raw or json.loads(raw).get("step") != "human"
        await revision.check_before_write()
        seen.append(raw)
        return {"answer": "Retomada sintética", "step": "review"}

    monkeypatch.setattr(qr_ai, "turn", turn)
    response = await request(adopted.api, adopted.identity)
    assert response.status_code == 200 and response.json()["step"] == "review"
    assert seen == [None] and adopted.redis.values[state_key + ":verified-receipt"] == "preserved-receipt"
    assert state_key + ":commerce" not in adopted.redis.values
    assert state_key + ":birthday-invite" not in adopted.redis.values
    if operator_resume:
        assert str(adopted.redis.values[state_key + ":ai-resume-generation"]) == "7"
        draft = json.dumps({"step": "confirm", "new_draft": True})
        adopted.redis.values[state_key] = draft
        response = await request(adopted.api, adopted.identity, message_id="synthetic-next")
        assert response.status_code == 200 and seen == [None, draft]
        assert adopted.redis.values[state_key] == draft
    assert_unbound()


async def test_adopted_new_handoff_uses_persistent_controller_without_legacy_pause(adopted, monkeypatch):
    from app import automation_runtime

    async def interaction(_tenant, _phone, *, channel_identity=None, human=False):
        assert channel_identity == adopted.identity and human is False

    handoff = AsyncMock()
    monkeypatch.setattr(automation_runtime, "interaction", interaction)
    monkeypatch.setattr(control, "customer_handoff", handoff)
    monkeypatch.setattr(qr_ai, "turn", AsyncMock(return_value={"answer": None, "step": "human"}))
    response = await request(adopted.api, adopted.identity)
    assert response.status_code == 200 and response.json() == {"answer": None, "step": "human"}
    handoff.assert_awaited_once_with(adopted.identity.tenant_id, adopted.identity.channel_id, PHONE)
    assert_unbound()
