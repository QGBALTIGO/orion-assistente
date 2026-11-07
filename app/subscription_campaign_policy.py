"""Pure calendar, frozen personalization and recipient projections for campaigns."""
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
import math
from string import Formatter
from zoneinfo import ZoneInfo

from app.subscription_chatbot_rules import render_contact
from app.subscription_chatbot_schemas import clean_text

KINDS = frozenset({"text", "image", "video", "audio", "document", "sticker", "ptv", "location"})
CAMPAIGN_LABELS = {"draft": "Rascunho", "scheduled": "Agendada", "running": "Em andamento",
                   "paused": "Pausada", "completed": "Concluída", "cancelled": "Cancelada"}
RECIPIENT_LABELS = {"pending": "Pendente", "sent": "Enviado", "failed": "Falhou",
                    "uncertain": "Resultado incerto", "cancelled": "Cancelado"}


def local_instant(day, clock, zone):
    """Require an unambiguous wall clock; never guess either side of a DST fold."""
    day = date.fromisoformat(day) if isinstance(day, str) else day
    zone = ZoneInfo(zone) if isinstance(zone, str) else zone
    naive = datetime.combine(day, time.fromisoformat(clock))
    possible = set()
    for fold in (0, 1):
        try:
            utc = naive.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
            if utc.astimezone(zone).replace(tzinfo=None) == naive:
                possible.add(utc)
        except OverflowError:
            raise ValueError("Horário fora do calendário disponível") from None
    if len(possible) != 1:
        raise ValueError("O horário local é inexistente ou ambíguo; escolha outro horário")
    return possible.pop()


def start_at(definition):
    return local_instant(definition["start_date"], definition["start_time"], definition["timezone"])


def window_bounds(definition, day):
    if definition["window_start"] == definition["window_end"]:
        raise ValueError("Janela vazia")
    try:
        end_day = day + timedelta(days=int(definition["window_end"] < definition["window_start"]))
    except OverflowError:
        raise ValueError("Janela fora do calendário disponível") from None
    return (local_instant(day, definition["window_start"], definition["timezone"]),
            local_instant(end_day, definition["window_end"], definition["timezone"]))


def active_window(definition, now):
    if now.tzinfo is None:
        raise ValueError("Relógio UTC obrigatório")
    if now < start_at(definition):
        return None
    local_day = now.astimezone(ZoneInfo(definition["timezone"])).date()
    days = (local_day,) if local_day == date.min else (local_day - timedelta(days=1), local_day)
    for day in days:
        if (day.weekday() + 1) % 7 not in definition["weekdays"]:
            continue
        try:
            begin, end = window_bounds(definition, day)
        except ValueError:
            continue  # A future DST-invalid window is unavailable, not silently shifted.
        if begin <= now < end:
            return begin, end
    return None


def next_window(definition, now):
    now = max(now, start_at(definition))
    if active_window(definition, now):
        return now
    day = now.astimezone(ZoneInfo(definition["timezone"])).date()
    for offset in range(370):
        try:
            candidate = day + timedelta(days=offset)
        except OverflowError:
            break
        if (candidate.weekday() + 1) % 7 not in definition["weekdays"]:
            continue
        try:
            begin, end = window_bounds(definition, candidate)
        except ValueError:
            continue
        if max(begin, now) < end:
            return max(begin, now)
    raise ValueError("Nenhuma janela válida disponível")


def admission_ready(now, tenant_finished, campaign_finished, interval, rolling_count):
    """Spacing begins after the previous sequence finishes, never at claim."""
    return (rolling_count < 10
        and (tenant_finished is None or now >= tenant_finished + timedelta(seconds=6))
        and (campaign_finished is None or now >= campaign_finished + timedelta(seconds=interval)))


def render_text(template, facts):
    parts = []
    for literal, field, spec, conversion in Formatter().parse(template):
        parts.append(literal)
        if field is None:
            continue
        if field not in {"name", "phone"} or spec or conversion:
            raise ValueError("Use apenas {name} e {phone}")
        value = facts.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError("Contato sem dados de personalização")
        parts.append(value)  # Insert once. Braces in names are never interpreted.
    return clean_text("".join(parts), maximum=3500)


def frozen_plan(definition, template, facts):
    if definition["message_type"] == "text":
        return [{"type": "text", "text": render_text(definition["text"], facts)}]
    if not template or template.get("kind") not in KINDS:
        raise ValueError("Template indisponível")
    kind = template["kind"]
    body = render_contact(template.get("body", ""), facts)
    if kind == "text":
        first = {"type": "text", "text": body}
    elif kind == "location":
        location = template.get("location")
        if body or not isinstance(location, dict) or set(location) != {"latitude", "longitude"} or any(
                type(location[key]) not in {int, float} or not math.isfinite(location[key])
                or abs(location[key]) > bound for key, bound in (("latitude", 90), ("longitude", 180))):
            raise ValueError("Localização inválida")
        first = {"type": kind, **deepcopy(location)}
    else:
        if not template.get("asset") or body and kind in {"sticker", "ptv"}:
            raise ValueError("Arquivo ou legenda inválidos")
        if body:
            body = clean_text(body, maximum=1024)
        first = {"type": kind, "asset": deepcopy(template["asset"]),
                 "caption": "" if kind == "audio" else body,
                 "file_name": template.get("file_name") or template["asset"]["file_name"]}
    result = [first]
    if kind == "audio" and body:
        result.append({"type": "text", "text": body})
    followup = template.get("followup_body", "")
    if followup:
        if kind in {"text", "location", "sticker", "ptv"}:
            raise ValueError("Mensagem complementar incompatível")
        result.append({"type": "text", "text": render_contact(followup, facts)})
    return result


def action_text(action):
    if action["type"] == "location":
        return f"Localização: {action['latitude']}, {action['longitude']}"
    return action.get("text") or action.get("caption") or action.get("file_name") or action["type"]


def recipient_state(recipient, parts, now):
    """Project stale work without mutating the SQL ledger on a public GET."""
    if not parts:
        return ("pending" if recipient["status"] == "running" else recipient["status"], recipient.get("error_code"))
    states = []
    for item in parts:
        state = item["status"]
        if state == "running" and item.get("authorized_at") and item["authorized_at"] < now - timedelta(seconds=180):
            state = "uncertain"
        elif state == "pending" and item["expires_at"] <= now:
            state = "not_sent"
        states.append(state)
    if all(state == "accepted" for state in states) and len(parts) == len(recipient["plan"]):
        return "sent", None
    if "uncertain" in states:
        return "uncertain", "MESSAGE_OUTCOME_UNCERTAIN"
    if "running" in states:
        return "pending", None
    failures = [item for item, state in zip(parts, states) if state in {"failed", "not_sent"}]
    if failures:
        code = next((item.get("error_code") for item in failures if item.get("error_code")), "CAMPAIGN_EFFECT_EXPIRED")
        cancelled = not any(state == "accepted" for state in states) and code in {
            "CAMPAIGN_PAUSED", "CAMPAIGN_CANCELLED", "CAMPAIGN_OPTED_OUT", "CAMPAIGN_MEMBER_REMOVED",
            "CAMPAIGN_REVOKED", "CAMPAIGN_CONTEXT_CHANGED", "CAMPAIGN_TEMPLATE_CHANGED"}
        return ("cancelled" if cancelled else "failed"), code
    return "pending", None


def progress(states):
    counts = {key: states.count(key) for key in ("sent", "pending", "failed", "uncertain", "cancelled")}
    total = len(states)
    processed = total - counts["pending"]
    return {"total": total, "processed": processed, **counts,
            "percent": round(processed * 100 / total, 2) if total else 0}
