"""Delivery conversation on the existing authenticated device/effect broker.

Only explicitly selected, eligible water items are admitted in this pilot.
Reuse the reception's conservative product and confirmation interpreter and
the exact same signed quote, stock reservation, and manual Pix as the store.
No external send, language-model call or provider I/O runs in a transaction.
"""
from datetime import datetime, timezone
from decimal import Decimal
import json
import re
from uuid import uuid4

from cryptography.fernet import InvalidToken
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import StrictBool, StrictInt, Field, ValidationError
from sqlalchemy import text

from app import qr_channels as channels
from app.business_profile_schemas import ProfileModel
from app.database import get_session_factory, tenant_transaction
from app.management_schemas import normalize_phone, MAX_MONEY
from app.onboarding_schemas import Address
from app.qr_commerce_understanding import interpret, norm
from app.qr_dialogue import contextual_response
from app.workspace_policy import delivery_preview_enabled

PURPOSE = "delivery_reply"
STATE_TTL = 1800
CONFIRMATION_FALLBACK = "\nResponda *CONFIRMO* para registrar o pedido ou *NÃO* para cancelar o resumo."
CONFIRMATION_STALE = "Esse botão não corresponde ao resumo atual. Nenhum novo pedido foi registrado."
PAYMENTS = {"cash": "dinheiro", "pix": "Pix manual", "debit_card": "cartão de débito",
            "credit_card": "cartão de crédito"}
HELP = "Se quiser falar com nossa equipe, é só escrever *atendente*."
ADDRESS_PROMPT = ("📍 Me passa o endereço de entrega, por favor?\n\n"
                  "Rua, número, bairro, cidade e estado. Se tiver complemento ou ponto de referência, pode mandar também.\n\n"
                  "_Ex.: Rua das Flores, 123, Centro, Cidade, MS_")


class Settings(ProfileModel):
    expected_revision: StrictInt = Field(ge=1)
    enabled: StrictBool


def enabled(device):
    return device["settings"].get("delivery_assistant_enabled") is True


def money(cents):
    value = int(cents)
    return f"R$ {value // 100},{value % 100:02d}"


def clean(body):
    return norm(body).strip(" .!?")


def affirmative(body):
    return contextual_response(body, awaiting_confirmation=True, confirmation_action="order").kind == "affirm"


def emergency(body):
    """Recognize immediate gas/fire risk before any commercial interpretation."""
    value = clean(body)
    return bool(re.search(r"\b(?:vazamento|incendio|peg(?:ando|ou) fogo|explosao|"
        r"cheiro (?:forte )?de gas|gas (?:esta |ta )?(?:vazando|escapando)|"
        r"(?:botijao|mangueira|registro|valvula) (?:esta |ta )?vazando|"
        r"registro nao fecha|botijao (?:caiu|danificado)|"
        r"(?:botijao|mangueira|registro) (?:esta |ta )?(?:danificado|danificada|com defeito))\b", value))


async def customer_for_phone(db, tenant, phone):
    """Use the canonical tenant/phone identity, never a provider display name."""
    row = (await db.execute(text("""SELECT id,name,active,deleted_at FROM customers
        WHERE tenant_id=:t AND whatsapp_e164=:phone"""),
        {"t":tenant,"phone":phone})).mappings().one_or_none()
    return dict(row) if row is not None else None


async def previous_verified_name(db, tenant, phone):
    # Older WhatsApp checkouts retained the verified phone in the immutable
    # quote but did not link the CRM until fulfillment. Use the name only
    # when that contact has one unambiguous name, and show it in the review.
    names = (await db.execute(text("""SELECT DISTINCT q.customer_name FROM commercial_orders o
        JOIN commercial_quotes q ON (q.tenant_id,q.id)=(o.tenant_id,o.quote_id)
        JOIN delivery_order_details d ON (d.tenant_id,d.order_id)=(o.tenant_id,o.id)
        WHERE o.tenant_id=:t AND o.origin='delivery' AND q.contact_e164=:phone
          AND d.intake_channel='whatsapp' AND o.status IN ('confirmed','dispatched','fulfilled')
          AND q.customer_name IS NOT NULL AND btrim(q.customer_name)<>''
        LIMIT 2"""),{"t":tenant,"phone":phone})).scalars().all()
    return names[0] if len(names)==1 else None


def saved_address_request(body):
    """An explicit request, never an inferred address from unrelated prose."""
    value = clean(body)
    if value in {"mesmo endereco", "no mesmo endereco", "o mesmo endereco",
                 "mesmo endereco da ultima entrega", "no mesmo endereco da ultima entrega",
                 "mesmo endereco da ultima vez", "manda no mesmo endereco"}:
        return ("previous", None)
    match = re.fullmatch(r"(?:usar|use|entregar no|entrega no|manda no|quero usar)\s+(?:o\s+)?endereco\s+(?:salvo\s+)?(.{1,60})", value)
    if match:
        return ("label", match.group(1).strip())
    return None


def save_address_request(body):
    match = re.fullmatch(r"(?:salvar|salve|guardar|guarde)\s+(?:o\s+)?endere[cç]o\s+como\s+([^?\r\n]{1,60})", body.strip(), re.IGNORECASE)
    if match and not any(ord(c)<32 for c in match.group(1)):
        label = " ".join(match.group(1).split())
        if label and label not in {"*", "_", "~"}:
            return label
    return None


def verified_customer(customer, state):
    return (customer is not None and customer["active"] and customer["deleted_at"] is None
            and (not state.get("customer_name") or
                 clean(customer["name"]) == clean(state["customer_name"])))


async def recover_saved_address(db, tenant, phone, state, request):
    """Reuse only the verified contact's exact named address or fulfilled delivery."""
    from app import delivery_customer_addresses as book, delivery_orders as orders
    customer = await customer_for_phone(db,tenant,phone)
    if not verified_customer(customer,state):
        return None
    kind, label = request
    if kind == "previous":
        record = await orders.previous_address(db,tenant,customer["id"])
        raw = record.get("address")
    else:
        result = await book.list_addresses(db,tenant,customer["id"])
        matches = [entry["address"] for entry in result["items"] if clean(entry["label"])==label]
        raw = matches[0] if len(matches)==1 else None
    if raw is None:
        return None
    try:
        return Address.model_validate(raw).model_dump(mode="json")
    except ValidationError:
        return None


