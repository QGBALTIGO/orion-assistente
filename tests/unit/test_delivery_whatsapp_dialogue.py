"""Tests for customer-facing Delivery conversation without WhatsApp I/O."""
from datetime import datetime, timedelta, timezone

import pytest

from app import delivery_whatsapp as wa


NOW = datetime(2026, 11, 27, 22, 0, tzinfo=timezone.utc)
SHOP = {
    "title": "Distribuidora QA",
    "allow_pickup": True, "allow_delivery": False,
    "pickup_details": "Rua de Teste, 1",
    "payment_methods": ["cash", "pix"],
    "opening_hours": [], "closed_dates": [], "timezone": "America/Campo_Grande",
    "accepting_orders": True,
}
WATER = {
    "variant_id": "00000000-0000-0000-0000-000000000001",
    "label": "Água mineral 20 L", "name": "Água mineral 20 L",
    "unit_price_cents": 1000, "mode": "tracked",
    "observed_at": NOW - timedelta(hours=1),
    "valid_until": NOW + timedelta(hours=1),
    "quantity_on_hand": 5, "reserved_quantity": 0,
}
CART = {"items": [{"variant_id": WATER["variant_id"], "quantity": 2}]}


def test_questions_have_real_answers_not_catalog_fallback():
    result = wa.information_reply("Tem desconto? Posso retirar?", SHOP, [WATER], {}, NOW)
    assert "desconto" in result and "*retirar no local*" in result
    assert "\n\n" in result
    assert "Rua de Teste, 1" in result
    assert "não estamos fazendo entregas" in wa.information_reply(
        "Quanto tempo leva a entrega?", SHOP, [WATER], {}, NOW
    )
    assert "💳" in wa.information_reply("Aceita pix?", SHOP, [WATER], {}, NOW)
    assert "atendendo agora" in wa.information_reply("Qual é o horário?", SHOP, [WATER], {}, NOW)


def test_delivery_does_not_invent_time_or_freight():
    shop = {**SHOP, "allow_delivery": True}
    reply = wa.information_reply("Quanto tempo leva a entrega?", shop, [WATER], {}, NOW)
    assert "Ainda não tenho uma previsão" in reply
    reply = wa.information_reply("Quanto é o frete?", shop, [WATER], {}, NOW)
    assert "depende do endereço" in reply
    shop.update(estimate_min_minutes=20, estimate_max_minutes=40)
    assert "*20 a 40 min*" in wa.information_reply("Quanto tempo leva a entrega?", shop, [WATER], {}, NOW)


def test_questions_preserve_confirmation_and_quote():
    state = {**CART, "stage": "confirm", "summary": "📝 *Seu pedido*\n\n*CONFIRMO*"}
    result = wa.information_reply("Aceita Pix?", SHOP, [WATER], state, NOW)
    assert state["summary"] in result
    assert state["items"][0]["quantity"] == 2
    assert wa.information_reply("Confirmo", SHOP, [WATER], state, NOW) is None


def test_pure_price_query_stays_on_original_offer_flow():
    assert wa.information_reply("Quanto custa uma água?", SHOP, [WATER], {}, NOW) is None
    assert "*R$ 10,00*" in wa.information_reply("Qual o preço?", SHOP, [WATER], CART, NOW)


def test_cart_edit_and_stock_guard():
    assert wa.revised_items("melhor uma água", [WATER], CART, NOW) == [
        {"variant_id": WATER["variant_id"], "quantity": 1}
    ]
    assert wa.revised_items("adiciona uma água", [WATER], CART, NOW) == [
        {"variant_id": WATER["variant_id"], "quantity": 3}
    ]
    assert wa.revised_items("melhor seis águas", [WATER], CART, NOW) == "unavailable"
    assert wa.revised_items("tira a água", [WATER], CART, NOW) == []
    assert CART["items"][0]["quantity"] == 2


def test_natural_cash_and_layout():
    assert wa.change("troco pra 50 reais") == 5000
    assert wa.change("para 20") == 2000
    assert wa.change("sem troco") is None
    with pytest.raises(ValueError):
        wa.change("qualquer valor")
    assert "*R$ 10,00*" in wa.list_products([WATER], NOW)
    assert "\n\n" in wa.state_question("payment", {}, SHOP, [WATER])


