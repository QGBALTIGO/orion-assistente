"""Real PostgreSQL RLS and room-overlap tests. No production credentials or data."""
from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import tenant_transaction
from app.booking_service import protocol

pytestmark = pytest.mark.integration


def async_url(v):
    return v.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1).replace(
        "postgresql://", "postgresql+asyncpg://", 1)


@pytest_asyncio.fixture
async def clinic_db():
    if not os.getenv("APP_DATABASE_URL") or not os.getenv("MIGRATION_DATABASE_URL"):
        if os.getenv("REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("Isolated PostgreSQL URLs are required for real clinical tests")
        pytest.skip("Real PostgreSQL is required")
    runtime=create_async_engine(async_url(os.environ["APP_DATABASE_URL"]),pool_size=3,max_overflow=3)
    migrator=create_async_engine(async_url(os.environ["MIGRATION_DATABASE_URL"]))
    a,b=uuid4(),uuid4()
    account,assistant,account_b=(uuid4() for _ in range(3))
    pro_a,pro_a2,pro_b=(uuid4() for _ in range(3))
    patient_a,patient_b=uuid4(),uuid4()
    room_a,room_a2=uuid4(),uuid4()
    customer_a,customer_b=uuid4(),uuid4()
    appt_a,appt_a2=uuid4(),uuid4()
    when=datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0)+timedelta(days=25)
    try:
        async with migrator.begin() as db:
            for tid in (a,b):
                await db.execute(text("""
                    INSERT INTO tenants(id,name,slug)
                    VALUES(:id,'Clínica exclusivamente sintética',:slug)
                """),{"id":tid,"slug":"dental-test-"+str(tid)})
            for who in (account,assistant,account_b):
                await db.execute(text("""
                    INSERT INTO panel_accounts(id,name,email,password_hash)
                    VALUES(:id,'Conta sintética',:email,'invalid-synthetic-hash')
                """),{"id":who,"email":str(who)+"@example.invalid"})
            for tid,pro in ((a,pro_a),(a,pro_a2),(b,pro_b)):
                await db.execute(text("""
                    INSERT INTO professionals(id,tenant_id,name)
                    VALUES(:id,:tenant,'Profissional de teste')
                """),{"id":pro,"tenant":tid})
            for tid,acc,pro,level in ((a,account,pro_a,'dentist'),
                                       (a,assistant,pro_a2,'assistant'),
                                       (b,account_b,pro_b,'dentist')):
                await db.execute(text("""
                    INSERT INTO dental_clinicians
                      (tenant_id,account_id,professional_id,level,cro_number,cro_state,granted_by)
                    VALUES (:tenant,:account,:pro,:level,:cro,:uf,:grant)
                """),{"tenant":tid,"account":acc,"pro":pro,"level":level,
                       "cro":"12345" if level=="dentist" else None,
                       "uf":"MS" if level=="dentist" else None,"grant":acc})
            for tid,pat,acc in ((a,patient_a,account),(b,patient_b,account_b)):
                await db.execute(text("""
                   INSERT INTO dental_patients(id,tenant_id,name,created_by)
                   VALUES (:id,:tenant,'Paciente estritamente sintético',:author)
                """),{"id":pat,"tenant":tid,"author":acc})
            for tid,cust in ((a,customer_a),(b,customer_b)):
                await db.execute(text("""
                    INSERT INTO customers(id,tenant_id,name)
                    VALUES(:id,:tenant,'Contato de teste')
                """),{"id":cust,"tenant":tid})
            for idx,(apid,cust,pro) in enumerate(((appt_a,customer_a,pro_a),
                                                  (appt_a2,customer_a,pro_a2)),1):
                await db.execute(text("""
                    INSERT INTO appointments(
                      id,tenant_id,customer_id,professional_id,protocol,
                      idempotency_key,request_fingerprint,start_at,end_at,
                      occupied_from,occupied_until,status,total_cents
                    ) VALUES(
                      :id,:tenant,:customer,:pro,:protocol,:key,:fingerprint,
                      :start,:end,:start,:end,'confirmed',1000
                    )
                """),{"id":apid,"tenant":a,"customer":cust,"pro":pro,
                       "protocol":protocol(),
                       "key":str(apid),"fingerprint":"0"*64,
                       "start":when,"end":when+timedelta(minutes=40)})
            for rid,name in ((room_a,'Consultório A'),(room_a2,'Consultório B')):
                await db.execute(text("""
                    INSERT INTO dental_rooms(id,tenant_id,name)
                    VALUES (:id,:tenant,:name)
                """),{"id":rid,"tenant":a,"name":name})
        yield {"runtime":runtime,"migrator":migrator,"a":a,"b":b,
               "account":account,"assistant":assistant,"account_b":account_b,
               "pro_a":pro_a,"pro_a2":pro_a2,"pro_b":pro_b,
               "patient_a":patient_a,"patient_b":patient_b,
               "appt_a":appt_a,"appt_a2":appt_a2,"room_a":room_a,
               "room_a2":room_a2,"when":when}
    finally:
        async with migrator.begin() as db:
            for tbl in ("dental_visit_patient_events","dental_access_audit","dental_laboratories","dental_intake_templates","dental_room_reservations",
                        "dental_exam_files","dental_records","dental_supply_movements",
                        "dental_supplies","dental_patients","dental_rooms",
                        "dental_clinicians","appointment_history","appointment_items",
                        "appointments","customers","professionals"):
                await db.execute(text(f"DELETE FROM public.{tbl} WHERE tenant_id IN (:a,:b)"),
                                 {"a":a,"b":b})
            await db.execute(text("DELETE FROM public.tenants WHERE id IN (:a,:b)"),{"a":a,"b":b})
            await db.execute(text("""
                DELETE FROM panel_accounts WHERE id IN (:a,:b,:c)
            """),{"a":account,"b":assistant,"c":account_b})
        await runtime.dispose()
        await migrator.dispose()


async def count_patients(session,tenant,actor=None):
    async with tenant_transaction(session,tenant):
        if actor:
            await session.execute(text("SELECT set_config('app.dental_actor',:actor,true)"),
                                  {"actor":str(actor)})
        return (await session.execute(text("SELECT count(*) FROM dental_patients"))).scalar_one()


@pytest.mark.asyncio
async def test_patient_rls_requires_both_verified_tenant_and_clinician(clinic_db):
    d=clinic_db
    factory=async_sessionmaker(d["runtime"],expire_on_commit=False)
    async with factory() as s:
        assert await count_patients(s,d["a"])==0
        assert await count_patients(s,d["a"],d["account"])==1
        assert await count_patients(s,d["b"],d["account"])==0
        assert await count_patients(s,d["b"],d["account_b"])==1
        assert await count_patients(s,d["a"])==0  # pooled connection never retains identity


@pytest.mark.asyncio
async def test_assistant_cannot_read_health_documents_even_with_patient_access(clinic_db):
    d=clinic_db
    factory=async_sessionmaker(d["runtime"],expire_on_commit=False)
    async with factory() as s:
        async with tenant_transaction(s,d["a"]):
            await s.execute(text("SELECT set_config('app.dental_actor',:actor,true)"),
                            {"actor":str(d["assistant"])})
            assert (await s.execute(text("SELECT count(*) FROM dental_patients"))).scalar_one()==1
            assert (await s.execute(text("SELECT count(*) FROM dental_records"))).scalar_one()==0
            assert (await s.execute(text("SELECT count(*) FROM dental_exam_files"))).scalar_one()==0


