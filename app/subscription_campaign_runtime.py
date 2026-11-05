"""Tenant-serialized campaign admission and last-moment transport authority.

Only the existing authenticated device poll calls admission. Public reads never
call this module's mutators. A recipient is admitted once, with all frozen parts;
neither a failed part nor an uncertain receipt is a request to send it again.
"""
from datetime import timedelta
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text

from app import subscription_campaign_policy as policy
from app import subscription_campaign_schemas as schemas
from app import subscription_campaign_store as store

CANCELLATION_CODES = frozenset({"CAMPAIGN_PAUSED", "CAMPAIGN_CANCELLED", "CAMPAIGN_OPTED_OUT",
    "CAMPAIGN_MEMBER_REMOVED", "CAMPAIGN_REVOKED", "CAMPAIGN_CONTEXT_CHANGED", "CAMPAIGN_TEMPLATE_CHANGED"})


async def feature_available(db, tenant):
    try:
        await store.shared.workspace(db, tenant, feature=store.FEATURE)
    except HTTPException as exc:
        if exc.status_code == 403:
            return False
        raise
    return True


async def check_frozen_context(db, campaign):
    snapshot = campaign.get("snapshot") or {}
    if not snapshot.get("consent", {}).get("confirmed") or not snapshot.get("devices"):
        raise store.fail("CAMPAIGN_REVOKED")
    for frozen in snapshot["devices"]:
        try:
            row, channel = await store.shared.check_device(db, campaign["tenant_id"], UUID(frozen["id"]),
                actor_id=campaign["owner_account_id"])
        except HTTPException:
            raise store.fail("CAMPAIGN_CONTEXT_CHANGED") from None
        if (str(row["generation"]) != frozen["generation"] or row["revision"] != frozen["settings_revision"]
                or str(row["channel_id"]) != frozen["channel_id"] or row["status"] == "disabled"
                or not channel["enabled"] or str(row["owner_account_id"]) != frozen["owner_account_id"]):
            raise store.fail("CAMPAIGN_CONTEXT_CHANGED")
    if campaign["template_id"]:
        current = await store.template_snapshot(db, campaign["tenant_id"], campaign["template_id"])
        if current != snapshot.get("template") or current["revision"] != campaign["template_revision"]:
            raise store.fail("CAMPAIGN_TEMPLATE_CHANGED")


async def complete_campaign(db, tenant, campaign_id=None):
    await db.execute(text("""UPDATE subscription_campaigns c SET status='completed',version=version+1,
        updated_at=clock_timestamp() WHERE c.tenant_id=:t AND c.status IN ('scheduled','running')
        AND (CAST(:id AS uuid) IS NULL OR c.id=CAST(:id AS uuid))
        AND EXISTS(SELECT 1 FROM subscription_campaign_recipients r
          WHERE r.tenant_id=c.tenant_id AND r.campaign_id=c.id AND r.run_version=c.run_version)
        AND NOT EXISTS(SELECT 1 FROM subscription_campaign_recipients r
          WHERE r.tenant_id=c.tenant_id AND r.campaign_id=c.id AND r.run_version=c.run_version
            AND r.status IN ('pending','running'))"""), {"t": tenant, "id": campaign_id})


