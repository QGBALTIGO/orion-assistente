"""One durable dispatch per participant; unknown outcomes never return to pending."""
import asyncio
from datetime import timedelta
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text

from app.catalog_api import support_session_factory
from app import subscription_group_add_store as store
from app import subscription_group_add_schemas as schemas

ACTIVE = ("pending", "running")


async def update_progress(db, batch, now, *, error=None):
    states = (await db.execute(text("SELECT status FROM subscription_group_add_items WHERE tenant_id=:t AND batch_id=:b"),
        {"t": batch["tenant_id"], "b": batch["id"]})).scalars().all()
    if any(state in ACTIVE for state in states):
        status = "running" if batch["started_at"] is not None or "running" in states else "pending"
    elif "unknown" in states:
        status = "unknown"
    elif all(state in {"added", "already_member"} for state in states):
        status = "completed"
    elif all(state == "cancelled" for state in states):
        status = "cancelled"
    else:
        status = "partial"
    await db.execute(text("""UPDATE subscription_group_add_batches SET status=CAST(:status AS text),version=version+1,
        finished_at=:finished,error_code=CASE WHEN CAST(:status AS text)<>'unknown' AND error_code='GROUP_ADD_OUTCOME_UNKNOWN'
          THEN CAST(:error AS text) ELSE COALESCE(CAST(:error AS text),error_code) END,next_item_at=:next
        WHERE tenant_id=:t AND id=:id"""), {"t": batch["tenant_id"], "id": batch["id"], "status": status,
        "finished": None if status in ACTIVE else now, "error": error,
        "next": now + timedelta(seconds=schemas.INTERVAL_SECONDS)})


async def cancel_pending(db, batch, now, code):
    await db.execute(text("""UPDATE subscription_group_add_items SET status='cancelled',finished_at=:now,error_code=:code
        WHERE tenant_id=:t AND batch_id=:b AND status='pending'"""),
        {"t": batch["tenant_id"], "b": batch["id"], "now": now, "code": code})


def private_payload(batch, item):
    return {"device_id": str(batch["device_id"]), "generation": str(batch["generation"]),
        "settings_revision": batch["settings_revision"], "operation_id": str(item["id"]),
        "group_id": batch["destination_group_id"], "phone": item["phone"], "claim_token": str(item["claim_token"])}


