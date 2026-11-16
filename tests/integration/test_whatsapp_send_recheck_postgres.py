"""Real PostgreSQL authority during visual preparation; zero provider sends."""
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import HTTPException
import pytest
import pytest_asyncio
from sqlalchemy import text

from app import inbox_control as control, inbox_runtime, qr_channels
from app.database import tenant_transaction
from app.qr_channels import ChannelIdentity
from tests.integration.test_inbox_tools_postgres import inbox_db
from tests.integration.test_automation_postgres import automation_db, PHONE
from test_subscription_devices_postgres import (
    device_case, disposable_environment, new_owner_http, onboarding_db,
    create, settings, action, event, permit, internal, scope,
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def pacing_device(device_case):
    c = device_case
    try:
        yield c
    finally:
        # The base device fixture predates campaign opt-outs. Remove only this
        # scenario's FK before its parent deletes the synthetic source event.
        async with c.ctx.migration.begin() as db:
            await db.execute(text("""DELETE FROM subscription_campaign_permissions WHERE tenant_id=:t
                AND event_id IN (SELECT id FROM subscription_device_events WHERE tenant_id=:t
                  AND provider_event_id='SYNTHETIC_OPTOUT_DURING_PREPARATION')"""), {"t":c.tenant})


async def device_attempt(c):
    await create(c)
    await settings(c, lambda value: value["business_hours"].update(enabled=True, outside_message="Fora do horário"))
    await action(c, "connect")
    incoming = await event(c, message_id="SYNTHETIC_PREPARATION_INPUT")
    effect, = incoming["effects"]
    assert (await permit(c, effect))["authorized"]
    return effect


async def device_snapshot(c, effect):
    async with c.ctx.migration.connect() as db:
        return dict((await db.execute(text("SELECT * FROM subscription_device_effects WHERE id=:id"),
            {"id": UUID(effect["operation_id"])})).mappings().one())


async def test_device_text_recheck_is_read_only_and_does_not_consume_another_cooldown(pacing_device):
    c = pacing_device
    effect = await device_attempt(c)
    before = await device_snapshot(c, effect)
    for _ in range(3):
        result = await internal(c, "/effects/authorize", {**scope(c), "operation_id": effect["operation_id"], "recheck": True})
        assert result.status_code == 200 and result.json()["authorized"], result.text
        assert result.json()["payload"] == before["payload"]
    assert await device_snapshot(c, effect) == before and before["attempts"] == 1 and before["status"] == "running"


@pytest.mark.parametrize("changed", ["expired", "optout", "fence", "revision"])
async def test_device_preparation_loses_authority_without_relabelling_the_running_attempt(pacing_device, changed):
    c = pacing_device
    effect = await device_attempt(c)
    headers = None
    if changed == "optout":
        await event(c,event_id="SYNTHETIC_OPTOUT_DURING_PREPARATION",message_id="SYNTHETIC_OPTOUT",
            text="Não quero mensagens promocionais")
    async with c.ctx.migration.begin() as db:
        if changed == "expired":
            await db.execute(text("UPDATE subscription_device_effects SET expires_at=clock_timestamp()-interval '1 second' WHERE id=:id"), {"id": UUID(effect["operation_id"])})
        elif changed == "optout":
            assert (await db.execute(text("SELECT revoked FROM subscription_campaign_permissions WHERE tenant_id=:t AND phone=:phone"),
                {"t":c.tenant,"phone":PHONE.lstrip("+")})).scalar_one() is True
        elif changed == "fence":
            await db.execute(text("UPDATE qr_channels SET fence=fence+1 WHERE id=:id"), {"id": c.row["id"]})
            headers = c.bridge.headers(c.row, live=True)
            headers["X-Orion-Channel-Fence"] = str(int(headers["X-Orion-Channel-Fence"])+1)
        else:
            await db.execute(text("UPDATE subscription_devices SET revision=revision+1 WHERE id=:id"), {"id": UUID(c.device["id"])})
    before = await device_snapshot(c, effect)
    result = await internal(c, "/effects/authorize", {**scope(c), "operation_id": effect["operation_id"], "recheck": True}, headers=headers)
    assert result.status_code in {200, 409}, result.text
    if result.status_code == 200: assert result.json()["authorized"] is False
    assert await device_snapshot(c, effect) == before and before["attempts"] == 1 and before["status"] == "running"


@pytest_asyncio.fixture
async def live_inbox(inbox_db, monkeypatch, request):
    c = inbox_db
    from app import inbox_send_recheck
    monkeypatch.setattr(control, "get_session_factory", lambda: c.factory)
    monkeypatch.setenv("INBOX_SEND_ENABLED","1")
    async with c.migration.begin() as db:
        await db.execute(text("UPDATE tenant_plan_controls SET manual_plan_code='elite',version=version+1 WHERE tenant_id=:t"), {"t":c.tenant})
        await db.execute(text("UPDATE tenants SET bot_enabled=true WHERE id=:t"), {"t":c.tenant})
        lease = uuid4()
        await db.execute(text("""UPDATE qr_channels SET assistant_enabled=true,lease_owner=:owner,fence=1,
            lease_expires_at=clock_timestamp()+interval '30 seconds' WHERE id=:c"""), {"owner":lease, "c":c.channel})
    state = SimpleNamespace(identity=ChannelIdentity(c.channel,c.tenant,c.account,"baileys",1,lease,True,True))
    async def authenticated(request, *, phone):
        assert phone == PHONE.lstrip("+")
        return state.identity
    monkeypatch.setattr(qr_channels, "authenticated_channel", authenticated)
    revision = await control.assistant_revision(c.tenant,c.channel,PHONE)
    source = getattr(request,"param","assistant")
    if source == "manual":
        changed = await control.change_control(c.tenant,c.channel,
            control.Change(contact_e164=PHONE,expected_revision=revision,mode="human"),c.actor)
        revision = changed["revision"]
    payload = control.SendSlot(phone=PHONE.lstrip("+"),operation_id=uuid4(),source=source,control_revision=revision,
        text="Resposta sintética preparada",fingerprint=inbox_runtime.fingerprint(PHONE,"Resposta sintética preparada"))
    if source == "manual":
        async with c.factory() as db:
            async with tenant_transaction(db,c.tenant):
                row = await control.lock_control(db,c.tenant,c.channel,PHONE)
                version = await inbox_runtime.operator_version(db,c.tenant,c.account)
                await inbox_runtime.record_outbound(db,c.tenant,row,payload.operation_id,"manual",payload.text,payload.fingerprint,
                    status="sending",actor=c.account,auth_version=version)
                await db.execute(text("""UPDATE inbox_controls SET pending_operation=:op,pending_fingerprint=:fp,
                    pending_since=clock_timestamp() WHERE tenant_id=:t AND id=:id"""),
                    {"op":payload.operation_id,"fp":payload.fingerprint,"t":c.tenant,"id":row["id"]})
    assert (await control.authorize_send(c.channel,payload,None))["authorized"]
    return c,state,payload


async def inbox_snapshot(c, operation):
    async with c.factory() as db:
        async with tenant_transaction(db,c.tenant):
            message = dict((await db.execute(text("SELECT * FROM inbox_messages WHERE tenant_id=:t AND id=:id"),
                {"t":c.tenant,"id":operation})).mappings().one())
            row = dict((await db.execute(text("SELECT * FROM inbox_controls WHERE tenant_id=:t AND id=:id"),
                {"t":c.tenant,"id":message["control_id"]})).mappings().one())
            return {"message":message,"control":row}


@pytest.mark.parametrize("live_inbox",["assistant","manual"],indirect=True)
async def test_inbox_send_recheck_has_same_owner_lease_body_and_never_mutates_or_releases_slot(live_inbox):
    c,state,payload = live_inbox
    before = await inbox_snapshot(c,payload.operation_id)
    assert before["message"]["envelope"]["_qr_send_grant"]["owner"] == str(state.identity.lease_owner)
    for _ in range(3): assert (await control.authorize_send(c.channel,payload.model_copy(update={"recheck":True}),None))["authorized"]
    assert await inbox_snapshot(c,payload.operation_id) == before


@pytest.mark.parametrize("changed", ["lease", "expired", "missing", "text", "operation", "new_input", "human"])
async def test_inbox_preparation_cannot_cross_changed_authority_or_replay_unknown_work(live_inbox, changed):
    c,state,payload = live_inbox
    if changed == "lease":
        owner = uuid4()
        async with c.migration.begin() as db:
            await db.execute(text("UPDATE qr_channels SET lease_owner=:owner,fence=2 WHERE id=:id"), {"owner":owner,"id":c.channel})
        state.identity = replace(state.identity,lease_owner=owner,fence=2)
    elif changed in {"expired","missing"}:
        async with c.migration.begin() as db:
            if changed == "missing":
                await db.execute(text("UPDATE inbox_messages SET envelope=envelope-'_qr_send_grant' WHERE id=:id"), {"id":payload.operation_id})
            else:
                await db.execute(text("""UPDATE inbox_messages SET envelope=jsonb_set(envelope,'{_qr_send_grant,expires_at}',
                    to_jsonb(extract(epoch from clock_timestamp())-1)) WHERE id=:id"""), {"id":payload.operation_id})
    elif changed in {"new_input","human"}:
        async with c.factory() as db:
            async with tenant_transaction(db,c.tenant):
                await inbox_runtime.record_inbound(db,c.tenant,c.channel,PHONE,"synthetic-new-input",
                    "Quero falar com atendente" if changed == "human" else "Corrigindo a solicitação")
    before = await inbox_snapshot(c,payload.operation_id)
    request = payload.model_copy(update={"recheck":True})
    if changed == "text": request = request.model_copy(update={"text":"Outro texto"})
    if changed == "operation": request = request.model_copy(update={"operation_id":uuid4()})
    with pytest.raises(HTTPException): await control.authorize_send(c.channel,request,None)
    assert await inbox_snapshot(c,payload.operation_id) == before
    assert before["control"]["pending_operation"] == payload.operation_id


@pytest.mark.parametrize("live_inbox",["manual"],indirect=True)
async def test_manual_operator_revoked_during_preparation_cannot_recheck_or_release_slot(live_inbox):
    c,state,payload = live_inbox
    async with c.migration.begin() as db:
        await db.execute(text("UPDATE tenant_memberships SET active=false WHERE tenant_id=:t AND account_id=:a"),
            {"t":c.tenant,"a":c.account})
    before = await inbox_snapshot(c,payload.operation_id)
    with pytest.raises(HTTPException): await control.authorize_send(c.channel,payload.model_copy(update={"recheck":True}),None)
    assert await inbox_snapshot(c,payload.operation_id) == before
