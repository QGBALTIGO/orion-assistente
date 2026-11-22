"""Orion Dental — explicitly authorized clinical workspace, isolated from CRM/WhatsApp.
All health entries are AES-GCM encrypted, immutable and audited. No automated diagnosis
or legally-valid electronic signature is implied by storage in this module.
"""
import base64
import binascii
import hashlib
import json
import os
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.catalog_api import Actor, authenticate, support_session_factory
from app.account_business import account_membership_role
from app.database import get_session_factory, tenant_transaction
from app.plan_permissions import plan_state
from app.tenant_access import resolve_tenant_access
from app.dental_clinic_schemas import (
    ClinicianGrant, PatientRegistration, PatientSearch, PatientPatch, VisitPatientLink, RecordCreate,
    IntakeTemplateCreate, IntakeTemplateUpdate, LaboratoryCreate, LaboratoryUpdate,
    ProsthesisStatus, ProsthesisTransition, ProsthesisVisit,
    RoomCreate, RoomRename, RoomAssign, ChairAvailability, SupplyCreate, SupplyMove, ExamUpload
)


class PrivateJSON(JSONResponse):
    def __init__(self, content, status_code: int = 200, **kwargs):
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.update({"Cache-Control": "no-store, max-age=0",
                        "Pragma": "no-cache", "X-Content-Type-Options": "nosniff",
                        "Referrer-Policy": "no-referrer"})
        super().__init__(content, status_code=status_code, headers=headers, **kwargs)


