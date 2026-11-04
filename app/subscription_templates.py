"""Owner-scoped template library; CRUD and previews never execute messages."""
import asyncio
import base64
import binascii
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import os
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text

from app.catalog_api import Actor, authenticate
from app.subscription_chatbot import bounded_json
from app import subscription_chatbot_media as media
from app import subscription_chatbot_rules as rules
from app import subscription_chatbot_store as store
from app import subscription_templates_schemas as schemas

BASE = "/api/v1/tenants/{tenant_id}/subscription-whatsapp/templates"
FEATURE = "templates.manage"
router = APIRouter(tags=["Templates de assinaturas"])
ERRORS = {
    "TEMPLATE_NOT_FOUND": "O template não foi encontrado.",
    "TEMPLATE_IN_USE": "Este template é usado por respostas. Remova essas referências antes de excluí-lo.",
    "TEMPLATE_LIMIT": "A biblioteca permite até 100 templates. Remova um item antes de adicionar outro.",
    "TEMPLATE_KIND_IMMUTABLE": "O tipo do template não pode ser alterado. Crie um novo template para outro formato.",
    "TEMPLATE_ASSET_INVALID": "Selecione um arquivo válido deste negócio e do mesmo tipo do template.",
    "TEMPLATE_MEDIA_UNAVAILABLE": "O armazenamento ou a preparação desse formato está indisponível.",
}
MEDIA_TYPES = {
    "image": {"mimes": ["image/jpeg"], "max_bytes": 5_000_000},
    "sticker": {"mimes": ["image/webp"], "max_bytes": 500_000},
    "video": {"mimes": ["video/mp4"], "max_bytes": 16_000_000},
    "ptv": {"mimes": ["video/mp4"], "max_bytes": 16_000_000},
    "audio": {"mimes": ["audio/mpeg"], "max_bytes": 16_000_000},
    "document": {"mimes": ["application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.android.package-archive", "text/plain; charset=utf-8"], "max_bytes": 10_000_000},
}
TAGS = [{"key": key, "label": label} for key, label in (
    ("name", "Nome do contato"), ("phone_number", "Telefone do contato"),
    ("my_name", "Nome do negócio"), ("my_email", "E-mail da conta"),
    ("my_contact_number", "Contato comercial"))]


def fail(code, status=409):
    return HTTPException(status, {"code": code, "message": ERRORS.get(code, "A operação não foi concluída.")})


def owner_transaction(tenant, actor):
    return store.owner_transaction(tenant, actor, feature=FEATURE)


async def authorize(tenant, actor):
    async with owner_transaction(tenant, actor):
        pass


def capabilities():
    storage = all(os.getenv(name) for name in ("ORION_MEDIA_S3_ENDPOINT", "ORION_MEDIA_S3_BUCKET",
        "ORION_MEDIA_S3_REGION", "ORION_MEDIA_S3_ACCESS_KEY_ID", "ORION_MEDIA_S3_SECRET_ACCESS_KEY"))
    decoder = media.video_available()
    return {"manage": True, "upload": storage, "private_media": storage,
        "types": {kind: kind in {"text", "location"} or bool(storage and (
            kind not in {"video", "ptv", "audio"} or decoder)) for kind in schemas.KINDS}}


def limits():
    return {"templates": schemas.MAX_TEMPLATES, "name_utf16": 100, "text_utf16": 1000,
        "followup_utf16": 1000, "upload_bytes": schemas.UPLOAD_BYTES,
        "sticker_upload_bytes": schemas.STICKER_UPLOAD_BYTES, "ptv_seconds": 60,
        "audio_seconds": 1800, "media_extensions": ["jpg", "jpeg", "png", "webp", "pdf", "docx", "xlsx",
            "csv", "txt", "mp3", "mp4", "ogg", "wav", "opus", "apk"],
        "sticker_extensions": ["jpg", "jpeg", "png"], "ptv_extensions": ["mp4"]}


