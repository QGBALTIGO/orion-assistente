"""Administrative contracts for the dental reception, separate from clinical entries."""
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, UUID4, field_validator, model_validator

from app.brazilian_fields import normalize_tax_document
from app.dental_clinic_schemas import PatientCreate, Strict, UF
from app.professional_commissions import ProfessionalConfiguration


class DentalAddress(Strict):
    postal_code: str | None = Field(None, pattern=r"^[0-9]{5}-?[0-9]{3}$")
    street: str | None = Field(None, max_length=160)
    number: str | None = Field(None, max_length=20)
    complement: str | None = Field(None, max_length=120)
    neighborhood: str | None = Field(None, max_length=100)
    city: str | None = Field(None, max_length=100)
    state: str | None = None

    @field_validator("state")
    @classmethod
    def validate_state(cls, value):
        if value and value not in UF:
            raise ValueError("Selecione uma UF válida")
        return value or None


class PatientAdministrativeProfile(Strict):
    cpf: str | None = Field(None, max_length=18)
    identity_document: str | None = Field(None, max_length=35)
    email: str | None = Field(None, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    secondary_phone: str | None = Field(None, max_length=30)
    record_number: str | None = Field(None, max_length=40)
    occupation: str | None = Field(None, max_length=120)
    origin: str | None = Field(None, max_length=120)
    guardian_relationship: str | None = Field(None, max_length=80)
    financial_name: str | None = Field(None, max_length=120)
    financial_phone: str | None = Field(None, max_length=30)
    insurance_plan_id: UUID | None = None
    insurance_number: str | None = Field(None, max_length=70)
    address: DentalAddress | None = None
    administrative_note: str | None = Field(None, max_length=500)

    @field_validator("cpf")
    @classmethod
    def valid_cpf(cls, value):
        return normalize_tax_document("cpf", value)

    @field_validator("secondary_phone", "financial_phone")
    @classmethod
    def valid_contact(cls, value):
        return PatientCreate.valid_phone(value)


class ReceptionPatient(PatientCreate):
    administrative_profile: PatientAdministrativeProfile | None = None

    @model_validator(mode="after")
    def independent_patient(self):
        if self.customer_id is not None:
            raise ValueError("O identificador do contato é resolvido pela agenda")
        if self.guardian_phone and not self.guardian_name:
            raise ValueError("Informe o nome do responsável")
        profile = self.administrative_profile
        if profile and profile.financial_phone and not profile.financial_name:
            raise ValueError("Informe o nome do responsável financeiro")
        if profile and profile.insurance_number and not profile.insurance_plan_id:
            raise ValueError("Selecione o convênio da carteirinha")
        return self


class ReceptionPatientCreate(ReceptionPatient):
    request_id: UUID4


class ReceptionPatientPatch(ReceptionPatient):
    expected_version: int = Field(ge=1, strict=True)


class DentalProfessionalProfile(Strict):
    specialties: list[str] = Field(default_factory=list, max_length=12)
    cro_number: str | None = Field(None, pattern=r"^[0-9]{3,14}$")
    cro_state: str | None = None
    default_room_id: UUID | None = None

    @model_validator(mode="after")
    def verify_profile(self):
        self.specialties = [name.strip() for name in self.specialties]
        if any(not 2 <= len(name) <= 100 for name in self.specialties):
            raise ValueError("Informe especialidades de 2 a 100 caracteres")
        if len({name.casefold() for name in self.specialties}) != len(self.specialties):
            raise ValueError("Selecione cada especialidade apenas uma vez")
        if bool(self.cro_number) != bool(self.cro_state) or self.cro_state and self.cro_state not in UF:
            raise ValueError("Informe CRO e UF juntos")
        return self


class ReceptionProfessional(Strict):
    professional: ProfessionalConfiguration
    profile: DentalProfessionalProfile


class ReceptionAppointment(Strict):
    patient_id: UUID
    professional_id: UUID
    procedure_id: UUID
    room_id: UUID | None = None
    duration_min: int = Field(ge=5, le=720, strict=True)
    visit_type: Literal["evaluation", "first_visit", "return", "session", "emergency"]
    administrative_note: str | None = Field(None, max_length=500)
    contact: Literal["patient", "guardian", "none"] = "patient"
    mode: Literal["standard", "exceptional"] = "standard"
    start_at: AwareDatetime
    request_id: UUID4


class ReceptionAppointmentSelection(Strict):
    ids: list[UUID] = Field(max_length=200)
