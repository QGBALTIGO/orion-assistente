"""One-shot bulk sequences under the existing tenant-locked device poll.

Each row is intentional, including duplicate destinations. Admission preserves
the batch ordinal; it never transfers work between devices or rebuilds a plan.
Transport receipts remain authoritative and an uncertain row is never replayed.
"""
from datetime import timedelta
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text

from app import subscription_bulk_policy as policy
from app import subscription_bulk_store as store
from app.subscription_schedule_runtime import verified_self_phone

CANCELLATION_CODES = policy.CANCELLATION_CODES
TERMINAL = policy.TERMINAL


async def feature_available(db, tenant, *, origin="bulk"):
    feature = store.ORIGIN_FEATURES.get(origin)
    if feature is None:
        return False
    try:
        await store.shared.workspace(db, tenant, feature=feature)
    except HTTPException as exc:
        if exc.status_code == 403:
            return False
        raise
    return True


def admission_ready(now, tenant_finished, batch_finished, interval, recent):
    return (type(interval) is int and 10 <= interval <= 60 and recent < 10
            and (tenant_finished is None or now >= tenant_finished + timedelta(seconds=6))
            and (batch_finished is None or now >= batch_finished + timedelta(seconds=interval)))


async def check_frozen_context(db, batch, recipient):
    consent = (batch.get("snapshot") or {}).get("consent") or {}
    context = recipient.get("context") or {}
    frozen = context.get("device") or {}
    if (consent.get("confirmed") is not True
            or consent.get("actor_account_id") != str(batch["owner_account_id"])):
        raise store.fail("BULK_REVOKED")
    if (recipient["tenant_id"] != batch["tenant_id"] or recipient["bulk_id"] != batch["id"]
            or recipient["run_version"] != batch["run_version"]
            or frozen.get("id") != str(recipient["device_id"])
            or frozen.get("generation") != str(recipient["generation"])
            or frozen.get("settings_revision") != recipient["settings_revision"]
            or recipient["source"] == "manual" and recipient["manual_consent"] is not True):
        raise store.fail("BULK_CONTEXT_CHANGED")
    try:
        row, channel = await store.shared.check_device(
            db, batch["tenant_id"], UUID(frozen["id"]), actor_id=batch["owner_account_id"])
    except (HTTPException, KeyError, TypeError, ValueError):
        raise store.fail("BULK_CONTEXT_CHANGED") from None
    if (str(row["generation"]) != frozen.get("generation")
            or row["revision"] != frozen.get("settings_revision")
            or str(row["channel_id"]) != frozen.get("channel_id")
            or str(row["owner_account_id"]) != frozen.get("owner_account_id")
            or row["status"] in {"disabled", "removing", "removed"} or not channel["enabled"]):
        raise store.fail("BULK_CONTEXT_CHANGED")
    if recipient["template_id"]:
        current = await store.template_snapshot(db, batch["tenant_id"], recipient["template_id"])
        if current != context.get("template") or current["revision"] != recipient["template_revision"]:
            raise store.fail("BULK_TEMPLATE_CHANGED")
    elif context.get("template") is not None:
        raise store.fail("BULK_CONTEXT_CHANGED")
    media = context.get("media")
    if media is not None:
        if batch.get("origin", "bulk") != "direct" or recipient["template_id"] is not None:
            raise store.fail("BULK_CONTEXT_CHANGED")
        try:
            asset_id = UUID(media["id"])
        except (KeyError, TypeError, ValueError):
            raise store.fail("BULK_TEMPLATE_CHANGED") from None
        asset = (await db.execute(text("""SELECT * FROM subscription_chatbot_assets
            WHERE tenant_id=:t AND id=:id AND status='ready'"""),
            {"t": batch["tenant_id"], "id": asset_id})).mappings().one_or_none()
        if asset is None or jsonable_encoder(store.shared.asset_descriptor(asset)) != media:
            raise store.fail("BULK_TEMPLATE_CHANGED")
        plan = recipient["plan"]
        if (len(plan) != 1 or plan[0].get("asset") != media or plan[0].get("type") != media.get("kind")
                or media.get("kind") not in {"image", "video", "audio", "document"}
                or media.get("kind") == "audio" and plan[0].get("caption") != ""):
            raise store.fail("BULK_CONTEXT_CHANGED")


