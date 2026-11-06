"""Native choices use only backend slots actually listed by the formatter."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app import qr_assistant_interactions as choices
from app import qr_presentation as presentation

TENANT, SERVICE, PROFESSIONAL = UUID(int=42), UUID(int=7), UUID(int=8)
PHONE = "5567999999901"
ZONE, DAY = "America/Cuiaba", "2026-11-07"
SLOTS = ["2026-11-07T13:00:00Z", "2026-11-07T14:15:00Z", "2026-11-07T15:15:00Z",
         "2026-11-07T16:30:00Z", "2026-11-07T17:45:00Z", "2026-11-07T19:00:00Z",
         "2026-11-07T20:00:00Z", "2026-11-07T21:15:00Z"]
CLOCKS = ["09:00", "10:15", "11:15", "12:30", "13:45", "15:00", "16:00", "17:15"]
FOOTER = "Disponibilidade conferida novamente ao reservar."


class Redis:
    def __init__(self): self.data = {}; self.writes = []
    async def get(self, key): return self.data.get(key)
    async def set(self, key, value, **kwargs):
        self.data[key] = value; self.writes.append(key); return True
    async def delete(self, *keys):
        for key in keys: self.data.pop(key, None)


def formatted(slots=SLOTS, **kwargs):
    return presentation.render_slots(["Corte"], slots, timezone=ZONE, day=DAY,
        professional="Bruno", total_cents=4000, duration_min=40, **kwargs)


def offered_bodies(result, record):
    # Redis JSON sorts digest keys; the row IDs, rather than map order, bind
    # every displayed option to its canonical decision.
    return [record["choices"][choices._digest(row["id"])]["body"]
            for row in result["presentation"]["action"]["sections"][0]["rows"]]


async def project(monkeypatch, answer, *, step="time", state=None, capable=True, zone=ZONE):
    redis = Redis(); key = f"orion:qr:ai:{TENANT}:{PHONE}"
    state = state if state is not None else {"step": step, "slots": SLOTS, "day": DAY,
        "services": [str(SERVICE)], "professional": str(PROFESSIONAL)}
    original = deepcopy(state)
    await redis.set(key, json.dumps(state)); redis.writes.clear()
    async def tenant_zone(actual_tenant):
        assert actual_tenant == TENANT
        return ZoneInfo(zone) if zone is not None else None
    monkeypatch.setattr(choices, "_timezone", tenant_zone)
    result = await choices.present(redis, TENANT, PHONE, PHONE, {"answer": answer, "step": step},
        choices.Capabilities(assistant_interactions=1) if capable else None)
    assert json.loads(await redis.get(key)) == original
    assert all(written == key+":presentation" for written in redis.writes)
    record = json.loads(await redis.get(key+":presentation")) if await redis.get(key+":presentation") else None
    return result, record


@pytest.mark.parametrize("step", ["time", "reschedule_time"])
async def test_real_formatter_produces_native_choices_for_both_time_states(monkeypatch, step):
    answer = "Aceitamos Pix conforme cadastro.\n\n" + formatted()
    assert "09h • 10h15 • 11h15 • 12h30" in answer
    assert "13h45 • 15h • 16h • 17h15" in answer
    result, record = await project(monkeypatch, answer, step=step)
    assert "presentation" in result
    action = result["presentation"]["action"]
    assert action["type"] == "list" and action["description"] == answer+"\n\n"+FOOTER
    assert action["footer"] == ""
    assert [row["title"] for row in action["sections"][0]["rows"]] == CLOCKS
    assert offered_bodies(result, record) == CLOCKS
    assert all(spec["kind"] == "text" for spec in record["choices"].values())
    assert all(row["id"].startswith("oa1_") for row in action["sections"][0]["rows"])


@pytest.mark.parametrize("style", ["warm", "professional", "relaxed", "direct"])
@pytest.mark.parametrize("length", ["short", "balanced", "detailed"])
@pytest.mark.parametrize("emoji", ["none", "light"])
async def test_display_preferences_keep_the_same_verified_slot_choices(monkeypatch, style, length, emoji):
    answer = formatted(preferences={"style": style, "length": length, "emoji": emoji})
    result, _ = await project(monkeypatch, answer)
    action = result["presentation"]["action"]
    assert action["description"] == answer+"\n\n"+FOOTER and action["footer"] == ""
    assert [row["title"] for row in action["sections"][0]["rows"]] == CLOCKS


async def test_only_displayed_subset_is_offered_and_duplicate_instants_do_not_duplicate_rows(monkeypatch):
    state = {"step": "time", "slots": [*SLOTS, SLOTS[0]], "day": DAY}
    result, record = await project(monkeypatch, formatted(SLOTS[:3]), state=state)
    assert [row["title"] for row in result["presentation"]["action"]["sections"][0]["rows"]] == CLOCKS[:3]
    assert offered_bodies(result, record) == CLOCKS[:3]


@pytest.mark.parametrize("answer", [
    "O Pix funciona às 10h15; isso é uma FAQ, não uma consulta de horários.",
    "O expediente começa às 09:00 e termina às 17:15.",
    "FAQ de pagamento:\n10h15",
    "FAQ de pagamento:\n09:00",
    "109h • 10h150 • 11h15min",
    "109:00 • 10:150",
    "*Sábado, 07/11/2026*\n109h • 10h150",
    "*Sábado, 07/11/2026*\n09h • 09h45",  # not a real candidate
    "*Domingo, 08/11/2026*\n09h • 10h15",
])
async def test_faq_substrings_unavailable_slots_or_wrong_day_do_not_become_choices(monkeypatch, answer):
    result, record = await project(monkeypatch, answer)
    assert "presentation" not in result and record is None
    assert result["answer"] == answer


async def test_existing_numbered_colon_producer_still_projects_exact_values(monkeypatch):
    answer = "Encontrei estes horários livres para 07/11:\n" + "\n".join(
        f"{index}. {clock}" for index, clock in enumerate(CLOCKS, 1)) + "\nQual horário prefere?"
    state = {"step": "time", "slots": SLOTS, "day": DAY, "slot_menu_numbered": True}
    result, record = await project(monkeypatch, answer, state=state)
    assert [row["title"] for row in result["presentation"]["action"]["sections"][0]["rows"]] == CLOCKS
    assert offered_bodies(result, record) == CLOCKS


async def test_existing_colon_only_list_is_not_expanded_to_new_times(monkeypatch):
    answer = "Encontrei estes horários livres para 07/11:\n09:00 • 10:15\nQual horário prefere?"
    result, record = await project(monkeypatch, answer)
    assert offered_bodies(result, record) == CLOCKS[:2]
    assert result["presentation"]["action"]["description"] == answer+"\n\n"+FOOTER
    assert result["presentation"]["action"]["footer"] == ""


@pytest.mark.parametrize("slots", [None, "09h", {}, ["not-an-instant"], ["2026-11-07T09:00:00"],
    ["2026-11-07T13:00:30Z"], ["2026-11-07T13:00:00.000001Z"], [True], ["2026-11-08T13:00:00Z"],
    SLOTS + ["2026-11-07T13:00:00"], SLOTS * 126])
async def test_invalid_unknown_or_wrong_day_instants_fail_closed_without_state_writes(monkeypatch, slots):
    result, record = await project(monkeypatch, formatted(), state={"step": "time", "slots": slots, "day": DAY})
    assert "presentation" not in result and record is None


@pytest.mark.parametrize("day", ["2026-11-08", "not-a-day", None])
async def test_explicit_day_mismatch_or_invalid_day_cannot_offer_wrong_query(monkeypatch, day):
    state = {"step": "time", "slots": SLOTS, "day": day}
    result, _ = await project(monkeypatch, formatted(), state=state)
    if day is None:
        assert len(result["presentation"]["action"]["sections"][0]["rows"]) == 8
    else:
        assert "presentation" not in result


async def test_tenant_timezone_converts_utc_before_matching_displayed_values(monkeypatch):
    result, record = await project(monkeypatch, formatted())
    assert offered_bodies(result, record)[0] == "09:00"
    assert "13:00" not in [row["title"] for row in result["presentation"]["action"]["sections"][0]["rows"]]
    result, _ = await project(monkeypatch, formatted(), zone=None)
    assert "presentation" not in result


async def test_native_list_has_at_most_ten_rows_from_actual_displayed_instants(monkeypatch):
    start = datetime(2026, 11, 7, 13, tzinfo=timezone.utc)
    slots = [(start+timedelta(minutes=15*i)).isoformat() for i in range(12)]
    result, record = await project(monkeypatch, formatted(slots), state={"step": "time", "slots": slots, "day": DAY})
    rows = result["presentation"]["action"]["sections"][0]["rows"]
    assert len(rows) == len(record["choices"]) == 10
    assert [row["title"] for row in rows] == ["09:00", "09:15", "09:30", "09:45", "10:00", "10:15", "10:30", "10:45", "11:00", "11:15"]


@pytest.mark.parametrize("units", [1000, 1001])
async def test_full_factual_prefix_uses_existing_utf16_bound_instead_of_truncation(monkeypatch, units):
    body = formatted(SLOTS[:1])
    remaining = units - presentation.message_units(body) - 2
    prefix = "😀"*(remaining//2) + "x"*(remaining % 2)
    answer = prefix+"\n\n"+body
    assert presentation.message_units(answer) == units
    result, record = await project(monkeypatch, answer)
    assert "presentation" not in result and record is None
    if units == 1000:
        assert result["answer"] == answer+"\n\n"+FOOTER
    else:
        assert result["answer"] == answer


@pytest.mark.parametrize("combined_units", [1000, 1001])
async def test_combined_footer_limit_keeps_all_facts_and_never_issues_hidden_choices(monkeypatch, combined_units):
    body = formatted(SLOTS[:1])
    remaining = combined_units - presentation.message_units(body+"\n\n"+FOOTER) - 2
    answer = "😀"*(remaining//2) + "x"*(remaining % 2) + "\n\n" + body
    complete = answer+"\n\n"+FOOTER
    assert presentation.message_units(complete) == combined_units
    result, record = await project(monkeypatch, answer)
    if combined_units == 1000:
        action = result["presentation"]["action"]
        assert action["description"] == complete and action["footer"] == ""
        assert offered_bodies(result, record) == CLOCKS[:1]
        assert result["answer"] == answer
    else:
        assert result == {"answer": complete, "step": "time"}
        assert record is None


@pytest.mark.parametrize("capable,state_step", [(False, "time"), (True, "menu")])
async def test_existing_capability_and_current_step_guards_are_unchanged(monkeypatch, capable, state_step):
    result, record = await project(monkeypatch, formatted(), capable=capable,
        state={"step": state_step, "slots": SLOTS, "day": DAY})
    assert "presentation" not in result and record is None


def test_pure_projection_rejects_invalid_zone_and_ambiguous_local_clock():
    assert presentation.displayed_slot_clocks(formatted(), SLOTS, timezone="Unknown/Zone", day=DAY) == ()
    slots = ["2026-11-01T05:30:00Z", "2026-11-01T06:30:00Z"]
    answer = "*Domingo, 01/11/2026*\n01h30"
    assert presentation.displayed_slot_clocks(answer, slots, timezone="America/New_York", day="2026-11-01") == ()