@pytest.mark.asyncio
async def test_clinical_evolution_is_append_only_and_private(clinic_db):
    d=clinic_db
    from app.dental_clinic_api import seal
    import base64
    from uuid import uuid4
    os.environ["DENTAL_DATA_KEY_B64"]=base64.b64encode(bytes(range(32))).decode()
    identity=uuid4()
    nonce,cipher,digest=seal(b'{"description":"patient synthetic"}',
                             d["a"],d["patient_a"],"progress",identity)
    factory=async_sessionmaker(d["runtime"],expire_on_commit=False)
    async with factory() as s:
        async with tenant_transaction(s,d["a"]):
            await s.execute(text("SELECT set_config('app.dental_actor',:actor,true)"),
                            {"actor":str(d["account"])})
            await s.execute(text("""
                INSERT INTO dental_records
                  (id,tenant_id,patient_id,kind,encrypted_content,nonce,digest,
                   authored_by,professional_id,entry_key)
                VALUES (:id,:t,:patient,'progress',:cipher,:nonce,:digest,
                        :actor,(SELECT professional_id FROM dental_clinicians
                                WHERE tenant_id=:t AND account_id=:actor),:key)
            """),{"id":identity,"t":d["a"],"patient":d["patient_a"],
                   "cipher":cipher,"nonce":nonce,"digest":digest,
                   "actor":d["account"],"key":str(uuid4())})
    async with factory() as s:
        async with tenant_transaction(s,d["a"]):
            await s.execute(text("SELECT set_config('app.dental_actor',:actor,true)"),
                            {"actor":str(d["account"])})
            rows=(await s.execute(text("SELECT encrypted_content FROM dental_records"))).scalars().all()
            assert len(rows)==1 and b'patient synthetic' not in bytes(rows[0])
            with pytest.raises(DBAPIError):
                await s.execute(text("""
                    UPDATE dental_records SET digest=:sha WHERE tenant_id=:tenant AND id=:id
                """),{"sha":"0"*64,"tenant":d["a"],"id":identity})


@pytest.mark.asyncio
async def test_room_exclusion_blocks_two_dentists_using_one_room(clinic_db):
    d=clinic_db
    factory=async_sessionmaker(d["runtime"],expire_on_commit=False)
    a,b=d["appt_a"],d["appt_a2"]
    async with factory() as s:
        async with tenant_transaction(s,d["a"]):
            await s.execute(text("""
                INSERT INTO dental_room_reservations
                 (tenant_id,appointment_id,room_id,period)
                VALUES (:t,:a,:r,tstzrange(now(),now()+interval '1 second','[)'))
            """),{"t":d["a"],"a":a,"r":d["room_a"]})
    async with factory() as s:
        with pytest.raises(DBAPIError):
            async with tenant_transaction(s,d["a"]):
                await s.execute(text("""
                    INSERT INTO dental_room_reservations
                     (tenant_id,appointment_id,room_id,period)
                    VALUES (:t,:a,:r,tstzrange(now(),now()+interval '1 second','[)'))
                """),{"t":d["a"],"a":b,"r":d["room_a"]})
    async with factory() as s:
        async with tenant_transaction(s,d["a"]):
            await s.execute(text("""
                INSERT INTO dental_room_reservations
                 (tenant_id,appointment_id,room_id,period)
                VALUES (:t,:a,:r,tstzrange(now(),now()+interval '1 second','[)'))
            """),{"t":d["a"],"a":b,"r":d["room_a2"]})
            assert (await s.execute(text("""
                SELECT count(*) FROM dental_room_reservations
                WHERE tenant_id=:tenant AND active
            """),{"tenant":d["a"]})).scalar_one()==2


@pytest.mark.asyncio
async def test_cancelled_appointment_releases_its_room(clinic_db):
    d=clinic_db
    factory=async_sessionmaker(d["runtime"],expire_on_commit=False)
    async with factory() as s:
        async with tenant_transaction(s,d["a"]):
            await s.execute(text("""
              INSERT INTO dental_room_reservations
                (tenant_id,appointment_id,room_id,period)
              VALUES (:t,:a,:r,tstzrange(now(),now()+interval '1 second','[)'))
            """),{"t":d["a"],"a":d["appt_a"],"r":d["room_a"]})
            await s.execute(text("""
              UPDATE appointments SET status='cancelled' WHERE tenant_id=:t AND id=:a
            """),{"t":d["a"],"a":d["appt_a"]})
            assert (await s.execute(text("""
              SELECT active FROM dental_room_reservations
              WHERE tenant_id=:t AND appointment_id=:a
            """),{"t":d["a"],"a":d["appt_a"]})).scalar_one() is False
            await s.execute(text("""
              INSERT INTO dental_room_reservations
                (tenant_id,appointment_id,room_id,period)
              VALUES (:t,:a,:r,tstzrange(now(),now()+interval '1 second','[)'))
            """),{"t":d["a"],"a":d["appt_a2"],"r":d["room_a"]})


@pytest.mark.asyncio
async def test_simultaneous_room_requests_have_only_one_winner(clinic_db):
    import asyncio
    d=clinic_db
    factory=async_sessionmaker(d["runtime"],expire_on_commit=False)
    async def reserve(appointment):
        try:
            async with factory() as session:
                async with tenant_transaction(session,d["a"]):
                    await session.execute(text("""
                        INSERT INTO dental_room_reservations
                          (tenant_id,appointment_id,room_id,period)
                        VALUES (:tenant,:appointment,:room,
                                tstzrange(now(),now()+interval '1 second','[)'))
                    """),{"tenant":d["a"],"appointment":appointment,"room":d["room_a"]})
            return True
        except DBAPIError:
            return False
    outcomes=await asyncio.wait_for(
        asyncio.gather(reserve(d["appt_a"]),reserve(d["appt_a2"])),timeout=20)
    assert sorted(outcomes)==[False,True]
    async with factory() as session:
        async with tenant_transaction(session,d["a"]):
            count=(await session.execute(text("""
                SELECT count(*) FROM dental_room_reservations
                WHERE tenant_id=:tenant AND room_id=:room AND active
            """),{"tenant":d["a"],"room":d["room_a"]})).scalar_one()
            assert count==1


@pytest_asyncio.fixture
async def clinic_api_db(clinic_db,monkeypatch):
    """Two synthetic tenants with genuine membership, active plans and dental niche."""
    from plan_fixtures import seed_fixture_plan, clear_fixture_plans
    d=clinic_db
    monkeypatch.setenv("BUSINESS_PROFILES_ENABLED","1")
    monkeypatch.setenv("ORION_DENTAL_CLINIC_ENABLED","1")
    monkeypatch.setenv("ORION_DENTAL_0076_RELEASE_ENABLED","1")
    import base64
    monkeypatch.setenv("DENTAL_DATA_KEY_B64",base64.b64encode(os.urandom(32)).decode())
    from app.catalog_api import support_session_factory
    from app.database import get_engine
    support_session_factory.cache_clear()
    get_engine.cache_clear()
    plan_actor=None
    try:
        async with d["migrator"].begin() as db:
            plan_actor=await seed_fixture_plan(db,(d["a"],d["b"]),"elite")
            for tenant,actor,role in (
                (d["a"],d["account"],"owner"),
                (d["a"],d["assistant"],"editor"),
                (d["b"],d["account_b"],"owner")
            ):
                await db.execute(text("""
                    INSERT INTO tenant_memberships(tenant_id,account_id,role)
                    VALUES (:tenant,:account,:role)
                """),{"tenant":tenant,"account":actor,"role":role})
                if role=="owner":
                    await db.execute(text("""
                      INSERT INTO business_onboarding
                        (tenant_id,created_by,configuration)
                      VALUES (:tenant,:actor,CAST(:configuration AS jsonb))
                    """),{"tenant":tenant,"actor":actor,
                          "configuration":'{"niche":"odontologia"}'})
        yield d
    finally:
        async with d["migrator"].begin() as db:
            await db.execute(text("""
                DELETE FROM tenant_memberships WHERE tenant_id IN (:a,:b)
            """),{"a":d["a"],"b":d["b"]})
            await db.execute(text("""
                DELETE FROM business_onboarding WHERE tenant_id IN (:a,:b)
            """),{"a":d["a"],"b":d["b"]})
            await clear_fixture_plans(db,(d["a"],d["b"]),(plan_actor,))
        await get_engine().dispose()
        get_engine.cache_clear()
        support_session_factory.cache_clear()


