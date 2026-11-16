"""Notice send proof comes from the exact current SQL journal, not Redis order."""
import hashlib
import json
import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text

from app import qr_ai, qr_channel_scope, inbox_control
from tests.integration.test_qr_native_selection_roundtrip_postgres import (  # noqa: F401
    roundtrip_case, inbox_db, automation_db, PHONE,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
RAW = "synthetic-notice-raw-id"


async def journal(c, message_id=RAW, *, kind="audio"):
    response = await c.post("/messages", {"phone": PHONE.removeprefix("+"), "message_id": message_id,
        "text": "", "kind": kind})
    assert response.status_code == 200, response.text


async def notice(c, message_id=RAW):
    response = await c.post("/interaction", {"phone": PHONE.removeprefix("+"), "message_id": message_id})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["recorded"] is True and type(result["revision"]) is int
    assert qr_channel_scope.current() is None and inbox_control._assistant_scope.get() is None
    return result


async def snapshot(c):
    async with c.db.migration.connect() as db:
        return {table: (await db.execute(text(f"SELECT to_jsonb(x) FROM {table} x WHERE tenant_id=:t ORDER BY id"),
            {"t": c.db.tenant})).scalars().all()
            for table in ("inbox_controls", "inbox_messages", "inbox_control_events")}


@pytest.mark.parametrize("global_control,kind", [("0", "audio"), ("1", "audio"), ("0", "text")])
async def test_notice_returns_sql_revision_for_exact_input_and_never_redis_intake_counter(roundtrip_case, monkeypatch, global_control, kind):
    c = roundtrip_case
    monkeypatch.setenv("INBOX_CONTROL_ENABLED", global_control)
    await journal(c, kind=kind)
    before = await snapshot(c)
    result = await notice(c)
    assert result["revision"] == 1
    assert result["control_revision"] == 2 and result["control_revision"] != result["revision"]
    assert type(result["control_revision"]) is int
    assert await snapshot(c) == before
    duplicate = await notice(c)
    assert duplicate == result and await snapshot(c) == before
    assert not c.db.sent


async def test_notice_without_journal_creates_no_control_and_returns_no_send_grant(roundtrip_case):
    c = roundtrip_case
    before = await snapshot(c)
    assert before["inbox_controls"] == []
    result = await notice(c)
    assert result["control_revision"] is None and await snapshot(c) == before


async def test_old_input_never_borrows_current_revision_of_the_next_input(roundtrip_case):
    c = roundtrip_case
    await journal(c)
    first = await notice(c)
    await journal(c, "synthetic-later-input")
    second = await notice(c, "synthetic-later-input")
    assert second["control_revision"] == first["control_revision"] + 1
    assert (await notice(c))["control_revision"] is None


@pytest.mark.parametrize("change", ["human", "pending", "wrong_raw_id", "foreign_contact", "message_revision"])
async def test_notice_denies_noncurrent_or_busy_proofs_without_changing_safety_registration(roundtrip_case, change):
    c = roundtrip_case
    await journal(c)
    if change == "pending":
        proof = await notice(c)
        body = "Resposta sintética aguardando recibo"
        slot = await c.post("/send-authorize", {"phone": PHONE.removeprefix("+"), "operation_id": str(uuid4()),
            "source": "assistant", "control_revision": proof["control_revision"], "text": body,
            "fingerprint": hashlib.sha256(json.dumps([PHONE.removeprefix("+"), body], ensure_ascii=False,
                separators=(",", ":")).encode()).hexdigest()})
        assert slot.status_code == 200, slot.text
    message_id = RAW
    async with c.db.migration.begin() as db:
        values = {"t": c.db.tenant, "c": c.channel, "phone": PHONE, "id": RAW}
        if change == "human":
            await db.execute(text("UPDATE inbox_controls SET mode='human' WHERE tenant_id=:t AND channel_id=:c"), values)
        elif change == "pending":
            pass  # The normal send-authorize endpoint reserved the real slot.
        elif change == "wrong_raw_id":
            message_id = hashlib.sha256(RAW.encode()).hexdigest()
        elif change == "foreign_contact":
            await db.execute(text("UPDATE inbox_controls SET contact_e164='+5567999999902' WHERE tenant_id=:t AND channel_id=:c"), values)
        else:
            await db.execute(text("UPDATE inbox_messages SET control_revision=control_revision+1 WHERE tenant_id=:t AND external_id=:id"), values)
    before = await snapshot(c)
    assert (await notice(c, message_id))["control_revision"] is None
    assert await snapshot(c) == before and not c.db.sent


async def test_foreign_tenant_journal_with_same_raw_id_is_not_a_send_grant(roundtrip_case):
    c = roundtrip_case
    foreign_channel, foreign_control = uuid4(), uuid4()
    async with c.db.migration.begin() as db:
        await db.execute(text("INSERT INTO tenant_memberships(tenant_id,account_id,role) VALUES(:t,:a,'owner')"),
            {"t": c.db.other, "a": c.db.account})
        await db.execute(text("""INSERT INTO qr_channels(id,tenant_id,owner_account_id,provider,label,
            credential_hash,credential_encrypted,allowed_senders,enabled,assistant_enabled,
            lease_owner,lease_expires_at,fence,idempotency_key,request_fingerprint)
            VALUES(:c,:t,:a,'baileys','Foreign synthetic',:fp,'unused',
            CAST(:senders AS jsonb),true,true,:owner,clock_timestamp()+interval '5 minutes',1,:registration,:fp)"""),
            {"c": foreign_channel, "t": c.db.other, "a": c.db.account, "owner": uuid4(), "registration": uuid4(), "fp": "f"*64,
             "senders": json.dumps([PHONE.removeprefix("+")])})
        await db.execute(text("INSERT INTO inbox_channel_links(tenant_id,channel_id,provider,qr_channel_id) VALUES(:t,:c,'qr',:c)"),
            {"t": c.db.other, "c": foreign_channel})
        await db.execute(text("INSERT INTO inbox_controls(id,tenant_id,channel_id,contact_e164,revision) VALUES(:id,:t,:c,:p,2)"),
            {"id": foreign_control, "t": c.db.other, "c": foreign_channel, "p": PHONE})
        await db.execute(text("""INSERT INTO inbox_messages(tenant_id,control_id,direction,source,kind,external_id,control_revision,status)
            VALUES(:t,:c,'in','customer','audio',:id,2,'received')"""),
            {"t": c.db.other, "c": foreign_control, "id": RAW})
    before = await snapshot(c)
    assert (await notice(c))["control_revision"] is None and await snapshot(c) == before


async def test_revoked_fence_during_optional_read_returns_no_grant_but_preserves_notice(roundtrip_case, monkeypatch):
    c = roundtrip_case
    await journal(c)
    original = qr_ai._interaction_control_revision
    async def revoke(identity, phone, message_id, guard):
        async with c.db.migration.begin() as db:
            await db.execute(text("UPDATE qr_channels SET fence=fence+1 WHERE id=:c"), {"c": c.channel})
        return await original(identity, phone, message_id, guard)
    monkeypatch.setattr(qr_ai, "_interaction_control_revision", revoke)
    before = await snapshot(c)
    assert (await notice(c))["control_revision"] is None and await snapshot(c) == before


async def test_assistant_off_notice_remains_recorded_without_grant(roundtrip_case):
    c = roundtrip_case
    await journal(c)
    async with c.db.migration.begin() as db:
        await db.execute(text("UPDATE qr_channels SET assistant_enabled=false WHERE id=:c"), {"c": c.channel})
    before = await snapshot(c)
    assert (await notice(c))["control_revision"] is None and await snapshot(c) == before


async def test_later_input_after_read_is_still_rejected_by_normal_send_authorization(roundtrip_case, monkeypatch):
    c = roundtrip_case
    await journal(c)
    original = qr_ai._interaction_control_revision
    async def subsequent_input(identity, phone, message_id, guard):
        revision = await original(identity, phone, message_id, guard)
        await journal(c, "synthetic-input-after-proof")
        return revision
    monkeypatch.setattr(qr_ai, "_interaction_control_revision", subsequent_input)
    result = await notice(c)
    assert result["control_revision"] == 2
    body = "Esclarecimento sintético"
    response = await c.post("/send-authorize", {"phone": PHONE.removeprefix("+"), "operation_id": str(uuid4()),
        "source": "assistant", "control_revision": result["control_revision"], "text": body,
        "fingerprint": hashlib.sha256(json.dumps([PHONE.removeprefix("+"), body], ensure_ascii=False,
            separators=(",", ":")).encode()).hexdigest()})
    assert response.status_code == 409, response.text
    after = await snapshot(c)
    assert not any(row["direction"] == "out" for row in after["inbox_messages"])
    assert all(row["pending_operation"] is None for row in after["inbox_controls"])
    assert not c.db.sent


@pytest.mark.parametrize("failure", ["connection", "deadline"])
async def test_optional_proof_failure_preserves_notice_without_a_send_grant(roundtrip_case, monkeypatch, failure):
    c = roundtrip_case
    await journal(c)
    before = await snapshot(c)
    original = qr_ai._interaction_control_revision
    class UnavailableRead:
        async def __aenter__(self):
            if failure == "connection":
                raise ConnectionError("Synthetic read unavailable")
            await asyncio.sleep(10)  # The real existing two-second deadline must cancel this read.
            raise AssertionError("The optional proof exceeded its deadline")
        async def __aexit__(self, *_args):
            return False
    async def fail_read(identity, phone, message_id, guard):
        with monkeypatch.context() as patch:
            patch.setattr(qr_ai, "get_session_factory", lambda: UnavailableRead)
            return await original(identity, phone, message_id, guard)
    monkeypatch.setattr(qr_ai, "_interaction_control_revision", fail_read)
    assert (await notice(c))["control_revision"] is None
    assert await snapshot(c) == before and not c.db.sent
