"""Durable conversation journal and single-attempt human/Meta outbox.

No transport is invoked by a read or an HTTP enqueue. Unknown outcomes retain
their persistent slot; recovery never changes sending/uncertain back to queued.
"""
import hashlib
import json
import os
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text

from app.database import get_session_factory, tenant_transaction
from app import inbox_control as control


def send_enabled():
    return control.enabled() and os.getenv("INBOX_SEND_ENABLED", "0") == "1"


def fingerprint(phone, body):
    return hashlib.sha256(json.dumps([phone.removeprefix("+"), body], ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


async def record_inbound(db, tenant, channel, phone, external, body="", kind="text", provider_at=None):
    row = await control.lock_control(db, tenant, channel, "+"+phone.removeprefix("+"))
    persisted=(await db.execute(text("""INSERT INTO inbox_messages(tenant_id,control_id,direction,source,kind,body,
      external_id,control_revision,status,provider_at) VALUES(:t,:c,'in','customer',:kind,:body,:external,:rev,'received',:at)
      ON CONFLICT(tenant_id,control_id,direction,external_id) DO NOTHING RETURNING id"""),
      {"t":tenant,"c":row["id"],"kind":kind,"body":body,"external":external,"rev":row["revision"]+(1 if row["mode"]=="ai" else 0),"at":provider_at})).scalar_one_or_none()
    if persisted and row['mode']=='ai':
        await control.control_event(db,tenant,row,'ai','customer')
        await db.execute(text("UPDATE inbox_controls SET revision=revision+1 WHERE tenant_id=:t AND id=:id"),{'t':tenant,'id':row['id']})
    await db.execute(text("UPDATE inbox_controls SET updated_at=clock_timestamp() WHERE tenant_id=:t AND id=:id"),
                     {"t":tenant,"id":row["id"]})
    return row


async def operator_version(db, tenant, account):
    return (await db.execute(text("SELECT public.inbox_operator_version(:t,:a)"),
                            {"t":tenant,"a":account})).scalar_one()


async def record_outbound(db, tenant, row, operation, source, body, digest, *, status="queued", actor=None, auth_version=None):
    await db.execute(text("""INSERT INTO inbox_messages(id,tenant_id,control_id,direction,source,body,
      fingerprint,actor_account_id,actor_auth_version,control_revision,status)
      VALUES(:id,:t,:c,'out',:source,:body,:fp,:a,:av,:rev,:status) ON CONFLICT(id) DO NOTHING"""),
      {"id":operation,"t":tenant,"c":row["id"],"source":source,"body":body,"fp":digest,
       "a":actor,"av":auth_version,"rev":row["revision"],"status":status})


async def accepted(db, tenant, operation, external):
    await db.execute(text("""UPDATE inbox_messages SET status=CASE WHEN status IN ('delivered','read') THEN status
      ELSE 'accepted' END,external_id=COALESCE(external_id,:external),updated_at=clock_timestamp()
      WHERE tenant_id=:t AND id=:id AND direction='out' AND status IN ('sending','uncertain','accepted','delivered','read')"""),
      {"t":tenant,"id":operation,"external":external})
    await db.execute(text("""UPDATE inbox_messages m SET status=r.status FROM inbox_controls c,inbox_provider_receipts r
      WHERE m.tenant_id=:t AND m.id=:id AND m.control_id=c.id AND c.tenant_id=m.tenant_id
      AND r.tenant_id=m.tenant_id AND r.channel_id=c.channel_id AND r.contact_e164=c.contact_e164
      AND r.external_id=m.external_id AND (r.status='read' OR m.status<>'read' AND r.status='delivered'
        OR m.status NOT IN ('read','delivered') AND r.status='failed')"""),{"t":tenant,"id":operation})


async def provider_receipt(db,tenant,channel,phone,external,status,operation=None):
    # Receipt may arrive before the HTTP response. Persist it first; accepted()
    # joins it later. A delayed sent/delivered event never downgrades read.
    if operation is not None:
        await db.execute(text("""UPDATE inbox_messages m SET external_id=:external FROM inbox_controls c
          WHERE m.tenant_id=:t AND m.id=:op AND m.tenant_id=c.tenant_id AND m.control_id=c.id
          AND c.channel_id=:c AND c.contact_e164=:phone AND m.direction='out'
          AND m.status IN ('sending','uncertain','accepted','delivered','read','failed')
          AND (m.external_id IS NULL OR m.external_id=:external)"""),
          {"t":tenant,"op":UUID(str(operation)),"c":channel,"phone":phone,"external":external})
    await db.execute(text("""INSERT INTO inbox_provider_receipts(tenant_id,channel_id,contact_e164,external_id,status)
      SELECT :t,:c,:phone,:external,:status WHERE EXISTS(SELECT 1 FROM inbox_channel_links WHERE tenant_id=:t AND channel_id=:c)
      ON CONFLICT(tenant_id,channel_id,external_id) DO UPDATE SET status=CASE
        WHEN inbox_provider_receipts.status='read' OR EXCLUDED.status='read' THEN 'read'
        WHEN inbox_provider_receipts.status='delivered' OR EXCLUDED.status='delivered' THEN 'delivered'
        WHEN inbox_provider_receipts.status='failed' OR EXCLUDED.status='failed' THEN 'failed' ELSE 'accepted' END,
        updated_at=clock_timestamp() WHERE inbox_provider_receipts.contact_e164=EXCLUDED.contact_e164"""),
      {"t":tenant,"c":channel,"phone":phone,"external":external,"status":status})
    rows=(await db.execute(text("""SELECT m.id FROM inbox_messages m JOIN inbox_controls c
      ON c.tenant_id=m.tenant_id AND c.id=m.control_id WHERE m.tenant_id=:t AND c.channel_id=:c
      AND c.contact_e164=:phone AND m.direction='out' AND m.external_id=:external"""),
      {"t":tenant,"c":channel,"phone":phone,"external":external})).scalars().all()
    for operation in rows:
        await accepted(db,tenant,operation,external)
        await db.execute(text("""UPDATE inbox_controls SET pending_operation=NULL,pending_fingerprint=NULL,pending_since=NULL
          WHERE tenant_id=:t AND channel_id=:c AND contact_e164=:phone AND pending_operation=:op"""),
          {"t":tenant,"c":channel,"phone":phone,"op":operation})


async def manual_slot(db, tenant, row, payload):
    item = (await db.execute(text("SELECT * FROM inbox_messages WHERE tenant_id=:t AND id=:id FOR UPDATE"),
                             {"t":tenant,"id":payload.operation_id})).mappings().one_or_none()
    if (not send_enabled() or not item or item["control_id"] != row["id"] or item["source"] != "manual"
        or item["status"] != "sending" or item["fingerprint"] != payload.fingerprint
        or row["pending_operation"] != payload.operation_id or row["pending_fingerprint"] != payload.fingerprint
        or row["mode"] not in {"human","waiting"} or row["assigned_account_id"] != item["actor_account_id"]
        or await operator_version(db, tenant, item["actor_account_id"]) != item["actor_auth_version"]):
        raise HTTPException(409, "Envio humano sem autorização atual")


async def process_inbox_message(ctx, tenant_id, message_id):
    if not send_enabled():
        return "not_enabled"
    tenant, operation = UUID(tenant_id), UUID(message_id)
    factory = get_session_factory()
    async with factory() as db:
        async with tenant_transaction(db, tenant):
            # Same order as operator changes and the bridge: control, message.
            header = (await db.execute(text("""SELECT c.channel_id,c.contact_e164 FROM inbox_messages m
              JOIN inbox_controls c ON c.tenant_id=m.tenant_id AND c.id=m.control_id
              WHERE m.tenant_id=:t AND m.id=:id AND m.status='queued'"""), {"t":tenant,"id":operation})).mappings().one_or_none()
            if not header:
                return "already_claimed"
            row = await control.lock_control(db, tenant, header["channel_id"], header["contact_e164"])
            item = (await db.execute(text("SELECT * FROM inbox_messages WHERE tenant_id=:t AND id=:id FOR UPDATE"),
                                    {"t":tenant,"id":operation})).mappings().one()
            if item["status"] != "queued":
                return "already_claimed"
            if row["pending_operation"] is not None:
                return "conversation_busy"
            provider = (await db.execute(text("SELECT provider FROM inbox_channel_links WHERE tenant_id=:t AND channel_id=:c"),
                                        {"t":tenant,"c":header["channel_id"]})).scalar_one()
            valid = row["revision"] == item["control_revision"] and row["pending_operation"] is None
            if item["source"] == "manual":
                valid = valid and row["mode"] in {"human","waiting"} and row["assigned_account_id"] == item["actor_account_id"]
                valid = valid and await operator_version(db,tenant,item["actor_account_id"]) == item["actor_auth_version"]
            else:
                valid = valid and row["mode"] == "ai"
            try:
                await control.check_channel(db,tenant,header["channel_id"],header["contact_e164"])
            except HTTPException:
                valid = False
            channel = None
            if provider == "meta":
                channel = (await db.execute(text("""SELECT c.phone_number_id,c.token_enc,c.status,c.bot_enabled,
                  t.bot_enabled AS tenant_bot FROM whatsapp_channels c JOIN tenants t ON t.id=c.tenant_id
                  WHERE c.tenant_id=:t AND c.id=:c FOR SHARE OF c,t"""), {"t":tenant,"c":header["channel_id"]})).mappings().one()
                recent = (await db.execute(text("""SELECT EXISTS(SELECT 1 FROM inbox_messages
                  WHERE tenant_id=:t AND control_id=:c AND direction='in' AND provider_at>clock_timestamp()-interval '24 hours'
                    AND provider_at<=clock_timestamp())"""),
                  {"t":tenant,"c":row["id"]})).scalar_one()
                if os.getenv("META_LIVE_SEND") != "1":
                    return "meta_not_enabled"
                valid = valid and recent and channel["status"] == "connected"
                if item["source"] != "manual":
                    valid = valid and channel["bot_enabled"] and channel["tenant_bot"]
                # Credential failure is known before the irreversible attempt.
                try:
                    from cryptography.fernet import Fernet
                    access_token = Fernet(os.environ["INTEGRATION_FERNET_KEY"].encode()).decrypt(channel["token_enc"]).decode()
                except (KeyError, ValueError, TypeError, __import__('cryptography.fernet',fromlist=['InvalidToken']).InvalidToken):
                    valid = False
            if not valid:
                await db.execute(text("UPDATE inbox_messages SET status='failed',updated_at=clock_timestamp() WHERE tenant_id=:t AND id=:id"), {"t":tenant,"id":operation})
                return "cancelled_before_send"
            await db.execute(text("UPDATE inbox_messages SET status='sending',updated_at=clock_timestamp() WHERE tenant_id=:t AND id=:id"), {"t":tenant,"id":operation})
            await db.execute(text("""UPDATE inbox_controls SET pending_operation=:op,pending_fingerprint=:fp,pending_since=clock_timestamp()
              WHERE tenant_id=:t AND id=:id"""), {"t":tenant,"id":row["id"],"op":operation,"fp":item["fingerprint"]})
    # The single-attempt claim is committed before any provider I/O.
    try:
        if provider == "qr":
            from app.automation_transport import _request
            result = await (ctx.get("inbox_qr_sender") or _request)(tenant,header["channel_id"],operation,
                message={"to":header["contact_e164"].removeprefix("+"),"text":item["body"],"operation_id":str(operation),
                         "source":item["source"],"control_revision":item["control_revision"]})
            if result.get("accepted") is not True or result.get("state") != "accepted":
                raise ValueError("delivery_unverified")
            external = result.get("provider_id")
        else:
            from app.messaging import CloudApiProvider
            sender = ctx.get("inbox_meta_sender") or CloudApiProvider().send_text
            external = await sender({"phone_number_id":channel["phone_number_id"],"access_token":access_token,"operation_id":str(operation)}, header["contact_e164"],item["body"])
            if not isinstance(external,str) or not 1<=len(external)<=200:
                raise ValueError("delivery_unverified")
    except Exception:
        async with factory() as db:
            async with tenant_transaction(db,tenant):
                await db.execute(text("""UPDATE inbox_messages SET status='uncertain',updated_at=clock_timestamp()
                  WHERE tenant_id=:t AND id=:id AND status='sending'"""), {"t":tenant,"id":operation})
        return "uncertain"
    async with factory() as db:
        async with tenant_transaction(db,tenant):
            await control.lock_control(db,tenant,header["channel_id"],header["contact_e164"])
            await accepted(db,tenant,operation,external)
            await db.execute(text("""UPDATE inbox_controls SET pending_operation=NULL,pending_fingerprint=NULL,pending_since=NULL
              WHERE tenant_id=:t AND id=:id AND pending_operation=:op AND pending_fingerprint=:fp"""),
              {"t":tenant,"id":row["id"],"op":operation,"fp":item["fingerprint"]})
    return "accepted"


async def scan_inbox(ctx):
    if not send_enabled():
        return 0
    from app.catalog_api import support_session_factory
    async with support_session_factory()() as db:
        rows = (await db.execute(text("SELECT tenant_id,id FROM inbox_messages WHERE status='queued' ORDER BY created_at,id LIMIT 100"))).mappings().all()
    for row in rows:
        # A blocked queue item can be reconsidered on the next bounded scan;
        # only queued rows are scanned and the DB claim still admits one send.
        import time
        await ctx["redis"].enqueue_job("process_inbox_message",str(row["tenant_id"]),str(row["id"]),_job_id="inbox:"+str(row["id"])+":"+str(int(time.time())//30))
    return len(rows)
