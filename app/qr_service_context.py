"""Pure, catalog-scoped answers to an actual pending service question.

No interpretation here authorizes an appointment, product order or write. The
caller retains the active task, catalog revision, deadline and ingress guards.
Prices/durations come from the caller's factual backend, never this resolver.
"""
from dataclasses import dataclass
from itertools import product
import re
from typing import Literal
from uuid import UUID

from app.qr_understanding import normalized


@dataclass(frozen=True)
class ServiceOption:
    id: UUID
    name: str


@dataclass(frozen=True)
class ServiceResolution:
    kind: Literal["resolved", "ambiguous", "unmatched", "inapplicable"]
    service_ids: tuple[UUID, ...] = ()
    options: tuple[ServiceOption, ...] = ()
    clarification: str | None = None
    corrected: bool = False


_CUT = re.compile(r"corte(?: de cabelo| cabelo| masculino| feminino| infantil)?")
_BEARD = re.compile(r"barba(?: completa| tradicional| desenhada)?")
_HAIR = re.compile(r"\b(?:cabel\w*|capilar\w*|coloracao|colorir|tintura|mechas|luzes|"
                   r"alisamento|progressiva|reconstrucao capilar|escova progressiva)\b|"
                   r"^(?:hidratacao|escova)(?: capilar| de cabelo| tradicional| completa)?$")
_UNSAFE = re.compile(r"\d|[?¿]|\b(?:nao|nem|sem|nunca|menos|exceto|ou|se|caso|desde que|talvez|"
    r"depende|quanto\w*|preco\w*|valor\w*|custa\w*|quando|quais?|quem|onde|como|porque|por que|"
    r"amanha|hoje|ontem|segunda|terca|quarta|quinta|sexta|sabado|domingo|"
    r"compr\w*|pedido\w*|carrinho|orcamento|quantidade|unidades?|paga\w*|estoque|produtos?|"
    r"agend\w*|reserv\w*|cancel\w*|desmarc\w*|remarc\w*|reagend\w*|confirm\w*|"
    r"troca\w*|muda\w*|retira\w*|remove\w*|tira\w*|melhor|pera|espera|inclu\w*|adicion\w*|"
    r"nome|chamo|profissional|barbeiro|humano|atendente|sim|sm)\b")
_PREFIX = re.compile(r"\s*(?:(?:quero|queria|prefiro|gostaria de)\s+)?(?:fazer\s+)?"
                     r"(?:(?:so|apenas|somente)\s+)?(?:(?:o|a|um|uma)\s+)?")
_GAP = re.compile(r"\s*(?:e|\+|&|,\s*e?)\s*(?:(?:o|a|um|uma)\s+)?")
_FILLER = {"quero", "queria", "prefiro", "gostaria", "de", "fazer", "so", "apenas", "somente", "o", "a", "um", "uma", "e"}


def _options(ids: set[UUID], rows: list[ServiceOption]) -> tuple[ServiceOption, ...]:
    return tuple(row for row in rows if row.id in ids)


def _ambiguous(ids: set[UUID], rows: list[ServiceOption], corrected=False) -> ServiceResolution:
    choices = _options(ids, rows)
    if not choices:
        return ServiceResolution("unmatched", corrected=corrected)
    names = [row.name for row in choices]
    if len(names) == 1:
        question = "Você se refere a " + names[0] + "?"
    else:
        question = "Você procura " + ", ".join(names[:-1]) + " ou " + names[-1] + "?"
    return ServiceResolution("ambiguous", options=choices, clarification=question, corrected=corrected)


def _catalog(catalog):
    if not isinstance(catalog, list) or len(catalog) > 60:
        return None
    rows, aliases, names = [], {}, {}
    try:
        for item in catalog:
            sid = UUID(str(item["id"]))
            name = item["name"]
            supplied = item.get("aliases", [])
            if (not isinstance(name, str) or not name.strip() or len(name) > 160 or sid in names or
                    not isinstance(supplied, (list, tuple)) or len(supplied) > 20):
                return None
            terms = [name, *supplied]
            if any(not isinstance(term, str) or not term.strip() or len(term) > 160 for term in terms):
                return None
            names[sid] = [normalized(term).strip() for term in terms]
            rows.append(ServiceOption(sid, name))
            for term in names[sid]:
                aliases.setdefault(term, set()).add(sid)
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    published = {term: set(ids) for term, ids in aliases.items()}
    cut = {sid for sid, terms in names.items() if any(_CUT.fullmatch(term) for term in terms)}
    beard = {sid for sid, terms in names.items() if any(_BEARD.fullmatch(term) for term in terms)}
    hair = {sid for sid, terms in names.items() if any(_HAIR.search(term) for term in terms)}
    # A bare "Corte" in another trade is insufficient evidence of a haircut.
    if hair or beard or any(term != "corte" and _CUT.fullmatch(term) for terms in names.values() for term in terms):
        hair |= cut
    hair |= aliases.get("cabelo", set()) | aliases.get("cabelos", set())
    for alias in ("cabelo", "cabelos"):
        aliases.setdefault(alias, set()).update(hair)
    aliases.setdefault("corte", set()).update(cut)
    for alias in ("corte de cabelo", "cortar cabelo", "cortar o cabelo"):
        aliases.setdefault(alias, set(cut))
    aliases.setdefault("barba", set()).update(beard)
    for alias in [term for term in aliases if re.match(r"barba\b", term)]:
        for prefix in ("corte de ", "corte da ", "cortar ", "cortar a "):
            aliases.setdefault(prefix + alias, set(aliases[alias]))
    return rows, aliases, cut, published


