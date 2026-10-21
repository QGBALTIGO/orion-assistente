"""Pure WhatsApp presentation of backend-verified facts; no I/O or AI authority.

Callers load style_rules from the authenticated business profile and provide
validated facts. This module never selects a business, reads a catalog, applies
discounts, changes an operation or generates factual prose with a language model.
"""
from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Literal, Mapping, Sequence
import unicodedata
from zoneinfo import ZoneInfo


STYLE_LABELS = {
    "warm": "Acolhedor e próximo",
    "professional": "Elegante e profissional",
    "relaxed": "Descontraído",
    "direct": "Direto e cordial",
}
Style = Literal["warm", "professional", "relaxed", "direct"]
NextAction = Literal["none", "ask_day", "ask_service", "ask_professional", "ask_time"]


def message_units(value: str) -> int:
    """Match the JavaScript bridge's UTF-16 length, including emoji pairs."""
    if not isinstance(value, str):
        raise ValueError("Message text required")
    return sum(2 if ord(character) > 0xFFFF else 1 for character in value)


def fits_message(value: str, limit: int = 1200) -> bool:
    if type(limit) is not int or limit < 1:
        raise ValueError("Positive message limit required")
    return message_units(value) <= limit


def _text(value: str, maximum: int = 120) -> str:
    if not isinstance(value, str):
        raise ValueError("Expected text")
    value = " ".join(value.split())
    if not value or len(value) > maximum or any(unicodedata.category(c) == "Cc" for c in value):
        raise ValueError("Invalid factual label")
    return value


def _integer(value: int, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("Invalid factual number")
    return value


def _label(value: str) -> str:
    """Escape WhatsApp delimiters in factual labels without double escaping.

    Internal underscores in identifiers (SKU_250_ML) are literal already. FAQ
    prose is never passed through this helper, preserving its approved wording.
    """
    result, slashes = [], 0
    for index, char in enumerate(value):
        internal = char == "_" and 0 < index < len(value) - 1 and value[index - 1].isalnum() and value[index + 1].isalnum()
        if char in {"*", "_"} and not internal and slashes % 2 == 0:
            result.append("\\")
        result.append(char)
        slashes = slashes + 1 if char == "\\" else 0
    return "".join(result)


def escape_label(value: str) -> str:
    """Public boundary for a validated name inserted by a caller-owned template."""
    if not isinstance(value, str):
        raise ValueError("Expected a factual label")
    return _label(value)


def display_name(value: str, *, simulation: bool = False, verified_suffix: str | None = None) -> str:
    """Only an explicit simulation marker may be separated from a display name.

    The caller must still label the whole response as a simulation. Names in a
    real booking are preserved, including words that resemble internal metadata.
    """
    name = _text(value)
    if type(simulation) is not bool:
        raise ValueError("Explicit simulation choice required")
    if verified_suffix is not None:
        suffix = _text(verified_suffix)
        if simulation and name.endswith(suffix):
            candidate = name[:-len(suffix)].rstrip()
            if candidate:
                return candidate
    return name


def service_icon(services: Sequence[str]) -> str:
    """Decorate explicit catalog labels, without classifying the whole tenant."""
    names = {"".join(c for c in unicodedata.normalize("NFD", name.casefold())
                     if unicodedata.category(c) != "Mn") for name in _labels(services, 12)}
    if names and names <= {"barba", "corte de barba", "aparar barba"}:
        return "🪒"
    if names and names <= {"corte", "corte de cabelo", "cabelo", "barba", "corte de barba"} and (
            "barba" in names or "corte de cabelo" in names or "cabelo" in names):
        return "✂️"
    if names and all("manicure" in name or "pedicure" in name for name in names):
        return "💅"
    if names and all("veterin" in name or "banho e tosa" in name for name in names):
        return "🐾"
    if names and all("odontolog" in name or "limpeza dental" in name for name in names):
        return "🦷"
    return "📋"


_FIELD_ICONS = {"Profissional": "👤", "Data": "📅", "Dia consultado": "📅",
                "Horário": "🕒", "Duração": "⏳", "Duração estimada": "⏳",
                "Total": "💰", "Protocolo": "🔖"}


def _field(label: str, value: str, *, light: bool = False, icon: str | None = None) -> str:
    marker = icon or _FIELD_ICONS.get(label, "")
    return (marker + " " if light and marker else "") + f"*{label}:* {value}"


@dataclass(frozen=True)
class PresentationPreferences:
    style: Style = "warm"
    emoji: Literal["none", "light"] = "none"
    length: Literal["short", "balanced", "detailed"] = "balanced"
    formality: Literal["neutral", "formal"] = "neutral"
    commercial_intensity: Literal["none", "contextual"] = "none"
    avoid_phrases: tuple[str, ...] = ()

    def __post_init__(self):
        for field, allowed in (
            ("style", STYLE_LABELS), ("emoji", {"none", "light"}),
            ("length", {"short", "balanced", "detailed"}),
            ("formality", {"neutral", "formal"}),
            ("commercial_intensity", {"none", "contextual"}),
        ):
            if getattr(self, field) not in allowed:
                raise ValueError("Unknown presentation preference: " + field)
        if not isinstance(self.avoid_phrases, (tuple, list)) or len(self.avoid_phrases) > 20:
            raise ValueError("Invalid avoided phrases")
        object.__setattr__(self, "avoid_phrases", tuple(_text(x, 80) for x in self.avoid_phrases))

    @classmethod
    def from_mapping(cls, value: Mapping | None = None):
        value = dict(value or {})
        if set(value) - set(cls.__dataclass_fields__):
            raise ValueError("Unknown presentation preference")
        return cls(**value)


@dataclass(frozen=True)
class InteractionContext:
    information_only: bool = False
    declined: bool = False
    complaint: bool = False
    handoff: bool = False

    @property
    def suppress_follow_up(self) -> bool:
        return self.information_only or self.declined or self.complaint or self.handoff

    @classmethod
    def from_message(cls, message: str, **remembered):
        """Conservative presentation hints; the core owns intent and refusal memory."""
        lower = "".join(c for c in unicodedata.normalize("NFD", message.casefold())
                        if unicodedata.category(c) != "Mn")
        only = bool(re.search(r"\b(?:so|apenas|somente)\b.*\b(?:preco|valor|pesquisando|pesquisar|consultando|saber)\b", lower)
                    or re.search(r"\b(?:estou|to)\s+(?:apenas\s+|so\s+)?pesquisando\b", lower))
        declined = bool(re.fullmatch(r"\s*(?:nao|n|dispenso)[.!\s]*", lower)
                        or re.search(r"\b(?:nao quero|nao tenho interesse|sem produto|so o corte)\b", lower))
        complaint = bool(re.search(r"\b(?:reclamacao|reclamar|insatisfeito|insatisfeita|cobranca indevida)\b", lower))
        return cls(information_only=only or bool(remembered.get("information_only")),
                   declined=declined or bool(remembered.get("declined")),
                   complaint=complaint or bool(remembered.get("complaint")),
                   handoff=bool(remembered.get("handoff")))


@dataclass(frozen=True)
class QuoteItem:
    name: str
    unit_price_cents: int | None
    quantity: int = 1
    duration_min: int | None = None
    price_kind: Literal["fixed", "from", "quote"] = "fixed"

    def __post_init__(self):
        object.__setattr__(self, "name", _text(self.name))
        _integer(self.quantity, 1, 1000)
        if self.price_kind not in {"fixed", "from", "quote"}:
            raise ValueError("Unknown price kind")
        if self.unit_price_cents is None:
            if self.price_kind != "quote":
                raise ValueError("Missing verified price")
        else:
            _integer(self.unit_price_cents, 0, 100_000_000)
            if self.price_kind == "quote":
                raise ValueError("A custom quote cannot imply a fixed price")
        if self.duration_min is not None:
            _integer(self.duration_min, 1, 1440)


@dataclass(frozen=True)
class ApprovedSuggestion:
    """The caller must verify this relation against the current business catalog."""
    name: str
    approved_relation_id: str
    price_cents: int | None = None

    def __post_init__(self):
        object.__setattr__(self, "name", _text(self.name))
        object.__setattr__(self, "approved_relation_id", _text(self.approved_relation_id, 160))
        if self.price_cents is not None:
            _integer(self.price_cents, 0, 100_000_000)


@dataclass(frozen=True)
class BookingSummary:
    services: tuple[str, ...]
    starts_at: datetime
    timezone: str
    total_cents: int
    professional: str | None = None
    duration_min: int | None = None
    protocol: str | None = None

    def __post_init__(self):
        if not isinstance(self.services, (tuple, list)) or not 1 <= len(self.services) <= 12:
            raise ValueError("Missing verified services")
        object.__setattr__(self, "services", tuple(_text(x) for x in self.services))
        if not isinstance(self.starts_at, datetime) or self.starts_at.utcoffset() is None:
            raise ValueError("A summary requires a timezone-aware start")
        ZoneInfo(self.timezone)
        _integer(self.total_cents, 0, 100_000_000)
        if self.professional is not None:
            object.__setattr__(self, "professional", _text(self.professional))
        if self.duration_min is not None:
            _integer(self.duration_min, 1, 1440)
        if self.protocol is not None:
            object.__setattr__(self, "protocol", _text(self.protocol, 80))


def money(cents: int) -> str:
    _integer(cents, 0, 3_000_000_000_000)  # At most 30 validated commercial lines.
    amount = f"{cents // 100:,}".replace(",", ".")
    return "R$ " + amount + f",{cents % 100:02d}"


def duration(minutes: int) -> str:
    _integer(minutes, 1, 17_280_000)
    hours, remainder = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} " + ("hora" if hours == 1 else "horas"))
    if remainder:
        parts.append(f"{remainder} " + ("minuto" if remainder == 1 else "minutos"))
    return " e ".join(parts)