def dependency_map(graph):
    result = {}
    for row in sorted(graph.values(), key=lambda item: (item["creation_order"], str(item["id"])), reverse=True):
        for template_id in rules.template_references(row["response"]):
            result.setdefault(template_id, []).append({key: row[key] for key in
                ("id", "keyword", "enabled", "device_id")} | {"response_kind": row["response"]["kind"]})
    return result


def asset_dto(row, tenant, revision):
    result = store.asset_descriptor(row)
    prefix = BASE.format(tenant_id=tenant) + f"/assets/{row['id']}/content?expected_chatbot_revision={revision}&variant="
    return result | {"content_url": prefix + "original", "thumbnail_url": prefix + "thumbnail" if result["thumbnail"] else None}


def template_dto(row, tenant, revision, dependencies, assets):
    asset = assets.get(str(row["asset_id"])) if row.get("asset_id") else None
    return {key: row[key] for key in ("id", "revision", "name", "kind", "asset_id", "created_at", "updated_at")} | {
        "text": row["body"], "followup_text": row.get("followup_body", ""),
        "location": row.get("location"), "file_name": row.get("file_name"),
        "asset": asset_dto(asset, tenant, revision) if asset else None,
        "dependent_count": len(dependencies.get(str(row["id"]), []))}


def detail(row, tenant, revision, dependencies, assets):
    return {"template": template_dto(row, tenant, revision, dependencies, assets),
        "dependents": dependencies.get(str(row["id"]), []), "chatbot_revision": revision}


async def get_template(db, tenant, identity, expected=None):
    row = (await db.execute(text("""SELECT * FROM subscription_chatbot_templates
        WHERE tenant_id=:t AND id=:id AND removed_at IS NULL FOR UPDATE"""),
        {"t": tenant, "id": identity})).mappings().one_or_none()
    if row is None:
        raise fail("TEMPLATE_NOT_FOUND", 404)
    if expected is not None and row["revision"] != expected:
        raise store.fail("CHATBOT_CONFLICT")
    return dict(row)


async def ready_asset(db, tenant, identity):
    row = (await db.execute(text("""SELECT * FROM subscription_chatbot_assets
        WHERE tenant_id=:t AND id=:id AND status='ready'"""),
        {"t": tenant, "id": identity})).mappings().one_or_none()
    if row is None:
        raise fail("TEMPLATE_ASSET_INVALID", 422)
    return dict(row)


@router.get(BASE)
async def list_templates(tenant_id: UUID, response: Response, q: str = Query(default="", max_length=255),
                         actor: Actor = Depends(authenticate)):
    response.headers["cache-control"] = "no-store"
    try:
        q = rules.normalize(schemas.clean_text(q, empty=True, maximum=255))
    except ValueError:
        raise HTTPException(422, "Confira o texto da pesquisa.") from None
    async with owner_transaction(tenant_id, actor) as db:
        graph, templates, assets = await store.load_graph(db, tenant_id)
        revision = await store.revision(db, tenant_id)
        dependencies = dependency_map(graph)
        selected = [row for row in templates.values() if not q or q in rules.normalize(
            row["name"] + " " + row["body"] + " " + row.get("followup_body", ""))]
        selected.sort(key=lambda row: (row["created_at"], str(row["id"])), reverse=True)
        return jsonable_encoder({"chatbot_revision": revision,
            "templates": [template_dto(row, tenant_id, revision, dependencies, assets) for row in selected],
            "counts": {"total": len(templates), "filtered": len(selected), "limit": schemas.MAX_TEMPLATES},
            "capabilities": capabilities(), "limits": limits(), "media_types": MEDIA_TYPES, "tags": TAGS})


@router.get(BASE + "/{template_id}")
async def template_detail(tenant_id: UUID, template_id: UUID, response: Response,
                          actor: Actor = Depends(authenticate)):
    response.headers["cache-control"] = "no-store"
    async with owner_transaction(tenant_id, actor) as db:
        row = await get_template(db, tenant_id, template_id)
        graph, _, assets = await store.load_graph(db, tenant_id)
        return jsonable_encoder(detail(row, tenant_id, await store.revision(db, tenant_id), dependency_map(graph), assets))


