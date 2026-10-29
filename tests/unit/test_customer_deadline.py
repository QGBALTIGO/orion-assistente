from datetime import datetime, timedelta, timezone

import pytest

from app.appointment_lifecycle import customer_can_change


@pytest.mark.parametrize("offset,expected", [(-1, True), (0, True), (1, False)])
def test_inclusive_server_deadline(offset, expected):
    start = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
    now = start - timedelta(minutes=60) + timedelta(microseconds=offset)
    assert customer_can_change({"status": "confirmed", "start_at": start}, now) is expected


@pytest.mark.parametrize("status", ["cancelled", "completed", "no_show", "hold"])
def test_terminal_states_are_never_customer_mutable(status):
    now = datetime.now(timezone.utc)
    assert not customer_can_change({"status": status, "start_at": now+timedelta(days=1)}, now)


def test_client_naive_clock_is_rejected():
    with pytest.raises(ValueError):
        customer_can_change({"status": "confirmed", "start_at": datetime.now(timezone.utc)}, datetime.now())