def _preferences(value) -> PresentationPreferences:
    return value if isinstance(value, PresentationPreferences) else PresentationPreferences.from_mapping(value)


def _optional(text: str, prefs: PresentationPreferences) -> str:
    # Avoided expressions affect optional wording, never catalog labels or facts.
    return "" if any(x.casefold() in text.casefold() for x in prefs.avoid_phrases) else text


def _intro(prefs: PresentationPreferences) -> str:
    candidates = {"warm": "Valores para você", "professional": "Consulta de valores",
                  "relaxed": "Esses são os valores", "direct": "Valores"}
    text = candidates[prefs.style]
    if prefs.formality == "formal" and prefs.style == "relaxed":
        text = "Valores consultados"
    text = _optional(text, prefs)
    if text and prefs.emoji == "light":
        text = "💬 " + text
    return f"*{text}*" if text else ""


def _follow_up(action: NextAction, prefs: PresentationPreferences) -> str:
    prompts = {
        "none": "", "ask_day": "Tem algum dia em mente? Posso conferir os horários para você.",
        "ask_service": "Qual serviço você gostaria de consultar?",
        "ask_professional": "Tem preferência por algum profissional?",
        "ask_time": "Qual dos horários apresentados você prefere?",
    }
    if action not in prompts:
        raise ValueError("Unknown next action")
    text = prompts[action]
    if action == "ask_day" and prefs.formality == "formal":
        text = "Se desejar, posso consultar os horários. Há uma data de preferência?"
    return _optional(text, prefs)


def render_approved_suggestion(suggestion: ApprovedSuggestion, preferences=None, *, context: InteractionContext) -> str:
    """At most one caller-verified relation, without implying stock or a discount."""
    prefs = _preferences(preferences)
    if not isinstance(context, InteractionContext) or not isinstance(suggestion, ApprovedSuggestion):
        raise ValueError("A suggestion requires verified context and an approved relation")
    if context.suppress_follow_up or prefs.commercial_intensity != "contextual":
        return ""
    price = f", por *{money(suggestion.price_cents)}*" if suggestion.price_cents is not None else ""
    return _optional(f"Uma opção complementar cadastrada é *{_label(suggestion.name)}*{price}.", prefs)


