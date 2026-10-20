"""Pure scheduling and rendering of owner-reviewed facts; never sends messages."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from string import Formatter
from zoneinfo import ZoneInfo

OPERATIONAL_EVENTS = frozenset({"booking_confirmed", "reminder_24h", "reminder_2h", "reminder_1h", "rescheduled", "cancelled"})
MARKETING_EVENTS = frozenset({"inactive_60d", "cancelled_7d", "birthday_7d", "birthday_2d", "loyalty_reward", "after_service", "campaign"})
FIELDS = frozenset({"cliente", "empresa", "data", "horario", "profissional", "servico", "valor", "protocolo", "cupom", "link_agendar", "link_review"})
UNAVAILABLE_AUTOMATIONS = {"vacancy": "waitlist_contract_missing"}


def feature_status():
    """This rollout has no waitlist/preferences/offer-acceptance contract."""
    return {name: {"available": False, "enabled": False, "mode": "draft", "reason": reason}
            for name, reason in UNAVAILABLE_AUTOMATIONS.items()}


def unavailable_requested(preferences):
    rules = (preferences or {}).get("automations", {})
    return bool(isinstance(rules, dict) and rules.get("vacancy_requested") is True)


# Server-owned predicates share the c(customer)/t(tenant) aliases in review and
# the final eligibility check. No fragment is accepted from the caller/model.
AUDIENCE_SQL = {
    "all_active": "true",
    "inactive_customers": "(SELECT max(a.start_at) FROM appointments a WHERE a.tenant_id=c.tenant_id AND a.customer_id=c.id AND a.status='completed') < now()-interval '60 days' AND NOT EXISTS(SELECT 1 FROM appointments f WHERE f.tenant_id=c.tenant_id AND f.customer_id=c.id AND f.status='confirmed' AND f.start_at>now())",
    "birthday_month": "EXTRACT(MONTH FROM c.birth_date)=EXTRACT(MONTH FROM now() AT TIME ZONE t.timezone)",
    "new_customers": "c.created_at>now()-interval '30 days'",
}


def text_units(value):
    return len(value.encode("utf-16-le")) // 2


def render_template(template, facts):
    """Only known scalar placeholders; no attribute access or formatting language."""
    if not isinstance(template, str) or not 1 <= len(template) <= 1500:
        raise ValueError("invalid_template")
    result = []
    for literal, field, spec, conversion in Formatter().parse(template):
        result.append(literal)
        if field is None:
            continue
        if field not in FIELDS or spec or conversion:
            raise ValueError("unsupported_placeholder")
        value = facts.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > 500:
            raise ValueError("missing_template_fact")
        result.append(value)
    body = "".join(result).strip()
    if not body or text_units(body) > 1200:
        raise ValueError("message_too_long")
    return body


def delivery_reason(job, consent, contact, settings, *, now, preferences_version, appointment=None, campaign=None):
    """Return a finite stop reason. Facts are loaded again immediately before claim."""
    if now.tzinfo is None:
        raise ValueError("aware_clock_required")
    if job["event"] in UNAVAILABLE_AUTOMATIONS:
        return "waitlist_contract_missing"
    if job["event"] not in OPERATIONAL_EVENTS | MARKETING_EVENTS:
        return "unknown_event"
    if not settings or not settings.get("enabled"):
        return "execution_paused"
    if job["settings_version"] != settings["version"] or job["preferences_version"] != preferences_version:
        return "configuration_changed"
    if now >= job["expires_at"]:
        return "expired"
    if now < job["due_at"]:
        return "not_due"
    required = "operational_opt_in" if job["event"] in OPERATIONAL_EVENTS else "marketing_opt_in"
    if not consent or not consent.get(required) or consent["updated_at"] != job["consent_version"]:
        return "consent_changed"
    if (contact or {}).get("human_paused") or (contact or {}).get("revision", 0) != job["interaction_revision"]:
        return "conversation_changed"
    if job.get("appointment_id"):
        expected = "completed" if job["event"] == "after_service" else "cancelled" if job["event"] in {"cancelled", "cancelled_7d"} else "confirmed"
        if not appointment or appointment["status"] != expected or appointment["updated_at"] != job["appointment_version"]:
            return "appointment_changed"
        if expected == "confirmed" and appointment["start_at"] <= now:
            return "appointment_started"
    if job.get("campaign_id") and (not campaign or campaign["status"] != "approved"
            or campaign.get("version") != job.get("campaign_version")):
        return "campaign_paused"
    return None


def send_window(now, tz_name):
    """Conservative fixed rollout window, explicitly shown at activation (09–18)."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("aware_clock_required")
    local = now.astimezone(ZoneInfo(tz_name))
    if 9 <= local.hour < 18:
        return now
    next_day = local + timedelta(days=1) if local.hour >= 18 else local
    return next_day.replace(hour=9, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


@dataclass(frozen=True)
class Schedule:
    due_at: datetime
    expires_at: datetime


def schedule_for(event, facts, now, tz_name, recovery_days=60):
    if event in {"reminder_24h", "reminder_2h", "reminder_1h"}:
        if facts.get("status") != "confirmed" or not facts.get("start_at"):
            return None
        hours = {"reminder_24h": 24, "reminder_2h": 2, "reminder_1h": 1}[event]
        due = facts["start_at"] - timedelta(hours=hours)
        expiry = min(due + timedelta(minutes=15), facts["start_at"])
    elif event in {"booking_confirmed", "rescheduled", "cancelled", "after_service", "loyalty_reward"}:
        due = facts.get("occurred_at")
        if due is None:
            return None
        if event == "after_service" and facts.get("status") != "completed":
            return None
        if event == "cancelled" and facts.get("status") != "cancelled":
            return None
        if event in {"booking_confirmed", "rescheduled"} and facts.get("status") != "confirmed":
            return None
        expiry = due + timedelta(hours=24)
    elif event in {"inactive_60d", "cancelled_7d"}:
        if facts.get("has_future_booking") or not facts.get("occurred_at"):
            return None
        due = facts["occurred_at"] + timedelta(days=recovery_days if event == "inactive_60d" else 7)
        expiry = due + timedelta(days=1)
    elif event in {"birthday_7d", "birthday_2d"}:
        birthday = facts.get("birth_date")
        if not birthday or facts.get("has_future_booking"):
            return None
        local = now.astimezone(ZoneInfo(tz_name))
        try:
            upcoming = birthday.replace(year=local.year)
            if upcoming < local.date():
                upcoming = birthday.replace(year=local.year + 1)
        except ValueError:
            return None  # No invented leap-day policy.
        offset = 7 if event == "birthday_7d" else 2
        if (upcoming - local.date()).days != offset:
            return None
        due = local.replace(hour=9, minute=0, second=0, microsecond=0)
        expiry = local.replace(hour=18, minute=0, second=0, microsecond=0)
    elif event == "campaign":
        due = facts.get("scheduled_at") or now
        expiry = due + timedelta(days=1)
    else:
        return None
    due = send_window(due, tz_name)
    return Schedule(due, expiry) if due < expiry and now < expiry else None
