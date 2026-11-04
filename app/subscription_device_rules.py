"""Deterministic device policy; transport never chooses recipient or content."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def easter(year):
    # Gregorian computus. National holiday dates are deliberately separate
    # from optional federal-office closure days and municipal holidays.
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    n = h + l - 7 * m + 114
    return date(year, n // 31, n % 31 + 1)


def brazil_holiday(day):
    """MGI's national calendar: nine fixed dates plus Good Friday.

    Reference: agenciagov.ebc.com.br/noticias/202512/
    confira-o-calendario-oficial-de-feriados-nacionais-e-pontos-facultativos-em-2026
    Carnival, Corpus Christi and optional administrative closures are excluded.
    """
    return ((day.month, day.day) in {(1, 1), (4, 21), (5, 1), (9, 7), (10, 12),
                                   (11, 2), (11, 15), (11, 20), (12, 25)}
            or day == easter(day.year) - timedelta(days=2))


def schedule_state(schedule, local):
    if schedule["holiday_off"] and brazil_holiday(local.date()):
        return "closed"
    row = next(d for d in schedule["days"] if d["weekday"] == local.weekday())
    clock = local.strftime("%H:%M")
    if not row["enabled"] or not row["start"] <= clock < row["end"]:
        return "closed"
    if row.get("break_start") and row["break_start"] <= clock < row["break_end"]:
        return "break"
    return "open"


def business_state(settings, now=None):
    hours = settings["business_hours"]
    if not hours["enabled"]:
        return "open"
    return schedule_state(hours, (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(settings["timezone"])))


def effects_for_event(settings, event, *, now=None, audio_available=False):
    """Return intentions. The DB reserves idempotency/cooldown before permits."""
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(ZoneInfo(settings["timezone"]))
    kind, phone = event["kind"], event.get("phone")
    if kind not in {"text", "audio", "call"} or kind != "call" and not phone:
        return []
    state = business_state(settings, now)
    hours, calls = settings["business_hours"], settings["calls"]
    effects = []
    if kind == "call":
        reject = (calls["reject_enabled"] and phone not in calls["whitelist"]
                  and (not calls["schedule_enabled"] or schedule_state(calls, local) != "open"))
        if reject:
            effects.append({"type": "reject_call", "purpose": "call_rejection", "payload": {
                "phone": phone, "call_id": event["call_id"], "call_from": event["call_from"]}})
            if phone and calls["reply_enabled"] and calls["reply_message"].strip():
                effects.append({"type": "send_text", "purpose": "call_reply", "cooldown": True,
                                "payload": {"phone": phone, "text": calls["reply_message"]}})
    elif state != "open":
        message = hours["break_message"] if state == "break" else hours["outside_message"]
        if message.strip():
            effects.append({"type": "send_text", "purpose": "absence", "cooldown": True,
                            "payload": {"phone": phone, "text": message}})
    # The observed hours checkbox silences keyword/menu/chatbot answers.
    # Call rejection and the transcription itself have independent switches.
    # Future matching of the transcript must apply block_replies_outside_hours.
    if kind == "audio" and settings["transcription"]["enabled"] and audio_available:
        effects.append({"type": "transcribe", "purpose": "transcription", "payload": {
            "phone": phone, "message_id": event["message_id"], "max_bytes": 25000000,
            "max_seconds": settings["transcription"]["max_minutes"] * 60}})
    return effects


def transcription_parts(settings, event, transcript):
    if not isinstance(transcript, str) or not transcript.strip() or len(transcript) > 120000:
        raise ValueError("Transcrição indisponível ou acima do limite")
    config = settings["transcription"]
    rendered = config["template"].format(name=event.get("name") or event["phone"],
                                         transcript=transcript, footer=config["footer"]).strip()
    # Only this module uses long messages; the legacy send limit is unchanged.
    # Every part is independently ledgered and waits for its accepted predecessor.
    parts, part, size = [], [], 0
    for char in rendered:
        units = 2 if ord(char) > 0xFFFF else 1
        if size + units > 32000:
            parts.append("".join(part))
            part, size = [], 0
        part.append(char)
        size += units
    if part:
        parts.append("".join(part))
    return parts
