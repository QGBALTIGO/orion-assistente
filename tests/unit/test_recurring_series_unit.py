"""Contrato unitário da agenda de clientes fixos — nenhum banco externo necessário."""
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
import pytest

from app.recurring_appointments import SeriesCreate, SeriesDraft, series_days


PROFESSIONAL = UUID("50000000-0000-4000-8000-000000000005")
SERVICE = UUID("60000000-0000-4000-8000-000000000006")
NOW = datetime(2030, 4, 1, 12, tzinfo=timezone.utc)
FRIDAY = date(2030, 4, 5)


def draft(**changes):
    value = {
        "professional_id": PROFESSIONAL, "service_ids": [SERVICE],
        "customer_name": "Cliente sintético", "customer_phone": "+5511999990000",
        "first_date": FRIDAY, "weekday": FRIDAY.weekday(), "local_time": time(14),
        "interval_weeks": 1, "occurrence_count": 3,
    }
    value.update(changes)
    return SeriesDraft(**value)


def test_three_fridays_reserve_exactly_three_dates_and_keep_local_time():
    rows = series_days(draft(), "America/Sao_Paulo", NOW)
    assert [(i, d) for i, d, _ in rows] == [
        (1, date(2030, 4, 5)), (2, date(2030, 4, 12)), (3, date(2030, 4, 19))]
    assert [local.hour for _, _, local in rows] == [14, 14, 14]
    assert all(local.tzinfo is not None for _, _, local in rows)


@pytest.mark.parametrize("weeks,days", [(1, 7), (2, 14), (4, 28)])
def test_intervals_are_exact_weeks(weeks, days):
    result = series_days(draft(interval_weeks=weeks), "America/Campo_Grande", NOW)
    assert (result[1][1] - result[0][1]).days == days


def test_largest_weekly_series_stays_inside_a_year():
    result = series_days(draft(occurrence_count=52), "America/Campo_Grande", NOW)
    assert len(result) == 52
    assert result[-1][1] - result[0][1] == timedelta(days=357)


def test_longer_than_365_days_is_rejected_instead_of_silently_truncated():
    with pytest.raises(HTTPException, match="365"):
        series_days(draft(interval_weeks=4, occurrence_count=15), "America/Sao_Paulo", NOW)


def test_first_date_must_be_on_selected_weekday():
    with pytest.raises(ValidationError, match="primeira data"):
        draft(weekday=0)


def test_duplicate_service_id_cannot_be_repeated():
    with pytest.raises(ValidationError, match="diferentes"):
        draft(service_ids=[SERVICE, SERVICE])


@pytest.mark.parametrize("count", [1, 53])
def test_count_bounds_reject_invalid_series(count):
    with pytest.raises(ValidationError):
        draft(occurrence_count=count)


def test_past_date_is_rejected():
    with pytest.raises(HTTPException, match="365"):
        series_days(draft(first_date=date(2029, 4, 6)), "America/Sao_Paulo", NOW)


def test_ambiguous_dst_hour_is_rejected():
    starting = date(2027, 11, 7)
    now = datetime(2027, 11, 1, 12, tzinfo=timezone.utc)
    with pytest.raises(HTTPException, match="ambíguo"):
        series_days(draft(first_date=starting, weekday=starting.weekday(),
                          local_time=time(1, 30), occurrence_count=2),
                    "America/New_York", now)


def test_nonexistent_dst_hour_is_rejected():
    starting = date(2027, 3, 14)
    now = datetime(2027, 3, 1, 12, tzinfo=timezone.utc)
    with pytest.raises(HTTPException, match="ambíguo"):
        series_days(draft(first_date=starting, weekday=starting.weekday(),
                          local_time=time(2, 30), occurrence_count=2),
                    "America/New_York", now)


def test_idempotency_key_must_be_valid():
    fields = draft().model_dump()
    with pytest.raises(ValidationError):
        SeriesCreate(**fields, idempotency_key="!")
    assert SeriesCreate(**fields, idempotency_key="c1c81a4b-7367-47a4-b01a-0054936c191e")