@pytest.mark.asyncio
async def test_dental_http_patient_records_exams_and_clinical_role_restrictions(clinic_api_db):
    import base64
    import httpx
    from fastapi import FastAPI
    from app.catalog_api import Actor, authenticate
    from app.dental_clinic_api import router
    import app.dental_clinic_media
    d=clinic_api_db
    api=FastAPI()
    api.include_router(router)
    api.dependency_overrides[authenticate]=lambda:Actor("member",d["a"],d["account"],"owner")
    base=f"/api/v1/tenants/{d['a']}/dental"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),
                                 base_url="http://test.invalid") as client:
        access=await client.get(base+"/access")
        assert access.status_code==200,access.text
        assert access.json()["can_read_records"] is True
        assert access.json()["electronic_signatures_homologated"] is False
        guardian="(67) 99999-0000"
        patients=[]
        for name in ("Criança Um","Criança Dois"):
            result=await client.post(base+"/patients",json={
                "name":name,"guardian_name":"Responsável comum",
                "guardian_phone":guardian})
            assert result.status_code==201,result.text
            patients.append(result.json()["id"])
        assert patients[0]!=patients[1]
        secret="Histórico clínico estritamente sintético"
        entry=str(uuid4())
        body={"kind":"anamnesis","content":{
            "complaint":secret,"answered_by":"guardian"},"entry_key":entry}
        created=await client.post(base+"/patients/"+patients[0]+"/records",json=body)
        assert created.status_code==201,created.text
        assert created.json()["qualified_signature"] is False
        repeat=await client.post(base+"/patients/"+patients[0]+"/records",json=body)
        assert repeat.status_code==201 and repeat.json()["id"]==created.json()["id"]
        incompatible=await client.post(base+"/patients/"+patients[0]+"/records",json={
            **body,"content":{"complaint":"Outro conteúdo","answered_by":"patient"}})
        assert incompatible.status_code==409
        records=await client.get(base+"/patients/"+patients[0]+"/records")
        assert records.status_code==200,records.text
        assert records.json()["items"][0]["content"]["complaint"]==secret
        assert "no-store" in records.headers["cache-control"]
        no_record=await client.get(base+"/patients/"+patients[1]+"/records")
        assert no_record.status_code==200 and no_record.json()["items"]==[]
        invalid=await client.post(base+"/patients/"+patients[0]+"/records",json={
            "kind":"anamnesis","content":{"complaint":"SEGREDO-NAO-ECOAR"},
            "entry_key":str(uuid4())})
        assert invalid.status_code==422
        assert "SEGREDO-NAO-ECOAR" not in invalid.text
        raw=b"%PDF-1.4\\n1 0 obj\\n<<>>\\nendobj\\n"
        upload=await client.post(base+"/patients/"+patients[0]+"/exams",json={
            "filename":"exame.pdf","media_type":"application/pdf",
            "data_base64":base64.b64encode(raw).decode()})
        assert upload.status_code==201,upload.text
        exam_id=upload.json()["id"]
        opened=await client.get(base+"/patients/"+patients[0]+"/exams/"+exam_id)
        assert opened.status_code==200 and opened.content==raw
        assert "no-store" in opened.headers["cache-control"]
        assert "attachment" in opened.headers["content-disposition"]
        api.dependency_overrides[authenticate]=lambda:Actor(
            "member",d["a"],d["assistant"],"editor")
        administrative=await client.get(base+"/patients/"+patients[0])
        assert administrative.status_code==200,administrative.text
        forbidden_records=await client.get(base+"/patients/"+patients[0]+"/records")
        forbidden_exams=await client.get(base+"/patients/"+patients[0]+"/exams")
        assert forbidden_records.status_code==403
        assert forbidden_exams.status_code==403
        api.dependency_overrides[authenticate]=lambda:Actor(
            "member",d["b"],d["account_b"],"owner")
        other=await client.get(base+"/patients/"+patients[0])
        assert other.status_code==403


@pytest.mark.asyncio
async def test_dental_http_schedule_select_and_conflict_is_real(clinic_api_db):
    import httpx
    from fastapi import FastAPI
    from app.catalog_api import Actor, authenticate
    from app.dental_clinic_api import router
    import app.dental_clinic_media
    from zoneinfo import ZoneInfo
    d=clinic_api_db
    api=FastAPI()
    api.include_router(router)
    api.dependency_overrides[authenticate]=lambda:Actor("member",d["a"],d["account"],"owner")
    base=f"/api/v1/tenants/{d['a']}/dental"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),
                                 base_url="http://test.invalid") as client:
        day=d["when"].astimezone(ZoneInfo("America/Campo_Grande")).date()
        schedule=await client.get(base+"/appointments",params={"day":day.isoformat()})
        assert schedule.status_code==200,schedule.text
        assert len(schedule.json()["items"])==2
        room=await client.post(base+"/rooms",json={"name":"Sala Clínica"})
        assert room.status_code==201,room.text
        rid=room.json()["id"]
        first=await client.put(base+"/appointments/"+str(d["appt_a"])+"/room",
                               json={"room_id":rid})
        assert first.status_code==200,first.text
        second=await client.put(base+"/appointments/"+str(d["appt_a2"])+"/room",
                                json={"room_id":rid})
        assert second.status_code==409,second.text
        rooms=await client.get(base+"/appointments/"+str(d["appt_a"])+"/room")
        assert rooms.status_code==200 and rooms.json()["reservation"]["room_id"]==rid


@pytest.mark.asyncio
async def test_owner_manages_chairs_without_opening_clinical_records(clinic_api_db):
    import httpx
    from fastapi import FastAPI
    from app.catalog_api import Actor, authenticate
    from app.dental_clinic_api import router

    d = clinic_api_db
    app = FastAPI()
    app.include_router(router)
    actor = Actor("member", d["a"], d["account"], "owner")
    app.dependency_overrides[authenticate] = lambda: actor
    primary = f"/api/v1/tenants/{d['a']}/dental/chair-settings"
    secondary = f"/api/v1/tenants/{d['b']}/dental/chair-settings"

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test.invalid") as client:
        assert (await client.get(secondary)).status_code == 403
        created = await client.post(primary, json={"name": "Cadeira sintética"})
        assert created.status_code == 201, created.text
        chair_id = created.json()["id"]
        chair_path = primary + "/" + chair_id
        assert (await client.post(primary, json={"name": "Cadeira sintética"})).status_code == 409
        renamed = await client.patch(chair_path,
            json={"name": "Cadeira de avaliação", "expected_name": "Cadeira sintética"})
        assert renamed.status_code == 200, renamed.text
        assert renamed.json() == {"id": chair_id, "active": True, "name": "Cadeira de avaliação"}
        assert (await client.patch(chair_path,
            json={"name": "Nome antigo", "expected_name": "Cadeira sintética"})).status_code == 409
        assert (await client.get(primary)).headers["cache-control"].startswith("no-store")

        actor = Actor("member", d["a"], d["assistant"], "editor")
        assert (await client.get(primary)).status_code == 403
        assert (await client.patch(chair_path,
            json={"name": "Recusado", "expected_name": "Cadeira de avaliação"})).status_code == 403
        actor = Actor("member", d["b"], d["account_b"], "owner")
        assert (await client.get(secondary)).json() == {"items": []}
        assert (await client.patch(secondary + "/" + chair_id,
            json={"name": "Recusado", "expected_name": "Cadeira de avaliação"})).status_code == 404
        async with d["migrator"].begin() as db:
            await db.execute(text("""UPDATE business_onboarding
                SET configuration='{"niche":"barbearia"}'::jsonb WHERE tenant_id=:tenant
            """), {"tenant": d["b"]})
        assert (await client.get(secondary)).status_code == 404