async def validate_candidate(tenant, definition, identity, graph, templates, assets, *, existing=None):
    if definition["kind"] != (existing or definition)["kind"]:
        raise fail("TEMPLATE_KIND_IMMUTABLE")
    if existing is None and len(templates) >= schemas.MAX_TEMPLATES:
        raise fail("TEMPLATE_LIMIT")
    asset_id = definition["asset_id"]
    asset = assets.get(str(asset_id)) if asset_id else None
    if asset_id and (asset is None or asset["kind"] != definition["kind"]):
        raise fail("TEMPLATE_ASSET_INVALID", 422)
    if asset:
        if asset["mime"] not in MEDIA_TYPES[definition["kind"]]["mimes"]:
            raise fail("TEMPLATE_ASSET_INVALID", 422)
        if definition["file_name"] is not None:
            try:
                normalized = schemas.media_filename(definition["file_name"], asset["mime"])
            except ValueError:
                raise fail("TEMPLATE_ASSET_INVALID", 422) from None
            if normalized != definition["file_name"]:
                raise fail("TEMPLATE_ASSET_INVALID", 422)
    value = {"id": identity, "name": definition["name"], "kind": definition["kind"],
        "body": definition["text"], "followup_body": definition["followup_text"],
        "asset_id": asset_id, "location": definition["location"], "file_name": definition["file_name"],
        "asset": store.asset_descriptor(asset) if asset else None}
    changed = {**templates, str(identity): value}
    store.check_graph_budget(graph, changed)
    try:
        # Inactive references are intentionally retained. Structural validation
        # and compile limits apply equally to sequence and list-row dependencies.
        rules.validate_graph(graph, changed)
    except ValueError:
        raise store.fail("CHATBOT_INVALID_GRAPH", 422,
                         "A alteração não é compatível com as respostas que usam este template.") from None
    return value


async def mutate(tenant, actor, action, payload, identity=None):
    values = payload.model_dump(mode="json") | ({"template_id": str(identity)} if identity else {})
    async with owner_transaction(tenant, actor) as db:
        job, replay = await store.request_job(db, tenant, actor, "template_" + action,
            payload.request_id, values, payload.expected_chatbot_revision)
        if replay:
            if job["status"] != "succeeded":
                raise store.fail("CHATBOT_BUSY")
            return job["result"]
        await store.writable(db, tenant)
        old = await get_template(db, tenant, identity, payload.expected_revision) if identity else None
        graph, templates, assets = await store.load_graph(db, tenant)
        dependencies = dependency_map(graph)
        if action == "delete":
            if dependencies.get(str(identity)):
                raise fail("TEMPLATE_IN_USE")
            await db.execute(text("""UPDATE subscription_chatbot_templates SET removed_at=clock_timestamp(),
                updated_at=clock_timestamp(),revision=revision+1 WHERE tenant_id=:t AND id=:id"""),
                {"t": tenant, "id": identity})
            result = {"request_id": payload.request_id, "deleted": 1, "template_id": identity,
                      "chatbot_revision": await store.bump_revision(db, tenant)}
        else:
            identity = identity or uuid4()
            definition = payload.template.model_dump(mode="python")
            candidate = await validate_candidate(tenant, definition, identity, graph, templates, assets, existing=old)
            query_values = {"t": tenant, "id": identity, "name": candidate["name"], "kind": candidate["kind"],
                "body": candidate["body"], "followup": candidate["followup_body"], "asset": candidate["asset_id"],
                "location": store.encode(candidate["location"]) if candidate["location"] is not None else None,
                "filename": candidate["file_name"]}
            if old:
                query = """UPDATE subscription_chatbot_templates SET name=:name,body=:body,followup_body=:followup,
                    asset_id=:asset,location=CAST(:location AS jsonb),file_name=:filename,
                    revision=revision+1,updated_at=clock_timestamp() WHERE tenant_id=:t AND id=:id RETURNING *"""
            else:
                query = """INSERT INTO subscription_chatbot_templates
                    (id,tenant_id,origin_id,name,kind,body,followup_body,asset_id,location,file_name)
                    VALUES(:id,:t,:id,:name,:kind,:body,:followup,:asset,CAST(:location AS jsonb),:filename) RETURNING *"""
            row = dict((await db.execute(text(query), query_values)).mappings().one())
            revision = await store.bump_revision(db, tenant)
            result = detail(row, tenant, revision, dependencies, assets) | {"request_id": payload.request_id}
        await store.complete_job(db, job["id"], result)
        return jsonable_encoder(result)


