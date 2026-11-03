from datetime import date, datetime, time, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import ValidationError
import pytest

from app import booking_service as booking
from app import start_policies
from app.onboarding_service import _fitting_bindings
from app.plan_routing import feature_for_path
from app.start_policies import StartPolicyUpdate, open_periods, policy_response, validate_manual_times

TENANT, PROFESSIONAL, HOUR = UUID(int=1), UUID(int=2), UUID(int=3)
DAY = date(2026, 12, 7)
NOW = datetime(2026, 12, 6, tzinfo=timezone.utc)
SHOP = {"timezone": "UTC", "slot_step_minutes": 15, "min_notice_minutes": 0, "booking_horizon_days": 60}
HOURS = ({"id": HOUR, "start_time": time(8), "end_time": time(18)},)
PAUSES = ({"working_hour_id": HOUR, "start_time": time(12), "end_time": time(13)},)


def at(hour, minute=0):
    return datetime.combine(DAY, time(hour, minute), tzinfo=timezone.utc)


def item(duration=40, prep=0, cleanup=0):
    return {"duration_min": duration, "prep_min": prep, "cleanup_min": cleanup, "price_cents": 3500}


def constraints(*, times=(time(8), time(8, 40), time(9, 20)), mode="manual", reservations=(), pauses=PAUSES):
    return booking._SlotConstraints(TENANT, PROFESSIONAL, DAY, HOURS, pauses, (), reservations,
                                    {"mode": mode, "start_times": times, "version": 1})


class NoQueries:
    async def execute(self, *_args, **_kwargs):
        pytest.fail("A cached read-only grid must not issue per-candidate SQL")


def test_policy_payload_is_explicit_sorted_unique_and_minute_precision():
    policy = StartPolicyUpdate(mode="manual", start_times=["09:20", "08:40"], expected_version=0)
    assert policy.start_times == ["08:40", "09:20"]
    assert StartPolicyUpdate(mode="automatic", expected_version=1).start_times is None
    for values in (["08:40", "08:40"], ["8:40"], ["08:40:00"], ["24:00"], ["08:40-04:00"]):
        with pytest.raises(ValidationError):
            StartPolicyUpdate(mode="manual", start_times=values, expected_version=0)
    with pytest.raises(ValidationError):
        StartPolicyUpdate(mode="automatic", expected_version=True)
    assert policy_response(2) == {"weekday": 2, "mode": "automatic", "start_times": [], "version": 0}


def test_start_policy_routes_have_explicit_agenda_permission():
    for path in ("/professionals/{professional_id}/start-policies",
                 "/professionals/{professional_id}/start-policies/{weekday}"):
        assert feature_for_path(path) == "agenda.manage"


def test_period_union_preserves_gaps_and_subtracts_only_each_shifts_pauses():
    afternoon = {"id": UUID(int=4), "start_time": time(14), "end_time": time(18)}
    morning = {"id": HOUR, "start_time": time(8), "end_time": time(12)}
    pauses = ({"working_hour_id": HOUR, "start_time": time(10), "end_time": time(10, 30)},)
    assert open_periods((morning, afternoon), pauses) == (
        (time(8), time(10)), (time(10, 30), time(12)), (time(14), time(18)))
    validate_manual_times([time(8), time(10, 30), time(14)], (morning, afternoon), pauses)
    for values in ([], [time(10)], [time(12)], [time(13)], [time(18)]):
        with pytest.raises(HTTPException) as error:
            validate_manual_times(values, (morning, afternoon), pauses)
        assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_manual_membership_replaces_modulo_but_automatic_keeps_current_grid():
    result = await booking.check_slot(NoQueries(), TENANT, PROFESSIONAL, SHOP, [item()], at(8, 40), NOW,
                                      _constraints=constraints())
    assert result["end_at"] == at(9, 20)
    with pytest.raises(HTTPException) as error:
        await booking.check_slot(NoQueries(), TENANT, PROFESSIONAL, SHOP, [item()], at(8, 45), NOW,
                                  _constraints=constraints())
    assert error.value.status_code == 422
    automatic = constraints(mode="automatic")  # The saved list must be inactive.
    await booking.check_slot(NoQueries(), TENANT, PROFESSIONAL, SHOP, [item()], at(8, 45), NOW,
                              _constraints=automatic)
    with pytest.raises(HTTPException):
        await booking.check_slot(NoQueries(), TENANT, PROFESSIONAL, SHOP, [item()], at(8, 40), NOW,
                                  _constraints=automatic)