async def complete_batches(db, tenant, bulk_id=None, *, now=None):
    now = now or await store.clock(db)
    await db.execute(text("""WITH summaries AS (
      SELECT b.id,count(r.id) AS total,
        count(r.id) FILTER(WHERE r.status IN ('pending','running')) AS outstanding,
        count(r.id) FILTER(WHERE r.status='uncertain') AS uncertain,
        count(r.id) FILTER(WHERE r.status='failed') AS failed,
        count(r.id) FILTER(WHERE r.status='cancelled') AS cancelled,
        (array_agg(r.error_code ORDER BY r.ordinal) FILTER(WHERE r.error_code IS NOT NULL))[1] AS error
      FROM subscription_bulk_batches b LEFT JOIN subscription_bulk_recipients r
        ON r.tenant_id=b.tenant_id AND r.bulk_id=b.id AND r.run_version=b.run_version
      WHERE b.tenant_id=:t AND (CAST(:bulk AS uuid) IS NULL OR b.id=CAST(:bulk AS uuid))
        AND (b.status IN ('pending','running') OR CAST(:bulk AS uuid) IS NOT NULL)
      GROUP BY b.id
    ), desired AS (
      SELECT b.id,CASE
          WHEN z.outstanding>0 THEN CASE WHEN b.first_admitted_at IS NULL THEN 'pending' ELSE 'running' END
          WHEN z.uncertain>0 THEN 'uncertain'
          WHEN z.failed>0 OR z.total=0 THEN 'failed'
          WHEN b.error_code=ANY(CAST(:cancellations AS varchar[])) OR z.cancelled=z.total THEN 'cancelled'
          ELSE 'completed' END AS status,
        CASE WHEN z.outstanding>0 THEN b.error_code
          WHEN z.uncertain>0 THEN 'MESSAGE_OUTCOME_UNCERTAIN'
          WHEN z.total=0 THEN 'BULK_CONTEXT_CHANGED'
          WHEN b.error_code=ANY(CAST(:cancellations AS varchar[])) THEN b.error_code
          WHEN z.failed>0 OR z.cancelled=z.total THEN z.error ELSE NULL END AS error_code
      FROM subscription_bulk_batches b JOIN summaries z ON z.id=b.id WHERE b.tenant_id=:t
    ), final AS (
      SELECT b.id,d.status,d.error_code,
        CASE WHEN d.status IN ('pending','running') THEN NULL ELSE COALESCE(b.finished_at,:now) END AS finished_at,
        CASE WHEN d.status='cancelled' THEN COALESCE(b.cancelled_at,:now) ELSE b.cancelled_at END AS cancelled_at
      FROM subscription_bulk_batches b JOIN desired d ON d.id=b.id WHERE b.tenant_id=:t
    ) UPDATE subscription_bulk_batches b SET status=f.status,error_code=f.error_code,
        finished_at=f.finished_at,cancelled_at=f.cancelled_at,version=b.version+1,updated_at=:now
      FROM final f WHERE b.tenant_id=:t AND b.id=f.id AND
        (b.status IS DISTINCT FROM f.status OR b.error_code IS DISTINCT FROM f.error_code
          OR b.finished_at IS DISTINCT FROM f.finished_at OR b.cancelled_at IS DISTINCT FROM f.cancelled_at)"""),
        {"t": tenant, "bulk": bulk_id, "now": now, "cancellations": sorted(CANCELLATION_CODES)})


