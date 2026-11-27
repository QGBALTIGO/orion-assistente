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

from app import delivery_storefront as store, qr_channels as channels
from app.business_profile_schemas import ProfileModel
from app.database import get_session_factory, tenant_transaction
from app.management_schemas import normalize_phone, MAX_MONEY
from app.onboarding_schemas import Address
from app.qr_commerce_understanding import interpret, norm
from app.qr_dialogue import contextual_response
from app.workspace_policy import delivery_preview_enabled

PURPOSE = "delivery_reply"
STATE_TTL = 1800
PAYMENTS = {"cash": "dinheiro", "pix": "Pix manual", "debit_card": "cartão de débito",
            "credit_card": "cartão de crédito"}
HELP = "Se preferir falar com uma pessoa, escreva atendente."
ADDRESS_PROMPT = ("Qual o endereço de entrega? Informe rua, número, bairro, cidade e UF. "
                  "Pode separar por vírgulas e acrescentar o complemento no final.")


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


def forbidden(body):
    value = re.sub(r"\baguas?\s+(?:com|sem)\s+gas\b", "agua", norm(body))
    return bool(store.RESTRICTED.search(value) or re.search(
        r"\b(?:glp|buj[aã]o|botij\w*|gas(?:es)?|combustivel|querosene|gasolina|etanol|diesel|fogos|explosiv\w*|alcool|veneno|solvente|acido)\b", value))


def eligible_water(shop, row):
    # Explicit storefront approval plus positive, narrow classification.
    # A label alone cannot make a fuel or another restricted item eligible.
    names = " ".join(store.normalized_text(row.get(k)) for k in
                     ("label", "description", "name", "variant_name", "product_description", "category", "brand"))
    return (row["kind"] == "water" and bool(re.search(r"\bagua\b", names))
            and store.available_item(shop, row) and not forbidden(names))


async def shop_and_items(db, tenant, *, lock=False):
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
    lines = [f"{row['label']} — {money(row['unit_price_cents'])}" +
             ("" if in_stock(row, now) else " (indisponível)") for row in items[:10]]
    suffix = "\nHá mais opções; informe o nome do produto." if len(items)>10 else ""
    return "Produtos disponíveis para consulta:\n" + "\n".join(lines) + suffix + "\nQual você deseja?"


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
    match = re.fullmatch(r"(?:troco\s+(?:para|pra|de)\s+)?(?:r\$\s*)?(\d{1,7}(?:[.,]\d{1,2})?)", value)
    if not match:
        raise ValueError("valor")
    amount = int(Decimal(match.group(1).replace(",", "."))*100)
    if not 0 < amount <= MAX_MONEY:
        raise ValueError("valor")
    return amount


def state_question(stage, state, shop, items):
    if stage == "name":
        return "Qual é o seu nome?"
    if stage == "mode":
        choices = [label for key,label in (("pickup","retirada"),("delivery","entrega")) if shop.get("allow_"+key)]
        return "Você prefere " + " ou ".join(choices) + "?"
    if stage == "address":
        return ADDRESS_PROMPT
    if stage == "exchange":
        return "Você confirma que tem os vasilhames vazios correspondentes aos itens do pedido para trocar?"
    if stage == "payment":
        choices = [PAYMENTS[key] for key in shop["payment_methods"]]
        return "Como prefere pagar? " + ", ".join(choices) + "."
    if stage == "change":
        return "Precisa de troco? Informe o valor para o qual precisa de troco ou diga sem troco."
    if stage == "offer":
        return "Deseja fazer o pedido?"
    return state.get("summary") or list_products(items, datetime.now(timezone.utc))