async def save_last_delivery_address(db, tenant, phone, state, label):
    """Explicit opt-in to store a recently confirmed address under a new label.

    No draft, arbitrary order ID, other phone, or different recipient can
    write the address book. Existing labels are not silently overwritten.
    """
    from app import delivery_customer_addresses as book
    from app.delivery_storefront import PUBLIC_ACTOR
    code = state.get("last_order")
    if state.get("stage") or not isinstance(code,str) or not re.fullmatch(r"[A-F0-9]{8}",code):
        return "Para guardar um endereço, primeiro confirme um pedido de entrega por aqui."
    customer = await customer_for_phone(db,tenant,phone)
    if not verified_customer(customer,state):
        return "Não consegui verificar seu cadastro para guardar esse endereço. " + HELP
    await book.require_customer(db,tenant,customer["id"],lock=True)
    rows = (await db.execute(text("""SELECT d.address,q.customer_name FROM delivery_order_details d
        JOIN commercial_orders o ON (o.tenant_id,o.id)=(d.tenant_id,d.order_id)
        JOIN commercial_quotes q ON (q.tenant_id,q.id)=(o.tenant_id,o.quote_id)
        WHERE d.tenant_id=:t AND d.customer_id=:customer AND q.contact_e164=:phone
          AND d.intake_channel='whatsapp' AND d.mode='delivery'
          AND o.origin='delivery' AND o.status IN ('confirmed','dispatched','fulfilled')
          AND upper(left(o.id::text,8))=:code
          AND o.created_at>=now()-interval '30 minutes' LIMIT 2"""),
        {"t":tenant,"customer":customer["id"],"phone":phone,"code":code})).mappings().all()
    if len(rows)!=1 or clean(rows[0]["customer_name"] or "")!=clean(customer["name"]):
        return "Não consegui localizar um endereço deste pedido para guardar. " + HELP
    try:
        address = Address.model_validate(rows[0]["address"])
        current = await book.list_addresses(db,tenant,customer["id"])
        matching = [entry for entry in current["items"] if clean(entry["label"])==clean(label)]
        if matching:
            if len(matching)==1 and Address.model_validate(matching[0]["address"])==address:
                return "📍 Esse endereço já está salvo como *" + label + "*."
            return "Já existe um endereço com esse nome. Escolha outro nome para não substituir o anterior."
        await book.save_address(db,tenant,PUBLIC_ACTOR,customer["id"],uuid4(),
            book.AddressEdit(label=label,address=address,expected_revision=0))
    except (ValidationError, HTTPException):
        return "Não consegui guardar o endereço agora. " + HELP
    return "📍 Endereço salvo como *" + label + "*! Na próxima entrega, você pode dizer _usar endereço " + label + "_."


def forbidden(body):
    from app import delivery_storefront as store
    value = re.sub(r"\baguas?(?:\s+miner(?:al|ais))?\s+(?:com|sem)\s+gas\b", "agua", norm(body))
    return bool(store.RESTRICTED.search(value) or re.search(
        r"\b(?:glp|buj[aã]o|botij\w*|gas(?:es)?|combustivel|querosene|gasolina|etanol|diesel|fogos|explosiv\w*|alcool|veneno|solvente|acido|sanitaria|oxigenada|cloro|desinfetante|detergente|limpeza|pesticida|inseticida)\b", value))


def eligible_water(shop, row):
    from app import delivery_storefront as store
    # Explicit storefront approval plus positive, narrow classification.
    # A label alone cannot make a fuel or another restricted item eligible.
    names = " ".join(store.normalized_text(row.get(k)) for k in
                     ("label", "description", "name", "variant_name", "product_description", "category", "brand"))
    return (row["kind"] == "water" and bool(re.search(r"\bagua\b", names))
            and store.available_item(shop, row) and not forbidden(names))


async def shop_and_items(db, tenant, *, lock=False):
    from app import delivery_storefront as store
    company = await store.business(db, tenant)
    if company["status"] != "active" or not delivery_preview_enabled(tenant, company["niche"]):
        raise HTTPException(403, "Atendimento indisponível.")
    record = (await db.execute(text("""
        SELECT slug,revision,settings,enabled FROM delivery_storefronts
        WHERE tenant_id=:t
    """ + (" FOR SHARE" if lock else "")), {"t":tenant})).mappings().one_or_none()
    if record is None:
        return None, []
    shop = {**store.STORE_OPTIONS, **record["settings"], **company,
            "slug":record["slug"], "revision":record["revision"], "enabled":record["enabled"],
            "title":record["settings"]["title"]}
    rows = (await db.execute(text(store.ITEMS+" ORDER BY s.sort_order,s.variant_id"),
                             {"tenant":tenant})).mappings().all()
    items = [dict(row) for row in rows if eligible_water(shop, row)]
    return shop, items


async def catalogue_ready(tenant):
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            _, items = await shop_and_items(db, tenant)
            return len(items)


def selected_catalog(items):
    # The existing interpreter understands product names, quantities and
    # variant names. Keep ambiguous water sizes separate rather than guessing.
    result = []
    for row in items:
        identity = str(row["variant_id"])
        aliases = []
        if len(items) == 1:
            aliases = ["agua", "garrafao", row["name"]]
        elif sum(other["name"] == row["name"] for other in items) == 1:
            aliases = [row["name"]]
        result.append({"id":identity, "name":row["label"], "synonyms":aliases,
                       "variants":[{"id":identity, "name":row["label"], "attributes":{}}]})
    return result


def in_stock(row, now, quantity=1):
    return (row["mode"] == "tracked" and row["observed_at"] <= now < row["valid_until"]
            and row["quantity_on_hand"]-row["reserved_quantity"] >= quantity)


