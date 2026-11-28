"""Validated media metadata from the authenticated WhatsApp Delivery device.

Coordinates are a user-shared point, not a verified street or coverage result.
A live point is a snapshot; only actual incoming updates may replace it.
"""
from datetime import datetime
from typing import Literal
import math

from pydantic import AwareDatetime, Field, field_validator, model_validator

from app.business_profile_schemas import ProfileModel
from app.onboarding_schemas import Address


class DeliveryPin(ProfileModel):
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    kind: Literal["location", "live_location"]
    captured_at: AwareDatetime

    @field_validator("latitude", "longitude", mode="before")
    @classmethod
    def numeric_coordinate(cls, value, info):
        limit=90 if info.field_name=="latitude" else 180
        if type(value) not in (float,int) or not math.isfinite(value) or abs(value)>limit:
            raise ValueError("Coordenada inválida")
        return round(value,6)

    def map_url(self):
        return f"https://www.google.com/maps/search/?api=1&query={self.latitude:.6f}%2C{self.longitude:.6f}"


class DeliveryAddress(Address):
    # Old stored addresses, signatures and other niches keep their wire shape.
    location: DeliveryPin | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_address(cls, value):
        if isinstance(value,Address) and not isinstance(value,cls):
            return value.model_dump()
        return value


class DeliveryMedia(ProfileModel):
    kind: Literal["image", "image_unavailable", "location", "live_location", "invalid_location",
                  "unsupported_audio", "document", "video", "view_once"]
    latitude: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    longitude: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    source_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._:-]{1,160}$")
    sequence: int | None = Field(default=None, strict=True, ge=0, le=9007199254740991)
    update: bool = Field(default=False, strict=True)
    size: int | None = Field(default=None, strict=True, ge=1, le=8_000_000)
    media_type: Literal["image/jpeg", "image/png", "image/webp"] | None = None

    @field_validator("latitude", "longitude", mode="before")
    @classmethod
    def numeric_coordinate(cls, value, info):
        if value is None:
            return None
        return DeliveryPin.numeric_coordinate(value,info)

    @model_validator(mode="after")
    def validate_shape(self):
        if self.kind in {"location", "live_location"}:
            if self.latitude is None or self.longitude is None:
                raise ValueError("Localização exige duas coordenadas")
            if self.kind == "live_location" and (self.source_id is None or self.sequence is None):
                raise ValueError("Localização ao vivo exige origem e sequência")
        elif self.latitude is not None or self.longitude is not None:
            raise ValueError("Coordenadas fora de um evento de localização")
        if self.kind != "live_location" and (self.source_id is not None or self.sequence is not None or self.update):
            raise ValueError("Atualização fora de uma localização ao vivo")
        if self.kind == "image":
            if self.size is None or self.media_type is None:
                raise ValueError("Imagem exige metadados válidos")
        elif self.size is not None or self.media_type is not None:
            raise ValueError("Metadados de imagem inválidos")
        return self

    def pin(self, occurred_at: datetime):
        if self.kind not in {"location", "live_location"}:
            raise ValueError("Evento não contém localização")
        return DeliveryPin(latitude=self.latitude, longitude=self.longitude,
                           kind=self.kind, captured_at=occurred_at)


CALL_REPLY = ("Oi! Vi sua ligação. 😊\n\n"
              "Por aqui consigo te atender por mensagem. Se preferir, pode mandar um áudio "
              "ou escrever *atendente* para solicitar a nossa equipe.\n\n"
              "Como posso te ajudar?")
PHOTO_FALLBACK = ("📷 Recebi sua foto, mas não consegui identificar os detalhes com segurança.\n\n"
                  "Pode me dizer qual produto, tamanho ou endereço aparece nela? "
                  "Não confirmei nem alterei nenhum pedido.")
RECEIPT_REPLY = ("📎 Recebi a imagem do comprovante.\n\n"
                 "*O pagamento ainda precisa ser conferido pela equipe.* "
                 "Não vou marcar o Pix como pago automaticamente.")
FALLBACKS = {
    "image_unavailable": PHOTO_FALLBACK,
    "invalid_location": "📍 Não consegui ler essa localização. Pode enviar outro ponto ou escrever o endereço completo?",
    "unsupported_audio": "🎙️ Não consegui abrir esse áudio. Pode gravar um mais curto ou escrever sua mensagem?",
    "document": "📎 Recebi um documento. Pode me informar por mensagem o que você precisa? A equipe pode conferir o arquivo.",
    "video": "🎥 Recebi um vídeo. Pode descrever o que quer pedir ou enviar uma foto?",
    "view_once": "Não consigo abrir mídia de visualização única. Se quiser, envie uma foto comum ou escreva sua mensagem.",
}
