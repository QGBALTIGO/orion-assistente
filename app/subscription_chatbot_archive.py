"""Owner-scoped portable archives and atomic, resumable graph restoration.

Snapshots contain definitions and immutable asset references, never executable
instructions. Import previews decode real media without writing it. A restore
publishes rules only in its final guarded transaction after every required
private object has been prepared and verified.
"""
import asyncio
import base64
import binascii
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from uuid import UUID, uuid4

from fastapi import HTTPException, Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text

from app.catalog_api import Actor
from app import subscription_chatbot_archive_format as codec
from app import subscription_chatbot_media as media
from app import subscription_chatbot_rules as rules
from app import subscription_chatbot_schemas as schemas
from app import subscription_chatbot_store as store


TTL = timedelta(minutes=15)
CLAIM_TIMEOUT = timedelta(minutes=10)
MAX_PREVIEWS = 3
MAX_EXPORTS = 5
# A ten MiB uploaded file occupies up to 13.34 MiB as base64. The remaining
# bounded space holds ID maps and normalized hashes, not a second rule graph.
MAX_PREVIEW_BYTES = 15 * 1024 * 1024
ARCHIVE_TIMEOUT = 150
RESTORE_TIMEOUT = 9 * 60
_FIELDS = ("device_id", "enabled", "match_mode", "keyword", "response")
_ASSET_FIELDS = ("id", "kind", "mime", "size", "sha256", "width", "height", "duration_ms", "thumbnail")
log = logging.getLogger(__name__)


def _now():
    return datetime.now(timezone.utc)


def _code(error):
    detail = error.detail if isinstance(error, HTTPException) else None
    code = detail.get("code") if isinstance(detail, dict) else None
    allowed = set(store.ERRORS) | {"WORKSPACE_FEATURE_UNAVAILABLE", "CHATBOT_MEDIA_INVALID",
        "CHATBOT_MEDIA_TOO_LARGE", "CHATBOT_MEDIA_DURATION", "CHATBOT_MEDIA_UNAVAILABLE",
        "CHATBOT_MEDIA_BUSY", "CHATBOT_MEDIA_STORAGE", "CHATBOT_MEDIA_TIMEOUT"}
    return code if code in allowed else "CHATBOT_EFFECT_REVOKED" if isinstance(error, HTTPException) and error.status_code in {401, 403} else "CHATBOT_RESTORE_FAILED"


def _invalid():
    return store.fail("CHATBOT_IMPORT_INVALID", 422)


def _bounded_size(value, limit):
    """Count UTF-8 incrementally; never materialize an oversized JSON string."""
    total = 0
    try:
        for chunk in json.JSONEncoder(ensure_ascii=False, separators=(",", ":"), allow_nan=False).iterencode(jsonable_encoder(value)):
            total += len(chunk.encode("utf-8"))
            if total > limit:
                raise store.fail("CHATBOT_INVALID_GRAPH", 413, "O conjunto ultrapassa o limite de tamanho. Reduza a seleção.")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise _invalid() from None
    return total


def _metadata(identity, kind, prepared):
    return {"id": str(identity), "kind": kind, "mime": prepared.mime, "size": prepared.size,
        "sha256": prepared.sha256, "width": prepared.width, "height": prepared.height,
        "duration_ms": prepared.duration_ms, "thumbnail": {
            "mime": prepared.thumbnail_mime, "size": len(prepared.thumbnail),
            "sha256": prepared.thumbnail_sha256} if prepared.thumbnail else None}


def _asset_metadata(row):
    descriptor = store.asset_descriptor(row)
    return jsonable_encoder({key: descriptor[key] for key in _ASSET_FIELDS})


def _rewrite(response, rule_ids, template_ids):
    result = deepcopy(response)
    try:
        for action in rules.response_actions(result):
            if action["type"] in {"nested_reply", "nested_list"}:
                action["rule_id"] = str(rule_ids[str(action["rule_id"])])
            elif action["type"] in {"template", "sticker", "shortvideo"}:
                action["template_id"] = str(template_ids[str(action["template_id"])])
    except KeyError:
        raise store.fail("CHATBOT_INVALID_GRAPH", 422) from None
    return result


def _graph_fingerprint(graph):
    current_rules, templates, _ = graph
    value = {"rules": [{key: row.get(key) for key in ("id", "origin_id", "revision", "creation_order", *_FIELDS)}
                       for row in sorted(current_rules.values(), key=lambda row: str(row["id"]))],
             "templates": [{key: row.get(key) for key in ("id", "origin_id", "revision", "name", "kind", "body", "asset_id")}
                           for row in sorted(templates.values(), key=lambda row: str(row["id"]))]}
    return store.fingerprint(value)


