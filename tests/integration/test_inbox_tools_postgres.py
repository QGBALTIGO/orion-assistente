"""Real PostgreSQL/RLS/CAS. No provider or production data is contacted."""
import asyncio
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import inbox_api as api, inbox_control as control
from app.catalog_api import Actor
from app.database import tenant_transaction
from app.inbox_templates import LabelSave, QuickReplySave
from tests.integration.test_automation_postgres import automation_db, PHONE

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def inbox_db(automation_db, monkeypatch):
    ctx = automation_db
    monkeypatch.setenv("INBOX_TOOLS_ENABLED", "1")
    monkeypatch.setenv("INBOX_CONTROL_ENABLED", "1")
    try:
        yield ctx
    finally:
        async with ctx.migration.begin() as db:
            for table in ("inbox_controls", "inbox_customer_labels", "inbox_labels", "inbox_quick_replies"):
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
