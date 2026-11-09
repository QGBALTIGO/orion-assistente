"""Clinical records fail closed, validate real FDI surfaces and never invent measurements."""
import base64
import os
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.catalog_api import Actor
from app.dental_clinic_api import key_material, seal, unseal, require_member
from app.dental_clinic_schemas import (
    ClinicianGrant, PatientCreate, RecordCreate, Odontogram, Periodontal,
    Anamnesis, ExamUpload, Budget, FDI_ALL
)


def test_fdi_exact_count_and_all_quadrants():
    assert len(FDI_ALL) == 52
    assert {11,18,21,28,31,38,41,48,51,55,61,65,71,75,81,85} <= FDI_ALL
    assert {10,19,49,50,59,69,79,89}.isdisjoint(FDI_ALL)


@pytest.mark.parametrize("payload", [
    {"dentition":"permanent","findings":[{"tooth":51,"face":"M","finding":"caries"}]},
    {"dentition":"deciduous","findings":[{"tooth":11,"face":"M","finding":"watch"}]},
    {"dentition":"permanent","findings":[{"tooth":11,"face":"M","finding":"watch"},
                                         {"tooth":11,"face":"M","finding":"caries"}]},
    {"dentition":"permanent","findings":[{"tooth":49,"face":"M","finding":"caries"}]},
    {"dentition":"permanent","findings":[{"tooth":11,"face":"X","finding":"caries"}]},
    {"dentition":"permanent","findings":[]},
])
def test_odontogram_rejects_invalid_fdi_or_duplicate_faces(payload):
    with pytest.raises(ValidationError): Odontogram.model_validate(payload)


def test_odontogram_allows_only_explicit_professional_findings():
    chart=Odontogram(dentition="permanent",findings=[{"tooth":11,"face":"M","finding":"watch"}])
    assert len(chart.findings)==1 and chart.findings[0].tooth==11
    assert chart.findings[0].finding=="watch"


def test_periodontal_requires_six_sites_without_default_depth():
    sites=[{"depth_mm":None,"bleeding":None} for _ in range(6)]
    with pytest.raises(ValidationError):
        Periodontal.model_validate({"teeth":[{"tooth":11,"sites":sites}]})
    sites[0]["depth_mm"]=3
    chart=Periodontal.model_validate({"teeth":[{"tooth":11,"sites":sites}]})
    assert chart.teeth[0].sites[0].depth_mm==3
    assert chart.teeth[0].sites[1].depth_mm is None
    assert chart.teeth[0].sites[1].bleeding is None
    with pytest.raises(ValidationError):
        Periodontal.model_validate({"teeth":[{"tooth":11,"sites":sites[:5]}]})


@pytest.mark.parametrize("body", [
    {"complaint":"Dor","answered_by":"patient"},
    {"complaint":"Dor","answered_by":"guardian","declared_allergies":None},
])
def test_anamnesis_does_not_fabricate_negative_answers(body):
    item=Anamnesis.model_validate(body).model_dump(exclude_none=True)
    assert "declared_allergies" not in item
    assert "declared_medications" not in item


@pytest.mark.parametrize("payload", [
    {"account_id":str(uuid4()),"professional_id":str(uuid4()),"level":"dentist",
     "cro_number":"12345","cro_state":"MS","professional_license_checked":False},
    {"account_id":str(uuid4()),"professional_id":str(uuid4()),"level":"dentist",
     "cro_number":"12345","cro_state":"ZZ","professional_license_checked":True},
    {"account_id":str(uuid4()),"professional_id":str(uuid4()),"level":"assistant",
     "cro_number":"12345","professional_license_checked":False},
])
def test_clinical_grant_requires_attested_manual_cro_check(payload):
    with pytest.raises(ValidationError): ClinicianGrant.model_validate(payload)


def test_patient_guardian_phone_is_not_unique_or_used_as_identity():
    one=PatientCreate(name="Irmão mais velho",guardian_phone="(67) 99999-0000")
    two=PatientCreate(name="Irmã mais nova",guardian_phone="(67) 99999-0000")
    assert one.name!=two.name and one.guardian_phone==two.guardian_phone


def test_budget_approval_is_not_accepted_as_electronic_signature():
    with pytest.raises(ValidationError):
        Budget(description="Tratamento",items=[{"description":"Procedimento"}],total_cents=100, status="approved")


def test_medical_write_requires_member_with_real_account_and_no_maintenance():
    tenant,account=uuid4(),uuid4()
    for actor in (Actor("support"),Actor("admin",account_id=account),
                  Actor("viewer",tenant),Actor("member",tenant,account,"viewer"),
                  Actor("member",tenant,account,"owner",True),
                  Actor("member",uuid4(),account,"owner")):
        with pytest.raises(HTTPException) as exc: require_member(actor,tenant)
        assert exc.value.status_code==403
    require_member(Actor("member",tenant,account,"owner"),tenant)