def test_cash_change_uses_combined_message_without_reasking():
    state = {}
    wa.set_payment(state, "Dinheiro, troco pra 50 reais", "cash")
    assert state == {"payment_preference": "cash", "change_for_cents": 5000}
    wa.set_payment(state, "Pix", "pix")
    assert state == {"payment_preference": "pix"}
    wa.set_payment(state, "Dinheiro, sem troco", "cash")
    assert state == {"payment_preference": "cash", "change_for_cents": None}
    assert wa.cash_change_from_message("dinheiro, troco pra algum valor") == (False, None)
    assert wa.cash_change_from_message("dinheiro, troco pra 50 ou 100") == (False, None)


def test_confirmation_buttons_bind_current_summary_and_receipt():
    text = "📝 *Seu pedido*\n\nTotal: *R$ 10,00*" + wa.CONFIRMATION_FALLBACK
    state = {"stage": "confirm", "summary": text}
    presentation = wa.confirmation_presentation(text, state)
    buttons = presentation["action"]["buttons"]
    assert presentation["action"]["text"] + wa.CONFIRMATION_FALLBACK == text
    assert [b["title"] for b in buttons] == ["Confirmar pedido", "Cancelar"]
    assert wa.confirmation_selection(state, buttons[0]["id"], "provider1", "provider1") == "confirmo"
    assert wa.confirmation_selection(state, buttons[1]["id"], "provider1", "provider1") == "nao"
    assert wa.confirmation_selection(state, buttons[0]["id"], "old-provider", "provider1") is None
    assert wa.confirmation_selection(state, buttons[0]["id"], "provider1", None) is None
    assert wa.confirmation_selection({}, buttons[0]["id"], "provider1", "provider1") is None
    updated = wa.confirmation_presentation(text, state)
    assert updated["id"] != presentation["id"]
    assert wa.confirmation_selection(state, buttons[0]["id"], "provider1", "provider1") is None
    assert wa.confirmation_presentation("Seu pedido mudou.", state) is None
    assert "delivery_presentation_id" not in state
    long_text = "🙂" * 600 + wa.CONFIRMATION_FALLBACK
    assert wa.confirmation_presentation(long_text, {"stage":"confirm","summary":long_text}) is None


@pytest.mark.parametrize("body", [
    "Meu gás está vazando", "Está com cheiro forte de gás", "O botijão pegou fogo",
    "A mangueira está vazando", "O registro não fecha", "O gás tá escapando",
    "Meu botijão caiu", "O botijão está danificado",
])
def test_gas_risk_interrupts_commercial_flow(body):
    assert wa.emergency(body)


@pytest.mark.parametrize("body", ["Tem água com gás?", "Qual o preço da água?", "Quero retirar meu pedido"])
def test_normal_questions_do_not_trigger_emergency(body):
    assert not wa.emergency(body)


@pytest.mark.parametrize("body,expected", [
    ("mesmo endereço", ("previous", None)),
    ("manda no mesmo endereço", ("previous", None)),
    ("usar endereço Casa", ("label", "casa")),
    ("entregar no endereço salvo Trabalho", ("label", "trabalho")),
    ("Qual meu endereço?", None),
    ("Me mostra o endereço do vizinho", None),
])
def test_address_reuse_requires_explicit_request(body, expected):
    assert wa.saved_address_request(body)==expected


def test_address_save_is_explicit_and_shared_number_is_conservative():
    assert wa.save_address_request("salvar endereço como Casa")=="Casa"
    assert wa.save_address_request("Hoje entrega na casa da minha mãe") is None
    assert wa.save_address_request("salvar endereço como Casa?") is None
    customer={"name":"Cliente QA", "active":True,"deleted_at":None}
    assert wa.verified_customer(customer,{"customer_name":"Cliente QA"})
    assert not wa.verified_customer(customer,{"customer_name":"Outra pessoa"})
    assert not wa.verified_customer({**customer,"active":False},{})
