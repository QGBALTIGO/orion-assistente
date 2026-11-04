"""Bounded campaign inputs. Browser data never supplies transport authority."""
from datetime import date
import re
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator

from app.catalog_schemas import StrictModel
from app.subscription_chatbot_schemas import clean_text

MAX_GROUPS = 100
MAX_MEMBERS = 5000
MAX_RECIPIENTS = 10000
PAGE_SIZE = 30
MAX_BODY = 2_097_152
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
KINDS = ("text", "image", "video", "audio", "document", "sticker", "ptv", "location")
STATES = ("draft", "scheduled", "running", "paused", "completed", "cancelled")


def phone(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 40
            or re.fullmatch(r"\+?[0-9 ()-]+", value.strip()) is None):
        raise ValueError("Informe um telefone com código do país, sem extensão ou identificador do WhatsApp")
    digits = re.sub(r"[ ()-]", "", value.strip()).removeprefix("+")
    if re.fullmatch(r"[1-9][0-9]{7,14}", digits) is None:
        raise ValueError("Informe de 8 a 15 dígitos com o código do país")
    return digits


class Member(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    phone: str = Field(min_length=1, max_length=40)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        return clean_text(value, maximum=100)

    @field_validator("phone")
    @classmethod
    def clean_phone(cls, value):
        return phone(value)


class CampaignDefinition(StrictModel):
    title: str = Field(min_length=1, max_length=120)
    device_ids: list[UUID] = Field(min_length=1, max_length=100)
    group_ids: list[UUID] = Field(min_length=1, max_length=MAX_GROUPS)
    message_type: Literal["text", "template"]
    text: str | None = Field(default=None, max_length=3500)
    template_id: UUID | None = None
    start_date: date
    start_time: str = Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    timezone: str = Field(default="America/Sao_Paulo", min_length=1, max_length=64)
    window_start: str = Field(default="08:00", pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    window_end: str = Field(default="18:00", pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    weekdays: list[StrictInt] = Field(default_factory=lambda: [1, 2, 3, 4, 5], min_length=1, max_length=7)
    interval_seconds: StrictInt = Field(default=60, ge=30, le=60)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value):
        return clean_text(value, maximum=120)

    @field_validator("device_ids", "group_ids")
    @classmethod
    def unique_ids(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("Seleção repetida")
        return sorted(value, key=str)

    @field_validator("timezone")
    @classmethod
    def known_zone(cls, value):
        try:
            if value in {"Factory", "localtime"}:
                raise ValueError()
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Fuso IANA inválido") from None
        return value

    @field_validator("weekdays")
    @classmethod
    def unique_days(cls, value):
        if any(not 0 <= item <= 6 for item in value) or len(set(value)) != len(value):
            raise ValueError("Escolha dias distintos de domingo a sábado")
        return sorted(value)

    @model_validator(mode="after")
    def valid_content_and_schedule(self):
        from app.subscription_campaign_policy import render_text, start_at, window_bounds, next_window
        if self.message_type == "text":
            if self.template_id is not None or self.text is None:
                raise ValueError("Escolha texto ou template")
            self.text = clean_text(self.text, maximum=3500)
            render_text(self.text, {"name": "Contato", "phone": "+5567999999901"})
        elif self.text is not None or self.template_id is None:
            raise ValueError("O template substitui o texto da campanha")
        if self.window_start == self.window_end:
            raise ValueError("A janela deve ter início e fim diferentes")
        value = self.model_dump(mode="json")
        instant = start_at(value)
        window_bounds(value, self.start_date)
        next_window(value, instant)
        return self


class Operation(StrictModel):
    idempotency_key: UUID


class CreateCampaign(Operation):
    campaign: CampaignDefinition


class UpdateCampaign(CreateCampaign):
    expected_version: StrictInt = Field(ge=1)


class ReviewCampaign(Operation):
    expected_version: StrictInt = Field(ge=1)


class ConfirmCampaign(Operation):
    review_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmation: Literal["AGENDAR"]
    consent_confirmed: StrictBool

    @field_validator("consent_confirmed")
    @classmethod
    def explicit_consent(cls, value):
        if value is not True:
            raise ValueError("Confirme a autorização dos destinatários")
        return value


class CampaignAction(Operation):
    expected_version: StrictInt = Field(ge=1)
    action: Literal["pause", "resume", "edit", "archive"]


class CreateGroup(Operation):
    name: str = Field(min_length=1, max_length=100)
    members: list[Member] = Field(max_length=MAX_MEMBERS)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        return clean_text(value, maximum=100)

    @field_validator("members")
    @classmethod
    def unique_members(cls, value):
        if len({item.phone for item in value}) != len(value):
            raise ValueError("Há telefones repetidos no grupo")
        return sorted(value, key=lambda item: item.phone)


class UpdateGroup(CreateGroup):
    expected_version: StrictInt = Field(ge=1)


class ArchiveGroup(Operation):
    expected_version: StrictInt = Field(ge=1)


class PermissionContact(StrictModel):
    phone: str = Field(min_length=1, max_length=40)
    version: StrictInt = Field(ge=0)

    @field_validator("phone")
    @classmethod
    def clean_phone(cls, value):
        return phone(value)


class GroupPermissions(Operation):
    expected_version: StrictInt = Field(ge=1)
    contacts: list[PermissionContact] = Field(min_length=1, max_length=MAX_MEMBERS)
    evidence: str = Field(min_length=8, max_length=500)
    confirmation: Literal["AUTORIZAR"]

    @field_validator("evidence")
    @classmethod
    def clean_evidence(cls, value):
        value = clean_text(value, maximum=500)
        if len(value) < 8:
            raise ValueError("Descreva a autorização recebida")
        return value

    @field_validator("contacts")
    @classmethod
    def unique_contacts(cls, value):
        if len({item.phone for item in value}) != len(value):
            raise ValueError("Seleção repetida")
        return sorted(value, key=lambda item: item.phone)
