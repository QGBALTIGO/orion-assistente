"""Campaigns over real cookie/CSRF, PostgreSQL roles and the device broker.

Only storage, WhatsApp and paid ASR are synthetic. Scheduling starts exclusively
from the authenticated private poll, never from public reads or confirmation.
"""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from threading import Event
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import qr_channels, subscription_campaigns as api
from app import subscription_campaign_schemas as schemas, subscription_campaign_store as store
from app import subscription_chatbot_media as media, subscription_chatbot_rules, subscription_device_audio
from app import subscription_sent_messages, subscription_templates
from app.catalog_api import support_session_factory
from app.database import tenant_transaction
from app.plan_routing import include_plan_router
from test_subscription_chatbot_postgres import (
    chatbot_case, device_case, disposable_environment, new_owner_http, onboarding_db, private_media,
    input_event, rule_create, definition as rule_definition,
)  # noqa: F401 - actual transitive auth/session/role/lease fixtures.
from test_subscription_chatbot_test_postgres import revoke
from test_subscription_devices_postgres import action, event, internal, permit, scope, settings
from test_subscription_templates_postgres import create_template, save_template, template_definition, upload, png_bytes, wav_bytes

pytestmark = pytest.mark.integration
TABLES = ("subscription_campaign_groups", "subscription_campaign_members", "subscription_campaign_permissions",
    "subscription_campaigns", "subscription_campaign_sources", "subscription_campaign_recipients", "subscription_campaign_requests")
LIVE = ("subscription_device_effects", "subscription_device_events", "subscription_device_operations", "subscription_devices",
        "subscription_chatbot_runs", "subscription_chatbot_state")
PHONE = "5567999999901"


@pytest_asyncio.fixture
async def campaign_case(chatbot_case, monkeypatch):
    c = chatbot_case
    generic = [route for route in c.app.router.routes if "{resource}" in getattr(route, "path", "")]
    c.app.router.routes[:] = [route for route in c.app.router.routes if route not in generic]
    for router in (api.router, subscription_templates.router, subscription_sent_messages.router):
        include_plan_router(c.app, router)
    c.app.router.routes.extend(generic)
    c.campaigns = f"/api/v1/tenants/{c.tenant}/subscription-whatsapp/campaigns"
    c.templates = f"/api/v1/tenants/{c.tenant}/subscription-whatsapp/templates"
    c.history = f"/api/v1/tenants/{c.tenant}/subscription-whatsapp/sent-messages"
    for key in ("ENDPOINT", "BUCKET", "REGION", "ACCESS_KEY_ID", "SECRET_ACCESS_KEY"):
        monkeypatch.setenv("ORION_MEDIA_S3_" + key, "synthetic-campaign-" + key.lower())
    try:
        yield c
    finally:
        async with c.ctx.migration.begin() as db:
            for tenant in (c.tenant, c.ctx.a, c.ctx.b):
                await db.execute(text("DELETE FROM subscription_campaign_requests WHERE tenant_id=:t"), {"t": tenant})
                await db.execute(text("DELETE FROM subscription_device_effects WHERE tenant_id=:t AND type='campaign_action'"), {"t": tenant})
                for table in ("subscription_campaign_recipients", "subscription_campaign_sources", "subscription_campaigns",
                              "subscription_campaign_permissions", "subscription_campaign_members", "subscription_campaign_groups"):
                    await db.execute(text(f"DELETE FROM {table} WHERE tenant_id=:t"), {"t": tenant})


async def snapshot(c):
    result = {}
    async with c.ctx.migration.connect() as db:
        for table in (*TABLES, *LIVE):
            result[table] = (await db.execute(text(f"""SELECT COALESCE(jsonb_agg(to_jsonb(x)
                ORDER BY to_jsonb(x)::text),'[]'::jsonb) FROM {table} x WHERE tenant_id=:t"""), {"t": c.tenant})).scalar_one()
    return result


@asynccontextmanager
async def no_external(c, monkeypatch, *, readonly=False):
    before = await snapshot(c) if readonly else None
    calls, forbidden = list(c.bridge.calls), []
    async def deny(*args, **kwargs):
        forbidden.append("network")
        pytest.fail("Campaign management/read attempted external work")
    def deny_compile(*args, **kwargs):
        forbidden.append("compile/download")
        pytest.fail("Campaign operation recompiled a chatbot rule or downloaded media")
    with monkeypatch.context() as patch:
        patch.setattr(qr_channels, "_bridge_control", deny)
        patch.setattr(subscription_device_audio, "transcribe_audio", deny)
        patch.setattr(subscription_chatbot_rules, "compile_plan", deny_compile)
        patch.setattr(media, "get_media", deny_compile)
        yield
    assert not forbidden and c.bridge.calls == calls
    if readonly:
        assert await snapshot(c) == before


