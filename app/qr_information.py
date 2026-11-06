"""Compose read-only replies from this turn's authenticated, pinned facts.

The caller supplies business.profile from the published snapshot, or an explicit
isolated preview version. Draft/state objects are never searched as a fallback.
FAQ IDs are version-local: faq_catalog and info must share the SAME snapshot;
the returned profile_version lets the caller check that binding. This module has
no database, model, networking, scheduling, stock, payment or send operations.
"""
from dataclasses import dataclass
from datetime import time
import math
import re
from typing import Literal
from uuid import UUID

from app.business_profile_schemas import StyleRules
from app.qr_presentation import InteractionContext, QuoteItem, render_quote
from app.qr_understanding import UnderstandingPlan, normalized


@dataclass(frozen=True)
class InformationPart:
    kind: Literal["price", "catalog", "faq", "identity", "address", "hours", "contact", "unknown"]
    text: str
    source_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class InformationReply:
    answer: str | None
    parts: tuple[InformationPart, ...]
    unresolved: tuple[str, ...]
    profile_version: str | None


PRICE_REFERENCE_SECONDS = 300


def price_followup_question(message: str) -> bool:
    """Only a complete price explanation question, never a mixed operation."""
    if not isinstance(message, str) or len(message) > 1500:
        return False
    text = re.sub(r"\s+", " ", normalized(message)).strip(" ?!.,\"'“”")
    text = re.sub(r"^(?:mas|e)\s+", "", text)
    return bool(re.fullmatch(
        r"(?:(?:por que|porque|pq)(?: (?:esse|este|o) (?:preco|valor)(?: e)?)? (?:e )?a partir de|"
        r"(?:o que (?:significa|quer dizer)|como assim) (?:esse )?a partir de|"
        r"(?:(?:esse|este|o) )?(?:preco|valor) e (?:fechado|fixo|final))", text))


def current_price_reference(reference, catalog: list[dict], *, revision: str, scope: str, now: float) -> dict | None:
    """A short-lived read-only subject. It supplies no booking selection."""
    if (not isinstance(reference, dict) or reference.get("catalog_revision") != revision
            or reference.get("scope") != scope):
        return None
    quoted_at, ids = reference.get("quoted_at"), reference.get("service_ids")
    if (type(quoted_at) not in {int, float} or not math.isfinite(quoted_at)
            or not 0 <= now-quoted_at <= PRICE_REFERENCE_SECONDS
            or not isinstance(ids, list) or not 1 <= len(ids) <= 6
            or len(catalog) > 500):
        return None
    try:
        if any(not isinstance(value, str) or str(UUID(value)) != value for value in ids):
            return None
        by_id = {str(row["id"]): row for row in catalog}
        if (len(by_id) != len(catalog) or len(set(ids)) != len(ids)
                or any(value not in by_id or by_id[value].get("active") is False for value in ids)):
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return {"service_ids": list(ids), "catalog_revision": revision, "scope": scope, "quoted_at": quoted_at}


def explain_price_followup(message: str, business: dict, catalog: list[dict], reference, *,
                           revision: str, scope: str, now: float) -> tuple[str, dict | None] | None:
    """Explain the price kind from current catalog facts, without inventing why it varies."""
    if not price_followup_question(message):
        return None
    clarification = ("Qual serviço você quer consultar? Preciso identificar o serviço no catálogo atual "
                     "para explicar se o preço é fixo ou inicial.", None)
    current = current_price_reference(reference, catalog, revision=revision, scope=scope, now=now)
    if current is None or len(current["service_ids"]) != 1:
        return clarification
    row = next(row for row in catalog if str(row["id"]) == current["service_ids"][0])
    kind = row.get("price_mode", "fixed")
    if kind not in {"fixed", "from"}:
        return clarification
    try:
        quote = render_quote([QuoteItem(row["name"], row.get("price_cents"), price_kind=kind)],
            _profile(business).get("style_rules"), context=InteractionContext(information_only=True), show_total=False)
    except (KeyError, TypeError, ValueError):
        return clarification
    explanation = ("Esse é o valor inicial cadastrado, não um preço fechado. A equipe precisa confirmar o preço final "
        "para esse serviço. Para saber o que pode alterar esse valor, confirme com a equipe."
        if kind == "from" else "Esse é o preço fixo cadastrado para esse serviço.")
    return quote + "\n\n" + explanation, current


