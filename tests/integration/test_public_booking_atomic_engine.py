"""Public-only transaction ownership, lock compatibility and atomic rollback.

These tests use synthetic tenants and real PostgreSQL roles. No public transport,
provider or production state participates in the engine assertions.
"""
import asyncio
from datetime import datetime, time, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import booking_service
from app.booking_service import (
    acquire_professional_lock, book_appointment, confirm_hold, public_booking_transaction,
)
from app.database import tenant_transaction
from test_public_catalog import public_db, publish

pytestmark = pytest.mark.integration
TZ = ZoneInfo("America/Sao_Paulo")
PHONE = "+5511987654321"


def start(d, hour=9, minute=0, *, day=None):
    return datetime.combine(day or d["day"], time(hour, minute), tzinfo=TZ)


def factory(d):
    return async_sessionmaker(d["runtime"], expire_on_commit=False)


async def create(s, d, *, at=None, key=None, phone=PHONE, public=True):
    return await book_appointment(
        s, d["a"], d["pro"], [d["cut"]], phone, "Cliente exclusivamente sintético",
        at or start(d), key or "atomic-" + uuid4().hex,
        source="public_site", _public_transaction=public)


async def confirm(s, d, hold, *, phone=PHONE):
    return await confirm_hold(s, d["a"], hold["id"], customer_phone=phone,
                              source="public_site", _public_transaction=True)


async def assert_no_creation(d):
    async with d["migrator"].connect() as db:
        for table in ("appointments", "appointment_items", "appointment_history", "calendar_outbox", "usage_counters"):
            count = (await db.execute(text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                                      {"tenant": d["a"]})).scalar_one()
            assert count == 0, table
        assert (await db.execute(text("SELECT count(*) FROM customers WHERE tenant_id=:tenant AND whatsapp_e164=:phone"),
                                 {"tenant": d["a"], "phone": PHONE})).scalar_one() == 0


@pytest.mark.parametrize("failure", ["during_confirmation", "after_confirmation"])
async def test_public_confirmation_failure_rolls_back_every_engine_write(public_db, monkeypatch, failure):
    d = public_db
    await publish(d)
    original_history = booking_service.history
    async def failing_history(s, tenant, appointment, event, **kwargs):
        if event == "confirmed" and failure == "during_confirmation":
            raise RuntimeError("synthetic confirmation failure")
        return await original_history(s, tenant, appointment, event, **kwargs)
    monkeypatch.setattr(booking_service, "history", failing_history)
    async with factory(d)() as s:
        with pytest.raises(RuntimeError, match="synthetic"):
            async with public_booking_transaction(s, d["a"], d["pro"], d["day"]):
                backend = (await s.execute(text("SELECT pg_backend_pid()"))).scalar_one()
                hold = await create(s, d)
                result = await confirm(s, d, hold)
                assert result["status"] == "confirmed"
                assert (await s.execute(text("SELECT pg_backend_pid()"))).scalar_one() == backend
                # Even a completely confirmed result is invisible until the one
                # enclosing transaction commits the receipt/publication work.
                await assert_no_creation(d)
                raise RuntimeError("synthetic response/receipt failure")
        assert not s.in_transaction()
        assert "orion.public_booking_transaction" not in s.info
    await assert_no_creation(d)


async def test_public_days_are_independent_and_conflict_with_legacy_professional_lock(public_db):
    d = public_db
    sessions = factory(d)
    async with sessions() as first, sessions() as other, sessions() as editor:
        async with public_booking_transaction(first, d["a"], d["pro"], d["day"]):
            async with public_booking_transaction(other, d["a"], d["pro"], d["day"] + timedelta(days=1)):
                async with tenant_transaction(editor, d["a"]):
                    locked = (await editor.execute(text("SELECT pg_try_advisory_xact_lock(hashtextextended(:scope,0))"),
                        {"scope": str(d["a"]) + ":" + str(d["pro"])})).scalar_one()
                    assert not locked
        # Existing callers need no flag or lock migration: their exact original
        # exclusive key becomes available after both public transactions leave.
        async with tenant_transaction(editor, d["a"]):
            await asyncio.wait_for(acquire_professional_lock(editor, d["a"], d["pro"]), timeout=1)
            for day in (d["day"], d["day"] + timedelta(days=1)):
                with pytest.raises(HTTPException) as error:
                    async with public_booking_transaction(other, d["a"], d["pro"], day):
                        pytest.fail("Exclusive legacy/editor ownership must reject public admission")
                assert error.value.status_code == 409
                assert error.value.detail["code"] == "agenda_busy"
                assert error.value.detail["retryable"] is True
                assert not other.in_transaction()


async def test_public_independent_day_creations_defer_monthly_counter_and_replay_charges_nothing(public_db):
    d = public_db
    await publish(d)
    later_day = d["day"] + timedelta(days=7)
    second_phone = "+5511987654322"
    key = "public-replay-" + uuid4().hex
    sessions = factory(d)
    # A premature monthly counter UPDATE would keep the second transaction
    # waiting for the first, although their day locks are independent.
    async with asyncio.timeout(4):
        async with sessions() as first, sessions() as other:
            async with public_booking_transaction(first, d["a"], d["pro"], d["day"]):
                hold = await create(first, d, key=key)
                first_result = await confirm(first, d, hold)
                async with public_booking_transaction(other, d["a"], d["pro"], later_day):
                    second_hold = await create(other, d, at=start(d, day=later_day), phone=second_phone)
                    second_result = await confirm(other, d, second_hold, phone=second_phone)
                    assert second_result["status"] == "confirmed"
            async with public_booking_transaction(first, d["a"], d["pro"], d["day"]):
                replayed_hold = await create(first, d, key=key)
                replay = await confirm(first, d, replayed_hold)
                assert replay["id"] == first_result["id"]
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT bookings FROM usage_counters WHERE tenant_id=:tenant"),
                                 {"tenant": d["a"]})).scalar_one() == 2
        assert (await db.execute(text("SELECT count(*) FROM appointments WHERE tenant_id=:tenant AND status='confirmed'"),
                                 {"tenant": d["a"]})).scalar_one() == 2


