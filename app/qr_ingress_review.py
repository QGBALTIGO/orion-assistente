"""Platform-owner, no-replay review of the preserved PRIMARY admission queue.

The destination, channel and operator are server-derived. This route neither
reads Signal material nor sends/replays messages or performs booking actions.
"""
import hashlib
import hmac
import json
import time
from typing import Annotated, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator
from sqlalchemy import text

from app.admin_accounts import require_platform_owner
from app import qr_api, qr_channels, qr_legacy

router = APIRouter(tags=["Diagnóstico restrito do WhatsApp"])
PRIVATE_PATH = "/internal/ops/assistant-ingress-review"
PUBLIC_PATH = "/api/v1/admin/whatsapp/ingress-review"
BODY_LIMIT = 16 * 1024
RESPONSE_LIMIT = 32 * 1024
HEX64 = r"^[a-f0-9]{64}$"
TRACE = r"^[a-f0-9]{20}$"
MAX_INTEGER = 2**53 - 1


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Scope(Closed):
    tenant_id: UUID
    channel_id: UUID
    session_digest: str = Field(strict=True, pattern=HEX64)
    auth_epoch: str = Field(strict=True, pattern=HEX64)
    owner: UUID
    fence: int = Field(strict=True, ge=1, le=MAX_INTEGER)


class Expected(Closed):
    scope: Scope
    snapshot_sha256: str = Field(strict=True, pattern=HEX64)


class Evidence(Closed):
    kind: Literal["operator_attested_prepare_failed", "not_started"]
    receipt_sha256: str = Field(strict=True, pattern=HEX64)
    build_sha: str = Field(strict=True, pattern=r"^[a-f0-9]{40}$")
    event_at: int = Field(strict=True, ge=1, le=MAX_INTEGER)


class Entry(Closed):
    trace: str = Field(strict=True, pattern=TRACE)
    fingerprint: str = Field(strict=True, pattern=HEX64)
    disposition: Literal["do_not_replay"]
    evidence: Evidence


class DryRequest(Closed):
    version: int = Field(strict=True, ge=1, le=1)
    mode: Literal["dry_run"]
    operation_id: UUID
    traces: list[Annotated[str, Field(strict=True, pattern=TRACE)]] = Field(max_length=2)

    @model_validator(mode="after")
    def unique(self):
        if len(set(self.traces)) != len(self.traces):
            raise ValueError("Entrada repetida")
        return self


class ApplyRequest(Closed):
    version: int = Field(strict=True, ge=1, le=1)
    mode: Literal["apply"]
    operation_id: UUID
    expected: Expected
    entries: list[Entry] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def unique(self):
        if len({row.trace for row in self.entries}) != len(self.entries):
            raise ValueError("Entrada repetida")
        return self


RequestPayload = TypeAdapter(Annotated[DryRequest | ApplyRequest, Field(discriminator="mode")])
Reason = Literal["expired", "scope_changed", "policy_denied", "invalid_message",
                 "authorization_revoked", "broker_refused", "identity_unresolved",
                 "business_started", "preparation_failed", "operator_do_not_replay"]
Phase = Literal["preparing", "consuming", "preparation_failed", "operator_retired"]


class EntryView(Closed):
    trace: str = Field(strict=True, pattern=TRACE)
    fingerprint: str = Field(strict=True, pattern=HEX64)
    at: int = Field(strict=True, ge=1, le=MAX_INTEGER)
    sequence: int = Field(strict=True, ge=1, le=MAX_INTEGER)
    state: Literal["pending", "blocked"]
    blocked_reason: Reason | None
    phase: Phase | None
    eligible_as: list[Literal["operator_attested_prepare_failed", "not_started"]] = Field(max_length=2)


