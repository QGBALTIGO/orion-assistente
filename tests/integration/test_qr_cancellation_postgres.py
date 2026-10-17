"""Real PG/RLS cancellation contract for the isolated QR45 module.

Requires the existing booking integration fixture and runtime/migrator roles.
All tenants, contacts and appointments are synthetic and removed by that fixture.
"""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text

from app import qr_cancellation as flow, qr_customer, whatsapp_dialogue
from app.booking_service import book_appointment, confirm_hold, reschedule_appointment, cancel_appointment
from test_booking import booking_db, phone, start_at

pytestmark = pytest.mark.integration


class MemoryRedis:
    def __init__(self):
        self.data = {}

    async def set(self, key, value, ex=None):
        self.data[key] = value

    async def delete(self, key):
        self.data.pop(key, None)


@pytest_asyncio.fixture
async def customer_cancellation(booking_db, monkeypatch):
    d = booking_db
    sender = phone(845)
    monkeypatch.setattr(flow, "get_session_factory", lambda: d["factory"])
    monkeypatch.setattr(qr_customer, "get_session_factory", lambda: d["factory"])
    monkeypatch.setattr(whatsapp_dialogue, "get_session_factory", lambda: d["factory"])
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    redis = MemoryRedis()
    business = {"name": "Synthetic cancellation", "timezone": "America/Campo_Grande", "profile_id": "synthetic-current",
        "profile_revision": 1, "profile": {"capabilities": ["appointments"], "policies": [], "style_rules": {}}}
    async with d["factory"]() as db:
        record = await book_appointment(db, d["a"], d["pro"], [d["beard"]], sender,
            "Synthetic cancellation customer", start_at(d["day"], 10), "qr-cancel-contract")
        await confirm_hold(db, d["a"], record["id"])
    key = f"orion:qr:ai:{d['a']}:{sender[1:]}"
    async def message(body, *, message_id="synthetic-next", requested=False, preview=False, tenant=None, contact=None):
        t = tenant or d["a"]; pn = contact or sender[1:]
        target = f"orion:qr:ai:{t}:{pn}"
        state = json.loads(redis.data.get(target, "{}"))
        return await flow.handle(t, pn, body, business=business, state=state, redis=redis, key=target,
            message_id=message_id, requested=requested, preview=preview)
    async def persisted():
        async with d["migrator"].connect() as db:
            row = (await db.execute(text("SELECT status,start_at FROM appointments WHERE id=:id"), {"id": record["id"]})).mappings().one()
            cancelled = (await db.execute(text("SELECT count(*) FROM appointment_history WHERE appointment_id=:id AND event='cancelled'"), {"id": record["id"]})).scalar_one()
            outbox = (await db.execute(text("SELECT action FROM calendar_outbox WHERE appointment_id=:id"), {"id": record["id"]})).scalar_one_or_none()
            return dict(row), cancelled, outbox
    return SimpleNamespace(**locals())


@pytest.mark.asyncio
async def test_actual_qr_proposal_new_message_cancel_and_terminal_marker(customer_cancellation):
    h = customer_cancellation
    proposal = await h.message("Quero cancelar minha reserva", requested=True, message_id="synthetic-summary")
    assert proposal["step"] == "cancel_confirm" and "_verified_operation" not in proposal
    row, count, outbox = await h.persisted()
    assert row["status"] == "confirmed" and count == 0 and outbox == "create"
    result = await h.message("Pode cancelar")
    assert result["step"] == "cancelled" and result["_verified_operation"] is True
    assert h.record["protocol"] in result["answer"]
    row, count, outbox = await h.persisted()
    assert row["status"] == "cancelled" and count == 1 and outbox == "delete"
    assert await h.message("Pode cancelar", message_id="synthetic-after-completion") is None
    assert (await h.persisted())[1] == 1


@pytest.mark.asyncio
async def test_cancela_after_actual_owned_summary_is_deterministic_and_writes_once(customer_cancellation, monkeypatch):
    h = customer_cancellation
    async def forbidden(**_):
        raise AssertionError("An exact reply to the current cancellation summary needs no provider")
    monkeypatch.setattr(flow, "interpret_contextual_response", forbidden)
    await h.message("Quero cancelar minha reserva", requested=True, message_id="synthetic-summary")
    same = await h.message("Cancela", message_id="synthetic-summary")
    assert same["step"] == "cancel_confirm" and (await h.persisted())[1] == 0
    result = await h.message("Cancela", message_id="synthetic-cancel-consent")
    assert result["step"] == "cancelled" and result["_verified_operation"] is True
    row, count, outbox = await h.persisted()
    assert row["status"] == "cancelled" and count == 1 and outbox == "delete"
    assert await h.message("Cancela", message_id="synthetic-after-completion") is None
    assert (await h.persisted())[1] == 1