async def claim(tenant, identity):
    async with support_session_factory()() as db:
        async with db.begin():
            original = (await db.execute(text("SELECT * FROM subscription_group_add_batches WHERE tenant_id=:t AND id=:id"),
                {"t": tenant, "id": identity})).mappings().one_or_none()
            if original is None or original["status"] not in ACTIVE:
                return None
            original = dict(original)
            try:
                _, transport = await store.owner_authority(db, original)
            except HTTPException:
                # A denial may occur before owner_authority reaches its tenant
                # lock. Serialize with finish/reaping in the same lock order,
                # and never use the earlier unlocked progress snapshot.
                await db.execute(text("SELECT id FROM tenants WHERE id=:t FOR UPDATE"), {"t": tenant})
                locked = (await db.execute(text("""SELECT * FROM subscription_group_add_batches
                    WHERE tenant_id=:t AND id=:id FOR UPDATE"""),
                    {"t": tenant, "id": identity})).mappings().one_or_none()
                if locked is None or locked["status"] not in ACTIVE:
                    return None
                original = dict(locked)
                now = await store.clock(db)
                await cancel_pending(db, original, now, "GROUP_ADD_CONTEXT_CHANGED")
                await update_progress(db, original, now, error="GROUP_ADD_CONTEXT_CHANGED")
                return None
            batch = dict((await db.execute(text("""SELECT * FROM subscription_group_add_batches
                WHERE tenant_id=:t AND id=:id FOR UPDATE"""), {"t": tenant, "id": identity})).mappings().one())
            now = await store.clock(db)
            if batch["status"] not in ACTIVE or batch["next_item_at"] > now:
                return None
            # This also blocks a replacement process while an older IQ may
            # still settle. A receipt can resolve uncertainty; another add cannot.
            busy = (await db.execute(text("""SELECT id FROM subscription_group_add_items
                WHERE tenant_id=:t AND device_id=:d
                  AND (status='running' OR status='unknown' AND NOT provider_settled) LIMIT 1"""),
                {"t": tenant, "d": batch["device_id"]})).scalar_one_or_none()
            if busy is not None:
                return None
            item = (await db.execute(text("""SELECT * FROM subscription_group_add_items
                WHERE tenant_id=:t AND batch_id=:b AND status='pending' ORDER BY ordinal LIMIT 1 FOR UPDATE"""),
                {"t": tenant, "b": identity})).mappings().one_or_none()
            if item is None:
                await update_progress(db, batch, now)
                return None
            item = dict(item)
            try:
                await store.check_item_source(db, batch, item)
            except HTTPException as exc:
                code = exc.detail.get("code", "GROUP_ADD_SOURCE_CHANGED") if isinstance(exc.detail, dict) else "GROUP_ADD_SOURCE_CHANGED"
                await db.execute(text("""UPDATE subscription_group_add_items SET status='cancelled',finished_at=:now,error_code=:code
                    WHERE tenant_id=:t AND id=:id AND status='pending'"""),
                    {"t": tenant, "id": item["id"], "now": now, "code": code})
                await update_progress(db, batch, now)
                return None
            # Commit this irreversible attempt marker BEFORE the HTTP request.
            # Nothing (including ARQ retries) can claim this item a second time.
            await db.execute(text("""UPDATE subscription_group_add_items SET status='running',dispatch_at=:now
                WHERE tenant_id=:t AND id=:id AND status='pending' AND dispatch_at IS NULL"""),
                {"t": tenant, "id": item["id"], "now": now})
            await db.execute(text("""UPDATE subscription_group_add_batches SET status='running',
                started_at=COALESCE(started_at,:now),version=version+1 WHERE tenant_id=:t AND id=:id"""),
                {"t": tenant, "id": identity, "now": now})
            return {"tenant": tenant, "batch": batch, "item": item, "transport": transport,
                    "payload": private_payload(batch, item)}


def outcome(raw, payload):
    value = schemas.PrivateResult.model_validate(raw)
    for key in ("device_id", "generation", "settings_revision", "operation_id", "group_id", "phone"):
        if str(getattr(value, key)) != str(payload[key]):
            raise ValueError("Unrelated operation receipt")
    if value.status in {"added", "already_member", "not_added"} and not value.settled:
        raise ValueError("A completed provider result requires a settled invocation")
    if value.status == "added":
        return "added", None, False, value.settled
    if value.status == "already_member":
        return "already_member", None, False, value.settled
    if value.status == "not_added":
        return "not_added", "GROUP_ADD_PROVIDER_REFUSED", False, value.settled
    if value.status == "not_attempted":
        if not value.settled:
            return "unknown", "GROUP_ADD_OUTCOME_UNKNOWN", True, False
        code = "GROUP_ADD_ADMIN_REQUIRED" if value.reason == "not_admin" else "GROUP_ADD_CONTEXT_CHANGED"
        return "not_added", code, True, value.settled
    return "unknown", "GROUP_ADD_OUTCOME_UNKNOWN", True, value.settled