def list_products(items, now):
    if not items:
        return "Ainda não há produtos liberados para este atendimento. " + HELP
    lines = [f"• {row['label']} — *{money(row['unit_price_cents'])}*" +
             ("" if in_stock(row, now) else " _(indisponível no momento)_") for row in items[:10]]
    suffix = "\nHá mais opções; pode me dizer o nome do produto." if len(items)>10 else ""
    return "💧 *Nosso catálogo*\n\n" + "\n".join(lines) + suffix + "\n\nQual você gostaria de pedir?"


def parse_address(body):
    parts = [value.strip() for value in re.split(r"[,;]", body)]
    if len(parts) not in (5,6):
        return None
    street, number, neighborhood, city, state = parts[:5]
    no_number = clean(number) in {"s/n", "sn", "sem numero"}
    try:
        return Address(street=street, number=None if no_number else number, no_number=no_number,
                       neighborhood=neighborhood, city=city, state=state.upper(),
                       complement=parts[5] if len(parts)==6 else None).model_dump(mode="json")
    except (ValidationError, ValueError):
        return None


def payment(body):
    value = clean(body)
    if "pix" in value:
        return "pix"
    if "dinheiro" in value or "especie" in value:
        return "cash"
    if "debito" in value:
        return "debit_card"
    if "credito" in value:
        return "credit_card"
    return None


def change(body):
    value = clean(body)
    if value in {"nao", "sem troco", "valor certo", "nao preciso", "nao precisa"}:
        return None
    match = re.fullmatch(r"(?:(?:troco\s+)?(?:para|pra|de)\s+)?(?:r\$\s*)?(\d{1,7}(?:[.,]\d{1,2})?)(?:\s+reais?)?", value)
    if not match:
        raise ValueError("valor")
    amount = int(Decimal(match.group(1).replace(",", "."))*100)
    if not 0 < amount <= MAX_MONEY:
        raise ValueError("valor")
    return amount


def cash_change_from_message(body):
    """Extract only an explicit, complete cash-change instruction."""
    value = clean(body)
    if re.search(r"\b(?:sem troco|nao (?:preciso|precisa) de troco|valor (?:certo|exato))$", value):
        return True, None
    match = re.search(r"\btroco\s+(?:(?:para|pra|de)\s+)?(?:r\$\s*)?\d{1,7}(?:[.,]\d{1,2})?(?:\s+reais?)?$", value)
    if match:
        try:
            return True, change(match.group())
        except ValueError:
            pass
    return False, None


def set_payment(state, body, method):
    if state.get("payment_preference") != method:
        state.pop("change_for_cents", None)
    state["payment_preference"] = method
    if method == "cash":
        supplied, amount = cash_change_from_message(body)
        if supplied:
            state["change_for_cents"] = amount


def confirmation_presentation(reply, state):
    """Only a complete, freshly reviewed server summary may carry buttons.

    Text remains the canonical fallback for an older or unsupported bridge.
    The opaque ID lives in the encrypted contact-scoped conversation, not in
    the product, model prompt, or an untrusted display label.
    """
    state.pop("delivery_presentation_id", None)
    summary = state.get("summary")
    if (not isinstance(reply, str) or state.get("stage") != "confirm" or
            not isinstance(summary, str) or not reply.endswith(summary) or
            not reply.endswith(CONFIRMATION_FALLBACK)):
        return None
    native_text = reply[:-len(CONFIRMATION_FALLBACK)]
    if not native_text.strip() or len(native_text.encode("utf-16-le")) // 2 > 1024:
        return None
    identity = uuid4().hex
    state["delivery_presentation_id"] = identity
    return {"version": 1, "id": identity, "action": {
        "type": "reply_buttons", "text": native_text, "buttons": [
            {"id": f"oa1_{identity}_0", "title": "Confirmar pedido"},
            {"id": f"oa1_{identity}_1", "title": "Cancelar"},
        ]}}


def confirmation_selection(state, selection_id, quoted_message_id, accepted_provider_id):
    """An exact current summary, accepted receipt and contact are required.

    The caller already resolves the last encrypted state by tenant/device/
    generation/phone. A forged label, old summary or duplicate click never
    becomes a new order.
    """
    identity = state.get("delivery_presentation_id")
    if (state.get("stage") != "confirm" or not isinstance(identity, str) or
            not re.fullmatch(r"[a-f0-9]{32}", identity) or not accepted_provider_id or
            quoted_message_id != accepted_provider_id):
        return None
    return {f"oa1_{identity}_0": "confirmo", f"oa1_{identity}_1": "nao"}.get(selection_id)


def state_question(stage, state, shop, items):
    if stage == "name":
        return "Combinado! 😊 Qual é o seu nome?"
    if stage == "mode":
        choices = [label for key,label in (("pickup","retirada"),("delivery","entrega")) if shop.get("allow_"+key)]
        if choices == ["retirada"]:
            return "Por aqui, estamos atendendo com *retirada no local*. Você consegue retirar?"
        if choices == ["entrega"]:
            return "Vamos de *entrega*? Me confirma, por favor."
        if not choices:
            return "No momento, nenhuma forma de recebimento está disponível. " + HELP
        return "Você prefere receber em casa ou retirar aqui com a gente? 😊"
    if stage == "address":
        return ADDRESS_PROMPT + "\n\nSe já tiver um endereço salvo, diga *usar endereço Casa*, por exemplo."
    if stage == "exchange":
        return "Você tem os vasilhames vazios para devolver na troca?"
    if stage == "payment":
        choices = [PAYMENTS[key] for key in shop["payment_methods"]]
        return "💳 Como prefere pagar?\n\n" + "\n".join("• " + choice.capitalize() for choice in choices)
    if stage == "change":
        return "Precisa de troco? 😊\n\nSe precisar, me diz para quanto. Se não, pode responder *sem troco*."
    if stage == "offer":
        return "Quer que eu prepare seu pedido?"
    return state.get("summary") or list_products(items, datetime.now(timezone.utc))