def _profile(business: dict) -> dict:
    value = business.get("profile")
    # No business['draft'], draft_template, or model-provided description is used.
    return value if isinstance(value, dict) else {}


def faq_catalog(business: dict) -> list[dict]:
    """IDs index the current immutable profile version, not the conversation.

    Pass only id/question to the model. info retrieves answers independently.
    The producer must bind the plan to this same business/profile revision.
    """
    faqs = _profile(business).get("faqs", [])
    return [{"id": f"faq-{index}", "question": item["question"]}
            for index, item in enumerate(faqs[:30]) if isinstance(item, dict) and
            isinstance(item.get("question"), str) and item["question"].strip() and
            isinstance(item.get("answer"), str) and item["answer"].strip()]


def _operating_hours(profile: dict, business: dict) -> str | None:
    weekdays = ("Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo")
    rows = profile.get("hours") or []
    if not rows:
        return None
    values = []
    try:
        for row in rows:
            day = row["weekday"]
            if type(day) is not int or not 0 <= day <= 6:
                return None
            opens, closes = time.fromisoformat(row["opens"]), time.fromisoformat(row["closes"])
            if opens.tzinfo or closes.tzinfo or opens >= closes:
                return None
            display = lambda clock: clock.strftime("%H:%M:%S" if clock.second else "%H:%M")
            values.append((day, opens, f"{weekdays[day]}: {display(opens)} às {display(closes)}."))
    except (KeyError, TypeError, ValueError):
        return None
    zone = profile.get("timezone") or business.get("timezone")
    heading = "Horários de funcionamento" + (f" ({zone})" if zone else "") + ":"
    return heading + "\n" + "\n".join(row[2] for row in sorted(values)) + \
        "\nEsses são os horários informados pela empresa. As vagas dependem da agenda dos profissionais."


