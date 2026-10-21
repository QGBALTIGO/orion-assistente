"""Scoped spoken rescheduling against synthetic PostgreSQL appointments only."""
from datetime import timedelta

import pytest
from sqlalchemy import text

from app import booking_service as booking
from test_booking import booking_db, phone, start_at
from test_qr_hold_recovery_postgres import facts, qr_route

pytestmark = pytest.mark.integration


async def confirmed(d, pro, day, hour, minute, identity, *, customer=None):
    async with d["factory"]() as db:
        row = await booking.book_appointment(db, d["a"], pro, [d["cut"]], customer or phone(940),
            "Cliente sintético", start_at(day, hour, minute), identity)
    async with d["factory"]() as db:
        return await booking.confirm_hold(db, d["a"], row["id"], customer_phone=customer or phone(940),
            expected_snapshot=row["expected_snapshot"])


async def scenario(d, monkeypatch, *, source_customer=None):
    message, state, redis, key = await qr_route(d, monkeypatch)
    target_day = d["day"] + timedelta(days=1)
    # The fixture's other weekday has no schedule until this real synthetic
    # registration. No model or hardcoded global opening hours authorizes it.
    async with d["migrator"].begin() as db:
        await db.execute(text("""INSERT INTO working_hours (tenant_id,professional_id,weekday,start_time,end_time)
            VALUES (:t,:pro,:weekday,'08:00','18:00'),(:t,:other,:weekday,'08:00','18:00')"""),
            {"t": d["a"], "pro": d["pro"], "other": d["pro2"], "weekday": target_day.weekday()})
    originals = [await confirmed(d, d["pro2"], d["day"], hour, 15, f"original-{hour}") for hour in range(8, 15)]
    # A booking already at the DESTINATION must never become the selected SOURCE.
    originals.append(await confirmed(d, d["pro2"], target_day, 16, 0, "original-at-destination"))
    source = await confirmed(d, d["pro"], d["day"], 15, 30, "new-synthetic-source", customer=source_customer)
    body = (f"Quero remarcar o corte com o Bruno do dia {d['day']} às 3 e meia "
            f"para o dia {target_day} às 4 da tarde.")
    return message, state, body, source, originals, target_day


async def originals_snapshot(d, source_id):
    async with d["migrator"].connect() as db:
        result = {}
        for table, field in (("appointments", "id"), ("appointment_items", "appointment_id"),
                             ("appointment_history", "appointment_id"), ("calendar_outbox", "appointment_id")):
            result[table] = [tuple(row) for row in (await db.execute(text(
                f"SELECT * FROM {table} WHERE tenant_id=:t AND {field}<>:source ORDER BY id"),
                {"t": d["a"], "source": source_id})).all()]
        result["customers"] = [tuple(row) for row in (await db.execute(text(
            "SELECT * FROM customers WHERE tenant_id=:t ORDER BY id"), {"t": d["a"]})).all()]
        return result


@pytest.mark.asyncio
async def test_actual_scoped_request_readonly_then_fresh_consent_moves_only_new_source_with_eight_originals_intact(booking_db, monkeypatch):
    d = booking_db
    message, state, body, source, originals, target_day = await scenario(d, monkeypatch)
    before = await facts(d);preserved = await originals_snapshot(d, source["id"])
    proposal = await message(body, "pg-source-destination-request")
    assert proposal["step"] == "reschedule_confirm" and state()["appointment"] == str(source["id"])
    assert "16h" in proposal["answer"] and target_day.strftime("%d/%m/%Y") in proposal["answer"]
    assert "Bruno" in proposal["answer"] and "R$ 35,00" in proposal["answer"]
    assert state()["expected_snapshot"] and state()["confirmation_message_id"] == "pg-source-destination-request"
    assert await facts(d) == before  # No hold, movement, cancellation, customer or event write.
    done = await message("Sm", "pg-source-destination-fresh-consent")
    assert done["step"] == "rescheduled" and done["_verified_operation"] is True
    assert await originals_snapshot(d, source["id"]) == preserved
    async with d["migrator"].connect() as db:
        row = (await db.execute(text("SELECT start_at,status,protocol FROM appointments WHERE tenant_id=:t AND id=:id"),
            {"t": d["a"], "id": source["id"]})).mappings().one()
        assert row["start_at"] == start_at(target_day, 16) and row["status"] == "confirmed" and row["protocol"] == source["protocol"]
        assert (await db.execute(text("SELECT count(*) FROM appointment_history WHERE appointment_id=:id AND event='rescheduled'"),
            {"id": source["id"]})).scalar_one() == 1
        assert (await db.execute(text("SELECT count(*) FROM appointments WHERE tenant_id=:t"), {"t": d["a"]})).scalar_one() == len(originals)+1