def information_reply(body, shop, items, state, now):
    """Answer grounded FAQs without discarding a draft or accepting a sale."""
    value = clean(body)
    question = "?" in body or bool(re.search(
        r"\b(?:quanto\w*|qual|quais|como|onde|quando|posso|pode|voces tem|tem desconto|aceita\w*)\b", value))
    # Never reinterpret a direct order as a question merely because the
    # customer says "pode mandar".
    if not question or (re.search(r"\b(?:pode mandar|pode enviar|pode trazer)\b", value) and "?" not in body
                        and not re.search(r"\b(?:quanto|qual|onde|como|quando)\b", value)):
        return None
    answers = []
    if re.search(r"\b(?:desconto|promoc\w*|cupom|mais barato|abatimento)\b", value):
        answers.append("Sobre desconto: não tenho nenhuma condição especial cadastrada para te oferecer agora. Posso encaminhar à equipe se você quiser negociar.")
    if re.search(r"\b(?:retirada|retirar|buscar|balcao|pegar ai|pegar no local)\b", value):
        if shop.get("allow_pickup"):
            reply = "Sim, você pode *retirar no local*!"
            if shop.get("pickup_details"):
                reply += "\n📍 " + shop["pickup_details"]
            else:
                reply += " O endereço de retirada ainda não está disponível aqui; posso te encaminhar à equipe."
            answers.append(reply)
        else:
            answers.append("Por enquanto, a retirada no local não está disponível.")
    delivery = bool(re.search(r"\b(?:entrega\w*|domicilio|em casa|frete|taxa)\b", value))
    duration = bool(re.search(r"\b(?:tempo|demora\w*|prazo|minutos?|horas?|chega\w*)\b", value))
    fee = bool(re.search(r"\b(?:frete|taxa|valor|quanto custa|quanto fica|gratis|gratuit\w*)\b", value))
    if delivery or duration:
        if not shop.get("allow_delivery"):
            answers.append("No momento, não estamos fazendo entregas." +
                           (" Você pode retirar no local." if shop.get("allow_pickup") else ""))
        else:
            if duration:
                low, high = shop.get("estimate_min_minutes"), shop.get("estimate_max_minutes")
                if low is not None and high is not None:
                    estimate = f"{low} a {high} min" if low != high else f"{low} min"
                    answers.append(f"⏱️ Nossa previsão geral é de *{estimate}*, mas o prazo pode variar conforme o endereço.")
                else:
                    answers.append("Ainda não tenho uma previsão confirmada para a sua região. Posso consultar a entrega com o endereço.")
            if fee:
                answers.append("🛵 O valor da entrega depende do endereço e dos bairros atendidos. A taxa é calculada com o endereço completo na preparação do pedido.")
            if not duration and not fee:
                answers.append("Fazemos entrega nas áreas atendidas pela empresa. Posso preparar seu pedido e verificar a cobertura pelo endereço.")
    if re.search(r"\b(?:endereco|localizacao|onde fica|onde voces ficam)\b", value) and not delivery and not any("📍" in a for a in answers):
        answers.append("📍 " + (shop.get("pickup_details") or "O endereço de retirada ainda não está cadastrado no atendimento. Posso encaminhar à equipe."))
    if re.search(r"\b(?:pix|cartao|credito|debito|dinheiro|pagamento|pagar|pagam)\b", value):
        methods = [PAYMENTS[k].capitalize() for k in shop.get("payment_methods", []) if k in PAYMENTS]
        answers.append("💳 *Formas de pagamento*\n" + ("\n".join("• " + method for method in methods) if methods else
                       "Ainda não tenho uma forma de pagamento cadastrada."))
        if "pix" in value and "pix" in shop.get("payment_methods", []):
            answers.append("_O Pix é conferido manualmente pela equipe._")
    if re.search(r"\b(?:funciona\w*|abre|fecha|aberto|fechado|horario\w*)\b", value) and not duration:
        from app.delivery_storefront import is_open
        opened = is_open(shop, now)
        answers.append("Estamos *atendendo agora*. 😊" if opened else "Estamos fora do horário de atendimento no momento.")
    if (answers or state.get("items")) and re.search(r"\b(?:preco|valor|quanto custa|quanto fica|custa)\b", value) and not (delivery and fee):
        plan = interpret(body, selected_catalog(items), previous=state.get("items", []))
        selected = plan.items if not plan.invalid_quantity and not plan.ambiguous_products else []
        indexed = {str(row["variant_id"]): row for row in items}
        if not selected and not plan.ambiguous_products and not plan.invalid_quantity:
            selected = state.get("items", [])
        if selected and all(line["variant_id"] in indexed for line in selected):
            answers.append("\n".join(f"💧 {indexed[line['variant_id']]['label']} — *{money(indexed[line['variant_id']]['unit_price_cents'])}* por unidade"
                                     for line in selected))
    if not answers:
        return None
    stage = state.get("stage")
    if stage == "confirm":
        answers.append("Seu pedido ainda está aguardando confirmação.\n\n" + state["summary"])
    elif stage and stage != "offer":
        answers.append(state_question(stage, state, shop, items))
    elif stage == "offer":
        answers.append("Quer que eu continue com o seu pedido?")
    return "\n\n".join(answers)


def revised_items(body, items, state, now):
    """Resolve only explicit changes to a pending cart; never confirm it."""
    if not state.get("items"):
        return None
    value = clean(body)
    if not re.search(r"\b(?:na verdade|melhor|troca|troque|muda|tira|retire|remove|remova|adiciona|adicione|inclui|inclua|coloca|coloque|so quero|apenas)\b", value):
        return None
    plan = interpret(body, selected_catalog(items), previous=state["items"])
    if plan.invalid_quantity or plan.ambiguous_products:
        return "ambiguous"
    previous = {str(line["variant_id"]): dict(line) for line in state["items"]}
    if plan.selection_mode == "remove":
        if not plan.removed_variants:
            return "ambiguous"
        for identity in plan.removed_variants:
            previous.pop(identity, None)
        chosen = list(previous.values())
    elif plan.selection_mode == "add":
        if not plan.items:
            return "ambiguous"
        for line in plan.items:
            identity = line["variant_id"]
            quantity = previous.get(identity, {}).get("quantity", 0) + line["quantity"]
            previous[identity] = {"variant_id": identity, "quantity": quantity}
        chosen = list(previous.values())
    elif plan.items:
        chosen = plan.items
    else:
        return None
    indexed = {str(row["variant_id"]): row for row in items}
    if sum(line["quantity"] for line in chosen) > 100 or any(
        line["variant_id"] not in indexed or not in_stock(indexed[line["variant_id"]], now, line["quantity"]) for line in chosen
    ):
        return "unavailable"
    return chosen