async def refresh_recipient(db, tenant, identity, *, now=None):
    recipient = (await db.execute(text("""SELECT * FROM subscription_campaign_recipients
        WHERE tenant_id=:t AND id=:id FOR UPDATE"""), {"t": tenant, "id": identity})).mappings().one()
    parts = [dict(row) for row in (await db.execute(text("""SELECT * FROM subscription_device_effects
        WHERE tenant_id=:t AND campaign_recipient_id=:id ORDER BY campaign_part_index"""),
        {"t": tenant, "id": identity})).mappings().all()]
    now = now or await store.clock(db)
    # Closing only pending tails preserves every running/terminal transport
    # record, including a successful first part followed by a failed second.
    if any(item["status"] in {"failed", "not_sent", "uncertain"} for item in parts):
        await db.execute(text("""UPDATE subscription_device_effects SET status='not_sent',
            error_code='CAMPAIGN_PREDECESSOR_FAILED',finished_at=:now
            WHERE tenant_id=:t AND campaign_recipient_id=:id AND status='pending'"""),
            {"t": tenant, "id": identity, "now": now})
        for item in parts:
            if item["status"] == "pending":
                item.update(status="not_sent", error_code="CAMPAIGN_PREDECESSOR_FAILED", finished_at=now)
    status, error = policy.recipient_state(recipient, parts, now)
    internal = "running" if status == "pending" and recipient["admitted_at"] else status
    if internal == "running" and recipient["error_code"] in CANCELLATION_CODES:
        # Revocation during a claimed operation must survive removal/re-addition
        # or pause/resume before its last transport check. The effect ledger is
        # unchanged: a receipt can still prove that an external send occurred.
        error = recipient["error_code"]
    await db.execute(text("""UPDATE subscription_campaign_recipients SET status=:status,error_code=:error,
        finished_at=CASE WHEN CAST(:status AS varchar) IN ('pending','running') THEN NULL
          ELSE COALESCE(finished_at,:now) END WHERE tenant_id=:t AND id=:id"""),
        {"status": internal, "error": error, "now": now, "t": tenant, "id": identity})
    await complete_campaign(db, tenant, recipient["campaign_id"])


async def cancel_campaign_pending(db, campaign, code, *, include_unadmitted):
    params = {"t": campaign["tenant_id"], "id": campaign["id"], "run": campaign["run_version"], "code": code}
    if include_unadmitted:
        await db.execute(text("""UPDATE subscription_campaign_recipients SET status='cancelled',error_code=:code,
            finished_at=clock_timestamp() WHERE tenant_id=:t AND campaign_id=:id AND run_version=:run
              AND status='pending' AND admitted_at IS NULL"""), params)
    await db.execute(text("""UPDATE subscription_campaign_recipients SET error_code=:code
        WHERE tenant_id=:t AND campaign_id=:id AND run_version=:run AND status='running' AND admitted_at IS NOT NULL"""), params)
    affected = (await db.execute(text("""UPDATE subscription_device_effects SET status='not_sent',error_code=:code,
        finished_at=clock_timestamp() WHERE tenant_id=:t AND campaign_id=:id AND status='pending'
        RETURNING campaign_recipient_id"""), params)).scalars().all()
    for identity in set(affected):
        await refresh_recipient(db, campaign["tenant_id"], identity)
    await complete_campaign(db, campaign["tenant_id"], campaign["id"])


# A removal is made permanent for the old run in the same tenant-locked group
# mutation. Re-adding later therefore cannot revive a not-yet-sent recipient.
INVALID_AUDIENCE = """SELECT r.id,CASE WHEN COALESCE(p.revoked,false)
        OR COALESCE(p.version,0)<>r.permission_version THEN 'CAMPAIGN_OPTED_OUT'
        ELSE 'CAMPAIGN_MEMBER_REMOVED' END AS code
    FROM subscription_campaign_recipients r LEFT JOIN subscription_campaign_permissions p
      ON p.tenant_id=r.tenant_id AND p.phone=r.phone
    WHERE r.tenant_id=:t AND r.status IN ('pending','running')
      AND (COALESCE(p.revoked,false) OR COALESCE(p.version,0)<>r.permission_version
        OR NOT EXISTS(SELECT 1 FROM subscription_campaign_members m JOIN subscription_campaign_groups g
          ON g.tenant_id=m.tenant_id AND g.id=m.group_id AND g.archived_at IS NULL
          WHERE m.tenant_id=r.tenant_id AND m.phone=r.phone AND m.group_id=ANY(r.source_group_ids)
            AND m.removed_at IS NULL))"""


async def invalidate_audiences(db, tenant):
    # First terminate unadmitted rows in one statement; their number can be
    # large. Only the admitted sequence needs per-recipient effect projection.
    await db.execute(text("WITH invalid AS (" + INVALID_AUDIENCE + """)
        UPDATE subscription_campaign_recipients r SET status='cancelled',error_code=i.code,
          finished_at=clock_timestamp() FROM invalid i
        WHERE r.tenant_id=:t AND r.id=i.id AND r.admitted_at IS NULL"""), {"t": tenant})
    await db.execute(text("WITH invalid AS (" + INVALID_AUDIENCE + """)
        UPDATE subscription_campaign_recipients r SET error_code=i.code FROM invalid i
        WHERE r.tenant_id=:t AND r.id=i.id AND r.admitted_at IS NOT NULL AND r.status='running'"""), {"t": tenant})
    affected = (await db.execute(text("WITH invalid AS (" + INVALID_AUDIENCE + """)
        UPDATE subscription_device_effects e SET status='not_sent',error_code=i.code,finished_at=clock_timestamp()
        FROM invalid i WHERE e.tenant_id=:t AND e.campaign_recipient_id=i.id AND e.status='pending'
        RETURNING e.campaign_recipient_id"""), {"t": tenant})).scalars().all()
    for identity in set(affected):
        await refresh_recipient(db, tenant, identity)
    await complete_campaign(db, tenant)


