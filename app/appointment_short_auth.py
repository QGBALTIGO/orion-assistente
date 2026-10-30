"""Short-link OTP proof and professional sessions; no protocol-only access.

Code requests are deliberately indistinguishable for unknown/unauthorized links.
The OTP is stored as an HMAC, not plaintext or an unsalted six-digit digest.
"""
from datetime import timedelta
from uuid import uuid4

from cryptography.fernet import InvalidToken
from fastapi import HTTPException
from sqlalchemy import text

from app import appointment_access as access
from app.appointment_short_links import (
    GENERIC_ERROR, GENERIC_REQUEST, OTP_TTL_SECONDS, SESSION_TTL_SECONDS,
    otp_digest, opaque_token, recipient_hash, token_digest,
)
from app.database import get_session_factory, tenant_transaction

async def target(db, tenant, appointment, role):
    """Return the registered recipient only; never accept a phone from a caller."""
    row = (await db.execute(text("""
        SELECT a.customer_id,a.professional_id,a.customer_access_blocked,
          t.status AS tenant_status,c.whatsapp_e164 AS customer_phone,
          c.active AS customer_active,c.deleted_at AS customer_deleted,
          p.phone_e164 AS professional_phone,p.active AS professional_active,
          s.operational_opt_in,sig.enabled AS communication_enabled,
          sig.channel_id,sig.signature
        FROM appointments a
        JOIN tenants t ON t.id=a.tenant_id
        JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
        JOIN professionals p ON p.tenant_id=a.tenant_id AND p.id=a.professional_id
        LEFT JOIN contact_consents s ON s.tenant_id=c.tenant_id
          AND s.contact_e164=c.whatsapp_e164
        LEFT JOIN appointment_communication_settings sig ON sig.tenant_id=a.tenant_id
        WHERE a.tenant_id=:tenant AND a.id=:appointment
    """), {"tenant": tenant, "appointment": appointment})).mappings().one_or_none()
    if not row or row["tenant_status"] != "active" or not row["communication_enabled"] or not row["channel_id"]:
        return None
    if role == "customer":
        if (row["customer_access_blocked"] or not row["customer_active"]
                or row["customer_deleted"] is not None or row["operational_opt_in"] is not True):
            return None
        phone, person = row["customer_phone"], row["customer_id"]
    else:
        if not row["professional_active"]:
            return None
        phone, person = row["professional_phone"], row["professional_id"]
    if not phone or not access.recipient_allowed(phone):
        return None
    return {"phone": phone, "person": person, "channel_id": row["channel_id"],
            "signature": row["signature"] or "Orion Agenda",
            "professional_id": row["professional_id"]}


async def request_otp(tenant, appointment, role):
    """Persist an encrypted OTP before asynchronous send; constant public reply."""
    if not access.tenant_enabled(tenant):
        return GENERIC_REQUEST
    from app.catalog_api import support_session_factory
    from app.qr_channels import _canonical
    from app.appointment_message_templates import label
    from app.appointment_notifications import cipher
    import secrets

    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            recipient = await target(db, tenant, appointment, role)
            if not recipient:
                return GENERIC_REQUEST
            phone = recipient["phone"]
            async with support_session_factory()() as registry:
                channel, state = await _canonical(registry, tenant)
            if (not channel or state != "ready" or channel["id"] != recipient["channel_id"]
                    or phone.lstrip("+") not in (channel.get("allowed_senders") or [])):
                return GENERIC_REQUEST
            await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k,0))"),
                {"k": "short-otp:" + str(tenant) + ":" + str(appointment) + ":" + role})
            recent = (await db.execute(text("""
                SELECT count(*) FROM appointment_short_challenges
                WHERE tenant_id=:t AND appointment_id=:a AND recipient_kind=:role
                  AND created_at>clock_timestamp()-interval '30 minutes'
            """), {"t": tenant, "a": appointment, "role": role})).scalar_one()
            if recent >= 3:
                return GENERIC_REQUEST
            await db.execute(text("""
                UPDATE appointment_short_challenges SET status='failed',body_encrypted=NULL
                WHERE tenant_id=:t AND appointment_id=:a AND recipient_kind=:role
                  AND status IN ('pending','accepted')
            """), {"t": tenant, "a": appointment, "role": role})
            otp = f"{secrets.randbelow(1_000_000):06d}"
            challenge_id, operation_id = uuid4(), uuid4()
            signature = label(recipient["signature"], 80)
            body = (f"🔐 *Código de acesso ao agendamento*\n\n"
                    f"Seu código é: *{otp}*\n\n"
                    f"_Válido por 5 minutos. Não compartilhe com ninguém._\n\n{signature}")
            encrypted = cipher().encrypt(body.encode()).decode()
            await db.execute(text("""
                INSERT INTO appointment_short_challenges(
                  id,tenant_id,appointment_id,recipient_kind,recipient_id,recipient_hash,
                  otp_hash,operation_id,body_encrypted,expires_at)
                VALUES(:id,:t,:a,:role,:person,:phone,:digest,:operation,:body,
                       clock_timestamp()+interval '5 minutes')
            """), {"id": challenge_id, "t": tenant, "a": appointment, "role": role,
                   "person": recipient["person"], "phone": recipient_hash(phone),
                   "digest": otp_digest(challenge_id, otp), "operation": operation_id,
                   "body": encrypted})
    # No plaintext code, phone, existence detail, or sending latency reaches the browser.
    return GENERIC_REQUEST


