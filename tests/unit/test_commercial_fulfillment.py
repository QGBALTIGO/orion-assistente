"""Receiving authority and immutable factual contracts, without external I/O."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError
import pytest

from app import commercial_fulfillment as fulfillment
from app.business_profile_schemas import BusinessProfile
from app.commercial_schemas import EstimateRequest, FulfillmentSelection


class NoDatabase:
    async def execute(self, *_args, **_kwargs):
        raise AssertionError("Legacy or disabled receiving must not access a new authority")


def test_receiving_is_disabled_in_old_profiles_and_legacy_payloads():
    profile = BusinessProfile.model_validate({"business_name": "Synthetic company"})
    assert profile.fulfillment.enabled_modes == []
    payload = EstimateRequest(items=[{"variant_id": uuid4(), "quantity": 1}])
    assert payload.fulfillment is None and payload.contact_e164 is None


@pytest.mark.parametrize("value", [
    {"mode": "shipping"}, {"mode": []}, {"mode": "pickup", "snapshot": {"trusted": True}},
    {"mode": "delivery", "pickup_appointment_id": str(uuid4())},
    {"mode": "pickup", "pickup_appointment_id": "not-an-id"},
])
def test_client_cannot_invent_receiving_mode_snapshot_or_delivery_booking(value):
    with pytest.raises(ValidationError):
        FulfillmentSelection.model_validate(value)


def test_profile_modes_are_bounded_unique_and_do_not_grant_order_capability():
    with pytest.raises(ValidationError):
        BusinessProfile(fulfillment={"enabled_modes": ["pickup", "pickup"]})
    profile = BusinessProfile(fulfillment={"enabled_modes": ["pickup"]})
    assert "product_orders" not in profile.capabilities


@pytest.mark.asyncio
async def test_old_quotes_keep_none_even_after_feature_disabled(monkeypatch):
    monkeypatch.delenv("COMMERCIAL_FULFILLMENT_ENABLED", raising=False)
    tenant = uuid4()
    assert await fulfillment.resolve(NoDatabase(), tenant, None, None) == fulfillment.EMPTY
    assert await fulfillment.validate(NoDatabase(), tenant, {"fulfillment_mode": None}, confirming=True) == fulfillment.EMPTY
    assert await fulfillment.current_appointment(NoDatabase(), tenant, {}) is None
    assert (await fulfillment.options(NoDatabase(), tenant))["enabled"] is False


@pytest.fixture
def policy(monkeypatch):
    tenant, version, item, customer, pro = (uuid4() for _ in range(5))
    start = datetime.now(timezone.utc) + timedelta(days=2)
    row = {"id": item, "protocol": "SYNTHETIC", "start_at": start, "status": "confirmed",
        "updated_at": start-timedelta(days=2), "timezone": "America/Campo_Grande", "professional": "Synthetic professional"}
    record = {**row, "end_at": start+timedelta(minutes=20), "customer_id": customer,
        "professional_id": pro, "total_cents": 2000, "items": []}
    options = {"enabled": True, "enabled_modes": ["pickup", "delivery"], "profile_version_id": str(version),
        "profile_revision": 1, "_pickup_location": "Published company location"}
    async def current_policy(*_args, **_kwargs):
        return deepcopy(options)
    async def owned(_db, t, contact, identity, **_kwargs):
        assert t == tenant and contact == "+5567999999999"
        return deepcopy(row) if identity == item else None
    async def detail(*_args):
        return deepcopy(record)
    monkeypatch.setattr(fulfillment, "options", current_policy)
    monkeypatch.setattr(fulfillment, "_owned_appointment", owned)
    monkeypatch.setattr(fulfillment, "read_appointment", detail)
    return tenant, item, options, row


@pytest.mark.asyncio
async def test_server_snapshot_uses_owned_booking_and_published_facts_only(policy):
    tenant, item, _options, _row = policy
    contract = await fulfillment.resolve(NoDatabase(), tenant, "+5567999999999",
        {"mode": "pickup", "pickup_appointment_id": str(item)}, lock=True)
    snap = contract["fulfillment_snapshot"]
    assert snap["schema_version"] == 1 and snap["mode"] == "pickup"
    assert snap["pickup_appointment"]["id"] == str(item)
    assert len(snap["pickup_appointment"]["expected_snapshot"]) == 64
    assert snap["pickup_location"] == "Published company location"
    assert not snap["requires_team_review"]
    assert not any(name in str(snap) for name in ("payment", "customer_phone", "delivery_address"))


@pytest.mark.asyncio
async def test_unknown_or_foreign_booking_cannot_become_receiving_authority(policy):
    tenant, *_ = policy
    with pytest.raises(HTTPException) as error:
        await fulfillment.resolve(NoDatabase(), tenant, "+5567999999999",
            {"mode": "pickup", "pickup_appointment_id": str(uuid4())})
    assert error.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["disabled", "mode", "version", "appointment"])
async def test_final_validation_requires_same_published_policy_and_owned_snapshot(policy, change):
    tenant, item, options, row = policy
    contract = await fulfillment.resolve(NoDatabase(), tenant, "+5567999999999",
        {"mode": "pickup", "pickup_appointment_id": str(item)})
    quote = {**contract, "contact_e164": "+5567999999999"}
    if change == "disabled":
        options["enabled"] = False
    elif change == "mode":
        options["enabled_modes"] = ["delivery"]
    elif change == "version":
        options["profile_version_id"] = str(uuid4())
    else:
        row["start_at"] += timedelta(minutes=30)
    with pytest.raises(HTTPException) as error:
        await fulfillment.validate(NoDatabase(), tenant, quote, confirming=True)
    assert error.value.status_code in {409, 423}


@pytest.mark.asyncio
async def test_delivery_is_a_human_review_request_and_never_confirmable(policy):
    tenant, *_ = policy
    contract = await fulfillment.resolve(NoDatabase(), tenant, "+5567999999999", {"mode": "delivery"})
    facts = {"total_cents": 2500, "confirmation_requirements_met": True, "limitations": []}
    result = fulfillment.apply(facts, contract)
    assert result["total_cents"] == 2500 and not result["confirmation_requirements_met"]
    assert result["limitations"] == ["delivery_requires_team_review"]
    assert contract["pickup_appointment_id"] is None and contract["fulfillment_snapshot"]["pickup_location"] is None
    assert facts["confirmation_requirements_met"] is True
    with pytest.raises(HTTPException) as error:
        await fulfillment.validate(NoDatabase(), tenant, {**contract, "contact_e164": "+5567999999999"}, confirming=True)
    assert error.value.status_code == 409


def test_replay_matches_exact_mode_and_optional_booking():
    item = uuid4()
    quote = {"fulfillment_mode": "pickup", "pickup_appointment_id": item}
    assert fulfillment.same_selection(quote, {"mode": "pickup", "pickup_appointment_id": str(item)})
    assert not fulfillment.same_selection(quote, {"mode": "pickup"})
    assert not fulfillment.same_selection(quote, {"mode": "delivery"})
    assert not fulfillment.same_selection(quote, None)
    assert fulfillment.same_selection({"fulfillment_mode": None}, None)
