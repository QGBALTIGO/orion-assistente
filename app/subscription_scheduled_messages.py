"""Owner-confirmed scheduled messages; public reads never admit transport work."""
import asyncio
from datetime import date, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from sqlalchemy import text

from app.catalog_api import Actor, authenticate
from app import qr_channels as channels
from app import subscription_schedule_policy as policy
from app import subscription_schedule_schemas as schemas
from app import subscription_schedule_store as store

BASE = "/api/v1/tenants/{tenant_id}/subscription-whatsapp/scheduled-messages"
PRIVATE = "/internal/qr/channels/{channel_id}/device/scheduled"
router = APIRouter(tags=["Mensagens programadas de assinaturas"])


def output(response, value):
    response.headers["Cache-Control"] = "no-store"
    return jsonable_encoder(value)


def validate_search(value):
    # PostgreSQL text has no NUL representation, including in bound parameters.
    if "\x00" in value:
        raise store.fail("SCHEDULE_INVALID", 422)


async def payload_after_owner(tenant, actor, request, model):
    async with store.owner_transaction(tenant, actor):
        pass
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > schemas.MAX_BODY:
            raise store.fail("SCHEDULE_LIMIT", 413)
        raw.extend(chunk)
    try:
        return model.model_validate_json(bytes(raw))
    except (ValidationError, ValueError):
        raise store.fail("SCHEDULE_INVALID", 422) from None


async def owned_schedule(db, tenant, actor, identity, *, lock=False):
    row = await store.schedule_row(db, tenant, identity, lock=lock)
    if row["owner_account_id"] != actor.account_id:
        raise store.fail("SCHEDULE_NOT_FOUND", 404)
    return row


async def scoped_run(db, schedule, identity=None, *, lock=False):
    if identity is None:
        identity = (await db.execute(text("""SELECT id FROM subscription_schedule_runs
            WHERE tenant_id=:t AND schedule_id=:s ORDER BY occurrence_index DESC LIMIT 1"""),
            {"t": schedule["tenant_id"], "s": schedule["id"]})).scalar_one_or_none()
        if identity is None:
            raise store.fail("SCHEDULE_NOT_FOUND", 404)
    row = await store.run_row(db, schedule["tenant_id"], identity, lock=lock)
    if row["schedule_id"] != schedule["id"]:
        raise store.fail("SCHEDULE_NOT_FOUND", 404)
    return row


async def editable_run(db, schedule, run):
    if schedule["status"] != "active" or run["status"] != "pending" or run["first_admitted_at"] is not None:
        raise store.fail("SCHEDULE_BUSY")
    admitted = (await db.execute(text("""SELECT EXISTS(SELECT 1 FROM subscription_schedule_recipients
        WHERE tenant_id=:t AND run_id=:r AND admitted_at IS NOT NULL)"""),
        {"t": schedule["tenant_id"], "r": run["id"]})).scalar_one()
    if admitted:
        raise store.fail("SCHEDULE_BUSY")


@router.get(BASE)
async def list_schedules(tenant_id: UUID, response: Response, actor: Actor = Depends(authenticate),
                         tab: str = "active", status: str = "", q: str = Query(default="", max_length=255),
                         page: int = Query(default=1, ge=1, le=10000)):
    validate_search(q)
    if tab not in {"active", "past_due", "history", "all"} or status and (
            tab != "active" or status not in {"attention", "queued", "processing", "upcoming"}):
        raise store.fail("SCHEDULE_INVALID", 422)
    async with store.owner_transaction(tenant_id, actor) as db:
        return output(response, await store.list_runs(db, tenant_id, actor, await store.clock(db),
            await store.timezone_for(db, tenant_id), tab=tab, status=status, q=q, page=page))


@router.get(BASE + "/options")
async def options(tenant_id: UUID, response: Response, actor: Actor = Depends(authenticate)):
    async with store.owner_transaction(tenant_id, actor) as db:
        now, zone = await store.clock(db), await store.timezone_for(db, tenant_id)
        today = now.astimezone(ZoneInfo(zone)).date()
        return output(response, {"devices": await store.campaigns.device_options(db, tenant_id, actor),
            "templates": await store.campaigns.template_options(db, tenant_id),
            "groups": await store.campaigns.group_summaries(db, tenant_id), "timezone": zone, "today": today,
            "defaults": {"start_date": today + timedelta(days=1), "start_time": "14:00", "interval_seconds": 60,
                "repeat_every_30_days": False, "message_type": "text"},
            "limits": {"title": 120, "text_utf16": 1000, "groups": schemas.MAX_GROUPS,
                "recipients": schemas.MAX_RECIPIENTS, "interval_seconds": schemas.INTERVALS,
                "review_seconds": schemas.REVIEW_SECONDS}, "generated_at": now})