async def refresh_recipient(db, tenant, identity, *, now=None):
    recipient = (await db.execute(text("""SELECT * FROM subscription_bulk_recipients
        WHERE tenant_id=:t AND id=:id FOR UPDATE"""), {"t": tenant, "id": identity})).mappings().one()
    parts = [dict(row) for row in (await db.execute(text("""SELECT * FROM subscription_device_effects
        WHERE tenant_id=:t AND bulk_recipient_id=:id ORDER BY bulk_part_index"""),
        {"t": tenant, "id": identity})).mappings().all()]
    now = now or await store.clock(db)
    if any(item["status"] in {"failed", "not_sent", "uncertain"} for item in parts):
        await db.execute(text("""UPDATE subscription_device_effects SET status='not_sent',
            error_code='BULK_PREDECESSOR_FAILED',finished_at=:now
            WHERE tenant_id=:t AND bulk_recipient_id=:id AND status='pending'"""),
            {"t": tenant, "id": identity, "now": now})
        for item in parts:
            if item["status"] == "pending":
                item.update(status="not_sent", error_code="BULK_PREDECESSOR_FAILED", finished_at=now)
    status, error = policy.recipient_state(recipient, parts, now)
    internal = "running" if status == "pending" and recipient["admitted_at"] else status
    if internal == "running" and recipient["error_code"] in CANCELLATION_CODES:
        error = recipient["error_code"]
    await db.execute(text("""UPDATE subscription_bulk_recipients SET status=:status,error_code=:error,
        finished_at=CASE WHEN CAST(:status AS varchar) IN ('pending','running') THEN NULL
          ELSE COALESCE(finished_at,:now) END WHERE tenant_id=:t AND id=:id"""),
        {"status": internal, "error": error, "now": now, "t": tenant, "id": identity})
    await complete_batches(db, tenant, recipient["bulk_id"], now=now)


async def reconcile_terminal_recipients(db, tenant, *, now, bulk_id=None):
    identities = (await db.execute(text("""SELECT r.id FROM subscription_bulk_recipients r
        WHERE r.tenant_id=:t AND r.status='running' AND r.admitted_at IS NOT NULL
          AND (CAST(:bulk AS uuid) IS NULL OR r.bulk_id=CAST(:bulk AS uuid))
          AND EXISTS(SELECT 1 FROM subscription_device_effects e
            WHERE e.tenant_id=r.tenant_id AND e.bulk_recipient_id=r.id)
          AND NOT EXISTS(SELECT 1 FROM subscription_device_effects e
            WHERE e.tenant_id=r.tenant_id AND e.bulk_recipient_id=r.id AND e.status IN ('pending','running'))
        ORDER BY r.admitted_at,r.id FOR UPDATE OF r"""),
        {"t": tenant, "bulk": bulk_id})).scalars().all()
    for identity in identities:
        await refresh_recipient(db, tenant, identity, now=now)


async def cancel_bulk_pending(db, batch, code, *, include_unadmitted=True):
    tenant, now = batch["tenant_id"], await store.clock(db)
    params = {"t": tenant, "bulk": batch["id"], "code": code, "now": now}
    if include_unadmitted:
        await db.execute(text("""UPDATE subscription_bulk_recipients SET status='cancelled',error_code=:code,
            finished_at=:now WHERE tenant_id=:t AND bulk_id=:bulk AND status='pending' AND admitted_at IS NULL"""), params)
    await db.execute(text("""UPDATE subscription_bulk_recipients SET error_code=:code
        WHERE tenant_id=:t AND bulk_id=:bulk AND status='running' AND admitted_at IS NOT NULL"""), params)
    affected = (await db.execute(text("""UPDATE subscription_device_effects SET status='not_sent',error_code=:code,
        finished_at=:now WHERE tenant_id=:t AND bulk_id=:bulk AND status='pending'
        RETURNING bulk_recipient_id"""), params)).scalars().all()
    await db.execute(text("""UPDATE subscription_bulk_batches SET error_code=:code,
        cancelled_at=COALESCE(cancelled_at,:now),version=version+1,updated_at=:now
        WHERE tenant_id=:t AND id=:bulk AND status IN ('pending','running')"""), params)
    for identity in set(affected):
        await refresh_recipient(db, tenant, identity, now=now)
    await reconcile_terminal_recipients(db, tenant, now=now, bulk_id=batch["id"])
    await complete_batches(db, tenant, batch["id"], now=now)