async def next_step(db, tenant, shop, items, state, phone):
    from app import delivery_storefront as store
    chosen = {str(row["variant_id"]):row for row in items}
    if not state.get("items") or any(line["variant_id"] not in chosen for line in state["items"]):
        return list_products(items, datetime.now(timezone.utc)), {}
    if not state.get("customer_name"):
        customer = await customer_for_phone(db,tenant,phone)
        if customer is not None and (not customer["active"] or customer["deleted_at"] is not None):
            return "Seu cadastro precisa de uma revisão pela equipe antes de um novo pedido. " + HELP, {}
        name = customer["name"] if customer else await previous_verified_name(db,tenant,phone)
        if name:
            state["customer_name"] = name
        else:
            state["stage"] = "name"
            return state_question("name",state,shop,items),state
    if not state.get("mode"):
        state["stage"] = "mode"
        return state_question("mode",state,shop,items),state
    if state["mode"] == "delivery" and not state.get("address"):
        state["stage"] = "address"
        return state_question("address",state,shop,items),state
    if any(chosen[line["variant_id"]]["exchange_container_id"] is not None for line in state["items"]) and not state.get("accept_exchange"):
        state["stage"] = "exchange"
        return state_question("exchange",state,shop,items),state
    if not state.get("payment_preference"):
        state["stage"] = "payment"
        return state_question("payment",state,shop,items),state
    if state["payment_preference"] == "cash" and "change_for_cents" not in state:
        state["stage"] = "change"
        return state_question("change",state,shop,items),state
    try:
        draft = store.CheckoutDraft.model_validate({key:state[key] for key in store.CheckoutDraft.model_fields if key in state})
        estimate, _, logistics = await store.prepare_checkout(db,tenant,shop,draft,source="whatsapp")
    except (HTTPException, ValidationError):
        return "Não consegui validar preço, estoque, endereço ou troco. Nenhum pedido foi registrado. Informe o produto novamente ou escreva atendente.",{}
    expires = int(datetime.now(timezone.utc).timestamp())+600
    signature = store.quote_signature(tenant,shop,draft,estimate,logistics["policy_revision"],expires)
    proposal = store.Checkout(**draft.model_dump(), customer_name=state["customer_name"],customer_phone=phone,
                              confirmed=True, quote=f"{expires}.{signature}", idempotency_key=str(uuid4()))
    lines = [f"• {line['quantity']} × {line['product_name']} — {money(line['total_cents'])}" for line in estimate["items"]]
    address = draft.address
    destination = (f"{address.street}, {address.number or 's/n'}, {address.neighborhood}, {address.city}/{address.state}" +
                   (f" — {address.complement}" if address.complement else "")) if address else (shop.get("pickup_details") or "Retirada no estabelecimento")
    summary = ("📝 *Seu pedido*\n\n" + "\n".join(lines) +
               f"\n\n👤 *Nome:* {state['customer_name']}" +
               f"\n\n📍 *{'Entrega' if address else 'Retirada'}:* {destination}\nTaxa: {money(estimate['delivery_fee_cents'])}"
               f"\n\n💰 *Total: {money(estimate['total_cents'])}*\nPagamento: {PAYMENTS[draft.payment_preference]}")
    if estimate.get("eta_minutes") is not None:
        summary += f"\nPrevisão estimada: {estimate['eta_minutes']} min."
    if draft.accept_exchange:
        summary += "\nCom devolução dos vasilhames vazios."
    if draft.change_for_cents is not None:
        summary += "\nTroco previsto: "+money(draft.change_for_cents-estimate["total_cents"])
    summary += "\n\nEstá tudo certinho? 😊" + CONFIRMATION_FALLBACK
    state.update(stage="confirm",proposal=proposal.model_dump(mode="json"),summary=summary)
    return summary,state