async def next_step(db, tenant, shop, items, state, phone):
    chosen = {str(row["variant_id"]):row for row in items}
    if not state.get("items") or any(line["variant_id"] not in chosen for line in state["items"]):
        return list_products(items, datetime.now(timezone.utc)), {}
    if not state.get("customer_name"):
        name = (await db.execute(text("""SELECT name FROM customers WHERE tenant_id=:t
            AND whatsapp_e164=:phone AND active AND deleted_at IS NULL ORDER BY id LIMIT 2"""),
            {"t":tenant,"phone":phone})).scalars().all()
        if len(name) == 1:
            state["customer_name"] = name[0]
        else:
            state["stage"] = "name"
            return state_question("name",state,shop,items),state
    if not state.get("mode"):
        state["stage"] = "mode"
        return state_question("mode",state,shop,items),state
    if state["mode"] == "delivery" and not state.get("address"):
        state["stage"] = "address"
        return ADDRESS_PROMPT,state
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
    lines = [f"{line['quantity']} × {line['product_name']} — {money(line['total_cents'])}" for line in estimate["items"]]
    address = draft.address
    destination = (f"{address.street}, {address.number or 's/n'}, {address.neighborhood}, {address.city}/{address.state}" +
                   (f" — {address.complement}" if address.complement else "")) if address else (shop.get("pickup_details") or "Retirada no estabelecimento")
    summary = ("Confira o pedido antes de confirmar:\n" + "\n".join(lines) +
               f"\n{'Entrega' if address else 'Retirada'}: {destination}\nTaxa: {money(estimate['delivery_fee_cents'])}"
               f"\nTotal: {money(estimate['total_cents'])}\nPagamento: {PAYMENTS[draft.payment_preference]}")
    if estimate.get("eta_minutes") is not None:
        summary += f"\nPrevisão estimada: {estimate['eta_minutes']} min."
    if draft.accept_exchange:
        summary += "\nCom devolução dos vasilhames vazios."
    if draft.change_for_cents is not None:
        summary += "\nTroco previsto: "+money(draft.change_for_cents-estimate["total_cents"])
    summary += "\nResponda CONFIRMO para registrar o pedido, ou NÃO para cancelar o resumo."
    state.update(stage="confirm",proposal=proposal.model_dump(mode="json"),summary=summary)
    return summary,state


