"""Schemas da FATIA 1. Nunca aceitar tenant_id, id ou preços arbitrários sem validação."""
from datetime import datetime, time
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Business(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=80)
    timezone: str = Field(default="America/Campo_Grande", max_length=64)

    @field_validator("timezone")
    @classmethod
    def known_tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Fuso horário IANA inválido") from exc
        return v


class Staff(StrictModel):
    phone_e164: str = Field(pattern=r"^\+55[1-9][0-9]{9,10}$")
    role: Literal["owner", "barber", "assistant"]
    active: bool = True


class Professional(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    active: bool = True


class Service(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    duration_min: int = Field(ge=1, le=1440)
    price_cents: int = Field(ge=0, le=100_000_000)
    prep_min: int = Field(default=0, ge=0, le=240)
    cleanup_min: int = Field(default=0, ge=0, le=240)
    active: bool = True


class ProfessionalService(StrictModel):
    professional_id: UUID
    service_id: UUID


class WorkingHour(StrictModel):
    professional_id: UUID
    weekday: int = Field(ge=0, le=6)
    start_time: time
    end_time: time

    @model_validator(mode="after")
    def interval(self):
        if self.start_time.tzinfo or self.end_time.tzinfo or self.start_time >= self.end_time:
            raise ValueError("Expediente deve ter horários locais válidos no mesmo dia")
        return self


class WorkingBreak(StrictModel):
    working_hour_id: UUID
    start_time: time
    end_time: time

    @model_validator(mode="after")
    def interval(self):
        if self.start_time.tzinfo or self.end_time.tzinfo or self.start_time >= self.end_time:
            raise ValueError("Pausa deve ter início anterior ao fim no mesmo dia")
        return self


class Absence(StrictModel):
    professional_id: UUID
    starts_at: datetime
    ends_at: datetime
    reason: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def interval(self):
        if not self.starts_at.tzinfo or not self.ends_at.tzinfo:
            raise ValueError("Datas exigem fuso explícito")
        if self.starts_at >= self.ends_at:
            raise ValueError("Data final deve ser posterior à inicial")
        return self


RESOURCE_MODELS = {
    "authorized_staff": Staff,
    "professionals": Professional,
    "services": Service,
    "professional_services": ProfessionalService,
    "working_hours": WorkingHour,
    "working_breaks": WorkingBreak,
    "time_off": Absence,
    "schedule_blocks": Absence,
}
