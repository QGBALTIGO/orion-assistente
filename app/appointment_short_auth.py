"""Short-link OTP proof and professional sessions; no protocol-only access.

Code requests are deliberately indistinguishable for unknown/unauthorized links.
The OTP is stored as an HMAC, not plaintext or an unsalted six-digit digest.
"""
from datetime import timedelta
from uuid import uuid4

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
    """Bounded per-appointment OTP dispatch to a verified, allowed QR contact."""
    if not access.tenant_enabled(tenant):
        return GENERIC_REQUEST
    from app.appointment_short_links import otp_digest
    from app.catalog_api import support_session_factory
    from app.qr_channels import _canonical
    from app.appointment_message_templates import label
    from app.automation_transport import _request
    import secrets

    factory = get_session_factory()
    async with factory() as db:
        async with tenant_transaction(db, tenant):
            recipient = await target(db, tenant, appointment, role)
            if not recipient:
                return GENERIC_REQUEST
            phone = recipient["phone"]
            # A platform-wide public link cannot change the channel or its allowlist.
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
            # Only the latest challenge can be redeemed.
            await db.execute(text("""
                UPDATE appointment_short_challenges SET status='failed'
                WHERE tenant_id=:t AND appointment_id=:a AND recipient_kind=:role
                  AND status IN ('pending','accepted')
            """), {"t": tenant, "a": appointment, "role": role})
            otp = f"{secrets.randbelow(1_000_000):06d}"
            challenge_id, operation_id = uuid4(), uuid4()
            await db.execute(text("""
                INSERT INTO appointment_short_challenges(
                  id,tenant_id,appointment_id,recipient_kind,recipient_id,recipient_hash,
                  otp_hash,operation_id,expires_at)
                VALUES(:id,:t,:a,:role,:person,:phone,:digest,:operation,
                       clock_timestamp()+interval '5 minutes')
            """), {"id": challenge_id, "t": tenant, "a": appointment, "role": role,
                   "person": recipient["person"], "phone": recipient_hash(phone),
                   "digest": otp_digest(challenge_id, otp), "operation": operation_id})
            channel_id = recipient["channel_id"]
            signature = label(recipient["signature"], 80)

    # Persist first, then submit to the idempotent QR transport once. Uncertain
    # results are NEVER replayed. No secret is exposed to the HTTP response.
    body = (f"🔐 *Código de acesso ao agendamento*\n\n"
            f"Seu código é: *{otp}*\n\n"
            f"_Válido por 5 minutos. Não compartilhe com ninguém._\n\n{signature}")
    accepted = False
    try:
        outcome = await _request(tenant, channel_id, operation_id,
            message={"to": phone.lstrip("+"), "text": body, "operation_id": str(operation_id)})
        accepted = outcome.get("accepted") is True and outcome.get("state") == "accepted"
    except Exception:
        pass  # No provider response, credential, number, or code enters logs.
    finally:
        del otp
    async with factory() as db:
        async with tenant_transaction(db, tenant):
            await db.execute(text("""
                UPDATE appointment_short_challenges SET
                  status=:state,sent_at=CASE WHEN :accepted THEN clock_timestamp() ELSE NULL END
                WHERE tenant_id=:t AND id=:id AND status='pending'
            """), {"state": "accepted" if accepted else "failed", "accepted": accepted,
                   "t": tenant, "id": challenge_id})
    return GENERIC_REQUEST


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
                            link = await access.issue(db, tenant, appointment)
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
