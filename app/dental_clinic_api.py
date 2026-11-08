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
from datetime import date
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.catalog_api import Actor, authenticate, support_session_factory
from app.account_business import account_membership_role
from app.database import get_session_factory, tenant_transaction
from app.plan_permissions import plan_state
from app.tenant_access import resolve_tenant_access
from app.dental_clinic_schemas import (
    ClinicianGrant, PatientCreate, PatientPatch, RecordCreate,
    RoomCreate, RoomAssign, SupplyCreate, SupplyMove, ExamUpload
)


class PrivateJSON(JSONResponse):
    def __init__(self, content, **kwargs):
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.update({"Cache-Control": "no-store, max-age=0",
                        "Pragma": "no-cache", "X-Content-Type-Options": "nosniff",
                        "Referrer-Policy": "no-referrer"})
        super().__init__(content, headers=headers, **kwargs)


router = APIRouter(prefix="/api/v1/tenants/{tenant_id}/dental",
                   tags=["Odontologia — dados privados"], default_response_class=PrivateJSON)


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
    if not state.get("active") or not state.get("permissions",{}).get("clients.manage"):
        raise HTTPException(403, "Acesso operacional indisponível para esta clínica")
    return state


def require_member(actor,tenant):
    if (actor.role != "member" or actor.maintenance or actor.tenant_id != tenant
            or not isinstance(actor.account_id,UUID)
            or actor.membership_role not in {"owner","editor"}):
        raise HTTPException(403,"Acesso clínico restrito a membros autorizados da clínica")


@asynccontextmanager
async def clinical_session(tenant: UUID, actor: Actor, *, write=False, dentist=False):
    require_member(actor,tenant)
    access=await resolve_tenant_access(actor,tenant,"draft" if write else "read")
    if access.role not in {"owner","editor"} or access.account_id != actor.account_id:
        raise HTTPException(403,"Acesso clínico não autorizado")
    async with get_session_factory()() as db:
        async with tenant_transaction(db,tenant):
            await verify_dental_plan(db,tenant)
            grant=(await db.execute(text("""
                SELECT g.account_id,g.professional_id,g.level,g.cro_number,g.cro_state
                FROM dental_clinicians g JOIN professionals p
                  ON p.tenant_id=g.tenant_id AND p.id=g.professional_id
                WHERE g.tenant_id=:tenant AND g.account_id=:account
                  AND g.enabled AND p.active
            """),{"tenant":tenant,"account":actor.account_id})).mappings().one_or_none()
            if not grant:
                raise HTTPException(403,"O proprietário precisa autorizar seu acesso clínico na equipe.")
            if dentist and grant["level"] != "dentist":
                raise HTTPException(403,"Este registro exige um cirurgião-dentista autorizado.")
            await db.execute(text("SELECT set_config('app.dental_actor',:actor,true)"),
                             {"actor":str(actor.account_id)})
            yield db, dict(grant)


@asynccontextmanager
async def owner_session(tenant: UUID, actor: Actor):
    if (actor.role != "member" or actor.maintenance or actor.tenant_id != tenant
            or actor.membership_role != "owner" or not isinstance(actor.account_id,UUID)):
        raise HTTPException(403,"Somente o proprietário da clínica pode configurar acessos clínicos.")
    access=await resolve_tenant_access(actor,tenant,"publish")
    if access.role != "owner" or access.account_id != actor.account_id:
        raise HTTPException(403,"Vínculo do proprietário não confirmado")
    async with get_session_factory()() as db:
        async with tenant_transaction(db,tenant):
            await verify_dental_plan(db,tenant)
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


