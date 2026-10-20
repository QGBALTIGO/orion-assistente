"""A grid reads fresh request-local constraints; mutations still recheck SQL."""
from collections import Counter
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException
import pytest

from app import booking_service as booking

UTC = timezone.utc
TENANT, PRO, SERVICE = UUID(int=1), UUID(int=2), UUID(int=3)
DAY = date(2030, 1, 2)
NOW = datetime(2030, 1, 1, tzinfo=UTC)


class Result:
    def __init__(self, rows=()): self.rows = list(rows)
    def mappings(self): return self
    def all(self): return self.rows
    def first(self): return self.rows[0] if self.rows else None


class Database:
    def __init__(self, *, step=15, tz="America/Campo_Grande", start=time(9), end=time(18)):
        self.shop = {"timezone": tz, "slot_step_minutes": step, "min_notice_minutes": 0,
            "booking_horizon_days": 3650, "status": "active", "professional_name": "Synthetic"}
        self.item = {"id": str(SERVICE), "name": "Synthetic", "duration_min": 40,
            "price_cents": 3500, "prep_min": 0, "cleanup_min": 0}
        self.hours = {"id": UUID(int=4), "start_time": start, "end_time": end}
        self.pauses, self.time_off, self.schedule_blocks, self.appointments = [], [], [], []
        self.calls = Counter()

    @asynccontextmanager
    async def begin(self): yield self

    def interval(self, hour, end, *, day=DAY, **extra):
        tz = ZoneInfo(self.shop["timezone"])
        return {"starts_at": datetime.combine(day, time(hour), tzinfo=tz).astimezone(UTC),
            "ends_at": datetime.combine(day, time(end), tzinfo=tz).astimezone(UTC), **extra}

    async def execute(self, statement, params):
        sql = str(statement)
        if "set_config(" in sql:
            self.calls["tenant"] += 1; return Result()
        if "jsonb_build_object" in sql:
            self.calls["bundle"] += 1
            if params["bundle_tenant"] != TENANT or params["bundle_professional"] != PRO: return Result()
            return Result([{"shop": self.shop, "professional_available": True, "items": [self.item]}])
        table = next(name for name in ("working_hours", "working_breaks", "time_off", "schedule_blocks", "appointments") if "FROM " + name in sql)
        self.calls[table] += 1
        assert params["tenant"] == TENANT
        if table == "working_hours": return Result([self.hours] if self.hours else [])
        if table == "working_breaks": return Result(self.pauses)
        assert params["professional"] == PRO
        rows = getattr(self, table)
        matching = [row for row in rows if row.get("tenant", TENANT) == TENANT and row.get("pro", PRO) == PRO
            and row["starts_at"] < params["stop"] and row["ends_at"] > params["begin"]]
        if table == "appointments":
            matching = [row for row in matching if row["id"] != params["ignored"] and row["status"] in ("hold", "confirmed")
                and (row["status"] != "hold" or row["hold_expires_at"] > params["now"])]
        return Result(matching[:1] if "SELECT 1" in sql else matching)


async def exhaustive(db, target=DAY, now=NOW):
    """The existing per-candidate SQL path remains the correctness oracle."""
    async with booking.tenant_transaction(db, TENANT):
        shop, items = await booking.load_bundle(db, TENANT, PRO, [SERVICE])
        tz = ZoneInfo(shop["timezone"])
        values = []
        for minute in range(0, 1440, shop["slot_step_minutes"]):
            naive = datetime.combine(target, time.min) + timedelta(minutes=minute)
            start = naive.replace(tzinfo=tz).astimezone(UTC).astimezone(tz)
            if start.replace(tzinfo=None) != naive: continue
            try:
                await booking.check_slot(db, TENANT, PRO, shop, items, start, now)
            except HTTPException:
                continue
            values.append(start.isoformat())
        return values


@pytest.mark.asyncio
@pytest.mark.parametrize("step", [5, 15, 30])
async def test_grid_uses_seven_constant_queries_instead_of_per_candidate_reads(step):
    db = Database(step=step)
    actual = await booking.list_slots(db, TENANT, PRO, [SERVICE], DAY, now=NOW)
    assert db.calls == Counter({"tenant": 1, "bundle": 1, "working_hours": 1, "working_breaks": 1,
        "time_off": 1, "schedule_blocks": 1, "appointments": 1})
    original_calls = sum(db.calls.values())
    expected = await exhaustive(db)
    assert actual["slots"] == expected and sum(db.calls.values()) > original_calls + 100
    assert actual["price_cents"] == 3500 and actual["duration_min"] == 40
    if step == 15: assert len(expected) == 34


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["pauses", "time_off", "schedule_blocks", "live_hold", "expired_hold", "confirmed", "cancelled", "foreign_tenant", "foreign_professional", "buffers"])
async def test_prefetch_preserves_each_constraint_and_its_boundaries(kind):
    db = Database()
    if kind == "pauses": db.pauses = [{"start_time": time(12), "end_time": time(13)}]
    elif kind in ("time_off", "schedule_blocks"): getattr(db, kind).append(db.interval(12, 13))
    elif kind == "buffers": db.item.update(prep_min=5, cleanup_min=10)
    else:
        status = "hold" if kind in ("live_hold", "expired_hold") else "cancelled" if kind == "cancelled" else "confirmed"
        db.appointments.append(db.interval(12, 13, id=UUID(int=10), status=status,
            hold_expires_at=NOW + timedelta(minutes=-1 if kind == "expired_hold" else 5),
            **({"tenant": UUID(int=99)} if kind == "foreign_tenant" else {"pro": UUID(int=99)} if kind == "foreign_professional" else {})))
    actual = await booking.list_slots(db, TENANT, PRO, [SERVICE], DAY, now=NOW)
    assert actual["slots"] == await exhaustive(db)
    noon = datetime.combine(DAY, time(12), tzinfo=ZoneInfo(db.shop["timezone"])).isoformat()
    if kind in ("pauses", "time_off", "schedule_blocks", "live_hold", "confirmed"): assert noon not in actual["slots"]
    elif kind != "buffers": assert noon in actual["slots"]
    if kind == "buffers":
        assert not actual["slots"][0].endswith("09:00:00-04:00")
        assert actual["prep_min"] == 5 and actual["cleanup_min"] == 10


