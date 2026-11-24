"""Verified dental reception. The shared appointment ledger remains the schedule."""
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from typing import Literal
from uuid import UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import catalog_api
from app.account_business import account_membership_role
from app.appointment_lifecycle import panel_actor
from app.appointment_operations import _insert_items, _lock_catalogue, _manual_customer, check_private_conflicts
from app.booking_service import (
    acquire_professional_lock, bounds, check_slot, expire_holds, history, load_bundle, protocol,
    validate_local, verified_slot_grid,
)
from app.catalog_api import Actor, authenticate
from app.database import tenant_transaction
from app.dental_clinic_api import PrivateClinicalRoute, PrivateJSON, audit, require_member, verify_dental_plan
from app.dental_clinic_schemas import PatientSearch
from app.dental_reception_schemas import (
    ReceptionAppointment, ReceptionAppointmentSelection, ReceptionPatientCreate, ReceptionPatientPatch,
    ReceptionProfessional,
)
from app.management_schemas import normalize_phone
from app.plan_permissions import require_feature
from app.professional_commissions import save_professional_configuration

router = APIRouter(prefix="/api/v1/tenants/{tenant_id}/dental/reception",
                   tags=["Recepção odontológica"], default_response_class=PrivateJSON,
                   route_class=PrivateClinicalRoute)
UTC = timezone.utc


@asynccontextmanager
async def reception_scope(tenant_id, actor, *, write=False, feature=None):
    require_member(actor, tenant_id)
    if actor.membership_role not in {"owner", "editor"}:
        raise HTTPException(403, "Cadastro e agenda disponíveis para a recepção autorizada")
    async with catalog_api.support_session_factory()() as support:
        async with support.begin():
            role = await account_membership_role(support, actor.account_id, tenant_id, lock=write)
            if role != actor.membership_role:
                raise HTTPException(403, "Vínculo com a recepção não confirmado")
            # Use the same factory as the protected clinical endpoints.
            from app.dental_clinic_api import get_session_factory
            async with get_session_factory()() as db:
                async with tenant_transaction(db, tenant_id):
                    await verify_dental_plan(db, tenant_id)
                    if feature:
                        await require_feature(db, tenant_id, feature)
                    await db.execute(text("SELECT set_config('app.dental_reception_actor', :actor, true)"),
                                     {"actor": str(actor.account_id)})
                    yield db


PATIENT_FIELDS = """id, name, birth_date, contact_phone, guardian_name, guardian_phone,
    administrative_profile, created_at, updated_at, version"""


async def reception_patient(db, tenant, patient, *, lock=False):
    row = (await db.execute(text(f"""
        SELECT {PATIENT_FIELDS} FROM dental_patients WHERE tenant_id=:tenant AND id=:patient
    """ + (" FOR UPDATE" if lock else "")), {"tenant": tenant, "patient": patient})).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "Paciente não encontrado nesta clínica")
    return dict(row)


@router.post("/patients/search")
async def search_patients(tenant_id: UUID, payload: PatientSearch, actor: Actor = Depends(authenticate)):
    async with reception_scope(tenant_id, actor) as db:
        term = payload.query
        digits = "".join(c for c in term if c in "0123456789")
        if len(digits) >= 4 and all(c in "+0123456789 ()-./" for c in term):
            where = """(regexp_replace(coalesce(contact_phone,''),'[^0-9]','','g') LIKE :term
                       OR regexp_replace(coalesce(guardian_phone,''),'[^0-9]','','g') LIKE :term
                       OR administrative_profile->>'cpf' = :digits
                       OR administrative_profile->>'record_number' = :raw)"""
            pattern = f"%{digits}%"
        else:
            where = """(name ILIKE :term ESCAPE '!' OR administrative_profile->>'record_number' = :raw)"""
            pattern = "%" + term.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        rows = (await db.execute(text(f"""
            SELECT id, name, birth_date, contact_phone, guardian_name, guardian_phone
            FROM dental_patients WHERE tenant_id=:tenant AND {where}
            ORDER BY lower(name), id LIMIT :limit OFFSET :offset
        """), {"tenant": tenant_id, "term": pattern, "raw": term, "digits": digits,
               "limit": payload.limit + 1, "offset": payload.offset})).mappings().all()
        await audit(db, tenant_id, actor, "patient_directory_read")
        return {"items": [dict(row) for row in rows[:payload.limit]], "has_more": len(rows) > payload.limit}