@router.get(BASE + "/contacts")
async def contacts(tenant_id: UUID, response: Response, actor: Actor = Depends(authenticate),
                   q: str = Query(default="", max_length=255), page: int = Query(default=1, ge=1, le=10000)):
    validate_search(q)
    async with store.owner_transaction(tenant_id, actor) as db:
        return output(response, await store.contacts_page(db, tenant_id, q, page, await store.clock(db),
                                                         await store.timezone_for(db, tenant_id)))


async def audience_value(db, tenant, payload):
    values = payload.model_dump(mode="json")
    groups = await store.campaigns.group_summaries(db, tenant, payload.group_ids)
    if len(groups) != len(payload.group_ids):
        raise store.fail("SCHEDULE_NOT_FOUND", 404)
    counts = await store.audience_counts(db, tenant, values)
    now, zone = await store.clock(db), await store.timezone_for(db, tenant)
    start = end = None
    if payload.start_date is not None:
        try:
            if payload.start_date < now.astimezone(ZoneInfo(zone)).date():
                raise ValueError()
            start = policy.start_at({**values, "timezone": zone})
            end = policy.estimate_completion(start, counts["total"], payload.interval_seconds)
        except (ValueError, OverflowError):
            raise store.fail("SCHEDULE_INVALID", 422) from None
    return {**counts, "generated_at": now, "timezone": zone, "start_at": start, "estimated_end_at": end}


@router.get(BASE + "/audience")
async def audience_get(tenant_id: UUID, response: Response, actor: Actor = Depends(authenticate),
                       group_ids: str = Query(default="", max_length=3699),
                       contact_keys: str = Query(default="", max_length=220000), all_contacts: bool = False):
    async with store.owner_transaction(tenant_id, actor) as db:
        try:
            payload = schemas.AudienceRequest(group_ids=group_ids.split(",") if group_ids else [],
                contact_keys=contact_keys.split(",") if contact_keys else [], all_contacts=all_contacts)
        except (ValidationError, ValueError):
            raise store.fail("SCHEDULE_INVALID", 422) from None
        return output(response, await audience_value(db, tenant_id, payload))


