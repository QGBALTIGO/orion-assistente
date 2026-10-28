"""Business hours are independent of professional booking availability."""
import pytest
from pydantic import ValidationError
from app.onboarding_schemas import BusinessOpeningHours, Hours


def week():
    return [
        {"weekday": i, "open": i < 5, **({
            "start_time": "09:00", "end_time": "18:00",
            "break_start": "12:00", "break_end": "13:00",
        } if i < 5 else {})}
        for i in range(7)
    ]


def test_company_hours_without_professional():
    model = BusinessOpeningHours.model_validate({"days": week()})
    assert len(model.days) == 7
    assert model.days[0].break_start.isoformat() == "12:00:00"
    assert model.days[6].open is False
    assert "professional_id" not in model.model_dump()


@pytest.mark.parametrize("change", [
    lambda d: d.pop(),
    lambda d: d[1].update(weekday=0),
    lambda d: d[0].update(start_time="19:00"),
    lambda d: d[0].update(break_start="08:00"),
    lambda d: d[0].update(break_end=None),
    lambda d: d[6].update(start_time="09:00"),
    lambda d: [row.update(open=False, start_time=None, end_time=None,
                           break_start=None, break_end=None) for row in d],
])
def test_reject_invalid_business_week(change):
    days=week()
    change(days)
    with pytest.raises(ValidationError):
        BusinessOpeningHours.model_validate({"days":days})


def test_legacy_staff_hours_contract_is_unchanged():
    with pytest.raises(ValidationError):
        Hours.model_validate({"weekdays":[0],"start_time":"09:00","end_time":"18:00"})
