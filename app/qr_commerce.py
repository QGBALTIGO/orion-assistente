"""Factual QR commerce turns. The caller locks and persists state by tenant/phone.

Returned state is INTERNAL and can contain a confirmation token. Return only
answer/step to the external bridge/UI. Preview uses estimates and no write path.
"""
from copy import deepcopy
from dataclasses import replace
import hashlib
import os
import re
from uuid import UUID

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder

from app import qr_commerce_store as store
from app.qr_commerce_understanding import explicit_confirmation, interpret, norm
from app.qr_dialogue import contextual_response, interpret_contextual_response
from app.qr_presentation import (render_commerce_order, render_commerce_summary, InteractionContext,
    ApprovedSuggestion, render_approved_suggestion)
from app.qr_presentation import escape_label, fits_message
from app.qr_commerce_information import catalog_answer, existence_query
from app import qr_commerce_fulfillment as receiving


def enabled():
    return os.getenv("COMMERCIAL_CATALOG_ENABLED") == "1" and os.getenv("QR_AI_COMMERCE_ENABLED") == "1"


MAX_REPLY = 1200
TOO_LONG = "O resumo completo ficou extenso. Quais itens você quer revisar primeiro? Mantive sua seleção; nenhuma nova operação foi confirmada."


class SummaryTooLong(ValueError):
    pass


def bounded(answer):
    if not fits_message(answer,MAX_REPLY):
        raise SummaryTooLong("Complete summary exceeds transport limit")
    return answer


def summary(estimate, preferences=None):
    details = receiving.render_receiving(estimate)
    return bounded(render_commerce_summary(estimate, preferences) + ("\n\n" + details if details else ""))


def order_summary(estimate, preferences=None, *, kind="proposal", order_id=None):
    answer = render_commerce_order(estimate, preferences, state=kind, order_id=order_id)
    details = receiving.render_receiving(estimate)
    if details:
        answer += "\n\n" + details
    if kind == "proposal":
        answer += "\n\n_Resumo válido por 15 minutos._"
    return bounded(answer)


def preflight(facts, preferences, order_id, *, prefix=""):
    # Validate every possible outcome BEFORE any write, including inside the
    # database transaction against its freshly loaded factual snapshot.
    bounded(prefix + summary(facts, preferences) + "\n\n_Orçamento válido por 15 minutos. Nenhum pedido foi confirmado._")
    for kind in ("proposal", "confirmed", "cancel_proposal", "cancelled", "preview"):
        bounded(prefix + order_summary(facts, preferences, kind=kind, order_id=order_id))


def finish(answer, step, state, scope):
    if not fits_message(answer,MAX_REPLY):
        state.pop("pending", None)
        answer, step = TOO_LONG, "commerce_split_review"
    state = jsonable_encoder(state)
    state.update(kind="commerce", tenant=str(scope.tenant), phone=scope.phone,
                 last_message_id=scope.message_id, last_answer=answer, last_step=step)
    return {"answer": answer, "step": step, "state": state}


def fresh_state(scope, state):
    if not state or state.get("kind") != "commerce":
        return {"cart": []}
    if state.get("tenant") != str(scope.tenant) or state.get("phone") != scope.phone:
        raise HTTPException(403, "Contexto fora do contato autenticado")
    return deepcopy(state)


async def prepare(scope, items, state, *, kind, preview, customer_name, preferences=None, prefix="", body=""):
    fulfillment = None
    if receiving.enabled():
        state["cart"] = items
        state["requested_kind"] = kind
        options = await store.fulfillment_options(scope)
        fulfillment, prompt = await receiving.select(scope, body, state, options, store)
        if prompt:
            facts = await store.estimate(scope, items)
            return finish(prefix + summary(facts, preferences) + "\n\n" + prompt,
                "commerce_fulfillment" if options.get("enabled") else "commerce_review_required", state, scope)
    facts = await store.estimate(scope, items, **({"fulfillment": fulfillment} if fulfillment else {}))
    state["cart"] = items
    state.pop("pending", None)
    validate = lambda value: preflight(value, preferences, str(scope.identity("order")), prefix=prefix)
    validate(facts)
    if preview:
        state["preview_pending"] = kind == "order"
        return finish(order_summary(facts, preferences, kind="preview"), "commerce_preview", state, scope)
    quote = await store.create_quote(scope, items, customer_name=customer_name, validate_summary=validate,
        **({"fulfillment": fulfillment} if fulfillment else {}))
    state["last_quote_id"] = str(quote["id"])
    state["requested_kind"] = kind
    if kind == "quote":
        return finish(prefix + summary(quote, preferences) + "\n\n_Orçamento válido por 15 minutos. Nenhum pedido foi confirmado._", "commerce_quote", state, scope)
    order = await store.create_order(scope, quote["id"], validate_summary=validate)
    state["last_order_id"] = str(order["id"])
    if order["status"] != "draft":
        return finish("Este pedido já está " + ("confirmado" if order["status"] == "confirmed" else "cancelado") + ". Pagamento não verificado.", "commerce_status", state, scope)
    if not quote.get("confirmation_requirements_met"):
        return finish(prefix + order_summary(quote, preferences), "commerce_review_required", state, scope)
    state["pending"] = {"action": "confirm", "quote_id": str(quote["id"]), "order_id": str(order["id"]),
        "revision": order["revision"], "token": quote["confirmation_token"],
        "summary_message_id": scope.message_id, "expires_at": quote["expires_at"]}
    return finish(prefix + order_summary(quote, preferences),
                  "commerce_confirm", state, scope)


