"""Tenant-serialized scheduled occurrences and final transport authority.

Only authenticated device polling admits work. Each recipient has one frozen
sequence; an uncertain transport result is evidence, never a retry request.
Recurring occurrences clone the preceding frozen audience and cannot add a
contact discovered later. Public reads never invoke these mutators.
"""
from datetime import date, timedelta
import re
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text

from app import subscription_schedule_policy as policy
from app import subscription_schedule_store as store

CANCELLATION_CODES = frozenset({
    "SCHEDULE_CANCELLED", "SCHEDULE_OPTED_OUT", "SCHEDULE_SOURCE_REMOVED",
    "SCHEDULE_REVOKED", "SCHEDULE_CONTEXT_CHANGED", "SCHEDULE_TEMPLATE_CHANGED",
    "SCHEDULE_SELF_CONTACT",
})
TERMINAL = frozenset({"completed", "failed", "uncertain", "cancelled"})
MAX_EXPANDED_BYTES = 64 * 1024 * 1024


def verified_self_phone(device):
    runtime = device.get("runtime") or {}
    if not isinstance(runtime, dict):
        return None
    phone = runtime.get("phone")
    return (phone if runtime.get("phone_verified") is True and isinstance(phone, str)
            and re.fullmatch(r"[1-9][0-9]{7,14}", phone) else None)


async def feature_available(db, tenant):
    try:
        await store.shared.workspace(db, tenant, feature=store.FEATURE)
    except HTTPException as exc:
        if exc.status_code == 403:
            return False
        raise
    return True


async def check_frozen_context(db, schedule, run):
    snapshot = run.get("snapshot") or {}
    frozen, consent = snapshot.get("device") or {}, snapshot.get("consent") or {}
    if (consent.get("confirmed") is not True
            or consent.get("actor_account_id") != str(schedule["owner_account_id"])):
        raise store.fail("SCHEDULE_REVOKED")
    if (run["tenant_id"] != schedule["tenant_id"] or run["schedule_id"] != schedule["id"]
            or run["schedule_version"] != schedule["version"]
            or run["run_version"] != schedule["run_version"]
            or frozen.get("id") != str(schedule["device_id"])):
        raise store.fail("SCHEDULE_CONTEXT_CHANGED")
    try:
        row, channel = await store.shared.check_device(
            db, schedule["tenant_id"], UUID(frozen["id"]), actor_id=schedule["owner_account_id"])
    except (HTTPException, KeyError, TypeError, ValueError):
        raise store.fail("SCHEDULE_CONTEXT_CHANGED") from None
    if (str(row["generation"]) != frozen.get("generation")
            or row["revision"] != frozen.get("settings_revision")
            or str(row["channel_id"]) != frozen.get("channel_id")
            or str(row["owner_account_id"]) != frozen.get("owner_account_id")
            or row["status"] in {"disabled", "removing", "removed"} or not channel["enabled"]):
        raise store.fail("SCHEDULE_CONTEXT_CHANGED")
    if schedule["template_id"]:
        current = await store.template_snapshot(db, schedule["tenant_id"], schedule["template_id"])
        if (current != snapshot.get("template")
                or current["revision"] != schedule["template_revision"]):
            raise store.fail("SCHEDULE_TEMPLATE_CHANGED")
    elif snapshot.get("template") is not None:
        raise store.fail("SCHEDULE_CONTEXT_CHANGED")


def past_due_without_confirmation(run, now):
    day = run["local_date"]
    if isinstance(day, str):
        day = date.fromisoformat(day)
    return (run["first_admitted_at"] is None and run["manual_released_at"] is None
            and day < now.astimezone(ZoneInfo(run["timezone"])).date())


def admission_ready(now, tenant_finished, run_finished, interval, recent):
    """Both modules share capacity; spacing starts after the full sequence."""
    return (type(interval) is int and interval in {10, 20, 30, 40, 50, 60} and recent < 10
            and (tenant_finished is None or now >= tenant_finished + timedelta(seconds=6))
            and (run_finished is None or now >= run_finished + timedelta(seconds=interval)))