def _parse(text, rows, aliases, cut, published):
    spans = []
    for alias in sorted(aliases, key=len, reverse=True):
        for match in re.finditer(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", text):
            if any(start < match.end() and end > match.start() for start, end, *_ in spans):
                continue
            spans.append((match.start(), match.end(), alias, aliases[alias]))
    spans.sort(key=lambda span: span[0])
    if not spans or not _PREFIX.fullmatch(text[:spans[0][0]]) or not re.fullmatch(r"[.!\s]*", text[spans[-1][1]:]):
        return ServiceResolution("unmatched")
    if any(not _GAP.fullmatch(text[previous[1]:current[0]]) for previous, current in zip(spans, spans[1:])):
        return ServiceResolution("unmatched")
    unclear = set()
    chosen = []
    for _, _, alias, ids in spans:
        if not ids:
            return ServiceResolution("unmatched")
        if len(ids) != 1:
            unclear |= ids
        elif alias in {"cabelo", "cabelos"} and not ids <= cut and published.get(alias) != ids:
            # A single coloring/treatment is still not automatically a haircut.
            unclear |= ids
        else:
            sid = next(iter(ids))
            if sid in chosen:
                return ServiceResolution("inapplicable")  # Repetition may mean quantity.
            chosen.append(sid)
    if unclear:
        return _ambiguous(unclear, rows)
    if len(chosen) > 6:
        return ServiceResolution("inapplicable")
    return ServiceResolution("resolved", tuple(chosen), _options(set(chosen), rows))


def _one_edit(a: str, b: str) -> bool:
    """One insertion, deletion, substitution or adjacent transposition."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        different = [i for i, (left, right) in enumerate(zip(a, b)) if left != right]
        return len(different) == 1 or (len(different) == 2 and different[1] == different[0]+1 and
            a[different[0]] == b[different[1]] and a[different[1]] == b[different[0]])
    small, large = (a, b) if len(a) < len(b) else (b, a)
    return any(large[:i] + large[i+1:] == small for i in range(len(large)))


def resolve_service_reply(message: str, catalog: list[dict], *, awaiting_service: bool) -> ServiceResolution:
    """Resolve only an integral positive answer to a current service question.

    The caller must prove that question/revision remains current. Unknown words
    are never dropped. Typo correction is bounded to two alphabetic words with
    one edit and the same two initial letters, with all candidate parses tested.
    Equal-distance alternatives never select the first catalog entry.
    """
    if awaiting_service is not True or not isinstance(message, str) or not message.strip() or len(message) > 180:
        return ServiceResolution("inapplicable")
    text = re.sub(r"\s+", " ", normalized(message)).strip()
    if _UNSAFE.search(text):
        return ServiceResolution("inapplicable")
    prepared = _catalog(catalog)
    if prepared is None:
        return ServiceResolution("inapplicable")
    rows, aliases, cut, published = prepared
    exact = _parse(text, rows, aliases, cut, published)
    if exact.kind != "unmatched":
        return exact
    vocabulary = {word for alias, ids in aliases.items() if ids for word in re.findall(r"[a-z]+", alias)}
    unknown = [match for match in re.finditer(r"[a-z]+", text) if match.group() not in vocabulary | _FILLER]
    if not 1 <= len(unknown) <= 2 or any(len(match.group()) < 5 for match in unknown):
        return exact
    replacements = [[word for word in vocabulary if len(word) >= 5 and word[:2] == match.group()[:2] and
        _one_edit(match.group(), word)] for match in unknown]
    if any(not choices for choices in replacements) or len(replacements[0]) * (len(replacements[1]) if len(replacements) == 2 else 1) > 8:
        return exact
    results = []
    for combination in product(*replacements):
        corrected = text
        for match, replacement in reversed(list(zip(unknown, combination))):
            corrected = corrected[:match.start()] + replacement + corrected[match.end():]
        result = _parse(corrected, rows, aliases, cut, published)
        if result.kind in {"resolved", "ambiguous"}:
            results.append(result)
    identities = {result.service_ids for result in results if result.kind == "resolved"}
    if len(identities) == 1 and all(result.kind == "resolved" for result in results):
        ids = next(iter(identities))
        return ServiceResolution("resolved", ids, _options(set(ids), rows), corrected=True)
    if results:
        return _ambiguous({option.id for result in results for option in result.options}, rows, corrected=True)
    return exact
