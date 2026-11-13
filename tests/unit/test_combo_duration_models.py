"""Combo duration fields accept only nullable integer-minute overrides."""
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.service_packages_api import PackageFields

FIRST = UUID("a0000000-0000-4000-8000-000000000001")
SECOND = UUID("a0000000-0000-4000-8000-000000000002")


def fields(**kwargs):
    return {"name": "Combo sintético", "service_ids": [FIRST, SECOND],
            "price_cents": 4200, **kwargs}


def test_automatic_is_default_and_nullable():
    assert PackageFields(**fields()).duration_override_min is None
    assert PackageFields(**fields(duration_override_min=None)).duration_override_min is None


@pytest.mark.parametrize("minutes", [1, 20, 45, 90, 1440])
def test_custom_minutes_are_exact(minutes):
    assert PackageFields(**fields(duration_override_min=minutes)).duration_override_min == minutes


@pytest.mark.parametrize("invalid", [0, -1, 1441, "60", 59.5, True])
def test_invalid_or_coerced_duration_is_rejected(invalid):
    with pytest.raises(ValidationError):
        PackageFields(**fields(duration_override_min=invalid))