async def complete_runs(db, tenant, run_id=None, *, now=None, force=False):
    """Project only the current frozen revision, preserving terminal receipts.

    A future audience that becomes entirely revoked remains visible for
    attention until due. This avoids creating and cancelling an unbounded chain
    of future recurrences in one poll. Explicit cancellation may close it now.
    """
    now = now or await store.clock(db)
    await db.execute(text("""WITH summaries AS (
      SELECT u.id,count(r.id) AS total,
        count(r.id) FILTER(WHERE r.status IN ('pending','running')) AS outstanding,
        count(r.id) FILTER(WHERE r.status='uncertain') AS uncertain,
        count(r.id) FILTER(WHERE r.status='failed') AS failed,
        count(r.id) FILTER(WHERE r.status='cancelled') AS cancelled,
        (array_agg(r.error_code ORDER BY r.ordinal) FILTER(WHERE r.error_code IS NOT NULL))[1] AS error
      FROM subscription_schedule_runs u LEFT JOIN subscription_schedule_recipients r
        ON r.tenant_id=u.tenant_id AND r.schedule_id=u.schedule_id AND r.run_id=u.id
          AND r.run_version=u.run_version
      WHERE u.tenant_id=:t AND (CAST(:run AS uuid) IS NULL OR u.id=CAST(:run AS uuid))
        AND (u.status IN ('pending','running') OR CAST(:run AS uuid) IS NOT NULL)
      GROUP BY u.id
    ), desired AS (
      SELECT u.id,CASE
          WHEN z.outstanding>0 THEN CASE WHEN u.first_admitted_at IS NULL THEN 'pending' ELSE 'running' END
          WHEN NOT CAST(:force AS boolean) AND u.first_admitted_at IS NULL
            AND (u.due_at IS NULL OR u.due_at>CAST(:now AS timestamptz)) THEN 'pending'
          WHEN z.uncertain>0 THEN 'uncertain'
          WHEN z.failed>0 OR z.total=0 THEN 'failed'
          WHEN u.error_code=ANY(CAST(:cancellations AS varchar[])) OR z.cancelled=z.total THEN 'cancelled'
          ELSE 'completed' END AS status,
        CASE WHEN u.due_at IS NULL THEN 'SCHEDULE_RECURRENCE_INVALID'
          WHEN z.outstanding>0 THEN u.error_code
          WHEN z.uncertain>0 THEN 'MESSAGE_OUTCOME_UNCERTAIN'
          WHEN z.total=0 THEN 'SCHEDULE_CONTEXT_CHANGED'
          WHEN u.error_code=ANY(CAST(:cancellations AS varchar[])) THEN u.error_code
          WHEN z.failed>0 OR z.cancelled=z.total THEN z.error
          ELSE NULL END AS error_code
      FROM subscription_schedule_runs u JOIN summaries z ON z.id=u.id WHERE u.tenant_id=:t
    ), final AS (
      SELECT u.id,d.status,d.error_code,
        CASE WHEN d.status IN ('pending','running') THEN NULL ELSE COALESCE(u.finished_at,:now) END AS finished_at
      FROM subscription_schedule_runs u JOIN desired d ON d.id=u.id WHERE u.tenant_id=:t
    ) UPDATE subscription_schedule_runs u SET status=f.status,error_code=f.error_code,
        finished_at=f.finished_at,version=u.version+1,updated_at=:now
      FROM final f WHERE u.tenant_id=:t AND u.id=f.id AND
        (u.status IS DISTINCT FROM f.status OR u.error_code IS DISTINCT FROM f.error_code
          OR u.finished_at IS DISTINCT FROM f.finished_at)"""),
        {"t": tenant, "run": run_id, "now": now, "force": force, "cancellations": sorted(CANCELLATION_CODES)})
    await db.execute(text("""UPDATE subscription_schedules s SET status='completed',version=version+1,updated_at=:now
        WHERE s.tenant_id=:t AND s.status='active' AND NOT s.repeat_every_30_days
          AND EXISTS(SELECT 1 FROM subscription_schedule_runs u WHERE u.tenant_id=s.tenant_id AND u.schedule_id=s.id)
          AND NOT EXISTS(SELECT 1 FROM subscription_schedule_runs u WHERE u.tenant_id=s.tenant_id
            AND u.schedule_id=s.id AND u.status IN ('pending','running'))"""), {"t": tenant, "now": now})


async def refresh_recipient(db, tenant, identity, *, now=None):
    recipient = (await db.execute(text("""SELECT * FROM subscription_schedule_recipients
        WHERE tenant_id=:t AND id=:id FOR UPDATE"""), {"t": tenant, "id": identity})).mappings().one()
    parts = [dict(row) for row in (await db.execute(text("""SELECT * FROM subscription_device_effects
        WHERE tenant_id=:t AND schedule_recipient_id=:id ORDER BY schedule_part_index"""),
        {"t": tenant, "id": identity})).mappings().all()]
    now = now or await store.clock(db)
    if any(item["status"] in {"failed", "not_sent", "uncertain"} for item in parts):
        await db.execute(text("""UPDATE subscription_device_effects SET status='not_sent',
            error_code='SCHEDULE_PREDECESSOR_FAILED',finished_at=:now
            WHERE tenant_id=:t AND schedule_recipient_id=:id AND status='pending'"""),
            {"t": tenant, "id": identity, "now": now})
        for item in parts:
            if item["status"] == "pending":
                item.update(status="not_sent", error_code="SCHEDULE_PREDECESSOR_FAILED", finished_at=now)
    status, error = policy.recipient_state(recipient, parts, now)
    internal = "running" if status == "pending" and recipient["admitted_at"] else status
    if internal == "running" and recipient["error_code"] in CANCELLATION_CODES:
        error = recipient["error_code"]
    await db.execute(text("""UPDATE subscription_schedule_recipients SET status=:status,error_code=:error,
        finished_at=CASE WHEN CAST(:status AS varchar) IN ('pending','running') THEN NULL
          ELSE COALESCE(finished_at,:now) END WHERE tenant_id=:t AND id=:id"""),
        {"status": internal, "error": error, "now": now, "t": tenant, "id": identity})
    await complete_runs(db, tenant, recipient["run_id"], now=now)


