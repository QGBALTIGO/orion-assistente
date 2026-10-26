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


def test_lunch_and_other_configured_breaks_split_real_shifts():
    rows = []
    for begin, end in (("12:00","13:00"),("15:00","15:30")):
        rows.append({
            **hour(0,"09:00","18:00"),
            "working_hour_id":"person-one-monday",
            "break_start":time.fromisoformat(begin),
            "break_end":time.fromisoformat(end),
        })
    result = public_opening_hours(rows)
    assert result[0]["intervals"] == [
        {"open":"09:00","close":"12:00"},
        {"open":"13:00","close":"15:00"},
        {"open":"15:30","close":"18:00"},
    ]
    assert "working_hour_id" not in str(result)


def test_break_closes_business_only_when_all_bookable_staff_are_on_break():
    rows = [
        {**hour(0,"09:00","18:00"), "working_hour_id":"barber-1",
         "break_start":time(12,0),"break_end":time(13,0)},
        {**hour(0,"09:00","18:00"), "working_hour_id":"barber-2"},
    ]
    # One barber remains available during the other's lunch break.
    assert public_opening_hours(rows)[0]["intervals"] == [
        {"open":"09:00","close":"18:00"}
    ]
    # When both are on break, the public schedule shows the actual gap.
    rows[1].update({"break_start":time(12,0),"break_end":time(13,0)})
    assert public_opening_hours(rows)[0]["intervals"] == [
        {"open":"09:00","close":"12:00"},
        {"open":"13:00","close":"18:00"},
    ]


def test_breaks_are_clipped_to_the_parent_shift_not_exposed_across_days():
    result = public_opening_hours([
        {**hour(5,"10:00","14:00"),"working_hour_id":"sat",
         "break_start":time(8,0),"break_end":time(11,0)}
    ])
    assert result[5]["intervals"] == [{"open":"11:00","close":"14:00"}]
    assert not result[6]["intervals"]
