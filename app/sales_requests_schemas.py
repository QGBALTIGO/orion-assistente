"""Contact requests from the public Orion site, never a paid subscription."""
import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

from app.management_schemas import normalize_phone

Intent = Literal["trial", "essencial", "profissional", "escala", "contact"]
Segment = Literal["barbearia", "estetica", "odontologia", "restaurante", "fitness", "outro"]
Status = Literal["new", "contacted", "closed"]
CONSENT_VERSION = "sales-contact-v1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def clean_text(value):
    if not isinstance(value, str):
        return value
    # Store plain text; reject hidden controls rather than copying them into
    # contact details or later links. Consumers still escape all rendered text.
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise ValueError("Texto contém caracteres inválidos")
    return " ".join(unicodedata.normalize("NFKC", value).split())


class SalesRequest(StrictModel):
    name: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=5, max_length=254)
    phone: str | None = Field(default=None, max_length=40)
    business_name: str | None = Field(default=None, max_length=160)
    segment: Segment
    intent: Intent
    consent: StrictBool
    website: str = Field(default="", max_length=200)

    _text = field_validator("name", "email", "phone", "business_name", "website", mode="before")(clean_text)

    @field_validator("email")
    @classmethod
    def email_address(cls, value):
        value = value.lower()
        if not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+", value):
            raise ValueError("Informe um e-mail válido")
        local, domain = value.rsplit("@", 1)
        if (len(local) > 64 or local.startswith(".") or local.endswith(".") or ".." in local
                or any(label.startswith("-") or label.endswith("-") or len(label) > 63 for label in domain.split("."))):
            raise ValueError("Informe um e-mail válido")
        return value

    _phone = field_validator("phone")(normalize_phone)

    @field_validator("business_name")
    @classmethod
    def optional_business(cls, value):
        return value or None

    @field_validator("consent")
    @classmethod
    def contact_permission(cls, value):
        if value is not True:
            raise ValueError("Autorize o contato para enviar sua solicitação")
        return value


class UpdateSalesRequest(StrictModel):
    status: Status
    expected_version: StrictInt = Field(ge=1)
