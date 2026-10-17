"""Customer commerce: consent is a new turn, previews and research do not write."""
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException
import pytest

from app import commercial_service, qr_commerce as flow, qr_commerce_store as store
from app.qr_commerce_understanding import explicit_confirmation, interpret


@pytest.fixture
def catalog():
    return [
        {"id": uuid4(), "name": "Pomada", "synonyms": ["modelador"], "description": "Acabamento fosco cadastrado.",
         "official_benefits": ["Fixação média informada pelo fabricante."], "faqs": [{"question": "A pomada tem perfume?", "answer": "Sem perfume, conforme cadastro."}],
         "variants": [{"id": uuid4(), "name": "Pote 50g", "attributes": {"peso": "50g"}, "price_kind": "fixed", "unit_price_cents": 2500}]},
        {"id": uuid4(), "name": "Camiseta", "synonyms": [], "faqs": [], "variants": [
            {"id": uuid4(), "name": "Azul P", "attributes": {"cor": "azul", "tamanho": "P"}, "price_kind": "fixed", "unit_price_cents": 5000},
            {"id": uuid4(), "name": "Azul M", "attributes": {"cor": "azul", "tamanho": "M"}, "price_kind": "fixed", "unit_price_cents": 5500},
            {"id": uuid4(), "name": "Vermelha M", "attributes": {"cor": "vermelha", "tamanho": "M"}, "price_kind": "fixed", "unit_price_cents": 5500},
         ]},
    ]


@pytest.mark.parametrize("body,intent,qty", [
    ("Quanto custa 2 pomada?", "price", 2),
    ("Quero comprar duas pomada", "order", 2),
    ("Quero comprar duas pomadas", "order", 2),
    ("Quero um orçamento de 3 pomada", "quote", 3),
    ("Só estou pesquisando o preço da pomada", "price", 1),
    ("Não quero comprar, só o preço da pomada", "price", 1),
    ("Tem pomada disponível?", "stock", 1),
    ("Como cancelar meu pedido?", "faq", None),
])
def test_distinct_intents_do_not_create_authority(catalog, body, intent, qty):
    plan = interpret(body, catalog)
    assert plan.intent == intent
    if qty is not None:
        assert plan.items == [{"variant_id": str(catalog[0]["variants"][0]["id"]), "quantity": qty}]


@pytest.mark.parametrize("body", ["sim, mas muda para duas", "sim?", "acho que sim", "não confirmo", "confirmo se tiver desconto", "sim e adicionar pomada"])
def test_qualified_or_conditional_text_is_not_confirmation(body):
    assert explicit_confirmation(body) is False


def test_variant_ambiguity_and_exact_attributes(catalog):
    ambiguous = interpret("quero comprar 2 camiseta", catalog)
    assert ambiguous.items == [] and ambiguous.ambiguous_quantities == {str(catalog[1]["id"]): 2}
    chosen = interpret("azul M", catalog, choices=ambiguous.ambiguous_quantities)
    assert chosen.items == [{"variant_id": str(catalog[1]["variants"][1]["id"]), "quantity": 2}]


@pytest.mark.parametrize("quantity", ["0", "1001", "-2", "2,5"])
def test_invalid_quantity_does_not_become_one(catalog, quantity):
    plan = interpret(f"Quero comprar {quantity} pomada", catalog)
    assert plan.invalid_quantity and not plan.items


@pytest.mark.parametrize("phrase,expected",[("onze",11),("doze",12),("vinte e dois",22),("meia dúzia de",6),("uma dúzia de",12)])
def test_spoken_quantities_are_preserved(catalog,phrase,expected):
    plan=interpret("Quero comprar "+phrase+" pomadas",catalog)
    assert not plan.invalid_quantity and plan.items[0]["quantity"]==expected


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase",["muitas","algumas","duzentas","centenas de","1/2","dois ou três"])
async def test_unknown_explicit_quantity_clarifies_before_writing(backend,phrase):
    events,_=backend
    result=await turn("Quero comprar "+phrase+" pomadas")
    assert result["step"]=="commerce_quantity" and events==[]


