"""FATIA 4: CSV seguro, separação de calendários, outbox e revogação."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import os

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.calendar_provider import FakeCalendar,deterministic_event_id
from app.csv_export import safe_cell,csv_document
from app.booking_service import history
from app.database import tenant_transaction
from app.main import app


def test_csv_excel_ptbr_no_formula_injection():
    data=csv_document(["Nome","Preço"],[('=HYPERLINK("https://evil")','59,90'),
            ('  +SUM(1,2)','79,90'),('@abc','89,90'),('João; Silva','12,00')])
    assert data.startswith(b"\xef\xbb\xbf")
    content=data.decode("utf-8-sig")
    assert "'=HYPERLINK" in content
    assert "'  +SUM" in content
    assert "'@abc" in content
    assert '"João; Silva"' in content
    assert "\r\n" in content


@pytest.mark.asyncio
async def test_fake_google_two_calendars_failures_and_revocation_semantics():
    provider=FakeCalendar()
    x,y=uuid4(),uuid4()
    event={"id":x,"start_at":datetime.now(timezone.utc),"end_at":datetime.now(timezone.utc)+timedelta(hours=1)}
    await provider.apply("calendar-A",deterministic_event_id(x),"create",event)
    await provider.apply("calendar-B",deterministic_event_id(y),"create",event)
    assert len(provider.events)==2
    provider.fail=True
    with pytest.raises(RuntimeError,match="simulada"):
        await provider.apply("calendar-A",deterministic_event_id(x),"update",event)
    assert len(provider.events)==2
    provider.fail=False
    await provider.apply("calendar-A",deterministic_event_id(x),"delete",event)
    assert len(provider.events)==1
    assert ("calendar-B",deterministic_event_id(y)) in provider.events


@pytest.mark.integration
@pytest.mark.asyncio
async def test_transactional_calendar_outbox_and_rls(monkeypatch):
    migrator=os.getenv("MIGRATION_DATABASE_URL")
    runtime=os.getenv("APP_DATABASE_URL")
    if not migrator or not runtime:
        if os.getenv("REQUIRE_POSTGRES_TESTS")=="1": pytest.fail("PostgreSQL real necessário")
        pytest.skip("PostgreSQL real não conectado")
    tenant,other,person,customer,appointment=[uuid4() for _ in range(5)]
    admin=create_async_engine(migrator.replace("postgresql+psycopg://","postgresql+asyncpg://",1))
    app_engine=create_async_engine(runtime)
    now=datetime.now(timezone.utc)+timedelta(days=4)
    try:
        async with admin.begin() as s:
            for tid in (tenant,other):
                await s.execute(text("INSERT INTO tenants (id,name,slug) VALUES (:id,'Test',:slug)"),
                                {"id":tid,"slug":"fatia4-"+str(tid)})
            await s.execute(text("INSERT INTO professionals (id,tenant_id,name) VALUES (:id,:t,'Test')"),
                            {"id":person,"t":tenant})
            await s.execute(text("""
                INSERT INTO customers (id,tenant_id,name,whatsapp_e164)
                VALUES (:id,:t,'Cliente','+5567999999999')
            """),{"id":customer,"t":tenant})
            await s.execute(text("""
                INSERT INTO appointments
                (id,tenant_id,customer_id,professional_id,protocol,idempotency_key,
                 request_fingerprint,start_at,end_at,occupied_from,occupied_until,
                 status,total_cents)
                VALUES (:id,:t,:customer,:professional,'AG-ABCDEF','outbox-test','x',
                        :start,:end,:start,:end,'confirmed',3500)
            """),{"id":appointment,"t":tenant,"customer":customer,"professional":person,
                  "start":now,"end":now+timedelta(minutes=40)})
        async with app_engine.connect() as s:
            async with tenant_transaction(s,tenant):
                await history(s,tenant,appointment,"confirmed")
        async with admin.begin() as s:
            out=(await s.execute(text("""
                SELECT action,status FROM calendar_outbox WHERE tenant_id=:t
            """),{"t":tenant})).one()
            assert out==("create","pending")
        async with app_engine.connect() as s:
            async with tenant_transaction(s,tenant):
                await history(s,tenant,appointment,"rescheduled")
                await history(s,tenant,appointment,"cancelled")
        async with admin.connect() as s:
            action=(await s.execute(text("""
                SELECT action FROM calendar_outbox WHERE tenant_id=:t
            """),{"t":tenant})).scalar_one()
            assert action=="delete"
        async with app_engine.connect() as s:
            async with tenant_transaction(s,other):
                assert (await s.execute(text("SELECT id FROM calendar_outbox"))).all()==[]
                assert (await s.execute(text("SELECT id FROM google_connections"))).all()==[]
        async with app_engine.connect() as s:
            assert (await s.execute(text("SELECT id FROM calendar_outbox"))).all()==[]
        monkeypatch.delenv("GOOGLE_CLIENT_ID",raising=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
            key=os.getenv("SUPPORT_API_TOKEN")
            if key:
                response=await client.post(f"/api/v1/tenants/{tenant}/google/connect",
                                          headers={"Authorization":"Bearer "+key})
                assert response.status_code==503
                response=await client.get(f"/api/v1/tenants/{other}/export/appointments.csv",
                                          headers={"Authorization":"Bearer "+key})
                assert response.status_code==200
                assert "AG-ABCDEF" not in response.text
                resp=await client.post(f"/api/v1/tenants/{tenant}/google/revoke",
                                       headers={"Authorization":"Bearer "+key})
                assert resp.status_code==200
    finally:
        async with admin.begin() as s:
            for table in ("calendar_outbox","google_connections","appointment_history",
                          "appointment_items","appointments","customers","professionals"):
                await s.execute(text(f"DELETE FROM {table} WHERE tenant_id=:t"),{"t":tenant})
            await s.execute(text("DELETE FROM tenants WHERE id IN (:t,:other)"),{"t":tenant,"other":other})
        await admin.dispose()
        await app_engine.dispose()
