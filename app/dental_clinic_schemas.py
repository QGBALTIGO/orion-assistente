"""Validated dental inputs. No defaults that fabricate clinical findings."""
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


UF = frozenset("AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split())


class ClinicianGrant(Strict):
    account_id: UUID
    professional_id: UUID
    level: Literal["dentist","assistant"]
    cro_number: str | None = Field(None, min_length=3, max_length=14, pattern=r"^[0-9]+$")
    cro_state: str | None = Field(None, min_length=2, max_length=2)
    professional_license_checked: bool = False

    @model_validator(mode="after")
    def license(self):
        if self.level == "dentist":
            if not self.professional_license_checked or not self.cro_number or self.cro_state not in UF:
                raise ValueError("Confirme manualmente CRO e UF do profissional antes de conceder acesso clínico")
        elif self.cro_number is not None or self.cro_state is not None:
            raise ValueError("CRO é exclusivo do cirurgião-dentista")
        return self


class PatientCreate(Strict):
    name: str = Field(min_length=2, max_length=120)
    birth_date: date | None = None
    customer_id: UUID | None = None
    contact_phone: str | None = Field(None, max_length=30)
    guardian_name: str | None = Field(None, min_length=2, max_length=120)
    guardian_phone: str | None = Field(None, max_length=30)

    @field_validator("birth_date")
    @classmethod
    def valid_birth(cls, value):
        if value is not None and (value < date(1900,1,1) or value > date.today()):
            raise ValueError("Data de nascimento inválida")
        return value

    @field_validator("contact_phone","guardian_phone")
    @classmethod
    def valid_phone(cls, value):
        if value is not None and (len(value)<8 or not all(c in "+0123456789 ()-" for c in value)):
            raise ValueError("Telefone inválido")
        return value


class PatientPatch(PatientCreate):
    expected_version: int = Field(ge=1)


class ToothFinding(Strict):
    tooth: int = Field(ge=11,le=85)
    face: Literal["M","D","V","L","O","I","G"]
    finding: Literal["caries","restoration","crown","fracture","absent","implant","healthy","watch"]
    note: str | None = Field(None,max_length=300)

    @model_validator(mode="after")
    def valid_tooth(self):
        if self.tooth not in FDI_ALL:
            raise ValueError("Número FDI inválido")
        return self


FDI_ALL = frozenset((quadrant*10+tooth for quadrant in range(1,9)
                     for tooth in range(1,9 if quadrant<=4 else 6)))


class Odontogram(Strict):
    dentition: Literal["permanent","deciduous","mixed"]
    findings: list[ToothFinding] = Field(min_length=1,max_length=160)
    narrative: str | None = Field(None,max_length=3000)

    @model_validator(mode="after")
    def unique_faces(self):
        seen=set()
        for row in self.findings:
            identifier=(row.tooth,row.face)
            if identifier in seen:
                raise ValueError("Face do mesmo dente duplicada")
            if self.dentition=="permanent" and row.tooth>=50 or self.dentition=="deciduous" and row.tooth<50:
                raise ValueError("Dentição incompatível com o número FDI")
            seen.add(identifier)
        return self


class PeriodontalSite(Strict):
    depth_mm: int | None = Field(None,ge=0,le=15)
    recession_mm: int | None = Field(None,ge=-15,le=15)
    bleeding: bool | None = None


class PeriodontalTooth(Strict):
    tooth: int
    sites: list[PeriodontalSite] = Field(min_length=6,max_length=6)

    @field_validator("tooth")
    @classmethod
    def valid_tooth(cls, value):
        if value not in FDI_ALL: raise ValueError("Dente FDI inválido")
        return value

    @model_validator(mode="after")
    def at_least_one_measurement(self):
        if not any(s.depth_mm is not None or s.recession_mm is not None or s.bleeding is not None for s in self.sites):
            raise ValueError("Não registrar sítios vazios como medições")
        return self


class Periodontal(Strict):
    teeth: list[PeriodontalTooth] = Field(min_length=1,max_length=52)
    note: str | None = Field(None,max_length=3000)

    @model_validator(mode="after")
    def distinct(self):
        if len({x.tooth for x in self.teeth}) != len(self.teeth):
            raise ValueError("Dente duplicado")
        return self


class Anamnesis(Strict):
    complaint: str = Field(min_length=2,max_length=2500)
    medical_history: str | None = Field(None,max_length=4000)
    declared_allergies: str | None = Field(None,max_length=2000)
    declared_medications: str | None = Field(None,max_length=2000)
    observations: str | None = Field(None,max_length=2500)
    answered_by: Literal["patient","guardian","clinician"]


class Progress(Strict):
    description: str = Field(min_length=3,max_length=8000)
    appointment_id: UUID | None = None


class Stage(Strict):
    description: str = Field(min_length=2,max_length=400)
    tooth: int | None = None
    estimate_cents: int | None = Field(None,ge=0,le=100000000)
    state: Literal["planned","in_progress","completed","cancelled"] = "planned"

    @field_validator("tooth")
    @classmethod
    def valid_tooth(cls, value):
        if value is not None and value not in FDI_ALL: raise ValueError("Dente FDI inválido")
        return value


class Treatment(Strict):
    description: str = Field(min_length=3,max_length=3000)
    stages: list[Stage] = Field(min_length=1,max_length=60)
    note: str | None = Field(None,max_length=2500)


class Budget(Strict):
    description: str = Field(min_length=3,max_length=3000)
    items: list[Stage] = Field(min_length=1,max_length=60)
    total_cents: int = Field(ge=0,le=100000000)
    status: Literal["draft","proposed","approved","declined"] = "draft"

    @model_validator(mode="after")
    def no_fake_consent(self):
        if self.status in {"approved","declined"}:
            raise ValueError("Aceite não pode ser presumido: registre apenas orçamento em rascunho ou apresentado")
        return self


class PlainDocument(Strict):
    text: str = Field(min_length=3,max_length=8000)
    reference: str | None = Field(None,max_length=400)
    state: Literal["draft","recorded"] = "draft"


class Prosthesis(Strict):
    laboratory: str = Field(min_length=2,max_length=120)
    description: str = Field(min_length=3,max_length=3000)
    due_date: date | None = None
    status: Literal["requested","working","delivered","cancelled"] = "requested"


class Insurance(Strict):
    insurer: str = Field(min_length=2,max_length=120)
    plan: str | None = Field(None,max_length=160)
    eligibility: Literal["not_verified","verified_manually","rejected"] = "not_verified"
    notes: str | None = Field(None,max_length=2000)


PAYLOADS = {
    "anamnesis": Anamnesis, "odontogram": Odontogram,
    "periodontal": Periodontal, "progress": Progress, "treatment": Treatment,
    "budget": Budget, "prosthesis": Prosthesis, "insurance": Insurance,
    "consent": PlainDocument, "exam_note": PlainDocument,
    "certificate": PlainDocument, "referral": PlainDocument,
}


class RecordCreate(Strict):
    kind: Literal["anamnesis","odontogram","periodontal","progress","treatment",
                  "consent","budget","insurance","prosthesis","exam_note",
                  "certificate","referral"]
    content: dict = Field(min_length=1)
    entry_key: str = Field(min_length=8,max_length=120,pattern=r"^[a-zA-Z0-9._:-]+$")

    @model_validator(mode="after")
    def verify_content(self):
        model=PAYLOADS[self.kind]
        self.content=model.model_validate(self.content).model_dump(mode="json",exclude_none=True)
        return self


class RoomCreate(Strict):
    name: str = Field(min_length=2,max_length=80)


class RoomAssign(Strict):
    room_id: UUID


class SupplyCreate(Strict):
    name: str = Field(min_length=2,max_length=120)
    unit: Literal["unit","box","ml","g","kit"]


class SupplyMove(Strict):
    movement: Literal["in","out"]
    quantity: int = Field(ge=1,le=1000000,strict=True)
    entry_key: str = Field(min_length=8,max_length=120,pattern=r"^[a-zA-Z0-9._:-]+$")


class ExamUpload(Strict):
    filename: str = Field(min_length=1,max_length=180)
    media_type: Literal["application/pdf","image/png","image/jpeg"]
    data_base64: str = Field(min_length=4,max_length=12000000)
