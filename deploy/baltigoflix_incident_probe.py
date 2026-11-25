"""Emit a bounded, private delivery diagnosis without customer data or writes."""
import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from sqlalchemy.engine import make_url

DEVICE = """
SELECT d.tenant_id, d.id, d.generation, d.revision, d.applied_revision, d.status, d.runtime_at,
       d.settings->>'timezone' AS timezone,
       (d.settings#>>'{business_hours,block_replies_outside_hours}')::boolean AS blocks_outside_hours,
       (d.settings#>>'{transcription,enabled}')::boolean AS transcription_enabled,
       (d.settings#>>'{transcription,reply_enabled}')::boolean AS audio_reply_enabled,
       (SELECT count(*) FROM subscription_chatbot_rules r WHERE r.tenant_id=d.tenant_id
          AND r.removed_at IS NULL AND r.enabled AND (r.device_id IS NULL OR r.device_id=d.id)) AS enabled_rules,
       (SELECT revision FROM subscription_chatbot_state s WHERE s.tenant_id=d.tenant_id) AS chatbot_revision
FROM subscription_devices d JOIN tenants t ON t.id=d.tenant_id
WHERE lower(btrim(t.name))='baltigoflix' AND d.removed_at IS NULL LIMIT 2
"""

RECENT = """
SELECT e.occurred_at, e.kind, (e.payload->>'origin'='contact') AS from_contact,
       e.payload ? 'chatbot_revision' AS chatbot_revision_present,
       (e.payload->>'chatbot_revision') = CAST(%(rev)s AS text) AS chatbot_revision_matches,
       r.status AS run, r.error_code AS run_error,
       COALESCE(x.effects, '[]'::jsonb) AS effects
FROM subscription_device_events e
LEFT JOIN subscription_chatbot_runs r ON r.tenant_id=e.tenant_id AND r.source_event_id=e.id
LEFT JOIN LATERAL (
    SELECT jsonb_agg(jsonb_build_object('type', f.type, 'status', f.status,
        'error', f.error_code, 'attempts', f.attempts, 'finished_at', f.finished_at,
        'provider_id_present', f.provider_id IS NOT NULL) ORDER BY f.created_at) AS effects
    FROM subscription_device_effects f WHERE f.tenant_id=e.tenant_id AND f.event_id=e.id
) x ON true
WHERE e.tenant_id=%(tenant)s AND e.device_id=%(device)s AND e.kind IN ('text','audio')
  AND e.occurred_at > now()-interval '36 hours'
ORDER BY e.occurred_at DESC LIMIT 30
"""

COUNTS = """
SELECT type, status, error_code, count(*) AS quantity, max(created_at) AS latest
FROM subscription_device_effects
WHERE tenant_id=%(tenant)s AND device_id=%(device)s
  AND (created_at>now()-interval '36 hours' OR status IN ('pending','running','uncertain'))
GROUP BY type,status,error_code ORDER BY latest DESC LIMIT 24
"""

HTTP_EFFECTS = """
SELECT f.created_at, f.finished_at, f.status, f.error_code, f.attempts,
       f.payload#>>'{action,method}' AS method, f.payload#>>'{action,url}' AS url,
       f.payload#>>'{action,as_file}' AS as_file,
       (f.result->>'http_fingerprint') IS NOT NULL AS has_response,
       r.input_kind, r.origin, (r.chatbot_revision=%(rev)s) AS current_revision
FROM subscription_device_effects f
JOIN subscription_chatbot_runs r ON r.id=f.chatbot_run_id AND r.tenant_id=f.tenant_id
WHERE f.tenant_id=%(tenant)s AND f.device_id=%(device)s
  AND f.type='chatbot_action' AND f.payload#>>'{action,type}'='http'
ORDER BY f.created_at DESC LIMIT 28
"""

FALLBACK = """
SELECT r.created_at, r.status AS run, r.error_code, f.status AS effect, f.attempts,
       f.provider_id IS NOT NULL AS provider_id_present
FROM subscription_chatbot_runs r
LEFT JOIN subscription_device_effects f ON f.tenant_id=r.tenant_id AND f.chatbot_run_id=r.id
WHERE r.tenant_id=%(tenant)s AND r.device_id=%(device)s
  AND r.rule_id='95bc1a3b-45c3-497a-bb75-9e1e81f968fa'
ORDER BY r.created_at DESC LIMIT 24
"""


def _log(state, **fields):
    print("orion_baltigoflix_triage " + json.dumps({"state": state, **fields}, default=str), flush=True)


def _safe_code(code):
    return code if code is None or re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", code) else "other"


async def collect():
    try:
        url = make_url(os.environ["MIGRATION_DATABASE_URL"]).set(drivername="postgresql")
        db = await AsyncConnection.connect(url.render_as_string(hide_password=False), autocommit=True,
            row_factory=dict_row, connect_timeout=6,
            options="-c default_transaction_read_only=on -c statement_timeout=6500")
        async with db:
            devices = await (await db.execute(DEVICE)).fetchall()
            if len(devices) != 1:
                _log("device_count", count=len(devices))
                return
            device = devices[0]
            params = {"tenant": device.pop("tenant_id"), "device": device.pop("id"),
                      "rev": device["chatbot_revision"]}
            device.pop("generation")
            device["snapshot_at"] = datetime.now(timezone.utc)
            _log("device", **device)
            for row in await (await db.execute(COUNTS, params)).fetchall():
                row["error_code"] = _safe_code(row["error_code"])
                _log("effect_count", **row)
            for row in await (await db.execute(RECENT, params)).fetchall():
                row["run_error"] = _safe_code(row["run_error"])
                for item in row["effects"]:
                    item["error"] = _safe_code(item["error"])
                _log("inbound", **row)
            for row in await (await db.execute(HTTP_EFFECTS, params)).fetchall():
                url = row.pop("url")
                target = urlsplit(url or "")
                host = target.hostname
                row["destination_host"] = host if host and re.fullmatch(r"[a-z0-9.-]{1,253}", host) else None
                row["scheme"] = target.scheme if target.scheme in {"http", "https"} else None
                row["has_query"] = bool(target.query)
                row["error_code"] = _safe_code(row["error_code"])
                _log("http_effect", **row)
            for row in await (await db.execute(FALLBACK, params)).fetchall():
                row["error_code"] = _safe_code(row["error_code"])
                _log("fallback", **row)
            _log("complete")
    except Exception as exc:
        _log("error", kind=type(exc).__name__, sqlstate=getattr(exc, "sqlstate", None))