@pytest.mark.asyncio
async def test_chair_inactivation_preserves_reservations_and_serializes_with_booking(clinic_api_db):
    import asyncio
    import httpx
    from fastapi import FastAPI
    from app.catalog_api import Actor, authenticate
    from app.dental_clinic_api import router

    d = clinic_api_db
    api = FastAPI()
    api.include_router(router)
    api.dependency_overrides[authenticate] = lambda: Actor("member", d["a"], d["account"], "owner")
    base = f"/api/v1/tenants/{d['a']}/dental"
    appointment = f"{base}/appointments/{d['appt_a']}/room"
    availability = f"{base}/chair-settings/{d['room_a']}/availability"

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),
                                 base_url="http://test.invalid") as client:
        assert (await client.put(appointment, json={"room_id": str(d["room_a"])})).status_code == 200
        assert (await client.patch(availability, json={"active": False})).status_code == 409

        factory = async_sessionmaker(d["runtime"], expire_on_commit=False)
        async with factory() as db:
            async with tenant_transaction(db, d["a"]):
                await db.execute(text("""
                    UPDATE appointments SET status='completed' WHERE tenant_id=:tenant AND id=:id
                """), {"tenant": d["a"], "id": d["appt_a"]})
        inactive = await client.patch(availability, json={"active": False})
        assert inactive.status_code == 200 and inactive.json()["active"] is False
        async with factory() as db:
            async with tenant_transaction(db, d["a"]):
                await db.execute(text("""
                    UPDATE appointments SET start_at=start_at WHERE tenant_id=:tenant AND id=:id
                """), {"tenant": d["a"], "id": d["appt_a"]})
        assert (await client.get(appointment)).json()["reservation"]["room_id"] == str(d["room_a"])
        another = f"{base}/appointments/{d['appt_a2']}/room"
        assert (await client.put(another, json={"room_id": str(d["room_a"])})).status_code == 409
        active = await client.patch(availability, json={"active": True})
        assert active.status_code == 200 and active.json()["active"] is True

        second = f"{base}/chair-settings/{d['room_a2']}/availability"
        booked, retired = await asyncio.wait_for(asyncio.gather(
            client.put(another, json={"room_id": str(d["room_a2"])}),
            client.patch(second, json={"active": False}),
        ), timeout=15)
        assert sorted([booked.status_code, retired.status_code]) == [200, 409]


@pytest.mark.asyncio
async def test_intake_configuration_is_versioned_and_private(clinic_api_db):
    import httpx
    from fastapi import FastAPI
    from app.catalog_api import Actor, authenticate
    from app.dental_clinic_api import router

    d = clinic_api_db
    app = FastAPI()
    app.include_router(router)
    actor = Actor("member", d["a"], d["account"], "owner")
    app.dependency_overrides[authenticate] = lambda: actor
    base = f"/api/v1/tenants/{d['a']}/dental/intake-templates"
    other = f"/api/v1/tenants/{d['b']}/dental/intake-templates"
    question = {"prompt": "Pergunta sintética de teste", "active": True}
    payload = {"name": "Modelo sintético", "questions": [question]}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test.invalid") as client:
        assert (await client.get(base)).json() == {"items": []}
        duplicate = await client.post(base, json={**payload, "questions": [question, question]})
        assert duplicate.status_code == 422
        assert question["prompt"] not in duplicate.text
        c = await client.post(base, json=payload)
        assert c.status_code == 201, c.text
        template_path = base + "/" + c.json()["id"]
        assert (await client.post(base, json=payload)).status_code == 409
        updated_payload = {**payload, "name": "Modelo revisado", "expected_version": 1}
        updated = await client.patch(template_path, json=updated_payload)
        assert updated.status_code == 200 and updated.json()["version"] == 2
        assert (await client.patch(template_path, json=updated_payload)).json()["version"] == 2
        assert (await client.patch(template_path, json={**updated_payload, "name": "Versão antiga"})).status_code == 409
        assert (await client.get(other)).status_code == 403
        actor = Actor("member", d["b"], d["account_b"], "owner")
        assert (await client.get(other)).json() == {"items": []}
        assert (await client.patch(other+"/"+c.json()["id"], json=updated_payload)).status_code == 404
        actor = Actor("member", d["a"], d["assistant"], "editor")
        assert (await client.get(base)).status_code == 403

    factory = async_sessionmaker(d["runtime"], expire_on_commit=False)
    async with factory() as db:
        async with tenant_transaction(db, d["a"]):
            templates = (await db.execute(text("SELECT count(*) FROM dental_intake_templates"))).scalar_one()
            assert templates == 0


@pytest.mark.asyncio
async def test_anamnesis_uses_canonical_template_snapshot_and_preserves_history(clinic_api_db):
    import httpx
    from fastapi import FastAPI
    from app.catalog_api import Actor, authenticate
    from app.dental_clinic_api import router

    d = clinic_api_db
    app = FastAPI()
    app.include_router(router)
    actor = Actor("member", d["a"], d["account"], "owner")
    app.dependency_overrides[authenticate] = lambda: actor
    base = f"/api/v1/tenants/{d['a']}/dental"
    record_path = f"{base}/patients/{d['patient_a']}/records"
    prompt = "Pergunta de demonstração"
    answer = "Resposta inteiramente sintética"
    original = {"name": "Modelo de demonstração",
                "questions": [{"prompt": prompt, "active": True},
                              {"prompt": "Inativa", "active": False}]}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test.invalid") as client:
        created = await client.post(base+"/intake-templates", json=original)
        assert created.status_code == 201, created.text
        template_id = created.json()["id"]
        templates = await client.get(base+"/intake-templates/clinical")
        assert templates.json()["items"][0]["questions"] == [prompt]
        content = {"complaint": "Descrição sintética", "answered_by": "clinician",
                   "intake_template": {"template_id": template_id, "version": 1, "answers": [answer]}}
        booking = {"kind": "anamnesis", "entry_key": "synthetic-intake-record", "content": content}
        saved = await client.post(record_path, json=booking)
        assert saved.status_code == 201, saved.text
        record_id = saved.json()["id"]

        modified = await client.patch(base+"/intake-templates/"+template_id,
            json={**original, "questions": [{"prompt": "Pergunta revisada", "active": True}],
                  "expected_version": 1})
        assert modified.status_code == 200, modified.text
        retry = await client.post(record_path, json=booking)
        assert retry.status_code == 201 and retry.json()["id"] == record_id
        conflicting_retry = {**booking, "content": {**content, "complaint": "Descrição diferente"}}
        assert (await client.post(record_path, json=conflicting_retry)).status_code == 409
        stale = await client.post(record_path, json={**booking, "entry_key": "new-stale-intake"})
        assert stale.status_code == 409
        history = await client.get(record_path, params={"kind": "anamnesis"})
        assert history.json()["items"][0]["content"]["intake_template"] == {
            "template_id": template_id, "version": 1, "name": original["name"],
            "responses": [{"prompt": prompt, "answer": answer}],
        }

        actor = Actor("member", d["a"], d["assistant"], "editor")
        assert (await client.get(base+"/intake-templates/clinical")).status_code == 403
        assert (await client.get(record_path)).status_code == 403

    async with d["migrator"].begin() as db:
        encrypted = (await db.execute(text("""
            SELECT encrypted_content FROM dental_records WHERE id=CAST(:id AS uuid)
        """), {"id": record_id})).scalar_one()
        assert answer.encode() not in encrypted and prompt.encode() not in encrypted