async def reconcile_terminal_recipients(db, tenant, *, now, run_id=None):
    """Project effects already closed by a settings change or device removal.

    Revocation can finish the effect outside this runtime. Reconcile across
    the tenant, including removed devices, before consulting the shared live
    sequence gate. A real pending or running part is never closed here.
    """
    identities = (await db.execute(text("""SELECT r.id FROM subscription_schedule_recipients r
        WHERE r.tenant_id=:t AND r.status='running' AND r.admitted_at IS NOT NULL
          AND (CAST(:run AS uuid) IS NULL OR r.run_id=CAST(:run AS uuid))
          AND EXISTS(SELECT 1 FROM subscription_device_effects e
            WHERE e.tenant_id=r.tenant_id AND e.schedule_recipient_id=r.id)
          AND NOT EXISTS(SELECT 1 FROM subscription_device_effects e
            WHERE e.tenant_id=r.tenant_id AND e.schedule_recipient_id=r.id
              AND e.status IN ('pending','running'))
        ORDER BY r.admitted_at,r.id FOR UPDATE OF r"""),
        {"t": tenant, "run": run_id})).scalars().all()
    for identity in identities:
        await refresh_recipient(db, tenant, identity, now=now)


async def cancel_schedule_pending(db, schedule, code, *, run_id=None, include_unadmitted=True):
    tenant, now = schedule["tenant_id"], await store.clock(db)
    runs = (await db.execute(text("""SELECT * FROM subscription_schedule_runs
        WHERE tenant_id=:t AND schedule_id=:schedule
          AND (CAST(:run AS uuid) IS NULL OR id=CAST(:run AS uuid))
          AND status IN ('pending','running') ORDER BY occurrence_index FOR UPDATE"""),
        {"t": tenant, "schedule": schedule["id"], "run": run_id})).mappings().all()
    for run in runs:
        params = {"t": tenant, "run": run["id"], "code": code, "now": now}
        if include_unadmitted:
            await db.execute(text("""UPDATE subscription_schedule_recipients SET status='cancelled',
                error_code=:code,finished_at=:now WHERE tenant_id=:t AND run_id=:run
                  AND status='pending' AND admitted_at IS NULL"""), params)
        await db.execute(text("""UPDATE subscription_schedule_recipients SET error_code=:code
            WHERE tenant_id=:t AND run_id=:run AND status='running' AND admitted_at IS NOT NULL"""), params)
        affected = (await db.execute(text("""UPDATE subscription_device_effects SET status='not_sent',
            error_code=:code,finished_at=:now WHERE tenant_id=:t AND schedule_run_id=:run AND status='pending'
            RETURNING schedule_recipient_id"""), params)).scalars().all()
        # Keep the local-time failure when cancelling a DST placeholder. Its
        # missing UTC instant must never be replaced just to satisfy a guard.
        await db.execute(text("""UPDATE subscription_schedule_runs SET
            error_code=CASE WHEN due_at IS NULL THEN 'SCHEDULE_RECURRENCE_INVALID' ELSE :code END,
            version=version+1,updated_at=:now WHERE tenant_id=:t AND id=:run"""), params)
        for identity in set(affected):
            await refresh_recipient(db, tenant, identity, now=now)
        await reconcile_terminal_recipients(db, tenant, now=now, run_id=run["id"])
        await complete_runs(db, tenant, run["id"], now=now, force=include_unadmitted)


SELF_CONTACT_SQL = """(d.runtime->'phone_verified'='true'::jsonb
    AND d.runtime->>'phone' ~ '^[1-9][0-9]{7,14}$' AND d.runtime->>'phone'=r.phone)"""

