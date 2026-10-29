"""Synthetic plan and Cakto contracts, no real customers or payment requests."""
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import plan_management as plans
from app import cakto_api


def test_offered_plan_prices_and_staff_limits_never_drift():
    assert [(code,item["price_cents"],item["max_professionals"])
            for code,item in plans.CODES.items()] == [
       ("essencial",2990,2),("profissional",8990,6),("elite",13990,10)]
    assert "79.90" not in str(plans._catalog())
    assert list(plans._catalog()[1].values())[:2] == ["profissional", "Orion Profissional"]


@pytest.mark.parametrize("value,expected",[
    ("(67) 99999-1111","+5567999991111"),
    ("+55 (11) 98888-1234","+5511988881234"),
])
def test_trial_phone_formatter_accepts_brazilian_cellphones(value,expected):
    result=plans.TrialRequest(full_name="Pessoa Sintética",
                             company_name="Empresa Exemplo",whatsapp=value,privacy_ack=True)
    assert result.whatsapp==expected


@pytest.mark.parametrize("value",[
    "00000000000", "(00) 99999-1111","(67) 12345-1111",
    "(67) 89999-1111", "12345", "+1 (123) 123 1234",
    "<svg onload=alert(1)>",
])
def test_trial_rejects_invalid_whatsapp(value):
    with pytest.raises(ValidationError):
        plans.TrialRequest(full_name="Pessoa Sintética",
                           company_name="Empresa Exemplo",whatsapp=value,privacy_ack=True)


def test_trial_rejects_extra_fields_and_blank_or_unsafe_names():
    with pytest.raises(ValidationError):
        plans.TrialRequest(full_name="Correto Exemplo",company_name="Empresa",
                           whatsapp="(67) 99999-1111",privacy_ack=True,tenant_id=str(uuid4()))
    with pytest.raises(ValidationError):
        plans.TrialRequest(full_name="  ",company_name="Empresa",
                           whatsapp="(67) 99999-1111",privacy_ack=True)


def test_offer_requires_matching_name_cents_monthly_type_and_unique_match():
    offer={"id":"offer-fake", "name":"Orion Pro", "price":89.9,
           "status":"active","type":"subscription","recurrence_period":30}
    assert plans._exact_offer([offer],plans.CODES["profissional"])==offer
    for change in ({"price":79.9},{"status":"inactive"},
                   {"recurrence_period":365},{"type":"unique"},
                   {"name":"Orion Elite"},{"recurrence_period":0}):
        assert plans._exact_offer([{**offer,**change}],plans.CODES["profissional"]) is None
    assert plans._exact_offer([offer,dict(offer,id="second")],plans.CODES["profissional"]) is None


def test_cakto_callbacks_are_hashed_without_buyers_card_data_or_order_bump():
    payload={"event":"purchase_approved","data":[
        {"id":"fake-order","status":"paid","offer_type":"main",
         "offer":{"id":"fake-offer"},"sck":"callback_synthetic_012345",
         "amount":89.9,"currency":"BRL",
         "customer":{"email":"secret@example.test"},"card":{"lastDigits":"1234"}},
        {"id":"bump","status":"paid","offer_type":"orderbump",
         "sck":"callback_synthetic_012345"}]}
    rows=cakto_api.normalized_events(payload)
    assert len(rows)==1
    assert rows[0]["callback_hash"]==hashlib.sha256(b"callback_synthetic_012345").hexdigest()
    assert rows[0]["amount_cents"]==8990
    assert rows[0]["currency"]=="BRL"
    assert rows==cakto_api.normalized_events(payload)
    assert "secret@example.test" not in str(rows)
    assert "1234" not in str(rows)
    assert "callback_synthetic_012345" not in str(rows)


@pytest.mark.asyncio
async def test_unverified_payment_cannot_activate_tenant():
    assert await plans.apply_verified_cakto_payment(
        {"event_type":"purchase_approved","external_order_id":"fake-order"},
        {"id":"fake-order","status":"paid"}) == "unlinked"


@pytest.mark.asyncio
async def test_checkout_denied_when_billing_flag_is_disabled(monkeypatch):
    from app.catalog_api import Actor
    account_id=uuid4()
    tenant=uuid4()
    async def granted(*args,**kwargs):
        return SimpleNamespace(role="owner",account_id=account_id)
    monkeypatch.setattr(plans, "resolve_site_access", granted)
    monkeypatch.delenv("CAKTO_BILLING_LIVE_ENABLED",raising=False)
    with pytest.raises(HTTPException) as caught:
        await plans.start_plan_checkout(tenant,plans.CheckoutChoice(plan_code="profissional"),
                                        Actor("member",account_id=account_id))
    assert caught.value.status_code==409
    assert "homologação" in caught.value.detail


