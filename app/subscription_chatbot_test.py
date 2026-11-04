"""Owner-scoped local conversation simulation and normalized private previews.

This router performs reads only. It never calls the live event engine, bridge,
HTTP actions, ASR, cooldown storage or durable jobs.
"""
import asyncio
import os
import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text

from app.catalog_api import Actor, authenticate
from app.subscription_chatbot import bounded_json
from app import subscription_chatbot_rules as rules
from app import subscription_chatbot_store as store
from app import subscription_chatbot_test_engine as engine
from app.subscription_chatbot_test_schemas import BODY_BYTES, MESSAGE_UTF16, SimulateRequest


BASE = "/api/v1/tenants/{tenant_id}/subscription-whatsapp/chatbot-test"
router = APIRouter(tags=["Simulação do Chatbot de assinaturas"])


def invalid_context():
    return HTTPException(409, {"code": "CHATBOT_TEST_CONTEXT_INVALID",
        "message": "Este teste mudou ou expirou. Envie uma nova mensagem para continuar.", "refresh": True})


def context_unavailable():
    return HTTPException(503, {"code": "CHATBOT_TEST_UNAVAILABLE",
        "message": "Não foi possível preparar o contexto de teste. Tente novamente."})


def device_dto(row):
    runtime = row.get("runtime") or {}
    phone = runtime.get("phone")
    verified = runtime.get("phone_verified") is True and isinstance(phone, str) and re.fullmatch(r"[1-9][0-9]{7,14}", phone)
    return {"id": row["id"], "name": row["name"], "status": row["status"],
            "phone_e164": "+" + phone if verified else None}


def private_media_available():
    return all(os.getenv(key) for key in ("ORION_MEDIA_S3_ENDPOINT", "ORION_MEDIA_S3_BUCKET",
        "ORION_MEDIA_S3_REGION", "ORION_MEDIA_S3_ACCESS_KEY_ID", "ORION_MEDIA_S3_SECRET_ACCESS_KEY"))


@router.get(BASE)
async def bootstrap(tenant_id: UUID, response: Response, actor: Actor = Depends(authenticate)):
    response.headers["Cache-Control"] = "no-store"
    async with store.owner_transaction(tenant_id, actor) as db:
        rows = (await db.execute(text("""SELECT id,name,status,runtime FROM subscription_devices
            WHERE tenant_id=:t AND owner_account_id=:a AND removed_at IS NULL
              AND status NOT IN ('removing','removed') ORDER BY created_at,id"""),
            {"t": tenant_id, "a": actor.account_id})).mappings().all()
        return jsonable_encoder({"chatbot_revision": await store.revision(db, tenant_id),
            "devices": [device_dto(dict(row)) for row in rows],
            "capabilities": {"simulate": True, "include_inactive": True, "private_media": private_media_available()},
            "limits": {"message_utf16": MESSAGE_UTF16, "max_outputs": rules.MAX_ACTIONS,
                       "context_ttl_seconds": engine.CONTEXT_TTL}})


async def _facts(db, tenant, actor):
    row = (await db.execute(text("""SELECT t.name,a.email,b.configuration->>'contact_phone' AS contact_phone
        FROM tenants t JOIN panel_accounts a ON a.id=:a
        LEFT JOIN business_onboarding b ON b.tenant_id=t.id WHERE t.id=:t"""),
        {"t": tenant, "a": actor.account_id})).mappings().one()
    return {"name": "Contato de teste", "phone_number": "Telefone de teste",
            "my_name": row["name"], "my_email": row["email"] or "E-mail não informado",
            "my_contact_number": row["contact_phone"] or "Contato comercial não informado"}


def _select(payload, graph, scope, facts):
    value = payload.input
    selected_action = None
    if value.type == "text":
        copied, _ = engine.simulation_graph(graph)
        candidates = rules.candidates(copied.values(), value.text, payload.device_id)
        selected = graph[0].get(str(candidates[0]["id"])) if candidates else None
        reason = "wildcard" if selected and selected["keyword"] in rules.WILDCARDS else "keyword"
        incoming = {"type": "text", "text": value.text, "rule_id": None, "row_id": None}
    elif value.type == "rule":
        selected = graph[0].get(str(value.rule_id))
        if not engine.eligible(selected, payload.device_id):
            raise store.fail("CHATBOT_NOT_FOUND", 404)
        reason = "forced_rule"
        incoming = {"type": "rule", "text": selected["keyword"], "rule_id": str(selected["id"]), "row_id": None}
    else:
        try:
            ticket = engine.read_ticket(value.selection_token, "menu", expected=scope)
            selected = graph[0].get(ticket.get("rule_id"))
            if (not engine.eligible(selected, payload.device_id) or selected["revision"] != ticket.get("rule_revision")
                    or selected["response"]["kind"] != "list"):
                raise engine.InvalidContext()
            row = next((row for section in selected["response"]["sections"] for row in section["rows"]
                        if str(row["id"]) == str(value.row_id)), None)
            if row is None:
                raise engine.InvalidContext()
            selected_action = row["action"]
            label = rules.render_contact(row["title"], facts, maximum=24)
        except (engine.InvalidContext, ValueError):
            raise invalid_context() from None
        reason = "selection"
        incoming = {"type": "selection", "text": label, "rule_id": str(selected["id"]), "row_id": str(value.row_id)}
    return selected, selected_action, reason, incoming


