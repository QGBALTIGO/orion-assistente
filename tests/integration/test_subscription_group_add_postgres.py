"""Durable membership jobs with real cookie/CSRF, PostgreSQL roles and grants.

Only WhatsApp I/O is synthetic. Its add callback uses the actual private SQL
authorization endpoint and checks that the one-shot dispatch was committed.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import qr_channels
from app import subscription_group_add as api
from app import subscription_group_add_store as store
from app import subscription_group_add_worker as worker
from app.catalog_api import support_session_factory
from app.database import tenant_transaction
from app.plan_routing import include_plan_router
from test_subscription_campaigns_postgres import (
    campaign_case, chatbot_case, device_case, disposable_environment, new_owner_http, onboarding_db,
    group as local_group, edit_group, event, revoke,
)  # noqa: F401 - existing real authentication, roles and disposable services.

pytestmark = pytest.mark.integration
DEST, NONADMIN, SOURCE = (f"1203630000000000{index:02d}@g.us" for index in (17, 18, 19))
PHONE, SECOND, EXISTING, FOURTH = (f"+55679999999{index:02d}" for index in (1, 2, 3, 4))
TABLES = ("subscription_group_add_batches", "subscription_group_add_items")


@pytest_asyncio.fixture
async def group_add_case(campaign_case, monkeypatch):
    c = campaign_case
    generic = [route for route in c.app.router.routes if "{resource}" in getattr(route, "path", "")]
    c.app.router.routes[:] = [route for route in c.app.router.routes if route not in generic]
    include_plan_router(c.app, api.router)
    c.app.router.routes.extend(generic)
    c.add_url = f"/api/v1/tenants/{c.tenant}/subscription-whatsapp/group-add"
    c.add_reads, c.add_calls, c.sdk_calls, c.lookup_calls, c.add_customers = [], [], [], [], []
    c.after_member_read, c.before_add, c.transport_failure = None, None, None
    c.add_status, c.add_reason, c.add_settled = "added", None, True
    c.lookup_status, c.lookup_reason, c.lookup_settled = "unrecorded", None, False
    c.admin = True
    c.present = {EXISTING}
    c.source_members = [{"phone": phone, "name": name, "role": "member"} for phone, name in (
        (PHONE, "Contato Um"), (SECOND, ""), (EXISTING, "Presente"), (FOURTH, "Contato Quatro"))]
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE subscription_devices SET status='connected' WHERE tenant_id=:t AND id=:d"),
                         {"t": c.tenant, "d": UUID(c.device["id"])})
    c.local = await local_group(c, members=[{"phone": phone, "name": name} for phone, name in (
        (PHONE, "Contato Um"), (SECOND, "Contato Dois"), (EXISTING, "Presente"))])
    parent_transport = qr_channels._bridge_control

    async def private_request(action, payload, *, live):
        return await c.private.post(f"/internal/qr/channels/{c.row['id']}/device/group-add/"+action,
            json=payload, headers=c.bridge.headers(c.row, live=live))

    async def transport(channel, action, method, payload=None):
        if not action.startswith("device/group-add/"):
            return await parent_transport(channel, action, method, payload)
        assert method == "POST" and channel["id"] == c.row["id"] and channel["tenant_id"] == c.tenant
        context = {key: payload[key] for key in ("device_id", "generation", "settings_revision")}
        if action.endswith("/groups"):
            c.add_reads.append((action, deepcopy(payload)))
            return {**context, "groups": [{"id": group, "name": name, "member_count": 6,
                "can_add": c.admin and group != NONADMIN} for group, name in (
                    (DEST, "Destino sintético"), (NONADMIN, "Sem administração"), (SOURCE, "Origem sintética"))]}
        if action.endswith("/members"):
            c.add_reads.append((action, deepcopy(payload)))
            members = deepcopy(c.source_members) if payload["group_id"] == SOURCE else [
                {"phone": phone, "name": "", "role": "member"} for phone in sorted(c.present)]
            if c.after_member_read: await c.after_member_read()
            return {**context, "group": {"id": payload["group_id"], "name": "Grupo sintético", "member_count": len(members)+2},
                "members": members, "total": len(members)+2, "unavailable_count": 1, "duplicate_count": 1,
                "is_admin": c.admin and payload["group_id"] != NONADMIN}
        if action.endswith("/lookup"):
            c.lookup_calls.append(deepcopy(payload))
            receipt = await private_request("receipt", payload, live=False)
            assert receipt.status_code == 200 and receipt.json()["read_only"] is True, receipt.text
            assert "owner" not in receipt.json() and "fence" not in receipt.json()
            return {key: value for key, value in payload.items() if key != "claim_token"} | {
                "status": c.lookup_status, "reason": c.lookup_reason, "settled": c.lookup_settled}
        assert action.endswith("/add")
        c.add_calls.append(deepcopy(payload))
        # This separate connection proves visibility before the external boundary.
        async with c.ctx.migration.connect() as db:
            saved = (await db.execute(text("SELECT * FROM subscription_group_add_items WHERE id=:id"),
                {"id": UUID(payload["operation_id"])})).mappings().one()
            assert saved["status"] == "running" and saved["dispatch_at"] is not None
            assert saved["claim_token"] == UUID(payload["claim_token"])
        first = await private_request("authorize", payload | {"recheck": False}, live=True)
        assert first.status_code == 200, first.text
        if c.before_add: await c.before_add()
        final = await private_request("authorize", payload | {"recheck": True}, live=True)
        if final.status_code != 200 or not final.json().get("authorized"):
            return {key: value for key, value in payload.items() if key != "claim_token"} | {
                "status": "not_attempted", "reason": "context_changed", "settled": True}
        assert first.json()["authorized"] and final.json()["claim_token"] == payload["claim_token"]
        if c.add_status != "not_attempted":
            c.sdk_calls.append(deepcopy(payload))
        if c.transport_failure: raise c.transport_failure
        return {key: value for key, value in payload.items() if key != "claim_token"} | {
            "status": c.add_status, "reason": c.add_reason, "settled": c.add_settled}

    monkeypatch.setattr(qr_channels, "_bridge_control", transport)
    try:
        yield c
    finally:
        async with c.ctx.migration.begin() as db:
            for tenant in (c.tenant, c.ctx.a, c.ctx.b):
                await db.execute(text("DELETE FROM subscription_group_add_items WHERE tenant_id=:t"), {"t": tenant})
                await db.execute(text("DELETE FROM subscription_group_add_batches WHERE tenant_id=:t"), {"t": tenant})
            if c.add_customers:
                await db.execute(text("DELETE FROM customers WHERE id=ANY(CAST(:ids AS uuid[]))"), {"ids": c.add_customers})


def preview_body(c, **changes):
    return {"device_id": c.device["id"], "destination_group_id": DEST, "source": "contacts",
        "contact_group_id": None, "without_group": False, "search": "", "created_from": None,
        "created_to": None, "source_group_id": None} | changes


async def preview(c, **changes):
    result = await c.client.post(c.add_url+"/preview", json=preview_body(c, **changes))
    assert result.status_code == 200, result.text
    assert result.headers["cache-control"] == "no-store"
    return result.json()


def batch_body(value, *, phones=None, key=None):
    return {"idempotency_key": str(key or uuid4()), "preview_token": value["preview_token"],
        "phones": phones or [row["phone"] for row in value["members"] if row["status"] == "ready"]}


async def admitted(c, *, phones=None, value=None):
    body = batch_body(value or await preview(c), phones=phones)
    result = await c.client.post(c.add_url+"/batches", json=body)
    assert result.status_code == 200, result.text
    return body, result.json()


async def detail(c, value):
    result = await c.client.get(c.add_url+"/batches/"+value["batch"]["id"])
    assert result.status_code == 200, result.text
    return result.json()


async def snapshot(c):
    async with c.ctx.migration.connect() as db:
        return {table: (await db.execute(text(f"""SELECT COALESCE(jsonb_agg(to_jsonb(x)
            ORDER BY to_jsonb(x)::text),'[]'::jsonb) FROM {table} x WHERE tenant_id=:t"""),
            {"t": c.tenant})).scalar_one() for table in TABLES}


async def customer(c, phone, *, name="Cliente sintético", created=None):
    async with c.ctx.migration.begin() as db:
        identity = (await db.execute(text("""INSERT INTO customers(tenant_id,name,whatsapp_e164,created_at)
            VALUES(:t,:name,:phone,COALESCE(:created,clock_timestamp())) RETURNING id"""),
            {"t": c.tenant, "name": name, "phone": phone, "created": created})).scalar_one()
    c.add_customers.append(identity)
    return identity


async def process(c, value):
    return await worker.process_subscription_group_add({}, c.tenant, UUID(value["batch"]["id"]))


async def advance(c, value):
    async with c.ctx.migration.begin() as db:
        await db.execute(text("""UPDATE subscription_group_add_batches SET next_item_at=clock_timestamp()-interval '1 second'
            WHERE tenant_id=:t AND id=:id"""), {"t": c.tenant, "id": UUID(value["batch"]["id"])})


async def optout(c, phone=PHONE):
    # Real opt-out ingestion retains its event/evidence FK; a hand-written
    # permission row would bypass the existing consent constraints.
    await event(c, text="Não quero mensagens promocionais", phone=phone[1:])


async def test_options_groups_and_previews_are_reads_and_no_admin_does_not_issue_token(group_add_case):
    c = group_add_case
    before = await snapshot(c)
    options = await c.client.get(c.add_url+"/options")
    groups = await c.client.get(c.add_url+"/groups", params={"device_id": c.device["id"]})
    assert options.status_code == groups.status_code == 200
    assert options.json()["devices"][0]["available"] is True
    assert [row["can_add"] for row in groups.json()["items"]] == [True, False, True]
    no_target = await preview(c, destination_group_id=None)
    assert no_target["counts"]["ready"] == 3 and no_target["preview_token"] is None
    no_admin = await preview(c, destination_group_id=NONADMIN)
    assert no_admin["destination"]["is_admin"] is False and no_admin["preview_token"] is None
    assert await snapshot(c) == before and c.add_calls == []


async def test_local_sources_deduplicate_and_filters_do_not_expose_unrelated_contacts(group_add_case):
    c = group_add_case
    await customer(c, PHONE, name="Nome do cadastro")
    await customer(c, FOURTH, name="Fora da lista")
    all_contacts = await preview(c)
    assert all_contacts["counts"]["total"] == 4 and all_contacts["counts"]["duplicates"] == 1
    assert all_contacts["members"][0]["name"] == "Nome do cadastro"
    selected = await preview(c, contact_group_id=c.local["id"])
    assert {row["phone"] for row in selected["members"]} == {PHONE, SECOND, EXISTING}
    ungrouped = await preview(c, without_group=True)
    assert [row["phone"] for row in ungrouped["members"]] == [FOURTH]
    by_name = await preview(c, search="CADASTRO")
    by_phone = await preview(c, search=PHONE)
    assert [row["phone"] for row in by_name["members"]] == [row["phone"] for row in by_phone["members"]] == [PHONE]
    assert all_contacts["counts"]["unavailable"] == 0 and c.add_calls == []


async def test_dates_use_company_timezone_and_include_the_whole_selected_day(group_add_case):
    c = group_add_case
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE tenants SET timezone='America/Sao_Paulo' WHERE id=:t"), {"t": c.tenant})
    dates = [datetime(2026, 10, 31, 2, 59, 59, tzinfo=timezone.utc), datetime(2026, 10, 31, 3, tzinfo=timezone.utc),
             datetime(2026, 11, 1, 2, 59, 59, tzinfo=timezone.utc), datetime(2026, 11, 1, 3, tzinfo=timezone.utc)]
    phones = [f"+12025550{n:03d}" for n in range(101, 105)]
    for phone, created in zip(phones, dates): await customer(c, phone, created=created)
    value = await preview(c, without_group=True, created_from="2026-10-31", created_to="2026-10-31")
    assert [row["phone"] for row in value["members"]] == phones[1:3]


async def test_catalog_above_one_thousand_is_complete_and_limit_is_not_silent_truncation(group_add_case):
    c = group_add_case
    async with c.ctx.migration.begin() as db:
        c.add_customers.extend((await db.execute(text("""INSERT INTO customers(tenant_id,name,whatsapp_e164)
            SELECT :t,'Sintético '||n,'+1202'||lpad(n::text,9,'0') FROM generate_series(1,1024) n RETURNING id"""),
            {"t": c.tenant})).scalars().all())
    value = await preview(c, without_group=True, destination_group_id=None)
    assert value["counts"]["total"] == 1024 and len(value["members"]) == 1024
    async with c.ctx.migration.begin() as db:
        c.add_customers.extend((await db.execute(text("""INSERT INTO customers(tenant_id,name,whatsapp_e164)
            SELECT :t,'Sintético '||n,'+1202'||lpad(n::text,9,'0') FROM generate_series(1025,5000) n RETURNING id"""),
            {"t": c.tenant})).scalars().all())
    maximum = await preview(c, without_group=True)
    assert maximum["counts"]["ready"] == 5000 and len(maximum["preview_token"]) <= 360000
    await customer(c, "+1202999999999")
    too_many = await c.client.post(c.add_url+"/preview", json=preview_body(c, without_group=True))
    assert too_many.status_code == 422 and too_many.json()["detail"]["code"] == "GROUP_ADD_LIMIT"
    assert all(not rows for rows in (await snapshot(c)).values()) and c.add_calls == []


async def test_other_group_source_keeps_missing_names_and_accounted_unavailable_members(group_add_case):
    c = group_add_case
    await optout(c, FOURTH)
    value = await preview(c, source="group", source_group_id=SOURCE)
    assert value["counts"] == {"total": 4, "ready": 2, "already_member": 1, "blocked": 1, "unavailable": 1, "duplicates": 1}
    assert value["members"][1]["name"] == ""
    before = len(c.add_reads)
    rejected = await c.client.post(c.add_url+"/preview", json=preview_body(c, source="group", source_group_id=DEST))
    assert rejected.status_code == 422 and len(c.add_reads) == before


async def test_concurrent_confirmation_has_one_atomic_batch_and_ordered_selected_subset(group_add_case):
    c = group_add_case
    prepared = await preview(c)
    body = batch_body(prepared, phones=[SECOND, PHONE])
    replies = await asyncio.gather(*(c.client.post(c.add_url+"/batches", json=body) for _ in range(2)))
    assert [reply.status_code for reply in replies] == [200, 200], [r.text for r in replies]
    assert replies[0].json()["batch"]["id"] == replies[1].json()["batch"]["id"]
    saved = await snapshot(c)
    assert len(saved[TABLES[0]]) == 1 and len(saved[TABLES[1]]) == 2 and c.add_calls == []
    assert [row["phone"] for row in replies[0].json()["members"]] == [PHONE, SECOND]
    assert [row["ordinal"] for row in replies[0].json()["members"]] == [1, 2]
    assert {reply.json()["replayed"] for reply in replies} == {False, True}


async def test_recovery_and_exact_replay_precede_expiry_but_changed_request_conflicts(group_add_case, monkeypatch):
    c = group_add_case
    body, value = await admitted(c, phones=[PHONE])
    real_clock = store.clock
    async def expired(db): return await real_clock(db)+timedelta(minutes=6)
    monkeypatch.setattr(store, "clock", expired)
    reads = len(c.add_reads)
    replay = await c.client.post(c.add_url+"/batches", json=body)
    recovery = await c.client.get(c.add_url+"/requests/"+body["idempotency_key"])
    assert replay.status_code == recovery.status_code == 200
    assert replay.json()["batch"] == recovery.json()["batch"] == value["batch"] and len(c.add_reads) == reads
    conflict = await c.client.post(c.add_url+"/batches", json=body | {"phones": [SECOND]})
    assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "GROUP_ADD_IDEMPOTENCY_CONFLICT"


@pytest.mark.parametrize("change", ["phone", "token", "expiry", "revision"])
async def test_changed_or_expired_preview_cannot_create_work(group_add_case, monkeypatch, change):
    c = group_add_case
    body = batch_body(await preview(c), phones=[PHONE])
    if change == "phone": body["phones"] = [FOURTH]
    elif change == "token": body["preview_token"] = body["preview_token"][:-8]+"abcdefgh"
    elif change == "expiry":
        real_clock = store.clock
        async def expired(db): return await real_clock(db)+timedelta(minutes=5)
        monkeypatch.setattr(store, "clock", expired)
    else:
        async with c.ctx.migration.begin() as db:
            await db.execute(text("UPDATE subscription_devices SET revision=revision+1 WHERE tenant_id=:t"), {"t": c.tenant})
    result = await c.client.post(c.add_url+"/batches", json=body)
    assert result.status_code == {"phone": 422, "token": 422, "expiry": 410, "revision": 409}[change], result.text
    assert all(not rows for rows in (await snapshot(c)).values()) and c.add_calls == []


async def test_local_source_change_during_provider_read_is_rejected_before_snapshot(group_add_case):
    c = group_add_case
    async def remove():
        c.after_member_read = None
        async with c.ctx.migration.begin() as db:
            await db.execute(text("""UPDATE subscription_campaign_members SET removed_at=clock_timestamp()
                WHERE tenant_id=:t AND phone=:p"""), {"t": c.tenant, "p": PHONE[1:]})
    c.after_member_read = remove
    result = await c.client.post(c.add_url+"/preview", json=preview_body(c))
    assert result.status_code == 409 and result.json()["detail"]["code"] == "GROUP_ADD_SOURCE_CHANGED"
    assert all(not rows for rows in (await snapshot(c)).values())


async def test_insert_failure_rolls_back_batch_items_and_idempotency_key(group_add_case, monkeypatch):
    c = group_add_case
    body = batch_body(await preview(c))
    async def fail_after_insert(*args, **kwargs): raise HTTPException(503, "Synthetic transaction failure")
    monkeypatch.setattr(store, "response", fail_after_insert)
    result = await c.client.post(c.add_url+"/batches", json=body)
    assert result.status_code == 503
    assert all(not rows for rows in (await snapshot(c)).values()) and c.add_calls == []


async def test_fresh_admin_proof_is_required_at_confirmation(group_add_case):
    c = group_add_case
    body = batch_body(await preview(c))
    c.admin = False
    result = await c.client.post(c.add_url+"/batches", json=body)
    assert result.status_code == 409 and result.json()["detail"]["code"] == "GROUP_ADD_ADMIN_REQUIRED"
    assert all(not rows for rows in (await snapshot(c)).values())


async def test_concurrent_workers_dispatch_once_then_obey_three_second_cadence(group_add_case):
    c = group_add_case
    _, value = await admitted(c)
    result = await asyncio.gather(process(c, value), process(c, value))
    assert sorted(result) == ["added", "not_claimed"] and len(c.add_calls) == len(c.sdk_calls) == 1
    progress = await detail(c, value)
    assert progress["batch"]["progress"]["added"] == progress["batch"]["progress"]["pending"] == 1
    async with c.ctx.migration.connect() as db:
        interval = (await db.execute(text("""SELECT b.next_item_at-i.finished_at FROM subscription_group_add_batches b
            JOIN subscription_group_add_items i ON i.batch_id=b.id WHERE b.id=:id AND i.status='added'"""),
            {"id": UUID(value["batch"]["id"])})).scalar_one()
    assert interval == timedelta(seconds=3)
    assert await process(c, value) == "not_claimed" and len(c.add_calls) == 1
    await advance(c, value)
    assert await process(c, value) == "added"
    final = await detail(c, value)
    assert final["batch"]["status"] == "completed" and final["batch"]["progress"]["added"] == 2
    assert len({call["operation_id"] for call in c.sdk_calls}) == 2


async def test_known_individual_refusal_continues_but_does_not_send_an_invitation(group_add_case):
    c = group_add_case
    _, value = await admitted(c)
    c.add_status, c.add_reason = "not_added", "provider_refused"
    assert await process(c, value) == "not_added"
    assert (await detail(c, value))["batch"]["status"] == "running"
    await advance(c, value)
    c.add_status, c.add_reason = "added", None
    await process(c, value)
    final = await detail(c, value)
    assert final["batch"]["status"] == "partial" and final["batch"]["progress"]["not_added"] == 1
    assert len(c.add_calls) == 2 and not any("invite" in str(call) for call in c.add_calls)


@pytest.mark.parametrize("kind", ["admin", "timeout", "unverified"])
async def test_context_or_unknown_outcome_stops_remaining_items_without_replay(group_add_case, kind):
    c = group_add_case
    _, value = await admitted(c)
    if kind == "admin": c.add_status, c.add_reason = "not_attempted", "not_admin"
    elif kind == "timeout": c.transport_failure = TimeoutError("Synthetic lost acknowledgement")
    else: c.add_status, c.add_reason = "uncertain", "provider_result_unverified"
    await process(c, value)
    final = await detail(c, value)
    expected = "partial" if kind == "admin" else "unknown"
    assert final["batch"]["status"] == expected and final["batch"]["progress"]["cancelled"] == 1
    await advance(c, value)
    assert await process(c, value) == "not_claimed" and len(c.add_calls) == 1


async def test_crash_after_dispatch_is_reaped_to_unknown_and_never_readmitted(group_add_case):
    c = group_add_case
    _, value = await admitted(c)
    claimed = await worker.claim(c.tenant, UUID(value["batch"]["id"]))
    assert claimed and not c.add_calls
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE subscription_group_add_items SET dispatch_at=clock_timestamp()-interval '46 seconds' WHERE id=:id"),
            {"id": claimed["item"]["id"]})
    await worker.reap_interrupted()
    assert (await detail(c, value))["batch"]["status"] == "unknown"
    assert await process(c, value) == "not_claimed" and c.add_calls == []


@pytest.mark.parametrize("change", ["optout", "source"])
async def test_pending_source_revocation_is_checked_before_dispatch(group_add_case, change):
    c = group_add_case
    _, value = await admitted(c, phones=[PHONE])
    if change == "optout": await optout(c)
    else:
        async with c.ctx.migration.begin() as db:
            await db.execute(text("UPDATE subscription_campaign_members SET removed_at=clock_timestamp() WHERE tenant_id=:t AND phone=:p"),
                {"t": c.tenant, "p": PHONE[1:]})
    assert await process(c, value) == "not_claimed"
    assert (await detail(c, value))["batch"]["status"] == "cancelled" and c.add_calls == []


async def test_source_revoked_between_grants_is_denied_before_the_provider_effect(group_add_case):
    c = group_add_case
    _, value = await admitted(c)
    async def revoke_source(): await optout(c)
    c.before_add = revoke_source
    assert await process(c, value) == "not_added"
    assert len(c.add_calls) == 1 and c.sdk_calls == []
    final = await detail(c, value)
    assert final["batch"]["status"] == "partial" and final["batch"]["progress"]["cancelled"] == 1


async def test_owner_revoked_after_confirmation_cannot_dispatch_or_recover(group_add_case):
    c = group_add_case
    body, value = await admitted(c)
    await revoke(c, "owner")
    assert await process(c, value) == "not_claimed" and c.add_calls == []
    assert (await c.client.get(c.add_url+"/requests/"+body["idempotency_key"])).status_code == 403
    assert (await c.client.post(c.add_url+"/batches", content=b"not-json")).status_code == 403


async def test_denied_claim_locks_tenant_and_batch_before_pending_rows_and_preserves_receipt(group_add_case, monkeypatch):
    c = group_add_case
    _, value = await admitted(c)
    claimed = await worker.claim(c.tenant, UUID(value["batch"]["id"]))
    await revoke(c, "owner")
    authority, denied = store.owner_authority, asyncio.Event()
    backend_pid = None

    async def observed_denial(db, batch):
        nonlocal backend_pid
        try:
            return await authority(db, batch)
        except HTTPException:
            backend_pid = (await db.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            denied.set()
            raise

    monkeypatch.setattr(store, "owner_authority", observed_denial)
    pending = None
    try:
        async with c.ctx.migration.begin() as held:
            await held.execute(text("SELECT id FROM tenants WHERE id=:t FOR UPDATE"), {"t": c.tenant})
            await held.execute(text("SELECT id FROM subscription_group_add_batches WHERE id=:id FOR UPDATE"),
                {"id": UUID(value["batch"]["id"])})
            pending = asyncio.create_task(worker.claim(c.tenant, UUID(value["batch"]["id"])))
            await asyncio.wait_for(denied.wait(), timeout=3)
            # Wait for a real PostgreSQL lock wait, not an assumed sleep timing.
            async with c.ctx.migration.connect() as observer:
                async with asyncio.timeout(3):
                    while not (await observer.execute(text("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=:pid AND NOT granted)"),
                            {"pid": backend_pid})).scalar_one():
                        await asyncio.sleep(0.01)
            await held.execute(text("SET LOCAL lock_timeout='500ms'"))
            rows = (await held.execute(text("""SELECT id FROM subscription_group_add_items
                WHERE batch_id=:id AND status='pending' FOR UPDATE"""),
                {"id": UUID(value["batch"]["id"])})).scalars().all()
            assert len(rows) == 1  # The denied claim must not own this row yet.
        assert await asyncio.wait_for(pending, timeout=3) is None
        await worker.finish(claimed, "added", None, False, True)
        saved = await snapshot(c)
        assert saved[TABLES[0]][0]["status"] == "partial"
        assert sorted(row["status"] for row in saved[TABLES[1]]) == ["added", "cancelled"]
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


async def make_unknown(c):
    body, value = await admitted(c, phones=[PHONE])
    c.transport_failure = TimeoutError("Synthetic timeout after dispatch")
    assert await process(c, value) == "unknown"
    c.transport_failure = None
    return body, value


async def read_receipt(c, value):
    return await worker.process_subscription_group_add_receipt({}, c.tenant, UUID(value["members"][0]["id"]))


async def test_unsettled_unknown_blocks_device_and_cannot_be_released_by_time_or_read(group_add_case):
    c = group_add_case
    _, value = await make_unknown(c)
    other = batch_body(await preview(c), phones=[SECOND])
    denied = await c.client.post(c.add_url+"/batches", json=other)
    assert denied.status_code == 409 and denied.json()["detail"]["code"] == "GROUP_ADD_BUSY"
    assert await read_receipt(c, value) == "unknown"
    denied_again = await c.client.post(c.add_url+"/batches", json=other)
    assert denied_again.status_code == 409 and len(c.add_calls) == 1 and len(c.lookup_calls) == 1


async def test_unsettled_preflight_keeps_sql_lane_until_terminal_read_receipt(group_add_case):
    c = group_add_case
    _, value = await admitted(c, phones=[PHONE])
    c.add_status, c.add_reason, c.add_settled = "not_attempted", "deadline_exceeded", False
    assert await process(c, value) == "unknown"
    other = batch_body(await preview(c), phones=[SECOND])
    denied = await c.client.post(c.add_url+"/batches", json=other)
    assert denied.status_code == 409 and denied.json()["detail"]["code"] == "GROUP_ADD_BUSY"
    c.lookup_status, c.lookup_reason, c.lookup_settled = "not_attempted", "deadline_exceeded", True
    assert await read_receipt(c, value) == "not_added"
    final = await detail(c, value)
    assert final["batch"]["status"] == "partial" and final["batch"]["progress"]["unknown"] == 0
    allowed = await c.client.post(c.add_url+"/batches", json=other)
    assert allowed.status_code == 200 and len(c.add_calls) == 1 and len(c.lookup_calls) == 1 and c.sdk_calls == []


async def test_settled_unknown_allows_other_contacts_but_same_destination_phone_stays_blocked(group_add_case):
    c = group_add_case
    old_body, value = await make_unknown(c)
    c.lookup_status, c.lookup_reason, c.lookup_settled = "uncertain", "provider_result_unverified", True
    assert await read_receipt(c, value) == "unknown"
    same = await c.client.post(c.add_url+"/batches", json=old_body | {"idempotency_key": str(uuid4())})
    assert same.status_code == 422 and same.json()["detail"]["code"] == "GROUP_ADD_NO_ELIGIBLE"
    _, other = await admitted(c, phones=[SECOND])
    assert other["batch"]["status"] == "pending" and len(c.add_calls) == 1
    assert (await detail(c, value))["batch"]["status"] == "unknown"


async def test_current_presence_after_settlement_is_already_member_without_claiming_add_success(group_add_case):
    c = group_add_case
    _, value = await make_unknown(c)
    c.lookup_status, c.lookup_reason, c.lookup_settled = "uncertain", "deadline_exceeded", True
    c.present.add(PHONE)
    assert await read_receipt(c, value) == "already_member"
    final = await detail(c, value)
    assert final["batch"]["status"] == "completed"
    assert final["batch"]["progress"]["already_member"] == 1 and final["batch"]["progress"]["added"] == 0
    assert final["batch"]["error_code"] is None and len(c.add_calls) == len(c.sdk_calls) == 1


async def test_private_grants_bind_claim_scope_lease_and_receipt_is_read_only_after_disconnect(group_add_case):
    c = group_add_case
    _, value = await admitted(c, phones=[PHONE])
    claimed = await worker.claim(c.tenant, UUID(value["batch"]["id"]))
    payload = claimed["payload"]
    prefix = f"/internal/qr/channels/{c.row['id']}/device/group-add"
    headers = c.bridge.headers(c.row, live=True)
    valid = await c.private.post(prefix+"/authorize", json=payload | {"recheck": True}, headers=headers)
    assert valid.status_code == 200 and valid.json()["authorized"] is True
    for patch in ({"claim_token": str(uuid4())}, {"operation_id": str(uuid4())}, {"phone": SECOND}):
        denied = await c.private.post(prefix+"/authorize", json=payload | patch, headers=headers)
        assert denied.status_code in {404, 409}, denied.text
    stale = await c.private.post(prefix+"/authorize", json=payload,
        headers=headers | {"X-Orion-Channel-Fence": str(int(headers["X-Orion-Channel-Fence"])+1)})
    assert stale.status_code in {401, 409}
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE subscription_devices SET status='disconnected',revision=revision+1 WHERE tenant_id=:t"), {"t": c.tenant})
    receipt = await c.private.post(prefix+"/receipt", json=payload, headers=c.bridge.headers(c.row))
    assert receipt.status_code == 200 and receipt.json()["read_only"] is True
    denied = await c.private.post(prefix+"/authorize", json=payload, headers=headers)
    assert denied.status_code == 200 and denied.json()["authorized"] is False


async def test_cookie_csrf_tenant_scope_and_database_immutability_are_enforced(group_add_case):
    c = group_add_case
    assert (await c.private.get(c.add_url+"/options")).status_code == 401
    assert (await c.client.post(c.add_url+"/preview", json=preview_body(c), headers={"x-orion-csrf": ""})).status_code == 403
    assert (await c.client.get(c.add_url.replace(str(c.tenant), str(c.ctx.b))+"/options")).status_code == 403
    _, value = await admitted(c)
    foreign = await c.client.get(c.add_url.replace(str(c.tenant), str(c.ctx.b))+"/batches/"+value["batch"]["id"])
    assert foreign.status_code == 403
    async with c.ctx.runtime() as db:
        for table in TABLES:
            with pytest.raises(DBAPIError) as denied:
                async with tenant_transaction(db, c.tenant): await db.execute(text(f"SELECT * FROM {table}"))
            assert denied.value.orig.sqlstate == "42501"
    async with support_session_factory()() as db:
        for table, column, expression in ((TABLES[0], "destination_group_id", "'120363000000000099@g.us'"),
                (TABLES[0], "idempotency_key", "gen_random_uuid()"),
                (TABLES[1], "claim_token", "gen_random_uuid()"), (TABLES[1], "phone", "'+12025550123'")):
            with pytest.raises(DBAPIError) as denied:
                async with db.begin():
                    await db.execute(text(f"UPDATE {table} SET {column}={expression} WHERE tenant_id=:t"), {"t": c.tenant})
            assert denied.value.orig.sqlstate == "42501"
    async with c.ctx.migration.connect() as db:
        with pytest.raises(DBAPIError) as foreign_context:
            async with db.begin():
                await db.execute(text("UPDATE subscription_group_add_items SET tenant_id=:other WHERE tenant_id=:t"),
                    {"t": c.tenant, "other": c.ctx.b})
        assert foreign_context.value.orig.sqlstate == "23503"