INVALID_AUDIENCE = """SELECT r.id,r.run_id,CASE WHEN r.run_version<>u.run_version
        THEN 'SCHEDULE_CONTEXT_CHANGED'
        WHEN """ + SELF_CONTACT_SQL + """ THEN 'SCHEDULE_SELF_CONTACT'
        WHEN COALESCE(p.revoked,false) OR COALESCE(p.version,0)<>r.permission_version
          THEN 'SCHEDULE_OPTED_OUT' ELSE 'SCHEDULE_SOURCE_REMOVED' END AS code
    FROM subscription_schedule_recipients r
    JOIN subscription_schedule_runs u ON u.tenant_id=r.tenant_id AND u.schedule_id=r.schedule_id AND u.id=r.run_id
    LEFT JOIN subscription_devices d ON d.tenant_id=r.tenant_id AND d.id=r.device_id
    LEFT JOIN subscription_campaign_permissions p ON p.tenant_id=r.tenant_id AND p.phone=r.phone
    WHERE r.tenant_id=:t AND r.status IN ('pending','running') AND
      (r.run_version<>u.run_version OR """ + SELF_CONTACT_SQL + """ OR COALESCE(p.revoked,false)
       OR COALESCE(p.version,0)<>r.permission_version OR NOT (""" + store.SOURCE_VALID_SQL + "))"


async def invalidate_schedule_contacts(db, tenant):
    # No per-contact reads: a source mutation closes the old frozen audience in
    # the same tenant transaction. Later re-addition cannot erase this marker.
    await db.execute(text("WITH invalid AS (" + INVALID_AUDIENCE + """)
        UPDATE subscription_schedule_recipients r SET status='cancelled',error_code=i.code,
          finished_at=clock_timestamp() FROM invalid i
        WHERE r.tenant_id=:t AND r.id=i.id AND r.admitted_at IS NULL"""), {"t": tenant})
    await db.execute(text("WITH invalid AS (" + INVALID_AUDIENCE + """)
        UPDATE subscription_schedule_recipients r SET error_code=i.code FROM invalid i
        WHERE r.tenant_id=:t AND r.id=i.id AND r.admitted_at IS NOT NULL AND r.status='running'"""), {"t": tenant})
    affected = (await db.execute(text("WITH invalid AS (" + INVALID_AUDIENCE + """)
        UPDATE subscription_device_effects e SET status='not_sent',error_code=i.code,finished_at=clock_timestamp()
        FROM invalid i WHERE e.tenant_id=:t AND e.schedule_recipient_id=i.id AND e.status='pending'
        RETURNING e.schedule_recipient_id"""), {"t": tenant})).scalars().all()
    for identity in set(affected):
        await refresh_recipient(db, tenant, identity)
    await complete_runs(db, tenant)


async def reap_schedules(db, tenant, now):
    affected = (await db.execute(text("""UPDATE subscription_device_effects SET
        status=CASE WHEN status='running' THEN 'uncertain' ELSE 'not_sent' END,
        error_code=CASE WHEN status='running' THEN 'MESSAGE_OUTCOME_UNCERTAIN' ELSE 'SCHEDULE_EFFECT_EXPIRED' END,
        finished_at=:now WHERE tenant_id=:t AND type='scheduled_action'
          AND (status='pending' AND expires_at<=:now
            OR status='running' AND authorized_at<CAST(:now AS timestamptz)-interval '180 seconds')
        RETURNING schedule_recipient_id"""), {"t": tenant, "now": now})).scalars().all()
    for identity in set(affected):
        await refresh_recipient(db, tenant, identity, now=now)
    await reconcile_terminal_recipients(db, tenant, now=now)


