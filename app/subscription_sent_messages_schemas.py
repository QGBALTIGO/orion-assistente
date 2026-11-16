"""Bounded owner input; a retry never accepts destination or message content."""
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class ResendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: UUID
    expected_version: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