@pytest.mark.asyncio
async def test_dental_professional_portal_link_clinician_permission_and_revocation(clinic_api_db):
    """A real invited dentist uses their professional link; cannot access other tenants."""
    import httpx
    from fastapi import FastAPI
    from app.catalog_api import Actor,authenticate
    from app.dental_clinic_api import router as dental_router
    from app.professional_portal import router as portal_router
    from app.plan_routing import include_plan_router
    import app.dental_clinic_media

    d=clinic_api_db
    professional_account=uuid4()
    email=str(professional_account)+"@example.invalid"
    async with d["migrator"].begin() as db:
        await db.execute(text("""
            INSERT INTO panel_accounts(id,name,email,password_hash,verified_at)
            VALUES (:id,'Cirurgião sintético',:email,'synthetic-invalid-hash',clock_timestamp())
        """),{"id":professional_account,"email":email})
        await db.execute(text("""
            INSERT INTO tenant_memberships(tenant_id,account_id,role)
            VALUES (:tenant,:account,'professional')
        """),{"tenant":d["a"],"account":professional_account})
        await db.execute(text("""
            INSERT INTO professional_portal_links
              (tenant_id,professional_id,email,account_id,active)
            VALUES (:tenant,:pro,:email,:account,true)
        """),{"tenant":d["a"],"pro":d["pro_a"],
               "email":email,"account":professional_account})
    try:
        app=FastAPI()
        # Exercise the same central feature guard used by app.main in production.
        include_plan_router(app,dental_router)
        app.include_router(portal_router)
        base=f"/api/v1/tenants/{d['a']}/dental"
        app.dependency_overrides[authenticate]=lambda:Actor(
            "member",d["a"],professional_account,"professional")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test.invalid") as client:
            before=await client.get("/api/v1/professional/dental/context")
            assert before.status_code==200 and before.json()["enabled"] is False
            access=await client.get(base+"/access")
            assert access.status_code==200 and access.json()["clinical_access"] is None
            denied=await client.get(base+"/patients")
            assert denied.status_code==403

            app.dependency_overrides[authenticate]=lambda:Actor(
                "member",d["a"],d["account"],"owner")
            mismatched=await client.put(base+"/clinicians",json={
                "account_id":str(professional_account),
                "professional_id":str(d["pro_a2"]),"level":"dentist",
                "cro_number":"12345","cro_state":"MS",
                "professional_license_checked":True})
            assert mismatched.status_code==422
            granted=await client.put(base+"/clinicians",json={
                "account_id":str(professional_account),
                "professional_id":str(d["pro_a"]),"level":"dentist",
                "cro_number":"12345","cro_state":"MS",
                "professional_license_checked":True})
            assert granted.status_code==200,granted.text

            app.dependency_overrides[authenticate]=lambda:Actor(
                "member",d["a"],professional_account,"professional")
            approved=await client.get("/api/v1/professional/dental/context")
            assert approved.status_code==200 and approved.json()=={
                "enabled":True,"tenant_id":str(d["a"])}
            patient=await client.get(base+"/patients")
            assert patient.status_code==200,patient.text
            assert patient.json()["total"]==1
            other=await client.get(f"/api/v1/tenants/{d['b']}/dental/patients")
            assert other.status_code==403

            app.dependency_overrides[authenticate]=lambda:Actor(
                "member",d["a"],d["account"],"owner")
            revoke=await client.delete(base+"/clinicians/"+str(professional_account))
            assert revoke.status_code==200
            app.dependency_overrides[authenticate]=lambda:Actor(
                "member",d["a"],professional_account,"professional")
            after=await client.get("/api/v1/professional/dental/context")
            assert after.status_code==200 and after.json()["enabled"] is False
            revoked=await client.get(base+"/patients")
            assert revoked.status_code==403

            async with d["migrator"].begin() as db:
                await db.execute(text("""
                    UPDATE professional_portal_links SET active=false
                    WHERE tenant_id=:tenant AND account_id=:account
                """),{"tenant":d["a"],"account":professional_account})
            disabled=await client.get("/api/v1/professional/dental/context")
            assert disabled.status_code==403
    finally:
        async with d["migrator"].begin() as db:
            await db.execute(text("""
                DELETE FROM dental_access_audit
                WHERE tenant_id=:tenant AND actor_account_id=:account
            """),{"tenant":d["a"],"account":professional_account})
            await db.execute(text("""
                DELETE FROM dental_clinicians
                WHERE tenant_id=:tenant AND account_id=:account
            """),{"tenant":d["a"],"account":professional_account})
            await db.execute(text("""
                DELETE FROM professional_portal_links
                WHERE tenant_id=:tenant AND account_id=:account
            """),{"tenant":d["a"],"account":professional_account})
            await db.execute(text("""
                DELETE FROM tenant_memberships
                WHERE tenant_id=:tenant AND account_id=:account
            """),{"tenant":d["a"],"account":professional_account})
            await db.execute(text("DELETE FROM panel_accounts WHERE id=:account"),
                             {"account":professional_account})


