"""FATIA 2: integração real PostgreSQL, inclusive 50 tentativas concorrentes."""
import asyncio
import os
from datetime import datetime, timedelta, time, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.booking_service import (
    book_appointment, cancel_appointment, confirm_hold, list_slots,
    reschedule_appointment, preview_reschedule,
)
from app.main import app

pytestmark = pytest.mark.integration
TZ = ZoneInfo("America/Campo_Grande")
UTC = timezone.utc


def async_url(v):
    return v.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)


def headers():
    return {"Authorization": "Bearer " + os.environ["SUPPORT_API_TOKEN"]}


def phone(n):
    return "+55679" + f"{n:08d}"


def start_at(day, hh, minute=0):
    return datetime.combine(day, time(hh, minute), tzinfo=TZ)


@pytest_asyncio.fixture
async def booking_db():
    from plan_fixtures import seed_fixture_plan, clear_fixture_plans
    plan_admin=None
    rurl, murl = os.getenv("APP_DATABASE_URL"), os.getenv("MIGRATION_DATABASE_URL")
    if not rurl or not murl:
        if os.getenv("REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("PostgreSQL real obrigatório")
        pytest.skip("PostgreSQL não configurado")
    runtime = create_async_engine(async_url(rurl), pool_size=5, max_overflow=50, pool_timeout=60)
    migrator = create_async_engine(async_url(murl))
    session_factory = async_sessionmaker(runtime, expire_on_commit=False)
    a, b, pro, pro2 = uuid4(), uuid4(), uuid4(), uuid4()
    cut, beard, botox, platinum = [uuid4() for _ in range(4)]
    day = (datetime.now(UTC).astimezone(TZ) + timedelta(days=7)).date()
    values = (
        (cut, "Corte", 40, 3500, 5, 10),
        (beard, "Barba", 20, 2000, 0, 0),
        (botox, "Botox", 90, 8000, 0, 0),
        (platinum, "Platinado", 120, 15000, 0, 0),
    )
    try:
        async with migrator.begin() as s:
            for tid in (a, b):
                await s.execute(text("""
                    INSERT INTO tenants (id,name,slug) VALUES (:id,:name,:slug)
                """), {"id": tid, "name": "Piloto fictício", "slug": "booking-" + str(tid)})
            plan_admin=await seed_fixture_plan(s,(a,b),"essencial")
            for pid in (pro, pro2):
                await s.execute(text("""
                    INSERT INTO professionals (id,tenant_id,name) VALUES (:id,:tenant,'Barbeiro sintético')
                """), {"id": pid, "tenant": a})
                await s.execute(text("""
                    INSERT INTO working_hours (tenant_id,professional_id,weekday,start_time,end_time)
                    VALUES (:tenant,:id,:weekday,'08:00','18:00')
                """), {"tenant": a, "id": pid, "weekday": day.weekday()})
            for sid, name, duration, price, prep, cleanup in values:
                await s.execute(text("""
                    INSERT INTO services (id,tenant_id,name,duration_min,price_cents,prep_min,cleanup_min)
                    VALUES (:id,:tenant,:name,:duration,:price,:prep,:cleanup)
                """), {"id": sid, "tenant": a, "name": name, "duration": duration,
                       "price": price, "prep": prep, "cleanup": cleanup})
                for pid in (pro, pro2):
                    await s.execute(text("""
                        INSERT INTO professional_services (tenant_id,professional_id,service_id)
                        VALUES (:tenant,:professional,:service)
                    """), {"tenant": a, "professional": pid, "service": sid})
        yield {
            "runtime": runtime, "migrator": migrator, "factory": session_factory,
            "a": a, "b": b, "pro": pro, "pro2": pro2, "cut": cut,
            "beard": beard, "botox": botox, "platinum": platinum, "day": day,
        }
    finally:
        async with migrator.begin() as s:
            for table in ("message_outbox", "inbound_events", "authorized_staff", "whatsapp_channels", "usage_counters", "calendar_outbox", "appointment_history", "appointment_items", "appointments",
                          "customers", "working_breaks", "professional_services",
                          "working_hours", "schedule_blocks", "time_off", "services",
                          "professionals", "tenant_audit"):
                await s.execute(text(f"DELETE FROM {table} WHERE tenant_id=:tenant"), {"tenant": a})
            await clear_fixture_plans(s,(a,b),(plan_admin,))
            await s.execute(text("DELETE FROM tenants WHERE id IN (:a,:b)"), {"a": a, "b": b})
        await runtime.dispose()
        await migrator.dispose()


@pytest.mark.asyncio
async def test_combined_services_buffers_shift_pause_and_blocks(booking_db):
    d = booking_db
    async with d["migrator"].begin() as s:
        shift = (await s.execute(text("""
            SELECT id FROM working_hours WHERE tenant_id=:tenant AND professional_id=:pro
        """), {"tenant": d["a"], "pro": d["pro"]})).scalar_one()
        await s.execute(text("""
            INSERT INTO working_breaks (tenant_id,working_hour_id,start_time,end_time)
            VALUES (:tenant,:id,'12:00','13:00')
        """), {"tenant": d["a"], "id": shift})
        for table, hh in (("schedule_blocks", 15), ("time_off", 16)):
            await s.execute(text(f"""
                INSERT INTO {table} (tenant_id, professional_id, starts_at, ends_at)
                VALUES (:tenant,:pro,:start,:end)
            """), {"tenant": d["a"], "pro": d["pro"],
                   "start": start_at(d["day"], hh).astimezone(UTC),
                   "end": start_at(d["day"], hh+1).astimezone(UTC)})
    async with d["factory"]() as s:
        slots = await list_slots(s, d["a"], d["pro"], [d["cut"], d["beard"]], d["day"])
    assert slots["duration_min"] == 60
    assert slots["price_cents"] == 5500
    assert slots["prep_min"] == 5
    assert slots["cleanup_min"] == 10
    available = slots["slots"]
    assert start_at(d["day"], 8).isoformat() in available  # início do expediente sem cliente anterior
    assert start_at(d["day"], 8, 15).isoformat() in available
    assert start_at(d["day"], 11, 30).isoformat() not in available
    assert start_at(d["day"], 14, 30).isoformat() not in available
    assert start_at(d["day"], 16, 30).isoformat() not in available
    assert start_at(d["day"], 17).isoformat() not in available
    # Após o intervalo, o primeiro atendimento pode começar às 13h.
    assert start_at(d["day"], 13).isoformat() in available
    assert start_at(d["day"], 13, 15).isoformat() in available
    async with d["factory"]() as s:
        long = await list_slots(s, d["a"], d["pro2"], [d["platinum"]], d["day"])
    assert start_at(d["day"], 17).isoformat() not in long["slots"]


@pytest.mark.asyncio
async def test_hold_confirmation_cancel_and_idempotency_http(booking_db):
    d = booking_db
    url = f"/api/v1/tenants/{d['a']}/appointments"
    payload = {"professional_id": str(d["pro"]), "service_ids": [str(d["cut"]), str(d["beard"])],
               "customer_name": "Cliente sintético", "customer_phone": phone(1),
               "start_at": start_at(d["day"], 10).isoformat(),
               "idempotency_key": "hold-lifecycle-001"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(url + "/hold", json=payload, headers=headers())
        assert r.status_code == 201, r.text
        hold = r.json()
        assert hold["status"] == "hold"
        assert hold["protocol"].startswith("AG-") and len(hold["protocol"]) == 9
        assert hold["total_cents"] == 5500
        assert len(hold["items"]) == 2
        replay = await client.post(url + "/hold", json=payload, headers=headers())
        assert replay.status_code == 201 and replay.json()["id"] == hold["id"]
        altered = await client.post(url + "/hold", json={**payload, "start_at": start_at(d["day"], 11).isoformat()}, headers=headers())
        assert altered.status_code == 409
        confirmed = await client.post(url + "/" + hold["id"] + "/confirm", headers=headers())
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["status"] == "confirmed"
        again = await client.post(url + "/" + hold["id"] + "/confirm", headers=headers())
        assert again.status_code == 200 and again.json()["id"] == hold["id"]
        conflict = await client.post(url + "/hold", headers=headers(), json={
            **payload, "customer_phone": phone(2), "idempotency_key": "other-person-002"
        })
        assert conflict.status_code == 409
        listed = await client.get(url + "?day=" + d["day"].isoformat(), headers=headers())
        assert listed.status_code == 200 and len(listed.json()) == 1
        assert (await client.get(f"/api/v1/tenants/{d['b']}/appointments/{hold['id']}", headers=headers())).status_code == 404
        cancelled = await client.post(url + "/" + hold["id"] + "/cancel", headers=headers())
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
        cancel_again = await client.post(url + "/" + hold["id"] + "/cancel", headers=headers())
        assert cancel_again.status_code == 200
        new_hold = await client.post(url + "/hold", headers=headers(), json={
            **payload, "customer_phone": phone(3), "idempotency_key": "new-person-003"
        })
        assert new_hold.status_code == 201


@pytest.mark.asyncio
async def test_expired_hold_releases_slot_and_denies_confirmation(booking_db):
    d = booking_db
    now = datetime.now(UTC)
    async with d["factory"]() as s:
        old = await book_appointment(s, d["a"], d["pro"], [d["beard"]],
                                     phone(10), "Pessoa dez", start_at(d["day"], 10),
                                     "expiring-0001", now=now)
    async with d["factory"]() as s:
        with pytest.raises(HTTPException) as exc:
            await confirm_hold(s, d["a"], old["id"], now=now+timedelta(minutes=6))
    assert exc.value.status_code == 409
    async with d["factory"]() as s:
        new = await book_appointment(s, d["a"], d["pro"], [d["beard"]],
                                     phone(11), "Pessoa onze", start_at(d["day"], 10),
                                     "after-expire-0002", now=now+timedelta(minutes=6))
    assert new["id"] != old["id"]
    async with d["migrator"].begin() as s:
        status = (await s.execute(text("SELECT status FROM appointments WHERE id=:id"),
                                  {"id": old["id"]})).scalar_one()
        assert status == "cancelled"


@pytest.mark.asyncio
async def test_reschedule_is_atomic_preserves_protocol_and_updates_snapshot(booking_db):
    d = booking_db
    async with d["factory"]() as s:
        one = await book_appointment(s, d["a"], d["pro"], [d["cut"]],
                                     phone(20), "Pessoa vinte", start_at(d["day"], 10),
                                     "resched-001")
    async with d["factory"]() as s:
        confirmed = await confirm_hold(s, d["a"], one["id"])
    async with d["factory"]() as s:
        other = await book_appointment(s, d["a"], d["pro"], [d["beard"]],
                                       phone(21), "Pessoa vinte e um", start_at(d["day"], 11),
                                       "resched-002")
    async with d["factory"]() as s:
        await confirm_hold(s, d["a"], other["id"])
    async with d["factory"]() as s:
        with pytest.raises(HTTPException) as exc:
            await reschedule_appointment(s, d["a"], one["id"], start_at(d["day"], 11))
    assert exc.value.status_code == 409
    async with d["migrator"].begin() as s:
        start = (await s.execute(text("SELECT start_at FROM appointments WHERE id=:id"), {"id": one["id"]})).scalar_one()
        assert start == start_at(d["day"], 10).astimezone(UTC)
        await s.execute(text("UPDATE services SET price_cents=4500 WHERE id=:id"), {"id": d["cut"]})
    async with d["factory"]() as s:
        changed = await reschedule_appointment(s, d["a"], one["id"], start_at(d["day"], 14))
    assert changed["protocol"] == confirmed["protocol"]
    assert changed["total_cents"] == 4500
    assert changed["items"][0]["price_snapshot_cents"] == 4500
    assert changed["start_at"] == start_at(d["day"], 14).astimezone(UTC)


@pytest.mark.asyncio
async def test_fifty_simultaneous_requests_one_hold(booking_db):
    d = booking_db
    requested = start_at(d["day"], 10)
    async def one(index):
        try:
            async with d["factory"]() as s:
                result = await book_appointment(s, d["a"], d["pro"], [d["beard"]],
                                                phone(100+index), "Cliente teste",
                                                requested, f"concurrent-{index:04d}")
            return result["id"]
        except HTTPException as exc:
            assert exc.status_code == 409
            return None
    results = await asyncio.gather(*(one(i) for i in range(50)))
    assert sum(x is not None for x in results) == 1, results
    winner = next(x for x in results if x is not None)
    async with d["factory"]() as s:
        confirmation = await confirm_hold(s, d["a"], winner)
    assert confirmation["status"] == "confirmed"
    async with d["migrator"].begin() as s:
        count = (await s.execute(text("""
            SELECT count(*) FROM appointments WHERE tenant_id=:t
              AND professional_id=:p AND status='confirmed'
        """), {"t": d["a"], "p": d["pro"]})).scalar_one()
        assert count == 1


@pytest.mark.asyncio
async def test_other_professional_and_other_tenant_remain_isolated(booking_db):
    d = booking_db
    at = start_at(d["day"], 9)
    async with d["factory"]() as s:
        first = await book_appointment(s, d["a"], d["pro"], [d["beard"]],
                                       phone(220), "Pessoa", at, "proone-0001")
    async with d["factory"]() as s:
        second = await book_appointment(s, d["a"], d["pro2"], [d["beard"]],
                                        phone(221), "Pessoa", at, "protwo-0002")
    assert first["id"] != second["id"]
    async with d["runtime"].begin() as s:
        assert (await s.execute(text("SELECT id FROM appointments"))).all() == []
        assert (await s.execute(text("SELECT id FROM customers"))).all() == []
    from app.database import tenant_transaction
    async with d["factory"]() as s:
        async with tenant_transaction(s, d["b"]):
            assert (await s.execute(text("SELECT id FROM appointments"))).all() == []
            assert (await s.execute(text("SELECT id FROM customers"))).all() == []
    async with d["factory"]() as s:
        with pytest.raises(DBAPIError):
            async with tenant_transaction(s, d["b"]):
                await s.execute(text("""
                    INSERT INTO customers (tenant_id,name,whatsapp_e164)
                    VALUES (:tenant,'Negado',:phone)
                """), {"tenant": d["a"], "phone": phone(222)})


@pytest.mark.asyncio
async def test_postgres_exclusion_rejects_overlap_even_without_booking_service(booking_db):
    """Segunda escrita SQL direta também é vetada pela constraint btree_gist."""
    d = booking_db
    at = start_at(d["day"], 10)
    async with d["factory"]() as s:
        booked = await book_appointment(s, d["a"], d["pro"], [d["beard"]],
                                        phone(320), "Primeira pessoa", at, "constraint-0001")
    async with d["migrator"].begin() as s:
        data = (await s.execute(text("""
            SELECT customer_id,start_at,end_at,occupied_from,occupied_until
            FROM appointments WHERE id=:id
        """), {"id": booked["id"]})).mappings().one()
    from app.database import tenant_transaction
    async with d["factory"]() as s:
        with pytest.raises(DBAPIError):
            async with tenant_transaction(s, d["a"]):
                await s.execute(text("""
                    INSERT INTO appointments (
                        tenant_id,customer_id,professional_id,protocol,idempotency_key,
                        request_fingerprint,start_at,end_at,occupied_from,occupied_until,
                        status,hold_expires_at,total_cents
                    ) VALUES (
                        :tenant,:customer,:pro,:protocol,:idem,:fp,:start,:end,:begin,:stop,
                        'hold',:expiry,2000
                    )
                """), {"tenant": d["a"], "customer": data["customer_id"],
                       "pro": d["pro"], "protocol": "AG-XYZABC",
                       "idem": "constraint-direct-write-0002", "fp": "x"*64,
                       "start": data["start_at"], "end": data["end_at"],
                       "begin": data["occupied_from"], "stop": data["occupied_until"],
                       "expiry": datetime.now(UTC)+timedelta(minutes=5)})



class MemoryRedis:
    def __init__(self):
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.data[key] = value

    async def delete(self, key):
        self.data.pop(key, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["price", "duration", "original"])
async def test_reschedule_requires_same_snapshot_under_write_lock(booking_db, change):
    d = booking_db
    sender = phone(730)
    async with d["factory"]() as s:
        reservation = await book_appointment(s, d["a"], d["pro"], [d["beard"]], sender,
            "Synthetic snapshot", start_at(d["day"], 10), "snapshot-booking")
        await confirm_hold(s, d["a"], reservation["id"])
        proposal = await preview_reschedule(s, d["a"], reservation["id"], start_at(d["day"], 14), customer_phone=sender)
    async with d["migrator"].begin() as s:
        if change == "price":
            await s.execute(text("UPDATE services SET price_cents=price_cents+100 WHERE tenant_id=:t AND id=:id"), {"t": d["a"], "id": d["beard"]})
        elif change == "duration":
            await s.execute(text("UPDATE services SET duration_min=duration_min+5 WHERE tenant_id=:t AND id=:id"), {"t": d["a"], "id": d["beard"]})
        else:
            await s.execute(text("UPDATE appointments SET updated_at=updated_at+interval '1 second' WHERE tenant_id=:t AND id=:id"), {"t": d["a"], "id": reservation["id"]})
    async with d["factory"]() as s:
        with pytest.raises(HTTPException) as stale:
            await reschedule_appointment(s, d["a"], reservation["id"], start_at(d["day"], 14),
                expected_snapshot=proposal["expected_snapshot"], customer_phone=sender)
        assert stale.value.status_code == 409
    async with d["migrator"].connect() as s:
        original = (await s.execute(text("SELECT start_at,total_cents FROM appointments WHERE id=:id"), {"id": reservation["id"]})).one()
        assert original.start_at == start_at(d["day"], 10) and original.total_cents == 2000


@pytest.mark.asyncio
async def test_reschedule_preview_is_contact_bound_and_verified_snapshot_moves_once(booking_db):
    d = booking_db
    sender = phone(731)
    async with d["factory"]() as s:
        reservation = await book_appointment(s, d["a"], d["pro"], [d["beard"]], sender,
            "Synthetic snapshot", start_at(d["day"], 10), "snapshot-success")
        await confirm_hold(s, d["a"], reservation["id"])
        with pytest.raises(HTTPException) as denied:
            await preview_reschedule(s, d["a"], reservation["id"], start_at(d["day"], 14), customer_phone=phone(732))
        assert denied.value.status_code == 404
        proposal = await preview_reschedule(s, d["a"], reservation["id"], start_at(d["day"], 14), customer_phone=sender)
        moved = await reschedule_appointment(s, d["a"], reservation["id"], start_at(d["day"], 14),
            expected_snapshot=proposal["expected_snapshot"], customer_phone=sender)
        assert moved["start_at"] == start_at(d["day"], 14) and moved["protocol"] == reservation["protocol"]


@pytest.mark.asyncio
async def test_customer_confirmation_and_cancel_verify_contact_and_unchanged_summary(booking_db):
    from app.whatsapp_dialogue import owned_booking
    d=booking_db
    sender=phone(740)
    async with d["factory"]() as s:
        reservation=await book_appointment(s,d["a"],d["pro"],[d["beard"]],sender,
            "Synthetic consent",start_at(d["day"],10),"customer-confirm-snapshot")
    hold=await owned_booking(d["a"],sender,item_id=reservation["id"],statuses=("hold",))
    assert hold and hold["expected_snapshot"]
    assert await owned_booking(d["b"],sender,item_id=reservation["id"],statuses=("hold",)) is None
    async with d["factory"]() as s:
        with pytest.raises(HTTPException) as denied:
            await confirm_hold(s,d["a"],reservation["id"],customer_phone=phone(741),expected_snapshot=hold["expected_snapshot"])
        assert denied.value.status_code==404
        await confirm_hold(s,d["a"],reservation["id"],customer_phone=sender,expected_snapshot=hold["expected_snapshot"])
    current=await owned_booking(d["a"],sender,item_id=reservation["id"])
    async with d["factory"]() as s:
        await reschedule_appointment(s,d["a"],reservation["id"],start_at(d["day"],14))
        with pytest.raises(HTTPException) as stale:
            await cancel_appointment(s,d["a"],reservation["id"],customer_phone=sender,expected_snapshot=current["expected_snapshot"])
        assert stale.value.status_code==409
    async with d["migrator"].connect() as s:
        persisted=(await s.execute(text("SELECT status,start_at FROM appointments WHERE id=:id"),{"id":reservation["id"]})).one()
        assert persisted.status=="confirmed" and persisted.start_at==start_at(d["day"],14)


@pytest.mark.asyncio
async def test_catalog_bundle_preserves_existing_privileges_and_input_order(booking_db):
    from app.booking_service import load_bundle
    from app.database import tenant_transaction

    d = booking_db
    async with d["factory"]() as s:
        async with tenant_transaction(s, d["a"]):
            for table in ("tenants", "professionals", "services", "professional_services"):
                grants = (await s.execute(text("""SELECT
                    has_table_privilege(current_user, :table, 'SELECT') AS can_read,
                    has_any_column_privilege(current_user, :table, 'UPDATE') AS can_update
                """), {"table": table})).one()
                assert grants.can_read
                # Existing migrations give catalog DML to runtime, but tenants
                # remains SELECT-only. This fix must not widen that privilege.
                assert grants.can_update == (table != "tenants")
            shop, items = await load_bundle(s, d["a"], d["pro"], [d["beard"], d["cut"]])
            assert shop["timezone"] == "America/Campo_Grande"
            assert [item["id"] for item in items] == [d["beard"], d["cut"]]
            assert [(item["price_cents"], item["duration_min"], item["prep_min"], item["cleanup_min"])
                    for item in items] == [(2000, 20, 0, 0), (3500, 40, 5, 10)]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["confirm", "reschedule"])
@pytest.mark.parametrize("change", ["price", "duration", "buffer", "name", "service_active",
                                  "professional_active", "professional_name", "assignment", "shop", "contact"])
async def test_atomic_booking_write_rechecks_changes_after_slot_validation(booking_db, monkeypatch, operation, change):
    """A real second connection commits in the gap before the final UPDATE."""
    from app import booking_service
    from app.whatsapp_dialogue import owned_booking

    d = booking_db
    sender = phone(750)
    async with d["factory"]() as s:
        reservation = await book_appointment(s, d["a"], d["pro"], [d["beard"]], sender,
            "Synthetic atomic contract", start_at(d["day"], 10), "atomic-contract")
        if operation == "reschedule":
            await confirm_hold(s, d["a"], reservation["id"])
            proposal = await preview_reschedule(s, d["a"], reservation["id"], start_at(d["day"], 14), customer_phone=sender)
        else:
            proposal = await owned_booking(d["a"], sender, item_id=reservation["id"], statuses=("hold",))

    async def persisted_state():
        async with d["migrator"].connect() as connection:
            appointment = (await connection.execute(text("SELECT row_to_json(a) FROM appointments a WHERE id=:id"),
                {"id": reservation["id"]})).scalar_one()
            events = (await connection.execute(text("SELECT count(*) FROM appointment_history WHERE appointment_id=:id"),
                {"id": reservation["id"]})).scalar_one()
            outbox = (await connection.execute(text("SELECT row_to_json(o) FROM calendar_outbox o WHERE appointment_id=:id"),
                {"id": reservation["id"]})).scalar_one_or_none()
            items = (await connection.execute(text("SELECT row_to_json(i) FROM appointment_items i WHERE appointment_id=:id ORDER BY item_order"),
                {"id": reservation["id"]})).scalars().all()
            return appointment, events, outbox, items

    before = await persisted_state()
    original_check = booking_service.check_slot
    changed = False

    async def check_then_change(*args, **kwargs):
        nonlocal changed
        result = await original_check(*args, **kwargs)
        assert not changed
        changed = True
        statements = {
            "price": "UPDATE services SET price_cents=price_cents+100 WHERE tenant_id=:tenant AND id=:service",
            "duration": "UPDATE services SET duration_min=duration_min+5 WHERE tenant_id=:tenant AND id=:service",
            "buffer": "UPDATE services SET cleanup_min=cleanup_min+5 WHERE tenant_id=:tenant AND id=:service",
            "name": "UPDATE services SET name='Nome revisado' WHERE tenant_id=:tenant AND id=:service",
            "service_active": "UPDATE services SET active=false WHERE tenant_id=:tenant AND id=:service",
            "professional_active": "UPDATE professionals SET active=false WHERE tenant_id=:tenant AND id=:professional",
            "professional_name": "UPDATE professionals SET name='Nome revisado' WHERE tenant_id=:tenant AND id=:professional",
            "assignment": "DELETE FROM professional_services WHERE tenant_id=:tenant AND professional_id=:professional AND service_id=:service",
            "shop": "UPDATE tenants SET min_notice_minutes=min_notice_minutes+5 WHERE id=:tenant",
            "contact": "UPDATE customers SET whatsapp_e164=:other WHERE tenant_id=:tenant AND whatsapp_e164=:sender",
        }
        async with d["migrator"].begin() as connection:
            await connection.execute(text(statements[change]), {"tenant": d["a"], "service": d["beard"],
                "professional": d["pro"], "sender": sender, "other": phone(751)})
        return result

    monkeypatch.setattr(booking_service, "check_slot", check_then_change)
    async with d["factory"]() as s:
        with pytest.raises(HTTPException) as conflict:
            if operation == "confirm":
                await confirm_hold(s, d["a"], reservation["id"], customer_phone=sender,
                    expected_snapshot=proposal["expected_snapshot"])
            else:
                await reschedule_appointment(s, d["a"], reservation["id"], start_at(d["day"], 14),
                    customer_phone=sender, expected_snapshot=proposal["expected_snapshot"])
        assert conflict.value.status_code == 409
    assert changed
    assert await persisted_state() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["cancel", "expire"])