@router.get("/access")
async def dental_access(tenant_id: UUID,actor:Actor=Depends(authenticate)):
    require_member(actor,tenant_id)
    access=await resolve_tenant_access(actor,tenant_id,"read")
    async with get_session_factory()() as db:
        async with tenant_transaction(db,tenant_id):
            await verify_dental_plan(db,tenant_id)
            row=(await db.execute(text("""
                SELECT g.account_id,g.professional_id,g.level,g.cro_number,g.cro_state
                FROM dental_clinicians g JOIN professionals p
                  ON p.tenant_id=g.tenant_id AND p.id=g.professional_id
                WHERE g.tenant_id=:tenant AND g.account_id=:account AND g.enabled AND p.active
            """),{"tenant":tenant_id,"account":actor.account_id})).mappings().first()
            grants=[]
            if access.role=="owner":
                grants=[dict(x) for x in (await db.execute(text("""
                    SELECT g.account_id,g.professional_id,g.level,g.cro_number,g.cro_state,g.enabled
                    FROM dental_clinicians g WHERE g.tenant_id=:tenant ORDER BY g.granted_at
                """),{"tenant":tenant_id})).mappings().all()]
            return {"can_manage":access.role=="owner","account_id":actor.account_id,
                    "clinical_access":dict(row) if row else None,"grants":grants,
                    "data_encryption_configured":len(os.environ.get("DENTAL_DATA_KEY_B64",""))>0,
                    "electronic_signatures_homologated":False}


@router.put("/clinicians")
async def set_clinician(tenant_id: UUID,payload:ClinicianGrant,actor:Actor=Depends(authenticate)):
    # Recipient must have a currently verified account membership, never just an email.
    async with owner_session(tenant_id,actor) as db:
        async with support_session_factory()() as support:
            member=await account_membership_role(support,payload.account_id,tenant_id)
        if member not in {"owner","editor"}:
            raise HTTPException(422,"Vincule o profissional à equipe autorizada desta clínica primeiro")
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


@router.post("/patients",status_code=201)
async def create_dental_patient(tenant_id:UUID,payload:PatientCreate,actor:Actor=Depends(authenticate)):
    async with clinical_session(tenant_id,actor,write=True) as (db,grant):
        if payload.customer_id is not None:
            ok=(await db.execute(text("""
                SELECT id FROM customers WHERE tenant_id=:tenant AND id=:customer AND deleted_at IS NULL
            """),{"tenant":tenant_id,"customer":payload.customer_id})).scalar_one_or_none()
            if not ok:raise HTTPException(404,"Cliente administrativo não pertence a esta clínica")
        identity=uuid4()
        await db.execute(text("""
            INSERT INTO dental_patients
             (id,tenant_id,customer_id,name,birth_date,guardian_name,guardian_phone,
              contact_phone,created_by)
            VALUES (:id,:tenant,:customer,:name,:birth,:guardian,:guardian_phone,:phone,:actor)
        """),{"id":identity,"tenant":tenant_id,"customer":payload.customer_id,
               "name":payload.name,"birth":payload.birth_date,
               "guardian":payload.guardian_name,"guardian_phone":payload.guardian_phone,
               "phone":payload.contact_phone,"actor":actor.account_id})
        await audit(db,tenant_id,actor,"patient_created",identity)
        return await patient_row(db,tenant_id,identity)


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
    async with clinical_session(tenant_id,actor) as (db,grant):
        await patient_row(db,tenant_id,patient_id)
        if kind and kind not in (
            "anamnesis","odontogram","periodontal","progress","treatment","consent",
            "budget","insurance","prosthesis","exam_note","certificate","referral"):
            raise HTTPException(422,"Tipo de registro inválido")
        records=(await db.execute(text("""
            SELECT id,kind,encrypted_content,nonce,digest,authored_by,professional_id,created_at
            FROM dental_records WHERE tenant_id=:tenant AND patient_id=:patient
              AND (:kind IS NULL OR kind=:kind)
            ORDER BY created_at DESC,id DESC LIMIT :limit
        """),{"tenant":tenant_id,"patient":patient_id,"kind":kind,"limit":limit})).mappings().all()
        result=[]
        for row in records:
            data=json.loads(unseal(row,tenant_id,patient_id,row["kind"]))
            result.append({"id":row["id"],"kind":row["kind"],"content":data,
                           "authored_by":row["authored_by"],"professional_id":row["professional_id"],
                           "created_at":row["created_at"]})
        await audit(db,tenant_id,actor,"clinical_records_read",patient_id)
        return {"items":result}


