"""Real PostgreSQL parity with the unchanged per-candidate write validator."""
from collections import Counter
from datetime import datetime, time, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
import pytest
from sqlalchemy import event, text

from app import booking_service as booking
from app.database import tenant_transaction
from test_booking import booking_db, phone, start_at  # noqa: F401

pytestmark = pytest.mark.integration
UTC = timezone.utc


async def oracle(d, services, clock, *, ignored=None):
    async with d["factory"]() as db:
        async with tenant_transaction(db, d["a"]):
            shop, items = await booking.load_bundle(db, d["a"], d["pro"], services)
            tz = ZoneInfo(shop["timezone"])
            slots = []
            for minute in range(0, 1440, shop["slot_step_minutes"]):
                naive = datetime.combine(d["day"], time.min) + timedelta(minutes=minute)
                start = naive.replace(tzinfo=tz).astimezone(UTC).astimezone(tz)
                if start.replace(tzinfo=None) != naive: continue
                try:
                    await booking.check_slot(db, d["a"], d["pro"], shop, items, start, clock, ignore_appointment=ignored)
                except HTTPException:
                    continue
                slots.append(start.isoformat())
            duration, prep, cleanup, price = booking.amounts(items)
            return {"slots": slots, "duration_min": duration, "prep_min": prep, "cleanup_min": cleanup,
                "price_cents": price, "timezone": shop["timezone"]}


async def create(d, at, suffix, clock, *, pro=None):
    async with d["factory"]() as db:
        return await booking.book_appointment(db, d["a"], pro or d["pro"], [d["cut"]], phone(84),
            "Synthetic parity customer", start_at(d["day"], at), "slot-prefetch-"+suffix+"-"+str(uuid4()), now=clock)


@pytest.mark.asyncio
@pytest.mark.parametrize("names", [("cut",), ("beard",), ("cut", "beard")])
async def test_grid_matches_exhaustive_sql_with_pauses_buffers_blocks_live_and_expired_holds(booking_db, names):
    d = booking_db; clock = datetime.now(UTC)
    async with d["migrator"].begin() as db:
        shift = (await db.execute(text("SELECT id FROM working_hours WHERE tenant_id=:t AND professional_id=:p"),
            {"t": d["a"], "p": d["pro"]})).scalar_one()
        await db.execute(text("INSERT INTO working_breaks(tenant_id,working_hour_id,start_time,end_time) VALUES(:t,:h,'12:00','13:00')"),
            {"t": d["a"], "h": shift})
        for table, begin, stop in (("schedule_blocks", 10, 10.5), ("time_off", 15, 16)):
            await db.execute(text(f"INSERT INTO {table}(tenant_id,professional_id,starts_at,ends_at) VALUES(:t,:p,:lo,:hi)"),
                {"t": d["a"], "p": d["pro"], "lo": start_at(d["day"], begin),
                    "hi": start_at(d["day"], int(stop), 30 if stop != int(stop) else 0)})
    await create(d, 9, "live", clock)
    confirmed = await create(d, 11, "confirmed", clock)
    async with d["factory"]() as db: await booking.confirm_hold(db, d["a"], confirmed["id"], now=clock)
    await create(d, 17, "expired", clock-timedelta(minutes=10))
    await create(d, 13, "other-professional", clock, pro=d["pro2"])
    services = [d[name] for name in names]
    async with d["factory"]() as db: actual = await booking.list_slots(db, d["a"], d["pro"], services, d["day"], now=clock)
    assert actual == await oracle(d, services, clock)
    assert start_at(d["day"], 13, 15).isoformat() in actual["slots"]
    assert start_at(d["day"], 9).isoformat() not in actual["slots"]
    assert start_at(d["day"], 12).isoformat() not in actual["slots"]
    assert start_at(d["day"], 15).isoformat() not in actual["slots"]
    if names == ("beard",): assert start_at(d["day"], 17).isoformat() in actual["slots"]