async def request_short_otp(ctx, code, role):
    """Private worker resolves a public identifier with no public timing oracle."""
    from app.appointment_short_api import enabled
    from app.appointment_short_links import checked_code, resolve_global
    if not enabled() or role not in {"customer","professional"}:
        return "disabled"
    try:
        checked_code(code)
        target_mapping = await resolve_global(code)
        if target_mapping:
            await request_otp(target_mapping["tenant_id"], target_mapping["appointment_id"], role)
        return "queued"  # Same result for missing, unauthorized and eligible codes.
    except Exception:
        return "queued"  # No sensitive exception or existence detail leaves the worker.


async def scan_short_otps(ctx):
    """A durable selector is read by app_support; workers execute tenant-scoped."""
    from app.catalog_api import support_session_factory
    from app.appointment_short_api import enabled
    if not enabled():
        return 0
    async with support_session_factory()() as db:
        jobs = (await db.execute(text("SELECT * FROM public.orion_due_short_challenges(100)"))).mappings().all()
    for job in jobs:
        await ctx["redis"].enqueue_job("process_short_otp", str(job["tenant_id"]), str(job["id"]),
              _job_id="orion-short-otp:" + str(job["id"]))
    return len(jobs)


async def process_short_otp(ctx, tenant_id, challenge_id):
    """Single provider attempt, no retry after an ambiguous transport receipt."""
    from uuid import UUID
    from app.appointment_short_api import enabled
    from app.appointment_notifications import cipher
    from app.catalog_api import support_session_factory
    from app.qr_channels import _canonical
    from app.automation_transport import _request
    if not enabled():
        return "disabled"
    tenant, identity = UUID(tenant_id), UUID(challenge_id)
    ready = None
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            job = (await db.execute(text("""
                SELECT * FROM appointment_short_challenges
                WHERE tenant_id=:t AND id=:id FOR UPDATE SKIP LOCKED
            """), {"t": tenant, "id": identity})).mappings().first()
            if not job or job["status"] != "pending":
                return "already_processed"
            now = (await db.execute(text("SELECT clock_timestamp()"))).scalar_one()
            recipient = await target(db, tenant, job["appointment_id"], job["recipient_kind"])
            if (not recipient or now >= job["expires_at"]
                    or recipient["person"] != job["recipient_id"]
                    or recipient_hash(recipient["phone"]) != job["recipient_hash"]):
                await db.execute(text("""
                    UPDATE appointment_short_challenges SET status='failed',body_encrypted=NULL
                    WHERE tenant_id=:t AND id=:id
                """), {"t": tenant, "id": identity})
                return "not_authorized"
            try:
                body = cipher().decrypt(job["body_encrypted"].encode()).decode()
                if len(body) > 1200:
                    raise ValueError("invalid_body")
            except (InvalidToken,ValueError,AttributeError,UnicodeError):
                await db.execute(text("""
                    UPDATE appointment_short_challenges SET status='failed',body_encrypted=NULL
                    WHERE tenant_id=:t AND id=:id
                """), {"t": tenant, "id": identity})
                return "invalid_body"
            async with support_session_factory()() as registry:
                channel, state = await _canonical(registry, tenant)
            if (not channel or state != "ready" or channel["id"] != recipient["channel_id"]
                    or recipient["phone"].lstrip("+") not in (channel.get("allowed_senders") or [])):
                await db.execute(text("""
                    UPDATE appointment_short_challenges SET status='failed',body_encrypted=NULL
                    WHERE tenant_id=:t AND id=:id
                """), {"t": tenant, "id": identity})
                return "channel_unavailable"
            ready = (recipient["phone"], recipient["channel_id"], job["operation_id"], body)
            await db.execute(text("""
                UPDATE appointment_short_challenges SET status='sending',attempted_at=clock_timestamp()
                WHERE tenant_id=:t AND id=:id AND status='pending'
            """), {"t": tenant, "id": identity})
    if ready is None:
        return "not_ready"
    phone, channel, operation, body = ready
    accepted = False
    try:
        # Recheck the same authorized recipient before the irreversible send.
        async with get_session_factory()() as db:
            async with tenant_transaction(db, tenant):
                job = (await db.execute(text("""
                    SELECT recipient_kind,appointment_id,recipient_hash,recipient_id,expires_at,status
                    FROM appointment_short_challenges WHERE tenant_id=:t AND id=:id
                """), {"t": tenant, "id": identity})).mappings().first()
                fresh = await target(db, tenant, job["appointment_id"], job["recipient_kind"]) if job else None
                now = (await db.execute(text("SELECT clock_timestamp()"))).scalar_one()
                permitted = bool(job and fresh and job["status"] == "sending"
                    and job["expires_at"] > now and fresh["person"] == job["recipient_id"]
                    and recipient_hash(fresh["phone"]) == job["recipient_hash"]
                    and fresh["channel_id"] == channel)
        if permitted:
            result = await _request(tenant, channel, operation,
                message={"to": phone.lstrip("+"), "text": body, "operation_id": str(operation)})
            accepted = result.get("accepted") is True and result.get("state") == "accepted"
    except Exception:
        pass
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            await db.execute(text("""
                UPDATE appointment_short_challenges
                SET status=:state,body_encrypted=NULL,
                    sent_at=CASE WHEN :accepted THEN clock_timestamp() ELSE NULL END
                WHERE tenant_id=:t AND id=:id AND status='sending'
            """), {"t": tenant, "id": identity,
                   "state": "accepted" if accepted else "failed", "accepted": accepted})
    return "accepted" if accepted else "failed"

