"""Structured business facts and presentation rules; never a free-form system prompt."""
from datetime import time
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.catalog_schemas import StrictModel

ShortText = Annotated[str, Field(min_length=1, max_length=80)]
Capability = Literal["catalog_answers", "appointments", "rescheduling", "human_handoff_request"]
SUPPORTED_CAPABILITIES = ("catalog_answers", "appointments", "rescheduling", "human_handoff_request")


class ProfileModel(StrictModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class StyleRules(ProfileModel):
    style: Literal["warm", "professional", "relaxed", "direct"] = "warm"
    emoji: Literal["none", "light"] = "none"
    length: Literal["short", "balanced", "detailed"] = "balanced"
    formality: Literal["neutral", "formal"] = "neutral"
    commercial_intensity: Literal["none", "contextual"] = "none"
    avoid_phrases: list[ShortText] = Field(default_factory=list, max_length=20)


class BusinessHours(ProfileModel):
    weekday: int = Field(ge=0, le=6)
    opens: time
    closes: time

    @model_validator(mode="after")
    def interval(self):
        if self.opens.tzinfo or self.closes.tzinfo or self.opens >= self.closes:
            raise ValueError("Use horários locais no mesmo dia, com abertura antes do fechamento")
        return self


class AutonomyRules(ProfileModel):
    # These operations have no engine/provider implementation. Keep explicit denials.
    discounts: Literal["human_only"] = "human_only"
    price_changes: Literal["human_only"] = "human_only"
    refunds: Literal["human_only"] = "human_only"
    cancellation_exceptions: Literal["human_only"] = "human_only"
    payment_confirmation: Literal["verified_provider_only"] = "verified_provider_only"


class FAQ(ProfileModel):
    question: str = Field(min_length=1, max_length=240)
    answer: str = Field(min_length=1, max_length=1200)


class BusinessPolicy(ProfileModel):
    topic: ShortText
    description: str = Field(min_length=1, max_length=1200)


class BusinessProfile(ProfileModel):
    business_name: str | None = Field(default=None, min_length=1, max_length=160)
    unit_name: str | None = Field(default=None, min_length=1, max_length=120)
    categories: list[ShortText] = Field(default_factory=list, max_length=10)
    description: str | None = Field(default=None, min_length=1, max_length=1000)
    address: str | None = Field(default=None, min_length=1, max_length=500)
    timezone: str | None = Field(default=None, max_length=64)
    contact_phone: str | None = Field(default=None, pattern=r"^\+[1-9][0-9]{7,14}$")
    assistant_name: str | None = Field(default=None, min_length=1, max_length=80)
    faqs: list[FAQ] = Field(default_factory=list, max_length=30)
    policies: list[BusinessPolicy] = Field(default_factory=list, max_length=20)
    # Owner-supplied facts only. Publishing is an attestation, not external verification.
    differentiators: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(default_factory=list, max_length=15)
    objectives: list[Literal["answer_questions", "help_choose", "offer_relevant_complements", "receive_appointment_requests", "request_human_help"]] = Field(default_factory=list, max_length=5)
    hours: list[BusinessHours] = Field(default_factory=list, max_length=21)
    capabilities: list[Capability] = Field(default_factory=list, max_length=4)
    style_rules: StyleRules = Field(default_factory=StyleRules)
    autonomy_rules: AutonomyRules = Field(default_factory=AutonomyRules)

    @field_validator("timezone")
    @classmethod
    def timezone_exists(cls, value):
        if value is not None:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError("Fuso horário IANA inválido") from exc
        return value

    @model_validator(mode="after")
    def consistent(self):
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("Capacidades duplicadas")
        intervals = sorted(self.hours, key=lambda item: (item.weekday, item.opens))
        for first, second in zip(intervals, intervals[1:]):
            if first.weekday == second.weekday and first.closes > second.opens:
                raise ValueError("Expedientes sobrepostos")
        # These facts do not alter the scheduling timezone/hours. They are drafts
        # until explicitly published and consumed by the presentation layer.
        return self


class SaveDraft(ProfileModel):
    expected_generation: int = Field(ge=0)
    profile: BusinessProfile


class SelectVersion(ProfileModel):
    expected_generation: int = Field(ge=0)
    version_id: UUID


class MembershipChange(ProfileModel):
    role: Literal["owner", "editor", "viewer"]
    active: bool = True
