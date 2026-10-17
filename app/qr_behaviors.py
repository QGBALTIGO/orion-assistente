"""Deterministic, testable control intents: never grant the LLM business authority."""
import re
import unicodedata

def norm(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.casefold())
                   if unicodedata.category(c) != "Mn").strip()

def control_intent(text: str) -> str | None:
    lower = norm(text)
    if any(s in lower for s in ("quero falar com atendente", "atendente humano",
                               "pessoa de verdade", "falar com uma pessoa")):
        return "human"
    if any(s in lower for s in ("remarcar", "trocar meu horario", "mudar meu horario",
                               "adiar meu horario", "reagendar")):
        return "reschedule"
    if any(s in lower for s in ("meus agendamentos", "tenho agendamento",
                               "minha reserva", "meu horario marcado",
                               "quando esta meu horario", "historico de agendamentos")):
        return "my_bookings"
    if any(s in lower for s in ("quero cancelar", "cancelar minha reserva",
                               "desmarcar meu horario")):
        return "cancel_request"
    if re.fullmatch(r"(oi+|ola+|bom dia|boa tarde|boa noite|e ai|eae|opa)[!.,\s]*", lower):
        return "greeting"
    if re.search(r"\b(idiota|burro|imbecil|vai se foder|porra|merda)\b",lower):
        return "hostile"
    return None


def catalog_match(text: str, catalog: list[dict]) -> list:
    lower = norm(text)
    found = []
    # Deterministic fallback only when an exact, active catalog name is quoted.
    # Do not rely on a substring that is another product or an arbitrary synonym.
    for item in catalog:
        name = norm(item["name"])
        if len(name) >= 3 and re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", lower):
            found.append(item["id"])
    return found[:6]
