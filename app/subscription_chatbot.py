"""Owner-only subscription WhatsApp chatbot API; reads never execute rules."""
import asyncio
import base64
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from sqlalchemy import text

from app.catalog_api import Actor, authenticate
from app import subscription_chatbot_schemas as schemas
from app import subscription_chatbot_rules as rules_engine
from app import subscription_chatbot_store as store


BASE = "/api/v1/tenants/{tenant_id}/subscription-whatsapp/chatbot"
router = APIRouter(tags=["Chatbot de assinaturas"])


def _unique_json(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


async def bounded_json(request, model, *, limit=2 * 1024 * 1024):
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise HTTPException(415, "Envie os dados no formato JSON")
    length = request.headers.get("content-length")
    if length is not None and (not length.isascii() or not length.isdigit() or len(length) > 9 or int(length) > limit):
        raise HTTPException(413, "A solicitação ultrapassa o limite de tamanho")
    data = bytearray()
    try:
        async with asyncio.timeout(15):
            async for chunk in request.stream():
                if len(data) + len(chunk) > limit:
                    raise HTTPException(413, "A solicitação ultrapassa o limite de tamanho")
                data.extend(chunk)
        value = json.loads(bytes(data).decode("utf-8"), object_pairs_hook=_unique_json,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite")))
        return model.model_validate(value)
    except HTTPException:
        raise
    except TimeoutError:
        raise HTTPException(408, "O envio não foi concluído") from None
    except ValidationError:
        # Discriminator messages and arbitrary extra-key locations can contain
        # submitted secrets even when Pydantic's input/url fields are omitted.
        raise HTTPException(422, "Confira os campos e os limites da resposta.") from None
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(422, "JSON inválido") from None


async def _rule(db, tenant, identity, *, expected=None):
    row = (await db.execute(text("""SELECT * FROM subscription_chatbot_rules WHERE tenant_id=:t AND id=:id
        AND removed_at IS NULL FOR UPDATE"""), {"t": tenant, "id": identity})).mappings().one_or_none()
    if not row:
        raise store.fail("CHATBOT_NOT_FOUND", 404)
    if expected is not None and row["revision"] != expected:
        raise store.fail("CHATBOT_CONFLICT")
    return dict(row)


def capabilities(can_manage):
    # Editing text/reference rules does not require a connected socket. Media
    # capability describes the installed storage/decoder, never a paid grant.
    import os
    from app.subscription_chatbot_media import video_available
    media = all(os.getenv(key) for key in ("ORION_MEDIA_S3_ENDPOINT", "ORION_MEDIA_S3_BUCKET",
        "ORION_MEDIA_S3_REGION", "ORION_MEDIA_S3_ACCESS_KEY_ID", "ORION_MEDIA_S3_SECRET_ACCESS_KEY"))
    return {"manage": can_manage, "test_response": can_manage, "read_url_tags": can_manage,
            "import": can_manage, "export": True, "backups": True, "restore": can_manage,
            "actions": {"text": True, "template": True, "sticker": media,
                        "shortvideo": media and video_available(), "carousel": media, "url": True,
                        "nested_reply": True, "nested_list": True}}


async def catalog(db, tenant, actor, *, graph=None):
    state = await store.workspace(db, tenant, write=False)
    graph = graph or await store.load_graph(db, tenant)
    rules, templates, _ = graph
    devices = (await db.execute(text("""SELECT id,name,status FROM subscription_devices
        WHERE tenant_id=:t AND owner_account_id=:a AND removed_at IS NULL ORDER BY created_at,id"""),
        {"t": tenant, "a": actor.account_id})).mappings().all()
    return {"chatbot_revision": await store.revision(db, tenant),
        "devices": [dict(row) for row in devices],
        "templates": [{"id": row["id"], "revision": row["revision"], "name": row["name"], "kind": row["kind"],
                       "available": row["kind"] == "text" or row["asset"] is not None} for row in templates.values()],
        "rules": [{key: row[key] for key in ("id", "keyword", "device_id", "enabled", "revision")}
                  | {"response_kind": row["response"]["kind"]} for row in sorted(rules.values(), key=lambda item: item["creation_order"], reverse=True)],
        "capabilities": capabilities(bool(state.get("permissions", {}).get("chatbot.manage"))),
        "limits": {"rules": schemas.MAX_RULES, "keyword_chars": 255, "sequence_actions": 30,
                   "sequence_delay_seconds": 30, "action_delay_seconds": 15, "list_sections": 10,
                   "list_rows": 10, "list_row_delay_seconds": 5, "import_bytes": schemas.IMPORT_BYTES},
        "tags": [{"key": key, "label": label} for key, label in (
            ("name", "Nome do contato"), ("phone_number", "Telefone do contato"), ("my_name", "Nome do negócio"),
            ("my_email", "E-mail da conta"), ("my_contact_number", "Contato comercial"))]}


@router.get(BASE)
async def list_rules(tenant_id: UUID, response: Response, actor: Actor = Depends(authenticate),
    q: str = Query(default="", max_length=255), search_in: str = "both", device_id: str = "",
    keyword: str = Query(default="", max_length=255), status: str = "", match_mode: str = "",
    response_type: str = "", created_from: date | None = None, created_to: date | None = None,
    page: int = Query(default=1, ge=1, le=10000), per_page: int = Query(default=20, ge=1, le=100)):
    response.headers["Cache-Control"] = "no-store"
    if (search_in not in {"keyword", "response", "both"} or status not in {"", "active", "inactive"}
            or match_mode not in {"", "equal", "like", "array"}
            or response_type not in {"", "list", "buttons", *schemas.ACTION_TYPES}
            or created_from and created_to and created_from > created_to):
        raise HTTPException(422, "Filtro inválido")
    try:
        selected_device = UUID(device_id) if device_id not in {"", "all"} else None
    except ValueError:
        raise HTTPException(422, "Dispositivo inválido") from None
    async with store.owner_transaction(tenant_id, actor, write=False) as db:
        graph = await store.load_graph(db, tenant_id)
        values = list(graph[0].values())
        counts = {"total": len(values), "active": sum(row["enabled"] for row in values),
                  "inactive": sum(not row["enabled"] for row in values)}
        context = await catalog(db, tenant_id, actor, graph=graph)
        zone_name = (await db.execute(text("SELECT timezone FROM tenants WHERE id=:t"), {"t": tenant_id})).scalar_one()
        zone = ZoneInfo(zone_name)
        target = rules_engine.normalize(q)
        filtered = []
        for row in values:
            types = rules_engine.summary(row)["response_types"]
            if device_id == "all" and row["device_id"] is not None or selected_device is not None and row["device_id"] != selected_device:
                continue
            if keyword and row["keyword"] != keyword or status and row["enabled"] != (status == "active") or match_mode and row["match_mode"] != match_mode:
                continue
            if response_type and response_type not in types:
                continue
            day = row["created_at"].astimezone(zone).date()
            if created_from and day < created_from or created_to and day > created_to:
                continue
            haystack = (row["keyword"] if search_in == "keyword" else store.encode(row["response"])
                        if search_in == "response" else row["keyword"] + " " + store.encode(row["response"]))
            if target and target not in rules_engine.normalize(haystack):
                continue
            filtered.append(row)
        filtered.sort(key=lambda row: row["creation_order"], reverse=True)
        names = {str(row["id"]): row["name"] for row in context["devices"]}
        pending = await store.pending_restore(db, tenant_id)
        return jsonable_encoder({"items": [store.rule_summary(row, names) for row in filtered[(page-1)*per_page:page*per_page]],
            "counts": counts, "filtered_total": len(filtered), "page": page, "per_page": per_page,
            "total_pages": max(1, (len(filtered)+per_page-1)//per_page),
            "pending_job": store.job_dto(pending) if pending else None,
            **{key: value for key, value in context.items() if key != "rules"}})


@router.get(BASE + "/catalog")
async def get_catalog(tenant_id: UUID, response: Response, actor: Actor = Depends(authenticate)):
    response.headers["Cache-Control"] = "no-store"
    async with store.owner_transaction(tenant_id, actor, write=False) as db:
        return jsonable_encoder(await catalog(db, tenant_id, actor))


@router.get(BASE + "/rules/{rule_id}")
async def get_rule(tenant_id: UUID, rule_id: UUID, response: Response, actor: Actor = Depends(authenticate)):
    response.headers["Cache-Control"] = "no-store"
    async with store.owner_transaction(tenant_id, actor, write=False) as db:
        return jsonable_encoder({"rule": store.rule_dto(await _rule(db, tenant_id, rule_id)),
                                 "chatbot_revision": await store.revision(db, tenant_id)})


async def validate_definition(db, tenant, actor, identity, definition, *, graph=None):
    await store.check_device(db, tenant, definition["device_id"], actor_id=actor.account_id)
    graph = graph or await store.load_graph(db, tenant)
    rules, templates, _ = graph
    rules = dict(rules)
    rules[str(identity)] = {"id": identity, "revision": 1, "creation_order": 1, **definition}
    store.check_graph_budget(rules, templates)
    try:
        rules_engine.validate_graph(rules, templates)
        if definition["enabled"]:
            rules_engine.validate_graph(rules, templates, require_enabled=True, root_ids=[str(identity)])
    except ValueError as error:
        raise store.fail("CHATBOT_INVALID_GRAPH", 422, str(error)) from None


async def _write_rule(db, tenant, actor, identity, definition, *, existing=None, origin=None):
    values = {"id": identity, "t": tenant, "a": actor.account_id, "origin": origin or identity,
              "device": definition["device_id"], "enabled": definition["enabled"], "mode": definition["match_mode"],
              "keyword": definition["keyword"], "normalized": rules_engine.normalize(definition["keyword"]),
              "response": store.encode(definition["response"])}
    if existing:
        query = """UPDATE subscription_chatbot_rules SET device_id=:device,enabled=:enabled,match_mode=:mode,
            keyword=:keyword,normalized_keyword=:normalized,response=CAST(:response AS jsonb),revision=revision+1,
            updated_at=clock_timestamp() WHERE tenant_id=:t AND id=:id RETURNING *"""
    else:
        values["order"] = await store.next_order(db, tenant)
        query = """INSERT INTO subscription_chatbot_rules
            (id,tenant_id,origin_id,creation_order,device_id,enabled,match_mode,keyword,normalized_keyword,response,created_by)
            VALUES(:id,:t,:origin,:order,:device,:enabled,:mode,:keyword,:normalized,CAST(:response AS jsonb),:a) RETURNING *"""
    return dict((await db.execute(text(query), values)).mappings().one())


async def mutate_rule(tenant, actor, kind, payload, rule_id=None):
    values = payload.model_dump(mode="json") | ({"rule_id": str(rule_id)} if rule_id else {})
    async with store.owner_transaction(tenant, actor) as db:
        job, replay = await store.request_job(db, tenant, actor, kind, payload.idempotency_key,
                                            values, payload.expected_chatbot_revision)
        if replay:
            if job["status"] != "succeeded":
                raise store.fail("CHATBOT_BUSY")
            return job["result"]
        await store.writable(db, tenant)
        old = await _rule(db, tenant, rule_id, expected=payload.expected_revision) if rule_id else None
        if kind in {"delete", "delete_all"}:
            if old:
                graph, _, _ = await store.load_graph(db, tenant)
                if any(str(rule_id) in rules_engine.references(row["response"]) for row in graph.values() if row["id"] != rule_id):
                    raise store.fail("CHATBOT_INVALID_GRAPH", 409, "Outra resposta usa este item. Remova essa referência antes de excluir.")
                deleted = 1
                await db.execute(text("""UPDATE subscription_chatbot_rules SET removed_at=clock_timestamp(),enabled=false,
                    revision=revision+1,updated_at=clock_timestamp() WHERE tenant_id=:t AND id=:id"""), {"t": tenant, "id": rule_id})
            else:
                deleted = (await db.execute(text("SELECT count(*) FROM subscription_chatbot_rules WHERE tenant_id=:t AND removed_at IS NULL"), {"t": tenant})).scalar_one()
                await db.execute(text("""UPDATE subscription_chatbot_rules SET removed_at=clock_timestamp(),enabled=false,
                    revision=revision+1,updated_at=clock_timestamp() WHERE tenant_id=:t AND removed_at IS NULL"""), {"t": tenant})
            result = {"deleted": deleted, "chatbot_revision": await store.bump_revision(db, tenant)}
        else:
            identity = uuid4() if kind in {"create", "duplicate"} else rule_id
            if kind in {"create", "update"}:
                definition = payload.rule.model_dump(mode="python")
            else:
                definition = {key: deepcopy(old[key]) for key in ("device_id", "enabled", "match_mode", "keyword", "response")}
                if kind == "toggle":
                    definition["enabled"] = payload.enabled
                else:
                    definition["enabled"] = False
                    definition["response"] = rules_engine.clone_response(definition["response"])
                    for action in rules_engine.response_actions(definition["response"]):
                        if action.get("rule_id") is not None and str(action["rule_id"]) == str(rule_id):
                            action["rule_id"] = str(identity)
            # model_dump(json) fixes all nested UUID representations while SQL
            # scope IDs retain their PostgreSQL uuid binding type.
            definition["response"] = json.loads(store.encode(definition["response"]))
            await validate_definition(db, tenant, actor, identity, definition)
            saved = await _write_rule(db, tenant, actor, identity, definition, existing=old if kind in {"update", "toggle"} else None)
            result = {"rule": store.rule_dto(saved), "chatbot_revision": await store.bump_revision(db, tenant)}
        await store.complete_job(db, job["id"], result)
        return jsonable_encoder(result)


@router.post(BASE + "/rules", status_code=201)
async def create_rule(tenant_id: UUID, request: Request, actor: Actor = Depends(authenticate)):
    return await mutate_rule(tenant_id, actor, "create", await bounded_json(request, schemas.CreateRule))


@router.put(BASE + "/rules/{rule_id}")
async def save_rule(tenant_id: UUID, rule_id: UUID, request: Request, actor: Actor = Depends(authenticate)):
    return await mutate_rule(tenant_id, actor, "update", await bounded_json(request, schemas.SaveRule), rule_id)


@router.patch(BASE + "/rules/{rule_id}/enabled")
async def toggle_rule(tenant_id: UUID, rule_id: UUID, request: Request, actor: Actor = Depends(authenticate)):
    return await mutate_rule(tenant_id, actor, "toggle", await bounded_json(request, schemas.ToggleRule), rule_id)


@router.post(BASE + "/rules/{rule_id}/duplicate")
async def duplicate_rule(tenant_id: UUID, rule_id: UUID, request: Request, actor: Actor = Depends(authenticate)):
    return await mutate_rule(tenant_id, actor, "duplicate", await bounded_json(request, schemas.RuleOperation), rule_id)


@router.delete(BASE + "/rules/{rule_id}")
async def delete_rule(tenant_id: UUID, rule_id: UUID, request: Request, actor: Actor = Depends(authenticate)):
    return await mutate_rule(tenant_id, actor, "delete", await bounded_json(request, schemas.RuleOperation), rule_id)


@router.post(BASE + "/delete-all")
async def delete_all(tenant_id: UUID, request: Request, actor: Actor = Depends(authenticate)):
    return await mutate_rule(tenant_id, actor, "delete_all", await bounded_json(request, schemas.DeleteAll))


@router.post(BASE + "/rules/{rule_id}/test", status_code=202)
async def test_rule(tenant_id: UUID, rule_id: UUID, request: Request, actor: Actor = Depends(authenticate)):
    payload = await bounded_json(request, schemas.TestRule)
    from app.subscription_chatbot_engine import create_test
    async with store.owner_transaction(tenant_id, actor) as db:
        job, replay = await store.request_job(db, tenant_id, actor, "test_response", payload.idempotency_key,
            payload.model_dump(mode="json") | {"rule_id": str(rule_id)}, payload.expected_chatbot_revision)
        if not replay:
            await store.writable(db, tenant_id)
            row = await _rule(db, tenant_id, rule_id, expected=payload.expected_revision)
            await create_test(db, tenant_id, actor, row, payload, job)
            job = dict((await db.execute(text("SELECT * FROM subscription_chatbot_jobs WHERE id=:id"), {"id": job["id"]})).mappings().one())
        return jsonable_encoder({"job": store.job_dto(job)})


@router.post(BASE + "/url-tags", status_code=202)
async def read_url_tags(tenant_id: UUID, request: Request, actor: Actor = Depends(authenticate)):
    payload = await bounded_json(request, schemas.ReadUrlTags)
    async with store.owner_transaction(tenant_id, actor) as db:
        job, replay = await store.request_job(db, tenant_id, actor, "url_tags", payload.idempotency_key,
            payload.model_dump(mode="json"), payload.expected_chatbot_revision)
        if replay:
            return jsonable_encoder({"job": store.job_dto(job)})
        if payload.device_id:
            await store.check_device(db, tenant_id, payload.device_id, actor_id=actor.account_id)
        await db.execute(text("UPDATE subscription_chatbot_jobs SET status='running',started_at=clock_timestamp() WHERE id=:id"), {"id": job["id"]})
    async def authorize():
        async with store.owner_transaction(tenant_id, actor) as db:
            await store.check_revision(db, tenant_id, payload.expected_chatbot_revision)
            current = (await db.execute(text("SELECT status FROM subscription_chatbot_jobs WHERE tenant_id=:t AND id=:id AND actor_account_id=:a"),
                                       {"t": tenant_id, "id": job["id"], "a": actor.account_id})).scalar_one_or_none()
            if current != "running":
                raise store.fail("CHATBOT_CONFLICT")
    from app.subscription_chatbot_http import read_url_tags as probe
    try:
        tags = await probe(payload.url, payload.method, request_id=job["id"], authorize=authorize)
        async with store.owner_transaction(tenant_id, actor) as db:
            await store.check_revision(db, tenant_id, payload.expected_chatbot_revision)
            result = await store.complete_job(db, job["id"], {"tags": tags})
    except HTTPException as error:
        async with store.support_session_factory()() as db:
            async with db.begin():
                result = (await db.execute(text("""UPDATE subscription_chatbot_jobs SET status='failed',
                    error_code=:code,finished_at=clock_timestamp() WHERE tenant_id=:t AND id=:id RETURNING *"""),
                    {"t": tenant_id, "id": job["id"], "code": "CHATBOT_CONFLICT" if error.status_code in {401,403,409}
                     else "CHATBOT_URL_UNAVAILABLE"})).mappings().one()
    return jsonable_encoder({"job": store.job_dto(result)})


@router.get(BASE + "/jobs/{job_id}")
async def get_job(tenant_id: UUID, job_id: UUID, response: Response, actor: Actor = Depends(authenticate)):
    response.headers["Cache-Control"] = "no-store"
    async with store.owner_transaction(tenant_id, actor, write=False) as db:
        row = (await db.execute(text("SELECT * FROM subscription_chatbot_jobs WHERE tenant_id=:t AND id=:id AND actor_account_id=:a"),
                               {"t": tenant_id, "id": job_id, "a": actor.account_id})).mappings().one_or_none()
        if not row or row["kind"] not in {"restore", "test_response", "url_tags"}:
            raise store.fail("CHATBOT_NOT_FOUND", 404)
        return jsonable_encoder({"job": store.job_dto(row)})


@router.post(BASE + "/exports")
async def create_export(tenant_id: UUID, request: Request, actor: Actor = Depends(authenticate)):
    payload = await bounded_json(request, schemas.ExportSelection)
    from app.subscription_chatbot_archive import reserve_export
    return await reserve_export(tenant_id, actor, payload)


@router.get(BASE + "/exports/{export_id}")
async def download_export(tenant_id: UUID, export_id: UUID, actor: Actor = Depends(authenticate)):
    from app.subscription_chatbot_archive import export_download
    return await export_download(tenant_id, actor, export_id)


@router.post(BASE + "/imports/preview")
async def import_preview(tenant_id: UUID, request: Request, actor: Actor = Depends(authenticate)):
    # Authenticate/workspace check before consuming a potentially ten MiB file.
    async with store.owner_transaction(tenant_id, actor):
        pass
    payload = await bounded_json(request, schemas.ImportPreview, limit=schemas.IMPORT_BODY_BYTES)
    from app.subscription_chatbot_archive import preview_import
    return await preview_import(tenant_id, actor, payload)


@router.get(BASE + "/backups")
async def list_backups(tenant_id: UUID, response: Response, actor: Actor = Depends(authenticate),
    page: int = Query(default=1, ge=1, le=10000), per_page: int = Query(default=20, ge=1, le=100)):
    response.headers["Cache-Control"] = "no-store"
    from app.subscription_chatbot_archive import backups
    return await backups(tenant_id, actor, page, per_page)


@router.get(BASE + "/backups/{backup_id}/download")
async def download_backup(tenant_id: UUID, backup_id: UUID, actor: Actor = Depends(authenticate)):
    from app.subscription_chatbot_archive import backup_download
    return await backup_download(tenant_id, actor, backup_id)


@router.post(BASE + "/backups/{backup_id}/preview")
async def restore_preview(tenant_id: UUID, backup_id: UUID, request: Request, actor: Actor = Depends(authenticate)):
    payload = await bounded_json(request, schemas.RestorePreview)
    from app.subscription_chatbot_archive import preview_backup
    return await preview_backup(tenant_id, actor, backup_id, payload)


@router.post(BASE + "/previews/{preview_id}/apply", status_code=202)
async def apply_preview(tenant_id: UUID, preview_id: UUID, request: Request, actor: Actor = Depends(authenticate)):
    payload = await bounded_json(request, schemas.ApplyPreview)
    from app.subscription_chatbot_archive import enqueue_restore
    return await enqueue_restore(tenant_id, actor, preview_id, payload)
