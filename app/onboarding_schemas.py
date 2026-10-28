"""Initial business setup uses the existing operational catalog, never examples."""
from datetime import time
from typing import Literal
from uuid import UUID

from pydantic import Field, PrivateAttr, StrictBool, StrictInt, field_validator, model_validator

from app.business_profile_schemas import ProfileModel, StyleRules, Capability
from app.brazilian_fields import normalize_postal_code, normalize_state
from app.catalog_schemas import Business, Professional, Service
from app.commercial_schemas import Product, Variant, IdempotencyKey
from app.management_schemas import normalize_phone


class Identity(ProfileModel):
    name: str = Field(min_length=1, max_length=160)
    timezone: str = Field(min_length=1, max_length=64)
    niche: str = Field(min_length=1, max_length=80)
    other_niche: str | None = Field(default=None, min_length=1, max_length=300)
    activity: Literal["services", "products", "both"]
    contact_phone: str | None = Field(default=None, max_length=40)

    @field_validator("name")
    @classmethod
    def readable_name(cls, value: str) -> str:
        # Treat a company name as prose, not a numeric mask or forced title case.
        readable = " ".join(value.split())
        if not readable:
            raise ValueError("Informe o nome do negócio")
        return readable

    _phone = field_validator("contact_phone")(normalize_phone)
    _timezone = field_validator("timezone")(Business.known_tz.__func__)

    @model_validator(mode="after")
    def other(self):
        if self.niche.casefold() in {"outro", "other"} and not self.other_niche:
            raise ValueError("Descreva o ramo do negócio")
        return self


class CreateBusiness(Identity):
    idempotency_key: IdempotencyKey


class Address(ProfileModel):
    postal_code: str | None = Field(default=None, pattern=r"^[0-9]{5}-?[0-9]{3}$")
    street: str = Field(min_length=1, max_length=160)
    number: str | None = Field(default=None, min_length=1, max_length=30)
    no_number: StrictBool = False
    complement: str | None = Field(default=None, max_length=160)
    neighborhood: str = Field(min_length=1, max_length=120)
    city: str = Field(min_length=1, max_length=120)
    state: str = Field(pattern=r"^[A-Z]{2}$")

    _state = field_validator("state", mode="before")(normalize_state)

    @field_validator("postal_code", mode="before")
    @classmethod
    def valid_postal_code(cls, value):
        normalized = normalize_postal_code(value)
        # Existing setup operation digests include this field's exact wire
        # representation. Validate both accepted forms without changing a
        # previously successful eight-digit replay into a different request.
        return value.strip() if normalized is not None else None

    @model_validator(mode="after")
    def street_number(self):
        if bool(self.number) == self.no_number:
            raise ValueError("Informe o número ou marque sem número")
        return self


class Location(ProfileModel):
    modality: Literal["onsite", "online", "client_address", "hybrid"]
    address: Address | None = None

    @model_validator(mode="after")
    def required_address(self):
        if self.modality in {"onsite", "hybrid"} and self.address is None:
            raise ValueError("Informe o endereço operacional; ele não será publicado")
        return self


class ProfessionalItem(Professional):
    model_config = ProfileModel.model_config
    id: UUID | None = None
    expected_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class Professionals(ProfileModel):
    item: ProfessionalItem
    self_provider: StrictBool = False


class ServiceItem(Service):
    model_config = ProfileModel.model_config
    price_cents: StrictInt = Field(ge=0, le=100_000_000)
    duration_min: StrictInt = Field(ge=1, le=1440)
    id: UUID | None = None
    expected_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ProductItem(Product):
    id: UUID | None = None
    expected_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    active: bool = True


class VariantItem(Variant):
    id: UUID | None = None
    active: bool = True


class BusinessMetrics(ProfileModel):
    """Short, estimated company profile collected during onboarding.

    These bands are stable stored identifiers, not customer or financial
    records. Keep the operational catalogue separate from the quick survey.
    """

    operating_age: Literal[
        "not_started", "under_6_months", "6_to_12_months", "1_to_3_years",
        "3_to_5_years", "over_5_years",
    ]
    team_size: Literal["none", "1_2", "3_5", "6_10", "11_20", "over_20"]
    monthly_revenue: Literal[
        "none", "under_5k", "5k_10k", "10k_25k", "25k_50k",
        "50k_100k", "over_100k", "prefer_not_to_say",
    ]
    monthly_volume: Literal[
        "none", "up_to_50", "51_100", "101_300", "301_600",
        "601_1000", "over_1000",
    ]