def render_quote(items: Sequence[QuoteItem], preferences=None, *, context: InteractionContext | None = None,
                 next_action: NextAction = "none", suggestion: ApprovedSuggestion | None = None,
                 show_total: bool = True) -> str:
    prefs, context = _preferences(preferences), context or InteractionContext()
    if type(show_total) is not bool:
        raise ValueError("Explicit total display choice required")
    if not 1 <= len(items) <= 12 or any(not isinstance(x, QuoteItem) for x in items):
        raise ValueError("A quote requires verified items")
    lines = []
    for item in items:
        name = (f"{item.quantity} × " if item.quantity != 1 else "") + _label(item.name)
        if item.price_kind == "quote":
            price = "valor sob consulta"
        else:
            price = f"*{money(item.unit_price_cents * item.quantity)}*"
            if item.price_kind == "from":
                price = "a partir de " + price
        detail = f" · {duration(item.duration_min * item.quantity)}" if prefs.length == "detailed" and item.duration_min else ""
        icon = service_icon((item.name,)) + " " if prefs.emoji == "light" else ""
        lines.append(f"{icon}*{name}* — {price}{detail}")
    parts = [_intro(prefs), "\n".join(lines)]
    totals = []
    if show_total and all(x.price_kind == "fixed" for x in items) and (len(items) > 1 or items[0].quantity > 1):
        total = sum(x.unit_price_cents * x.quantity for x in items)
        totals.append(_field("Total", money(total), light=prefs.emoji == "light"))
    if show_total and all(x.duration_min is not None for x in items):
        minutes = sum(x.duration_min * x.quantity for x in items)
        totals.append(_field("Duração estimada", duration(minutes), light=prefs.emoji == "light"))
    if totals:
        parts.append("\n".join(totals))
    if not context.suppress_follow_up:
        if suggestion is not None and prefs.commercial_intensity == "contextual":
            parts.append(render_approved_suggestion(suggestion, prefs, context=context))
        parts.append(_follow_up(next_action, prefs))
    separator = "\n" if prefs.length == "short" else "\n\n"
    return separator.join(part for part in parts if part)


def render_service_menu(business_name: str, catalog: Sequence[Mapping], preferences=None, *,
                        greeting: str = "") -> str:
    """Present the first 12 catalog alternatives in the caller's exact order.

    The core retains menu_ids and conversation state. A menu is not a selection:
    never total its prices/durations or imply availability, a hold or a booking.
    Names (including explicit demonstration labels) are preserved, not inferred.
    The existing final message boundary owns oversized-menu handling.
    """
    prefs = _preferences(preferences)
    name = _label(_text(business_name, 160))
    if not isinstance(catalog, (tuple, list)) or not isinstance(greeting, str):
        raise ValueError("A verified catalog and textual greeting are required")
    opening = _label(_text(greeting, 400)) + " " if greeting.strip() else ""
    opening += f"Bem-vindo à *{name}*!"
    headings = {"warm": "Conheça nossos serviços", "professional": "Serviços e valores",
                "relaxed": "Veja nossos serviços", "direct": "Serviços"}
    heading = headings[prefs.style]
    if prefs.style == "relaxed" and prefs.formality == "formal":
        heading = "Serviços para consulta"
    heading = _optional(heading, prefs)
    blocks = []
    for row in catalog[:12]:
        if not isinstance(row, Mapping):
            raise ValueError("Expected a verified service mapping")
        item = QuoteItem(row["name"], row["price_cents"], duration_min=row["duration_min"])
        if item.duration_min is None:
            raise ValueError("A catalog service requires its registered duration")
        marker = service_icon((item.name,)) + " " if prefs.emoji == "light" else "• "
        blocks.append(f"{marker}*{_label(item.name)}* — *{money(item.unit_price_cents)}*\n"
                      + _field("Duração", duration(item.duration_min), light=prefs.emoji == "light"))
    if not blocks:
        return opening + "\n\nNão há serviços no catálogo para exibir.\n\nComo posso ajudar?"
    separator = "\n" if prefs.length == "short" else "\n\n"
    parts = [opening, f"*{heading}*" if heading else "", separator.join(blocks), render_prompt("service", prefs)]
    return "\n\n".join(part for part in parts if part)


def render_draft_request(services: Sequence[str], preferences=None, *,
                         desired_day: str | None = None, desired_time: str | None = None,
                         professional: str | None = None, ask_review: bool = True) -> str:
    """Present caller-owned wishes, never a checked slot or an actionable summary.

    The core supplies display-ready day/time, including any before/after/around
    qualifier. No catalog arithmetic, availability inference or consent is added.
    The existing final message boundary owns oversized-answer handling.
    """
    prefs = _preferences(preferences)
    names = _labels(services, 12)
    if type(ask_review) is not bool:
        raise ValueError("Explicit review-question choice required")
    light = prefs.emoji == "light"
    identity, wishes = [], []
    if names:
        identity.append(_field("Serviço" if len(names) == 1 else "Serviços",
                               " e ".join(_label(name) for name in names),
                               light=light, icon=service_icon(names)))
    if professional is not None:
        identity.append(_field("Profissional", _label(_text(professional)), light=light))
    if desired_day is not None:
        wishes.append(_field("Data desejada", _label(_text(desired_day)), light=light, icon="📅"))
    if desired_time is not None:
        wishes.append(_field("Horário desejado", _label(_text(desired_time)), light=light, icon="🕒"))
    parts = ["*Pedido em preparação — ainda sem reserva*", "\n".join(identity), "\n".join(wishes),
             "_Ainda preciso consultar a disponibilidade na agenda._"]
    if ask_review:
        parts.append("Posso verificar e mostrar um novo resumo?")
    return "\n\n".join(part for part in parts if part)


@dataclass(frozen=True)
class SummaryComposition:
    """A prepared body and its single core-owned decision, never a consent token."""
    body: str
    prompt: str = ""

    def __post_init__(self):
        if not isinstance(self.body, str) or not self.body.strip() or message_units(self.body) > 12000:
            raise ValueError("Complete prepared summary required")
        if not isinstance(self.prompt, str) or self.prompt not in {"", "Posso confirmar esse agendamento?", "Posso confirmar essa remarcação?",
                               "Posso cancelar esse agendamento?"}:
            raise ValueError("Unknown structured confirmation question")

    def render(self) -> str:
        return self.body + ("\n\n" + self.prompt if self.prompt else "")

    def as_dict(self) -> dict:
        return {"schema_version": 1, "body": self.body, "prompt": self.prompt}

    @classmethod
    def from_mapping(cls, value: Mapping):
        if set(value) != {"schema_version", "body", "prompt"} or type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise ValueError("Unknown summary composition schema")
        return cls(value["body"], value["prompt"])