@pytest.mark.asyncio
async def test_professional_checkout_requires_exact_active_offer_even_when_flag_enabled(monkeypatch):
    from app.catalog_api import Actor
    account_id=uuid4()
    tenant=uuid4()
    async def granted(*args,**kwargs):
        return SimpleNamespace(role="owner",account_id=account_id)
    class ReadOnlyCakto:
        async def list_offers(self):
            return [{"id":"synthetic-offer","name":"Orion Pro","price":79.90,
                     "status":"active","type":"subscription","recurrence_period":30}]
    monkeypatch.setattr(plans,"resolve_site_access",granted)
    monkeypatch.setattr(plans,"CaktoClient",ReadOnlyCakto)
    monkeypatch.setenv("CAKTO_BILLING_LIVE_ENABLED","1")
    
    with pytest.raises(HTTPException) as error:
        await plans.start_plan_checkout(
            tenant,plans.CheckoutChoice(plan_code="profissional"),
            Actor("member",account_id=account_id))
    assert error.value.status_code==409
    assert "valor apresentado" in error.value.detail


def test_recurring_monthly_cakto_offer_uses_30_day_period_not_unique_interval():
    offer={"id":"monthly-synthetic","name":"Orion Profissional","price":"89.90",
           "status":"active","type":"subscription","recurrence_period":30,
           "intervalType":"week","interval":7}
    assert plans._exact_offer([offer],plans.CODES["profissional"])==offer
    assert plans._exact_offer([{**offer,"recurrence_period":365}],
                              plans.CODES["profissional"]) is None


