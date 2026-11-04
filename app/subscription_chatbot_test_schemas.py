"""Strict inputs for the local-only Chatbot conversation simulator."""
from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import Field, field_validator

from app.catalog_schemas import StrictModel
from app.subscription_chatbot_schemas import clean_text


MESSAGE_UTF16 = 400
BODY_BYTES = 8192
TOKEN_CHARS = 4096


class TextInput(StrictModel):
    type: Literal["text"]
    text: str = Field(min_length=1, max_length=MESSAGE_UTF16)

    @field_validator("text")
    @classmethod
    def one_line(cls, value):
        # INPUT maxlength counts the submitted value, including its outer
        # spaces, in UTF-16 units. Normalize only after enforcing that limit.
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Use uma mensagem de uma linha")
        try:
            if len(value.encode("utf-16-le")) // 2 > MESSAGE_UTF16:
                raise ValueError("A mensagem ultrapassa o limite")
        except UnicodeError:
            raise ValueError("Texto inválido") from None
        return clean_text(value, maximum=MESSAGE_UTF16)


class RuleInput(StrictModel):
    type: Literal["rule"]
    rule_id: UUID


class SelectionInput(StrictModel):
    type: Literal["selection"]
    selection_token: str = Field(min_length=1, max_length=TOKEN_CHARS)
    row_id: UUID


SimulationInput = Annotated[Union[TextInput, RuleInput, SelectionInput], Field(discriminator="type")]


class SimulateRequest(StrictModel):
    request_id: UUID
    context_id: UUID
    device_id: UUID
    expected_chatbot_revision: int = Field(strict=True, ge=1, le=2**63 - 1)
    input: SimulationInput