async def answer(db, tenant, phone, body, state, summary_delivered):
    from app import delivery_storefront as store
    value = clean(body)
    # Never process an emergency or a restricted sale as a water purchase.
    if emergency(body):
        return ("⚠️ *Sua segurança vem primeiro.*\n\n"
                "Afaste-se da área de risco e mantenha outras pessoas afastadas. "
                "Não acenda chamas nem acione interruptores ou aparelhos elétricos na área afetada.\n\n"
                "Em caso de emergência, ligue *193 (Corpo de Bombeiros)* a partir de um local seguro. "
                "Não tente reparar o equipamento.\n\n"
                "Interrompi o atendimento automático."),{"paused":True}
    if state.get("paused"):
        if value not in {"retomar atendimento","voltar atendimento","voltar ao bot"}:
            return None,state
        state = {}
    if value in {"atendente","humano","falar com atendente","falar com humano","parar","pare","sair","stop","cancelar atendimento"}:
        return "Pausei o atendimento automático. Aguarde uma pessoa da empresa responder. Para voltar, escreva retomar atendimento.",{"paused":True}
    label = save_address_request(body)
    if label is not None:
        return await save_last_delivery_address(db,tenant,phone,state,label),state
    if forbidden(body):
        return ("Por aqui, consigo ajudar com pedidos de água. Para consultar gás ou outros produtos, "
                "precisamos da nossa equipe. Não registrei nenhum item novo. " + HELP),{}
    shop, items = await shop_and_items(db,tenant,lock=state.get("stage")=="confirm")
    if not shop or not items:
        return "Ainda não tenho o catálogo disponível por aqui. 😊\n\n"+HELP,{}
    if value in {"meu pedido","status do pedido","cadê meu pedido","cade meu pedido","acompanhar pedido"}:
        row = (await db.execute(text("""SELECT o.id,o.status FROM commercial_orders o
            JOIN commercial_quotes q ON (q.tenant_id,q.id)=(o.tenant_id,o.quote_id)
            JOIN delivery_order_details d ON (d.tenant_id,d.order_id)=(o.tenant_id,o.id)
            WHERE o.tenant_id=:t AND o.origin='delivery' AND q.contact_e164=:phone
              AND d.intake_channel='whatsapp' AND o.created_at>=now()-interval '30 days'
            ORDER BY o.created_at DESC,o.id DESC LIMIT 1"""),{"t":tenant,"phone":phone})).mappings().one_or_none()
        if row:
            labels={"confirmed":"recebido, aguardando atendimento","dispatched":"em rota","fulfilled":"concluído","cancelled":"cancelado"}
            return f"Pedido {str(row['id'])[:8].upper()}: {labels.get(row['status'],'em análise')}.",state
        return "Não encontrei pedido recente deste número pelo WhatsApp. "+HELP,state
    if value in {"cancelar pedido","cancela meu pedido","quero cancelar"}:
        return "Entendi! Vou pausar as respostas automáticas para nossa equipe conferir seu pedido.\n\n⚠️ _O cancelamento ainda não foi efetuado._",{"paused":True}
    now = datetime.now(timezone.utc)
    stage = state.get("stage")
    if stage and value in {"cancelar", "desistir", "nao quero mais", "deixa pra la", "cancela tudo", "nao vou querer"}:
        return "Tudo bem! 😊 Cancelei o resumo. Nenhum novo pedido foi registrado.",{}
    info = information_reply(body, shop, items, state, now)
    if not store.is_open(shop, now):
        closed = shop.get("closed_message") or "No momento estamos fora do horário de atendimento."
        return (info + "\n\n" if info else "") + closed + "\n\n" + HELP,state
    if info:
        return info,state
    edits = revised_items(body, items, state, now)
    if edits == "ambiguous":
        return "Claro, posso ajustar! Me diz exatamente quais produtos e quantidades você quer manter.",state
    if edits == "unavailable":
        return "Não consegui encontrar essa quantidade no estoque. Quer escolher outra quantidade?",state
    if isinstance(edits, list):
        if not edits:
            return "Pronto, retirei os itens do resumo. Nenhum novo pedido foi registrado.",{}
        state = {k:v for k,v in state.items() if k not in {"proposal","summary","change_for_cents","accept_exchange","stage"}}
        state["items"] = edits
        reply, state = await next_step(db,tenant,shop,items,state,phone)
        return "Combinado, ajustei o seu pedido!\n\n" + reply,state
    if not stage and (value in {"menu","catalogo","retomar atendimento","voltar atendimento","voltar ao bot"} or
                      re.fullmatch(r"(?:oi+|ola|opa|e ai|bom dia|boa tarde|boa noite)(?:[,! ]+(?:tudo bem|td bem|tudo bom|beleza))?", value)):
        return (f"Oi! Tudo bem? 😊\n\nSou a atendente virtual da *{shop['title']}*. "
                "Posso te ajudar com valores, disponibilidade e pedidos de água.\n\n"
                "Me conta o que você precisa!"),{}
    if stage=="confirm":
        corrected_name = re.fullmatch(r"(?:meu nome (?:é|e)|me chamo|pode me chamar de)\s+([^?\n\r]{2,120})", body.strip(), re.IGNORECASE)
        if corrected_name and any(c.isalpha() for c in corrected_name.group(1)) and not re.search(r"\d", corrected_name.group(1)):
            # The current order can name a different recipient on a shared
            # number. The permanent CRM identity is not silently overwritten.
            state["customer_name"] = " ".join(corrected_name.group(1).split())
            state.pop("proposal", None)
            state.pop("summary", None)
            return await next_step(db,tenant,shop,items,state,phone)
        if value in {"nao","não","cancelar","desistir","nao confirmo"}:
            return "Tudo bem! 😊 Cancelei o resumo. Nenhum pedido novo foi registrado.",{}
        if value in {"ver resumo","rever resumo","como ficou","mostra de novo"}:
            return state["summary"],state
        if value in {"confirmo","confirmar","pode confirmar","sim","s","sm","isso mesmo","fechou","de acordo","tudo certo","pode ser","combinado","ok pode confirmar"} and affirmative(body):
            if not summary_delivered:
                return state["summary"],state
            proposal = store.Checkout.model_validate(state["proposal"])
            if proposal.customer_phone != phone:
                return "Não foi possível verificar o pedido. "+HELP,{}
            # The same persisted proposal key makes replay safe even if the
            # runtime committed before the device acknowledgement was stored.
            try:
                async with db.begin_nested():
                    result = await store.save_checkout(db,tenant,shop,proposal,source="whatsapp")
            except (HTTPException, ValidationError):
                return "O resumo mudou ou expirou. Não confirmei um novo pedido. Confira os itens novamente ou escreva atendente.",{}
            reply = (f"✅ *Pedido {result['order_code']} registrado!*\n\n"
                     f"Total: *{money(result['total_cents'])}*\n\nObrigada! 😊")
            if proposal.payment_preference=="pix":
                reply += " O Pix depende da conferência manual do pagamento pela empresa."
            if proposal.mode == "delivery":
                reply += "\n\nSe quiser guardar este endereço para a próxima entrega, diga *salvar endereço como Casa*."
            if shop["enabled"] and store.configured_public(tenant,shop["niche"]):
                from app.public_origins import canonical_public_origin
                # The public store accepts a private tracking code; do not
                # reveal another customer's address or order history.
                reply += f"\nAcompanhe em {canonical_public_origin()}/loja/{shop['slug']}. Código: {result['tracking_code']}"
            return reply,{"last_order":result["order_code"]}
        return "Ainda não confirmei o pedido. Se quiser mudar algo, pode me dizer.\n\n"+state["summary"],state
    if stage=="name":
        name = " ".join(body.split())
        name = re.sub(r"^(?:meu nome (?:é|e)|me chamo|pode me chamar de|sou)\s+", "", name, flags=re.IGNORECASE)
        if "?" in name or not 2<=len(name)<=120 or not any(c.isalpha() for c in name) or re.search(r"\d",name):
            return "Informe apenas seu nome, por favor.",state
        state["customer_name"]=name
    elif stage=="mode":
        mode = "delivery" if re.search(r"\b(?:entrega|entregar|em casa|a domicilio)\b",value) else "pickup" if re.search(r"\b(?:retirada|retirar|buscar|balcao|pegar ai)\b",value) else None
        if mode is None and affirmative(body):
            modes = [key for key in ("pickup", "delivery") if shop.get("allow_"+key)]
            mode = modes[0] if len(modes)==1 else None
        if not mode or not shop.get("allow_"+mode):
            return state_question("mode",state,shop,items),state
        state["mode"]=mode
    elif stage=="address":
        request=saved_address_request(body)
        if request:
            addr=await recover_saved_address(db,tenant,phone,state,request)
            if addr is None:
                return "Não consegui localizar esse endereço com segurança. Vamos informar o endereço desta entrega?\n\n"+ADDRESS_PROMPT,state
        else:
            addr=parse_address(body)
        if addr is None:
            return state_question("address",state,shop,items),state
        state["address"]=addr
    elif stage=="exchange":
        if not affirmative(body):
            if value in {"nao","nao tenho","sem troca"}:
                return "Sem os vasilhames vazios, escolha uma variação sem troca que esteja cadastrada. "+list_products(items,datetime.now(timezone.utc)),{}
            return state_question("exchange",state,shop,items),state
        state["accept_exchange"]=True
    elif stage=="payment":
        method=payment(body)
        if method not in shop["payment_methods"]:
            return state_question("payment",state,shop,items),state
        set_payment(state, body, method)
    elif stage=="change":
        try:
            supplied, amount = cash_change_from_message(body)
            state["change_for_cents"]=amount if supplied else change(body)
        except ValueError:
            return state_question("change",state,shop,items),state
    elif stage=="offer" and affirmative(body):
        pass
    else:
        plan=interpret(body,selected_catalog(items))
        if plan.ambiguous_products or plan.invalid_quantity or not plan.items:
            return list_products(items,datetime.now(timezone.utc))+" "+HELP,{}
        if plan.intent in {"decline","cancel"}:
            return "Certo. Nenhum pedido foi registrado.",{}
        indexed={str(row["variant_id"]):row for row in items}
        if sum(line["quantity"] for line in plan.items)>100 or any(not in_stock(indexed[line["variant_id"]],datetime.now(timezone.utc),line["quantity"]) for line in plan.items):
            return "A quantidade solicitada não está disponível. Confira o catálogo ou fale com um atendente.",{}
        state={"items":plan.items}
        if plan.intent in {"price","stock","catalog"}:
            state["stage"]="offer"
            return ("\n".join(f"💧 {indexed[line['variant_id']]['label']} — *{money(indexed[line['variant_id']]['unit_price_cents'])}* por unidade" for line in plan.items)
                    +"\n\nQuer que eu prepare seu pedido? 😊"),state
        if re.search(r"\b(?:entrega|entregar|em casa)\b",value) and shop.get("allow_delivery"):
            state["mode"]="delivery"
        elif re.search(r"\b(?:retirada|retirar|buscar)\b",value) and shop.get("allow_pickup"):
            state["mode"]="pickup"
        method=payment(body)
        if method in shop["payment_methods"]:
            set_payment(state, body, method)
    return await next_step(db,tenant,shop,items,state,phone)


