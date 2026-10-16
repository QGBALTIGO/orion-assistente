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
    reschedule_appointment,
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
            for table in ("appointment_history", "appointment_items", "appointments",
                          "customers", "working_breaks", "professional_services",
                          "working_hours", "schedule_blocks", "time_off", "services",
                          "professionals", "tenant_audit"):
                await s.execute(text(f"DELETE FROM {table} WHERE tenant_id=:tenant"), {"tenant": a})
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
    assert start_at(d["day"], 8).isoformat() not in available  # 5 min de preparação
    assert start_at(d["day"], 8, 15).isoformat() in available
    assert start_at(d["day"], 11, 30).isoformat() not in available
    assert start_at(d["day"], 14, 30).isoformat() not in available
    assert start_at(d["day"], 16, 30).isoformat() not in available
    assert start_at(d["day"], 17).isoformat() not in available
    # 13h ainda conflita com a pausa por exigir 5 min de preparação.
    assert start_at(d["day"], 13).isoformat() not in available
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
