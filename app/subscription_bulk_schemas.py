"""Bounded bulk workbench input; each row is one explicit owner-selected send."""
import re
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator

from app.catalog_schemas import StrictModel
from app.subscription_chatbot_schemas import clean_text

MAX_GROUPS = 100
MAX_RECIPIENTS = 500
MAX_BODY = 2_097_152
MAX_METADATA_BYTES = 1_048_576
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_PLAN_BYTES = 131072
MAX_VERSION = 2**63 - 1
PAGE_SIZE = 30
REVIEW_SECONDS = 120
KINDS = ("text", "image", "video", "audio", "document", "sticker", "ptv", "location")
BATCH_STATES = ("pending", "running", "completed", "failed", "uncertain", "cancelled")
RECIPIENT_STATES = ("pending", "running", "sent", "failed", "uncertain", "cancelled")


def canonical_phone(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 40
            or re.fullmatch(r"\+?[0-9 ().-]+", value.strip()) is None):
        raise ValueError("Informe um telefone válido com código do país, de 8 a 15 dígitos")
    value = re.sub(r"[ ().-]", "", value.strip()).removeprefix("+")
    if re.fullmatch(r"[1-9][0-9]{7,14}", value) is None:
        raise ValueError("Informe um telefone válido com código do país, de 8 a 15 dígitos")
    return value


class Selection(StrictModel):
    group_ids: list[UUID] = Field(default_factory=list, max_length=MAX_GROUPS)
    contact_keys: list[str] = Field(default_factory=list, max_length=MAX_RECIPIENTS)
    all_contacts: StrictBool = False

    @field_validator("group_ids")
    @classmethod
    def unique_groups(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Grupo repetido")
        return sorted(value, key=str)

    @field_validator("contact_keys")
    @classmethod
    def contacts_valid(cls, value):
        if any(re.fullmatch(r"phone:[1-9][0-9]{7,14}", key) is None for key in value):
            raise ValueError("Selecione contatos existentes no catálogo")
        if len(value) != len(set(value)):
            raise ValueError("Contato repetido nesta adição")
        return sorted(value)

    @model_validator(mode="after")
    def coherent_selection(self):
        if self.all_contacts and self.contact_keys:
            raise ValueError("Selecionar todos substitui a seleção individual")
        return self


class AudienceRequest(Selection):
    pass


class BulkItem(StrictModel):
    row_key: UUID
    device_id: UUID
    source: Literal["catalog", "manual"]
    contact_key: str | None = Field(default=None, max_length=21)
    phone: str | None = Field(default=None, max_length=40)
    message_type: Literal["text", "template"]
    text: str | None = Field(default=None, max_length=1000)
    template_id: UUID | None = None

    @model_validator(mode="after")
    def source_and_content_valid(self):
        if self.source == "catalog":
            if self.phone is not None or self.contact_key is None or re.fullmatch(
                    r"phone:[1-9][0-9]{7,14}", self.contact_key) is None:
                raise ValueError("Selecione um contato existente no catálogo")
        elif self.contact_key is not None:
            raise ValueError("A entrada manual recebe somente o número informado")
        else:
            self.phone = canonical_phone(self.phone)
        if self.message_type == "text":
            if self.text is None or self.template_id is not None:
                raise ValueError("Escolha texto ou template")
            self.text = clean_text(self.text, maximum=1000)
        elif self.text is not None or self.template_id is None:
            raise ValueError("O template substitui o texto desta linha")
        return self


class BulkDefinition(StrictModel):
    interval_seconds: StrictInt = Field(default=10, ge=10, le=60)
    items: list[BulkItem] = Field(min_length=1, max_length=MAX_RECIPIENTS)

    @model_validator(mode="after")
    def unique_rows(self):
        if len({item.row_key for item in self.items}) != len(self.items):
            raise ValueError("Cada linha precisa de uma identidade própria")
        # Equal destinations are intentional workbench rows, including across
        # additions. Preserve their order and never silently deduplicate here.
        return self


class Operation(StrictModel):
    idempotency_key: UUID


class ReviewBulk(Operation):
    batch: BulkDefinition


class ConfirmBulk(Operation):
    review_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmation: Literal["ENVIAR"]
    consent_confirmed: StrictBool

    @field_validator("consent_confirmed")
    @classmethod
    def explicit_consent(cls, value):
        if value is not True:
            raise ValueError("Confirme a autorização dos destinatários")
        return value


class BulkAction(Operation):
    action: Literal["cancel"]
    expected_version: StrictInt = Field(ge=1, le=MAX_VERSION)
    confirmation: Literal["CANCELAR"]
