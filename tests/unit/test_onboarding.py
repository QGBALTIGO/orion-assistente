"""Setup contracts and readiness, with no provider calls or production fixtures."""
from copy import deepcopy
from datetime import time
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from app import onboarding_schemas as schemas, onboarding_service as service
from app.catalog_api import Actor, authenticate
from app.onboarding_api import router


def identity(**changes):
    return {"name": "Empresa sintética", "timezone": "America/Cuiaba", "niche": "barbearia", "activity": "services", **changes}


def facts():
    pro, srv, hour, product, variant = (uuid4() for _ in range(5))
    return {"business": {"id": uuid4(), "name": "Sintética", "timezone": "America/Cuiaba", "status": "paused"},
            "professionals": [{"id": pro, "name": "Pessoa sintética", "active": True, "accepts_appointments": True}],
            "services": [{"id": srv, "name": "Serviço sintético", "active": True, "price_cents": 3500, "duration_min": 40, "prep_min": 5, "cleanup_min": 10}],
            "links": [{"id": uuid4(), "professional_id": pro, "service_id": srv}],
            "hours": [{"id": hour, "professional_id": pro, "weekday": 0, "start_time": time(9), "end_time": time(18)}],
            "breaks": [], "products": [{"id": product, "active": True}],
            "variants": [{"id": variant, "product_id": product, "active": True, "price_kind": "on_request", "unit_price_cents": None}]}


def configuration(**changes):
    return {"niche": "barbearia", "activity": "services", "modality": "online", **changes}


@pytest.mark.parametrize("field,value", [("name", ""), ("timezone", "Not/AZone"), ("activity", "unknown"),
    ("niche", ""), ("niche", "outro"), ("contact_phone", "https://example.test"),
    ("contact_phone", "+11234567890"), ("other_niche", ""), ("name", " "*5)])
def test_identity_rejects_unusable_or_unscoped_facts(field, value):
    with pytest.raises(ValidationError):
        schemas.Identity.model_validate(identity(**{field: value}))


@pytest.mark.parametrize("phone", ["(67) 99999-9901", "+55 67 99999 9901", "67999999901"])
def test_contact_normalization_reuses_existing_contract(phone):
    assert schemas.Identity.model_validate(identity(contact_phone=phone)).contact_phone == "+5567999999901"


@pytest.mark.parametrize("activity", ["services", "products", "both"])
def test_other_niche_and_activities_are_data_not_hardcoded_trade(activity):
    value = schemas.Identity.model_validate(identity(niche="outro", other_niche="Reparos", activity=activity))
    assert value.activity == activity


@pytest.mark.parametrize("mode,needs_address", [("onsite", True), ("hybrid", True), ("online", False), ("client_address", False)])
def test_private_address_requirement_follows_modality(mode, needs_address):
    if needs_address:
        with pytest.raises(ValidationError):
            schemas.Location(modality=mode)
    else:
        assert schemas.Location(modality=mode).address is None


@pytest.mark.parametrize("number,no_number,valid", [("10", False, True), (None, True, True), (None, False, False), ("10", True, False)])
def test_address_requires_number_or_explicit_no_number(number, no_number, valid):
    data = dict(street="Rua sintética", number=number, no_number=no_number, neighborhood="Centro", city="Cidade", state="MS")
    if valid:
        assert schemas.Address(**data).street
    else:
        with pytest.raises(ValidationError):
            schemas.Address(**data)


@pytest.mark.parametrize("change", [{"weekdays": [1, 1]}, {"weekdays": [7]}, {"weekdays": [True]},
    {"start_time": "18:00"}, {"end_time": "08:00"}, {"break_start": "12:00"},
    {"break_start": "08:00", "break_end": "09:30"}, {"break_start": "12:00", "break_end": "19:00"},
    {"start_time": "09:00+03:00"}, {"weekdays": []}])
def test_hours_reject_invalid_local_intervals_before_write(change):
    data = {"professional_id": uuid4(), "weekdays": [0, 2], "start_time": "09:00", "end_time": "18:00", **change}
    with pytest.raises(ValidationError):
        schemas.Hours.model_validate(data)


@pytest.mark.parametrize("kind,price", [("fixed", None), ("from", None), ("on_request", 0), ("custom", 1500)])
def test_unknown_product_price_never_becomes_zero(kind, price):
    with pytest.raises(ValidationError):
        schemas.Catalog(kind="product", item={"name": "Produto"}, variant={"name": "Padrão", "price_kind": kind, "unit_price_cents": price})