async def cancel_recipient_pending(db, tenant, identity, code, *, now):
    params = {"t": tenant, "id": identity, "code": code, "now": now}
    await db.execute(text("""UPDATE subscription_bulk_recipients SET status='cancelled',error_code=:code,
        finished_at=:now WHERE tenant_id=:t AND id=:id AND status='pending' AND admitted_at IS NULL"""), params)
    await db.execute(text("""UPDATE subscription_bulk_recipients SET error_code=:code
        WHERE tenant_id=:t AND id=:id AND status='running' AND admitted_at IS NOT NULL"""), params)
    affected = (await db.execute(text("""UPDATE subscription_device_effects SET status='not_sent',error_code=:code,
        finished_at=:now WHERE tenant_id=:t AND bulk_recipient_id=:id AND status='pending'
        RETURNING bulk_recipient_id"""), params)).scalars().all()
    if affected:
        await refresh_recipient(db, tenant, identity, now=now)
    await complete_batches(db, tenant, now=now)


SELF_CONTACT_SQL = """(d.runtime->'phone_verified'='true'::jsonb
    AND d.runtime->>'phone' ~ '^[1-9][0-9]{7,14}$' AND d.runtime->>'phone'=r.phone)"""
CHANGED_CONTEXT_SQL = """(r.run_version<>b.run_version OR d.id IS NULL OR d.removed_at IS NOT NULL
    OR d.status IN ('disabled','removing','removed') OR d.generation<>r.generation
    OR d.revision<>r.settings_revision OR d.owner_account_id<>b.owner_account_id
    OR q.id IS NULL OR NOT q.enabled OR q.owner_account_id<>b.owner_account_id)"""
INVALID_AUDIENCE = """SELECT r.id,r.bulk_id,CASE WHEN """ + CHANGED_CONTEXT_SQL + """
        THEN 'BULK_CONTEXT_CHANGED' WHEN """ + SELF_CONTACT_SQL + """ THEN 'BULK_SELF_CONTACT'
        WHEN COALESCE(p.revoked,false) OR COALESCE(p.version,0)<>r.permission_version
          THEN 'BULK_OPTED_OUT' ELSE 'BULK_SOURCE_REMOVED' END AS code
    FROM subscription_bulk_recipients r
    JOIN subscription_bulk_batches b ON b.tenant_id=r.tenant_id AND b.id=r.bulk_id
    LEFT JOIN subscription_devices d ON d.tenant_id=r.tenant_id AND d.id=r.device_id
    LEFT JOIN qr_channels q ON q.tenant_id=d.tenant_id AND q.id=d.channel_id
    LEFT JOIN subscription_campaign_permissions p ON p.tenant_id=r.tenant_id AND p.phone=r.phone
    WHERE r.tenant_id=:t AND r.status IN ('pending','running') AND
      (""" + CHANGED_CONTEXT_SQL + """ OR """ + SELF_CONTACT_SQL + """ OR COALESCE(p.revoked,false)
       OR COALESCE(p.version,0)<>r.permission_version OR NOT (""" + store.SOURCE_VALID_SQL + "))"


async def invalidate_bulk_contacts(db, tenant):
    # Revoke every frozen matching row; repeated phones do not collapse rows.
    # Removed/changed devices are considered across the tenant so an absent
    # head device cannot leave all later ordinals permanently blocked.
    await db.execute(text("WITH invalid AS (" + INVALID_AUDIENCE + """)
        UPDATE subscription_bulk_recipients r SET status='cancelled',error_code=i.code,
          finished_at=clock_timestamp() FROM invalid i
        WHERE r.tenant_id=:t AND r.id=i.id AND r.admitted_at IS NULL"""), {"t": tenant})
    await db.execute(text("WITH invalid AS (" + INVALID_AUDIENCE + """)
        UPDATE subscription_bulk_recipients r SET error_code=i.code FROM invalid i
        WHERE r.tenant_id=:t AND r.id=i.id AND r.admitted_at IS NOT NULL AND r.status='running'"""), {"t": tenant})
    affected = (await db.execute(text("WITH invalid AS (" + INVALID_AUDIENCE + """)
        UPDATE subscription_device_effects e SET status='not_sent',error_code=i.code,finished_at=clock_timestamp()
        FROM invalid i WHERE e.tenant_id=:t AND e.bulk_recipient_id=i.id AND e.status='pending'
        RETURNING e.bulk_recipient_id"""), {"t": tenant})).scalars().all()
    for identity in set(affected):
        await refresh_recipient(db, tenant, identity)
    await complete_batches(db, tenant)


