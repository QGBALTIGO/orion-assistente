"""Internal conversation tests. Real answer/DTO/summary code, fake I/O only.

The fixtures are deliberately synthetic. No provider, customer, real database,
or WhatsApp session is accessed by these tests.
"""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from copy import deepcopy

from fastapi import HTTPException
import pytest

from app import delivery_whatsapp as wa, delivery_storefront as store


PHONE="+5567999999902"
TENANT="10000000-0000-4000-8000-000000000001"
WATER_ID="20000000-0000-4000-8000-000000000001"


class FakeDB:
    @asynccontextmanager
    async def begin_nested(self):
        yield


@pytest.fixture
def chat(monkeypatch):
    now=datetime.now(timezone.utc)
    water={"variant_id":WATER_ID,"label":"Água mineral 20 L","name":"Água mineral 20 L",
           "unit_price_cents":1250,"mode":"tracked","observed_at":now-timedelta(hours=1),
           "valid_until":now+timedelta(hours=1),"quantity_on_hand":5,"reserved_quantity":0,
           "exchange_container_id":None}
    shop={"title":"Distribuidora QA","allow_pickup":True,"allow_delivery":True,
          "pickup_details":"Depósito sintético","payment_methods":["cash","pix","debit_card","credit_card"],
          "opening_hours":[],"closed_dates":[],"timezone":"America/Campo_Grande","accepting_orders":True,
          "enabled":False,"slug":"qa"}
    calls=[]
    async def catalogue(db,tenant,*,lock=False):
        assert tenant==TENANT
        return shop,[water]
    async def customer(db,tenant,phone):
        assert tenant==TENANT and phone==PHONE
        return None
    async def previous(db,tenant,phone):
        return None
    async def prepare(db,tenant,config,draft,*,source):
        assert source=="whatsapp"
        quantity=draft.items[0].quantity
        if quantity>water["quantity_on_hand"]:
            raise HTTPException(409,"estoque")
        fee=450 if draft.mode=="delivery" else 0
        total=quantity*1250+fee
        if draft.change_for_cents is not None and draft.change_for_cents<total:
            raise HTTPException(422,"troco insuficiente")
        return {"items":[{"quantity":quantity,"product_name":water["label"],"total_cents":quantity*1250}],
                "delivery_fee_cents":fee,"total_cents":total,"eta_minutes":30 if fee else None},[],{"policy_revision":1}
    async def save(db,tenant,config,proposal,*,source):
        assert tenant==TENANT and proposal.customer_phone==PHONE
        calls.append(proposal.model_dump(mode="json"))
        return {"order_code":"ABCDEF01","total_cents":1250*proposal.items[0].quantity+(450 if proposal.mode=="delivery" else 0)}
    monkeypatch.setattr(wa,"shop_and_items",catalogue)
    monkeypatch.setattr(wa,"customer_for_phone",customer)
    monkeypatch.setattr(wa,"previous_verified_name",previous)
    monkeypatch.setattr(store,"prepare_checkout",prepare)
    monkeypatch.setattr(store,"quote_signature",lambda *args:"a"*64)
    monkeypatch.setattr(store,"save_checkout",save)
    class Chat:
        state={}
        response=""
        def __init__(self):
            self.state={}
            self.shop=shop
            self.calls=calls
        async def say(self,body,*,delivered=True,audio=False):
            self.response,self.state=await wa.answer(
                FakeDB(),TENANT,PHONE,body,self.state,delivered,allow_confirmation=not audio)
            return self.response
        async def pickup(self):
            await self.say("Quero uma água para retirar")
            await self.say("Mariana")
            await self.say("dinheiro, troco pra 50 reais")
            assert self.state["stage"]=="confirm",self.state
            return deepcopy(self.state)
    return Chat()


@pytest.mark.asyncio
async def test_payment_correction_invalidates_old_summary(chat):
    old=await chat.pickup()
    await chat.say("na verdade vou pagar no Pix")
    assert chat.state["payment_preference"]=="pix"
    assert "change_for_cents" not in chat.state
    assert chat.state["proposal"]["idempotency_key"]!=old["proposal"]["idempotency_key"]
    assert "Pix" in chat.response and "Troco previsto" not in chat.response
    assert not chat.calls


@pytest.mark.asyncio
async def test_change_correction_invalidates_old_summary(chat):
    old=await chat.pickup()
    await chat.say("troco pra 100 reais")
    assert chat.state["change_for_cents"]==10000
    assert chat.state["proposal"]["idempotency_key"]!=old["proposal"]["idempotency_key"]
    assert "R$ 87,50" in chat.response and not chat.calls


@pytest.mark.asyncio
async def test_switch_pickup_to_delivery_requests_address(chat):
    await chat.pickup()
    await chat.say("na verdade quero entrega")
    assert chat.state["mode"]=="delivery" and chat.state["stage"]=="address"
    assert "proposal" not in chat.state and "summary" not in chat.state
    assert not chat.calls


@pytest.mark.asyncio
async def test_delivery_to_pickup_removes_address_and_fee(chat):
    await chat.say("Quero uma água para entrega")
    await chat.say("Mariana")
    await chat.say("Rua Teste, 12, Centro, Campo Grande, MS")
    await chat.say("Pix")
    old=deepcopy(chat.state)
    assert old["stage"]=="confirm"
    await chat.say("prefiro retirar")
    assert chat.state["stage"]=="confirm" and chat.state["mode"]=="pickup"
    assert not chat.state.get("address")
    assert "R$ 12,50" in chat.response and "R$ 17,00" not in chat.response
    assert chat.state["proposal"]["idempotency_key"]!=old["proposal"]["idempotency_key"]