async def finish(claimed, state, code, stop, settled=False):
    async with support_session_factory()() as db:
        async with db.begin():
            # Preserve an actual receipt even if business permission changed
            # after dispatch. This path cannot authorize any provider operation.
            await db.execute(text("SELECT id FROM tenants WHERE id=:t FOR UPDATE"), {"t": claimed["tenant"]})
            batch = dict((await db.execute(text("""SELECT * FROM subscription_group_add_batches
                WHERE tenant_id=:t AND id=:id FOR UPDATE"""),
                {"t": claimed["tenant"], "id": claimed["batch"]["id"]})).mappings().one())
            item = (await db.execute(text("""SELECT * FROM subscription_group_add_items
                WHERE tenant_id=:t AND id=:id FOR UPDATE"""),
                {"t": claimed["tenant"], "id": claimed["item"]["id"]})).mappings().one()
            if (item["claim_token"] != claimed["item"]["claim_token"] or item["status"] not in {"running", "unknown"}
                    or item["dispatch_at"] is None):
                return
            now = await store.clock(db)
            await db.execute(text("""UPDATE subscription_group_add_items SET status=:state,error_code=:code,finished_at=:now,
                provider_settled=provider_settled OR :settled
                WHERE tenant_id=:t AND id=:id"""),
                {"t": batch["tenant_id"], "id": item["id"], "state": state, "code": code, "now": now, "settled": settled})
            if stop:
                await cancel_pending(db, batch, now, "GROUP_ADD_STOPPED")
            await update_progress(db, batch, now, error=code if stop else None)


async def process_subscription_group_add(ctx, tenant_id, batch_id):
    claimed = await claim(UUID(str(tenant_id)), UUID(str(batch_id)))
    if claimed is None:
        return "not_claimed"
    try:
        async with asyncio.timeout(15):
            raw = await store.channels._bridge_control(claimed["transport"], "device/group-add/add", "POST", claimed["payload"])
        store.bounded(raw, 4096)
        state, code, stop, settled = outcome(raw, claimed["payload"])
    except Exception:
        state, code, stop, settled = "unknown", "GROUP_ADD_OUTCOME_UNKNOWN", True, False
    await finish(claimed, state, code, stop, settled)
    return state


async def reap_interrupted():
    async with support_session_factory()() as db:
        stale = (await db.execute(text("""SELECT tenant_id,batch_id,id FROM subscription_group_add_items
            WHERE status='running' AND dispatch_at<clock_timestamp()-interval '45 seconds'
            ORDER BY dispatch_at LIMIT 100"""))).mappings().all()
    for row in stale:
        async with support_session_factory()() as db:
            async with db.begin():
                await db.execute(text("SELECT id FROM tenants WHERE id=:t FOR UPDATE"), {"t": row["tenant_id"]})
                batch = dict((await db.execute(text("""SELECT * FROM subscription_group_add_batches
                    WHERE tenant_id=:t AND id=:id FOR UPDATE"""),
                    {"t": row["tenant_id"], "id": row["batch_id"]})).mappings().one())
                now = await store.clock(db)
                changed = (await db.execute(text("""UPDATE subscription_group_add_items
                    SET status='unknown',error_code='GROUP_ADD_OUTCOME_UNKNOWN',finished_at=CAST(:now AS timestamptz)
                    WHERE tenant_id=:t AND id=:id AND status='running'
                      AND dispatch_at<CAST(:now AS timestamptz)-interval '45 seconds' RETURNING id"""),
                    {"t": row["tenant_id"], "id": row["id"], "now": now})).scalar_one_or_none()
                if changed is not None:
                    await cancel_pending(db, batch, now, "GROUP_ADD_STOPPED")
                    await update_progress(db, batch, now, error="GROUP_ADD_OUTCOME_UNKNOWN")


async def observe_presence(claimed):
    from app.catalog_api import Actor
    from app.subscription_group_add import bridge_read
    tenant, batch = claimed["tenant"], claimed["batch"]
    actor = Actor("member", account_id=batch["owner_account_id"])
    async with store.owner_transaction(tenant, actor) as db:
        device, transport = await store.scope(db, tenant, batch["device_id"], actor)
    members = await bridge_read(transport, device, batch["destination_group_id"])
    async with store.owner_transaction(tenant, actor) as db:
        current, _ = await store.scope(db, tenant, batch["device_id"], actor)
        if store.contacts.device_version(device) != store.contacts.device_version(current):
            return False
    return any(member.phone == claimed["item"]["phone"] for member in members.members)