@pytest.mark.asyncio
async def test_supply_history_paginates_filters_and_isolates_clinics(clinic_api_db):
    import httpx
    from fastapi import FastAPI
    from app.catalog_api import Actor, authenticate
    from app.dental_clinic_api import router

    d = clinic_api_db
    app = FastAPI()
    app.include_router(router)
    actor = Actor("member", d["a"], d["account"], "owner")
    app.dependency_overrides[authenticate] = lambda: actor
    base = f"/api/v1/tenants/{d['a']}/dental/supplies"
    other = f"/api/v1/tenants/{d['b']}/dental/supplies"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test.invalid") as client:
        supply = await client.post(base, json={"name": "Gaze sintética", "unit": "box"})
        assert supply.status_code == 201, supply.text
        supply_id = supply.json()["id"]
        for name in ("Algodão de teste", "Gaze 50% sintética", "Gaze 100% sintética"):
            assert (await client.post(base, json={"name": name, "unit": "unit"})).status_code == 201
        first_material = (await client.get(base, params={"q":"gAze", "order":"desc", "limit":1})).json()
        assert [r["name"] for r in first_material["items"]] == ["Gaze sintética"]
        next_materials = (await client.get(base, params={
            "q":"gaze", "order":"desc", "cursor":first_material["next_cursor"]
        })).json()
        assert [r["name"] for r in next_materials["items"]] == ["Gaze 50% sintética", "Gaze 100% sintética"]
        percentages = (await client.get(base, params={"q":"%"})).json()["items"]
        assert len(percentages) == 2
        assert not (await client.get(base, params={"q":"inexistente"})).json()["items"]
        assert (await client.get(base, params={"q":"Algodão", "cursor":supply_id})).status_code == 400
        assert (await client.get(base, params={"order":"inválido"})).status_code == 422
        path = f"{base}/{supply_id}/movements"
        assert (await client.get(path)).json() == {"items": [], "next_cursor": None}
        for direction, quantity, key in (("in", 10, "synthetic-initial-stock"),
                                          ("out", 2, "synthetic-stock-withdrawal"),
                                          ("in", 5, "synthetic-stock-replenishment")):
            movement = {"movement": direction, "quantity": quantity, "entry_key": key}
            assert (await client.post(path, json=movement)).status_code == 200
            assert (await client.post(path, json=movement)).json()["replayed"] is True
        first = await client.get(path, params={"limit": 2})
        assert first.status_code == 200, first.text
        assert "no-store" in first.headers["cache-control"]
        first_page = first.json()
        assert [(item["movement"], item["quantity"]) for item in first_page["items"]] == [("in", 5), ("out", 2)]
        assert set(first_page["items"][0]) == {"id", "movement", "quantity", "occurred_at"}
        assert first_page["next_cursor"] == first_page["items"][-1]["id"]
        next_page = (await client.get(path, params={"cursor": first_page["next_cursor"]})).json()
        assert [item["quantity"] for item in next_page["items"]] == [10]
        assert next_page["next_cursor"] is None
        assert (await client.get(path, params={"movement": "in", "cursor": first_page["next_cursor"]})).status_code == 400
        actor = Actor("member", d["a"], d["assistant"], "editor")
        withdrawals = (await client.get(path, params={"movement": "out"})).json()
        assert [item["quantity"] for item in withdrawals["items"]] == [2]
        actor = Actor("member", d["b"], d["account_b"], "owner")
        assert (await client.get(path)).status_code == 403
        assert (await client.get(f"{other}/{supply_id}/movements")).status_code == 404


@pytest.mark.asyncio
async def test_prosthesis_worklist_preserves_versions_and_clinic_perimeters(clinic_api_db):
    import asyncio

    import httpx
    from fastapi import FastAPI
    from app.catalog_api import Actor, authenticate
    from app.dental_clinic_api import router

    d = clinic_api_db
    app = FastAPI()
    app.include_router(router)
    actor = Actor("member", d["a"], d["account"], "owner")
    app.dependency_overrides[authenticate] = lambda: actor
    base = f"/api/v1/tenants/{d['a']}/dental"
    other = f"/api/v1/tenants/{d['b']}/dental"
    history_path = f"{base}/patients/{d['patient_a']}/records"
    work_path = f"{base}/prostheses"
    request = {"kind": "prosthesis", "entry_key": "synthetic-prosthesis-first",
               "content": {"laboratory": "Laboratório sintético", "description": "Peça de teste",
                           "due_date": "2027-01-10", "status": "requested"}}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test.invalid") as client:
        created = await client.post(history_path, json=request)
        assert created.status_code == 201, created.text
        initial = created.json()["id"]
        assert (await client.post(history_path, json=request)).json()["id"] == initial

        second = await client.post(history_path, json={
            **request, "entry_key": "synthetic-prosthesis-legacy",
            "content": {**request["content"], "status": "delivered",
                        "description": "Peça registrada antes da mudança"}
        })
        assert second.status_code == 201, second.text
        legacy = second.json()["id"]
        page = await client.get(work_path, params={"limit": 1})
        assert page.status_code == 200, page.text
        assert "no-store" in page.headers["cache-control"]
        assert page.json()["items"][0]["id"] == legacy
        older = await client.get(work_path, params={"cursor": page.json()["next_cursor"]})
        assert [row["id"] for row in older.json()["items"]] == [initial]
        found = await client.get(work_path, params={"status": "requested", "limit": 1})
        assert [row["id"] for row in found.json()["items"]] == [initial]

        async def transition(record_id, status, key):
            return await client.patch(f"{work_path}/{record_id}/status",
                                      json={"status": status, "entry_key": key})

        blocked = await transition(initial, "completed", "synthetic-invalid-step")
        assert blocked.status_code == 409
        prepared = await transition(initial, "ready_to_send", "synthetic-ready-step")
        assert prepared.status_code == 200, prepared.text
        prepared_id = prepared.json()["id"]
        assert (await transition(initial, "ready_to_send", "synthetic-ready-step")).json()["id"] == prepared_id
        assert (await transition(initial, "cancelled", "synthetic-stale-step")).status_code == 409

        contenders = await asyncio.wait_for(asyncio.gather(
            transition(prepared_id, "working", "synthetic-lab-step"),
            transition(prepared_id, "cancelled", "synthetic-cancel-step"),
        ), timeout=12)
        assert sorted(result.status_code for result in contenders) == [200, 409]
        result = next(result.json() for result in contenders if result.status_code == 200)
        assert result["status"] in {"working", "cancelled"}

        returned = await transition(legacy, "working", "synthetic-return-to-lab")
        assert returned.status_code == 200, returned.text
        received = await transition(returned.json()["id"], "delivered", "synthetic-received")
        complete = await transition(received.json()["id"], "completed", "synthetic-complete")
        assert complete.status_code == 200, complete.text
        assert (await transition(complete.json()["id"], "working", "synthetic-after-complete")).status_code == 409

        overview = (await client.get(work_path)).json()["items"]
        assert {item["id"] for item in overview} == {complete.json()["id"], result["id"]}
        assert all(item["content"]["due_date"] == "2027-01-10" for item in overview)
        assert (await client.get(work_path, params={"status": "requested"})).json()["items"] == []

        history = (await client.get(history_path, params={"kind": "prosthesis"})).json()["items"]
        by_id = {row["id"]: row for row in history}
        assert len(history) == 7
        assert by_id[initial]["content"]["status"] == "requested"
        assert by_id[prepared_id]["previous_prosthesis_id"] == initial
        assert by_id[result["id"]]["previous_prosthesis_id"] == prepared_id

        actor = Actor("member", d["a"], d["assistant"], "editor")
        assert (await client.get(work_path)).status_code == 403
        assert (await transition(prepared_id, "working", "synthetic-assistant-step")).status_code == 403
        actor = Actor("member", d["b"], d["account_b"], "owner")
        assert (await client.get(work_path)).status_code == 403
        assert (await client.get(other+"/prostheses")).json()["items"] == []
        assert (await client.get(other+"/prostheses", params={"cursor": initial})).status_code == 400
        assert (await client.patch(other+f"/prostheses/{initial}/status",
                                   json={"status": "ready_to_send", "entry_key": "synthetic-another-clinic"})).status_code == 404

    async with d["migrator"].connect() as db:
        protected = (await db.execute(text("""
            SELECT encrypted_content FROM dental_records WHERE tenant_id=:tenant AND kind='prosthesis'
        """), {"tenant": d["a"]})).scalars().all()
        assert len(protected) == 7
        assert all(b"Laborat" not in ciphertext and b"requested" not in ciphertext for ciphertext in protected)
        audit_events = await db.scalar(text("""
            SELECT count(*) FROM dental_access_audit
            WHERE tenant_id=:tenant AND action='prosthesis_status_changed'
        """), {"tenant": d["a"]})
        assert audit_events == 5


