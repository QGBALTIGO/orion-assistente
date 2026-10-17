"""Independent, read-only commercial facts for a compound conversation turn.

Authority is the authenticated CustomerScope and the caller's pinned server
profile. Model text can choose a read topic, never supply facts, IDs or writes.
No conversation state, cart, estimate, quote or order is read or created here.
"""
import os
import re
from collections.abc import Mapping

from app import qr_commerce_store as store
from app.qr_commerce_understanding import interpret, norm
from app.qr_presentation import InteractionContext, QuoteItem, escape_label, fits_message, render_quote

KINDS = frozenset({"catalog", "faq", "price", "stock"})
NARROW_QUERY = "Há várias informações nesse catálogo. Qual produto ou variante você quer consultar primeiro?"
UNKNOWN_PRODUCT = "Não encontrei esse produto cadastrado neste atendimento; a equipe pode confirmar."


def existence_query(query):
    """Presence in the catalog is different from stock or an order for one unit."""
    value = norm(query)
    return bool(re.search(r"\b(?:vendem|vende|comercializam|trabalham com|tem no catalogo|ha no catalogo)\b", value)) and not re.search(
        r"\b(?:quanto|valor|preco|custa|estoque|disponivel|disponibilidade|comprar|pedido|orcamento)\b", value)


def _bounded(value):
    return value if fits_message(value) else None


def _selection(query, catalog):
    # Only fresh message mentions; deliberately no old cart/variant choices.
    plan = interpret(query, catalog)
    selected = {str(item["variant_id"]) for item in plan.items}
    products = [p for p in catalog if p["name"] in plan.ambiguous_products or
                any(str(v["id"]) in selected for v in p["variants"])]
    variants = [(p, v) for p in products for v in p["variants"]
                if not selected or str(v["id"]) in selected or p["name"] in plan.ambiguous_products]
    return products, variants


def catalog_answer(query, catalog):
    """Pure catalog presence; no unit, total, stock promise or sales suggestion."""
    # The compound-turn caller already established that this question is
    # independent of consent. A catalog miss is a known lack of registration,
    # not evidence that the business does not sell it or has zero stock.
    existence = existence_query(query)
    if existence and (re.search(r"\bou\b", norm(query)) or re.search(
            r"\b(?:vendem|vende|comercializam|trabalham com)\s*(?:isso|aquilo|esse|este|aquele|essa|esta|aquela)?\s*[?!.]*$", norm(query))):
        return None
    if not catalog:
        return UNKNOWN_PRODUCT if existence else "Ainda não há produtos ativos cadastrados neste catálogo."
    products, _ = _selection(query, catalog)
    generic = bool(re.fullmatch(r"(?:(?:mostre|mostrar|ver|quero ver|me mande|mande|pode mostrar) (?:o |a )?)?"
        r"(?:catalogo|catalogo de produtos|lista de produtos|produtos|quais produtos(?: voces (?:vendem|tem))?)",
        norm(query).strip(" ?!.,")))
    if not products and not generic:
        return UNKNOWN_PRODUCT if existence else None
    chosen = products or catalog
    if len(chosen) > 12:
        return None
    lines = ["• " + escape_label(p["name"]) + ": " + "; ".join(escape_label(v["name"]) for v in p["variants"])
             for p in chosen]
    return _bounded("O catálogo inclui:\n" + "\n".join(lines) +
                    "\nA presença no catálogo não confirma disponibilidade em estoque.")


async def readonly_information(scope, query, *, kind, profile, preferences=None):
    """Return complete facts or honest catalog absence; ambiguity returns None.

    The caller must budget the text together with the operation reply. A short
    clarification is never substituted for ambiguous or overlong factual content.
    """
    if not isinstance(scope, store.CustomerScope) or not isinstance(query, str) or not query.strip() or len(query) > 1500:
        raise ValueError("Authenticated scope and complete bounded query required")
    if kind not in KINDS:
        raise ValueError("Unsupported read-only topic")
    if os.getenv("COMMERCIAL_CATALOG_ENABLED") != "1" or os.getenv("QR_AI_COMMERCE_ENABLED") != "1":
        return None
    if (not isinstance(profile, Mapping) or not isinstance(profile.get("capabilities"), (list, tuple))
            or "catalog_answers" not in profile["capabilities"]):
        return None
    catalog = await store.active_catalog(scope)
    if kind == "catalog" or existence_query(query):
        return catalog_answer(query, catalog)
    products, variants = _selection(query, catalog)
    if kind == "faq":
        message = norm(query).strip(" ?!.,")
        matches = [row["answer"] for row in [*profile.get("faqs", []),
            *(row for p in catalog for row in p.get("faqs", []))]
            if isinstance(row, Mapping) and isinstance(row.get("question"), str)
            and isinstance(row.get("answer"), str)
            and norm(row["question"]).strip(" ?!.,") == message]
        if len(set(matches)) == 1:
            return _bounded(matches[0])
        if re.search(r"\b(?:beneficios?|diferenciais?|descricao|para que serve|como e)\b", message):
            facts = [escape_label(p["name"]) + ": " + " ".join([p.get("description") or "", *p.get("official_benefits", [])]).strip()
                     for p in products if p.get("description") or p.get("official_benefits")]
            if facts:
                return _bounded("\n".join(facts))
        return None
    if not variants:
        return None
    if len(variants) > 12:
        return None
    if kind == "price":
        try:
            items = [QuoteItem(p["name"] + " · " + v["name"],
                    v["unit_price_cents"] if v["price_kind"] in {"fixed", "from"} else None,
                    price_kind=v["price_kind"] if v["price_kind"] in {"fixed", "from"} else "quote")
                     for p, v in variants]
            text = render_quote(items, preferences or profile.get("style_rules"),
                context=InteractionContext(information_only=True), show_total=False)
        except ValueError:
            return None
        return _bounded("Valores unitários cadastrados:\n" + text)
    stocks = await store.stock_information(scope, [v["id"] for _, v in variants])
    lines = []
    for product, variant in variants:
        stock = stocks.get(str(variant["id"]), {})
        quantity = stock.get("available_quantity")
        if quantity is None:
            detail = "disponibilidade não confirmada; o estoque está desconhecido ou a contagem venceu"
        elif quantity == 0:
            detail = "a última contagem válida não tem unidades livres"
        else:
            detail = f"{quantity} unidade(s) livre(s) na última contagem válida"
        lines.append(escape_label(product["name"]) + " · " + escape_label(variant["name"]) + ": " + detail + ".")
    return _bounded("\n".join(lines) + "\nEssa consulta não reserva unidades; a confirmação de um pedido verifica o estoque novamente.")
