"""Pure commission policy calculations: fixed amount, %, split, discounts, packages."""
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.commission_policies import (
    calculate_service_commissions, money_parts, split_appointment_services,
)
from app.management_schemas import CommissionPlanUpdate


A, B, C = (str(uuid4()) for _ in range(3))


def base(identity=A, amount=10000, name="Corte"):
    return {"service_id": identity, "name_snapshot": name,
            "price_snapshot_cents": amount, "catalog_kind": "base",
            "catalog_snapshot": None}


def test_largest_remainder_is_exact_and_stable():
    components = [{"weight_cents": 100}, {"weight_cents": 100}, {"weight_cents": 100}]
    assert money_parts(components, 100) == [34, 33, 33]
    assert money_parts(components, 0) == [0, 0, 0]
    assert money_parts([{"weight_cents": 0}, {"weight_cents": 0}], 3) == [2, 1]
    assert money_parts([{"weight_cents": 10000}, {"weight_cents": 5000}], 12000) == [8000, 4000]


def test_percent_and_fixed_cents_for_two_services_after_discount():
    items = [base(A, 10000), base(B, 5000, "Barba")]
    policy = {A: {"kind": "percent", "percent": "10.00"},
              B: {"kind": "fixed", "fixed_cents": 1000}}
    earned, details = calculate_service_commissions(items, policy, 12000)
    assert earned == 1800
    assert [x["service_net_cents"] for x in details] == [8000, 4000]
    assert [x["commission_cents"] for x in details] == [800, 1000]
    assert sum(x["service_net_cents"] for x in details) == 12000


def test_per_service_fixed_never_exceeds_actual_net_revenue():
    earned, detail = calculate_service_commissions([base(A, 500)], {
        A: {"kind": "fixed", "fixed_cents": 1000}}, 500)
    assert earned == detail[0]["commission_cents"] == 500
    earned, detail = calculate_service_commissions([base(A, 10000)], {
        A: {"kind": "fixed", "fixed_cents": 1000}}, 0)
    assert earned == detail[0]["commission_cents"] == 0


def test_combo_and_variant_use_original_component_prices_not_new_catalog_prices():
    snapshot = {"kind": "variant", "items": [
        {"service_id": str(uuid4()), "name": "Combo no agendamento", "price_cents": 12000,
         "components": [
             {"service_id": A, "name": "Corte do agendamento", "price_cents": 6000},
             {"service_id": B, "name": "Barba do agendamento", "price_cents": 4000},
         ]},
        {"service_id": C, "name": "Acabamento", "price_cents": 5000, "components": None},
    ]}
    items = [{**base(str(uuid4()), 15000, "Pacote sintético"),
              "catalog_kind": "variant", "catalog_snapshot": snapshot}]
    parts = split_appointment_services(items)
    assert [x["service_id"] for x in parts] == [A, B, C]
    assert [x["name"] for x in parts] == [
        "Corte do agendamento", "Barba do agendamento", "Acabamento"]
    earned, detail = calculate_service_commissions(items, {
        A: {"kind": "percent", "percent": "10.00"},
        B: {"kind": "fixed", "fixed_cents": 1000},
        C: {"kind": "percent", "percent": "20.00"}}, 15000)
    assert [x["commission_cents"] for x in detail] == [600, 1000, 1000]
    assert earned == 2600


def test_zero_commission_is_explicit_and_valid():
    total, lines = calculate_service_commissions([base()], {
        A: {"kind": "percent", "percent": "0.00"}}, 10000)
    assert total == 0 and lines[0]["commission_cents"] == 0


@pytest.mark.parametrize("policy", [
    {}, {A: {"kind": "none"}},
    {A: {"kind": "fixed", "fixed_cents": -1}},
    {A: {"kind": "percent", "percent": "101"}},
    {A: {"kind": "percent", "percent": "12.345"}},
])
def test_misconfigured_rules_never_generate_a_payout(policy):
    with pytest.raises(HTTPException) as error:
        calculate_service_commissions([base()], policy, 10000)
    assert error.value.status_code == 409


def test_missing_synthetic_snapshot_must_be_repaired_not_guessed():
    with pytest.raises(HTTPException) as error:
        calculate_service_commissions([{**base(), "catalog_kind": "variant"}],
                                      {A: {"kind": "fixed", "fixed_cents": 1000}}, 10000)
    assert error.value.status_code == 409


@pytest.mark.parametrize("data", [
    {"mode": "per_service", "expected_version": 1, "services": []},
    {"mode": "per_service", "expected_version": 1,
     "services": [{"service_id": A}]},
    {"mode": "per_service", "expected_version": 1,
     "services": [{"service_id": A, "kind": "fixed", "fixed_cents": 100, "percent": "10"}]},
    {"mode": "per_service", "expected_version": 1,
     "services": [{"service_id": A, "kind": "percent", "percent": "123"}]},
    {"mode": "per_service", "expected_version": 1,
     "services": [{"service_id": A, "kind": "fixed", "fixed_cents": 100},
                  {"service_id": A, "kind": "fixed", "fixed_cents": 100}]},
])
def test_commission_plan_schema_rejects_partial_or_conflicting_rates(data):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        CommissionPlanUpdate.model_validate(data)