def _snapshot(graph, selected=None):
    """Portable identities are origins; selecting a root includes its closure."""
    current_rules, templates, assets = graph
    chosen = set(current_rules) if selected is None else {str(value) for value in selected}
    if not chosen <= current_rules.keys():
        raise store.fail("CHATBOT_NOT_FOUND", 404)
    pending = list(chosen)
    while pending:
        identity = pending.pop()
        for child in rules.references(current_rules[identity]["response"]):
            if child not in current_rules:
                raise store.fail("CHATBOT_INVALID_GRAPH", 422)
            if child not in chosen:
                chosen.add(child)
                pending.append(child)
    template_keys = set().union(*(rules.template_references(current_rules[key]["response"]) for key in chosen)) if chosen else set()
    if not template_keys <= templates.keys():
        raise store.fail("CHATBOT_INVALID_GRAPH", 422)
    rule_ids = {key: str(current_rules[key]["origin_id"]) for key in chosen}
    template_ids = {key: str(templates[key]["origin_id"]) for key in template_keys}
    document = {"format": codec.FORMAT, "version": codec.VERSION, "exported_at": _now().isoformat(),
                "rules": [], "templates": [], "assets": []}
    for key in sorted(chosen, key=lambda key: current_rules[key]["creation_order"]):
        row = current_rules[key]
        definition = jsonable_encoder({field: row[field] for field in _FIELDS})
        definition["response"] = _rewrite(definition["response"], rule_ids, template_ids)
        document["rules"].append({"id": rule_ids[key], "creation_order": row["creation_order"], "definition": definition})
    asset_keys = set()
    for key in sorted(template_keys):
        row = templates[key]
        asset = str(row["asset_id"]) if row["asset_id"] else None
        if asset:
            asset_keys.add(asset)
        document["templates"].append({"id": template_ids[key], "definition": {
            "name": row["name"], "kind": row["kind"], "text": row["body"], "asset_id": asset}})
    if not asset_keys <= assets.keys():
        raise store.fail("CHATBOT_CAPABILITY_UNAVAILABLE", 409, "Uma mídia necessária não está disponível.")
    document["assets"] = [_asset_metadata(assets[key]) for key in sorted(asset_keys)]
    _bounded_size(document, store.MAX_GRAPH_BYTES)
    return document


def _document_size(document):
    # Listed size describes the complete snapshot before ZIP compression.
    return _bounded_size(document, store.MAX_GRAPH_BYTES) + sum(
        item["size"] + (item["thumbnail"]["size"] if item["thumbnail"] else 0) for item in document["assets"])


async def _cleanup_previews(db, tenant):
    await db.execute(text("""UPDATE subscription_chatbot_previews p SET snapshot='{}'::jsonb
        WHERE p.tenant_id=:t AND p.snapshot<>'{}'::jsonb
          AND (p.expires_at<=clock_timestamp() OR p.applied_job_id IS NOT NULL)
          AND NOT EXISTS(SELECT 1 FROM subscription_chatbot_jobs j WHERE j.tenant_id=p.tenant_id
            AND j.id=p.applied_job_id AND j.status IN ('queued','running'))"""), {"t": tenant})
    await db.execute(text("""UPDATE subscription_chatbot_jobs SET payload='{}'::jsonb
        WHERE tenant_id=:t AND kind='export' AND payload<>'{}'::jsonb
          AND created_at<clock_timestamp()-interval '15 minutes'"""), {"t": tenant})


def _preview_dto(row):
    return jsonable_encoder({"preview": {"id": row["id"], "chatbot_revision": row["revision"],
        "source": row["source"], "mode": row["mode"], "expires_at": row["expires_at"],
        "counts": row["counts"], "warnings": row["warnings"],
        "can_apply": bool(row["snapshot"].get("plan", {}).get("can_apply"))}, "chatbot_revision": row["revision"]})


def _template_signature(kind, body, asset, name):
    return store.fingerprint({"kind": kind, "body": body, "name": name,
        "asset": {"kind": asset["kind"], "sha256": asset["sha256"]} if asset else None})


def _materialize(document, plan, graph):
    """Reconstruct the reviewed graph using its fixed IDs, without DB writes."""
    existing, templates, _ = graph
    source_rules = {item["id"]: item for item in document["rules"]}
    source_templates = {item["id"]: item["definition"] for item in document["templates"]}
    source_assets = {item["id"]: item for item in document["assets"]}
    rule_ids = {item["source_id"]: item["id"] for item in plan["rules"]}
    template_ids = {item["source_id"]: item["id"] for item in plan["templates"]}
    merged = {key: dict(row) for key, row in existing.items() if key not in plan["delete_ids"]}
    merged_templates = dict(templates)
    writes, template_writes = [], []
    for item in plan["templates"]:
        if item["operation"] != "create":
            continue
        source = source_templates[item["source_id"]]
        asset = source_assets.get(source["asset_id"])
        value = {"id": item["id"], "origin_id": item["origin_id"], "revision": 1,
                 "name": source["name"], "kind": source["kind"], "body": source["text"],
                 "asset_id": source["asset_id"], "asset": asset}
        merged_templates[item["id"]] = value
        template_writes.append(value)
    for item in plan["rules"]:
        if item["operation"] == "skip":
            continue
        source = source_rules[item["source_id"]]
        definition = deepcopy(source["definition"])
        if plan["device_id"] is not None:
            definition["device_id"] = plan["device_id"]
        definition["response"] = _rewrite(definition["response"], rule_ids, template_ids)
        value = {"id": item["id"], "origin_id": item["source_id"], "revision": 1,
                 "creation_order": source["creation_order"], **definition}
        merged[item["id"]] = value
        writes.append((item, value))
    store.check_graph_budget(merged, merged_templates)
    try:
        # A saved graph may deliberately contain an enabled parent whose
        # dependency was disabled later. Restore those flags exactly; only the
        # execution engine decides whether a live traversal may run.
        rules.validate_graph(merged, merged_templates)
    except (ValueError, KeyError, TypeError):
        raise store.fail("CHATBOT_INVALID_GRAPH", 422) from None
    return writes, template_writes, merged, merged_templates


