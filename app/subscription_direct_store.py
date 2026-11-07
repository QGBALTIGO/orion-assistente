"""Direct-origin snapshots and projections over the shared outbound ledger."""
from contextlib import asynccontextmanager
from datetime import timedelta
from uuid import UUID, uuid5

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder

from app import subscription_bulk_store as bulk
from app import subscription_direct_policy as policy
from app import subscription_direct_schemas as schemas
from app import subscription_templates as library
from app import subscription_templates_schemas as template_schemas
from app.whatsapp_footer import FooterTooLong

FEATURE = "direct_messages.manage"
ORIGIN = "direct"
shared = bulk.shared
encode, fingerprint, clock, timezone_for = bulk.encode, bulk.fingerprint, bulk.clock, bulk.timezone_for
ERRORS = {"DIRECT_" + key.removeprefix("BULK_"): value.replace("lote", "envio").replace("linhas", "destinatários")
          for key, value in bulk.ERRORS.items() if key.startswith("BULK_")}
ERRORS.update({
    "DIRECT_INVALID": "Confira o dispositivo, os destinatários e o conteúdo da mensagem.",
    "DIRECT_NOT_FOUND": "Envio, contato ou pedido não encontrado nesta empresa.",
    "DIRECT_LIMIT": "A mensagem ou a seleção ultrapassa os limites permitidos.",
    "DIRECT_TEMPLATE_INVALID": "Para salvar como template, use somente as cinco tags disponíveis na biblioteca.",
    "DIRECT_ASSET_INVALID": "Selecione um arquivo validado desta empresa, com o nome devolvido pelo upload.",
    "DIRECT_FORMAT_UNAVAILABLE": "Este dispositivo não está pronto para mensagens com botão. Confira a conexão e tente novamente.",
    "DIRECT_FOOTER_TOO_LONG": "A mensagem com rodapé ultrapassa o limite do botão. Reduza o texto ou o rodapé antes de confirmar.",
})


def fail(code, status=409, **extra):
    return HTTPException(status, {"code": code, "message": ERRORS.get(code, "A operação não pôde ser concluída."), **extra},
                         headers={"Cache-Control": "no-store"})


@asynccontextmanager
async def owner_transaction(tenant, actor):
    try:
        async with shared.owner_transaction(tenant, actor, feature=FEATURE) as db:
            yield db
    except HTTPException as exc:
        if isinstance(exc.detail, dict) and exc.detail.get("code", "").startswith("BULK_"):
            code = "DIRECT_" + exc.detail["code"].removeprefix("BULK_")
            extra = {key: value for key, value in exc.detail.items() if key not in {"code", "message"}}
            raise fail(code, exc.status_code, **extra) from None
        raise


async def request_row(db, tenant, actor, key):
    return await bulk.request_row(db, tenant, actor, key, origin=ORIGIN)


async def existing_request(db, tenant, actor, key, kind, values, *, send_id=None):
    return await bulk.existing_request(db, tenant, actor, key, kind, values, bulk_id=send_id, origin=ORIGIN)


async def capture_review(db, tenant, actor, message, key):
    definition = policy.expanded_definition(message, key)
    prepared, warnings = None, []
    if message["kind"] == "text":
        prepared = {item["row_key"]: {"plan": [{"type": "text", "text": message["text"]}]}
                    for item in definition["items"]}
    elif message["kind"] == "url_button":
        try:
            plan = policy.url_button_plan(message)
        except FooterTooLong:
            raise fail("DIRECT_FOOTER_TOO_LONG", 422) from None
        except ValueError:
            raise fail("DIRECT_INVALID", 422) from None
        prepared = {item["row_key"]: {"plan": plan} for item in definition["items"]}
    elif message["kind"] == "media":
        asset = await library.ready_asset(db, tenant, UUID(message["asset_id"]))
        limits = library.MEDIA_TYPES.get(asset["kind"])
        if (asset["kind"] not in policy.MEDIA_KINDS or not limits or asset["mime"] not in limits["mimes"]
                or not 0 < asset["size"] <= limits["max_bytes"]):
            raise fail("DIRECT_ASSET_INVALID", 422)
        descriptor = jsonable_encoder(shared.asset_descriptor(asset))
        try:
            plan, warnings = policy.media_plan(message, descriptor)
        except ValueError:
            raise fail("DIRECT_ASSET_INVALID", 422) from None
        prepared = {definition["items"][0]["row_key"]: {"plan": plan, "media": descriptor}}
    snapshot = await bulk.capture_review(db, tenant, actor, definition, prepared_messages=prepared)
    snapshot.update(message=message, idempotency_key=str(key), warnings=warnings, save_template=None,
                    template_library_revision=None)
    if message.get("save_template") is not None:
        try:
            candidate = policy.optional_template(message, snapshot["rows"][0]["plan"])
        except ValueError:
            raise fail("DIRECT_TEMPLATE_INVALID", 422) from None
        await shared.workspace(db, tenant, feature=library.FEATURE)
        await shared.writable(db, tenant)
        graph, templates, assets = await shared.load_graph(db, tenant)
        await library.validate_candidate(tenant, template_schemas.LibraryTemplate.model_validate(candidate).model_dump(),
            uuid5(UUID(str(key)), "saved-template-review"), graph, templates, assets)
        snapshot["template_library_revision"] = await shared.revision(db, tenant)
        snapshot["save_template"] = candidate
    bulk.bounded_json(snapshot, bulk.schemas.MAX_EXPANDED_BYTES)
    return jsonable_encoder(snapshot)


def review_preview(snapshot, identity, now):
    candidate = snapshot["save_template"]
    return {"review_version": fingerprint(snapshot), "expires_at": now + timedelta(seconds=schemas.REVIEW_SECONDS),
        "kind": snapshot["message"]["kind"], "timezone": snapshot["timezone"],
        **{key: snapshot["counts"][key] for key in ("total", "eligible", "blocked")},
        "items": [bulk.row_preview(row) for row in snapshot["rows"]],
        "save_template": {key: candidate[key] for key in ("name", "kind")} if candidate else None,
        "warnings": snapshot["warnings"]}


def operation_dto(row, now, *, replayed=False):
    value = bulk.operation_dto(row, now, replayed=replayed)
    value["send_id"] = value.pop("batch_id")
    return value


async def owned_send(db, tenant, actor, identity, *, lock=False):
    row = await bulk.batch_row(db, tenant, identity, lock=lock)
    if row["owner_account_id"] != actor.account_id or row.get("origin") != ORIGIN:
        raise fail("DIRECT_NOT_FOUND", 404)
    return row


async def send_item(db, tenant, identity, actor, now):
    value = await bulk.batch_item(db, tenant, identity, actor, now, origin=ORIGIN)
    value["send_id"] = value.pop("batch_id")
    value.pop("interval_seconds")
    return value


async def recipients_page(db, send, now):
    value = await bulk.recipient_page(db, send, now)
    for item in value["items"]:
        item["send_id"] = item.pop("batch_id")
    return value