async def test_atomic_cancel_rechecks_contact_after_snapshot_validation(booking_db, monkeypatch, operation):
    from app import booking_service
    from app.whatsapp_dialogue import owned_booking

    d = booking_db
    sender = phone(760)
    async with d["factory"]() as s:
        reservation = await book_appointment(s, d["a"], d["pro"], [d["beard"]], sender,
            "Synthetic cancel contract", start_at(d["day"], 10), "atomic-cancel")
        if operation == "cancel":
            await confirm_hold(s, d["a"], reservation["id"])
    status = "confirmed" if operation == "cancel" else "hold"
    proposal = await owned_booking(d["a"], sender, item_id=reservation["id"], statuses=(status,))
    original_verify = booking_service.verify_customer_snapshot

    async def verify_then_change(*args, **kwargs):
        await original_verify(*args, **kwargs)
        async with d["migrator"].begin() as connection:
            await connection.execute(text("UPDATE customers SET whatsapp_e164=:other WHERE tenant_id=:tenant AND whatsapp_e164=:sender"),
                {"tenant": d["a"], "sender": sender, "other": phone(761)})

    monkeypatch.setattr(booking_service, "verify_customer_snapshot", verify_then_change)
    async with d["factory"]() as s:
        with pytest.raises(HTTPException) as conflict:
            if operation == "cancel":
                await cancel_appointment(s, d["a"], reservation["id"], customer_phone=sender,
                    expected_snapshot=proposal["expected_snapshot"])
            else:
                await confirm_hold(s, d["a"], reservation["id"], customer_phone=sender,
                    expected_snapshot=proposal["expected_snapshot"], now=datetime.now(UTC)+timedelta(minutes=6))
        assert conflict.value.status_code == 409
    async with d["migrator"].connect() as connection:
        assert (await connection.execute(text("SELECT status FROM appointments WHERE id=:id"), {"id": reservation["id"]})).scalar_one() == status
        assert (await connection.execute(text("SELECT count(*) FROM appointment_history WHERE appointment_id=:id AND event IN ('cancelled','hold_expired')"), {"id": reservation["id"]})).scalar_one() == 0
        assert (await connection.execute(text("SELECT action FROM calendar_outbox WHERE appointment_id=:id"), {"id": reservation["id"]})).scalar_one_or_none() == ("create" if operation == "cancel" else None)