def compose_summary(summary: BookingSummary, preferences=None, *, confirmed: bool = False,
                    simulation: bool = False, operation: str = "booking") -> SummaryComposition:
    prefs = _preferences(preferences)
    if type(confirmed) is not bool or type(simulation) is not bool:
        raise ValueError("Explicit operation outcome required")
    if not isinstance(summary, BookingSummary) or (confirmed and not summary.protocol):
        raise ValueError("A confirmed summary requires the verified protocol")
    if confirmed and simulation:
        raise ValueError("Simulation cannot claim an actual confirmation")
    if operation not in {"booking", "reschedule", "cancel"}:
        raise ValueError("Unknown verified booking operation")
    headings = {"warm": "Confira seu agendamento", "professional": "Proposta de agendamento",
                "relaxed": "Seu agendamento, antes de confirmar", "direct": "Resumo para confirmação"}
    confirmations = {"warm": "Seu agendamento está confirmado.", "professional": "Agendamento confirmado.",
                     "relaxed": "Tudo certo, agendamento confirmado.", "direct": "Agendamento confirmado."}
    heading = confirmations[prefs.style] if confirmed else headings[prefs.style]
    if operation == "reschedule":
        heading = "Remarcação confirmada." if confirmed else "Proposta de remarcação"
    elif operation == "cancel":
        heading = "Cancelamento confirmado." if confirmed else "Agendamento selecionado para cancelamento"
    if simulation:
        heading = "Prévia do resumo — nenhum agendamento foi criado." if operation == "booking" else "Prévia do resumo — nenhuma alteração foi realizada."
    light = prefs.emoji == "light"
    lines = [("✅ " if confirmed and light else "") + f"*{heading}*", "", *_booking_lines(summary, light=light)]
    prompt = ""
    if confirmed and operation == "cancel":
        lines.append(_field("Protocolo", _label(summary.protocol), light=light))
    elif not confirmed and not simulation:
        if operation == "reschedule":
            lines.extend(["", "_O agendamento original permanece até a confirmação da remarcação._"])
        prompt = {"booking": "Posso confirmar esse agendamento?", "reschedule": "Posso confirmar essa remarcação?",
                  "cancel": "Posso cancelar esse agendamento?"}[operation]
    return SummaryComposition("\n".join(lines), prompt)


def render_summary(summary: BookingSummary, preferences=None, *, confirmed: bool = False,
                   simulation: bool = False, operation: str = "booking") -> str:
    return compose_summary(summary, preferences, confirmed=confirmed, simulation=simulation, operation=operation).render()


def render_summary_review(summary: SummaryComposition | Mapping | str, preferences=None) -> str:
    """Replay prepared structure; never infer/append a question from prose.

    Legacy complete strings are preserved verbatim. The core can revalidate and
    rebuild old proposals from facts, but presentation must not invent consent.
    """
    _preferences(preferences)
    if isinstance(summary, Mapping):
        summary = SummaryComposition.from_mapping(summary)
    if isinstance(summary, SummaryComposition):
        body = summary.render()
    elif isinstance(summary, str) and summary.strip():
        body = summary.strip()
    else:
        raise ValueError("Complete stored summary required")
    answer = "Este é o resumo atual:\n\n" + body
    return answer if fits_message(answer) else body


def render_booking_history(records: Sequence[Mapping], preferences=None, *, timezone: str) -> str:
    """Compact customer-facing history, from scoped backend rows only."""
    prefs = _preferences(preferences)
    zone = ZoneInfo(timezone)
    labels = {"confirmed": "confirmado", "cancelled": "cancelado", "hold": "aguardando confirmação",
              "completed": "concluído", "no_show": "não compareceu"}
    lines = []
    for index, row in enumerate(records, 1):
        when = row["start_at"]
        if isinstance(when, str):
            when = datetime.fromisoformat(when)
        if not isinstance(when, datetime) or when.tzinfo is None or when.utcoffset() is None:
            raise ValueError("Verified timezone-aware booking time required")
        local = when.astimezone(zone)
        protocol = _label(_text(row["protocol"], 80))
        status = labels.get(row.get("status"), "a conferir")
        lines.append(f"{index}. " + ("📅 " if prefs.emoji == "light" else "") +
                     f"{local:%d/%m/%Y} às {_clock(local)} · {status}\n" +
                     _field("Protocolo", protocol, light=prefs.emoji == "light"))
    answer = "*Seus agendamentos*\n\n" + "\n\n".join(lines)
    if not fits_message(answer):
        return "O histórico completo precisa ser consultado por partes. Qual data ou protocolo você quer conferir?"
    return answer


_WEEKDAYS = ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo")


def _clock(value: datetime) -> str:
    return f"{value.hour:02d}h" + (f"{value.minute:02d}" if value.minute else "")


def _booking_lines(summary: BookingSummary, *, light: bool = False) -> list[str]:
    when = summary.starts_at.astimezone(ZoneInfo(summary.timezone))
    label = "Serviço" if len(summary.services) == 1 else "Serviços"
    lines = [_field(label, " e ".join(_label(value) for value in summary.services), light=light,
                    icon=service_icon(summary.services))]
    if summary.professional:
        lines.append(_field("Profissional", _label(summary.professional), light=light))
    lines.extend(["", _field("Data", f"{_WEEKDAYS[when.weekday()]}, {when:%d/%m/%Y}", light=light),
                  _field("Horário", _clock(when), light=light)])
    if summary.duration_min:
        lines.append(_field("Duração", duration(summary.duration_min), light=light))
    lines.extend(["", _field("Total", money(summary.total_cents), light=light)])
    return lines