async def reap_bulk(db, tenant, now):
    affected = (await db.execute(text("""UPDATE subscription_device_effects SET
        status=CASE WHEN status='running' THEN 'uncertain' ELSE 'not_sent' END,
        error_code=CASE WHEN status='running' THEN 'MESSAGE_OUTCOME_UNCERTAIN' ELSE 'BULK_EFFECT_EXPIRED' END,
        finished_at=:now WHERE tenant_id=:t AND type='bulk_action'
          AND (status='pending' AND expires_at<=:now
            OR status='running' AND authorized_at<CAST(:now AS timestamptz)-interval '180 seconds')
        RETURNING bulk_recipient_id"""), {"t": tenant, "now": now})).scalars().all()
    for identity in set(affected):
        await refresh_recipient(db, tenant, identity, now=now)
    await reconcile_terminal_recipients(db, tenant, now=now)


async def reap_outbound_work(db, tenant, now=None):
    """Called under the tenant lock before any module's shared admission gate.

Feature revocation must not strand a previously admitted sequence; cleanup is
independent of feature eligibility and never starts new work.
"""
    from app.subscription_campaign_runtime import reap_campaigns
    from app.subscription_schedule_runtime import reap_schedules
    now = now or await store.clock(db)
    await reap_campaigns(db, tenant, now)
    await reap_schedules(db, tenant, now)
    await reap_bulk(db, tenant, now)


