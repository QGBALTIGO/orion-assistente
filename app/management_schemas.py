"""Validated management contracts; money is always integer BRL cents."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import re
from typing import Literal
from urllib.parse import urlsplit
from app.media_paths import is_media_url
from uuid import UUID

from pydantic import AwareDatetime, Field, StrictBool, StrictInt, field_validator, model_validator

from app.catalog_schemas import StrictModel
from app.brazilian_fields import normalize_postal_code, normalize_state

MAX_MONEY = 1_000_000_000
MAX_REPORT_DAYS = 366
PaymentMethod = Literal["pix", "cash", "credit_card", "debit_card", "bank_transfer", "other"]
FinanceStatus = Literal["pending", "paid", "cancelled"]
Audience = Literal["all_active", "inactive_customers", "birthday_month", "new_customers"]


def normalize_phone(value: str | None) -> str | None:
    if value is None or value.strip() == "":
        return None
    if not re.fullmatch(r"[+\d\s().-]+", value):
        raise ValueError("Telefone brasileiro inválido")
    digits = re.sub(r"\D", "", value)
    # Foreign numbers must be explicitly prefixed with '+'. Never reinterpret a
    # malformed Brazilian national input as a different country's phone.
    if value.lstrip().startswith("+") and re.fullmatch(r"5959[0-9]{8}",digits):
        return "+" + digits
    if value.lstrip().startswith("+") and (not digits.startswith("55") or len(digits) not in (12, 13)):
        raise ValueError("Informe +55 com DDD ou +595 com celular paraguaio")
    if len(digits) in (10, 11):
        digits = "55" + digits
    normalized = "+" + digits
    if not re.fullmatch(r"\+55[1-9][0-9]{9,10}", normalized):
        raise ValueError("Informe DDD e telefone brasileiro")
    return normalized


class CleanModel(StrictModel):
    @field_validator("*", mode="before")
    @classmethod
    def trim_strings(cls, value):
        if isinstance(value, str):
            if "\x00" in value:
                raise ValueError("Caractere inválido")
            return value.strip()
        return value


class CreateOperation(CleanModel):
    idempotency_key: str = Field(min_length=8, max_length=120, pattern=r"^[a-zA-Z0-9._:-]+$")


class VersionedOperation(CleanModel):
    expected_version: StrictInt = Field(ge=1)


class CustomerFields(CleanModel):
    name: str = Field(min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    instagram: str | None = Field(default=None, max_length=120)
    neighborhood: str | None = Field(default=None, max_length=120)
    city: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=320)
    birth_date: date | None = None
    birth_month: int | None = Field(default=None,ge=1,le=12,strict=True)
    birth_day: int | None = Field(default=None,ge=1,le=31,strict=True)
    notes: str | None = Field(default=None, max_length=5000)
    active: bool = True

    @model_validator(mode='after')
    def birthday_pair(self):
        # A complete date is canonical; day/month remain compatible with older
        # readers and contacts that never supplied a year.
        if self.birth_date is not None:
            self.birth_month = self.birth_date.month
            self.birth_day = self.birth_date.day
        if (self.birth_month is None)!=(self.birth_day is None):raise ValueError('Informe dia e mês juntos')
        if self.birth_month is not None:date(2000,self.birth_month,self.birth_day)
        return self

    _phone = field_validator("phone")(normalize_phone)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        if value in (None, ""):
            return None
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("E-mail inválido")
        return value.lower()

    @field_validator("birth_date")
    @classmethod
    def valid_birth(cls, value):
        if value is not None and not date(1900, 1, 1) <= value <= datetime.now(timezone.utc).date():
            raise ValueError("Data de nascimento inválida")
        return value


class CustomerCreate(CustomerFields, CreateOperation):
    pass


class CustomerPatch(VersionedOperation):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    instagram: str | None = Field(default=None, max_length=120)
    neighborhood: str | None = Field(default=None, max_length=120)
    city: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=320)
    birth_date: date | None = None
    birth_month: int | None = Field(default=None,ge=1,le=12,strict=True)
    birth_day: int | None = Field(default=None,ge=1,le=31,strict=True)
    notes: str | None = Field(default=None, max_length=5000)
    active: bool | None = None

    _phone = field_validator("phone")(normalize_phone)
    _email = field_validator("email")(CustomerFields.valid_email.__func__)
    _birth = field_validator("birth_date")(CustomerFields.valid_birth.__func__)

    @model_validator(mode="after")
    def required_values(self):
        if "birth_date" in self.model_fields_set:
            # Assignment deliberately adds the derived fields to fields_set so
            # a partial update also persists them (or clears the entire date).
            self.birth_month = self.birth_date.month if self.birth_date else None
            self.birth_day = self.birth_date.day if self.birth_date else None
        for field in ("name", "active"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} não pode ser nulo")
        return self


class FinanceFields(CleanModel):
    kind: Literal["income", "expense"]
    description: str = Field(min_length=1, max_length=255)
    amount_cents: StrictInt = Field(ge=1, le=MAX_MONEY)
    category: str = Field(default="Geral", min_length=1, max_length=64)
    payment_method: PaymentMethod = "other"
    status: FinanceStatus = "paid"
    entry_type: Literal["general", "advance", "commission_payment"] = "general"
    occurred_at: AwareDatetime
    customer_id: UUID | None = None
    professional_id: UUID | None = None
    appointment_id: UUID | None = None
    notes: str | None = Field(default=None, max_length=5000)

    @field_validator("occurred_at")
    @classmethod
    def valid_timestamp(cls, value):
        value = value.astimezone(timezone.utc)
        if not 1970 <= value.year <= 2100:
            raise ValueError("Data do lançamento fora do intervalo permitido")
        return value

    @model_validator(mode="after")
    def staff_expense(self):
        if self.entry_type != "general" and (self.kind != "expense" or self.professional_id is None):
            raise ValueError("Vales e pagamentos de comissão exigem despesa e profissional")
        return self


class FinanceCreate(FinanceFields, CreateOperation):
    pass


class FinancePatch(VersionedOperation):
    kind: Literal["income", "expense"] | None = None
    description: str | None = Field(default=None, min_length=1, max_length=255)
    amount_cents: StrictInt | None = Field(default=None, ge=1, le=MAX_MONEY)
    category: str | None = Field(default=None, min_length=1, max_length=64)
    payment_method: PaymentMethod | None = None
    status: FinanceStatus | None = None
    entry_type: Literal["general", "advance", "commission_payment"] | None = None
    occurred_at: AwareDatetime | None = None
    customer_id: UUID | None = None
    professional_id: UUID | None = None
    appointment_id: UUID | None = None
    notes: str | None = Field(default=None, max_length=5000)


class LoyaltyRuleFields(CleanModel):
    name: str = Field(min_length=1, max_length=120)
    earning_mode: Literal["visit", "spend", "both"] = "visit"
    points_per_visit: StrictInt = Field(default=1, ge=0, le=10000)
    spend_step_cents: StrictInt = Field(default=1000, ge=1, le=MAX_MONEY)
    points_per_step: StrictInt = Field(default=1, ge=0, le=10000)
    reward_points: StrictInt = Field(default=10, ge=1, le=1_000_000)
    reward_description: str = Field(min_length=1, max_length=1000)
    reward_type: Literal["manual", "percent", "fixed", "free_service"] = "manual"
    reward_percent: Decimal | None = Field(default=None, gt=0, le=100, max_digits=5, decimal_places=2)
    reward_value_cents: StrictInt | None = Field(default=None, ge=1, le=MAX_MONEY)
    reward_service_id: UUID | None = None
    reward_valid_days: StrictInt = Field(default=30, ge=1, le=3650)
    active: bool = False

    @model_validator(mode="after")
    def valid_program(self):
        positive = ((self.earning_mode in ("visit", "both") and self.points_per_visit > 0) or
                    (self.earning_mode in ("spend", "both") and self.points_per_step > 0))
        if self.active and not positive:
            raise ValueError("Configure pelo menos uma forma de acumular pontos antes de ativar")
        required = {"percent": self.reward_percent, "fixed": self.reward_value_cents, "free_service": self.reward_service_id}
        if self.reward_type in required and required[self.reward_type] is None:
            raise ValueError("Informe o valor ou serviço correspondente ao tipo de recompensa")
        return self


class LoyaltyRuleCreate(LoyaltyRuleFields, CreateOperation):
    pass


class LoyaltyRulePatch(VersionedOperation):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    earning_mode: Literal["visit", "spend", "both"] | None = None
    points_per_visit: StrictInt | None = Field(default=None, ge=0, le=10000)
    spend_step_cents: StrictInt | None = Field(default=None, ge=1, le=MAX_MONEY)
    points_per_step: StrictInt | None = Field(default=None, ge=0, le=10000)
    reward_points: StrictInt | None = Field(default=None, ge=1, le=1_000_000)
    reward_description: str | None = Field(default=None, min_length=1, max_length=1000)
    reward_type: Literal["manual", "percent", "fixed", "free_service"] | None = None
    reward_percent: Decimal | None = Field(default=None, gt=0, le=100, max_digits=5, decimal_places=2)
    reward_value_cents: StrictInt | None = Field(default=None, ge=1, le=MAX_MONEY)
    reward_service_id: UUID | None = None
    reward_valid_days: StrictInt | None = Field(default=None, ge=1, le=3650)
    active: bool | None = None


class LoyaltyTransactionCreate(CreateOperation):
    customer_id: UUID
    points_delta: StrictInt = Field(ge=-1_000_000, le=1_000_000)
    reason: str = Field(min_length=1, max_length=500)
    rule_id: UUID | None = None
    appointment_id: UUID | None = None

    @field_validator("points_delta")
    @classmethod
    def nonzero(cls, value):
        if value == 0:
            raise ValueError("O lançamento deve acrescentar ou resgatar pontos")
        return value


class GoalFields(CleanModel):
    professional_id: UUID | None = None
    scope: Literal["company", "professional"] | None = None
    metric: Literal["revenue_cents", "appointments"]
    recurrence: Literal["none", "monthly"] = "monthly"
    start_date: date | None = None
    end_date: date | None = None
    target_value: StrictInt = Field(ge=1, le=MAX_MONEY)
    active: bool = True

    @model_validator(mode="before")
    @classmethod
    def legacy_period_and_scope(cls, values):
        if isinstance(values, dict):
            values = dict(values)
            # Existing clients send explicit intervals and know no recurrence
            # field. New clients may simply send a monthly target.
            if "recurrence" not in values and (values.get("start_date") is not None or values.get("end_date") is not None):
                values["recurrence"] = "none"
            if values.get("scope") is None:
                values["scope"] = "professional" if values.get("professional_id") is not None else "company"
        return values

    @model_validator(mode="after")
    def valid_period(self):
        if (self.scope == "professional") != (self.professional_id is not None):
            raise ValueError("Informe um profissional para a meta individual, ou nenhum para a meta da empresa")
        if self.start_date is None and self.end_date is None and self.recurrence == "monthly":
            return self
        if self.start_date is None or self.end_date is None or not 0 <= (self.end_date - self.start_date).days < MAX_REPORT_DAYS:
            raise ValueError("A meta exige um período de até 366 dias")
        if self.start_date.year < 1900 or self.end_date.year > 2100:
            raise ValueError("O período da meta deve estar entre 1900 e 2100")
        if self.recurrence == "monthly" and (
            self.start_date.day != 1 or self.start_date.month != self.end_date.month
            or self.start_date.year != self.end_date.year
            or (self.end_date + timedelta(days=1)).day != 1
        ):
            raise ValueError("A meta mensal deve cobrir um mês completo")
        return self


class GoalCreate(GoalFields, CreateOperation):
    pass


class GoalPatch(VersionedOperation):
    professional_id: UUID | None = None
    scope: Literal["company", "professional"] | None = None
    metric: Literal["revenue_cents", "appointments"] | None = None
    recurrence: Literal["none", "monthly"] | None = None
    start_date: date | None = None
    end_date: date | None = None
    target_value: StrictInt | None = Field(default=None, ge=1, le=MAX_MONEY)
    active: bool | None = None


class SitePreferences(CleanModel):
    title: str = Field(default="", max_length=160)
    description: str = Field(default="", max_length=5000)
    slug: str | None = Field(default=None, min_length=3, max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    theme_color: str = Field(default="#31ad52", pattern=r"^#[0-9a-fA-F]{6}$")
    logo_url: str | None = Field(default=None, max_length=2048)
    cover_url: str | None = Field(default=None, max_length=2048)
    address: str | None = Field(default=None, max_length=500)
    address_number: str | None = Field(default=None, max_length=30)
    address_complement: str | None = Field(default=None, max_length=160)
    neighborhood: str | None = Field(default=None, max_length=120)
    address_no_number: StrictBool = False
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    postal_code: str | None = Field(default=None, pattern=r"^[0-9]{5}-[0-9]{3}$")
    contact_phone: str | None = Field(default=None, max_length=40)
    whatsapp_phone: str | None = Field(default=None, max_length=40)
    instagram_url: str | None = Field(default=None, max_length=2048)
    maps_url: str | None = Field(default=None, max_length=2048)
    facebook_url: str | None = Field(default=None, max_length=2048)
    tiktok_url: str | None = Field(default=None, max_length=2048)
    external_url: str | None = Field(default=None, max_length=2048)
    hide_orion_brand: bool = False
    show_prices: bool = True
    # Public amenities are opt-in: never advertise services that a tenant has
    # not explicitly enabled in its site configuration.
    amenity_wifi: StrictBool = False
    amenity_parking: StrictBool = False
    amenity_accessibility: StrictBool = False
    amenity_children: StrictBool = False

    _phone = field_validator("contact_phone", "whatsapp_phone")(normalize_phone)

    @field_validator("slug", mode="before")
    @classmethod
    def normalize_editable_slug(cls, value):
        if value is None or isinstance(value, str) and not value.strip():
            return None
        if isinstance(value, str):
            from app.site_slugs import normalize_site_slug, SLUG_INPUT_LIMIT
            if len(value) > SLUG_INPUT_LIMIT:
                raise ValueError("Use até 160 caracteres para definir o endereço do site")
            return normalize_site_slug(value)
        return value

    @field_validator("address_number", "address_complement", "neighborhood", mode="before")
    @classmethod
    def optional_address_text(cls, value):
        if isinstance(value, str):
            return value.strip() or None
        return value

    @model_validator(mode="after")
    def consistent_address_number(self):
        if self.address_no_number:
            self.address_number = None
        return self

    @field_validator("state", "postal_code", mode="before")
    @classmethod
    def normalize_location(cls, value, info):
        return normalize_postal_code(value) if info.field_name == "postal_code" else normalize_state(value)

    @field_validator("logo_url", "cover_url", "instagram_url", "maps_url", "facebook_url", "tiktok_url", "external_url")
    @classmethod
    def https_only(cls, value, info):
        if value in (None, ""):
            return None
        if info.field_name in {"logo_url", "cover_url"} and is_media_url(value):
            return value
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Use um endereço HTTPS válido")
        return value


class SiteSave(SitePreferences):
    expected_version: StrictInt = Field(ge=0)

    @field_validator("slug")
    @classmethod
    def public_address(cls, value):
        if value is not None:
            from app.site_publication import RESERVED_SLUGS
            if value in RESERVED_SLUGS:
                raise ValueError("Este endereço é reservado. Escolha outro identificador")
        return value


class MarketingFields(CleanModel):
    name: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=5000)
    audience: Audience = "all_active"
    scheduled_at: AwareDatetime | None = None


class MarketingCreate(MarketingFields, CreateOperation):
    pass


class MarketingPatch(VersionedOperation):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    message: str | None = Field(default=None, min_length=1, max_length=5000)
    audience: Audience | None = None
    scheduled_at: AwareDatetime | None = None


class AppointmentTransition(CreateOperation):
    expected_status: Literal["confirmed"] = "confirmed"
    coupon_id: UUID | None = None
    final_total_cents: StrictInt | None = Field(default=None, ge=0, le=100_000_000)
    expected_updated_at: AwareDatetime | None = None


class CouponIssue(CreateOperation):
    customer_id: UUID


class SettlementCreate(CreateOperation):
    professional_id: UUID
    expected_snapshot: str = Field(pattern=r"^[0-9a-f]{64}$")
    payment_method: PaymentMethod = "other"
    occurred_at: AwareDatetime | None = None
    notes: str | None = Field(default=None, max_length=5000)

    @field_validator("occurred_at")
    @classmethod
    def bounded_date(cls, value):
        return FinanceFields.valid_timestamp(value) if value is not None else None