def test_package_size_and_sku_numbers_do_not_become_order_quantity(catalog):
    catalog[0]["variants"][0].update(name="Pote 250mL",attributes={"volume":"250mL","SKU":"PX250"})
    assert interpret("Quero comprar pomada Pote 250mL",catalog).items[0]["quantity"]==1
    assert interpret("Quero comprar duas pomadas Pote 250mL",catalog).items[0]["quantity"]==2


def test_correction_quantity_and_longest_product_name(catalog):
    vid = str(catalog[0]["variants"][0]["id"])
    plan = interpret("melhor duas", catalog, [{"variant_id": vid, "quantity": 1}])
    assert plan.intent == "adjust" and plan.items == [{"variant_id": vid, "quantity": 2}]
    specific = {**catalog[0], "id": uuid4(), "name": "Pomada infantil", "synonyms": [],
                "variants": [{**catalog[0]["variants"][0], "id": uuid4()}]}
    plan = interpret("quero comprar pomada infantil", [catalog[0], specific])
    assert plan.items == [{"variant_id": str(specific["variants"][0]["id"]), "quantity": 1}]


@pytest.fixture
def backend(monkeypatch, catalog):
    monkeypatch.setenv("COMMERCIAL_CATALOG_ENABLED", "1")
    monkeypatch.setenv("QR_AI_COMMERCE_ENABLED", "1")
    events = []
    data = {"stock_mode": "tracked", "confirm_error": None, "quotes": {}, "orders": {}}
    async def active(_):
        return catalog
    async def estimate(scope, items):
        events.append("estimate")
        now = datetime.now(timezone.utc)
        rows = []
        for item in items:
            product, variant = next((p, v) for p in catalog for v in p["variants"] if str(v["id"]) == str(item["variant_id"]))
            rows.append({"product_id": product["id"], "product_name": product["name"], "variant_id": variant["id"],
                "variant_name": variant["name"], "attributes": variant["attributes"], "price_kind": variant["price_kind"],
                "unit_price_cents": variant["unit_price_cents"], "quantity": item["quantity"], "mode": data["stock_mode"],
                "quantity_on_hand": 100, "reserved_quantity": 0, "observed_at": now - timedelta(seconds=1),
                "valid_until": now + timedelta(minutes=5), "source": "Synthetic count", "product_revision": 1,
                "variant_revision": 1, "inventory_revision": 1})
        return commercial_service.calculate(rows, now)
    async def quote(scope, items, **kwargs):
        facts = await estimate(scope, items)
        if kwargs.get("validate_summary"):
            kwargs["validate_summary"](facts)
        events.append("quote")
        result = {"id": scope.identity("quote"), "expires_at": datetime.now(timezone.utc) + timedelta(minutes=15),
                "confirmation_token": "x" * 43, **facts}
        data["quotes"][result["id"]] = result
        return result
    async def order(scope, quote_id, **kwargs):
        if kwargs.get("validate_summary"):
            kwargs["validate_summary"](data["quotes"][quote_id])
        events.append("order")
        result = {"id": scope.identity("order"), "quote_id": quote_id, "status": "draft", "revision": 1,
                  "quote": data["quotes"][quote_id]}
        data["orders"][result["id"]] = result
        return result
    async def get_order(scope, order_id):
        return data["orders"][order_id]
    async def confirm(scope, order_id, **kwargs):
        events.append("confirm")
        assert scope.message_id != kwargs["summary_message_id"]
        if data["confirm_error"]:
            raise data["confirm_error"]
        kwargs["validate_summary"](data["orders"][order_id]["quote"])
        return {**data["orders"][order_id], "status": "confirmed"}
    async def cancel(scope, order_id, **kwargs):
        kwargs["validate_summary"](data["orders"][order_id]["quote"])
        events.append("cancel")
        return {**data["orders"][order_id], "status": "cancelled"}
    monkeypatch.setattr(store, "active_catalog", active)
    monkeypatch.setattr(store, "estimate", estimate)
    monkeypatch.setattr(store, "create_quote", quote)
    monkeypatch.setattr(store, "create_order", order)
    monkeypatch.setattr(store, "confirm_order", confirm)
    monkeypatch.setattr(store, "cancel_order", cancel)
    monkeypatch.setattr(store, "get_order", get_order)
    return events, data


