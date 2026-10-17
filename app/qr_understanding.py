"""Pure conversation planning; no database, Redis, booking, sending or tool calls.

service_ids is a proposed final selection, query_service_ids is a read-only
subject. A plan is never authorization: the caller must validate the current
catalog revision, ownership, capacity and a fresh explicit confirmation summary.
Relative dates require the tenant-local reference_date supplied by the caller.
"""
import re
import unicodedata
from datetime import date, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

Intent = Literal["price", "availability", "booking", "reschedule", "cancel", "confirm",
                 "catalog", "faq", "human", "other"]
Ambiguity = Literal["service", "date", "time", "professional", "customer_name", "faq",
                    "intent", "unsupported_operation"]
INTENTS = ["price", "availability", "booking", "reschedule", "cancel", "confirm",
           "catalog", "faq", "human", "other"]
AMBIGUITIES = ["service", "date", "time", "professional", "customer_name", "faq",
               "intent", "unsupported_operation"]


class UnderstandingPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    intents: list[Intent] = Field(default_factory=lambda: ["other"], min_length=1, max_length=10)
    service_ids: list[UUID] = Field(default_factory=list, max_length=6)
    query_service_ids: list[UUID] = Field(default_factory=list, max_length=6)
    selection_mode: Literal["none", "replace", "add", "remove", "simulate"] = "none"
    requested_date: str | None = Field(default=None, max_length=10)
    requested_time: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    time_relation: Literal["none", "exact", "after", "before", "around"] = "none"
    professional_id: UUID | None = None
    customer_name: str | None = Field(default=None, min_length=2, max_length=60)
    faq_ids: list[str] = Field(default_factory=list, max_length=10)
    declined_service_ids: list[UUID] = Field(default_factory=list, max_length=6)
    declined_offers: list[Literal["products", "extras", "booking", "all"]] = Field(default_factory=list, max_length=4)
    sales_optional: bool = True
    ambiguities: list[Ambiguity] = Field(default_factory=list, max_length=8)

    @field_validator("requested_date")
    @classmethod
    def valid_date(cls, value):
        if value is not None and date.fromisoformat(value).isoformat() != value:
            raise ValueError("Use a calendar date in ISO format")
        return value

    @property
    def requires_clarification(self) -> bool:
        return bool(self.ambiguities)

    @property
    def requests_critical_action(self) -> bool:
        return bool(set(self.intents) & {"booking", "reschedule", "cancel", "confirm"})


def normalized(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", value.casefold())
                   if unicodedata.category(c) != "Mn")


def _unique(items):
    return list(dict.fromkeys(items))


def _negative(prefix: str) -> bool:
    # 'não sei o valor' is not a denial of the service that follows it.
    return bool(re.search(r"\b(?:sem|menos|nem|exceto|tira|tirar|retira|retirar|remove|remover|dispenso|troca|trocar|substitui|substituir)"
                          r"\s+(?:(?:a|o|de|da|do|fazer)\s+)*$|"
                          r"\bem vez\s+(?:(?:de|da|do|a|o)\s+)*$|"
                          r"\bnao\s+(?:(?:quero|vou|preciso|inclua|incluir|fazer|o|a|cortar)\s+)*$",
                          prefix[-100:]))