async def group(c, *, members=None, name="Grupo sintético"):
    body = {"idempotency_key": str(uuid4()), "name": name,
        "members": members if members is not None else [{"name": "Pessoa {phone}", "phone": "+"+PHONE}]}
    response = await c.client.post(c.campaigns + "/groups", json=body)
    assert response.status_code == 201, response.text
    return response.json()["result"]["group"]


async def edit_group(c, row, members, *, name=None):
    response = await c.client.patch(c.campaigns + "/groups/" + row["id"], json={"idempotency_key": str(uuid4()),
        "expected_version": row["version"], "name": name or row["name"], "members": members})
    assert response.status_code == 200, response.text
    return response.json()["result"]["group"]


def definition(c, groups, **changes):
    # Long enough to span a midnight CI boundary without an invented all-day
    # window: the overnight UTC window is valid every day and starts yesterday.
    now = datetime.now(timezone.utc)
    begin = (now-timedelta(hours=12)).strftime("%H:%M")
    end = (now+timedelta(hours=11, minutes=59)).strftime("%H:%M")
    return {"title": "Campanha sintética", "device_ids": [c.device["id"]], "group_ids": [g["id"] for g in groups],
        "message_type": "text", "text": "Olá, {name}! {phone}", "template_id": None,
        "start_date": (now-timedelta(days=1)).date().isoformat(), "start_time": "00:00", "timezone": "UTC",
        "window_start": begin, "window_end": end, "weekdays": list(range(7)), "interval_seconds": 30} | changes


async def draft(c, groups=None, **changes):
    groups = groups or [await group(c)]
    payload = {"idempotency_key": str(uuid4()), "campaign": definition(c, groups, **changes)}
    response = await c.client.post(c.campaigns, json=payload)
    assert response.status_code == 201, response.text
    return response.json()["result"]["campaign"]


async def review(c, row, *, key=None):
    response = await c.client.post(c.campaigns + "/" + row["id"] + "/review",
        json={"idempotency_key": str(key or uuid4()), "expected_version": row["version"]})
    assert response.status_code == 201, response.text
    return response.json()


def confirmation(prepared):
    return {"idempotency_key": prepared["idempotency_key"], "review_version": prepared["preview"]["review_version"],
        "confirmation": "AGENDAR", "consent_confirmed": True}


async def confirm(c, prepared):
    return await c.client.post(c.campaigns + "/" + prepared["campaign_id"] + "/confirm", json=confirmation(prepared))


async def schedule(c, groups=None, **changes):
    row = await draft(c, groups, **changes)
    response = await confirm(c, await review(c, row))
    assert response.status_code == 202, response.text
    return response.json()["result"]["campaign"]


async def renew(c):
    lease = c.bridge.connections[str(c.row["id"])]
    response = await c.private.post(f"/internal/qr/channels/{c.row['id']}/lease/renew",
        headers=c.bridge.headers(c.row), json={"owner": str(c.bridge.owner), "fence": lease["fence"]})
    assert response.status_code == 200, response.text


async def poll(c):
    await renew(c)
    response = await internal(c, "/effects/pending", method="GET")
    assert response.status_code == 200, response.text
    return [item for item in response.json()["effects"] if item["type"] == "campaign_action"]


async def grant(c, effect, *, recheck=False):
    await renew(c)
    response = await internal(c, "/effects/authorize", {**scope(c), "operation_id": effect["operation_id"], "recheck": recheck})
    assert response.status_code == 200, response.text
    return response.json()


async def finish(c, effect, state="accepted", *, code=None, reconcile=False):
    await renew(c)
    response = await internal(c, "/effects/finish", {**scope(c), "operation_id": effect["operation_id"], "state": state,
        "reconcile": reconcile, **({"error_code": code} if code else {}),
        **({"provider_id": "SYNTHETIC_CAMPAIGN_"+uuid4().hex} if state == "accepted" else {})})
    assert response.status_code == 200, response.text
    return response.json()


async def detail(c, row):
    response = await c.client.get(c.campaigns + "/" + row["id"])
    assert response.status_code == 200, response.text
    return response.json()


async def control(c, row, command):
    current = (await detail(c, row))["campaign"]
    response = await c.client.post(c.campaigns + "/" + row["id"] + "/actions", json={
        "idempotency_key": str(uuid4()), "expected_version": current["version"], "action": command})
    assert response.status_code == 200, response.text
    return response.json()["result"]["campaign"]


async def ledger(c):
    async with c.ctx.migration.connect() as db:
        return [dict(row) for row in (await db.execute(text("""SELECT * FROM subscription_device_effects
            WHERE tenant_id=:t AND type='campaign_action' ORDER BY created_at,campaign_part_index"""), {"t": c.tenant})).mappings().all()]


