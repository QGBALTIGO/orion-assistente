"""Form values round-trip without changing identifiers, dates or money units."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.brazilian_fields import normalize_tax_document
from app.catalog_schemas import Professional, Service, WorkingBreak
from app.commercial_schemas import Variant
from app.management_preferences import (
    BusinessPreferences, BusinessSave, DeliveryPolicy, validated_business_update,
)
from app.management_schemas import CustomerCreate, CustomerPatch, FinanceCreate, LoyaltyRuleFields, SitePreferences
from app.onboarding_schemas import Address, Identity
from app.public_site_api import PublicBooking
from app.professional_commissions import CommissionRuleInput


# CNPJ alpha vector: Receita Federal/SERPRO technical manual, section 1.2.
# CPF vector: UFPR CI1055 p2-20191.pdf (111.444.777-35); zero-prefix vector
# 012.345.678-90 has weighted sums 156 and 210, hence check digits 9 and 0.
@pytest.mark.parametrize("kind,raw,canonical", [
    ("cpf", "111.444.777-35", "11144477735"),
    ("cpf", "012.345.678-90", "01234567890"),
    ("cnpj", "11.222.333/0001-81", "11222333000181"),
    ("cnpj", "12.ABC.345/01DE-35", "12ABC34501DE35"),
    ("cnpj", " 12.abc.345/01de-35 ", "12ABC34501DE35"),
])
def test_documents_are_text_with_verified_check_digits(kind, raw, canonical):
    value = BusinessPreferences(tax_document_kind=kind, tax_document=raw)
    assert value.tax_document == canonical
    assert BusinessPreferences.model_validate(value.model_dump()).tax_document == canonical


@pytest.mark.parametrize("kind,raw", [
    ("cpf", "111.444.777-36"), ("cpf", "01234567891"), ("cpf", "00000000000"),
    ("cpf", "11111111111"), ("cpf", "１２３４５６７８９０９"), ("cpf", "123x45678909"),
    ("cpf", "11144477735 extra"), ("cpf", 12345678909),
    ("cnpj", "12.ABC.345/01DE-36"), ("cnpj", "12.ABC.345/01DE-3A"),
    ("cnpj", "12.ÁBC.345/01DE-35"), ("cnpj", "12.ABſ.345/01DE-35"), ("cnpj", "00000000000000"),
    ("cnpj", "11222333000182"), ("none", "11144477735"),
])
def test_new_documents_reject_wrong_checksum_length_or_characters(kind, raw):
    with pytest.raises((ValueError, ValidationError)):
        normalize_tax_document(kind, raw)


def test_non_ascii_cnpj_does_not_become_a_different_identifier_after_uppercase():
    with pytest.raises(ValueError):
        normalize_tax_document("cnpj", "12.ABſ.345/01DE-35", check_digits=False)


@pytest.mark.parametrize("kind", ["none", "cpf", "cnpj"])
@pytest.mark.parametrize("value", [None, "", "   "])
def test_an_optional_empty_document_stays_empty(kind, value):
    assert BusinessPreferences(tax_document_kind=kind, tax_document=value).tax_document is None


def historical_business():
    return BusinessPreferences.model_validate({
        "tax_document_kind": "cpf", "tax_document": "12345678900", "legal_name": "Cadastro anterior",
        "monthly_goal_cents": 987654, "contact_phone": "+5555999999901",
    }, context={"existing_tax_document": True}).model_dump()


@pytest.mark.parametrize("changes", [
    {"trade_name": "Nome atualizado"},
    {"tax_document_kind": "cpf", "tax_document": "123.456.789-00", "monthly_goal_cents": 123456},
])
def test_unchanged_historical_document_does_not_block_an_unrelated_edit(changes):
    before = historical_business()
    assert validated_business_update(before, changes)["tax_document"] == "12345678900"
    assert before == historical_business()


def test_partial_input_keeps_omission_distinct_from_clearing():
    omitted = BusinessSave(expected_version=1, business={"trade_name": "Outro nome"})
    assert omitted.business.model_dump(exclude_unset=True) == {"trade_name": "Outro nome"}
    result = validated_business_update(historical_business(), omitted.business.model_dump(exclude_unset=True))
    assert (result["tax_document"], result["monthly_goal_cents"], result["legal_name"]) == (
        "12345678900", 987654, "Cadastro anterior")
    cleared = BusinessSave(expected_version=1, business={"tax_document": None})
    assert validated_business_update(result, cleared.business.model_dump(exclude_unset=True))["tax_document"] is None


def test_changed_document_or_kind_has_to_validate_even_if_previous_record_was_invalid():
    old = historical_business()
    for changes in ({"tax_document": "12345678901"}, {"tax_document_kind": "cnpj"}):
        with pytest.raises((ValidationError, ValueError)):
            validated_business_update(old, changes)
    updated = validated_business_update(old, {"tax_document_kind": "cnpj", "tax_document": "12.ABC.345/01DE-35"})
    assert updated["tax_document"] == "12ABC34501DE35"
    with pytest.raises(ValidationError):
        BusinessPreferences.model_validate(old)  # Default validation never opts out.


class PreferencesDB:
    """No provider calls or production data: emulate only the persistence boundary."""
    def __init__(self, business):
        self.row = {"preferences": {"business": deepcopy(business)}, "version": 7, "updated_at": None}
        self.statements = []

    async def execute(self, statement, params):
        import json
        sql = str(statement)
        self.statements.append(sql)
        if "INSERT INTO management_preferences" in sql:
            self.row["preferences"][params["section"]] = json.loads(params["value"])
            self.row["version"] += 1
        return self

    def mappings(self):
        return self

    def one_or_none(self):
        return self.row


@pytest.mark.asyncio
async def test_document_is_checked_under_version_lock_before_any_write(monkeypatch):
    from app import management_preferences as module
    audits = []
    async def audit(*args):
        audits.append(args)
    monkeypatch.setattr(module, "audit", audit)
    db = PreferencesDB(historical_business())
    with pytest.raises(HTTPException) as error:
        await module.save_section(db, uuid4(), "business", {"tax_document": "12345678901"}, 7)
    assert error.value.status_code == 422
    assert "pg_advisory_xact_lock" in db.statements[0]
    assert not any("INSERT" in sql for sql in db.statements) and not audits
    assert db.row["preferences"]["business"]["tax_document"] == "12345678900"
    with pytest.raises(HTTPException) as stale:
        await module.save_section(db, uuid4(), "business", {"trade_name": "Atualizada"}, 6)
    assert stale.value.status_code == 409
    saved = await module.save_section(db, uuid4(), "business", {"trade_name": "Atualizada"}, 7)
    assert saved["business"]["tax_document"] == "12345678900" and saved["version"] == 8
    assert len(audits) == 1


@pytest.mark.parametrize("phone", ["(55) 99999-9901", "55999999901", "+55 (55) 99999-9901", "5555999999901"])
def test_same_brazilian_phone_has_same_identity_in_all_form_contracts(phone):
    customer = CustomerCreate(name="Ana D'Ávila-Sousa", phone=phone, idempotency_key="test-customer")
    business = BusinessPreferences(contact_phone=phone)
    identity = Identity(name="Negócio", timezone="America/Cuiaba", niche="barbearia", activity="services", contact_phone=phone)
    public = PublicBooking(quote="a" * 40, start_at="2026-11-20T15:00:00-04:00", customer_name="Ana D'Ávila-Sousa",
                           customer_phone=phone, confirmed=True)
    assert {customer.phone, business.contact_phone, identity.contact_phone, public.customer_phone} == {"+5555999999901"}
    assert customer.name == "Ana D'Ávila-Sousa" and public.customer_birth_date is None


@pytest.mark.parametrize("raw", ["01001000", "01001-000", " 01001-000 "])
def test_cep_and_uf_preserve_their_contract_without_losing_address_details(raw):
    data = dict(postal_code=raw, state="sp", city="São Paulo", neighborhood="Sé")
    site = SitePreferences(**data, address_number="123A", address_complement="Sala 2")
    address = Address(**data, street="Praça da Sé", number="123A", complement="Sala 2")
    assert site.postal_code == "01001-000"
    assert address.postal_code == raw.strip()  # Preserve old setup operation digests.
    assert address.postal_code.replace("-", "") == "01001000"
    assert site.state == address.state == "SP"
    assert (site.address_number, address.number, site.address_complement, address.complement) == ("123A", "123A", "Sala 2", "Sala 2")


@pytest.mark.parametrize("changes", [{"state": "ZZ"}, {"state": "MSS"}, {"postal_code": "0100100"},
                                     {"postal_code": "01001-000x"}, {"postal_code": 1001000}])
def test_address_rejects_invalid_uf_or_partial_cep(changes):
    with pytest.raises(ValidationError):
        SitePreferences(**changes)
    with pytest.raises(ValidationError):
        Address(street="Rua", number="123A", neighborhood="Centro", city="Cidade", **{"state": "MS", **changes})


def test_no_number_and_empty_optional_cep_remain_supported():
    address = Address(street="Rua", no_number=True, neighborhood="Centro", city="Cidade", state="MS", postal_code="")
    assert address.number is None and address.postal_code is None
    site = SitePreferences(address_no_number=True, address_number="123A", postal_code="")
    assert site.address_number is None and site.postal_code is None


def test_civil_birthday_partial_history_and_delivery_midnight_are_preserved():
    patch = CustomerPatch(expected_version=2, birth_date="2000-02-29")
    assert patch.model_dump(mode="json", exclude_unset=True) == {
        "expected_version": 2, "birth_date": "2000-02-29", "birth_month": 2, "birth_day": 29,
    }
    partial = CustomerPatch(expected_version=2, notes="Aniversário sem ano")
    assert not {"birth_date", "birth_month", "birth_day"} & partial.model_fields_set
    for day in ("2001-02-29", (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()):
        with pytest.raises(ValidationError):
            CustomerPatch(expected_version=2, birth_date=day)
    assert DeliveryPolicy(operational_end_hour=24, marketing_end_hour=24).operational_end_hour == 24
    with pytest.raises(ValidationError):
        DeliveryPolicy(operational_start_hour=18, operational_end_hour=9)
    with pytest.raises(ValidationError):
        WorkingBreak(working_hour_id=uuid4(), start_time="13:00", end_time="12:00")


def test_money_and_percentages_keep_distinct_units_and_existing_limits():
    finance = FinanceCreate(kind="income", description="Serviço", amount_cents=123456,
                            occurred_at="2026-10-28T00:05:00-04:00", idempotency_key="synthetic-finance")
    assert finance.amount_cents == 123456
    assert finance.occurred_at == datetime(2026, 10, 28, 4, 5, tzinfo=timezone.utc)
    for value in (1234.56, "1234,56", True):
        with pytest.raises(ValidationError):
            FinanceCreate(kind="income", description="Serviço", amount_cents=value,
                          occurred_at="2026-10-28T00:05:00-04:00", idempotency_key="synthetic-finance")
    assert Professional(name="Pessoa", commission_percent=Decimal("10.50")).commission_percent == Decimal("10.50")
    assert Variant(name="Sob consulta", price_kind="on_request").unit_price_cents is None
    with pytest.raises(ValidationError):
        Service(name="A partir de", duration_min=30, price_mode="from", price_cents=0)
    with pytest.raises(ValidationError):
        LoyaltyRuleFields(name="Programa", reward_description="Desconto", reward_type="percent", reward_percent=0)


@pytest.mark.parametrize("value", [True, False, 1234.56, "1.234,56", "R$ 1.234,56"])
@pytest.mark.parametrize("model,base,field", [
    (Service, {"name": "Serviço", "duration_min": 30}, "price_cents"),
    (Variant, {"name": "Variante", "price_kind": "fixed"}, "unit_price_cents"),
    (BusinessPreferences, {}, "monthly_goal_cents"),
    (CommissionRuleInput, {"service_id": "00000000-0000-4000-8000-000000000001", "commission_kind": "fixed"}, "commission_fixed_cents"),
])
def test_catalog_money_does_not_accept_boolean_or_localized_reais_as_cents(value, model, base, field):
    with pytest.raises(ValidationError):
        model(**base, **{field: value})


@pytest.mark.parametrize("value", [123456, 123456.0, "123456"])
@pytest.mark.parametrize("model,base,field", [
    (Service, {"name": "Serviço", "duration_min": 30}, "price_cents"),
    (Variant, {"name": "Variante", "price_kind": "fixed"}, "unit_price_cents"),
    (BusinessPreferences, {}, "monthly_goal_cents"),
    (CommissionRuleInput, {"service_id": "00000000-0000-4000-8000-000000000001", "commission_kind": "fixed"}, "commission_fixed_cents"),
])
def test_existing_integral_money_contracts_round_trip_without_unit_changes(value, model, base, field):
    parsed = model(**base, **{field: value})
    assert type(getattr(parsed, field)) is int and getattr(parsed, field) == 123456
    assert getattr(model.model_validate(parsed.model_dump()), field) == 123456