async def turn(body, *, state=None, message="message-1", preview=False, allow=True, tenant=None, phone="5567999999901", **kwargs):
    return await flow.handle(tenant or UUID("11111111-1111-4111-8111-111111111111"), phone, body,
        message_id=message, state=state, preview=preview, allow_orders=allow, **kwargs)


@pytest.mark.asyncio
async def test_complete_oversized_summary_prevents_all_writes_and_retains_selection(backend, catalog):
    events, _ = backend
    catalog[0]["variants"][0]["attributes"] = {"atributo" + str(i): "A" * 80 for i in range(12)}
    result = await turn("Quero comprar pomada", preferences={"length": "detailed"})
    assert result["step"] == "commerce_split_review" and len(result["answer"]) <= 1200
    assert result["state"]["cart"] and "pending" not in result["state"]
    assert events == ["estimate"]


@pytest.mark.asyncio
async def test_emoji_transport_units_are_checked_before_quote_or_order(backend,catalog):
    from app.qr_presentation import render_commerce_order, message_units
    events,_=backend
    catalog[0]["variants"][0]["attributes"]={"atributo"+str(i):"😀"*80 for i in range(6)}
    scope=store.CustomerScope(UUID("11111111-1111-4111-8111-111111111111"),"5567999999901","synthetic")
    facts=await store.estimate(scope,[{"variant_id":str(catalog[0]["variants"][0]["id"]),"quantity":1}])
    rendered=render_commerce_order(facts,{"length":"detailed"})
    assert len(rendered)<1200<message_units(rendered)
    events.clear()
    result=await turn("Quero comprar pomada",preferences={"length":"detailed"})
    assert result["step"]=="commerce_split_review" and events==["estimate"]


@pytest.mark.asyncio
async def test_facts_changed_between_estimate_and_quote_rechecked_before_insert(backend, catalog, monkeypatch):
    events, _ = backend
    original = store.create_quote
    async def changing(scope, items, **kwargs):
        catalog[0]["variants"][0]["attributes"] = {"atributo" + str(i): "A" * 80 for i in range(12)}
        return await original(scope, items, **kwargs)
    monkeypatch.setattr(store, "create_quote", changing)
    result = await turn("Quero comprar pomada", preferences={"length": "detailed"})
    assert result["step"] == "commerce_split_review"
    assert "quote" not in events and "order" not in events


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["confirm", "cancel"])
async def test_outcome_preflight_blocks_mutation_when_full_summary_no_longer_fits(backend, action):
    events, data = backend
    first = await turn("Quero comprar pomada")
    if action == "cancel":
        first = await turn("cancelar meu pedido", state=first["state"], message="cancel-request")
    for value in data["quotes"].values():
        value["items"][0]["attributes"] = {"atributo" + str(i): "A" * 80 for i in range(12)}
    result = await turn("pode cancelar" if action == "cancel" else "pode confirmar", state=first["state"], message="confirmation-new", preferences={"length": "detailed"})
    assert result["step"] == "commerce_split_review" and action not in events
    assert result["state"]["cart"] and "pending" not in result["state"]


@pytest.mark.asyncio
async def test_contextual_semantics_never_override_question_or_change(backend):
    events, _ = backend
    first = await turn("Quero comprar pomada")
    uncertain = await turn("Pode ser, quanto fica?", state=first["state"], message="question", semantic_kind="affirm")
    assert "confirm" not in events
    changed = await turn("Pode confirmar mas melhor duas", state=first["state"], message="change", semantic_kind="affirm")
    assert "confirm" not in events and changed["state"]["cart"][0]["quantity"] == 2
    done = await turn("Do jeito que você mostrou, pode seguir", state=first["state"], message="natural", semantic_kind="affirm")
    assert done["step"] == "commerce_confirmed" and "R$ 25,00" in done["answer"]
    assert first["state"]["last_order_id"] not in done["answer"]


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["Confirmo se tiver desconto", "Pode confirmar desde que aceitem Pix",
    "Caso consigam entregar, pode confirmar", "Pode seguir a menos que haja taxa",
    "Contanto que seja o mesmo preço, confirmo"])
