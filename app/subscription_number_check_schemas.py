"""Conservative read-only verification of user-authorized WhatsApp numbers."""
import re
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator

from app.catalog_schemas import StrictModel

MAX_PER_REQUEST = 5
MAX_PER_HOUR = 30
MAX_BODY = 2048
BRIDGE_BYTES = 4096
PHONE = re.compile(r"\+[1-9][0-9]{7,14}")


class CheckRequest(StrictModel):
    device_id: UUID
    phones: list[str] = Field(min_length=1, max_length=MAX_PER_REQUEST)
    confirmed_authorization: StrictBool

    @field_validator("phones")
    @classmethod
    def canonical_phones(cls, phones):
        if any(not isinstance(phone, str) or PHONE.fullmatch(phone) is None for phone in phones):
            raise ValueError("Informe números internacionais válidos, com + e código do país")
        if len(set(phones)) != len(phones):
            raise ValueError("Não repita números na mesma verificação")
        return phones

    @model_validator(mode="after")
    def consent(self):
        if self.confirmed_authorization is not True:
            raise ValueError("Confirme que tem autorização para verificar esses números")
        return self


class CheckedNumber(StrictModel):
    phone: str
    status: Literal["registered", "not_registered", "unknown"]

    @field_validator("phone")
    @classmethod
    def canonical(cls, phone):
        if PHONE.fullmatch(phone) is None:
            raise ValueError("Número inválido")
        return phone


class BridgeResult(StrictModel):
    device_id: UUID
    generation: UUID
    settings_revision: StrictInt = Field(ge=1)
    items: list[CheckedNumber] = Field(min_length=1, max_length=MAX_PER_REQUEST)

    @model_validator(mode="after")
    def unique(self):
        if len({item.phone for item in self.items}) != len(self.items):
            raise ValueError("Respostas duplicadas")
        return self
