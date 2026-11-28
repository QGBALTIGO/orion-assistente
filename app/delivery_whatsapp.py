"""Delivery conversation on the existing authenticated device/effect broker.

Only explicitly selected, eligible water items are admitted in this pilot.
Reuse the reception's conservative product and confirmation interpreter and
the exact same signed quote, stock reservation, and manual Pix as the store.
No external send, language-model call or provider I/O runs in a transaction.
"""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
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
from app.delivery_whatsapp_media import (
    DeliveryAddress as Address, DeliveryPin, CALL_REPLY, PHOTO_FALLBACK, RECEIPT_REPLY, FALLBACKS,
)
from app import delivery_whatsapp_vision as vision
from app.qr_commerce_understanding import interpret, norm
from app.qr_dialogue import contextual_response
from app.workspace_policy import delivery_preview_enabled

PURPOSE = "delivery_reply"
STATE_TTL = 1800
AUDIO_MINUTES = 2
AUDIO_BYTES = 25_000_000
AUDIO_FALLBACK = ("Recebi seu áudio, mas não consegui entender com segurança. "
                  "Pode enviar uma mensagem escrita ou gravar novamente? "
                  "Não alterei nem confirmei nenhum pedido.")
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


def audio_enabled(device):
    # An isolated pilot, independent of subscription niche settings.
    allowed = {value.strip() for value in os.getenv("ORION_DELIVERY_AUDIO_PREVIEW_TENANTS", "").split(",") if value.strip()}
    return enabled(device) and str(device["tenant_id"]) in allowed


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
        if address.location and address.location.kind=="live_location":
            # Explicit save stores a fixed destination, never a tracking grant.
            address=address.model_copy(update={"location":address.location.model_copy(update={"kind":"location"})})
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
    # Never arbitrarily choose the first of two methods or treat a negated
    # payment method as authorization. Split tender needs its own workflow.
    if re.search(r"\b(?:nao (?:vou |quero |posso )?(?:pagar|usar)|sem (?:pix|dinheiro|cartao|credito|debito))\b",value):
        return None
    methods = {
        key for key, pattern in (
            ("pix",r"\bpix\b"), ("cash",r"\b(?:dinheiro|especie)\b"),
            ("debit_card",r"\bdebito\b"), ("credit_card",r"\bcredito\b"))
        if re.search(pattern,value)
    }
    if len(methods)!=1:
        return None
    method=next(iter(methods))
    if method!="cash" and re.search(r"\btroco\s+(?:para|pra|de)\s+\d",value):
        return None
    return method


def payment_instruction(body):
    """Separate an explicit instruction from a question about methods."""
    if "?" in body:
        return False
    value = clean(body)
    value = re.sub(r"^(?:(?:na verdade|dessa vez|agora|melhor)[, ]+)", "", value)
    return bool(
        re.fullmatch(r"(?:pix|dinheiro|em dinheiro|especie|cartao(?: de (?:credito|debito))?|credito|debito)(?:[, ]+(?:troco\b.*|sem troco|valor (?:certo|exato)))?", value)
        or re.match(r"^(?:vou pagar|quero pagar|prefiro pagar|pago|pagamento|pagar)\b",value)
        or re.fullmatch(r"prefiro (?:pix|dinheiro|especie|cartao(?: de (?:credito|debito))?|credito|debito)",value)
        or re.fullmatch(r"troca (?:o )?pagamento (?:para|pra|por) .+",value)
    )


def mode_instruction(body):
    if "?" in body:
        return None
    value=clean(body)
    value=re.sub(r"^(?:(?:na verdade|dessa vez|agora|melhor)[, ]+)","",value)
    if re.fullmatch(r"(?:(?:quero|prefiro|vou|pode|vamos de) )?(?:retirada|retirar(?: ai| no local)?|buscar(?: ai| no local)?|pegar ai|balcao)",value):
        return "pickup"
    if re.fullmatch(r"(?:(?:quero|prefiro|pode|vamos de) )?(?:entrega|entregar(?: em casa)?|receber em casa|em casa|a domicilio)",value):
        return "delivery"
    return None


def customer_name_from_reply(body):
    """Conservatively accept a name, not a command, greeting or question."""
    raw=" ".join(body.split())
    if "?" in raw:
        return None
    name=re.sub(r"^(?:meu nome (?:é|e)|me chamo|pode me chamar de|sou)\s+","",raw,flags=re.IGNORECASE)
    value=clean(name)
    if not 2<=len(name)<=120 or not any(c.isalpha() for c in name):
        return None
    if not all(c.isalpha() or c in " '-.’" for c in name):
        return None
    if (value in {"sim","s","nao","oi","ola","opa","ok","okay","tudo bem","td bem",
                  "tudo bom","beleza","bom dia","boa tarde","boa noite","nao sei","isso","isso mesmo",
                  "confirmo","confirmar","fechou","pode ser","pode mandar","manda","combinado",
                  "certinho","certo","obrigado","obrigada","pera ai","espera","deixa pra la"}
        or re.fullmatch(r"[kh]+",value)
        or re.match(r"^(?:quero|manda|mandar|pode|vou|prefiro|preciso|aceita|quanto|qual|onde|como|tem|troco|entrega|retirada|nao)\b",value)
        or payment_instruction(name) or mode_instruction(name)):
        return None
    return name


