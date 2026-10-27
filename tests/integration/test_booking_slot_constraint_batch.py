"""Compare the batched read grid with the unchanged scalar mutation rules."""
from datetime import date, datetime, time, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
import pytest
from sqlalchemy import event, text

from app.booking_service import _read_slot_constraints, check_slot, load_bundle, verified_slot_grid
from app.database import tenant_transaction
from test_booking import booking_db

pytestmark = pytest.mark.integration
UTC = timezone.utc


async def scalar_grid(db, d, shop, items, target, now, ignored):
    """Every candidate executes the original scalar checks used by mutations."""
    zone = ZoneInfo(shop["timezone"])
    result = []
    for minute in range(0, 1440, shop["slot_step_minutes"]):
        naive = datetime.combine(target, time.min) + timedelta(minutes=minute)
        start = naive.replace(tzinfo=zone).astimezone(UTC).astimezone(zone)
        if start.replace(tzinfo=None) != naive:
            continue
        try:
            await check_slot(db, d["a"], d["pro"], shop, items, start, now,
                             ignore_appointment=ignored)
        except HTTPException as error:
            assert error.status_code in {409, 422}
        else:
            result.append(start.isoformat())
    return result


@pytest.mark.parametrize("zone_name,target", [
    ("America/Sao_Paulo", date(2030, 1, 15)),
    ("America/New_York", date(2030, 3, 10)),  # Nonexistent spring-forward clocks.
    ("America/New_York", date(2030, 11, 3)),  # Ambiguous fall-back clocks.
])
async def test_fresh_batched_constraints_match_scalar_rules_across_dst(booking_db, zone_name, target):
    d = booking_db
    zone = ZoneInfo(zone_name)
    now = datetime.combine(target - timedelta(days=2), time(0), tzinfo=zone).astimezone(UTC)
    ignored, customer = uuid4(), uuid4()
    def at(hour, minute=0):
        return datetime.combine(target, time(hour, minute), tzinfo=zone).astimezone(UTC)

    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE tenants SET timezone=:zone, min_notice_minutes=0 WHERE id=:tenant"),
                         {"zone": zone_name, "tenant": d["a"]})
        await db.execute(text("DELETE FROM working_hours WHERE tenant_id=:tenant AND professional_id=:pro"),
                         {"tenant": d["a"], "pro": d["pro"]})
        shift = (await db.execute(text("""INSERT INTO working_hours
            (tenant_id,professional_id,weekday,start_time,end_time)
            VALUES (:tenant,:pro,:weekday,'00:30','23:30') RETURNING id"""),
            {"tenant": d["a"], "pro": d["pro"], "weekday": target.weekday()})).scalar_one()
        await db.execute(text("""INSERT INTO working_breaks (tenant_id,working_hour_id,start_time,end_time)
            VALUES (:tenant,:shift,'12:00','13:00')"""), {"tenant": d["a"], "shift": shift})
        for table, hour in (("schedule_blocks", 15), ("time_off", 17)):
            await db.execute(text(f"""INSERT INTO {table} (tenant_id,professional_id,starts_at,ends_at)
                VALUES (:tenant,:pro,:start,:stop)"""),
                {"tenant": d["a"], "pro": d["pro"], "start": at(hour), "stop": at(hour + 1)})
        # An unrelated professional's whole-day block must never enter the grid.
        await db.execute(text("""INSERT INTO schedule_blocks (tenant_id,professional_id,starts_at,ends_at)
            VALUES (:tenant,:pro,:start,:stop)"""),
            {"tenant": d["a"], "pro": d["pro2"], "start": at(0), "stop": at(23)})
        await db.execute(text("""INSERT INTO customers(id,tenant_id,name,whatsapp_e164)
            VALUES (:id,:tenant,'Synthetic grid customer','+5511999999911')"""),
            {"id": customer, "tenant": d["a"]})
        for hour, status, expiry, identifier in (
            (9, "confirmed", None, uuid4()),
            (10, "hold", now + timedelta(minutes=5), uuid4()),
            (11, "hold", now - timedelta(minutes=1), uuid4()),
            (14, "cancelled", None, uuid4()),
            (20, "confirmed", None, ignored),
        ):
            await db.execute(text("""INSERT INTO appointments
                (id,tenant_id,professional_id,customer_id,protocol,idempotency_key,request_fingerprint,
                 start_at,end_at,occupied_from,occupied_until,status,hold_expires_at,total_cents)
                VALUES (:id,:tenant,:pro,:customer,:protocol,:idem,:fingerprint,:start,:end,:begin,:stop,
                        :status,:expiry,3500)"""),
                {"id": identifier, "tenant": d["a"], "pro": d["pro"], "customer": customer,
                 "protocol": "G" + identifier.hex[:8], "idem": "grid-" + identifier.hex,
                 "fingerprint": "f" * 64, "start": at(hour), "end": at(hour, 40),
                 "begin": at(hour) - timedelta(minutes=5), "stop": at(hour, 50),
                 "status": status, "expiry": expiry})

    async with d["factory"]() as db:
        async with tenant_transaction(db, d["a"]):
            # JSON timestamptz output follows the PostgreSQL session timezone;
            # the engine must preserve those instants when parsing its batch.
            await db.execute(text("SET LOCAL TIME ZONE 'Pacific/Auckland'"))
            shop, items = await load_bundle(db, d["a"], d["pro"], [d["cut"]])
            statements = []
            def observed(connection, cursor, statement, parameters, context, many):
                statements.append(statement)
            event.listen(d["runtime"].sync_engine, "before_cursor_execute", observed)
            try:
                inputs = await _read_slot_constraints(db, d["a"], d["pro"], target, zone, now, ignored)
            finally:
                event.remove(d["runtime"].sync_engine, "before_cursor_execute", observed)
            assert len(statements) == 1
            assert len(inputs.pauses) == 1 and len(inputs.blocks) == 2
            assert len(inputs.reservations) == 2  # Confirmed + live hold only.
            for ignored_id in (None, ignored):
                grid, reason = await verified_slot_grid(db, d["a"], d["pro"], shop, items,
                                                       target, now, ignored=ignored_id, strict_errors=True)
                expected = await scalar_grid(db, d, shop, items, target, now, ignored_id)
                assert grid == expected
                assert reason == "available" and grid
            assert datetime.combine(target, time(11), tzinfo=zone).isoformat() in grid
            assert datetime.combine(target, time(20, 15), tzinfo=zone).isoformat() in grid
            assert datetime.combine(target, time(9, 15), tzinfo=zone).isoformat() not in grid
            if zone_name == "America/New_York":
                assert not any(value.startswith(target.isoformat() + ("T02:" if target.month == 3 else "T01:"))
                               for value in grid)

            # A second call observes a newly inserted closure without a cache.
            await db.execute(text("""INSERT INTO schedule_blocks (tenant_id,professional_id,starts_at,ends_at)
                VALUES (:tenant,:pro,:start,:stop)"""),
                {"tenant": d["a"], "pro": d["pro"], "start": at(21), "stop": at(22)})
            fresh, _ = await verified_slot_grid(db, d["a"], d["pro"], shop, items, target, now, ignored=ignored)
            assert fresh == await scalar_grid(db, d, shop, items, target, now, ignored)
            assert len(fresh) < len(grid)


async def test_batch_without_working_hours_returns_empty_grid(booking_db):
    d = booking_db
    target = d["day"] + timedelta(days=1)
    async with d["factory"]() as db:
        async with tenant_transaction(db, d["a"]):
            shop, items = await load_bundle(db, d["a"], d["pro"], [d["cut"]])
            grid, reason = await verified_slot_grid(db, d["a"], d["pro"], shop, items, target, datetime.now(UTC))
            assert grid == [] and reason == "no_working_hours"
