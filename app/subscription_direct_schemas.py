"""Strict, bounded owner input for the three direct-send tabs."""
import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.catalog_schemas import StrictModel
from app.subscription_bulk_schemas import BulkAction, ConfirmBulk, canonical_phone
from app.subscription_chatbot_schemas import UrlButtonContent, clean_text
from app.subscription_templates_schemas import safe_filename

MAX_BODY = 32768
MAX_CONTACTS = 10
REVIEW_SECONDS = 120


def literal_content(value, *, empty=False):
    # Validate control characters, meaningful nonempty text and UTF-16 size,
    # but keep the author's spaces/newlines in an immediate literal message.
    clean_text(value, empty=empty, maximum=1000)
    if len(value.encode("utf-16-le")) // 2 > 1000:
        raise ValueError("O texto deve ter até 1000 caracteres")
    return value


class Recipients(StrictModel):
    mode: Literal["phone", "contacts"]
    phone: str | None = Field(default=None, max_length=40)
    contact_keys: list[str] = Field(default_factory=list, max_length=MAX_CONTACTS)

    @field_validator("contact_keys", mode="before")
    @classmethod
    def unique_contacts(cls, value):
        if not isinstance(value, list) or any(not isinstance(key, str) or re.fullmatch(
                r"phone:[1-9][0-9]{7,14}", key) is None for key in value):
            raise ValueError("Selecione até dez contatos existentes no catálogo")
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def selection(self):
        if self.mode == "phone":
            if self.contact_keys or self.phone is None:
                raise ValueError("Informe um número ou selecione contatos")
            self.phone = canonical_phone(self.phone)
        elif self.phone is not None or not self.contact_keys:
            raise ValueError("Selecione de um a dez contatos")
        return self


class SaveTemplate(StrictModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def required_name(cls, value):
        return clean_text(value, maximum=100)


class DirectMessage(StrictModel):
    device_id: UUID
    recipients: Recipients


class TextMessage(DirectMessage):
    kind: Literal["text"]
    text: str = Field(min_length=1, max_length=1000)
    save_template: SaveTemplate | None = None

    @field_validator("text")
    @classmethod
    def literal_text(cls, value):
        return literal_content(value)


class MediaMessage(DirectMessage):
    kind: Literal["media"]
    asset_id: UUID
    file_name: str = Field(min_length=1, max_length=120)
    caption: str = Field(default="", max_length=1000)
    save_template: SaveTemplate | None = None

    @field_validator("caption")
    @classmethod
    def literal_caption(cls, value):
        return literal_content(value, empty=True)

    @field_validator("file_name")
    @classmethod
    def display_filename(cls, value):
        return safe_filename(value)

    @model_validator(mode="after")
    def one_number(self):
        if self.recipients.mode != "phone":
            raise ValueError("Mídia avulsa usa um número por envio")
        return self


class TemplateMessage(DirectMessage):
    kind: Literal["template"]
    template_id: UUID


class UrlButtonMessage(DirectMessage, UrlButtonContent):
    # Immediate direct messages stay literal, including brace expressions.
    # Only saved Chatbot actions apply the existing contact-template language.
    kind: Literal["url_button"]


class ReviewDirectMessage(StrictModel):
    idempotency_key: UUID
    message: Annotated[TextMessage | MediaMessage | TemplateMessage | UrlButtonMessage, Field(discriminator="kind")]


class ConfirmDirectMessage(ConfirmBulk):
    pass


class DirectAction(BulkAction):
    pass