@pytest.mark.asyncio
async def test_own_hold_and_confirmed_reschedule_ignore_only_the_validated_record(booking_db):
    d = booking_db; clock = datetime.now(UTC); hold = await create(d, 10, "replace", clock)
    async with d["factory"]() as db:
        ordinary = await booking.list_slots(db, d["a"], d["pro"], [d["cut"]], d["day"], now=clock)
    assert start_at(d["day"], 10).isoformat() not in ordinary["slots"]
    async with d["factory"]() as db:
        replaced = await booking.list_slots(db, d["a"], d["pro"], [d["cut"]], d["day"], now=clock,
            replace_hold_id=hold["id"], customer_phone=phone(84), expected_snapshot=hold["expected_snapshot"])
    assert replaced == await oracle(d, [d["cut"]], clock, ignored=hold["id"])
    assert start_at(d["day"], 10).isoformat() in replaced["slots"]
    for contact, snapshot, code in [(phone(85), hold["expected_snapshot"], 404), (phone(84), "invalid", 409)]:
        async with d["factory"]() as db:
            with pytest.raises(HTTPException) as caught:
                await booking.list_slots(db, d["a"], d["pro"], [d["cut"]], d["day"], now=clock,
                    replace_hold_id=hold["id"], customer_phone=contact, expected_snapshot=snapshot)
            assert caught.value.status_code == code
    async with d["factory"]() as db: await booking.confirm_hold(db, d["a"], hold["id"], now=clock)
    async with d["factory"]() as db:
        reschedule = await booking.list_slots(db, d["a"], d["pro"], [d["cut"]], d["day"], now=clock, appointment_id=hold["id"])
    assert reschedule == await oracle(d, [d["cut"]], clock, ignored=hold["id"])
    async with d["factory"]() as db:
        with pytest.raises(HTTPException) as caught:
            await booking.list_slots(db, d["b"], d["pro"], [d["cut"]], d["day"], now=clock)
        assert caught.value.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("step", [5, 15, 30])
async def test_fixed_query_budget_and_new_request_and_write_observe_new_block(booking_db, step):
    d = booking_db; clock = datetime.now(UTC)
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE tenants SET slot_step_minutes=:step WHERE id=:t"), {"step": step, "t": d["a"]})
    # Exclude dialect/connection initialization from the function's SQL budget.
    async with d["factory"]() as db:
        async with tenant_transaction(db, d["a"]): await db.execute(text("SELECT 1"))
    statements = []
    def observed(_connection, _cursor, statement, _parameters, _context, _many): statements.append(statement)
    event.listen(d["runtime"].sync_engine, "before_cursor_execute", observed)
    try:
        async with d["factory"]() as db: first = await booking.list_slots(db, d["a"], d["pro"], [d["cut"]], d["day"], now=clock)
    finally:
        event.remove(d["runtime"].sync_engine, "before_cursor_execute", observed)
    assert len(statements) == 7, statements
    assert Counter(name for name in ("working_hours", "working_breaks", "time_off", "schedule_blocks", "appointments")
        for statement in statements if "FROM " + name in statement) == Counter({name: 1 for name in
            ("working_hours", "working_breaks", "time_off", "schedule_blocks", "appointments")})
    assert start_at(d["day"], 14).isoformat() in first["slots"]
    async with d["migrator"].begin() as db:
        await db.execute(text("INSERT INTO schedule_blocks(tenant_id,professional_id,starts_at,ends_at) VALUES(:t,:p,:lo,:hi)"),
            {"t": d["a"], "p": d["pro"], "lo": start_at(d["day"], 14), "hi": start_at(d["day"], 15)})
        before = (await db.execute(text("SELECT count(*) FROM appointments WHERE tenant_id=:t"), {"t": d["a"]})).scalar_one()
    async with d["factory"]() as db: current = await booking.list_slots(db, d["a"], d["pro"], [d["cut"]], d["day"], now=clock)
    assert current == await oracle(d, [d["cut"]], clock)
    assert start_at(d["day"], 14).isoformat() not in current["slots"]
    with pytest.raises(HTTPException) as caught: await create(d, 14, "blocked-after-read", clock)
    assert caught.value.status_code == 409
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM appointments WHERE tenant_id=:t"), {"t": d["a"]})).scalar_one() == before
        assert (await db.execute(text("SELECT count(*) FROM appointment_history WHERE tenant_id=:t"), {"t": d["a"]})).scalar_one() == 0
        assert (await db.execute(text("SELECT count(*) FROM calendar_outbox WHERE tenant_id=:t"), {"t": d["a"]})).scalar_one() == 0