def _service_mentions(message: str, catalog: list[dict]):
    lower = normalized(message)
    spans = []
    aliases: dict[str, set[UUID]] = {}
    for item in catalog:
        sid = UUID(str(item["id"]))
        for alias in [item["name"], *item.get("aliases", [])]:
            term = normalized(str(alias)).strip()
            if term:
                aliases.setdefault(term, set()).add(sid)
    for alias, pattern in [("cabelo", r"corte(?: de cabelo| cabelo| masculino| feminino| infantil)?"),
                           ("corte", r"corte(?: de cabelo| cabelo| masculino| feminino| infantil)?"),
                           ("barba", r"barba(?: completa| tradicional| desenhada)?")]:
        candidates = {UUID(str(x["id"])) for x in catalog if re.fullmatch(pattern, normalized(x["name"]))}
        if candidates:
            aliases.setdefault(alias, set()).update(candidates)
    # Here "corte" describes the operation on the beard, not a second service.
    # An owner's complete name/alias wins; ambiguous or missing beard IDs never
    # fall back to the separate haircut that happens to share the first word.
    for alias in _unique([*[term for term in aliases if re.match(r"barba\b", term)], "barba"]):
        for operation in ("corte de ", "corte da ", "cortar ", "cortar a "):
            aliases.setdefault(operation + alias, set(aliases.get(alias, ())))
    for alias in sorted(aliases, key=len, reverse=True):
        for match in re.finditer(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", lower):
            if any(a <= match.start() and b >= match.end() for a, b, *_ in spans):
                continue
            spans.append((match.start(), match.end(), aliases[alias], _negative(lower[:match.start()])))
    spans.sort(key=lambda x: x[0])
    return spans


def _literal_service_subject(message: str, spans: list, professionals: list[dict]) -> bool:
    """Whether a complete literal subject can correct a model's extra service.

    Unknown semantic descriptions remain for the provider/clarification path.
    Recognizing date/time/name syntax here grants no availability or consent.
    """
    if not spans or any(negative or len(ids) != 1 for _, _, ids, negative in spans):
        return False
    text = normalized(message)
    prefix = text[:spans[0][0]]
    if not re.fullmatch(r"\s*(?:(?:quanto(?: que)?\s+(?:custa|custam|fica|ficam)|"
        r"(?:quero|queria|prefiro)(?:\s+(?:fazer|agendar|marcar|reservar))?|"
        r"gostaria de(?:\s+(?:fazer|agendar|marcar|reservar))?)\s+)?"
        r"(?:(?:so|apenas|somente)\s+)?(?:(?:o|a|os|as|um|uma)\s+)?", prefix):
        return False
    for previous, current in zip(spans, spans[1:]):
        if not re.fullmatch(r"\s*(?:e|\+|&|,)\s*(?:(?:o|a|um|uma)\s+)?", text[previous[1]:current[0]]):
            return False
    suffix = text[spans[-1][1]:]
    # Only explicit, registered names and already-supported calendar/clock
    # syntax may follow the literal subject; no free-form clause is discarded.
    for person in professionals:
        name = normalized(person["name"])
        for term in (name, name.split()[0]):
            suffix = re.sub(r"\bcom\s+(?:[oa]\s+)?" + re.escape(term) + r"\b", "", suffix)
    suffix = re.sub(r"\b(?:no|na|para|pra|pro)\s+(?=" + _DAY.pattern + ")", "", suffix)
    suffix = _DAY.sub("", suffix)
    suffix = _HOUR.sub("", suffix)
    return bool(re.fullmatch(r"[\s,.!?]*(?:quanto(?: que)?\s+(?:custa|custam|fica|ficam)[\s,.!?]*)?", suffix))


def unique_service_reply(message: str, catalog: list[dict], *, context_step: str) -> list[UUID] | None:
    """Resolve a whole positive service-only answer to an actual service menu.

    This is a narrow parser, not another intent classifier. Uncovered words or
    any ambiguity defer the complete utterance to the normal understanding path.
    The caller separately verifies that a service question was really presented.
    """
    if context_step != "menu" or not isinstance(message, str) or not message.strip() or len(message) > 1500 or len(catalog) > 60:
        return None
    text = normalized(message)
    if "?" in text or "¿" in text or re.search(r"\d|\b(?:nao|nem|sem|nunca|menos|exceto|"
        r"quanto|custa\w*|preco\w*|valor\w*|como|quando|quais?|onde|porque|por que|sera|duvida|"
        r"se|caso|desde que|depende|talvez|ou|amanha|hoje|ontem|segunda|terca|quarta|quinta|sexta|sabado|domingo|"
        r"compr\w*|pedido\w*|carrinho|orcamento|quantidade|unidades?|paga\w*|estoque|produtos?|"
        r"agend\w*|reserv\w*|cancel\w*|remarc\w*|reagend\w*)\b", text):
        return None
    spans = _service_mentions(message, catalog)
    if not spans:
        return None
    selected, previous_end = [], 0
    for start, end, ids, negative in spans:
        if len(ids) != 1 or negative or start < previous_end:
            return None
        gap = text[previous_end:start]
        if previous_end == 0:
            if gap.strip():
                return None
        elif not re.fullmatch(r"\s*(?:e|\+|&|,\s*e?)\s*", gap):
            return None
        sid = next(iter(ids))
        if sid in selected:
            return None  # A repeated service must not silently imply a quantity.
        selected.append(sid)
        previous_end = end
    return selected if re.fullmatch(r"[.!\s]*", text[previous_end:]) else None


_WEEKDAYS = {"segunda": 0, "terca": 1, "quarta": 2, "quinta": 3, "sexta": 4, "sabado": 5, "domingo": 6}
_DAY = re.compile(r"\b(?:depois de amanha|amanha|hoje|\d{4}-\d{2}-\d{2}|"
                  r"\d{1,2}/\d{1,2}(?:/\d{4})?|segunda(?:-feira)?|terca(?:-feira)?|"
                  r"quarta(?:-feira)?|quinta(?:-feira)?|sexta(?:-feira)?|sabado|domingo)\b")
_CORRECTION = re.compile(r"\b(?:melhor|corrig\w*|pera|em vez|quer dizer|na verdade|deixa|nao)\b")


def _dates(message: str, reference: date | None):
    lower = normalized(message)
    matches = [x for x in _DAY.finditer(lower) if not re.match(
        r"\s+(?:opcao|vez|pessoa|alternativa|parcela|unidade)\b", lower[x.end():])]
    resolved = []
    invalid = False
    for match in matches:
        if _negative(lower[:match.start()]):
            continue
        value = match.group()
        try:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                candidate = date.fromisoformat(value)
            elif "/" in value:
                if reference is None:
                    raise ValueError
                parts = [int(x) for x in value.split("/")]
                candidate = date(parts[2] if len(parts) == 3 else reference.year, parts[1], parts[0])
                if len(parts) == 2 and candidate < reference:
                    candidate = candidate.replace(year=reference.year + 1)
            else:
                if reference is None:
                    raise ValueError
                if value in {"hoje", "amanha", "depois de amanha"}:
                    candidate = reference + timedelta(days={"hoje": 0, "amanha": 1, "depois de amanha": 2}[value])
                else:
                    weekday = _WEEKDAYS[value.split("-")[0]]
                    delta = (weekday - reference.weekday()) % 7
                    if delta == 0 and re.search(r"\bproxim[ao]\s*$", lower[:match.start()]):
                        delta = 7
                    candidate = reference + timedelta(days=delta)
            resolved.append((match.start(), match.end(), candidate.isoformat()))
        except (ValueError, KeyError):
            invalid = True
    distinct = _unique(x[2] for x in resolved)
    previous = next((x for x in reversed(resolved[:-1]) if x[2] != resolved[-1][2]), None) if resolved else None
    if invalid or (len(distinct) > 1 and not _CORRECTION.search(lower[previous[1]:resolved[-1][0]])):
        return None, bool(matches), True
    return (resolved[-1][2] if resolved else None), bool(matches), False


_NUMBERS = {"uma": 1, "um": 1, "duas": 2, "dois": 2, "tres": 3, "quatro": 4, "cinco": 5,
            "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12,
            "treze": 13, "catorze": 14, "quatorze": 14, "quinze": 15, "dezesseis": 16,
            "dezessete": 17, "dezoito": 18, "dezenove": 19, "vinte": 20}
_NUMBER = "(?:" + "|".join(sorted(_NUMBERS, key=len, reverse=True)) + r"|\d{1,2})"
_HOUR = re.compile(r"(?<!\w)(?:(?P<relation>depois das?|antes das?|por volta das?|pelas?|as|das|pras)\s*(?:umas?\s+)?)"
                   r"(?P<hour>" + _NUMBER + r")(?:(?:[:h](?P<minute>\d{2}))|(?P<half>\s+e\s+mei[ao]))?"
                   r"(?:\s*(?:horas?|h))?(?:\s*(?P<period>da manha|da tarde|da noite))?\b|"
                   r"(?<![\d/-])(?P<clock>[01]?\d|2[0-3])[:h](?P<clockmin>[0-5]\d)(?!\d)")


def _times(message: str, context: dict):
    lower = normalized(message)
    # An explicit final correction can omit the repeated preposition. Preserve
    # the correction marker outside the clock span so last-choice checks apply.
    lower = re.sub(r"\b(melhor|quer dizer|na verdade)(?:\s+|(?=\d))(?=" + _NUMBER +
        r"(?:\s+e\s+mei[ao]\b|[:h]|\s+horas?\b|\s*mil\b))", r"\1 as ", lower)
    noisy = list(re.finditer(r"\b(?:as|pelas)\s*" + _NUMBER + r"\s*mil\b", lower))
    if re.fullmatch(_NUMBER + r"(?:\s+e\s+mei[ao])?(?:\s+horas?)?(?:\s+da (?:manha|tarde|noite))?[.!?\s]*", lower) and (
            context.get("step") in {"time", "reschedule_time"} or re.search(r"\bda (?:manha|tarde|noite)\b", lower)):
        lower = "as " + lower
    values, errors = [], [match.start() for match in noisy if not _negative(lower[:match.start()])]
    matches = list(_HOUR.finditer(lower))
    for match in matches:
        if _negative(lower[:match.start()]):
            continue
        token = match.group("hour") or match.group("clock")
        hour = _NUMBERS.get(token, int(token) if token.isdigit() else -1)
        minute = int(match.group("minute") or match.group("clockmin") or (30 if match.group("half") else 0))
        period = match.group("period")
        if not period:
            previous = list(re.finditer(r"\b(?:da tarde|de tarde|da noite|de noite|da manha|de manha)\b", lower[:match.start()]))
            period = previous[-1].group() if previous else context.get("time_period")
        if hour < 12 and match.group("clock") is None:
            if period in {"afternoon", "evening", "da tarde", "de tarde", "da noite", "de noite"}:
                hour += 12
            elif period not in {"morning", "da manha", "de manha"}:
                # Owner-published hours may resolve AM/PM only when exactly one
                # interpretation fits. They do not establish slot availability.
                candidates = []
                for h in [hour, hour + 12]:
                    clock = f"{h:02d}:{minute:02d}"
                    if any(x.get("opens", "99:99") <= clock < x.get("closes", "00:00")
                           for x in context.get("hours", [])):
                        candidates.append(h)
                if len(candidates) != 1:
                    errors.append(match.start())
                    continue
                hour = candidates[0]
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            errors.append(match.start())
            continue
        relation = match.group("relation") or "as"
        relation = "after" if relation.startswith("depois") else "before" if relation.startswith("antes") else "around" if relation.startswith(("por volta", "pel")) else "exact"
        values.append((match.start(), match.end(), f"{hour:02d}:{minute:02d}", relation))
    invalid = bool(errors)
    if errors and values and max(errors) < values[-1][0] and _CORRECTION.search(lower[min(errors):values[-1][0]]):
        invalid = False  # A clear final correction supersedes an uncertain first time.
    if len({x[2:] for x in values}) > 1 and not _CORRECTION.search(lower[values[0][1]:values[-1][0]]):
        invalid = True
    if matches and re.search(r"\bou\s+(?:" + _NUMBER + r"|cinquenta)\b", lower):
        invalid = True
    return (values[-1][2:] if values and not invalid else (None, "none")), bool(matches or noisy), invalid


def clarification_for(plan: UnderstandingPlan) -> str | None:
    """Targeted questions contain no model-provided facts or operational promises."""
    questions = {"service": "Qual serviço você quer? Preciso distinguir as opções do catálogo.",
                 "date": "Qual é o dia que você prefere?", "time": "Qual horário você prefere? Pode dizer, por exemplo, três e meia da tarde.",
                 "professional": "Qual profissional você prefere? Não consegui distinguir o nome com segurança.",
                 "customer_name": "Como você prefere que eu te chame?",
                 "faq": "Pode especificar qual informação você procura?",
                 "intent": "Você quer apenas consultar ou preparar uma alteração?",
                 "unsupported_operation": "Esse pedido precisa ser encaminhado à equipe. Quer falar com uma pessoa?"}
    return questions[plan.ambiguities[0]] if plan.ambiguities else None


def build_plan(*, message: str, catalog: list[dict], professionals: list[dict] | None = None,
               faqs: list[dict] | None = None, context: dict | None = None,
               reference_date: date | None = None, proposal: UnderstandingPlan | None = None) -> UnderstandingPlan:
    """Validate a model proposal and apply explicit whole-message corrections.

    context may contain service_ids, time_period and owner-published hours.
    Missing fields never silently reuse a previous day, time or professional.
    Supplied lists must already belong to the authenticated tenant/revision.
    """
    if not isinstance(message, str) or not message.strip() or len(message) > 1500:
        raise ValueError("Message outside supported bounds")
    professionals, faqs, context = professionals or [], faqs or [], context or {}
    if len(catalog) > 60 or len(professionals) > 30 or len(faqs) > 30:
        raise ValueError("Context outside supported bounds")
    data = (proposal or UnderstandingPlan()).model_dump()
    valid_services = {UUID(str(x["id"])) for x in catalog}
    valid_pros = {UUID(str(x["id"])) for x in professionals}
    valid_faqs = {str(x["id"]) for x in faqs}
    if any(x not in valid_services for name in ("service_ids", "query_service_ids", "declined_service_ids") for x in data[name]) or (
            data["professional_id"] is not None and data["professional_id"] not in valid_pros) or any(x not in valid_faqs for x in data["faq_ids"]):
        raise ValueError("Plan refers to data outside the supplied catalog")
    lower = normalized(message)
    ambiguities = list(data["ambiguities"])
    intents = [x for x in data["intents"] if x != "other"]
    price = bool(re.search(r"\b(?:quanto|preco|valor|custa|custam)\b", lower))
    available = bool(re.search(r"\b(?:disponive[li]s?|vagas?|livre|livres|se tiver|se houver)\b|"
                               r"\b(?:que|qual|quais)\s+dias?\b|\btem\b.*\bhorarios?\b", lower))
    explicit_booking = any(not _negative(lower[:x.start()]) for x in
                           re.finditer(r"\b(?:agendar|marcar|reservar|quero fazer|quero cortar)\b", lower))
    reschedule = [x for x in re.finditer(r"\b(?:remarcar|reagendar|mudar|trocar|adiar)\b", lower) if not _negative(lower[:x.start()])]
    cancel = [x for x in re.finditer(r"\b(?:cancelar?|desmarcar)\b", lower) if not _negative(lower[:x.start()])]
    if price:
        intents.append("price")
    if available:
        intents.append("availability")
    if explicit_booking and not re.search(r"\b(?:nao quero (?:agendar|marcar)|so (?:quero )?(?:saber|pesquis))", lower):
        intents.append("booking")
    if reschedule:
        intents.append("reschedule")
    if cancel:
        intents.append("cancel")
    if cancel and reschedule and _CORRECTION.search(lower[cancel[0].end():reschedule[-1].start()]):
        intents = [x for x in intents if x != "cancel"]
    from app.qr_dialogue import contextual_response
    awaiting_confirmation = context.get("step") in {"confirm", "reschedule_confirm"}
    consent = contextual_response(message, awaiting_confirmation=awaiting_confirmation,
        semantic_kind="affirm" if "confirm" in intents else None)
    if consent.kind == "affirm":
        intents.append("confirm")
    else:
        intents = [x for x in intents if x != "confirm"]
        if consent.reason == "condition_or_hypothesis":
            ambiguities.append("intent")
        if consent.reason in {"negation_or_cancellation", "question_or_doubt"} and not (price or available):
            if re.search(r"\b(?:confirm|pode ser|pode marcar|fechou|isso mesmo)\b", lower):
                ambiguities.append("intent")
        if re.search(r"\b(?:pode confirmar|confirmo)\b.*\b(?:nao|pera|espera)\b", lower):
            ambiguities.append("intent")
    if re.search(r"\b(?:aceita\w*|pix|endereco|onde fica|pagamento|politica|horario de funcionamento)\b", lower):
        intents.append("faq")
    if re.search(r"\b(?:vendem|produto|pomada|shampoo|catalogo)\b", lower):
        intents.append("catalog")
    if re.search(r"\b(?:como|qual(?: e)? a politica)\b.*\b(?:cancelar|remarcar|desmarcar|cancelamento)\b", lower):
        intents = [x for x in intents if x not in {"cancel", "reschedule", "confirm"}]
        intents.append("faq")
    information_only = bool(re.search(r"\b(?:(?:so|apenas)(?: (?:quero|queria|gostaria))? (?:saber|consultar|pesquis)|estou (?:apenas )?pesquis|nao quero (?:agendar|marcar))", lower))
    if information_only or (price and not explicit_booking and not reschedule and not cancel):
        intents = [x for x in intents if x not in {"booking", "confirm"}]
        data.update(service_ids=[], selection_mode="none")
    mentions = _service_mentions(message, catalog)
    composed_subject = any(re.match(r"(?:corte d[ae]|cortar(?: a)?) barba\b", lower[start:end]) for start, end, *_ in mentions)
    literal_composition = composed_subject and _literal_service_subject(message, mentions, professionals)
    if mentions and not price and not available and re.search(r"\b(?:quero|queria|gostaria|prefiro|deixa|inclua|tirar|retirar)\b", lower):
        if not set(intents) & {"reschedule", "cancel"}:
            intents.append("booking")
    chosen, declined = [], list(data["declined_service_ids"])
    replaced, local_service_ambiguity, previous_end = False, False, 0
    for start, _end, ids, negative in mentions:
        if len(ids) != 1:
            local_service_ambiguity = True
            previous_end = _end
            continue
        sid = next(iter(ids))
        if negative:
            declined.append(sid);chosen = [x for x in chosen if x != sid]
        else:
            replacement = re.search(r"\b(?:so|apenas|somente|melhor|quer dizer|na verdade)\s+(?:(?:quero|fazer|o|a)\s+)*$", lower[:start])
            if replacement:
                declined += [x for x in chosen if x != sid]
                chosen = [];replaced = True;local_service_ambiguity = False
            elif chosen and re.search(r"\bou\b", lower[previous_end:start]):
                local_service_ambiguity = True
            chosen.append(sid)
            # A later explicit inclusion supersedes an earlier refusal.
            declined = [x for x in declined if x != sid]
        previous_end = _end
    if local_service_ambiguity:
        ambiguities.append("service")
    elif replaced or literal_composition:
        ambiguities = [x for x in ambiguities if x != "service"]
    chosen, declined = _unique(chosen), _unique(declined)
    if composed_subject and proposal and not literal_composition and not replaced and not declined:
        # A second semantic service may live in an uncovered clause. Ask rather
        # than either erase it or sum the word "corte" inside "corte de barba".
        if (set(data["service_ids"]) | set(data["query_service_ids"])) - set(chosen):
            ambiguities.append("service")
    base = [UUID(str(x)) for x in context.get("service_ids", []) if UUID(str(x)) in valid_services]
    if declined and not chosen:
        if price or available:
            # A hypothetical removal changes only this read-only subject, not
            # the services selected in the booking being discussed.
            remaining = [x for x in data["query_service_ids"] if x not in declined]
            data["query_service_ids"] = remaining or [x for x in base if x not in declined]
            if not data["query_service_ids"]:
                ambiguities.append("service")
        elif base:
            # An explicit refusal has a final selection even when no positive
            # service name is repeated and the provider returned mode none.
            remaining = [x for x in data["service_ids"] if x not in declined]
            data.update(service_ids=remaining or [x for x in base if x not in declined], selection_mode="remove")
            if not set(intents) & {"reschedule", "cancel", "human"}:
                intents.append("booking")
    if price and "booking" not in intents and chosen:
        # Local aliases must not discard a semantic match the provider found
        # (e.g. 'dar um tapa no visual' plus an exact 'barba'). Explicit final
        # replacement/negation remains authoritative over the provider.
        if not (proposal and data["query_service_ids"] and not declined and not replaced and not literal_composition):
            data["query_service_ids"] = chosen
    elif chosen and (not proposal or not data["service_ids"] or replaced or declined or literal_composition) and (
            not (price and "booking" in intents and proposal) or replaced or declined or literal_composition):
        data["service_ids"] = chosen
        if "booking" in intents or "reschedule" in intents:
            data["selection_mode"] = "replace"
    if not price and not available and re.search(r"\b(?:inclu(?:ir|a)|adiciona\w*|acrescenta\w*)\b", lower) and chosen:
        data.update(service_ids=_unique(base + chosen), selection_mode="add")
        intents.append("booking")
    if not price and re.search(r"\b(?:tira|tirar|retira|retirar|remove|remover)\b", lower) and declined:
        data.update(service_ids=[x for x in base if x not in declined], selection_mode="remove")
        intents.append("booking")
        if not base:
            ambiguities.append("service")
    if re.search(r"\b(?:e com|se (?:eu )?(?:colocar|incluir)|se incluir)\b", lower):
        data.update(query_service_ids=_unique(base + chosen), service_ids=[], selection_mode="simulate")
        if not price:
            intents.append("availability")
        intents = [x for x in intents if x != "booking"]
    for name in ("service_ids", "query_service_ids"):
        data[name] = [x for x in data[name] if x not in declined]
    data["declined_service_ids"] = declined
    if "service" in ambiguities:
        data.update(service_ids=[], query_service_ids=[], selection_mode="none")
    if re.search(r"\b(?:fazer tudo|quero tudo|todos os servicos)\b", lower) and not chosen:
        # 'Everything' does not select an entire catalog or an old menu by guess.
        ambiguities.append("service");data.update(service_ids=[], query_service_ids=[], selection_mode="none")
    day, mentioned_day, bad_day = _dates(message, reference_date)
    if mentioned_day:
        data["requested_date"] = day
        if bad_day:
            ambiguities.append("date")
        else:
            ambiguities = [x for x in ambiguities if x != "date"]
    elif not re.search(r"\b(?:janeiro|fevereiro|marco|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)\b", lower):
        data["requested_date"] = None
    if mentioned_day and not set(intents) & {"booking", "reschedule", "cancel"} and context.get("service_ids"):
        intents.append("availability")
    (clock, relation), mentioned_time, bad_time = _times(message, context)
    if mentioned_time:
        data.update(requested_time=clock, time_relation=relation)
        if bad_time:
            ambiguities.append("time")
        else:
            ambiguities = [x for x in ambiguities if x != "time"]
    else:
        data.update(requested_time=None, time_relation="none")
    pro_matches = []
    data["professional_id"] = None  # Never carry a prior model/context name by habit.
    names_by_id = {UUID(str(x["id"])): normalized(x["name"]) for x in professionals}
    for person in professionals:
        name = normalized(person["name"])
        first = name.split()[0]
        for match in re.finditer(r"\b" + re.escape(first) + r"\b", lower):
            declared = re.search(r"\b(?:me chamo|meu nome e|sou [oa])\s+$", lower[:match.start()])
            if not declared and not _negative(lower[:match.start()]):
                pro_matches.append((match.start(), UUID(str(person["id"]))))
    ids = _unique(x[1] for x in sorted(pro_matches))
    if len(ids) == 1:
        # Names self-declared by a customer are not professional selections.
        if not re.search(r"\b(?:me chamo|meu nome e|sou [oa])\s+" + re.escape(names_by_id[ids[0]].split()[0]), lower):
            data["professional_id"] = ids[0]
    elif len(ids) > 1:
        positions = sorted(pro_matches)
        if _CORRECTION.search(lower[positions[0][0]:positions[-1][0]]) and not re.search(r"\bou\b", lower[positions[0][0]:positions[-1][0]]):
            data["professional_id"] = positions[-1][1]
        else:
            data["professional_id"] = None;ambiguities.append("professional")
    non_people = {"barba", "cabelo", "corte", "produto", "produtos", "pix", "cartao", "dinheiro", "desconto", "voces", "certeza", "isso", "atendente", "humano", "uma", "pessoa"}
    first_names = {name.split()[0] for name in names_by_id.values()}
    introduced = re.findall(r"\b(?:com|profissional|barbeiro|barbeira)\s+(?:o\s+|a\s+)?([a-z]+)", lower)
    alternative = re.search(r"\b(?:" + "|".join(re.escape(x) for x in first_names) + r")\s+ou\s+(?:com\s+)?(?:o\s+|a\s+)?([a-z]+)", lower) if first_names else None
    if any(word not in first_names | non_people for word in introduced) or (
            alternative and alternative.group(1) not in first_names | non_people | set(_WEEKDAYS) | {"amanha", "hoje"}):
        data["professional_id"] = None;ambiguities.append("professional")
    from app.qr_customer import supplied_name
    data["customer_name"] = supplied_name(message)
    for faq in faqs:
        if any(normalized(str(term)).strip(" ?!.") in lower for term in [faq["question"], *faq.get("aliases", [])] if str(term).strip()):
            data["faq_ids"].append(str(faq["id"]))
            intents.append("faq")
    if information_only:
        data["sales_optional"] = False
        data["declined_offers"].append("booking")
    if re.search(r"\b(?:sem|nao quero|nao vou)\s+(?:agendar|marcar|reservar)\b", lower):
        intents = [x for x in intents if x not in {"booking", "confirm"}]
        data.update(service_ids=[], selection_mode="none", sales_optional=False)
        data["declined_offers"].append("booking")
    if re.search(r"\b(?:nao quero produtos?|sem produtos?)\b", lower):
        data["declined_offers"].append("products");data["sales_optional"] = False
    if declined or re.search(r"\b(?:so o|so a|nao quero adicional|sem adicional)\b", lower):
        data["declined_offers"].append("extras");data["sales_optional"] = False
    if set(intents) & {"cancel", "human"}:
        data["sales_optional"] = False
    if awaiting_confirmation and consent.kind == "amend" and not (price or available or "?" in lower):
        # A consent word plus an explicit correction prepares a NEW proposal.
        # It never authorizes the old summary, even if the model said confirm.
        if (data["service_ids"] or data["requested_date"] or data["requested_time"] or data["professional_id"] or
                (data["selection_mode"] == "remove" and data["declined_service_ids"])):
            intents = [x for x in intents if x not in {"confirm", "availability"}]
            intents.append("reschedule" if context["step"] == "reschedule_confirm" else "booking")
        else:
            ambiguities.append("intent")
    if "catalog" in intents and not price and re.search(r"\b(?:comprar|pagar|fechar pedido|confirmar compra)\b", lower):
        ambiguities.append("unsupported_operation")
    if re.search(r"\b(?:atendente humano|falar com (?:uma pessoa|um humano|atendente)|pessoa de verdade)\b", lower) and not re.search(r"\bnao quero falar\b", lower):
        intents = ["human"];data.update(service_ids=[], selection_mode="none", sales_optional=False)
    data.update(intents=_unique(intents) or ["other"], ambiguities=_unique(ambiguities))
    for name in ("service_ids", "query_service_ids", "declined_service_ids", "faq_ids", "declined_offers"):
        data[name] = _unique(data[name])
    if any(len(data[name]) > 6 for name in ("service_ids", "query_service_ids", "declined_service_ids")):
        data.update(service_ids=[], query_service_ids=[], declined_service_ids=[], selection_mode="none")
        data["ambiguities"] = _unique([*data["ambiguities"], "service"])
    return UnderstandingPlan.model_validate(data)