class Catalog(ProfileModel):
    _item_fields: set[str] = PrivateAttr(default_factory=set)
    kind: Literal["service", "product"]
    item: dict
    professional_ids: list[UUID] = Field(default_factory=list, max_length=30)
    variant: VariantItem | None = None

    @model_validator(mode="after")
    def catalog_contract(self):
        self._item_fields = set(self.item)
        model = ServiceItem if self.kind == "service" else ProductItem
        self.item = model.model_validate(self.item).model_dump(mode="json")
        if len(self.professional_ids) != len(set(self.professional_ids)):
            raise ValueError("Profissional duplicado")
        if self.kind == "product" and (self.variant is None or self.professional_ids):
            raise ValueError("Produto exige variante, sem vínculo a profissional")
        if self.kind == "service" and self.variant is not None:
            raise ValueError("Serviço não recebe variante de produto")
        return self


class Hours(ProfileModel):
    professional_id: UUID
    weekdays: list[StrictInt] = Field(min_length=1, max_length=7)
    start_time: time
    end_time: time
    break_start: time | None = None
    break_end: time | None = None
    replace_existing: StrictBool = False
    expected_fingerprints: dict[str, str] = Field(default_factory=dict, max_length=7)

    @model_validator(mode="after")
    def intervals(self):
        if len(set(self.weekdays)) != len(self.weekdays) or any(not 0 <= day <= 6 for day in self.weekdays):
            raise ValueError("Selecione dias válidos, sem repetição")
        if any(value and value.tzinfo for value in (self.start_time, self.end_time, self.break_start, self.break_end)):
            raise ValueError("Use horários locais")
        if self.start_time >= self.end_time:
            raise ValueError("A abertura deve ocorrer antes do fechamento")
        if (self.break_start is None) != (self.break_end is None):
            raise ValueError("Informe início e fim do intervalo")
        if self.break_start is not None and not self.start_time <= self.break_start < self.break_end <= self.end_time:
            raise ValueError("O intervalo deve caber no expediente")
        return self



class BusinessPriorities(ProfileModel):
    """Self-reported onboarding answers, not channel or account permissions.

    The company can change these estimates later without granting the person
    any tenant role or enabling WhatsApp, AI or publication automatically.
    """

    main_challenge: Literal[
        "scheduling", "cancellations", "whatsapp", "finance",
        "new_customers", "loyalty", "team",
    ]
    desired_outcome: Literal[
        "save_time", "grow_revenue", "professional_service",
        "automate_whatsapp", "better_control", "more_bookings",
    ]
    respondent_role: Literal["owner", "partner", "manager", "other"]


class Channels(ProfileModel):
    assistant_name: str | None = Field(default=None, min_length=1, max_length=80)
    style_rules: StyleRules
    capabilities: list[Capability] = Field(default_factory=list, max_length=5)
    expected_profile_generation: StrictInt = Field(ge=0)


class Review(ProfileModel):
    acknowledged: StrictBool

    @field_validator("acknowledged")
    @classmethod
    def explicit(cls, value):
        if value is not True:
            raise ValueError("Revise a configuração antes de concluir")
        return value


STEPS = {"business": Identity, "location": Location, "professionals": Professionals,
         "profile": BusinessMetrics, "catalog": Catalog, "hours": Hours,
         "goals": BusinessPriorities, "channels": Channels, "review": Review}


class SaveStep(ProfileModel):
    expected_version: StrictInt = Field(ge=0)
    idempotency_key: IdempotencyKey
    data: dict


class Complete(ProfileModel):
    expected_version: StrictInt = Field(ge=0)
    expected_snapshot: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: IdempotencyKey
    activate_catalog: StrictBool
    acknowledged: StrictBool

    _explicit = field_validator("acknowledged")(Review.explicit.__func__)