async def materialize_next_occurrences(db, device, now):
    """One strictly future cycle per series; no live catalog expansion."""
    tenant = device["tenant_id"]
    candidates = (await db.execute(text("""SELECT s.id FROM subscription_schedules s
        JOIN LATERAL(SELECT u.status FROM subscription_schedule_runs u
          WHERE u.tenant_id=s.tenant_id AND u.schedule_id=s.id ORDER BY u.occurrence_index DESC LIMIT 1) last ON true
        WHERE s.tenant_id=:t AND s.device_id=:d AND s.owner_account_id=:owner
          AND s.status='active' AND s.repeat_every_30_days
          AND last.status IN ('completed','failed','uncertain','cancelled')
        ORDER BY s.created_at,s.id LIMIT 25"""),
        {"t": tenant, "d": device["id"], "owner": device["owner_account_id"]})).scalars().all()
    for identity in candidates:
        schedule = await store.schedule_row(db, tenant, identity)
        previous = (await db.execute(text("""SELECT * FROM subscription_schedule_runs
            WHERE tenant_id=:t AND schedule_id=:schedule ORDER BY occurrence_index DESC LIMIT 1 FOR UPDATE"""),
            {"t": tenant, "schedule": identity})).mappings().one()
        if previous["status"] not in TERMINAL:
            continue
        anchor = {"start_date": schedule["anchor_date"], "start_time": schedule["anchor_time"],
                  "timezone": schedule["timezone"]}
        try:
            index, day, due = policy.next_occurrence(anchor, now, previous["occurrence_index"])
        except ValueError:
            continue  # No representable calendar date: never invent a UTC time.
        snapshot = previous["snapshot"]
        store.bounded_json(snapshot, 1048576)
        run_id = uuid4()
        # A single statement snapshots eligibility, budgets immutable content,
        # creates the occurrence and clones its rows. A failed budget writes
        # neither occurrence nor recipient; no orphan or partially cloned run.
        created = (await db.execute(text("""WITH source AS MATERIALIZED (
          SELECT gen_random_uuid() AS new_id,r.*,CASE
            WHEN r.error_code='SCHEDULE_SELF_CONTACT' OR r.phone=CAST(:self_phone AS varchar)
              THEN 'SCHEDULE_SELF_CONTACT'
            WHEN COALESCE(p.revoked,false)
              OR COALESCE(p.version,0)<>r.permission_version THEN 'SCHEDULE_OPTED_OUT'
            WHEN NOT (""" + store.SOURCE_VALID_SQL + """) THEN 'SCHEDULE_SOURCE_REMOVED'
            ELSE NULL END AS invalid_code
          FROM subscription_schedule_recipients r LEFT JOIN subscription_campaign_permissions p
            ON p.tenant_id=r.tenant_id AND p.phone=r.phone
          WHERE r.tenant_id=:t AND r.schedule_id=:schedule AND r.run_id=:previous AND r.run_version=:hash
        ), budget AS (
          SELECT count(*) AS total,COALESCE(sum(octet_length(jsonb_build_object(
            'id',new_id,'ordinal',ordinal,'phone',phone,'name',name,'origins',origins,
            'permission_version',permission_version,'device_id',device_id,'generation',generation,
            'settings_revision',settings_revision,'plan',plan,'plan_hash',plan_hash)::text)),0) AS bytes
          FROM source
        ), new_run AS (
          INSERT INTO subscription_schedule_runs(id,tenant_id,schedule_id,occurrence_index,schedule_version,
            run_version,due_at,local_date,timezone,snapshot,next_admission_at,error_code,source_run_id,created_at,updated_at)
          SELECT :run,:t,:schedule,:index,:version,:hash,:due,:day,:zone,CAST(:snapshot AS jsonb),:due,:error,:previous,:now,:now
          FROM budget WHERE total BETWEEN 1 AND 10000 AND bytes<=:maximum RETURNING id
        ), copied AS (
          INSERT INTO subscription_schedule_recipients(id,tenant_id,schedule_id,run_id,run_version,ordinal,phone,name,
            origins,permission_version,device_id,generation,settings_revision,plan,plan_hash,status,finished_at,error_code,created_at)
          SELECT r.new_id,:t,:schedule,u.id,:hash,r.ordinal,r.phone,r.name,r.origins,r.permission_version,
            r.device_id,r.generation,r.settings_revision,r.plan,r.plan_hash,
            CASE WHEN r.invalid_code IS NULL THEN 'pending' ELSE 'cancelled' END,
            CASE WHEN r.invalid_code IS NULL THEN NULL ELSE CAST(:now AS timestamptz) END,r.invalid_code,:now
          FROM source r CROSS JOIN new_run u RETURNING id
        ) SELECT u.id FROM new_run u CROSS JOIN budget b WHERE (SELECT count(*) FROM copied)=b.total"""),
            {"t": tenant, "schedule": identity, "previous": previous["id"], "hash": previous["run_version"],
             "run": run_id, "index": index, "version": schedule["version"], "due": due, "day": day,
             "zone": schedule["timezone"], "snapshot": store.encode(snapshot), "now": now,
             "error": "SCHEDULE_RECURRENCE_INVALID" if due is None else None, "maximum": MAX_EXPANDED_BYTES,
             "self_phone": verified_self_phone(device)})).scalar_one_or_none()
        if created is not None:
            await complete_runs(db, tenant, created, now=now)