async def probe(tenant, phone, body, *, message_id, state=None):
    """Read-only routing probe for mixed product/appointment requests."""
    if not enabled():
        return False
    scope = store.CustomerScope(tenant, phone, message_id)
    current = fresh_state(scope, state)
    plan = interpret(body, await store.active_catalog(scope), current.get("cart", []), current.get("choices", {}))
    return plan.relevant or bool(receiving.enabled() and receiving.receiving_reference(body, current) and
        (current.get("cart") or receiving.interpret_receiving(body).query)) or bool(current.get("kind")=="commerce" and (
        plan.intent in {"confirm", "cancel", "status", "adjust", "decline"} or
        re.search(r"\b(?:nao quero|sem|dispenso)\b.*\b(?:complementos?|adicionais?|extras?)\b",norm(body))))


async def with_suggestion(answer, scope, products, state, body, preferences, *, catalog, information_only=False):
    """Optional presentation only: approved relation never inserts a cart item."""
    context=InteractionContext.from_message(body,information_only=information_only,
        declined=state.get("declined_complements",False),complaint=state.get("sales_paused",False))
    if context.suppress_follow_up or not preferences or preferences.get("commercial_intensity")!="contextual":
        return answer
    if len(products)!=1:
        return answer
    selected={str(item["variant_id"]) for item in state.get("cart",[])}
    selected_products={str(p["id"]) for p in catalog if any(str(v["id"]) in selected for v in p["variants"])}
    relations=await store.approved_complements(scope,products[0]["id"])
    for relation in relations:
        # A target already in the factual current cart is not an extra offer.
        if str(relation["target_product_id"]) in selected_products or str(relation["approved_relation_id"]) in state.get("suggested_relations",[]):
            continue
        if len(relation["name"])>120:
            continue
        suggestion=ApprovedSuggestion(name=relation["name"],approved_relation_id=str(relation["approved_relation_id"]))
        addition=render_approved_suggestion(suggestion,preferences,context=context)
        if addition and fits_message(answer+"\n\n"+addition,MAX_REPLY):
            state["suggested_relations"]=[*state.get("suggested_relations",[]),str(relation["approved_relation_id"])][-20:]
            return answer+"\n\n"+addition
        break
    return answer