@router.post(BASE + "/simulate")
async def simulate(tenant_id: UUID, request: Request, response: Response, actor: Actor = Depends(authenticate)):
    response.headers["Cache-Control"] = "no-store"
    # Check the current owner before reading even the bounded body. Do not
    # keep database locks while a client streams the request.
    async with store.owner_transaction(tenant_id, actor):
        pass
    payload = await bounded_json(request, SimulateRequest, limit=BODY_BYTES)
    try:
        async with store.owner_transaction(tenant_id, actor) as db:
            revision = await store.check_revision(db, tenant_id, payload.expected_chatbot_revision)
            device, _ = await store.check_device(db, tenant_id, payload.device_id, actor_id=actor.account_id, live=False)
            graph = await store.load_graph(db, tenant_id)
            facts = await _facts(db, tenant_id, actor)
            scope = engine.context_scope(tenant_id, actor.account_id, device, revision, payload.context_id)
            selected, selected_action, reason, incoming = _select(payload, graph, scope, facts)
            matched = None
            if selected is None:
                outcome, messages = "no_match", [engine.text_message(engine.NO_MATCH)]
            else:
                matched = {key: selected[key] for key in ("id", "keyword", "match_mode", "enabled")} | {"reason": reason}
                try:
                    messages = engine.compile_messages(selected, graph, scope, facts, {}, BASE.format(tenant_id=tenant_id),
                                                       selected_action=selected_action)
                    outcome = "matched"
                except ValueError:
                    outcome, messages = "unavailable", [engine.text_message(engine.UNAVAILABLE, unavailable=True)]
            return jsonable_encoder({"request_id": payload.request_id, "context_id": payload.context_id,
                "chatbot_revision": revision, "device": device_dto(device), "input": incoming,
                "outcome": outcome, "matched_rule": matched, "messages": messages,
                "keywords": engine.keyword_catalog(graph, payload.device_id)})
    except engine.ContextUnavailable:
        raise context_unavailable() from None


async def _asset_authority(db, tenant, actor, asset_id, ticket):
    try:
        signed = engine.read_ticket(ticket, "media", expected={"tenant_id": str(tenant), "actor_id": str(actor.account_id)})
        device, _ = await store.check_device(db, tenant, UUID(signed["device_id"]), actor_id=actor.account_id, live=False)
        revision = await store.check_revision(db, tenant, signed["chatbot_revision"])
        scope = engine.context_scope(tenant, actor.account_id, device, revision, UUID(signed["context_id"]))
        engine.read_ticket(ticket, "media", expected=scope)
        if signed["asset"]["id"] != str(asset_id):
            raise engine.InvalidContext()
        root = (await db.execute(text("""SELECT id,revision,device_id,removed_at FROM subscription_chatbot_rules
            WHERE tenant_id=:t AND id=:id AND removed_at IS NULL"""),
            {"t": tenant, "id": UUID(signed["rule_id"])})).mappings().one_or_none()
        if not engine.eligible(root, device["id"]) or root["revision"] != signed["rule_revision"]:
            raise engine.InvalidContext()
        row = (await db.execute(text("""SELECT * FROM subscription_chatbot_assets
            WHERE tenant_id=:t AND id=:id AND status='ready'"""), {"t": tenant, "id": asset_id})).mappings().one_or_none()
        if row is None:
            raise engine.InvalidContext()
        descriptor = store.asset_descriptor(dict(row))
        if engine.asset_reference(descriptor) != signed["asset"]:
            raise engine.InvalidContext()
        return descriptor
    except (engine.InvalidContext, KeyError, ValueError, TypeError):
        raise invalid_context() from None


@router.get(BASE + "/assets/{asset_id}/content")
async def asset_content(tenant_id: UUID, asset_id: UUID, ticket: str, variant: str = "original",
                        actor: Actor = Depends(authenticate)):
    if variant not in {"original", "thumbnail"}:
        raise store.fail("CHATBOT_NOT_FOUND", 404)
    try:
        async with store.owner_transaction(tenant_id, actor) as db:
            asset = await _asset_authority(db, tenant_id, actor, asset_id, ticket)
            expected = asset if variant == "original" else asset["thumbnail"]
            if not expected:
                raise store.fail("CHATBOT_NOT_FOUND", 404)
        from app.subscription_chatbot_media import get_media
        try:
            async with asyncio.timeout(20):
                data = await asyncio.to_thread(get_media, tenant_id, asset_id, expected_size=expected["size"],
                                              expected_sha256=expected["sha256"], variant=variant)
        except TimeoutError:
            raise context_unavailable() from None
        async with store.owner_transaction(tenant_id, actor) as db:
            await _asset_authority(db, tenant_id, actor, asset_id, ticket)
        disposition = "attachment" if asset["kind"] == "document" else "inline"
        filename = asset["file_name"] if variant == "original" else "previa.jpg"
        return Response(data, media_type=expected["mime"], headers={"Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Content-Disposition": f'{disposition}; filename="{filename}"'})
    except engine.ContextUnavailable:
        raise context_unavailable() from None
