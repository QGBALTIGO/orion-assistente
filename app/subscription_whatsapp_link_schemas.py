"""International click-to-chat links, preserving the complete message."""
import unicodedata

from pydantic import Field, StrictStr, field_validator

from app.catalog_schemas import StrictModel
from app.subscription_number_check_schemas import normalize_phone

MAX_MESSAGE = 1000


class LinkRequest(StrictModel):
    phone: StrictStr = Field(min_length=1, max_length=80)
    message: StrictStr = Field(default="", max_length=MAX_MESSAGE)

    @field_validator("phone")
    @classmethod
    def canonical_phone(cls, value):
        return normalize_phone(value)

    @field_validator("message")
    @classmethod
    def safe_message(cls, value):
        # Preserve accents, line breaks, tabs and emoji joiners exactly as entered.
        if any(unicodedata.category(char) in {"Cc", "Cs"} and char not in {"\n", "\r", "\t"}
               for char in value):
            raise ValueError("A mensagem contém caracteres de controle inválidos.")
        return value