def invalidate_review(state):
    for key in ("proposal","summary","delivery_presentation_id","stage"):
        state.pop(key,None)


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
    if state["mode"] == "delivery" and state.get("pending_pin"):
        state["address"]={**state["address"],"location":state["pending_pin"]["point"]}
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
    if address and address.location:
        point=address.location
        destination += "\n📌 " + ("Último ponto recebido ao vivo: " if point.kind=="live_location" else "Localização enviada: ") + point.map_url()
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


async def answer(db, tenant, phone, body, state, summary_delivered, *, allow_confirmation=True):
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
    if state.get("items"):
        if payment_instruction(body):
            method=payment(body)
            invalidate_review(state)
            if method not in shop["payment_methods"]:
                state.pop("payment_preference",None)
                state.pop("change_for_cents",None)
                # Preserve missing-name/address ordering; do not keep an old quote.
                reply,state=await next_step(db,tenant,shop,items,state,phone)
                return "Me confirma qual forma de pagamento você prefere?\n\n"+reply,state
            set_payment(state,body,method)
            return await next_step(db,tenant,shop,items,state,phone)
        if stage=="confirm" and state.get("payment_preference")=="cash":
            supplied,amount=cash_change_from_message(body)
            if supplied:
                invalidate_review(state)
                state["change_for_cents"]=amount
                return await next_step(db,tenant,shop,items,state,phone)
        mode=mode_instruction(body)
        if mode is not None:
            invalidate_review(state)
            if state.get("mode")!=mode:
                state.pop("address",None)
                state.pop("scheduled_at",None)
            if mode=="pickup":
                state.pop("pending_pin",None)
            if not shop.get("allow_"+mode):
                state.pop("mode",None)
                state.pop("address",None)
                state.pop("scheduled_at",None)
                reply,state=await next_step(db,tenant,shop,items,state,phone)
                return "Essa opção não está disponível no momento.\n\n"+reply,state
            state["mode"]=mode
            return await next_step(db,tenant,shop,items,state,phone)
        if (state.get("mode")=="delivery" and
                re.fullmatch(r"(?:(?:na verdade|agora)[, ]+)?(?:mudei de endereco|troca(?:r)? (?:o )?endereco|muda(?:r)? (?:o )?endereco|outro endereco)",value)):
            invalidate_review(state)
            state.pop("address",None)
            state.pop("pending_pin",None)
            return await next_step(db,tenant,shop,items,state,phone)
    if stage=="media_review" and (affirmative(body) or value in {"ver resumo","rever resumo","como ficou","mostra de novo"}):
        return await next_step(db,tenant,shop,items,state,phone)
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
        corrected_name = re.match(r"^(?:meu nome (?:é|e)|me chamo|pode me chamar de)\s+",body.strip(),re.IGNORECASE)
        if corrected_name:
            name=customer_name_from_reply(body)
            if name is None:
                return "Me informa apenas o nome que deve constar no pedido? 😊",state
            # The current order can name a different recipient on a shared
            # number. The permanent CRM identity is not silently overwritten.
            state["customer_name"]=name
            invalidate_review(state)
            return await next_step(db,tenant,shop,items,state,phone)
        if value in {"nao","não","cancelar","desistir","nao confirmo"}:
            return "Tudo bem! 😊 Cancelei o resumo. Nenhum pedido novo foi registrado.",{}
        if value in {"ver resumo","rever resumo","como ficou","mostra de novo"}:
            return state["summary"],state
        if value in {"confirmo","confirmar","pode confirmar","sim","s","sm","isso mesmo","fechou","de acordo","tudo certo","pode ser","combinado","ok pode confirmar"} and affirmative(body):
            if not allow_confirmation:
                return ("Entendi seu áudio! Para evitar enganos, revise o resumo e confirme pelo botão ou por texto.\n\n"
                        +state["summary"]),state
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
        name=customer_name_from_reply(body)
        if name is None:
            return "Só falta me dizer como posso te chamar. 😊",state
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
            if addr is not None:
                state.pop("pending_pin",None)
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
        pending_pin=state.get("pending_pin")
        state={"items":plan.items}
        if pending_pin:
            state["pending_pin"]=pending_pin
        if plan.intent in {"price","stock","catalog"}:
            state["stage"]="offer"
            return ("\n".join(f"💧 {indexed[line['variant_id']]['label']} — *{money(indexed[line['variant_id']]['unit_price_cents'])}* por unidade" for line in plan.items)
                    +"\n\nQuer que eu prepare seu pedido? 😊"),state
        if re.search(r"\b(?:entrega|entregar|em casa)\b",value) and shop.get("allow_delivery"):
            state["mode"]="delivery"
        elif re.search(r"\b(?:retirada|retirar|buscar)\b",value) and shop.get("allow_pickup"):
            state["mode"]="pickup"
            state.pop("pending_pin",None)
        method=payment(body)
        if method in shop["payment_methods"]:
            set_payment(state, body, method)
    return await next_step(db,tenant,shop,items,state,phone)