def render_slots(services: Sequence[str], slots: Sequence[datetime | str], preferences=None, *,
                 timezone: str, day: date | str | None = None, total_cents: int | None = None,
                 duration_min: int | None = None, simulation: bool = False, ask_selection: bool = True,
                 professional: str | None = None) -> str:
    """Group only the supplied real slots. Never synthesize intervals between them."""
    prefs, zone = _preferences(preferences), ZoneInfo(timezone)
    names = _labels(services, 12)
    if not names or isinstance(slots, (str, bytes)) or not isinstance(slots, Sequence) or len(slots) > 1000:
        raise ValueError("Verified services and real slots are required")
    if type(simulation) is not bool or type(ask_selection) is not bool:
        raise ValueError("Explicit display choices required")
    target_day = date.fromisoformat(day) if isinstance(day, str) else day
    if target_day is not None and type(target_day) is not date:
        raise ValueError("Invalid query day")
    grouped = {}
    for raw in slots:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00")) if isinstance(raw, str) else raw
        if not isinstance(value, datetime) or value.utcoffset() is None or value.second or value.microsecond:
            raise ValueError("Slots require aware minute precision")
        value = value.astimezone(zone)
        if target_day is not None and value.date() != target_day:
            raise ValueError("A slot falls outside the verified query day")
        grouped.setdefault(value.date(), set()).add(value)
    if total_cents is not None:
        _integer(total_cents, 0, 100_000_000)
    if duration_min is not None:
        _integer(duration_min, 1, 1440)
    if not grouped:
        return render_prompt("unavailable", prefs) if ask_selection else "Não encontrei horários nessa consulta."
    heading = {"warm": "Horários para você", "professional": "Horários disponíveis na consulta",
               "relaxed": "Veja os horários disponíveis", "direct": "Horários disponíveis"}[prefs.style]
    light = prefs.emoji == "light"
    facts = [_field("Serviço" if len(names) == 1 else "Serviços", " e ".join(_label(value) for value in names),
                    light=light, icon=service_icon(names))]
    if professional is not None:
        facts.append(_field("Profissional", _label(_text(professional)), light=light))
    if duration_min is not None:
        facts.append(_field("Duração", duration(duration_min), light=light))
    if total_cents is not None:
        facts.append(_field("Total", money(total_cents), light=light))
    parts = [("🕒 " if light else "") + f"*{heading}*", "\n".join(facts)]
    count = 6 if prefs.length == "short" else 4
    for index, (on_day, values) in enumerate(sorted(grouped.items())):
        clocks = [_clock(value) for value in sorted(values)]
        rows = [" • ".join(clocks[i:i + count]) for i in range(0, len(clocks), count)]
        parts.append(("📅 " if light and index == 0 else "") + f"*{_WEEKDAYS[on_day.weekday()].capitalize()}, {on_day:%d/%m/%Y}*\n" + "\n".join(rows))
    if prefs.length == "detailed":
        parts.append(f"_Fuso dos horários: {timezone}._")
    if simulation:
        parts.append("_Simulação: nenhum agendamento foi criado._")
    if ask_selection:
        parts.append(render_prompt("time", prefs))
    return "\n\n".join(parts)


def render_professional_availability(services: Sequence[str], candidates: Sequence[Mapping], preferences=None, *,
                                     timezone: str, day: date | str, simulation: bool = False) -> str:
    """Display queried candidates in the exact ordinal order provided by the core.

    Reject an oversized answer, rather than silently changing selectable rows.
    Prices/durations are supplied per professional; no catalog arithmetic here.
    """
    prefs, zone = _preferences(preferences), ZoneInfo(timezone)
    names = _labels(services, 12)
    target = date.fromisoformat(day) if isinstance(day, str) else day
    if not names or type(target) is not date or type(simulation) is not bool:
        raise ValueError("Verified services, local day and simulation state required")
    if not isinstance(candidates, (tuple, list)) or any(not isinstance(row, Mapping) for row in candidates):
        raise ValueError("Verified professional candidates required")
    if any(not isinstance(row.get("slots"), (tuple, list)) for row in candidates):
        raise ValueError("Missing slot results cannot be presented as no availability")
    shown = [row for row in candidates if row.get("slots")]
    if len(shown) > 4:
        raise ValueError("Narrow the query before presenting more than four professionals")
    if not shown:
        return render_prompt("unavailable", prefs)
    light = prefs.emoji == "light"
    parts = ["*Profissionais com horários nesta consulta*",
             _field("Serviço" if len(names) == 1 else "Serviços", " e ".join(_label(x) for x in names),
                    light=light, icon=service_icon(names)),
             _field("Data", f"{_WEEKDAYS[target.weekday()]}, {target:%d/%m/%Y}", light=light)]
    seen = set()
    for index, row in enumerate(shown, 1):
        identity = str(row["id"]) if row.get("id") is not None and type(row["id"]) is not bool else ""
        if not identity or identity in seen:
            raise ValueError("Distinct verified professional IDs required")
        seen.add(identity)
        name = _label(_text(row.get("name")))
        slots = row["slots"]
        if not isinstance(slots, (tuple, list)) or len(slots) > 8:
            raise ValueError("Bounded verified slots required")
        clocks = []
        for raw in slots:
            value = datetime.fromisoformat(raw.replace("Z", "+00:00")) if isinstance(raw, str) else raw
            if not isinstance(value, datetime) or value.utcoffset() is None or value.second or value.microsecond:
                raise ValueError("Aware minute-precision slots required")
            local = value.astimezone(zone)
            if local.date() != target:
                raise ValueError("Slot outside queried local day")
            clocks.append(_clock(local))
        total = _integer(row.get("price_cents"), 0, 100_000_000)
        minutes = _integer(row.get("duration_min"), 1, 1440)
        block = [f"{index}. " + _field("Profissional", name, light=light),
                 _field("Horário", " • ".join(clocks[:4]), light=light)]
        if len(clocks) > 4:
            block.append(" • ".join(clocks[4:]))
        block.extend([_field("Duração", duration(minutes), light=light),
                      _field("Total", money(total), light=light)])
        parts.append("\n".join(block))
    if simulation:
        parts.append("_Simulação: nenhum agendamento foi criado._")
    parts.append("Qual horário e profissional você prefere?" if len(shown) > 1 else "Qual horário você prefere?")
    answer = "\n\n".join(parts)
    if not fits_message(answer):
        raise ValueError("Narrow the query before displaying an oversized choice list")
    return answer


def _labels(values: Sequence[str], maximum: int = 30, size: int = 120) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence) or len(values) > maximum:
        raise ValueError("Expected a bounded list of factual labels")
    return tuple(_text(value, size) for value in values)