async def record_campaign_optout(db, device, event_data, event_id):
    from app.contact_preferences import preference_intent
    if (event_data.get("kind") != "text" or event_data.get("origin") != "contact"
            or not isinstance(event_data.get("text"), str)
            or preference_intent(event_data["text"]) not in {"marketing", "all"}):
        return False
    try:
        phone = schemas.phone(event_data.get("phone"))
    except ValueError:
        return False
    # The text adapter passes an actual current-device event; the ASR adapter
    # passes its original audio event after successful transcription. No new
    # ASR call, external content or client-supplied transcript is accepted here.
    source = (await db.execute(text("""SELECT e.kind,e.payload FROM subscription_device_events e
        WHERE e.tenant_id=:t AND e.id=:id AND e.device_id=:d AND e.generation=:g
          AND e.kind IN ('text','audio') AND e.payload->>'origin'='contact'
          AND e.payload->>'phone'=:phone"""),
        {"t": device["tenant_id"], "id": event_id, "d": device["id"], "g": device["generation"], "phone": phone})).mappings().one_or_none()
    if source is None or source["kind"] == "text" and source["payload"].get("text") != event_data["text"]:
        return False
    if source["kind"] == "audio":
        accepted = (await db.execute(text("""SELECT id FROM subscription_device_effects
            WHERE tenant_id=:t AND event_id=:id AND device_id=:d AND generation=:g
              AND type='transcribe' AND status='accepted' LIMIT 1"""),
            {"t": device["tenant_id"], "id": event_id, "d": device["id"], "g": device["generation"]})).scalar_one_or_none()
        if accepted is None:
            return False
    changed = (await db.execute(text("""INSERT INTO subscription_campaign_permissions
        (tenant_id,phone,revoked,source,event_id) VALUES(:t,:phone,true,'optout',:event)
        ON CONFLICT(tenant_id,phone) DO UPDATE SET version=subscription_campaign_permissions.version+1,
          revoked=true,source='optout',event_id=EXCLUDED.event_id,actor_account_id=NULL,evidence_hash=NULL,
          updated_at=clock_timestamp() WHERE subscription_campaign_permissions.event_id IS DISTINCT FROM EXCLUDED.event_id
        RETURNING phone"""), {"t": device["tenant_id"], "phone": phone, "event": event_id})).scalar_one_or_none()
    if changed is not None:
        await invalidate_audiences(db, device["tenant_id"])
    return changed is not None


async def reap_campaigns(db, tenant, now):
    affected = (await db.execute(text("""UPDATE subscription_device_effects SET
        status=CASE WHEN status='running' THEN 'uncertain' ELSE 'not_sent' END,
        error_code=CASE WHEN status='running' THEN 'MESSAGE_OUTCOME_UNCERTAIN' ELSE 'CAMPAIGN_EFFECT_EXPIRED' END,
        finished_at=:now WHERE tenant_id=:t AND type='campaign_action'
          AND (status='pending' AND expires_at<=:now
            OR status='running' AND authorized_at<CAST(:now AS timestamptz)-interval '180 seconds')
        RETURNING campaign_recipient_id"""), {"t": tenant, "now": now})).scalars().all()
    for identity in set(affected):
        await refresh_recipient(db, tenant, identity, now=now)


