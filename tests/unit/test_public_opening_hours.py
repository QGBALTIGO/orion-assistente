"""Hours shown on the public site come only from actual bookable team schedules."""
from datetime import time
from app.public_site_api import public_opening_hours, HEADERS


def hour(weekday, start, end):
    return {"weekday": weekday, "start_time": time.fromisoformat(start),
            "end_time": time.fromisoformat(end)}


def test_staff_intervals_merge_overlapping_and_adjacent_shifts_without_faking_gaps():
    result = public_opening_hours([
        hour(0, "08:00", "12:00"),
        hour(0, "10:00", "14:00"),
        hour(0, "15:00", "18:00"),
        hour(0, "18:00", "20:00"),
        hour(5, "09:00", "13:00")
    ])
    assert len(result) == 7
    assert result[0] == {
        "weekday": 0,
        "intervals": [{"open": "08:00", "close": "14:00"},
                      {"open": "15:00", "close": "20:00"}]
    }
    assert result[5]["intervals"] == [{"open": "09:00", "close": "13:00"}]
    for weekday in (1, 2, 3, 4, 6):
        assert result[weekday]["intervals"] == []


def test_empty_hours_never_invent_business_opening_times():
    result = public_opening_hours([])
    assert len(result) == 7
    assert all(not item["intervals"] for item in result)


def test_hours_no_employee_ids_and_external_iframe_is_strictly_limited():
    result = public_opening_hours([hour(2, "10:30", "17:00")])
    assert set(result[2]) == {"weekday", "intervals"}
    assert set(result[2]["intervals"][0]) == {"open", "close"}
    csp = HEADERS["Content-Security-Policy"]
    assert "frame-src https://www.google.com https://maps.google.com;" in csp
    assert "frame-ancestors 'none'" in csp
    assert "script-src 'self'" in csp
