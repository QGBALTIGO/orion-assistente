"""End-to-end format and real PostgreSQL booking for +595 (no live sends).

A temporary customer and appointment are created only inside the CI database.
Existing BR +55 behavior is retained; all test records are scoped to a
synthetic tenant and cleaned by booking_db teardown.
"""
import os
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.management_schemas import normalize_phone
from app.catalog_schemas import Professional
from app.public_site_api import PublicBooking
from app.booking_api import HoldRequest
from app.booking_service import book_appointment, confirm_hold
from tests.integration.test_booking import booking_db,async_url,start_at

pytestmark = pytest.mark.integration
PY_MOBILE = "+595992000001"
BR_PRO = "+5521999999999"


def test_exact_explicit_paraguay_e164_only_and_brazil_preserved():
    assert normalize_phone("+595 992 000 001") == PY_MOBILE
    assert normalize_phone("+595-992-000-001") == PY_MOBILE
    assert normalize_phone("21999999999") == BR_PRO
    assert normalize_phone("(21) 99999-9999") == BR_PRO
    assert normalize_phone("+55 21 99999 9999") == BR_PRO
    assert Professional(name="Profissional sintético",phone_e164="21999999999").phone_e164 == BR_PRO
    assert PublicBooking(quote="x"*80,start_at="2030-01-10T10:00:00-04:00",
        customer_name="Cliente teste",customer_phone="+595 992 000 001",confirmed=True).customer_phone == PY_MOBILE
    assert HoldRequest(professional_id=uuid4(),service_ids=[uuid4()],
        customer_name="Cliente teste",customer_phone=PY_MOBILE,
        start_at="2030-01-10T10:00:00-04:00",idempotency_key="paraguay-test-001").customer_phone == PY_MOBILE
    for bad in ("595992000001","+595 899 000 001","+59599200001",
                "+595992000001999","+59599900000","00595992000001","+54 11 99123456"):
        with pytest.raises((ValueError,TypeError)):
            normalize_phone(bad)


@pytest.mark.asyncio
async def test_real_engine_creates_and_confirms_paraguay_client_and_admin_professional(booking_db):
    d=booking_db
    async with d["migrator"].begin() as db:
        await db.execute(text("""UPDATE professionals SET phone_e164=:p
            WHERE tenant_id=:tenant AND id=:professional"""),
            {"p":BR_PRO,"tenant":d["a"],"professional":d["pro"]})
    async with d["factory"]() as db:
        hold=await book_appointment(
            db,d["a"],d["pro"],[d["cut"]],PY_MOBILE,
            "Cliente internacional sintético",start_at(d["day"],10),
            "py-595-"+str(uuid4()))
        result=await confirm_hold(db,d["a"],hold["id"],customer_phone=PY_MOBILE,
                                  source="public_site")
    assert result["status"]=="confirmed"
    assert result["customer_phone"]==PY_MOBILE
    assert result["professional_id"]==d["pro"]
    assert result["professional_name"]=="Barbeiro sintético"
    assert result["items"][0]["name_snapshot"]=="Corte"
    async with d["migrator"].begin() as db:
        row=(await db.execute(text("""SELECT c.whatsapp_e164,a.status,p.phone_e164
            FROM appointments a
            JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
            JOIN professionals p ON p.tenant_id=a.tenant_id AND p.id=a.professional_id
            WHERE a.tenant_id=:t AND a.id=:id"""),
            {"t":d["a"],"id":result["id"]})).one()
        assert row== (PY_MOBILE,"confirmed",BR_PRO)


@pytest.mark.asyncio
async def test_verified_paraguay_sender_can_resolve_only_own_protocol_in_admin_hub(booking_db):
    d=booking_db
    async with d["factory"]() as db:
        hold=await book_appointment(
            db,d["a"],d["pro"],[d["cut"]],PY_MOBILE,"Cliente sintético",
            start_at(d["day"],11),"py-confirm-"+str(uuid4()))
        appointment=await confirm_hold(db,d["a"],hold["id"])
    support=create_async_engine(async_url(os.environ["SUPPORT_DATABASE_URL"]))
    try:
        async with support.connect() as db:
            async def lookup(phone):
                return (await db.execute(text("""
                    SELECT tenant_id,appointment_id
                    FROM public.orion_resolve_central_appointment(:protocol,:phone)"""),
                   {"protocol":appointment["protocol"],"phone":phone})).mappings().all()
            matched=await lookup(PY_MOBILE)
            assert len(matched)==1
            assert matched[0]["tenant_id"]==d["a"]
            assert matched[0]["appointment_id"]==appointment["id"]
            assert await lookup("+595992000002")==[]
            assert await lookup("+5521999999999")==[]
    finally:
        await support.dispose()


@pytest.mark.asyncio
async def test_operational_consent_can_store_paraguay_mobile_only_under_its_tenant(booking_db):
    d=booking_db
    try:
        async with d["migrator"].begin() as db:
            await db.execute(text("""INSERT INTO contact_consents
               (tenant_id,contact_e164,operational_opt_in,marketing_opt_in)
               VALUES(:t,:phone,true,false)"""),{"t":d["a"],"phone":PY_MOBILE})
            flag=(await db.execute(text("""SELECT operational_opt_in,marketing_opt_in
               FROM contact_consents WHERE tenant_id=:t AND contact_e164=:phone"""),
               {"t":d["a"],"phone":PY_MOBILE})).one()
            assert flag==(True,False)
    finally:
        async with d["migrator"].begin() as db:
            await db.execute(text("""DELETE FROM contact_consents
                WHERE tenant_id=:t AND contact_e164=:phone"""),
                {"t":d["a"],"phone":PY_MOBILE})
