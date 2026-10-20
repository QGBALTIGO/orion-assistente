"""Real PostgreSQL/RLS/CAS. No provider or production data is contacted."""
import asyncio
from uuid import uuid4

import pytest
import pytest_asyncio
import httpx
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import inbox_api as api, inbox_control as control
from app.catalog_api import Actor
from app.catalog_api import authenticate
from app.main import app
from app.database import tenant_transaction
from app.qr_channels import ChannelIdentity
from app.inbox_templates import LabelSave, QuickReplySave
from tests.integration.test_automation_postgres import automation_db, PHONE

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def inbox_db(automation_db, monkeypatch):
    ctx = automation_db
    monkeypatch.setenv("INBOX_TOOLS_ENABLED", "1")
    monkeypatch.setenv("INBOX_CONTROL_ENABLED", "1")
    monkeypatch.setenv("QR_CHANNELS_ENABLED", "1")
    try:
        yield ctx
    finally:
        async with ctx.migration.begin() as db:
            for table in ("inbox_control_events", "inbox_controls", "inbox_customer_labels", "inbox_labels", "inbox_quick_replies"):
                await db.execute(text(f"DELETE FROM {table} WHERE tenant_id IN (:a,:b)"), {"a": ctx.tenant, "b": ctx.other})


async def test_labels_customer_identity_and_cross_tenant_rls(inbox_db):
    c = inbox_db
    tag = await api.create_label(c.tenant, LabelSave(name="Retorno autorizado", color="#18836b"), c.actor)
    identity = tag["id"]
    for _ in range(2):
        assert (await api.assign_label(c.tenant, c.customer, identity, c.actor))["assigned"]
    crm = await api.customer_context(c.tenant, c.customer, c.actor)
    assert len(crm["labels"]) == 1
    assert crm["customer"]["id"] == str(c.customer)
    async with c.factory() as db:
        assert (await db.execute(text("SELECT id FROM inbox_labels"))).all() == []
    async with c.factory() as db:
        async with tenant_transaction(db, c.other):
            assert (await db.execute(text("SELECT id FROM inbox_labels"))).all() == []
            with pytest.raises(DBAPIError):
                await db.execute(text("INSERT INTO inbox_labels(tenant_id,name,color) VALUES(:t,'cross','#18836b')"), {"t": c.tenant})
    async with c.factory() as db:
        async with tenant_transaction(db, c.other):
            with pytest.raises(DBAPIError):
                await db.execute(text("""INSERT INTO inbox_customer_labels(tenant_id,customer_id,label_id)
                    VALUES(:t,:customer,:label)"""), {"t": c.other, "customer": c.customer, "label": identity})


async def test_reply_preview_uses_customer_facts_and_never_invents_appointment(inbox_db):
    c = inbox_db
    saved = await api.create_reply(c.tenant, QuickReplySave(title="Olá", text="Olá {{cliente.nome}}!"), c.actor)
    result = await api.preview_reply(c.tenant, saved["id"], api.Preview(customer_id=c.customer, expected_version=1), c.actor)
    assert result["text"].startswith("Olá ") and result["sent"] is False
    await api.update_reply(c.tenant, saved["id"], QuickReplySave(title="Horário", text="{{agendamento.protocolo}}", expected_version=1), c.actor)
    with pytest.raises(HTTPException) as error:
        await api.preview_reply(c.tenant, saved["id"], api.Preview(customer_id=c.customer, expected_version=2), c.actor)
    assert error.value.status_code == 422


async def test_personal_replies_are_not_visible_to_other_accounts(inbox_db):
    c = inbox_db
    saved = await api.create_reply(c.tenant, QuickReplySave(title="Privada", visibility="personal", text="Texto"), c.actor)
    other = Actor("support", account_id=uuid4())
    assert (await api.replies(c.tenant, "", other))["items"] == []
    with pytest.raises(HTTPException) as error:
        await api.delete_reply(c.tenant, saved["id"], api.Version(expected_version=1), other)
    assert error.value.status_code == 404


async def test_two_label_edits_have_exactly_one_winner(inbox_db):
    c = inbox_db
    tag = await api.create_label(c.tenant, LabelSave(name="Inicial"), c.actor)
    results = await asyncio.gather(*(api.update_label(c.tenant, tag["id"], LabelSave(name=name, expected_version=1), c.actor)
        for name in ("Primeira", "Segunda")), return_exceptions=True)
    assert sum(isinstance(x, dict) and x["version"] == 2 for x in results) == 1
    assert sum(isinstance(x, HTTPException) and x.status_code == 409 for x in results) == 1


