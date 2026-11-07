"""Scheduled messages through real cookies, CSRF, tenant roles and device leases.

PostgreSQL/Redis are disposable loopback services. Media/WhatsApp/ASR are
synthetic; no production contact, schedule or provider is touched.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from threading import Event
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import qr_channels, subscription_chatbot_media as media, subscription_chatbot_rules
from app import subscription_device_audio, subscription_scheduled_messages as api
from app.catalog_api import support_session_factory
from app.database import tenant_transaction
from app.plan_routing import include_plan_router
from test_subscription_campaigns_postgres import (
    campaign_case, chatbot_case, device_case, disposable_environment, new_owner_http,
    onboarding_db, private_media, group, edit_group, renew, grant, finish,
    schedule as campaign_schedule, TABLES as CAMPAIGN_TABLES, LIVE,
)  # noqa: F401 - real transitive fixtures, with synthetic transport only.
from test_subscription_chatbot_test_postgres import revoke
from test_subscription_devices_postgres import event, internal, scope, settings
from test_subscription_templates_postgres import create_template, save_template, template_definition, upload, png_bytes, wav_bytes

pytestmark = pytest.mark.integration
TABLES = ("subscription_schedules", "subscription_schedule_runs",
          "subscription_schedule_recipients", "subscription_schedule_requests")
PHONE = "5567999999901"


@pytest_asyncio.fixture
async def scheduled_case(campaign_case):
    c = campaign_case
    generic = [route for route in c.app.router.routes if "{resource}" in getattr(route, "path", "")]
    c.app.router.routes[:] = [route for route in c.app.router.routes if route not in generic]
    include_plan_router(c.app, api.router)
    c.app.router.routes.extend(generic)
    c.scheduled = f"/api/v1/tenants/{c.tenant}/subscription-whatsapp/scheduled-messages"
    c.scheduled_customers = []
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE tenants SET timezone='UTC' WHERE id=:t"), {"t": c.tenant})
    # The inherited chatbot fixture uses the default recipient phone in its
    # status event. Give this device its own distinct, verified synthetic PN.
    await event(c, kind="status", state="connected", phone="5567999999998", name="Dispositivo de agendamentos")
    try:
        yield c
    finally:
        async with c.ctx.migration.begin() as db:
            for tenant in (c.tenant, c.ctx.a, c.ctx.b):
                await db.execute(text("DELETE FROM subscription_schedule_requests WHERE tenant_id=:t"), {"t": tenant})
                await db.execute(text("DELETE FROM subscription_device_effects WHERE tenant_id=:t AND type='scheduled_action'"), {"t": tenant})
                for table in ("subscription_schedule_recipients", "subscription_schedule_runs", "subscription_schedules"):
                    await db.execute(text(f"DELETE FROM {table} WHERE tenant_id=:t"), {"t": tenant})
            for identity in c.scheduled_customers:
                await db.execute(text("DELETE FROM customers WHERE id=:id"), {"id": identity})


async def customer(c, *, phone=PHONE, name="Nome do cliente", active=True, tenant=None):
    async with c.ctx.migration.begin() as db:
        identity = (await db.execute(text("""INSERT INTO customers
            (tenant_id,name,whatsapp_e164,active) VALUES(:t,:name,:phone,:active) RETURNING id"""),
            {"t": tenant or c.tenant, "name": name, "phone": "+"+phone if phone else None, "active": active})).scalar_one()
    c.scheduled_customers.append(identity)
    return identity


def definition(c, groups=(), **changes):
    return {"title": "Agendamento sintético", "device_id": c.device["id"],
        "group_ids": [g["id"] for g in groups], "contact_keys": [], "all_contacts": False,
        "start_date": datetime.now(timezone.utc).date().isoformat(), "start_time": "00:00",
        "interval_seconds": 10, "repeat_every_30_days": False,
        "message_type": "text", "text": "Olá, {name}!", "template_id": None} | changes


async def review(c, groups=None, *, key=None, row=None, **changes):
    if groups is None and not changes.get("contact_keys") and not changes.get("all_contacts"):
        groups = [await group(c)]
    payload = {"idempotency_key": str(key or uuid4()), "schedule": definition(c, groups or [], **changes)}
    path = c.scheduled+"/review"
    if row:
        path = c.scheduled+"/"+row["schedule_id"]+"/review"
        payload |= {"run_id": row["run_id"], "expected_version": row["version"], "expected_run_version": row["run_version"]}
    response = await c.client.post(path, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def confirmation(prepared, *, pending=False):
    return {"idempotency_key": prepared["idempotency_key"], "review_version": prepared["preview"]["review_version"],
        "confirmation": "ENVIAR PENDENTES" if pending else "AGENDAR", "consent_confirmed": True}


async def confirm(c, prepared):
    return await c.client.post(c.scheduled+"/confirm", json=confirmation(prepared))


async def schedule(c, groups=None, **changes):
    prepared = await review(c, groups, **changes)
    response = await confirm(c, prepared)
    assert response.status_code == 202, response.text
    return response.json()["result"]["schedule"]


async def detail(c, row):
    response = await c.client.get(c.scheduled+"/"+row["schedule_id"], params={"run_id": row["run_id"]})
    assert response.status_code == 200, response.text
    return response.json()


async def cancel(c, row, *, series=False):
    current = (await detail(c, row))["schedule"]
    payload = {"idempotency_key": str(uuid4()), "expected_version": current["version"],
        "action": "cancel_series" if series else "cancel_run", "confirmation": "CANCELAR"}
    if not series:
        payload |= {"run_id": current["run_id"], "expected_run_version": current["run_version"]}
    response = await c.client.post(c.scheduled+"/"+row["schedule_id"]+"/actions", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


async def poll(c):
    await renew(c)
    response = await internal(c, "/effects/pending", method="GET")
    assert response.status_code == 200, response.text
    return [effect for effect in response.json()["effects"] if effect["type"] == "scheduled_action"]


async def ledger(c):
    async with c.ctx.migration.connect() as db:
        return [dict(row) for row in (await db.execute(text("""SELECT * FROM subscription_device_effects
            WHERE tenant_id=:t AND type='scheduled_action' ORDER BY created_at,schedule_part_index"""),
            {"t": c.tenant})).mappings().all()]


async def snapshot(c):
    result = {}
    async with c.ctx.migration.connect() as db:
        for table in (*TABLES, *CAMPAIGN_TABLES, *LIVE):
            result[table] = (await db.execute(text(f"""SELECT COALESCE(jsonb_agg(to_jsonb(x)
                ORDER BY to_jsonb(x)::text),'[]'::jsonb) FROM {table} x WHERE tenant_id=:t"""), {"t": c.tenant})).scalar_one()
    return result


@asynccontextmanager
async def no_external(c, monkeypatch, *, readonly=False):
    before = await snapshot(c) if readonly else None
    calls = list(c.bridge.calls)
    async def denied(*args, **kwargs):
        pytest.fail("Scheduled management attempted external work")
    def denied_sync(*args, **kwargs):
        pytest.fail("Scheduled management downloaded media or recompiled chatbot rules")
    with monkeypatch.context() as patch:
        patch.setattr(qr_channels, "_bridge_control", denied)
        patch.setattr(subscription_device_audio, "transcribe_audio", denied)
        patch.setattr(subscription_chatbot_rules, "compile_plan", denied_sync)
        patch.setattr(media, "get_media", denied_sync)
        yield
    assert calls == c.bridge.calls
    if readonly:
        assert await snapshot(c) == before


async def test_catalog_is_tenant_scoped_deduplicated_and_does_not_query_transport(scheduled_case, monkeypatch):
    c = scheduled_case
    await customer(c, name="Cliente " + "a"*110)
    await customer(c, phone=None, name="Sem telefone")
    await customer(c, phone="5567999999902", name="Inativo", active=False)
    await customer(c, phone="5567999999903", name="Outra empresa", tenant=c.ctx.b)
    g = await group(c, members=[{"name": "Nome do grupo", "phone": PHONE}, {"name": "Internacional", "phone": "12025550123"}])
    async with no_external(c, monkeypatch, readonly=True):
        response = await c.client.get(c.scheduled+"/contacts")
        assert response.status_code == 200, response.text
        rows = response.json()["items"]
        assert response.json()["total"] == 2
        assert {item["key"] for item in rows} == {"phone:"+PHONE, "phone:12025550123"}
        assert next(item for item in rows if item["key"] == "phone:"+PHONE)["name"] == "Cliente " + "a"*110
        count = await c.client.post(c.scheduled+"/audience", json={"group_ids": [g["id"]], "contact_keys": ["phone:"+PHONE],
            "start_date": "2030-01-01", "start_time": "23:59", "interval_seconds": 60})
        assert count.status_code == 200, count.text
        assert count.json()["total"] == 2 and count.json()["duplicates"] == 1
        assert count.json()["timezone"] == "UTC"
        assert datetime.fromisoformat(count.json()["estimated_end_at"]) - datetime.fromisoformat(count.json()["start_at"]) == timedelta(seconds=120)
        foreign = await c.client.post(c.scheduled+"/audience", json={"contact_keys": ["phone:5567999999903"]})
        assert foreign.status_code in {404, 409, 422}


async def test_all_reads_are_pure_and_future_confirmation_creates_no_transport_effect(scheduled_case, monkeypatch):
    c = scheduled_case
    assert await poll(c) == []
    g = await group(c)
    tomorrow = (datetime.now(timezone.utc)+timedelta(days=1)).date().isoformat()
    async with no_external(c, monkeypatch):
        prepared = await review(c, [g], start_date=tomorrow, start_time="14:00")
        state = await snapshot(c)
        assert state["subscription_schedules"] == state["subscription_schedule_runs"] == state["subscription_schedule_recipients"] == []
        response = await confirm(c, prepared)
        assert response.status_code == 202, response.text
        row = response.json()["result"]["schedule"]
    assert await ledger(c) == [] and await poll(c) == []
    async with no_external(c, monkeypatch, readonly=True):
        suffixes = ("", "/options", "/contacts", "/requests/"+prepared["idempotency_key"],
                    "/"+row["schedule_id"]+"?run_id="+row["run_id"],
                    "/"+row["schedule_id"]+"/recipients?run_id="+row["run_id"], "/"+row["schedule_id"]+"/runs")
        for suffix in suffixes:
            result = await c.client.get(c.scheduled+suffix)
            assert result.status_code == 200 and result.headers["cache-control"] == "no-store", result.text
        assert (await detail(c, row))["schedule"]["active_filter"] == "upcoming"
        for tab in ("active", "past_due", "history", "all"):
            result = await c.client.get(c.scheduled, params={"tab": tab, "q": "sintético"})
            assert result.status_code == 200, result.text


async def test_confirmation_is_concurrent_idempotent_and_freezes_deduplicated_audience(scheduled_case, monkeypatch):
    c = scheduled_case
    g = await group(c, members=[{"name": "Um {name}", "phone": PHONE}, {"name": "Bloqueado", "phone": "5567999999902"}])
    await customer(c, name="Cliente {name}")
    await event(c, text="Não quero mensagens promocionais", phone="5567999999902")
    async with no_external(c, monkeypatch):
        prepared = await review(c, [g], contact_keys=["phone:"+PHONE])
        assert prepared["preview"]["total"] == 2 and prepared["preview"]["duplicates"] == 1
        assert prepared["preview"]["blocked"] == 1 and prepared["preview"]["eligible"] == 1
        responses = await asyncio.gather(confirm(c, prepared), confirm(c, prepared))
    assert [r.status_code for r in responses] == [202, 202], [r.text for r in responses]
    assert sorted(r.json()["replayed"] for r in responses) == [False, True]
    assert responses[0].json()["result"] == responses[1].json()["result"]
    state = await snapshot(c)
    assert len(state["subscription_schedules"]) == len(state["subscription_schedule_runs"]) == 1
    recipients = state["subscription_schedule_recipients"]
    assert len(recipients) == 2
    chosen = next(r for r in recipients if r["phone"] == PHONE)
    assert chosen["plan"][0]["text"] == "Olá, Cliente {name}!"
    assert len(chosen["origins"]) == 2
    assert await ledger(c) == []
    recovered = await c.client.get(c.scheduled+"/requests/"+prepared["idempotency_key"])
    assert recovered.status_code == 200 and recovered.json()["replayed"]


@pytest.mark.parametrize("change", ["group", "timezone", "expired"])
async def test_changed_review_cannot_create_a_schedule(scheduled_case, change):
    c = scheduled_case
    g = await group(c)
    prepared = await review(c, [g])
    if change == "group":
        await edit_group(c, g, [{"name": "Alterado", "phone": PHONE}])
    else:
        async with c.ctx.migration.begin() as db:
            if change == "timezone":
                await db.execute(text("UPDATE tenants SET timezone='America/Cuiaba' WHERE id=:t"), {"t": c.tenant})
            else:
                await db.execute(text("""UPDATE subscription_schedule_requests SET
                    created_at=statement_timestamp()-interval '3 minutes',expires_at=statement_timestamp()-interval '1 minute'
                    WHERE tenant_id=:t AND idempotency_key=:key"""), {"t": c.tenant, "key": UUID(prepared["idempotency_key"])})
    response = await confirm(c, prepared)
    assert response.status_code == (410 if change == "expired" else 409), response.text
    state = await snapshot(c)
    assert state["subscription_schedules"] == state["subscription_schedule_runs"] == state["subscription_schedule_recipients"] == []
    assert await ledger(c) == []


async def test_edit_preserves_old_recipient_revision_but_admits_only_new_frozen_plan(scheduled_case):
    c = scheduled_case
    g = await group(c)
    row = await schedule(c, [g], text="Texto anterior")
    prepared = await review(c, [g], row=row, text="Texto novo")
    response = await confirm(c, prepared)
    assert response.status_code == 202, response.text
    updated = response.json()["result"]["schedule"]
    assert updated["run_id"] == row["run_id"] and updated["version"] > row["version"]
    state = await snapshot(c)
    assert len(state["subscription_schedule_recipients"]) == 2
    assert len({r["run_version"] for r in state["subscription_schedule_recipients"]}) == 2
    effect, = await poll(c)
    authorized = await grant(c, effect)
    assert authorized["authorized"] and authorized["payload"]["action"]["text"] == "Texto novo"
    assert (await detail(c, updated))["schedule"]["progress"]["total"] == 1
    stale = await c.client.post(c.scheduled+"/"+row["schedule_id"]+"/review", json={"idempotency_key": str(uuid4()),
        "run_id": row["run_id"], "expected_version": updated["version"], "expected_run_version": updated["run_version"],
        "schedule": definition(c, [g], text="Depois da admissão")})
    assert stale.status_code == 409


async def test_pending_now_releases_only_due_unstarted_runs_and_is_idempotent(scheduled_case, monkeypatch):
    c = scheduled_case
    old = await schedule(c)
    future = await schedule(c, start_date=(datetime.now(timezone.utc)+timedelta(days=1)).date().isoformat())
    async with c.ctx.migration.begin() as db:
        await db.execute(text("""UPDATE subscription_schedule_runs SET due_at=clock_timestamp()-interval '1 day',
            local_date=(clock_timestamp() AT TIME ZONE 'UTC')::date-1 WHERE id=:id"""), {"id": UUID(old["run_id"])})
    assert await poll(c) == []
    assert (await detail(c, old))["schedule"]["bucket"] == "past_due"
    async with no_external(c, monkeypatch):
        response = await c.client.post(c.scheduled+"/pending/review", json={"idempotency_key": str(uuid4())})
        assert response.status_code == 201, response.text
        prepared = response.json()
        assert prepared["preview"]["total"] == 1
        responses = await asyncio.gather(*[c.client.post(c.scheduled+"/pending/confirm", json=confirmation(prepared, pending=True)) for _ in range(2)])
        assert [r.status_code for r in responses] == [202, 202], [r.text for r in responses]
        assert responses[0].json()["result"]["released"] == [{"schedule_id": old["schedule_id"], "run_id": old["run_id"]}]
        assert await ledger(c) == []
    effect, = await poll(c)
    assert (await grant(c, effect))["authorized"]
    assert (await detail(c, future))["schedule"]["active_filter"] == "upcoming"


async def test_parallel_polls_and_campaigns_share_one_serial_admission(scheduled_case):
    c = scheduled_case
    await schedule(c)
    await schedule(c)
    await campaign_schedule(c)
    await renew(c)
    responses = await asyncio.gather(*(internal(c, "/effects/pending", method="GET") for _ in range(2)))
    assert all(response.status_code == 200 for response in responses), [response.text for response in responses]
    advertised = {effect["operation_id"]: effect for response in responses for effect in response.json()["effects"]}
    async with c.ctx.migration.connect() as db:
        running = (await db.execute(text("""SELECT id,'scheduled_action' AS type
            FROM subscription_schedule_recipients WHERE tenant_id=:t AND status='running'
            UNION ALL SELECT id,'campaign_action' AS type
            FROM subscription_campaign_recipients WHERE tenant_id=:t AND status='running'"""),
            {"t": c.tenant})).mappings().all()
        effects = (await db.execute(text("""SELECT id,type,status,attempts,
            schedule_recipient_id,campaign_recipient_id FROM subscription_device_effects
            WHERE tenant_id=:t AND type IN ('scheduled_action','campaign_action')"""),
            {"t": c.tenant})).mappings().all()
    # Fair turns may choose either module. Both parallel polls must advertise
    # exactly the one persisted effect for the sole admitted recipient.
    assert len(running) == len(effects) == len(advertised) == 1
    live, = running
    effect, = effects
    assert effect["type"] == live["type"] == advertised[str(effect["id"])]["type"]
    assert (effect["status"], effect["attempts"]) == ("pending", 0)
    recipient_key = "schedule_recipient_id" if live["type"] == "scheduled_action" else "campaign_recipient_id"
    other_key = "campaign_recipient_id" if live["type"] == "scheduled_action" else "schedule_recipient_id"
    assert effect[recipient_key] == live["id"] and effect[other_key] is None


async def test_spacing_starts_after_last_sequence_finishes(scheduled_case):
    c = scheduled_case
    g = await group(c, members=[{"name": "Um", "phone": PHONE}, {"name": "Dois", "phone": "5567999999902"}])
    row = await schedule(c, [g], interval_seconds=60)
    first, = await poll(c)
    assert (await grant(c, first))["authorized"]
    await finish(c, first)
    assert await poll(c) == []
    async with c.ctx.migration.begin() as db:
        await db.execute(text("""UPDATE subscription_schedule_recipients SET finished_at=clock_timestamp()-interval '61 seconds',
            admitted_at=clock_timestamp()-interval '62 seconds' WHERE run_id=:id AND status='sent'"""), {"id": UUID(row["run_id"])})
        await db.execute(text("UPDATE subscription_schedule_runs SET next_admission_at=clock_timestamp()-interval '1 second' WHERE id=:id"), {"id": UUID(row["run_id"])})
    second, = await poll(c)
    assert second["operation_id"] != first["operation_id"]


@pytest.mark.parametrize("state", ["accepted", "not_sent", "uncertain"])
async def test_true_receipt_history_is_readonly_and_never_offers_generic_resend(scheduled_case, monkeypatch, state):
    c = scheduled_case
    row = await schedule(c)
    effect, = await poll(c)
    assert (await grant(c, effect))["authorized"]
    await finish(c, effect, state, code=None if state == "accepted" else "TRANSPORT_UNAVAILABLE_BEFORE_SEND" if state == "not_sent" else "MESSAGE_OUTCOME_UNCERTAIN")
    expected = {"accepted": "sent", "not_sent": "failed", "uncertain": "uncertain"}[state]
    async with no_external(c, monkeypatch, readonly=True):
        assert (await detail(c, row))["schedule"]["progress"][expected] == 1
        result = await c.client.get(c.history+"/"+effect["operation_id"])
        assert result.status_code == 200, result.text
        item = result.json()["item"]
        assert item["origin"] == {"code": "scheduled", "label": "Mensagem programada", "is_resend": False}
        assert item["status"] == expected and not item["resend"]["eligible"] and item["actions"] == []
    assert await poll(c) == [] and not (await grant(c, effect))["authorized"]
    assert len(await ledger(c)) == 1


async def test_interrupted_attempt_can_reconcile_without_another_send(scheduled_case):
    c = scheduled_case
    row = await schedule(c)
    effect, = await poll(c)
    assert (await grant(c, effect))["authorized"]
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE subscription_device_effects SET authorized_at=clock_timestamp()-interval '181 seconds' WHERE id=:id"), {"id": UUID(effect["operation_id"])})
    assert await poll(c) == []
    assert (await detail(c, row))["schedule"]["progress"]["uncertain"] == 1
    await finish(c, effect, reconcile=True)
    assert (await detail(c, row))["schedule"]["progress"]["sent"] == 1
    saved, = await ledger(c)
    assert saved["status"] == "accepted" and saved["attempts"] == 1


@pytest.mark.parametrize("change", ["cancel", "optout", "source", "customer"])
async def test_revocation_after_claim_denies_final_transport_recheck(scheduled_case, change):
    c = scheduled_case
    if change == "customer":
        identity = await customer(c)
        row = await schedule(c, [], contact_keys=["phone:"+PHONE])
    else:
        g = await group(c)
        row = await schedule(c, [g])
    effect, = await poll(c)
    assert (await grant(c, effect))["authorized"]
    if change == "cancel":
        await cancel(c, row)
    elif change == "optout":
        await event(c, text="Não quero mensagens promocionais", phone=PHONE)
    elif change == "source":
        changed = await edit_group(c, g, [])
        await edit_group(c, changed, [{"name": "Recriado", "phone": PHONE}])
    else:
        async with c.ctx.migration.begin() as db:
            await db.execute(text("UPDATE customers SET version=version+1,active=false WHERE id=:id"), {"id": identity})
            await db.execute(text("UPDATE customers SET version=version+1,active=true WHERE id=:id"), {"id": identity})
    denied = await grant(c, effect, recheck=True)
    assert not denied["authorized"]
    saved, = await ledger(c)
    assert saved["attempts"] == 1 and saved["status"] == "running"


async def test_recurrence_clones_approved_audience_and_creates_only_one_future_cycle(scheduled_case):
    c = scheduled_case
    g = await group(c)
    row = await schedule(c, [g], repeat_every_30_days=True)
    effect, = await poll(c)
    assert (await grant(c, effect))["authorized"]
    await finish(c, effect)
    await poll(c)
    state = await snapshot(c)
    runs = sorted(state["subscription_schedule_runs"], key=lambda r: r["occurrence_index"])
    assert len(runs) == 2 and runs[0]["status"] == "completed" and runs[1]["status"] == "pending"
    assert datetime.fromisoformat(runs[1]["due_at"]) - datetime.fromisoformat(runs[0]["due_at"]) == timedelta(days=30)
    assert runs[1]["source_run_id"] == runs[0]["id"]
    assert len(state["subscription_schedule_recipients"]) == 2
    await edit_group(c, g, [{"name": "Pessoa {phone}", "phone": PHONE}, {"name": "Novo", "phone": "5567999999902"}])
    assert await poll(c) == []
    assert len((await snapshot(c))["subscription_schedule_recipients"]) == 2
    await cancel(c, row, series=True)
    await poll(c)
    assert len((await snapshot(c))["subscription_schedule_runs"]) == 2


async def test_audio_template_keeps_ordered_parts_and_closes_unsent_tail(scheduled_case, private_media):
    c = scheduled_case
    resource = await upload(c, wav_bytes(), "audio", "synthetic.wav")
    template = await create_template(c, template_definition(kind="audio", text="Olá, {name}", followup_text="Final", asset_id=resource["asset"]["id"]))
    row = await schedule(c, message_type="template", text=None, template_id=template["id"])
    first, = await poll(c)
    assert (await grant(c, first))["payload"]["action"]["type"] == "audio"
    parts = await ledger(c)
    assert [part["payload"]["action"]["type"] for part in parts] == ["audio", "text", "text"]
    assert not (await grant(c, {"operation_id": str(parts[1]["id"])}))["authorized"]
    await finish(c, first)
    second, = await poll(c)
    assert (await grant(c, second))["authorized"]
    await finish(c, second, "not_sent", code="MESSAGE_CONTENT_INVALID")
    assert await poll(c) == []
    assert [part["status"] for part in await ledger(c)] == ["accepted", "not_sent", "not_sent"]
    assert (await detail(c, row))["schedule"]["progress"]["failed"] == 1


async def test_private_media_rechecks_cancel_after_storage_read(scheduled_case, private_media, monkeypatch):
    c = scheduled_case
    resource = await upload(c, png_bytes(), "image", "synthetic.png")
    template = await create_template(c, template_definition(kind="image", text="Legenda", followup_text="", asset_id=resource["asset"]["id"]))
    row = await schedule(c, message_type="template", text=None, template_id=template["id"])
    effect, = await poll(c)
    asset = (await grant(c, effect))["payload"]["action"]["asset"]
    path = f"/internal/qr/channels/{c.row['id']}/device/scheduled/assets/{asset['id']}"
    params = scope(c) | {"operation_id": effect["operation_id"], "variant": "original"}
    headers = c.bridge.headers(c.row, live=True) | {"X-Orion-Device-Id": c.device["id"],
        "X-Orion-Device-Generation": str(c.saved["generation"]), "X-Orion-Device-Revision": str(c.device["revision"]),
        "X-Orion-Device-Effect": effect["operation_id"]}
    response = await c.private.get(path, params=params, headers=headers)
    assert response.status_code == 200 and response.content == private_media[(str(c.tenant), asset["id"], "original")]
    bad = await c.private.get(path, params=params | {"operation_id": str(uuid4())}, headers=headers)
    assert bad.status_code == 401
    started, released = Event(), Event()
    original = media.get_media
    def blocked(*args, **kwargs):
        started.set()
        assert released.wait(10)
        return original(*args, **kwargs)
    monkeypatch.setattr(media, "get_media", blocked)
    task = asyncio.create_task(c.private.get(path, params=params, headers=headers))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        await cancel(c, row)
    finally:
        released.set()
    denied = await asyncio.wait_for(task, 10)
    assert denied.status_code == 409 and denied.json()["detail"]["code"] == "SCHEDULE_CANCELLED"


async def test_auth_csrf_scope_and_private_columns_are_enforced(scheduled_case):
    c = scheduled_case
    prepared = await review(c)
    assert (await c.private.get(c.scheduled)).status_code == 401
    assert (await c.client.post(c.scheduled+"/review", json={}, headers={"x-orion-csrf": ""})).status_code == 403
    assert (await c.client.get(c.scheduled.replace(str(c.tenant), str(c.ctx.b)))).status_code == 403
    async with c.ctx.runtime() as db:
        for table in TABLES:
            with pytest.raises(DBAPIError) as denied:
                async with tenant_transaction(db, c.tenant):
                    await db.execute(text(f"SELECT * FROM {table}"))
            assert denied.value.orig.sqlstate == "42501"
    async with support_session_factory()() as db:
        for table, column in (("subscription_schedule_recipients", "plan"), ("subscription_schedule_requests", "snapshot")):
            with pytest.raises(DBAPIError) as denied:
                async with db.begin():
                    await db.execute(text(f"UPDATE {table} SET {column}='{{}}'::jsonb WHERE tenant_id=:t"), {"t": c.tenant})
            assert denied.value.orig.sqlstate == "42501"
    assert (await c.client.get(c.scheduled+"/requests/"+prepared["idempotency_key"])).status_code == 200


@pytest.mark.parametrize("change", ["owner", "viewer", "niche"])
async def test_current_owner_and_niche_precede_recovery_and_body_decoding(scheduled_case, change):
    c = scheduled_case
    prepared = await review(c)
    await revoke(c, change)
    for suffix in ("", "/options", "/contacts", "/requests/"+prepared["idempotency_key"]):
        assert (await c.client.get(c.scheduled+suffix)).status_code == 403
    for suffix in ("/review", "/confirm", "/pending/review", "/pending/confirm"):
        response = await c.client.post(c.scheduled+suffix, content=b"not-json", headers={"content-type": "application/json"})
        assert response.status_code == 403, response.text


async def test_twenty_five_unreleased_old_runs_cannot_starve_a_due_current_run(scheduled_case):
    c = scheduled_case
    g = await group(c)
    old = [await schedule(c, [g], title=f"Pendência antiga {index}") for index in range(25)]
    async with c.ctx.migration.begin() as db:
        await db.execute(text("""UPDATE subscription_schedule_runs SET due_at=clock_timestamp()-interval '1 day',
            local_date=(clock_timestamp() AT TIME ZONE 'UTC')::date-1
            WHERE tenant_id=:t AND id=ANY(CAST(:ids AS uuid[]))"""),
            {"t": c.tenant, "ids": [UUID(item["run_id"]) for item in old]})
    current = await schedule(c, [g], title="Devida hoje")
    effect, = await poll(c)
    permit = await grant(c, effect)
    assert permit["authorized"] and permit["run_id"] == current["run_id"]
    assert (await detail(c, old[0]))["schedule"]["bucket"] == "past_due"


async def test_revoked_future_audience_stays_editable_in_attention_without_recurrence_chain(scheduled_case):
    c = scheduled_case
    g = await group(c)
    row = await schedule(c, [g], start_date=(datetime.now(timezone.utc)+timedelta(days=2)).date().isoformat(), repeat_every_30_days=True)
    await edit_group(c, g, [])
    assert await poll(c) == []
    current = (await detail(c, row))["schedule"]
    assert current["status"] == "pending" and current["active_filter"] == "attention" and current["bucket"] == "active"
    assert "edit" in current["actions"] and current["progress"]["cancelled"] == 1
    state = await snapshot(c)
    assert len(state["subscription_schedule_runs"]) == 1 and await ledger(c) == []


async def test_empty_global_review_is_recoverable_and_cannot_confirm_any_send(scheduled_case, monkeypatch):
    c = scheduled_case
    async with no_external(c, monkeypatch):
        response = await c.client.post(c.scheduled+"/pending/review", json={"idempotency_key": str(uuid4())})
        assert response.status_code == 201, response.text
        prepared = response.json()
        assert prepared["preview"]["total"] == 0 and prepared["preview"]["runs"] == []
        recovered = await c.client.get(c.scheduled+"/requests/"+prepared["idempotency_key"])
        assert recovered.status_code == 200 and recovered.json()["status"] == "prepared"
        blocked = await c.client.post(c.scheduled+"/pending/confirm", json=confirmation(prepared, pending=True))
        assert blocked.status_code == 422 and blocked.json()["detail"]["code"] == "SCHEDULE_NO_ELIGIBLE"
    assert await ledger(c) == [] and (await snapshot(c))["subscription_schedule_runs"] == []


async def test_known_device_own_phone_is_blocked_before_confirmation(scheduled_case):
    c = scheduled_case
    await event(c, kind="status", state="connected", phone=PHONE)
    prepared = await review(c)
    assert prepared["preview"]["total"] == 1 and prepared["preview"]["blocked"] == 1 and prepared["preview"]["eligible"] == 0
    blocked = await confirm(c, prepared)
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "SCHEDULE_NO_ELIGIBLE"
    assert (await snapshot(c))["subscription_schedules"] == [] and await ledger(c) == []


async def test_negative_recovery_before_delayed_review_does_not_poison_the_key(scheduled_case):
    c = scheduled_case
    g, key = await group(c), uuid4()
    assert (await c.client.get(c.scheduled+"/requests/"+str(key))).status_code == 404
    prepared = await review(c, [g], key=key)
    repeated = await review(c, [g], key=key)
    assert repeated["replayed"] and repeated["request_id"] == prepared["request_id"]
    response = await c.client.post(c.scheduled+"/review", json={"idempotency_key": str(key), "schedule": definition(c, [g], text="Outro conteúdo")})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "SCHEDULE_IDEMPOTENCY_CONFLICT"
    recovered = await c.client.get(c.scheduled+"/requests/"+str(key))
    assert recovered.json()["preview"] == prepared["preview"]
    assert len((await snapshot(c))["subscription_schedule_requests"]) == 1


@pytest.mark.parametrize("next_module", ["scheduled", "campaign"])
async def test_settings_close_before_claim_reconciles_recipient_and_releases_shared_gate(scheduled_case, next_module):
    c = scheduled_case
    g = await group(c)
    old = await schedule(c, [g])
    first, = await poll(c)
    original, = await ledger(c)
    assert original["status"] == "pending" and original["attempts"] == 0
    async with c.ctx.migration.connect() as db:
        admitted = (await db.execute(text("""SELECT status,admitted_at FROM subscription_schedule_recipients
            WHERE tenant_id=:t AND run_id=:run"""), {"t": c.tenant, "run": UUID(old["run_id"])})).mappings().one()
        assert admitted["status"] == "running" and admitted["admitted_at"] is not None

    previous_revision = c.device["revision"]
    await settings(c, lambda value: value.update(name="Configuração atualizada"))  # Real owner PUT, no claim/send.
    assert c.device["revision"] > previous_revision
    closed, = await ledger(c)
    assert closed["id"] == UUID(first["operation_id"])
    assert (closed["status"], closed["error_code"], closed["attempts"]) == ("not_sent", "settings_changed", 0)

    assert await poll(c) == []  # Reconcile persisted terminal effects before the tenant-wide live gate.
    async with c.ctx.migration.connect() as db:
        recipient = (await db.execute(text("""SELECT status,error_code,finished_at FROM subscription_schedule_recipients
            WHERE tenant_id=:t AND run_id=:run"""), {"t": c.tenant, "run": UUID(old["run_id"])})).mappings().one()
        run_status = (await db.execute(text("SELECT status FROM subscription_schedule_runs WHERE tenant_id=:t AND id=:run"),
            {"t": c.tenant, "run": UUID(old["run_id"])})).scalar_one()
        assert recipient["status"] == "failed" and recipient["error_code"] == "settings_changed"
        assert recipient["finished_at"] is not None and run_status == "failed"
    assert await ledger(c) == [closed]  # Reconciliation must not rewrite the transport evidence.

    if next_module == "scheduled":
        following = await schedule(c, [g], title="Agendamento com revisão atual")
    else:
        following = await campaign_schedule(c, [g], title="Campanha com revisão atual")
    async with c.ctx.migration.begin() as db:
        # Advance only this disposable ledger's pacing clock; do not sleep or
        # weaken the shared six-second interval in production code.
        await db.execute(text("""UPDATE subscription_schedule_recipients
            SET admitted_at=statement_timestamp()-interval '8 seconds',
                finished_at=statement_timestamp()-interval '7 seconds'
            WHERE tenant_id=:t AND run_id=:run AND status='failed'"""),
            {"t": c.tenant, "run": UUID(old["run_id"])})
    await renew(c)
    response = await internal(c, "/effects/pending", method="GET")
    assert response.status_code == 200, response.text
    next_effect, = response.json()["effects"]
    assert next_effect["type"] == ("scheduled_action" if next_module == "scheduled" else "campaign_action")
    assert next_effect["operation_id"] != first["operation_id"]
    permit = await grant(c, next_effect)
    assert permit["authorized"]
    if next_module == "scheduled":
        assert permit["schedule_id"] == following["schedule_id"] and permit["run_id"] == following["run_id"]
    else:
        assert permit["campaign_id"] == following["id"]
    old_saved = next(item for item in await ledger(c) if item["id"] == closed["id"])
    assert old_saved == closed


@pytest.mark.parametrize("series", [False, True])
async def test_cancel_after_external_terminal_effect_does_not_leave_running_recipient(scheduled_case, series):
    c = scheduled_case
    row = await schedule(c)
    first, = await poll(c)
    await settings(c, lambda value: value.update(name="Configuração antes de cancelar"))
    closed, = await ledger(c)
    assert (closed["status"], closed["error_code"], closed["attempts"]) == ("not_sent", "settings_changed", 0)

    # Deliberately cancel before another poll has had a chance to reconcile.
    response = await cancel(c, row, series=series)
    assert response["status"] == "succeeded"
    async with c.ctx.migration.connect() as db:
        recipient = (await db.execute(text("""SELECT status,error_code FROM subscription_schedule_recipients
            WHERE tenant_id=:t AND run_id=:run"""), {"t": c.tenant, "run": UUID(row["run_id"])})).mappings().one()
        run_status = (await db.execute(text("SELECT status FROM subscription_schedule_runs WHERE tenant_id=:t AND id=:run"),
            {"t": c.tenant, "run": UUID(row["run_id"])})).scalar_one()
        assert recipient["status"] == "failed" and recipient["error_code"] == "settings_changed"
        assert run_status == "failed"
    assert await ledger(c) == [closed]
    assert await poll(c) == [] and not (await grant(c, first))["authorized"]
    assert await ledger(c) == [closed]


async def test_removed_device_terminal_sequence_is_reconciled_by_replacement_device_poll(scheduled_case):
    from test_subscription_devices_postgres import action, create

    c = scheduled_case
    g = await group(c)
    old = await schedule(c, [g])
    first, = await poll(c)
    old_device, old_generation = c.device["id"], c.saved["generation"]
    removed = await action(c, "remove")
    assert removed["operation"]["status"] == "succeeded" and removed["device"] is None
    closed, = await ledger(c)
    assert (closed["status"], closed["error_code"], closed["attempts"]) == ("not_sent", "device_removing", 0)

    await create(c, name="Dispositivo substituto")
    await action(c, "connect")
    await event(c, kind="status", state="connected", phone="5567999999998")
    assert c.device["id"] != old_device and c.saved["generation"] != old_generation
    assert await poll(c) == []
    async with c.ctx.migration.connect() as db:
        recipient = (await db.execute(text("""SELECT status,error_code,device_id,generation
            FROM subscription_schedule_recipients WHERE tenant_id=:t AND run_id=:run"""),
            {"t": c.tenant, "run": UUID(old["run_id"])})).mappings().one()
        assert recipient["status"] == "failed" and recipient["error_code"] == "device_removing"
        assert recipient["device_id"] == UUID(old_device) and recipient["generation"] == old_generation
    assert await ledger(c) == [closed]

    following = await schedule(c, [g], title="Novo agendamento no dispositivo substituto")
    async with c.ctx.migration.begin() as db:
        await db.execute(text("""UPDATE subscription_schedule_recipients
            SET admitted_at=statement_timestamp()-interval '8 seconds',
                finished_at=statement_timestamp()-interval '7 seconds'
            WHERE tenant_id=:t AND run_id=:run AND status='failed'"""),
            {"t": c.tenant, "run": UUID(old["run_id"])})
    next_effect, = await poll(c)
    assert next_effect["operation_id"] != first["operation_id"] and next_effect["device_id"] == c.device["id"]
    permit = await grant(c, next_effect)
    assert permit["authorized"] and permit["schedule_id"] == following["schedule_id"]
    assert next(item for item in await ledger(c) if item["id"] == closed["id"]) == closed


async def test_externally_closed_campaign_releases_the_gate_for_a_current_revision_schedule(scheduled_case):
    c = scheduled_case
    g = await group(c)
    old = await campaign_schedule(c, [g])
    await renew(c)
    response = await internal(c, "/effects/pending", method="GET")
    assert response.status_code == 200, response.text
    first, = response.json()["effects"]
    assert first["type"] == "campaign_action"
    await settings(c, lambda value: value.update(name="Configuração após admissão da campanha"))
    async with c.ctx.migration.connect() as db:
        closed = dict((await db.execute(text("""SELECT * FROM subscription_device_effects
            WHERE tenant_id=:t AND id=:id"""), {"t": c.tenant, "id": UUID(first["operation_id"])})).mappings().one())
        assert (closed["status"], closed["error_code"], closed["attempts"]) == ("not_sent", "settings_changed", 0)

    following = await schedule(c, [g], title="Programada após fechamento externo da campanha")
    assert await poll(c) == []  # The ordinary poll also reconciles the campaign side of the shared gate.
    async with c.ctx.migration.connect() as db:
        recipient = (await db.execute(text("""SELECT status,error_code,finished_at
            FROM subscription_campaign_recipients WHERE tenant_id=:t AND campaign_id=:campaign"""),
            {"t": c.tenant, "campaign": UUID(old["id"])})).mappings().one()
        assert recipient["status"] == "failed" and recipient["error_code"] == "settings_changed"
        assert recipient["finished_at"] is not None
    assert await ledger(c) == []
    async with c.ctx.migration.begin() as db:
        await db.execute(text("""UPDATE subscription_campaign_recipients
            SET admitted_at=statement_timestamp()-interval '8 seconds',
                finished_at=statement_timestamp()-interval '7 seconds'
            WHERE tenant_id=:t AND campaign_id=:campaign AND status='failed'"""),
            {"t": c.tenant, "campaign": UUID(old["id"])})

    next_effect, = await poll(c)
    assert next_effect["type"] == "scheduled_action" and next_effect["operation_id"] != first["operation_id"]
    permit = await grant(c, next_effect)
    assert permit["authorized"] and permit["schedule_id"] == following["schedule_id"]
    async with c.ctx.migration.connect() as db:
        saved = dict((await db.execute(text("""SELECT * FROM subscription_device_effects
            WHERE tenant_id=:t AND id=:id"""), {"t": c.tenant, "id": UUID(first["operation_id"])})).mappings().one())
        assert saved == closed


async def test_customer_catalog_support_grants_are_readonly_and_limited_to_subscription_niche(scheduled_case):
    c = scheduled_case
    subscribed = await customer(c, name="Cliente do catálogo de assinaturas")
    ordinary = await customer(c, phone="5567999999904", name="Cliente de empresa padrão", tenant=c.ctx.a)
    async with c.ctx.migration.begin() as db:
        # A missing onboarding record also fails this SELECT policy. If this
        # shared fixture supplies one, make its standard niche explicit.
        await db.execute(text("""UPDATE business_onboarding
            SET configuration=jsonb_set(configuration,'{niche}','\"barbearia\"'::jsonb)
            WHERE tenant_id=:t"""), {"t": c.ctx.a})
    async with support_session_factory()() as db:
        async with db.begin():
            rows = (await db.execute(text("""SELECT id,tenant_id,whatsapp_e164,name,active,deleted_at,version
                FROM customers WHERE id=ANY(CAST(:ids AS uuid[])) ORDER BY id"""),
                {"ids": [subscribed, ordinary]})).mappings().all()
            assert len(rows) == 1 and rows[0]["id"] == subscribed and rows[0]["tenant_id"] == c.tenant
            assert rows[0]["whatsapp_e164"] == "+" + PHONE and rows[0]["active"] is True
            assert rows[0]["deleted_at"] is None and rows[0]["version"] == 1
        for sql in ("SELECT notes FROM customers WHERE id=:id", "UPDATE customers SET name='Não autorizado' WHERE id=:id"):
            with pytest.raises(DBAPIError) as denied:
                async with db.begin():
                    await db.execute(text(sql), {"id": subscribed})
            assert denied.value.orig.sqlstate == "42501"