def test_cakto_uses_documented_callback_and_sck_for_tenant_binding():
    token = "synthetic_same_token_01234567"
    payload = {"event": "purchase_approved", "data": {
        "id": "synthetic-order", "offer_type": "main", "status": "paid",
        "callback": token, "sck": token, "amount": 89.90,
        "offer": {"id": "CIProOffer"}}}
    row = cakto_api.normalized_events(payload)[0]
    assert row["callback_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert row["amount_cents"] == 8990
    assert row["currency"] is None
    payload["data"]["sck"] = "mismatched-checkout-token-123456"
    assert cakto_api.normalized_events(payload)[0]["callback_hash"] is None
    payload["data"].pop("sck")
    assert cakto_api.normalized_events(payload)[0]["callback_hash"] == hashlib.sha256(token.encode()).hexdigest()


def test_checkout_token_is_replayable_for_same_tenant_and_unguessable(monkeypatch):
    from uuid import uuid4
    monkeypatch.setenv("INTEGRATION_FERNET_KEY", "synthetic-domain-separated-checkout-key-for-ci-only-12345")
    tenant, intent = uuid4(), uuid4()
    first = plans._replayable_checkout_token(tenant, intent, "profissional")
    assert first == plans._replayable_checkout_token(tenant, intent, "profissional")
    assert first != plans._replayable_checkout_token(uuid4(), intent, "profissional")
    assert first != plans._replayable_checkout_token(tenant, intent, "elite")
    assert first.startswith("orion_") and len(first) > 38
    url = plans._checkout_url("https://pay.cakto.com.br/3652fm4", first)
    from urllib.parse import urlsplit, parse_qs
    params = parse_qs(urlsplit(url).query)
    assert params["callback"] == params["sck"] == [first]


def test_admin_can_buy_only_when_explicit_business_owner_membership_was_verified():
    from app.catalog_api import Actor
    from app.tenant_access import TenantAccess
    owner = uuid4()
    access = TenantAccess(role="owner", account_id=owner)
    assert plans._owner_account(access, Actor("member", account_id=owner)) == owner
    assert plans._owner_account(access, Actor("admin", account_id=owner)) == owner
    for role in ("support", "viewer"):
        with pytest.raises(HTTPException) as ex:
            plans._owner_account(access, Actor(role, account_id=owner))
        assert ex.value.status_code == 403
    with pytest.raises(HTTPException) as ex:
        plans._owner_account(TenantAccess(role="admin", account_id=owner),
                             Actor("admin", account_id=owner))
    assert ex.value.status_code == 403


def test_direct_checkout_never_enables_without_explicit_live_and_processor_flags(monkeypatch):
    from app import orion_direct_checkout as direct
    for key in ("ORION_DIRECT_PAYMENTS_ENABLED", "CAKTO_BILLING_LIVE_ENABLED",
                "CAKTO_CLIENT_ID", "CAKTO_CLIENT_SECRET", "CAKTO_WEBHOOK_SECRET"):
        monkeypatch.delenv(key, raising=False)
    assert not direct.checkout_enabled()
    monkeypatch.setenv("ORION_DIRECT_PAYMENTS_ENABLED", "1")
    assert not direct.checkout_enabled()
    monkeypatch.setenv("CAKTO_BILLING_LIVE_ENABLED", "1")
    monkeypatch.setenv("CAKTO_CLIENT_ID", "synthetic-id")
    monkeypatch.setenv("CAKTO_CLIENT_SECRET", "synthetic-secret")
    assert not direct.checkout_enabled()
    monkeypatch.setenv("CAKTO_WEBHOOK_SECRET", "synthetic-secret-for-ci-only-32-characters-minimum")
    assert not direct.checkout_enabled()
    monkeypatch.setenv("ORION_DIRECT_RECURRING_VERIFIED", "1")
    assert direct.checkout_enabled()


@pytest.mark.asyncio
async def test_direct_charge_fail_closed_before_any_provider_or_database(monkeypatch):
    from app import orion_direct_checkout as direct
    monkeypatch.delenv("ORION_DIRECT_PAYMENTS_ENABLED", raising=False)
    with pytest.raises(HTTPException) as ex:
        await direct.direct_prepare(uuid4(), direct.DirectPrepare(plan_code="profissional"),
                                    None)
    assert ex.value.status_code == 409
    assert "Nenhuma cobrança" in ex.value.detail


def test_direct_checkout_payer_validates_document_and_rejects_extra_card_data():
    from app import orion_direct_checkout as direct
    good = dict(name="Pessoa Sintética", email="pessoa@example.test",
                phone="(67) 99999-1111", doc_type="cpf", doc_number="529.982.247-25",
                fingerprint="synthetic-fingerprint-abcde")
    payer=direct.Payer(**good)
    assert payer.phone=="5567999991111"
    assert payer.doc_number=="52998224725"
    for bad in ({**good,"doc_number":"111.111.111-11"},
                {**good,"email":"example"},
                {**good,"cvv":"1234"}):
        with pytest.raises(ValidationError):
            direct.Payer(**bad)
    assert direct._valid_document("cnpj","11444777000161")
    with pytest.raises(ValidationError):
        direct.DirectCharge(intent_id=uuid4(),method="pix",payer=payer,
                            confirmed_monthly=True,consent_to_processor=True,
                            card_token="synthetic-card-token-123456")


def test_direct_3ds_requires_only_token_and_complete_bank_authentication():
    from app import orion_direct_checkout as direct
    payer=direct.Payer(name="Pessoa Sintética",email="pessoa@example.test",
                phone="67999991111",doc_type="cpf",doc_number="52998224725",
                fingerprint="synthetic-fingerprint-abcde")
    secure=dict(cavv="synth-cavv", eci="05",xid="synth-xid",
                referenceId="synth-ref",version="2.2.0")
    data=direct.DirectCharge(intent_id=uuid4(),method="card",payer=payer,
                  confirmed_monthly=True,consent_to_processor=True,
                  card_token="synthetic-card-token-123456",
                  antifraud_reference="synthetic-antifraud-reference",
                  three_ds=secure)
    assert data.method=="card"
    for change in ({"three_ds":None},{"card_token":None},
                   {"antifraud_reference":None},{"three_ds":{**secure,"cvv":"123"}}):
        with pytest.raises(ValidationError):
            direct.DirectCharge(**{**data.model_dump(),**change})


def test_direct_payment_payload_offer_tracking_and_amount_are_verified():
    from app import orion_direct_checkout as direct
    from types import SimpleNamespace
    from uuid import uuid4
    payer=direct.Payer(name="Pessoa Sintética",email="pessoa@example.test",
                phone="67999991111",doc_type="cpf",doc_number="52998224725",
                fingerprint="synthetic-fingerprint-abcde")
    charge=direct.DirectCharge(intent_id=uuid4(),method="pix",payer=payer,
               confirmed_monthly=True,consent_to_processor=True)
    intent={"offer_id":"synthetic-offer","expected_cents":8990}
    payload=direct.provider_request(intent,charge,"synthetic-checkout-binding")
    assert payload["paymentMethod"]=="pix_auto"
    assert payload["items"][0]["offerId"]=="synthetic-offer"
    assert payload["metadata"]["sck"]=="synthetic-checkout-binding"
    assert "card" not in payload
    assert "cvv" not in str(payload)
    ok={"id":"synthetic-order","paymentMethod":"pix_auto","offer":{"id":"synthetic-offer"},
        "amount":"89.90","status":"waiting_payment",
        "pix":{"qrCode":"synthetic-qr-code-" + "0" * 100,"user_journey":"JORNADA_1"}}
    assert direct.verified_creation(ok,intent,"pix")
    assert not direct.verified_creation({**ok,"amount":"88.90"},intent,"pix")
    assert not direct.verified_creation({**ok,"offer":{"id":"other"}},intent,"pix")
    assert not direct.verified_creation({**ok,"paymentMethod":"threeDs"},intent,"pix")
    assert not direct.verified_creation({**ok,"paymentMethod":"pix"},intent,"pix")
    assert not direct.verified_creation({**ok,"pix":{}},intent,"pix")
    assert not direct.verified_creation({**ok,"pix":{**ok["pix"],"user_journey":"UNKNOWN"}},intent,"pix")