def media_review(state):
    if state.get("stage")=="confirm":
        invalidate_review(state)
        state["stage"]="media_review"


def photo_reply(analysis):
    if analysis is None or analysis.category=="unclear":
        return PHOTO_FALLBACK
    if analysis.category=="receipt":
        return RECEIPT_REPLY
    if analysis.category=="water":
        if analysis.volume_ml is not None:
            volume=Decimal(analysis.volume_ml)/1000
            return (f"💧 Pela foto, parece ser um recipiente de água de *{volume.normalize():f} L*.\n\n"
                    "É esse tamanho mesmo? Me diga a quantidade que você precisa.")
        return "💧 Parece ser um recipiente de água. Me confirma o tamanho e a quantidade, por favor?"
    if analysis.category=="gas_container":
        return ("A foto parece mostrar um botijão ou recipiente de gás.\n\n"
                "Para identificar o tamanho e consultar as condições, vou precisar da nossa equipe. " + HELP)
    if analysis.category=="address":
        if analysis.address:
            a=analysis.address
            # OCR/vision output is untrusted, even if it is shaped like an address.
            fields=[a.street,a.number or "s/n",a.neighborhood,a.city,a.state]
            if a.complement:
                fields.append(a.complement)
            def clean_field(value):
                return " ".join(re.sub(r"[*_\`~<>\\]", "",value).split())
            address=", ".join(clean_field(value) for value in fields)
            return ("📍 Parece que o endereço na foto é:\n\n"+address+
                    "\n\nConfere? Se for para entrega, envie esse endereço por escrito ou corrija os dados.")
        return "📍 Parece ser uma foto de endereço. Pode me informar rua, número, bairro, cidade e estado por escrito?"
    if analysis.category=="damaged_product":
        return "Recebi uma foto que parece mostrar um produto danificado. " + HELP
    return "Recebi a foto! 😊 Me conta o que você precisa saber sobre ela para eu te ajudar."


async def handle_media(db, tenant, phone, state, media, occurred_at, *, analysis=None):
    """Process validated metadata without accepting a sale or inventing coverage."""
    if state.get("paused"):
        return None,state,False
    if media.kind in {"location","live_location"}:
        previous=state.get("pending_pin") or {}
        if media.kind=="live_location":
            if media.update and (previous.get("source_id")!=media.source_id or not previous):
                # No new tracking session may be created by a late update.
                return None,state,True
            if previous.get("source_id")==media.source_id and media.sequence<=previous.get("sequence",-1):
                return None,state,True
        pin=media.pin(occurred_at)
        state["pending_pin"]={"point":pin.model_dump(mode="json"),"source_id":media.source_id,
                              "sequence":media.sequence}
        update=media.kind=="live_location" and previous.get("source_id")==media.source_id and bool(previous)
        if not state.get("items"):
            if update:
                return None,state,False
            return ("📍 Recebi " + ("a localização em tempo real" if media.kind=="live_location" else "sua localização")+
                    "! Vou usar o ponto recebido se você fizer um pedido de entrega. O que você precisa?"),state,False
        shop,items=await shop_and_items(db,tenant,lock=state.get("stage")=="confirm")
        if not shop or not items:
            media_review(state)
            return "Recebi o ponto. No momento o catálogo não está disponível. " + HELP,state,False
        if not shop.get("allow_delivery"):
            media_review(state)
            state.pop("pending_pin",None)
            return "📍 Recebi a localização, mas não estamos fazendo entregas neste momento.",state,False
        if state.get("mode")=="pickup":
            invalidate_review(state)
            state.pop("mode",None)
            state["stage"]="mode"
            return "📍 Recebi o ponto. Você quer mudar seu pedido para entrega ou prefere retirar?",state,False
        state["mode"]="delivery"
        if state.get("address"):
            state["address"]={**state["address"],"location":pin.model_dump(mode="json")}
            media_review(state)
            if update:
                # Refresh only the draft point. Do not spam on every movement.
                # A later confirmation must review the refreshed snapshot.
                return None,state,False
            reply,state=await next_step(db,tenant,shop,items,state,phone)
            return "📍 Localização recebida! Confira se o endereço do pedido está correto.\n\n"+reply,state,False
        media_review(state)
        if update:
            return None,state,False
        reply,state=await next_step(db,tenant,shop,items,state,phone)
        return ("📍 Recebi seu ponto no mapa!\n\n"
                "Para calcular a entrega corretamente, ainda preciso confirmar o endereço.\n\n"+reply),state,False
    media_review(state)
    if media.kind=="image":
        return photo_reply(analysis),state,False
    return FALLBACKS.get(media.kind,PHOTO_FALLBACK),state,False