async def test_draft_groups_options_and_every_read_are_pure(campaign_case, monkeypatch):
    c = campaign_case
    async with no_external(c, monkeypatch):
        g = await group(c)
        row = await draft(c, [g])
    assert row["progress"]["total"] == 0 and row["status"] == "draft"
    async with no_external(c, monkeypatch, readonly=True):
        for suffix in ("", "/options", "/groups", "/groups/"+g["id"], "/"+row["id"], "/"+row["id"]+"/recipients"):
            result = await c.client.get(c.campaigns+suffix)
            assert result.status_code == 200 and result.headers["cache-control"] == "no-store", result.text
        options = (await c.client.get(c.campaigns+"/options")).json()
        assert options["devices"][0]["available"] and options["groups"][0]["member_count"] == 1
        assert "UTC" in options["timezones"] and not {"Factory", "localtime"}.intersection(options["timezones"])
        empty = await c.client.get(c.campaigns, params={"q": "ausente"})
        assert empty.json()["total"] == 0 and empty.json()["page_size"] == 30
        assert (await detail(c, row))["recipients"]["items"] == []
    state = await snapshot(c)
    assert state["subscription_campaign_permissions"] == [] and state["subscription_campaign_recipients"] == []
    assert await ledger(c) == []


async def test_confirm_deduplicates_once_freezes_names_and_is_concurrent_idempotent(campaign_case, monkeypatch):
    c = campaign_case
    one = await group(c, members=[{"name": "Primeiro", "phone": "+55 (67) 99999-9901"}, {"name": "Segundo", "phone": "5567999999902"}])
    two = await group(c, members=[{"name": "Outro nome", "phone": PHONE}])
    await event(c, text="Não quero mensagens promocionais", phone="5567999999902")
    row = await draft(c, [one, two])
    async with no_external(c, monkeypatch):
        prepared = await review(c, row)
        assert prepared["preview"]["total"] == 2 and prepared["preview"]["duplicates"] == 1
        assert prepared["preview"]["blocked"] == 1
        results = await asyncio.gather(confirm(c, prepared), confirm(c, prepared))
    assert [r.status_code for r in results] == [202, 202], [r.text for r in results]
    assert sorted(r.json()["replayed"] for r in results) == [False, True]
    assert all(r.json()["preview"] is None for r in results)
    state = await snapshot(c)
    assert len(state["subscription_campaign_recipients"]) == 2 and len(state["subscription_campaign_sources"]) == 2
    assert all(r["admitted_at"] is None for r in state["subscription_campaign_recipients"])
    assert len(state["subscription_campaign_requests"]) == 4  # two groups, draft, shared review/confirm
    assert await ledger(c) == []
    expected_name = "Primeiro" if UUID(one["id"]) < UUID(two["id"]) else "Outro nome"
    phone_row = next(r for r in state["subscription_campaign_recipients"] if r["phone"] == PHONE)
    assert phone_row["name"] == expected_name and phone_row["plan"][0]["text"] == f"Olá, {expected_name}! +{PHONE}"
    await edit_group(c, one, [{"name": "Nome novo", "phone": PHONE}, {"name": "Adicionado depois", "phone": "5567999999903"}], name="Renomeado")
    current = await detail(c, row)
    assert current["progress"]["total"] == 2
    assert any(item["name"] == expected_name for item in current["recipients"]["items"])
    assert {item["name"] for item in current["groups"]} == {one["name"], two["name"]}
    assert current["groups"] == prepared["preview"]["groups"]
    for item in current["groups"]:
        assert set(item) == {"id", "name", "version", "member_count", "blocked_count", "archived"}
        assert type(item["blocked_count"]) is int and type(item["archived"]) is bool
    assert next(item for item in current["groups"] if item["id"] == one["id"])["blocked_count"] == 1
    recovered = await c.client.get(c.campaigns+"/requests/"+prepared["idempotency_key"])
    assert recovered.status_code == 200 and recovered.json()["result"] == results[0].json()["result"]
    assert (await confirm(c, prepared)).json()["replayed"]


async def test_two_prepared_reviews_cannot_schedule_the_same_campaign_twice(campaign_case):
    c = campaign_case
    row = await draft(c)
    a, b = await review(c, row), await review(c, row)
    result = await asyncio.gather(confirm(c, a), confirm(c, b))
    assert sorted(r.status_code for r in result) == [202, 409]
    state = await snapshot(c)
    assert len(state["subscription_campaign_recipients"]) == 1
    assert len([r for r in state["subscription_campaign_requests"] if r["kind"] == "review" and r["status"] == "succeeded"]) == 1