@pytest.mark.asyncio
@pytest.mark.parametrize("target,missing_hour", [(date(2030, 3, 10), 2), (date(2030, 11, 3), 1)])
async def test_dst_nonexistent_and_ambiguous_hours_remain_unoffered(target, missing_hour):
    db = Database(step=30, tz="America/New_York", start=time(0), end=time(6))
    actual = await booking.list_slots(db, TENANT, PRO, [SERVICE], target, now=NOW)
    assert actual["slots"] == await exhaustive(db, target)
    assert not any(datetime.fromisoformat(value).hour == missing_hour for value in actual["slots"])


@pytest.mark.asyncio
async def test_no_shift_is_empty_and_infrastructure_errors_do_not_become_no_availability():
    db = Database(); db.hours = None
    assert (await booking.list_slots(db, TENANT, PRO, [SERVICE], DAY, now=NOW))["slots"] == []
    assert db.calls == Counter({"tenant": 1, "bundle": 1, "working_hours": 1})
    db = Database(); original = db.execute
    async def broken(statement, params):
        if "FROM schedule_blocks" in str(statement): raise ConnectionError("Synthetic database unavailable")
        return await original(statement, params)
    db.execute = broken
    with pytest.raises(ConnectionError): await booking.list_slots(db, TENANT, PRO, [SERVICE], DAY, now=NOW)


@pytest.mark.asyncio
async def test_second_request_and_mutation_recheck_changed_database_without_reusing_grid():
    db = Database(); first = await booking.list_slots(db, TENANT, PRO, [SERVICE], DAY, now=NOW)
    start = datetime.combine(DAY, time(14), tzinfo=ZoneInfo(db.shop["timezone"]))
    assert start.isoformat() in first["slots"]
    db.schedule_blocks.append(db.interval(14, 15))
    before = sum(db.calls.values())
    second = await booking.list_slots(db, TENANT, PRO, [SERVICE], DAY, now=NOW)
    assert sum(db.calls.values()) - before == 7 and start.isoformat() not in second["slots"]
    shop, items = await booking.load_bundle(db, TENANT, PRO, [SERVICE])
    before = db.calls["schedule_blocks"]
    with pytest.raises(HTTPException) as caught: await booking.check_slot(db, TENANT, PRO, shop, items, start, NOW)
    assert caught.value.status_code == 409 and db.calls["schedule_blocks"] == before + 1


@pytest.mark.asyncio
async def test_request_constraints_cannot_be_reused_for_another_professional_or_day():
    db = Database(); tz = ZoneInfo(db.shop["timezone"])
    constraints = await booking._read_slot_constraints(db, TENANT, PRO, DAY, tz, NOW, None)
    shop, items = await booking.load_bundle(db, TENANT, PRO, [SERVICE])
    for tenant, pro, target in [(UUID(int=99), PRO, DAY), (TENANT, UUID(int=99), DAY), (TENANT, PRO, DAY+timedelta(days=1))]:
        with pytest.raises(ValueError):
            await booking.check_slot(db, tenant, pro, shop, items, datetime.combine(target, time(14), tzinfo=tz), NOW, _constraints=constraints)


@pytest.mark.asyncio
async def test_minimum_notice_and_horizon_still_restrict_grid_before_any_proposal():
    db = Database(); db.shop.update(min_notice_minutes=60, booking_horizon_days=0)
    clock = datetime.combine(DAY, time(16), tzinfo=UTC)  # Noon in the business timezone.
    actual = await booking.list_slots(db, TENANT, PRO, [SERVICE], DAY, now=clock)
    assert actual["slots"] == await exhaustive(db, now=clock)
    assert datetime.combine(DAY, time(13), tzinfo=ZoneInfo(db.shop["timezone"])).isoformat() in actual["slots"]
    assert all(datetime.fromisoformat(value).hour >= 13 for value in actual["slots"])
    for target in (DAY-timedelta(days=1), DAY+timedelta(days=1)):
        before = db.calls["working_hours"]
        with pytest.raises(HTTPException) as caught:
            await booking.list_slots(db, TENANT, PRO, [SERVICE], target, now=clock)
        assert caught.value.status_code == 422 and db.calls["working_hours"] == before