async def test_product_conditional_acceptance_never_confirms_even_with_semantic_affirm(backend, text):
    events, _ = backend
    first = await turn("Quero comprar pomada")
    result = await turn(text, state=first["state"], message="conditional", semantic_kind="affirm")
    assert result is None or result["step"] != "commerce_confirmed"
    assert events.count("order") == 1 and "confirm" not in events and "cancel" not in events


@pytest.mark.asyncio
async def test_cancel_requires_its_own_fresh_summary_and_new_consent(backend):
    events, _ = backend
    first = await turn("Quero comprar pomada")
    proposal = await turn("cancelar meu pedido", state=first["state"], message="cancel-request")
    assert proposal["step"] == "commerce_cancel_confirm" and "cancel" not in events
    assert "_verified_operation" not in proposal
    done = await turn("pode cancelar", state=proposal["state"], message="cancel-consent")
    assert done["step"] == "commerce_cancelled" and events.count("cancel") == 1
    assert done["_verified_operation"] is True and "_verified_operation" not in done["state"]
    assert "*Pomada*" in done["answer"] and "Pagamento não verificado" in done["answer"]


@pytest.mark.asyncio
async def test_routing_probe_is_read_only_even_for_explicit_purchase(backend):
    events, _ = backend
    found = await flow.probe(UUID("11111111-1111-4111-8111-111111111111"), "5567999999901", "Quero comprar pomada e agendar corte", message_id="mixed")
    assert found is True and events == []


@pytest.mark.asyncio
async def test_price_research_has_no_quote_or_order_or_call_to_action(backend):
    events, _ = backend
    result = await turn("Só o preço de duas pomada")
    assert "R$ 50,00" in result["answer"] and "CONFIRMO" not in result["answer"]
    assert events == ["estimate"] and result["state"]["cart"] == []


@pytest.mark.asyncio
async def test_catalog_existence_does_not_assume_one_unit_or_change_cart(backend, catalog):
    events, _ = backend
    first = await turn("Vocês vendem pomada?")
    assert "Pomada" in first["answer"] and "R$" not in first["answer"]
    assert "não confirma disponibilidade" in first["answer"]
    first["state"]["cart"] = [{"variant_id": str(catalog[0]["variants"][0]["id"]), "quantity": 3}]
    again = await turn("Vendem camiseta?", state=first["state"], message="existence-next")
    assert again["state"]["cart"] == first["state"]["cart"]
    assert "Camiseta" in again["answer"] and "R$" not in again["answer"] and events == []


@pytest.mark.asyncio
async def test_superseded_write_is_not_caught_as_price_change_or_retried(backend, monkeypatch):
    from app.qr_intake_revision import Superseded
    calls = []
    async def superseded(*args, **kwargs):
        calls.append("quote")
        raise Superseded(not_committed=True)
    monkeypatch.setattr(store, "create_quote", superseded)
    with pytest.raises(Superseded) as stale:
        await turn("Quero comprar duas pomadas")
    assert stale.value.not_committed and calls == ["quote"]


@pytest.mark.asyncio
async def test_order_summary_then_new_confirmation_and_duplicate_message(backend):
    events, _ = backend
    prepared = await turn("Quero comprar duas pomada")
    assert prepared["step"] == "commerce_confirm" and "confirm" not in events
    assert "_verified_operation" not in prepared
    duplicate = await turn("CONFIRMO", state=prepared["state"], message="message-1")
    assert duplicate["answer"] == prepared["answer"] and "confirm" not in events
    done = await turn("CONFIRMO", state=prepared["state"], message="message-2")
    assert done["step"] == "commerce_confirmed" and events.count("confirm") == 1
    assert done["_verified_operation"] is True and "_verified_operation" not in done["state"]
    duplicate_done = await turn("CONFIRMO", state=done["state"], message="message-2")
    assert duplicate_done["answer"] == done["answer"] and events.count("confirm") == 1
    assert "_verified_operation" not in duplicate_done