async def record_image(db,device,payload):
    """Admit the photo before any download or provider call."""
    from app.subscription_devices import _insert_effect
    skipped={"effects":[],"duplicate":False}
    if not enabled(device) or payload.origin!="contact" or not payload.phone or not payload.message_id:
        return skipped
    try:
        if not normalize_phone("+"+payload.phone):
            return skipped
    except ValueError:
        return skipped
    params={"t":device["tenant_id"],"d":device["id"],"g":device["generation"],"phone":payload.phone}
    if await db.scalar(text("""SELECT COALESCE((SELECT revoked FROM subscription_campaign_permissions
        WHERE tenant_id=:t AND phone=:phone),false)"""),params):
        return skipped
    duplicate=await db.scalar(text("""SELECT id FROM subscription_device_events
        WHERE tenant_id=:t AND device_id=:d AND (provider_event_id=:event OR
        (generation=:g AND payload->>'message_id'=:message AND (payload->>'delivery_image'='true'
         OR payload->>'delivery'='true'))) LIMIT 1"""),{**params,"event":payload.event_id,"message":payload.message_id})
    if duplicate:
        return {"effects":[],"duplicate":True}
    last=(await db.execute(text("""SELECT payload FROM subscription_device_events
        WHERE tenant_id=:t AND device_id=:d AND generation=:g AND payload->>'phone'=:phone
          AND payload->>'delivery'='true' ORDER BY received_at DESC,id DESC LIMIT 1"""),params)).scalar_one_or_none()
    if last and last.get("delivery_paused") is True:
        return skipped
    # A clearly labeled receipt needs manual verification, not a vision call.
    receipt=bool(payload.text and re.search(r"\bcomprovante\b",clean(payload.text)))
    if payload.text and emergency(payload.text):
        return await record_event(db,device,payload,skip_image=True)
    if receipt or not vision.available(device):
        return await record_event(db,device,payload,skip_image=True,
                                  fallback=RECEIPT_REPLY if receipt else PHOTO_FALLBACK)
    data={"delivery_image":True,"phone":payload.phone,"message_id":payload.message_id,
          "settings_revision":device["revision"],"media":payload.delivery_media.model_dump(mode="json")}
    event_id=(await db.execute(text("""INSERT INTO subscription_device_events
        (tenant_id,device_id,generation,provider_event_id,kind,occurred_at,payload)
        VALUES(:t,:d,:g,:event,'text',:at,CAST(:data AS jsonb))
        ON CONFLICT DO NOTHING RETURNING id"""),
        {**params,"event":payload.event_id,"at":payload.occurred_at,"data":json.dumps(data)})).scalar_one_or_none()
    if event_id is None:
        return {"effects":[],"duplicate":True}
    effect_id=await _insert_effect(db,device,event_id,"delivery-vision",
        {"type":"analyze_image","purpose":PURPOSE,"payload":{"phone":payload.phone,
         "message_id":payload.message_id,"max_bytes":vision.MAX_BYTES}})
    return {"effects":[{"operation_id":str(effect_id),"type":"analyze_image"}],"duplicate":False}


