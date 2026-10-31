"""Read-only verification for the already-attempted 7364 evaluation QA.

Checks the original provider operation receipt without re-sending, decrypts the
existing body in memory to verify its actual individual access token, and emits
only safe status flags. Never prints the link or customer details.
"""
import os
import re
from sqlalchemy import text

from app.database import get_session_factory,tenant_transaction
from app.appointment_notifications import reconcile_notification,cipher
from app import appointment_access as access
from app.review_qa_send_once import TENANT,PHONE,KEY

LINK = re.compile(r"https://orionagenda\.pro/a#([0-9a-f-]{36}\.[A-Za-z0-9_-]{43})")


async def inspect_once(ctx):
    if os.getenv("ORION_REVIEW_QA_VERIFY_7364") != "1":
        return {"state":"disabled"}
    factory=get_session_factory()
    async with factory() as db:
        async with tenant_transaction(db,TENANT):
            job=(await db.execute(text("""
                SELECT id,status,body_encrypted,appointment_id
                FROM appointment_notifications
                WHERE tenant_id=:t AND event_key=:key AND event='review'
            """),{"t":TENANT,"key":KEY})).mappings().first()
    if job is None:
        return {"state":"notice_not_found"}
    # External QR read-only receipt reconciliation; NEVER re-send the original.
    try:
        observed=await reconcile_notification(ctx,str(TENANT),str(job["id"]))
    except Exception:
        observed="unavailable"
    result={"state":"inspected","receipt":observed if observed in
            {"accepted","delivered","read","unknown","unavailable"} else "unknown",
            "contains_review_link":False,
            "link_token_valid":False,
            "rating_form_available":False,
            "consent_automatically_revoked":False,
            "published_review_message":False}
    raw=job.get("body_encrypted")
    if not isinstance(raw,str):
        return result
    try:
        body=cipher().decrypt(raw.encode()).decode("utf-8")
        match=LINK.search(body)
        result["published_review_message"]=(
            body.startswith("🧪 *TESTE ORION — AVALIAÇÃO*")
            and "⭐ *Que tal avaliar sua experiência?*" in body
            and "{{" not in body and "}}" not in body)
        if match is None:
            return result
        result["contains_review_link"]=True
        link_tenant,digest=access.parse_token(match.group(1))
        if link_tenant!=TENANT:
            return result
        async with factory() as db:
            async with tenant_transaction(db,TENANT):
                identity=await access.verify(db,TENANT,digest)
                result["link_token_valid"]=identity.appointment_id==job["appointment_id"]
                if not result["link_token_valid"]:
                    return result
                status=(await db.execute(text("""
                    SELECT a.status,
                        EXISTS(SELECT 1 FROM appointment_reviews r
                               WHERE r.tenant_id=a.tenant_id
                                 AND r.appointment_id=a.id) AS reviewed,
                        EXISTS(SELECT 1 FROM appointment_history h
                               WHERE h.tenant_id=a.tenant_id
                                 AND h.appointment_id=a.id
                                 AND h.event='completed'
                                 AND h.details->>'source'='panel'
                                 AND h.details->'actor'->>'role'
                                     IN ('owner','editor','admin','support')) AS completed_by_staff
                    FROM appointments a JOIN customers c
                      ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
                    WHERE a.tenant_id=:t AND a.id=:id
                      AND c.whatsapp_e164=:phone
                """),{"t":TENANT,"id":job["appointment_id"],"phone":PHONE})).mappings().first()
                result["rating_form_available"]=bool(
                    status and status["status"]=="completed" and
                    status["completed_by_staff"] and not status["reviewed"])
                consent=(await db.execute(text("""
                    SELECT operational_opt_in,marketing_opt_in FROM contact_consents
                    WHERE tenant_id=:t AND contact_e164=:phone
                """),{"t":TENANT,"phone":PHONE})).mappings().first()
                result["consent_automatically_revoked"]=not consent or not (
                    consent["operational_opt_in"] or consent["marketing_opt_in"])
    except Exception:
        # Tokens, JIDs, ciphertext or DB error text must never be exposed.
        pass
    return result