async def admit_bulk_messages(db, device):
    tenant = device["tenant_id"]
    now = await store.clock(db)
    await reap_bulk(db, tenant, now)
    await invalidate_bulk_contacts(db, tenant)
    allowed = [origin for origin in store.ORIGIN_FEATURES if await feature_available(db, tenant, origin=origin)]
    denied = [origin for origin in store.ORIGIN_FEATURES if origin not in allowed]
    if denied:
        revoked = (await db.execute(text("""SELECT * FROM subscription_bulk_batches
            WHERE tenant_id=:t AND origin=ANY(CAST(:origins AS varchar[]))
              AND status IN ('pending','running') AND cancelled_at IS NULL"""),
            {"t": tenant, "origins": denied})).mappings().all()
        for batch in revoked:
            await cancel_bulk_pending(db, dict(batch), "BULK_REVOKED")
    if not allowed or device["status"] != "connected":
        return []
    live = (await db.execute(text("""SELECT id FROM (
        SELECT id FROM subscription_bulk_recipients WHERE tenant_id=:t AND admitted_at IS NOT NULL AND status='running'
        UNION ALL SELECT id FROM subscription_campaign_recipients WHERE tenant_id=:t AND admitted_at IS NOT NULL AND status='running'
        UNION ALL SELECT id FROM subscription_schedule_recipients WHERE tenant_id=:t AND admitted_at IS NOT NULL AND status='running'
        ) live LIMIT 1"""), {"t": tenant})).scalar_one_or_none()
    if live is not None:
        return []
    pace = (await db.execute(text("""SELECT max(finished_at) AS finished,
        count(*) FILTER(WHERE admitted_at>CAST(:now AS timestamptz)-interval '60 seconds') AS recent FROM (
          SELECT finished_at,admitted_at FROM subscription_bulk_recipients WHERE tenant_id=:t AND admitted_at IS NOT NULL
          UNION ALL SELECT finished_at,admitted_at FROM subscription_campaign_recipients WHERE tenant_id=:t AND admitted_at IS NOT NULL
          UNION ALL SELECT finished_at,admitted_at FROM subscription_schedule_recipients WHERE tenant_id=:t AND admitted_at IS NOT NULL
        ) pace"""), {"t": tenant, "now": now})).mappings().one()
    if not admission_ready(now, pace["finished"], None, 10, pace["recent"]):
        return []
    candidates = (await db.execute(text("""SELECT b.* FROM subscription_bulk_batches b
        JOIN LATERAL(SELECT r.device_id FROM subscription_bulk_recipients r
          WHERE r.tenant_id=b.tenant_id AND r.bulk_id=b.id AND r.run_version=b.run_version
            AND r.status='pending' AND r.admitted_at IS NULL ORDER BY r.ordinal,r.id LIMIT 1) head ON true
        WHERE b.tenant_id=:t AND b.status IN ('pending','running') AND b.cancelled_at IS NULL
          AND b.origin=ANY(CAST(:origins AS varchar[]))
          AND COALESCE(b.next_admission_at,b.confirmed_at)<=:now AND head.device_id=:d
        ORDER BY COALESCE(b.next_admission_at,b.confirmed_at),b.id LIMIT 25"""),
        {"t": tenant, "d": device["id"], "now": now, "origins": allowed})).mappings().all()
    for raw in candidates:
        batch = dict(raw)
        if batch["owner_account_id"] != device["owner_account_id"]:
            await cancel_bulk_pending(db, batch, "BULK_REVOKED")
            continue
        finished = (await db.execute(text("""SELECT max(finished_at) FROM subscription_bulk_recipients
            WHERE tenant_id=:t AND bulk_id=:bulk AND admitted_at IS NOT NULL"""),
            {"t": tenant, "bulk": batch["id"]})).scalar_one()
        if not admission_ready(now, pace["finished"], finished, batch["interval_seconds"], pace["recent"]):
            continue
        recipient = (await db.execute(text("""SELECT r.* FROM subscription_bulk_recipients r
            WHERE r.tenant_id=:t AND r.bulk_id=:bulk AND r.run_version=:run
              AND r.status='pending' AND r.admitted_at IS NULL
            ORDER BY r.ordinal,r.id LIMIT 1 FOR UPDATE OF r"""),
            {"t": tenant, "bulk": batch["id"], "run": batch["run_version"]})).mappings().one_or_none()
        if recipient is None or recipient["device_id"] != device["id"]:
            continue
        recipient = dict(recipient)
        try:
            await check_frozen_context(db, batch, recipient)
            if store.fingerprint(recipient["plan"]) != recipient["plan_hash"]:
                raise store.fail("BULK_CONTEXT_CHANGED")
        except HTTPException as exc:
            code = exc.detail.get("code", "BULK_CONTEXT_CHANGED") if isinstance(exc.detail, dict) else "BULK_CONTEXT_CHANGED"
            await cancel_recipient_pending(db, tenant, recipient["id"], code, now=now)
            continue
        busy = (await db.execute(text("""SELECT id FROM subscription_device_effects
            WHERE tenant_id=:t AND device_id=:d AND payload->>'phone'=:phone
              AND type IN ('send_text','chatbot_action','campaign_action','scheduled_action','bulk_action')
              AND status IN ('pending','running','uncertain') AND expires_at>:now LIMIT 1"""),
            {"t": tenant, "d": device["id"], "phone": recipient["phone"], "now": now})).scalar_one_or_none()
        if busy is not None:
            continue  # The head row remains first; no later ordinal is skipped.
        return await create_effects(db, device, batch, recipient, now)
    return []