@pytest.mark.parametrize("field,value", [("price_cents", None), ("price_cents", True), ("price_cents", -1), ("duration_min", 0), ("duration_min", None)])
def test_service_needs_explicit_price_and_duration(field, value):
    data = {"name": "Serviço", "price_cents": 3500, "duration_min": 40, field: value}
    with pytest.raises(ValidationError):
        schemas.Catalog(kind="service", item=data)


@pytest.mark.parametrize("kind,extra", [("service", {"variant": {"name": "Padrão"}}),
    ("product", {}), ("product", {"professional_ids": [str(uuid4())], "variant": {"name": "Padrão"}})])
def test_product_and_service_contracts_do_not_cross(kind, extra):
    with pytest.raises(ValidationError):
        schemas.Catalog(kind=kind, item={"name": "Item", "price_cents": 3500, "duration_min": 40} if kind == "service" else {"name": "Item"}, **extra)


@pytest.mark.parametrize("flag,value", [("acknowledged", False), ("acknowledged", "true"), ("activate_catalog", "true"), ("expected_version", True)])
def test_final_authority_is_explicit_and_strict(flag, value):
    data = {"expected_version": 0, "expected_snapshot": "a"*64, "idempotency_key": "synthetic-final", "activate_catalog": True, "acknowledged": True, flag: value}
    with pytest.raises(ValidationError):
        schemas.Complete(**data)


@pytest.mark.parametrize("activity", ["services", "products", "both"])
def test_readiness_accepts_real_capabilities_without_customers_or_appointments(activity, monkeypatch):
    monkeypatch.setenv("COMMERCIAL_CATALOG_ENABLED", "1")
    assert service.readiness(configuration(activity=activity), facts()) == []


@pytest.mark.parametrize("missing,code", [("professionals", "professional"), ("services", "service"),
    ("links", "scheduling_bindings"), ("hours", "scheduling_bindings")])
def test_scheduling_requires_actual_eligible_bindings(missing, code):
    data = facts()
    data[missing] = []
    assert code in {p["code"] for p in service.readiness(configuration(), data)}


@pytest.mark.parametrize("change", ["inactive_pro", "inactive_service", "no_appointments", "wrong_pro_link", "wrong_service_link", "short_shift", "all_break"])
def test_invalid_scheduling_structure_cannot_complete(change):
    data = facts()
    if change == "inactive_pro": data["professionals"][0]["active"] = False
    if change == "inactive_service": data["services"][0]["active"] = False
    if change == "no_appointments": data["professionals"][0]["accepts_appointments"] = False
    if change == "wrong_pro_link": data["links"][0]["professional_id"] = uuid4()
    if change == "wrong_service_link": data["links"][0]["service_id"] = uuid4()
    if change == "short_shift": data["hours"][0]["end_time"] = time(9, 54)
    if change == "all_break": data["breaks"] = [{"id": uuid4(), "working_hour_id": data["hours"][0]["id"], "start_time": time(9), "end_time": time(18)}]
    assert any(p["code"] == "scheduling_bindings" for p in service.readiness(configuration(), data))


def test_buffers_and_breaks_can_leave_a_valid_segment():
    data = facts()
    data["hours"][0]["end_time"] = time(11)
    data["breaks"] = [{"id": uuid4(), "working_hour_id": data["hours"][0]["id"], "start_time": time(9, 30), "end_time": time(10)}]
    assert not service.readiness(configuration(), data)
    data["hours"][0]["end_time"] = time(10, 54)
    assert service.readiness(configuration(), data)


def test_staged_records_count_for_draft_but_not_for_runtime():
    data = facts()
    managed = {}
    for table, key in (("professionals", "professionals"), ("services", "services")):
        data[key][0]["active"] = False
        managed[table] = {str(data[key][0]["id"]): True}
    assert service.readiness(configuration(), data, managed) == []
    assert service.readiness(configuration(), data, managed, staged=False)
    managed["services"][str(data["services"][0]["id"])] = False
    assert service.readiness(configuration(), data, managed)