async def test_control_two_operators_cas_and_persistent_pause(inbox_db):
    c = inbox_db
    payload = control.Change(contact_e164=PHONE, expected_revision=1, mode="human")
    results = await asyncio.gather(*(control.change_control(c.tenant, c.channel, payload, c.actor) for _ in range(2)), return_exceptions=True)
    assert sum(isinstance(x, dict) and x["mode"] == "human" for x in results) == 1
    assert sum(isinstance(x, HTTPException) and x.status_code == 409 for x in results) == 1
    assert await control.blocked(c.tenant, c.channel, PHONE)
    # An independent transaction after a simulated restart sees the same state.
    row = await control.get_control(c.tenant, c.channel, PHONE, c.actor)
    assert row["assigned_account_id"] == str(c.account) and row["revision"] == 2
    history = await control.control_history(c.tenant, c.channel, PHONE, c.actor)
    assert len(history["items"]) == 1 and history["items"][0]["actor_account_id"] == str(c.account)
    await control.change_control(c.tenant, c.channel, control.Change(contact_e164=PHONE, expected_revision=2, mode="ai"), c.actor)
    assert not await control.blocked(c.tenant, c.channel, PHONE)


async def test_pending_send_never_expires_or_allows_takeover(inbox_db):
    c = inbox_db
    operation = uuid4()
    async with c.factory() as db:
        async with tenant_transaction(db, c.tenant):
            row = await control.lock_control(db, c.tenant, c.channel, PHONE)
            await db.execute(text("""UPDATE inbox_controls SET pending_operation=:op,pending_fingerprint=:fingerprint,
                pending_since=now()-interval '7 days' WHERE tenant_id=:t AND id=:id"""),
                {"t": c.tenant, "id": row["id"], "op": operation, "fingerprint": "a"*64})
    with pytest.raises(HTTPException) as error:
        await control.change_control(c.tenant, c.channel, control.Change(contact_e164=PHONE, expected_revision=1, mode="human"), c.actor)
    assert error.value.status_code == 409
    assert await control.blocked(c.tenant, c.channel, PHONE)


async def test_http_contract_and_current_membership_reject_other_company_and_reader_writes(inbox_db):
    c = inbox_db
    app.dependency_overrides[authenticate] = lambda: c.actor
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            prefix = f"/api/v1/tenants/{c.tenant}/management/inbox"
            saved = await client.post(prefix+"/labels", json={"name":"HTTP sintético","expected_version":0})
            assert saved.status_code == 201, saved.text
            assert len((await client.get(prefix+"/labels")).json()["items"]) == 1
            assert (await client.get(f"/api/v1/tenants/{c.other}/management/inbox/labels")).status_code == 403
            context = await client.get(prefix+f"/customers/{c.customer}")
            assert context.status_code == 200 and context.json()["customer"]["id"] == str(c.customer)
            assert (await client.get(prefix+f"/customers/{uuid4()}")).status_code == 404
            app.dependency_overrides[authenticate] = lambda: Actor("viewer", tenant_id=c.tenant)
            assert (await client.post(prefix+"/labels", json={"name":"Proibido"})).status_code == 403
            assert (await client.get(prefix+"/labels")).status_code == 200
    finally:
        app.dependency_overrides.pop(authenticate, None)


async def test_business_transaction_finishes_before_handoff_and_stale_generation_cannot_write(inbox_db):
    c = inbox_db
    await control.assistant_revision(c.tenant, c.channel, PHONE)
    identity = ChannelIdentity(c.channel, c.tenant, c.account, "baileys", 1, uuid4(), True, True)
    token = control.bind_assistant(identity, PHONE, 1)
    ready, release = asyncio.Event(), asyncio.Event()

    async def business_operation():
        async with c.factory() as db:
            async with tenant_transaction(db, c.tenant):
                await control.guard_transaction(db, identity)
                ready.set()
                await release.wait()
                await db.execute(text("UPDATE customers SET notes='Synthetic committed operation' WHERE tenant_id=:t AND id=:id"), {"t": c.tenant, "id": c.customer})

    task = asyncio.create_task(business_operation())
    operator = None
    try:
        await asyncio.wait_for(ready.wait(), 3)
        operator = asyncio.create_task(control.change_control(c.tenant, c.channel,
            control.Change(contact_e164=PHONE, expected_revision=1, mode="human"), c.actor))
        done, _ = await asyncio.wait({operator}, timeout=.1)
        assert not done  # SQL SHARE lock fences the transfer until domain commit.
        release.set()
        await asyncio.wait_for(task, 3)
        assert (await asyncio.wait_for(operator, 3))["revision"] == 2
        async with c.factory() as db:
            async with tenant_transaction(db, c.tenant):
                with pytest.raises(HTTPException) as error:
                    await control.guard_transaction(db, identity)
                assert error.value.status_code == 409
    finally:
        release.set()
        await asyncio.gather(task, *([operator] if operator is not None else []), return_exceptions=True)
        control.reset_assistant(token)


