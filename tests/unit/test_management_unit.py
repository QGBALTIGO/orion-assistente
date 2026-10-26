"""Management boundaries and authorization without replacing PostgreSQL RLS tests."""
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from fastapi import FastAPI, HTTPException
import httpx
import pytest
from pydantic import ValidationError

from app.catalog_api import Actor, authenticate
from app.catalog_schemas import Professional, Service
from app import management_api as api
from app.management_models import customer_filter, report_period
from app.management_schemas import (
    AppointmentTransition, CustomerCreate, CustomerPatch, FinanceCreate, FinanceFields,
    FinancePatch, GoalCreate, LoyaltyTransactionCreate, MarketingCreate, SettlementCreate, SiteSave,
)


def finance_payload(**changes):
    return {"kind": "income", "description": "Serviço", "amount_cents": 2500,
            "occurred_at": "2026-10-17T09:30:00-03:00", "idempotency_key": "finance-test-001", **changes}


@pytest.mark.parametrize("value", [None, "", "   "])
def test_manual_customer_needs_only_name_and_does_not_invent_phone(value):
    customer = CustomerCreate(name="  Ana Souza  ", phone=value, idempotency_key="customer-test-001")
    assert customer.name == "Ana Souza"
    assert customer.phone is None
    assert CustomerCreate(name="Ana", idempotency_key="customer-test-002").phone is None
    assert CustomerPatch(phone=None, expected_version=1).model_dump(exclude_unset=True)["phone"] is None


def test_customer_phone_and_optional_fields_are_normalized_without_changing_booking_contract():
    customer = CustomerCreate(name="Ana", phone="(11) 91234-5678", instagram="@ana",
                              neighborhood="Centro", city="São Paulo", email=" ANA@EXAMPLE.COM ",
                              birth_date="2001-06-20", idempotency_key="customer-test-003")
    assert customer.phone == "+5511912345678"
    assert customer.email == "ana@example.com"
    assert customer.instagram == "@ana"
    from app.booking_api import HoldRequest
    assert "customer_phone" in HoldRequest.model_fields
    assert HoldRequest.model_fields["customer_phone"].is_required()


@pytest.mark.parametrize("field,value", [("name", "   "), ("phone", "abc"), ("phone", "+12025550123"),
                                        ("email", "invalid"), ("birth_date", "1800-01-01"),
                                        ("birth_date", "2999-01-01"), ("notes", "a\x00b"), ("tenant_id", str(uuid4()))])
def test_customer_rejects_invalid_or_authority_fields(field, value):
    with pytest.raises(ValidationError):
        CustomerCreate(**{"name": "Ana", "idempotency_key": "customer-test-004", field: value})


@pytest.mark.parametrize("amount", [-1, 0, 1_000_000_001, True, False, 25.50, "2500"])
def test_money_requires_positive_bounded_integer_cents(amount):
    with pytest.raises(ValidationError):
        FinanceCreate(**finance_payload(amount_cents=amount))


@pytest.mark.parametrize("value", ["2026-10-17T09:30:00", "1800-01-01T00:00:00Z", "2500-01-01T00:00:00Z"])
def test_ledger_requires_aware_and_bounded_timestamp(value):
    with pytest.raises(ValidationError):
        FinanceCreate(**finance_payload(occurred_at=value))


def test_ledger_normalizes_offset_and_rejects_advance_without_professional():
    assert FinanceCreate(**finance_payload()).occurred_at == datetime(2026, 10, 17, 12, 30, tzinfo=timezone.utc)
    with pytest.raises(ValidationError):
        FinanceCreate(**finance_payload(kind="expense", entry_type="advance"))
    value = FinanceCreate(**finance_payload(kind="expense", entry_type="advance", professional_id=uuid4()))
    assert value.entry_type == "advance"


def test_partial_update_preserves_values_but_revalidates_required_fields():
    current = FinanceCreate(**finance_payload()).model_dump(exclude={"idempotency_key"})
    changed = api.merged_changes(current, FinancePatch(description="Novo", expected_version=1), FinanceFields)
    assert changed == {"description": "Novo"}
    for patch in (FinancePatch(amount_cents=None, expected_version=1), FinancePatch(expected_version=1)):
        with pytest.raises(HTTPException) as error:
            api.merged_changes(current, patch, FinanceFields)
        assert error.value.status_code == 422


