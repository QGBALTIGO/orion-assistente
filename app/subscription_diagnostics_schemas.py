"""Bounded owner intentions; message content and proxy targets stay server owned."""
from datetime import datetime
import re
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.catalog_schemas import StrictModel
from app.subscription_device_schemas import phone as normalize_phone


class DiagnosticIntent(StrictModel):
    idempotency_key: UUID
    expected_version: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")


class PrepareMessage(DiagnosticIntent):
    phone: str | None = Field(default=None, max_length=40)

    @field_validator("phone")
    @classmethod
    def recipient(cls, value):
        if value is None or value == "":
            return None
        if not re.fullmatch(r"[+0-9 ()-]+", value):
            raise ValueError("Informe somente o telefone, com código do país")
        canonical = re.sub(r"[ ()-]", "", value)
        return normalize_phone(canonical) if canonical else None


class ConfirmMessage(StrictModel):
    idempotency_key: UUID
    confirmation_version: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    confirmation: Literal["ENVIAR"]


class ProxyScope(StrictModel):
    request_id: UUID
    device_id: UUID
    generation: UUID
    settings_revision: int = Field(strict=True, ge=1, le=2**63-1)
    owner: UUID
    claim_token: UUID


class ProxyAuthorize(ProxyScope):
    recheck: bool = Field(default=False, strict=True)
    claim_fence: int | None = Field(default=None, strict=True, ge=1, le=2**63-1)

    @model_validator(mode="after")
    def claimed(self):
        if self.recheck != (self.claim_fence is not None):
            raise ValueError("A recheck must identify its existing claim")
        return self


class ProxyResult(StrictModel):
    scope: Literal["configured_proxy"]
    configuration_version: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    applied_to_session: bool | None
    state: Literal["reachable", "not_configured", "failed", "uncertain"]
    latency_ms: int | None = Field(default=None, strict=True, ge=0, le=30000)
    target: Literal["tls_reference"]
    observed_at: datetime
    error_code: str | None = Field(default=None, max_length=80, pattern=r"^[A-Z][A-Z0-9_]*$")

    @field_validator("observed_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("Timezone required")
        return value


class ProxyFinish(ProxyScope):
    claim_fence: int = Field(strict=True, ge=1, le=2**63-1)
    status: Literal["succeeded", "failed", "uncertain"]
    result: ProxyResult


class ProxyReceipt(StrictModel):
    request_id: UUID