@pytest.mark.asyncio
async def test_first_key_preflight_reads_presence_only_with_verified_migrator_scope(clinic_db):
    from scripts.check_dental_key_history import ciphertext_exists

    d = clinic_db
    migration_url = os.environ["MIGRATION_DATABASE_URL"]
    assert ciphertext_exists(migration_url) is False
    runtime_url = os.environ["APP_DATABASE_URL"].replace("+asyncpg", "+psycopg", 1)
    with pytest.raises(DBAPIError):
        ciphertext_exists(runtime_url)
    async with d["migrator"].begin() as db:
        await db.execute(text("""
            INSERT INTO dental_records
            (tenant_id,patient_id,kind,encrypted_content,nonce,digest,authored_by,professional_id,entry_key)
            VALUES (:tenant,:patient,'prosthesis',:cipher,:nonce,:digest,:author,:professional,:key)
        """), {"tenant": d["b"], "patient": d["patient_b"], "cipher": b"x" * 17,
               "nonce": b"x" * 12, "digest": "0" * 64, "author": d["account_b"],
               "professional": d["pro_b"], "key": "synthetic-ciphertext-first-key-check"})
    assert ciphertext_exists(migration_url) is True
    async with d["migrator"].begin() as db:
        await db.execute(text("DELETE FROM dental_records WHERE tenant_id=:tenant"), {"tenant": d["b"]})
        await db.execute(text("""
            INSERT INTO dental_exam_files
            (tenant_id,patient_id,filename,media_type,byte_count,encrypted_content,nonce,digest,authored_by)
            VALUES (:tenant,:patient,'exame-sintetico.pdf','application/pdf',1,:cipher,:nonce,:digest,:author)
        """), {"tenant": d["b"], "patient": d["patient_b"], "cipher": b"x" * 17,
               "nonce": b"x" * 12, "digest": "0" * 64, "author": d["account_b"]})
    assert ciphertext_exists(migration_url) is True


@pytest.mark.asyncio
async def test_laboratory_directory_is_private_versioned_and_keeps_prior_clinical_text(clinic_api_db):
    import httpx
    from fastapi import FastAPI
    from app.catalog_api import Actor, authenticate
    from app.dental_clinic_api import router

    d = clinic_api_db
    app = FastAPI()
    app.include_router(router)
    actor = Actor("member", d["a"], d["account"], "owner")
    app.dependency_overrides[authenticate] = lambda: actor
    base = f"/api/v1/tenants/{d['a']}/dental"
    other = f"/api/v1/tenants/{d['b']}/dental/laboratories"
    directory = base + "/laboratories"
    initial = {"name": "Laboratório Sintético", "phone": "(67) 99999-0000"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test.invalid") as client:
        created = await client.post(directory, json=initial)
        assert created.status_code == 201, created.text
        lab_id = created.json()["id"]
        item = f"{directory}/{lab_id}"
        duplicate = await client.post(directory, json={**initial, "name": "laboratório sintético"})
        assert duplicate.status_code == 409
        assert (await client.post(directory, json={"name":"Inválido", "phone":"--------"})).status_code == 422

        records = f"{base}/patients/{d['patient_a']}/records"
        written = await client.post(records, json={"kind":"prosthesis", "entry_key":"synthetic-lab-snapshot",
            "content":{"laboratory":initial["name"], "description":"Prótese fictícia"}})
        assert written.status_code == 201, written.text

        update = {"name": "Laboratório Revisado", "phone": None, "active": False, "expected_version": 1}
        edited = await client.patch(item, json=update)
        assert edited.status_code == 200, edited.text
        assert edited.json()["version"] == 2 and edited.json()["active"] is False
        assert (await client.patch(item, json=update)).json()["version"] == 2
        assert (await client.patch(item, json={**update, "active": True})).status_code == 409
        live = await client.patch(item, json={**update, "active": True, "expected_version": 2})
        assert live.json()["version"] == 3 and live.json()["active"] is True
        assert (await client.get(records)).json()["items"][0]["content"]["laboratory"] == initial["name"]

        actor = Actor("member", d["a"], d["assistant"], "editor")
        assert (await client.get(directory)).status_code == 403
        actor = Actor("member", d["b"], d["account_b"], "owner")
        assert (await client.get(directory)).status_code == 403
        assert (await client.get(other)).json() == {"items": []}
        assert (await client.patch(f"{other}/{lab_id}", json=update)).status_code == 404

    factory = async_sessionmaker(d["runtime"], expire_on_commit=False)
    async with factory() as db:
        async with tenant_transaction(db, d["a"]):
            assert await db.scalar(text("SELECT count(*) FROM dental_laboratories")) == 0
            await db.execute(text("SELECT set_config('app.dental_actor',:actor,true)"),
                             {"actor": str(d["assistant"])})
            assert await db.scalar(text("SELECT count(*) FROM dental_laboratories")) == 0


@pytest.mark.asyncio
async def test_dental_reception_aggregates_real_slots_in_the_clinic_day(clinic_db):
    from datetime import time
    from zoneinfo import ZoneInfo
    from app.dental_clinic_api import reception_snapshot

    d = clinic_db
    zone = ZoneInfo("America/Campo_Grande")
    date_at_clinic = d["when"].astimezone(zone).date()
    midnight = datetime.combine(date_at_clinic, time.min, tzinfo=zone)
    # At 23:20 in MS the UTC calendar has already crossed midnight.
    start = midnight + timedelta(hours=23, minutes=20)
    async with d["migrator"].begin() as connection:
        await connection.execute(text("UPDATE tenants SET timezone=:tz WHERE id=:id"),
                                 {"tz": zone.key, "id": d["a"]})
        await connection.execute(text("""
            UPDATE appointments SET start_at=:start, end_at=:end,
                occupied_from=:start, occupied_until=:end
            WHERE tenant_id=:tenant
        """), {"start": start, "end": start+timedelta(minutes=30), "tenant": d["a"]})
    factory = async_sessionmaker(d["runtime"], expire_on_commit=False)
    async with factory() as session:
        async with tenant_transaction(session, d["a"]):
            snapshot = await reception_snapshot(session, d["a"], start-timedelta(minutes=1))
            assert snapshot["day"] == date_at_clinic
            assert snapshot["counts"] == {"confirmed":2,"completed":0,"cancelled":0,"no_show":0}
            assert len(snapshot["upcoming"]) == 2
            assert all(set(slot) == {"start_at","end_at","professional"} for slot in snapshot["upcoming"])
            assert datetime.fromisoformat(snapshot["upcoming"][0]["start_at"]) == start
            assert not (await reception_snapshot(session, d["a"], start+timedelta(minutes=1)))["upcoming"]
            tomorrow = await reception_snapshot(session, d["a"], midnight+timedelta(days=1))
            assert not any(tomorrow["counts"].values())
        async with tenant_transaction(session, d["b"]):
            other = await reception_snapshot(session, d["b"], start)
            assert not any(other["counts"].values()) and other["upcoming"] == []


@pytest.mark.asyncio
async def test_reception_rechecks_membership_and_niche_without_a_clinical_grant(clinic_api_db, monkeypatch):
    import httpx
    from fastapi import FastAPI
    from app.catalog_api import Actor, authenticate
    from app.dental_clinic_api import router

    d = clinic_api_db
    monkeypatch.setenv("ORION_DENTAL_CLINIC_ENABLED", "0")
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE dental_clinicians SET enabled=false WHERE tenant_id=:tenant"),
                         {"tenant": d["a"]})
    api = FastAPI()
    api.include_router(router)
    api.dependency_overrides[authenticate] = lambda: Actor("member",d["a"],d["assistant"],"editor")
    def path(tenant):
        return f"/api/v1/tenants/{tenant}/dental/reception/today"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),base_url="http://test.invalid") as client:
        result = await client.get(path(d["a"]))
        assert result.status_code == 200, result.text
        assert "no-store" in result.headers["Cache-Control"]
        assert result.json()["upcoming"] == []
        assert (await client.get(path(d["b"]))).status_code == 403
        async with d["migrator"].begin() as db:
            await db.execute(text("UPDATE business_onboarding SET configuration=CAST(:data AS jsonb) WHERE tenant_id=:tenant"),
                             {"tenant":d["a"],"data":'{"niche":"barbearia"}'})
        assert (await client.get(path(d["a"]))).status_code == 404