async def create_effects(db, device, batch, recipient, now):
    tenant, event_id = device["tenant_id"], uuid4()
    await db.execute(text("""INSERT INTO subscription_device_events
        (id,tenant_id,device_id,generation,provider_event_id,kind,occurred_at,payload)
        VALUES(:id,:t,:d,:g,:provider,'bulk',:now,CAST(:payload AS jsonb))"""),
        {"id": event_id, "t": tenant, "d": device["id"], "g": device["generation"],
         "provider": "bulk:" + str(recipient["id"]), "now": now,
         "payload": store.encode({"kind": "bulk", "origin": batch.get("origin", "bulk"), "phone": recipient["phone"],
             "name": recipient["name"], "batch_id": batch["id"], "recipient_id": recipient["id"]})})
    ids, previous = [], None
    for index, action in enumerate(recipient["plan"]):
        identity, payload = uuid4(), {"phone": recipient["phone"], "action": action}
        store.bounded_json(payload, 131072)
        await db.execute(text("""INSERT INTO subscription_device_effects
            (id,tenant_id,device_id,generation,settings_revision,event_id,effect_key,type,purpose,payload,previous_effect,
             bulk_id,bulk_recipient_id,bulk_part_index,due_at,expires_at)
            VALUES(:id,:t,:d,:g,:r,:event,:key,'bulk_action','bulk_action',CAST(:payload AS jsonb),:previous,
              :bulk,:recipient,:part,:now,:expiry)"""),
            {"id": identity, "t": tenant, "d": device["id"], "g": device["generation"], "r": device["revision"],
             "event": event_id, "key": "bulk:" + str(index), "payload": store.encode(payload), "previous": previous,
             "bulk": batch["id"], "recipient": recipient["id"], "part": index,
             "now": now, "expiry": now + timedelta(minutes=10)})
        ids.append(identity)
        previous = identity
    await db.execute(text("""UPDATE subscription_bulk_recipients SET status='running',admitted_at=:now
        WHERE tenant_id=:t AND id=:id AND bulk_id=:bulk AND run_version=:run
          AND status='pending' AND admitted_at IS NULL"""),
        {"t": tenant, "id": recipient["id"], "bulk": batch["id"], "run": batch["run_version"], "now": now})
    await db.execute(text("""UPDATE subscription_bulk_batches SET status='running',
        first_admitted_at=COALESCE(first_admitted_at,:now),last_admitted_at=:now,
        next_admission_at=:next,version=version+1,updated_at=:now WHERE tenant_id=:t AND id=:bulk"""),
        {"t": tenant, "bulk": batch["id"], "now": now, "next": now + timedelta(seconds=batch["interval_seconds"])})
    return ids


async def bulk_effect_authority(db, device, effect, now):
    """Read-only final authority, also used around each private asset read."""
    if (effect["type"] != "bulk_action" or effect["purpose"] != "bulk_action"
            or effect["tenant_id"] != device["tenant_id"] or effect["device_id"] != device["id"]
            or effect["generation"] != device["generation"] or effect["settings_revision"] != device["revision"]
            or device["removed_at"] is not None or device["status"] in {"disabled", "removing", "removed"}):
        return "BULK_CONTEXT_CHANGED", None
    if effect["expires_at"] <= now:
        return "BULK_EFFECT_EXPIRED", None
    try:
        batch = await store.batch_row(db, device["tenant_id"], effect["bulk_id"])
    except HTTPException:
        return "BULK_CONTEXT_CHANGED", None
    if not await feature_available(db, device["tenant_id"], origin=batch.get("origin", "bulk")):
        return "BULK_REVOKED", None
    recipient = (await db.execute(text("""SELECT * FROM subscription_bulk_recipients
        WHERE tenant_id=:t AND bulk_id=:bulk AND id=:id"""),
        {"t": device["tenant_id"], "bulk": batch["id"], "id": effect["bulk_recipient_id"]})).mappings().one_or_none()
    if batch["owner_account_id"] != device["owner_account_id"]:
        return "BULK_REVOKED", None
    if batch["status"] == "cancelled" or batch["cancelled_at"] is not None:
        return "BULK_CANCELLED", None
    if batch["status"] != "running" or not recipient or recipient["status"] != "running":
        return "BULK_REVOKED", None
    if batch["error_code"] in CANCELLATION_CODES or recipient["error_code"] in CANCELLATION_CODES:
        return batch["error_code"] if batch["error_code"] in CANCELLATION_CODES else recipient["error_code"], None
    if recipient["phone"] == verified_self_phone(device):
        return "BULK_SELF_CONTACT", None
    part = effect["bulk_part_index"]
    if (recipient["run_version"] != batch["run_version"] or recipient["admitted_at"] is None
            or batch["first_admitted_at"] is None or recipient["device_id"] != device["id"]
            or recipient["generation"] != device["generation"] or recipient["settings_revision"] != device["revision"]
            or type(part) is not int or not 0 <= part < len(recipient["plan"])
            or store.fingerprint(recipient["plan"]) != recipient["plan_hash"]
            or effect["payload"] != {"phone": recipient["phone"], "action": recipient["plan"][part]}):
        return "BULK_CONTEXT_CHANGED", None
    try:
        await check_frozen_context(db, batch, recipient)
    except HTTPException as exc:
        return exc.detail.get("code", "BULK_CONTEXT_CHANGED") if isinstance(exc.detail, dict) else "BULK_CONTEXT_CHANGED", None
    permission = (await db.execute(text("""SELECT COALESCE(p.revoked,false) AS revoked,COALESCE(p.version,0) AS version
        FROM (SELECT 1) singleton LEFT JOIN subscription_campaign_permissions p
          ON p.tenant_id=:t AND p.phone=:phone"""),
        {"t": device["tenant_id"], "phone": recipient["phone"]})).mappings().one()
    if permission["revoked"] or permission["version"] != recipient["permission_version"]:
        return "BULK_OPTED_OUT", None
    if not await store.sources_valid(db, device["tenant_id"], recipient["origins"], recipient["phone"],
                                     source=recipient["source"], manual_consent=recipient["manual_consent"]):
        return "BULK_SOURCE_REMOVED", None
    if part:
        previous = (await db.execute(text("""SELECT id,status FROM subscription_device_effects
            WHERE tenant_id=:t AND bulk_id=:bulk AND bulk_recipient_id=:id AND bulk_part_index=:part"""),
            {"t": device["tenant_id"], "bulk": batch["id"], "id": recipient["id"], "part": part - 1})).mappings().one_or_none()
        if not previous or previous["id"] != effect["previous_effect"] or previous["status"] != "accepted":
            return "BULK_PREDECESSOR_FAILED", None
    elif effect["previous_effect"] is not None:
        return "BULK_CONTEXT_CHANGED", None
    from app.subscription_sent_messages import message_authority
    if not await message_authority(db, device, effect):
        return "BULK_BUSY", None
    return None, batch


