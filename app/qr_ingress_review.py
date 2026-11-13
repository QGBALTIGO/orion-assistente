"""Platform-owner, no-replay review of the preserved PRIMARY admission queue.

The destination, channel and operator are server-derived. This route neither
reads Signal material nor sends/replays messages or performs booking actions.
"""
import hashlib
import hmac
import json
import time
import secrets
from cryptography.fernet import InvalidToken
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
BODY_LIMIT = 32 * 1024
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
    kind: Literal["operator_attested_prepare_failed", "operator_attested_uncertain", "not_started"]
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
    entries: list[Entry] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def unique(self):
        if len({row.trace for row in self.entries}) != len(self.entries):
            raise ValueError("Entrada repetida")
        if len(self.entries) > 2 and any(row.evidence.kind != "not_started" for row in self.entries):
            raise ValueError("Lote permitido somente para entradas não iniciadas")
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
    eligible_as: list[Literal["operator_attested_prepare_failed", "operator_attested_uncertain", "not_started"]] = Field(max_length=2)


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


async def _read_request(request: Request, adapter=RequestPayload):
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "Envie a revisão no formato correto.")
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > BODY_LIMIT:
            raise HTTPException(413, "Pedido de revisão muito grande.")
        content.extend(chunk)
    try:
        parsed = json.loads(bytes(content), object_pairs_hook=_pairs, parse_constant=_non_json)
        return adapter.validate_python(parsed)
    except (ValueError, TypeError, RecursionError, ValidationError):
        raise HTTPException(422, "Pedido de revisão inválido.") from None


async def _private_review(body: dict, actor: UUID, *, private_path=PRIVATE_PATH, channel_key=None) -> dict:
    url, secret = qr_api._config()
    headers = {"X-Orion-Bridge-Key": secret, "Content-Type": "application/json", **_assertion(body, actor, secret)}
    if channel_key is not None:
        headers["X-Orion-Channel-Key"] = channel_key
    try:
        async with httpx.AsyncClient(timeout=18, trust_env=False, follow_redirects=False) as client:
            async with client.stream("POST", url + private_path, headers=headers, content=_canonical(body)) as result:
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


# Separate platform-owner review for subscription devices. These DTOs cannot
# be confused with the PRIMARY namespace, and are not exposed to tenant owners.
DEVICE_LIST_PATH = "/api/v1/admin/whatsapp/ingress-devices"
DEVICE_PUBLIC_PATH = "/api/v1/admin/whatsapp/devices/{device_id}/ingress-review"
DEVICE_PRIVATE_PREFIX = "/internal/ops/device-ingress-review/"


class DeviceScope(Closed):
    tenant_id: UUID
    channel_id: UUID
    device_id: UUID
    generation: UUID
    settings_revision: int = Field(strict=True, ge=1, le=MAX_INTEGER)
    auth_epoch: str = Field(strict=True, pattern=HEX64)
    owner: UUID
    fence: int = Field(strict=True, ge=1, le=MAX_INTEGER)


class DeviceExpected(Closed):
    scope: DeviceScope
    snapshot_sha256: str = Field(strict=True, pattern=HEX64)


class DeviceEvidence(Closed):
    # No assertion that an uncertain audio/message was never executed.
    # A reviewed uncertain source is archived, NEVER replayed or re-sent.
    kind: Literal["operator_attested_uncertain", "operator_attested_no_replay"]
    receipt_sha256: str = Field(strict=True, pattern=HEX64)
    build_sha: str = Field(strict=True, pattern=r"^[a-f0-9]{40}$")
    event_at: int = Field(strict=True, ge=1, le=MAX_INTEGER)


class DeviceEntry(Closed):
    trace: str = Field(strict=True, pattern=TRACE)
    fingerprint: str = Field(strict=True, pattern=HEX64)
    disposition: Literal["do_not_replay"]
    evidence: DeviceEvidence


class DeviceApplyRequest(Closed):
    version: int = Field(strict=True, ge=1, le=1)
    mode: Literal["apply"]
    operation_id: UUID
    expected: DeviceExpected
    entries: list[DeviceEntry] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def unique(self):
        if len({row.trace for row in self.entries}) != len(self.entries):
            raise ValueError("Entrada repetida")
        return self


DevicePayload = TypeAdapter(Annotated[DryRequest | DeviceApplyRequest, Field(discriminator="mode")])


class DeviceEntryView(Closed):
    trace: str = Field(strict=True, pattern=TRACE)
    fingerprint: str = Field(strict=True, pattern=HEX64)
    at: int = Field(strict=True, ge=1, le=MAX_INTEGER)
    sequence: int = Field(strict=True, ge=1, le=MAX_INTEGER)
    state: Literal["pending", "blocked"]
    blocked_reason: Reason | None
    phase: Phase | None
    eligible_as: list[Literal["operator_attested_uncertain", "operator_attested_no_replay"]] = Field(max_length=2)


class DeviceDryResult(Closed):
    version: int = Field(strict=True, ge=1, le=1)
    mode: Literal["dry_run"]
    scope: DeviceScope
    snapshot_sha256: str = Field(strict=True, pattern=HEX64)
    entries: list[DeviceEntryView] = Field(max_length=32)
    total_entries: int = Field(strict=True, ge=0, le=512)
    truncated: bool = Field(strict=True)
    mutation: bool = Field(strict=True)

    @model_validator(mode="after")
    def read_only(self):
        if self.mutation or len({row.trace for row in self.entries}) != len(self.entries):
            raise ValueError("invalid_dry_run")
        return self


