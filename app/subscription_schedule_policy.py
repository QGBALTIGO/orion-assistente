"""Pure time, personalization and projection rules for scheduled messages."""
from datetime import date, datetime, timedelta, timezone
from string import Formatter
from zoneinfo import ZoneInfo

from app import subscription_campaign_policy as campaigns
from app.subscription_chatbot_schemas import clean_text

local_instant = campaigns.local_instant
progress = campaigns.progress
TERMINAL = frozenset({"completed", "failed", "uncertain", "cancelled"})
LABELS = {"attention": "Necessita de atenção", "queued": "Enfileirado",
          "processing": "Enviando", "upcoming": "Próximo", "past_due": "Pendência passada",
          "completed": "Concluída", "failed": "Falhou", "uncertain": "Resultado incerto",
          "cancelled": "Cancelada"}
RECIPIENT_LABELS = {"pending": "Pendente", "running": "Enviando", "sent": "Enviada",
                    "failed": "Falhou", "uncertain": "Resultado incerto", "cancelled": "Cancelada"}


def start_at(definition):
    return local_instant(definition["start_date"], definition["start_time"], definition["timezone"])


def next_occurrence(definition, now, after_index=0):
    """Next strictly future anchored cycle; never replay intervening cycles.

    Callers pass the series anchor as start_date/start_time. For an invalid
    future local instant, callers retain its date and explicitly expose the
    invalid occurrence instead of inventing a UTC instant.
    """
    if now.tzinfo is None or type(after_index) is not int or after_index < 0:
        raise ValueError("Relógio ou índice inválido")
    index, day = next_local_date(definition, now, after_index)
    try:
        instant = local_instant(day, definition["start_time"], definition["timezone"])
    except ValueError:
        return index, day, None
    return index, day, instant


def next_local_date(definition, now, after_index=0):
    """Date/index used to preserve a future DST-invalid cycle without UTC."""
    anchor = date.fromisoformat(definition["start_date"]) if isinstance(definition["start_date"], str) else definition["start_date"]
    local = now.astimezone(ZoneInfo(definition["timezone"]))
    index = max(after_index + 1, max(0, (local.date() - anchor).days // 30))
    try:
        day = anchor + timedelta(days=30 * index)
        if day < local.date() or day == local.date() and definition["start_time"] <= local.strftime("%H:%M"):
            index += 1
            day = anchor + timedelta(days=30 * index)
        return index, day
    except (OverflowError, ValueError):
        raise ValueError("Ciclo fora do calendário disponível") from None


def estimate_completion(due_at, count, interval):
    if due_at is None or not count:
        return None
    if due_at.tzinfo is None or type(count) is not int or count < 0 or type(interval) is not int:
        raise ValueError("Estimativa inválida")
    try:
        return due_at + timedelta(seconds=count * interval)
    except OverflowError:
        raise ValueError("Estimativa fora do calendário disponível") from None


def render_text(template, facts):
    pieces = []
    for literal, field, spec, conversion in Formatter().parse(template):
        pieces.append(literal)
        if field is None:
            continue
        if field != "name" or spec or conversion:
            raise ValueError("Use apenas {name}")
        value = facts.get("name")
        if not isinstance(value, str) or not value:
            raise ValueError("Contato sem nome de apresentação")
        pieces.append(value)
    return clean_text("".join(pieces), maximum=1000)


def frozen_plan(definition, template, facts):
    if definition["message_type"] == "text":
        return [{"type": "text", "text": render_text(definition["text"], facts)}]
    return campaigns.frozen_plan(definition, template, facts)


def recipient_state(recipient, parts, now):
    state, code = campaigns.recipient_state(recipient, parts, now)
    if code == "CAMPAIGN_EFFECT_EXPIRED":
        code = "SCHEDULE_EFFECT_EXPIRED"
    if state == "failed" and code in {
        "SCHEDULE_CANCELLED", "SCHEDULE_OPTED_OUT", "SCHEDULE_SOURCE_REMOVED",
        "SCHEDULE_REVOKED", "SCHEDULE_CONTEXT_CHANGED", "SCHEDULE_TEMPLATE_CHANGED", "SCHEDULE_SELF_CONTACT",
    } and not any(part.get("status") == "accepted" for part in parts):
        return "cancelled", code
    return state, code


def run_projection(run, now, device_available=True):
    status = run["status"]
    if status in TERMINAL:
        return {"bucket": "history", "active_filter": None, "status_label": LABELS[status],
                "attention_reason": run.get("error_code") if status in {"failed", "uncertain"} else None}
    if status == "running":
        state = "processing" if run.get("effect_running") else "queued"
        return {"bucket": "active", "active_filter": state, "status_label": LABELS[state],
                "attention_reason": None}
    due = run.get("due_at")
    if due is None:
        return {"bucket": "active", "active_filter": "attention", "status_label": LABELS["attention"],
                "attention_reason": run.get("error_code") or "SCHEDULE_RECURRENCE_INVALID"}
    day = run["local_date"]
    if isinstance(day, str):
        day = date.fromisoformat(day)
    today = now.astimezone(ZoneInfo(run["timezone"])).date()
    if day < today and not run.get("first_admitted_at") and not run.get("manual_released_at"):
        return {"bucket": "past_due", "active_filter": None, "status_label": LABELS["past_due"],
                "attention_reason": "SCHEDULE_PAST_DUE"}
    if not device_available or run.get("error_code"):
        return {"bucket": "active", "active_filter": "attention", "status_label": LABELS["attention"],
                "attention_reason": run.get("error_code") or "SCHEDULE_DEVICE_UNAVAILABLE"}
    state = "upcoming" if due > now else "attention"
    return {"bucket": "active", "active_filter": state, "status_label": LABELS[state],
            "attention_reason": "SCHEDULE_DUE" if state == "attention" else None}