@pytest.mark.asyncio
async def test_signed_phone_scopes_customer_cancel_and_reschedule(booking_db):
    """Signed ownership and a fresh reply to an accepted summary are both required.

    Real PostgreSQL domain/receipt rows, in-memory Redis and synthetic provider
    acceptance only. No Meta request, webhook queue or real message is sent.
    """
    import json
    from app.whatsapp_commands import try_command
    from app.whatsapp_bot import reply
    from app.communication_events import summary_was_accepted

    d=booking_db
    redis=MemoryRedis()
    channel=uuid4()
    sender=phone(432)
    other=phone(433)
    async with d["migrator"].begin() as s:
        # This scenario exercises the WhatsApp assistant as well as booking.
        await s.execute(text("UPDATE tenant_plan_controls SET manual_plan_code='elite',version=version+1 WHERE tenant_id=:tenant"),
                        {"tenant":d["a"]})
        await s.execute(text("""INSERT INTO whatsapp_channels
            (id,tenant_id,phone_number_id,business_number,status)
            VALUES(:id,:tenant,:number,'+5567999999900','testing')"""),
            {"id":channel,"tenant":d["a"],"number":"synthetic-"+channel.hex})
    async with d["factory"]() as s:
        reservation=await book_appointment(s,d["a"],d["pro"],[d["beard"]],
                                           sender,"Cliente sintético",start_at(d["day"],10),
                                           "wa-owner-001")
    async with d["factory"]() as s:
        await confirm_hold(s,d["a"],reservation["id"])
    protocol=reservation["protocol"]
    key=f"wa:state:{d['a']}:{channel}:{sender}"

    async def appointment_state():
        async with d["migrator"].connect() as s:
            return (await s.execute(text("SELECT start_at,status FROM appointments WHERE id=:id"),
                {"id":reservation["id"]})).mappings().one()

    async def accept_current_summary():
        # Match the worker/outbox boundary without invoking any transport. The
        # actual reply wrapper above supplies UUID identity and exact last_answer.
        saved=json.loads(redis.data[key])
        event=UUID(saved["summary_message_id"])
        assert saved["last_message_id"]==str(event) and saved["last_answer"]
        async with d["migrator"].begin() as s:
            await s.execute(text("""INSERT INTO inbound_events
                (id,tenant_id,channel_id,external_id,sender_e164,message_text,status,processed_at)
                VALUES(:id,:tenant,:channel,:external,:phone,'','processed',now())"""),
                {"id":event,"tenant":d["a"],"channel":channel,"external":"synthetic-"+event.hex,"phone":sender})
            await s.execute(text("""INSERT INTO message_outbox
                (tenant_id,channel_id,event_id,to_e164,body,status)
                VALUES(:tenant,:channel,:event,:phone,:body,'sent')"""),
                {"tenant":d["a"],"channel":channel,"event":event,"phone":sender,"body":saved["last_answer"]})
        assert await summary_was_accepted(d["a"],channel,sender,saved)
        assert not await summary_was_accepted(d["a"],channel,other,saved)
        assert not await summary_was_accepted(d["a"],uuid4(),sender,saved)
        assert not await summary_was_accepted(d["b"],channel,sender,saved)
        return str(event),saved["last_answer"]

    ignored,warning=await try_command(d["a"],channel,other,
          "cancelar "+protocol.lower(),{},redis,"other-key")
    assert ignored and "não encontrada" in warning.lower()

    prompt=await reply(d["a"],channel,sender,"remarcar "+protocol.lower(),redis,message_id=str(uuid4()))
    assert "nova data" in prompt.lower()
    proposal=await reply(d["a"],channel,sender,start_at(d["day"],14).strftime("%Y-%m-%d %H:%M"),
                         redis,message_id=str(uuid4()))
    assert "Proposta de remarcação" in proposal
    assert (await appointment_state())["start_at"]==start_at(d["day"],10)
    # Seeing an answer in Redis alone does not establish transport acceptance.
    reviewed=await reply(d["a"],channel,sender,"pode confirmar",redis,message_id=str(uuid4()))
    assert "Proposta de remarcação" in reviewed
    assert (await appointment_state())["start_at"]==start_at(d["day"],10)
    accepted_id,accepted_body=await accept_current_summary()
    saved=json.loads(redis.data[key])
    ignored,warning=await try_command(d["a"],channel,sender,"pode confirmar",saved,redis,key,message_id=accepted_id)
    assert ignored and "nova resposta" in warning
    ignored,warning=await try_command(d["a"],channel,other,"pode confirmar",saved,redis,"other-key",message_id=str(uuid4()))
    assert ignored and "não está disponível" in warning
    assert await reply(d["a"],channel,sender,"pode confirmar",redis,message_id=accepted_id)==accepted_body
    assert (await appointment_state())["start_at"]==start_at(d["day"],10)
    moved=await reply(d["a"],channel,sender,"pode confirmar",redis,message_id=str(uuid4()))
    assert "Remarcação confirmada" in moved
    assert protocol in moved
    assert (await appointment_state())["start_at"]==start_at(d["day"],14)
    proposal=await reply(d["a"],channel,sender,"cancelar "+protocol.lower(),redis,message_id=str(uuid4()))
    assert "Agendamento selecionado para cancelamento" in proposal
    reviewed=await reply(d["a"],channel,sender,"pode cancelar",redis,message_id=str(uuid4()))
    assert "Agendamento selecionado para cancelamento" in reviewed and (await appointment_state())["status"]=="confirmed"
    accepted_id,accepted_body=await accept_current_summary()
    ignored,warning=await try_command(d["a"],channel,sender,"pode cancelar",json.loads(redis.data[key]),redis,key,message_id=accepted_id)
    assert ignored and "nova resposta" in warning
    assert await reply(d["a"],channel,sender,"pode cancelar",redis,message_id=accepted_id)==accepted_body
    assert (await appointment_state())["status"]=="confirmed"
    cancelled=await reply(d["a"],channel,sender,"pode cancelar",redis,message_id=str(uuid4()))
    assert "Cancelamento confirmado" in cancelled and protocol in cancelled
    assert (await appointment_state())["status"]=="cancelled"