async def record_event(db, device, payload):
    if (not enabled(device) or payload.kind!="text" or payload.origin!="contact" or not payload.message_id
            or not (payload.text or payload.selection_id)):
        return {"effects":[],"duplicate":False}
    if payload.text and len(payload.text)>1500:
        return {"effects":[],"duplicate":False}
    try:
        phone=normalize_phone("+"+payload.phone)
    except ValueError:
        return {"effects":[],"duplicate":False}
    params={"t":device["tenant_id"],"d":device["id"],"g":device["generation"],"phone":payload.phone}
    if await db.scalar(text("""SELECT COALESCE((SELECT revoked FROM subscription_campaign_permissions
        WHERE tenant_id=:t AND phone=:phone),false)"""),params):
        return {"effects":[],"duplicate":False,"ignored":"contact_optout"}
    duplicate=await db.scalar(text("""SELECT id FROM subscription_device_events
        WHERE tenant_id=:t AND device_id=:d AND (provider_event_id=:event OR
        (generation=:g AND payload->>'message_id'=:message AND payload->>'delivery'='true')) LIMIT 1"""),
        {**params,"event":payload.event_id,"message":payload.message_id})
    if duplicate:
        return {"effects":[],"duplicate":True}
    last=(await db.execute(text("""SELECT id,payload,occurred_at FROM subscription_device_events
        WHERE tenant_id=:t AND device_id=:d AND generation=:g AND payload->>'phone'=:phone
          AND payload->>'delivery'='true' ORDER BY received_at DESC,id DESC LIMIT 1"""),params)).mappings().one_or_none()
    state={}
    delivered=False
    accepted_provider_id=None
    if last:
        if payload.occurred_at<last["occurred_at"]:
            return {"effects":[],"duplicate":False,"ignored":"out_of_order"}
        old=last["payload"]
        if old.get("delivery_paused") is True:
            state={"paused":True}
        if old.get("settings_revision")==device["revision"] and old.get("delivery_state"):
            try:
                decoded=channels._cipher().decrypt(old["delivery_state"].encode(),ttl=STATE_TTL)
                state=json.loads(decoded)
            except (InvalidToken,ValueError,TypeError):
                pass
        if old.get("delivery_summary") is True:
            accepted=(await db.execute(text("""SELECT provider_id FROM subscription_device_effects
                WHERE tenant_id=:t AND device_id=:d AND generation=:g AND event_id=:event
                  AND purpose=:purpose AND status='accepted' AND settings_revision=:revision
                  AND payload->>'phone'=:phone"""),
                {**params,"event":last["id"],"purpose":PURPOSE,"revision":device["revision"]})).mappings().one_or_none()
            delivered=accepted is not None
            accepted_provider_id=accepted["provider_id"] if accepted else None
    # Savepoint avoids retaining half an order after a business rejection. The
    # checkout's own idempotency key is reused across delivery retries.
    body = payload.text
    if payload.selection_id:
        body = confirmation_selection(state, payload.selection_id, payload.quoted_message_id, accepted_provider_id)
    if body:
        async with get_session_factory()() as runtime:
            async with tenant_transaction(runtime,device["tenant_id"]):
                reply,state=await answer(runtime,device["tenant_id"],phone,body,state,delivered)
    elif state.get("paused"):
        reply = None
    else:
        reply = CONFIRMATION_STALE
        if state.get("stage") == "confirm" and state.get("summary"):
            reply += "\n\n" + state["summary"]
    presentation = confirmation_presentation(reply, state)
    data={"delivery":True,"phone":payload.phone,"message_id":payload.message_id,
          "settings_revision":device["revision"],"delivery_paused":state.get("paused") is True,
          "delivery_summary": bool(reply and state.get("stage")=="confirm"
                                   and state.get("summary") and state["summary"] in reply)}
    if state and not state.get("paused"):
        data["delivery_state"]=channels._cipher().encrypt(json.dumps(jsonable_encoder(state),ensure_ascii=False).encode()).decode()
    event_id=(await db.execute(text("""INSERT INTO subscription_device_events
        (tenant_id,device_id,generation,provider_event_id,kind,occurred_at,payload)
        VALUES(:t,:d,:g,:event,'text',:at,CAST(:data AS jsonb)) ON CONFLICT DO NOTHING RETURNING id"""),
        {**params,"event":payload.event_id,"at":payload.occurred_at,"data":json.dumps(data)})).scalar_one_or_none()
    if event_id is None:
        return {"effects":[],"duplicate":True}
    if last:
        await db.execute(text("""UPDATE subscription_device_events SET payload=payload-'delivery_state'
            WHERE tenant_id=:t AND device_id=:d AND id=:event"""),{**params,"event":last["id"]})
    # Clear expired encrypted conversational drafts, but retain the event
    # identity for deduplication and opt-out markers.
    await db.execute(text("""UPDATE subscription_device_events SET payload=payload-'delivery_state'
        WHERE tenant_id=:t AND device_id=:d AND payload ? 'delivery_state'
          AND received_at<now()-interval '30 minutes'"""),params)
    # Suppress any older unsent reply to this contact.
    await db.execute(text("""UPDATE subscription_device_effects SET status='not_sent',finished_at=clock_timestamp(),
        error_code='newer_delivery_input' WHERE tenant_id=:t AND device_id=:d AND purpose=:purpose
        AND payload->>'phone'=:phone AND status='pending'"""),{**params,"purpose":PURPOSE})
    if not reply:
        return {"effects":[],"duplicate":False}
    from app.subscription_devices import _insert_effect
    effect_payload={"phone":payload.phone,"text":reply}
    if presentation is not None:
        effect_payload["delivery_presentation"]=presentation
    effect_id=await _insert_effect(db,device,event_id,"delivery-reply",
        {"type":"send_text","purpose":PURPOSE,"payload":effect_payload})
    return {"effects":[{"operation_id":str(effect_id),"type":"send_text"}],"duplicate":False}


