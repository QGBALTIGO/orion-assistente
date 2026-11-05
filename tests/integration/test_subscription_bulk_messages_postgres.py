"""Bulk workbench over real auth, PostgreSQL roles and the private device poll.

The inherited fixtures refuse non-loopback PostgreSQL/Redis. WhatsApp, media
storage and transcription remain synthetic; no real recipient is contacted.
"""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import json
from threading import Event
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import qr_channels, subscription_bulk_messages as api
from app import subscription_chatbot_media as media, subscription_chatbot_rules, subscription_device_audio
from app.catalog_api import support_session_factory
from app.database import tenant_transaction
from app.plan_routing import include_plan_router
from test_subscription_scheduled_messages_postgres import (
    scheduled_case, campaign_case, chatbot_case, device_case, disposable_environment,
    new_owner_http, onboarding_db, private_media, customer,
    schedule as scheduled_message, TABLES as SCHEDULE_TABLES,
)  # noqa: F401 - real transitive cookie/CSRF/role/lease fixtures.
from test_subscription_campaigns_postgres import (
    group, edit_group, renew, grant, finish, schedule as campaign,
    TABLES as CAMPAIGN_TABLES, LIVE,
)
from test_subscription_chatbot_test_postgres import revoke
from test_subscription_devices_postgres import action, create, event, internal, scope, settings
from test_subscription_templates_postgres import (
    create_template, save_template, template_definition, upload, png_bytes, wav_bytes,
)

pytestmark = pytest.mark.integration
TABLES = ("subscription_bulk_batches", "subscription_bulk_recipients", "subscription_bulk_requests")
PHONE = "5567999999901"
OTHER_PHONE = "5567999999902"


@pytest_asyncio.fixture
async def bulk_case(scheduled_case):
    c = scheduled_case
    generic = [route for route in c.app.router.routes if "{resource}" in getattr(route, "path", "")]
    c.app.router.routes[:] = [route for route in c.app.router.routes if route not in generic]
    include_plan_router(c.app, api.router)
    c.app.router.routes.extend(generic)
    c.bulk = f"/api/v1/tenants/{c.tenant}/subscription-whatsapp/bulk-messages"
    try:
        yield c
    finally:
        async with c.ctx.migration.begin() as db:
            for tenant in (c.tenant, c.ctx.a, c.ctx.b):
                await db.execute(text("DELETE FROM subscription_bulk_requests WHERE tenant_id=:t"), {"t": tenant})
                await db.execute(text("DELETE FROM subscription_device_effects WHERE tenant_id=:t AND type='bulk_action'"), {"t": tenant})
                await db.execute(text("DELETE FROM subscription_bulk_recipients WHERE tenant_id=:t"), {"t": tenant})
                await db.execute(text("DELETE FROM subscription_bulk_batches WHERE tenant_id=:t"), {"t": tenant})


def item(c, *, phone=PHONE, source="manual", **changes):
    return {"row_key": str(uuid4()), "device_id": c.device["id"], "source": source,
        "contact_key": "phone:" + phone if source == "catalog" else None,
        "phone": phone if source == "manual" else None,
        "message_type": "text", "text": "Mensagem literal {name}", "template_id": None} | changes


def review_payload(c, items=None, *, key=None, interval=10):
    return {"idempotency_key": str(key or uuid4()),
        "batch": {"interval_seconds": interval, "items": items if items is not None else [item(c)]}}


async def review(c, items=None, *, key=None, interval=10):
    response = await c.client.post(c.bulk + "/review", json=review_payload(c, items, key=key, interval=interval))
    assert response.status_code == 201, response.text
    return response.json()


def confirmation(prepared):
    return {"idempotency_key": prepared["idempotency_key"], "review_version": prepared["preview"]["review_version"],
        "confirmation": "ENVIAR", "consent_confirmed": True}


async def confirm(c, prepared):
    return await c.client.post(c.bulk + "/confirm", json=confirmation(prepared))


async def batch(c, items=None, *, interval=10):
    response = await confirm(c, await review(c, items, interval=interval))
    assert response.status_code == 202, response.text
    return response.json()["result"]["batch"]


async def detail(c, row):
    response = await c.client.get(c.bulk + "/" + row["batch_id"])
    assert response.status_code == 200, response.text
    return response.json()