async def complete_image(db,device,event_id,analysis):
    from app.subscription_device_schemas import DeviceEvent
    row=(await db.execute(text("""SELECT id,provider_event_id,occurred_at,received_at,payload
        FROM subscription_device_events WHERE tenant_id=:t AND device_id=:d AND generation=:g
          AND id=:event AND payload->>'delivery_image'='true'"""),
        {"t":device["tenant_id"],"d":device["id"],"g":device["generation"],"event":event_id})).mappings().one_or_none()
    if not row or row["payload"].get("settings_revision")!=device["revision"] or not enabled(device):
        return {"effects":[],"duplicate":False,"delivery_image_handled":False}
    old=row["payload"]
    if old.get("delivery_image_outcome"):
        return {"effects":[],"duplicate":True,"delivery_image_handled":True}
    newer=await db.scalar(text("""SELECT 1 FROM subscription_device_events
        WHERE tenant_id=:t AND device_id=:d AND generation=:g AND payload->>'phone'=:phone
          AND (payload->>'delivery'='true' OR payload->>'delivery_image'='true' OR payload->>'delivery_audio'='true')
          AND (received_at,id)>(:received,:event) LIMIT 1"""),
        {"t":device["tenant_id"],"d":device["id"],"g":device["generation"],"phone":old["phone"],
         "received":row["received_at"],"event":event_id})
    if newer:
        await db.execute(text("""UPDATE subscription_device_events
            SET payload=payload||'{"delivery_image_outcome":"superseded"}'::jsonb
            WHERE tenant_id=:t AND id=:event"""),{"t":device["tenant_id"],"event":event_id})
        return {"effects":[],"duplicate":False,"delivery_image_handled":True}
    payload=DeviceEvent(device_id=device["id"],generation=device["generation"],
        settings_revision=device["revision"],event_id=row["provider_event_id"],kind="text",
        occurred_at=row["occurred_at"],phone=old["phone"],message_id=old["message_id"],
        delivery_media=old["media"])
    result=await record_event(db,device,payload,image_event=event_id,analysis=analysis)
    return {"effects":result["effects"],"duplicate":result["duplicate"],"delivery_image_handled":True}


async def record_audio(db, device, payload):
    """Admit a real audio event once; the existing ASR broker owns the grant."""
    from app.subscription_devices import _insert_effect, audio_capable
    skipped={"effects":[],"duplicate":False,"delivery_audio_skipped":True}
    if not enabled(device) or payload.origin!="contact" or not payload.phone or not payload.message_id or payload.audio is None:
        return skipped
    try:
        if not normalize_phone("+"+payload.phone):
            return skipped
    except ValueError:
        return skipped
    params={"t":device["tenant_id"],"d":device["id"],"g":device["generation"],"phone":payload.phone}
    if await db.scalar(text("""SELECT COALESCE((SELECT revoked FROM subscription_campaign_permissions
        WHERE tenant_id=:t AND phone=:phone),false)"""),params):
        return skipped
    duplicate=await db.scalar(text("""SELECT id FROM subscription_device_events
        WHERE tenant_id=:t AND device_id=:d AND (provider_event_id=:event OR
        (generation=:g AND payload->>'message_id'=:message AND payload->>'delivery_audio'='true')) LIMIT 1"""),
        {**params,"event":payload.event_id,"message":payload.message_id})
    if duplicate:
        return {"effects":[],"duplicate":True}
    data={"delivery_audio":True,"phone":payload.phone,"message_id":payload.message_id,
          "settings_revision":device["revision"],"audio":payload.audio.model_dump(mode="json")}
    event_id=(await db.execute(text("""INSERT INTO subscription_device_events
        (tenant_id,device_id,generation,provider_event_id,kind,occurred_at,payload)
        VALUES(:t,:d,:g,:event,'audio',:at,CAST(:data AS jsonb))
        ON CONFLICT DO NOTHING RETURNING id"""),
        {**params,"event":payload.event_id,"at":payload.occurred_at,"data":json.dumps(data)})).scalar_one_or_none()
    if event_id is None:
        return {"effects":[],"duplicate":True}
    last=(await db.execute(text("""SELECT payload FROM subscription_device_events
        WHERE tenant_id=:t AND device_id=:d AND generation=:g AND payload->>'phone'=:phone
          AND payload->>'delivery'='true' ORDER BY received_at DESC,id DESC LIMIT 1"""),params)).scalar_one_or_none()
    if last and last.get("delivery_paused") is True:
        return skipped
    meta=payload.audio
    if (not audio_enabled(device) or not audio_capable() or
            meta.seconds is not None and meta.seconds>AUDIO_MINUTES*60 or
            meta.size is not None and meta.size>AUDIO_BYTES):
        reply=await complete_audio(db,device,event_id,None)
        return {**skipped,"effects":reply["effects"]}
    effect_id=await _insert_effect(db,device,event_id,"delivery-transcribe",
        {"type":"transcribe","purpose":PURPOSE,"payload":{"phone":payload.phone,
         "message_id":payload.message_id,"max_seconds":AUDIO_MINUTES*60,"max_bytes":AUDIO_BYTES}})
    return {"effects":[{"operation_id":str(effect_id),"type":"transcribe"}],"duplicate":False}