@pytest.mark.parametrize("delta", [0, 1.5, True, 1_000_001, -1_000_001])
def test_point_ledger_requires_bounded_nonzero_integer(delta):
    with pytest.raises(ValidationError):
        LoyaltyTransactionCreate(customer_id=uuid4(), points_delta=delta, reason="Ajuste", idempotency_key="point-test-001")


def test_goal_validates_complete_period_and_money_bound():
    with pytest.raises(ValidationError):
        GoalCreate(professional_id=uuid4(), metric="appointments", start_date="2026-10-20", end_date="2026-10-01",
                   target_value=10, idempotency_key="goal-test-001")


def test_report_period_respects_tenant_midnight_and_dst():
    period, lower, upper = report_period(date(2026, 3, 8), date(2026, 3, 8), "America/New_York")
    assert period["start_date"] == period["end_date"]
    assert upper - lower == timedelta(hours=23)
    assert lower.hour == 5 and upper.hour == 4
    default, _, _ = report_period(None, None, "America/Sao_Paulo", datetime(2026, 11, 1, 1, tzinfo=timezone.utc))
    assert default["end_date"] == date(2026, 10, 31)
    assert default["start_date"] == date(2026, 10, 1)


@pytest.mark.parametrize("start,end", [(date(2026, 10, 2), date(2026, 10, 1)),
                                      (date(2025, 1, 1), date(2026, 1, 2)),
                                      (date(9999, 12, 31), date(9999, 12, 31))])
def test_report_rejects_reversed_unbounded_or_overflowing_period(start, end):
    with pytest.raises(HTTPException) as error:
        report_period(start, end, "America/Sao_Paulo")
    assert error.value.status_code == 422


def test_customer_search_is_literal_and_directory_hides_deleted_records():
    tenant = uuid4()
    where, parameters = customer_filter(tenant, "50%_\\", "at_risk")
    assert parameters["search"] == "%50\\%\\_\\\\%"
    assert "deleted_at IS NULL" in where and "risk_days" in where
    assert parameters["tenant"] == tenant


@pytest.mark.parametrize("url", ["javascript:alert(1)", "http://example.com/image.png", "https://user:pass@example.com/x"])
def test_public_image_fields_reject_unsafe_urls(url):
    for model, payload in ((SiteSave, {"expected_version": 0, "logo_url": url}),
                           (Professional, {"name": "Pessoa", "image_url": url}),
                           (Service, {"name": "Corte", "duration_min": 30, "price_cents": 1000, "image_url": url})):
        with pytest.raises(ValidationError):
            model(**payload)


def test_marketing_and_site_contracts_do_not_accept_send_or_publish_switches():
    with pytest.raises(ValidationError):
        MarketingCreate(name="Campanha", message="Olá", idempotency_key="campaign-test-001", status="approved")
    with pytest.raises(ValidationError):
        SiteSave(expected_version=0, published=True)
    with pytest.raises(ValidationError):
        AppointmentTransition(idempotency_key="complete-test-001", expected_status="hold")