async def cancel(c, row, *, key=None):
    current = (await detail(c, row))["batch"]
    payload = {"idempotency_key": str(key or uuid4()), "action": "cancel", "confirmation": "CANCELAR",
        "expected_version": current["version"]}
    response = await c.client.post(c.bulk + "/" + row["batch_id"] + "/actions", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


async def poll(c):
    await renew(c)
    response = await internal(c, "/effects/pending", method="GET")
    assert response.status_code == 200, response.text
    return [effect for effect in response.json()["effects"] if effect["type"] == "bulk_action"]


async def ledger(c):
    async with c.ctx.migration.connect() as db:
        return [dict(row) for row in (await db.execute(text("""SELECT * FROM subscription_device_effects
            WHERE tenant_id=:t AND type='bulk_action' ORDER BY created_at,bulk_part_index,id"""),
            {"t": c.tenant})).mappings().all()]


async def snapshot(c):
    result = {}
    async with c.ctx.migration.connect() as db:
        for table in (*TABLES, *SCHEDULE_TABLES, *CAMPAIGN_TABLES, *LIVE, "customers"):
            result[table] = (await db.execute(text(f"""SELECT COALESCE(jsonb_agg(to_jsonb(x)
                ORDER BY to_jsonb(x)::text),'[]'::jsonb) FROM {table} x WHERE tenant_id=:t"""),
                {"t": c.tenant})).scalar_one()
    return result


@asynccontextmanager
async def no_external(c, monkeypatch, *, readonly=False):
    before = await snapshot(c) if readonly else None
    calls = list(c.bridge.calls)
    async def denied(*args, **kwargs):
        pytest.fail("Bulk management attempted external transport/transcription")
    def denied_sync(*args, **kwargs):
        pytest.fail("Bulk management downloaded media or recompiled a chatbot rule")
    with monkeypatch.context() as patch:
        patch.setattr(qr_channels, "_bridge_control", denied)
        patch.setattr(subscription_device_audio, "transcribe_audio", denied)
        patch.setattr(subscription_chatbot_rules, "compile_plan", denied_sync)
        patch.setattr(media, "get_media", denied_sync)
        yield
    assert calls == c.bridge.calls
    if readonly:
        assert await snapshot(c) == before


async def test_catalog_selection_is_tenant_scoped_deduplicated_and_readonly(bulk_case, monkeypatch):
    c = bulk_case
    await customer(c, name="Nome do cliente")
    await customer(c, phone="5567999999903", name="Outra empresa", tenant=c.ctx.b)
    g = await group(c, members=[{"name": "Nome do grupo", "phone": PHONE},
                               {"name": "Segundo", "phone": OTHER_PHONE}])
    async with no_external(c, monkeypatch, readonly=True):
        catalog = await c.client.get(c.bulk + "/contacts")
        assert catalog.status_code == 200, catalog.text
        assert catalog.json()["total"] == 2
        selected = await c.client.post(c.bulk + "/audience", json={"group_ids": [g["id"]], "contact_keys": ["phone:"+PHONE]})
        assert selected.status_code == 200, selected.text
        value = selected.json()
        assert (value["total"], value["duplicates"], value["eligible"]) == (2, 1, 2)
        assert {r["phone_e164"] for r in value["items"]} == {"+"+PHONE, "+"+OTHER_PHONE}
        assert next(r for r in value["items"] if r["key"] == "phone:"+PHONE)["name"] == "Nome do cliente"
        all_contacts = await c.client.post(c.bulk + "/audience", json={"all_contacts": True})
        assert all_contacts.status_code == 200 and all_contacts.json()["total"] == 2, all_contacts.text
        foreign = await c.client.post(c.bulk + "/audience", json={"contact_keys": ["phone:5567999999903"]})
        assert foreign.status_code == 422 and foreign.json()["detail"]["code"] == "BULK_CONTACT_CHANGED"
        missing = await c.client.post(c.bulk + "/audience", json={"group_ids": [str(uuid4())]})
        assert missing.status_code == 404


async def test_prepare_confirm_and_all_reads_never_create_transport_or_customer_records(bulk_case, monkeypatch):
    c = bulk_case
    assert await poll(c) == []  # Exercise timestamp/aggregate SQL even with empty tables.
    before = await snapshot(c)
    async with no_external(c, monkeypatch):
        prepared = await review(c)
        only_review = await snapshot(c)
        assert only_review["subscription_bulk_batches"] == only_review["subscription_bulk_recipients"] == []
        assert len(only_review["subscription_bulk_requests"]) == 1
        result = await confirm(c, prepared)
        assert result.status_code == 202, result.text
    row = result.json()["result"]["batch"]
    assert row["status"] == "pending" and row["progress"]["pending"] == 1
    assert await ledger(c) == []
    after = await snapshot(c)
    assert after["customers"] == before["customers"]
    assert after["subscription_campaign_permissions"] == before["subscription_campaign_permissions"]
    async with no_external(c, monkeypatch, readonly=True):
        for suffix in ("", "/options", "/contacts", "/requests/"+prepared["idempotency_key"],
                       "/"+row["batch_id"], "/"+row["batch_id"]+"/recipients"):
            response = await c.client.get(c.bulk+suffix)
            assert response.status_code == 200 and response.headers["cache-control"] == "no-store", response.text
        options = (await c.client.get(c.bulk+"/options")).json()
        assert options["defaults"] == {"interval_seconds": 10, "message_type": "text"}
        assert options["limits"]["interval_seconds"] == {"min": 10, "max": 60, "step": 1}
        history = (await c.client.get(c.bulk)).json()
        assert history["counts"] == {"total": 0, "last_30_days": 0, "today": 0}
        assert history["active_batches_total"] == 1


async def test_concurrent_review_and_confirmation_preserve_explicit_duplicate_rows_once(bulk_case, monkeypatch):
    c = bulk_case
    await group(c)
    rows = [item(c, phone="+55 (67) 99999-9901", text="Primeira {name}"),
            item(c, source="catalog", text="Segunda {phone}"), item(c, text="Terceira")]
    payload = review_payload(c, rows)
    async with no_external(c, monkeypatch):
        reviews = await asyncio.gather(*(c.client.post(c.bulk+"/review", json=payload) for _ in range(2)))
        assert [r.status_code for r in reviews] == [201, 201], [r.text for r in reviews]
        assert reviews[0].json()["request_id"] == reviews[1].json()["request_id"]
        assert sorted(r.json()["replayed"] for r in reviews) == [False, True]
        prepared = reviews[0].json()
        assert {key: prepared["preview"][key] for key in ("total", "duplicates", "duplicate_destinations", "unique_destinations")} == {
            "total": 3, "duplicates": 2, "duplicate_destinations": 1, "unique_destinations": 1}
        responses = await asyncio.gather(confirm(c, prepared), confirm(c, prepared))
    assert [r.status_code for r in responses] == [202, 202], [r.text for r in responses]
    assert sorted(r.json()["replayed"] for r in responses) == [False, True]
    assert responses[0].json()["result"] == responses[1].json()["result"]
    current = await snapshot(c)
    assert len(current["subscription_bulk_batches"]) == len(current["subscription_bulk_requests"]) == 1
    recipients = sorted(current["subscription_bulk_recipients"], key=lambda r: r["ordinal"])
    assert len(recipients) == 3 and len({r["id"] for r in recipients}) == 3
    assert [r["row_key"] for r in recipients] == [r["row_key"] for r in rows]
    assert [r["plan"][0]["text"] for r in recipients] == [r["text"] for r in rows]
    assert {r["phone"] for r in recipients} == {PHONE}
    assert [r["manual_consent"] for r in recipients] == [True, False, True]
    assert all(r["admitted_at"] is None for r in recipients) and await ledger(c) == []
    recovered = await c.client.get(c.bulk+"/requests/"+prepared["idempotency_key"])
    assert recovered.status_code == 200 and recovered.json()["result"] == responses[0].json()["result"]
    changed = deepcopy(payload)
    changed["batch"]["items"][0]["text"] = "Outro pedido"
    rejected = await c.client.post(c.bulk+"/review", json=changed)
    assert rejected.status_code == 409 and rejected.json()["detail"]["code"] == "BULK_IDEMPOTENCY_CONFLICT"


async def test_negative_recovery_before_delayed_review_does_not_poison_the_key(bulk_case):
    c = bulk_case
    payload = review_payload(c)
    started, released = asyncio.Event(), asyncio.Event()
    async def body():
        started.set()
        await released.wait()
        yield json.dumps(payload).encode()
    pending = asyncio.create_task(c.client.post(c.bulk+"/review", content=body(), headers={"content-type": "application/json"}))
    try:
        await asyncio.wait_for(started.wait(), 5)
        negative = await c.client.get(c.bulk+"/requests/"+payload["idempotency_key"])
        assert negative.status_code == 404 and negative.headers["cache-control"] == "no-store"
    finally:
        released.set()
    response = await asyncio.wait_for(pending, 10)
    assert response.status_code == 201, response.text
    recovered = await c.client.get(c.bulk+"/requests/"+payload["idempotency_key"])
    assert recovered.status_code == 200 and recovered.json()["request_id"] == response.json()["request_id"]


async def test_500_explicit_rows_are_preserved_and_overflow_is_rejected_without_partial_batch(bulk_case):
    c = bulk_case
    rows = [item(c, text=f"Linha {index}") for index in range(500)]
    over_limit = await c.client.post(c.bulk+"/review", json=review_payload(c, rows+[item(c)]))
    assert over_limit.status_code == 422
    assert (await snapshot(c))["subscription_bulk_requests"] == []
    row = await batch(c, rows)
    current = await detail(c, row)
    assert current["batch"]["progress"]["total"] == 500
    assert current["recipients"]["total"] == 500 and current["recipients"]["pages"] == 17
    last = await c.client.get(c.bulk+"/"+row["batch_id"]+"/recipients", params={"page": 17})
    assert last.status_code == 200 and len(last.json()["items"]) == 20, last.text
    assert [r["ordinal"] for r in last.json()["items"]] == list(range(481, 501))
    assert await ledger(c) == []


@pytest.mark.parametrize("change", ["catalog", "permission", "device", "template", "timezone", "expired"])
async def test_confirmation_rechecks_frozen_review_and_expiry_before_inserting_batch(bulk_case, change):
    c = bulk_case
    g = await group(c)
    template = await create_template(c, template_definition(text="Texto original")) if change == "template" else None
    rows = [item(c, source="catalog", message_type="template" if template else "text",
                 template_id=template["id"] if template else None, text=None if template else "Literal")]
    prepared = await review(c, rows)
    if change == "catalog":
        await edit_group(c, g, [{"name": "Nome posterior", "phone": PHONE}])
    elif change == "permission":
        await event(c, text="Não quero mensagens promocionais", phone=PHONE)
    elif change == "device":
        await settings(c, lambda value: value.update(name="Dispositivo revisado"))
    elif change == "template":
        result = await save_template(c, template, template_definition(text="Texto posterior"))
        assert result.status_code == 200, result.text
    else:
        async with c.ctx.migration.begin() as db:
            if change == "timezone":
                await db.execute(text("UPDATE tenants SET timezone='America/Cuiaba' WHERE id=:t"), {"t": c.tenant})
            else:
                await db.execute(text("""UPDATE subscription_bulk_requests SET
                    created_at=statement_timestamp()-interval '121 seconds',expires_at=statement_timestamp()-interval '1 second'
                    WHERE tenant_id=:t AND idempotency_key=:key"""),
                    {"t": c.tenant, "key": UUID(prepared["idempotency_key"])})
    rejected = await confirm(c, prepared)
    assert rejected.status_code == (410 if change == "expired" else 409), rejected.text
    state = await snapshot(c)
    assert state["subscription_bulk_batches"] == state["subscription_bulk_recipients"] == []
    assert await ledger(c) == []


async def test_manual_consent_never_overrides_optout_or_verified_self_number(bulk_case):
    c = bulk_case
    await event(c, text="Não quero mensagens promocionais", phone=PHONE)
    prepared = await review(c, [item(c), item(c, phone="5567999999998")])
    assert prepared["preview"]["eligible"] == 0 and prepared["preview"]["blocked"] == 2
    assert {r["error_code"] for r in prepared["preview"]["items"]} == {"BULK_OPTED_OUT", "BULK_SELF_CONTACT"}
    rejected = await confirm(c, prepared)
    assert rejected.status_code == 409 and rejected.json()["detail"]["code"] == "BULK_NO_ELIGIBLE"
    mixed = await batch(c, [item(c), item(c, phone=OTHER_PHONE)])
    data = await detail(c, mixed)
    assert data["batch"]["progress"]["cancelled"] == 1 and data["batch"]["progress"]["pending"] == 1
    first, = await poll(c)
    permit = await grant(c, first)
    assert permit["authorized"] and permit["payload"]["phone"] == OTHER_PHONE


async def test_parallel_polls_serialize_bulk_campaign_and_scheduled_admission(bulk_case):
    c = bulk_case
    await batch(c, [item(c), item(c, phone=OTHER_PHONE)])
    await batch(c, [item(c, phone="5567999999904")])
    await campaign(c)
    await scheduled_message(c)
    await renew(c)
    responses = await asyncio.gather(*(internal(c, "/effects/pending", method="GET") for _ in range(3)))
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    effects = {r["operation_id"] for response in responses for r in response.json()["effects"]}
    assert len(effects) == 1
    async with c.ctx.migration.connect() as db:
        live = (await db.execute(text("""SELECT count(*) FROM (
            SELECT id FROM subscription_bulk_recipients WHERE tenant_id=:t AND status='running'
            UNION ALL SELECT id FROM subscription_campaign_recipients WHERE tenant_id=:t AND status='running'
            UNION ALL SELECT id FROM subscription_schedule_recipients WHERE tenant_id=:t AND status='running'
            ) running"""), {"t": c.tenant})).scalar_one()
        assert live == 1


async def test_duplicate_rows_keep_order_and_wait_interval_after_terminal_receipt(bulk_case):
    c = bulk_case
    row = await batch(c, [item(c, text="Primeira"), item(c, text="Segunda")], interval=60)
    first, = await poll(c)
    permit = await grant(c, first)
    assert permit["authorized"] and permit["payload"]["action"]["text"] == "Primeira"
    async with c.ctx.migration.begin() as db:
        await db.execute(text("""UPDATE subscription_bulk_batches SET next_admission_at=clock_timestamp()-interval '90 seconds'
            WHERE tenant_id=:t AND id=:id"""), {"t": c.tenant, "id": UUID(row["batch_id"])})
        await db.execute(text("""UPDATE subscription_bulk_recipients SET admitted_at=clock_timestamp()-interval '90 seconds'
            WHERE tenant_id=:t AND bulk_id=:id AND admitted_at IS NOT NULL"""), {"t": c.tenant, "id": UUID(row["batch_id"])})
    assert await poll(c) == []  # A long-running first send cannot release the second row.
    await finish(c, first)
    assert await poll(c) == []
    async with c.ctx.migration.begin() as db:
        await db.execute(text("""UPDATE subscription_bulk_recipients SET finished_at=clock_timestamp()-interval '7 seconds'
            WHERE tenant_id=:t AND bulk_id=:id AND status='sent'"""), {"t": c.tenant, "id": UUID(row["batch_id"])})
    assert await poll(c) == []  # Shared six-second gap passed; this batch's 60 seconds have not.
    async with c.ctx.migration.begin() as db:
        await db.execute(text("""UPDATE subscription_bulk_recipients SET finished_at=clock_timestamp()-interval '61 seconds'
            WHERE tenant_id=:t AND bulk_id=:id AND status='sent'"""), {"t": c.tenant, "id": UUID(row["batch_id"])})
    second, = await poll(c)
    assert second["operation_id"] != first["operation_id"]
    permit = await grant(c, second)
    assert permit["authorized"] and permit["payload"]["action"]["text"] == "Segunda"
    await finish(c, second)
    current = (await detail(c, row))["batch"]
    assert current["status"] == "completed" and current["progress"]["sent"] == 2
    assert {effect["payload"]["phone"] for effect in await ledger(c)} == {PHONE}


@pytest.mark.parametrize("state", ["accepted", "not_sent", "uncertain"])
async def test_receipts_drive_history_without_generic_resend_or_extra_attempts(bulk_case, monkeypatch, state):
    c = bulk_case
    row = await batch(c)
    first, = await poll(c)
    assert (await grant(c, first))["authorized"]
    await finish(c, first, state, code=None if state == "accepted" else
        "TRANSPORT_UNAVAILABLE_BEFORE_SEND" if state == "not_sent" else "MESSAGE_OUTCOME_UNCERTAIN")
    expected = {"accepted": "sent", "not_sent": "failed", "uncertain": "uncertain"}[state]
    async with no_external(c, monkeypatch, readonly=True):
        current = await detail(c, row)
        assert current["batch"]["progress"][expected] == 1
        assert current["recipients"]["items"][0]["status"] == expected
        result = await c.client.get(c.history+"/"+first["operation_id"])
        assert result.status_code == 200, result.text
        value = result.json()["item"]
        assert value["origin"] == {"code": "bulk", "label": "Mensagem em massa", "is_resend": False}
        assert value["status"] == expected and value["actions"] == [] and not value["resend"]["eligible"]
        history = (await c.client.get(c.bulk)).json()
        assert history["counts"] == {"total": int(state == "accepted"), "last_30_days": int(state == "accepted"), "today": int(state == "accepted")}
    assert await poll(c) == [] and not (await grant(c, first))["authorized"]
    saved, = await ledger(c)
    assert saved["attempts"] == 1


async def test_lost_receipt_becomes_uncertain_and_reconciles_without_repeating_send(bulk_case, monkeypatch):
    c = bulk_case
    row = await batch(c)
    first, = await poll(c)
    assert (await grant(c, first))["authorized"]
    async with c.ctx.migration.begin() as db:
        await db.execute(text("""UPDATE subscription_device_effects SET authorized_at=clock_timestamp()-interval '181 seconds'
            WHERE tenant_id=:t AND id=:id"""), {"t": c.tenant, "id": UUID(first["operation_id"])})
    async with no_external(c, monkeypatch, readonly=True):
        # Projection must be honest even before the private poll persists cleanup.
        assert (await detail(c, row))["batch"]["progress"]["uncertain"] == 1
    assert await poll(c) == []
    assert (await detail(c, row))["batch"]["status"] == "uncertain"
    assert not (await grant(c, first))["authorized"]
    await finish(c, first, reconcile=True)
    current = (await detail(c, row))["batch"]
    assert current["status"] == "completed" and current["progress"]["sent"] == 1
    saved, = await ledger(c)
    assert saved["status"] == "accepted" and saved["attempts"] == 1
    assert await poll(c) == []


async def test_cancel_has_optimistic_version_and_recovers_same_committed_operation(bulk_case):
    c = bulk_case
    row = await batch(c)
    first, = await poll(c)
    stale = {"idempotency_key": str(uuid4()), "expected_version": row["version"],
        "action": "cancel", "confirmation": "CANCELAR"}
    rejected = await c.client.post(c.bulk+"/"+row["batch_id"]+"/actions", json=stale)
    assert rejected.status_code == 409 and rejected.json()["detail"]["rejection"] == {
        "reason": "stale_version", "idempotency_key": stale["idempotency_key"], "kind": "cancel",
        "batch_id": row["batch_id"], "expected_version": row["version"]}
    assert (await c.client.get(c.bulk+"/requests/"+stale["idempotency_key"])).status_code == 404
    current = (await detail(c, row))["batch"]
    payload = stale | {"expected_version": current["version"]}
    replies = await asyncio.gather(*(c.client.post(c.bulk+"/"+row["batch_id"]+"/actions", json=payload) for _ in range(2)))
    assert [r.status_code for r in replies] == [200, 200], [r.text for r in replies]
    assert sorted(r.json()["replayed"] for r in replies) == [False, True]
    assert replies[0].json()["result"] == replies[1].json()["result"]
    assert replies[0].json()["result"]["batch"]["status"] == "cancelled"
    assert not (await grant(c, first))["authorized"] and await poll(c) == []
    saved, = await ledger(c)
    assert (saved["status"], saved["attempts"], saved["error_code"]) == ("not_sent", 0, "BULK_CANCELLED")


async def test_cancel_keeps_late_accepted_receipt_and_closes_only_pending_rows(bulk_case):
    c = bulk_case
    row = await batch(c, [item(c), item(c, phone=OTHER_PHONE)])
    first, = await poll(c)
    assert (await grant(c, first))["authorized"]
    await cancel(c, row)
    assert not (await grant(c, first, recheck=True))["authorized"]
    await finish(c, first)  # Provider acceptance may race with the cancel action.
    current = (await detail(c, row))["batch"]
    assert current["status"] == "cancelled"
    assert current["progress"]["sent"] == 1 and current["progress"]["cancelled"] == 1
    assert await poll(c) == [] and len(await ledger(c)) == 1


@pytest.mark.parametrize("change", ["optout", "group", "customer", "template"])
async def test_revocation_after_claim_denies_final_recheck_without_new_source_authority(bulk_case, change):
    c = bulk_case
    identity = await customer(c) if change == "customer" else None
    g = await group(c) if change == "group" else None
    template = await create_template(c, template_definition(text="Texto congelado")) if change == "template" else None
    rows = [item(c, source="catalog" if change in {"group", "customer"} else "manual",
        message_type="template" if template else "text", template_id=template["id"] if template else None,
        text=None if template else "Texto congelado")]
    row = await batch(c, rows)
    first, = await poll(c)
    assert (await grant(c, first))["authorized"]
    if change == "optout":
        await event(c, text="Não quero mensagens promocionais", phone=PHONE)
    elif change == "group":
        changed = await edit_group(c, g, [])
        await edit_group(c, changed, [{"name": "Adicionado novamente", "phone": PHONE}])
    elif change == "customer":
        async with c.ctx.migration.begin() as db:
            await db.execute(text("UPDATE customers SET version=version+1,active=false WHERE id=:id"), {"id": identity})
            await db.execute(text("UPDATE customers SET version=version+1,active=true WHERE id=:id"), {"id": identity})
    else:
        result = await save_template(c, template, template_definition(text="Alteração posterior"))
        assert result.status_code == 200, result.text
    denied = await grant(c, first, recheck=True)
    assert not denied["authorized"]
    assert denied["error_code"] == {"optout": "BULK_OPTED_OUT", "group": "BULK_SOURCE_REMOVED",
        "customer": "BULK_SOURCE_REMOVED", "template": "BULK_TEMPLATE_CHANGED"}[change]
    saved, = await ledger(c)
    assert saved["status"] == "running" and saved["attempts"] == 1
    assert saved["payload"]["action"]["text"] == "Texto congelado"


async def test_audio_template_keeps_three_parts_and_does_not_count_partial_acceptance(bulk_case, private_media):
    c = bulk_case
    resource = await upload(c, wav_bytes(), "audio", "synthetic.wav")
    template = await create_template(c, template_definition(kind="audio", text="Olá, {name}",
        followup_text="Final", asset_id=resource["asset"]["id"]))
    row = await batch(c, [item(c, message_type="template", template_id=template["id"], text=None)])
    first, = await poll(c)
    assert (await grant(c, first))["payload"]["action"]["type"] == "audio"
    parts = await ledger(c)
    assert [p["payload"]["action"]["type"] for p in parts] == ["audio", "text", "text"]
    assert not (await grant(c, {"operation_id": str(parts[1]["id"])}))["authorized"]
    await finish(c, first)
    second, = await poll(c)
    assert (await grant(c, second))["authorized"]
    await finish(c, second, "not_sent", code="MESSAGE_CONTENT_INVALID")
    assert await poll(c) == []
    assert [p["status"] for p in await ledger(c)] == ["accepted", "not_sent", "not_sent"]
    assert (await detail(c, row))["batch"]["progress"]["failed"] == 1
    history = (await c.client.get(c.bulk)).json()
    assert history["counts"] == {"total": 0, "last_30_days": 0, "today": 0}


async def test_private_media_checks_exact_effect_and_cancellation_after_storage_read(bulk_case, private_media, monkeypatch):
    c = bulk_case
    resource = await upload(c, png_bytes(), "image", "synthetic.png")
    template = await create_template(c, template_definition(kind="image", text="Legenda", followup_text="",
        asset_id=resource["asset"]["id"]))
    row = await batch(c, [item(c, message_type="template", template_id=template["id"], text=None)])
    effect, = await poll(c)
    asset = (await grant(c, effect))["payload"]["action"]["asset"]
    path = f"/internal/qr/channels/{c.row['id']}/device/bulk/assets/{asset['id']}"
    params = scope(c) | {"operation_id": effect["operation_id"], "variant": "original"}
    headers = c.bridge.headers(c.row, live=True) | {"X-Orion-Device-Id": c.device["id"],
        "X-Orion-Device-Generation": str(c.saved["generation"]), "X-Orion-Device-Revision": str(c.device["revision"]),
        "X-Orion-Device-Effect": effect["operation_id"]}
    response = await c.private.get(path, params=params, headers=headers)
    assert response.status_code == 200 and response.content == private_media[(str(c.tenant), asset["id"], "original")]
    denied = await c.private.get(path, params=params | {"operation_id": str(uuid4())}, headers=headers)
    assert denied.status_code == 401
    started, released = Event(), Event()
    original = media.get_media
    def blocked(*args, **kwargs):
        started.set()
        assert released.wait(10), "Synthetic storage read was not released"
        return original(*args, **kwargs)
    monkeypatch.setattr(media, "get_media", blocked)
    pending = asyncio.create_task(c.private.get(path, params=params, headers=headers))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        await cancel(c, row)
    finally:
        released.set()
    rejected = await asyncio.wait_for(pending, 10)
    assert rejected.status_code == 409 and rejected.json()["detail"]["code"] == "BULK_CANCELLED"


async def test_auth_csrf_tenant_scope_and_private_immutable_columns_are_enforced(bulk_case):
    c = bulk_case
    prepared = await review(c)
    assert (await c.private.get(c.bulk)).status_code == 401
    assert (await c.client.post(c.bulk+"/review", json={}, headers={"x-orion-csrf": ""})).status_code == 403
    assert (await c.client.get(c.bulk.replace(str(c.tenant), str(c.ctx.b)))).status_code == 403
    assert (await c.client.get(c.bulk+"/"+str(uuid4()))).status_code == 404
    async with c.ctx.runtime() as db:
        for table in TABLES:
            with pytest.raises(DBAPIError) as denied:
                async with tenant_transaction(db, c.tenant):
                    await db.execute(text(f"SELECT * FROM {table}"))
            assert denied.value.orig.sqlstate == "42501"
    async with support_session_factory()() as db:
        for sql in ("UPDATE subscription_bulk_requests SET snapshot='{}'::jsonb WHERE tenant_id=:t",
                    "UPDATE subscription_bulk_recipients SET plan='[]'::jsonb WHERE tenant_id=:t",
                    "UPDATE subscription_bulk_recipients SET origins='[]'::jsonb WHERE tenant_id=:t",
                    "UPDATE subscription_bulk_batches SET interval_seconds=10 WHERE tenant_id=:t",
                    "DELETE FROM subscription_bulk_requests WHERE tenant_id=:t"):
            with pytest.raises(DBAPIError) as denied:
                async with db.begin():
                    await db.execute(text(sql), {"t": c.tenant})
            assert denied.value.orig.sqlstate == "42501"
    recovered = await c.client.get(c.bulk+"/requests/"+prepared["idempotency_key"])
    assert recovered.status_code == 200


async def test_previous_account_request_and_batch_do_not_leak_to_current_owner(bulk_case):
    c = bulk_case
    prepared = await review(c)
    response = await confirm(c, prepared)
    assert response.status_code == 202, response.text
    row = response.json()["result"]["batch"]
    async with c.ctx.migration.begin() as db:
        # Model retained history from another account without granting that
        # account any active access or changing the current authenticated owner.
        await db.execute(text("""INSERT INTO tenant_memberships(tenant_id,account_id,role,active)
            VALUES(:t,:a,'viewer',false)"""), {"t": c.tenant, "a": c.ctx.other})
        await db.execute(text("""UPDATE subscription_bulk_requests SET actor_account_id=:a
            WHERE tenant_id=:t AND idempotency_key=:key"""),
            {"t": c.tenant, "a": c.ctx.other, "key": UUID(prepared["idempotency_key"])})
        await db.execute(text("UPDATE subscription_bulk_batches SET owner_account_id=:a WHERE tenant_id=:t AND id=:id"),
            {"t": c.tenant, "a": c.ctx.other, "id": UUID(row["batch_id"])})
    for suffix in ("/requests/"+prepared["idempotency_key"], "/"+row["batch_id"], "/"+row["batch_id"]+"/recipients"):
        rejected = await c.client.get(c.bulk+suffix)
        assert rejected.status_code == 404, rejected.text
    rejected = await confirm(c, prepared)
    assert rejected.status_code == 404
    history = await c.client.get(c.bulk)
    assert history.status_code == 200 and history.json()["total"] == 0
    assert history.json()["active_batches_total"] == 0
    assert await poll(c) == [] and await ledger(c) == []


@pytest.mark.parametrize("change", ["owner", "viewer", "niche"])
async def test_current_owner_and_niche_precede_recovery_and_body_decoding(bulk_case, change):
    c = bulk_case
    prepared = await review(c)
    result = await confirm(c, prepared)
    assert result.status_code == 202, result.text
    row = result.json()["result"]["batch"]
    await revoke(c, change)
    for suffix in ("", "/options", "/contacts", "/requests/"+prepared["idempotency_key"],
                   "/"+row["batch_id"], "/"+row["batch_id"]+"/recipients"):
        rejected = await c.client.get(c.bulk+suffix)
        assert rejected.status_code == 403, rejected.text
    for suffix in ("/review", "/confirm", "/audience", "/"+row["batch_id"]+"/actions"):
        rejected = await c.client.post(c.bulk+suffix, content=b"not-json", headers={"content-type": "application/json"})
        assert rejected.status_code == 403, rejected.text


async def test_effect_cannot_be_rebound_to_recipient_of_another_valid_batch(bulk_case):
    c = bulk_case
    first = await batch(c)
    second = await batch(c, [item(c, phone=OTHER_PHONE)])
    effect, = await poll(c)
    saved, = await ledger(c)
    other_id = second["batch_id"] if str(saved["bulk_id"]) == first["batch_id"] else first["batch_id"]
    async with c.ctx.migration.connect() as db:
        other = (await db.execute(text("""SELECT id FROM subscription_bulk_recipients
            WHERE tenant_id=:t AND bulk_id=:bulk"""), {"t": c.tenant, "bulk": UUID(other_id)})).scalar_one()
    async with c.ctx.migration.begin() as db:
        with pytest.raises(DBAPIError) as denied:
            async with db.begin_nested():
                await db.execute(text("""UPDATE subscription_device_effects SET bulk_recipient_id=:other
                    WHERE tenant_id=:t AND id=:id"""), {"t": c.tenant, "id": saved["id"], "other": other})
        assert denied.value.orig.sqlstate == "23503"
        with pytest.raises(DBAPIError) as denied:
            async with db.begin_nested():
                await db.execute(text("""UPDATE subscription_bulk_recipients SET tenant_id=:foreign
                    WHERE tenant_id=:t AND id=:id"""), {"t": c.tenant, "id": other, "foreign": c.ctx.b})
        assert denied.value.orig.sqlstate == "23503"
    assert await ledger(c) == [saved]
    assert (await grant(c, effect))["authorized"]


@pytest.mark.parametrize("next_module", ["bulk", "scheduled", "campaign"])
async def test_external_terminal_effect_releases_shared_gate_without_rewriting_evidence(bulk_case, next_module):
    c = bulk_case
    old = await batch(c)
    first, = await poll(c)
    await settings(c, lambda value: value.update(name="Configuração após admissão"))
    closed, = await ledger(c)
    assert (closed["status"], closed["error_code"], closed["attempts"]) == ("not_sent", "settings_changed", 0)
    assert await poll(c) == []
    assert (await detail(c, old))["batch"]["status"] == "failed"
    assert await ledger(c) == [closed]
    if next_module == "bulk":
        following = await batch(c)
    elif next_module == "scheduled":
        following = await scheduled_message(c)
    else:
        following = await campaign(c)
    async with c.ctx.migration.begin() as db:
        # Advance only the disposable ledger; retain production pacing checks.
        await db.execute(text("""UPDATE subscription_bulk_recipients
            SET admitted_at=statement_timestamp()-interval '8 seconds',finished_at=statement_timestamp()-interval '7 seconds'
            WHERE tenant_id=:t AND bulk_id=:bulk AND status='failed'"""), {"t": c.tenant, "bulk": UUID(old["batch_id"])})
    await renew(c)
    response = await internal(c, "/effects/pending", method="GET")
    assert response.status_code == 200, response.text
    upcoming, = response.json()["effects"]
    assert upcoming["type"] == {"bulk": "bulk_action", "scheduled": "scheduled_action", "campaign": "campaign_action"}[next_module]
    assert upcoming["operation_id"] != first["operation_id"]
    permit = await grant(c, upcoming)
    assert permit["authorized"]
    assert permit[{"bulk": "batch_id", "scheduled": "schedule_id", "campaign": "campaign_id"}[next_module]] == following[
        {"bulk": "batch_id", "scheduled": "schedule_id", "campaign": "id"}[next_module]]
    assert next(r for r in await ledger(c) if r["id"] == closed["id"]) == closed


async def test_removed_device_is_reconciled_by_replacement_poll_without_transferring_frozen_row(bulk_case):
    c = bulk_case
    old = await batch(c, [item(c), item(c, phone=OTHER_PHONE)])
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
    data = await detail(c, old)
    assert data["batch"]["status"] == "failed" and data["batch"]["progress"]["cancelled"] == 1
    assert {r["device"]["id"] for r in data["recipients"]["items"]} == {old_device}
    assert await ledger(c) == [closed]
    following = await batch(c)
    async with c.ctx.migration.begin() as db:
        await db.execute(text("""UPDATE subscription_bulk_recipients
            SET admitted_at=statement_timestamp()-interval '8 seconds',finished_at=statement_timestamp()-interval '7 seconds'
            WHERE tenant_id=:t AND bulk_id=:bulk AND status='failed'"""), {"t": c.tenant, "bulk": UUID(old["batch_id"])})
    upcoming, = await poll(c)
    assert upcoming["operation_id"] != first["operation_id"] and upcoming["device_id"] == c.device["id"]
    permit = await grant(c, upcoming)
    assert permit["authorized"] and permit["batch_id"] == following["batch_id"]
    assert next(r for r in await ledger(c) if r["id"] == closed["id"]) == closed