async def handle(tenant, phone, body, *, message_id, state=None, preview=False,
                 customer_name=None, allow_orders=False, preferences=None, semantic_kind=None):
    if not enabled():
        return None
    scope = store.CustomerScope(tenant, phone, message_id)
    state = fresh_state(scope, state)
    message_digest = hashlib.sha256(body.encode()).hexdigest() if receiving.enabled() else None
    if state.get("last_message_id") == message_id:
        if message_digest and state.get("last_input_digest") not in {None, message_digest}:
            raise HTTPException(409, "Esta mensagem já apresentou outro contrato; envie a alteração em uma nova mensagem")
        return {"answer": state["last_answer"], "step": state["last_step"], "state": state}
    if message_digest:
        state["last_input_digest"] = message_digest
    catalog = await store.active_catalog(scope)
    plan = interpret(body, catalog, state.get("cart", []), state.get("choices", {}))
    receiving_turn = receiving.enabled() and receiving.receiving_reference(body, state)
    receiving_intent = receiving.interpret_receiving(body)
    if receiving_turn and receiving_intent.query:
        plan = replace(plan, relevant=True, intent="faq")
    if receiving_turn and not receiving_intent.query and plan.intent not in {"cancel", "status"} and (state.get("cart") or plan.items):
        if (not receiving_intent.relevant and not plan.items and receiving.target_reference(body)
                and (state.get("fulfillment") or {}).get("pickup_appointment_id")):
            state["fulfillment_waiting_for"] = "appointment"
        plan = replace(plan, intent="adjust" if state.get("cart") else plan.intent, relevant=True, correction=True)
    decision = contextual_response(body, awaiting_confirmation=bool(state.get("pending")), semantic_kind=semantic_kind,
        confirmation_action="cancel" if state.get("pending", {}).get("action") == "cancel" else "order")
    pending = state.get("pending", {})
    if (semantic_kind is None and decision.reason == "unresolved" and pending.get("summary_message_id")
            and state.get("last_message_id") == pending["summary_message_id"] and message_id != pending["summary_message_id"]):
        decision = await interpret_contextual_response(message=body,awaiting_confirmation=True,
            confirmation_action="cancel" if pending.get("action") == "cancel" else "order",
            key=os.getenv("GROQ_API_KEY", ""),model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"))
    if decision.kind == "affirm" and not receiving_turn:
        plan = replace(plan, intent="confirm")
    if plan.intent == "confirm" and "cancel" in norm(body) and state.get("pending", {}).get("action") != "cancel":
        plan = replace(plan, intent="cancel")
    if state.get("choices") and plan.items and plan.intent == "faq":
        plan = replace(plan, intent=state.get("choice_intent", "price"))
    in_flow = bool(state.get("cart") or state.get("pending") or state.get("last_order_id"))
    commercial_context=InteractionContext.from_message(body)
    if commercial_context.declined:
        state["declined_complements"]=True
    if commercial_context.complaint:
        state["sales_paused"]=True
    if (in_flow or state.get("kind")=="commerce") and re.search(r"\b(?:nao quero|sem|dispenso)\b.*\b(?:complementos?|adicionais?|extras?)\b",norm(body)):
        state["declined_complements"]=True
        return finish("Tudo bem, mantenho sua seleção sem sugestões adicionais.","commerce_information",state,scope)
    if not plan.relevant and not ((in_flow or state.get("kind")=="commerce") and plan.intent in {"confirm", "cancel", "status", "adjust", "decline"}):
        if pending and decision.kind == "uncertain":
            return finish("Você quer ajustar algum detalhe ou revisar o resumo antes de continuar? Nenhuma nova operação foi confirmada.", "commerce_clarify_confirmation", state, scope)
        return None
    exact_faqs = [faq["answer"] for p in catalog for faq in p.get("faqs", [])
                  if norm(faq["question"]).strip(" ?!.") == norm(body).strip(" ?!.")]
    if len(set(exact_faqs)) == 1 and not receiving_turn:
        return finish(exact_faqs[0], "commerce_information", state, scope)
    if existence_query(body) and not receiving_turn:
        return finish(catalog_answer(body, catalog) or
            "Não consegui identificar uma resposta completa no catálogo ativo. Qual produto ou variante você quer consultar?",
            "commerce_information", state, scope)
    if plan.intent == "decline" or norm(body).strip(" .!?") in {"nao", "nao quero", "deixa pra la"}:
        state.pop("pending", None)
        state.pop("requested_kind", None)
        return finish("Tudo bem. Nenhum novo pedido foi confirmado.", "commerce_declined", state, scope)
    if plan.ambiguous_products:
        state["choices"] = plan.ambiguous_quantities
        state["choice_intent"] = plan.intent if plan.intent in {"order", "quote", "stock", "price"} else "price"
        choices = []
        for product in catalog:
            if product["name"] in plan.ambiguous_products:
                choices.append(product["name"] + ": " + "; ".join(v["name"] +
                    (" (" + ", ".join(v.get("attributes", {}).values()) + ")" if v.get("attributes") else "")
                    for v in product["variants"]))
        state.pop("pending", None)
        return finish("Qual variante você deseja?\n" + "\n".join(choices), "commerce_choose_variant", state, scope)
    if plan.items:
        state.pop("choices", None)
        state.pop("choice_intent", None)
    if plan.invalid_quantity or len(plan.items) > 30:
        state.pop("pending", None)
        return finish("Informe uma quantidade inteira de 1 a 1000 por variante.", "commerce_quantity", state, scope)
    try:
        if receiving_turn and receiving_intent.query:
            return finish(receiving.options_answer(await store.fulfillment_options(scope)), "commerce_information", state, scope)
        if plan.intent == "confirm":
            pending = state.get("pending")
            if preview:
                state.pop("pending", None)
                return finish("Simulação concluída. Nenhum pedido, pagamento ou estoque foi alterado.", "commerce_preview_complete", state, scope)
            if not allow_orders or not pending or decision.kind != "affirm":
                return finish("Não há um resumo de pedido aguardando confirmação. Solicite o pedido para conferir os itens primeiro.", "commerce_no_confirmation", state, scope)
            if receiving.enabled() and pending["action"] != "cancel":
                options = await store.fulfillment_options(scope)
                if (options.get("enabled") is not True or state.get("fulfillment_context") != receiving.context_revision(options)):
                    receiving.clear_selection(state)
                    state["fulfillment_context"] = receiving.context_revision(options)
                    return finish("As opções de recebimento precisam de uma nova revisão.\n\n" + receiving.mode_question(options),
                        "commerce_new_confirmation_required", state, scope)
            if pending["action"] == "cancel" and norm(body).strip(" .!") in {"confirmo o pedido", "confirmo esse pedido", "pode confirmar meu pedido"}:
                state.pop("pending", None)
                return finish("Você deseja manter o pedido ou solicitar o cancelamento? Nenhuma alteração foi feita.", "commerce_clarify_action", state, scope)
            if state.get("last_message_id") != pending.get("summary_message_id"):
                state.pop("pending", None)
                return finish("Conversamos sobre outros detalhes após o resumo. Solicite um novo resumo do pedido antes de confirmar.", "commerce_new_confirmation_required", state, scope)
            if pending.get("summary_message_id") == scope.message_id:
                return finish("Responda à confirmação em uma nova mensagem após conferir o resumo.", "commerce_new_confirmation_required", state, scope)
            validate = lambda facts: preflight(facts, preferences, pending["order_id"])
            current_order = await store.get_order(scope, UUID(pending["order_id"]))
            validate(current_order["quote"])
            if pending["action"] == "cancel":
                order = await store.cancel_order(scope, UUID(pending["order_id"]), revision=pending["revision"], summary_message_id=pending["summary_message_id"], validate_summary=validate)
                state.pop("pending", None)
                return {**finish(order_summary(order["quote"], preferences, kind="cancelled", order_id=str(order["id"])), "commerce_cancelled", state, scope), "_verified_operation": True}
            try:
                order = await store.confirm_order(scope, UUID(pending["order_id"]), revision=pending["revision"],
                    token=pending["token"], summary_message_id=pending["summary_message_id"], validate_summary=validate)
            except HTTPException as error:
                if error.status_code != 409:
                    raise
                state.pop("pending", None)
                return await prepare(scope, state["cart"], state, kind="order", preview=False, customer_name=customer_name,
                    preferences=preferences, prefix="Os dados precisam de uma nova revisão; o pedido anterior não foi confirmado.\n\n")
            state.pop("pending", None)
            return {**finish(order_summary(order["quote"], preferences, kind="confirmed", order_id=str(order["id"])), "commerce_confirmed", state, scope), "_verified_operation": True}
        if plan.intent in {"cancel", "status"}:
            if preview:
                return finish("A simulação não consulta nem modifica pedidos reais de clientes.", "commerce_preview", state, scope)
            if not state.get("last_order_id"):
                return finish("Não há pedido identificado nesta conversa. A equipe pode localizar seus pedidos pelo contato deste atendimento.", "commerce_no_order", state, scope)
            order = await store.get_order(scope, UUID(state["last_order_id"]))
            if plan.intent == "status":
                labels = {"draft": "aguardando confirmação", "confirmed": "confirmado", "cancelled": "cancelado"}
                details = receiving.render_receiving(order["quote"])
                return finish("Seu pedido está " + labels[order["status"]] + ". Pagamento não verificado." +
                    ("\n\n" + details if details else ""), "commerce_status", state, scope)
            if not allow_orders:
                return finish("O cancelamento precisa ser solicitado à equipe deste estabelecimento.", "commerce_review_required", state, scope)
            if order["status"] == "cancelled":
                return finish("Esse pedido já está cancelado.", "commerce_cancelled", state, scope)
            preflight(order["quote"], preferences, str(order["id"]))
            state["pending"] = {"action": "cancel", "order_id": str(order["id"]), "revision": order["revision"], "summary_message_id": scope.message_id}
            return finish(order_summary(order["quote"], preferences, kind="cancel_proposal", order_id=str(order["id"])), "commerce_cancel_confirm", state, scope)
        if plan.intent == "catalog":
            names = [p["name"] for p in catalog]
            return finish("Produtos cadastrados: " + "; ".join(names) + "." if names else "Ainda não há produtos ativos cadastrados neste catálogo.", "commerce_catalog", state, scope)
        items = plan.items
        if plan.intent == "adjust" and plan.selection_mode == "add":
            combined = {item["variant_id"]: dict(item) for item in state.get("cart", [])}
            for item in plan.items:
                if item["variant_id"] in combined:
                    combined[item["variant_id"]]["quantity"] += item["quantity"]
                else:
                    combined[item["variant_id"]] = dict(item)
            items = list(combined.values())
            if any(item["quantity"] > 1000 for item in items) or len(items) > 30:
                state.pop("pending", None)
                return finish("A quantidade excede o limite do orçamento. Informe uma quantidade de até 1000 por variante.", "commerce_quantity", state, scope)
        elif plan.intent == "adjust" and plan.selection_mode == "remove":
            if not plan.removed_variants:
                state.pop("pending", None)
                return finish("Qual produto você deseja retirar do resumo?", "commerce_choose_items", state, scope)
            items = [item for item in state.get("cart", []) if item["variant_id"] not in plan.removed_variants]
            if not items:
                state["cart"] = []
                state.pop("pending", None)
                return finish("O resumo ficou sem itens. Nenhum pedido foi confirmado.", "commerce_empty", state, scope)
        if not items and plan.intent in {"quote", "order", "adjust"}:
            items = state.get("cart", [])
        if plan.correction:
            # Any correction revokes a pending acceptance, even when ambiguous.
            state.pop("pending", None)
        if plan.intent == "faq":
            matches = [faq["answer"] for p in catalog for faq in p.get("faqs", [])
                       if norm(faq["question"]).strip(" ?!.") == norm(body).strip(" ?!.")]
            if len(set(matches)) == 1:
                return finish(matches[0], "commerce_information", state, scope)
            selected_ids = {item["variant_id"] for item in items}
            products = [p for p in catalog if any(str(v["id"]) in selected_ids for v in p["variants"])]
            if any(word in norm(body) for word in ("beneficio", "diferencial", "descricao", "para que serve", "como e")):
                facts = [escape_label(p["name"]) + ": " + " ".join([p.get("description") or "", *p.get("official_benefits", [])]).strip()
                         for p in products if p.get("description") or p.get("official_benefits")]
                answer="\n".join(facts) if facts else "Essa informação ainda não está cadastrada; precisa de confirmação da equipe."
                return finish(await with_suggestion(answer,scope,products,state,body,preferences,catalog=catalog), "commerce_information", state, scope)
        if items and plan.intent in {"price", "stock", "faq"}:
            facts = await store.estimate(scope, items)
            # Research never changes an existing order selection or creates quotes.
            selected_ids={item["variant_id"] for item in items}
            products=[p for p in catalog if any(str(v["id"]) in selected_ids for v in p["variants"])]
            answer=await with_suggestion(summary(facts,preferences),scope,products,state,body,preferences,catalog=catalog,information_only=plan.intent=="price")
            return finish(answer, "commerce_information", state, scope)
        if plan.intent == "faq":
            return finish("Posso informar os produtos e preços cadastrados. Essa condição específica precisa ser confirmada pela equipe.", "commerce_information", state, scope)
        if plan.intent in {"quote", "order", "adjust"}:
            if not items:
                return finish("Quais produtos, variantes e quantidades você deseja consultar?", "commerce_choose_items", state, scope)
            if not allow_orders:
                return finish(summary(await store.estimate(scope, items), preferences) + "\nPara registrar um orçamento ou pedido, fale com a equipe.", "commerce_information", state, scope)
            kind = state.get("requested_kind", "order") if plan.intent == "adjust" else plan.intent
            return await prepare(scope, items, state, kind=kind, preview=preview, customer_name=customer_name, preferences=preferences, body=body)
    except SummaryTooLong:
        state.pop("pending", None)
        return finish(TOO_LONG, "commerce_split_review", state, scope)
    except ValueError:
        state.pop("pending", None)
        return finish("Os dados deste resumo precisam de revisão da equipe antes de qualquer operação.", "commerce_review_required", state, scope)
    except HTTPException as error:
        state.pop("pending", None)
        if error.status_code == 404:
            return finish("Não encontrei esse pedido ou orçamento para o contato deste atendimento.", "commerce_not_found", state, scope)
        if error.status_code in {403, 409, 422, 423, 503}:
            return finish("Não confirmei nenhuma nova operação. Os itens ou a disponibilidade precisam de revisão; solicite um novo resumo ou fale com a equipe.", "commerce_review_required", state, scope)
        raise
    return None
