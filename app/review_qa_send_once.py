"""One real, user-authorized review WhatsApp to the admin demo test contact.

Only a completed, team-verified, unreviewed booking of the consented test
phone is usable. Enqueue through the ordinary central transactional outbox.
Never send twice, including on process restart. Temporarily grant and then
restore test-only contact permission; no general marketing authorization.
"""
import json
import os
from uuid import UUID
from sqlalchemy import text

from app.database import get_session_factory, tenant_transaction
from app.plan_permissions import has_feature
from app.appointment_notifications import (
    central_channel, central_enabled, notification_settings, process_notification)

TENANT = UUID("82552b25-d0c8-5338-8c42-49f11de59e45")
PHONE = "+5521997557364"
KEY = "orion-qa-review-7364-20261030"
SOURCE = "orion_one_time_review_test"
MARKER = "orion_review_qa_7364"


def authorized():
    return (os.getenv("ORION_REVIEW_QA_SEND_7364") == "1"
            and os.getenv("ORION_REVIEW_QA_TEMP_CONSENT") == "1")


async def restore_consent(original):
    """Never overwrite concurrent changes or a customer's subsequent opt-out."""
    if not isinstance(original, dict):
        return "snapshot_missing"
    factory = get_session_factory()
    async with factory() as db:
        async with tenant_transaction(db, TENANT):
            row = (await db.execute(text("""
                SELECT operational_opt_in,marketing_opt_in,source
                FROM contact_consents WHERE tenant_id=:t AND contact_e164=:phone
                FOR UPDATE
            """), {"t": TENANT, "phone": PHONE})).mappings().first()
            if row is None:
                return "already_absent"
            if row["source"] != SOURCE or row["operational_opt_in"] is not True or row["marketing_opt_in"] is not True:
                return "changed_by_another_actor"
            if original.get("missing") is True:
                # A row created only for this test may have become referenced
                # by another record. In that case fail closed to both opt-outs.
                try:
                    async with db.begin_nested():
                        await db.execute(text("""
                            DELETE FROM contact_consents
                            WHERE tenant_id=:t AND contact_e164=:phone AND source=:source
                        """), {"t":TENANT,"phone":PHONE,"source":SOURCE})
                    return "deleted_new_qa_consent"
                except Exception:
                    await db.execute(text("""
                        UPDATE contact_consents SET
                            operational_opt_in=false,marketing_opt_in=false,
                            source='orion_qa_consent_expired',updated_at=clock_timestamp()
                        WHERE tenant_id=:t AND contact_e164=:phone AND source=:source
                    """), {"t":TENANT,"phone":PHONE,"source":SOURCE})
                    return "revoked_qa_consent"
            if not isinstance(original.get("operational"), bool) or not isinstance(original.get("marketing"), bool):
                return "invalid_consent_snapshot"
            restored_source = original.get("source")
            if not isinstance(restored_source,str) or len(restored_source)>40:
                return "invalid_consent_source"
            await db.execute(text("""
                UPDATE contact_consents SET
                  operational_opt_in=:op,marketing_opt_in=:marketing,
                  source=:source,updated_at=clock_timestamp()
                WHERE tenant_id=:t AND contact_e164=:phone AND source=:qa_source
            """), {"t":TENANT,"phone":PHONE,"op":original["operational"],
                   "marketing":original["marketing"],"source":restored_source,
                   "qa_source":SOURCE})
            return "original_consent_restored"