async def test_same_key_crud_returns_original_and_different_payload_never_overwrites(campaign_case):
    c = campaign_case
    g = await group(c)
    payload = {"idempotency_key": str(uuid4()), "campaign": definition(c, [g])}
    result = await asyncio.gather(*(c.client.post(c.campaigns, json=payload) for _ in range(2)))
    assert all(r.status_code == 201 for r in result), [r.text for r in result]
    assert result[0].json()["request_id"] == result[1].json()["request_id"]
    different = await c.client.post(c.campaigns, json=payload | {"campaign": payload["campaign"] | {"title": "Outro pedido"}})
    assert different.status_code == 409 and different.json()["detail"]["code"] == "CAMPAIGN_IDEMPOTENCY_CONFLICT"
    recovered = await c.client.get(c.campaigns+"/requests/"+payload["idempotency_key"])
    assert recovered.json()["result"] == result[0].json()["result"]
    assert len((await snapshot(c))["subscription_campaigns"]) == 1


async def test_negative_recovery_before_delayed_post_does_not_prevent_later_same_key_commit(campaign_case):
    c = campaign_case
    g = await group(c)
    key, started, release = str(uuid4()), asyncio.Event(), asyncio.Event()
    body = json.dumps({"idempotency_key": key, "campaign": definition(c, [g])}).encode()
    async def stream():
        started.set()
        await release.wait()
        yield body
    task = asyncio.create_task(c.client.post(c.campaigns, content=stream(), headers={"content-type": "application/json"}))
    try:
        await asyncio.wait_for(started.wait(), 5)
        absent = await c.client.get(c.campaigns+"/requests/"+key)
        assert absent.status_code == 404 and absent.headers["cache-control"] == "no-store"
    finally:
        release.set()
    created = await asyncio.wait_for(task, 10)
    assert created.status_code == 201, created.text
    recovered = await c.client.get(c.campaigns+"/requests/"+key)
    assert recovered.status_code == 200 and recovered.json()["request_id"] == created.json()["request_id"]


@pytest.mark.parametrize("change", ["group", "device_revision", "expired"])
async def test_review_revalidates_current_versions_and_expiry_before_creating_audience(campaign_case, change):
    c = campaign_case
    g = await group(c)
    row, prepared = await draft(c, [g]), None
    prepared = await review(c, row)
    if change == "group":
        await edit_group(c, g, [{"name": "Nome alterado", "phone": PHONE}])
    elif change == "device_revision":
        await settings(c, lambda value: value.update(name="Configuração alterada") if "name" in value else value.update(test_phone_e164="+5567999999903"))
    else:
        async with c.ctx.migration.begin() as db:
            await db.execute(text("""UPDATE subscription_campaign_requests SET created_at=statement_timestamp()-interval '121 seconds',
                expires_at=statement_timestamp()-interval '1 second' WHERE id=:id"""), {"id": UUID(prepared["request_id"])})
    result = await confirm(c, prepared)
    assert result.status_code == (410 if change == "expired" else 409), result.text
    state = await snapshot(c)
    assert state["subscription_campaign_recipients"] == [] and state["subscription_campaign_sources"] == []
    assert state["subscription_campaigns"][0]["status"] == "draft" and await ledger(c) == []


async def test_expanded_budget_rejects_review_before_preview_or_audience_insert(campaign_case, monkeypatch):
    c = campaign_case
    row = await draft(c, [await group(c, members=[{"name": "Pessoa", "phone": f"55679999999{i:02d}"} for i in range(3)])])
    before = await snapshot(c)
    monkeypatch.setattr(schemas, "MAX_EXPANDED_BYTES", 1000)
    result = await c.client.post(c.campaigns+"/"+row["id"]+"/review", json={"idempotency_key": str(uuid4()), "expected_version": row["version"]})
    assert result.status_code == 422 and result.json()["detail"]["code"] == "CAMPAIGN_LIMIT", result.text
    assert await snapshot(c) == before


async def test_confirm_rechecks_aggregate_budget_before_first_audience_write(campaign_case, monkeypatch):
    c = campaign_case
    row = await draft(c)
    prepared = await review(c, row)
    before = await snapshot(c)
    monkeypatch.setattr(schemas, "MAX_EXPANDED_BYTES", 100)
    result = await confirm(c, prepared)
    assert result.status_code == 422 and result.json()["detail"]["code"] == "CAMPAIGN_LIMIT"
    assert await snapshot(c) == before


async def test_auth_csrf_cross_tenant_and_private_columns_have_real_postgres_guards(campaign_case):
    c = campaign_case
    row = await draft(c)
    prepared = await review(c, row)
    assert (await c.private.get(c.campaigns)).status_code == 401
    assert (await c.client.post(c.campaigns, json={}, headers={"x-orion-csrf": ""})).status_code == 403
    assert (await c.client.get(c.campaigns.replace(str(c.tenant), str(c.ctx.b)))).status_code == 403
    assert (await c.client.get(c.campaigns+"/"+str(uuid4()))).status_code == 404
    async with c.ctx.runtime() as db:
        for table in TABLES:
            with pytest.raises(DBAPIError) as denied:
                async with tenant_transaction(db, c.tenant):
                    await db.execute(text(f"SELECT * FROM {table}"))
            assert denied.value.orig.sqlstate == "42501"
    async with support_session_factory()() as db:
        for table, assignment in (("subscription_campaign_requests", "snapshot='{}'::jsonb"),
                                  ("subscription_campaign_recipients", "plan='[]'::jsonb"),
                                  ("subscription_campaign_sources", "name='changed'")):
            with pytest.raises(DBAPIError) as denied:
                async with db.begin():
                    await db.execute(text(f"UPDATE {table} SET {assignment} WHERE tenant_id=:t"), {"t": c.tenant})
            assert denied.value.orig.sqlstate == "42501"
    assert (await c.client.get(c.campaigns+"/requests/"+prepared["idempotency_key"])).status_code == 200