@pytest.mark.asyncio
@pytest.mark.parametrize("actor,write", [(Actor("viewer", uuid4()), False), (Actor("viewer"), True), (Actor("unknown"), False)])
async def test_tenant_authorization_precedes_database_access(monkeypatch, actor, write):
    def forbidden_factory():
        pytest.fail("Unauthorized request opened a database connection")
    monkeypatch.setattr(api, "get_session_factory", forbidden_factory)
    with pytest.raises(HTTPException) as error:
        async with api.management_session(uuid4(), actor, write=write):
            pytest.fail("Unauthorized transaction entered")
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_viewer_in_own_scope_cannot_create_or_transition(monkeypatch):
    app = FastAPI()
    app.include_router(api.router)
    tenant = uuid4()
    app.dependency_overrides[authenticate] = lambda: Actor("viewer", tenant)
    monkeypatch.setattr(api, "get_session_factory", lambda: pytest.fail("Write opened runtime DB for a viewer"))
    base = f"/api/v1/tenants/{tenant}/management"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(base + "/customers", json={"name": "Ana", "idempotency_key": "viewer-test-001"})
        assert response.status_code == 403
        response = await client.post(base + f"/appointments/{uuid4()}/complete", json={"idempotency_key": "viewer-test-002"})
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_dashboard_is_rejected_before_database(monkeypatch):
    app = FastAPI()
    app.include_router(api.router)
    monkeypatch.setenv("SUPPORT_API_TOKEN", "support-test-token-at-least-thirty-two-characters")
    monkeypatch.setattr(api, "get_session_factory", lambda: pytest.fail("Unauthenticated request opened runtime DB"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/tenants/{uuid4()}/management/dashboard")
        assert response.status_code == 401


def test_csv_neutralizes_formula_names_and_preserves_optional_empty_phone():
    response = api.csv_response("clientes.csv", ["Nome", "Telefone"], [["=HYPERLINK(\"x\")", None]])
    assert response.body.startswith(b"\xef\xbb\xbf")
    assert b"'=HYPERLINK" in response.body
    assert response.headers["cache-control"] == "no-store"


def test_site_draft_supports_location_links_without_publication_authority():
    result = SiteSave(expected_version=0, state="sp", postal_code="01001000", city="São Paulo",
                      whatsapp_phone="(11) 91234-5678", maps_url="https://maps.example.invalid/address",
                      hide_orion_brand=True)
    assert (result.state, result.postal_code, result.whatsapp_phone) == ("SP", "01001-000", "+5511912345678")
    for field, value in (("public_enabled", True), ("public_snapshot", {}), ("public_slug", "approved-slug")):
        with pytest.raises(ValidationError):
            SiteSave(expected_version=0, **{field: value})


@pytest.mark.parametrize("earned,paid,advances,allocations,payable", [
    (2500, 0, [(500, 0), (1000, 0)], [500, 1000], 1000),
    (2500, 0, [(4000, 0)], [2500], 0),
    (5000, 0, [(4000, 2500)], [1500], 1000),
    (1000, 1500, [(100, 0)], [0], 0),
    (10000, 1000, [(1000, 1000)], [0], 8000),
])
def test_settlement_allocation_preserves_partial_advances_and_never_pays_negative(earned, paid, advances, allocations, payable):
    from app.financial_settlements import allocate_advances
    plan, result, allocated = allocate_advances(earned, paid,
        [{"amount_cents": amount, "allocated_cents": prior} for amount, prior in advances])
    assert [item["allocation_cents"] for item in plan] == allocations
    assert result == payable and allocated == sum(prior for _, prior in advances)
    assert all(0 <= item["allocation_cents"] <= item["pending_cents"] for item in plan)


def test_settlement_contract_requires_a_snapshot_and_has_stable_omitted_timestamp():
    values = {"professional_id": uuid4(), "expected_snapshot": "a" * 64, "idempotency_key": "settlement-test-key"}
    assert SettlementCreate(**values).occurred_at is None
    for changes in ({"expected_snapshot": "incorrect"}, {"amount_cents": 1000}, {"occurred_at": "2026-10-18T10:00:00"}):
        with pytest.raises(ValidationError):
            SettlementCreate(**{**values, **changes})

def test_public_amenities_are_opt_in_and_require_real_boolean_values():
    fields = ("amenity_wifi", "amenity_parking", "amenity_accessibility", "amenity_children")
    baseline = SiteSave(expected_version=0)
    assert all(getattr(baseline, key) is False for key in fields)
    assert set(fields).issubset(baseline.model_dump())
    enabled = SiteSave(expected_version=0, amenity_wifi=True, amenity_parking=True,
                       amenity_accessibility=True, amenity_children=True)
    assert all(getattr(enabled, key) is True for key in fields)
    for field in fields:
        with pytest.raises(ValidationError):
            SiteSave(expected_version=0, **{field: "true"})
        with pytest.raises(ValidationError):
            SiteSave(expected_version=0, **{field: 1})