async def process_subscription_group_add_receipt(ctx, tenant_id, item_id):
    tenant, identity = UUID(str(tenant_id)), UUID(str(item_id))
    async with support_session_factory()() as db:
        async with db.begin():
            item = (await db.execute(text("""UPDATE subscription_group_add_items SET last_receipt_at=clock_timestamp()
                WHERE tenant_id=:t AND id=:id AND status='unknown'
                  AND (last_receipt_at IS NULL OR last_receipt_at<clock_timestamp()-interval '30 seconds') RETURNING *"""),
                {"t": tenant, "id": identity})).mappings().one_or_none()
            if item is None:
                return "not_due"
            batch = (await db.execute(text("SELECT * FROM subscription_group_add_batches WHERE tenant_id=:t AND id=:id"),
                {"t": tenant, "id": item["batch_id"]})).mappings().one()
            transport = (await db.execute(text("SELECT * FROM qr_channels WHERE tenant_id=:t AND id=:id"),
                {"t": tenant, "id": batch["channel_id"]})).mappings().one_or_none()
            if transport is None:
                return "unavailable"
            claimed = {"tenant": tenant, "batch": dict(batch), "item": dict(item),
                       "transport": dict(transport), "payload": private_payload(batch, item)}
    try:
        async with asyncio.timeout(15):
            raw = await store.channels._bridge_control(claimed["transport"], "device/group-add/lookup", "POST", claimed["payload"])
        store.bounded(raw, 4096)
        state, code, stop, settled = outcome(raw, claimed["payload"])
        if state != "unknown":
            await finish(claimed, state, code, stop, settled)
            return state
        if settled:
            # Settled proves the old call ended, not that it succeeded. Keep
            # this destination+phone blocked unless current presence is proved.
            async with support_session_factory()() as db:
                async with db.begin():
                    await db.execute(text("""UPDATE subscription_group_add_items SET provider_settled=true
                        WHERE tenant_id=:t AND id=:id AND claim_token=:token AND status='unknown'"""),
                        {"t": tenant, "id": identity, "token": item["claim_token"]})
            if await observe_presence(claimed):
                await finish(claimed, "already_member", None, False, True)
                return "already_member"
    except Exception:
        pass  # An absent receipt or an absent member never authorizes a retry.
    return "unknown"


async def scan_subscription_group_add(ctx):
    await reap_interrupted()
    async with support_session_factory()() as db:
        rows = (await db.execute(text("""SELECT b.tenant_id,b.id FROM subscription_group_add_batches b
            WHERE b.status IN ('pending','running') AND b.next_item_at<=clock_timestamp()
              AND NOT EXISTS(SELECT 1 FROM subscription_group_add_items i
                WHERE i.tenant_id=b.tenant_id AND i.device_id=b.device_id
                  AND (i.status='running' OR i.status='unknown' AND NOT i.provider_settled))
            ORDER BY b.next_item_at,b.id LIMIT 100"""))).mappings().all()
        receipts = (await db.execute(text("""SELECT tenant_id,id FROM subscription_group_add_items
            WHERE status='unknown' AND (last_receipt_at IS NULL OR last_receipt_at<clock_timestamp()-interval '30 seconds')
            ORDER BY last_receipt_at NULLS FIRST,dispatch_at LIMIT 20"""))).mappings().all()
    for row in rows:
        await ctx["redis"].enqueue_job("process_subscription_group_add", str(row["tenant_id"]), str(row["id"]),
                                      _job_id="group-add:" + str(row["id"]))
    for row in receipts:
        await ctx["redis"].enqueue_job("process_subscription_group_add_receipt", str(row["tenant_id"]), str(row["id"]),
                                      _job_id="group-add-receipt:" + str(row["id"]))
    return len(rows)