@router.post("/patients/{patient_id}/records",status_code=201)
async def append_dental_record(tenant_id:UUID,patient_id:UUID,payload:RecordCreate,
                               actor:Actor=Depends(authenticate)):
    async with clinical_session(tenant_id,actor,write=True,dentist=True) as (db,grant):
        await patient_row(db,tenant_id,patient_id)
        identity=uuid4()
        data=json.dumps(payload.content,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()
        nonce,cipher,digest=seal(data,tenant_id,patient_id,payload.kind,identity)
        inserted=(await db.execute(text("""
            INSERT INTO dental_records
              (id,tenant_id,patient_id,kind,encrypted_content,nonce,digest,
               authored_by,professional_id,entry_key)
            VALUES (:id,:tenant,:patient,:kind,:content,:nonce,:digest,:actor,:professional,:key)
            ON CONFLICT(tenant_id,entry_key) DO NOTHING RETURNING id
        """),{"id":identity,"tenant":tenant_id,"patient":patient_id,"kind":payload.kind,
               "content":cipher,"nonce":nonce,"digest":digest,
               "actor":actor.account_id,"professional":grant["professional_id"],
               "key":payload.entry_key})).scalar_one_or_none()
        if inserted is None:
            prior=(await db.execute(text("""
                SELECT id,patient_id,kind,digest,authored_by FROM dental_records
                WHERE tenant_id=:tenant AND entry_key=:key
            """),{"tenant":tenant_id,"key":payload.entry_key})).mappings().one()
            if (prior["patient_id"]!=patient_id or prior["kind"]!=payload.kind
                    or prior["digest"]!=digest or prior["authored_by"]!=actor.account_id):
                raise HTTPException(409,"Chave de registro utilizada para outro conteúdo")
            identity=prior["id"]
        else:
            await audit(db,tenant_id,actor,"clinical_record_created",patient_id)
        return {"id":identity,"patient_id":patient_id,"kind":payload.kind,
                "recorded":True,"qualified_signature":False}


@router.get("/rooms")
async def dental_rooms(tenant_id:UUID,actor:Actor=Depends(authenticate)):
    async with clinical_session(tenant_id,actor) as (db,grant):
        rows=(await db.execute(text("""
            SELECT id,name,active FROM dental_rooms WHERE tenant_id=:tenant ORDER BY name
        """),{"tenant":tenant_id})).mappings().all()
        return {"items":[dict(row) for row in rows]}


@router.post("/rooms",status_code=201)
async def add_dental_room(tenant_id:UUID,payload:RoomCreate,actor:Actor=Depends(authenticate)):
    async with clinical_session(tenant_id,actor,write=True) as (db,grant):
        rid=uuid4()
        try:
            async with db.begin_nested():
                await db.execute(text("""
                    INSERT INTO dental_rooms (id,tenant_id,name) VALUES(:id,:tenant,:name)
                """),{"id":rid,"tenant":tenant_id,"name":payload.name})
        except IntegrityError:
            raise HTTPException(409,"Este consultório já está cadastrado") from None
        await audit(db,tenant_id,actor,"room_created")
        return {"id":rid,"name":payload.name,"active":True}


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


@router.get("/supplies")
async def dental_supplies(tenant_id:UUID,actor:Actor=Depends(authenticate)):
    async with clinical_session(tenant_id,actor) as (db,grant):
        items=(await db.execute(text("""
            SELECT id,name,unit,quantity,version FROM dental_supplies
            WHERE tenant_id=:tenant ORDER BY name LIMIT 200
        """),{"tenant":tenant_id})).mappings().all()
        return {"items":[dict(x) for x in items]}


@router.post("/supplies",status_code=201)
async def add_supply(tenant_id:UUID,payload:SupplyCreate,actor:Actor=Depends(authenticate)):
    async with clinical_session(tenant_id,actor,write=True) as (db,grant):
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


@router.post("/supplies/{supply_id}/movements")
async def move_supply(tenant_id:UUID,supply_id:UUID,payload:SupplyMove,
                      actor:Actor=Depends(authenticate)):
    async with clinical_session(tenant_id,actor,write=True) as (db,grant):
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
