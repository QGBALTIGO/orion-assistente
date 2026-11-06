"""Input constraints for a no-send WhatsApp click-to-chat link and QR generator."""
import re
import unicodedata
from pydantic import Field, StrictStr, field_validator, model_validator
from app.catalog_schemas import StrictModel

E164 = re.compile(r"\+[1-9][0-9]{7,14}")


class LinkRequest(StrictModel):
    phone: StrictStr = Field(max_length=16)
    message: StrictStr = Field(default="", max_length=360)

    @field_validator("phone")
    @classmethod
    def valid_phone(cls, phone):
        if E164.fullmatch(phone) is None:
            raise ValueError("Informe um número com +, DDI e DDD")
        return phone

    @field_validator("message")
    @classmethod
    def safe_message(cls, message):
        clean = unicodedata.normalize("NFC", message)
        if any(unicodedata.category(char) in {"Cc", "Cs", "Cf"}
               and char not in {"\n", "\t", "\u200d"} for char in clean):
            raise ValueError("A mensagem contém caracteres de controle")
        if len(clean.encode("utf-8")) > 1200:
            raise ValueError("A mensagem é muito longa")
        return clean

    @model_validator(mode="after")
    def size(self):
        # Leave room for URL encoding and the QR encoder at a bounded size.
        if len(self.message.encode("utf-8")) > 1200:
            raise ValueError("A mensagem é muito longa")
        return self