async def _check_devices(db, tenant, actor, document, plan):
    source_rules = {item["id"]: item["definition"] for item in document["rules"]}
    checked, warnings = set(), []
    for item in plan["rules"]:
        if item["operation"] == "skip":
            continue
        device = plan["device_id"] or source_rules[item["source_id"]]["device_id"]
        if device is None or device in checked:
            continue
        checked.add(device)
        try:
            await store.check_device(db, tenant, UUID(device), actor_id=actor.account_id)
        except HTTPException:
            warnings.append("Um dispositivo original não está disponível. Escolha um dispositivo para atribuir as respostas antes de aplicar.")
    return list(dict.fromkeys(warnings))


async def _media_quota(db, tenant, document, plan):
    needed = set(plan["asset_ids"])
    assets = {item["id"]: item for item in document["assets"] if item["id"] in needed}
    rows = (await db.execute(text("SELECT kind,sha256,size,thumbnail_size,status FROM subscription_chatbot_assets WHERE tenant_id=:t"),
                             {"t": tenant})).mappings().all()
    present = {(row["kind"], row["sha256"]): row for row in rows}
    unique = {(item["kind"], item["sha256"]): item for item in assets.values()}
    additions = [item for key, item in unique.items() if key not in present]
    total = sum(row["size"] + (row["thumbnail_size"] or 0) for row in rows)
    total += sum(item["size"] + (item["thumbnail"]["size"] if item["thumbnail"] else 0) for item in additions)
    if len(rows) + len(additions) > store.MAX_ASSETS or total > store.MAX_TENANT_MEDIA_BYTES:
        raise store.fail("CHATBOT_MEDIA_QUOTA", 413)
    return sum(key not in present or present[key]["status"] != "ready" for key in unique)


async def _plan(db, tenant, actor, document, mode, device_id, graph):
    current, templates, _ = graph
    by_origin = {str(row["origin_id"]): row for row in current.values()}
    source = {row["id"]: row for row in document["rules"]}
    source_templates = {row["id"]: row["definition"] for row in document["templates"]}
    source_assets = {row["id"]: row for row in document["assets"]}
    plan = {"version": 1, "base_fingerprint": _graph_fingerprint(graph), "mode": mode,
            "device_id": str(device_id) if device_id else None, "rules": [], "templates": [], "asset_ids": [],
            "delete_ids": sorted(str(row["id"]) for origin, row in by_origin.items() if mode == "exact" and origin not in source)}
    needed_templates = set()
    for item in document["rules"]:
        old = by_origin.get(item["id"])
        operation = "skip" if old and mode == "add_missing" else "update" if old else "create"
        plan["rules"].append({"source_id": item["id"], "id": str(old["id"]) if old else str(uuid4()), "operation": operation})
        if operation != "skip":
            needed_templates.update(rules.template_references(item["definition"]["response"]))
    signatures = {_template_signature(row["kind"], row["body"], row.get("asset"), row["name"]): row
                  for row in sorted(templates.values(), key=lambda row: str(row["id"]))
                  if row["kind"] == "text" or row.get("asset") is not None}
    occupied_origins = {str(row["origin_id"]) for row in templates.values()}
    for identity in sorted(needed_templates):
        value = source_templates[identity]
        asset = source_assets.get(value["asset_id"])
        signature = _template_signature(value["kind"], value["text"], asset, value["name"])
        candidate = signatures.get(signature)
        if candidate:
            item = {"source_id": identity, "id": str(candidate["id"]), "origin_id": str(candidate["origin_id"]), "operation": "reuse"}
        else:
            item = {"source_id": identity, "id": str(uuid4()),
                    "origin_id": str(uuid4()) if identity in occupied_origins else identity, "operation": "create"}
            signatures[signature] = item
            occupied_origins.add(item["origin_id"])
        if value["asset_id"]:
            plan["asset_ids"].append(value["asset_id"])
        plan["templates"].append(item)
    plan["asset_ids"] = sorted(set(plan["asset_ids"]))
    _materialize(document, plan, graph)
    warnings = await _check_devices(db, tenant, actor, document, plan)
    counts = {key: sum(item["operation"] == key for item in plan["rules"]) for key in ("create", "update", "skip")}
    counts.update(delete=len(plan["delete_ids"]), template_clones=sum(item["operation"] == "create" for item in plan["templates"]),
                  media_copies=await _media_quota(db, tenant, document, plan))
    plan["can_apply"] = not warnings and bool(counts["create"] or counts["update"] or counts["delete"])
    return plan, counts, warnings


def _decode_raw(encoded):
    try:
        raw = base64.b64decode(encoded, validate=True)
        if not raw or len(raw) > schemas.IMPORT_BYTES or base64.b64encode(raw).decode() != encoded:
            raise ValueError()
        return raw
    except (ValueError, TypeError, binascii.Error):
        raise _invalid() from None


async def _normalize_document(raw, filename):
    try:
        document = await asyncio.to_thread(codec.read_archive, raw, filename)
    except (ValueError, TypeError):
        raise _invalid() from None
    normalized, expanded = [], 0
    for item in document["assets"]:
        prepared = await asyncio.to_thread(media.prepare_media, item["data"], item["kind"])
        expanded += prepared.size + len(prepared.thumbnail or b"")
        if expanded > codec.MAX_EXPANDED_BYTES:
            raise store.fail("CHATBOT_MEDIA_QUOTA", 413)
        normalized.append(_metadata(item["id"], item["kind"], prepared))
    document["assets"] = normalized
    return document