class DryResult(Closed):
    version: int = Field(strict=True, ge=1, le=1)
    mode: Literal["dry_run"]
    scope: Scope
    snapshot_sha256: str = Field(strict=True, pattern=HEX64)
    entries: list[EntryView] = Field(max_length=32)
    total_entries: int = Field(strict=True, ge=0, le=512)
    truncated: bool = Field(strict=True)
    mutation: bool = Field(strict=True)

    @model_validator(mode="after")
    def read_only(self):
        if self.mutation or len({row.trace for row in self.entries}) != len(self.entries):
            raise ValueError("invalid_dry_run")
        return self


class Backup(Closed):
    reference: UUID
    sha256: str = Field(strict=True, pattern=HEX64)
    bytes: int = Field(strict=True, ge=1, le=16 * 1024 * 1024)


class ApplyResult(Closed):
    version: int = Field(strict=True, ge=1, le=1)
    mode: Literal["apply"]
    operation_id: UUID
    replayed: bool = Field(strict=True)
    mutation: bool = Field(strict=True)
    before_snapshot_sha256: str = Field(strict=True, pattern=HEX64)
    after_snapshot_sha256: str = Field(strict=True, pattern=HEX64)
    after_entries_sha256: str = Field(strict=True, pattern=HEX64)
    backup: Backup
    review_required: bool = Field(strict=True)

    @model_validator(mode="after")
    def retained_review(self):
        if not self.review_required or self.replayed == self.mutation:
            raise ValueError("invalid_apply_result")
        return self


ERRORS = {
    "invalid_ingress_review_request": (400, "Pedido de revisão inválido."),
    "unauthorized": (503, "A revisão privada não pôde ser autenticada."),
    "invalid_operator_assertion": (503, "A revisão privada não pôde ser autenticada."),
    "ingress_review_not_ready": (409, "Aguarde a conexão ficar pronta para revisão."),
    "ingress_review_scope_conflict": (409, "A conexão mudou. Consulte novamente antes de continuar."),
    "ingress_review_snapshot_conflict": (409, "A fila mudou. Consulte novamente antes de continuar."),
    "ingress_review_operation_conflict": (409, "Esta operação já foi usada com outros dados."),
    "ingress_review_entry_refused": (409, "Esta entrada não permite liberação sem reprocessamento."),
    "ingress_review_identity_unresolved": (409, "A identidade desta entrada precisa de revisão."),
    "ingress_review_backup_failed": (503, "Não foi possível preservar a cópia da fila. Nenhuma liberação foi feita."),
    "ingress_review_capacity": (503, "Não há espaço seguro para registrar esta revisão."),
    "ingress_review_storage_unavailable": (503, "O armazenamento da revisão está indisponível."),
}


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _assertion(body: dict, actor: UUID, secret: str, *, at: int | None = None) -> dict[str, str]:
    timestamp = str(at if at is not None else time.time_ns() // 1_000_000)
    digest = hashlib.sha256(_canonical(body)).hexdigest()
    content = "\n".join(("orion:assistant-ingress-operator:v1", str(actor), timestamp, digest))
    return {"X-Orion-Ops-Actor": str(actor), "X-Orion-Ops-At": timestamp,
            "X-Orion-Ops-Signature": hmac.new(secret.encode(), content.encode(), hashlib.sha256).hexdigest()}


async def _primary_binding() -> dict:
    """Read only the configured, adopted PRIMARY with its live owner and lease."""
    tenant = qr_legacy.configured_tenant()
    channel = qr_legacy.channel_id(tenant)
    qr_channels.enabled()
    async with qr_channels.support_session_factory()() as db:
        row = (await db.execute(text("""SELECT c.*, clock_timestamp() AS server_now
            FROM qr_channels c
            JOIN tenants t ON t.id=c.tenant_id
            JOIN tenant_memberships m ON m.tenant_id=c.tenant_id AND m.account_id=c.owner_account_id
            WHERE c.tenant_id=:tenant AND c.id=:channel AND c.enabled
              AND t.status='active' AND m.active AND m.role='owner'
              AND t.whatsapp_channel_id=c.id"""), {"tenant": tenant, "channel": channel})).mappings().one_or_none()
    if row is None or not isinstance(row["lease_owner"], UUID) or not 1 <= row["fence"] <= MAX_INTEGER:
        raise HTTPException(409, "A conexão principal não está pronta para revisão.")
    qr_channels._current(row, row["lease_owner"], row["fence"])
    if not await qr_channels._legacy_binding_valid(row):
        raise HTTPException(409, "O vínculo da conexão principal precisa de revisão.")
    return {"tenant_id": str(tenant), "channel_id": str(channel),
            "session_digest": row["request_fingerprint"],
            "owner": str(row["lease_owner"]), "fence": row["fence"]}


def _check_scope(scope: Scope, binding: dict) -> None:
    actual = scope.model_dump(mode="json")
    if any(actual.get(key) != value for key, value in binding.items()):
        raise HTTPException(409, "A conexão mudou. Consulte novamente antes de continuar.")


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _non_json(_):
    raise ValueError("invalid_json_number")


async def _read_request(request: Request):
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "Envie a revisão no formato correto.")
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > BODY_LIMIT:
            raise HTTPException(413, "Pedido de revisão muito grande.")
        content.extend(chunk)
    try:
        parsed = json.loads(bytes(content), object_pairs_hook=_pairs, parse_constant=_non_json)
        return RequestPayload.validate_python(parsed)
    except (ValueError, TypeError, RecursionError, ValidationError):
        raise HTTPException(422, "Pedido de revisão inválido.") from None


