"""Stage-05 actual cookies, CSRF, PostgreSQL/RLS and frozen resend admission.

Only the external WhatsApp transport, object store and paid ASR are synthetic.
Reads and admission never connect a real device or deliver a real message.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import qr_channels, subscription_chatbot_engine, subscription_chatbot_http
from app import subscription_chatbot_rules, subscription_device_audio
from app import subscription_sent_messages as api
from app.database import tenant_transaction
from app.plan_routing import include_plan_router
from test_subscription_chatbot_postgres import (
    accepted, chat_effects, chatbot_case, definition, device_case, disposable_environment,
    edit, grant, input_event, media_headers, new_owner_http, onboarding_db, pending,
    private_media, rule_create,
)  # noqa: F401 - use the actual session/role/lease/broker fixture chain.
from test_subscription_chatbot_test_postgres import ISOLATED_TABLES, image_rule, revoke
from test_subscription_devices_postgres import action, create, event, internal, permit, scope, settings

pytestmark = pytest.mark.integration
SAFE_FAILURE = "TRANSPORT_UNAVAILABLE_BEFORE_SEND"
TABLES = (*ISOLATED_TABLES, "subscription_sent_message_resends")


@pytest_asyncio.fixture
async def messages_case(chatbot_case):
    c = chatbot_case
    generic = [route for route in c.app.router.routes if "{resource}" in getattr(route, "path", "")]
    c.app.router.routes[:] = [route for route in c.app.router.routes if route not in generic]
    include_plan_router(c.app, api.router)
    c.app.router.routes.extend(generic)
    c.messages = f"/api/v1/tenants/{c.tenant}/subscription-whatsapp/sent-messages"
    c.sent_serial, c.sent_rule = 0, None
    try:
        yield c
    finally:
        # Remove these append-only FK dependants before the parent fixture
        # deletes its synthetic effects, runs, devices and memberships.
        async with c.ctx.migration.begin() as db:
            await db.execute(text("DELETE FROM subscription_sent_message_resends WHERE tenant_id IN (:t,:a,:b)"),
                {"t": c.tenant, "a": c.ctx.a, "b": c.ctx.b})


async def state(c):
    result = {}
    async with c.ctx.migration.connect() as db:
        for table in TABLES:
            result[table] = (await db.execute(text(f"""SELECT COALESCE(
              jsonb_agg(to_jsonb(entry) ORDER BY to_jsonb(entry)::text),'[]'::jsonb)
              FROM {table} entry WHERE tenant_id IN (:t,:a,:b)"""),
              {"t": c.tenant, "a": c.ctx.a, "b": c.ctx.b})).scalar_one()
    return result


@asynccontextmanager
async def no_execution(c, monkeypatch, *, readonly=False):
    before = await state(c) if readonly else None
    calls = list(c.bridge.calls)
    forbidden = []

    async def denied(*args, **kwargs):
        forbidden.append("external execution")
        pytest.fail("History/admission attempted external execution")

    def compile_denied(*args, **kwargs):
        forbidden.append("recompile")
        pytest.fail("A frozen message must not compile the current rule again")

    with monkeypatch.context() as patch:
        patch.setattr(qr_channels, "_bridge_control", denied)
        patch.setattr(subscription_chatbot_http, "read_url_tags", denied)
        patch.setattr(subscription_device_audio, "transcribe_audio", denied)
        patch.setattr(subscription_device_audio, "_provider_transcribe", denied)
        patch.setattr(subscription_chatbot_rules, "compile_plan", compile_denied)
        try:
            yield
        finally:
            assert not forbidden
            assert c.bridge.calls == calls
            if readonly:
                after = await state(c)
                assert after == before, [table for table in TABLES if after[table] != before[table]]


async def effect_row(c, identity):
    async with c.ctx.migration.connect() as db:
        return dict((await db.execute(text("SELECT * FROM subscription_device_effects WHERE tenant_id=:t AND id=:id"),
            {"t": c.tenant, "id": UUID(str(identity))})).mappings().one())


async def finish_message(c, effect, outcome="not_sent", *, code=SAFE_FAILURE, authorized=False):
    if not authorized:
        assert (await grant(c, effect))["authorized"]
    response = await internal(c, "/effects/finish", {**scope(c), "operation_id": effect["operation_id"],
        "chatbot_revision": effect.get("chatbot_revision", c.chat_revision), "state": outcome,
        **({"error_code": code} if code else {}),
        **({"provider_id": "SYNTHETIC_" + uuid4().hex} if outcome == "accepted" else {})})
    assert response.status_code == 200, response.text
    return response.json()


async def message(c, outcome="pending", *, code=SAFE_FAILURE, phone=None):
    # A larger synthetic history may take longer than one 30-second lease on a
    # slow runner. Renew through the real fenced endpoint during preparation;
    # reads below still run without heartbeat, transport calls or DB mutation.
    connection = c.bridge.connections[str(c.row["id"])]
    renewed = await c.private.post(f"/internal/qr/channels/{c.row['id']}/lease/renew",
        headers=c.bridge.headers(c.row), json={"owner": str(c.bridge.owner), "fence": connection["fence"]})
    assert renewed.status_code == 200, renewed.text
    if c.sent_rule is None:
        c.sent_rule = await rule_create(c, definition(keyword="renovar", value="Resposta congelada para {phone_number}."))
    c.sent_serial += 1
    effect, = chat_effects(await input_event(c, "renovar", phone=phone or f"5567900{c.sent_serial:06d}"))
    if outcome != "pending":
        await finish_message(c, effect, outcome, code=code)
    return effect


async def detail(c, identity):
    response = await c.client.get(c.messages + "/" + str(identity))
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert set(data) == {"item", "timezone", "generated_at"}
    return data["item"]


async def resend(c, identity, *, key=None, version=None):
    if version is None:
        version = (await detail(c, identity))["version"]
    payload = {"expected_version": version, "idempotency_key": str(key or uuid4())}
    return await c.client.post(c.messages + "/" + str(identity) + "/resend", json=payload)


def descriptor(c, operation):
    return {"operation_id": operation["effect_id"], "type": "chatbot_action", "chatbot_revision": c.chat_revision}


async def foreign_message(c):
    """Valid independent company/device evidence; no transport is provisioned."""
    channel, device, generation, source, effect = (uuid4() for _ in range(5))
    async with c.ctx.migration.begin() as db:
        await db.execute(text("INSERT INTO tenant_memberships(tenant_id,account_id,role) VALUES(:t,:a,'owner')"),
            {"t": c.ctx.b, "a": c.ctx.other})
        await db.execute(text("""INSERT INTO qr_channels
          (id,tenant_id,owner_account_id,provider,label,credential_hash,credential_encrypted,
           allowed_senders,enabled,assistant_enabled,idempotency_key,request_fingerprint)
          SELECT :id,:t,:a,provider,'Canal sintético alheio',credential_hash,credential_encrypted,
           allowed_senders,false,false,:key,request_fingerprint FROM qr_channels WHERE id=:source"""),
          {"id": channel, "t": c.ctx.b, "a": c.ctx.other, "key": uuid4(), "source": c.row["id"]})
        await db.execute(text("""INSERT INTO subscription_devices
          (id,tenant_id,channel_id,owner_account_id,generation,name,settings,idempotency_key,request_fingerprint)
          SELECT :id,:t,:c,:a,:g,'Dispositivo alheio',settings,:key,request_fingerprint
          FROM subscription_devices WHERE id=:source"""),
          {"id": device, "t": c.ctx.b, "c": channel, "a": c.ctx.other, "g": generation,
           "key": uuid4(), "source": UUID(c.device["id"])})
        await db.execute(text("""INSERT INTO subscription_device_events
          (id,tenant_id,device_id,generation,provider_event_id,kind,occurred_at,payload)
          VALUES(:id,:t,:d,:g,'SYNTHETIC_FOREIGN','text',clock_timestamp(),
          '{"phone":"5567999999901","name":"FOREIGN_PRIVATE_CONTACT","text":"FOREIGN_PRIVATE_INPUT"}')"""),
          {"id": source, "t": c.ctx.b, "d": device, "g": generation})
        await db.execute(text("""INSERT INTO subscription_device_effects
          (id,tenant_id,device_id,generation,settings_revision,event_id,effect_key,type,purpose,payload,status,error_code)
          VALUES(:id,:t,:d,:g,1,:event,'synthetic-foreign','send_text','absence',
            '{"phone":"5567999999901","text":"FOREIGN_PRIVATE_OUTPUT"}','not_sent',:code)"""),
          {"id": effect, "t": c.ctx.b, "d": device, "g": generation, "event": source, "code": SAFE_FAILURE})
    return effect


async def test_history_statuses_private_detail_and_failures_are_reads_only(messages_case, monkeypatch):
    c = messages_case
    attempts = {outcome: await message(c, outcome, code="transport_timeout" if outcome == "uncertain" else SAFE_FAILURE)
                for outcome in ("accepted", "not_sent", "uncertain", "pending", "failed")}
    async with no_execution(c, monkeypatch, readonly=True):
        response = await c.client.get(c.messages)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        data = response.json()
        assert data["per_page"] == 30 and data["page"] == data["total_pages"] == 1
        assert data["filtered_total"] == data["counts"]["total"] == 5
        assert data["counts"]["scope"] == "message_attempts"
        indexed = {row["id"]: row for row in data["items"]}
        for outcome, expected in (("accepted", "sent"), ("not_sent", "failed"), ("uncertain", "uncertain"),
                                  ("pending", "pending"), ("failed", "failed")):
            row = indexed[attempts[outcome]["operation_id"]]
            assert row["status"] == expected and row["outcome"] == outcome
            assert row["origin"] == {"code": "chatbot", "label": "Chatbot", "is_resend": False}
            assert row["received"]["text"] == "renovar"
            assert row["sent"]["text"].startswith("Resposta congelada para ")
            assert row["resend"]["eligible"] is (outcome == "not_sent")
            assert bool(row["actions"]) is (outcome == "not_sent")
            detailed = await detail(c, row["id"])
            assert detailed["version"] == row["version"]
        uncertain = indexed[attempts["uncertain"]["operation_id"]]
        assert uncertain["failure"]["code"] == "MESSAGE_OUTCOME_UNCERTAIN"
        assert uncertain["resend"]["blocked_reason"]
        assert not any(secret in response.text for secret in ("credential_encrypted", "claimed_owner", "claimed_fence", "token_hash", "source_snapshot_hash"))


async def test_filters_snapshot_pagination_equal_timestamps_and_global_counts(messages_case, monkeypatch):
    c = messages_case
    effects = [await message(c) for _ in range(35)]
    # Equal timestamps force the documented UUID tiebreaker, independent of
    # row insertion order or a planner's chosen index.
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE subscription_device_effects SET created_at=statement_timestamp()-interval '1 minute' WHERE tenant_id=:t"), {"t": c.tenant})
    first = await c.client.get(c.messages)
    assert first.status_code == 200, first.text
    one = first.json()
    assert len(one["items"]) == 30 and one["total_pages"] == 2 and one["filtered_total"] == 35
    inserted = await message(c)
    async with no_execution(c, monkeypatch, readonly=True):
        response = await c.client.get(c.messages, params={"page": 2, "snapshot": one["snapshot"]})
        assert response.status_code == 200, response.text
        two = response.json()
        assert len(two["items"]) == 5 and two["snapshot"] == one["snapshot"]
        assert two["filtered_total"] == 35 and two["counts"]["total"] == 35
        combined = [row["id"] for row in one["items"] + two["items"]]
        assert len(set(combined)) == 35 and set(combined) == {row["operation_id"] for row in effects}
        assert combined == sorted(combined, reverse=True)
        assert inserted["operation_id"] not in combined
        fresh = (await c.client.get(c.messages)).json()
        assert fresh["filtered_total"] == 36 and fresh["items"][0]["id"] == inserted["operation_id"]
        target = one["items"][0]
        filtered = await c.client.get(c.messages, params={"device_id": c.device["id"],
            "contact": target["recipient"]["phone_e164"], "q": "chatbot"})
        assert filtered.status_code == 200, filtered.text
        assert [row["id"] for row in filtered.json()["items"]] == [target["id"]]
        assert filtered.json()["counts"]["total"] == 36
        absent = await c.client.get(c.messages, params={"q": "no-synthetic-match"})
        assert absent.status_code == 200 and absent.json()["items"] == []
        assert absent.json()["counts"]["total"] == 36


async def test_message_counters_use_company_timezone_without_counting_receipts_twice(messages_case, monkeypatch):
    c = messages_case
    effects = [await message(c, "accepted") for _ in range(4)]
    current = datetime.now(timezone.utc)
    local_start = current.astimezone(ZoneInfo("America/Cuiaba")).replace(hour=0, minute=0, second=0, microsecond=0)
    dates = [current, local_start - timedelta(seconds=1), local_start - timedelta(days=29), local_start - timedelta(days=31)]
    async with c.ctx.migration.begin() as db:
        for effect, created in zip(effects, dates):
            await db.execute(text("UPDATE subscription_device_effects SET created_at=:at WHERE tenant_id=:t AND id=:id"),
                {"t": c.tenant, "id": UUID(effect["operation_id"]), "at": created})
    await event(c, kind="outbound", message_id="SYNTHETIC_RECEIPT", state="delivered")
    await event(c, kind="outbound", message_id="SYNTHETIC_RECEIPT", state="read")
    async with no_execution(c, monkeypatch, readonly=True):
        result = (await c.client.get(c.messages)).json()
        assert result["timezone"] == "America/Cuiaba"
        assert result["counts"] == {"total": 4, "today": 1, "last_30_days": 3, "scope": "message_attempts"}


async def test_real_session_csrf_tenant_ownership_and_runtime_grants(messages_case, monkeypatch):
    c = messages_case
    effect = await message(c, "not_sent")
    foreign = await foreign_message(c)
    item = await detail(c, effect["operation_id"])
    anonymous = await c.private.get(c.messages)
    assert anonymous.status_code == 401
    no_csrf = await c.client.post(c.messages + "/" + item["id"] + "/resend", headers={"x-orion-csrf": ""}, json={})
    assert no_csrf.status_code == 403
    async with no_execution(c, monkeypatch, readonly=True):
        listing = await c.client.get(c.messages)
        assert listing.status_code == 200 and listing.json()["counts"]["total"] == 1
        assert "FOREIGN_PRIVATE" not in listing.text
        assert (await c.client.get(c.messages + "/" + str(foreign))).status_code == 404
        assert (await c.client.get(c.messages.replace(str(c.tenant), str(c.ctx.b)))).status_code == 403
        assert (await resend(c, foreign, version=item["version"])).status_code == 404
    async with c.ctx.runtime() as db:
        async with tenant_transaction(db, c.tenant):
            visible = (await db.execute(text("SELECT id FROM subscription_device_effects WHERE id=:id"), {"id": foreign})).scalar_one_or_none()
            assert visible is None
        with pytest.raises(DBAPIError):
            async with tenant_transaction(db, c.tenant):
                await db.execute(text("SELECT payload FROM subscription_device_effects"))
        with pytest.raises(DBAPIError):
            async with tenant_transaction(db, c.tenant):
                await db.execute(text("SELECT source_snapshot_hash FROM subscription_sent_message_resends"))


@pytest.mark.parametrize("change", ["owner", "viewer", "niche"])
async def test_each_read_and_resend_rechecks_owner_and_niche_before_body(messages_case, monkeypatch, change):
    c = messages_case
    effect = await message(c, "not_sent")
    await revoke(c, change)
    async with no_execution(c, monkeypatch, readonly=True):
        for path in (c.messages, c.messages + "/" + effect["operation_id"], c.messages + "/resend-requests/" + str(uuid4())):
            assert (await c.client.get(path)).status_code == 403
        denied = await c.client.post(c.messages + "/" + effect["operation_id"] + "/resend",
            content=b"not-json", headers={"content-type": "application/json"})
        assert denied.status_code == 403


async def test_concurrent_same_key_lost_reply_recovery_and_original_are_idempotent(messages_case, monkeypatch):
    c = messages_case
    effect = await message(c, "not_sent")
    item = await detail(c, effect["operation_id"])
    original = await effect_row(c, item["id"])
    key = uuid4()
    async with no_execution(c, monkeypatch):
        results = await asyncio.gather(*[resend(c, item["id"], key=key, version=item["version"]) for _ in range(2)])
        assert [response.status_code for response in results] == [202, 202], [response.text for response in results]
        bodies = [response.json() for response in results]
        assert len({value["effect_id"] for value in bodies}) == 1
        assert all(value["request_id"] != str(key) for value in bodies)
        assert sorted(value["replayed"] for value in bodies) == [False, True]
        assert await effect_row(c, item["id"]) == original
        # Recover without trusting the first POST's response, exactly as after a
        # connection loss. GET must not reserve another effect or mutate rows.
        async with no_execution(c, monkeypatch, readonly=True):
            recovered = await c.client.get(c.messages + "/resend-requests/" + str(key))
            assert recovered.status_code == 200, recovered.text
            assert recovered.json()["effect_id"] == bodies[0]["effect_id"]
            assert recovered.json()["original_effect_id"] == item["id"]
        altered = await resend(c, item["id"], key=key, version="0" * 64)
        assert altered.status_code == 409
    async with c.ctx.migration.connect() as db:
        rows = (await db.execute(text("SELECT source_effect_id,new_effect_id,root_effect_id FROM subscription_sent_message_resends WHERE tenant_id=:t"), {"t": c.tenant})).mappings().all()
        assert len(rows) == 1
        assert str(rows[0]["source_effect_id"]) == str(rows[0]["root_effect_id"]) == item["id"]
        assert str(rows[0]["new_effect_id"]) == bodies[0]["effect_id"]
    retry = await detail(c, bodies[0]["effect_id"])
    assert retry["status"] == "pending" and retry["outcome"] == "pending"
    assert retry["origin"]["is_resend"] is True and retry["retry_of_effect_id"] == item["id"]
    assert retry["sent"] == item["sent"] and retry["received"] == item["received"]


async def test_concurrent_distinct_keys_for_one_source_create_only_one_attempt(messages_case, monkeypatch):
    c = messages_case
    effect = await message(c, "not_sent")
    item = await detail(c, effect["operation_id"])
    async with no_execution(c, monkeypatch):
        results = await asyncio.gather(*[resend(c, item["id"], version=item["version"]) for _ in range(2)])
        assert sorted(response.status_code for response in results) == [202, 409], [response.text for response in results]
    async with c.ctx.migration.connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM subscription_sent_message_resends WHERE tenant_id=:t"), {"t": c.tenant})).scalar_one() == 1


@pytest.mark.parametrize("outcome,code", [("accepted", SAFE_FAILURE), ("uncertain", SAFE_FAILURE),
    ("failed", SAFE_FAILURE), ("not_sent", None), ("not_sent", "RECIPIENT_NOT_REGISTERED"),
    ("not_sent", "unrecognized_secret_failure"), ("pending", None)])
async def test_uncertain_accepted_pending_and_unproven_failures_never_resend(messages_case, monkeypatch, outcome, code):
    c = messages_case
    effect = await message(c, outcome, code=code)
    async with no_execution(c, monkeypatch, readonly=True):
        item = await detail(c, effect["operation_id"])
        assert item["resend"]["eligible"] is False and item["actions"] == []
        denied = await resend(c, item["id"], version=item["version"])
        assert denied.status_code == 409, denied.text
        failed = (await c.client.get(c.messages, params={"status": "failed"})).json()
        expected = outcome in {"uncertain", "failed", "not_sent"}
        assert bool(failed["items"]) is expected
        safe = (await c.client.get(c.messages, params={"status": "failed_resendable"})).json()
        assert safe["items"] == []
        assert "unrecognized_secret_failure" not in json.dumps(item)


async def test_expired_unclaimed_attempt_is_derived_failed_without_mutating_original(messages_case, monkeypatch):
    c = messages_case
    effect = await message(c)
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE subscription_device_effects SET expires_at=clock_timestamp()-interval '1 second' WHERE id=:id"),
            {"id": UUID(effect["operation_id"])})
    before = await effect_row(c, effect["operation_id"])
    async with no_execution(c, monkeypatch, readonly=True):
        item = await detail(c, effect["operation_id"])
        assert item["status"] == "failed" and item["outcome"] == "not_sent"
        assert item["resend"]["eligible"] is True
        assert item["failure"]["code"] == "MESSAGE_EXPIRED_BEFORE_SEND"
    async with no_execution(c, monkeypatch):
        admitted = await resend(c, item["id"], version=item["version"])
        assert admitted.status_code == 202, admitted.text
    assert await effect_row(c, item["id"]) == before
    assert before["status"] == "pending" and before["attempts"] == 0
    retry = descriptor(c, admitted.json())
    offered = chat_effects(await pending(c))
    assert retry["operation_id"] in {effect["operation_id"] for effect in offered}
    assert (await grant(c, retry))["authorized"]


async def test_failed_sequence_needs_each_explicit_retry_and_accepted_predecessor(messages_case, monkeypatch):
    c = messages_case
    actions = [{"id": str(uuid4()), "type": "text", "text": value, "delay_seconds": 1}
               for value in ("Primeira congelada", "Segunda congelada", "Terceira congelada")]
    await rule_create(c, definition(response={"kind": "sequence", "actions": actions}))
    first, = chat_effects(await input_event(c))
    await finish_message(c, first)
    async with c.ctx.migration.connect() as db:
        original = (await db.execute(text("SELECT * FROM subscription_device_effects WHERE tenant_id=:t AND type='chatbot_action' ORDER BY effect_key"),
            {"t": c.tenant})).mappings().all()
    assert len(original) == 3 and all(row["status"] == "not_sent" for row in original)
    first_id, second_id, third_id = [str(row["id"]) for row in original]
    assert first_id == first["operation_id"]
    assert (await detail(c, first_id))["resend"]["eligible"] is True
    assert (await detail(c, second_id))["resend"]["eligible"] is False
    assert (await detail(c, third_id))["resend"]["eligible"] is False
    async with no_execution(c, monkeypatch):
        admitted = await resend(c, first_id)
        assert admitted.status_code == 202, admitted.text
        denied = await resend(c, second_id)
        assert denied.status_code == 409
    retry_one = descriptor(c, admitted.json())
    assert (await grant(c, retry_one))["payload"]["action"]["text"] == "Primeira congelada"
    await accepted(c, retry_one)
    assert chat_effects(await pending(c)) == []  # no automatic old tail revival
    assert (await detail(c, second_id))["resend"]["eligible"] is True
    assert (await detail(c, third_id))["resend"]["eligible"] is False
    async with no_execution(c, monkeypatch):
        admitted_two = await resend(c, second_id)
        assert admitted_two.status_code == 202, admitted_two.text
    retry_two = descriptor(c, admitted_two.json())
    record = await effect_row(c, retry_two["operation_id"])
    assert str(record["previous_effect"]) == retry_one["operation_id"]
    assert (await grant(c, retry_two))["payload"]["action"]["text"] == "Segunda congelada"
    await accepted(c, retry_two)
    assert chat_effects(await pending(c)) == []
    assert (await detail(c, third_id))["resend"]["eligible"] is True
    for identity, text_value in zip((first_id, second_id, third_id), ("Primeira congelada", "Segunda congelada", "Terceira congelada")):
        kept = await effect_row(c, identity)
        assert kept["status"] == "not_sent" and kept["payload"]["action"]["text"] == text_value
    async with c.ctx.migration.connect() as db:
        plans = (await db.execute(text("""SELECT r.plan FROM subscription_sent_message_resends x
          JOIN subscription_device_effects e ON e.tenant_id=x.tenant_id AND e.id=x.new_effect_id
          JOIN subscription_chatbot_runs r ON r.tenant_id=e.tenant_id AND r.id=e.chatbot_run_id
          WHERE x.tenant_id=:t"""), {"t": c.tenant})).scalars().all()
    assert len(plans) == 2 and all(len(plan) == 1 for plan in plans)


async def test_list_retry_clones_tokens_and_the_original_bound_action_without_recompiling(messages_case, monkeypatch):
    c = messages_case
    menu = {"kind": "list", "title": "Atendimento", "description": "Descrição pesquisável exclusiva",
        "footer": "Informações", "button_text": "Abrir", "sections": [{"id": str(uuid4()), "title": "Seção",
        "rows": [{"id": str(uuid4()), "title": "Renovação", "description": "Detalhe da renovação",
            "action": {"id": str(uuid4()), "type": "text", "text": "Ação original preservada", "delay_seconds": 1}}]}]}
    await rule_create(c, definition(keyword="menu", response=menu))
    first, = chat_effects(await input_event(c, "menu"))
    await finish_message(c, first)
    source = await effect_row(c, first["operation_id"])
    old_token = source["payload"]["action"]["sections"][0]["rows"][0]["id"]
    async with c.ctx.migration.connect() as db:
        old_binding = (await db.execute(text("SELECT action FROM subscription_chatbot_selections WHERE outgoing_effect_id=:id"),
            {"id": source["id"]})).scalar_one()
    async with no_execution(c, monkeypatch):
        searched = await c.client.get(c.messages, params={"q": "Descrição pesquisável exclusiva"})
        assert searched.status_code == 200 and [item["id"] for item in searched.json()["items"]] == [first["operation_id"]]
        admitted = await resend(c, first["operation_id"])
        assert admitted.status_code == 202, admitted.text
    retry = descriptor(c, admitted.json())
    cloned = await effect_row(c, retry["operation_id"])
    new_token = cloned["payload"]["action"]["sections"][0]["rows"][0]["id"]
    assert new_token.startswith("ocb:") and new_token != old_token
    async with c.ctx.migration.connect() as db:
        bindings = (await db.execute(text("SELECT * FROM subscription_chatbot_selections WHERE tenant_id=:t"),
            {"t": c.tenant})).mappings().all()
        new, = [row for row in bindings if row["outgoing_effect_id"] == cloned["id"]]
        assert new["token_hash"] == hashlib.sha256(new_token.encode()).hexdigest()
        assert new["action"] == old_binding
        assert new["run_id"] == cloned["chatbot_run_id"] and new["phone"] == source["payload"]["phone"]
        c.ingress = (await db.execute(text("SELECT max(ingress_order) FROM subscription_chatbot_runs WHERE tenant_id=:t"),
            {"t": c.tenant})).scalar_one()
    assert (await grant(c, retry))["authorized"]
    await accepted(c, retry, provider="RETRY_MENU_ACCEPTED")
    for token, quoted, phone in ((old_token, "RETRY_MENU_ACCEPTED", source["payload"]["phone"]),
                                (new_token, "WRONG_MESSAGE", source["payload"]["phone"]),
                                (new_token, "RETRY_MENU_ACCEPTED", "5567999999909")):
        rejected = await input_event(c, "", selection_id=token, quoted_message_id=quoted, phone=phone)
        assert chat_effects(rejected) == []
    selected = await input_event(c, "", selection_id=new_token, quoted_message_id="RETRY_MENU_ACCEPTED", phone=source["payload"]["phone"])
    answer, = chat_effects(selected)
    assert (await grant(c, answer))["payload"]["action"]["text"] == "Ação original preservada"


@pytest.mark.parametrize("complete", [False, True])
async def test_carousel_retry_requires_all_frozen_assets_and_new_card_bindings(messages_case, private_media, monkeypatch, complete):
    from io import BytesIO
    from PIL import Image
    c = messages_case
    cards = [{"id": str(uuid4()), "title": f"Cartão {index}", "body": "Descrição sintética",
        "image_url": f"https://example.com/card-{index}.png", "button_text": "Selecionar"} for index in range(3)]
    await rule_create(c, definition(keyword="cards", response={"kind": "sequence", "actions": [
        {"id": str(uuid4()), "type": "carousel", "body": "Catálogo", "cards": cards, "delay_seconds": 1}]}))
    source_effect, = chat_effects(await input_event(c, "cards"))
    assert (await grant(c, source_effect))["authorized"]
    for index in range(3 if complete else 2):
        buffer = BytesIO()
        Image.new("RGB", (32, 24), (30 + index * 60, 80, 160)).save(buffer, format="PNG")
        headers = media_headers(c, source_effect) | {"X-Orion-Chatbot-Card": str(index), "Content-Type": "application/octet-stream"}
        prepared = await c.private.post(f"/internal/qr/channels/{c.row['id']}/device/chatbot/carousel-image",
            headers=headers, content=buffer.getvalue())
        assert prepared.status_code == 200, prepared.text
    await finish_message(c, source_effect, authorized=True)
    source = await effect_row(c, source_effect["operation_id"])
    async with no_execution(c, monkeypatch):
        item = await detail(c, source_effect["operation_id"])
        assert item["resend"]["eligible"] is complete
        admitted = await resend(c, item["id"], version=item["version"])
        assert admitted.status_code == (202 if complete else 409), admitted.text
    assert await effect_row(c, source_effect["operation_id"]) == source
    if not complete:
        async with c.ctx.migration.connect() as db:
            assert (await db.execute(text("SELECT count(*) FROM subscription_sent_message_resends WHERE tenant_id=:t"), {"t": c.tenant})).scalar_one() == 0
        return
    retry = descriptor(c, admitted.json())
    cloned = await effect_row(c, retry["operation_id"])
    assert cloned["result"]["carousel_assets"] == source["result"]["carousel_assets"]
    new_cards = cloned["payload"]["action"]["cards"]
    assert len(new_cards) == 3
    for index, (old, new) in enumerate(zip(source["payload"]["action"]["cards"], new_cards)):
        assert "image_url" not in new and new["asset"]["id"] == source["result"]["carousel_assets"][str(index)]
        assert new["selection_id"] != old["selection_id"]
    async with c.ctx.migration.connect() as db:
        selected = (await db.execute(text("SELECT action FROM subscription_chatbot_selections WHERE outgoing_effect_id=:id ORDER BY action->>'card_index'"),
            {"id": cloned["id"]})).scalars().all()
        assert selected == [{"kind": "carousel", "card_index": index} for index in range(3)]
    assert (await grant(c, retry))["payload"]["action"]["cards"] == new_cards


@pytest.mark.parametrize("available", [False, True])
async def test_http_derived_document_retry_uses_only_the_completed_private_snapshot(messages_case, private_media, monkeypatch, available):
    c = messages_case
    await rule_create(c, definition(keyword="arquivo", response={"kind": "sequence", "actions": [
        {"id": str(uuid4()), "type": "url", "url": "https://example.com/synthetic", "method": "POST",
         "custom_template": "", "as_file": True, "delay_seconds": 1}]}))
    request, = chat_effects(await input_event(c, "arquivo"))
    assert (await grant(c, request))["authorized"]
    completed = await internal(c, "/chatbot/http-result", {**scope(c), "operation_id": request["operation_id"],
        "chatbot_revision": c.chat_revision, "response_json": {"reply": "Documento completo congelado"}})
    assert completed.status_code == 200, completed.text
    document, = chat_effects(completed.json())
    await finish_message(c, document)
    source = await effect_row(c, document["operation_id"])
    if not available:
        async with c.ctx.migration.begin() as db:
            await db.execute(text("UPDATE subscription_chatbot_assets SET status='failed' WHERE tenant_id=:t AND id=:id"),
                {"t": c.tenant, "id": UUID(source["payload"]["action"]["asset"]["id"])})
    async with no_execution(c, monkeypatch):
        history = (await c.client.get(c.messages)).json()
        assert history["counts"]["total"] == 1
        item, = history["items"]
        assert item["sent"]["type"] == "document" and item["resend"]["eligible"] is available
        assert "url" not in item["sent"]["media"] and "sha256" not in item["sent"]["media"]
        admitted = await resend(c, item["id"], version=item["version"])
        assert admitted.status_code == (202 if available else 409), admitted.text
    assert await effect_row(c, source["id"]) == source
    if available:
        cloned = await effect_row(c, admitted.json()["effect_id"])
        assert cloned["payload"] == source["payload"]
        assert cloned["previous_effect"] == UUID(request["operation_id"])
        async with c.ctx.migration.connect() as db:
            plan = (await db.execute(text("SELECT plan FROM subscription_chatbot_runs WHERE id=:id"),
                {"id": cloned["chatbot_run_id"]})).scalar_one()
        assert len(plan) == 1 and plan[0]["action"]["type"] == "document"


@pytest.mark.parametrize("change", ["owner", "device_revision", "niche"])
@pytest.mark.parametrize("claimed", [False, True], ids=["before-claim", "before-relay-recheck"])
async def test_admitted_retry_loses_dispatch_authority_when_current_scope_is_revoked(messages_case, change, claimed):
    c = messages_case
    source = await message(c, "not_sent")
    admitted = await resend(c, source["operation_id"])
    assert admitted.status_code == 202, admitted.text
    retry = descriptor(c, admitted.json())
    if claimed:
        assert (await grant(c, retry))["authorized"]
    await revoke(c, change)
    response = await internal(c, "/effects/authorize", {**scope(c), "operation_id": retry["operation_id"],
        "chatbot_revision": c.chat_revision, "recheck": claimed})
    if change == "owner":
        assert response.status_code == 401, response.text
    elif change == "niche":
        assert response.status_code == 403, response.text
    else:
        assert response.status_code == 409 and response.json()["detail"]["code"] == "DEVICE_CONFLICT", response.text
    kept = await effect_row(c, retry["operation_id"])
    assert kept["attempts"] == (1 if claimed else 0) and kept["provider_id"] is None


async def test_actual_audio_duration_unknown_history_and_daily_utc_reservation_stay_distinct(messages_case, monkeypatch):
    c = messages_case
    await settings(c, lambda value: value["transcription"].update(enabled=True, max_minutes=30, reply_enabled=False))
    await action(c, "restart")
    await event(c, kind="status", state="connected")
    source = (await event(c, kind="audio", message_id="SYNTHETIC_STAGE05_AUDIO",
        audio={"seconds": 1, "size": 10, "media_type": "audio/ogg"}))["effects"][0]
    assert source["type"] == "transcribe" and (await permit(c, source))["authorized"]
    charged = []

    async def synthetic_audio(request, *, max_minutes, authorize):
        await authorize(12.75, phase="before_provider")
        charged.append("once")
        await authorize(12.75, phase="after_provider")
        return {"text": "Transcrição sintética completa", "duration_seconds": 12.75}

    monkeypatch.setattr(subscription_device_audio, "transcribe_audio", synthetic_audio)
    headers = c.bridge.headers(c.row, live=True) | {"Content-Type": "audio/ogg", "X-Orion-Device-Id": c.device["id"],
        "X-Orion-Device-Generation": str(c.saved["generation"]), "X-Orion-Device-Revision": str(c.device["revision"]),
        "X-Orion-Device-Effect": source["operation_id"]}
    completed = await c.private.post(f"/internal/qr/channels/{c.row['id']}/device/audio", headers=headers, content=b"synthetic-audio")
    assert completed.status_code == 200 and charged == ["once"], completed.text
    row = await effect_row(c, source["operation_id"])
    assert float(row["audio_duration_seconds"]) == 12.75 and row["audio_reserved_seconds"] == 13
    assert row["audio_completed_at"] is not None
    previous_month = datetime.now(timezone.utc).astimezone(ZoneInfo("America/Cuiaba")).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0) - timedelta(seconds=1)
    async with c.ctx.migration.begin() as db:
        await db.execute(text("""INSERT INTO subscription_device_effects
          (tenant_id,device_id,generation,settings_revision,event_id,effect_key,type,purpose,payload,
           status,attempts,audio_reserved_seconds,created_at,finished_at)
          SELECT tenant_id,device_id,generation,settings_revision,event_id,'historical-audio-without-duration',
           type,purpose,payload,'accepted',1,600,:at,:at FROM subscription_device_effects WHERE id=:id"""),
          {"id": row["id"], "at": previous_month})
        await db.execute(text("""INSERT INTO subscription_device_audio_usage(tenant_id,usage_day,reserved_seconds,attempts)
          VALUES(:t,(clock_timestamp() AT TIME ZONE 'UTC')::date-1,9000,1),
                (:other,(clock_timestamp() AT TIME ZONE 'UTC')::date,2400,1)"""),
          {"t": c.tenant, "other": c.ctx.b})
    async with no_execution(c, monkeypatch, readonly=True):
        response = await c.client.get(c.messages)
        assert response.status_code == 200, response.text
        metrics = response.json()["transcription"]
        assert metrics["total"] == {"known_seconds": 12.75, "unknown_completions": 1, "complete": False}
        assert metrics["month"] == {"known_seconds": 12.75, "unknown_completions": 0, "complete": True}
        assert metrics["daily"]["limit_seconds"] == 21600
        assert metrics["daily"]["reserved_seconds"] == 13
        assert metrics["daily"]["remaining_seconds"] == 21587
        reset = datetime.fromisoformat(metrics["daily"]["reset_at"].replace("Z", "+00:00"))
        assert reset.utcoffset() == timedelta(0) and (reset.hour, reset.minute, reset.second) == (0, 0, 0)
        assert "UTC" in metrics["daily"]["label"]
    assert charged == ["once"]


async def test_list_preview_marks_partial_text_and_detail_preserves_the_recorded_content(messages_case, monkeypatch):
    c = messages_case
    source_text = "á🙂" * 1500
    await rule_create(c, definition(value=source_text))
    effect, = chat_effects(await input_event(c))
    await finish_message(c, effect)
    async with no_execution(c, monkeypatch, readonly=True):
        response = await c.client.get(c.messages)
        assert response.status_code == 200, response.text
        summary, = response.json()["items"]
        assert summary["sent"]["truncated"] is True
        assert len(summary["sent"]["text"]) == api.LIST_TEXT_LIMIT
        full = await detail(c, effect["operation_id"])
        assert full["sent"]["text"] == source_text and full["sent"]["truncated"] is False


async def test_removed_device_history_survives_replacement_without_gaining_new_authority(messages_case, monkeypatch):
    c = messages_case
    effect = await message(c, "not_sent")
    original_device = c.device["id"]
    await action(c, "remove")
    await create(c, name="Novo dispositivo sintético")
    await action(c, "connect")
    await event(c, kind="status", state="connected")
    assert c.device["id"] != original_device
    async with no_execution(c, monkeypatch, readonly=True):
        item = await detail(c, effect["operation_id"])
        assert item["device"]["id"] == original_device and item["device"]["removed"] is True
        assert item["resend"]["eligible"] is False and item["actions"] == []
        listing = await c.client.get(c.messages, params={"device_id": original_device})
        assert listing.status_code == 200 and listing.json()["filtered_total"] == 1
        assert {row["id"]: row["removed"] for row in listing.json()["devices"]} == {
            original_device: True, c.device["id"]: False}
        denied = await resend(c, item["id"], version=item["version"])
        assert denied.status_code == 409