async def _insert_preview(db, tenant, actor, *, source, backup_id, mode, device_id, revision, snapshot, counts, warnings):
    await _cleanup_previews(db, tenant)
    active = (await db.execute(text("""SELECT count(*) FROM subscription_chatbot_previews
        WHERE tenant_id=:t AND applied_job_id IS NULL AND expires_at>clock_timestamp()"""), {"t": tenant})).scalar_one()
    if active >= MAX_PREVIEWS:
        raise store.fail("CHATBOT_BUSY", 429, "Há três prévias em aberto. Aplique uma delas ou aguarde sua expiração.")
    _bounded_size(snapshot, MAX_PREVIEW_BYTES)
    row = (await db.execute(text("""INSERT INTO subscription_chatbot_previews
        (tenant_id,actor_account_id,revision,source,backup_id,mode,device_id,snapshot,counts,warnings)
        VALUES(:t,:a,:revision,:source,:backup,:mode,:device,CAST(:snapshot AS jsonb),CAST(:counts AS jsonb),CAST(:warnings AS jsonb))
        RETURNING *"""), {"t": tenant, "a": actor.account_id, "revision": revision, "source": source,
        "backup": backup_id, "mode": mode, "device": device_id, "snapshot": store.encode(snapshot),
        "counts": store.encode(counts), "warnings": store.encode(warnings)})).mappings().one()
    return _preview_dto(row)


async def preview_import(tenant, actor, payload):
    async with store.owner_transaction(tenant, actor) as db:
        await store.writable(db, tenant)
        revision = await store.revision(db, tenant)
        if payload.device_id:
            await store.check_device(db, tenant, payload.device_id, actor_id=actor.account_id)
    raw = _decode_raw(payload.content_base64)
    try:
        async with asyncio.timeout(ARCHIVE_TIMEOUT):
            document = await _normalize_document(raw, payload.filename)
    except TimeoutError:
        raise store.fail("CHATBOT_MEDIA_TIMEOUT", 422, "A preparação demorou demais. Reduza a seleção de arquivos.") from None
    async with store.owner_transaction(tenant, actor) as db:
        await store.check_revision(db, tenant, revision)
        await store.writable(db, tenant)
        graph = await store.load_graph(db, tenant)
        plan, counts, warnings = await _plan(db, tenant, actor, document, payload.mode, payload.device_id, graph)
        snapshot = {"version": 1, "filename": payload.filename, "content_base64": payload.content_base64,
                    "archive_sha256": hashlib.sha256(raw).hexdigest(), "prepared_assets": document["assets"],
                    "document_fingerprint": store.fingerprint(document), "plan": plan}
        return await _insert_preview(db, tenant, actor, source="import", backup_id=None, mode=payload.mode,
            device_id=payload.device_id, revision=revision, snapshot=snapshot, counts=counts, warnings=warnings)


async def _backup(db, tenant, identity):
    row = (await db.execute(text("SELECT * FROM subscription_chatbot_backups WHERE tenant_id=:t AND id=:id"),
                            {"t": tenant, "id": identity})).mappings().one_or_none()
    if not row:
        raise store.fail("CHATBOT_NOT_FOUND", 404)
    return dict(row)


async def preview_backup(tenant, actor, backup_id, payload):
    async with store.owner_transaction(tenant, actor) as db:
        await store.writable(db, tenant)
        row = await _backup(db, tenant, backup_id)
        document = row["snapshot"]
        if payload.device_id:
            await store.check_device(db, tenant, payload.device_id, actor_id=actor.account_id)
        graph = await store.load_graph(db, tenant)
        plan, counts, warnings = await _plan(db, tenant, actor, document, payload.mode, payload.device_id, graph)
        snapshot = {"version": 1, "backup_id": str(backup_id), "document_fingerprint": store.fingerprint(document), "plan": plan}
        return await _insert_preview(db, tenant, actor, source="backup", backup_id=backup_id, mode=payload.mode,
            device_id=payload.device_id, revision=await store.revision(db, tenant), snapshot=snapshot, counts=counts, warnings=warnings)


async def _preview(db, tenant, actor_id, identity):
    row = (await db.execute(text("""SELECT * FROM subscription_chatbot_previews
        WHERE tenant_id=:t AND id=:id AND actor_account_id=:a FOR UPDATE"""),
        {"t": tenant, "id": identity, "a": actor_id})).mappings().one_or_none()
    if not row:
        raise store.fail("CHATBOT_NOT_FOUND", 404)
    return dict(row)


async def _reviewed_document(db, tenant, preview, *, check_expiry=True):
    snapshot = preview["snapshot"]
    if check_expiry and preview["expires_at"] <= _now():
        raise store.fail("CHATBOT_PREVIEW_EXPIRED")
    if not snapshot or not snapshot.get("plan"):
        raise store.fail("CHATBOT_PREVIEW_STALE")
    if preview["source"] == "backup":
        document = (await _backup(db, tenant, preview["backup_id"]))["snapshot"]
    else:
        raw = _decode_raw(snapshot["content_base64"])
        if hashlib.sha256(raw).hexdigest() != snapshot["archive_sha256"]:
            raise _invalid()
        try:
            document = codec.read_archive(raw, snapshot["filename"])
        except ValueError:
            raise _invalid() from None
        document["assets"] = snapshot["prepared_assets"]
    if store.fingerprint(document) != snapshot["document_fingerprint"]:
        raise _invalid()
    return document


async def _validate_review(db, tenant, actor, preview, document, *, check_expiry=True):
    if check_expiry and preview["expires_at"] <= _now():
        raise store.fail("CHATBOT_PREVIEW_EXPIRED")
    if await store.revision(db, tenant) != preview["revision"]:
        raise store.fail("CHATBOT_PREVIEW_STALE")
    plan = preview["snapshot"].get("plan")
    if not plan or not plan.get("can_apply"):
        raise store.fail("CHATBOT_INVALID_GRAPH", 422, "A prévia não contém alterações aplicáveis. Revise os avisos e gere outra prévia.")
    graph = await store.load_graph(db, tenant)
    if _graph_fingerprint(graph) != plan["base_fingerprint"]:
        raise store.fail("CHATBOT_PREVIEW_STALE")
    _materialize(document, plan, graph)
    if await _check_devices(db, tenant, actor, document, plan):
        raise store.fail("CHATBOT_EFFECT_REVOKED")
    await _media_quota(db, tenant, document, plan)
    return graph


