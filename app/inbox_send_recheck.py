"""A short, immutable transport proof for one already-reserved QR send.

Only the first authorize writes the server-owned outbound envelope metadata.
Repeated final checks never claim, release, retry, or extend that proof.
"""
from datetime import timedelta
import json
import math

from fastapi import HTTPException
from sqlalchemy import text


KEY = "_qr_send_grant"
PROOF_SECONDS = 30


def refused():
    return HTTPException(409, "A autorização da mensagem mudou durante a preparação")


async def _message(db, identity, row, payload):
    from app.inbox_runtime import fingerprint
    saved = (await db.execute(text("SELECT * FROM inbox_messages WHERE tenant_id=:t AND id=:id FOR UPDATE"),
        {"t": identity.tenant_id, "id": payload.operation_id})).mappings().one_or_none()
    if (not saved or payload.text is None or saved["direction"] != "out" or saved["status"] != "sending"
            or saved["control_id"] != row["id"] or saved["source"] != payload.source
            or saved["body"] != payload.text or saved["fingerprint"] != payload.fingerprint
            or fingerprint(payload.phone, payload.text) != payload.fingerprint
            or saved["control_revision"] != row["revision"]):
        raise refused()
    return saved


def _scope(identity, row):
    return {"v": 1, "channel": str(identity.channel_id), "account": str(identity.owner_account_id),
        "owner": str(identity.lease_owner), "fence": identity.fence, "revision": row["revision"]}


async def _verify(db, identity, row, saved):
    envelope = saved.get("envelope")
    grant = envelope.get(KEY) if isinstance(envelope, dict) else None
    if not isinstance(grant, dict) or set(grant) != {*_scope(identity, row), "expires_at"}:
        raise refused()
    if any(grant.get(key) != value or type(grant.get(key)) is not type(value)
           for key, value in _scope(identity, row).items()):
        raise refused()
    deadline = grant.get("expires_at")
    if type(deadline) not in {float, int} or not math.isfinite(deadline):
        raise refused()
    now = (await db.execute(text("SELECT clock_timestamp()"))).scalar_one()
    if deadline <= now.timestamp() or deadline > now.timestamp() + PROOF_SECONDS:
        raise refused()


async def bind(db, identity, row, payload):
    # Old callers without an exact body retain their first-authorize contract;
    # they cannot acquire the newly negotiated read-only recheck capability.
    if payload.text is None:
        return
    saved = await _message(db, identity, row, payload)
    if isinstance(saved.get("envelope"), dict) and KEY in saved["envelope"]:
        await _verify(db, identity, row, saved)
        return
    now = (await db.execute(text("SELECT clock_timestamp()"))).scalar_one()
    value = {**_scope(identity, row), "expires_at": (now + timedelta(seconds=PROOF_SECONDS)).timestamp()}
    await db.execute(text("""UPDATE inbox_messages SET envelope=envelope||CAST(:grant AS jsonb)
        WHERE tenant_id=:t AND id=:id AND direction='out' AND status='sending'"""),
        {"t": identity.tenant_id, "id": payload.operation_id, "grant": json.dumps({KEY: value})})


async def recheck(db, identity, row, payload):
    if row["pending_operation"] != payload.operation_id or row["pending_fingerprint"] != payload.fingerprint:
        raise refused()
    await _verify(db, identity, row, await _message(db, identity, row, payload))


async def automatic_current(db, identity, payload):
    """Re-read the real producer's current TTL, consent and activity facts."""
    job = (await db.execute(text("""SELECT * FROM automation_jobs WHERE tenant_id=:t AND id=:id
        AND channel_id=:channel AND status='sending'"""),
        {"t": identity.tenant_id, "id": payload.operation_id, "channel": identity.channel_id})).mappings().one_or_none()
    if job:
        from app.automation_runtime import execution_enabled, job_reason, settings_for
        if (not execution_enabled() or job["contact_e164"] != "+"+payload.phone or job["body"] != payload.text
                or await job_reason(db, identity.tenant_id, dict(job), await settings_for(db, identity.tenant_id))):
            raise refused()
        return
    notice = (await db.execute(text("""SELECT * FROM appointment_notifications WHERE tenant_id=:t AND id=:id
        AND channel_id=:channel AND status='sending'"""),
        {"t": identity.tenant_id, "id": payload.operation_id, "channel": identity.channel_id})).mappings().one_or_none()
    if not notice:
        raise refused()
    from app.appointment_notifications import cipher, eligibility
    reason, _, phone, _ = await eligibility(db, identity.tenant_id, dict(notice))
    try:
        original = cipher().decrypt(notice["body_encrypted"].encode()).decode()
    except Exception:
        raise refused() from None
    if reason or phone != "+"+payload.phone or original != payload.text:
        raise refused()