def test_clinical_key_absence_fails_closed(monkeypatch):
    monkeypatch.delenv("DENTAL_DATA_KEY_B64",raising=False)
    with pytest.raises(HTTPException) as err:key_material()
    assert err.value.status_code==503


def test_encrypted_record_roundtrip_and_cross_patient_swap_denied(monkeypatch):
    monkeypatch.setenv("DENTAL_DATA_KEY_B64",base64.b64encode(os.urandom(32)).decode())
    tenant,patient,entry=uuid4(),uuid4(),uuid4()
    original=b'{"complaint":"Sintoma sintetico"}'
    nonce,cipher,digest=seal(original,tenant,patient,"anamnesis",entry)
    assert original not in cipher
    row={"id":entry,"nonce":nonce,"encrypted_content":cipher,"digest":digest}
    assert unseal(row,tenant,patient,"anamnesis")==original
    with pytest.raises(HTTPException) as err:unseal(row,tenant,uuid4(),"anamnesis")
    assert err.value.status_code==503
    with pytest.raises(HTTPException):unseal(row,uuid4(),patient,"anamnesis")
    assert len(nonce)==12


def test_exam_upload_rejects_unknown_mime_and_oversize_base64():
    with pytest.raises(ValidationError):
        ExamUpload(filename="x.svg",media_type="image/svg+xml",data_base64="AAAA")
    with pytest.raises(ValidationError):
        ExamUpload(filename="x.pdf",media_type="application/pdf",data_base64="A"*12000001)


def test_mutations_and_access_never_have_implicit_clinical_rights():
    from app.dental_clinic_api import router
    import app.dental_clinic_media  # loads protected exam endpoints before inspecting router
    paths={r.path for r in router.routes}
    assert any(p.endswith("/access") for p in paths)
    assert any(p.endswith("/records") for p in paths)
    assert any(p.endswith("/exams/{exam_id}") for p in paths)


def test_dental_rollout_fails_closed_without_exact_flag_and_strong_key(monkeypatch):
    from app.plan_permissions import dental_environment_ready
    monkeypatch.delenv("ORION_DENTAL_CLINIC_ENABLED", raising=False)
    monkeypatch.delenv("DENTAL_DATA_KEY_B64", raising=False)
    assert dental_environment_ready() is False
    monkeypatch.setenv("ORION_DENTAL_CLINIC_ENABLED","1")
    assert dental_environment_ready() is False
    monkeypatch.setenv("DENTAL_DATA_KEY_B64","not-base64")
    assert dental_environment_ready() is False
    monkeypatch.setenv("DENTAL_DATA_KEY_B64",base64.b64encode(b"too-short").decode())
    assert dental_environment_ready() is False
    monkeypatch.setenv("DENTAL_DATA_KEY_B64",base64.b64encode(os.urandom(32)).decode())
    assert dental_environment_ready() is True
    monkeypatch.setenv("ORION_DENTAL_CLINIC_ENABLED","0")
    assert dental_environment_ready() is False


def test_api_startup_barrier_selects_clinical_schema_only_after_reviewed_release_flag(monkeypatch):
    from scripts import start_api
    called=[]
    def record(args, **kw):
        called.append((args, kw))
    monkeypatch.setattr(start_api.subprocess,"run",record)
    start_api.wait_for_campaigns_schema({"APP_DATABASE_URL":"synthetic"})
    assert called[0][0][-1] == "scripts.wait_for_fixed_client_monthly_schema"
    called.clear()
    start_api.wait_for_campaigns_schema({
        "APP_DATABASE_URL":"synthetic","ORION_DENTAL_0076_RELEASE_ENABLED":"1"})
    assert called[0][0][-1] == "scripts.wait_for_dental_schema"
    assert called[0][1]["check"] is True
    assert called[0][1]["timeout"] == 95
    assert "MIGRATION_DATABASE_URL" not in called[0][1]["env"]


def test_0076_schema_contract_keeps_0075_monthly_and_clinical_rls():
    from scripts import wait_for_dental_schema as schema
    assert schema.REVISION == "0076_dental_clinic"
    assert schema.MONTHLY_CONTRACT in schema.CONTRACT_QUERY
    assert "relforcerowsecurity" in schema.ROLE_CONTRACT
    assert "dental_room_reservations" in schema.APPEND_ONLY
    assert "NOT has_table_privilege('app_runtime',c.oid,'SELECT')" in schema.ROLE_CONTRACT
