"""Versioned, strictly declarative configuration for the subscription chatbot."""
from datetime import date
import math
import re
from typing import Annotated, Literal, Union
from uuid import UUID, uuid4

from pydantic import Field, field_validator, model_validator

from app.catalog_schemas import StrictModel
from app.subscription_device_schemas import destination, phone


IMPORT_BYTES = 10 * 1024 * 1024
IMPORT_BODY_BYTES = ((IMPORT_BYTES + 2) // 3) * 4 + 8192
MAX_RULES = 2000
MAX_ASSETS = 500
MAX_TEMPLATE_BYTES = 32000
CONTACT_TAGS = frozenset({"name", "phone_number", "my_name", "my_email", "my_contact_number"})
ACTION_TYPES = ("text", "template", "sticker", "shortvideo", "carousel", "url", "nested_reply", "nested_list")
RESTORE_MODES = ("add_missing", "merge", "exact")
TAG_PATTERN = re.compile(r"\{(name|phone_number|my_name|my_email|my_contact_number)\}")
URL_TAG_PATTERN = re.compile(r"\{\{([a-zA-Z_][a-zA-Z0-9_]{0,79})\}\}")


def clean_text(value, *, empty=False, maximum=32000):
    if not isinstance(value, str) or "\x00" in value or re.search(r"[\ud800-\udfff]", value):
        raise ValueError("Texto inválido")
    value = value.strip()
    if not empty and not value:
        raise ValueError("Preencha o texto")
    if len(value.encode("utf-16-le")) // 2 > maximum:
        raise ValueError(f"O texto deve ter até {maximum} caracteres")
    return value


def contact_template(value):
    value = clean_text(value)
    # JSON/code is not a template language. Unknown brace expressions are
    # rejected instead of being evaluated, interpolated recursively or guessed.
    residue = TAG_PATTERN.sub("", value)
    if "{" in residue or "}" in residue:
        raise ValueError("Use somente as cinco tags de contato disponíveis")
    return value


class ActionBase(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    delay_seconds: int = Field(default=1, strict=True, ge=1, le=15)


class TextAction(ActionBase):
    type: Literal["text"] = "text"
    text: str = Field(min_length=1, max_length=32000)

    @field_validator("text")
    @classmethod
    def valid_text(cls, value):
        return contact_template(value)


class TemplateAction(ActionBase):
    type: Literal["template", "sticker", "shortvideo"]
    template_id: UUID


class ReferenceAction(ActionBase):
    type: Literal["nested_reply", "nested_list"]
    rule_id: UUID


class UrlAction(ActionBase):
    type: Literal["url"] = "url"
    url: str = Field(min_length=1, max_length=2048)
    method: Literal["POST", "GET"] = "POST"
    custom_template: str = Field(default="", max_length=32000)
    as_file: bool = Field(default=False, strict=True)

    @field_validator("url")
    @classmethod
    def valid_url(cls, value):
        return destination(value)

    @field_validator("custom_template")
    @classmethod
    def valid_template(cls, value):
        value = clean_text(value, empty=True)
        residue = URL_TAG_PATTERN.sub("", value)
        if "{" in residue or "}" in residue:
            raise ValueError("Use tags simples no formato {{campo}}, sem expressões ou campos aninhados")
        return value


class CarouselCard(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1, max_length=60)
    body: str = Field(default="", max_length=1024)
    image_url: str = Field(min_length=1, max_length=2048)
    button_text: str = Field(min_length=1, max_length=20)

    @field_validator("image_url")
    @classmethod
    def valid_url(cls, value):
        return destination(value)

    @field_validator("title", "button_text")
    @classmethod
    def required_text(cls, value, info):
        return clean_text(value, maximum={"title": 60, "button_text": 20}[info.field_name])

    @field_validator("body")
    @classmethod
    def optional_text(cls, value):
        return clean_text(value, empty=True, maximum=1024)


class CarouselAction(ActionBase):
    type: Literal["carousel"] = "carousel"
    body: str = Field(min_length=1, max_length=1024)
    cards: list[CarouselCard] = Field(min_length=3, max_length=3)

    @field_validator("body")
    @classmethod
    def required_body(cls, value):
        return clean_text(value, maximum=1024)

    @model_validator(mode="after")
    def unique_cards(self):
        if len({card.id for card in self.cards}) != 3 or not self.cards[0].body:
            raise ValueError("Use três cards distintos e preencha o corpo do primeiro")
        return self


Action = Annotated[Union[TextAction, TemplateAction, ReferenceAction, UrlAction, CarouselAction], Field(discriminator="type")]


class SequenceResponse(StrictModel):
    kind: Literal["sequence"] = "sequence"
    actions: list[Action] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def sequence_limits(self):
        if len({action.id for action in self.actions}) != len(self.actions):
            raise ValueError("As ações precisam ter identidades distintas")
        if sum(action.delay_seconds for action in self.actions) > 30:
            raise ValueError("A soma dos atrasos não pode ultrapassar 30 segundos")
        return self


class ListRow(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1, max_length=24)
    description: str = Field(default="", max_length=72)
    action: Action

    @field_validator("title")
    @classmethod
    def required_text(cls, value):
        return clean_text(value, maximum=24)

    @field_validator("description")
    @classmethod
    def optional_text(cls, value):
        return clean_text(value, empty=True, maximum=72)

    @model_validator(mode="after")
    def delay_limit(self):
        if self.action.delay_seconds > 5:
            raise ValueError("O atraso de uma opção deve ser de 1 a 5 segundos")
        return self


class ListSection(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    title: str = Field(default="", max_length=24)
    rows: list[ListRow] = Field(min_length=1, max_length=10)

    @field_validator("title")
    @classmethod
    def optional_text(cls, value):
        return clean_text(value, empty=True, maximum=24)


class ListResponse(StrictModel):
    kind: Literal["list"] = "list"
    title: str = Field(min_length=1, max_length=60)
    description: str = Field(min_length=1, max_length=1000)
    footer: str = Field(default="", max_length=60)
    button_text: str = Field(min_length=1, max_length=20)
    sections: list[ListSection] = Field(min_length=1, max_length=10)

    @field_validator("title", "description", "button_text")
    @classmethod
    def required_text(cls, value, info):
        return clean_text(value, maximum={"title": 60, "description": 1000, "button_text": 20}[info.field_name])

    @field_validator("footer")
    @classmethod
    def optional_text(cls, value):
        return clean_text(value, empty=True, maximum=60)

    @model_validator(mode="after")
    def list_limits(self):
        rows = [row for section in self.sections for row in section.rows]
        if len(rows) > 10:
            raise ValueError("A lista pode ter até dez opções no total")
        if (len({section.id for section in self.sections}) != len(self.sections)
                or len({row.id for row in rows}) != len(rows)
                or len({row.action.id for row in rows}) != len(rows)):
            raise ValueError("Seções, opções e ações precisam ter identidades distintas")
        return self


RuleResponse = Annotated[Union[SequenceResponse, ListResponse], Field(discriminator="kind")]


class RuleDefinition(StrictModel):
    device_id: UUID | None = None
    enabled: bool = Field(default=True, strict=True)
    match_mode: Literal["equal", "like", "array"] = "equal"
    keyword: str = Field(min_length=1, max_length=255)
    response: RuleResponse

    @field_validator("keyword")
    @classmethod
    def valid_keyword(cls, value):
        value = clean_text(value, maximum=255)
        if any(ord(char) < 32 for char in value):
            raise ValueError("A palavra-chave deve ocupar uma linha")
        return value

    @model_validator(mode="after")
    def array_terms(self):
        if self.match_mode == "array" and not any(term.strip() for term in self.keyword.split(",")):
            raise ValueError("Informe ao menos um termo da lista")
        return self


class VersionedRequest(StrictModel):
    expected_chatbot_revision: int = Field(strict=True, ge=1)
    idempotency_key: UUID


class CreateRule(VersionedRequest):
    rule: RuleDefinition


class SaveRule(CreateRule):
    expected_revision: int = Field(strict=True, ge=1)


class RuleOperation(VersionedRequest):
    expected_revision: int = Field(strict=True, ge=1)


class ToggleRule(RuleOperation):
    enabled: bool = Field(strict=True)


class DeleteAll(VersionedRequest):
    confirm: Literal[True]


class TestRule(RuleOperation):
    device_id: UUID
    phone: str

    @field_validator("phone")
    @classmethod
    def valid_phone(cls, value):
        return phone(value)


class ReadUrlTags(VersionedRequest):
    device_id: UUID | None = None
    url: str = Field(min_length=1, max_length=2048)
    method: Literal["POST", "GET"] = "POST"

    @field_validator("url")
    @classmethod
    def valid_url(cls, value):
        return destination(value)


class ImportPreview(StrictModel):
    filename: str = Field(min_length=1, max_length=150)
    content_base64: str = Field(min_length=1, max_length=((IMPORT_BYTES + 2) // 3) * 4)
    device_id: UUID | None = None
    mode: Literal["add_missing", "merge"] = "add_missing"

    @field_validator("filename")
    @classmethod
    def filename_only(cls, value):
        if "/" in value or "\\" in value or any(ord(char) < 32 for char in value):
            raise ValueError("Nome de arquivo inválido")
        if not value.lower().endswith((".json", ".zip")):
            raise ValueError("Selecione um arquivo JSON ou ZIP exportado pelo Orion")
        return value


class RestorePreview(StrictModel):
    mode: Literal["add_missing", "merge", "exact"]
    device_id: UUID | None = None


class ApplyPreview(VersionedRequest):
    confirmation: str | None = Field(default=None, max_length=20)


class ExportSelection(StrictModel):
    ids: list[UUID] = Field(min_length=1, max_length=MAX_RULES)
    format: Literal["json", "zip"] = "zip"


class HttpResult(StrictModel):
    operation_id: UUID
    device_id: UUID
    generation: UUID
    settings_revision: int = Field(strict=True, ge=1)
    chatbot_revision: int = Field(strict=True, ge=1)
    response_json: dict
    reconcile: bool = Field(default=False, strict=True)


class AssetUpload(StrictModel):
    kind: Literal["image", "sticker", "video", "ptv", "document", "audio"]
    content_base64: str = Field(min_length=1, max_length=23_000_000)


class TemplateLocation(StrictModel):
    latitude: float
    longitude: float

    @field_validator("latitude", "longitude", mode="before")
    @classmethod
    def numeric_coordinate(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("Coordenada inválida")
        return value

    @model_validator(mode="after")
    def ranges(self):
        if not -90 <= self.latitude <= 90 or not -180 <= self.longitude <= 180:
            raise ValueError("Coordenada fora dos limites")
        return self


class TemplateDefinition(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["text", "image", "sticker", "video", "ptv", "document", "audio", "location"]
    text: str = Field(default="", max_length=32000)
    followup_text: str = Field(default="", max_length=32000)
    asset_id: UUID | None = None
    location: TemplateLocation | None = None
    file_name: str | None = Field(default=None, max_length=120)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        return clean_text(value, maximum=100)

    @model_validator(mode="after")
    def template_content(self):
        if self.file_name is not None:
            self.file_name = clean_text(self.file_name, maximum=120)
            if (any(char in self.file_name for char in '/\\:')
                    or re.search(r"[\x00-\x1f\x7f]", self.file_name)
                    or self.file_name in {".", ".."} or self.file_name.endswith(".")):
                raise ValueError("Nome de arquivo inválido")
        if self.followup_text:
            self.followup_text = contact_template(self.followup_text)
        if self.kind in {"text", "location", "sticker", "ptv"} and self.followup_text:
            raise ValueError("Esse formato não usa mensagem complementar")
        if self.kind == "location":
            if self.location is None or self.asset_id is not None or self.text or self.file_name:
                raise ValueError("Preencha somente as coordenadas da localização")
            return self
        if self.location is not None:
            raise ValueError("Somente templates de localização usam coordenadas")
        if self.kind == "text":
            self.text = contact_template(self.text)
            if self.asset_id is not None or self.file_name is not None:
                raise ValueError("Template de texto não usa arquivo")
        elif self.asset_id is None:
            raise ValueError("Selecione o arquivo do template")
        elif self.text:
            self.text = contact_template(self.text)
            if self.kind in {"sticker", "ptv"}:
                raise ValueError("Figurinhas e recados de vídeo não têm legenda. Adicione uma ação Texto separada.")
            if len(self.text.encode("utf-16-le")) // 2 > 1024:
                raise ValueError("A legenda deve ter até 1024 caracteres. Use uma ação Texto para mensagens maiores.")
        return self
