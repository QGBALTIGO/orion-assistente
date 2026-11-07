"""Bounded group reads and an explicit additive contact import."""
import re
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictInt, field_validator, model_validator

from app.catalog_schemas import StrictModel
from app.subscription_chatbot_schemas import clean_text

MAX_GROUPS = 100
MAX_MEMBERS = 5000
MAX_REMOTE_GROUPS = 1000
MAX_BODY = 512_000
MAX_PREVIEW_BYTES = 256_000
MAX_TOKEN = 360_000
BRIDGE_BYTES = 240_000
PREVIEW_SECONDS = 300
MAX_VERSION = 9_223_372_036_854_775_807
GROUP_ID = re.compile(r"[0-9]{5,20}(?:-[0-9]{1,20})?@g\.us")
PHONE = re.compile(r"\+[1-9][0-9]{7,14}")


def group_id(value):
    if not isinstance(value, str) or GROUP_ID.fullmatch(value) is None:
        raise ValueError("Selecione um grupo do dispositivo")
    return value


def bounded_name(value):
    value = clean_text(value, maximum=100)
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("Nome inválido")
    return value


def member_name(value):
    value = clean_text(value, empty=True, maximum=100)
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("Nome inválido")
    return value


class RemoteGroup(StrictModel):
    id: str = Field(max_length=47)
    name: str = Field(min_length=1, max_length=100)
    member_count: StrictInt = Field(ge=0, le=MAX_MEMBERS)

    _id = field_validator("id")(group_id)
    _name = field_validator("name")(bounded_name)


class Member(StrictModel):
    phone: str = Field(min_length=9, max_length=16)
    name: str = Field(max_length=100)
    role: Literal["owner", "admin", "member"]

    @field_validator("phone")
    @classmethod
    def canonical_phone(cls, value):
        if PHONE.fullmatch(value) is None:
            raise ValueError("Número não disponível")
        return value

    _name = field_validator("name")(member_name)


class DeviceScope(StrictModel):
    device_id: UUID
    generation: UUID
    settings_revision: StrictInt = Field(ge=1, le=MAX_VERSION)


class BridgeGroups(DeviceScope):
    groups: list[RemoteGroup] = Field(max_length=MAX_REMOTE_GROUPS)
    # Count unavailable metadata without presenting a partial list as complete.
    unavailable_groups: StrictInt = Field(default=0, ge=0, le=MAX_REMOTE_GROUPS)

    @model_validator(mode="after")
    def unique_groups(self):
        if len({group.id for group in self.groups}) != len(self.groups):
            raise ValueError("Grupos repetidos")
        return self


class BridgeMembers(DeviceScope):
    group: RemoteGroup
    members: list[Member] = Field(max_length=MAX_MEMBERS)
    total: StrictInt = Field(ge=0, le=MAX_MEMBERS)
    unavailable_count: StrictInt = Field(ge=0, le=MAX_MEMBERS)
    duplicate_count: StrictInt = Field(ge=0, le=MAX_MEMBERS)

    @model_validator(mode="after")
    def exact_counts(self):
        if (len({member.phone for member in self.members}) != len(self.members)
                or self.total != len(self.members) + self.unavailable_count + self.duplicate_count
                or self.group.member_count != self.total):
            raise ValueError("Contagens de participantes inconsistentes")
        return self


class ImportContacts(StrictModel):
    idempotency_key: UUID
    preview_token: str = Field(min_length=100, max_length=MAX_TOKEN)
    phones: list[str] = Field(min_length=1, max_length=MAX_MEMBERS)
    target_group_id: UUID | None
    expected_version: StrictInt | None = Field(ge=1, le=MAX_VERSION)
    new_group_name: str | None = Field(min_length=1, max_length=100)

    @field_validator("phones")
    @classmethod
    def selected_phones(cls, values):
        if any(PHONE.fullmatch(value) is None for value in values) or len(set(values)) != len(values):
            raise ValueError("Selecione números únicos da prévia")
        return sorted(values)

    @field_validator("new_group_name")
    @classmethod
    def name(cls, value):
        return bounded_name(value) if value is not None else None

    @model_validator(mode="after")
    def exact_target(self):
        if self.target_group_id is None:
            if self.expected_version is not None or self.new_group_name is None:
                raise ValueError("Informe o nome do novo grupo")
        elif self.expected_version is None or self.new_group_name is not None:
            raise ValueError("Selecione o grupo local e sua versão")
        return self