async def permits(db, device, effect):
    if not enabled(device) or effect["purpose"]!=PURPOSE or effect["type"]!="send_text":
        return False
    return bool(await db.scalar(text("""SELECT 1 FROM subscription_device_events e
        WHERE e.tenant_id=:t AND e.device_id=:d AND e.generation=:g AND e.id=:event
          AND e.payload->>'delivery'='true' AND e.payload->>'phone'=:phone
          AND e.occurred_at>clock_timestamp()-interval '24 hours'
          AND NOT EXISTS(SELECT 1 FROM subscription_device_events newer WHERE newer.tenant_id=e.tenant_id
            AND newer.device_id=e.device_id AND newer.generation=e.generation
            AND newer.payload->>'delivery'='true' AND newer.payload->>'phone'=:phone
            AND (newer.received_at,newer.id)>(e.received_at,e.id))
          AND NOT COALESCE((SELECT revoked FROM subscription_campaign_permissions WHERE tenant_id=:t AND phone=:phone),false)
    """),{"t":device["tenant_id"],"d":device["id"],"g":device["generation"],
          "event":effect["event_id"],"phone":effect["payload"]["phone"]}))


async def pending(db, device):
    params={"t":device["tenant_id"],"d":device["id"],"g":device["generation"],"r":device["revision"],"purpose":PURPOSE}
    await db.execute(text("""UPDATE subscription_device_effects SET status='uncertain',error_code='interrupted_attempt'
        WHERE tenant_id=:t AND device_id=:d AND purpose=:purpose AND status='running'
          AND authorized_at<clock_timestamp()-interval '180 seconds'"""),params)
    await db.execute(text("""UPDATE subscription_device_effects SET status='not_sent',finished_at=clock_timestamp(),
        error_code='delivery_reply_expired' WHERE tenant_id=:t AND device_id=:d AND purpose=:purpose
          AND status='pending' AND (expires_at<=clock_timestamp() OR settings_revision<>:r)"""),params)
    rows=(await db.execute(text("""SELECT id AS operation_id,type FROM subscription_device_effects
        WHERE tenant_id=:t AND device_id=:d AND generation=:g AND settings_revision=:r
          AND purpose=:purpose AND type='send_text' AND status='pending'
          AND due_at<=clock_timestamp() AND expires_at>clock_timestamp() ORDER BY created_at,id LIMIT 25"""),params)).mappings().all() if enabled(device) else []
    reconcile=(await db.execute(text("""SELECT id AS operation_id,type,device_id,generation,settings_revision
        FROM subscription_device_effects WHERE tenant_id=:t AND device_id=:d AND generation=:g
          AND purpose=:purpose AND type='send_text' AND status='uncertain' ORDER BY created_at,id LIMIT 25"""),params)).mappings().all()
    return jsonable_encoder({"effects":[dict(row) for row in rows],"reconcile":[dict(row) for row in reconcile]})