@pytest.mark.asyncio
async def test_explicit_visit_identity_is_audited_and_independent_of_booking_contact(clinic_api_db):
    import asyncio
    from zoneinfo import ZoneInfo

    import httpx
    from fastapi import FastAPI
    from app.catalog_api import Actor, authenticate
    from app.dental_clinic_api import router

    d = clinic_api_db
    web = FastAPI()
    web.include_router(router)
    web.dependency_overrides[authenticate] = lambda: Actor("member", d["a"], d["assistant"], "editor")
    base = f"/api/v1/tenants/{d['a']}/dental"
    appointment = str(d["appt_a"])
    endpoint = base + "/appointments/" + appointment + "/patient"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web), base_url="https://test.invalid") as client:
        # Family members use one booking contact; there is no inferred relationship.
        siblings = []
        for name in ("Paciente sintético Alfa", "Paciente sintético Beta"):
            saved = await client.post(base + "/patients", json={"name": name, "guardian_phone": "67999999999"})
            assert saved.status_code == 201
            siblings.append(saved.json()["id"])
        day = d["when"].astimezone(ZoneInfo("America/Campo_Grande")).date().isoformat()
        options = await client.get(base + "/visit-options", params={"day": day, "limit": 1})
        assert options.status_code == 200, options.text
        assert options.json()["has_more"] and options.json()["items"][0]["version"] == 0
        page2 = await client.get(base + "/visit-options", params={"day": day, "limit": 1, "offset": 1})
        assert options.json()["items"][0]["id"] != page2.json()["items"][0]["id"]
        assert all(x["patient_id"] is None for x in [*options.json()["items"], *page2.json()["items"]])
        assert (await client.get(base + "/visit-options", params={"day": "9999-12-31"})).status_code == 422

        def history(patient):
            return base + "/patients/" + patient + "/visits"

        assert (await client.get(history(siblings[0]))).json()["items"] == []
        # Two different choices from the same stale page cannot both commit.
        responses = await asyncio.gather(*(client.put(endpoint, json={"patient_id": who, "expected_version": 0})
                                           for who in siblings))
        assert sorted(r.status_code for r in responses) == [200, 409]
        winner = next(r.json()["patient_id"] for r in responses if r.status_code == 200)
        other = next(who for who in siblings if who != winner)
        replay = await client.put(endpoint, json={"patient_id": winner, "expected_version": 0})
        assert replay.json()["version"] == 1
        assert [x["id"] for x in (await client.get(history(winner))).json()["items"]] == [appointment]
        assert (await client.get(history(other))).json()["items"] == []
        foreign = await client.put(endpoint, json={"patient_id": str(d["patient_b"]), "expected_version": 1})
        assert foreign.status_code == 404
        moved = await client.put(endpoint, json={"patient_id": other, "expected_version": 1})
        assert moved.json()["version"] == 2
        assert (await client.get(history(winner))).json()["items"] == []
        assert [x["id"] for x in (await client.get(history(other))).json()["items"]] == [appointment]
        assert (await client.put(endpoint, json={"patient_id": None, "expected_version": 2})).json()["version"] == 3
        assert (await client.get(history(other))).json()["items"] == []
        assert (await client.get(base + "/patients/" + other + "/records")).status_code == 403
        web.dependency_overrides[authenticate] = lambda: Actor("member", d["b"], d["account_b"], "owner")
        assert (await client.get(history(other))).status_code == 403
        other_base = f"/api/v1/tenants/{d['b']}/dental"
        assert (await client.get(other_base + "/patients/" + other + "/visits")).status_code == 404
        assert (await client.put(other_base + "/appointments/" + appointment + "/patient",
                                 json={"patient_id": str(d["patient_b"]), "expected_version": 0})).status_code == 404

    async with d["migrator"].connect() as db:
        events = (await db.execute(text("""SELECT patient_id,version,actor_account_id FROM dental_visit_patient_events
            WHERE tenant_id=:t AND appointment_id=:visit ORDER BY version"""),
            {"t": d["a"], "visit": d["appt_a"]})).mappings().all()
        assert [(str(e["patient_id"]) if e["patient_id"] else None, e["version"]) for e in events] == [
            (winner, 1), (other, 2), (None, 3)]
        assert all(e["actor_account_id"] == d["assistant"] for e in events)
        assert not await db.scalar(text("""SELECT has_table_privilege('app_runtime',
            'dental_visit_patient_events','UPDATE,DELETE,TRUNCATE')"""))


@pytest.mark.asyncio
async def test_patient_directory_shared_contact_name_only_and_retry(clinic_api_db):
    import asyncio
    from uuid import UUID
    import httpx
    from fastapi import FastAPI
    from app.catalog_api import Actor, authenticate
    from app.dental_clinic_api import router

    d = clinic_api_db
    api = FastAPI()
    api.include_router(router)
    api.dependency_overrides[authenticate] = lambda: Actor("member", d["a"], d["account"], "owner")
    base = f"/api/v1/tenants/{d['a']}/dental/patients"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test.invalid") as client:
        family = {"guardian_name":"Pessoa sintética", "guardian_phone":"(67) 99999-8765"}
        first = {"name":"Ana % Sintética", "request_id":str(uuid4()), **family}
        responses = await asyncio.gather(*(client.post(base, json=first) for _ in range(2)))
        assert [r.status_code for r in responses] == [201,201]
        identity = responses[0].json()["id"]
        assert identity == responses[1].json()["id"]
        changed = await client.post(base, json={**first, "name":"Outro nome"})
        assert changed.status_code == 409
        second = await client.post(base, json={"name":"Bia Sintética", "request_id":str(uuid4()), **family})
        assert second.status_code == 201 and second.json()["id"] != identity
        minimal = await client.post(base, json={"name":"Somente Nome"})
        assert minimal.status_code == 201 and minimal.json()["contact_phone"] is None
        invalid = await client.post(base, json={"name":"Telefone inválido", "contact_phone":"--------0"})
        assert invalid.status_code == 422
        search = await client.post(base + "/search", json={"query":"999998765","limit":1})
        result = search.json()
        assert search.status_code == 200 and result["has_more"]
        assert [x["id"] for x in result["items"]] == [identity]
        assert "contact_phone" not in result["items"][0]
        assert "no-store" in search.headers["cache-control"]
        next_page = (await client.post(base + "/search", json={"query":"(67) 99999-8765","limit":1,"offset":1})).json()
        assert not next_page["has_more"] and next_page["items"][0]["id"] == second.json()["id"]
        by_name = (await client.post(base + "/search", json={"query":"%"})).json()
        assert [p["id"] for p in by_name["items"]] == [identity]
        assert (await client.post(base + "/search", json={"query":"zzzz"})).json()["items"] == []
        other = f"/api/v1/tenants/{d['b']}/dental/patients/search"
        assert (await client.post(other, json={"query":"Sintética"})).status_code in (403,404)
    async with d["migrator"].begin() as db:
        event_count = (await db.execute(text("""
            SELECT count(*) FROM dental_access_audit WHERE tenant_id=:tenant AND patient_id=:id AND action='patient_created'
        """), {"tenant":d["a"],"id":UUID(identity)})).scalar_one()
        assert event_count == 1