@pytest.mark.asyncio
async def test_payment_given_before_name_is_not_stored_as_name(chat):
    await chat.say("Quero uma água para retirar")
    await chat.say("vou pagar no Pix")
    assert chat.state["stage"]=="name"
    assert not chat.state.get("customer_name")
    assert chat.state["payment_preference"]=="pix"
    await chat.say("Mariana")
    assert chat.state["stage"]=="confirm" and chat.state["customer_name"]=="Mariana"
    assert "Pix" in chat.response


@pytest.mark.asyncio
@pytest.mark.parametrize("body",["sim","oi","tudo bem","pode mandar","kkkk","não sei","👍"])
async def test_short_replies_are_not_customer_names(chat,body):
    await chat.say("Quero uma água para retirar")
    await chat.say(body)
    assert chat.state["stage"]=="name"
    assert not chat.state.get("customer_name")
    assert not chat.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("body",["Aceita Pix?","Tem desconto?","Posso retirar?"])
async def test_questions_do_not_mutate_review(chat,body):
    old=await chat.pickup()
    await chat.say(body)
    assert chat.state["proposal"]==old["proposal"]
    assert chat.state["summary"] in chat.response
    assert not chat.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("body",["Confirmo, mas se ficar mais barato","sim?","pode, se for de graça","não confirmo"])
async def test_ambiguous_confirmation_never_commits(chat,body):
    await chat.pickup()
    await chat.say(body)
    assert not chat.calls


@pytest.mark.asyncio
async def test_audio_cannot_confirm_without_text(chat):
    await chat.pickup()
    await chat.say("confirmo",audio=True)
    assert not chat.calls and chat.state["stage"]=="confirm"
    assert "pelo botão ou por texto" in chat.response
    await chat.say("confirmo",delivered=True)
    assert len(chat.calls)==1 and chat.state["last_order"]=="ABCDEF01"


@pytest.mark.asyncio
async def test_undelivered_summary_does_not_commit(chat):
    await chat.pickup()
    await chat.say("confirmo",delivered=False)
    assert not chat.calls and chat.state["stage"]=="confirm"


@pytest.mark.asyncio
@pytest.mark.parametrize("body",["Meu gás está vazando","O botijão pegou fogo","O registro não fecha"])
async def test_emergency_interrupts_an_existing_review(chat,body):
    await chat.pickup()
    reply=await chat.say(body)
    assert "193" in reply and "local seguro" in reply
    assert chat.state=={"paused":True}
    await chat.say("confirmo")
    assert not chat.calls and chat.response is None


@pytest.mark.asyncio
async def test_cancel_draft_is_not_confirmed_order(chat):
    await chat.pickup()
    await chat.say("cancelar")
    assert not chat.calls and not chat.state


@pytest.mark.asyncio
@pytest.mark.parametrize("body",["vou pagar no Pix ou dinheiro","prefiro cartão","vou pagar no Pix, troco pra 50 reais"])
async def test_ambiguous_payment_revokes_old_quote(chat,body):
    await chat.pickup()
    await chat.say(body)
    assert chat.state["stage"]=="payment"
    assert "proposal" not in chat.state and "summary" not in chat.state
    await chat.say("confirmo")
    assert not chat.calls


@pytest.mark.asyncio
async def test_new_payment_not_available_revokes_old_quote(chat):
    await chat.pickup()
    chat.shop["payment_methods"]=["cash"]
    await chat.say("vou pagar no Pix")
    assert chat.state["stage"]=="payment" and not chat.state.get("proposal")
    await chat.say("confirmo")
    assert not chat.calls


@pytest.mark.asyncio
async def test_address_correction_revokes_quote(chat):
    await chat.say("Quero uma água para entrega")
    await chat.say("Mariana")
    await chat.say("Rua Teste, 12, Centro, Campo Grande, MS")
    await chat.say("Pix")
    old=deepcopy(chat.state)
    await chat.say("mudei de endereço")
    assert chat.state["stage"]=="address" and not chat.state.get("address")
    assert not chat.state.get("proposal")
    await chat.say("Rua de Ensaio, 34, Centro, Campo Grande, MS")
    assert chat.state["stage"]=="confirm"
    assert chat.state["proposal"]["idempotency_key"]!=old["proposal"]["idempotency_key"]
    assert "Rua de Ensaio, 34" in chat.response and "Rua Teste" not in chat.response


@pytest.mark.asyncio
async def test_insufficient_new_troco_does_not_confirm_old_order(chat):
    await chat.pickup()
    await chat.say("troco pra 5 reais")
    assert not chat.calls and "CONFIRMO" not in chat.response
    assert not chat.state.get("proposal")


@pytest.mark.asyncio
async def test_changed_review_rejects_old_button(chat):
    await chat.pickup()
    first=wa.confirmation_presentation(chat.response,chat.state)
    first_id=first["action"]["buttons"][0]["id"]
    await chat.say("vou pagar no Pix")
    new=wa.confirmation_presentation(chat.response,chat.state)
    new_id=new["action"]["buttons"][0]["id"]
    assert new_id!=first_id
    assert wa.confirmation_selection(chat.state,first_id,"old","new") is None
    assert wa.confirmation_selection(chat.state,new_id,"new","new")=="confirmo"
    assert not chat.calls


@pytest.mark.asyncio
async def test_gas_inquiry_does_not_create_water_order(chat):
    await chat.pickup()
    reply=await chat.say("quero gás industrial")
    assert "equipe" in reply
    assert not chat.state.get("proposal") and not chat.calls