@pytest.mark.asyncio
async def test_preview_runs_estimator_but_no_persistent_actions(backend):
    events, _ = backend
    prepared = await turn("Quero comprar duas pomada", preview=True)
    done = await turn("CONFIRMO", state=prepared["state"], message="preview-2", preview=True)
    assert events == ["estimate"]
    assert prepared["step"] == "commerce_preview" and done["step"] == "commerce_preview_complete"
    assert "confirmation_token" not in str(prepared["state"])
    assert "_verified_operation" not in prepared and "_verified_operation" not in done


@pytest.mark.asyncio
async def test_unknown_stock_never_offers_confirmation(backend):
    events, data = backend
    data["stock_mode"] = "unknown"
    prepared = await turn("Quero comprar pomada")
    assert prepared["step"] == "commerce_review_required" and "pending" not in prepared["state"]
    assert "disponibilidade de estoque precisa" in prepared["answer"]
    await turn("CONFIRMO", state=prepared["state"], message="message-2")
    assert "confirm" not in events


@pytest.mark.asyncio
async def test_variants_retain_requested_quantity_after_clarification(backend):
    events, _ = backend
    first = await turn("Quero comprar duas camiseta")
    assert first["step"] == "commerce_choose_variant" and events == []
    second = await turn("azul M", state=first["state"], message="message-2")
    assert second["step"] == "commerce_confirm" and "*Camiseta*" in second["answer"] and "2 un." in second["answer"]
    assert "R$ 110,00" in second["answer"]


@pytest.mark.asyncio
async def test_correction_revokes_token_and_requires_another_summary(backend):
    events, _ = backend
    first = await turn("Quero comprar pomada")
    second = await turn("melhor duas", state=first["state"], message="message-2")
    assert second["step"] == "commerce_confirm" and "*Pomada*" in second["answer"] and "2 un." in second["answer"]
    assert second["state"]["pending"]["summary_message_id"] == "message-2"
    assert "confirm" not in events


@pytest.mark.asyncio
async def test_add_and_remove_items_require_new_summary(backend):
    events, _ = backend
    first = await turn("Quero comprar duas pomadas")
    second = await turn("adiciona uma pomada", state=first["state"], message="message-2")
    assert "3 un." in second["answer"] and "confirm" not in events
    third = await turn("retira a pomada", state=second["state"], message="message-3")
    assert third["step"] == "commerce_empty" and "pending" not in third["state"]


@pytest.mark.asyncio
async def test_catalog_change_creates_fresh_summary_without_confirming(backend):
    events, data = backend
    first = await turn("Quero comprar pomada")
    data["confirm_error"] = HTTPException(409, "catalog changed")
    second = await turn("CONFIRMO", state=first["state"], message="message-2")
    assert second["step"] == "commerce_confirm" and "não foi confirmado" in second["answer"]
    assert second["state"]["pending"]["summary_message_id"] == "message-2"


@pytest.mark.asyncio
async def test_information_between_summary_and_yes_requires_new_summary(backend):
    events, _ = backend
    first = await turn("Quero comprar pomada")
    research = await turn("Quanto fica duas pomada?", state=first["state"], message="message-2")
    result = await turn("CONFIRMO", state=research["state"], message="message-3")
    assert result["step"] == "commerce_new_confirmation_required" and "confirm" not in events


@pytest.mark.asyncio
async def test_decline_clears_pending_and_disabled_capability_does_not_write(backend):
    events, _ = backend
    first = await turn("Quero comprar pomada")
    declined = await turn("não", state=first["state"], message="message-2")
    assert "pending" not in declined["state"] and "confirm" not in events
    events.clear()
    result = await turn("Quero comprar pomada", allow=False)
    assert result["step"] == "commerce_information" and events == ["estimate"]