async def admit_due_campaigns(db, device):
    tenant = device["tenant_id"]
    if not await feature_available(db, tenant):
        return []  # Feature revocation must not break ordinary device polling.
    now = await store.clock(db)
    await reap_campaigns(db, tenant, now)
    await invalidate_audiences(db, tenant)
    if device["status"] != "connected":
        return []
    live = (await db.execute(text("""SELECT id FROM subscription_campaign_recipients
        WHERE tenant_id=:t AND admitted_at IS NOT NULL AND status='running' LIMIT 1"""), {"t": tenant})).scalar_one_or_none()
    if live is not None:
        return []
    pace = (await db.execute(text("""SELECT max(finished_at) AS finished,
        count(*) FILTER(WHERE admitted_at>CAST(:now AS timestamptz)-interval '60 seconds') AS recent
        FROM subscription_campaign_recipients WHERE tenant_id=:t AND admitted_at IS NOT NULL"""),
        {"t": tenant, "now": now})).mappings().one()
    if not policy.admission_ready(now, pace["finished"], None, 30, pace["recent"]):
        return []
    candidates = (await db.execute(text("""SELECT c.* FROM subscription_campaigns c WHERE c.tenant_id=:t
        AND c.status IN ('scheduled','running') AND c.archived_at IS NULL AND c.next_admission_at<=:now
        AND EXISTS(SELECT 1 FROM subscription_campaign_recipients r WHERE r.tenant_id=c.tenant_id
          AND r.campaign_id=c.id AND r.run_version=c.run_version AND r.device_id=:d
          AND r.status='pending' AND r.admitted_at IS NULL)
        ORDER BY c.next_admission_at,c.id LIMIT 25"""), {"t": tenant, "d": device["id"], "now": now})).mappings().all()
    for raw in candidates:
        campaign = dict(raw)
        try:
            if campaign["owner_account_id"] != device["owner_account_id"]:
                raise store.fail("CAMPAIGN_REVOKED")
            await check_frozen_context(db, campaign)
        except HTTPException as exc:
            code = exc.detail.get("code", "CAMPAIGN_CONTEXT_CHANGED") if isinstance(exc.detail, dict) else "CAMPAIGN_CONTEXT_CHANGED"
            await cancel_campaign_pending(db, campaign, code, include_unadmitted=True)
            continue
        window = policy.active_window(campaign["definition"], now)
        if window is None:
            await db.execute(text("UPDATE subscription_campaigns SET next_admission_at=:next WHERE tenant_id=:t AND id=:id"),
                {"next": policy.next_window(campaign["definition"], now), "t": tenant, "id": campaign["id"]})
            continue
        finished = (await db.execute(text("""SELECT max(finished_at) FROM subscription_campaign_recipients
            WHERE tenant_id=:t AND campaign_id=:id AND admitted_at IS NOT NULL"""),
            {"t": tenant, "id": campaign["id"]})).scalar_one()
        if not policy.admission_ready(now, pace["finished"], finished, campaign["definition"]["interval_seconds"], pace["recent"]):
            continue
        recipient = (await db.execute(text("""SELECT r.* FROM subscription_campaign_recipients r
            WHERE r.tenant_id=:t AND r.campaign_id=:id AND r.run_version=:run AND r.device_id=:d
              AND r.status='pending' AND r.admitted_at IS NULL
              AND NOT EXISTS(SELECT 1 FROM subscription_device_effects e WHERE e.tenant_id=r.tenant_id
                AND e.device_id=r.device_id AND e.payload->>'phone'=r.phone
                AND e.type IN ('send_text','chatbot_action','campaign_action')
                AND e.status IN ('pending','running','uncertain') AND e.expires_at>:now)
            ORDER BY r.ordinal,r.id LIMIT 1 FOR UPDATE OF r"""),
            {"t": tenant, "id": campaign["id"], "run": campaign["run_version"], "d": device["id"], "now": now})).mappings().one_or_none()
        if recipient is None:
            continue
        if recipient["generation"] != device["generation"] or recipient["settings_revision"] != device["revision"]:
            await cancel_campaign_pending(db, campaign, "CAMPAIGN_CONTEXT_CHANGED", include_unadmitted=True)
            continue
        return await create_effects(db, device, campaign, dict(recipient), now, window[1])
    return []


