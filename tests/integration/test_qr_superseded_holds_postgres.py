"""Real temporary proposals must be replaced without orphaning prior holds."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from redis.asyncio import from_url
from sqlalchemy import text
from fastapi import HTTPException

from app import booking_service as booking, qr_ai, qr_cancellation, whatsapp_dialogue
from app.database import tenant_transaction
from test_booking import booking_db, phone, start_at
from test_qr_hold_recovery_postgres import facts
from test_qr_intelligence_postgres import turn_setup

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def conversation(booking_db, monkeypatch):
    d = booking_db
    url = os.getenv("TEST_REDIS_URL")
    if not url:
        if os.getenv("REQUIRE_REDIS_TESTS") == "1":
            pytest.fail("Disposable Redis required")
        pytest.skip("Disposable Redis not configured")
    redis = from_url(url, decode_responses=True, socket_connect_timeout=2, socket_timeout=3)
    await redis.ping()
    _, namespace, turn = await turn_setup(d, monkeypatch, redis)
    for module in (qr_cancellation, whatsapp_dialogue):
        monkeypatch.setattr(module, "get_session_factory", lambda: d["factory"])
    key = f"orion:qr:ai:{d['a']}:{namespace}"

    async def state():
        return json.loads(await redis.get(key) or "{}")

    async def rows():
        async with d["migrator"].connect() as db:
            result = await db.execute(text("""SELECT a.id,a.status,a.professional_id,a.total_cents,a.hold_expires_at
                FROM appointments a JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
                WHERE a.tenant_id=:tenant AND c.whatsapp_e164=:phone ORDER BY a.created_at,a.id"""),
                {"tenant": d["a"], "phone": phone(981)})
            return [dict(row) for row in result.mappings()]

    try:
        yield d, turn, state, rows, redis, key
    finally:
        # Assertions inspect active holds before teardown; no cancellation is
        # hidden in this harness. Only fixture-owned auxiliary receipts/keys go.
        async with d["migrator"].begin() as db:
            await db.execute(text("DELETE FROM management_requests WHERE tenant_id IN (:a,:b)"),
                {"a": d["a"], "b": d["b"]})
        await redis.delete(key, key+":catalog-context", key+":presentation")
        await redis.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["professional", "price", "service_disabled", "professional_disabled"])
async def test_changed_proposal_replaces_own_old_hold_and_leaves_no_orphan_after_cancel(conversation, change):
    d, turn, state, rows, _, _ = conversation
    first = await turn(f"Quero corte em {d['day']} às 10:00 com Bruno")
    assert first["step"] == "confirm", first
    old_id = UUID((await state())["hold"])
    target, professional, cents = "corte", "Bruno", 3500
    if change == "professional":
        professional = "Breno"
    else:
        async with d["migrator"].begin() as db:
            if change == "price":
                await db.execute(text("UPDATE services SET price_cents=4500 WHERE tenant_id=:t AND id=:id"),
                    {"t": d["a"], "id": d["cut"]})
                cents = 4500
            elif change == "service_disabled":
                await db.execute(text("UPDATE services SET active=false WHERE tenant_id=:t AND id=:id"),
                    {"t": d["a"], "id": d["cut"]})
                target, cents = "barba", 2000
            else:
                await db.execute(text("UPDATE professionals SET active=false WHERE tenant_id=:t AND id=:id"),
                    {"t": d["a"], "id": d["pro"]})
                professional = "Breno"
        stale = await turn("Pode marcar")
        assert stale["step"] != "confirmed" and all(row["status"] != "confirmed" for row in await rows())
    replacement = await turn(f"Quero {target} em {d['day']} às 10:00 com {professional}")
    assert replacement["step"] == "confirm", replacement
    new_id = UUID((await state())["hold"])
    actual = {row["id"]: row for row in await rows()}
    assert new_id != old_id and actual[old_id]["status"] == "cancelled", actual
    assert actual[new_id]["status"] == "hold" and actual[new_id]["total_cents"] == cents
    assert sum(row["status"] == "hold" for row in actual.values()) == 1
    assert (await turn("Pode marcar"))["step"] == "confirmed"
    assert (await turn("Quero cancelar minha reserva"))["step"] == "cancel_confirm"
    assert (await turn("Pode cancelar"))["step"] == "cancelled"
    assert all(row["status"] == "cancelled" for row in await rows())


async def create_hold(d, *, professional=None, customer=None, hour=10, **kwargs):
    async with d["factory"]() as db:
        return await booking.book_appointment(db, d["a"], professional or d["pro"], [d["cut"]],
            customer or phone(981), "Cliente sintético", start_at(d["day"], hour), str(uuid4()),
            source="whatsapp_qr", **kwargs)


async def current_snapshot(d, identity):
    async with d["factory"]() as db:
        async with tenant_transaction(db, d["a"]):
            return booking.appointment_snapshot(await booking.read_appointment(db, d["a"], identity))


@pytest.mark.asyncio
@pytest.mark.parametrize("original", ["own_hold", "confirmed", "foreign_phone"])
async def test_inline_no_professional_preference_preserves_only_valid_replacement_until_date(conversation, original, monkeypatch):
    d, turn, state, rows, redis, key = conversation
    assert (await turn(f"Quero corte em {d['day']} às 10:00 com Bruno"))["step"] == "confirm"
    old_id = UUID((await state())["hold"])
    if original == "confirmed":
        async with d["factory"]() as db:
            await booking.confirm_hold(db, d["a"], old_id)
        saved = await state()
        saved["hold_snapshot"] = await current_snapshot(d, old_id)
        await redis.set(key, json.dumps(saved), ex=300)
    elif original == "foreign_phone":
        foreign = await create_hold(d, customer=phone(982), hour=14)
        saved = await state()
        saved.update(hold=str(foreign["id"]), hold_snapshot=foreign["expected_snapshot"],
            confirmation_expires_at=foreign["hold_expires_at"].timestamp())
        await redis.set(key, json.dumps(saved), ex=300)
    expected_replacement = qr_ai._replacement_hold(await state())
    checks, real_guard = [], booking._replacement_hold
    async def tracked_guard(db, tenant, professional, item_id, customer, snapshot, now, **kwargs):
        checks.append((tenant, item_id, customer, snapshot))
        return await real_guard(db, tenant, professional, item_id, customer, snapshot, now, **kwargs)
    monkeypatch.setattr(booking, "_replacement_hold", tracked_guard)
    before = await facts(d)
    question = await turn("Quero agendar barba, tanto faz o profissional")
    assert question["step"] == "date", question
    draft = await state()
    assert draft.get("replacement_hold") == expected_replacement
    assert not any(draft.get(name) for name in ("hold", "hold_snapshot", "confirmation_summary", "confirmation_message_id"))
    assert await facts(d) == before, "Choosing an unspecified day cannot release or confirm any appointment"
    offered = await turn("Em " + str(d["day"]))
    assert await facts(d) == before, "A date alone does not choose a clock or create a replacement"
    if offered["step"] == "time":
        result = await turn("08:00")
    else:
        assert original != "own_hold", offered
        result = offered
    assert (d["a"], UUID(expected_replacement["id"]), phone(981), expected_replacement["expected_snapshot"]) in checks
    if original != "own_hold":
        assert result["step"] != "confirm", result
        assert await facts(d) == before, "A current snapshot cannot authorize replacing confirmed or foreign records"
        return
    assert result["step"] == "confirm", result
    actual = {row["id"]: row for row in await rows()}
    new_id = UUID((await state())["hold"])
    assert new_id != old_id and actual[old_id]["status"] == "cancelled"
    assert actual[new_id]["status"] == "hold" and actual[new_id]["total_cents"] == 2000
    assert sum(row["status"] == "hold" for row in actual.values()) == 1
    assert (await turn("Pode marcar"))["step"] == "confirmed"
    assert (await turn("Quero cancelar minha reserva"))["step"] == "cancel_confirm"
    assert (await turn("Pode cancelar"))["step"] == "cancelled"
    assert all(row["status"] == "cancelled" for row in await rows())


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["foreign_phone", "foreign_tenant", "confirmed", "in_progress", "completed",
    "stale_snapshot", "wrong_original_professional", "expired"])
async def test_explicit_cross_professional_replacement_cannot_cancel_non_owned_or_non_hold_record(conversation, invalid):
    d, _, _, _, _, _ = conversation
    old = await create_hold(d)
    snapshot = old["expected_snapshot"]
    if invalid in {"confirmed", "in_progress", "completed"}:
        async with d["factory"]() as db:
            await booking.confirm_hold(db, d["a"], old["id"])
        if invalid == "in_progress":
            # The persisted status is still confirmed while the appointment
            # is underway; "in_progress" is not a database status enum.
            async with d["migrator"].begin() as db:
                now = datetime.now(timezone.utc)
                await db.execute(text("""UPDATE appointments SET
                    start_at=:start,end_at=:end,occupied_from=:occupied_from,occupied_until=:occupied_until
                    WHERE tenant_id=:t AND id=:id"""),
                    {"start": now-timedelta(minutes=5), "end": now+timedelta(minutes=35),
                     "occupied_from": now-timedelta(minutes=10), "occupied_until": now+timedelta(minutes=45),
                     "t": d["a"], "id": old["id"]})
        elif invalid == "completed":
            async with d["migrator"].begin() as db:
                await db.execute(text("UPDATE appointments SET status=:status WHERE tenant_id=:t AND id=:id"),
                    {"status": invalid, "t": d["a"], "id": old["id"]})
        # Use a current, correct snapshot: status itself must still forbid a
        # replacement, rather than merely relying on an outdated digest.
        snapshot = await current_snapshot(d, old["id"])
    before = await facts(d)
    async with d["factory"]() as db:
        with pytest.raises(HTTPException):
            await booking.book_appointment(db, d["b"] if invalid == "foreign_tenant" else d["a"],
                d["pro2"], [d["cut"]], phone(982) if invalid == "foreign_phone" else phone(981),
                "Cliente sintético", start_at(d["day"], 11), str(uuid4()),
                replace_hold_id=old["id"], expected_snapshot="f"*64 if invalid == "stale_snapshot" else snapshot,
                replace_hold_professional_id=d["pro2"] if invalid == "wrong_original_professional" else d["pro"],
                now=old["hold_expires_at"]+timedelta(seconds=1) if invalid == "expired" else None,
                source="whatsapp_qr")
    assert await facts(d) == before
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM appointments WHERE tenant_id=:t"),
            {"t": d["b"]})).scalar_one() == 0


@pytest.mark.asyncio
async def test_busy_alternative_preserves_old_hold_then_free_alternative_replaces_it_without_touching_other_contact(conversation):
    d, turn, state, rows, _, _ = conversation
    await turn(f"Quero corte em {d['day']} às 10:00 com Bruno")
    old_id = UUID((await state())["hold"])
    other = await create_hold(d, professional=d["pro2"], customer=phone(982))
    unavailable = await turn(f"Quero corte em {d['day']} às 10:00 com Breno")
    assert unavailable["step"] != "confirm"
    assert next(row for row in await rows() if row["id"] == old_id)["status"] == "hold"
    assert (await state())["replacement_hold"]["id"] == str(old_id)
    assert (await turn(f"Quero corte em {d['day']} às 11:00 com Breno"))["step"] == "confirm"
    actual = {row["id"]: row for row in await rows()}
    assert actual[old_id]["status"] == "cancelled" and sum(row["status"] == "hold" for row in actual.values()) == 1
    async with d["migrator"].connect() as db:
        unchanged = (await db.execute(text("SELECT status,hold_expires_at FROM appointments WHERE tenant_id=:t AND id=:id"),
            {"t": d["a"], "id": other["id"]})).mappings().one()
    assert unchanged["status"] == "hold" and unchanged["hold_expires_at"] == other["hold_expires_at"]


@pytest.mark.asyncio
async def test_cross_professional_replacement_failure_rolls_back_both_calendars_and_customer_records(conversation, monkeypatch):
    d, _, _, _, _, _ = conversation
    old = await create_hold(d)
    before = await facts(d)
    original = booking.history
    async def fail_after_insert(db, tenant, identity, event, **kwargs):
        await original(db, tenant, identity, event, **kwargs)
        if event == "hold_created":
            raise RuntimeError("Synthetic failure after replacement inserts")
    monkeypatch.setattr(booking, "history", fail_after_insert)
    with pytest.raises(RuntimeError, match="Synthetic failure"):
        await create_hold(d, professional=d["pro2"], hour=11, replace_hold_id=old["id"],
            expected_snapshot=old["expected_snapshot"], replace_hold_professional_id=d["pro"])
    assert await facts(d) == before


@pytest.mark.asyncio
async def test_confirmation_racing_with_cross_professional_replacement_has_one_atomic_winner(conversation):
    d, _, _, rows, _, _ = conversation
    old = await create_hold(d)
    async def confirm():
        async with d["factory"]() as db:
            try:
                return await booking.confirm_hold(db, d["a"], old["id"], customer_phone=phone(981),
                    expected_snapshot=old["expected_snapshot"])
            except HTTPException as error:
                return error.status_code
    async def replace():
        try:
            return await create_hold(d, professional=d["pro2"], hour=11, replace_hold_id=old["id"],
                expected_snapshot=old["expected_snapshot"], replace_hold_professional_id=d["pro"])
        except HTTPException as error:
            return error.status_code
    results = await asyncio.gather(confirm(), replace())
    assert sum(isinstance(result, dict) for result in results) == 1 and results.count(409) == 1
    actual = {row["id"]: row for row in await rows()}
    if actual[old["id"]]["status"] == "confirmed":
        assert len(actual) == 1
    else:
        assert actual[old["id"]]["status"] == "cancelled" and sum(row["status"] == "hold" for row in actual.values()) == 1


@pytest.mark.asyncio
async def test_cross_professional_replay_keeps_one_hold_and_rejects_changed_original_professional(conversation):
    d, _, _, _, _, _ = conversation
    old = await create_hold(d)
    operation = str(uuid4())
    async def replace(original_professional):
        async with d["factory"]() as db:
            return await booking.book_appointment(db, d["a"], d["pro2"], [d["cut"]], phone(981),
                "Cliente sintético", start_at(d["day"], 11), operation, replace_hold_id=old["id"],
                expected_snapshot=old["expected_snapshot"], replace_hold_professional_id=original_professional)
    first = await replace(d["pro"])
    before = await facts(d)
    replay = await replace(d["pro"])
    assert first["id"] == replay["id"] and first["hold_expires_at"] == replay["hold_expires_at"]
    assert await facts(d) == before
    with pytest.raises(HTTPException) as error:
        await replace(d["pro2"])
    assert error.value.status_code == 409 and await facts(d) == before