async def admit_due_schedules(db, device):
    tenant = device["tenant_id"]
    if not await feature_available(db, tenant):
        return []
    now = await store.clock(db)
    await reap_schedules(db, tenant, now)
    await invalidate_schedule_contacts(db, tenant)
    await materialize_next_occurrences(db, device, now)
    if device["status"] != "connected":
        return []
    live = (await db.execute(text("""SELECT id FROM (
        SELECT id FROM subscription_schedule_recipients WHERE tenant_id=:t AND admitted_at IS NOT NULL AND status='running'
        UNION ALL SELECT id FROM subscription_campaign_recipients WHERE tenant_id=:t AND admitted_at IS NOT NULL AND status='running'
        UNION ALL SELECT id FROM subscription_bulk_recipients WHERE tenant_id=:t AND admitted_at IS NOT NULL AND status='running'
        ) live LIMIT 1"""), {"t": tenant})).scalar_one_or_none()
    if live is not None:
        return []
    pace = (await db.execute(text("""SELECT max(finished_at) AS finished,
        count(*) FILTER(WHERE admitted_at>CAST(:now AS timestamptz)-interval '60 seconds') AS recent FROM (
          SELECT finished_at,admitted_at FROM subscription_schedule_recipients WHERE tenant_id=:t AND admitted_at IS NOT NULL
          UNION ALL SELECT finished_at,admitted_at FROM subscription_campaign_recipients WHERE tenant_id=:t AND admitted_at IS NOT NULL
          UNION ALL SELECT finished_at,admitted_at FROM subscription_bulk_recipients WHERE tenant_id=:t AND admitted_at IS NOT NULL
        ) pace"""), {"t": tenant, "now": now})).mappings().one()
    if not admission_ready(now, pace["finished"], None, 10, pace["recent"]):
        return []
    candidates = (await db.execute(text("""SELECT u.* FROM subscription_schedule_runs u
        JOIN subscription_schedules s ON s.tenant_id=u.tenant_id AND s.id=u.schedule_id
        WHERE u.tenant_id=:t AND s.device_id=:d AND s.status='active'
          AND u.status IN ('pending','running') AND u.due_at<=:now
          AND COALESCE(u.next_admission_at,u.due_at)<=:now
          AND (u.first_admitted_at IS NOT NULL OR u.manual_released_at IS NOT NULL
            OR u.local_date >= (CAST(:now AS timestamptz) AT TIME ZONE u.timezone)::date)
          AND EXISTS(SELECT 1 FROM subscription_schedule_recipients r WHERE r.tenant_id=u.tenant_id
            AND r.schedule_id=u.schedule_id AND r.run_id=u.id AND r.run_version=u.run_version
            AND r.device_id=:d AND r.status='pending' AND r.admitted_at IS NULL)
        ORDER BY u.due_at,u.occurrence_index,u.id LIMIT 25"""),
        {"t": tenant, "d": device["id"], "now": now})).mappings().all()
    for raw in candidates:
        run = dict(raw)
        if run["due_at"] is None or past_due_without_confirmation(run, now):
            continue
        schedule = await store.schedule_row(db, tenant, run["schedule_id"])
        try:
            if schedule["owner_account_id"] != device["owner_account_id"]:
                raise store.fail("SCHEDULE_REVOKED")
            await check_frozen_context(db, schedule, run)
        except HTTPException as exc:
            code = exc.detail.get("code", "SCHEDULE_CONTEXT_CHANGED") if isinstance(exc.detail, dict) else "SCHEDULE_CONTEXT_CHANGED"
            await cancel_schedule_pending(db, schedule, code, run_id=run["id"])
            continue
        finished = (await db.execute(text("""SELECT max(finished_at) FROM subscription_schedule_recipients
            WHERE tenant_id=:t AND run_id=:run AND run_version=:hash AND admitted_at IS NOT NULL"""),
            {"t": tenant, "run": run["id"], "hash": run["run_version"]})).scalar_one()
        if not admission_ready(now, pace["finished"], finished, schedule["interval_seconds"], pace["recent"]):
            continue
        recipient = (await db.execute(text("""SELECT r.* FROM subscription_schedule_recipients r
            WHERE r.tenant_id=:t AND r.schedule_id=:schedule AND r.run_id=:run AND r.run_version=:hash
              AND r.device_id=:d AND r.status='pending' AND r.admitted_at IS NULL
              AND NOT EXISTS(SELECT 1 FROM subscription_device_effects e WHERE e.tenant_id=r.tenant_id
                AND e.device_id=r.device_id AND e.payload->>'phone'=r.phone
                AND e.type IN ('send_text','chatbot_action','campaign_action','scheduled_action','bulk_action')
                AND e.status IN ('pending','running','uncertain') AND e.expires_at>:now)
            ORDER BY r.ordinal,r.id LIMIT 1 FOR UPDATE OF r"""),
            {"t": tenant, "schedule": schedule["id"], "run": run["id"], "hash": run["run_version"],
             "d": device["id"], "now": now})).mappings().one_or_none()
        if recipient is None:
            continue
        if (recipient["generation"] != device["generation"] or recipient["settings_revision"] != device["revision"]
                or store.fingerprint(recipient["plan"]) != recipient["plan_hash"]):
            await cancel_schedule_pending(db, schedule, "SCHEDULE_CONTEXT_CHANGED", run_id=run["id"])
            continue
        return await create_effects(db, device, schedule, run, dict(recipient), now)
    return []