@pytest.mark.parametrize("change", ["owner", "viewer", "niche"])
async def test_owner_and_niche_are_checked_before_recovery_and_decoding_mutations(campaign_case, change):
    c = campaign_case
    row = await draft(c)
    prepared = await review(c, row)
    await revoke(c, change)
    for suffix in ("", "/options", "/groups", "/"+row["id"], "/requests/"+prepared["idempotency_key"]):
        assert (await c.client.get(c.campaigns+suffix)).status_code == 403
    for suffix in ("", "/groups", "/"+row["id"]+"/confirm", "/"+row["id"]+"/actions"):
        result = await c.client.post(c.campaigns+suffix, content=b"not-json", headers={"content-type": "application/json"})
        assert result.status_code == 403, result.text


async def test_stale_only_marker_and_same_key_recovery_preserve_original_group_operation(campaign_case):
    c = campaign_case
    g = await group(c)
    changed = await edit_group(c, g, [])
    key = str(uuid4())
    old = {"idempotency_key": key, "expected_version": g["version"], "name": g["name"], "members": []}
    result = await c.client.patch(c.campaigns+"/groups/"+g["id"], json=old)
    assert result.status_code == 409 and result.json()["detail"]["rejection"] == {
        "reason": "stale_version", "idempotency_key": key, "kind": "group_update", "campaign_id": None,
        "group_id": g["id"], "expected_version": g["version"]}
    future = await c.client.patch(c.campaigns+"/groups/"+g["id"], json=old | {"expected_version": changed["version"]+1})
    assert future.status_code == 409 and "rejection" not in future.json()["detail"]
    assert (await c.client.get(c.campaigns+"/requests/"+key)).status_code == 404


async def test_serial_pacing_waits_for_real_terminal_time_after_slow_claim(campaign_case):
    c = campaign_case
    g = await group(c, members=[{"name": str(i), "phone": f"55679999999{i:02d}"} for i in range(2)])
    row = await schedule(c, [g])
    first, = await poll(c)
    assert (await grant(c, first))["authorized"]
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE subscription_campaigns SET next_admission_at=clock_timestamp()-interval '90 seconds' WHERE id=:id"), {"id": UUID(row["id"])})
        await db.execute(text("UPDATE subscription_campaign_recipients SET admitted_at=clock_timestamp()-interval '90 seconds' WHERE tenant_id=:t AND admitted_at IS NOT NULL"), {"t": c.tenant})
    assert await poll(c) == [] and len(await ledger(c)) == 1
    await finish(c, first)
    assert await poll(c) == []  # Admission time is old; the actual finish is new.
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE subscription_campaign_recipients SET finished_at=clock_timestamp()-interval '7 seconds' WHERE tenant_id=:t AND status='sent'"), {"t": c.tenant})
    assert await poll(c) == []  # Tenant gap passed, campaign's 30 s did not.
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE subscription_campaign_recipients SET finished_at=clock_timestamp()-interval '31 seconds' WHERE tenant_id=:t AND status='sent'"), {"t": c.tenant})
    second, = await poll(c)
    assert second["operation_id"] != first["operation_id"] and len(await ledger(c)) == 2


async def test_parallel_polls_and_competing_campaigns_admit_one_live_recipient(campaign_case):
    c = campaign_case
    g = await group(c)
    await schedule(c, [g], title="Primeira")
    await schedule(c, [g], title="Segunda")
    await renew(c)
    responses = await asyncio.gather(*(internal(c, "/effects/pending", method="GET") for _ in range(2)))
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    ids = {item["operation_id"] for response in responses for item in response.json()["effects"] if item["type"] == "campaign_action"}
    assert len(ids) == 1 and len(await ledger(c)) == 1


async def test_pause_before_claim_cancels_admitted_attempt_and_resume_never_readmits_it(campaign_case):
    c = campaign_case
    row = await schedule(c)
    first, = await poll(c)
    paused = await control(c, row, "pause")
    assert paused["status"] == "paused"
    assert not (await grant(c, first))["authorized"]
    original = await ledger(c)
    resumed = await control(c, row, "resume")
    assert resumed["status"] == "completed" and resumed["progress"]["cancelled"] == 1
    assert await poll(c) == [] and await ledger(c) == original


