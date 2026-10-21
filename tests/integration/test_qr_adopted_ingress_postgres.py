"""Real QR authentication/RLS/authority locks; inference/transport are local fakes."""
import json
from uuid import uuid4

from fastapi import FastAPI
import httpx
import pytest
from sqlalchemy import text

from app import database, inbox_control as control, qr_ai, qr_channels, qr_legacy
from app import qr_channel_scope as scope
from app.database import tenant_transaction
from tests.integration.test_automation_postgres import automation_db, PHONE  # noqa: F401
from tests.integration.test_inbox_tools_postgres import inbox_db  # noqa: F401

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("global_control,mode,revoke_before_control", [
    ("0", None, False), ("1", None, False), ("0", "human", False),
    ("0", "paused", False), ("0", None, True),
])
async def test_adopted_ingress_reads_initial_authority_with_real_transaction_guard(
        inbox_db, monkeypatch, global_control, mode, revoke_before_control):
    c = inbox_db
    channel = qr_legacy.channel_id(c.tenant)
    owner = uuid4()
    key = "synthetic-adopted-ingress-secret-" + str(uuid4())
    monkeypatch.setenv("INBOX_CONTROL_ENABLED", global_control)
    monkeypatch.setenv("INBOX_SEND_ENABLED", "0")
    monkeypatch.setenv("QR_LEGACY_CENTRAL_ENABLED", "1")
    monkeypatch.setenv("QR_AI_ENABLED", "1")
    monkeypatch.setenv("QR_AI_TENANT_ID", str(c.tenant))
    monkeypatch.setattr(database, "get_session_factory", lambda: c.factory)
    monkeypatch.setattr(control, "get_session_factory", lambda: c.factory)
    monkeypatch.setattr(qr_ai, "get_session_factory", lambda: c.factory)
    async with c.migration.begin() as db:
        await db.execute(text("UPDATE tenants SET status='active',bot_enabled=true WHERE id=:t"), {"t": c.tenant})
        await db.execute(text("""INSERT INTO qr_channels(id,tenant_id,owner_account_id,provider,label,
            credential_hash,credential_encrypted,allowed_senders,enabled,assistant_enabled,
            lease_owner,lease_expires_at,fence,idempotency_key,request_fingerprint)
            VALUES(:id,:t,:a,'baileys','Alias sintético',:key,'unused-synthetic',CAST(:senders AS jsonb),
            true,true,:owner,clock_timestamp()+interval '10 minutes',4,:registration,:fingerprint)"""),
            {"id": channel, "t": c.tenant, "a": c.account, "key": qr_channels.credential_hash(key),
             "senders": json.dumps([PHONE.removeprefix("+")]), "owner": owner,
             "registration": uuid4(), "fingerprint": "b" * 64})
        await db.execute(text("""INSERT INTO inbox_channel_links(tenant_id,channel_id,provider,qr_channel_id)
            VALUES(:t,:c,'qr',:c)"""), {"t": c.tenant, "c": channel})
        if mode:
            await db.execute(text("""INSERT INTO inbox_controls(tenant_id,channel_id,contact_e164,mode)
                VALUES(:t,:c,:phone,:mode)"""), {"t": c.tenant, "c": channel, "phone": PHONE, "mode": mode})

    calls = []

    async def inference(tenant, phone, _body, namespace, _message_id, **kwargs):
        assert tenant == c.tenant and phone == PHONE.removeprefix("+") and namespace == phone
        assert kwargs["intake_revision"] == 1
        assert scope.current().central_adopted and control._assistant_scope.get()[3] == 1
        # Real transaction_guard repeats membership/fence/company and the
        # persistent control revision before and after this synthetic read.
        async with c.factory() as db:
            async with tenant_transaction(db, c.tenant):
                assert (await db.execute(text("SELECT 1"))).scalar_one() == 1
        calls.append("synthetic-inference")
        return {"answer": "Resposta sintética sem envio", "step": "review"}

    monkeypatch.setattr(qr_ai, "run_locked", inference)
    if revoke_before_control:
        original_ready = qr_ai._managed_assistant_ready

        async def revoke_after_authentication(identity):
            ready = await original_ready(identity)
            async with c.migration.begin() as db:
                await db.execute(text("UPDATE qr_channels SET fence=fence+1 WHERE id=:id"), {"id": channel})
            return ready

        monkeypatch.setattr(qr_ai, "_managed_assistant_ready", revoke_after_authentication)

    api = FastAPI()
    api.include_router(qr_ai.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="https://test") as client:
        response = await client.post(f"/internal/qr/channels/{channel}/ai/reply",
            headers={"X-Orion-Channel-Key": key, "X-Orion-Channel-Owner": str(owner), "X-Orion-Channel-Fence": "4"},
            json={"phone": PHONE.removeprefix("+"), "message_id": "synthetic-ingress", "text": "Olá", "intake_revision": 1})
    if revoke_before_control:
        assert response.status_code == 409 and not calls
    elif mode:
        assert response.status_code == 200 and response.json() == {"answer": None, "step": "human"}
        assert not calls
    else:
        assert response.status_code == 200, response.text
        assert response.json()["step"] == "review" and response.json()["control_revision"] == 1
        assert calls == ["synthetic-inference"]
    assert scope.current() is None and control._assistant_scope.get() is None and scope._hold.get() is None
    async with c.factory() as db:
        async with tenant_transaction(db, c.tenant):
            row = (await db.execute(text("""SELECT mode,revision FROM inbox_controls
                WHERE tenant_id=:t AND channel_id=:c AND contact_e164=:phone"""),
                {"t": c.tenant, "c": channel, "phone": PHONE})).mappings().one_or_none()
            assert row is None if revoke_before_control else row["mode"] == (mode or "ai") and row["revision"] == 1
    assert not c.sent
