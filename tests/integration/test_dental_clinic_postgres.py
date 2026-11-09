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
                       "protocol":"AG-"+str(uuid4()).replace("-","")[:6],
                       "key":str(apid),"fingerprint":"0"*64,
                       "start":when,"end":when+timedelta(minutes=40)})
            for rid,name in ((room_a,'Consultório A'),(room_a2,'Consultório B')):
                await db.execute(text("""
                    INSERT INTO dental_rooms(id,tenant_id,name)
                    VALUES (:id,:tenant,:name)
                """),{"id":rid,"tenant":a,"name":name})
        yield {"runtime":runtime,"migrator":migrator,"a":a,"b":b,
               "account":account,"assistant":assistant,"account_b":account_b,
               "patient_a":patient_a,"patient_b":patient_b,
               "appt_a":appt_a,"appt_a2":appt_a2,"room_a":room_a,
               "room_a2":room_a2,"when":when}
    finally:
        async with migrator.begin() as db:
            for tbl in ("dental_access_audit","dental_room_reservations",
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
