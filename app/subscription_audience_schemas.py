"""Bounded, owner-managed subscription contacts and local audiences."""
from datetime import date
import re
import unicodedata
from uuid import UUID

from pydantic import AliasChoices, Field, StrictBool, StrictInt, StrictStr, field_validator, model_validator

from app.catalog_schemas import StrictModel

MAX_CONTACTS = 100_000
MAX_IMPORT_ROWS = 20_000
MAX_IMPORT_BYTES = 8 * 1024 * 1024
MAX_BODY = 16 * 1024 * 1024
MAX_PREVIEW_BYTES = 7 * 1024 * 1024
MAX_TOKEN = 10 * 1024 * 1024
PREVIEW_SECONDS = 900
MAX_GROUPS = 100
MAX_GROUP_MEMBERS = 5000
MAX_PAGE_SIZE = 100
PHONE = re.compile(r"\+[1-9][0-9]{7,14}")
CONTACT_KEY = re.compile(r"(?:phone:[1-9][0-9]{7,14}|contact:[0-9a-fA-F-]{36})")
COUNTRY_CODES = {"BR": "55", "PY": "595", "US": "1", "CA": "1", "PT": "351",
                 "AR": "54", "UY": "598", "CL": "56", "MX": "52", "GB": "44",
                 "ES": "34", "FR": "33", "DE": "49", "IT": "39", "BO": "591"}


def clean(value, *, maximum, multiline=False, empty=True):
    if not isinstance(value, str):
        raise ValueError("Informe um texto válido")
    value = unicodedata.normalize("NFC", value).strip()
    if any(unicodedata.category(char) in {"Cc", "Cs", "Cf"}
           and not (multiline and char in "\n\r\t") and char != "\u200d" for char in value):
        raise ValueError("O texto contém caracteres de controle")
    if len(value) > maximum or not empty and not value:
        raise ValueError("O tamanho do texto é inválido")
    return value


def country_code(value):
    if not isinstance(value, str):
        raise ValueError("Selecione o país dos números sem DDI")
    value = value.strip().upper()
    if value in COUNTRY_CODES:
        return COUNTRY_CODES[value]
    if re.fullmatch(r"[1-9][0-9]{0,2}", value):
        return value
    if value in {"", "INT"}:
        return ""
    raise ValueError("País inválido; use BR, PY ou o DDI")


def normalize_phone(value, default_country="BR"):
    """Normalize presentation; this does not claim that WhatsApp is registered.

    Explicit +/00 numbers always preserve their country. Local numbers use the
    chosen country. Unprefixed 12–15 digit numbers already carry an international
    code; Brazilian/PY/US formats with their own DDI are recognized separately.
    Never accept JIDs, LIDs, extensions, spreadsheet formulas or scientific form.
    """
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > 80:
        raise ValueError("Telefone inválido")
    raw = value.strip()
    if not raw or re.fullmatch(r"\+?[0-9 ()\.\-]+", raw) is None:
        raise ValueError("Use um telefone com DDD e DDI; não use links ou identificadores")
    digits = re.sub(r"[ ()\.\-]", "", raw).removeprefix("+")
    code = country_code(default_country)
    if raw.startswith("+"):
        pass
    elif digits.startswith("00"):
        digits = digits[2:]
    elif not code or len(digits) >= 12:
        pass
    elif code == "1" and len(digits) == 11 and digits.startswith("1"):
        pass
    else:
        # A national trunk prefix is not part of an E.164 number.
        if digits.startswith("0"):
            digits = digits[1:]
        digits = code + digits
    phone = "+" + digits
    if PHONE.fullmatch(phone) is None:
        raise ValueError("Informe de 8 a 15 dígitos, incluindo o código do país")
    return phone


def key(value):
    if not isinstance(value, str) or CONTACT_KEY.fullmatch(value) is None:
        raise ValueError("Contato inválido")
    if value.startswith("contact:"):
        return "contact:" + str(UUID(value[8:]))
    return value


