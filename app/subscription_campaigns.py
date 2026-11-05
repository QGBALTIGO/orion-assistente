"""Owner-confirmed subscription campaigns. All public reads are side-effect free."""
import asyncio
import hashlib
from datetime import timedelta
from uuid import UUID, uuid4
from zoneinfo import available_timezones

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from sqlalchemy import text

from app.catalog_api import Actor, authenticate
from app import qr_channels as channels
from app import subscription_campaign_schemas as schemas
from app import subscription_campaign_policy as policy
from app import subscription_campaign_store as store

BASE = "/api/v1/tenants/{tenant_id}/subscription-whatsapp/campaigns"
PRIVATE = "/internal/qr/channels/{channel_id}/device/campaigns"
router = APIRouter(tags=["Campanhas de assinaturas"])


def output(response, value):
    response.headers["Cache-Control"] = "no-store"
    return jsonable_encoder(value)


async def payload_after_owner(tenant, actor, request, model):
    async with store.owner_transaction(tenant, actor):
        pass
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > schemas.MAX_BODY:
            raise store.fail("CAMPAIGN_LIMIT", 413)
        raw.extend(chunk)
    try:
        return model.model_validate_json(bytes(raw))
    except (ValidationError, ValueError):
        raise store.fail("CAMPAIGN_INVALID", 422) from None


async def timezone_for(db, tenant):
    return (await db.execute(text("SELECT timezone FROM tenants WHERE id=:t"), {"t": tenant})).scalar_one()


@router.get(BASE)
async def list_campaigns(tenant_id: UUID, response: Response, actor: Actor = Depends(authenticate),
                         q: str = Query(default="", max_length=255), status: str = "", page: int = Query(default=1, ge=1, le=10000)):
    if status and status not in schemas.STATES:
        raise store.fail("CAMPAIGN_INVALID", 422)
    async with store.owner_transaction(tenant_id, actor) as db:
        now = await store.clock(db)
        params = {"t": tenant_id, "status": status, "q": q.strip().casefold(), "offset": (page - 1) * schemas.PAGE_SIZE}
        clause = """ FROM subscription_campaigns WHERE tenant_id=:t AND archived_at IS NULL
            AND (:status='' OR status=:status) AND (:q='' OR position(:q in lower(title))>0)"""
        total = (await db.execute(text("SELECT count(*)" + clause), params)).scalar_one()
        rows = (await db.execute(text("SELECT *" + clause + " ORDER BY created_at DESC,id DESC LIMIT 30 OFFSET :offset"), params)).mappings().all()
        items = [await store.campaign_dto(db, dict(row), now) for row in rows]
        return output(response, store.page_response(items, total, page, now, await timezone_for(db, tenant_id)))


@router.get(BASE + "/options")
async def options(tenant_id: UUID, response: Response, actor: Actor = Depends(authenticate)):
    async with store.owner_transaction(tenant_id, actor) as db:
        return output(response, {"devices": await store.device_options(db, tenant_id, actor),
            "templates": await store.template_options(db, tenant_id), "groups": await store.group_summaries(db, tenant_id),
            "timezones": sorted(available_timezones() - {"Factory", "localtime"}), "timezone": await timezone_for(db, tenant_id),
            "limits": {"groups": schemas.MAX_GROUPS, "members_per_group": schemas.MAX_MEMBERS,
                "recipients_per_campaign": schemas.MAX_RECIPIENTS, "text_utf16": 3500,
                "interval_min_seconds": 30, "interval_max_seconds": 60, "contacts_per_minute": 10}})


@router.get(BASE + "/requests/{key}")
async def recover_request(tenant_id: UUID, key: UUID, response: Response, actor: Actor = Depends(authenticate)):
    async with store.owner_transaction(tenant_id, actor) as db:
        row = await store.request_row(db, tenant_id, actor, key)
        if row is None:
            raise store.fail("CAMPAIGN_NOT_FOUND", 404)
        return output(response, store.operation_dto(row, await store.clock(db), replayed=True))