async def redeem_otp(tenant, appointment, role, otp):
    """Returns a new opaque cookie, not personal data; wrong attempts commit."""
    if not access.tenant_enabled(tenant):
        raise HTTPException(403, GENERIC_ERROR)
    cookie = None
    factory = get_session_factory()
    async with factory() as db:
        async with tenant_transaction(db, tenant):
            await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k,0))"),
                {"k": "short-otp:" + str(tenant) + ":" + str(appointment) + ":" + role})
            challenge = (await db.execute(text("""
                SELECT * FROM appointment_short_challenges
                WHERE tenant_id=:t AND appointment_id=:a AND recipient_kind=:role
                ORDER BY created_at DESC,id DESC LIMIT 1 FOR UPDATE
            """), {"t": tenant, "a": appointment, "role": role})).mappings().first()
            info = await target(db, tenant, appointment, role)
            if (challenge and info and challenge["status"] == "accepted"
                    and challenge["attempts"] < 5
                    and challenge["expires_at"] > (await db.execute(text("SELECT clock_timestamp()"))).scalar_one()
                    and challenge["recipient_id"] == info["person"]
                    and challenge["recipient_hash"] == recipient_hash(info["phone"])):
                from secrets import compare_digest
                if compare_digest(challenge["otp_hash"], otp_digest(challenge["id"], otp)):
                    used = (await db.execute(text("""
                        UPDATE appointment_short_challenges SET status='consumed',used_at=clock_timestamp()
                        WHERE tenant_id=:t AND id=:id AND status='accepted'
                        RETURNING id
                    """), {"t": tenant, "id": challenge["id"]})).scalar_one_or_none()
                    if used:
                        if role == "customer":
                            link = await access.issue(db, tenant, appointment, short_session=True)
                            cookie = link.split("#", 1)[1]
                        else:
                            cookie = opaque_token()
                            await db.execute(text("""
                                INSERT INTO appointment_short_staff_sessions(
                                  tenant_id,appointment_id,professional_id,phone_hash,token_hash,expires_at)
                                VALUES (:t,:a,:professional,:phone,:token,
                                        clock_timestamp()+interval '2 hours')
                            """), {"t": tenant, "a": appointment,
                                   "professional": info["professional_id"],
                                   "phone": recipient_hash(info["phone"]),
                                   "token": token_digest(cookie)})
                else:
                    await db.execute(text("""
                        UPDATE appointment_short_challenges SET attempts=attempts+1
                        WHERE tenant_id=:t AND id=:id AND attempts<5
                    """), {"t": tenant, "id": challenge["id"]})
    if not cookie:
        raise HTTPException(403, GENERIC_ERROR)
    return cookie


async def verify_staff(db, tenant, appointment, cookie):
    if not isinstance(cookie, str) or len(cookie) != 43:
        raise HTTPException(403, GENERIC_ERROR)
    row = (await db.execute(text("""
        SELECT s.professional_id,p.phone_e164 FROM appointment_short_staff_sessions s
        JOIN appointments a ON a.tenant_id=s.tenant_id AND a.id=s.appointment_id
        JOIN professionals p ON p.tenant_id=s.tenant_id AND p.id=s.professional_id
        JOIN tenants t ON t.id=s.tenant_id
        WHERE s.tenant_id=:t AND s.appointment_id=:a AND s.token_hash=:digest
          AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp()
          AND a.professional_id=s.professional_id AND p.active
          AND t.status='active'
    """), {"t": tenant, "a": appointment, "digest": token_digest(cookie)})).mappings().first()
    if row is None or recipient_hash(row["phone_e164"] or "") != (
        await db.execute(text("""
            SELECT phone_hash FROM appointment_short_staff_sessions
            WHERE tenant_id=:t AND appointment_id=:a AND token_hash=:digest
        """), {"t": tenant, "a": appointment, "digest": token_digest(cookie)})
    ).scalar_one_or_none():
        raise HTTPException(403, GENERIC_ERROR)
    return row["professional_id"]