def render_prompt(kind: str, preferences=None, *, services: Sequence[str] = (), professional: str | None = None,
                  day: str | None = None, details: Sequence[str] = ()) -> str:
    """Closed prompts, optionally followed by caller-verified choices or context."""
    prefs = _preferences(preferences)
    prompts = {
        "service": "Qual serviço você tem em mente?",
        "date": "Qual dia funciona melhor para você?",
        "professional": "Você tem preferência por algum profissional?",
        "time": "Qual horário funciona melhor para você?",
        "name": "Como você prefere ser chamado?",
        "adjustment": "O que você quer ajustar no pedido?",
        "ask_adjustment": "O que você quer ajustar no pedido?",
        "confirmation": "Posso confirmar esse agendamento?",
        "confirmation_review": "Vamos conferir o resumo vigente antes de confirmar?",
        "context_changed": "As informações do catálogo mudaram. Vamos conferir sua escolha e os valores atuais antes de continuar?",
        "human_paused": "As respostas automáticas estão pausadas para atendimento da equipe.",
        "availability_service": "Para qual serviço você quer consultar a disponibilidade?",
        "availability_professional": "Com qual profissional você quer consultar os horários?",
        "availability_day": "Você tem algum dia ou período em mente para eu consultar?",
        "unavailable": "Não encontrei horários nessa consulta. Você prefere outro dia ou profissional?",
        "expired": "O prazo deste resumo terminou. Vamos consultar a disponibilidade novamente antes de confirmar?",
        "interpretation_error": "Não consegui entender esse detalhe com segurança. Pode me explicar de outro jeito?",
        "choose_priority": "Qual dessas solicitações você quer resolver primeiro?",
        "no_summary": "Não há um resumo vigente para confirmar. O que você gostaria de preparar?",
    }
    if kind not in prompts:
        raise ValueError("Unknown factual prompt kind")
    text = prompts[kind]
    variants = {
        "service": ("Qual serviço deseja consultar?", "Qual serviço você está procurando?", "Qual serviço prefere?"),
        "date": ("Para qual data deseja consultar a disponibilidade?", "Qual dia fica bom para você?", "Qual dia prefere?"),
        "professional": ("Há um profissional de sua preferência?", "Com quem você prefere agendar?", "Qual profissional prefere?"),
        "time": ("Qual dos horários consultados prefere?", "Qual desses horários fica bom para você?", "Qual horário prefere?"),
        "name": ("Como prefere que eu me dirija a você?", "Como posso chamar você?", "Como prefere ser chamado?"),
        "adjustment": ("Qual detalhe deseja ajustar no pedido?", "O que você quer mudar no pedido?", "O que deseja ajustar?"),
        "ask_adjustment": ("Qual detalhe deseja ajustar no pedido?", "O que você quer mudar no pedido?", "O que deseja ajustar?"),
        "confirmation": ("Posso confirmar esse agendamento?", "Posso confirmar esse agendamento?", "Posso confirmar esse agendamento?"),
        "confirmation_review": ("Podemos revisar o resumo vigente antes da confirmação?", "Vamos olhar o resumo atual antes de confirmar?", "Vamos revisar o resumo atual antes de confirmar?"),
        "availability_service": ("Para qual serviço deseja consultar a disponibilidade?", "Para qual serviço você quer ver os horários?", "Qual serviço deseja consultar?"),
        "availability_professional": ("Com qual profissional deseja consultar os horários?", "Com quem você quer ver os horários?", "Qual profissional deseja consultar?"),
        "availability_day": ("Qual data ou período deseja consultar?", "Quer ver os horários de qual dia ou período?", "Qual dia ou período deseja consultar?"),
        "interpretation_error": ("Não identifiquei esse detalhe com segurança. Pode reformular?", "Não entendi esse detalhe com segurança. Pode explicar de outro jeito?", "Pode explicar esse detalhe de outra forma? Não consegui identificá-lo com segurança."),
        "choose_priority": ("Qual solicitação deseja resolver primeiro?", "O que você quer resolver primeiro?", "Qual solicitação vem primeiro?"),
    }
    if kind in variants and prefs.style != "warm":
        text = variants[kind][("professional", "relaxed", "direct").index(prefs.style)]
    if prefs.formality == "formal" and kind == "name":
        text = "Como prefere que eu me dirija a você?"
    names, facts = _labels(services, 12), list(_labels(details, 30, 1200))
    context = []
    if names:
        context.append(_field("Serviço" if len(names) == 1 else "Serviços", " e ".join(_label(value) for value in names),
                              light=prefs.emoji == "light", icon=service_icon(names)))
    if professional is not None:
        context.append(_field("Profissional", _label(_text(professional)), light=prefs.emoji == "light"))
    if day is not None:
        context.append(_field("Dia consultado", _label(_text(day)), light=prefs.emoji == "light"))
    if facts:
        context.append("\n".join("• " + _label(fact) for fact in facts))
    return "\n\n".join(part for part in ("\n".join(context), text) if part)