async def prepare():
    """Idempotent and atomic. Never returns customer details or a raw link."""
    if not authorized():
        return "disabled", None, None
    if not central_enabled():
        return "central_disabled", None, None
    admin, channel = central_channel()
    if admin != TENANT or channel is None:
        return "admin_sender_unavailable", None, None
    async with get_session_factory()() as db:
        async with tenant_transaction(db, TENANT):
            await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                             {"key": KEY})
            existing = (await db.execute(text("""
                SELECT id,status,previous FROM appointment_notifications
                WHERE tenant_id=:t AND event_key=:key
                LIMIT 1
            """), {"t":TENANT,"key":KEY})).mappings().first()
            if existing:
                return "already_attempted_never_resend", None, existing["previous"].get("qa_before")
            if not await has_feature(db,TENANT,"notifications.basic"):
                return "plan_not_entitled", None, None
            saved = (await db.execute(text("""
                SELECT * FROM appointment_communication_settings WHERE tenant_id=:t
            """), {"t":TENANT})).mappings().first()
            prefs = notification_settings(saved,True,channel)
            if not prefs or not prefs["enabled"] or not prefs["review"]:
                return "review_disabled", None, None
            # Do not reuse an appointment already reviewed or already notified.
            row = (await db.execute(text("""
                SELECT a.id,a.customer_id,a.updated_at
                FROM appointments a
                JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
                JOIN tenants t ON t.id=a.tenant_id
                WHERE a.tenant_id=:t AND c.whatsapp_e164=:phone
                  AND c.active AND c.deleted_at IS NULL
                  AND t.status='active'
                  AND a.status='completed' AND a.end_at<=clock_timestamp()
                  AND NOT a.customer_access_blocked
                  AND EXISTS(SELECT 1 FROM appointment_history h
                      WHERE h.tenant_id=a.tenant_id AND h.appointment_id=a.id
                        AND h.event='completed' AND h.details->>'source'='panel'
                        AND h.details->'actor'->>'role' IN ('owner','editor','admin','support'))
                  AND NOT EXISTS(SELECT 1 FROM appointment_reviews r
                      WHERE r.tenant_id=a.tenant_id AND r.appointment_id=a.id)
                  AND NOT EXISTS(SELECT 1 FROM appointment_notifications n
                      WHERE n.tenant_id=a.tenant_id AND n.appointment_id=a.id
                        AND n.event='review')
                ORDER BY a.end_at DESC,a.id DESC LIMIT 1 FOR UPDATE OF a
            """), {"t":TENANT,"phone":PHONE})).mappings().first()
            if row is None:
                return "no_completed_unreviewed_test_booking", None, None
            original = (await db.execute(text("""
                SELECT operational_opt_in,marketing_opt_in,source
                FROM contact_consents WHERE tenant_id=:t AND contact_e164=:phone FOR UPDATE
            """), {"t":TENANT,"phone":PHONE})).mappings().first()
            if original and original["source"]=="customer_request" and not (
                    original["operational_opt_in"] and original["marketing_opt_in"]):
                return "customer_opted_out_no_override", None, None
            snapshot = ({"missing":False,
                         "operational":original["operational_opt_in"],
                         "marketing":original["marketing_opt_in"],
                         "source":original["source"]}
                        if original else {"missing":True})
            # The operator explicitly requested ONE evaluation test to this
            # number. Do not persist a permanent marketing opt-in.
            await db.execute(text("""
                INSERT INTO contact_consents
                  (tenant_id,contact_e164,operational_opt_in,marketing_opt_in,source)
                VALUES (:t,:phone,true,true,:source)
                ON CONFLICT (tenant_id,contact_e164) DO UPDATE SET
                  operational_opt_in=true,marketing_opt_in=true,
                  source=:source,updated_at=clock_timestamp()
            """), {"t":TENANT,"phone":PHONE,"source":SOURCE})
            now = (await db.execute(text("SELECT clock_timestamp()"))).scalar_one()
            inserted = (await db.execute(text("""
                INSERT INTO appointment_notifications
                  (tenant_id,appointment_id,appointment_version,event,
                   recipient_kind,recipient_id,channel_id,central_channel_id,
                   settings_version,event_key,previous,due_at,expires_at)
                VALUES (:t,:appt,:version,'review','customer',:customer,NULL,:channel,
                  :settings,:key,CAST(:details AS jsonb),:now,:now+interval '60 minutes')
                ON CONFLICT DO NOTHING RETURNING id
            """), {"t":TENANT,"appt":row["id"],"version":row["updated_at"],
                   "customer":row["customer_id"],"channel":channel,
                   "settings":prefs["version"],"key":KEY,
                   "details":json.dumps({MARKER:True,"qa_before":snapshot}),
                   "now":now})).scalar_one_or_none()
            if inserted is None:
                raise ValueError("qa_notice_collision_rollback")
            return "enqueued", inserted, snapshot


async def run_once(ctx):
    if not authorized():
        return {"state":"disabled"}
    state, notice_id, original = await prepare()
    if notice_id is None:
        # A partial previous attempt must never become a fresh send.
        if original is not None:
            cleanup = await restore_consent(original)
            return {"state":state,"consent_cleanup":cleanup}
        return {"state":state}
    result="unknown"
    try:
        result = await process_notification(ctx,str(TENANT),str(notice_id))
    except Exception:
        result="transport_unknown_never_resend"
    finally:
        cleanup = await restore_consent(original)
    return {"state":"attempted_once", "send_result":result,"consent_cleanup":cleanup}