@pytest.mark.asyncio
async def test_actual_foreign_contact_origin_never_uses_own_destination_or_first_booking(booking_db, monkeypatch):
    d = booking_db
    message, state, body, source, _, _ = await scenario(d, monkeypatch, source_customer=phone(941))
    before = await facts(d)
    reply = await message(body, "pg-foreign-source-request")
    assert reply["step"] == "reschedule_pick" and not state().get("appointment")
    await message("Sm", "pg-foreign-source-no-authority")
    assert await facts(d) == before


@pytest.mark.asyncio
async def test_actual_occupied_destination_keeps_original_and_returns_no_authorizable_summary(booking_db, monkeypatch):
    d = booking_db
    message, state, body, source, _, target_day = await scenario(d, monkeypatch)
    await confirmed(d, d["pro"], target_day, 16, 0, "foreign-occupied-target", customer=phone(941))
    before = await facts(d)
    reply = await message(body, "pg-occupied-target-request")
    assert reply["step"] == "reschedule_time" and state()["appointment"] == str(source["id"])
    assert not state().get("expected_snapshot") and not state().get("confirmation_summary")
    assert await facts(d) == before
    await message("Sm", "pg-occupied-target-no-authority")
    assert await facts(d) == before


@pytest.mark.asyncio
async def test_actual_two_owned_source_matches_require_selection_before_new_summary(booking_db, monkeypatch):
    d = booking_db
    message, state, body, source, _, target_day = await scenario(d, monkeypatch)
    other = await confirmed(d, d["pro"], d["day"], 10, 0, "second-own-source-match")
    body = body.replace(" às 3 e meia", "")
    before = await facts(d)
    reply = await message(body, "pg-ambiguous-source-request")
    assert reply["step"] == "reschedule_pick" and set(state()["candidate_ids"]) == {str(source["id"]), str(other["id"])}
    assert not state().get("appointment") and await facts(d) == before
    selected = await message(source["protocol"], "pg-explicit-source-selection")
    assert selected["step"] == "reschedule_confirm" and state()["appointment"] == str(source["id"])
    assert state()["day"] == target_day.isoformat() and "16h" in selected["answer"]
    assert await facts(d) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("incorrect", ["date", "unknown_service"])
async def test_actual_short_source_correction_preserves_desire_but_not_authority_or_other_appointments(booking_db, monkeypatch, incorrect):
    d = booking_db
    message, state, body, source, _, target_day = await scenario(d, monkeypatch)
    wrong = body.replace(str(d["day"]), str(d["day"]+timedelta(days=2)), 1) if incorrect == "date" else body.replace("o corte", "a massagem")
    before = await facts(d);preserved = await originals_snapshot(d, source["id"])
    answer = await message(wrong, "pg-source-needs-correction")
    assert answer["step"] == "reschedule_pick" and state()["reschedule_source_query"]
    assert not state().get("appointment") and not state().get("expected_snapshot") and await facts(d) == before
    short = f"{d['day']:%d/%m} às três e meia com Bruno"
    answer = await message(short, "pg-short-source-correction")
    if incorrect == "unknown_service":
        assert answer["step"] == "reschedule_pick" and "serviço" in answer["answer"]
        assert await facts(d) == before
        answer = await message("É corte", "pg-explicit-source-service-correction")
    assert answer["step"] == "reschedule_confirm" and state()["appointment"] == str(source["id"])
    assert state()["day"] == target_day.isoformat() and "16h" in answer["answer"] and await facts(d) == before
    done = await message("Sm", "pg-corrected-source-new-consent")
    assert done["step"] == "rescheduled" and await originals_snapshot(d, source["id"]) == preserved