class PrivateClinicalRoute(APIRoute):
    """Do not echo any submitted medical text in default Pydantic 422 errors."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def filtered(request):
            try:
                return await original(request)
            except RequestValidationError as exc:
                safe = [{"loc": list(error.get("loc", ())), "type": error.get("type", "value_error"),
                         "msg": "Verifique os campos informados."}
                        for error in exc.errors()]
                return PrivateJSON({"detail": safe}, status_code=422)

        return filtered


router = APIRouter(prefix="/api/v1/tenants/{tenant_id}/dental",
                   tags=["Odontologia — dados privados"], default_response_class=PrivateJSON,
                   route_class=PrivateClinicalRoute)


def key_material():
    try:
        value = base64.b64decode(os.environ.get("DENTAL_DATA_KEY_B64", ""), validate=True)
    except (binascii.Error, ValueError):
        value = b""
    if len(value) != 32:
        raise HTTPException(503, "Armazenamento clínico protegido ainda não está configurado")
    return value


def crypt_context(tenant, patient, kind, entry):
    return f"orion:dental:v1:{tenant}:{patient}:{kind}:{entry}".encode("ascii")


def seal(plaintext: bytes, tenant: UUID, patient: UUID, kind: str, entry: UUID):
    if not 0 < len(plaintext) < 120000:
        raise HTTPException(422, "Conteúdo clínico fora do tamanho permitido")
    nonce = secrets.token_bytes(12)
    encrypted = AESGCM(key_material()).encrypt(nonce, plaintext, crypt_context(tenant,patient,kind,entry))
    return nonce, encrypted, hashlib.sha256(plaintext).hexdigest()


def unseal(row, tenant: UUID, patient: UUID, kind: str):
    try:
        plaintext = AESGCM(key_material()).decrypt(bytes(row["nonce"]), bytes(row["encrypted_content"]),
            crypt_context(tenant,patient,kind,row["id"]))
        if hashlib.sha256(plaintext).hexdigest() != row["digest"]:
            raise InvalidTag("hash mismatch")
        return plaintext
    except (InvalidTag, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(503, "Não foi possível validar a integridade deste registro") from None


async def verify_dental_plan(db, tenant):
    state = await plan_state(db,tenant)
    if state.get("business_niche") != "odontologia" or state.get("platform_workspace"):
        raise HTTPException(404, "Módulo disponível apenas para clínicas odontológicas")
    if not state.get("dental_clinical_enabled"):
        raise HTTPException(503, "Área clínica em homologação: acesso não habilitado")
    if not state.get("active") or not state.get("permissions",{}).get("clients.manage"):
        raise HTTPException(403, "Acesso operacional indisponível para esta clínica")
    return state


def require_member(actor,tenant):
    if (actor.role != "member" or actor.maintenance or actor.tenant_id != tenant
            or not isinstance(actor.account_id,UUID)
            or actor.membership_role not in {"owner","editor","professional"}):
        raise HTTPException(403,"Acesso clínico restrito a membros autorizados da clínica")


@asynccontextmanager
async def verified_clinical_membership(tenant: UUID, actor: Actor, *, write=False):
    """Professional must hold the portal's live, exclusive professional link.

    The professional link remains under the original SHARE locks while the
    health operation commits; a caller-supplied tenant/professional is ignored.
    """
    require_member(actor, tenant)
    if actor.membership_role == "professional":
        from app.professional_portal import individual_access
        async with individual_access(actor) as (verified_tenant, verified_professional):
            if verified_tenant != tenant:
                raise HTTPException(403, "Profissional vinculado a outra clínica")
            yield "professional", verified_professional
    else:
        access = await resolve_tenant_access(
            actor, tenant, "draft" if write else "read")
        if access.role not in {"owner", "editor"} or access.account_id != actor.account_id:
            raise HTTPException(403, "Vínculo clínico não autorizado")
        yield access.role, None


@asynccontextmanager
async def clinical_session(tenant: UUID, actor: Actor, *, write=False, dentist=False):
    async with verified_clinical_membership(tenant, actor, write=write) as (role, linked_professional):
        async with get_session_factory()() as db:
            async with tenant_transaction(db,tenant):
                await verify_dental_plan(db,tenant)
                grant=(await db.execute(text("""
                    SELECT g.account_id,g.professional_id,g.level,g.cro_number,g.cro_state
                    FROM dental_clinicians g JOIN professionals p
                      ON p.tenant_id=g.tenant_id AND p.id=g.professional_id
                    WHERE g.tenant_id=:tenant AND g.account_id=:account
                      AND g.enabled AND p.active
                      AND (CAST(:linked_professional AS uuid) IS NULL
                           OR g.professional_id=:linked_professional)
                """),{"tenant":tenant,"account":actor.account_id,
                       "linked_professional":linked_professional})).mappings().one_or_none()
                if not grant:
                    raise HTTPException(403,"O proprietário precisa autorizar seu acesso clínico na equipe.")
                if dentist and grant["level"] != "dentist":
                    raise HTTPException(403,"Este registro exige um cirurgião-dentista autorizado.")
                await db.execute(text("SELECT set_config('app.dental_actor',:actor,true)"),
                                 {"actor":str(actor.account_id)})
                yield db, dict(grant)


@asynccontextmanager
async def owner_session(tenant: UUID, actor: Actor, *, feature=None):
    if (actor.role != "member" or actor.maintenance or actor.tenant_id != tenant
            or actor.membership_role != "owner" or not isinstance(actor.account_id,UUID)):
        raise HTTPException(403,"Somente o proprietário da clínica pode configurar acessos clínicos.")
    access=await resolve_tenant_access(actor,tenant,"publish")
    if access.role != "owner" or access.account_id != actor.account_id:
        raise HTTPException(403,"Vínculo do proprietário não confirmado")
    async with get_session_factory()() as db:
        async with tenant_transaction(db,tenant):
            plan = await verify_dental_plan(db, tenant)
            if feature and not plan["permissions"].get(feature):
                raise HTTPException(403, "O plano não permite configurar este recurso")
            await db.execute(text("SELECT set_config('app.dental_actor',:actor,true)"),
                             {"actor":str(actor.account_id)})
            yield db


async def audit(db,tenant,actor,action,patient=None):
    await db.execute(text("""
        INSERT INTO dental_access_audit(tenant_id,patient_id,actor_account_id,action)
        VALUES (:tenant,:patient,:actor,:action)
    """),{"tenant":tenant,"patient":patient,"actor":actor.account_id,"action":action})


async def patient_row(db,tenant,patient, *, lock=False):
    q="""SELECT id,name,birth_date,guardian_name,guardian_phone,contact_phone,
         customer_id,created_at,updated_at,version FROM dental_patients
         WHERE tenant_id=:tenant AND id=:patient"""
    result=(await db.execute(text(q+(" FOR UPDATE" if lock else "")),
                             {"tenant":tenant,"patient":patient})).mappings().one_or_none()
    if not result:
        raise HTTPException(404,"Paciente não encontrado nesta clínica")
    return dict(result)


async def reception_snapshot(db, tenant_id: UUID, as_of: datetime):
    zone_name = (await db.execute(text("SELECT timezone FROM tenants WHERE id=:tenant"),
                                  {"tenant": tenant_id})).scalar_one()
    zone = ZoneInfo(zone_name)
    day = as_of.astimezone(zone).date()
    start = datetime.combine(day, time.min, zone)
    end = datetime.combine(day + timedelta(days=1), time.min, zone)
    # Counts and the upcoming slots must describe the same committed agenda.
    row = (await db.execute(text("""
        WITH visits AS MATERIALIZED (
            SELECT a.status, a.start_at, a.end_at, a.professional_id, a.id
            FROM appointments a
            WHERE a.tenant_id=:tenant AND a.start_at>=:start AND a.start_at<:end
              AND a.status IN ('confirmed','completed','cancelled','no_show')
        ), upcoming AS (
            SELECT v.start_at, v.end_at, p.name AS professional, v.id
            FROM visits v JOIN professionals p ON p.tenant_id=:tenant AND p.id=v.professional_id
            WHERE v.status='confirmed' AND v.start_at>=:as_of
            ORDER BY v.start_at, v.id LIMIT 5
        )
        SELECT count(*) FILTER (WHERE status='confirmed') AS confirmed,
               count(*) FILTER (WHERE status='completed') AS completed,
               count(*) FILTER (WHERE status='cancelled') AS cancelled,
               count(*) FILTER (WHERE status='no_show') AS no_show,
               COALESCE((SELECT jsonb_agg(jsonb_build_object(
                   'start_at', start_at, 'end_at', end_at, 'professional', professional
               ) ORDER BY start_at,id) FROM upcoming), '[]'::jsonb) AS upcoming
        FROM visits
    """), {"tenant": tenant_id, "start": start, "end": end, "as_of": as_of})).mappings().one()
    return {"day": day, "timezone": zone_name, "as_of": as_of,
            "counts": {k: row[k] for k in ("confirmed","completed","cancelled","no_show")},
            "upcoming": row["upcoming"]}


@router.get("/reception/today")
async def reception_today(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    from app.management_api import management_session

    async with management_session(tenant_id, actor) as db:
        plan = await plan_state(db, tenant_id)
        if plan.get("business_niche") != "odontologia" or plan.get("platform_workspace"):
            raise HTTPException(404, "Recepção disponível apenas para clínicas odontológicas")
        if not plan.get("active") or not plan.get("permissions", {}).get("dashboard.basic"):
            raise HTTPException(403, "O acesso ao painel da clínica está indisponível")
        return await reception_snapshot(db, tenant_id, datetime.now(timezone.utc))


@router.get("/access")
async def dental_access(tenant_id: UUID,actor:Actor=Depends(authenticate)):
    async with verified_clinical_membership(tenant_id, actor) as (role, linked_professional):
        async with get_session_factory()() as db:
            async with tenant_transaction(db,tenant_id):
                await verify_dental_plan(db,tenant_id)
                row=(await db.execute(text("""
                    SELECT g.account_id,g.professional_id,g.level,g.cro_number,g.cro_state
                    FROM dental_clinicians g JOIN professionals p
                      ON p.tenant_id=g.tenant_id AND p.id=g.professional_id
                    WHERE g.tenant_id=:tenant AND g.account_id=:account
                      AND g.enabled AND p.active
                      AND (CAST(:linked_professional AS uuid) IS NULL
                           OR g.professional_id=:linked_professional)
                """),{"tenant":tenant_id,"account":actor.account_id,
                       "linked_professional":linked_professional})).mappings().first()
                grants=[]
                if role=="owner":
                    grants=[dict(x) for x in (await db.execute(text("""
                        SELECT g.account_id,g.professional_id,g.level,g.cro_number,g.cro_state,g.enabled
                        FROM dental_clinicians g
                        WHERE g.tenant_id=:tenant ORDER BY g.granted_at
                    """),{"tenant":tenant_id})).mappings().all()]
                return {"can_manage":role=="owner","account_id":actor.account_id,
                        "clinical_access":dict(row) if row else None,"grants":grants,
                        "can_manage_inventory":role=="owner" or row is not None,
                        "can_read_records":bool(row and row["level"] == "dentist"),
                        "data_encryption_configured":bool(os.environ.get("DENTAL_DATA_KEY_B64")),
                        "electronic_signatures_homologated":False}


@router.put("/clinicians")
async def set_clinician(tenant_id: UUID,payload:ClinicianGrant,actor:Actor=Depends(authenticate)):
    # Recipient must have a currently verified account membership, never just an email.
    async with owner_session(tenant_id,actor) as db:
        async with support_session_factory()() as support:
            member=await account_membership_role(support,payload.account_id,tenant_id)
            if member=="professional":
                verified=(await support.execute(text("""
                    SELECT professional_id FROM professional_portal_links
                    WHERE tenant_id=:tenant AND account_id=:account AND active
                """),{"tenant":tenant_id,"account":payload.account_id})).scalar_one_or_none()
                if verified != payload.professional_id or payload.level != "dentist":
                    raise HTTPException(422,
                        "O dentista deve ter vínculo profissional ativo com o mesmo cadastro")
            elif member not in {"owner","editor"}:
                raise HTTPException(422,"Vincule a conta à equipe autorizada da clínica primeiro")
        known=(await db.execute(text("""
            SELECT id FROM professionals WHERE tenant_id=:tenant AND id=:professional AND active
        """),{"tenant":tenant_id,"professional":payload.professional_id})).scalar_one_or_none()
        if not known: raise HTTPException(404,"Profissional não cadastrado nesta clínica")
        await db.execute(text("""
            INSERT INTO dental_clinicians
             (tenant_id,account_id,professional_id,level,cro_number,cro_state,granted_by,enabled)
            VALUES (:tenant,:account,:professional,:level,:cro,:uf,:owner,true)
            ON CONFLICT(tenant_id,account_id) DO UPDATE SET
               professional_id=excluded.professional_id,level=excluded.level,
               cro_number=excluded.cro_number,cro_state=excluded.cro_state,
               granted_by=excluded.granted_by,granted_at=clock_timestamp(),enabled=true
        """),{"tenant":tenant_id,"account":payload.account_id,
               "professional":payload.professional_id,"level":payload.level,
               "cro":payload.cro_number,"uf":payload.cro_state,"owner":actor.account_id})
        await audit(db,tenant_id,actor,"clinical_access_granted")
        return {"saved":True,"account_id":payload.account_id}


@router.delete("/clinicians/{account_id}")
async def revoke_clinician(tenant_id:UUID,account_id:UUID,actor:Actor=Depends(authenticate)):
    async with owner_session(tenant_id,actor) as db:
        changed=(await db.execute(text("""
            UPDATE dental_clinicians SET enabled=false
            WHERE tenant_id=:tenant AND account_id=:account AND enabled
            RETURNING account_id
        """),{"tenant":tenant_id,"account":account_id})).scalar_one_or_none()
        if not changed:raise HTTPException(404,"Acesso clínico não encontrado")
        await audit(db,tenant_id,actor,"clinical_access_revoked")
        return {"revoked":True}


@router.get("/patients")
async def list_dental_patients(tenant_id:UUID,q:str=Query("",max_length=120),
                               limit:int=Query(40,ge=1,le=100),
                               offset:int=Query(0,ge=0),actor:Actor=Depends(authenticate)):
    async with clinical_session(tenant_id,actor) as (db,grant):
        query=q.strip()
        safe="%"+query.replace("\\","\\\\").replace("%","\\%").replace("_","\\_")+"%"
        params={"tenant":tenant_id,"q":safe,"limit":limit,"offset":offset}
        result=(await db.execute(text("""
          SELECT id,name,birth_date,guardian_name,contact_phone,created_at,version
          FROM dental_patients WHERE tenant_id=:tenant AND name ILIKE :q ESCAPE '\\'
          ORDER BY name,id LIMIT :limit OFFSET :offset
        """),params)).mappings().all()
        count=(await db.execute(text("""
          SELECT count(*) FROM dental_patients
          WHERE tenant_id=:tenant AND name ILIKE :q ESCAPE '\\'
        """),params)).scalar_one()
        await audit(db,tenant_id,actor,"patient_directory_read")
        return {"items":[dict(x) for x in result],"total":count}


@router.post("/patients/search")
async def search_patient_directory(tenant_id: UUID, payload: PatientSearch, actor: Actor = Depends(authenticate)):
    async with clinical_session(tenant_id, actor) as (db, grant):
        term = payload.query
        digits = "".join(c for c in term if c in "0123456789")
        search_phone = len(digits) >= 4 and all(c in "+0123456789 ()-" for c in term)
        if search_phone:
            where = """(regexp_replace(coalesce(contact_phone,''), '[^0-9]', '', 'g') LIKE :term
                       OR regexp_replace(coalesce(guardian_phone,''), '[^0-9]', '', 'g') LIKE :term)"""
            pattern = f"%{digits}%"
        else:
            where = "name ILIKE :term ESCAPE '!'"
            pattern = "%" + term.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        result = (await db.execute(text(f"""
            SELECT id, name, birth_date, guardian_name
            FROM dental_patients WHERE tenant_id=:tenant AND {where}
            ORDER BY lower(name), id LIMIT :limit OFFSET :offset
        """), {"tenant":tenant_id, "term":pattern, "limit":payload.limit + 1,
               "offset":payload.offset})).mappings().all()
        await audit(db, tenant_id, actor, "patient_directory_read")
        return {"items":[dict(item) for item in result[:payload.limit]],
                "has_more":len(result)>payload.limit}


@router.post("/patients",status_code=201)
async def create_dental_patient(tenant_id:UUID,payload:PatientRegistration,actor:Actor=Depends(authenticate)):
    async with clinical_session(tenant_id,actor,write=True) as (db,grant):
        if payload.customer_id is not None:
            ok=(await db.execute(text("""
                SELECT id FROM customers WHERE tenant_id=:tenant AND id=:customer AND deleted_at IS NULL
            """),{"tenant":tenant_id,"customer":payload.customer_id})).scalar_one_or_none()
            if not ok:raise HTTPException(404,"Cliente administrativo não pertence a esta clínica")
        identity = uuid5(tenant_id, f"register:{actor.account_id}:{payload.request_id}") if payload.request_id else uuid4()
        inserted = (await db.execute(text("""
            INSERT INTO dental_patients
             (id,tenant_id,customer_id,name,birth_date,guardian_name,guardian_phone,
              contact_phone,created_by)
            VALUES (:id,:tenant,:customer,:name,:birth,:guardian,:guardian_phone,:phone,:actor)
            ON CONFLICT (id) DO NOTHING RETURNING id
        """),{"id":identity,"tenant":tenant_id,"customer":payload.customer_id,
               "name":payload.name,"birth":payload.birth_date,
               "guardian":payload.guardian_name,"guardian_phone":payload.guardian_phone,
               "phone":payload.contact_phone,"actor":actor.account_id})).scalar_one_or_none()
        patient = await patient_row(db,tenant_id,identity)
        if inserted is None:
            original = payload.model_dump(exclude={"request_id"})
            if any(patient[key] != value for key, value in original.items()):
                raise HTTPException(409, "Este cadastro já foi salvo com outros dados. Consulte o paciente antes de continuar.")
        else:
            await audit(db,tenant_id,actor,"patient_created",identity)
        return patient


@router.get("/patients/{patient_id}")
async def dental_patient_detail(tenant_id:UUID,patient_id:UUID,actor:Actor=Depends(authenticate)):
    async with clinical_session(tenant_id,actor) as (db,grant):
        row=await patient_row(db,tenant_id,patient_id)
        await audit(db,tenant_id,actor,"patient_read",patient_id)
        return row


@router.patch("/patients/{patient_id}")
async def update_dental_patient(tenant_id:UUID,patient_id:UUID,payload:PatientPatch,
                                actor:Actor=Depends(authenticate)):
    async with clinical_session(tenant_id,actor,write=True) as (db,grant):
        old=await patient_row(db,tenant_id,patient_id,lock=True)
        if payload.expected_version != old["version"]:
            raise HTTPException(409,"Cadastro foi alterado. Atualize antes de salvar")
        if payload.customer_id is not None:
            ok=(await db.execute(text("""
               SELECT id FROM customers WHERE tenant_id=:tenant AND id=:customer AND deleted_at IS NULL
            """),{"tenant":tenant_id,"customer":payload.customer_id})).scalar_one_or_none()
            if not ok:raise HTTPException(404,"Cliente administrativo indisponível nesta clínica")
        await db.execute(text("""
            UPDATE dental_patients SET name=:name,birth_date=:birth,guardian_name=:guardian,
              guardian_phone=:guardian_phone,contact_phone=:phone,customer_id=:customer,
              version=version+1,updated_at=clock_timestamp()
            WHERE tenant_id=:tenant AND id=:id
        """),{"tenant":tenant_id,"id":patient_id,"name":payload.name,
               "birth":payload.birth_date,"guardian":payload.guardian_name,
               "guardian_phone":payload.guardian_phone,"phone":payload.contact_phone,
               "customer":payload.customer_id})
        await audit(db,tenant_id,actor,"patient_updated",patient_id)
        return await patient_row(db,tenant_id,patient_id)


@router.get("/patients/{patient_id}/records")
async def list_dental_records(tenant_id:UUID,patient_id:UUID,
                              kind:str|None=Query(None,max_length=30),
                              limit:int=Query(40,ge=1,le=100),
                              actor:Actor=Depends(authenticate)):
    async with clinical_session(tenant_id,actor,dentist=True) as (db,grant):
        await patient_row(db,tenant_id,patient_id)
        if kind and kind not in (
            "anamnesis","odontogram","periodontal","progress","treatment","consent",
            "budget","insurance","prosthesis","exam_note","certificate","referral"):
            raise HTTPException(422,"Tipo de registro inválido")
        records=(await db.execute(text("""
            SELECT id,kind,encrypted_content,nonce,digest,authored_by,professional_id,created_at,
                   previous_prosthesis_id
            FROM dental_records WHERE tenant_id=:tenant AND patient_id=:patient
              AND (CAST(:kind AS varchar) IS NULL OR kind=:kind)
            ORDER BY created_at DESC,id DESC LIMIT :limit
        """),{"tenant":tenant_id,"patient":patient_id,"kind":kind,"limit":limit})).mappings().all()
        result=[]
        for row in records:
            data=json.loads(unseal(row,tenant_id,patient_id,row["kind"]))
            if row["kind"] == "prosthesis":
                data = public_prosthesis_content(data)
            result.append({"id":row["id"],"kind":row["kind"],"content":data,
                           "authored_by":row["authored_by"],"professional_id":row["professional_id"],
                           "created_at":row["created_at"]})
            if row["previous_prosthesis_id"] is not None:
                result[-1]["previous_prosthesis_id"] = row["previous_prosthesis_id"]
        await audit(db,tenant_id,actor,"clinical_records_read",patient_id)
        return {"items":result}


async def resolve_intake_snapshot(db, tenant, patient, actor, payload):
    content = dict(payload.content)
    submission = content.pop("intake_template")
    prior = (await db.execute(text("""
        SELECT id,patient_id,authored_by,kind,encrypted_content,nonce,digest FROM dental_records
        WHERE tenant_id=:tenant AND entry_key=:key
    """), {"tenant": tenant, "key": payload.entry_key})).mappings().one_or_none()
    if prior:
        if prior["patient_id"] != patient or prior["authored_by"] != actor.account_id or prior["kind"] != "anamnesis":
            raise HTTPException(409, "Chave de registro utilizada para outro conteúdo")
        stored = json.loads(unseal(prior, tenant, patient, "anamnesis"))
        snapshot = stored.pop("intake_template", None)
        if (not snapshot or stored != content or snapshot["template_id"] != submission["template_id"]
                or snapshot["version"] != submission["version"]
                or [item["answer"] for item in snapshot["responses"]] != submission["answers"]):
            raise HTTPException(409, "Chave de registro utilizada para outro conteúdo")
        return prior["id"], None

    template = (await db.execute(text("""
        SELECT name,version,questions FROM dental_intake_templates
        WHERE tenant_id=:tenant AND id=CAST(:id AS uuid) FOR SHARE
    """), {"tenant": tenant, "id": submission["template_id"]})).mappings().one_or_none()
    if not template:
        raise HTTPException(404, "Modelo de anamnese não encontrado")
    if template["version"] != submission["version"]:
        raise HTTPException(409, "O modelo mudou. Reabra a anamnese antes de registrar as respostas.")
    prompts = [question["prompt"] for question in template["questions"] if question["active"]]
    answers = submission["answers"]
    if len(answers) != len(prompts):
        raise HTTPException(422, "Confira as respostas das perguntas ativas")
    content["intake_template"] = {
        "template_id": submission["template_id"], "name": template["name"], "version": template["version"],
        "responses": [{"prompt": prompt, "answer": answer} for prompt, answer in zip(prompts, answers)],
    }
    return None, content


async def save_clinical_record(db, tenant, patient, actor, professional, kind, content, key, *,
                               previous=None, action="clinical_record_created"):
    identity = uuid4()
    data = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    nonce, cipher, digest = seal(data, tenant, patient, kind, identity)
    inserted = (await db.execute(text("""
        INSERT INTO dental_records
          (id,tenant_id,patient_id,kind,encrypted_content,nonce,digest,
           authored_by,professional_id,entry_key,previous_prosthesis_id)
        VALUES (:id,:tenant,:patient,:kind,:content,:nonce,:digest,:actor,:professional,:key,:previous)
        ON CONFLICT(tenant_id,entry_key) DO NOTHING RETURNING id
    """), {"id": identity, "tenant": tenant, "patient": patient, "kind": kind,
           "content": cipher, "nonce": nonce, "digest": digest, "actor": actor.account_id,
           "professional": professional, "key": key, "previous": previous})).scalar_one_or_none()
    if inserted is not None:
        await audit(db, tenant, actor, action, patient)
        return inserted
    prior = (await db.execute(text("""
        SELECT id,patient_id,kind,digest,authored_by,previous_prosthesis_id FROM dental_records
        WHERE tenant_id=:tenant AND entry_key=:key
    """), {"tenant": tenant, "key": key})).mappings().one()
    if (prior["patient_id"] != patient or prior["kind"] != kind or prior["digest"] != digest
            or prior["authored_by"] != actor.account_id or prior["previous_prosthesis_id"] != previous):
        raise HTTPException(409, "Chave de registro utilizada para outro conteúdo")
    return prior["id"]


@router.post("/patients/{patient_id}/records", status_code=201)
async def append_dental_record(tenant_id: UUID, patient_id: UUID, payload: RecordCreate,
                               actor: Actor = Depends(authenticate)):
    async with clinical_session(tenant_id, actor, write=True, dentist=True) as (db, grant):
        await patient_row(db, tenant_id, patient_id)
        identity, content = None, payload.content
        if payload.kind == "anamnesis" and "intake_template" in content:
            identity, content = await resolve_intake_snapshot(db, tenant_id, patient_id, actor, payload)
        if identity is None:
            identity = await save_clinical_record(db, tenant_id, patient_id, actor, grant["professional_id"],
                                                  payload.kind, content, payload.entry_key)
        return {"id": identity, "patient_id": patient_id, "kind": payload.kind,
                "recorded": True, "qualified_signature": False}


PROSTHESIS_TRANSITIONS = {
    "requested": ("ready_to_send", "cancelled"),
    "ready_to_send": ("working", "requested", "cancelled"),
    "working": ("delivered", "cancelled"),
    "delivered": ("completed", "working", "cancelled"),
}


def public_prosthesis_content(content):
    # An appointment may later be transferred. Resolve its identity and details at the point of access.
    details = dict(content)
    visit = details.pop("visit", None)
    if visit:
        details["visit_purpose"] = visit["purpose"]
    return details


@router.get("/prostheses")
async def prostheses(tenant_id: UUID, actor: Actor = Depends(authenticate),
                     cursor: UUID | None = None, status: ProsthesisStatus | None = None,
                     limit: int = Query(30, ge=1, le=50), overdue_only: bool = False):
    async with clinical_session(tenant_id, actor, dentist=True) as (db, _):
        clinic_day = await db.scalar(text("""
            SELECT (CURRENT_TIMESTAMP AT TIME ZONE timezone)::date FROM tenants WHERE id=:tenant
        """), {"tenant": tenant_id})
        # A prior version remains a valid page boundary after a concurrent status change.
        boundary = None
        if cursor is not None:
            boundary = await db.scalar(text("""
                SELECT created_at FROM dental_records
                WHERE tenant_id=:tenant AND id=:cursor AND kind='prosthesis'
            """), {"tenant": tenant_id, "cursor": cursor})
            if boundary is None:
                raise HTTPException(400, "Página de próteses inválida")
        scan_limit = 200 if status is not None or overdue_only else limit
        rows = (await db.execute(text("""
            SELECT r.id,r.patient_id,r.encrypted_content,r.nonce,r.digest,r.created_at,p.name AS patient_name
            FROM dental_records r JOIN dental_patients p ON p.tenant_id=r.tenant_id AND p.id=r.patient_id
            WHERE r.tenant_id=:tenant AND r.kind='prosthesis'
              AND (CAST(:before AS timestamptz) IS NULL OR (r.created_at,r.id)<(:before,:cursor))
              AND NOT EXISTS (
                SELECT 1 FROM dental_records newer
                WHERE newer.tenant_id=r.tenant_id AND newer.previous_prosthesis_id=r.id
              )
            ORDER BY r.created_at DESC,r.id DESC LIMIT :limit
        """), {"tenant": tenant_id, "before": boundary, "cursor": cursor,
               "limit": scan_limit + 1})).mappings().all()
        items = []
        checked = 0
        for record in rows[:scan_limit]:
            checked += 1
            content = json.loads(unseal(record, tenant_id, record["patient_id"], "prosthesis"))
            if status is not None and content["status"] != status:
                continue
            deadline_state = None
            if content.get("due_date") and content["status"] not in ("completed", "cancelled"):
                due = date.fromisoformat(content["due_date"])
                if due < clinic_day:
                    deadline_state = "overdue"
                elif due == clinic_day:
                    deadline_state = "today"
                else:
                    deadline_state = "upcoming"
            if overdue_only and deadline_state != "overdue":
                continue
            items.append({"id": record["id"], "patient_id": record["patient_id"],
                          "patient_name": record["patient_name"], "created_at": record["created_at"],
                          "deadline_state": deadline_state,
                          "content": public_prosthesis_content(content),
                          "actions": PROSTHESIS_TRANSITIONS.get(content["status"], ())})
            if len(items) == limit:
                break
        await audit(db, tenant_id, actor, "prosthesis_worklist_read")
        return {"items": items, "clinic_day": clinic_day,
                "next_cursor": rows[checked - 1]["id"] if len(rows) > checked else None}


async def read_prosthesis(db, tenant, record_id):
    record = (await db.execute(text("""
        SELECT id,patient_id,encrypted_content,nonce,digest FROM dental_records
        WHERE tenant_id=:tenant AND id=:id AND kind='prosthesis'
    """), {"tenant": tenant, "id": record_id})).mappings().one_or_none()
    if record is None:
        raise HTTPException(404, "Trabalho protético não encontrado")
    patient = record["patient_id"]
    return patient, json.loads(unseal(record, tenant, patient, "prosthesis"))


async def lock_prosthesis(db, tenant, record_id, key):
    # Both agenda and status mutations append to this immutable version.
    await db.execute(text("""
        SELECT pg_advisory_xact_lock(hashtextextended('orion-prosthesis:' || CAST(:id AS text), 0))
    """), {"id": str(record_id)})
    successor = await db.scalar(text("""
        SELECT entry_key FROM dental_records WHERE tenant_id=:tenant AND previous_prosthesis_id=:id
    """), {"tenant": tenant, "id": record_id})
    if successor is not None and successor != key:
        raise HTTPException(409, "O trabalho já mudou. Consulte a situação atual.")
    return successor is not None


@router.patch("/prostheses/{record_id}/status")
async def advance_prosthesis(tenant_id: UUID, record_id: UUID, payload: ProsthesisTransition,
                            actor: Actor = Depends(authenticate)):
    async with clinical_session(tenant_id, actor, write=True, dentist=True) as (db, grant):
        patient, content = await read_prosthesis(db, tenant_id, record_id)
        await lock_prosthesis(db, tenant_id, record_id, payload.entry_key)
        if payload.status not in PROSTHESIS_TRANSITIONS.get(content["status"], ()):
            raise HTTPException(409, "Essa mudança de etapa não está disponível")
        next_content = {**content, "status": payload.status}
        identity = await save_clinical_record(db, tenant_id, patient, actor, grant["professional_id"],
                                              "prosthesis", next_content, payload.entry_key, previous=record_id,
                                              action="prosthesis_status_changed")
        return {"id": identity, "status": payload.status}


@router.get("/laboratories")
async def dental_laboratories(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    async with clinical_session(tenant_id, actor, dentist=True) as (db, _):
        rows = (await db.execute(text("""
            SELECT id,name,phone,active,version FROM dental_laboratories
            WHERE tenant_id=:tenant ORDER BY lower(name),id
        """), {"tenant": tenant_id})).mappings().all()
        return {"items": [dict(row) for row in rows]}


@router.post("/laboratories", status_code=201)
async def create_laboratory(tenant_id: UUID, payload: LaboratoryCreate, actor: Actor = Depends(authenticate)):
    async with clinical_session(tenant_id, actor, write=True, dentist=True) as (db, _):
        try:
            async with db.begin_nested():
                saved = (await db.execute(text("""
                    INSERT INTO dental_laboratories (tenant_id,name,phone)
                    VALUES (:tenant,:name,:phone) RETURNING id,name,phone,active,version
                """), {"tenant": tenant_id, **payload.model_dump()})).mappings().one()
        except IntegrityError:
            raise HTTPException(409, "Já existe um laboratório com esse nome") from None
        await audit(db, tenant_id, actor, "laboratory_created")
        return dict(saved)


@router.patch("/laboratories/{laboratory_id}")
async def edit_laboratory(tenant_id: UUID, laboratory_id: UUID, payload: LaboratoryUpdate,
                          actor: Actor = Depends(authenticate)):
    async with clinical_session(tenant_id, actor, write=True, dentist=True) as (db, _):
        scope = {"tenant": tenant_id, "id": laboratory_id}
        current = (await db.execute(text("""
            SELECT id,name,phone,active,version FROM dental_laboratories
            WHERE tenant_id=:tenant AND id=:id FOR UPDATE
        """), scope)).mappings().one_or_none()
        if current is None:
            raise HTTPException(404, "Laboratório não encontrado")
        changes = payload.model_dump(exclude={"expected_version"})
        if all(current[key] == value for key, value in changes.items()):
            return dict(current)
        if current["version"] != payload.expected_version:
            raise HTTPException(409, "O cadastro mudou. Confira a versão atual.")
        try:
            async with db.begin_nested():
                saved = (await db.execute(text("""
                    UPDATE dental_laboratories SET name=:name,phone=:phone,active=:active,version=version+1
                    WHERE tenant_id=:tenant AND id=:id
                    RETURNING id,name,phone,active,version
                """), {**scope, **changes})).mappings().one()
        except IntegrityError:
            raise HTTPException(409, "Já existe um laboratório com esse nome") from None
        await audit(db, tenant_id, actor, "laboratory_updated")
        return dict(saved)


async def read_chairs(db, tenant):
    rows = (await db.execute(text("""
        SELECT id,name,active FROM dental_rooms WHERE tenant_id=:tenant
        ORDER BY lower(name),id
    """), {"tenant": tenant})).mappings().all()
    return {"items": [dict(row) for row in rows]}


async def create_chair(db, tenant, actor, name):
    try:
        async with db.begin_nested():
            chair = (await db.execute(text("""
                INSERT INTO dental_rooms (tenant_id,name) VALUES (:tenant,:name)
                RETURNING id,name,active
            """), {"tenant": tenant, "name": name})).mappings().one()
    except IntegrityError:
        raise HTTPException(409, "Já existe uma cadeira com esse nome") from None
    await audit(db, tenant, actor, "room_created")
    return dict(chair)


@router.get("/rooms")
async def dental_rooms(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    async with clinical_session(tenant_id, actor) as (db, _):
        return await read_chairs(db, tenant_id)


@router.post("/rooms", status_code=201)
async def add_dental_room(tenant_id: UUID, payload: RoomCreate, actor: Actor = Depends(authenticate)):
    async with clinical_session(tenant_id, actor, write=True) as (db, _):
        return await create_chair(db, tenant_id, actor, payload.name)


@router.get("/chair-settings")
async def list_chair_settings(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    async with owner_session(tenant_id, actor, feature="settings.manage") as db:
        return await read_chairs(db, tenant_id)


@router.post("/chair-settings", status_code=201)
async def create_chair_settings(tenant_id: UUID, payload: RoomCreate, actor: Actor = Depends(authenticate)):
    async with owner_session(tenant_id, actor, feature="settings.manage") as db:
        return await create_chair(db, tenant_id, actor, payload.name)


async def chair_for_update(db, tenant, room_id):
    chair = (await db.execute(text("""
        SELECT id,name,active FROM dental_rooms
        WHERE tenant_id=:tenant AND id=:id FOR UPDATE
    """), {"tenant": tenant, "id": room_id})).mappings().one_or_none()
    if not chair:
        raise HTTPException(404, "Cadeira não encontrada nesta clínica")
    return chair


@router.patch("/chair-settings/{room_id}")
async def rename_chair_settings(
    tenant_id: UUID, room_id: UUID, payload: RoomRename, actor: Actor = Depends(authenticate)
):
    async with owner_session(tenant_id, actor, feature="settings.manage") as db:
        params = {"tenant": tenant_id, "id": room_id, "name": payload.name}
        chair = await chair_for_update(db, tenant_id, room_id)
        if chair["name"] == payload.name:
            return dict(chair)
        if chair["name"] != payload.expected_name:
            raise HTTPException(409, "O nome da cadeira mudou. Reabra o cadastro para conferir.")
        try:
            async with db.begin_nested():
                updated = (await db.execute(text("""
                    UPDATE dental_rooms SET name=:name WHERE tenant_id=:tenant AND id=:id
                    RETURNING id,name,active
                """), params)).mappings().one()
        except IntegrityError:
            raise HTTPException(409, "Já existe uma cadeira com esse nome") from None
        await audit(db, tenant_id, actor, "room_renamed")
        return dict(updated)


@router.patch("/chair-settings/{room_id}/availability")
async def change_chair_availability(
    tenant_id: UUID, room_id: UUID, payload: ChairAvailability, actor: Actor = Depends(authenticate)
):
    async with owner_session(tenant_id, actor, feature="settings.manage") as db:
        chair = await chair_for_update(db, tenant_id, room_id)
        if chair["active"] == payload.active:
            return dict(chair)
        params = {"tenant": tenant_id, "id": room_id, "active": payload.active}
        if not payload.active:
            occupied = (await db.execute(text("""
                SELECT EXISTS (
                    SELECT 1 FROM dental_room_reservations r JOIN appointments a
                      ON a.tenant_id=r.tenant_id AND a.id=r.appointment_id
                    WHERE r.tenant_id=:tenant AND r.room_id=:id AND r.active
                      AND (a.status='confirmed' OR a.status='hold' AND a.hold_expires_at>clock_timestamp())
                )
            """), params)).scalar_one()
            if occupied:
                raise HTTPException(409, "A cadeira tem consultas em aberto. Libere essas reservas antes de inativá-la.")
        updated = (await db.execute(text("""
            UPDATE dental_rooms SET active=:active WHERE tenant_id=:tenant AND id=:id
            RETURNING id,name,active
        """), params)).mappings().one()
        await audit(db, tenant_id, actor, "room_activated" if payload.active else "room_inactivated")
        return dict(updated)


@router.get("/intake-templates")
async def list_intake_templates(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    async with owner_session(tenant_id, actor, feature="settings.manage") as db:
        templates = (await db.execute(text("""
            SELECT id,name,questions,version FROM dental_intake_templates
            WHERE tenant_id=:tenant ORDER BY lower(name),id
        """), {"tenant": tenant_id})).mappings().all()
        return {"items": [dict(template) for template in templates]}


@router.get("/intake-templates/clinical")
async def clinical_intake_templates(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    async with clinical_session(tenant_id, actor, dentist=True) as (db, _):
        templates = (await db.execute(text("""
            SELECT id,name,questions,version FROM dental_intake_templates
            WHERE tenant_id=:tenant ORDER BY lower(name),id
        """), {"tenant": tenant_id})).mappings().all()
        return {"items": [
            {**template, "questions": [q["prompt"] for q in template["questions"] if q["active"]]}
            for template in templates
        ]}


@router.post("/intake-templates", status_code=201)
async def create_intake_template(
    tenant_id: UUID, payload: IntakeTemplateCreate, actor: Actor = Depends(authenticate)
):
    async with owner_session(tenant_id, actor, feature="settings.manage") as db:
        values = {"tenant": tenant_id, "name": payload.name,
                  "questions": json.dumps(payload.model_dump(mode="json")["questions"])}
        try:
            async with db.begin_nested():
                created = (await db.execute(text("""
                    INSERT INTO dental_intake_templates (tenant_id,name,questions)
                    VALUES (:tenant,:name,CAST(:questions AS jsonb))
                    RETURNING id,name,questions,version
                """), values)).mappings().one()
        except IntegrityError:
            raise HTTPException(409, "Já existe um modelo com esse nome") from None
        await audit(db, tenant_id, actor, "intake_template_created")
        return dict(created)


@router.patch("/intake-templates/{template_id}")
async def update_intake_template(
    tenant_id: UUID, template_id: UUID, payload: IntakeTemplateUpdate,
    actor: Actor = Depends(authenticate)
):
    async with owner_session(tenant_id, actor, feature="settings.manage") as db:
        values = {"tenant": tenant_id, "id": template_id}
        current = (await db.execute(text("""
            SELECT id,name,questions,version FROM dental_intake_templates
            WHERE tenant_id=:tenant AND id=:id FOR UPDATE
        """), values)).mappings().one_or_none()
        if not current:
            raise HTTPException(404, "Modelo de anamnese não encontrado")
        questions = payload.model_dump(mode="json")["questions"]
        if current["name"] == payload.name and current["questions"] == questions:
            return dict(current)
        if current["version"] != payload.expected_version:
            raise HTTPException(409, "O modelo mudou. Reabra a edição para conferir.")
        try:
            async with db.begin_nested():
                updated = (await db.execute(text("""
                    UPDATE dental_intake_templates
                    SET name=:name,questions=CAST(:questions AS jsonb),version=version+1
                    WHERE tenant_id=:tenant AND id=:id
                    RETURNING id,name,questions,version
                """), {**values, "name": payload.name, "questions": json.dumps(questions)})).mappings().one()
        except IntegrityError:
            raise HTTPException(409, "Já existe um modelo com esse nome") from None
        await audit(db, tenant_id, actor, "intake_template_updated")
        return dict(updated)


# The booking contact belongs to the shared scheduler. A patient belongs to the
# clinic and is linked only by an explicit, versioned choice from its directory.
CURRENT_VISIT_PATIENT = """
    LEFT JOIN LATERAL (
        SELECT e.patient_id, e.version FROM dental_visit_patient_events e
        WHERE e.tenant_id=a.tenant_id AND e.appointment_id=a.id
        ORDER BY e.version DESC LIMIT 1
    ) link ON true