@router.get(BASE + "/groups")
async def list_groups(tenant_id: UUID, response: Response, actor: Actor = Depends(authenticate)):
    async with store.owner_transaction(tenant_id, actor) as db:
        items = await store.group_summaries(db, tenant_id)
        return output(response, {"items": items, "total": len(items)})


@router.get(BASE + "/groups/{group_id}")
async def group_detail(tenant_id: UUID, group_id: UUID, response: Response, actor: Actor = Depends(authenticate)):
    async with store.owner_transaction(tenant_id, actor) as db:
        return output(response, await store.group_detail(db, tenant_id, group_id))


async def save_members(db, tenant, group, members):
    phones = [member.phone for member in members]
    await db.execute(text("""UPDATE subscription_campaign_members SET removed_at=clock_timestamp(),updated_at=clock_timestamp()
        WHERE tenant_id=:t AND group_id=:g AND removed_at IS NULL AND NOT phone=ANY(CAST(:phones AS varchar[]))"""),
        {"t": tenant, "g": group, "phones": phones})
    if members:
        await db.execute(text("""INSERT INTO subscription_campaign_members(tenant_id,group_id,phone,name)
            SELECT :t,:g,x.phone,x.name FROM jsonb_to_recordset(CAST(:members AS jsonb)) AS x(phone varchar(15),name varchar(100))
            ON CONFLICT(tenant_id,group_id,phone) DO UPDATE SET name=EXCLUDED.name,removed_at=NULL,updated_at=clock_timestamp()"""),
            {"t": tenant, "g": group, "members": store.encode([member.model_dump() for member in members])})


async def mutate_group(tenant, actor, kind, payload, identity=None):
    values = payload.model_dump(mode="json")
    if "evidence" in values:
        values["evidence_hash"] = hashlib.sha256(values.pop("evidence").encode()).hexdigest()
    async with store.owner_transaction(tenant, actor) as db:
        existing = await store.existing_request(db, tenant, actor, payload.idempotency_key, kind, values, group_id=identity)
        if existing:
            return store.operation_dto(existing, await store.clock(db), replayed=True)
        if kind == "group_create":
            count = (await db.execute(text("SELECT count(*) FROM subscription_campaign_groups WHERE tenant_id=:t AND archived_at IS NULL"),
                {"t": tenant})).scalar_one()
            if count >= schemas.MAX_GROUPS:
                raise store.fail("CAMPAIGN_LIMIT", 422)
            identity = uuid4()
            await db.execute(text("INSERT INTO subscription_campaign_groups(id,tenant_id,name) VALUES(:id,:t,:name)"),
                {"id": identity, "t": tenant, "name": payload.name})
            await save_members(db, tenant, identity, payload.members)
        else:
            row = await store.group_row(db, tenant, identity, lock=True)
            store.check_version(row, payload, kind, group_id=identity)
            if row["archived_at"]:
                raise store.fail("CAMPAIGN_CONFLICT")
            if kind == "group_update":
                await db.execute(text("""UPDATE subscription_campaign_groups SET name=:name,version=version+1,
                    updated_at=clock_timestamp() WHERE tenant_id=:t AND id=:id"""), {"name": payload.name, "t": tenant, "id": identity})
                await save_members(db, tenant, identity, payload.members)
            elif kind == "group_archive":
                await db.execute(text("""UPDATE subscription_campaign_groups SET archived_at=clock_timestamp(),
                    version=version+1,updated_at=clock_timestamp() WHERE tenant_id=:t AND id=:id"""), {"t": tenant, "id": identity})
            elif kind == "group_permissions":
                contacts = store.encode([item.model_dump() for item in payload.contacts])
                matching = (await db.execute(text("""SELECT count(*) FROM
                    jsonb_to_recordset(CAST(:contacts AS jsonb)) AS x(phone varchar(15),version bigint)
                    JOIN subscription_campaign_members m ON m.tenant_id=:t AND m.group_id=:g AND m.phone=x.phone
                      AND m.removed_at IS NULL LEFT JOIN subscription_campaign_permissions p
                      ON p.tenant_id=m.tenant_id AND p.phone=m.phone WHERE COALESCE(p.version,0)=x.version"""),
                    {"contacts": contacts, "t": tenant, "g": identity})).scalar_one()
                if matching != len(payload.contacts):
                    raise store.fail("CAMPAIGN_PERMISSION_CONFLICT")
                await db.execute(text("""INSERT INTO subscription_campaign_permissions
                    (tenant_id,phone,revoked,source,actor_account_id,evidence_hash)
                    SELECT :t,x.phone,false,'owner_permission',:actor,:hash FROM
                      jsonb_to_recordset(CAST(:contacts AS jsonb)) AS x(phone varchar(15),version bigint)
                    ON CONFLICT(tenant_id,phone) DO UPDATE SET version=subscription_campaign_permissions.version+1,
                      revoked=false,source='owner_permission',actor_account_id=EXCLUDED.actor_account_id,
                      evidence_hash=EXCLUDED.evidence_hash,event_id=NULL,updated_at=clock_timestamp()"""),
                    {"contacts": contacts, "t": tenant, "actor": actor.account_id, "hash": values["evidence_hash"]})
                await db.execute(text("""UPDATE subscription_campaign_groups SET version=version+1,updated_at=clock_timestamp()
                    WHERE tenant_id=:t AND id=:id"""), {"t": tenant, "id": identity})
        from app.subscription_campaign_runtime import invalidate_audiences
        await invalidate_audiences(db, tenant)
        from app.subscription_schedule_runtime import invalidate_schedule_contacts
        await invalidate_schedule_contacts(db, tenant)
        result = await store.group_detail(db, tenant, identity)
        saved = await store.save_request(db, tenant, actor, payload.idempotency_key, kind, values, group_id=identity, result=result)
        return store.operation_dto(saved, await store.clock(db))


