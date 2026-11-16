"""Fresh completed-visit facts under real tenant RLS; no external providers."""
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app import qr_customer
from test_management import management_db, customer, appointment

pytestmark=pytest.mark.integration
PHONE="5511912345678"


@pytest.mark.asyncio
async def test_latest_completion_is_tenant_phone_scoped_and_excludes_other_statuses(management_db):
    d=management_db
    person=await customer(d,phone=PHONE)
    old=await appointment(d,person["id"],status="completed",start_at=d["now"]-timedelta(days=7))
    latest=await appointment(d,person["id"],status="completed",start_at=d["now"]-timedelta(days=2))
    for index,status in enumerate(("confirmed","cancelled","no_show","hold")):
        await appointment(d,person["id"],status=status,start_at=d["now"]+timedelta(days=index+1))
    other_person=await customer(d,phone="5511912345679")
    await appointment(d,other_person["id"],status="completed",start_at=d["now"]-timedelta(days=1))
    other_tenant=await customer(d,other=True,phone=PHONE)
    service=uuid4()
    async with d["migrator"].begin() as db:
        await db.execute(text("INSERT INTO services(id,tenant_id,name,duration_min,price_cents) VALUES(:id,:tenant,'Other service',30,1000)"),
            {"id":service,"tenant":d["b"]})
    await appointment({**d,"a":d["b"],"pro":d["other_pro"],"service":service},
        other_tenant["id"],status="completed",start_at=d["now"]-timedelta(hours=2))
    own=await qr_customer.customer_last_completed(d["a"],PHONE)
    foreign=await qr_customer.customer_last_completed(d["b"],PHONE)
    absent=await qr_customer.customer_last_completed(d["a"],"5511912345680")
    assert str(own["id"])==latest and own["status"]=="completed" and str(own["id"])!=old
    assert foreign and foreign["id"]!=own["id"] and absent is None
    assert all(item["name"]=="Corte sintético" for item in own["services"])


@pytest.mark.asyncio
async def test_completed_items_are_snapshots_and_private_customer_fields_are_not_selected(management_db):
    d=management_db
    person=await customer(d,phone=PHONE,notes="DO_NOT_EXPOSE",birth_date="1990-03-12")
    visit=await appointment(d,person["id"],status="completed",start_at=d["now"]-timedelta(days=1))
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE services SET name='Renamed current catalog',price_cents=20000 WHERE id=:id"),{"id":d["service"]})
    current=await qr_customer.customer_last_completed(d["a"],PHONE)
    assert str(current["id"])==visit and current["services"]==[
        {"service_id":str(d["service"]),"name":"Corte sintético","duration_min":30}]
    assert not any(key in current for key in ("name","notes","birth_date","customer_id","whatsapp_e164","phone","document"))
    assert "DO_NOT_EXPOSE" not in str(current) and "1990" not in str(current)


@pytest.mark.asyncio
async def test_deleted_customer_is_not_personalized_or_exposed_by_history(management_db):
    d=management_db
    person=await customer(d,phone=PHONE,name="Deleted customer")
    visit=await appointment(d,person["id"],status="completed",start_at=d["now"]-timedelta(days=1))
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE customers SET deleted_at=now() WHERE id=:id"),{"id":UUID(person["id"])})
    assert await qr_customer.customer_name(d["a"],PHONE) is None
    assert await qr_customer.customer_appointments(d["a"],PHONE)==[]
    assert await qr_customer.appointment_services(d["a"],UUID(visit),PHONE) is None
    assert await qr_customer.customer_last_completed(d["a"],PHONE) is None


@pytest.mark.asyncio
async def test_equal_completion_times_have_deterministic_id_order_and_no_mutation(management_db):
    d=management_db
    person=await customer(d,phone=PHONE)
    first=await appointment(d,person["id"],status="completed",start_at=d["now"]-timedelta(days=1))
    second=await appointment(d,person["id"],status="completed",start_at=d["now"]-timedelta(days=1))
    expected=max((UUID(first),UUID(second)),key=lambda value:value.int)
    one=await qr_customer.customer_last_completed(d["a"],PHONE)
    two=await qr_customer.customer_last_completed(d["a"],PHONE)
    assert one["id"]==two["id"]==expected
    async with d["migrator"].connect() as db:
        count=(await db.execute(text("SELECT count(*) FROM appointments WHERE tenant_id=:tenant"),{"tenant":d["a"]})).scalar_one()
    assert count==2


@pytest.mark.asyncio
async def test_missing_completed_record_cannot_be_fabricated_from_a_past_confirmed_time(management_db):
    d=management_db
    person=await customer(d,phone=PHONE)
    await appointment(d,person["id"],status="confirmed",start_at=d["now"]-timedelta(days=3))
    assert await qr_customer.customer_last_completed(d["a"],PHONE) is None

