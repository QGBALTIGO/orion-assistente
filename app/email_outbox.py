"""Private durable email delivery, sharing Orion's PostgreSQL pool and Arq worker.

Only producers of a new authenticated event enqueue. There is no historical
scanner for accounts, invitations or memberships, and no WhatsApp dependency.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from uuid import UUID, uuid4

from cryptography.fernet import InvalidToken
from redis.exceptions import RedisError
from sqlalchemy import text

from app import email_transport as transport

MAX_ATTEMPTS = 8
# Resend documents a 24-hour idempotency window. Stop safely before it expires.
MAX_RETRY_AGE = timedelta(hours=23)
STATUS_MESSAGES = {
    "queued": "Convite na fila de envio de e-mail.",
    "accepted": "O provedor aceitou o e-mail. Isso não confirma a entrega na caixa de entrada.",
    "failed": "Não foi possível enviar o e-mail. Copie o link do convite.",
    "disabled": "Envio de e-mail indisponível. Copie o link do convite.",
    "manual": "Copie e compartilhe o link do convite.",
    "suppressed": "Este destinatário não está habilitado para envio. Copie o link do convite.",
    "expired": "O prazo de envio deste e-mail expirou. Crie um novo convite, se necessário.",
    "uncertain": "O resultado do envio está em verificação. Não foi criado outro envio.",
}


def delivery_result(status):
    return {"status": status, "message": STATUS_MESSAGES[status]}


def recipient_digest(recipient):
    return hmac.new(os.environ["PANEL_OTP_SECRET"].encode(),
                    ("email-recipient:" + transport.normalize_recipient(recipient)).encode(), hashlib.sha256).hexdigest()


def _integer_setting(name, default, minimum, maximum):
    try:
        return max(minimum, min(maximum, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


QUOTA_SCRIPT = """
for i,key in ipairs(KEYS) do
  if tonumber(redis.call('GET',key) or '0') >= tonumber(ARGV[2*i-1]) then return 0 end
end
for i,key in ipairs(KEYS) do
  local n=redis.call('INCR',key)
  if n==1 then redis.call('EXPIRE',key,tonumber(ARGV[2*i])) end