def render_message(text: str, preferences=None, *, kind: str = "message") -> str:
    """Conservative final pass for legacy text, not an intent or fact rewriter.

    Only known control prompts are changed, and only in matching conversation
    steps. Informational/FAQ text and identifiers remain literal. Typed renderers
    remain responsible for grouping slots and choosing an operation outcome.
    """
    prefs = _preferences(preferences)
    if not isinstance(text, str) or not isinstance(kind, str):
        raise ValueError("Message text and its core-owned kind are required")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    controls = {"confirm", "reschedule_confirm", "professional", "time", "menu", "service", "human", "expired",
                "cancelled", "unknown", "reschedule_pick", "reschedule_time", "reschedule_day", "not_available"}
    if kind in controls:
        confirmation = render_prompt("confirmation", prefs)
        replacements = {
            "Posso confirmar esse resumo? Responda SIM ou NÃO.": confirmation,
            "Posso confirmar? Responda SIM ou NÃO.": confirmation,
            "Para confirmar este resumo, responda SIM. Para desistir, NÃO.": confirmation,
            "O pedido ainda não foi confirmado. Para concluir, responda SIM; para desistir, NÃO.":
                "O pedido ainda não foi confirmado.\n\n" + confirmation,
            "A reserva original permanece. Confirme a remarcação com SIM ou desista com NÃO.":
                "A reserva original permanece.\n\n" + confirmation,
            "Para concluir, responda SIM ou NÃO.": confirmation,
            "Responda SIM ou NÃO.": confirmation,
            "Responda com o número do agendamento que deseja remarcar.": "Qual agendamento você quer remarcar?",
            "Escolha um dos horários disponíveis pelo número ou horário (ex.: 14:00).": render_prompt("time", prefs),
            "Escolha um horário listado, pelo número ou pelo horário (ex.: 14:00).": render_prompt("time", prefs),
            "Qual profissional prefere? Pode responder pelo nome ou número indicado.": render_prompt("professional", prefs),
            "Escolha o número de um serviço listado.": render_prompt("service", prefs),
            "Pode dizer o nome ou número:": "Qual opção você prefere?",
            "escolha o profissional pelo nome ou número:": "qual profissional você prefere?",
            "Para retomar o bot, envie MENU.": "",
            "Para retomar o assistente, envie MENU.": "",
            "Envie MENU para preparar um novo pedido.": "",
            "Escreva MENU para começar novamente.": "",
            "Escreva MENU para tentar novamente.": "",
            "Escreva MENU para recomeçar.": "",
        }
        for old, replacement in replacements.items():
            text = re.sub(re.escape(old).replace(r"\ ", r"\s+"), lambda _: replacement, text)
        if kind in {"professional", "menu", "service", "reschedule_pick"}:
            text = re.sub(r"(?m)^\s*\d{1,2}[.)]\s+(?=\S)", "• ", text)
    # Known field layout is shared by typed and legacy paths. Never inspect prose
    # to infer an outcome or append/deduplicate a confirmation decision.
    label_pattern = r"(?m)^(?:(📋|✂️|🪒|💅|🐾|🦷|👤|📅|🕒|⏳|💰|🔖) )?\*?(Serviços?|Profissional|Data e horário|Data|Dia consultado|Horário|Duração(?: estimada)?|Total|Protocolo):\*?[ \t]+([^\n]+)$"
    def field(match):
        existing_icon, label, value = match.groups()
        icon = existing_icon
        if not icon and label in {"Serviço", "Serviços"}:
            icon = service_icon((value,)) if len(value) <= 120 else "📋"
        return _field(label, value, light=prefs.emoji == "light", icon=icon)
    if kind not in {"information", "commerce_information", "faq", "price"}:
        text = re.sub(label_pattern, field, text)
    headings = {"audio_error": ("🎙️", "Sobre o áudio"), "audio": ("🎙️", "Sobre o áudio"),
                "error": ("ℹ️", "Não consegui concluir a consulta"), "temporary": ("ℹ️", "Consulta indisponível agora"),
                "human": ("👤", "Atendimento da equipe"), "reminder": ("📅", "Lembrete do agendamento")}
    if kind in headings and text and not re.match(r"^[^\n*]{0,8}\*", text):
        icon, heading = headings[kind]
        text = (icon + " " if prefs.emoji == "light" else "") + f"*{heading}*\n\n" + text
    return re.sub(r"\n{3,}", "\n\n", "\n".join(line.rstrip() for line in text.split("\n"))).strip()


def render_error(kind: str, preferences=None) -> str:
    prefs = _preferences(preferences)
    messages = {
        "temporary": "*Não consegui consultar essa informação agora.*\n\nPodemos tentar novamente em instantes.",
        "unavailable": "*Esse horário não está mais disponível.*\n\nNenhum novo agendamento foi confirmado.",
        "expired": "*O prazo para confirmar esse resumo terminou.*\n\nÉ preciso conferir a disponibilidade novamente.",
        "uncertain": "*O resultado da tentativa ainda não foi confirmado.*\n\nPrecisamos conferir antes de tentar de novo.",
        "invalid_choice": "Não consegui identificar essa opção com segurança. Pode dizer qual opção prefere?",
    }
    if kind not in messages:
        raise ValueError("Unknown verified error kind")
    prefix = _optional("Sinto muito. ", prefs) if prefs.style == "warm" and kind == "temporary" else ""
    return ("ℹ️ " if prefs.emoji == "light" else "") + prefix + messages[kind]


def render_cancel(kind: str = "request_stopped", preferences=None, *, protocol: str | None = None,
                  summary: BookingSummary | None = None) -> str:
    prefs = _preferences(preferences)
    if kind == "request_stopped":
        first = {"warm": "Sem problema, encerrei esta solicitação.",
                 "professional": "Esta solicitação foi encerrada.",
                 "relaxed": "Tudo bem, paramos esta solicitação por aqui.",
                 "direct": "Solicitação encerrada."}[prefs.style]
        return first + "\n\n_Reservas já confirmadas permanecem como estavam._"
    if kind == "reschedule_abandoned":
        return "Tudo bem, a remarcação foi interrompida. O agendamento original permanece."
    if kind == "booking_cancelled" and protocol:
        if summary is not None and not isinstance(summary, BookingSummary):
            raise ValueError("Cancellation details require a verified summary")
        parts = [("✅ " if prefs.emoji == "light" else "") + "*Cancelamento confirmado.*"]
        if summary is not None:
            if summary.protocol and summary.protocol != protocol:
                raise ValueError("Cancellation protocol differs from its summary")
            parts.append("\n".join(_booking_lines(summary, light=prefs.emoji == "light")))
        parts.append(_field("Protocolo", _label(_text(protocol, 80)), light=prefs.emoji == "light"))
        return "\n\n".join(parts)
    raise ValueError("An actual cancellation requires its verified protocol")


def render_handoff(preferences=None) -> str:
    prefs = _preferences(preferences)
    first = "As respostas automáticas estão pausadas para atendimento da equipe."
    if prefs.style in {"warm", "relaxed"}:
        first = "Tudo bem, as respostas automáticas estão pausadas para a equipe continuar por aqui."
    return first


