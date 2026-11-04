"""Strict, tenant-neutral DTOs for the subscription device module."""
from datetime import datetime
import ipaddress
import re
from string import Formatter
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator

from app.catalog_schemas import StrictModel


def phone(value):
    if not isinstance(value, str) or not re.fullmatch(r"\+?[1-9][0-9]{7,14}", value):
        raise ValueError("Informe o telefone com código do país e DDD")
    return value.lstrip("+")


def destination(value, *, proxy=False):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 33 for c in value):
        raise ValueError("URL pública inválida")
    try:
        parts = urlsplit(value)
        port = parts.port
        host = (parts.hostname or "").rstrip(".").lower()
        if (parts.scheme not in ({"http"} if proxy else {"http", "https"})
                or not host or parts.username is not None or parts.password is not None
                or parts.fragment or (port is not None and not 1 <= port <= 65535)
                or host == "localhost" or host.endswith((".localhost", ".local", ".internal"))
                or proxy and (parts.path not in {"", "/"} or parts.query)):
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if "." not in host or not re.fullmatch(r"[a-z0-9.-]+", host):
                raise ValueError()
        else:
            if not address.is_global:
                raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("Informe uma URL pública, sem usuário ou senha no endereço") from None
    # DNS and the actual connected IP are rechecked/pinned by the bridge for
    # every attempt. Validation here never makes a future DNS answer trusted.
    return value


class Day(StrictModel):
    weekday: int = Field(strict=True, ge=0, le=6)
    enabled: bool = Field(default=False, strict=True)
    start: str | None = "09:00"
    break_start: str | None = None
    break_end: str | None = None
    end: str | None = "18:00"

    @field_validator("start", "end", "break_start", "break_end")
    @classmethod
    def clock(cls, value):
        if value is not None and not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
            raise ValueError("Horário deve usar HH:MM")
        return value

    @model_validator(mode="after")
    def interval(self):
        if self.enabled and (self.start is None or self.end is None or self.start >= self.end):
            raise ValueError("O início deve ser anterior ao fim do expediente")
        if (self.break_start is None) != (self.break_end is None):
            raise ValueError("Informe o início e o fim da pausa")
        if self.break_start is not None and (self.start is None or self.end is None
                or not self.start < self.break_start < self.break_end < self.end):
            raise ValueError("A pausa deve estar dentro do expediente")
        return self


def seven_days():
    return [Day(weekday=i) for i in range(7)]


class Schedule(StrictModel):
    days: list[Day] = Field(default_factory=seven_days, min_length=7, max_length=7)
    holiday_off: bool = Field(default=False, strict=True)

    @field_validator("days")
    @classmethod
    def unique_days(cls, value):
        if sorted(d.weekday for d in value) != list(range(7)):
            raise ValueError("Informe os sete dias da semana, sem repetir")
        return sorted(value, key=lambda d: d.weekday)


class Webhook(StrictModel):
    enabled: bool = Field(default=False, strict=True)
    url: str | None = Field(default=None, max_length=2048)
    disable_on_failure: bool = Field(default=True, strict=True)

    @field_validator("url")
    @classmethod
    def public_url(cls, value):
        return destination(value)

    @model_validator(mode="after")
    def complete(self):
        if self.enabled and not self.url:
            raise ValueError("Informe a URL do webhook")
        return self


class Proxy(StrictModel):
    enabled: bool = Field(default=False, strict=True)
    url: str | None = Field(default=None, max_length=2048)
    username: str | None = Field(default=None, max_length=200)
    password: str | None = Field(default=None, max_length=512, exclude=True)
    clear_password: bool = Field(default=False, strict=True, exclude=True)

    @field_validator("url")
    @classmethod
    def public_url(cls, value):
        return destination(value, proxy=True)

    @model_validator(mode="after")
    def complete(self):
        if self.enabled and not self.url:
            raise ValueError("Informe o endereço HTTP do proxy")
        if self.clear_password and self.password is not None:
            raise ValueError("Escolha substituir ou limpar a senha")
        if self.password is not None and (not self.password or not self.username):
            raise ValueError("Informe usuário e senha para autenticar o proxy")
        return self


class Transcription(StrictModel):
    enabled: bool = Field(default=False, strict=True)
    max_minutes: int = Field(default=2, strict=True, ge=1, le=30)
    template: str = Field(default="{name}\n{transcript}\n{footer}", min_length=1, max_length=2000)
    footer: str = Field(default="", max_length=500)
    reply_enabled: bool = Field(default=False, strict=True)

    @field_validator("template")
    @classmethod
    def supported_tags(cls, value):
        try:
            fields = []
            for _, key, spec, conversion in Formatter().parse(value):
                if key is not None:
                    if key not in {"name", "transcript", "footer"} or spec or conversion:
                        raise ValueError()
                    fields.append(key)
            if fields.count("transcript") != 1:
                raise ValueError()
        except ValueError:
            raise ValueError("Use {transcript} uma vez e somente as variáveis {name}, {transcript} e {footer}") from None
        return value


class Calls(Schedule):
    reject_enabled: bool = Field(default=False, strict=True)
    reply_enabled: bool = Field(default=False, strict=True)
    reply_message: str = Field(default="Não podemos atender ligações agora. Por favor, envie uma mensagem.", max_length=1200)
    whitelist: list[str] = Field(default_factory=list, max_length=100)
    schedule_enabled: bool = Field(default=False, strict=True)

    @field_validator("whitelist")
    @classmethod
    def normalize_whitelist(cls, value):
        normalized = [phone(item) for item in value]
        if len(set(normalized)) != len(normalized):
            raise ValueError("Telefone repetido na lista de permitidos")
        return normalized


