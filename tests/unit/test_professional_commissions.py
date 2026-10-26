"""Pure money and model contracts for the Orion professional commission modes."""
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.professional_commissions import (
    CommissionRuleInput,
    ProfessionalConfiguration,
    calculate_commission,
    proportional_split,
)


def item(cost, **kwargs):
    return {"id": uuid4(), "price_snapshot_cents": cost, **kwargs}


def test_legacy_percent_rounds_the_full_booking_then_allocates_exactly():
    booking = calculate_commission(5500, [item(3500), item(2000)], "percent", Decimal("10"))
    assert booking["commission_cents"] == 550
    assert [entry["value"] for entry in booking["items"]] == [350, 200]
    assert booking["percent"] == 10
    assert all(entry["kind"] == "percent" for entry in booking["items"])


def test_mixed_percentage_and_fixed_amount_per_service():
    booking = calculate_commission(
        5500, [
            item(3500, commission_kind="percent", rate_percent=Decimal("10.00")),
            item(2000, commission_kind="fixed", commission_fixed_cents=1000),
        ],
        "per_service", 0,
    )
    assert booking["commission_cents"] == 1350
    assert [entry["value"] for entry in booking["items"]] == [350, 1000]
    assert booking["items"][0]["percent"] == Decimal("10")
    assert booking["items"][1]["fixed"] == 1000
    assert booking["percent"] is None


def test_discount_is_apportioned_for_percent_but_does_not_reduce_fixed_amount():
    booking = calculate_commission(
        4000, [
            item(2500, commission_kind="percent", rate_percent=Decimal("20")),
            item(2500, commission_kind="fixed", commission_fixed_cents=1000),
        ],
        "per_service", 0,
    )
    assert [entry["base"] for entry in booking["items"]] == [2000, 2000]
    assert [entry["value"] for entry in booking["items"]] == [400, 1000]
    assert booking["commission_cents"] == 1400


def test_variable_price_extra_applies_only_to_variable_service():
    # Corte stays R$ 35 even if alisamento varies from R$ 50 to R$ 120.
    booking = calculate_commission(
        15500, [
            item(3500, price_mode_snapshot="fixed",
                 commission_kind="percent", rate_percent=Decimal("10")),
            item(5000, price_mode_snapshot="from",
                 commission_kind="percent", rate_percent=Decimal("20")),
        ],
        "per_service", 0, gross_cents=15500,
    )
    assert [line["base"] for line in booking["items"]] == [3500, 12000]
    assert [line["value"] for line in booking["items"]] == [350, 2400]


def test_fixed_commission_can_exceed_service_sale_for_a_real_loss():
    booking = calculate_commission(
        500, [item(500, commission_kind="fixed", commission_fixed_cents=1000)],
        "per_service", 0,
    )
    assert booking["commission_cents"] == 1000


def test_unconfigured_generated_service_uses_professional_default_rate():
    booking = calculate_commission(
        10000, [item(10000, commission_kind=None)], "per_service", Decimal("7.5")
    )
    assert booking["commission_cents"] == 750
    assert booking["items"][0]["source"] == "default"


def test_zero_and_half_up_cents():
    assert calculate_commission(5, [item(5)], "percent", Decimal("10"))["commission_cents"] == 1
    assert calculate_commission(1000, [item(1000, commission_kind="percent",
                                           rate_percent=0)], "per_service", 0)["commission_cents"] == 0


def test_largest_remainder_keeps_every_cent_and_ties_stable():
    assert proportional_split(100, [1, 1, 1]) == [34, 33, 33]
    assert proportional_split(1, [1, 1]) == [1, 0]
    assert proportional_split(49, [0, 0]) == [0, 49]
    assert proportional_split(0, [1, 2]) == [0, 0]


def test_fails_closed_for_invalid_money_or_mode():
    with pytest.raises(ValueError):
        proportional_split(-1, [10])
    with pytest.raises(ValueError):
        proportional_split(20, [-3])
    with pytest.raises(ValueError):
        calculate_commission(100, [item(100)], "unknown", 10)


def test_config_requires_rate_on_every_selected_service_in_custom_mode():
    service = uuid4()
    with pytest.raises(ValidationError):
        ProfessionalConfiguration(name="Barbeiro", commission_mode="per_service",
                                  service_ids=[service], commission_rules=[])
    valid = ProfessionalConfiguration(
        name="Barbeiro", commission_mode="per_service", service_ids=[service],
        commission_rules=[CommissionRuleInput(
            service_id=service, commission_kind="fixed", commission_fixed_cents=1000
        )],
    )
    assert valid.commission_rules[0].commission_fixed_cents == 1000


def test_config_rejects_cross_service_and_duplicate_rules():
    first, second = uuid4(), uuid4()
    with pytest.raises(ValidationError):
        ProfessionalConfiguration(
            name="Ana", commission_mode="per_service", service_ids=[first],
            commission_rules=[
                CommissionRuleInput(service_id=second, commission_kind="percent", commission_percent=10),
            ],
        )
    with pytest.raises(ValidationError):
        ProfessionalConfiguration(name="Ana", service_ids=[first, first])
    with pytest.raises(ValidationError):
        CommissionRuleInput(service_id=first, commission_kind="fixed",
                            commission_fixed_cents=10, commission_percent=5)


def test_admin_ui_uses_atomic_configuration_route_and_mobile_controls():
    from pathlib import Path
    script = (Path(__file__).resolve().parents[2] / "app/static/orion-suite.js").read_text()
    styles = (Path(__file__).resolve().parents[2] / "app/static/orion-suite.css").read_text()
    assert "/professionals/commission-rules" in script
    assert "syncProfessionalCommissionControls" in script
    assert "data-os-commission-row" in script
    assert "commission_fixed_cents" in script
    assert "os-commission-service-controls" in styles
