"""Commercial catalog contracts, independent of professionals and appointments."""
from datetime import datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.business_profile_schemas import FAQ, ProfileModel, ShortText
from app.brazilian_fields import reject_boolean_money

PriceKind = Literal["fixed", "from", "on_request", "custom"]
IdempotencyKey = Annotated[str, Field(min_length=8, max_length=120, pattern=r"^[a-zA-Z0-9._:-]+$")]


class Product(ProfileModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, min_length=1, max_length=2000)
    synonyms: list[ShortText] = Field(default_factory=list, max_length=20)
    faqs: list[FAQ] = Field(default_factory=list, max_length=20)
    official_benefits: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(default_factory=list, max_length=20)
    active: bool = False


class ReplaceProduct(ProfileModel):
    expected_revision: int = Field(ge=1)
    product: Product


class Variant(ProfileModel):
    name: str = Field(min_length=1, max_length=120)
    sku: str | None = Field(default=None, min_length=1, max_length=80)
    attributes: dict[ShortText, ShortText] = Field(default_factory=dict, max_length=12)
    price_kind: PriceKind = "on_request"
    unit_price_cents: int | None = Field(default=None, ge=0, le=100_000_000)
    active: bool = False

    _price = field_validator("unit_price_cents", mode="before")(reject_boolean_money)

    @model_validator(mode="after")
    def price_contract(self):
        numeric = self.price_kind in {"fixed", "from"}
        if numeric != (self.unit_price_cents is not None):
            raise ValueError("Preço fixo/a partir de exige valor; sob consulta/personalizado não tem valor automático")
        return self


class ReplaceVariant(ProfileModel):
    expected_revision: int = Field(ge=1)
    variant: Variant


class StockObservation(ProfileModel):
    mode: Literal["unknown", "tracked"] = "unknown"
    quantity_on_hand: int | None = Field(default=None, ge=0, le=10_000_000)
    observed_at: datetime | None = None
    source: str | None = Field(default=None, min_length=1, max_length=160)
    valid_until: datetime | None = None

    @model_validator(mode="after")
    def observation_contract(self):
        fields = (self.quantity_on_hand, self.observed_at, self.source, self.valid_until)
        if self.mode == "unknown":
            if any(value is not None for value in fields):
                raise ValueError("Estoque desconhecido não aceita quantidade ou comprovação de disponibilidade")
            return self
        if any(value is None for value in fields):
            raise ValueError("Informe quantidade, origem, data da contagem e validade do estoque")
        if not self.observed_at.tzinfo or not self.valid_until.tzinfo:
            raise ValueError("Datas de estoque exigem fuso explícito")
        if not self.observed_at < self.valid_until <= self.observed_at + timedelta(hours=24):
            raise ValueError("A validade da observação deve ser posterior à contagem e de até 24 horas")
        return self


class ReplaceStock(ProfileModel):
    expected_revision: int = Field(ge=1)
    stock: StockObservation


class RelatedComplement(ProfileModel):
    target_product_id: UUID
    reason: str = Field(min_length=1, max_length=300)
    approved: bool = False


class QuoteLineRequest(ProfileModel):
    variant_id: UUID
    quantity: int = Field(ge=1, le=1000)


class FulfillmentSelection(ProfileModel):
    mode: Literal["pickup", "delivery"]
    pickup_appointment_id: UUID | None = None

    @model_validator(mode="after")
    def pickup_only(self):
        if self.mode != "pickup" and self.pickup_appointment_id is not None:
            raise ValueError("A associação ao agendamento só pode ser usada na retirada")
        return self


class EstimateRequest(ProfileModel):
    items: list[QuoteLineRequest] = Field(min_length=1, max_length=30)
    contact_e164: str | None = Field(default=None, pattern=r"^\+[1-9][0-9]{7,14}$")
    fulfillment: FulfillmentSelection | None = None

    @model_validator(mode="after")
    def no_duplicate_variants(self):
        ids = [item.variant_id for item in self.items]
        if len(set(ids)) != len(ids):
            raise ValueError("Agrupe a quantidade de cada variante em uma única linha")
        return self


class CreateQuote(EstimateRequest):
    customer_name: str | None = Field(default=None, min_length=1, max_length=120)
    validity_minutes: int = Field(default=15, ge=1, le=60)


class CreateOrder(ProfileModel):
    quote_id: UUID
    idempotency_key: IdempotencyKey


class ConfirmOrder(ProfileModel):
    expected_revision: int = Field(ge=1)
    confirmation_token: str = Field(min_length=40, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")


class CancelOrder(ProfileModel):
    expected_revision: int = Field(ge=1)
