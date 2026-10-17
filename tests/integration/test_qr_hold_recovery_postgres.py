"""Temporary QR proposals use real PostgreSQL ownership, clocks and locks."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app import booking_service as booking
from test_booking import booking_db, phone, start_at

pytestmark = pytest.mark.integration
UTC = timezone.utc


async def make_hold(d, key, *, when=None, customer=None, now=None, **kwargs):
    async with d["factory"]() as db:
        return await booking.book_appointment(
            db, d["a"], d["pro"], [d["cut"]], customer or phone(940),
            "Cliente sintético", when or start_at(d["day"], 15), key, now=now, **kwargs)


async def facts(d):
    async with d["migrator"].connect() as db:
        result = {}
        for table in ("appointments", "appointment_history", "appointment_items", "calendar_outbox", "customers"):
            result[table] = [tuple(row) for row in (await db.execute(text(
                f"SELECT * FROM {table} WHERE tenant_id=:tenant ORDER BY id"),
                {"tenant": d["a"]})).all()]
        return result


@pytest.mark.asyncio
async def test_old_fingerprint_replay_does_not_renew_deadline_and_new_proposal_confirms_in_five_seconds(booking_db):
    d = booking_db
    now = datetime.now(UTC)
    old = await make_hold(d, "same-facts-old-proposal", now=now)
    later = now + timedelta(minutes=6)
    replay = await make_hold(d, "same-facts-old-proposal", now=later)
    assert replay["id"] == old["id"]
    assert replay["hold_expires_at"] == old["hold_expires_at"] < later
    async with d["factory"]() as db:
        with pytest.raises(HTTPException) as error:
            await booking.confirm_hold(db, d["a"], replay["id"], now=later,
                                       customer_phone=phone(940), expected_snapshot=replay["expected_snapshot"])
    assert error.value.status_code == 409
    fresh = await make_hold(d, "same-facts-new-message-proposal", now=later)
    assert fresh["id"] != old["id"] and fresh["status"] == "hold"
    assert fresh["hold_expires_at"] == later + booking.HOLD_TTL
    async with d["factory"]() as db:
        result = await booking.confirm_hold(db, d["a"], fresh["id"], now=later + timedelta(seconds=5),
                                            customer_phone=phone(940), expected_snapshot=fresh["expected_snapshot"])
    assert result["status"] == "confirmed" and result["id"] == fresh["id"]
    # Legacy replay is still a factual, idempotent result; it does not create a new hold.
    terminal_replay = await make_hold(d, "same-facts-new-message-proposal", now=later + timedelta(seconds=6))
    assert terminal_replay["id"] == fresh["id"] and terminal_replay["status"] == "confirmed"


@pytest.mark.asyncio
async def test_overlapping_own_hold_is_ignored_only_for_verified_read_and_replaced_atomically(booking_db):
    d = booking_db
    old = await make_hold(d, "overlap-old")
    before = await facts(d)
    async with d["factory"]() as db:
        preview = await booking.preview_hold(db, d["a"], old["id"], customer_phone=phone(940))
    assert preview["expected_snapshot"] == old["expected_snapshot"]
    assert preview["hold_expires_at"] == old["hold_expires_at"]
    when = start_at(d["day"], 15, 30)
    async with d["factory"]() as db:
        ordinary = await booking.list_slots(db, d["a"], d["pro"], [d["cut"]], d["day"])
    assert when.isoformat() not in ordinary["slots"]
    async with d["factory"]() as db:
        replacement = await booking.list_slots(db, d["a"], d["pro"], [d["cut"]], d["day"],
            replace_hold_id=old["id"], customer_phone=phone(940), expected_snapshot=old["expected_snapshot"])
    assert when.isoformat() in replacement["slots"]
    assert await facts(d) == before  # Both preview paths are genuinely read-only.
    with pytest.raises(HTTPException):
        await make_hold(d, "overlap-without-replacement", when=when)
    new = await make_hold(d, "overlap-replacement", when=when, replace_hold_id=old["id"],
                          expected_snapshot=old["expected_snapshot"])
    replay = await make_hold(d, "overlap-replacement", when=when, replace_hold_id=old["id"],
                             expected_snapshot=old["expected_snapshot"])
    assert new["id"] == replay["id"] != old["id"]
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT status FROM appointments WHERE id=:id"), {"id": old["id"]})).scalar_one() == "cancelled"
        assert (await db.execute(text("SELECT count(*) FROM appointments WHERE tenant_id=:t"), {"t": d["a"]})).scalar_one() == 2
        assert (await db.execute(text("SELECT count(*) FROM appointment_history WHERE appointment_id=:id AND event='cancelled'"), {"id": old["id"]})).scalar_one() == 1
        assert (await db.execute(text("SELECT count(*) FROM calendar_outbox WHERE tenant_id=:t"), {"t": d["a"]})).scalar_one() == 0
    async with d["factory"]() as db:
        result = await booking.confirm_hold(db, d["a"], new["id"], customer_phone=phone(940),
                                            expected_snapshot=new["expected_snapshot"])
    assert result["status"] == "confirmed" and result["start_at"] == when.astimezone(UTC)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["foreign_phone", "foreign_tenant", "confirmed", "other_professional", "stale_snapshot", "missing_snapshot", "expired"])
async def test_replacement_never_mutates_foreign_confirmed_expired_or_changed_hold(booking_db, invalid):
    d = booking_db
    now = datetime.now(UTC)
    old = await make_hold(d, "invalid-old", now=now)
    if invalid == "confirmed":
        async with d["factory"]() as db:
            await booking.confirm_hold(db, d["a"], old["id"])
    before = await facts(d)
    tenant = d["b"] if invalid == "foreign_tenant" else d["a"]
    pro = d["pro2"] if invalid == "other_professional" else d["pro"]
    customer = phone(941) if invalid == "foreign_phone" else phone(940)
    snapshot = "0" * 64 if invalid == "stale_snapshot" else old["expected_snapshot"]
    snapshot = None if invalid == "missing_snapshot" else snapshot
    clock = now + timedelta(minutes=6) if invalid == "expired" else now
    async with d["factory"]() as db:
        with pytest.raises(HTTPException):
            await booking.list_slots(db, tenant, pro, [d["cut"]], d["day"], now=clock,
                replace_hold_id=old["id"], customer_phone=customer, expected_snapshot=snapshot)
    async with d["factory"]() as db:
        with pytest.raises(HTTPException):
            await booking.book_appointment(db, tenant, pro, [d["cut"]], customer, "Cliente sintético",
                start_at(d["day"], 15, 30), "invalid-new", now=clock,
                replace_hold_id=old["id"], expected_snapshot=snapshot)
    assert await facts(d) == before


@pytest.mark.asyncio
async def test_occupied_new_target_leaves_old_proposal_and_history_unchanged(booking_db):
    d = booking_db
    old = await make_hold(d, "blocked-old")
    await make_hold(d, "blocked-other", when=start_at(d["day"], 16), customer=phone(941))
    before = await facts(d)
    with pytest.raises(HTTPException):
        await make_hold(d, "blocked-replacement", when=start_at(d["day"], 16),
                        replace_hold_id=old["id"], expected_snapshot=old["expected_snapshot"])
    assert await facts(d) == before


@pytest.mark.asyncio
async def test_failure_after_insert_rolls_back_old_hold_replacement_and_all_new_records(booking_db, monkeypatch):
    d = booking_db
    old = await make_hold(d, "rollback-old")
    before = await facts(d)
    original = booking.history

    async def fail_after_new_hold(db, tenant, appointment, event, **kwargs):
        await original(db, tenant, appointment, event, **kwargs)
        if event == "hold_created":
            raise RuntimeError("synthetic failure after real inserts")

    monkeypatch.setattr(booking, "history", fail_after_new_hold)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        await make_hold(d, "rollback-new", when=start_at(d["day"], 15, 30),
                        replace_hold_id=old["id"], expected_snapshot=old["expected_snapshot"])
    assert await facts(d) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("same_key", [False, True])
async def test_concurrent_replacements_are_serialized_without_cancelling_new_hold(booking_db, same_key):
    d = booking_db
    old = await make_hold(d, "concurrent-old")

    async def attempt(index):
        try:
            return await make_hold(d, "concurrent-new" if same_key else f"concurrent-new-{index}",
                when=start_at(d["day"], 15, 30), replace_hold_id=old["id"], expected_snapshot=old["expected_snapshot"])
        except HTTPException as error:
            assert error.status_code == 409
            return None

    results = await asyncio.gather(attempt(1), attempt(2))
    successful = [item for item in results if item is not None]
    assert len(successful) == (2 if same_key else 1)
    assert len({item["id"] for item in successful}) == 1
    async with d["migrator"].connect() as db:
        statuses = (await db.execute(text("SELECT status,count(*) FROM appointments WHERE tenant_id=:t GROUP BY status"), {"t": d["a"]})).all()
        assert dict(statuses) == {"cancelled": 1, "hold": 1}
        assert (await db.execute(text("SELECT count(*) FROM calendar_outbox WHERE tenant_id=:t"), {"t": d["a"]})).scalar_one() == 0


@pytest.mark.asyncio
async def test_same_idempotency_key_cannot_change_replacement_contract(booking_db):
    d = booking_db
    old = await make_hold(d, "contract-old")
    await make_hold(d, "contract-new", when=start_at(d["day"], 15, 30),
                    replace_hold_id=old["id"], expected_snapshot=old["expected_snapshot"])
    before = await facts(d)
    with pytest.raises(HTTPException) as error:
        await make_hold(d, "contract-new", when=start_at(d["day"], 15, 30),
                        replace_hold_id=old["id"], expected_snapshot="f" * 64)
    assert error.value.status_code == 409
    assert await facts(d) == before


@pytest.mark.asyncio
async def test_confirmation_waiting_for_professional_lock_cannot_outlive_real_hold_deadline(booking_db, monkeypatch):
    d = booking_db
    old = await make_hold(d, "clock-lock-old")
    entered = asyncio.Event()
    original_lock = booking.acquire_professional_lock

    async def observed_lock(db, tenant, professional):
        entered.set()
        await original_lock(db, tenant, professional)

    monkeypatch.setattr(booking, "acquire_professional_lock", observed_lock)

    async def confirm_after_wait():
        async with d["factory"]() as db:
            with pytest.raises(HTTPException) as error:
                await booking.confirm_hold(db, d["a"], old["id"], customer_phone=phone(940),
                                           expected_snapshot=old["expected_snapshot"])
            return error.value

    from app.database import tenant_transaction
    task = None
    try:
        async with d["factory"]() as blocker:
            async with tenant_transaction(blocker, d["a"]):
                await original_lock(blocker, d["a"], d["pro"])
                task = asyncio.create_task(confirm_after_wait())
                await asyncio.wait_for(entered.wait(), timeout=10)
                # The caller has entered confirmation with its previous clock,
                # but the professional lock still prevents its row validation.
                async with d["migrator"].begin() as db:
                    await db.execute(text("UPDATE appointments SET hold_expires_at=clock_timestamp()+interval '100 milliseconds' WHERE id=:id"), {"id": old["id"]})
                async with d["migrator"].connect() as db:
                    await db.execute(text("SELECT pg_sleep(0.15)"))
                    assert (await db.execute(text("SELECT hold_expires_at<clock_timestamp() FROM appointments WHERE id=:id"), {"id": old["id"]})).scalar_one()
        error = await asyncio.wait_for(task, timeout=10)
        assert error.status_code == 409 and "expirado" in error.detail.lower()
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT status FROM appointments WHERE id=:id"), {"id": old["id"]})).scalar_one() == "cancelled"
        assert (await db.execute(text("SELECT count(*) FROM appointment_history WHERE appointment_id=:id AND event='confirmed'"), {"id": old["id"]})).scalar_one() == 0
        assert (await db.execute(text("SELECT count(*) FROM calendar_outbox WHERE appointment_id=:id"), {"id": old["id"]})).scalar_one() == 0


async def qr_route(d, monkeypatch):
    import json
    from app import qr_ai, qr_customer
    from app.qr_understanding import build_plan
    from test_qr_business_time_postgres import Redis
    redis = Redis(); sender = phone(940)[1:]
    business = {"name": "Synthetic hold contract", "timezone": "America/Campo_Grande", "use_working_hours": True}
    catalog = [{"id": d["cut"], "name": "Corte", "price_cents": 3500, "duration_min": 40}]
    professionals = [{"id": d["pro"], "name": "Bruno"}]
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE professionals SET name='Bruno' WHERE tenant_id=:t AND id=:p"), {"t": d["a"], "p": d["pro"]})
    async def data(tenant):
        assert tenant == d["a"]; return business, catalog, professionals
    async def local(**kwargs):
        return build_plan(message=kwargs["message"], catalog=kwargs["catalog"], professionals=kwargs["professionals"],
            context=kwargs["context"], reference_date=kwargs["reference_date"])
    monkeypatch.setattr(qr_ai, "tenant_data", data)
    monkeypatch.setattr(qr_ai, "get_session_factory", lambda: d["factory"])
    monkeypatch.setattr(qr_customer, "get_session_factory", lambda: d["factory"])
    monkeypatch.setattr(qr_ai, "interpret_plan", local)
    monkeypatch.setenv("QR_AI_MULTI_INTENT_ENABLED", "1")
    key = f"orion:qr:ai:{d['a']}:{sender}"
    async def message(body, identity):
        token = qr_ai._message_identity.set(identity)
        try:
            return await qr_ai.turn(d["a"], sender, body, redis, sender, preview=False)
        finally:
            qr_ai._message_identity.reset(token)
    def state(): return json.loads(redis.values.get(key, "{}"))
    return message, state, redis, key


@pytest.mark.asyncio
async def test_actual_qr_old_global_hold_then_correction_replaces_self_and_new_consent_confirms_in_five_seconds(booking_db, monkeypatch):
    import hashlib
    from uuid import UUID
    from app import qr_ai
    d = booking_db
    message, state, redis, key = await qr_route(d, monkeypatch)
    target = start_at(d["day"], 15, 30)
    fp = booking.fingerprint(d["pro"], [d["cut"]], target, phone(940))
    stale_key = "qr-" + hashlib.sha256(f"qr:{d['a']}:{fp}".encode()).hexdigest()
    old = await make_hold(d, stale_key, when=target, now=datetime.now(UTC)-timedelta(hours=2))
    first = await message(f"Quero agendar corte com Bruno no dia {d['day']} às15h", "pg-first-summary")
    first_id = UUID(state()["hold"])
    assert first["step"] == "confirm" and first_id != old["id"]
    before = await facts(d)
    quote = await message("Pera, melhor só corte às3 e meia da tarde. Quanto fica?", "pg-price-correction")
    assert "R$ 35,00" in quote["answer"] and "15h30" in quote["answer"] and not state().get("hold")
    assert await facts(d) == before  # Price/correction is not a write.
    proposed = await message("SIM", "pg-new-summary")
    new_id = UUID(state()["hold"])
    assert proposed["step"] == "confirm" and "15h30" in proposed["answer"] and new_id not in {old["id"], first_id}
    async with d["migrator"].connect() as db:
        expiry = (await db.execute(text("SELECT hold_expires_at FROM appointments WHERE id=:id"), {"id": new_id})).scalar_one()
    assert state()["confirmation_expires_at"] == expiry.timestamp()
    real_confirm = booking.confirm_hold
    async def five_seconds(db, tenant, hold, **kwargs):
        assert hold == new_id and kwargs["customer_phone"] == phone(940) and len(kwargs["expected_snapshot"]) == 64
        return await real_confirm(db, tenant, hold, now=expiry-booking.HOLD_TTL+timedelta(seconds=5), **kwargs)
    monkeypatch.setattr(qr_ai, "confirm_hold", five_seconds)
    done = await message("SIM", "pg-final-consent")
    assert done["step"] == "confirmed" and "15h30" in done["answer"] and done["_verified_operation"] is True
    async with d["migrator"].connect() as db:
        rows = (await db.execute(text("SELECT id,status FROM appointments WHERE tenant_id=:t"), {"t": d["a"]})).all()
        assert dict(rows) == {old["id"]: "cancelled", first_id: "cancelled", new_id: "confirmed"}
        assert (await db.execute(text("SELECT count(*) FROM calendar_outbox WHERE tenant_id=:t AND action='create'"), {"t": d["a"]})).scalar_one() == 1


@pytest.mark.asyncio
async def test_actual_qr_expired_confirmation_keeps_choices_and_followup_never_replays_write(booking_db, monkeypatch):
    from uuid import UUID
    d = booking_db
    message, state, redis, key = await qr_route(d, monkeypatch)
    await message(f"Quero agendar corte com Bruno no dia {d['day']} às15h30", "pg-expiry-summary")
    hold_id = UUID(state()["hold"])
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE appointments SET hold_expires_at=now()-interval '1 second' WHERE id=:id"), {"id": hold_id})
    failed = await message("SIM", "pg-expiry-consent")
    assert failed["step"] == "time" and state()["services"] == [str(d["cut"])]
    assert state()["professional"] == str(d["pro"]) and state()["day"] == d["day"].isoformat()
    assert state()["price"] == 3500 and state()["duration"] == 40 and not state().get("hold")
    after = await facts(d)
    alternatives = await message("Qual outro horário disponível", "pg-expiry-alternatives")
    assert "Bruno" in alternatives["answer"] and "R$ 35,00" in alternatives["answer"]
    await message("SIM", "pg-stale-consent")
    assert await facts(d) == after and not state().get("hold")
