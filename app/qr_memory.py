"""Bounded conversational facts; never an authorization or appointment ledger."""
from __future__ import annotations

import re
import math
from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from app.customer_names import useful_name
from app.qr_behaviors import norm


TERMINAL_ACTIONS = frozenset({"confirmed", "rescheduled", "cancelled", "commerce_confirmed", "commerce_cancelled"})
OMITTED_PERSONAL_DATA = "[Dados pessoais desnecessários omitidos do contexto.]"


def privacy_input_kind(body: str) -> str | None:
    """Minimize explicit personal data; this is not a general PII detector."""
    text = norm(body)
    if re.search(r"\b(?:meu|minha)\s+(?:senha|password|token|chave de acesso|chave (?:da )?api|api[_ -]?key|pin|cvv|cvc)\s*(?::|=|e\b)|"
                 r"^\s*(?:senha|password|token|api[_ -]?key|cvv|cvc)\s*[:=]", text):
        return "credential"
    if (re.search(r"(?<!\d)\d{11}(?!\d)|(?<!\d)\d{14}(?!\d)|(?<!\d)\d{16}(?!\d)", text)
            or re.search(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b|\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b", text)
            or re.search(r"\b(?:meu|minha)\s+(?:cpf|cnpj|documento|identidade|cartao)\s*(?::|=|e\b)", text)):
        return "document"
    if (re.search(r"\b(?:nasci|nascimento|meu aniversario|minha data de aniversario)\b", text)
            and re.search(r"\d", text)):
        return "birth_date"
    if re.search(r"\b(?:sinto|tenho|estou com)\s+(?:(?:muita|forte|uma)\s+)?(?:dor|sangramento|febre)\b|"
                 r"\bmeu(?:s)?\s+(?:diagnostico|laudo|prontuario|exames?)\b|"
                 r"\b(?:estou gravida|sou diabetico|sou diabetica)\b", text):
        return "health"
    return None


def clean_history(values) -> list[dict]:
    if not isinstance(values, list):
        return []
    result, birthday_prompt = [], False
    for item in values:
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"} or not isinstance(item.get("content"), str):
            continue
        content = item["content"][:1500]
        birthday_data = "aniversario" in norm(content) and re.search(r"\b\d{1,2}[/.-]\d{1,2}(?:[/.-]\d{2,4})?\b", content)
        sensitive = privacy_input_kind(content) or birthday_data or (birthday_prompt and item["role"] == "user"
            and re.fullmatch(r"\s*\d{1,2}[/.-]\d{1,2}(?:[/.-]\d{2,4})?\s*", content))
        result.append({"role": item["role"], "content": OMITTED_PERSONAL_DATA if sensitive else content})
        birthday_prompt = item["role"] == "assistant" and "aniversario" in norm(content)
    return result[-8:]


def history_exchange(body: str, answer: str, step: str) -> list[dict]:
    if step == "birthday_preference":
        return [{"role": "user", "content": OMITTED_PERSONAL_DATA},
                {"role": "assistant", "content": "Preferência de aniversário tratada sem reter a data nesta conversa."}]
    return clean_history([{"role": "user", "content": body}, {"role": "assistant", "content": answer}])


def retain_dialogue(previous: dict, result: dict, *, now: float) -> dict:
    """A finished task retains dialogue, not its hold, snapshot or consent."""
    state = {"step": "menu"}
    if name := useful_name(previous.get("customer_name")):
        state["customer_name"] = name
    if isinstance(previous.get("history"), list):
        state["history"] = clean_history(previous["history"])
    old = previous.get("completed_task")
    if (isinstance(old, dict) and old.get("kind") in TERMINAL_ACTIONS
            and type(old.get("occurred_at")) in {int, float}
            and math.isfinite(old["occurred_at"]) and 0 <= old["occurred_at"] <= now):
        action = {"kind": old["kind"], "occurred_at": old["occurred_at"]}
        if isinstance(old.get("protocol"), str) and re.fullmatch(r"[a-zA-Z0-9._:-]{1,80}", old["protocol"]):
            action["protocol"] = old["protocol"]
        state["completed_task"] = action
    if result.get("_verified_operation") is True and result.get("step") in TERMINAL_ACTIONS:
        action = {"kind": result["step"], "occurred_at": now}
        protocol = result.get("protocol")
        if isinstance(protocol, str) and re.fullmatch(r"[a-zA-Z0-9._:-]{1,80}", protocol):
            action["protocol"] = protocol
        state["completed_task"] = action
    return state


def customer_history_intent(body: str) -> str | None:
    """Select a read-only history tool, not a service or a critical action."""
    text = norm(body)
    if re.search(r"\b(?:ja fui(?: ai)? hoje|acabei de (?:fazer|ser atendid[oa])|ja (?:fiz|cortei)(?:\s+\w+){0,5}\s+hoje)\b", text):
        return "completed_then_booking" if re.search(r"\b(?:agendar|marcar|reservar)\b", text) else "completed"
    historical = bool(re.search(r"\b(?:de sempre|ultima vez|ultimo atendimento|ultimo servico|ultimo corte|o que (?:eu )?fiz)\b", text))
    if not historical:
        return None
    if re.search(r"\b(?:nao|nem|sem)\b", text):
        return None
    if re.search(r"\b(?:quero|queria|gostaria|agendar|marcar|reservar|repetir|repete|novamente)\b", text):
        return "repeat"
    return "completed"


def changed_subject(body: str) -> bool:
    text = norm(body)
    return bool(not re.search(r"\b(?:nao|nem)\s+(?:esquece|esqueca|muda|mude)\b", text)
        and re.search(r"\b(?:esquece|esqueca|deixa|deixe)\s+(?:isso|esse pedido|este pedido)\b|"
                      r"\b(?:mudar|muda|mude) de assunto\b|\bqueria perguntar outra coisa\b", text))


def booking_after_completion(body: str) -> str | None:
    # Do not select a service merely mentioned as part of the completed visit.
    match = re.search(r"\b(?:(?:e|mas|agora)\s+)+(?:quero|queria|gostaria|preciso)\b", body, re.IGNORECASE)
    if match:
        return body[match.start():]
    return None


def factual_choices(state: dict, catalog: list[dict], professionals: list[dict], *, today: date, timezone: str) -> dict:
    """Hints for explicit references only; missing words grant no authority."""
    result = {}
    try:
        day = date.fromisoformat(state.get("day", ""))
        if day >= today:
            result["requested_date"] = day.isoformat()
    except (ValueError, TypeError):
        pass
    person = state.get("professional")
    if isinstance(person, str) and person in {str(row["id"]) for row in professionals}:
        try:
            result["professional_id"] = str(UUID(person))
        except ValueError:
            pass
    clock = state.get("pending_clock")
    if not clock and result.get("requested_date") and state.get("selected_start"):
        try:
            value = datetime.fromisoformat(state["selected_start"])
            if value.tzinfo is not None:
                value = value.astimezone(ZoneInfo(timezone))
                if value.date().isoformat() == result["requested_date"]:
                    clock = value.strftime("%H:%M")
        except (ValueError, TypeError):
            pass
    if isinstance(clock, str) and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", clock):
        relation = state.get("pending_relation", "exact")
        if relation in {"exact", "after", "before", "around"}:
            result["requested_time"] = clock
            result["time_relation"] = relation
    return result