@pytest.mark.asyncio
async def test_staff_commands_are_phone_bound_and_block_availability(booking_db):
    """Barber só bloqueia a agenda vinculada, pausa do bot só pode por owner."""
    import json
    from app.whatsapp_commands import try_command
    from app.whatsapp_bot import reply

    d=booking_db
    redis=MemoryRedis()
    channel=uuid4()
    owner=phone(700)
    barber=phone(701)
    stranger=phone(702)
    async with d["migrator"].begin() as s:
        await s.execute(text("""
            INSERT INTO whatsapp_channels
            (id,tenant_id,phone_number_id,business_number,status,bot_enabled)
            VALUES (:c,:tenant,:phone,'+5567999999999','testing',true)
        """),{"c":channel,"tenant":d["a"],"phone":"staff-"+str(channel)})
        await s.execute(text("""
            INSERT INTO authorized_staff(tenant_id,phone_e164,role,professional_id)
            VALUES (:t,:owner,'owner',:pro),(:t,:barber,'barber',:pro)
        """),{"t":d["a"],"owner":owner,"barber":barber,"pro":d["pro"]})
    start=start_at(d["day"],14)
    daystr=d["day"].isoformat()
    key=f"wa:state:{d['a']}:{channel}:{barber}"
    handled,response=await try_command(d["a"],channel,stranger,"agenda hoje",{},redis,"stranger")
    assert handled and "restrito" in response.lower()
    handled,response=await try_command(d["a"],channel,barber,"pausar bot",{},redis,key)
    assert handled and "reservado" in response.lower()
    handled,response=await try_command(d["a"],channel,barber,
                                      f"bloquear {daystr} 14:00-15:00",{},redis,key)
    assert handled and "confirmar" in response.lower()
    pending=json.loads(redis.data[key])
    handled,response=await try_command(d["a"],channel,barber,
                                      "confirmar bloqueio",pending,redis,key)
    assert handled and "bloqueado" in response.lower()
    async with d["factory"]() as s:
        avail=await list_slots(s,d["a"],d["pro"],[d["beard"]],d["day"])
    assert start.isoformat() not in avail["slots"]
    async with d["factory"]() as s:
        other_avail=await list_slots(s,d["a"],d["pro2"],[d["beard"]],d["day"])
    assert start.isoformat() in other_avail["slots"]
    ownerkey=f"wa:state:{d['a']}:{channel}:{owner}"
    handled,response=await try_command(d["a"],channel,owner,"pausar bot",{},redis,ownerkey)
    assert handled and "CONFIRMAR PAUSA" in response
    pending=json.loads(redis.data[ownerkey])
    handled,response=await try_command(d["a"],channel,owner,
                                      "confirmar pausa",pending,redis,ownerkey)
    assert handled and "pausado" in response
    async with d["migrator"].connect() as s:
        active=(await s.execute(text(
            "SELECT bot_enabled FROM whatsapp_channels WHERE id=:c"
        ),{"c":channel})).scalar_one()
    assert active is False

