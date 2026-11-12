"""Tests of REAL Orion booking-service pure functions (no database required)."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from app.booking_service import amounts, bounds, fingerprint, validate_local

TZ = ZoneInfo("America/Campo_Grande")


def item(duration, cents, prep=0, cleanup=0):
    return {"duration_min": duration, "price_cents": cents,
            "prep_min": prep, "cleanup_min": cleanup}


@pytest.mark.parametrize("duration", [10,15,20,30,40,45,60,75,90,120,180,240])
def test_real_engine_bounds_preserve_service_duration(duration):
    start = datetime(2026, 11, 20, 9, 0, tzinfo=TZ)
    result = bounds(start, [item(duration, 3500)])
    assert result["end_at"] - result["start_at"] == timedelta(minutes=duration)
    assert result["occupied_until"] - result["occupied_from"] == timedelta(minutes=duration)
    assert result["total_cents"] == 3500


def test_real_engine_peixoto_sixty_minute_combo_blocks_940():
    start = datetime(2026, 11, 20, 9, 0, tzinfo=TZ)
    first = bounds(start, [item(40, 4000), item(20, 2500)])
    next_start = datetime(2026, 11, 20, 9, 40, tzinfo=TZ).astimezone(timezone.utc)
    assert first["occupied_from"] < next_start < first["occupied_until"]
    assert first["total_cents"] == 6500
    assert first["end_at"] == start.astimezone(timezone.utc) + timedelta(minutes=60)


def test_real_engine_multiple_services_sum_prices_and_buffers():
    start = datetime(2026, 11, 20, 17, 0, tzinfo=TZ)
    chosen = [item(40, 4000, prep=5), item(20, 2500, cleanup=10)]
    assert amounts(chosen) == (60, 5, 10, 6500)
    result = bounds(start, chosen)
    assert result["occupied_from"] == start.astimezone(timezone.utc) - timedelta(minutes=5)
    assert result["occupied_until"] == result["end_at"] + timedelta(minutes=10)


def test_real_engine_overnight_duration_keeps_full_interval():
    start = datetime(2026, 11, 20, 23, 0, tzinfo=TZ)
    result = bounds(start, [item(180, 18000)])
    assert result["end_at"] - result["start_at"] == timedelta(hours=3)
    assert result["end_at"].astimezone(TZ).date() != result["start_at"].astimezone(TZ).date()


def test_real_engine_rejects_naive_time():
    with pytest.raises(HTTPException) as error:
        validate_local(datetime(2026, 11, 20, 9, 0), TZ)
    assert error.value.status_code == 422


def test_real_engine_rejects_wrong_timezone_offset():
    wrong_offset = datetime(2026, 11, 20, 9, 0,
                            tzinfo=timezone(timedelta(hours=-3)))
    with pytest.raises(HTTPException) as error:
        validate_local(wrong_offset, TZ)
    assert error.value.status_code == 422


def test_real_engine_fingerprint_is_deterministic_for_retries():
    prof = uuid4()
    service = uuid4()
    start = datetime(2026, 11, 20, 9, 0, tzinfo=TZ)
    first = fingerprint(prof, [service], start, "+5567900000000")
    assert first == fingerprint(prof, [service], start, "+5567900000000")
    assert first != fingerprint(prof, [service], start + timedelta(minutes=40),
                                "+5567900000000")