@router.post(BASE + "/groups", status_code=201)
async def create_group(tenant_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    payload = await payload_after_owner(tenant_id, actor, request, schemas.CreateGroup)
    return output(response, await mutate_group(tenant_id, actor, "group_create", payload))


@router.patch(BASE + "/groups/{group_id}")
async def update_group(tenant_id: UUID, group_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    payload = await payload_after_owner(tenant_id, actor, request, schemas.UpdateGroup)
    return output(response, await mutate_group(tenant_id, actor, "group_update", payload, group_id))


@router.delete(BASE + "/groups/{group_id}")
async def archive_group(tenant_id: UUID, group_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    payload = await payload_after_owner(tenant_id, actor, request, schemas.ArchiveGroup)
    return output(response, await mutate_group(tenant_id, actor, "group_archive", payload, group_id))


@router.post(BASE + "/groups/{group_id}/permissions")
async def group_permissions(tenant_id: UUID, group_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    payload = await payload_after_owner(tenant_id, actor, request, schemas.GroupPermissions)
    return output(response, await mutate_group(tenant_id, actor, "group_permissions", payload, group_id))


@router.post(BASE, status_code=201)
async def create_campaign(tenant_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    payload = await payload_after_owner(tenant_id, actor, request, schemas.CreateCampaign)
    values = payload.model_dump(mode="json")
    async with store.owner_transaction(tenant_id, actor) as db:
        existing = await store.existing_request(db, tenant_id, actor, payload.idempotency_key, "create", values)
        if existing:
            return output(response, store.operation_dto(existing, await store.clock(db), replayed=True))
        definition = values["campaign"]
        store.bounded_json(definition, 32768)
        await store.validate_references(db, tenant_id, actor, definition)
        identity = uuid4()
        row = (await db.execute(text("""INSERT INTO subscription_campaigns
            (id,tenant_id,owner_account_id,title,definition,start_at,template_id)
            VALUES(:id,:t,:a,:title,CAST(:definition AS jsonb),:start,:template) RETURNING *"""),
            {"id": identity, "t": tenant_id, "a": actor.account_id, "title": definition["title"],
             "definition": store.encode(definition), "start": policy.start_at(definition), "template": payload.campaign.template_id})).mappings().one()
        now = await store.clock(db)
        saved = await store.save_request(db, tenant_id, actor, payload.idempotency_key, "create", values,
            campaign_id=identity, result={"campaign": await store.campaign_dto(db, dict(row), now)})
        return output(response, store.operation_dto(saved, now))


@router.patch(BASE + "/{campaign_id}")
async def update_campaign(tenant_id: UUID, campaign_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    payload = await payload_after_owner(tenant_id, actor, request, schemas.UpdateCampaign)
    values = payload.model_dump(mode="json")
    async with store.owner_transaction(tenant_id, actor) as db:
        existing = await store.existing_request(db, tenant_id, actor, payload.idempotency_key, "update", values, campaign_id=campaign_id)
        if existing:
            return output(response, store.operation_dto(existing, await store.clock(db), replayed=True))
        row = await store.campaign_row(db, tenant_id, campaign_id, lock=True)
        store.check_version(row, payload, "update", campaign_id=campaign_id)
        if row["status"] != "draft" or row["archived_at"]:
            raise store.fail("CAMPAIGN_BUSY")
        definition = store.bounded_json(values["campaign"], 32768)
        await store.validate_references(db, tenant_id, actor, definition)
        updated = (await db.execute(text("""UPDATE subscription_campaigns SET owner_account_id=:a,title=:title,
            definition=CAST(:definition AS jsonb),start_at=:start,template_id=:template,template_revision=NULL,
            version=version+1,updated_at=clock_timestamp() WHERE tenant_id=:t AND id=:id RETURNING *"""),
            {"a": actor.account_id, "title": definition["title"], "definition": store.encode(definition),
             "start": policy.start_at(definition), "template": payload.campaign.template_id, "t": tenant_id, "id": campaign_id})).mappings().one()
        now = await store.clock(db)
        saved = await store.save_request(db, tenant_id, actor, payload.idempotency_key, "update", values,
            campaign_id=campaign_id, result={"campaign": await store.campaign_dto(db, dict(updated), now)})
        return output(response, store.operation_dto(saved, now))


@router.post(BASE + "/{campaign_id}/review", status_code=201)
async def review_campaign(tenant_id: UUID, campaign_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    payload = await payload_after_owner(tenant_id, actor, request, schemas.ReviewCampaign)
    values = payload.model_dump(mode="json")
    async with store.owner_transaction(tenant_id, actor) as db:
        existing = await store.existing_request(db, tenant_id, actor, payload.idempotency_key, "review", values, campaign_id=campaign_id)
        if existing:
            return output(response, store.operation_dto(existing, await store.clock(db), replayed=True))
        row = await store.campaign_row(db, tenant_id, campaign_id, lock=True)
        store.check_version(row, payload, "review", campaign_id=campaign_id)
        if row["status"] != "draft" or row["archived_at"] or row["owner_account_id"] != actor.account_id:
            raise store.fail("CAMPAIGN_CONFLICT")
        snapshot = await store.capture_review(db, row, actor)
        identity, now = uuid4(), await store.clock(db)
        snapshot["review_request_id"] = str(identity)
        version = store.fingerprint(snapshot)
        blocked = sum(contact["blocked"] for contact in snapshot["contacts"])
        current_devices = {str(item["id"]): item for item in await store.device_options(db, tenant_id, actor)}
        definition = row["definition"]
        preview = {"review_version": version, "expires_at": now + timedelta(seconds=120),
            "campaign": await store.campaign_dto(db, row, now), "total": len(snapshot["contacts"]),
            "eligible": len(snapshot["contacts"]) - blocked, "blocked": blocked, "duplicates": snapshot["duplicates"],
            "groups": snapshot["groups"], "devices": [current_devices[item["id"]] for item in snapshot["devices"]],
            "message_preview": store.message_preview(snapshot),
            "schedule": {**{key: definition[key] for key in ("start_date", "start_time", "timezone", "window_start", "window_end", "weekdays", "interval_seconds")},
                         "start_at": row["start_at"]}}
        saved = await store.save_request(db, tenant_id, actor, payload.idempotency_key, "review", values,
            campaign_id=campaign_id, snapshot=snapshot, preview=preview, identity=identity, now=now)
        return output(response, store.operation_dto(saved, now))


@router.post(BASE + "/{campaign_id}/confirm", status_code=202)
async def confirm_campaign(tenant_id: UUID, campaign_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    payload = await payload_after_owner(tenant_id, actor, request, schemas.ConfirmCampaign)
    async with store.owner_transaction(tenant_id, actor) as db:
        intent = await store.request_row(db, tenant_id, actor, payload.idempotency_key)
        if intent is None:
            raise store.fail("CAMPAIGN_NOT_FOUND", 404)
        if (intent["kind"] != "review" or intent["campaign_id"] != campaign_id
                or intent["review_version"] != payload.review_version):
            raise store.fail("CAMPAIGN_IDEMPOTENCY_CONFLICT")
        now = await store.clock(db)
        if intent["status"] == "succeeded":
            return output(response, store.operation_dto(intent, now, replayed=True))
        if intent["expires_at"] <= now:
            raise store.fail("CAMPAIGN_REVIEW_EXPIRED", 410)
        campaign = await store.campaign_row(db, tenant_id, campaign_id, lock=True)
        snapshot = intent["snapshot"]
        if (campaign["status"] != "draft" or campaign["archived_at"]
                or campaign["version"] != snapshot["campaign_version"]
                or campaign["owner_account_id"] != actor.account_id):
            raise store.fail("CAMPAIGN_CONFLICT")
        current = await store.capture_review(db, campaign, actor)
        current["review_request_id"] = str(intent["id"])
        if store.fingerprint(current) != intent["review_version"]:
            raise store.fail("CAMPAIGN_CONFLICT")
        if not any(not contact["blocked"] for contact in snapshot["contacts"]):
            raise store.fail("CAMPAIGN_NO_ELIGIBLE")
        # Keep only the shared frozen source on the campaign. Personalized
        # plans live once per recipient, not inside another 10k-row snapshot.
        frozen = {key: value for key, value in snapshot.items() if key != "contacts"}
        frozen["consent"] = {"confirmed": True, "actor_account_id": str(actor.account_id),
                             "review_request_id": str(intent["id"]), "confirmed_at": now}
        store.bounded_json(frozen, 1048576)
        recipients, expanded_bytes = [], 2
        for index, contact in enumerate(snapshot["contacts"]):
            assigned = snapshot["devices"][index % len(snapshot["devices"])]
            plan = policy.frozen_plan(snapshot["definition"], snapshot["template"],
                store.contact_facts(contact, snapshot["facts"]))
            if store.fingerprint(plan) != contact["plan_hash"]:
                raise store.fail("CAMPAIGN_CONFLICT")
            store.bounded_json(plan, 131072)
            expanded_bytes = store.expanded_budget(expanded_bytes, contact, plan)
            recipients.append({"id": str(uuid4()), "ordinal": index + 1, "phone": contact["phone"],
                "name": contact["name"], "source_group_ids": contact["groups"], "device_id": assigned["id"],
                "generation": assigned["generation"], "settings_revision": assigned["settings_revision"],
                "permission_version": contact["permission_version"], "plan": plan,
                "status": "cancelled" if contact["blocked"] else "pending",
                "finished_at": now if contact["blocked"] else None,
                "error_code": "CAMPAIGN_OPTED_OUT" if contact["blocked"] else None})
        # All validation is complete before the first durable audience row.
        await db.execute(text("""INSERT INTO subscription_campaign_recipients
            (id,tenant_id,campaign_id,run_version,ordinal,phone,name,source_group_ids,device_id,generation,
             settings_revision,permission_version,plan,status,finished_at,error_code)
            SELECT x.id,:t,:campaign,:run,x.ordinal,x.phone,x.name,x.source_group_ids,x.device_id,x.generation,
                   x.settings_revision,x.permission_version,x.plan,x.status,x.finished_at,x.error_code
            FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS x(id uuid,ordinal integer,phone varchar(15),name varchar(100),
              source_group_ids uuid[],device_id uuid,generation uuid,settings_revision bigint,permission_version bigint,
              plan jsonb,status varchar(16),finished_at timestamptz,error_code varchar(80))"""),
            {"t": tenant_id, "campaign": campaign_id, "run": intent["review_version"], "rows": store.encode(recipients)})
        await db.execute(text("""INSERT INTO subscription_campaign_sources
            (tenant_id,campaign_id,run_version,group_id,group_version,name,member_count)
            SELECT :t,:campaign,:run,x.id,x.version,x.name,x.member_count
            FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS x(id uuid,version bigint,name varchar(100),member_count integer)"""),
            {"t": tenant_id, "campaign": campaign_id, "run": intent["review_version"], "rows": store.encode(snapshot["groups"])})
        updated = (await db.execute(text("""UPDATE subscription_campaigns SET status='scheduled',version=version+1,
            run_version=:run,snapshot=CAST(:snapshot AS jsonb),confirmed_at=:now,template_revision=:revision,
            next_admission_at=:next,updated_at=:now WHERE tenant_id=:t AND id=:id RETURNING *"""),
            {"run": intent["review_version"], "snapshot": store.encode(frozen), "now": now,
             "revision": snapshot["template"]["revision"] if snapshot["template"] else None,
             "next": policy.next_window(campaign["definition"], now), "t": tenant_id, "id": campaign_id})).mappings().one()
        result = {"campaign": await store.campaign_dto(db, dict(updated), now)}
        store.bounded_json(result, 4194304)
        saved = (await db.execute(text("""UPDATE subscription_campaign_requests SET status='succeeded',
            confirmed_at=:now,result=CAST(:result AS jsonb) WHERE tenant_id=:t AND id=:id RETURNING *"""),
            {"now": now, "result": store.encode(result), "t": tenant_id, "id": intent["id"]})).mappings().one()
        return output(response, store.operation_dto(saved, now))


@router.post(BASE + "/{campaign_id}/actions")
async def campaign_action(tenant_id: UUID, campaign_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    from app import subscription_campaign_runtime as runtime
    payload = await payload_after_owner(tenant_id, actor, request, schemas.CampaignAction)
    values = payload.model_dump(mode="json")
    async with store.owner_transaction(tenant_id, actor) as db:
        existing = await store.existing_request(db, tenant_id, actor, payload.idempotency_key, "action", values, campaign_id=campaign_id)
        if existing:
            return output(response, store.operation_dto(existing, await store.clock(db), replayed=True))
        row = await store.campaign_row(db, tenant_id, campaign_id, lock=True)
        store.check_version(row, payload, "action", campaign_id=campaign_id)
        if row["archived_at"] or row["status"] == "cancelled":
            raise store.fail("CAMPAIGN_CONFLICT")
        now = await store.clock(db)
        action = payload.action
        if action == "pause":
            if row["status"] not in {"scheduled", "running"}:
                raise store.fail("CAMPAIGN_CONFLICT")
            await db.execute(text("""UPDATE subscription_campaigns SET status='paused',version=version+1,
                updated_at=:now WHERE tenant_id=:t AND id=:id"""), {"now": now, "t": tenant_id, "id": campaign_id})
            # An admitted sequence is never silently admitted a second time.
            await runtime.cancel_campaign_pending(db, row, "CAMPAIGN_PAUSED", include_unadmitted=False)
        elif action == "archive":
            await db.execute(text("""UPDATE subscription_campaigns SET status='cancelled',archived_at=:now,
                version=version+1,updated_at=:now WHERE tenant_id=:t AND id=:id"""), {"now": now, "t": tenant_id, "id": campaign_id})
            await runtime.cancel_campaign_pending(db, row, "CAMPAIGN_CANCELLED", include_unadmitted=True)
        elif action == "edit":
            if row["status"] not in {"draft", "scheduled", "paused"} or row["first_admitted_at"] is not None:
                raise store.fail("CAMPAIGN_BUSY")
            await runtime.cancel_campaign_pending(db, row, "CAMPAIGN_CANCELLED", include_unadmitted=True)
            await db.execute(text("""UPDATE subscription_campaigns SET status='draft',version=version+1,owner_account_id=:a,
                run_version=NULL,snapshot=NULL,confirmed_at=NULL,template_revision=NULL,next_admission_at=NULL,
                updated_at=:now WHERE tenant_id=:t AND id=:id"""), {"a": actor.account_id, "now": now, "t": tenant_id, "id": campaign_id})
        else:
            if row["status"] != "paused" or row["owner_account_id"] != actor.account_id:
                raise store.fail("CAMPAIGN_CONFLICT")
            await runtime.check_frozen_context(db, row)
            status = "running" if row["first_admitted_at"] else "scheduled"
            await db.execute(text("""UPDATE subscription_campaigns SET status=:status,version=version+1,
                next_admission_at=:next,updated_at=:now WHERE tenant_id=:t AND id=:id"""),
                {"status": status, "next": policy.next_window(row["definition"], now), "now": now, "t": tenant_id, "id": campaign_id})
            await runtime.invalidate_audiences(db, tenant_id)
            await runtime.complete_campaign(db, tenant_id, campaign_id)
        row = await store.campaign_row(db, tenant_id, campaign_id)
        result = {"campaign": await store.campaign_dto(db, row, now)}
        saved = await store.save_request(db, tenant_id, actor, payload.idempotency_key, "action", values,
            campaign_id=campaign_id, result=result)
        return output(response, store.operation_dto(saved, now))


@router.get(BASE + "/{campaign_id}/recipients")
async def campaign_recipients(tenant_id: UUID, campaign_id: UUID, response: Response, actor: Actor = Depends(authenticate),
                              status: str = "", q: str = Query(default="", max_length=255), page: int = Query(default=1, ge=1, le=10000)):
    if status and status not in policy.RECIPIENT_LABELS:
        raise store.fail("CAMPAIGN_INVALID", 422)
    async with store.owner_transaction(tenant_id, actor) as db:
        campaign = await store.campaign_row(db, tenant_id, campaign_id)
        return output(response, await store.recipient_page(db, campaign, await store.clock(db), status=status, q=q.strip(), page=page))


@router.get(BASE + "/{campaign_id}")
async def campaign_detail(tenant_id: UUID, campaign_id: UUID, response: Response, actor: Actor = Depends(authenticate)):
    async with store.owner_transaction(tenant_id, actor) as db:
        campaign = await store.campaign_row(db, tenant_id, campaign_id)
        now = await store.clock(db)
        dto = await store.campaign_dto(db, campaign, now)
        if campaign["run_version"]:
            # Preserve the complete group DTO reviewed at confirmation,
            # including blocked_count/archived; current groups may have changed.
            groups = campaign["snapshot"]["groups"]
        else:
            groups = await store.group_summaries(db, tenant_id, [UUID(v) for v in campaign["definition"]["group_ids"]], include_archived=True)
        return output(response, {"campaign": dto, "progress": dto["progress"], "groups": groups,
            "recipients": await store.recipient_page(db, campaign, now)})


def asset_header_scope(request, device_id, generation, settings_revision, operation_id):
    try:
        actual = (UUID(request.headers["x-orion-device-id"]), UUID(request.headers["x-orion-device-generation"]),
            int(request.headers["x-orion-device-revision"]), UUID(request.headers["x-orion-device-effect"]))
        if actual != (device_id, generation, settings_revision, operation_id):
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise store.fail("CAMPAIGN_REVOKED", 401) from None


async def authorized_asset(request, asset_id, scope, variant):
    from app import subscription_devices as devices
    from app import subscription_campaign_runtime as runtime
    async with devices._private_transaction(request, device_id=scope[0]) as (db, device, _, owner, fence):
        effect = await devices._effect(db, device, scope[3])
        if (effect["status"] != "running" or effect["attempts"] != 1 or effect["claimed_owner"] != owner
                or effect["claimed_fence"] != fence or effect["generation"] != scope[1]
                or effect["settings_revision"] != scope[2]):
            raise store.fail("CAMPAIGN_REVOKED")
        reason, _ = await runtime.effect_authority(db, device, effect, await store.clock(db))
        if reason:
            raise store.fail(reason)
        action = effect["payload"]["action"]
        if action["type"] not in {"image", "video", "audio", "document", "sticker", "ptv"}:
            raise store.fail("CAMPAIGN_NOT_FOUND", 404)
        descriptor = action.get("asset")
        if not isinstance(descriptor, dict) or descriptor.get("id") != str(asset_id):
            raise store.fail("CAMPAIGN_NOT_FOUND", 404)
        row = (await db.execute(text("""SELECT * FROM subscription_chatbot_assets
            WHERE tenant_id=:t AND id=:id AND status='ready'"""), {"t": device["tenant_id"], "id": asset_id})).mappings().one_or_none()
        if row is None or jsonable_encoder(store.shared.asset_descriptor(row)) != descriptor:
            raise store.fail("CAMPAIGN_TEMPLATE_CHANGED")
        if variant == "thumbnail" and not row["thumbnail_size"]:
            raise store.fail("CAMPAIGN_NOT_FOUND", 404)
        return device["tenant_id"], dict(row)


@router.get(PRIVATE + "/assets/{asset_id}", dependencies=[Depends(channels.enabled)])
async def campaign_asset(channel_id: UUID, asset_id: UUID, request: Request, device_id: UUID, generation: UUID,
                         operation_id: UUID, settings_revision: int = Query(ge=1, le=2**63-1), variant: str = "original"):
    if variant not in {"original", "thumbnail"}:
        raise store.fail("CAMPAIGN_NOT_FOUND", 404)
    asset_header_scope(request, device_id, generation, settings_revision, operation_id)
    scope = (device_id, generation, settings_revision, operation_id)
    tenant, asset = await authorized_asset(request, asset_id, scope, variant)
    prefix = "thumbnail_" if variant == "thumbnail" else ""
    from app.subscription_chatbot_media import get_media
    data = await asyncio.to_thread(get_media, tenant, asset_id, expected_size=asset[prefix + "size"],
        expected_sha256=asset[prefix + "sha256"], variant=variant)
    # Do not expose even previously-authorized bytes after pause/revocation or
    # lease/context change during the storage read. Neither check claims work.
    _, current = await authorized_asset(request, asset_id, scope, variant)
    if current[prefix + "sha256"] != asset[prefix + "sha256"] or current[prefix + "size"] != asset[prefix + "size"]:
        raise store.fail("CAMPAIGN_TEMPLATE_CHANGED")
    return Response(data, media_type=asset[prefix + "mime"],
                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


# The device adapter imports these hooks lazily, keeping the public API and
# private transport on the same implementation without a second scheduler.
from app.subscription_campaign_runtime import (  # noqa: E402,F401
    admit_due_campaigns, authorize_campaign_effect, after_campaign_effect_finish, record_campaign_optout,
)