class Contact(StrictModel):
    name: StrictStr = Field(min_length=1, max_length=120)
    phone_e164: StrictStr | None = Field(default=None, max_length=80,
        validation_alias=AliasChoices("phone_e164", "phone", "whatsapp"))
    default_country: StrictStr = Field(default="BR", max_length=4)
    telegram: StrictStr | None = Field(default=None, max_length=64)
    email: StrictStr | None = Field(default=None, max_length=320)
    notes: StrictStr | None = Field(default=None, max_length=5000)
    group_ids: list[UUID] = Field(default_factory=list, max_length=MAX_GROUPS)
    chatbot_enabled: StrictBool = True
    audio_transcription_enabled: StrictBool = True
    plan_name: StrictStr | None = Field(default=None, max_length=120)
    price_cents: StrictInt | None = Field(default=None, ge=0, le=100_000_000_000)
    due_date: date | None = None
    service_username: StrictStr | None = Field(default=None, max_length=255)
    service_password: StrictStr | None = Field(default=None, max_length=1024)
    clear_password: StrictBool = False

    @field_validator("name")
    @classmethod
    def name_text(cls, value):
        return clean(value, maximum=120, empty=False)

    @field_validator("telegram")
    @classmethod
    def telegram_identity(cls, value):
        if value in {None, ""}:
            return None
        value = value.strip()
        if not re.fullmatch(r"(?:@?[A-Za-z][A-Za-z0-9_]{3,31}|[1-9][0-9]{4,19})", value):
            raise ValueError("Use o usuário ou ID do Telegram")
        return value if value.isdecimal() else "@" + value.lstrip("@").lower()

    @field_validator("email")
    @classmethod
    def email_text(cls, value):
        if value in {None, ""}:
            return None
        value = clean(value, maximum=320)
        if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value) is None:
            raise ValueError("E-mail inválido")
        return value.lower()

    @field_validator("notes")
    @classmethod
    def notes_text(cls, value):
        return clean(value, maximum=5000, multiline=True) or None if value is not None else None

    @field_validator("plan_name", "service_username")
    @classmethod
    def optional_text(cls, value):
        return clean(value, maximum=255) or None if value is not None else None

    @field_validator("service_password")
    @classmethod
    def password_text(cls, value):
        if value is not None and ("\x00" in value or any(0xD800 <= ord(c) <= 0xDFFF for c in value)):
            raise ValueError("Senha inválida")
        return value

    @field_validator("group_ids")
    @classmethod
    def unique_groups(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("Não repita grupos")
        return sorted(value, key=str)

    @model_validator(mode="after")
    def identity(self):
        self.phone_e164 = normalize_phone(self.phone_e164, self.default_country)
        if self.phone_e164 is None and self.telegram is None:
            raise ValueError("Informe WhatsApp ou Telegram")
        if self.service_password is not None and self.clear_password:
            raise ValueError("Escolha alterar ou limpar a senha")
        return self


class Operation(StrictModel):
    idempotency_key: UUID


class CreateContact(Operation):
    contact: Contact


class UpdateContact(CreateContact):
    expected_version: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")


class ArchiveContact(Operation):
    expected_version: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")


class CreateGroup(Operation):
    name: StrictStr = Field(min_length=1, max_length=100)
    contact_keys: list[StrictStr] = Field(default_factory=list, max_length=MAX_GROUP_MEMBERS)

    @field_validator("name")
    @classmethod
    def name_text(cls, value):
        return clean(value, maximum=100, empty=False)

    @field_validator("contact_keys")
    @classmethod
    def keys(cls, values):
        if values is None:
            return None
        values = [key(value) for value in values]
        if len(set(values)) != len(values):
            raise ValueError("Não repita contatos no grupo")
        return sorted(values)


class UpdateGroup(CreateGroup):
    expected_version: StrictInt = Field(ge=1, le=2**63 - 1)
    contact_keys: list[StrictStr] | None = Field(default=None, max_length=MAX_GROUP_MEMBERS)

    @field_validator("contact_keys")
    @classmethod
    def nullable_keys(cls, values):
        return CreateGroup.keys(values) if values is not None else None


class ArchiveGroup(Operation):
    expected_version: StrictInt = Field(ge=1, le=2**63 - 1)


class ImportPreview(StrictModel):
    file_name: StrictStr = Field(min_length=1, max_length=255)
    content: StrictStr = Field(max_length=MAX_IMPORT_BYTES)
    default_country: StrictStr = Field(default="BR", max_length=4)
    group_ids: list[UUID] = Field(default_factory=list, max_length=MAX_GROUPS)

    @field_validator("file_name")
    @classmethod
    def extension(cls, value):
        if not value.lower().endswith((".csv", ".txt")):
            raise ValueError("Importe um arquivo CSV ou TXT")
        return clean(value, maximum=255, empty=False)

    @field_validator("default_country")
    @classmethod
    def country(cls, value):
        country_code(value)
        return value.upper()

    @field_validator("group_ids")
    @classmethod
    def groups(cls, value):
        return Contact.unique_groups(value)


class ImportCommit(Operation):
    preview_token: StrictStr = Field(min_length=100, max_length=MAX_TOKEN)
    skip_invalid: StrictBool = False