async def complete_audio(db, device, event_id, transcript):
    """Consume an authorized source once without storing the raw transcript.

    Newer admitted text (including cancellation/control) supersedes an older
    audio; the old transcript cannot change the newer conversation.
    """
    from app.subscription_device_schemas import DeviceEvent
    row=(await db.execute(text("""SELECT id,provider_event_id,occurred_at,received_at,payload
        FROM subscription_device_events WHERE tenant_id=:t AND device_id=:d AND generation=:g
          AND id=:event AND kind='audio' AND payload->>'delivery_audio'='true'"""),
        {"t":device["tenant_id"],"d":device["id"],"g":device["generation"],"event":event_id})).mappings().one_or_none()
    if not row or row["payload"].get("settings_revision")!=device["revision"] or not enabled(device):
        return {"effects":[],"duplicate":False,"delivery_audio_handled":False}
    old=row["payload"]
    if old.get("delivery") or old.get("delivery_audio_outcome"):
        return {"effects":[],"duplicate":True,"delivery_audio_handled":True}
    newer=await db.scalar(text("""SELECT 1 FROM subscription_device_events
        WHERE tenant_id=:t AND device_id=:d AND generation=:g AND payload->>'phone'=:phone
          AND payload->>'delivery'='true' AND (received_at,id)>(:received,:event) LIMIT 1"""),
        {"t":device["tenant_id"],"d":device["id"],"g":device["generation"],
         "phone":old["phone"],"received":row["received_at"],"event":event_id})
    if newer:
        await db.execute(text("""UPDATE subscription_device_events
            SET payload=payload||'{"delivery_audio_outcome":"superseded"}'::jsonb
            WHERE tenant_id=:t AND id=:event"""),{"t":device["tenant_id"],"event":event_id})
        return {"effects":[],"duplicate":False,"delivery_audio_handled":True}
    valid=isinstance(transcript,str) and bool(transcript.strip()) and len(transcript)<=1500
    payload=DeviceEvent(device_id=device["id"],generation=device["generation"],
        settings_revision=device["revision"],event_id=row["provider_event_id"],kind="audio",
        occurred_at=row["occurred_at"],phone=old["phone"],message_id=old["message_id"],
        text=transcript.strip() if valid else None,audio=old["audio"])
    result=await record_event(db,device,payload,audio_event=event_id,
                              fallback=None if valid else AUDIO_FALLBACK)
    return {"effects":result["effects"],"duplicate":result["duplicate"],"delivery_audio_handled":True}