async def test_pause_unadmitted_then_resume_keeps_exact_frozen_recipient(campaign_case):
    c = campaign_case
    row = await schedule(c)
    initial = (await snapshot(c))["subscription_campaign_recipients"]
    await control(c, row, "pause")
    assert await poll(c) == []
    await control(c, row, "resume")
    first, = await poll(c)
    assert (await grant(c, first))["payload"]["action"] == initial[0]["plan"][0]
    state = (await snapshot(c))["subscription_campaign_recipients"]
    assert len(state) == 1 and state[0]["id"] == initial[0]["id"]


async def test_remove_from_all_frozen_origins_then_readd_never_revives_old_audience(campaign_case):
    c = campaign_case
    a, b = await group(c), await group(c, name="Segundo grupo")
    row = await schedule(c, [a, b])
    a = await edit_group(c, a, [])
    assert (await detail(c, row))["progress"]["pending"] == 1
    b = await edit_group(c, b, [])
    assert (await detail(c, row))["progress"]["cancelled"] == 1
    await edit_group(c, a, [{"name": "Reimportado", "phone": PHONE}])
    assert await poll(c) == []
    current = await detail(c, row)
    assert current["progress"]["total"] == current["progress"]["cancelled"] == 1
    assert current["recipients"]["items"][0]["name"] == "Pessoa {phone}"


@pytest.mark.parametrize("revocation", ["remove_readd", "pause_resume"])
async def test_claim_revocation_survives_restored_membership_or_resumed_campaign_before_relay(campaign_case, revocation):
    c = campaign_case
    g = await group(c)
    row = await schedule(c, [g])
    effect, = await poll(c)
    assert (await grant(c, effect))["authorized"]
    original, = await ledger(c)
    if revocation == "remove_readd":
        g = await edit_group(c, g, [])
        await edit_group(c, g, [{"name": "Pessoa", "phone": PHONE}])
        reason = "CAMPAIGN_MEMBER_REMOVED"
    else:
        await control(c, row, "pause")
        await control(c, row, "resume")
        reason = "CAMPAIGN_PAUSED"
    assert (await detail(c, row))["campaign"]["status"] == "running"
    assert (await ledger(c)) == [original]  # No terminal rewrite of a running attempt.
    async with support_session_factory()() as db:
        async with db.begin():
            # Exercise the actual projection helper, not an endpoint mock, to
            # show another finish/poll cannot silently clear the old marker.
            from app.subscription_campaign_runtime import refresh_recipient
            await db.execute(text("SELECT id FROM tenants WHERE id=:t FOR UPDATE"), {"t": c.tenant})
            await refresh_recipient(db, c.tenant, original["campaign_recipient_id"])
    denied = await grant(c, effect, recheck=True)
    assert not denied["authorized"] and denied["error_code"] == reason
    await finish(c, effect)  # A send already attempted externally may still have a truthful accepted receipt.
    current = await detail(c, row)
    assert current["progress"]["sent"] == 1 and current["progress"]["pending"] == 0
    saved, = await ledger(c)
    assert saved["id"] == original["id"] and saved["attempts"] == 1 and saved["status"] == "accepted"
    assert await poll(c) == []


async def test_optout_after_claim_denies_final_recheck_and_explicit_reauthorization_is_only_for_new_campaigns(campaign_case):
    c = campaign_case
    g = await group(c)
    row = await schedule(c, [g])
    first, = await poll(c)
    assert (await grant(c, first))["authorized"]
    await event(c, text="Não quero mais mensagens promocionais", name="Pessoa", phone=PHONE)
    denied = await grant(c, first, recheck=True)
    assert not denied["authorized"] and denied["error_code"] == "CAMPAIGN_OPTED_OUT"
    await finish(c, first, "not_sent", code="CAMPAIGN_OPTED_OUT")
    current = (await c.client.get(c.campaigns+"/groups/"+g["id"])).json()
    member = current["members"][0]
    assert member["blocked"] and member["permission_version"] == 1
    request = {"idempotency_key": str(uuid4()), "expected_version": current["group"]["version"],
        "contacts": [{"phone": member["phone"], "version": member["permission_version"]}],
        "evidence": "Autorização sintética registrada novamente", "confirmation": "AUTORIZAR"}
    saved = await c.client.post(c.campaigns+"/groups/"+g["id"]+"/permissions", json=request)
    assert saved.status_code == 200, saved.text
    assert saved.json()["result"]["members"][0]["permission_version"] == 2
    assert (await detail(c, row))["progress"]["cancelled"] == 1 and await poll(c) == []
    state = await snapshot(c)
    permission, = state["subscription_campaign_permissions"]
    assert not permission["revoked"] and permission["evidence_hash"] and "Autorização sintética" not in json.dumps(state)
    assert len(await ledger(c)) == 1