async def create_effects(db, device, campaign, recipient, now, window_end):
    tenant, event_id = device["tenant_id"], uuid4()
    await db.execute(text("""INSERT INTO subscription_device_events
        (id,tenant_id,device_id,generation,provider_event_id,kind,occurred_at,payload)
        VALUES(:id,:t,:d,:g,:provider,'campaign',:now,CAST(:payload AS jsonb))"""),
        {"id": event_id, "t": tenant, "d": device["id"], "g": device["generation"],
         "provider": "campaign:" + str(recipient["id"]), "now": now,
         "payload": store.encode({"kind": "campaign", "origin": "campaign", "phone": recipient["phone"],
            "name": recipient["name"], "campaign_id": campaign["id"], "recipient_id": recipient["id"]})})
    ids, previous = [], None
    expiry = min(now + timedelta(minutes=10), window_end)
    for index, action in enumerate(recipient["plan"]):
        identity = uuid4()
        payload = {"phone": recipient["phone"], "action": action}
        store.bounded_json(payload, 131072)
        await db.execute(text("""INSERT INTO subscription_device_effects
            (id,tenant_id,device_id,generation,settings_revision,event_id,effect_key,type,purpose,payload,previous_effect,
             campaign_id,campaign_recipient_id,campaign_part_index,due_at,expires_at)
            VALUES(:id,:t,:d,:g,:r,:event,:key,'campaign_action','campaign_action',CAST(:payload AS jsonb),:previous,
              :campaign,:recipient,:part,:now,:expiry)"""),
            {"id": identity, "t": tenant, "d": device["id"], "g": device["generation"], "r": device["revision"],
             "event": event_id, "key": "campaign:" + str(index), "payload": store.encode(payload), "previous": previous,
             "campaign": campaign["id"], "recipient": recipient["id"], "part": index, "now": now, "expiry": expiry})
        ids.append(identity)
        previous = identity
    await db.execute(text("""UPDATE subscription_campaign_recipients SET status='running',admitted_at=:now
        WHERE tenant_id=:t AND id=:id AND status='pending' AND admitted_at IS NULL"""),
        {"t": tenant, "id": recipient["id"], "now": now})
    await db.execute(text("""UPDATE subscription_campaigns SET status='running',
        first_admitted_at=COALESCE(first_admitted_at,:now),last_admitted_at=:now,
        next_admission_at=:next,updated_at=:now WHERE tenant_id=:t AND id=:id"""),
        {"t": tenant, "id": campaign["id"], "now": now,
         "next": now + timedelta(seconds=campaign["definition"]["interval_seconds"])})
    return ids