def _commerce_lines(estimate: Mapping, prefs: PresentationPreferences) -> tuple[list[str], bool]:
    if not isinstance(estimate, Mapping) or estimate.get("currency", "BRL") != "BRL":
        raise ValueError("A verified BRL estimate is required")
    items = estimate.get("items")
    if not isinstance(items, (list, tuple)) or not 1 <= len(items) <= 30:
        raise ValueError("A commerce summary requires verified items")
    lines, fixed, numeric, exact_lines, minimum_lines = [], True, True, [], []
    unknown_stock = False
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("Invalid commerce item")
        name = _label(_text(item.get("product_name"), 160))
        variant = _label(_text(item["variant_name"], 160)) if item.get("variant_name") else None
        quantity = _integer(item.get("quantity"), 1, 1000)
        kind = item.get("price_kind")
        if kind not in {"fixed", "from", "on_request", "custom"}:
            raise ValueError("Unknown verified commerce price kind")
        fixed &= kind == "fixed"
        numeric &= kind in {"fixed", "from"}
        unit = item.get("unit_price_cents")
        if kind in {"fixed", "from"}:
            _integer(unit, 0, 100_000_000)
            line_price = item.get("total_cents") if kind == "fixed" else None
            minimum = item.get("minimum_total_cents")
            if line_price is not None:
                _integer(line_price, 0, 100_000_000_000)
                if line_price != unit * quantity:
                    raise ValueError("Inconsistent verified line total")
            if minimum is not None:
                _integer(minimum, 0, 100_000_000_000)
                if minimum != unit * quantity:
                    raise ValueError("Inconsistent verified minimum")
            exact_lines.append(line_price)
            minimum_lines.append(minimum if minimum is not None else line_price)
            price = ("a partir de " if kind == "from" else "") + money(unit) + " cada"
            if kind == "fixed" and line_price is not None:
                price += " · subtotal *" + money(line_price) + "*"
        else:
            if unit is not None or item.get("total_cents") is not None:
                raise ValueError("A price under review cannot imply a fixed value")
            price = "valor personalizado após análise" if kind == "custom" else "valor sob consulta"
        title = f"*{name}*" + (f" / {variant}" if variant and variant != name else "")
        block = title + f" · {quantity} un.\n" + price
        attributes = item.get("attributes") or {}
        if not isinstance(attributes, Mapping) or len(attributes) > 12:
            raise ValueError("Invalid verified attributes")
        attribute_labels = [_label(_text(key, 80)) + ": " + _label(_text(value, 80)) for key, value in attributes.items()]
        if prefs.length == "detailed" and attribute_labels:
            block += "\n_" + " · ".join(attribute_labels) + "_"
        stock = item.get("stock") or {}
        if not isinstance(stock, Mapping) or stock.get("mode", "unknown") not in {"unknown", "tracked"}:
            raise ValueError("Invalid verified stock mode")
        unknown_stock |= stock.get("mode", "unknown") != "tracked"
        lines.append(block)
    total, minimum = estimate.get("total_cents"), estimate.get("minimum_total_cents")
    if total is not None:
        _integer(total, 0, 3_000_000_000_000)
        if not fixed or any(value is None for value in exact_lines) or total != sum(exact_lines):
            raise ValueError("An exact total requires consistent fixed server line totals")
        lines.append(_field("Total", money(total), light=prefs.emoji == "light"))
    elif minimum is not None:
        _integer(minimum, 0, 3_000_000_000_000)
        if not numeric or any(value is None for value in minimum_lines) or minimum != sum(minimum_lines):
            raise ValueError("A minimum requires complete server line values")
        lines.append("*Valor mínimo:* " + money(minimum) + "\n_O valor final depende da análise._")
    else:
        lines.append("*Total sob consulta.*\n_Nenhum valor final foi definido._")
    limitation_text = {
        "non_fixed_price": "Há valores que dependem de análise.",
        "unknown_stock": "A disponibilidade de estoque precisa ser consultada.",
        "stale_stock": "A contagem de estoque precisa ser atualizada.",
        "insufficient_stock": "A quantidade consultada não está disponível no estoque informado.",
    }
    limitations = estimate.get("limitations", [])
    if not isinstance(limitations, (tuple, list)) or any(not isinstance(value, str) for value in limitations):
        raise ValueError("Invalid verified limitations")
    notices = list(dict.fromkeys(limitation_text.get(value, "Há uma informação que exige revisão da equipe.") for value in limitations))
    if unknown_stock and "unknown_stock" not in limitations:
        notices.append(limitation_text["unknown_stock"])
    if notices:
        lines.append("_" + " ".join(notices) + "_")
    confirmable = estimate.get("confirmation_requirements_met") is True
    if confirmable and (not fixed or total is None or unknown_stock or limitations):
        raise ValueError("Contradictory confirmation readiness")
    return lines, confirmable


def render_commerce_summary(estimate: Mapping, preferences=None, *, simulation: bool = False) -> str:
    """Display server totals verbatim after consistency checks; never price missing facts."""
    prefs = _preferences(preferences)
    if type(simulation) is not bool:
        raise ValueError("Explicit simulation choice required")
    lines, _ = _commerce_lines(estimate, prefs)
    heading = {"warm": "Sua consulta de produtos", "professional": "Resumo da proposta comercial",
               "relaxed": "Veja sua consulta de produtos", "direct": "Resumo dos produtos"}[prefs.style]
    parts = [f"*{heading}*", *lines]
    if simulation:
        parts.append("_Simulação: nenhum orçamento ou pedido foi criado._")
    return "\n\n".join(parts)


def render_commerce_order(estimate: Mapping, preferences=None, *, state: str = "proposal", order_id: str | None = None) -> str:
    """The caller supplies the actual operation outcome; presentation never performs it."""
    prefs = _preferences(preferences)
    if state not in {"proposal", "confirmed", "cancel_proposal", "cancelled", "preview"}:
        raise ValueError("Unknown verified commerce operation state")
    if order_id is not None:
        order_id = _text(order_id, 80)
    if state in {"confirmed", "cancelled", "cancel_proposal"} and not order_id:
        raise ValueError("An actual or cancellation outcome requires its verified order ID")
    lines, confirmable = _commerce_lines(estimate, prefs)
    headings = {
        "proposal": "Pedido para revisão — ainda não confirmado",
        "confirmed": "Pedido confirmado",
        "cancel_proposal": "Pedido selecionado para cancelamento — ainda não cancelado",
        "cancelled": "Cancelamento do pedido confirmado",
        "preview": "Prévia do pedido — nenhuma operação foi realizada",
    }
    heading = headings[state]
    parts = [("✅ " if prefs.emoji == "light" and state in {"confirmed", "cancelled"} else "") + f"*{heading}*", *lines]
    if order_id and not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", order_id):
        parts.append("*Pedido:* " + _label(order_id))
    if state == "proposal":
        parts.append("Posso confirmar esse pedido?" if confirmable
                     else "_A equipe precisa conferir preço e disponibilidade antes da confirmação._")
    elif state == "cancel_proposal":
        parts.append("Posso cancelar esse pedido?")
    parts.append("_Pagamento não verificado. Nenhuma cobrança foi realizada por este atendimento._")
    return "\n\n".join(parts)