async def _private_review(body: dict, actor: UUID) -> dict:
    url, secret = qr_api._config()
    headers = {"X-Orion-Bridge-Key": secret, "Content-Type": "application/json", **_assertion(body, actor, secret)}
    try:
        async with httpx.AsyncClient(timeout=18, trust_env=False, follow_redirects=False) as client:
            async with client.stream("POST", url + PRIVATE_PATH, headers=headers, content=_canonical(body)) as result:
                content = bytearray()
                async for chunk in result.aiter_bytes():
                    if len(content) + len(chunk) > RESPONSE_LIMIT:
                        raise ValueError("oversized_response")
                    content.extend(chunk)
                data = json.loads(bytes(content), object_pairs_hook=_pairs, parse_constant=_non_json)
                if result.status_code != 200:
                    code = data.get("error") if isinstance(data, dict) else None
                    status, detail = ERRORS.get(code, (503, "A revisão privada está indisponível."))
                    raise HTTPException(status, detail)
                if not isinstance(data, dict):
                    raise ValueError("invalid_response")
                return data
    except (httpx.RequestError, ValueError, TypeError, RecursionError):
        raise HTTPException(503, "A revisão privada está temporariamente indisponível.") from None


@router.post(PUBLIC_PATH, include_in_schema=False)
async def ingress_review(request: Request, response: Response, actor: UUID = Depends(require_platform_owner)):
    payload = await _read_request(request)
    binding = await _primary_binding()
    if isinstance(payload, ApplyRequest):
        _check_scope(payload.expected.scope, binding)
    body = payload.model_dump(mode="json")
    raw = await _private_review(body, actor)
    try:
        result = (DryResult if isinstance(payload, DryRequest) else ApplyResult).model_validate(raw)
    except ValidationError:
        raise HTTPException(503, "A revisão privada retornou um estado inválido.") from None
    if isinstance(result, DryResult):
        _check_scope(result.scope, binding)
        if payload.traces and any(entry.trace not in payload.traces for entry in result.entries):
            raise HTTPException(503, "A revisão privada retornou entradas não solicitadas.")
    elif (result.operation_id != payload.operation_id or result.backup.reference != payload.operation_id
          or result.before_snapshot_sha256 != payload.expected.snapshot_sha256):
        raise HTTPException(503, "Não foi possível correlacionar o resultado. Consulte o estado antes de tentar novamente.")
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return result.model_dump(mode="json")