async def test_public_same_day_contention_and_same_slot_never_create_residual_hold(public_db):
    d = public_db
    await publish(d)
    sessions = factory(d)
    async with sessions() as first, sessions() as contender:
        async with public_booking_transaction(first, d["a"], d["pro"], d["day"]):
            hold = await create(first, d)
            confirmed = await confirm(first, d, hold)
            with pytest.raises(HTTPException) as error:
                async with public_booking_transaction(contender, d["a"], d["pro"], d["day"]):
                    await create(contender, d, phone="+5511987654322")
            assert error.value.detail["code"] == "agenda_busy"
        with pytest.raises(HTTPException) as error:
            async with public_booking_transaction(contender, d["a"], d["pro"], d["day"]):
                await create(contender, d, phone="+5511987654322")
        assert error.value.status_code == 409
        assert "reservado" in error.value.detail
    async with d["migrator"].connect() as db:
        rows = (await db.execute(text("SELECT id,status FROM appointments WHERE tenant_id=:tenant"),
                                 {"tenant": d["a"]})).mappings().all()
        assert [dict(row) for row in rows] == [{"id": confirmed["id"], "status": "confirmed"}]
        assert (await db.execute(text("SELECT bookings FROM usage_counters WHERE tenant_id=:tenant"),
                                 {"tenant": d["a"]})).scalar_one() == 1


async def test_public_engine_flag_cannot_escape_session_tenant_professional_or_day(public_db):
    d = public_db
    await publish(d)
    async with factory(d)() as s:
        with pytest.raises(ValueError, match="active verified"):
            await create(s, d)
        with pytest.raises(ValueError, match="active verified"):
            await confirm_hold(s, d["a"], uuid4(), source="public_site", _public_transaction=True)
        async with public_booking_transaction(s, d["a"], d["pro"], d["day"]):
            with pytest.raises(ValueError, match="active verified"):
                await create(s, {**d, "a": d["b"]})
            with pytest.raises(ValueError, match="another professional"):
                await create(s, {**d, "pro": d["pro2"]})
            with pytest.raises(HTTPException) as error:
                await create(s, d, at=start(d, day=d["day"] + timedelta(days=1)))
            assert error.value.status_code == 422
            with pytest.raises(ValueError, match="own its session"):
                async with public_booking_transaction(s, d["a"], d["pro"], d["day"]):
                    pass
    await assert_no_creation(d)


@pytest.mark.parametrize("hour,minute,prep,cleanup", [(0, 30, 60, 0), (23, 30, 0, 30)])
async def test_public_full_occupied_interval_cannot_cross_its_locked_local_day(public_db, hour, minute, prep, cleanup):
    d = public_db
    await publish(d)
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE services SET prep_min=:prep,cleanup_min=:cleanup WHERE tenant_id=:tenant AND id=:id"),
                         {"tenant": d["a"], "id": d["cut"], "prep": prep, "cleanup": cleanup})
    async with factory(d)() as s:
        with pytest.raises(HTTPException) as error:
            async with public_booking_transaction(s, d["a"], d["pro"], d["day"]):
                await create(s, d, at=start(d, hour, minute))
        assert error.value.status_code == 409
        assert "buffers" in error.value.detail and "dia escolhido" in error.value.detail
    await assert_no_creation(d)


async def test_public_buffers_still_prevent_overlap_between_separate_start_times(public_db):
    d = public_db
    await publish(d)
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE services SET prep_min=15,cleanup_min=15 WHERE tenant_id=:tenant AND id=:id"),
                         {"tenant": d["a"], "id": d["cut"]})
    async with factory(d)() as s:
        async with public_booking_transaction(s, d["a"], d["pro"], d["day"]):
            hold = await create(s, d)
            await confirm(s, d, hold)
        with pytest.raises(HTTPException) as error:
            async with public_booking_transaction(s, d["a"], d["pro"], d["day"]):
                await create(s, d, at=start(d, 9, 30), phone="+5511987654322")
        assert error.value.status_code == 409
        assert "reservado" in error.value.detail


async def test_public_expiration_never_writes_another_days_unlocked_hold(public_db):
    d = public_db
    await publish(d)
    later_day = d["day"] + timedelta(days=7)  # Same configured weekday, within the horizon.
    async with factory(d)() as s:
        first = await create(s, d, public=False)
        other = await create(s, d, at=start(d, day=later_day), public=False)
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE appointments SET hold_expires_at=:expired WHERE tenant_id=:tenant AND status='hold'"),
                         {"tenant": d["a"], "expired": datetime.now(timezone.utc) - timedelta(minutes=1)})
    async with factory(d)() as s:
        async with public_booking_transaction(s, d["a"], d["pro"], d["day"]):
            fresh = await create(s, d, at=start(d, 10))
            await confirm(s, d, fresh)
    async with d["migrator"].connect() as db:
        statuses = dict((await db.execute(text("SELECT id,status FROM appointments WHERE tenant_id=:tenant"),
                                          {"tenant": d["a"]})).all())
        assert statuses[first["id"]] == "cancelled"
        assert statuses[other["id"]] == "hold"
        assert statuses[fresh["id"]] == "confirmed"
