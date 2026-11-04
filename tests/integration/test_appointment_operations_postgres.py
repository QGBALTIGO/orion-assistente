"""Real HTTP/PostgreSQL qualification of internal exceptions and service extras.

Synthetic tenants use the actual plan, booking, commission and finance engines.
No worker is started and all provider execution flags remain disabled.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import FastAPI
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from app.catalog_api import Actor, authenticate, support_session_factory
from app.database import get_engine
from test_booking import booking_db, phone, start_at

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def operations_db(booking_db, monkeypatch):
    d = booking_db
    for name in ("META_LIVE_SEND", "QR_AI_ENABLED", "INBOX_SEND_ENABLED",
                 "MARKETING_EXECUTION_ENABLED", "GOOGLE_LIVE_SYNC",
                 "PANEL_EMAIL_ENABLED", "PANEL_EMAIL_LIVE_SEND"):
        monkeypatch.setenv(name, "0")
    monkeypatch.setenv("BUSINESS_PROFILES_ENABLED", "1")
    monkeypatch.setenv("APPOINTMENT_COMMUNICATIONS_ENABLED", "1")
    monkeypatch.setenv("APPOINTMENT_COMMUNICATIONS_PILOT_ONLY", "0")
    monkeypatch.setenv("ORION_CENTRAL_APPOINTMENT_WHATSAPP_ENABLED", "0")
    get_engine.cache_clear()
    support_session_factory.cache_clear()
    owner, member, channel = uuid4(), uuid4(), uuid4()
    selected = {"actor": Actor("member", tenant_id=d["a"], account_id=owner,
                               membership_role="owner")}
    async with d["migrator"].begin() as db:
        for account, role in ((owner, "owner"), (member, "professional")):
            await db.execute(text("""INSERT INTO panel_accounts
                (id,name,email,password_hash,verified_at)
                VALUES (:id,'Pessoa sintética',:email,'synthetic-unused-hash',now())"""),
                {"id": account, "email": f"{account}@example.invalid"})
            await db.execute(text("""INSERT INTO tenant_memberships
                (tenant_id,account_id,role) VALUES(:t,:id,:role)"""),
                {"t": d["a"], "id": account, "role": role})
        await db.execute(text("""INSERT INTO professional_portal_links
            (tenant_id,professional_id,account_id,email,active)
            VALUES(:t,:p,:a,:email,true)"""),
            {"t": d["a"], "p": d["pro"], "a": member,
             "email": f"{member}@example.invalid"})
        await db.execute(text("UPDATE professionals SET commission_percent=25 WHERE tenant_id=:t"),
                         {"t": d["a"]})
        await db.execute(text("""INSERT INTO qr_channels
            (id,tenant_id,owner_account_id,provider,label,credential_hash,credential_encrypted,
             allowed_senders,enabled,idempotency_key,request_fingerprint)
            VALUES(:id,:t,:owner,'baileys','Canal sintético sem transporte',:hash,'unused',
                   CAST(:senders AS jsonb),true,:key,:hash)"""),
            {"id": channel, "t": d["a"], "owner": owner, "hash": "a" * 64,
             "senders": json.dumps([phone(910)[1:]]), "key": uuid4()})
        await db.execute(text("""INSERT INTO appointment_communication_settings
            (tenant_id,enabled,channel_id,reminder,review)
            VALUES(:t,true,:channel,true,true)"""), {"t": d["a"], "channel": channel})
    from app.main import app as production_app
    app = FastAPI()
    app.include_router(production_app.router)
    app.dependency_overrides[authenticate] = lambda: selected["actor"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                base_url="https://synthetic.invalid") as client:
        try:
            yield {**d, "app": app, "client": client, "selected": selected,
                   "owner": owner, "member": member,
                   "base": f"/api/v1/tenants/{d['a']}"}
        finally:
            async with d["migrator"].begin() as db:
                for table in ("commission_advance_allocations", "commission_settlements",
                              "loyalty_transactions", "loyalty_coupons", "financial_entries",
                              "appointment_notifications", "appointment_communication_settings", "qr_channels",
                              "management_requests", "service_addons", "professional_portal_links",
                              "tenant_membership_events", "tenant_memberships"):
                    await db.execute(text(f"DELETE FROM {table} WHERE tenant_id=:t"), {"t": d["a"]})
                await db.execute(text("DELETE FROM panel_accounts WHERE id IN (:a,:b)"),
                                 {"a": owner, "b": member})
            await get_engine().dispose()
            get_engine.cache_clear()
            support_session_factory.cache_clear()


def manual_payload(d, **changes):
    return {"mode": "exceptional", "professional_id": str(d["pro"]),
            "service_ids": [str(d["cut"])], "customer_name": "Cliente sintético",
            "customer_phone": phone(910), "walk_in": False,
            "start_at": start_at(d["day"], 19).isoformat(),
            "appointment_messages_opt_in": False, "idempotency_key": str(uuid4()), **changes}


async def create_manual(d, **changes):
    response = await d["client"].post(d["base"] + "/appointments/manual",
                                      json=manual_payload(d, **changes))
    assert response.status_code == 201, response.text
    return response.json()


async def create_normal(d, hour=10, minute=0, professional=None, services=None):
    data = {"professional_id": str(professional or d["pro"]),
            "service_ids": [str(value) for value in (services or [d["cut"]])],
            "customer_name": "Cliente agendado", "customer_phone": phone(920 + hour),
            "start_at": start_at(d["day"], hour, minute).isoformat(),
            "idempotency_key": str(uuid4())}
    response = await d["client"].post(d["base"] + "/appointments/hold", json=data)
    assert response.status_code == 201, response.text
    row = response.json()
    response = await d["client"].post(d["base"] + f"/appointments/{row['id']}/confirm", json={
        "expected_updated_at": row["updated_at"], "idempotency_key": str(uuid4())})
    assert response.status_code == 200, response.text
    return response.json()


def extras_payload(d, row, **changes):
    return {"service_ids": [str(d["beard"])], "expected_updated_at": row["updated_at"],
            "idempotency_key": str(uuid4()), "preserve_reserved_time": False, **changes}


async def count_rows(d, table, appointment):
    async with d["migrator"].connect() as db:
        return (await db.execute(text(f"SELECT count(*) FROM {table} WHERE tenant_id=:t AND appointment_id=:id"),
                                 {"t": d["a"], "id": UUID(appointment["id"])})).scalar_one()


@pytest.mark.asyncio
async def test_exceptional_19h_is_private_and_does_not_change_public_hours(operations_db):
    d = operations_db
    standard = manual_payload(d)
    for key in ("mode", "walk_in", "appointment_messages_opt_in"):
        standard.pop(key)
    denied = await d["client"].post(d["base"] + "/appointments/hold", json=standard)
    assert denied.status_code in (409, 422), denied.text
    row = await create_manual(d)
    assert row["status"] == "confirmed" and row["booking_mode"] == "exceptional"
    assert datetime.fromisoformat(row["start_at"]) == start_at(d["day"], 19)
    listed = await d["client"].get(d["base"] + "/availability", params={
        "professional_id": str(d["pro"]), "service_ids": str(d["cut"]), "day": str(d["day"])})
    assert listed.status_code == 200, listed.text
    assert all(datetime.fromisoformat(value).astimezone(start_at(d["day"], 0).tzinfo).hour < 18
               for value in listed.json()["slots"])
    # Extra exception switches are not accepted by the normal booking contract.
    forged = await d["client"].post(d["base"] + "/appointments/hold", json={**standard, "mode": "exceptional"})
    assert forged.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("table", ["schedule_blocks", "time_off"])
async def test_exceptional_time_still_honors_owner_blocks_and_time_off(operations_db, table):
    d = operations_db
    async with d["migrator"].begin() as db:
        await db.execute(text(f"""INSERT INTO {table}
            (tenant_id,professional_id,starts_at,ends_at)
            VALUES(:t,:p,:lo,:hi)"""),
            {"t": d["a"], "p": d["pro"], "lo": start_at(d["day"], 18),
             "hi": start_at(d["day"], 21)})
    response = await d["client"].post(d["base"] + "/appointments/manual", json=manual_payload(d))
    assert response.status_code == 409, response.text


@pytest.mark.asyncio
async def test_concurrent_exceptional_requests_and_retries_never_double_book(operations_db):
    d = operations_db
    first, second = manual_payload(d), manual_payload(d, customer_phone=phone(911))
    replies = await asyncio.gather(*(d["client"].post(d["base"] + "/appointments/manual", json=data)
                                     for data in (first, second)))
    assert sorted(reply.status_code for reply in replies) == [201, 409], [r.text for r in replies]
    winner = next(i for i, response in enumerate(replies) if response.status_code == 201)
    original = replies[winner].json()
    request = (first, second)[winner]
    repeats = await asyncio.gather(*(d["client"].post(d["base"] + "/appointments/manual", json=request)
                                    for _ in range(3)))
    assert all(reply.status_code == 201 and reply.json()["id"] == original["id"] for reply in repeats)
    mismatched = await d["client"].post(d["base"] + "/appointments/manual",
        json={**request, "customer_name": "Pessoa diferente"})
    assert mismatched.status_code == 409, mismatched.text
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM appointments WHERE tenant_id=:t"),
                                 {"t": d["a"]})).scalar_one() == 1


@pytest.mark.asyncio
async def test_retroactive_walk_in_has_true_date_pending_receivable_commission_and_no_messages(operations_db):
    d = operations_db
    past = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(days=1)
    row = await create_manual(d, mode="retroactive", start_at=past.isoformat(),
                              walk_in=True, customer_phone=None, final_total_cents=4000)
    assert row["status"] == "completed" and row["booking_mode"] == "retroactive"
    assert datetime.fromisoformat(row["start_at"]) == past
    assert row["total_cents"] == 4000
    async with d["migrator"].connect() as db:
        saved = (await db.execute(text("""SELECT a.created_at,a.start_at,a.end_at,a.commission_cents,
                c.whatsapp_e164,a.created_by_account_id FROM appointments a
                JOIN customers c ON c.id=a.customer_id AND c.tenant_id=a.tenant_id
                WHERE a.tenant_id=:t AND a.id=:id"""),
                {"t": d["a"], "id": UUID(row["id"])})).mappings().one()
        assert saved["created_at"] > saved["end_at"] > saved["start_at"]
        assert saved["whatsapp_e164"] is None and saved["created_by_account_id"] == d["owner"]
        assert saved["commission_cents"] == 1000
        finance = (await db.execute(text("""SELECT status,amount_cents,occurred_at FROM financial_entries
            WHERE tenant_id=:t AND appointment_id=:id"""),
            {"t": d["a"], "id": UUID(row["id"])})).mappings().one()
        assert finance["status"] == "pending" and finance["amount_cents"] == 4000
        assert finance["occurred_at"] == saved["end_at"]
        history = (await db.execute(text("SELECT event FROM appointment_history WHERE tenant_id=:t AND appointment_id=:id"),
                                   {"t": d["a"], "id": UUID(row["id"])})).scalars().all()
        assert "retroactive_created" in history
    assert await count_rows(d, "calendar_outbox", row) == 0
    assert await count_rows(d, "appointment_notifications", row) == 0


@pytest.mark.asyncio
async def test_retroactive_idempotency_and_time_modes_are_strict(operations_db):
    d = operations_db
    past = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(days=1)
    request = manual_payload(d, mode="retroactive", start_at=past.isoformat())
    first = await d["client"].post(d["base"] + "/appointments/manual", json=request)
    assert first.status_code == 201, first.text
    repeated = await d["client"].post(d["base"] + "/appointments/manual", json=request)
    assert repeated.status_code == 201 and first.json()["id"] == repeated.json()["id"]
    assert await count_rows(d, "financial_entries", first.json()) == 1
    for change in ({"mode": "exceptional", "start_at": past.isoformat()},
                   {"mode": "retroactive", "start_at": start_at(d["day"], 19).isoformat()},
                   {"mode": "exceptional", "walk_in": True, "customer_phone": phone(912)}):
        denied = await d["client"].post(d["base"] + "/appointments/manual", json=manual_payload(d, **change))
        assert denied.status_code in (409, 422), denied.text


@pytest.mark.asyncio
async def test_extras_preserve_original_price_and_snapshots_and_extend_free_slot(operations_db):
    d = operations_db
    row = await create_normal(d)
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE services SET price_cents=9900,duration_min=90 WHERE id=:id"), {"id": d["cut"]})
    response = await d["client"].post(d["base"] + f"/appointments/{row['id']}/extras", json=extras_payload(d, row))
    assert response.status_code == 200, response.text
    changed = response.json()
    assert changed["id"] == row["id"] and changed["protocol"] == row["protocol"]
    assert changed["total_cents"] == 5500
    original = next(item for item in changed["items"] if item["service_id"] == str(d["cut"]))
    extra = next(item for item in changed["items"] if item["service_id"] == str(d["beard"]))
    assert original["price_snapshot_cents"] == 3500 and original["duration_snapshot_min"] == 40
    assert original["item_origin"] == "booked" and extra["item_origin"] == "extra"
    assert extra["added_at"] and extra["added_by_account_id"] == str(d["owner"])
    assert datetime.fromisoformat(changed["end_at"]) - datetime.fromisoformat(row["end_at"]) == timedelta(minutes=20)
    async with d["migrator"].connect() as db:
        reminders = (await db.execute(text("""SELECT appointment_version FROM appointment_notifications
            WHERE tenant_id=:t AND appointment_id=:id AND event='reminder' AND status='pending'"""),
            {"t": d["a"], "id": UUID(row["id"])})).scalars().all()
        assert reminders == [datetime.fromisoformat(changed["updated_at"])]
    moved = await d["client"].post(d["base"] + f"/appointments/{row['id']}/reschedule", json={
        "start_at": start_at(d["day"], 14).isoformat(), "expected_updated_at": changed["updated_at"],
        "idempotency_key": str(uuid4())})
    assert moved.status_code == 200, moved.text
    moved = moved.json()
    assert moved["total_cents"] == 5500
    assert datetime.fromisoformat(moved["end_at"]) - datetime.fromisoformat(moved["start_at"]) == timedelta(minutes=60)
    assert next(item for item in moved["items"] if item["service_id"] == str(d["cut"]))["price_snapshot_cents"] == 3500


@pytest.mark.asyncio
async def test_extra_duration_conflict_requires_explicit_choice_and_never_moves_next_customer(operations_db):
    d = operations_db
    row = await create_normal(d)
    following = await create_normal(d, hour=11)
    request = extras_payload(d, row)
    preview = await d["client"].post(d["base"] + f"/appointments/{row['id']}/extras/preview", json=request)
    assert preview.status_code == 200, preview.text
    assert preview.json()["duration_conflict"] is True
    response = await d["client"].post(d["base"] + f"/appointments/{row['id']}/extras", json=request)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "extra_duration_conflict"
    saved = await d["client"].post(d["base"] + f"/appointments/{row['id']}/extras",
        json={**request, "preserve_reserved_time": True})
    assert saved.status_code == 200, saved.text
    assert saved.json()["end_at"] == row["end_at"]
    assert saved.json()["total_cents"] == 5500
    next_row = await d["client"].get(d["base"] + f"/appointments/{following['id']}")
    assert next_row.status_code == 200 and next_row.json()["start_at"] == following["start_at"]
    # A later add-on that takes no extra time must honor the earlier reservation choice.
    async with d["migrator"].begin() as db:
        await db.execute(text("""INSERT INTO service_addons
            (tenant_id,base_service_id,addon_service_id,add_duration)
            VALUES(:t,:base,:addon,false)"""),
            {"t": d["a"], "base": d["cut"], "addon": d["botox"]})
    no_time = await d["client"].post(d["base"] + f"/appointments/{row['id']}/extras",
        json=extras_payload(d, saved.json(), service_ids=[str(d["botox"])]))
    assert no_time.status_code == 200, no_time.text
    assert no_time.json()["end_at"] == row["end_at"]
    assert no_time.json()["total_cents"] == 13500
    assert no_time.json()["items"][-1]["added_duration_min"] == 0


@pytest.mark.asyncio
async def test_started_appointment_records_actual_duration_and_commission_without_moving_reserved_slot(operations_db):
    d = operations_db
    row = await create_manual(d)
    past = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=90)
    async with d["migrator"].begin() as db:
        await db.execute(text("""UPDATE appointments SET start_at=:lo,end_at=:hi,
            occupied_from=:before,occupied_until=:after WHERE tenant_id=:t AND id=:id"""),
            {"t": d["a"], "id": UUID(row["id"]), "lo": past, "hi": past + timedelta(minutes=40),
             "before": past - timedelta(minutes=5), "after": past + timedelta(minutes=50)})
    original = (await d["client"].get(d["base"] + f"/appointments/{row['id']}")).json()
    saved = await d["client"].post(d["base"] + f"/appointments/{row['id']}/extras",
        json=extras_payload(d, original, preserve_reserved_time=True, actual_duration_min=60))
    assert saved.status_code == 200, saved.text
    changed = saved.json()
    assert changed["end_at"] == original["end_at"] and changed["actual_duration_min"] == 60
    finish = {"expected_updated_at": changed["updated_at"], "idempotency_key": str(uuid4())}
    completed = await d["client"].post(d["base"] + f"/management/appointments/{row['id']}/complete", json=finish)
    assert completed.status_code == 200, completed.text
    replay = await d["client"].post(d["base"] + f"/management/appointments/{row['id']}/complete", json=finish)
    assert replay.status_code == 200, replay.text
    async with d["migrator"].connect() as db:
        commission = (await db.execute(text("SELECT commission_cents FROM appointments WHERE tenant_id=:t AND id=:id"),
            {"t": d["a"], "id": UUID(row["id"])})).scalar_one()
        assert commission == 1375
        finance = (await db.execute(text("SELECT amount_cents,status FROM financial_entries WHERE tenant_id=:t AND appointment_id=:id"),
            {"t": d["a"], "id": UUID(row["id"])})).mappings().one()
        assert finance["amount_cents"] == 5500 and finance["status"] == "pending"


@pytest.mark.asyncio
async def test_extras_idempotency_stale_versions_and_terminal_states(operations_db):
    d = operations_db
    row = await create_normal(d)
    request = extras_payload(d, row)
    url = d["base"] + f"/appointments/{row['id']}/extras"
    future_actual = await d["client"].post(url, json={**request, "actual_duration_min": 60})
    assert future_actual.status_code == 422, future_actual.text
    replies = await asyncio.gather(*(d["client"].post(url, json=request) for _ in range(3)))
    assert all(reply.status_code == 200 for reply in replies), [reply.text for reply in replies]
    assert all(len(reply.json()["items"]) == 2 and reply.json()["total_cents"] == 5500 for reply in replies)
    stale = await d["client"].post(url, json=extras_payload(d, row, service_ids=[str(d["botox"])]))
    assert stale.status_code == 409, stale.text
    completed = await create_manual(d, mode="retroactive", start_at=(datetime.now(timezone.utc).replace(second=0, microsecond=0)-timedelta(days=2)).isoformat())
    denied = await d["client"].post(d["base"] + f"/appointments/{completed['id']}/extras", json=extras_payload(d, completed))
    assert denied.status_code == 409, denied.text


@pytest.mark.asyncio
async def test_professional_and_tenant_boundaries_apply_to_manual_and_extras(operations_db):
    d = operations_db
    other = await create_normal(d, professional=d["pro2"])
    d["selected"]["actor"] = Actor("member", account_id=d["member"])
    request = manual_payload(d)
    request.pop("professional_id")
    response = await d["client"].post("/api/v1/professional/appointments/manual", json=request)
    assert response.status_code == 201, response.text
    own = response.json()
    forged = await d["client"].post("/api/v1/professional/appointments/manual",
                                   json={**request, "professional_id": str(d["pro2"])})
    assert forged.status_code == 422
    denied = await d["client"].post(f"/api/v1/professional/appointments/{other['id']}/extras",
                                  json=extras_payload(d, other))
    assert denied.status_code == 404, denied.text
    own_extra = await d["client"].post(f"/api/v1/professional/appointments/{own['id']}/extras",
                                     json=extras_payload(d, own))
    assert own_extra.status_code == 200, own_extra.text
    catalog = await d["client"].get(f"/api/v1/professional/appointments/{other['id']}/extras-catalog")
    assert catalog.status_code == 404
    company = await d["client"].post(d["base"] + "/appointments/manual", json=manual_payload(d))
    assert company.status_code == 403, company.text
    d["selected"]["actor"] = Actor("member", tenant_id=d["a"], account_id=d["owner"], membership_role="owner")
    cross_tenant = await d["client"].post(f"/api/v1/tenants/{d['b']}/appointments/manual", json=manual_payload(d))
    assert cross_tenant.status_code == 403, cross_tenant.text


@pytest.mark.asyncio
async def test_unavailable_and_cross_tenant_extra_services_are_rejected_without_mutation(operations_db):
    d = operations_db
    row = await create_normal(d)
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE services SET active=false WHERE id=:id"), {"id": d["beard"]})
    result = await d["client"].post(d["base"] + f"/appointments/{row['id']}/extras", json=extras_payload(d, row))
    assert result.status_code in (404, 409, 422), result.text
    current = await d["client"].get(d["base"] + f"/appointments/{row['id']}")
    assert current.status_code == 200 and current.json()["total_cents"] == row["total_cents"]
    assert len(current.json()["items"]) == 1
