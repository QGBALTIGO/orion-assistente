"""A confirmed booking is the authority for a reschedule reference.

Real domain engine, tenant RLS and Redis; the language boundary uses the
production deterministic planner. No provider, ASR or WhatsApp transport.
"""
from datetime import datetime
import json
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from app import qr_ai

from test_qr_complete_journeys_postgres import complete_journey, booking_db  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.mark.parametrize("identity,reference", [
    ("M28_same_professional", ", com o mesmo profissional"),
    ("M29_no_reference", ""),
])
async def test_reschedule_uses_current_owned_booking_after_confirmation(complete_journey, identity, reference):
    h = complete_journey
    await h.greeting()
    await h.booking(hour="10:15")
    original = await h.accept()
    state = await h.state()
    assert "professional" not in state and "services" not in state
    before = await h.tenant_agenda_snapshot()
    result = await h.say(f"Quero remarcar meu corte em {h.day} para as 11h15{reference}.", read_only=True)
    assert result["step"] == "reschedule_confirm", result
    state = await h.state()
    assert state["appointment"] == str(original["id"])
    assert state["professional"] == str(h.d["pro"])
    assert state["services"] == [str(h.d["cut"])]
    assert datetime.fromisoformat(state["new_start"]).strftime("%H:%M") == "11:15"
    assert await h.tenant_agenda_snapshot() == before
    unchanged = next(row for row in await h.rows() if row["id"] == original["id"])
    assert unchanged == original and unchanged["status"] == "confirmed"
    changed = await h.say("Pode remarcar", consent=True)
    assert changed["step"] == "rescheduled", changed
    moved = next(row for row in await h.rows() if row["id"] == original["id"])
    assert moved["professional_id"] == original["professional_id"]
    assert moved["start_at"].astimezone(ZoneInfo("America/Campo_Grande")).strftime("%H:%M") == "11:15"
    assert len(await h.rows()) == 1 and moved["status"] == "confirmed"


@pytest.mark.parametrize("identity,kind", [
    ("M28_cancelled", "cancelled"), ("M28_completed", "completed"),
    ("M28_past", "past"), ("M28_foreign_phone", "foreign_phone"),
])
async def test_noncurrent_or_unowned_booking_cannot_supply_staff(complete_journey, identity, kind):
    h = complete_journey
    await h.greeting()
    original = await h.background(hour="10:15", number=1902 if kind == "foreign_phone" else 1901)
    async with h.d["migrator"].begin() as db:
        if kind in {"cancelled", "completed"}:
            await db.execute(text("UPDATE appointments SET status=:status WHERE tenant_id=:t AND id=:id"),
                {"status": kind, "t": h.tenant, "id": original["id"]})
        elif kind == "past":
            await db.execute(text("""UPDATE appointments SET start_at=start_at-interval '14 days',
                end_at=end_at-interval '14 days',occupied_from=occupied_from-interval '14 days',
                occupied_until=occupied_until-interval '14 days' WHERE tenant_id=:t AND id=:id"""),
                {"t": h.tenant, "id": original["id"]})
    before = await h.tenant_agenda_snapshot()
    result = await h.say(f"Quero remarcar meu corte em {h.day} para as 11h15 com o mesmo profissional.", read_only=True)
    assert result["step"] == "no_reservation", result
    assert not h.slot_reads and await h.tenant_agenda_snapshot() == before


@pytest.mark.parametrize("identity", ["M28_multiple_targets"])
async def test_multiple_bookings_select_original_before_staff_and_preserve_requested_clock(complete_journey, identity):
    h = complete_journey
    await h.greeting()
    first = await h.background(hour="10:15", number=1901)
    second = await h.background(hour="13:15", number=1901, professional=h.d["pro2"])
    before = await h.tenant_agenda_snapshot()
    result = await h.say(f"Quero remarcar meu corte em {h.day} para as 11h15 com o mesmo profissional.", read_only=True)
    assert result["step"] == "reschedule_pick", result
    state = await h.state()
    assert set(state["candidate_ids"]) == {str(first["id"]), str(second["id"])}
    assert not state.get("professional") and not h.slot_reads
    result = await h.say("2", read_only=True)
    assert result["step"] == "reschedule_confirm", result
    state = await h.state()
    assert state["appointment"] == str(second["id"]) and state["professional"] == str(h.d["pro2"])
    assert datetime.fromisoformat(state["new_start"]).strftime("%H:%M") == "11:15"
    assert h.slot_reads[-1]["professional"] == str(h.d["pro2"])
    assert await h.tenant_agenda_snapshot() == before


@pytest.mark.parametrize("identity,foreign", [("M28_selected_foreign_phone", "phone"), ("M28_selected_foreign_tenant", "tenant")])
async def test_selected_foreign_target_cannot_reuse_staff_from_transient_state(complete_journey, identity, foreign):
    h = complete_journey
    await h.greeting()
    target = await h.background(hour="10:15", number=1902 if foreign == "phone" else 1901)
    before = await h.tenant_agenda_snapshot()
    source_tenant = h.tenant
    if foreign == "tenant":
        h.tenant = h.d["b"]
    await h.redis.set(h.key, json.dumps({"step": "reschedule_day", "appointment": str(target["id"]),
        "professional": str(h.d["pro"]), "services": [str(h.d["cut"])]}), ex=300)
    denied = await qr_ai.selected_reschedule(h.tenant, h.pn, target["id"], h.redis, h.key)
    assert denied["step"] == "no_reservation"
    result = await h.say(f"Quero remarcar meu corte em {h.day} para as 11h15 com o mesmo profissional.", read_only=True)
    assert result["step"] != "reschedule_confirm", result
    assert not h.slot_reads
    h.tenant = source_tenant
    assert await h.tenant_agenda_snapshot() == before