async def create_effects(db, device, schedule, run, recipient, now):
    tenant, event_id = device["tenant_id"], uuid4()
    await db.execute(text("""INSERT INTO subscription_device_events
        (id,tenant_id,device_id,generation,provider_event_id,kind,occurred_at,payload)
        VALUES(:id,:t,:d,:g,:provider,'scheduled',:now,CAST(:payload AS jsonb))"""),
        {"id": event_id, "t": tenant, "d": device["id"], "g": device["generation"],
         "provider": "scheduled:" + str(recipient["id"]), "now": now,
         "payload": store.encode({"kind": "scheduled", "origin": "scheduled", "phone": recipient["phone"],
             "name": recipient["name"], "schedule_id": schedule["id"], "run_id": run["id"], "recipient_id": recipient["id"]})})
    ids, previous = [], None
    for index, action in enumerate(recipient["plan"]):
        identity = uuid4()
        payload = {"phone": recipient["phone"], "action": action}
        store.bounded_json(payload, 131072)
        await db.execute(text("""INSERT INTO subscription_device_effects
            (id,tenant_id,device_id,generation,settings_revision,event_id,effect_key,type,purpose,payload,previous_effect,
             schedule_id,schedule_run_id,schedule_recipient_id,schedule_part_index,due_at,expires_at)
            VALUES(:id,:t,:d,:g,:r,:event,:key,'scheduled_action','scheduled_action',CAST(:payload AS jsonb),:previous,
              :schedule,:run,:recipient,:part,:now,:expiry)"""),
            {"id": identity, "t": tenant, "d": device["id"], "g": device["generation"], "r": device["revision"],
             "event": event_id, "key": "scheduled:" + str(index), "payload": store.encode(payload), "previous": previous,
             "schedule": schedule["id"], "run": run["id"], "recipient": recipient["id"], "part": index,
             "now": now, "expiry": now + timedelta(minutes=10)})
        ids.append(identity)
        previous = identity
    await db.execute(text("""UPDATE subscription_schedule_recipients SET status='running',admitted_at=:now
        WHERE tenant_id=:t AND id=:id AND run_id=:run AND run_version=:hash
          AND status='pending' AND admitted_at IS NULL"""),
        {"t": tenant, "id": recipient["id"], "run": run["id"], "hash": run["run_version"], "now": now})
    await db.execute(text("""UPDATE subscription_schedule_runs SET status='running',
        first_admitted_at=COALESCE(first_admitted_at,:now),last_admitted_at=:now,
        next_admission_at=:next,version=version+1,updated_at=:now WHERE tenant_id=:t AND id=:run"""),
        {"t": tenant, "run": run["id"], "now": now, "next": now + timedelta(seconds=schedule["interval_seconds"])})
    return ids