@pytest.mark.asyncio
async def test_contact_cannot_reuse_another_contacts_state(backend):
    first = await turn("Quero comprar pomada")
    with pytest.raises(HTTPException) as denied:
        await turn("CONFIRMO", state=first["state"], message="message-2", phone="5567999999902")
    assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_official_faq_and_benefits_are_returned_without_sales_writes(backend):
    events, _ = backend
    faq = await turn("A pomada tem perfume?")
    assert faq["answer"] == "Sem perfume, conforme cadastro."
    benefits = await turn("Qual o benefício da pomada?", message="message-2")
    assert "Acabamento fosco cadastrado" in benefits["answer"]
    assert events == []


def test_customer_scope_cannot_impersonate_operator_or_accept_user_tenant_string():
    with pytest.raises(ValueError):
        store.CustomerScope("not-verified-uuid", "5567999999901", "synthetic")
    with pytest.raises(ValueError):
        store.CustomerScope(uuid4(), "5567999999901", "synthetic", role="support")


def test_confirmation_token_binds_contact_tenant_and_quote(monkeypatch):
    monkeypatch.setenv("QR_BRIDGE_TOKEN", "synthetic-key-" + "x" * 32)
    a = store.CustomerScope(uuid4(), "5567999999901", "synthetic")
    b = store.CustomerScope(a.tenant, "5567999999902", "synthetic")
    quote_id = uuid4()
    assert store.confirmation_token(a, quote_id) != store.confirmation_token(b, quote_id)
    assert store.confirmation_token(a, quote_id) != store.confirmation_token(a, uuid4())


@pytest.mark.asyncio
async def test_only_one_approved_complement_is_optional_and_refusal_persists(backend,catalog,monkeypatch):
    events,_=backend
    calls=[]
    target=uuid4()
    async def related(scope,product_id):
        calls.append(product_id)
        return [{"approved_relation_id":uuid4(),"target_product_id":target,"name":"Pente oficial"},
                {"approved_relation_id":uuid4(),"target_product_id":uuid4(),"name":"Escova oficial"}]
    monkeypatch.setattr(store,"approved_complements",related)
    prefs={"commercial_intensity":"contextual"}
    offered=await turn("Qual o benefício da pomada?",preferences=prefs)
    assert "Pente oficial" in offered["answer"] and "Escova oficial" not in offered["answer"]
    assert offered["state"]["cart"]==[] and events==[] and len(calls)==1
    declined=await turn("não quero adicionais",state=offered["state"],message="refusal",preferences=prefs)
    assert declined["state"]["declined_complements"] is True
    again=await turn("Qual o benefício da pomada?",state=declined["state"],message="later",preferences=prefs)
    assert "Pente oficial" not in again["answer"] and len(calls)==1


@pytest.mark.asyncio
@pytest.mark.parametrize("body",["Só quero o preço da pomada","Estou só pesquisando, qual o benefício da pomada?",
                                "Tenho uma reclamação sobre o benefício da pomada"])
async def test_price_research_or_complaint_never_fetches_or_offers_complement(backend,monkeypatch,body):
    async def forbidden(*_): raise AssertionError("Optional offers must be suppressed before reading relations")
    monkeypatch.setattr(store,"approved_complements",forbidden)
    result=await turn(body,preferences={"commercial_intensity":"contextual"})
    assert "complementar" not in result["answer"] and "quote" not in backend[0]


@pytest.mark.asyncio
async def test_semantic_confirmation_fallback_only_for_fresh_unclassified_pending(backend,monkeypatch):
    from app.qr_dialogue import DialogueDecision
    calls=[]
    async def semantic(**kwargs):
        calls.append(kwargs)
        return DialogueDecision("affirm","semantic")
    monkeypatch.setattr(flow,"interpret_contextual_response",semantic)
    first=await turn("Quero comprar pomada")
    done=await turn("Do jeito que você mostrou, pode seguir",state=first["state"],message="new-natural")
    assert done["step"]=="commerce_confirmed" and len(calls)==1
    await turn("Conforme conversamos",state=first["state"],message="already-classified",semantic_kind="uncertain")
    assert len(calls)==1
    stale={**first["state"],"last_message_id":"intermediate-question"}
    result=await turn("Do jeito que você mostrou, pode seguir",state=stale,message="old-summary")
    assert len(calls)==1 and result["step"]=="commerce_clarify_confirmation"