async def enqueue_restore(tenant, actor, preview_id, payload):
    async with store.owner_transaction(tenant, actor) as db:
        preview = await _preview(db, tenant, actor.account_id, preview_id)
        request = payload.model_dump(mode="json") | {"preview_id": str(preview_id), "source": preview["source"]}
        existing = (await db.execute(text("SELECT id FROM subscription_chatbot_jobs WHERE tenant_id=:t AND idempotency_key=:key"),
                                    {"t": tenant, "key": payload.idempotency_key})).scalar_one_or_none()
        if existing:
            job, _ = await store.request_job(db, tenant, actor, "restore", payload.idempotency_key,
                                           request, payload.expected_chatbot_revision, preview=preview_id)
            return jsonable_encoder({"job": store.job_dto(job)})
        if preview["applied_job_id"]:
            raise store.fail("CHATBOT_CONFLICT")
        await store.writable(db, tenant)
        if payload.expected_chatbot_revision != preview["revision"]:
            raise store.fail("CHATBOT_PREVIEW_STALE")
        if preview["mode"] == "exact" and payload.confirmation != "RESTORE":
            raise store.fail("CHATBOT_RESTORE_CONFIRMATION")
        document = await _reviewed_document(db, tenant, preview)
        await _validate_review(db, tenant, actor, preview, document)
        job, _ = await store.request_job(db, tenant, actor, "restore", payload.idempotency_key,
                                       request, payload.expected_chatbot_revision, preview=preview_id)
        row = (await db.execute(text("UPDATE subscription_chatbot_jobs SET counts=CAST(:counts AS jsonb) WHERE id=:id RETURNING *"),
                               {"id": job["id"], "counts": store.encode(preview["counts"])})).mappings().one()
        await db.execute(text("UPDATE subscription_chatbot_previews SET applied_job_id=:job WHERE tenant_id=:t AND id=:id"),
                         {"t": tenant, "id": preview_id, "job": job["id"]})
        return jsonable_encoder({"job": store.job_dto(row)})


async def _registered_asset(db, tenant, metadata):
    row = (await db.execute(text("SELECT * FROM subscription_chatbot_assets WHERE tenant_id=:t AND id=:id AND status='ready'"),
                            {"t": tenant, "id": UUID(metadata["id"])})).mappings().one_or_none()
    if not row or _asset_metadata(row) != metadata:
        raise store.fail("CHATBOT_CAPABILITY_UNAVAILABLE", 409, "Uma mídia do arquivo não está disponível ou não confere com o snapshot.")
    return dict(row)


async def _read_asset(tenant, metadata):
    data = await asyncio.to_thread(media.get_media, tenant, UUID(metadata["id"]),
        expected_size=metadata["size"], expected_sha256=metadata["sha256"])
    thumbnail = metadata["thumbnail"]
    thumb = (await asyncio.to_thread(media.get_media, tenant, UUID(metadata["id"]),
        expected_size=thumbnail["size"], expected_sha256=thumbnail["sha256"], variant="thumbnail")) if thumbnail else None
    return {**metadata, "data": data, "thumbnail_data": thumb}


async def _hydrate(tenant, document, protected):
    expanded = sum(item["size"] + (item["thumbnail"]["size"] if item["thumbnail"] else 0) for item in document["assets"])
    if expanded > codec.MAX_EXPANDED_BYTES:
        raise store.fail("CHATBOT_INVALID_GRAPH", 413, "O download ultrapassa o limite portátil. Exporte uma seleção menor de respostas.")
    result = {**deepcopy(document), "assets": []}
    for metadata in document["assets"]:
        async with protected() as db:
            await _registered_asset(db, tenant, metadata)
        data = await _read_asset(tenant, metadata)
        async with protected() as db:
            await _registered_asset(db, tenant, metadata)
        result["assets"].append(data)
    return result


async def _download(tenant, document, format, protected, filename):
    try:
        async with asyncio.timeout(ARCHIVE_TIMEOUT):
            hydrated = await _hydrate(tenant, document, protected)
            encoded = await asyncio.to_thread(codec.write_archive, hydrated, format)
            async with protected():
                pass
    except TimeoutError:
        raise store.fail("CHATBOT_CAPABILITY_UNAVAILABLE", 503, "Não foi possível concluir o download. Tente novamente.") from None
    except ValueError:
        raise store.fail("CHATBOT_INVALID_GRAPH", 413, "O download ultrapassa o limite portátil ou contém uma referência inválida. Exporte uma seleção menor.") from None
    return Response(encoded, media_type="application/zip" if format == "zip" else "application/json",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                 "Content-Disposition": f'attachment; filename="{filename}"'})


