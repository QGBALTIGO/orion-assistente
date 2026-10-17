"""Conservative product interpretation. Text never supplies prices or authority."""
from dataclasses import dataclass, field
import re
import unicodedata
from uuid import UUID
from app.qr_dialogue import contextual_response


def norm(value):
    return "".join(c for c in unicodedata.normalize("NFD", value.casefold()) if unicodedata.category(c) != "Mn")


def explicit_confirmation(value):
    # Routing only: the handler must independently supply the real pending
    # action, freshness and ownership before authorizing any operation.
    return any(contextual_response(value, awaiting_confirmation=True, confirmation_action=action).kind == "affirm"
               for action in ("order", "cancel"))


@dataclass(frozen=True)
class CommercePlan:
    intent: str
    items: list[dict] = field(default_factory=list)
    ambiguous_products: list[str] = field(default_factory=list)
    relevant: bool = False
    correction: bool = False
    invalid_quantity: bool = False
    ambiguous_quantities: dict = field(default_factory=dict)
    selection_mode: str = "replace"
    removed_variants: list[str] = field(default_factory=list)


NUMBERS = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4,
           "cinco": 5, "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10,
           "onze":11,"doze":12,"treze":13,"catorze":14,"quatorze":14,"quinze":15,
           "dezesseis":16,"dezessete":17,"dezoito":18,"dezenove":19,"vinte":20,
           "trinta":30,"quarenta":40,"cinquenta":50,"sessenta":60,"setenta":70,
           "oitenta":80,"noventa":90,"cem":100,"mil":1000}
TENS = {key:value for key,value in NUMBERS.items() if 20 <= value <= 90}
UNITS = {key:value for key,value in NUMBERS.items() if value < 10}
QUANTITY_PHRASES = {**NUMBERS,"meia duzia":6,"uma duzia":12,"um duzia":12,"duzia":12}
QUANTITY_PHRASES.update({left+" e "+right:a+b for left,a in TENS.items() for right,b in UNITS.items()})
QUANTITY_PHRASES.update({word+" duzias":number*12 for word,number in NUMBERS.items() if number <= 10})
_QUANTITY_WORDS = "|".join(re.escape(value) for value in sorted(QUANTITY_PHRASES,key=len,reverse=True))
NEGATION = re.compile(r"\b(?:nao quero|nao vou|dispenso|sem|tira|retira|remove|remova|em vez de|troca|troque)\s+(?:(?:o|a|os|as|de|do|da)\s+)*$")
CORRECTION = re.compile(r"\b(?:melhor|na verdade|corrig\w*|em vez|troca|troque|substitu\w*|tira|retira|remove|remova)\b")


def matches(text, term):
    return list(re.finditer(r"(?<!\w)" + re.escape(norm(str(term)).strip()) + r"(?!\w)", text)) if str(term).strip() else []


def product_matches(text, term):
    term = norm(str(term)).strip()
    return matches(text, term) + (matches(text, term + "s") if term and term[-1] in "aeiou" else [])


def quantity(text, start, end):
    before, after = text[max(0, start - 90):start], text[end:end + 50]
    if re.search(r"(?:-\s*\d+|\d+[.,/]\d+)\s*(?:unidades?\s*)?(?:de\s*)?$", before):
        return -1
    word = r"(?:\d+(?:[.,]\d+)?|" + _QUANTITY_WORDS + ")"
    preceding = re.search(r"(?<![\w/-])(" + word + r")\s*(?:x\s*|unidades?\s*)?(?:de\s*)?$", before)
    following = re.match(r"\s*(?:[:=-]\s*|x\s*)(" + word + r")(?!\w)|\s+(" + word + r")\s+unidades?\b", after)
    found = preceding.group(1) if preceding else (next((g for g in following.groups() if g), None) if following else None)
    if preceding and re.search(r"\b(?:"+"|".join(NUMBERS)+r"|\d+|cento|duzent[oa]s|trezent[oa]s|quatrocent[oa]s|quinhent[oa]s|seiscent[oa]s|setecent[oa]s|oitocent[oa]s|novecent[oa]s)\s+(?:(?:e|ou)\s+)?$",before[:preceding.start()]):
        return -1  # Unsupported combinations/alternatives cannot become the final word alone.
    if found is None:
        if re.search(r"\b(?:algum[as]*|vari[ao]s?|muit[ao]s?|pouc[ao]s?|um[as]+|meia|metade|duzias?|centenas?|milhares?|cento|duzent[oa]s|trezent[oa]s|quatrocent[oa]s|quinhent[oa]s|seiscent[oa]s|setecent[oa]s|oitocent[oa]s|novecent[oa]s)\s*(?:de\s*)?$",before):
            return -1
        return None
    if found in QUANTITY_PHRASES:
        return QUANTITY_PHRASES[found]
    return int(found) if found.isdigit() and 1 <= int(found) <= 1000 else -1