@pytest.mark.asyncio
async def test_manual_starts_preserve_real_duration_cleanup_and_opening_prep_clipping():
    policy = constraints(times=(time(8), time(11, 40), time(13), time(17, 40)))
    for hour in (8, 13):
        value = await booking.check_slot(NoQueries(), TENANT, PROFESSIONAL, SHOP,
                                         [item(40, prep=5, cleanup=10)], at(hour), NOW,
                                         _constraints=policy)
        assert value["occupied_from"] == at(hour)
        assert value["occupied_until"] == at(hour, 50)
    for hour in (11, 17):
        with pytest.raises(HTTPException) as error:
            await booking.check_slot(NoQueries(), TENANT, PROFESSIONAL, SHOP, [item(60)],
                                      at(hour, 40), NOW, _constraints=policy)
        assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_combo_filters_fixed_list_without_inventing_start_at_its_end(monkeypatch):
    policy = constraints(reservations=({"starts_at": at(8), "ends_at": at(9)},))

    async def read(*_args):
        return policy

    monkeypatch.setattr(booking, "_read_slot_constraints", read)
    slots, reason = await booking.verified_slot_grid(NoQueries(), TENANT, PROFESSIONAL, SHOP,
                                                    [item()], DAY, NOW)
    assert slots == [at(9, 20).isoformat()]
    assert at(9).isoformat() not in slots
    assert reason == "available"


@pytest.mark.asyncio
async def test_cached_policy_cannot_be_reused_for_another_professional():
    with pytest.raises(ValueError, match="another request scope"):
        await booking.check_slot(NoQueries(), TENANT, UUID(int=55), SHOP, [item()], at(8), NOW,
                                  _constraints=constraints())


@pytest.mark.asyncio
async def test_manual_grid_preserves_timezone_and_skips_nonexistent_local_time(monkeypatch):
    day = date(2027, 3, 14)  # New York's spring transition.
    schedule = ({"id": HOUR, "start_time": time(1), "end_time": time(5)},)
    scoped = booking._SlotConstraints(TENANT, PROFESSIONAL, day, schedule, (), (), (),
                                      {"mode": "manual", "start_times": [time(2, 20), time(3, 20)]})

    async def read(*_args):
        return scoped

    monkeypatch.setattr(booking, "_read_slot_constraints", read)
    shop = {**SHOP, "timezone": "America/New_York"}
    now = datetime(2027, 3, 13, tzinfo=timezone.utc)
    slots, _ = await booking.verified_slot_grid(NoQueries(), TENANT, PROFESSIONAL, shop,
                                               [item(20)], day, now)
    assert slots == [datetime.combine(day, time(3, 20), tzinfo=ZoneInfo("America/New_York")).isoformat()]


def test_onboarding_requires_an_actual_manual_start_that_fits_the_service():
    professional = {"id": PROFESSIONAL}
    service = {"id": UUID(int=6), "duration_min": 40, "prep_min": 5, "cleanup_min": 0, "price_cents": 3500}
    facts = {"links": [{"professional_id": PROFESSIONAL, "service_id": service["id"]}],
             "hours": [{"id": HOUR, "professional_id": PROFESSIONAL, "weekday": 0,
                        "start_time": time(8), "end_time": time(12)}], "breaks": [],
             "start_policies": [{"professional_id": PROFESSIONAL, "weekday": 0,
                                 "mode": "manual", "start_times": [time(11, 40)]}]}
    assert _fitting_bindings([professional], [service], facts) == (set(), set())
    facts["start_policies"][0]["start_times"] = [time(11, 20)]
    expected = ({str(service["id"])}, {str(PROFESSIONAL)})
    assert _fitting_bindings([professional], [service], facts) == expected
    facts["start_policies"][0].update(mode="automatic", start_times=[time(11, 40)])
    assert _fitting_bindings([professional], [service], facts) == expected
    facts["start_policies"][0].update(mode="manual", weekday=1)
    assert _fitting_bindings([professional], [service], facts) == expected


@pytest.mark.asyncio
async def test_schedule_guard_rereads_weekday_after_waiting_for_professional_lock(monkeypatch):
    state = {"professional_id": PROFESSIONAL, "weekday": 0}

    class Result:
        def mappings(self): return self
        def first(self): return dict(state)

    class DB:
        async def execute(self, *_args): return Result()

    async def acquire(_db, _tenant, professional):
        assert professional == PROFESSIONAL
        state["weekday"] = 2  # Another editor committed while this request waited.

    monkeypatch.setattr(booking, "acquire_professional_lock", acquire)
    scopes = await start_policies.lock_schedule_edit(DB(), TENANT, "working_hours", item_id=HOUR)
    assert scopes == {(PROFESSIONAL, 2)}


@pytest.mark.asyncio
async def test_pause_moved_to_unlocked_professional_requires_retry_without_lock_reordering(monkeypatch):
    state = {"professional_id": PROFESSIONAL, "weekday": 0}
    acquired = []

    class Result:
        def mappings(self): return self
        def first(self): return dict(state)

    class DB:
        async def execute(self, *_args): return Result()

    async def acquire(_db, _tenant, professional):
        acquired.append(professional)
        state["professional_id"] = UUID(int=99)

    monkeypatch.setattr(booking, "acquire_professional_lock", acquire)
    with pytest.raises(HTTPException) as error:
        await start_policies.lock_schedule_edit(DB(), TENANT, "working_breaks", item_id=HOUR)
    assert error.value.status_code == 409 and acquired == [PROFESSIONAL]