async def answer(db, tenant, phone, body, state, summary_delivered):
    value = clean(body)
    # Never process an emergency or a restricted sale as a water purchase.
    if re.search(r"\b(?:vazamento|cheiro de gas|incendio|pegando fogo|explosao)\b", value):
        return "Em caso de risco imediato, afaste-se do local e ligue 193. Interrompi o atendimento automático.",{"paused":True}
    if state.get("paused"):
        if value not in {"retomar atendimento","voltar atendimento","voltar ao bot"}:
            return None,state
        state = {}
    if value in {"atendente","humano","falar com atendente","falar com humano","parar","pare","sair","stop","cancelar atendimento"}:
        return "Pausei o atendimento automático. Aguarde uma pessoa da empresa responder. Para voltar, escreva retomar atendimento.",{"paused":True}
    if forbidden(body):
        return "Não realizo cotação ou comercialização de produtos restritos neste canal.",{}
    shop, items = await shop_and_items(db,tenant,lock=state.get("stage")=="confirm")
    if not shop or not items:
        return "O catálogo deste atendimento ainda não está configurado. "+HELP,{}
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
        return "Pausei o atendimento automático para a equipe conferir seu pedido. Nenhum pedido foi cancelado automaticamente.",{"paused":True}
    if not store.is_open(shop, datetime.now(timezone.utc)):
        return "O atendimento comercial está fechado no momento. "+HELP,{}
    stage = state.get("stage")
    if not stage and value in {"oi","ola","bom dia","boa tarde","boa noite","menu","catalogo","retomar atendimento","voltar atendimento","voltar ao bot"}:
        return "Olá! Sou o atendente virtual da empresa. Posso ajudar com seus pedidos de água. "+list_products(items,datetime.now(timezone.utc))+" "+HELP,{}
    if stage=="confirm":
        if value in {"nao","não","cancelar","desistir","nao confirmo"}:
            return "Resumo cancelado. Nenhum pedido novo foi registrado.",{}
        if value in {"confirmo","confirmar","pode confirmar","sim","isso mesmo","fechou","de acordo","tudo certo"} and affirmative(body):
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
            reply = f"Pedido {result['order_code']} registrado! Total: {money(result['total_cents'])}."
            if proposal.payment_preference=="pix":
                reply += " O Pix depende da conferência manual do pagamento pela empresa."
            if shop["enabled"] and store.configured_public(tenant,shop["niche"]):
                from app.public_origins import canonical_public_origin
                # The public store accepts a private tracking code; do not
                # reveal another customer's address or order history.
                reply += f"\nAcompanhe em {canonical_public_origin()}/loja/{shop['slug']}. Código: {result['tracking_code']}"
            return reply,{"last_order":result["order_code"]}
        return "Nenhuma alteração foi registrada. "+state["summary"],state
    if stage=="name":
        name = " ".join(body.split())
        if not 2<=len(name)<=120 or not any(c.isalpha() for c in name) or re.search(r"\d{4,}",name):
            return "Informe apenas seu nome, por favor.",state
        state["customer_name"]=name
    elif stage=="mode":
        mode = "delivery" if re.search(r"\b(?:entrega|entregar|em casa|a domicilio)\b",value) else "pickup" if re.search(r"\b(?:retirada|retirar|buscar|balcao)\b",value) else None
        if not mode or not shop.get("allow_"+mode):
            return state_question("mode",state,shop,items),state
        state["mode"]=mode
    elif stage=="address":
        addr=parse_address(body)
        if addr is None:
            return ADDRESS_PROMPT,state
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
        state["payment_preference"]=method
    elif stage=="change":
        try:
            state["change_for_cents"]=change(body)
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
            return "\n".join(f"{indexed[line['variant_id']]['label']} — {money(indexed[line['variant_id']]['unit_price_cents'])} por unidade" for line in plan.items)+"\nDeseja fazer o pedido?",state
        if re.search(r"\b(?:entrega|entregar|em casa)\b",value) and shop.get("allow_delivery"):
            state["mode"]="delivery"
        elif re.search(r"\b(?:retirada|retirar|buscar)\b",value) and shop.get("allow_pickup"):
            state["mode"]="pickup"
        method=payment(body)
        if method in shop["payment_methods"]:
            state["payment_preference"]=method
    return await next_step(db,tenant,shop,items,state,phone)


async def record_event(db, device, payload):
    if not enabled(device) or payload.kind!="text" or payload.origin!="contact" or not payload.message_id or not payload.text:
        return {"effects":[],"duplicate":False}
    if len(payload.text)>1500:
        return {"effects":[],"duplicate":False}
    try:
        phone=normalize_phone("+"+payload.phone)
    except ValueError:
        return {"effects":[],"duplicate":False}
    params={"t":device["tenant_id"],"d":device["id"],"g":device["generation"],"phone":payload.phone}
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
        delivered=bool(await db.scalar(text("""SELECT 1 FROM subscription_device_effects
            WHERE tenant_id=:t AND device_id=:d AND event_id=:event AND purpose=:purpose AND status='accepted'"""),
            {**params,"event":last["id"],"purpose":PURPOSE}))
    # Savepoint avoids retaining half an order after a business rejection. The
    # checkout's own idempotency key is reused across delivery retries.
    async with get_session_factory()() as runtime:
        async with tenant_transaction(runtime,device["tenant_id"]):
            reply,state=await answer(runtime,device["tenant_id"],phone,payload.text,state,delivered)
    data={"delivery":True,"phone":payload.phone,"message_id":payload.message_id,
          "settings_revision":device["revision"],"delivery_paused":state.get("paused") is True}
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
    effect_id=await _insert_effect(db,device,event_id,"delivery-reply",
        {"type":"send_text","purpose":PURPOSE,"payload":{"phone":payload.phone,"text":reply}})
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