@pytest.mark.parametrize("empty", ["products", "variants"])
def test_products_only_needs_no_professional_or_hours_but_real_product(empty, monkeypatch):
    monkeypatch.setenv("COMMERCIAL_CATALOG_ENABLED", "1")
    data = facts()
    data.update(professionals=[], services=[], links=[], hours=[])
    assert not service.readiness(configuration(activity="products"), data)
    data[empty] = []
    assert service.readiness(configuration(activity="products"), data)[0]["code"] == "product"


def test_disabled_product_module_does_not_force_an_agenda(monkeypatch):
    monkeypatch.delenv("COMMERCIAL_CATALOG_ENABLED", raising=False)
    assert {p["code"] for p in service.readiness(configuration(activity="products"), facts())} == {"products_unavailable"}


@pytest.mark.parametrize("mutation", ["service_price", "service_duration", "link", "product_variant", "break"])
def test_entity_fingerprint_includes_relevant_real_facts(mutation):
    data = facts()
    table, key = ("commercial_products", "products") if mutation == "product_variant" else ("working_hours", "hours") if mutation == "break" else ("services", "services")
    before = service._entity_fingerprint(data, table, data[key][0])
    if mutation == "service_price": data["services"][0]["price_cents"] += 100
    if mutation == "service_duration": data["services"][0]["duration_min"] += 5
    if mutation == "link": data["links"][0]["professional_id"] = uuid4()
    if mutation == "product_variant": data["variants"][0]["name"] = "Mudou"
    if mutation == "break": data["breaks"].append({"id": uuid4(), "working_hour_id": data["hours"][0]["id"], "start_time": time(12), "end_time": time(13)})
    assert before != service._entity_fingerprint(data, table, data[key][0])


def test_fingerprint_is_order_independent_for_object_keys():
    assert service.digest({"b": 2, "a": 1}) == service.digest({"a": 1, "b": 2})


@pytest.mark.asyncio
async def test_disabled_entry_never_touches_database(monkeypatch):
    monkeypatch.delenv("ONBOARDING_ENABLED", raising=False)
    monkeypatch.setattr(service, "support_session_factory", lambda: pytest.fail("GET disabled must not access new tables"))
    assert (await service.entry(Actor("member", account_id=uuid4())))["enabled"] is False


@pytest.mark.asyncio
async def test_disabled_write_is_blocked_before_database(monkeypatch):
    monkeypatch.delenv("ONBOARDING_ENABLED", raising=False)
    with pytest.raises(HTTPException) as caught:
        await service.create_business(Actor("member", account_id=uuid4()), schemas.CreateBusiness(**identity(), idempotency_key="synthetic-create"))
    assert caught.value.status_code == 423


@pytest.mark.parametrize("actor", [Actor("viewer"), Actor("support"), Actor("member")])
@pytest.mark.asyncio
async def test_creation_never_elevates_a_shared_operator_or_anonymous_account(actor, monkeypatch):
    monkeypatch.setenv("ONBOARDING_ENABLED", "1")
    with pytest.raises(HTTPException) as caught:
        await service.create_business(actor, schemas.CreateBusiness(**identity(), idempotency_key="synthetic-create"))
    assert caught.value.status_code == 403


@pytest.mark.asyncio
async def test_router_rejects_missing_completion_ack_and_unknown_step(monkeypatch):
    monkeypatch.setenv("ONBOARDING_ENABLED", "1")
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[authenticate] = lambda: Actor("member", account_id=uuid4())
    tenant = uuid4()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
        response = await client.post(f"/api/v1/tenants/{tenant}/onboarding/complete", json={"expected_version": 0, "idempotency_key": "synthetic-final", "activate_catalog": True})
        assert response.status_code == 422
        response = await client.put(f"/api/v1/tenants/{tenant}/onboarding/steps/unknown", json={"expected_version": 0, "idempotency_key": "synthetic-step", "data": {}})
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_router_validation_does_not_serialize_exception_objects(monkeypatch):
    monkeypatch.setenv("ONBOARDING_ENABLED", "1")
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[authenticate] = lambda: Actor("member", account_id=uuid4())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
        response = await client.put(f"/api/v1/tenants/{uuid4()}/onboarding/steps/hours", json={"expected_version": 0, "idempotency_key": "synthetic-step", "data": {"professional_id": str(uuid4()), "weekdays": [1], "start_time": "18:00", "end_time": "09:00"}})
    assert response.status_code == 422