def interpret(body, catalog, previous=None, choices=None):
    text = norm(body).strip()
    previous = previous or []
    choices = choices or {}
    correction = bool(CORRECTION.search(text))
    selected, ambiguous, invalid, ambiguous_quantities = {}, [], False, {}
    mentioned_products = []
    product_spans = [(str(p["id"]), m.start(), m.end()) for p in catalog
                     for alias in [p["name"], *p.get("synonyms", [])] for m in product_matches(text, alias)]
    for product in catalog:
        aliases = [product["name"], *product.get("synonyms", [])]
        spans = [(m.start(), m.end()) for alias in aliases for m in product_matches(text, alias)]
        spans = [(start, end) for start, end in spans if not any(
            owner != str(product["id"]) and a <= start and b >= end and b - a > end - start
            for owner, a, b in product_spans)]
        # A specific variant/SKU is usable only when unique across active products.
        if not spans:
            for variant in product["variants"]:
                alias = variant["name"]
                owners = sum(any(norm(v["name"]) == norm(alias) for v in p["variants"]) for p in catalog)
                if len(norm(alias).strip()) >= 3 and owners == 1:
                    spans.extend((m.start(), m.end()) for m in matches(text, alias))
                if str(product["id"]) in choices:
                    spans.extend((m.start(), m.end()) for value in variant.get("attributes", {}).values() for m in matches(text, value))
        if not spans:
            continue
        mentioned_products.append(product)
        # Corrections such as 'troca a pomada pelo shampoo' remove the first item.
        positive = [(start, end) for start, end in spans if not NEGATION.search(text[max(0, start - 60):start])]
        if not positive:
            continue
        start, end = max(positive, key=lambda span: (span[0], span[1] - span[0])) if correction else max(positive, key=lambda span: span[1] - span[0])
        quantities = {quantity(text, a, b) for a, b in positive} - {None}
        if len(quantities) > 1 and not correction:
            invalid = True
            continue
        candidates = product["variants"]
        attribute_mentions = {norm(value) for v in candidates for value in v.get("attributes", {}).values() if matches(text, value)}
        specific = [v for v in candidates if (matches(text, v["name"]) or
                    (attribute_mentions and attribute_mentions <= {norm(value) for value in v.get("attributes", {}).values()}))]
        if specific:
            candidates = specific
        qty = quantity(text, start, end)
        if qty == -1:
            invalid = True
            continue
        if len(candidates) != 1:
            ambiguous.append(product["name"])
            ambiguous_quantities[str(product["id"])] = qty or choices.get(str(product["id"]), 1)
            continue
        variant = candidates[0]
        if qty is None:
            prior = next((x["quantity"] for x in previous if str(x["variant_id"]) == str(variant["id"])), None)
            qty = prior or choices.get(str(product["id"]), 1)
        selected[str(variant["id"])] = {"variant_id": str(variant["id"]), "quantity": qty}
    commerce_words = bool(re.search(r"\b(?:produto[s]?|catalogo|estoque|orcamento|pedido|comprar|compra)\b", text))
    relevant = bool(mentioned_products or commerce_words)
    query = bool(re.search(r"\b(?:quanto|valor|preco|custa|saber|pesquis\w*|compar\w*|so olhando|so vendo)\b", text))
    refusal = bool(re.search(r"\b(?:nao quero comprar|nao vou comprar|nao quero pedido|nao quero fechar|so pesquisando|so estou pesquisando)\b", text))
    if text.strip(" .!?") in {"nao", "nao quero", "deixa pra la"}:
        intent = "decline"
    elif refusal:
        intent = "price" if query and selected else "decline"
    elif explicit_confirmation(body):
        intent = "confirm"
    elif re.search(r"\b(?:como|posso|qual|quais)\b.*\b(?:cancel|pedido|orcamento)\w*", text):
        intent = "faq"
    elif re.search(r"\b(?:cancelar|cancela|cancele|desistir do|desisto do)\s+(?:o\s+|meu\s+)?pedido\b", text):
        intent = "cancel"
    elif re.search(r"\b(?:meu pedido|status do pedido|acompanhar pedido|ver pedido)\b", text) and not selected:
        intent = "status"
    elif query:
        intent = "price"
    elif re.search(r"\b(?:tem|estoque|disponivel|disponibilidade)\b", text):
        intent = "stock"
    elif re.search(r"\b(?:quero|preciso|gostaria|faz|faca|monta|monte|mande|envi[ae]|pode mandar)\b.*\borcamento\b", text) or text.strip("?!.") == "orcamento":
        intent = "quote"
    elif re.search(r"\b(?:quero comprar|quero pedir|vou levar|quero levar|fechar (?:o )?pedido|fazer (?:o |um )?pedido|pode (?:fazer|registrar|montar) (?:o |meu |um )?pedido)\b", text):
        intent = "order"
    elif re.search(r"\bquero\s+(?:\d+|um|uma|dois|duas|tres|quatro)\b", text) and selected:
        intent = "order"
    elif re.search(r"\b(?:catalogo|quais produtos|lista de produtos|ver produtos)\b", text):
        intent = "catalog"
    elif correction and previous:
        intent = "adjust"
    else:
        intent = "faq" if relevant else "other"
    selection_mode, removed_variants = "replace", []
    if previous and not query and re.search(r"\b(?:adiciona|adicione|inclua|incluir|acrescente|acrescenta|coloca|coloque)\b", text):
        intent, selection_mode, correction = "adjust", "add", True
    elif previous and not query and re.search(r"\b(?:tira|retira|retire|remove|remova)\b", text):
        intent, selection_mode, correction = "adjust", "remove", True
        removed_variants = [str(v["id"]) for p in mentioned_products for v in p["variants"]]
    # 'Melhor duas' can correct quantity only for one unambiguous existing item.
    if not selected and correction and len(previous) == 1 and not mentioned_products:
        match = re.search(r"\b(?:melhor|na verdade|quero)\s+(\d+|" + "|".join(NUMBERS) + r")(?!\w)", text)
        if match:
            value = match.group(1)
            qty = NUMBERS.get(value, int(value) if value.isdigit() else 0)
            invalid = not 1 <= qty <= 1000
            if not invalid:
                selected[str(previous[0]["variant_id"])] = {"variant_id": str(previous[0]["variant_id"]), "quantity": qty}
                relevant = True
                intent = "adjust"
    return CommercePlan(intent, list(selected.values()), list(dict.fromkeys(ambiguous)), relevant, correction, invalid,
                        ambiguous_quantities, selection_mode, removed_variants)