@router.post(BASE, status_code=201)
async def create_template(tenant_id: UUID, request: Request, response: Response,
                          actor: Actor = Depends(authenticate)):
    response.headers["cache-control"] = "no-store"
    await authorize(tenant_id, actor)
    return await mutate(tenant_id, actor, "create", await bounded_json(request, schemas.CreateTemplate, limit=24_000))


@router.put(BASE + "/{template_id}")
async def update_template(tenant_id: UUID, template_id: UUID, request: Request, response: Response,
                          actor: Actor = Depends(authenticate)):
    response.headers["cache-control"] = "no-store"
    await authorize(tenant_id, actor)
    return await mutate(tenant_id, actor, "update", await bounded_json(request, schemas.UpdateTemplate, limit=24_000), template_id)


@router.delete(BASE + "/{template_id}")
async def delete_template(tenant_id: UUID, template_id: UUID, request: Request, response: Response,
                          actor: Actor = Depends(authenticate)):
    response.headers["cache-control"] = "no-store"
    await authorize(tenant_id, actor)
    return await mutate(tenant_id, actor, "delete", await bounded_json(request, schemas.DeleteTemplate, limit=4096), template_id)


async def upload(tenant, actor, payload):
    if not capabilities()["types"].get(payload.kind):
        raise fail("TEMPLATE_MEDIA_UNAVAILABLE", 503)
    try:
        raw = base64.b64decode(payload.content_base64, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(422, "O arquivo não foi enviado corretamente.") from None
    maximum = schemas.STICKER_UPLOAD_BYTES if payload.kind == "sticker" else schemas.UPLOAD_BYTES
    if not raw or len(raw) > maximum:
        raise HTTPException(413, "O arquivo ultrapassa o limite permitido.")
    values = payload.model_dump(mode="json", exclude={"content_base64"}) | {
        "source_sha256": hashlib.sha256(raw).hexdigest(), "source_bytes": len(raw)}
    nonce = str(uuid4())
    async with owner_transaction(tenant, actor) as db:
        job, replay = await store.request_job(db, tenant, actor, "template_asset", payload.request_id,
                                            values, payload.expected_chatbot_revision)
        if replay and job["status"] == "succeeded":
            return job["result"]
        await store.check_revision(db, tenant, payload.expected_chatbot_revision)
        await store.writable(db, tenant)
        if replay and job["status"] == "running" and job["started_at"] is not None and (
                job["started_at"] > datetime.now(timezone.utc) - timedelta(minutes=3)):
            raise store.fail("CHATBOT_BUSY")
        await db.execute(text("""UPDATE subscription_chatbot_jobs SET status='running',
            started_at=clock_timestamp(),finished_at=NULL,error_code=NULL,
            payload=CAST(:payload AS jsonb) WHERE tenant_id=:t AND id=:id"""),
            {"t": tenant, "id": job["id"], "payload": store.encode(values | {"runner_nonce": nonce})})

    @asynccontextmanager
    async def protected():
        async with owner_transaction(tenant, actor) as db:
            await store.check_revision(db, tenant, payload.expected_chatbot_revision)
            await store.writable(db, tenant)
            current = (await db.execute(text("""SELECT status,payload FROM subscription_chatbot_jobs
                WHERE tenant_id=:t AND id=:id FOR UPDATE"""), {"t": tenant, "id": job["id"]})).mappings().one_or_none()
            if not current or current["status"] != "running" or current["payload"].get("runner_nonce") != nonce:
                raise store.fail("CHATBOT_CONFLICT")
            await db.execute(text("UPDATE subscription_chatbot_jobs SET started_at=clock_timestamp() WHERE id=:id"), {"id": job["id"]})
            yield db

    try:
        prepared = await asyncio.to_thread(media.prepare_media, raw, payload.kind, filename=payload.filename)
        try:
            filename = schemas.media_filename(payload.filename, prepared.mime)
        except ValueError:
            raise HTTPException(422, "Use um nome de arquivo com até 120 caracteres, incluindo a extensão final.") from None
        row = await store.persist_prepared(tenant, payload.kind, prepared, protected)
        async with protected() as db:
            result = {"request_id": payload.request_id, "chatbot_revision": payload.expected_chatbot_revision,
                "asset": asset_dto(row, tenant, payload.expected_chatbot_revision), "file_name": filename}
            await store.complete_job(db, job["id"], result)
        return jsonable_encoder(result)
    except BaseException:
        # This only fences the request. persist_prepared owns the upload thread,
        # registry reservation and S3 cleanup until its real completion.
        async def failed():
            async with store.support_session_factory()() as db:
                async with db.begin():
                    await db.execute(text("""UPDATE subscription_chatbot_jobs SET status='failed',
                        error_code='TEMPLATE_UPLOAD_FAILED',finished_at=clock_timestamp()
                        WHERE tenant_id=:t AND id=:id AND status='running'
                          AND payload->>'runner_nonce'=:nonce"""), {"t": tenant, "id": job["id"], "nonce": nonce})
        cleanup = store._track_media(asyncio.create_task(failed()))
        await asyncio.shield(cleanup)
        raise


@router.post(BASE + "/assets", status_code=201)
async def upload_asset(tenant_id: UUID, request: Request, response: Response,
                       actor: Actor = Depends(authenticate)):
    response.headers["cache-control"] = "no-store"
    await authorize(tenant_id, actor)
    payload = await bounded_json(request, schemas.UploadAsset, limit=schemas.UPLOAD_BODY_BYTES)
    return await upload(tenant_id, actor, payload)


@router.get(BASE + "/assets/{asset_id}/content")
async def asset_content(tenant_id: UUID, asset_id: UUID,
    expected_chatbot_revision: int = Query(ge=1), variant: str = "original", actor: Actor = Depends(authenticate)):
    if variant not in {"original", "thumbnail"}:
        raise HTTPException(422, "Versão de arquivo inválida.")
    async with owner_transaction(tenant_id, actor) as db:
        await store.check_revision(db, tenant_id, expected_chatbot_revision)
        row = await ready_asset(db, tenant_id, asset_id)
        if variant == "thumbnail":
            if not row["thumbnail_size"]:
                raise fail("TEMPLATE_NOT_FOUND", 404)
            size, digest, mime = row["thumbnail_size"], row["thumbnail_sha256"], row["thumbnail_mime"]
        else:
            size, digest, mime = row["size"], row["sha256"], row["mime"]
        allowed = MEDIA_TYPES.get(row["kind"])
        if not allowed or (variant == "original" and (mime not in allowed["mimes"] or size > allowed["max_bytes"])):
            raise fail("TEMPLATE_ASSET_INVALID", 422)
        if variant == "thumbnail" and (mime != "image/jpeg" or size > 100_000):
            raise fail("TEMPLATE_ASSET_INVALID", 422)
        metadata = dict(row)
    data = await asyncio.to_thread(media.get_media, tenant_id, asset_id, expected_size=size,
                                   expected_sha256=digest, variant=variant)
    async with owner_transaction(tenant_id, actor) as db:
        await store.check_revision(db, tenant_id, expected_chatbot_revision)
        current = await ready_asset(db, tenant_id, asset_id)
        if current != metadata:
            raise store.fail("CHATBOT_CONFLICT")
    name = "miniatura.jpg" if variant == "thumbnail" else store.asset_descriptor(row)["file_name"]
    disposition = "attachment" if row["kind"] == "document" and variant == "original" else "inline"
    return Response(data, media_type=mime, headers={"cache-control": "no-store", "x-content-type-options": "nosniff",
        "content-security-policy": "default-src 'none'; sandbox", "cross-origin-resource-policy": "same-origin",
        "content-disposition": f"{disposition}; filename*=UTF-8''{quote(name, safe='')}", "content-length": str(len(data))})