async def record_event(db, device, payload, *, audio_event=None, fallback=None,
                       image_event=None, analysis=None, skip_image=False):
    if payload.kind=="audio" and audio_event is None:
        return await record_audio(db,device,payload)
    media=payload.delivery_media
    if media and media.kind=="image" and image_event is None and not skip_image:
        return await record_image(db,device,payload)
    call=payload.kind=="call"
    if call and payload.phone:
        # Only the existing authenticated call-offer event is accepted.
        message_id="call:"+hashlib.sha256((payload.call_id+":"+payload.call_from).encode()).hexdigest()[:40]
        payload=payload.model_copy(update={"message_id":message_id})
    if (not enabled(device) or (payload.kind not in {"text","call"} and audio_event is None)
            or payload.origin!="contact" or not payload.phone or not payload.message_id
            or not (payload.text or payload.selection_id or fallback or media or call)):
        return {"effects":[],"duplicate":False}
    if payload.text and len(payload.text)>1500 and not media:
        return {"effects":[],"duplicate":False}
    try:
        phone=normalize_phone("+"+payload.phone)
    except ValueError:
        return {"effects":[],"duplicate":False}
    params={"t":device["tenant_id"],"d":device["id"],"g":device["generation"],"phone":payload.phone}
    if await db.scalar(text("""SELECT COALESCE((SELECT revoked FROM subscription_campaign_permissions
        WHERE tenant_id=:t AND phone=:phone),false)"""),params):
        return {"effects":[],"duplicate":False,"ignored":"contact_optout"}
    if call and await db.scalar(text("""SELECT 1 FROM subscription_device_events
        WHERE tenant_id=:t AND device_id=:d AND generation=:g AND payload->>'phone'=:phone
          AND payload->>'delivery_call'='true' AND received_at>clock_timestamp()-interval '5 minutes' LIMIT 1"""),params):
        return {"effects":[],"duplicate":True}
    if audio_event is None and image_event is None:
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
            return {"effects":[],"duplicate":False} if media or call else {"effects":[],"duplicate":False,"ignored":"out_of_order"}
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
                  AND purpose=:purpose AND type='send_text' AND status='accepted' AND settings_revision=:revision
                  AND payload->>'phone'=:phone"""),
                {**params,"event":last["id"],"purpose":PURPOSE,"revision":device["revision"]})).mappings().one_or_none()
            delivered=accepted is not None
            accepted_provider_id=accepted["provider_id"] if accepted else None
    if media and media.kind=="live_location" and (state.get("pending_pin") or {}).get("source_id")!=media.source_id:
        # Never resume a previously consumed or expired share from a replayed
        # upsert. A new location share must have a new provider identity.
        live_source=hashlib.sha256(media.source_id.encode()).hexdigest()
        if media.update or await db.scalar(text("""SELECT 1 FROM subscription_device_events
            WHERE tenant_id=:t AND device_id=:d AND generation=:g AND payload->>'phone'=:phone
              AND payload->>'delivery_live_source'=:source LIMIT 1"""),{**params,"source":live_source}):
            return {"effects":[],"duplicate":False}
    # Savepoint avoids retaining half an order after a business rejection. The
    # checkout's own idempotency key is reused across delivery retries.
    body = payload.text
    if payload.selection_id:
        body = confirmation_selection(state, payload.selection_id, payload.quoted_message_id, accepted_provider_id)
    if state.get("stage")=="confirm" and image_event is None and last:
        # An admitted but unprocessed photo can invalidate the prior quote.
        outstanding=await db.scalar(text("""SELECT 1 FROM subscription_device_events
            WHERE tenant_id=:t AND device_id=:d AND generation=:g AND payload->>'phone'=:phone
              AND payload->>'delivery_image'='true' AND NOT (payload ? 'delivery_image_outcome')
              AND received_at>(SELECT received_at FROM subscription_device_events WHERE id=:last)
              LIMIT 1"""),{**params,"last":last["id"]})
        if outstanding:
            media_review(state)
            delivered=False
    if media and payload.text and emergency(payload.text):
        async with get_session_factory()() as runtime:
            async with tenant_transaction(runtime,device["tenant_id"]):
                reply,state=await answer(runtime,device["tenant_id"],phone,payload.text,state,False)
    elif call:
        if state.get("paused"):
            return {"effects":[],"duplicate":False}
        media_review(state)
        reply=CALL_REPLY
    elif fallback is not None:
        if media:
            media_review(state)
        reply=None if state.get("paused") else fallback
    elif media:
        async with get_session_factory()() as runtime:
            async with tenant_transaction(runtime,device["tenant_id"]):
                reply,state,ignored=await handle_media(runtime,device["tenant_id"],phone,state,media,payload.occurred_at,analysis=analysis)
        if ignored:
            return {"effects":[],"duplicate":False}
    elif body:
        async with get_session_factory()() as runtime:
            async with tenant_transaction(runtime,device["tenant_id"]):
                reply,state=await answer(runtime,device["tenant_id"],phone,body,state,delivered,
                                         allow_confirmation=audio_event is None)
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
    if call:
        data["delivery_call"]=True
    if media:
        # The event metadata contains no raw coordinates. The encrypted
        # draft and confirmed address retain the point; replies remain scoped.
        data["delivery_media_kind"]=media.kind
        if media.kind=="live_location":
            data["delivery_live_source"]=hashlib.sha256(media.source_id.encode()).hexdigest()
    if state and not state.get("paused"):
        data["delivery_state"]=channels._cipher().encrypt(json.dumps(jsonable_encoder(state),ensure_ascii=False).encode()).decode()
    if audio_event is not None:
        data["delivery_audio_outcome"]="fallback" if fallback else "transcribed"
        event_id=(await db.execute(text("""UPDATE subscription_device_events SET payload=payload||CAST(:data AS jsonb)
            WHERE tenant_id=:t AND device_id=:d AND generation=:g AND id=:event
              AND payload->>'delivery_audio'='true' AND NOT (payload ? 'delivery_audio_outcome') RETURNING id"""),
            {**params,"event":audio_event,"data":json.dumps(data)})).scalar_one_or_none()
    elif image_event is not None:
        data["delivery_image_outcome"]="analyzed" if analysis is not None else "fallback"
        event_id=(await db.execute(text("""UPDATE subscription_device_events SET payload=(payload-'media')||CAST(:data AS jsonb)
            WHERE tenant_id=:t AND device_id=:d AND generation=:g AND id=:event
              AND payload->>'delivery_image'='true' AND NOT (payload ? 'delivery_image_outcome') RETURNING id"""),
            {**params,"event":image_event,"data":json.dumps(data)})).scalar_one_or_none()
    else:
        event_id=(await db.execute(text("""INSERT INTO subscription_device_events
            (tenant_id,device_id,generation,provider_event_id,kind,occurred_at,payload)
            VALUES(:t,:d,:g,:event,:kind,:at,CAST(:data AS jsonb)) ON CONFLICT DO NOTHING RETURNING id"""),
            {**params,"event":payload.event_id,"kind":"call" if call else "text","at":payload.occurred_at,"data":json.dumps(data)})).scalar_one_or_none()
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
        AND payload->>'phone'=:phone AND type='send_text' AND status='pending'"""),{**params,"purpose":PURPOSE})
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
    if not enabled(device) or effect["purpose"]!=PURPOSE:
        return False
    if effect["type"]=="analyze_image":
        if not vision.available(device):
            return False
        return bool(await db.scalar(text("""SELECT 1 FROM subscription_device_events e
            WHERE e.tenant_id=:t AND e.device_id=:d AND e.generation=:g AND e.id=:event
              AND e.payload->>'delivery_image'='true' AND NOT (e.payload ? 'delivery_image_outcome')
              AND e.payload->>'phone'=:phone AND e.payload->>'message_id'=:message
              AND e.occurred_at>clock_timestamp()-interval '5 minutes'
              AND NOT EXISTS(SELECT 1 FROM subscription_device_events newer WHERE newer.tenant_id=e.tenant_id
                AND newer.device_id=e.device_id AND newer.generation=e.generation AND newer.payload->>'phone'=:phone
                AND (newer.payload->>'delivery'='true' OR newer.payload->>'delivery_image'='true' OR newer.payload->>'delivery_audio'='true')
                AND (newer.received_at,newer.id)>(e.received_at,e.id))
              AND NOT COALESCE((SELECT revoked FROM subscription_campaign_permissions
                WHERE tenant_id=:t AND phone=:phone),false)"""),
            {"t":device["tenant_id"],"d":device["id"],"g":device["generation"],"event":effect["event_id"],
             "phone":effect["payload"]["phone"],"message":effect["payload"]["message_id"]}))
    if effect["type"]=="transcribe":
        from app.subscription_devices import audio_capable
        if not audio_enabled(device) or not audio_capable():
            return False
        return bool(await db.scalar(text("""SELECT 1 FROM subscription_device_events e
            WHERE e.tenant_id=:t AND e.device_id=:d AND e.generation=:g AND e.id=:event AND e.kind='audio'
              AND e.payload->>'delivery_audio'='true' AND NOT (e.payload ? 'delivery_audio_outcome')
              AND e.payload->>'phone'=:phone AND e.payload->>'message_id'=:message
              AND e.occurred_at>clock_timestamp()-interval '5 minutes'
              AND NOT COALESCE((SELECT revoked FROM subscription_campaign_permissions
                WHERE tenant_id=:t AND phone=:phone),false)"""),
            {"t":device["tenant_id"],"d":device["id"],"g":device["generation"],
             "event":effect["event_id"],"phone":effect["payload"]["phone"],"message":effect["payload"]["message_id"]}))
    if effect["type"]!="send_text":
        return False
    return bool(await db.scalar(text("""SELECT 1 FROM subscription_device_events e
        WHERE e.tenant_id=:t AND e.device_id=:d AND e.generation=:g AND e.id=:event
          AND e.payload->>'delivery'='true' AND e.payload->>'phone'=:phone
          AND e.occurred_at>clock_timestamp()-interval '24 hours'
          AND NOT EXISTS(SELECT 1 FROM subscription_device_events newer WHERE newer.tenant_id=e.tenant_id
            AND newer.device_id=e.device_id AND newer.generation=e.generation
            AND (newer.payload->>'delivery'='true' OR newer.payload->>'delivery_image'='true')
            AND newer.payload->>'phone'=:phone
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
    # A timed-out image attempt cannot be retried against the provider.
    # When still current, emit one safe fallback (or suppress it if superseded).
    if enabled(device):
        expired=(await db.execute(text("""SELECT f.event_id FROM subscription_device_effects f
            JOIN subscription_device_events e ON (e.tenant_id,e.id)=(f.tenant_id,f.event_id)
            WHERE f.tenant_id=:t AND f.device_id=:d AND f.generation=:g AND f.settings_revision=:r
              AND f.purpose=:purpose AND f.type='analyze_image'
              AND f.status IN ('uncertain','not_sent','failed')
              AND NOT (e.payload ? 'delivery_image_outcome') LIMIT 10"""),params)).scalars().all()
        for event_id in expired:
            await complete_image(db,device,event_id,None)
    rows=(await db.execute(text("""SELECT id AS operation_id,type FROM subscription_device_effects
        WHERE tenant_id=:t AND device_id=:d AND generation=:g AND settings_revision=:r
          AND purpose=:purpose AND type='send_text' AND status='pending'
          AND due_at<=clock_timestamp() AND expires_at>clock_timestamp() ORDER BY created_at,id LIMIT 25"""),params)).mappings().all() if enabled(device) else []
    reconcile=(await db.execute(text("""SELECT id AS operation_id,type,device_id,generation,settings_revision
        FROM subscription_device_effects WHERE tenant_id=:t AND device_id=:d AND generation=:g
          AND purpose=:purpose AND type='send_text' AND status='uncertain' ORDER BY created_at,id LIMIT 25"""),params)).mappings().all()
    return jsonable_encoder({"effects":[dict(row) for row in rows],"reconcile":[dict(row) for row in reconcile]})