async def patient_values(db, tenant, payload, *, existing_profile=None):
    values = payload.model_dump(include={"name", "birth_date", "contact_phone", "guardian_name", "guardian_phone"})
    profile = payload.administrative_profile
    if profile and profile.insurance_plan_id:
        insurance = await db.scalar(text("""
            SELECT id FROM dental_insurance_plans WHERE tenant_id=:tenant AND id=:plan
              AND (active OR id=CAST(:previous AS uuid))
        """), {"tenant": tenant, "plan": profile.insurance_plan_id,
               "previous": (existing_profile or {}).get("insurance_plan_id")})
        if insurance is None:
            raise HTTPException(422, "Selecione um convênio válido da clínica")
    values["administrative_profile"] = (
        profile.model_dump(mode="json", exclude_none=True) if profile else existing_profile or {}
    )
    return values


@router.get("/insurance-options")
async def insurance_options(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    async with reception_scope(tenant_id, actor) as db:
        items = (await db.execute(text("""
            SELECT id, insurer, plan, active FROM dental_insurance_plans
            WHERE tenant_id=:tenant ORDER BY active DESC, lower(insurer), lower(plan), id LIMIT 300
        """), {"tenant": tenant_id})).mappings().all()
        return {"items": [dict(row) for row in items]}


@router.post("/patients", status_code=201)
async def register_patient(tenant_id: UUID, payload: ReceptionPatientCreate, actor: Actor = Depends(authenticate)):
    async with reception_scope(tenant_id, actor, write=True) as db:
        values = await patient_values(db, tenant_id, payload)
        identity = uuid5(tenant_id, f"reception:{actor.account_id}:{payload.request_id}")
        saved = await db.scalar(text("""
            INSERT INTO dental_patients
                (id,tenant_id,name,birth_date,contact_phone,guardian_name,guardian_phone,administrative_profile,created_by)
            VALUES (:id,:tenant,:name,:birth_date,:contact_phone,:guardian_name,:guardian_phone,CAST(:profile AS jsonb),:actor)
            ON CONFLICT (id) DO NOTHING RETURNING id
        """), {**values, "id": identity, "tenant": tenant_id, "actor": actor.account_id,
               "profile": json.dumps(values["administrative_profile"])})
        patient = await reception_patient(db, tenant_id, identity)
        if saved is None and any(patient[key] != value for key, value in values.items()):
            raise HTTPException(409, "O cadastro já foi salvo com outros dados. Consulte a ficha antes de continuar.")
        if saved is not None:
            await audit(db, tenant_id, actor, "patient_created", identity)
        return patient


@router.get("/patients/{patient_id}")
async def patient_detail(tenant_id: UUID, patient_id: UUID, actor: Actor = Depends(authenticate)):
    async with reception_scope(tenant_id, actor) as db:
        patient = await reception_patient(db, tenant_id, patient_id)
        await audit(db, tenant_id, actor, "patient_read", patient_id)
        return patient


@router.patch("/patients/{patient_id}")
async def edit_patient(tenant_id: UUID, patient_id: UUID, payload: ReceptionPatientPatch,
                       actor: Actor = Depends(authenticate)):
    async with reception_scope(tenant_id, actor, write=True) as db:
        previous = await reception_patient(db, tenant_id, patient_id)
        values = await patient_values(db, tenant_id, payload, existing_profile=previous["administrative_profile"])
        saved = await db.scalar(text("""
            UPDATE dental_patients SET name=:name, birth_date=:birth_date, contact_phone=:contact_phone,
              guardian_name=:guardian_name, guardian_phone=:guardian_phone, administrative_profile=CAST(:profile AS jsonb),
              version=version+1, updated_at=clock_timestamp()
            WHERE tenant_id=:tenant AND id=:patient AND version=:version RETURNING id
        """), {**values, "tenant": tenant_id, "patient": patient_id, "version": payload.expected_version,
               "profile": json.dumps(values["administrative_profile"])})
        if saved is None:
            raise HTTPException(409, "O cadastro mudou. Atualize a ficha antes de salvar.")
        await audit(db, tenant_id, actor, "patient_updated", patient_id)
        return await reception_patient(db, tenant_id, patient_id)


@router.get("/patients/{patient_id}/visits")
async def reception_patient_visits(tenant_id: UUID, patient_id: UUID,
                                   limit: int = Query(15, ge=1, le=50), offset: int = Query(0, ge=0),
                                   actor: Actor = Depends(authenticate)):
    async with reception_scope(tenant_id, actor) as db:
        await reception_patient(db, tenant_id, patient_id)
        rows = (await db.execute(text("""
            SELECT a.id, a.protocol, a.start_at, a.end_at, a.status, p.name AS professional, t.timezone
            FROM dental_visit_patient_events e
            JOIN appointments a ON a.tenant_id=e.tenant_id AND a.id=e.appointment_id
            JOIN professionals p ON p.tenant_id=a.tenant_id AND p.id=a.professional_id
            JOIN tenants t ON t.id=a.tenant_id
            WHERE e.tenant_id=:tenant AND e.patient_id=:patient
              AND NOT EXISTS (SELECT 1 FROM dental_visit_patient_events newer
                  WHERE newer.tenant_id=e.tenant_id AND newer.appointment_id=e.appointment_id AND newer.version>e.version)
            ORDER BY a.start_at DESC,a.id LIMIT :limit OFFSET :offset
        """), {"tenant": tenant_id, "patient": patient_id, "limit": limit + 1, "offset": offset})).mappings().all()
        await audit(db, tenant_id, actor, "patient_visits_read", patient_id)
        return {"items": [dict(row) for row in rows[:limit]], "has_more": len(rows) > limit}


@router.get("/professionals")
async def reception_professionals(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    async with reception_scope(tenant_id, actor, feature="professionals.manage") as db:
        professionals = (await db.execute(text("""
            SELECT p.*, dp.specialties, dp.cro_number, dp.cro_state, dp.default_room_id,
                EXISTS (SELECT 1 FROM working_hours h
                        WHERE h.tenant_id=p.tenant_id AND h.professional_id=p.id) AS has_hours
            FROM professionals p LEFT JOIN dental_professional_profiles dp
              ON dp.tenant_id=p.tenant_id AND dp.professional_id=p.id
            WHERE p.tenant_id=:tenant ORDER BY lower(p.name), p.id
        """), {"tenant": tenant_id})).mappings().all()
        return {"items": [dict(row) for row in professionals]}


async def configure_dentist(tenant, actor, payload, professional=None):
    profile = payload.profile.model_dump()
    try:
        async with reception_scope(tenant, actor, write=True, feature="professionals.manage") as db:
            if profile["default_room_id"] is not None:
                await validate_room(db, tenant, profile["default_room_id"], lock=True)
            saved = await save_professional_configuration(
                db, tenant, payload.professional, professional, identity_details=payload.profile.model_dump(mode="json"))
            await db.execute(text("""
                INSERT INTO dental_professional_profiles (tenant_id,professional_id,specialties,cro_number,cro_state,default_room_id)
                VALUES (:tenant,:professional,:specialties,:cro_number,:cro_state,:default_room_id)
                ON CONFLICT (tenant_id,professional_id) DO UPDATE SET specialties=excluded.specialties,
                    cro_number=excluded.cro_number,cro_state=excluded.cro_state,default_room_id=excluded.default_room_id
                WHERE :editing AND (dental_professional_profiles.specialties,dental_professional_profiles.cro_number,
                       dental_professional_profiles.cro_state,dental_professional_profiles.default_room_id)
                   IS DISTINCT FROM (excluded.specialties,excluded.cro_number,excluded.cro_state,excluded.default_room_id)
            """), {**profile, "tenant": tenant, "professional": saved["id"], "editing": professional is not None})
            persisted = (await db.execute(text("""
                SELECT specialties,cro_number,cro_state,default_room_id FROM dental_professional_profiles
                WHERE tenant_id=:tenant AND professional_id=:id
            """), {"tenant": tenant, "id": saved["id"]})).mappings().one()
            return {**saved, **persisted}
    except IntegrityError:
        raise HTTPException(409, "Vínculo ou cadastro alterado. Confira a equipe e tente novamente.") from None


@router.post("/professionals", status_code=201)
async def register_dentist(tenant_id: UUID, payload: ReceptionProfessional, actor: Actor = Depends(authenticate)):
    return await configure_dentist(tenant_id, actor, payload)


@router.put("/professionals/{professional_id}")
async def edit_dentist(tenant_id: UUID, professional_id: UUID, payload: ReceptionProfessional,
                       actor: Actor = Depends(authenticate)):
    return await configure_dentist(tenant_id, actor, payload, professional_id)


async def active_rooms(db, tenant):
    return [dict(room) for room in (await db.execute(text("""
        SELECT id,name FROM dental_rooms WHERE tenant_id=:tenant AND active ORDER BY lower(name), id
    """), {"tenant": tenant})).mappings().all()]


@router.get("/rooms")
async def reception_rooms(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    async with reception_scope(tenant_id, actor) as db:
        return {"items": await active_rooms(db, tenant_id)}


async def validate_room(db, tenant, room, *, lock=False):
    query = "SELECT id FROM dental_rooms WHERE tenant_id=:tenant AND id=:room AND active"
    if not await db.scalar(text(query + (" FOR SHARE" if lock else "")), {"tenant": tenant, "room": room}):
        raise HTTPException(409, "Consultório inativo ou indisponível nesta clínica")


@router.get("/booking-options")
async def booking_options(tenant_id: UUID, actor: Actor = Depends(authenticate)):
    async with reception_scope(tenant_id, actor, feature="agenda.manage") as db:
        clinic = (await db.execute(text("""
            SELECT t.timezone,
              COALESCE((SELECT jsonb_agg(jsonb_build_object(
                  'id',p.id,'name',p.name,'default_room_id',dp.default_room_id,
                  'has_hours',EXISTS(SELECT 1 FROM working_hours h WHERE h.tenant_id=p.tenant_id
                                   AND h.professional_id=p.id),
                  'procedure_ids',COALESCE((SELECT jsonb_agg(ps.service_id)
                    FROM professional_services ps JOIN services s ON s.tenant_id=ps.tenant_id AND s.id=ps.service_id
                    WHERE ps.tenant_id=p.tenant_id AND ps.professional_id=p.id AND s.active AND s.catalog_kind='base'),'[]')
              ) ORDER BY lower(p.name),p.id)
              FROM professionals p LEFT JOIN dental_professional_profiles dp
              ON dp.tenant_id=p.tenant_id AND dp.professional_id=p.id
              WHERE p.tenant_id=t.id AND p.active AND p.accepts_appointments),'[]') AS professionals,
              COALESCE((SELECT jsonb_agg(jsonb_build_object('id',s.id,'name',s.name,'duration_min',s.duration_min)
                 ORDER BY lower(s.name),s.id) FROM services s WHERE s.tenant_id=t.id AND s.active
                   AND s.catalog_kind='base'),'[]') AS procedures
            FROM tenants t WHERE t.id=:tenant
        """), {"tenant": tenant_id})).mappings().one()
        return {**dict(clinic), "rooms": await active_rooms(db, tenant_id)}


async def dental_bundle(db, tenant, professional, procedure, duration):
    if not await db.scalar(text("""
        SELECT id FROM services WHERE tenant_id=:tenant AND id=:procedure AND catalog_kind='base'
    """), {"tenant": tenant, "procedure": procedure}):
        raise HTTPException(422, "Selecione um procedimento da clínica")
    shop, items = await load_bundle(db, tenant, professional, [procedure])
    items[0]["duration_min"] = duration
    return shop, items


@router.get("/availability")
async def dental_availability(tenant_id: UUID, professional_id: UUID, procedure_id: UUID, day: date,
                              duration_min: int = Query(ge=5, le=720), room_id: UUID | None = None,
                              actor: Actor = Depends(authenticate)):
    async with reception_scope(tenant_id, actor, feature="agenda.manage") as db:
        shop, items = await dental_bundle(db, tenant_id, professional_id, procedure_id, duration_min)
        now = datetime.now(UTC)
        zone = ZoneInfo(shop["timezone"])
        today = now.astimezone(zone).date()
        if day < today or day > today + timedelta(days=shop["booking_horizon_days"]):
            return {"slots": [], "reason": "outside_horizon", "timezone": shop["timezone"]}
        slots, reason = await verified_slot_grid(db, tenant_id, professional_id, shop, items, day, now)
        if room_id is not None:
            await validate_room(db, tenant_id, room_id)
        if room_id is not None and slots:
            lower = datetime.combine(day - timedelta(days=1), time.min, zone)
            upper = datetime.combine(day + timedelta(days=2), time.min, zone)
            reserved = (await db.execute(text("""
                SELECT lower(period) AS starts, upper(period) AS ends FROM dental_room_reservations
                WHERE tenant_id=:tenant AND room_id=:room AND active AND period && tstzrange(:lower,:upper,'[)')
            """), {"tenant": tenant_id, "room": room_id, "lower": lower, "upper": upper})).mappings().all()
            intervals = [(slot, bounds(datetime.fromisoformat(slot), items)) for slot in slots]
            slots = [slot for slot, interval in intervals if not any(
                room["starts"] < interval["occupied_until"] and room["ends"] > interval["occupied_from"]
                for room in reserved)]
            if not slots:
                reason = "room_occupied"
        return {"slots": slots, "reason": reason, "timezone": shop["timezone"]}


async def patient_booking_contact(db, tenant, patient, contact):
    name, phone = patient["name"], patient["contact_phone"]
    if contact == "guardian":
        name, phone = patient["guardian_name"], patient["guardian_phone"]
        if not name or not phone:
            raise HTTPException(422, "Preencha o contato do responsável na ficha ou selecione Sem telefone.")
    if contact == "none":
        phone = None
    if phone:
        try:
            phone = normalize_phone(phone)
        except ValueError:
            raise HTTPException(422, "O telefone selecionado precisa de DDD. Você pode reservar sem telefone.") from None
    customer = None
    if phone is None:
        # Reuse only a non-telephone contact from this patient's latest versions.
        customer = await db.scalar(text("""
            SELECT a.customer_id FROM dental_visit_patient_events e
            JOIN appointments a ON a.tenant_id=e.tenant_id AND a.id=e.appointment_id
            JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
            WHERE e.tenant_id=:tenant AND e.patient_id=:patient
              AND c.whatsapp_e164 IS NULL AND c.deleted_at IS NULL AND c.active
              AND NOT EXISTS (SELECT 1 FROM dental_visit_patient_events newer
                  WHERE newer.tenant_id=e.tenant_id AND newer.appointment_id=e.appointment_id AND newer.version>e.version)
            ORDER BY a.created_at DESC, a.id LIMIT 1
        """), {"tenant": tenant, "patient": patient["id"]})
    return await _manual_customer(db, tenant, SimpleNamespace(
        customer_id=customer, customer_name=name, customer_phone=phone))


async def allocate_dental_appointment(db, tenant, actor, payload):
    professional, procedure = payload.professional_id, payload.procedure_id
    key = f"dental:{payload.request_id}"
    content = payload.model_dump(mode="json", exclude={"request_id", "start_at"})
    content.update(actor=str(actor.account_id), start_at=payload.start_at.astimezone(UTC).isoformat())
    fingerprint = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
    await acquire_professional_lock(db, tenant, professional)
    existing = (await db.execute(text("""
        SELECT id,protocol,status,start_at,request_fingerprint FROM appointments WHERE tenant_id=:tenant AND idempotency_key=:key
    """), {"tenant": tenant, "key": key})).mappings().one_or_none()
    if existing is not None:
        if existing["request_fingerprint"] != fingerprint:
            raise HTTPException(409, "A operação já foi usada para outra consulta.")
        return {key: existing[key] for key in ("id", "protocol", "status", "start_at")}
    await _lock_catalogue(db, tenant, professional, [procedure])
    shop, items = await dental_bundle(db, tenant, professional, procedure, payload.duration_min)
    start = payload.start_at.astimezone(ZoneInfo(shop["timezone"]))
    now = datetime.now(UTC)
    await expire_holds(db, tenant, professional, now)
    if payload.mode == "exceptional":
        validate_local(start, ZoneInfo(shop["timezone"]))
        if start < now.replace(second=0, microsecond=0) or start > now + timedelta(days=366) or start.second or start.microsecond:
            raise HTTPException(422, "Escolha um horário futuro inteiro para o encaixe.")
        interval = bounds(start, items)
        await check_private_conflicts(db, tenant, professional, interval, now)
    else:
        interval = await check_slot(db, tenant, professional, shop, items, start, now)
    if payload.room_id:
        await validate_room(db, tenant, payload.room_id, lock=True)
    patient = await reception_patient(db, tenant, payload.patient_id, lock=True)
    occupied = await db.scalar(text("""
        SELECT a.id FROM dental_visit_patient_events e
        JOIN appointments a ON a.tenant_id=e.tenant_id AND a.id=e.appointment_id
        WHERE e.tenant_id=:tenant AND e.patient_id=:patient AND a.status='confirmed'
          AND a.start_at<:end_at AND a.end_at>:start_at
          AND NOT EXISTS (SELECT 1 FROM dental_visit_patient_events later
            WHERE later.tenant_id=e.tenant_id AND later.appointment_id=e.appointment_id AND later.version>e.version)
        LIMIT 1
    """), {**interval, "tenant": tenant, "patient": patient["id"]})
    if occupied is not None:
        raise HTTPException(409, "O paciente já tem uma consulta nesse horário.")
    customer = await patient_booking_contact(db, tenant, patient, payload.contact)
    appointment = uuid4()
    for _ in range(5):
        candidate = protocol()
        try:
            async with db.begin_nested():
                await db.execute(text("""
                    INSERT INTO appointments (id,tenant_id,customer_id,professional_id,protocol,idempotency_key,
                        request_fingerprint,start_at,end_at,occupied_from,occupied_until,total_cents,status,
                        booking_mode,created_by_account_id,preserve_catalog_snapshot)
                    VALUES (:id,:tenant,:customer,:professional,:candidate,:key,:fingerprint,:start_at,:end_at,
                        :occupied_from,:occupied_until,:total_cents,'confirmed',:mode,:actor,true)
                """), {**interval, "id": appointment, "tenant": tenant, "customer": customer,
                       "professional": professional, "candidate": candidate, "key": key, "fingerprint": fingerprint,
                       "mode": payload.mode, "actor": actor.account_id})
            break
        except IntegrityError as error:
            if "uq_appointment_protocol" not in str(error):
                raise
    else:
        raise HTTPException(503, "Não foi possível gerar o protocolo da consulta")
    await _insert_items(db, tenant, appointment, items)
    await db.execute(text("""
        INSERT INTO dental_visit_patient_events (tenant_id,appointment_id,patient_id,version,actor_account_id)
        VALUES (:tenant,:appointment,:patient,1,:actor)
    """), {"tenant": tenant, "appointment": appointment, "patient": patient["id"], "actor": actor.account_id})
    await db.execute(text("""
        INSERT INTO dental_reception_appointments (tenant_id,appointment_id,visit_type,administrative_note)
        VALUES (:tenant,:appointment,:visit_type,:note)
    """), {"tenant": tenant, "appointment": appointment, "visit_type": payload.visit_type,
           "note": payload.administrative_note})
    if payload.room_id:
        await db.execute(text("""
            INSERT INTO dental_room_reservations (tenant_id,appointment_id,room_id,period)
            VALUES (:tenant,:appointment,:room,tstzrange(:occupied_from,:occupied_until,'[)'))
        """), {**interval, "tenant": tenant, "appointment": appointment, "room": payload.room_id})
    await history(db, tenant, appointment, "confirmed", context=panel_actor(actor), notifications=False)
    await db.execute(text("""
        INSERT INTO usage_counters (tenant_id,period,bookings)
        VALUES (:tenant,date_trunc('month',CURRENT_DATE)::date,1)
        ON CONFLICT (tenant_id,period) DO UPDATE SET bookings=usage_counters.bookings+1
    """), {"tenant": tenant})
    await audit(db, tenant, actor, "reception_appointment_created", patient["id"])
    return {"id": appointment, "protocol": candidate, "status": "confirmed", "start_at": interval["start_at"]}


@router.post("/appointments", status_code=201)
async def create_reception_appointment(tenant_id: UUID, payload: ReceptionAppointment,
                                       actor: Actor = Depends(authenticate)):
    try:
        async with reception_scope(tenant_id, actor, write=True, feature="agenda.manage") as db:
            return await allocate_dental_appointment(db, tenant_id, actor, payload)
    except IntegrityError:
        raise HTTPException(409, "Horário ou consultório ocupado. Atualize a disponibilidade e selecione outro horário.") from None


ANNOTATIONS = """
    FROM appointments a
    LEFT JOIN LATERAL (SELECT patient_id FROM dental_visit_patient_events e
        WHERE e.tenant_id=a.tenant_id AND e.appointment_id=a.id ORDER BY version DESC LIMIT 1) link ON true
    LEFT JOIN dental_patients patient ON patient.tenant_id=a.tenant_id AND patient.id=link.patient_id
    LEFT JOIN dental_reception_appointments r ON r.tenant_id=a.tenant_id AND r.appointment_id=a.id
    LEFT JOIN dental_room_reservations reservation ON reservation.tenant_id=a.tenant_id AND reservation.appointment_id=a.id
    LEFT JOIN dental_rooms room ON room.tenant_id=a.tenant_id AND room.id=reservation.room_id
"""


@router.post("/appointment-labels")
async def appointment_labels(tenant_id: UUID, payload: ReceptionAppointmentSelection, actor: Actor = Depends(authenticate)):
    async with reception_scope(tenant_id, actor, feature="agenda.manage") as db:
        labels = (await db.execute(text("""
            SELECT a.id, patient.name AS patient_name, r.visit_type, room.name AS room_name
        """ + ANNOTATIONS + " WHERE a.tenant_id=:tenant AND a.id=ANY(CAST(:ids AS uuid[]))"),
            {"tenant": tenant_id, "ids": payload.ids})).mappings().all()
        await audit(db, tenant_id, actor, "reception_appointment_labels_read")
        return {"items": [dict(row) for row in labels]}


@router.get("/appointments/{appointment_id}")
async def appointment_reception_detail(tenant_id: UUID, appointment_id: UUID, actor: Actor = Depends(authenticate)):
    async with reception_scope(tenant_id, actor, feature="agenda.manage") as db:
        result = (await db.execute(text("""
            SELECT a.id, a.status, a.start_at, t.timezone, patient.id AS patient_id,
              patient.name AS patient_name, r.visit_type, room.name AS room_name, r.administrative_note,
              CASE WHEN r.progress_for_start_at=a.start_at THEN r.checked_in_at END AS checked_in_at,
              CASE WHEN r.progress_for_start_at=a.start_at THEN r.started_at END AS started_at
        """ + ANNOTATIONS + " JOIN tenants t ON t.id=a.tenant_id WHERE a.tenant_id=:tenant AND a.id=:id"),
            {"tenant": tenant_id, "id": appointment_id})).mappings().one_or_none()
        if result is None:
            raise HTTPException(404, "Consulta não encontrada nesta clínica")
        await audit(db, tenant_id, actor, "reception_appointment_read", result["patient_id"])
        return dict(result)


@router.post("/appointments/{appointment_id}/progress/{action}")
async def update_reception_progress(tenant_id: UUID, appointment_id: UUID, action: Literal["arrival", "start"],
                                    actor: Actor = Depends(authenticate)):
    async with reception_scope(tenant_id, actor, write=True, feature="agenda.manage") as db:
        params = {"tenant": tenant_id, "appointment": appointment_id}
        visit = (await db.execute(text("""
            SELECT a.status, a.start_at, t.timezone FROM appointments a JOIN tenants t ON t.id=a.tenant_id
            WHERE a.tenant_id=:tenant AND a.id=:appointment FOR UPDATE OF a
        """), params)).mappings().one_or_none()
        if not visit or visit["status"] != "confirmed":
            raise HTTPException(409, "Esta etapa exige uma consulta confirmada")
        zone = ZoneInfo(visit["timezone"])
        if visit["start_at"].astimezone(zone).date() != datetime.now(zone).date():
            raise HTTPException(409, "Registre a chegada e o início no dia da consulta")
        changed = await db.scalar(text("""
            UPDATE dental_reception_appointments SET
                progress_for_start_at=:start_at,
                checked_in_at=CASE WHEN :arrival AND progress_for_start_at IS DISTINCT FROM :start_at
                                   THEN clock_timestamp() ELSE checked_in_at END,
                started_at=CASE WHEN NOT :arrival THEN COALESCE(started_at,clock_timestamp())
                                WHEN progress_for_start_at IS DISTINCT FROM :start_at THEN NULL ELSE started_at END
            WHERE tenant_id=:tenant AND appointment_id=:appointment
              AND (:arrival OR (progress_for_start_at=:start_at AND checked_in_at IS NOT NULL))
            RETURNING appointment_id
        """), {**params, "start_at": visit["start_at"], "arrival": action == "arrival"})
        if changed is None:
            raise HTTPException(409, "Registre a chegada antes de iniciar esta consulta")
        await audit(db, tenant_id, actor, "reception_appointment_" + action)
        return {"id": appointment_id, "progress": action}