class BusinessHours(Schedule):
    enabled: bool = Field(default=False, strict=True)
    block_replies_outside_hours: bool = Field(default=False, strict=True)
    outside_message: str = Field(default="", max_length=1200)
    break_message: str = Field(default="", max_length=1200)


class DeviceSettings(StrictModel):
    name: str = Field(default="WhatsApp", min_length=1, max_length=100)
    timezone: str = Field(default="America/Sao_Paulo", max_length=64)
    test_phone_e164: str | None = None
    webhook: Webhook = Field(default_factory=Webhook)
    proxy: Proxy = Field(default_factory=Proxy)
    transcription: Transcription = Field(default_factory=Transcription)
    calls: Calls = Field(default_factory=Calls)
    business_hours: BusinessHours = Field(default_factory=BusinessHours)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("Nome inválido")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Fuso horário IANA inválido") from None
        return value

    @field_validator("test_phone_e164")
    @classmethod
    def test_phone(cls, value):
        return "+" + phone(value) if value is not None else None


class CreateDevice(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    idempotency_key: UUID

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        return DeviceSettings.clean_name(value)


class UpdateSettings(StrictModel):
    expected_revision: int = Field(strict=True, ge=1)
    settings: DeviceSettings


class DeviceAction(StrictModel):
    expected_revision: int = Field(strict=True, ge=1)
    idempotency_key: UUID
    action: Literal["connect", "restart", "relink", "remove"]


class DevicePair(StrictModel):
    expected_revision: int = Field(strict=True, ge=1)
    phone: str

    @field_validator("phone")
    @classmethod
    def valid_phone(cls, value):
        return phone(value)


class AudioMetadata(StrictModel):
    seconds: float | None = Field(default=None, ge=0, le=86400)
    size: int | None = Field(default=None, strict=True, ge=0, le=2147483647)
    media_type: str | None = Field(default=None, max_length=100)


class DeviceEvent(StrictModel):
    device_id: UUID
    generation: UUID
    settings_revision: int = Field(strict=True, ge=1)
    event_id: str = Field(min_length=1, max_length=200)
    kind: Literal["text", "audio", "call", "status", "outbound"]
    occurred_at: datetime
    phone: str | None = None
    message_id: str | None = Field(default=None, max_length=200)
    name: str | None = Field(default=None, max_length=100)
    text: str | None = Field(default=None, max_length=120000)
    call_id: str | None = Field(default=None, max_length=200)
    call_from: str | None = Field(default=None, max_length=200)
    audio: AudioMetadata | None = None
    state: dict | str | None = None
    avatar_url: str | None = Field(default=None, max_length=2048)
    chatbot_revision: int | None = Field(default=None, strict=True, ge=1)
    ingress_order: int | None = Field(default=None, strict=True, ge=1, le=9007199254740991)
    origin: Literal["contact", "owner_command"] = "contact"
    selection_id: str | None = Field(default=None, min_length=1, max_length=160,
                                     pattern=r"^[A-Za-z0-9:_-]+$")
    quoted_message_id: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("phone")
    @classmethod
    def valid_phone(cls, value):
        return phone(value) if value is not None else None

    @model_validator(mode="after")
    def complete(self):
        if self.occurred_at.tzinfo is None:
            raise ValueError("O evento deve informar data com fuso horário")
        if self.kind in {"text", "audio", "outbound"} and not self.phone:
            raise ValueError("Evento individual exige telefone verificado")
        if self.kind == "audio" and (not self.message_id or self.audio is None):
            raise ValueError("Áudio exige identidade da mensagem")
        if self.kind == "outbound" and not self.message_id:
            raise ValueError("Recibo exige identidade da mensagem")
        if self.kind == "call" and (not self.call_id or not self.call_from
                or not re.fullmatch(r"[1-9][0-9]{6,30}(?::[0-9]{1,3})?@(?:s\.whatsapp\.net|lid)", self.call_from)):
            raise ValueError("Ligação individual inválida")
        if (self.selection_id is None) != (self.quoted_message_id is None):
            raise ValueError("Seleção exige a identidade da mensagem original")
        if self.selection_id is not None and (self.kind != "text" or self.origin != "contact"):
            raise ValueError("Seleção individual inválida")
        if self.origin == "owner_command" and (self.kind != "text" or not (self.text or "").startswith("!")):
            raise ValueError("Comando do aparelho inválido")
        return self


class EffectRequest(StrictModel):
    operation_id: UUID
    device_id: UUID | None = None
    generation: UUID | None = None
    settings_revision: int | None = Field(default=None, strict=True, ge=1)
    chatbot_revision: int | None = Field(default=None, strict=True, ge=1)
    recheck: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def complete_scope(self):
        supplied = [self.device_id is not None, self.generation is not None, self.settings_revision is not None]
        if any(supplied) and not all(supplied):
            raise ValueError("Informe a identidade completa do dispositivo")
        return self


class EffectFinish(EffectRequest):
    state: Literal["accepted", "failed", "uncertain", "not_sent"]
    provider_id: str | None = Field(default=None, max_length=200)
    error_code: str | None = Field(default=None, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    reconcile: bool = Field(default=False, strict=True)


class ControlRequest(EffectRequest):
    device_id: UUID
    generation: UUID
    settings_revision: int = Field(strict=True, ge=1)


class ControlFinish(ControlRequest):
    status: Literal["succeeded", "failed", "uncertain"]
    error_code: str | None = Field(default=None, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