async def reserve_export(tenant, actor, payload):
    async with store.owner_transaction(tenant, actor, write=False) as db:
        await _cleanup_previews(db, tenant)
        outstanding = (await db.execute(text("""SELECT count(*) FROM subscription_chatbot_jobs
            WHERE tenant_id=:t AND kind='export' AND created_at>clock_timestamp()-interval '15 minutes'"""),
            {"t": tenant})).scalar_one()
        if outstanding >= MAX_EXPORTS:
            raise store.fail("CHATBOT_BUSY", 429, "Há cinco exportações disponíveis. Aguarde sua expiração antes de criar outra.")
        document = _snapshot(await store.load_graph(db, tenant), payload.ids)
        identity, now = uuid4(), _now()
        expires = now + TTL
        stored = {"snapshot": document, "format": payload.format, "expires_at": expires.isoformat()}
        await db.execute(text("""INSERT INTO subscription_chatbot_jobs
            (id,tenant_id,actor_account_id,kind,idempotency_key,request_fingerprint,expected_revision,status,payload,finished_at)
            VALUES(:id,:t,:a,'export',:key,:fingerprint,:revision,'succeeded',CAST(:payload AS jsonb),clock_timestamp())"""),
            {"id": identity, "t": tenant, "a": actor.account_id, "key": uuid4(),
             "fingerprint": store.fingerprint(payload.model_dump(mode="json")), "revision": await store.revision(db, tenant),
             "payload": store.encode(stored)})
        return jsonable_encoder({"export": {"id": identity, "filename": "orion-chatbot." + payload.format,
            "url": f"/api/v1/tenants/{tenant}/subscription-whatsapp/chatbot/exports/{identity}", "expires_at": expires}})


async def export_download(tenant, actor, export_id):
    async with store.owner_transaction(tenant, actor, write=False) as db:
        row = (await db.execute(text("""SELECT * FROM subscription_chatbot_jobs
            WHERE tenant_id=:t AND id=:id AND actor_account_id=:a AND kind='export' AND status='succeeded'"""),
            {"t": tenant, "id": export_id, "a": actor.account_id})).mappings().one_or_none()
        if not row or not row["payload"] or row["created_at"] + TTL <= _now():
            raise store.fail("CHATBOT_NOT_FOUND", 404)
        document, format = row["payload"]["snapshot"], row["payload"]["format"]
    @asynccontextmanager
    async def protected():
        async with store.owner_transaction(tenant, actor, write=False) as db:
            available = (await db.execute(text("""SELECT id FROM subscription_chatbot_jobs
                WHERE tenant_id=:t AND id=:id AND actor_account_id=:a AND kind='export' AND status='succeeded'
                  AND created_at>clock_timestamp()-interval '15 minutes' AND payload<>'{}'::jsonb"""),
                {"t": tenant, "id": export_id, "a": actor.account_id})).scalar_one_or_none()
            if not available:
                raise store.fail("CHATBOT_NOT_FOUND", 404)
            yield db
    return await _download(tenant, document, format, protected, "orion-chatbot." + format)


def _backup_dto(tenant, row):
    return {key: row[key] for key in ("id", "source", "created_at", "revision", "rules_count", "list_rows_count", "size", "status")} | {
        "download_url": f"/api/v1/tenants/{tenant}/subscription-whatsapp/chatbot/backups/{row['id']}/download"}