end
return 1
"""


async def request_quota(cache, recipient):
    """Applied before account lookup: quota responses reveal no account state."""
    digest = recipient_digest(recipient)
    try:
        result = await cache.eval(QUOTA_SCRIPT, 2,
            "orion:email:request:recipient:" + digest, "orion:email:request:global",
            _integer_setting("PANEL_EMAIL_REQUESTS_PER_RECIPIENT_HOUR", 6, 1, 100), 3600,
            _integer_setting("PANEL_EMAIL_REQUESTS_PER_HOUR", 2000, 1, 100000), 3600)
    except (RedisError, OSError):
        from fastapi import HTTPException
        raise HTTPException(503, "E-mails temporariamente indisponíveis") from None
    return bool(result)


async def _send_quota(cache, digest):
    now = datetime.now(timezone.utc)
    return bool(await cache.eval(QUOTA_SCRIPT, 3,
        "orion:email:send:second:" + str(int(now.timestamp())),
        "orion:email:send:day:" + now.strftime("%Y%m%d"),
        "orion:email:send:recipient:" + digest,
        _integer_setting("PANEL_EMAIL_RATE_PER_SECOND", 2, 1, 100), 2,
        _integer_setting("PANEL_EMAIL_DAILY_LIMIT", 100, 1, 1000000), 86400,
        _integer_setting("PANEL_EMAIL_RECIPIENT_DAILY_LIMIT", 20, 1, 1000), 86400))


async def enqueue(db, *, kind, recipient, dedupe_key, account_id=None, tenant_id=None,
                  invitation_id=None, token_id=None, expires_at=None, **template):
    if not transport.configured():
        return delivery_result("disabled")
    recipient = transport.normalize_recipient(recipient)
    if not transport.allowed_recipient(recipient):
        return delivery_result("suppressed")
    payload = transport.render_message(recipient, kind, **template)
    encrypted = transport.cipher().encrypt(json.dumps(payload, ensure_ascii=False,
        sort_keys=True, separators=(",", ":")).encode()).decode()
    now = datetime.now(timezone.utc)
    expiry = min(expires_at or now + MAX_RETRY_AGE, now + MAX_RETRY_AGE)
    if expiry <= now:
        return delivery_result("expired")
    await db.execute(text("""INSERT INTO panel_email_outbox
        (id,kind,recipient_digest,account_id,tenant_id,invitation_id,token_id,dedupe_key,payload_enc,expires_at)
        VALUES(:id,:kind,:recipient,:account,:tenant,:invitation,:token,:key,:payload,:expires)
        ON CONFLICT(dedupe_key) DO NOTHING"""),
        {"id": uuid4(), "kind": kind, "recipient": recipient_digest(recipient), "account": account_id,
         "tenant": tenant_id, "invitation": invitation_id, "token": token_id,
         "key": dedupe_key, "payload": encrypted, "expires": expiry})
    row = (await db.execute(text("SELECT status FROM panel_email_outbox WHERE dedupe_key=:key"),
                            {"key": dedupe_key})).mappings().one()
    return delivery_result({"pending": "queued", "dead": "failed"}.get(row["status"], row["status"]))


async def invitation_delivery(db, invitation):
    if not invitation.get("email"):
        return delivery_result("manual")
    status = (await db.execute(text("SELECT status FROM panel_email_outbox WHERE invitation_id=:id AND kind='team_invite'"),
                               {"id": invitation["id"]})).scalar_one_or_none()
    return delivery_result({"pending": "queued", "dead": "failed"}.get(status, status) if status else "disabled")


async def _source_is_current(db, row):
    # Source SHARE locks survive until the provider receipt is persisted. A
    # revocation or new code cannot finish while a stale email is being sent.
    if row["kind"] in {"verify", "reset"}:
        current = (await db.execute(text("""SELECT id FROM panel_email_tokens
            WHERE id=:id AND used_at IS NULL AND expires_at>clock_timestamp() FOR SHARE"""),
            {"id": row["token_id"]})).scalar_one_or_none()
        return current is not None
    if row["kind"] == "team_invite":
        current = (await db.execute(text("""SELECT id FROM management_team_invitations
            WHERE id=:id AND status='pending' AND expires_at>clock_timestamp() FOR SHARE"""),
            {"id": row["invitation_id"]})).scalar_one_or_none()
        return current is not None
    if row["kind"] in {"team_access_approved", "team_access_revoked"} and row["account_id"]:
        active = (await db.execute(text("""SELECT active FROM tenant_memberships
            WHERE tenant_id=:tenant AND account_id=:account FOR SHARE"""),
            {"tenant": row["tenant_id"], "account": row["account_id"]})).scalar_one_or_none()
        return active is (row["kind"] == "team_access_approved")
    if row["kind"] == "team_access_revoked" and row["invitation_id"]:
        status = (await db.execute(text("SELECT status FROM management_team_invitations WHERE id=:id FOR SHARE"),
                                   {"id": row["invitation_id"]})).scalar_one_or_none()
        return status == "revoked"
    return row["kind"] == "password_changed"


async def _terminal(db, row_id, status, code):
    await db.execute(text("""UPDATE panel_email_outbox SET status=:status,payload_enc=NULL,last_error_code=:code
        WHERE id=:id"""), {"id": row_id, "status": status, "code": code})


async def process_email_outbox(ctx, outbox_id):
    if not transport.configured():
        return "disabled"
    from app.catalog_api import support_session_factory
    async with support_session_factory()() as db:
        async with db.begin():
            row = (await db.execute(text("""SELECT * FROM panel_email_outbox
                WHERE id=:id AND status='pending' AND next_attempt_at<=clock_timestamp()
                FOR UPDATE SKIP LOCKED"""), {"id": UUID(outbox_id)})).mappings().one_or_none()
            if row is None:
                return "not_pending"
            now = datetime.now(timezone.utc)
            if row["expires_at"] <= now or (row["first_attempt_at"] and now - row["first_attempt_at"] >= MAX_RETRY_AGE):
                await _terminal(db, row["id"], "uncertain" if row["first_attempt_at"] else "expired", "email_retry_window_expired")
                return "expired"
            if not await _source_is_current(db, row):
                await _terminal(db, row["id"], "suppressed", "email_source_superseded")
                return "suppressed"
            if row["expires_at"] <= datetime.now(timezone.utc):
                await _terminal(db, row["id"], "expired", "email_deadline_after_source_lock")
                return "expired"
            try:
                payload = json.loads(transport.cipher().decrypt(row["payload_enc"].encode()).decode())
                recipient = transport.normalize_recipient(payload["to"][0])
                if not hmac.compare_digest(row["recipient_digest"], recipient_digest(recipient)):
                    raise ValueError("email_payload_scope_mismatch")
            except (InvalidToken, ValueError, KeyError, TypeError, IndexError):
                await _terminal(db, row["id"], "dead", "email_payload_unreadable")
                return "failed"
            if not transport.allowed_recipient(recipient):
                await _terminal(db, row["id"], "suppressed", "email_recipient_suppressed")
                return "suppressed"
            try:
                quota = await _send_quota(ctx["redis"], row["recipient_digest"])
            except (RedisError, OSError):
                return "quota_unavailable"
            if not quota:
                await db.execute(text("UPDATE panel_email_outbox SET next_attempt_at=clock_timestamp()+interval '30 seconds',last_error_code='email_rate_limited' WHERE id=:id"), {"id": row["id"]})
                return "rate_limited"
            attempts = row["attempts"] + 1
            await db.execute(text("""UPDATE panel_email_outbox SET attempts=:attempts,
                first_attempt_at=coalesce(first_attempt_at,clock_timestamp()) WHERE id=:id"""),
                {"id": row["id"], "attempts": attempts})
            try:
                receipt = await transport.send_message(payload, row["id"])
            except transport.EmailProviderError as exc:
                if not exc.retryable or attempts >= MAX_ATTEMPTS:
                    await _terminal(db, row["id"], "uncertain" if exc.ambiguous else "dead", exc.code)
                else:
                    await db.execute(text("""UPDATE panel_email_outbox SET last_error_code=:code,
                        next_attempt_at=clock_timestamp()+make_interval(secs=>:delay) WHERE id=:id"""),
                        {"id": row["id"], "code": exc.code,
                         "delay": min(3600, 15 * 2 ** (attempts - 1))})
                return "retry" if exc.retryable and attempts < MAX_ATTEMPTS else "failed"
            await db.execute(text("""UPDATE panel_email_outbox SET status='accepted',payload_enc=NULL,
                provider_message_id=:receipt,accepted_at=clock_timestamp(),last_error_code=NULL WHERE id=:id"""),
                {"id": row["id"], "receipt": receipt})
            return "accepted"


def email_job_id(item_id, next_attempt_at, scan_at):
    # Arq retains failed results for an hour by default. A failed claim must
    # not block this short-lived OTP for that entire hour. PostgreSQL remains
    # the concurrent-claim authority, and provider idempotency stays per row.
    return f"email:{item_id}:{next_attempt_at.isoformat()}:{int(scan_at.timestamp()) // 15}"


async def scan_email_outbox(ctx):
    await ctx["redis"].set("orion:email:worker:health", json.dumps({
        "configured": transport.configured(), "live_send": os.getenv("PANEL_EMAIL_LIVE_SEND") == "1"}), ex=150)
    from app.catalog_api import support_session_factory
    async with support_session_factory()() as db:
        # TTL disposal is independent of delivery flags and encryption-key
        # availability: disabling email must not retain expired access secrets.
        async with db.begin():
            # Railway may start the new worker before API pre-deploy finishes
            # migration0038. Absence alone is a temporary no-op; connectivity,
            # permission and every other SQL failure still propagate normally.
            present = (await db.execute(text("SELECT to_regclass('public.panel_email_outbox')"))).scalar_one()
            if present is None:
                return 0
            await db.execute(text("""WITH expired AS (
                SELECT id FROM panel_email_outbox WHERE status='pending' AND expires_at<=clock_timestamp()
                ORDER BY next_attempt_at,id LIMIT 100 FOR UPDATE SKIP LOCKED
            ) UPDATE panel_email_outbox o SET payload_enc=NULL,
                status=CASE WHEN first_attempt_at IS NULL THEN 'expired' ELSE 'uncertain' END,
                last_error_code='email_retry_window_expired' FROM expired WHERE o.id=expired.id"""))
            if not transport.configured():
                return 0
            pending = (await db.execute(text("""SELECT id,next_attempt_at FROM panel_email_outbox
                WHERE status='pending' AND next_attempt_at<=clock_timestamp()
                ORDER BY next_attempt_at,id LIMIT 100"""))).mappings().all()
    count = 0
    scan_at = datetime.now(timezone.utc)
    for row in pending:
        job = await ctx["redis"].enqueue_job("process_email_outbox", str(row["id"]),
            _job_id=email_job_id(row["id"], row["next_attempt_at"], scan_at))
        count += job is not None
    return count