@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['cancel', 'reschedule'])
async def test_customer_one_hour_rule_is_enforced_in_engine(booking_db, operation):
    from app.appointment_lifecycle import customer_actor
    d = booking_db
    async with d['factory']() as s:
        held = await book_appointment(s, d['a'], d['pro'], [d['cut']], phone(919),
            'Cliente sintético', start_at(d['day'], 10), 'deadline-' + operation)
        booking = await confirm_hold(s, d['a'], held['id'])
    async with d['migrator'].begin() as s:
        await s.execute(text("""UPDATE appointments SET start_at=now()+interval '59 minutes',
            end_at=now()+interval '99 minutes',occupied_from=now()+interval '54 minutes',
            occupied_until=now()+interval '109 minutes' WHERE tenant_id=:t AND id=:id"""),
            {'t': d['a'], 'id': booking['id']})
    async with d['factory']() as s:
        with pytest.raises(HTTPException) as exc:
            if operation == 'cancel':
                await cancel_appointment(s, d['a'], booking['id'],
                    context=customer_actor(booking['customer_id'], 'public_site'))
            else:
                await reschedule_appointment(s, d['a'], booking['id'], start_at(d['day'], 14),
                    context=customer_actor(booking['customer_id'], 'public_site'))
        assert exc.value.status_code == 409
    async with d['migrator'].begin() as s:
        assert (await s.execute(text('SELECT status FROM appointments WHERE id=:id'),
            {'id': booking['id']})).scalar_one() == 'confirmed'