async def effect_authority(db, device, effect, now):
    """Pure SQL authority. Also used before/after reading immutable asset bytes."""
    if not await feature_available(db, device["tenant_id"]):
        return "CAMPAIGN_REVOKED", None
    if (effect["type"] != "campaign_action" or effect["purpose"] != "campaign_action"
            or effect["tenant_id"] != device["tenant_id"] or effect["device_id"] != device["id"]
            or effect["generation"] != device["generation"] or effect["settings_revision"] != device["revision"]
            or device["removed_at"] is not None or device["status"] in {"disabled", "removing", "removed"}):
        return "CAMPAIGN_CONTEXT_CHANGED", None
    if effect["expires_at"] <= now:
        return "CAMPAIGN_EFFECT_EXPIRED", None
    campaign = await store.campaign_row(db, device["tenant_id"], effect["campaign_id"])
    recipient = (await db.execute(text("""SELECT * FROM subscription_campaign_recipients
        WHERE tenant_id=:t AND campaign_id=:c AND id=:id"""),
        {"t": device["tenant_id"], "c": campaign["id"], "id": effect["campaign_recipient_id"]})).mappings().one_or_none()
    if campaign["owner_account_id"] != device["owner_account_id"]:
        return "CAMPAIGN_REVOKED", None
    if campaign["archived_at"] or campaign["status"] == "cancelled":
        return "CAMPAIGN_CANCELLED", None
    if campaign["status"] == "paused":
        return "CAMPAIGN_PAUSED", None
    if campaign["status"] != "running" or not recipient or recipient["status"] != "running":
        return "CAMPAIGN_REVOKED", None
    if recipient["error_code"] in CANCELLATION_CODES:
        return recipient["error_code"], None
    part = effect["campaign_part_index"]
    if (recipient["run_version"] != campaign["run_version"] or recipient["admitted_at"] is None
            or recipient["device_id"] != device["id"] or recipient["generation"] != device["generation"]
            or recipient["settings_revision"] != device["revision"] or type(part) is not int
            or not 0 <= part < len(recipient["plan"])
            or effect["payload"] != {"phone": recipient["phone"], "action": recipient["plan"][part]}):
        return "CAMPAIGN_CONTEXT_CHANGED", None
    if policy.active_window(campaign["definition"], now) is None:
        return "CAMPAIGN_EFFECT_EXPIRED", None
    try:
        await check_frozen_context(db, campaign)
    except HTTPException as exc:
        return exc.detail.get("code", "CAMPAIGN_CONTEXT_CHANGED"), None
    allowed = (await db.execute(text("""SELECT COALESCE(p.revoked,false) AS revoked,COALESCE(p.version,0) AS version,
        EXISTS(SELECT 1 FROM subscription_campaign_members m JOIN subscription_campaign_groups g
          ON g.tenant_id=m.tenant_id AND g.id=m.group_id AND g.archived_at IS NULL
          WHERE m.tenant_id=:t AND m.phone=:phone AND m.group_id=ANY(CAST(:groups AS uuid[]))
            AND m.removed_at IS NULL) AS member FROM (SELECT 1) singleton
        LEFT JOIN subscription_campaign_permissions p ON p.tenant_id=:t AND p.phone=:phone"""),
        {"t": device["tenant_id"], "phone": recipient["phone"], "groups": recipient["source_group_ids"]})).mappings().one()
    if allowed["revoked"] or allowed["version"] != recipient["permission_version"]:
        return "CAMPAIGN_OPTED_OUT", None
    if not allowed["member"]:
        return "CAMPAIGN_MEMBER_REMOVED", None
    if part:
        previous = (await db.execute(text("""SELECT id,status FROM subscription_device_effects WHERE tenant_id=:t
            AND campaign_recipient_id=:id AND campaign_part_index=:part"""),
            {"t": device["tenant_id"], "id": recipient["id"], "part": part - 1})).mappings().one_or_none()
        if not previous or previous["id"] != effect["previous_effect"] or previous["status"] != "accepted":
            return "CAMPAIGN_PREDECESSOR_FAILED", None
    elif effect["previous_effect"] is not None:
        return "CAMPAIGN_CONTEXT_CHANGED", None
    from app.subscription_sent_messages import message_authority
    if not await message_authority(db, device, effect):
        return "CAMPAIGN_BUSY", None
    return None, campaign


async def authorize_campaign_effect(db, device, effect, owner, fence, payload):
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
    code, campaign = await effect_authority(db, device, effect, now)
    if code:
        if not payload.recheck and code not in {"CAMPAIGN_BUSY", "CAMPAIGN_PREDECESSOR_FAILED"}:
            await db.execute(text("""UPDATE subscription_device_effects SET status='not_sent',error_code=:code,
                finished_at=:now WHERE tenant_id=:t AND id=:id AND status='pending'"""),
                {"code": code, "now": now, "t": device["tenant_id"], "id": effect["id"]})
            await refresh_recipient(db, device["tenant_id"], effect["campaign_recipient_id"], now=now)
        return {**denied, "error_code": code}
    if not payload.recheck:
        await db.execute(text("""UPDATE subscription_device_effects SET status='running',attempts=1,authorized_at=:now,
            claimed_owner=:owner,claimed_fence=:fence WHERE tenant_id=:t AND id=:id AND status='pending'"""),
            {"now": now, "owner": owner, "fence": fence, "t": device["tenant_id"], "id": effect["id"]})
    return jsonable_encoder({"authorized": True, "operation_id": effect["id"], "type": "campaign_action",
        "purpose": "campaign_action", "device_id": device["id"], "generation": device["generation"],
        "settings_revision": device["revision"], "campaign_id": campaign["id"],
        "recipient_id": effect["campaign_recipient_id"], "run_version": campaign["run_version"],
        "part_index": effect["campaign_part_index"], "payload": effect["payload"]})


async def after_campaign_effect_finish(db, device, effect, state):
    # The caller has already stored the authenticated receipt. Read that row;
    # never rewrite its terminal ledger or infer another attempt from failure.
    await refresh_recipient(db, device["tenant_id"], effect["campaign_recipient_id"])
    return []