@pytest.mark.asyncio
async def test_declined_actual_cancellation_leaves_pg_identical_and_later_yes_has_no_authority(customer_cancellation):
    h = customer_cancellation
    await h.message("Quero cancelar minha reserva", requested=True, message_id="synthetic-summary")
    before = await _persisted_cancellation_contract(h)
    result = await h.message("Não cancela", message_id="synthetic-withdrawal")
    assert result["step"] == "cancel_declined" and "_verified_operation" not in result
    assert h.key not in h.redis.data
    assert await h.message("Sim", message_id="synthetic-unrelated-yes") is None
    assert await h.message("Cancela", message_id="synthetic-old-cancel") is None
    assert await _persisted_cancellation_contract(h) == before


@pytest.mark.asyncio
async def test_real_owned_records_are_not_visible_to_other_contact_or_tenant(customer_cancellation):
    h = customer_cancellation
    for params in ({"contact": phone(846)[1:]}, {"tenant": h.d["b"]}):
        result = await h.message("Cancelar " + h.record["protocol"], requested=True, **params)
        assert result["step"] == "no_reservation" and h.record["protocol"] not in result["answer"]
    row, count, outbox = await h.persisted()
    assert row["status"] == "confirmed" and count == 0 and outbox == "create"


@pytest.mark.asyncio
async def test_preview_and_unstructured_cancellation_policy_leave_pg_unchanged(customer_cancellation):
    h = customer_cancellation
    preview = await h.message("Quero cancelar minha reserva", requested=True, preview=True)
    assert preview["step"] == "preview_cancel" and h.redis.data == {}
    h.business["profile"]["policies"] = [{"topic": "Cancelamento", "description": "Prazo sujeito à análise da equipe."}]
    denied = await h.message("Quero cancelar minha reserva", requested=True)
    assert denied["step"] == "cancel_policy_review" and h.redis.data == {}
    row, count, outbox = await h.persisted()
    assert row["status"] == "confirmed" and count == 0 and outbox == "create"


@pytest.mark.asyncio
async def test_actual_mutation_between_proposal_and_consent_requires_new_summary(customer_cancellation):
    h = customer_cancellation
    await h.message("Quero cancelar minha reserva", requested=True, message_id="synthetic-summary")
    async with h.d["factory"]() as db:
        await reschedule_appointment(db, h.d["a"], h.record["id"], start_at(h.d["day"], 14))
    result = await h.message("Pode cancelar")
    assert result["step"] == "cancel_confirm" and "A reserva mudou" in result["answer"]
    row, count, outbox = await h.persisted()
    assert row["status"] == "confirmed" and row["start_at"] == start_at(h.d["day"], 14) and count == 0 and outbox == "update"


@pytest.mark.asyncio
async def test_backend_snapshot_rejects_race_after_module_preflight(customer_cancellation, monkeypatch):
    h = customer_cancellation
    await h.message("Quero cancelar minha reserva", requested=True, message_id="synthetic-summary")
    async def moved_before_write(db, tenant, item_id, **kwargs):
        async with h.d["factory"]() as other:
            await reschedule_appointment(other, tenant, item_id, start_at(h.d["day"], 14))
        return await cancel_appointment(db, tenant, item_id, **kwargs)
    monkeypatch.setattr(flow, "cancel_appointment", moved_before_write)
    result = await h.message("Pode cancelar")
    assert result["step"] == "cancel_review" and "_verified_operation" not in result
    row, count, outbox = await h.persisted()
    assert row["status"] == "confirmed" and row["start_at"] == start_at(h.d["day"], 14) and count == 0 and outbox == "update"


@pytest.mark.asyncio
async def test_customer_contact_revocation_after_summary_prevents_pg_cancel(customer_cancellation):
    h = customer_cancellation
    await h.message("Quero cancelar minha reserva", requested=True, message_id="synthetic-summary")
    async with h.d["migrator"].begin() as db:
        await db.execute(text("UPDATE customers SET whatsapp_e164=:phone WHERE tenant_id=:tenant AND id=:id"),
            {"phone": phone(847), "tenant": h.d["a"], "id": h.record["customer_id"]})
    result = await h.message("Pode cancelar")
    assert result["step"] == "no_reservation" and "_verified_operation" not in result
    row, count, outbox = await h.persisted()
    assert row["status"] == "confirmed" and count == 0 and outbox == "create"


