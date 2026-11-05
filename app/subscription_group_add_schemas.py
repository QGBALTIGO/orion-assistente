"""Bounded group membership intent; no message or invitation payload is accepted."""
from datetime import date
import re
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator

from app.catalog_schemas import StrictModel
from app.subscription_chatbot_schemas import clean_text
from app import subscription_group_contacts_schemas as contacts

MAX_MEMBERS = 5000
MAX_BODY = 2_097_152
MAX_TOKEN = 360000
PREVIEW_SECONDS = 300
INTERVAL_SECONDS = 3
BRIDGE_BYTES = 240000
GROUP_RE = r"^[0-9]{5,20}(?:-[0-9]{1,20})?@g\.us$"
PHONE_RE = r"^\+[1-9][0-9]{7,14}$"
STATUSES = ("pending", "running", "added", "already_member", "not_added", "unknown", "cancelled")
REASONS = ("provider_refused", "not_admin", "group_unavailable", "context_changed",
    "device_unavailable", "ingress_blocked", "device_busy", "deadline_exceeded",
    "provider_result_unverified", "operation_incomplete", "storage_unavailable")


class PreviewRequest(StrictModel):
    device_id: UUID
    destination_group_id: str | None = Field(default=None, max_length=47, pattern=GROUP_RE)
    source: Literal["contacts", "group"]
    contact_group_id: UUID | None = None
    without_group: StrictBool = False
    search: str = Field(default="", max_length=255)
    created_from: date | None = None
    created_to: date | None = None
    source_group_id: str | None = Field(default=None, max_length=47, pattern=GROUP_RE)

    @field_validator("search")
    @classmethod
    def bounded_search(cls, value):
        return clean_text(value, maximum=255, empty=True)

    @model_validator(mode="after")
    def coherent(self):
        if self.created_from and self.created_to and self.created_from > self.created_to:
            raise ValueError("Confira o intervalo de criação")
        if self.source == "contacts":
            if self.source_group_id is not None or self.contact_group_id and self.without_group:
                raise ValueError("Escolha uma origem de contatos")
        elif (self.source_group_id is None or self.contact_group_id is not None or self.without_group
                or self.search or self.created_from is not None or self.created_to is not None):
            raise ValueError("A origem de grupo não recebe filtros de contatos locais")
        if self.source == "group" and self.destination_group_id == self.source_group_id:
            raise ValueError("Selecione grupos de origem e destino diferentes")
        return self


class CreateBatch(StrictModel):
    idempotency_key: UUID
    preview_token: str = Field(min_length=100, max_length=MAX_TOKEN)
    phones: list[str] = Field(min_length=1, max_length=MAX_MEMBERS)

    @field_validator("phones")
    @classmethod
    def unique_phones(cls, values):
        if any(not isinstance(value, str) or re.fullmatch(PHONE_RE, value) is None for value in values):
            raise ValueError("Selecione telefones da prévia")
        if len(values) != len(set(values)):
            raise ValueError("Selecione cada telefone uma vez")
        return sorted(values)


class PreviewSeal(StrictModel):
    purpose: Literal["orion.subscription.group-add.preview.v1"]
    tenant_id: UUID
    actor_account_id: UUID
    channel_id: UUID
    generation: UUID
    settings_revision: StrictInt = Field(ge=1)
    device_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    request: PreviewRequest
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    phones: list[str] = Field(min_length=1, max_length=MAX_MEMBERS)
    issued_at: StrictInt = Field(ge=1)
    expires_at: StrictInt = Field(ge=1)

    @model_validator(mode="after")
    def bound_lifetime(self):
        if self.expires_at - self.issued_at != PREVIEW_SECONDS:
            raise ValueError("Invalid preview lifetime")
        if self.request.destination_group_id is None or len(set(self.phones)) != len(self.phones):
            raise ValueError("Invalid preview selection")
        if any(re.fullmatch(PHONE_RE, value) is None for value in self.phones):
            raise ValueError("Invalid preview phone")
        return self


class RemoteGroup(contacts.RemoteGroup):
    can_add: StrictBool


class BridgeGroups(StrictModel):
    device_id: UUID
    generation: UUID
    settings_revision: StrictInt = Field(ge=1)
    groups: list[RemoteGroup] = Field(max_length=contacts.MAX_REMOTE_GROUPS)

    @model_validator(mode="after")
    def unique_groups(self):
        if len({group.id for group in self.groups}) != len(self.groups):
            raise ValueError("Duplicate group")
        return self


class BridgeMembers(contacts.BridgeMembers):
    is_admin: StrictBool


class PrivateOperation(StrictModel):
    device_id: UUID
    generation: UUID
    settings_revision: StrictInt = Field(ge=1)
    operation_id: UUID
    group_id: str = Field(max_length=47, pattern=GROUP_RE)
    phone: str = Field(max_length=16, pattern=PHONE_RE)
    claim_token: UUID


class PrivateAuthorize(PrivateOperation):
    recheck: StrictBool = False


class PrivateResult(StrictModel):
    device_id: UUID
    generation: UUID
    settings_revision: StrictInt = Field(ge=1)
    operation_id: UUID
    group_id: str = Field(max_length=47, pattern=GROUP_RE)
    phone: str = Field(max_length=16, pattern=PHONE_RE)
    status: Literal["added", "already_member", "not_added", "uncertain", "not_attempted", "unrecorded"]
    reason: str | None = None
    settled: StrictBool = False

    @model_validator(mode="after")
    def valid_reason(self):
        if self.reason is not None and self.reason not in REASONS:
            raise ValueError("Unrecognized private result")
        if self.status in {"added", "already_member", "unrecorded"} and self.reason is not None:
            raise ValueError("Unexpected reason")
        if self.status == "unrecorded" and self.settled:
            raise ValueError("An absent receipt cannot prove settlement")
        if self.status == "not_added" and self.reason != "provider_refused":
            raise ValueError("Unverified refusal")
        if self.status in {"uncertain", "not_attempted"} and self.reason is None:
            raise ValueError("Missing reason")
        return self