@router.post(BASE + "/audience")
async def audience_post(tenant_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    payload = await payload_after_owner(tenant_id, actor, request, schemas.AudienceRequest)
    async with store.owner_transaction(tenant_id, actor) as db:
        return output(response, await audience_value(db, tenant_id, payload))


@router.get(BASE + "/requests/{key}")
async def recover_request(tenant_id: UUID, key: UUID, response: Response, actor: Actor = Depends(authenticate)):
    async with store.owner_transaction(tenant_id, actor) as db:
        row = await store.request_row(db, tenant_id, actor, key)
        if row is None:
            raise store.fail("SCHEDULE_NOT_FOUND", 404)
        return output(response, store.operation_dto(row, await store.clock(db), replayed=True))


async def prepare_review(tenant, actor, payload, schedule_id=None):
    kind = "review_edit" if schedule_id else "review_create"
    values = payload.model_dump(mode="json")
    async with store.owner_transaction(tenant, actor) as db:
        existing = await store.existing_request(db, tenant, actor, payload.idempotency_key, kind, values,
            schedule_id=schedule_id, run_id=payload.run_id if schedule_id else None)
        if existing:
            return store.operation_dto(existing, await store.clock(db), replayed=True)
        schedule = run = None
        if schedule_id:
            schedule = await owned_schedule(db, tenant, actor, schedule_id, lock=True)
            run = await scoped_run(db, schedule, payload.run_id, lock=True)
            store.check_versions(schedule, run, payload, kind)
            await editable_run(db, schedule, run)
        snapshot = await store.capture_review(db, tenant, actor, values["schedule"], schedule=schedule, run=run)
        identity, now = uuid4(), await store.clock(db)
        snapshot["review_request_id"] = str(identity)
        preview = store.review_preview(snapshot, identity, now)
        saved = await store.save_request(db, tenant, actor, payload.idempotency_key, kind, values,
            schedule_id=schedule_id, run_id=run["id"] if run else None, snapshot=snapshot, preview=preview,
            identity=identity, now=now)
        return store.operation_dto(saved, now)


@router.post(BASE + "/review", status_code=201)
async def review_schedule(tenant_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    payload = await payload_after_owner(tenant_id, actor, request, schemas.ReviewSchedule)
    return output(response, await prepare_review(tenant_id, actor, payload))


async def persist_confirmation(db, tenant, actor, intent, now, schedule=None, run=None):
    snapshot = intent["snapshot"]
    definition = snapshot["definition"]
    frozen = store.metadata_snapshot(snapshot, actor, intent["id"], now)
    due = datetime.fromisoformat(snapshot["due_at"])
    day = date.fromisoformat(definition["start_date"])
    try:
        anchor = day - timedelta(days=30 * run["occurrence_index"]) if run else day
    except OverflowError:
        raise store.fail("SCHEDULE_INVALID", 422) from None
    template = snapshot["template"]
    params = {"t": tenant, "a": actor.account_id, "s": schedule["id"] if schedule else uuid4(),
        "r": run["id"] if run else uuid4(), "title": definition["title"],
        "definition": store.encode(definition), "snapshot": store.encode(frozen), "hash": intent["review_version"],
        "device": UUID(snapshot["device"]["id"]), "template": UUID(template["id"]) if template else None,
        "revision": template["revision"] if template else None, "zone": definition["timezone"],
        "date": day, "anchor": anchor, "time": definition["start_time"], "interval": definition["interval_seconds"],
        "repeat": definition["repeat_every_30_days"], "due": due, "now": now}
    if schedule is None:
        await db.execute(text("""INSERT INTO subscription_schedules
            (id,tenant_id,owner_account_id,title,definition,snapshot,run_version,device_id,template_id,
             template_revision,timezone,anchor_date,anchor_time,interval_seconds,repeat_every_30_days,confirmed_at)
            VALUES(:s,:t,:a,:title,CAST(:definition AS jsonb),CAST(:snapshot AS jsonb),:hash,:device,:template,
             :revision,:zone,:anchor,:time,:interval,:repeat,:now)"""), params)
        await db.execute(text("""INSERT INTO subscription_schedule_runs
            (id,tenant_id,schedule_id,occurrence_index,schedule_version,run_version,due_at,local_date,
             timezone,snapshot,next_admission_at)
            VALUES(:r,:t,:s,0,1,:hash,:due,:date,:zone,CAST(:snapshot AS jsonb),:due)"""), params)
    else:
        # Prior recipients and their confirmed plans remain as immutable history.
        await db.execute(text("""UPDATE subscription_schedule_recipients SET status='cancelled',
            finished_at=:now,error_code='SCHEDULE_CANCELLED' WHERE tenant_id=:t AND run_id=:r
            AND status='pending' AND admitted_at IS NULL"""), params)
        await db.execute(text("""UPDATE subscription_schedules SET title=:title,definition=CAST(:definition AS jsonb),
            snapshot=CAST(:snapshot AS jsonb),run_version=:hash,device_id=:device,template_id=:template,
            template_revision=:revision,timezone=:zone,anchor_date=:anchor,anchor_time=:time,
            interval_seconds=:interval,repeat_every_30_days=:repeat,confirmed_at=:now,
            version=version+1,updated_at=:now WHERE tenant_id=:t AND id=:s"""), params)
        await db.execute(text("""UPDATE subscription_schedule_runs SET snapshot=CAST(:snapshot AS jsonb),
            run_version=:hash,version=version+1,schedule_version=:version,due_at=:due,local_date=:date,
            timezone=:zone,next_admission_at=:due,manual_released_at=NULL,error_code=NULL,
            status='pending',finished_at=NULL,updated_at=:now WHERE tenant_id=:t AND id=:r"""),
            params | {"version": schedule["version"] + 1})
    await store.insert_recipients(db, tenant, params["s"], params["r"], intent["review_version"], snapshot, now)
    result = {"schedule": await store.run_item(db, tenant, params["r"], actor, now)}
    return await store.complete_request(db, tenant, intent["id"], result, now,
                                        schedule_id=params["s"], run_id=params["r"])


@router.post(BASE + "/confirm", status_code=202)
async def confirm_schedule(tenant_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    payload = await payload_after_owner(tenant_id, actor, request, schemas.ConfirmSchedule)
    async with store.owner_transaction(tenant_id, actor) as db:
        intent = await store.request_row(db, tenant_id, actor, payload.idempotency_key)
        if intent is None:
            raise store.fail("SCHEDULE_NOT_FOUND", 404)
        if intent["kind"] not in {"review_create", "review_edit"} or intent["review_version"] != payload.review_version:
            raise store.fail("SCHEDULE_IDEMPOTENCY_CONFLICT")
        now = await store.clock(db)
        if intent["status"] == "succeeded":
            return output(response, store.operation_dto(intent, now, replayed=True))
        if intent["expires_at"] <= now:
            raise store.fail("SCHEDULE_REVIEW_EXPIRED", 410)
        snapshot = intent["snapshot"]
        schedule = run = None
        if intent["kind"] == "review_edit":
            schedule = await owned_schedule(db, tenant_id, actor, intent["schedule_id"], lock=True)
            run = await scoped_run(db, schedule, intent["run_id"], lock=True)
            if schedule["version"] != snapshot["schedule_version"] or run["version"] != snapshot["expected_run_version"]:
                raise store.fail("SCHEDULE_CONFLICT")
            await editable_run(db, schedule, run)
        current = await store.capture_review(db, tenant_id, actor, snapshot["definition"], schedule=schedule, run=run)
        current["review_request_id"] = str(intent["id"])
        if store.fingerprint(current) != intent["review_version"]:
            raise store.fail("SCHEDULE_CONFLICT")
        if not snapshot["counts"]["eligible"]:
            raise store.fail("SCHEDULE_NO_ELIGIBLE")
        saved = await persist_confirmation(db, tenant_id, actor, intent, now, schedule, run)
        return output(response, store.operation_dto(saved, now))


@router.post(BASE + "/pending/review", status_code=201)
async def review_pending(tenant_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    payload = await payload_after_owner(tenant_id, actor, request, schemas.ReviewPending)
    async with store.owner_transaction(tenant_id, actor) as db:
        existing = await store.existing_request(db, tenant_id, actor, payload.idempotency_key,
            "pending_review", payload.model_dump(mode="json"))
        now = await store.clock(db)
        if existing:
            return output(response, store.operation_dto(existing, now, replayed=True))
        targets = await store.pending_targets(db, tenant_id, actor, now)
        identity = uuid4()
        snapshot = {"runs": targets, "review_request_id": str(identity)}
        preview = {"review_version": store.fingerprint(snapshot), "expires_at": now + timedelta(seconds=120),
            "total": len(targets), "runs": [await store.run_item(db, tenant_id, UUID(item["id"]), actor, now) for item in targets]}
        saved = await store.save_request(db, tenant_id, actor, payload.idempotency_key, "pending_review",
            payload.model_dump(mode="json"), snapshot=snapshot, preview=preview, identity=identity, now=now)
        return output(response, store.operation_dto(saved, now))


@router.post(BASE + "/pending/confirm", status_code=202)
async def confirm_pending(tenant_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    payload = await payload_after_owner(tenant_id, actor, request, schemas.ConfirmPending)
    async with store.owner_transaction(tenant_id, actor) as db:
        intent = await store.request_row(db, tenant_id, actor, payload.idempotency_key)
        if intent is None:
            raise store.fail("SCHEDULE_NOT_FOUND", 404)
        if intent["kind"] != "pending_review" or intent["review_version"] != payload.review_version:
            raise store.fail("SCHEDULE_IDEMPOTENCY_CONFLICT")
        now = await store.clock(db)
        if intent["status"] == "succeeded":
            return output(response, store.operation_dto(intent, now, replayed=True))
        if intent["expires_at"] <= now:
            raise store.fail("SCHEDULE_REVIEW_EXPIRED", 410)
        targets = intent["snapshot"]["runs"]
        if not targets:
            raise store.fail("SCHEDULE_NO_ELIGIBLE", 422)
        # New runs becoming due after review do not enlarge the confirmed set.
        ids = [UUID(item["id"]) for item in targets]
        current = {item["id"]: item for item in await store.pending_targets(db, tenant_id, actor, now, ids=ids)}
        if any(current.get(item["id"]) != item for item in targets):
            raise store.fail("SCHEDULE_CONFLICT")
        await db.execute(text("""UPDATE subscription_schedule_runs SET manual_released_at=:now,
            next_admission_at=:now,version=version+1,updated_at=:now
            WHERE tenant_id=:t AND id=ANY(CAST(:ids AS uuid[]))"""), {"t": tenant_id, "ids": ids, "now": now})
        result = {"released": [{"schedule_id": item["schedule_id"], "run_id": item["id"]} for item in targets], "total": len(targets)}
        saved = await store.complete_request(db, tenant_id, intent["id"], result, now)
        return output(response, store.operation_dto(saved, now))


@router.post(BASE + "/{schedule_id}/review", status_code=201)
async def review_edit(tenant_id: UUID, schedule_id: UUID, request: Request, response: Response,
                      actor: Actor = Depends(authenticate)):
    payload = await payload_after_owner(tenant_id, actor, request, schemas.EditSchedule)
    return output(response, await prepare_review(tenant_id, actor, payload, schedule_id))


@router.post(BASE + "/{schedule_id}/actions")
async def schedule_action(tenant_id: UUID, schedule_id: UUID, request: Request, response: Response,
                          actor: Actor = Depends(authenticate)):
    payload = await payload_after_owner(tenant_id, actor, request, schemas.ScheduleAction)
    values = payload.model_dump(mode="json")
    async with store.owner_transaction(tenant_id, actor) as db:
        existing = await store.existing_request(db, tenant_id, actor, payload.idempotency_key, payload.action, values,
            schedule_id=schedule_id, run_id=payload.run_id)
        if existing:
            return output(response, store.operation_dto(existing, await store.clock(db), replayed=True))
        schedule = await owned_schedule(db, tenant_id, actor, schedule_id, lock=True)
        run = await scoped_run(db, schedule, payload.run_id, lock=True) if payload.action == "cancel_run" else None
        store.check_versions(schedule, run, payload, payload.action)
        if schedule["status"] != "active" or run and run["status"] not in {"pending", "running"}:
            raise store.fail("SCHEDULE_CONFLICT")
        now = await store.clock(db)
        if payload.action == "cancel_series":
            await db.execute(text("""UPDATE subscription_schedules SET status='cancelled',cancelled_at=:now,
                version=version+1,updated_at=:now WHERE tenant_id=:t AND id=:s"""),
                {"t": tenant_id, "s": schedule_id, "now": now})
        from app.subscription_schedule_runtime import cancel_schedule_pending
        await cancel_schedule_pending(db, schedule, "SCHEDULE_CANCELLED", run_id=run["id"] if run else None)
        result = {"schedule": await store.run_item(db, tenant_id, run["id"], actor, now)} if run else {
            "schedule_id": schedule_id, "cancelled": True}
        saved = await store.save_request(db, tenant_id, actor, payload.idempotency_key, payload.action, values,
            schedule_id=schedule_id, run_id=run["id"] if run else None, result=result, now=now)
        return output(response, store.operation_dto(saved, now))


@router.get(BASE + "/{schedule_id}/runs")
async def schedule_runs(tenant_id: UUID, schedule_id: UUID, response: Response,
                        actor: Actor = Depends(authenticate), page: int = Query(default=1, ge=1, le=10000)):
    async with store.owner_transaction(tenant_id, actor) as db:
        await owned_schedule(db, tenant_id, actor, schedule_id)
        return output(response, await store.list_runs(db, tenant_id, actor, await store.clock(db),
            await store.timezone_for(db, tenant_id), tab="all", page=page, schedule_id=schedule_id))


@router.get(BASE + "/{schedule_id}/recipients")
async def schedule_recipients(tenant_id: UUID, schedule_id: UUID, response: Response,
                              actor: Actor = Depends(authenticate), run_id: UUID | None = None,
                              status: str = "", q: str = Query(default="", max_length=255),
                              page: int = Query(default=1, ge=1, le=10000)):
    validate_search(q)
    if status and status not in schemas.RECIPIENT_STATES:
        raise store.fail("SCHEDULE_INVALID", 422)
    async with store.owner_transaction(tenant_id, actor) as db:
        schedule = await owned_schedule(db, tenant_id, actor, schedule_id)
        run = await scoped_run(db, schedule, run_id)
        return output(response, await store.recipient_page(db, run, await store.clock(db), status=status, q=q, page=page))


@router.get(BASE + "/{schedule_id}")
async def schedule_detail(tenant_id: UUID, schedule_id: UUID, response: Response,
                          actor: Actor = Depends(authenticate), run_id: UUID | None = None):
    async with store.owner_transaction(tenant_id, actor) as db:
        schedule = await owned_schedule(db, tenant_id, actor, schedule_id)
        run = await scoped_run(db, schedule, run_id)
        now = await store.clock(db)
        return output(response, {"schedule": await store.run_item(db, tenant_id, run["id"], actor, now),
            "definition": run["snapshot"]["definition"], "message_preview": store.message_preview(run["snapshot"]),
            "groups": run["snapshot"]["groups"], "recipients": await store.recipient_page(db, run, now)})


def asset_header_scope(request, device_id, generation, settings_revision, operation_id):
    try:
        actual = (UUID(request.headers["x-orion-device-id"]), UUID(request.headers["x-orion-device-generation"]),
            int(request.headers["x-orion-device-revision"]), UUID(request.headers["x-orion-device-effect"]))
        if actual != (device_id, generation, settings_revision, operation_id):
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise store.fail("SCHEDULE_REVOKED", 401) from None


async def authorized_asset(request, asset_id, scope, variant):
    from app import subscription_devices as devices
    from app.subscription_schedule_runtime import schedule_effect_authority
    async with devices._private_transaction(request, device_id=scope[0]) as (db, device, _, owner, fence):
        effect = await devices._effect(db, device, scope[3])
        if (effect["type"] != "scheduled_action" or effect["status"] != "running" or effect["attempts"] != 1
                or effect["claimed_owner"] != owner or effect["claimed_fence"] != fence
                or effect["generation"] != scope[1] or effect["settings_revision"] != scope[2]):
            raise store.fail("SCHEDULE_REVOKED")
        reason, _ = await schedule_effect_authority(db, device, effect, await store.clock(db))
        if reason:
            raise store.fail(reason)
        action = effect["payload"]["action"]
        if action["type"] not in {"image", "video", "audio", "document", "sticker", "ptv"}:
            raise store.fail("SCHEDULE_NOT_FOUND", 404)
        descriptor = action.get("asset")
        if not isinstance(descriptor, dict) or descriptor.get("id") != str(asset_id):
            raise store.fail("SCHEDULE_NOT_FOUND", 404)
        row = (await db.execute(text("""SELECT * FROM subscription_chatbot_assets
            WHERE tenant_id=:t AND id=:id AND status='ready'"""), {"t": device["tenant_id"], "id": asset_id})).mappings().one_or_none()
        if row is None or jsonable_encoder(store.shared.asset_descriptor(row)) != descriptor:
            raise store.fail("SCHEDULE_TEMPLATE_CHANGED")
        if variant == "thumbnail" and not row["thumbnail_size"]:
            raise store.fail("SCHEDULE_NOT_FOUND", 404)
        return device["tenant_id"], dict(row)


@router.get(PRIVATE + "/assets/{asset_id}", dependencies=[Depends(channels.enabled)])
async def scheduled_asset(channel_id: UUID, asset_id: UUID, request: Request, device_id: UUID, generation: UUID,
                          operation_id: UUID, settings_revision: int = Query(ge=1, le=2**63-1), variant: str = "original"):
    if variant not in {"original", "thumbnail"}:
        raise store.fail("SCHEDULE_NOT_FOUND", 404)
    asset_header_scope(request, device_id, generation, settings_revision, operation_id)
    scope = (device_id, generation, settings_revision, operation_id)
    tenant, asset = await authorized_asset(request, asset_id, scope, variant)
    prefix = "thumbnail_" if variant == "thumbnail" else ""
    from app.subscription_chatbot_media import get_media
    data = await asyncio.to_thread(get_media, tenant, asset_id, expected_size=asset[prefix + "size"],
        expected_sha256=asset[prefix + "sha256"], variant=variant)
    _, current = await authorized_asset(request, asset_id, scope, variant)
    if current[prefix + "sha256"] != asset[prefix + "sha256"] or current[prefix + "size"] != asset[prefix + "size"]:
        raise store.fail("SCHEDULE_TEMPLATE_CHANGED")
    return Response(data, media_type=asset[prefix + "mime"],
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