async def _device_binding(device_id: UUID):
    """The DB, not the browser, binds tenant, current device and channel key."""
    async with qr_channels.support_session_factory()() as db:
        device = (await db.execute(text("""
            SELECT * FROM subscription_devices
            WHERE id=:id AND removed_at IS NULL
        """), {"id": device_id})).mappings().one_or_none()
        if device is None:
            raise HTTPException(404, "Dispositivo não encontrado.")
        tenant_id, channel_id = device["tenant_id"], device["channel_id"]
        if qr_legacy.is_alias(tenant_id, channel_id):
            raise HTTPException(409, "Dispositivo preservado não permite esta revisão.")
        channel = await qr_channels._require_canonical(db, tenant_id, channel_id)
        # A paused workspace may still need its encrypted backlog retired. This
        # operator lane cannot send, replay, enable the assistant or change plan
        # state, so requiring an active plan would make safe recovery impossible.
        account_ok = (await db.execute(text("""
            SELECT 1 FROM tenants t
            JOIN tenant_memberships m ON m.tenant_id=t.id
            WHERE t.id=:tenant AND t.whatsapp_channel_id=:channel
              AND m.account_id=:owner AND m.active AND m.role='owner'
        """), {"tenant": tenant_id, "channel": channel_id,
               "owner": device["owner_account_id"]})).scalar_one_or_none()
        if (not account_ok or not channel["enabled"] or channel["owner_account_id"] != device["owner_account_id"]
                or channel["assistant_enabled"] or channel["provider"] != "baileys"
                or not isinstance(channel["lease_owner"], UUID) or
                not isinstance(channel["fence"], int) or channel["fence"] < 1):
            raise HTTPException(409, "Vínculo deste dispositivo precisa de revisão.")
        server_now = (await db.execute(text("SELECT clock_timestamp()"))).scalar_one()
        qr_channels._current({**channel, "server_now": server_now}, channel["lease_owner"], channel["fence"])
        try:
            credential = qr_channels._cipher().decrypt(channel["credential_encrypted"].encode()).decode()
            if not secrets.compare_digest(qr_channels.credential_hash(credential), channel["credential_hash"]):
                raise ValueError()
        except (InvalidToken, UnicodeError, ValueError):
            raise HTTPException(503, "Credencial do dispositivo indisponível.") from None
    binding = {"tenant_id": str(tenant_id), "channel_id": str(channel_id),
               "device_id": str(device_id), "generation": str(device["generation"]),
               "settings_revision": int(device["revision"]),
               "owner": str(channel["lease_owner"]), "fence": channel["fence"]}
    return binding, credential


def _check_device_scope(scope: DeviceScope, binding: dict) -> None:
    actual = scope.model_dump(mode="json")
    if any(actual.get(key) != value for key, value in binding.items()):
        raise HTTPException(409, "O dispositivo mudou. Consulte novamente antes de revisar.")


@router.get(DEVICE_LIST_PATH, include_in_schema=False)
async def device_review_candidates(response: Response, actor: UUID = Depends(require_platform_owner)):
    """Only operational summaries. No phone number or message text is returned."""
    async with qr_channels.support_session_factory()() as db:
        devices = (await db.execute(text("""
            SELECT d.id, d.name, t.name AS company,
              d.runtime_at,
              CASE WHEN jsonb_typeof(d.runtime#>'{connection_health,ingress_blocked}')='number'
                THEN LEAST(20000, GREATEST(0,(d.runtime#>>'{connection_health,ingress_blocked}')::integer))
                ELSE 0 END AS blocked
            FROM subscription_devices d JOIN tenants t ON t.id=d.tenant_id
            -- Include paused workspaces: listing is read-only and the review
            -- endpoint can only archive with a protected no-replay backup.
            WHERE d.removed_at IS NULL
            ORDER BY blocked DESC,d.runtime_at DESC NULLS LAST LIMIT 100
        """))).mappings().all()
    response.headers["Cache-Control"] = "no-store"
    return {"items": [{"id": str(row["id"]), "name": str(row["name"])[:100],
                       "company": str(row["company"])[:120], "blocked": row["blocked"],
                       "stale": row["runtime_at"] is None or
                           row["runtime_at"].timestamp() < time.time()-90} for row in devices
                      # The bridge is authoritative for the encrypted journal.
                      # Paused workspaces may stop publishing runtime snapshots,
                      # so platform owners must still be able to select them and
                      # perform the protected dry-run (which reveals no content).
                      ]}


@router.post(DEVICE_PUBLIC_PATH, include_in_schema=False)
async def device_ingress_review(device_id: UUID, request: Request, response: Response,
                                actor: UUID = Depends(require_platform_owner)):
    payload = await _read_request(request, DevicePayload)
    binding, channel_key = await _device_binding(device_id)
    if isinstance(payload, DeviceApplyRequest):
        _check_device_scope(payload.expected.scope, binding)
    body = payload.model_dump(mode="json")
    raw = await _private_review(body, actor,
        private_path=DEVICE_PRIVATE_PREFIX + binding["channel_id"], channel_key=channel_key)
    try:
        result = (DeviceDryResult if isinstance(payload, DryRequest) else ApplyResult).model_validate(raw)
    except ValidationError:
        raise HTTPException(503, "A revisão do dispositivo retornou um estado inválido.") from None
    if isinstance(result, DeviceDryResult):
        _check_device_scope(result.scope, binding)
        if payload.traces and any(row.trace not in payload.traces for row in result.entries):
            raise HTTPException(503, "A revisão retornou entradas não solicitadas.")
    elif (result.operation_id != payload.operation_id or result.backup.reference != payload.operation_id
          or result.before_snapshot_sha256 != payload.expected.snapshot_sha256):
        raise HTTPException(503, "Resposta não corresponde à revisão. Consulte o estado antes de repetir.")
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return result.model_dump(mode="json")