async def _move_synthetic_booking_to_db_clock(h, seconds):
    """Move only the fixture record; keep its occupied interval consistent."""
    async with h.d["migrator"].begin() as db:
        await db.execute(text("""
            WITH target AS (
                SELECT clock_timestamp() + CAST(:seconds AS double precision) * interval '1 second' AS starts
            )
            UPDATE appointments SET start_at=target.starts,
                end_at=target.starts+interval '20 minutes', occupied_from=target.starts,
                occupied_until=target.starts+interval '20 minutes', updated_at=clock_timestamp()
            FROM target WHERE tenant_id=:tenant AND id=:id
        """), {"tenant": h.d["a"], "id": h.record["id"], "seconds": seconds})


async def _persisted_cancellation_contract(h):
    async with h.d["migrator"].connect() as db:
        appointment = (await db.execute(text("SELECT * FROM appointments WHERE id=:id"),
            {"id": h.record["id"]})).mappings().one()
        history = (await db.execute(text("SELECT * FROM appointment_history WHERE appointment_id=:id ORDER BY id"),
            {"id": h.record["id"]})).mappings().all()
        outbox = (await db.execute(text("SELECT * FROM calendar_outbox WHERE appointment_id=:id ORDER BY id"),
            {"id": h.record["id"]})).mappings().all()
        return dict(appointment), [dict(row) for row in history], [dict(row) for row in outbox]


@pytest.mark.asyncio
async def test_future_only_rejects_started_booking_without_writes_and_preserves_legacy_default(customer_cancellation):
    h = customer_cancellation
    await _move_synthetic_booking_to_db_clock(h, -60)
    record = await whatsapp_dialogue.owned_booking(h.d["a"], h.sender, item_id=h.record["id"])
    assert record is not None and record["status"] == "confirmed"
    before = await _persisted_cancellation_contract(h)
    async with h.d["factory"]() as db:
        with pytest.raises(HTTPException) as exc:
            await cancel_appointment(db, h.d["a"], record["id"], customer_phone=h.sender,
                expected_snapshot=record["expected_snapshot"], future_only=True)
    assert exc.value.status_code == 409
    assert await _persisted_cancellation_contract(h) == before

    # Existing staff/Meta callers retain the explicit legacy cancellation policy.
    async with h.d["factory"]() as db:
        cancelled = await cancel_appointment(db, h.d["a"], record["id"], customer_phone=h.sender,
            expected_snapshot=record["expected_snapshot"])
    assert cancelled["status"] == "cancelled"
    row, count, outbox = await h.persisted()
    assert row["status"] == "cancelled" and count == 1 and outbox == "delete"


@pytest.mark.asyncio
async def test_db_clock_crosses_start_after_qr_preflight_but_final_update_cannot_cancel(customer_cancellation, monkeypatch):
    h = customer_cancellation
    await _move_synthetic_booking_to_db_clock(h, 5)
    proposal = await h.message("Quero cancelar minha reserva", requested=True, message_id="synthetic-summary")
    assert proposal["step"] == "cancel_confirm"
    before = await _persisted_cancellation_contract(h)
    crossed = []

    async def cross_start_at_final_update(db, tenant, item_id, **kwargs):
        assert kwargs["future_only"] is True
        execute = db.execute

        async def guarded_execute(statement, parameters=None, *args, **extra):
            if str(statement).lstrip().startswith("UPDATE appointments SET status='cancelled'"):
                future = (await execute(text("SELECT start_at>clock_timestamp() FROM appointments WHERE id=:id"),
                    {"id": item_id})).scalar_one()
                assert future is True  # Both QR preflight and backend snapshot have passed.
                await execute(text("""
                    SELECT pg_sleep(GREATEST(0, EXTRACT(EPOCH FROM (start_at-clock_timestamp()))) + 0.02)
                    FROM appointments WHERE id=:id
                """), {"id": item_id})
                started = (await execute(text("SELECT start_at<=clock_timestamp() FROM appointments WHERE id=:id"),
                    {"id": item_id})).scalar_one()
                assert started is True
                crossed.append(item_id)
            return await execute(statement, parameters, *args, **extra)

        monkeypatch.setattr(db, "execute", guarded_execute)
        return await cancel_appointment(db, tenant, item_id, **kwargs)

    monkeypatch.setattr(flow, "cancel_appointment", cross_start_at_final_update)
    result = await h.message("Pode cancelar")
    assert crossed == [h.record["id"]]
    assert result["step"] == "cancel_review" and "_verified_operation" not in result
    assert await _persisted_cancellation_contract(h) == before