async def test_accepted_audio_transcript_revokes_using_original_event_without_another_asr(campaign_case, monkeypatch):
    c = campaign_case
    await settings(c, lambda value: value["transcription"].update(enabled=True, reply_enabled=False))
    row = await schedule(c)
    first, = await poll(c)
    incoming = await event(c, kind="audio", text=None, message_id="SYNTHETIC_OPT_OUT_AUDIO",
        audio={"seconds": 12, "size": 3000, "media_type": "audio/ogg"})
    audio, = [item for item in incoming["effects"] if item["type"] == "transcribe"]
    assert (await permit(c, audio))["authorized"]
    calls = []
    async def transcribe_fake(request, *, max_minutes, authorize):
        calls.append("one ASR result")
        await authorize(12, phase="before_provider")
        await authorize(12, phase="after_provider")
        return {"text": "Não quero mensagens promocionais", "duration_seconds": 12}
    monkeypatch.setattr(subscription_device_audio, "transcribe_audio", transcribe_fake)
    headers = c.bridge.headers(c.row, live=True) | {"X-Orion-Device-Id": c.device["id"],
        "X-Orion-Device-Generation": str(c.saved["generation"]), "X-Orion-Device-Revision": str(c.device["revision"]),
        "X-Orion-Device-Effect": audio["operation_id"], "Content-Type": "audio/ogg"}
    response = await c.private.post(f"/internal/qr/channels/{c.row['id']}/device/audio", headers=headers, content=b"synthetic-audio")
    assert response.status_code == 200, response.text
    assert calls == ["one ASR result"]
    async with c.ctx.migration.connect() as db:
        proof = (await db.execute(text("""SELECT p.revoked,e.kind,x.status FROM subscription_campaign_permissions p
            JOIN subscription_device_events e ON e.tenant_id=p.tenant_id AND e.id=p.event_id
            JOIN subscription_device_effects x ON x.tenant_id=e.tenant_id AND x.event_id=e.id AND x.type='transcribe'
            WHERE p.tenant_id=:t AND p.phone=:phone"""), {"t": c.tenant, "phone": PHONE})).mappings().one()
    assert proof == {"revoked": True, "kind": "audio", "status": "accepted"}
    assert (await detail(c, row))["progress"]["cancelled"] == 1
    assert not (await grant(c, first))["authorized"]


@pytest.mark.parametrize("state", ["accepted", "not_sent", "uncertain"])
async def test_transport_receipt_history_and_readonly_recovery_keep_true_outcomes_without_resend(campaign_case, monkeypatch, state):
    c = campaign_case
    row = await schedule(c)
    attempt, = await poll(c)
    assert (await grant(c, attempt))["authorized"]
    await finish(c, attempt, state, code=None if state == "accepted" else "TRANSPORT_UNAVAILABLE_BEFORE_SEND" if state == "not_sent" else "MESSAGE_OUTCOME_UNCERTAIN")
    expected = {"accepted": "sent", "not_sent": "failed", "uncertain": "uncertain"}[state]
    async with no_external(c, monkeypatch, readonly=True):
        data = await detail(c, row)
        assert data["progress"][expected] == 1 and data["progress"]["pending"] == 0
        result = await c.client.get(c.history+"/"+attempt["operation_id"])
        assert result.status_code == 200, result.text
        item = result.json()["item"]
        assert item["origin"] == {"code": "campaign", "label": "Campanha", "is_resend": False}
        assert item["status"] == expected and not item["resend"]["eligible"] and item["actions"] == []
        assert item["received"]["text"] is None
    previous = await ledger(c)
    assert await poll(c) == [] and await ledger(c) == previous
    assert not (await grant(c, attempt))["authorized"]


async def test_interrupted_claim_is_reconciled_without_a_second_attempt(campaign_case):
    c = campaign_case
    row = await schedule(c)
    effect, = await poll(c)
    assert (await grant(c, effect))["authorized"]
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE subscription_device_effects SET authorized_at=clock_timestamp()-interval '181 seconds' WHERE id=:id"), {"id": UUID(effect["operation_id"])})
    assert await poll(c) == []
    assert (await detail(c, row))["progress"]["uncertain"] == 1
    await finish(c, effect, reconcile=True)
    assert (await detail(c, row))["progress"]["sent"] == 1
    saved, = await ledger(c)
    assert saved["attempts"] == 1 and saved["status"] == "accepted"
    assert await poll(c) == [] and not (await grant(c, effect))["authorized"]


