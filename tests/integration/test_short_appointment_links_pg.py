"""Isolated PostgreSQL proves short aliases cannot leak between businesses.

Never connect to production. All IDs/phone numbers are synthetic. Existing
appointment protocols stay unchanged. A short code never authorizes a session.
"""
from datetime import datetime,timedelta,timezone
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine,text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.pool import NullPool

pytestmark=pytest.mark.integration


def engine_from(name):
    raw=os.getenv(name)
    if not raw:
        if os.getenv("REQUIRE_POSTGRES_TESTS")=="1":
            pytest.fail("PostgreSQL isolated integration environment required")
        pytest.skip("PostgreSQL unavailable")
    if "127.0.0.1" not in raw and "localhost" not in raw:
        pytest.fail("Refusing short-link destructive synthetic test on nonlocal DB")
    return create_engine(raw.replace("postgresql+asyncpg://","postgresql+psycopg://"),
                         poolclass=NullPool)


def test_cross_tenant_duplicate_protocols_get_unique_aliases_and_private_resolver():
    mgr=engine_from("MIGRATION_DATABASE_URL")
    support=engine_from("SUPPORT_DATABASE_URL")
    runtime=engine_from("APP_DATABASE_URL")
    owners=[uuid4(),uuid4()]
    records=[]
    start=datetime.now(timezone.utc)+timedelta(days=15)
    try:
        with mgr.begin() as db:
            for index,tenant in enumerate(owners):
                customer,professional,appointment=uuid4(),uuid4(),uuid4()
                records.append((tenant,customer,professional,appointment))
                db.execute(text("""
                  INSERT INTO tenants(id,name,slug)
                  VALUES(:t,:name,:slug)
                """),{"t":tenant,"name":"QA isolated business",
                       "slug":"qa-short-"+str(tenant)})
                db.execute(text("""
                  INSERT INTO customers(id,tenant_id,name,whatsapp_e164)
                  VALUES(:id,:t,'Synthetic QA customer','+5567999999922')
                """),{"id":customer,"t":tenant})
                db.execute(text("""
                  INSERT INTO professionals(id,tenant_id,name,phone_e164)
                  VALUES(:id,:t,'Synthetic QA professional','+5538999999911')
                """),{"id":professional,"t":tenant})
                db.execute(text("""
                  INSERT INTO appointments(
                    id,tenant_id,customer_id,professional_id,protocol,
                    idempotency_key,request_fingerprint,start_at,end_at,
                    occupied_from,occupied_until,status,total_cents)
                  VALUES(:id,:t,:customer,:professional,'AG-ABCDEF',:key,:finger,
                    :start,:finish,:start,:finish,'confirmed',4000)
                """),{"id":appointment,"t":tenant,"customer":customer,
                       "professional":professional,"key":str(uuid4()),
                       "finger":"d"*64,"start":start,
                       "finish":start+timedelta(minutes=35)})
        with mgr.connect() as db:
            aliases=db.execute(text("""
              SELECT code,tenant_id,appointment_id
              FROM public.appointment_short_codes
              WHERE tenant_id IN (:a,:b) ORDER BY code
            """),{"a":owners[0],"b":owners[1]}).all()
            original=db.execute(text("""
              SELECT protocol FROM public.appointments
              WHERE tenant_id IN (:a,:b)
            """),{"a":owners[0],"b":owners[1]}).scalars().all()
        assert len(aliases)==2
        assert len(set(row.code for row in aliases))==2
        assert original==["AG-ABCDEF"]*2  # no source data was mutated
        assert any(row.code=="AG-ABCDEF" for row in aliases)
        assert any(row.code.startswith("AG-ABCDEF-") for row in aliases)
        with support.connect() as db:
            for alias in aliases:
                for role,phone in (("customer","+5567999999922"),
                                   ("professional","+5538999999911")):
                    found=db.execute(text("""
                      SELECT * FROM public.orion_resolve_short_appointment(
                         :code,:phone,:role)
                    """),{"code":alias.code,"phone":phone,"role":role}).all()
                    assert len(found)==1
                    assert found[0].tenant_id==alias.tenant_id
                    assert found[0].appointment_id==alias.appointment_id
                for phone in ("+5567999999923","+5538999999912"):
                    assert not db.execute(text("""
                      SELECT * FROM public.orion_resolve_short_appointment(
                        :code,:phone,'customer')
                    """),{"code":alias.code,"phone":phone}).all()
            assert db.execute(text("""
                SELECT has_table_privilege('app_support',
                  'public.appointment_short_codes','SELECT')
            """)).scalar_one() is False
        with runtime.connect() as db:
            assert db.execute(text("""
                SELECT has_function_privilege('app_runtime',
                  'public.orion_resolve_short_appointment(text,text,text)',
                  'EXECUTE')
            """)).scalar_one() is False
            # Ordinary runtime can only see the one verified tenant's rows.
            assert db.execute(text(
                "SELECT count(*) FROM public.appointment_short_codes")).scalar_one()==0
            db.execute(text("SELECT set_config('app.tenant_id',:t,true)"),
                       {"t":str(owners[0])})
            own=db.execute(text(
                "SELECT code FROM public.appointment_short_codes")).scalars().all()
            assert len(own)==1
            assert own[0] in {a.code for a in aliases if a.tenant_id==owners[0]}
    finally:
        with mgr.begin() as db:
            for tenant,customer,professional,appointment in records:
                db.execute(text(
                    "DELETE FROM appointments WHERE tenant_id=:t AND id=:a"),
                    {"t":tenant,"a":appointment})
                db.execute(text(
                    "DELETE FROM customers WHERE tenant_id=:t"),{"t":tenant})
                db.execute(text(
                    "DELETE FROM professionals WHERE tenant_id=:t"),{"t":tenant})
                db.execute(text(
                    "DELETE FROM tenants WHERE id=:t"),{"t":tenant})
        mgr.dispose();support.dispose();runtime.dispose()