async def schedule_effect_authority(db, device, effect, now):
    """Pure authority, also checked before and after reading private file bytes."""
    if not await feature_available(db, device["tenant_id"]):
        return "SCHEDULE_REVOKED", None
    if (effect["type"] != "scheduled_action" or effect["purpose"] != "scheduled_action"
            or effect["tenant_id"] != device["tenant_id"] or effect["device_id"] != device["id"]
            or effect["generation"] != device["generation"] or effect["settings_revision"] != device["revision"]
            or device["removed_at"] is not None or device["status"] in {"disabled", "removing", "removed"}):
        return "SCHEDULE_CONTEXT_CHANGED", None
    if effect["expires_at"] <= now:
        return "SCHEDULE_EFFECT_EXPIRED", None
    try:
        schedule = await store.schedule_row(db, device["tenant_id"], effect["schedule_id"])
        run = await store.run_row(db, device["tenant_id"], effect["schedule_run_id"])
    except HTTPException:
        return "SCHEDULE_CONTEXT_CHANGED", None
    recipient = (await db.execute(text("""SELECT * FROM subscription_schedule_recipients
        WHERE tenant_id=:t AND schedule_id=:schedule AND run_id=:run AND id=:id"""),
        {"t": device["tenant_id"], "schedule": schedule["id"], "run": run["id"],
         "id": effect["schedule_recipient_id"]})).mappings().one_or_none()
    if schedule["owner_account_id"] != device["owner_account_id"]:
        return "SCHEDULE_REVOKED", None
    if schedule["status"] == "cancelled" or run["status"] == "cancelled":
        return "SCHEDULE_CANCELLED", None
    if (schedule["status"] != "active" or run["status"] != "running" or not recipient
            or recipient["status"] != "running"):
        return "SCHEDULE_REVOKED", None
    if run["error_code"] in CANCELLATION_CODES or recipient["error_code"] in CANCELLATION_CODES:
        return run["error_code"] if run["error_code"] in CANCELLATION_CODES else recipient["error_code"], None
    if recipient["phone"] == verified_self_phone(device):
        return "SCHEDULE_SELF_CONTACT", None
    part = effect["schedule_part_index"]
    if (run["schedule_id"] != schedule["id"] or recipient["run_version"] != run["run_version"]
            or recipient["admitted_at"] is None or run["first_admitted_at"] is None
            or recipient["device_id"] != device["id"] or recipient["generation"] != device["generation"]
            or recipient["settings_revision"] != device["revision"] or type(part) is not int
            or not 0 <= part < len(recipient["plan"])
            or store.fingerprint(recipient["plan"]) != recipient["plan_hash"]
            or effect["payload"] != {"phone": recipient["phone"], "action": recipient["plan"][part]}):
        return "SCHEDULE_CONTEXT_CHANGED", None
    # A sequence that was admitted on its due day remains eligible across
    # midnight. Only a not-yet-started old occurrence needs explicit release.
    if run["due_at"] is None or run["due_at"] > now or past_due_without_confirmation(run, now):
        return "SCHEDULE_REVOKED", None
    try:
        await check_frozen_context(db, schedule, run)
    except HTTPException as exc:
        return exc.detail.get("code", "SCHEDULE_CONTEXT_CHANGED") if isinstance(exc.detail, dict) else "SCHEDULE_CONTEXT_CHANGED", None
    permission = (await db.execute(text("""SELECT COALESCE(p.revoked,false) AS revoked,COALESCE(p.version,0) AS version
        FROM (SELECT 1) singleton LEFT JOIN subscription_campaign_permissions p
          ON p.tenant_id=:t AND p.phone=:phone"""),
        {"t": device["tenant_id"], "phone": recipient["phone"]})).mappings().one()
    if permission["revoked"] or permission["version"] != recipient["permission_version"]:
        return "SCHEDULE_OPTED_OUT", None
    if not await store.sources_valid(db, device["tenant_id"], recipient["origins"], recipient["phone"]):
        return "SCHEDULE_SOURCE_REMOVED", None
    if part:
        previous = (await db.execute(text("""SELECT id,status FROM subscription_device_effects
            WHERE tenant_id=:t AND schedule_id=:schedule AND schedule_run_id=:run
              AND schedule_recipient_id=:id AND schedule_part_index=:part"""),
            {"t": device["tenant_id"], "schedule": schedule["id"], "run": run["id"],
             "id": recipient["id"], "part": part - 1})).mappings().one_or_none()
        if not previous or previous["id"] != effect["previous_effect"] or previous["status"] != "accepted":
            return "SCHEDULE_PREDECESSOR_FAILED", None
    elif effect["previous_effect"] is not None:
        return "SCHEDULE_CONTEXT_CHANGED", None
    from app.subscription_sent_messages import message_authority
    if not await message_authority(db, device, effect):
        return "SCHEDULE_BUSY", None
    return None, run


async def authorize_schedule_effect(db, device, effect, owner, fence, payload):
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
    code, run = await schedule_effect_authority(db, device, effect, now)
    if code:
        if not payload.recheck and code not in {"SCHEDULE_BUSY", "SCHEDULE_PREDECESSOR_FAILED"}:
            await db.execute(text("""UPDATE subscription_device_effects SET status='not_sent',error_code=:code,
                finished_at=:now WHERE tenant_id=:t AND id=:id AND status='pending'"""),
                {"code": code, "now": now, "t": device["tenant_id"], "id": effect["id"]})
            await refresh_recipient(db, device["tenant_id"], effect["schedule_recipient_id"], now=now)
        return {**denied, "error_code": code}
    if not payload.recheck:
        await db.execute(text("""UPDATE subscription_device_effects SET status='running',attempts=1,authorized_at=:now,
            claimed_owner=:owner,claimed_fence=:fence WHERE tenant_id=:t AND id=:id AND status='pending'"""),
            {"now": now, "owner": owner, "fence": fence, "t": device["tenant_id"], "id": effect["id"]})
    return jsonable_encoder({"authorized": True, "operation_id": effect["id"], "type": "scheduled_action",
        "purpose": "scheduled_action", "device_id": device["id"], "generation": device["generation"],
        "settings_revision": device["revision"], "schedule_id": effect["schedule_id"], "run_id": run["id"],
        "recipient_id": effect["schedule_recipient_id"], "run_version": run["run_version"],
        "part_index": effect["schedule_part_index"], "payload": effect["payload"]})


async def after_schedule_effect_finish(db, device, effect, state):
    # The caller has persisted the authenticated receipt. Read its real state;
    # never overwrite the transport ledger or manufacture another attempt.
    await refresh_recipient(db, device["tenant_id"], effect["schedule_recipient_id"])
    return []
