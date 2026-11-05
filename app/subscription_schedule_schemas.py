"""Bounded owner-facing scheduled-message requests; no transport authority."""
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator

from app.catalog_schemas import StrictModel
from app.subscription_chatbot_schemas import clean_text

MAX_GROUPS = 100
MAX_RECIPIENTS = 10000
MAX_PENDING_RUNS = 100
MAX_BODY = 2_097_152
MAX_METADATA_BYTES = 1_048_576
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_PLAN_BYTES = 131072
PAGE_SIZE = 30
REVIEW_SECONDS = 120
INTERVALS = (10, 20, 30, 40, 50, 60)
KINDS = ("text", "image", "video", "audio", "document", "sticker", "ptv", "location")
RUN_STATES = ("pending", "running", "completed", "failed", "uncertain", "cancelled")
RECIPIENT_STATES = ("pending", "running", "sent", "failed", "uncertain", "cancelled")
MAX_VERSION = 2**63 - 1


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
    def contact_keys_valid(cls, value):
        import re
        if any(not isinstance(key, str) or re.fullmatch(r"phone:[1-9][0-9]{7,14}", key) is None for key in value):
            raise ValueError("Selecione contatos existentes no catálogo")
        if len(value) != len(set(value)):
            raise ValueError("Contato repetido")
        return sorted(value)

    @model_validator(mode="after")
    def coherent_selection(self):
        if self.all_contacts and self.contact_keys:
            raise ValueError("Selecionar todos substitui a seleção individual")
        return self


class ScheduleDefinition(Selection):
    title: str = Field(min_length=1, max_length=120)
    device_id: UUID
    start_date: date
    start_time: str = Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    interval_seconds: StrictInt = 60
    repeat_every_30_days: StrictBool = False
    message_type: Literal["text", "template"]
    text: str | None = Field(default=None, max_length=1000)
    template_id: UUID | None = None

    @field_validator("title")
    @classmethod
    def title_valid(cls, value):
        return clean_text(value, maximum=120)

    @field_validator("interval_seconds")
    @classmethod
    def interval_valid(cls, value):
        if value not in INTERVALS:
            raise ValueError("Escolha um intervalo de 10 a 60 segundos")
        return value

    @model_validator(mode="after")
    def content_valid(self):
        from app.subscription_schedule_policy import render_text
        if not (self.group_ids or self.contact_keys or self.all_contacts):
            raise ValueError("Selecione ao menos um grupo ou contato")
        if self.message_type == "text":
            if self.text is None or self.template_id is not None:
                raise ValueError("Escolha texto ou template")
            self.text = clean_text(self.text, maximum=1000)
            render_text(self.text, {"name": "Contato"})
        elif self.text is not None or self.template_id is None:
            raise ValueError("O template substitui o texto")
        return self


class AudienceRequest(Selection):
    start_date: date | None = None
    start_time: str | None = Field(default=None, pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    interval_seconds: StrictInt | None = None

    @model_validator(mode="after")
    def estimate_fields(self):
        values = (self.start_date, self.start_time, self.interval_seconds)
        if any(value is not None for value in values) and not all(value is not None for value in values):
            raise ValueError("Informe data, horário e intervalo juntos")
        if self.interval_seconds is not None and self.interval_seconds not in INTERVALS:
            raise ValueError("Intervalo inválido")
        return self


class Operation(StrictModel):
    idempotency_key: UUID


class ReviewSchedule(Operation):
    schedule: ScheduleDefinition


class EditSchedule(ReviewSchedule):
    run_id: UUID
    expected_version: StrictInt = Field(ge=1, le=MAX_VERSION)
    expected_run_version: StrictInt = Field(ge=1, le=MAX_VERSION)


class ConfirmSchedule(Operation):
    review_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmation: Literal["AGENDAR"]
    consent_confirmed: StrictBool

    @field_validator("consent_confirmed")
    @classmethod
    def explicit_consent(cls, value):
        if value is not True:
            raise ValueError("Confirme a autorização dos destinatários")
        return value


class ReviewPending(Operation):
    pass


class ConfirmPending(ConfirmSchedule):
    confirmation: Literal["ENVIAR PENDENTES"]


class ScheduleAction(Operation):
    action: Literal["cancel_run", "cancel_series"]
    run_id: UUID | None = None
    expected_version: StrictInt = Field(ge=1, le=MAX_VERSION)
    expected_run_version: StrictInt | None = Field(default=None, ge=1, le=MAX_VERSION)
    confirmation: Literal["CANCELAR"]

    @model_validator(mode="after")
    def scope_valid(self):
        if self.action == "cancel_run":
            if self.run_id is None or self.expected_run_version is None:
                raise ValueError("Informe a ocorrência e sua versão")
        elif self.run_id is not None or self.expected_run_version is not None:
            raise ValueError("Cancelamento da série não recebe ocorrência")
        return self