def info(plan: UnderstandingPlan, business: dict, catalog: list[dict], *, message: str) -> InformationReply:
    """Answer identified factual subjects; never interpret selection as a sale.

    Permissions/capabilities are checked by the backend before calling this
    function. Prices use query_service_ids only, never an old booking selection.
    message is required for built-in identity/address/hours/contact questions;
    it is not allowed to supply any factual response.
    """
    if not isinstance(plan, UnderstandingPlan) or not isinstance(message, str) or len(message) > 1500:
        raise ValueError("A validated plan and the complete current message are required")
    if len(catalog) > 500:
        raise ValueError("Catalog outside supported bounds")
    profile = _profile(business)
    style = StyleRules.model_validate(profile.get("style_rules") or {}).model_dump()
    parts, unresolved = [], []
    version = str(business["profile_id"]) if business.get("profile_id") else None
    lower = normalized(message)
    product_query = "catalog" in plan.intents and bool(re.search(r"\b(?:produtos?|pomada|shampoo|estoque|vendem|comprar)\b", lower))

    def append(kind, text, ids=()):
        if text and not any(part.text == text for part in parts):
            parts.append(InformationPart(kind, text, tuple(ids)))

    def missing(subject, text):
        unresolved.append(subject);append("unknown", text)

    if ("price" in plan.intents or ("catalog" in plan.intents and re.search(r"\b(?:servicos?|catalogo|valores?|precos?)\b", lower))) and (
            not product_query or plan.query_service_ids):
        by_id = {str(x["id"]): x for x in catalog}
        ids = [str(x) for x in plan.query_service_ids]
        invalid = len(by_id) != len(catalog) or any(value not in by_id for value in ids)
        if "service" in plan.ambiguities or invalid:
            missing("service", "Para informar o valor certo, preciso confirmar quais serviços você quer consultar. Qual serviço ou opção do catálogo você prefere?")
        else:
            chosen = [by_id[value] for value in ids] if ids else catalog[:12]
            if chosen:
                items = [QuoteItem(x["name"], x.get("price_cents"),
                    duration_min=x.get("duration_min"), price_kind=x.get("price_mode",x.get("price_kind", "fixed"))) for x in chosen]
                text = render_quote(items, style, context=InteractionContext(information_only=True), show_total=bool(ids))
                if not ids:
                    text = "Estes são os valores cadastrados:\n" + text
                    if len(catalog) > 12:
                        text += f"\nMostrei 12 dos {len(catalog)} itens do catálogo."
                append("price" if ids else "catalog", text, ids)
            else:
                missing("catalog", "Não há itens cadastrados neste atendimento para informar esses valores com segurança.")

    known_faqs = {row["id"]: row for row in faq_catalog(business)}
    raw_faqs = profile.get("faqs", [])
    requested_faqs = list(dict.fromkeys(plan.faq_ids))
    literal_faqs = set()
    # Exact published wording may occur as one part of a multi-topic message.
    # No fuzzy match or one-word payment/policy guess can create a fact.
    for key, row in known_faqs.items():
        question = normalized(row["question"]).strip(" ?!.")
        if question and re.search(r"(?<!\w)" + re.escape(question) + r"(?!\w)", lower):
            literal_faqs.add(key)
            if key not in requested_faqs:
                requested_faqs.append(key)
    for key in requested_faqs:
        if key not in known_faqs:
            missing("faq", "Não encontrei essa informação no perfil atual da empresa. Preciso confirmar com a equipe, sem supor uma condição.")
            continue
        index = int(key.removeprefix("faq-"))
        append("faq", raw_faqs[index]["answer"].strip(), [key])

    topics = set()
    if re.search(r"\b(?:qual (?:e )?(?:o )?seu nome|como voce se chama|quem e voce|nome d[ao] (?:empresa|loja)|qual (?:e )?a empresa)\b", lower):
        topics.add("identity")
        name = profile.get("business_name") or business.get("name")
        assistant = profile.get("assistant_name") or "Orion"
        text = f"Sou {assistant}, assistente virtual" + (f" da {name}." if name else " deste atendimento.")
        if profile.get("unit_name"):
            text += f" Unidade: {profile['unit_name']}."
        append("identity", text)
    if re.search(r"\b(?:endereco|onde fica|localizacao)\b", lower):
        topics.add("address")
        if profile.get("address"):
            append("address", "Endereço: " + profile["address"])
        elif not any(key in literal_faqs and re.search(r"\b(?:endereco|onde fica|localizacao)\b",
                normalized(row["question"])) for key, row in known_faqs.items()):
            missing("address", "O endereço ainda não está cadastrado neste atendimento. Preciso confirmar com a equipe.")
    if re.search(r"\b(?:horarios? de funcionamento|horarios? d[ao] (?:empresa|loja)|que horas (?:abre|fecha)|(?:voces )?(?:abrem|fecham))\b", lower):
        topics.add("hours")
        text = _operating_hours(profile, business)
        if text:
            append("hours", text)
        else:
            missing("hours", "Os horários de funcionamento ainda não estão cadastrados com segurança. As vagas precisam ser consultadas na agenda dos profissionais.")
    if re.search(r"\b(?:telefone de contato|numero de contato|como entro em contato|telefone d[ao] (?:empresa|loja))\b", lower):
        topics.add("contact")
        if profile.get("contact_phone"):
            append("contact", "Telefone de contato: " + profile["contact_phone"])
        else:
            missing("contact", "O telefone de contato ainda não está cadastrado neste atendimento.")
    if "faq" in plan.intents:
        answered_questions = [normalized(known_faqs[key]["question"]) for key in requested_faqs if key in known_faqs]
        policies = [(index, row) for index, row in enumerate(profile.get("policies", [])) if
                    row.get("topic") and not any(normalized(row["topic"]) in question for question in answered_questions) and
                    re.search(r"(?<!\w)" + re.escape(normalized(row["topic"])) + r"(?!\w)", lower)]
        if len(policies) == 1 and policies[0][1].get("description"):
            index, row = policies[0]
            append("faq", row["topic"] + ": " + row["description"], [f"policy-{index}"])
            topics.add("policy")
        elif len(policies) > 1:
            topics.add("policy")
            missing("policy", "Encontrei mais de uma política relacionada. Qual condição você quer consultar? Preciso distinguir antes de responder.")
    if "faq" in plan.intents and ((not requested_faqs and not topics) or "faq" in plan.ambiguities):
        missing("faq", "Essa informação ainda não está cadastrada no atendimento. Preciso confirmar com a equipe; não vou supor formas de pagamento ou condições.")
    if product_query:
        missing("products", "Ainda não tenho um catálogo de produtos validado neste atendimento. Preciso confirmar com a equipe, sem garantir produto ou estoque.")
    separator = "\n" if style["length"] == "short" else "\n\n"
    answer = separator.join(part.text for part in parts) or None
    return InformationReply(answer, tuple(parts), tuple(dict.fromkeys(unresolved)), version)