asset_authority = bulk_effect_authority


async def authorize_bulk_effect(db, device, effect, owner, fence, payload):
    denied = {"authorized": False, "operation_id": str(effect["id"])}
    now = await store.clock(db)
    if payload.chatbot_revision is not None or effect["due_at"] > now:
        return denied
    if payload.recheck:
        if (effect["status"] != "running" or effect["attempts"] != 1
                or effect["claimed_owner"] != owner or effect["claimed_fence"] != fence):
            return denied
    elif effect["status"] != "pending" or effect["attempts"] != 0:
        return denied
    code, batch = await bulk_effect_authority(db, device, effect, now)
    if code:
        if not payload.recheck and code not in {"BULK_BUSY", "BULK_PREDECESSOR_FAILED"}:
            await db.execute(text("""UPDATE subscription_device_effects SET status='not_sent',error_code=:code,
                finished_at=:now WHERE tenant_id=:t AND id=:id AND status='pending'"""),
                {"code": code, "now": now, "t": device["tenant_id"], "id": effect["id"]})
            await refresh_recipient(db, device["tenant_id"], effect["bulk_recipient_id"], now=now)
        return {**denied, "error_code": code}
    if not payload.recheck:
        await db.execute(text("""UPDATE subscription_device_effects SET status='running',attempts=1,authorized_at=:now,
            claimed_owner=:owner,claimed_fence=:fence WHERE tenant_id=:t AND id=:id AND status='pending'"""),
            {"now": now, "owner": owner, "fence": fence, "t": device["tenant_id"], "id": effect["id"]})
    return jsonable_encoder({"authorized": True, "operation_id": effect["id"], "type": "bulk_action",
        "purpose": "bulk_action", "device_id": device["id"], "generation": device["generation"],
        "settings_revision": device["revision"], "batch_id": batch["id"], "recipient_id": effect["bulk_recipient_id"],
        "run_version": batch["run_version"], "part_index": effect["bulk_part_index"], "payload": effect["payload"]})


async def after_bulk_effect_finish(db, device, effect, state):
    await refresh_recipient(db, device["tenant_id"], effect["bulk_recipient_id"])
    return []