"""
VISIT_PROFESSIONAL = " AND (CAST(:professional AS uuid) IS NULL OR a.professional_id=:professional)"


def visit_professional(actor, grant):
    return grant["professional_id"] if actor.membership_role == "professional" else None


@router.get("/prostheses/{record_id}/visits")
async def prosthesis_visits(tenant_id: UUID, record_id: UUID, actor: Actor = Depends(authenticate),
                           limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0)):
    async with clinical_session(tenant_id, actor, dentist=True) as (db, grant):
        patient, content = await read_prosthesis(db, tenant_id, record_id)
        stored = content.get("visit")
        params = {"tenant":tenant_id, "patient":patient, "professional":visit_professional(actor, grant),
                  "linked":UUID(stored["appointment_id"]) if stored else None,
                  "identity_version":stored["identity_version"] if stored else None,
                  "limit":limit+1, "offset":offset}
        visits = """
            SELECT a.id,a.start_at,a.status,p.name AS professional,link.version AS identity_version
            FROM appointments a JOIN professionals p ON p.tenant_id=a.tenant_id AND p.id=a.professional_id
        """ + CURRENT_VISIT_PATIENT + """
            WHERE a.tenant_id=:tenant AND link.patient_id=:patient
        """ + VISIT_PROFESSIONAL
        linked = None
        if stored:
            linked = (await db.execute(text(visits + """
                AND a.id=:linked AND link.version=:identity_version
            """),params)).mappings().one_or_none()
        options = []
        if content["status"] == "delivered":
            options = (await db.execute(text(visits + """
                AND a.status='confirmed' AND a.start_at>=CURRENT_TIMESTAMP
                ORDER BY a.start_at,a.id LIMIT :limit OFFSET :offset
            """),params)).mappings().all()
        zone = await db.scalar(text("SELECT timezone FROM tenants WHERE id=:tenant"), params)
        await audit(db, tenant_id, actor, "prosthesis_visits_read", patient)
        return {"items":[dict(item) for item in options[:limit]], "has_more":len(options)>limit,
                "linked":dict(linked) if linked else None, "purpose":stored["purpose"] if stored else None,
                "timezone":zone}


@router.patch("/prostheses/{record_id}/visit")
async def set_prosthesis_visit(tenant_id: UUID, record_id: UUID, payload: ProsthesisVisit,
                              actor: Actor = Depends(authenticate)):
    async with clinical_session(tenant_id, actor, write=True, dentist=True) as (db, grant):
        patient, content = await read_prosthesis(db, tenant_id, record_id)
        if content["status"] != "delivered":
            raise HTTPException(409, "Registre o recebimento da peça antes de vincular uma consulta.")
        replay = await lock_prosthesis(db, tenant_id, record_id, payload.entry_key)
        old = content.get("visit")
        chosen = None
        if payload.appointment_id:
            chosen = {"appointment_id":str(payload.appointment_id), "identity_version":payload.identity_version,
                      "purpose":payload.purpose}
        professional = visit_professional(actor, grant)
        old_appointment = UUID(old["appointment_id"]) if old and professional is not None else None
        appointment_ids = {value for value in (old_appointment, payload.appointment_id) if value}
        if appointment_ids and not replay:
            # All writers lock the actual booking. A fixed order also permits two concurrent replacements.
            locked = (await db.execute(text("""
                SELECT a.id,(a.status='confirmed' AND a.start_at>=CURRENT_TIMESTAMP) AS eligible
                FROM appointments a
                WHERE a.tenant_id=:tenant AND a.id=ANY(CAST(:ids AS uuid[]))
            """ + VISIT_PROFESSIONAL + " ORDER BY a.id FOR UPDATE"),
                {"tenant":tenant_id,"ids":list(appointment_ids),"professional":professional})).all()
            eligible = dict(locked)
            if old_appointment and old_appointment not in eligible:
                raise HTTPException(403, "O vínculo existente pertence a outra agenda profissional.")
            if chosen and not eligible.get(payload.appointment_id):
                raise HTTPException(404, "Consulta confirmada indisponível para este paciente.")
        if chosen and not replay:
            # A separate statement observes the identity committed by any reassignment that held the lock.
            identity = (await db.execute(text("""
                SELECT patient_id,version FROM dental_visit_patient_events
                WHERE tenant_id=:tenant AND appointment_id=:appointment ORDER BY version DESC LIMIT 1
            """),{"tenant":tenant_id,"appointment":payload.appointment_id})).mappings().one_or_none()
            if not identity or identity["patient_id"] != patient:
                raise HTTPException(404, "Consulta confirmada indisponível para este paciente.")
            if identity["version"] != payload.identity_version:
                raise HTTPException(409, "A identificação da consulta mudou. Consulte a agenda novamente.")
        if chosen == old and not replay:
            return {"id":record_id}
        if chosen is None:
            content.pop("visit",None)
        else:
            content["visit"] = chosen
        saved = await save_clinical_record(db, tenant_id, patient, actor, grant["professional_id"],
                                           "prosthesis", content, payload.entry_key, previous=record_id,
                                           action="prosthesis_visit_linked" if chosen else "prosthesis_visit_removed")
        return {"id":saved}


@router.get("/visit-options")
async def visit_options(tenant_id: UUID, day: date | None = None,
                       limit: int = Query(30, ge=1, le=100),
                       offset: int = Query(0, ge=0), actor: Actor = Depends(authenticate)):
    async with clinical_session(tenant_id, actor) as (db, grant):
        zone_name = await db.scalar(text("SELECT timezone FROM tenants WHERE id=:tenant"), {"tenant": tenant_id})
        zone = ZoneInfo(zone_name)
        local_day = day or datetime.now(zone).date()
        if local_day.year < 1900 or local_day.year > 9998:
            raise HTTPException(422, "Data fora do período da agenda")
        start = datetime.combine(local_day, time.min, zone)
        end = datetime.combine(local_day + timedelta(days=1), time.min, zone)
        params = {"tenant": tenant_id, "lower": start, "upper": end, "limit": limit + 1,
                  "offset": offset, "professional": visit_professional(actor, grant)}
        visits = (await db.execute(text("""
            SELECT a.id, a.protocol, a.start_at, a.status, staff.name AS professional,
                   contact.name AS booking_contact, link.patient_id, p.name AS patient_name,
                   COALESCE(link.version,0) AS version
            FROM appointments a
            JOIN professionals staff ON staff.tenant_id=a.tenant_id AND staff.id=a.professional_id
            JOIN customers contact ON contact.tenant_id=a.tenant_id AND contact.id=a.customer_id
        """ + CURRENT_VISIT_PATIENT + """
            LEFT JOIN dental_patients p ON p.tenant_id=a.tenant_id AND p.id=link.patient_id
            WHERE a.tenant_id=:tenant AND a.start_at>=:lower AND a.start_at<:upper
              AND a.status IN ('confirmed','completed','cancelled','no_show')
        """ + VISIT_PROFESSIONAL + " ORDER BY a.start_at,a.id LIMIT :limit OFFSET :offset"),
            params)).mappings().all()
        await audit(db, tenant_id, actor, "visit_identity_directory_read")
        return {"day": local_day, "timezone": zone_name, "items": [dict(r) for r in visits[:limit]],
                "has_more": len(visits) > limit}


@router.get("/patients/{patient_id}/visits")
async def patient_visits(tenant_id: UUID, patient_id: UUID,
                        limit: int = Query(30, ge=1, le=100),
                        offset: int = Query(0, ge=0), actor: Actor = Depends(authenticate)):
    async with clinical_session(tenant_id, actor) as (db, grant):
        await patient_row(db, tenant_id, patient_id)
        params = {"tenant": tenant_id, "patient": patient_id, "limit": limit + 1,
                  "offset": offset, "professional": visit_professional(actor, grant)}
        visits = (await db.execute(text("""
            SELECT a.id, a.protocol, a.start_at, a.status, staff.name AS professional,
                   e.version, t.timezone
            FROM dental_visit_patient_events e
            JOIN appointments a ON a.tenant_id=e.tenant_id AND a.id=e.appointment_id
            JOIN tenants t ON t.id=e.tenant_id
            JOIN professionals staff ON staff.tenant_id=a.tenant_id AND staff.id=a.professional_id
            WHERE e.tenant_id=:tenant AND e.patient_id=:patient
              AND NOT EXISTS (SELECT 1 FROM dental_visit_patient_events n
                  WHERE n.tenant_id=e.tenant_id AND n.appointment_id=e.appointment_id AND n.version>e.version)
        """ + VISIT_PROFESSIONAL + " ORDER BY a.start_at DESC,a.id LIMIT :limit OFFSET :offset"),
            params)).mappings().all()
        await audit(db, tenant_id, actor, "patient_visits_read", patient_id)
        return {"items": [dict(r) for r in visits[:limit]], "has_more": len(visits) > limit}


@router.put("/appointments/{appointment_id}/patient")
async def link_visit_patient(tenant_id: UUID, appointment_id: UUID, payload: VisitPatientLink,
                             actor: Actor = Depends(authenticate)):
    async with clinical_session(tenant_id, actor, write=True) as (db, grant):
        params = {"tenant": tenant_id, "appointment": appointment_id, "actor": actor.account_id,
                  "patient": payload.patient_id, "professional": visit_professional(actor, grant)}
        # Lock the shared visit, including the initial assignment which has no prior event to lock.
        visit = await db.scalar(text("""
            SELECT a.id FROM appointments a WHERE a.tenant_id=:tenant AND a.id=:appointment
              AND a.status IN ('confirmed','completed','cancelled','no_show')
        """ + VISIT_PROFESSIONAL + " FOR UPDATE"), params)
        if visit is None:
            raise HTTPException(404, "Consulta indisponível nesta agenda")
        if payload.patient_id is not None:
            await patient_row(db, tenant_id, payload.patient_id)
        previous = (await db.execute(text("""
            SELECT patient_id,version,actor_account_id FROM dental_visit_patient_events
            WHERE tenant_id=:tenant AND appointment_id=:appointment ORDER BY version DESC LIMIT 1
        """), params)).mappings().one_or_none()
        version = previous["version"] if previous else 0
        current_patient = previous["patient_id"] if previous else None
        if payload.expected_version != version:
            replay = (previous and version == payload.expected_version + 1 and
                      current_patient == payload.patient_id and previous["actor_account_id"] == actor.account_id)
            if not replay:
                raise HTTPException(409, "O vínculo mudou. Consulte a agenda novamente.")
        elif current_patient != payload.patient_id:
            version += 1
            await db.execute(text("""
                INSERT INTO dental_visit_patient_events (tenant_id,appointment_id,patient_id,version,actor_account_id)
                VALUES (:tenant,:appointment,:patient,:version,:actor)
            """), {**params, "version": version})
            await audit(db, tenant_id, actor, "visit_identity_linked" if payload.patient_id else "visit_identity_removed",
                        payload.patient_id or current_patient)
        return {"appointment_id": appointment_id, "patient_id": payload.patient_id, "version": version}


@router.get("/appointments")
async def clinic_appointments(tenant_id: UUID,day:date,
                               actor:Actor=Depends(authenticate)):
    """Read appointments from the existing Orion agenda, not a second scheduler."""
    if not 1900 <= day.year <= 2100:
        raise HTTPException(422,"Informe uma data válida")
    async with clinical_session(tenant_id,actor) as (db,grant):
        zone_name=(await db.execute(text("""
            SELECT timezone FROM tenants WHERE id=:tenant
        """),{"tenant":tenant_id})).scalar_one()
        zone=ZoneInfo(zone_name)
        start=datetime.combine(day,time.min,zone).astimezone(timezone.utc)
        end=datetime.combine(day+timedelta(days=1),time.min,zone).astimezone(timezone.utc)
        records=(await db.execute(text("""
            SELECT a.id,a.start_at,a.end_at,a.status,c.name AS customer_name,
                   p.name AS professional_name,r.room_id,d.name AS room_name
            FROM appointments a
            JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
            JOIN professionals p ON p.tenant_id=a.tenant_id AND p.id=a.professional_id
            LEFT JOIN dental_room_reservations r
              ON r.tenant_id=a.tenant_id AND r.appointment_id=a.id AND r.active
            LEFT JOIN dental_rooms d ON d.tenant_id=r.tenant_id AND d.id=r.room_id
            WHERE a.tenant_id=:tenant AND a.start_at>=:start AND a.start_at<:end
              AND a.status='confirmed'
            ORDER BY a.start_at,a.id LIMIT 151
        """),{"tenant":tenant_id,"start":start,"end":end})).mappings().all()
        return {"day":day,"timezone":zone_name,"items":[dict(row) for row in records[:150]],
                "has_more":len(records)>150}


@router.put("/appointments/{appointment_id}/room")
async def assign_room(tenant_id:UUID,appointment_id:UUID,payload:RoomAssign,
                      actor:Actor=Depends(authenticate)):
    async with clinical_session(tenant_id,actor,write=True) as (db,grant):
        appt=(await db.execute(text("""
            SELECT id,status FROM appointments WHERE tenant_id=:tenant AND id=:id FOR UPDATE
        """),{"tenant":tenant_id,"id":appointment_id})).mappings().one_or_none()
        if not appt:raise HTTPException(404,"Agendamento não encontrado nesta clínica")
        if appt["status"] not in {"hold","confirmed"}:
            raise HTTPException(409,"Atribua consultórios apenas a consultas em aberto")
        try:
            async with db.begin_nested():
                await db.execute(text("""
                    INSERT INTO dental_room_reservations(tenant_id,appointment_id,room_id,period)
                    VALUES (:tenant,:appointment,:room,tstzrange(now(),now()+interval '1 second','[)'))
                    ON CONFLICT(tenant_id,appointment_id) DO UPDATE SET room_id=excluded.room_id
                """),{"tenant":tenant_id,"appointment":appointment_id,"room":payload.room_id})
        except IntegrityError:
            raise HTTPException(409,"Consultório ocupado, indisponível ou de outra empresa") from None
        await audit(db,tenant_id,actor,"room_assigned")
        return {"assigned":True,"appointment_id":appointment_id,"room_id":payload.room_id}


@router.get("/appointments/{appointment_id}/room")
async def appointment_room(tenant_id:UUID,appointment_id:UUID,actor:Actor=Depends(authenticate)):
    async with clinical_session(tenant_id,actor) as (db,grant):
        row=(await db.execute(text("""
            SELECT r.room_id,d.name,r.active,lower(r.period) AS starts_at,upper(r.period) AS ends_at
            FROM dental_room_reservations r JOIN dental_rooms d
              ON d.tenant_id=r.tenant_id AND d.id=r.room_id
            WHERE r.tenant_id=:tenant AND r.appointment_id=:appointment
        """),{"tenant":tenant_id,"appointment":appointment_id})).mappings().one_or_none()
        return {"reservation":dict(row) if row else None}


@asynccontextmanager
async def inventory_session(tenant: UUID, actor: Actor, *, write=False):
    # The owner can administer stock without a clinical grant. This scope is exclusive to inventory.
    if actor.membership_role == "owner":
        async with owner_session(tenant, actor) as db:
            yield db
    else:
        async with clinical_session(tenant, actor, write=write) as (db, _):
            yield db


@router.get("/supplies")
async def dental_supplies(
    tenant_id: UUID, actor: Actor = Depends(authenticate),
    q: str = Query(default="", max_length=120),
    order: Literal["asc", "desc"] = "asc",
    cursor: UUID | None = None, limit: int = Query(default=200, ge=1, le=200),
):
    async with inventory_session(tenant_id, actor) as db:
        params = {"tenant": tenant_id, "query": q.strip(), "take": limit + 1}
        scope = "tenant_id=:tenant AND strpos(lower(name),lower(CAST(:query AS text)))>0"
        after = ""
        # Only the two fixed directions are interpolated. Input values stay bound.
        direction, comparison = ("DESC", "<") if order == "desc" else ("ASC", ">")
        if cursor is not None:
            boundary = await db.scalar(text(
                "SELECT lower(name) FROM dental_supplies WHERE " + scope + " AND id=:id"
            ), {**params, "id": cursor})
            if boundary is None:
                raise HTTPException(400, "Página de materiais inválida")
            params.update(boundary=boundary, cursor=cursor)
            after = f" AND (lower(name),id) {comparison} (:boundary,:cursor)"
        items = (await db.execute(text(
            "SELECT id,name,unit,quantity,version FROM dental_supplies WHERE " + scope + after
            + f" ORDER BY lower(name) {direction},id {direction} LIMIT :take"
        ), params)).mappings().all()
        return {"items": [dict(item) for item in items[:limit]],
                "next_cursor": items[limit - 1]["id"] if len(items) > limit else None}


@router.post("/supplies",status_code=201)
async def add_supply(tenant_id:UUID,payload:SupplyCreate,actor:Actor=Depends(authenticate)):
    async with inventory_session(tenant_id, actor, write=True) as db:
        identity=uuid4()
        try:
            async with db.begin_nested():
                await db.execute(text("""
                    INSERT INTO dental_supplies(id,tenant_id,name,unit)
                    VALUES (:id,:tenant,:name,:unit)
                """),{"id":identity,"tenant":tenant_id,"name":payload.name,"unit":payload.unit})
        except IntegrityError:
            raise HTTPException(409,"Insumo já cadastrado") from None
        await audit(db,tenant_id,actor,"supply_created")
        return {"id":identity,"name":payload.name,"unit":payload.unit,"quantity":0}


@router.get("/supplies/{supply_id}/movements")
async def supply_history(
    tenant_id: UUID, supply_id: UUID, actor: Actor = Depends(authenticate),
    movement: Literal["in", "out"] | None = None, cursor: UUID | None = None,
    limit: int = Query(30, ge=1, le=100),
):
    async with inventory_session(tenant_id, actor) as db:
        scope = {"tenant": tenant_id, "supply": supply_id}
        exists = await db.scalar(text("""
            SELECT id FROM dental_supplies WHERE tenant_id=:tenant AND id=:supply
        """), scope)
        if exists is None:
            raise HTTPException(404, "Insumo não encontrado")
        boundary = None
        if cursor is not None:
            boundary = (await db.execute(text("""
                SELECT occurred_at,movement FROM dental_supply_movements
                WHERE tenant_id=:tenant AND supply_id=:supply AND id=:cursor
            """), {**scope, "cursor": cursor})).mappings().one_or_none()
            if boundary is None or (movement is not None and boundary["movement"] != movement):
                raise HTTPException(400, "Página do histórico inválida")
        rows = (await db.execute(text("""
            SELECT id,movement,quantity,occurred_at FROM dental_supply_movements
            WHERE tenant_id=:tenant AND supply_id=:supply
              AND (CAST(:movement AS varchar) IS NULL OR movement=:movement)
              AND (CAST(:before AS timestamptz) IS NULL OR (occurred_at,id)<(:before,:cursor))
            ORDER BY occurred_at DESC,id DESC LIMIT :limit
        """), {
            **scope, "movement": movement, "before": boundary["occurred_at"] if boundary else None,
            "cursor": cursor, "limit": limit + 1,
        })).mappings().all()
        return {
            "items": [dict(row) for row in rows[:limit]],
            "next_cursor": rows[limit - 1]["id"] if len(rows) > limit else None,
        }


@router.post("/supplies/{supply_id}/movements")
async def move_supply(tenant_id:UUID,supply_id:UUID,payload:SupplyMove,
                      actor:Actor=Depends(authenticate)):
    async with inventory_session(tenant_id, actor, write=True) as db:
        current=(await db.execute(text("""
            SELECT id,quantity,unit,version FROM dental_supplies
            WHERE tenant_id=:tenant AND id=:id FOR UPDATE
        """),{"tenant":tenant_id,"id":supply_id})).mappings().one_or_none()
        if not current:raise HTTPException(404,"Insumo não encontrado")
        prior=(await db.execute(text("""
            SELECT supply_id,movement,quantity FROM dental_supply_movements
            WHERE tenant_id=:tenant AND entry_key=:key
        """),{"tenant":tenant_id,"key":payload.entry_key})).mappings().one_or_none()
        if prior:
            if prior["supply_id"]!=supply_id or prior["quantity"]!=payload.quantity or prior["movement"]!=payload.movement:
                raise HTTPException(409,"Chave de operação reutilizada")
            return {"quantity":current["quantity"],"replayed":True}
        delta=payload.quantity*(1 if payload.movement=="in" else -1)
        new_quantity=current["quantity"]+delta
        if not 0<=new_quantity<=100000000:
            raise HTTPException(409,"Estoque insuficiente ou quantidade excessiva")
        await db.execute(text("""
            INSERT INTO dental_supply_movements(tenant_id,supply_id,movement,quantity,authored_by,entry_key)
            VALUES (:tenant,:id,:movement,:qty,:actor,:key)
        """),{"tenant":tenant_id,"id":supply_id,"movement":payload.movement,"qty":payload.quantity,
               "actor":actor.account_id,"key":payload.entry_key})
        await db.execute(text("""
            UPDATE dental_supplies SET quantity=:qty,version=version+1 WHERE tenant_id=:tenant AND id=:id
        """),{"tenant":tenant_id,"id":supply_id,"qty":new_quantity})
        await audit(db,tenant_id,actor,"supply_moved")
        return {"quantity":new_quantity,"replayed":False}