async def test_audio_template_preserves_three_parts_partial_failure_and_closed_tail(campaign_case, private_media):
    c = campaign_case
    resource = await upload(c, wav_bytes(), "audio", "synthetic.wav")
    template = await create_template(c, template_definition(kind="audio", text="Olá, {name}", followup_text="Mensagem final", asset_id=resource["asset"]["id"]))
    row = await schedule(c, message_type="template", text=None, template_id=template["id"])
    first, = await poll(c)
    media_grant = await grant(c, first)
    assert media_grant["payload"]["action"]["type"] == "audio" and media_grant["payload"]["action"]["caption"] == ""
    all_parts = await ledger(c)
    assert [item["payload"]["action"]["type"] for item in all_parts] == ["audio", "text", "text"]
    tail = {"operation_id": str(all_parts[1]["id"])}
    assert not (await grant(c, tail))["authorized"]
    await finish(c, first)
    second, = await poll(c)
    assert (await grant(c, second))["payload"]["action"]["text"] == "Olá, Pessoa {phone}"
    await finish(c, second, "not_sent", code="MESSAGE_CONTENT_INVALID")
    assert await poll(c) == []
    parts = await ledger(c)
    assert [item["status"] for item in parts] == ["accepted", "not_sent", "not_sent"]
    assert [item["attempts"] for item in parts] == [1, 1, 0]
    current = await detail(c, row)
    assert current["progress"]["failed"] == 1 and len(current["recipients"]["items"][0]["parts"]) == 3


async def test_private_media_is_exact_effect_scoped_and_revoked_during_storage_read(campaign_case, private_media, monkeypatch):
    c = campaign_case
    resource = await upload(c, png_bytes(), "image", "synthetic.png")
    template = await create_template(c, template_definition(kind="image", text="Legenda", followup_text="", asset_id=resource["asset"]["id"]))
    row = await schedule(c, message_type="template", text=None, template_id=template["id"])
    effect, = await poll(c)
    permit_value = await grant(c, effect)
    asset = permit_value["payload"]["action"]["asset"]
    path = f"/internal/qr/channels/{c.row['id']}/device/campaigns/assets/{asset['id']}"
    params = scope(c) | {"operation_id": effect["operation_id"], "variant": "original"}
    headers = c.bridge.headers(c.row, live=True) | {"X-Orion-Device-Id": c.device["id"],
        "X-Orion-Device-Generation": str(c.saved["generation"]), "X-Orion-Device-Revision": str(c.device["revision"]),
        "X-Orion-Device-Effect": effect["operation_id"]}
    before = await snapshot(c)
    content = await c.private.get(path, params=params, headers=headers)
    assert content.status_code == 200 and content.content == private_media[(str(c.tenant), asset["id"], "original")]
    assert await snapshot(c) == before
    bad = await c.private.get(path, params=params | {"operation_id": str(uuid4())}, headers=headers)
    assert bad.status_code == 401
    started, released = Event(), Event()
    original = media.get_media
    def blocked(*args, **kwargs):
        started.set()
        assert released.wait(10), "test did not release synthetic storage read"
        return original(*args, **kwargs)
    monkeypatch.setattr(media, "get_media", blocked)
    task = asyncio.create_task(c.private.get(path, params=params, headers=headers))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        await control(c, row, "pause")
    finally:
        released.set()
    denied = await asyncio.wait_for(task, 10)
    assert denied.status_code == 409 and denied.json()["detail"]["code"] == "CAMPAIGN_PAUSED"
    saved, = await ledger(c)
    assert saved["status"] == "running" and saved["attempts"] == 1


async def test_template_revision_change_denies_last_recheck_without_recompiling(campaign_case):
    c = campaign_case
    value = template_definition(text="Texto congelado")
    template = await create_template(c, value)
    row = await schedule(c, message_type="template", text=None, template_id=template["id"])
    effect, = await poll(c)
    original = await grant(c, effect)
    assert original["payload"]["action"]["text"] == "Texto congelado"
    edited = await save_template(c, template, value | {"text": "Texto posterior"})
    assert edited.status_code == 200, edited.text
    denied = await grant(c, effect, recheck=True)
    assert not denied["authorized"] and denied["error_code"] == "CAMPAIGN_TEMPLATE_CHANGED"
    assert (await ledger(c))[0]["payload"]["action"]["text"] == "Texto congelado"


async def test_archive_preserves_original_attempts_sources_and_requests(campaign_case, monkeypatch):
    c = campaign_case
    row = await schedule(c)
    effect, = await poll(c)
    assert (await grant(c, effect))["authorized"]
    archived = await control(c, row, "archive")
    assert archived["archived"] and archived["status"] == "cancelled"
    assert not (await grant(c, effect, recheck=True))["authorized"]
    await finish(c, effect)  # External acceptance may finish after archive.
    before = await ledger(c)
    async with no_external(c, monkeypatch, readonly=True):
        listing = await c.client.get(c.campaigns)
        assert listing.json()["total"] == 0
        current = await detail(c, row)
        assert current["campaign"]["status"] == "cancelled" and current["progress"]["sent"] == 1
    assert await poll(c) == [] and await ledger(c) == before