async def test_real_send_slot_has_one_winner_and_requires_matching_receipt(inbox_db, monkeypatch):
    from app import qr_channels
    c = inbox_db
    lease = uuid4()
    async with c.migration.begin() as db:
        await db.execute(text("UPDATE tenants SET bot_enabled=true WHERE id=:t"), {"t":c.tenant})
        await db.execute(text("""UPDATE qr_channels SET assistant_enabled=true,lease_owner=:owner,fence=1,
          lease_expires_at=now()+interval '30 seconds' WHERE tenant_id=:t AND id=:id"""),
          {"owner": lease, "t": c.tenant, "id": c.channel})
    identity = ChannelIdentity(c.channel, c.tenant, c.account, "baileys", 1, lease, True, True)
    async def authenticated(request, *, phone):
        assert phone == PHONE.lstrip("+")
        return identity
    monkeypatch.setattr(qr_channels, "authenticated_channel", authenticated)
    first = control.SendSlot(phone=PHONE.lstrip("+"), operation_id=uuid4(), fingerprint="a"*64, source="assistant", control_revision=1)
    second = first.model_copy(update={"operation_id":uuid4()})
    outcomes = await asyncio.gather(*(control.authorize_send(c.channel, p, None) for p in (first, second)), return_exceptions=True)
    assert sum(isinstance(x, dict) and x["authorized"] for x in outcomes) == 1, [getattr(x,"detail",type(x).__name__) for x in outcomes]
    winner = first if isinstance(outcomes[0], dict) else second
    assert sum(isinstance(x, HTTPException) and x.status_code == 409 for x in outcomes) == 1
    with pytest.raises(HTTPException):
        await control.finish_send(c.channel, control.FinishSlot(**{**winner.model_dump(), "fingerprint":"b"*64}, accepted=True), None)
    assert await control.blocked(c.tenant, c.channel, PHONE)
    assert (await control.finish_send(c.channel, control.FinishSlot(**winner.model_dump(), accepted=True), None))["released"]
    assert not await control.blocked(c.tenant, c.channel, PHONE)
    # A true identity from credential lookup cannot overrule a subsequent
    # persisted assistant-off change, even before the next lease refresh.
    async with c.migration.begin() as db:
        await db.execute(text("UPDATE qr_channels SET assistant_enabled=false WHERE tenant_id=:t AND id=:c"), {"t":c.tenant,"c":c.channel})
    with pytest.raises(HTTPException) as disabled:
        await control.authorize_send(c.channel, winner.model_copy(update={"operation_id":uuid4()}), None)
    assert disabled.value.status_code == 409
    assert not await control.blocked(c.tenant,c.channel,PHONE)


async def test_concurrent_assistant_revalidations_remain_read_only_under_channel_guard(inbox_db):
    from app import qr_channel_scope
    c = inbox_db
    lease = uuid4()
    async with c.migration.begin() as db:
        await db.execute(text("UPDATE tenants SET bot_enabled=true WHERE id=:t"), {"t":c.tenant})
        await db.execute(text("""UPDATE qr_channels SET assistant_enabled=true,lease_owner=:owner,fence=1,
          lease_expires_at=now()+interval '30 seconds' WHERE tenant_id=:t AND id=:id"""),
          {"owner":lease,"t":c.tenant,"id":c.channel})
    assert await control.assistant_revision(c.tenant,c.channel,PHONE) == 1
    identity = ChannelIdentity(c.channel,c.tenant,c.account,"baileys",1,lease,True,True)
    async def fresh():
        return identity
    scope = qr_channel_scope.bind(identity,fresh)
    token = control.bind_assistant(identity,PHONE,1)
    try:
        revisions = await asyncio.wait_for(asyncio.gather(*(control.assistant_revision(c.tenant,c.channel,PHONE) for _ in range(4))),5)
        assert revisions == [1,1,1,1]
    finally:
        control.reset_assistant(token)
        qr_channel_scope.reset(scope)