async def backups(tenant, actor, page, per_page):
    async with store.owner_transaction(tenant, actor, write=False) as db:
        total = (await db.execute(text("SELECT count(*) FROM subscription_chatbot_backups WHERE tenant_id=:t"), {"t": tenant})).scalar_one()
        rows = (await db.execute(text("""SELECT id,source,created_at,revision,rules_count,list_rows_count,size,status
            FROM subscription_chatbot_backups WHERE tenant_id=:t ORDER BY created_at DESC,id DESC LIMIT :limit OFFSET :offset"""),
            {"t": tenant, "limit": per_page, "offset": (page-1)*per_page})).mappings().all()
        return jsonable_encoder({"items": [_backup_dto(tenant, row) for row in rows], "page": page,
            "per_page": per_page, "total": total, "total_pages": max(1, (total+per_page-1)//per_page)})


async def backup_download(tenant, actor, backup_id):
    async with store.owner_transaction(tenant, actor, write=False) as db:
        document = (await _backup(db, tenant, backup_id))["snapshot"]
    @asynccontextmanager
    async def protected():
        async with store.owner_transaction(tenant, actor, write=False) as db:
            if not (await db.execute(text("SELECT id FROM subscription_chatbot_backups WHERE tenant_id=:t AND id=:id"),
                                     {"t": tenant, "id": backup_id})).scalar_one_or_none():
                raise store.fail("CHATBOT_NOT_FOUND", 404)
            yield db
    return await _download(tenant, document, "zip", protected, "orion-chatbot-backup.zip")


async def _insert_backup(db, tenant, document, revision, *, source, day=None):
    rows = sum(len(section["rows"]) for item in document["rules"] if item["definition"]["response"]["kind"] == "list"
               for section in item["definition"]["response"]["sections"])
    return (await db.execute(text("""INSERT INTO subscription_chatbot_backups
        (tenant_id,source,revision,backup_day,snapshot,rules_count,list_rows_count,size)
        VALUES(:t,:source,:revision,:day,CAST(:snapshot AS jsonb),:rules,:rows,:size) RETURNING id"""),
        {"t": tenant, "source": source, "revision": revision, "day": day, "snapshot": store.encode(document),
         "rules": len(document["rules"]), "rows": rows, "size": _document_size(document)})).scalar_one()


async def scheduled_backup(tenant):
    # Resolve a current owner, then take the same actual membership/workspace
    # locks as an interactive backup. No stale worker role is an authorization.
    async with store.support_session_factory()() as db:
        account = (await db.execute(text("""SELECT p.id,p.role FROM tenant_memberships m
            JOIN panel_accounts p ON p.id=m.account_id WHERE m.tenant_id=:t AND m.active AND m.role='owner'
            ORDER BY m.account_id LIMIT 1"""), {"t": tenant})).mappings().one_or_none()
    if not account or account["role"] not in {"member", "admin"}:
        return False
    actor = Actor(account["role"], account_id=account["id"])
    async with store.owner_transaction(tenant, actor, write=False) as db:
        day = (await db.execute(text("SELECT (clock_timestamp() AT TIME ZONE timezone)::date FROM tenants WHERE id=:t"),
                                {"t": tenant})).scalar_one()
        if (await db.execute(text("SELECT id FROM subscription_chatbot_backups WHERE tenant_id=:t AND source='scheduled' AND backup_day=:day"),
                             {"t": tenant, "day": day})).scalar_one_or_none():
            return False
        document = _snapshot(await store.load_graph(db, tenant))
        await _insert_backup(db, tenant, document, await store.revision(db, tenant), source="scheduled", day=day)
        await _cleanup_previews(db, tenant)
        return True


def _fresh_claim(job):
    return job["status"] == "running" and job["started_at"] is not None and job["started_at"] > _now() - CLAIM_TIMEOUT


async def _fail_job(tenant, job_id, error, *, nonce=None, claimable=False):
    async with store.support_session_factory()() as db:
        async with db.begin():
            await db.execute(text("SELECT id FROM tenants WHERE id=:t FOR UPDATE"), {"t": tenant})
            job = (await db.execute(text("SELECT * FROM subscription_chatbot_jobs WHERE tenant_id=:t AND id=:id AND kind='restore' FOR UPDATE"),
                                    {"t": tenant, "id": job_id})).mappings().one_or_none()
            if not job or job["status"] not in {"queued", "running"}:
                return
            if claimable:
                if _fresh_claim(job):
                    return
            elif (job["payload"] or {}).get("worker_nonce") != nonce:
                return
            await db.execute(text("""UPDATE subscription_chatbot_jobs SET status='failed',error_code=:code,
                finished_at=clock_timestamp() WHERE tenant_id=:t AND id=:id"""),
                {"t": tenant, "id": job_id, "code": _code(error)})
            await db.execute(text("""UPDATE subscription_chatbot_previews SET snapshot='{}'::jsonb
                WHERE tenant_id=:t AND id=:id AND applied_job_id=:job"""),
                {"t": tenant, "id": job["preview_id"], "job": job_id})


async def _claim_restore(tenant, job_id):
    async with store.support_session_factory()() as db:
        job = (await db.execute(text("SELECT * FROM subscription_chatbot_jobs WHERE tenant_id=:t AND id=:id AND kind='restore'"),
                                {"t": tenant, "id": job_id})).mappings().one_or_none()
    if not job:
        return "failed", None
    if job["status"] in {"succeeded", "failed"}:
        return job["status"], None
    if _fresh_claim(job):
        return "busy", None
    async with store.support_session_factory()() as db:
        account_role = (await db.execute(text("SELECT role FROM panel_accounts WHERE id=:a"),
                                        {"a": job["actor_account_id"]})).scalar_one_or_none()
    if account_role not in {"member", "admin"}:
        await _fail_job(tenant, job_id, store.fail("CHATBOT_EFFECT_REVOKED"), claimable=True)
        return "failed", None
    actor = Actor(account_role, account_id=job["actor_account_id"])
    try:
        async with store.owner_transaction(tenant, actor) as db:
            job = dict((await db.execute(text("SELECT * FROM subscription_chatbot_jobs WHERE tenant_id=:t AND id=:id FOR UPDATE"),
                                        {"t": tenant, "id": job_id})).mappings().one())
            if job["status"] in {"succeeded", "failed"}:
                return job["status"], None
            if _fresh_claim(job):
                return "busy", None
            preview = await _preview(db, tenant, actor.account_id, job["preview_id"])
            if preview["applied_job_id"] != job_id:
                raise store.fail("CHATBOT_CONFLICT")
            document = await _reviewed_document(db, tenant, preview, check_expiry=False)
            await _validate_review(db, tenant, actor, preview, document, check_expiry=False)
            nonce = str(uuid4())
            payload = dict(job["payload"]) | {"worker_nonce": nonce}
            await db.execute(text("""UPDATE subscription_chatbot_jobs SET status='running',started_at=clock_timestamp(),
                payload=CAST(:payload AS jsonb) WHERE tenant_id=:t AND id=:id"""),
                {"t": tenant, "id": job_id, "payload": store.encode(payload)})
            return "claimed", (actor, preview, document, nonce)
    except Exception as error:
        if not isinstance(error, HTTPException):
            log.warning("subscription_chatbot_restore_claim_failed type=%s", type(error).__name__)
        await _fail_job(tenant, job_id, error, claimable=True)
        return "failed", None


@asynccontextmanager
async def _restore_transaction(tenant, actor, job_id, preview, document, nonce, *, final=False):
    async with store.owner_transaction(tenant, actor) as db:
        job = (await db.execute(text("SELECT * FROM subscription_chatbot_jobs WHERE tenant_id=:t AND id=:id FOR UPDATE"),
                                {"t": tenant, "id": job_id})).mappings().one_or_none()
        if (not job or job["status"] != "running" or job["actor_account_id"] != actor.account_id
                or job["preview_id"] != preview["id"] or (job["payload"] or {}).get("worker_nonce") != nonce):
            raise store.fail("CHATBOT_EFFECT_REVOKED")
        if await store.revision(db, tenant) != preview["revision"]:
            raise store.fail("CHATBOT_PREVIEW_STALE")
        if await _check_devices(db, tenant, actor, document, preview["snapshot"]["plan"]):
            raise store.fail("CHATBOT_EFFECT_REVOKED")
        if final:
            await _validate_review(db, tenant, actor, preview, document, check_expiry=False)
        await db.execute(text("UPDATE subscription_chatbot_jobs SET started_at=clock_timestamp() WHERE tenant_id=:t AND id=:id"),
                         {"t": tenant, "id": job_id})
        yield db


async def _prepare_restore_assets(tenant, preview, document, protected):
    plan = preview["snapshot"]["plan"]
    normalized = {item["id"]: item for item in document["assets"]}
    originals = None
    if preview["source"] == "import":
        raw = _decode_raw(preview["snapshot"]["content_base64"])
        try:
            incoming = await asyncio.to_thread(codec.read_archive, raw, preview["snapshot"]["filename"])
        except ValueError:
            raise _invalid() from None
        originals = {item["id"]: item for item in incoming["assets"]}
    result = {}
    for identity in plan["asset_ids"]:
        async with protected() as db:
            if preview["source"] == "backup":
                row = await _registered_asset(db, tenant, normalized[identity])
        if originals is not None:
            source = originals[identity]
            prepared = await asyncio.to_thread(media.prepare_media, source["data"], source["kind"])
            if _metadata(identity, source["kind"], prepared) != normalized[identity]:
                raise store.fail("CHATBOT_PREVIEW_STALE")
            row = await store.persist_prepared(tenant, source["kind"], prepared, protected)
        else:
            # Internal snapshots already reference canonical normalized bytes.
            # Verify completeness, never trust a filename or fetch a rule URL.
            await _read_asset(tenant, normalized[identity])
            async with protected() as db:
                row = await _registered_asset(db, tenant, normalized[identity])
        result[identity] = row
    return result


async def _apply_graph(db, tenant, actor, preview, document, asset_rows):
    from app.subscription_chatbot import _write_rule
    graph = await store.load_graph(db, tenant)
    plan = preview["snapshot"]["plan"]
    writes, template_writes, _, _ = _materialize(document, plan, graph)
    safety = None
    if preview["mode"] == "exact":
        safety = await _insert_backup(db, tenant, _snapshot(graph), preview["revision"], source="safety")
    for value in template_writes:
        asset = asset_rows[value["asset_id"]]["id"] if value["asset_id"] else None
        await db.execute(text("""INSERT INTO subscription_chatbot_templates(id,tenant_id,origin_id,name,kind,body,asset_id)
            VALUES(:id,:t,:origin,:name,:kind,:body,:asset)"""),
            {"id": UUID(value["id"]), "t": tenant, "origin": UUID(value["origin_id"]),
             "name": value["name"], "kind": value["kind"], "body": value["body"], "asset": asset})
    for identity in plan["delete_ids"]:
        await db.execute(text("""UPDATE subscription_chatbot_rules SET removed_at=clock_timestamp(),enabled=false,
            revision=revision+1,updated_at=clock_timestamp() WHERE tenant_id=:t AND id=:id AND removed_at IS NULL"""),
            {"t": tenant, "id": UUID(identity)})
    for item, value in sorted(writes, key=lambda pair: pair[1]["creation_order"]):
        definition = {field: deepcopy(value[field]) for field in _FIELDS}
        if definition["device_id"]:
            definition["device_id"] = UUID(definition["device_id"])
        await _write_rule(db, tenant, actor, UUID(item["id"]), definition,
            existing=graph[0].get(item["id"]) if item["operation"] == "update" else None,
            origin=UUID(item["source_id"]))
        if preview["mode"] == "exact":
            order = await store.next_order(db, tenant)
            await db.execute(text("UPDATE subscription_chatbot_rules SET creation_order=:ordering WHERE tenant_id=:t AND id=:id"),
                             {"t": tenant, "id": UUID(item["id"]), "ordering": order})
    return safety, await store.bump_revision(db, tenant)


async def process_restore(tenant, job_id):
    outcome, claim = await _claim_restore(tenant, job_id)
    if claim is None:
        return outcome
    actor, preview, document, nonce = claim
    protected = lambda: _restore_transaction(tenant, actor, job_id, preview, document, nonce)
    try:
        async with asyncio.timeout(RESTORE_TIMEOUT):
            asset_rows = await _prepare_restore_assets(tenant, preview, document, protected)
            async with _restore_transaction(tenant, actor, job_id, preview, document, nonce, final=True) as db:
                safety, revision = await _apply_graph(db, tenant, actor, preview, document, asset_rows)
                await db.execute(text("UPDATE subscription_chatbot_jobs SET safety_backup_id=:backup WHERE tenant_id=:t AND id=:id"),
                                 {"t": tenant, "id": job_id, "backup": safety})
                await store.complete_job(db, job_id, {"chatbot_revision": revision}, counts=preview["counts"])
                await db.execute(text("UPDATE subscription_chatbot_previews SET snapshot='{}'::jsonb WHERE tenant_id=:t AND id=:id AND applied_job_id=:job"),
                                 {"t": tenant, "id": preview["id"], "job": job_id})
        return "succeeded"
    except asyncio.CancelledError:
        # The SQL heartbeat/nonce remains resumable. persist_prepared retains
        # its own media mutex until its writer and compensation really stop.
        raise
    except Exception as error:
        if not isinstance(error, HTTPException):
            log.warning("subscription_chatbot_restore_failed type=%s", type(error).__name__)
        await _fail_job(tenant, job_id, error, nonce=nonce)
        return "failed"
