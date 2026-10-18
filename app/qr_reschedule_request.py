"""Separate an explicitly identified old appointment from its new wishes.

This module only slices the original utterance. It neither identifies a booking
nor grants permission to change one; authenticated current records do that.
"""
import re
import unicodedata
from dataclasses import dataclass


def _norm(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", value.casefold())
                   if unicodedata.category(c) != "Mn")


_DAY = r"(?:segunda|terca|quarta|quinta|sexta|sabado|domingo)(?:-feira)?|hoje|amanha|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}(?:/\d{4})?|dia\s*\d{1,2}"
_NUMBER = r"(?:\d{1,2}|uma?|duas?|dois|tres|quatro|cinco|seis|sete|oito|nove|dez|onze|doze|treze|catorze|quatorze|quinze|dezesseis|dezessete|dezoito|dezenove|vinte)"
_CLOCK = _NUMBER + r"(?:(?:[:h][0-5]\d)|h|\s+e\s+mei[ao])?(?:\s+horas?)?"
_TEMPORAL = re.compile(r"\b(?:" + _DAY + r")\b|\b(?:as|das)\s*" + _CLOCK + r"\b|\b\d{1,2}[:h][0-5]\d\b")


@dataclass(frozen=True)
class ScopedReschedule:
    source: str
    destination: str


def split_request(message: str) -> ScopedReschedule | None:
    """Accept explicit positive old→new temporal scopes, never a general 'para'."""
    if not isinstance(message, str) or not message.strip() or len(message) > 1500:
        return None
    lower = _norm(message)
    verb = re.search(r"\b(?:remarcar|reagendar|adiar|(?:mudar|trocar)\s+(?:o\s+|a\s+)?(?:agendamento|reserva|horario))\b", lower)
    if not verb or re.search(r"\b(?:nao|nem|sem)\b", lower[:verb.end()]):
        return None
    from app.qr_dialogue import contextual_response
    if contextual_response(message, awaiting_confirmation=True).reason == "condition_or_hypothesis":
        return None
    if re.search(r"\b(?:politica|como funciona|como (?:eu )?(?:posso )?remarcar)\b", lower):
        return None
    # Conditions and alternative targets remain whole-message ambiguities in the
    # regular planner. A second operation must not disappear behind the split.
    if re.search(r"\b(?:se|caso|talvez|ou|depende|cancelar?|desmarcar|agendar|reservar)\b", lower):
        return None
    for connector in re.finditer(r"\b(?:para|pra|por)\b", message, re.IGNORECASE):
        source, destination = message[:connector.start()].strip(), message[connector.end():].strip()
        left, right = _norm(source), _norm(destination)
        if connector.start() <= verb.end() or not _TEMPORAL.search(left):
            continue
        if not re.match(r"(?:(?:a|o|na|no|as|das|dia|proxima?|depois de)\s*)*(?:" + _DAY + r"|" + _CLOCK + r")\b", right):
            continue
        if re.search(r"\b(?:nao|nem|sem)\b", left):
            return None
        # A local correction in the destination is still parsed by the existing
        # last-correction rules, but a negated target is not a positive request.
        if re.search(r"\b(?:nao|nem|sem)\b", right) and not re.search(r"\bnao\s*[,;]?\s*melhor\b", right):
            return None
        return ScopedReschedule(source, destination)
    return None


def explicit_day_numbers(source: str) -> tuple[int, ...]:
    """Bare day-of-month references constrain known records; invent no month."""
    return tuple(dict.fromkeys(int(value) for value in re.findall(r"\bdia\s*(\d{1,2})(?![\d/-])\b", _norm(source))))


def source_service_subject(source: str) -> str:
    """Return an explicitly named subject, including unknown catalog words.

    The caller must resolve the WHOLE subject against its real catalog. An empty
    result means the speaker identified the original only by date/time/name.
    """
    value = _norm(source)
    verb = re.search(r"\b(?:remarcar|reagendar|adiar|mudar|trocar)\b", value)
    value = value[verb.end():].strip() if verb else value
    boundary = re.search(r"\b(?:com|profissional|barbeir[oa])\b|\b(?:(?:de|da|do|na|no)\s+)?(?:dia\s+)?(?:" + _DAY + r")\b|" + _TEMPORAL.pattern, value)
    subject = value[:boundary.start()].strip() if boundary else value
    subject = re.sub(r"^(?:(?:o|a|os|as|meu|minha)\s+)+", "", subject)
    subject = re.sub(r"^(?:agendamento|reserva|horario)(?:\s+(?:de|do|da)\s+)?", "", subject).strip()
    return re.sub(r"^(?:o|a)\s+", "", subject).strip()