@pytest.mark.asyncio
async def test_first_opening_and_post_break_slot_are_persisted_without_otp_or_fake_availability(booking_db):
    d = booking_db
    clock = datetime.now(UTC)
    async with d["migrator"].begin() as s:
        await s.execute(text("""UPDATE working_hours SET start_time='09:00',end_time='19:00'
            WHERE tenant_id=:tenant AND professional_id=:pro"""), {"tenant":d["a"],"pro":d["pro"]})
        await s.execute(text("""UPDATE services SET prep_min=15,cleanup_min=0
            WHERE tenant_id=:tenant AND id=:service"""), {"tenant":d["a"],"service":d["cut"]})
        shift=(await s.execute(text("""SELECT id FROM working_hours
            WHERE tenant_id=:tenant AND professional_id=:pro"""),
            {"tenant":d["a"],"pro":d["pro"]})).scalar_one()
        await s.execute(text("""INSERT INTO working_breaks
            (tenant_id,working_hour_id,start_time,end_time)
            VALUES(:tenant,:shift,'14:00','16:00')"""), {"tenant":d["a"],"shift":shift})
    async with d["factory"]() as s:
        before=await list_slots(s,d["a"],d["pro"],[d["cut"]],d["day"],now=clock)
    assert start_at(d["day"],9).isoformat() in before["slots"]
    assert start_at(d["day"],16).isoformat() in before["slots"]
    assert start_at(d["day"],15,45).isoformat() not in before["slots"]
    async with d["factory"]() as s:
        hold=await book_appointment(s,d["a"],d["pro"],[d["cut"]],phone(91),
            "Cliente sintético 09",start_at(d["day"],9),str(uuid4()),now=clock)
    async with d["factory"]() as s:
        confirmed=await confirm_hold(s,d["a"],hold["id"],now=clock)
    assert confirmed["status"]=="confirmed"
    async with d["migrator"].connect() as s:
        occupied=(await s.execute(text("""SELECT occupied_from FROM appointments
            WHERE tenant_id=:tenant AND id=:id"""),
            {"tenant":d["a"],"id":hold["id"]})).scalar_one()
    assert occupied == start_at(d["day"],9).astimezone(UTC)
    async with d["factory"]() as s:
        after=await list_slots(s,d["a"],d["pro"],[d["cut"]],d["day"],now=clock)
    assert start_at(d["day"],9).isoformat() not in after["slots"]
    assert start_at(d["day"],9,15).isoformat() not in after["slots"]
    assert start_at(d["day"],16).isoformat() in after["slots"]
    async with d["factory"]() as s:
        afternoon=await book_appointment(s,d["a"],d["pro"],[d["cut"]],phone(92),
            "Cliente sintético 16",start_at(d["day"],16),str(uuid4()),now=clock)
    async with d["factory"]() as s:
        done=await confirm_hold(s,d["a"],afternoon["id"],now=clock)
    assert done["status"]=="confirmed"
    async with d["migrator"].connect() as s:
        occupied=(await s.execute(text("""SELECT occupied_from FROM appointments
            WHERE tenant_id=:tenant AND id=:id"""),
            {"tenant":d["a"],"id":afternoon["id"]})).scalar_one()
    assert occupied == start_at(d["day"],16).astimezone(UTC)

