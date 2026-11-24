"""Bounded inputs and exact provider results for the subscription number checker."""
import re
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictInt, StrictStr, field_validator, model_validator

from app.catalog_schemas import StrictModel

MAX_NUMBERS = 1000
MAX_BODY = 131_072
BRIDGE_BYTES = 200_000
PHONE = re.compile(r"\+[1-9][0-9]{7,14}")
STATUSES = ("registered", "not_registered", "inconclusive", "error")


def normalize_phone(value: str) -> str:
    """Keep international numbers; never infer a country or invent a ninth digit."""
    if not isinstance(value, str) or len(value) > 80:
        raise ValueError("Informe um número internacional com DDI e DDD.")
    value = value.strip()
    if re.fullmatch(r"\+?[0-9 ().\-]+", value) is None:
        raise ValueError("Informe somente o número, com DDI e DDD.")
    digits = re.sub(r"[ ().\-]", "", value)
    if digits.startswith("00"):
        digits = digits[2:]
    canonical = "+" + digits.removeprefix("+")
    if PHONE.fullmatch(canonical) is None:
        raise ValueError("Informe de 8 a 15 dígitos com o código do país.")
    return canonical


class CheckRequest(StrictModel):
    device_id: UUID
    numbers: list[StrictStr] = Field(default_factory=list, max_length=MAX_NUMBERS)
    group_id: UUID | None = None

    @field_validator("numbers")
    @classmethod
    def bounded_numbers(cls, values):
        if any(len(value) > 80 for value in values):
            raise ValueError("Uma linha ultrapassa o tamanho de um telefone.")
        return values

    @model_validator(mode="after")
    def exactly_one_source(self):
        if bool(self.numbers) == (self.group_id is not None):
            raise ValueError("Informe números ou selecione um grupo de contatos.")
        return self


class CheckedNumber(StrictModel):
    number: StrictStr
    status: Literal["registered", "not_registered", "inconclusive", "error"]
    reason: Literal["provider_timeout", "provider_failed", "provider_result_unverified", "deadline_exceeded"] | None

    @field_validator("number")
    @classmethod
    def canonical_number(cls, value):
        if PHONE.fullmatch(value) is None:
            raise ValueError("Resposta com número inválido.")
        return value

    @model_validator(mode="after")
    def definitive_has_no_failure(self):
        if self.status in {"registered", "not_registered"} and self.reason is not None:
            raise ValueError("Resposta definitiva contém falha do provedor.")
        if self.status in {"inconclusive", "error"} and self.reason is None:
            raise ValueError("Resposta inconclusiva sem motivo.")
        return self


class BridgeResult(StrictModel):
    device_id: UUID
    generation: UUID
    settings_revision: StrictInt = Field(ge=1)
    results: list[CheckedNumber] = Field(min_length=1, max_length=MAX_NUMBERS)
    total: StrictInt = Field(ge=1, le=MAX_NUMBERS)

    @model_validator(mode="after")
    def exact_total(self):
        if self.total != len(self.results):
            raise ValueError("Contagem de respostas inconsistente.")
        return self


def prepare_numbers(values: list[str]):
    """Return every invalid line, while checking each normalized phone only once."""
    seen, numbers, rows = set(), [], []
    duplicates = 0
    for value in values:
        try:
            phone = normalize_phone(value)
        except ValueError:
            rows.append({"number": value.strip(), "status": "error", "reason": "invalid_number"})
            continue
        if phone in seen:
            duplicates += 1
            continue
        seen.add(phone)
        numbers.append(phone)
        rows.append({"number": phone})
    return numbers, rows, duplicates
