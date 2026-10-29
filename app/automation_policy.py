"""Pure scheduling and rendering of owner-reviewed facts; never sends messages."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from string import Formatter
from zoneinfo import ZoneInfo
import unicodedata

OPERATIONAL_EVENTS = frozenset({"booking_confirmed", "reminder_24h", "reminder_2h", "reminder_1h", "rescheduled", "cancelled"})
MARKETING_EVENTS = frozenset({"inactive_60d", "cancelled_7d", "birthday_7d", "birthday_2d", "loyalty_reward", "after_service", "campaign"})
FIELDS = frozenset({"cliente", "empresa", "data", "horario", "profissional", "servico", "valor", "protocolo", "cupom", "link_agendar", "link_review"})
UNAVAILABLE_AUTOMATIONS = {"vacancy": "waitlist_contract_missing"}


def plan_feature(event):
    """Classify persisted events, independently of navigation or channel flags."""
    if event in OPERATIONAL_EVENTS or event == "after_service":
        return "notifications.basic"
    if event == "campaign":
        return "marketing.campaigns"
    if event == "loyalty_reward":
        return "loyalty.manage"
    if event in MARKETING_EVENTS:
        return "relationships.manage"
    return None


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
    "birthday_month": "coalesce(EXTRACT(MONTH FROM c.birth_date),c.birth_month)=EXTRACT(MONTH FROM now() AT TIME ZONE t.timezone)",
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
        if any(unicodedata.category(c)=='Cc' for c in value):raise ValueError('invalid_template_fact')
        from app.qr_presentation import escape_label
        result.append(value if field in {'link_agendar','link_review'} else escape_label(value))
    body = "".join(result).strip()
    if not body or text_units(body) > 1200:
        raise ValueError("message_too_long")
    return body


def consent_version(consent, event):
    purpose='birthday' if event.startswith('birthday_') else 'operational' if event in OPERATIONAL_EVENTS else 'marketing'
    return consent.get(purpose+'_updated_at',consent.get('updated_at'))


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
    required = 'birthday_opt_in' if job['event'].startswith('birthday_') else "operational_opt_in" if job["event"] in OPERATIONAL_EVENTS else "marketing_opt_in"
    if not consent or not consent.get(required) or consent_version(consent,job['event']) != job["consent_version"]:
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


def send_window(now, tz_name, policy=None, event=None):
    """Conservative fixed rollout window, explicitly shown at activation (09–18)."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("aware_clock_required")
    local = now.astimezone(ZoneInfo(tz_name))
    policy=policy or {}
    prefix='operational' if event in OPERATIONAL_EVENTS else 'marketing'
    opening=policy.get(prefix+'_start_hour',9);closing=policy.get(prefix+'_end_hour',18)
    weekdays=policy.get('weekdays',list(range(7)));holidays=policy.get('holidays',[])
    if not (type(opening) is int and type(closing) is int and 0<=opening<closing<=24):raise ValueError('invalid_send_window')
    def allowed(day):return day.weekday() in weekdays and day.date().isoformat() not in holidays
    if allowed(local) and opening <= local.hour < closing:
        return now
    next_day = local + timedelta(days=1) if local.hour >= closing else local
    for _ in range(370):
        if allowed(next_day):return next_day.replace(hour=opening,minute=0,second=0,microsecond=0).astimezone(timezone.utc)
        next_day+=timedelta(days=1)
    raise ValueError('no_send_window')


@dataclass(frozen=True)
class Schedule:
    due_at: datetime
    expires_at: datetime


def schedule_for(event, facts, now, tz_name, recovery_days=60, policy=None):
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
        if not birthday and facts.get('birth_month') and facts.get('birth_day'):
            from datetime import date
            try:birthday=date(2000,facts['birth_month'],facts['birth_day'])
            except (ValueError,TypeError):return None
        if not birthday or facts.get("has_future_booking"):
            return None
        local = now.astimezone(ZoneInfo(tz_name))
        try:
            upcoming = birthday.replace(year=local.year)
            if upcoming < local.date():
                upcoming = birthday.replace(year=local.year + 1)
        except ValueError:
            mode=(policy or {}).get('birthday_leap_day','skip')
            if birthday.month!=2 or birthday.day!=29 or mode not in {'feb28','mar01'}:return None
            from datetime import date
            upcoming=date(local.year,2,28) if mode=='feb28' else date(local.year,3,1)
            if upcoming<local.date():
                year=local.year+1
                try:upcoming=birthday.replace(year=year)
                except ValueError:upcoming=date(year,2,28) if mode=='feb28' else date(year,3,1)
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
    due = send_window(due, tz_name,policy,event)
    return Schedule(due, expiry) if due < expiry and now < expiry else None


def schedule_explanation(event,facts,now,tz_name,*,policy=None,recovery_days=60,approved_at=None):
    scheduled=schedule_for(event,facts,now,tz_name,recovery_days,policy)
    if scheduled is not None:
        if approved_at is not None and (scheduled.due_at<approved_at or event in {'booking_confirmed','rescheduled','cancelled','after_service','loyalty_reward'} and facts.get('occurred_at',now)<approved_at):
            return None,'before_activation'
        return scheduled,None
    if event.startswith('reminder_') and facts.get('status')=='confirmed' and facts.get('start_at'):
        hours={'reminder_24h':24,'reminder_2h':2,'reminder_1h':1}.get(event)
        if hours:
            due=facts['start_at']-timedelta(hours=hours)
            expiry=min(due+timedelta(minutes=15),facts['start_at'])
            return None,'expired' if now>=expiry else 'quiet_window_expired'
    if event.startswith('birthday_'):
        birthday=facts.get('birth_date')
        birthday_pair=(birthday.month,birthday.day) if birthday else (facts.get('birth_month'),facts.get('birth_day'))
        if birthday_pair==(2,29):return None,'birthday_calendar_policy'
    return None,'not_eligible_or_not_due'
