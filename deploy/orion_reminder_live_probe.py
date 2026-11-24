"""Read-only opt-in production check for one booking and scheduled reminders."""
import asyncio
import json
import os
from time import monotonic

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from sqlalchemy.engine import make_url

PROTOCOL = "AG-H43B9C"
PROBE_VARIABLE = "ORION_REMINDER_RELEASE_PROBE"

BOOKING = """
WITH matched AS (
    SELECT tenant_id, id AS appointment_id FROM appointments WHERE protocol = %(protocol)s
    UNION
    SELECT tenant_id, appointment_id FROM appointment_short_codes WHERE code = %(protocol)s
)
SELECT a.status, t.timezone, a.start_at, a.created_at, a.updated_at,
       (a.start_at AT TIME ZONE 'America/Cuiaba')::text AS starts_in_cuiaba,
       s.enabled AS communication_enabled, s.reminder AS reminder_enabled,
       consent.operational_opt_in AS contact_permission,
       scoped.operational_opt_in AS appointment_permission,
       EXISTS (
           SELECT 1 FROM appointment_history h
           WHERE h.tenant_id=a.tenant_id AND h.appointment_id=a.id AND h.event='services_added'
       ) AS services_were_added,
       COALESCE(events.notifications, '[]'::jsonb) AS notifications
FROM matched m
JOIN appointments a ON a.tenant_id=m.tenant_id AND a.id=m.appointment_id
JOIN tenants t ON t.id=a.tenant_id
JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
LEFT JOIN appointment_communication_settings s ON s.tenant_id=a.tenant_id
LEFT JOIN contact_consents consent ON consent.tenant_id=a.tenant_id AND consent.contact_e164=c.whatsapp_e164
LEFT JOIN appointment_message_consents scoped
    ON scoped.tenant_id=a.tenant_id AND scoped.appointment_id=a.id
       AND scoped.customer_id=a.customer_id AND scoped.contact_e164=c.whatsapp_e164
LEFT JOIN LATERAL (
    SELECT jsonb_agg(jsonb_build_object(
        'event', n.event, 'recipient', n.recipient_kind, 'status', n.status,
        'error', n.last_error, 'attempts', n.attempts, 'due_at', n.due_at,
        'expires_at', n.expires_at, 'attempted_at', n.attempted_at,
        'appointment_version_current', n.appointment_version=a.updated_at,
        'provider_id_present', n.provider_id IS NOT NULL
    ) ORDER BY n.created_at, n.event) AS notifications
    FROM appointment_notifications n
    WHERE n.tenant_id=a.tenant_id AND n.appointment_id=a.id
) events ON true
LIMIT 2
"""

CANDIDATES = """
SELECT n.id, n.due_at
FROM appointment_notifications n
JOIN appointments a ON a.tenant_id=n.tenant_id AND a.id=n.appointment_id
WHERE n.event='reminder' AND n.status='pending' AND a.status='confirmed'
    AND n.appointment_version=a.updated_at AND n.due_at >= now()
    AND n.due_at <= now() + interval '23 minutes' AND n.expires_at > now()
ORDER BY n.due_at, n.id LIMIT 3
"""

RECEIPTS = """
SELECT status, last_error, attempts, attempted_at, updated_at, provider_id IS NOT NULL AS provider_id_present
FROM appointment_notifications
WHERE event='reminder' AND id=ANY(%s)
ORDER BY due_at, id
"""


def _log(state, **facts):
    print("orion_reminder_live_probe " + json.dumps({"state": state, **facts}, default=str), flush=True)


async def _observe(db):
    rows = await (await db.execute(BOOKING, {"protocol": PROTOCOL})).fetchall()
    if len(rows) == 1:
        _log("case", protocol=PROTOCOL, booking=rows[0])
    else:
        _log("case_lookup", protocol=PROTOCOL, match_count=len(rows))
    candidates = await (await db.execute(CANDIDATES)).fetchall()
    if not candidates:
        _log("no_reminder_due_soon")
        return
    _log("watching_scheduled_reminders", due_times=[row["due_at"] for row in candidates])
    ids = [row["id"] for row in candidates]
    deadline = monotonic() + 25 * 60
    previous = None
    while monotonic() < deadline:
        receipts = await (await db.execute(RECEIPTS, (ids,))).fetchall()
        current = [(row["status"], row["attempts"], row["last_error"]) for row in receipts]
        if current != previous:
            _log("reminder_progress", events=receipts)
            previous = current
        if any(row["status"] in {"delivered", "read"} and row["attempted_at"] for row in receipts):
            _log("real_reminder_delivered")
            return
        if receipts and all(row["status"] in {"cancelled", "failed"} for row in receipts):
            _log("reminder_terminal_without_delivery")
            return
        await asyncio.sleep(5)
    _log("receipt_window_elapsed")


async def _run():
    try:
        raw = os.environ["MIGRATION_DATABASE_URL"]
        url = make_url(raw).set(drivername="postgresql").render_as_string(hide_password=False)
        async with await AsyncConnection.connect(
            url, autocommit=True, row_factory=dict_row, connect_timeout=6,
            options="-c default_transaction_read_only=on -c statement_timeout=6000",
        ) as db:
            await _observe(db)
    except Exception as exc:
        _log("probe_error", kind=type(exc).__name__, sqlstate=getattr(exc, "sqlstate", None))


def start(ctx):
    if os.getenv(PROBE_VARIABLE) == PROTOCOL:
        ctx["orion_reminder_probe_task"] = asyncio.create_task(_run())
