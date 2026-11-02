"""Real PostgreSQL/HTTP tests for professional-bound invitations and isolation.

No real email or external WhatsApp calls. The tests use synthetic accounts,
isolated tenants, RLS runtime and support roles, and the actual API routers.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text
from app.catalog_api import Actor
from tests.integration.test_management_extras import extras_db
from tests.integration.test_booking import booking_db

pytestmark = pytest.mark.integration


async def plan_for_tests(d):
    async with d["migrator"].begin() as db:
        await db.execute(text("""INSERT INTO tenant_plan_controls
            (tenant_id,manual_plan_code,manual_started_at,manual_actor_id,manual_reason)
            VALUES(:t,'essencial',now(),:actor,'CI synthetic professional portal test')
            ON CONFLICT(tenant_id) DO UPDATE SET
            manual_plan_code='essencial',manual_started_at=now()"""),
            {"t": d["tenant"], "actor":d["owner"]})


async def cleanup_portal(d):
    async with d["migrator"].begin() as db:
        await db.execute(text("DELETE FROM professional_portal_links WHERE tenant_id=:t"),
                         {"t":d["tenant"]})
        await db.execute(text("DELETE FROM tenant_plan_controls WHERE tenant_id=:t"),
                         {"t":d["tenant"]})


@pytest.mark.asyncio
async def test_verified_professional_invite_is_immediate_and_only_grants_own_data(extras_db,monkeypatch):
    d=extras_db
    monkeypatch.setenv("PANEL_PUBLIC_URL","https://orionagenda.pro")
    monkeypatch.setenv("PANEL_EMAIL_ENABLED","0")
    monkeypatch.setenv("PANEL_EMAIL_LIVE_SEND","0")
    await plan_for_tests(d)
    base=d["base"]+"/team/professionals/"+str(d["pro"])
    token=None
    try:
        owner=d["selected"]["actor"]
        invitation=await d["client"].post(base+"/invite",json={
            "email":d["guest_email"],"idempotency_key":str(uuid4())})
        assert invitation.status_code==201, invitation.text
        invite=invitation.json()
        assert invite["role"]=="professional"
        assert invite["professional_id"]==str(d["pro"])
        assert invite["email_delivery"]["status"]=="disabled"
        token=invite["link"].split("#token=",1)[1]

        preview=await d["client"].post("/api/v1/team-invitations/preview",json={"token":token})
        assert preview.status_code==200,preview.text
        assert preview.json()["role"]=="professional"
        assert preview.json()["requires_owner_approval"] is False

        d["selected"]["actor"]=Actor("member",account_id=d["guest"])
        accept=await d["client"].post("/api/v1/team-invitations/accept",json={"token":token})
        assert accept.status_code==200,accept.text
        assert accept.json()["status"]=="approved"
        assert (await d["client"].post("/api/v1/team-invitations/accept",json={"token":token})).json()["status"]=="approved"

        async with d["migrator"].connect() as db:
            membership=(await db.execute(text("""SELECT role,active FROM tenant_memberships
                WHERE tenant_id=:t AND account_id=:a"""),
                {"t":d["tenant"],"a":d["guest"]})).one()
            assert membership==("professional",True)
            link=(await db.execute(text("""SELECT professional_id,account_id,active FROM professional_portal_links
                WHERE tenant_id=:t"""),{"t":d["tenant"]})).one()
            assert link==(d["pro"],d["guest"],True)

        me=await d["client"].get("/api/v1/professional/me")
        assert me.status_code==200,me.text
        assert me.json()["id"]==str(d["pro"])
        assert me.json()["role"]=="professional"
        for path in ("/api/v1/professional/dashboard","/api/v1/professional/agenda",
                     "/api/v1/professional/finance","/api/v1/professional/goals"):
            reply=await d["client"].get(path)
            assert reply.status_code==200,(path,reply.text)
            assert d["guest_email"] not in reply.text
            assert str(d["other_pro"]) not in reply.text
        forbidden=await d["client"].get(d["base"]+"/customers")
        assert forbidden.status_code==403,forbidden.text

        d["selected"]["actor"]=owner
        members=(await d["client"].get(d["base"]+"/team")).json()
        access=next(l for l in members["professional_access"] if l["professional_id"]==str(d["pro"]))
        assert access["active"] is True
        suspended=await d["client"].patch(base+"/access",json={
            "action":"suspend","expected_updated_at":access["updated_at"]})
        assert suspended.status_code==200,suspended.text
        d["selected"]["actor"]=Actor("member",account_id=d["guest"])
        blocked=await d["client"].get("/api/v1/professional/me")
        assert blocked.status_code==403,blocked.text

        d["selected"]["actor"]=owner
        revived=await d["client"].patch(base+"/access",json={
            "action":"reactivate","expected_updated_at":suspended.json()["updated_at"]})
        assert revived.status_code==200,revived.text
        d["selected"]["actor"]=Actor("member",account_id=d["guest"])
        assert (await d["client"].get("/api/v1/professional/me")).status_code==200

        # Archiving fences out the previously approved account, even if
        # another edit later reactivates the operational professional record.
        async with d["migrator"].begin() as db:
            await db.execute(text("""UPDATE professionals SET active=false
                WHERE tenant_id=:t AND id=:p"""),
                {"t":d["tenant"],"p":d["pro"]})
        assert (await d["client"].get("/api/v1/professional/me")).status_code==403
        async with d["migrator"].begin() as db:
            await db.execute(text("""UPDATE professionals SET active=true
                WHERE tenant_id=:t AND id=:p"""),
                {"t":d["tenant"],"p":d["pro"]})
        assert (await d["client"].get("/api/v1/professional/me")).status_code==403
        d["selected"]["actor"]=owner
        reenabled=await d["client"].patch(base+"/access",json={
            "action":"reactivate","expected_updated_at":revived.json()["updated_at"]})
        assert reenabled.status_code==200,reenabled.text
        d["selected"]["actor"]=Actor("member",account_id=d["guest"])
        assert (await d["client"].get("/api/v1/professional/me")).status_code==200
        d["selected"]["actor"]=owner
        revoked=await d["client"].patch(base+"/access",json={
            "action":"revoke","expected_updated_at":reenabled.json()["updated_at"]})
        assert revoked.status_code==200,revoked.text
        assert revoked.json()["account_id"] is None
        d["selected"]["actor"]=Actor("member",account_id=d["guest"])
        assert (await d["client"].get("/api/v1/professional/agenda")).status_code==403
    finally:
        d["selected"]["actor"]=owner
        await cleanup_portal(d)


@pytest.mark.asyncio
async def test_professional_cannot_act_on_other_professionals_or_create_fake_invite(extras_db,monkeypatch):
    d=extras_db
    monkeypatch.setenv("PANEL_PUBLIC_URL","https://orionagenda.pro")
    monkeypatch.setenv("PANEL_EMAIL_ENABLED","0")
    await plan_for_tests(d)
    owner=d["selected"]["actor"]
    another=uuid4()
    booking=uuid4()
    try:
        async with d["migrator"].begin() as db:
            await db.execute(text("""INSERT INTO professionals(id,tenant_id,name)
                VALUES(:p,:t,'Profissional sem portal')"""),{"p":another,"t":d["tenant"]})
            customer=uuid4()
            await db.execute(text("""INSERT INTO customers(id,tenant_id,name)
                VALUES(:id,:t,'Cliente de outro profissional')"""),
                {"id":customer,"t":d["tenant"]})
            start=datetime.now(timezone.utc)+timedelta(hours=2)
            await db.execute(text("""INSERT INTO appointments
                (id,tenant_id,customer_id,professional_id,protocol,idempotency_key,
                 request_fingerprint,start_at,end_at,occupied_from,occupied_until,status,total_cents)
                VALUES(:id,:t,:c,:p,'AG-TESTPR',:key,:digest,:start,:end,:start,:end,'confirmed',2500)"""),
                {"id":booking,"t":d["tenant"],"c":customer,"p":another,"key":str(uuid4()),
                 "digest":"0"*64,"start":start,"end":start+timedelta(minutes=30)})
        base=d["base"]+"/team/professionals/"+str(d["pro"])
        invited=await d["client"].post(base+"/invite",json={
            "email":d["guest_email"],"idempotency_key":str(uuid4())})
        assert invited.status_code==201,invited.text
        token=invited.json()["link"].split("#token=",1)[1]
        d["selected"]["actor"]=Actor("member",account_id=d["guest"])
        approved=await d["client"].post("/api/v1/team-invitations/accept",json={"token":token})
        assert approved.status_code==200,approved.text
        today=datetime.now(timezone.utc).date().isoformat()
        agenda=await d["client"].get("/api/v1/professional/agenda",params={"day":today})
        assert agenda.status_code==200,agenda.text
        assert str(booking) not in agenda.text
        assert "Cliente de outro profissional" not in agenda.text
        cancelled=await d["client"].post("/api/v1/professional/appointments/"+str(booking)+"/cancel",
            json={"expected_updated_at":datetime.now(timezone.utc).isoformat(),
                  "idempotency_key":str(uuid4()),"reason":"Teste"})
        assert cancelled.status_code==404,cancelled.text
        after=await d["client"].get("/api/v1/professional/agenda",params={"day":today})
        assert str(booking) not in after.text
        admin=await d["client"].get(d["base"]+"/team")
        assert admin.status_code==403
        async with d["migrator"].connect() as db:
            status=(await db.execute(text("SELECT status FROM appointments WHERE id=:id"),
                                    {"id":booking})).scalar_one()
            assert status=="confirmed"
    finally:
        d["selected"]["actor"]=owner
        await cleanup_portal(d)


@pytest.mark.asyncio
async def test_owner_controls_optional_email_without_issuing_a_generic_invite(extras_db):
    d=extras_db
    owner=d["selected"]["actor"]
    await plan_for_tests(d)
    path=d["base"]+"/team/professionals/"+str(d["pro"])+"/email"
    try:
        generic=await d["client"].post(d["base"]+"/team/invitations",json={
            "email":d["guest_email"],"role":"editor","idempotency_key":str(uuid4())})
        assert generic.status_code==403
        registered=await d["client"].put(path,json={"email":d["guest_email"]})
        assert registered.status_code==200,registered.text
        assert registered.json()["email"]==d["guest_email"]
        async with d["migrator"].connect() as db:
            stored=(await db.execute(text("""SELECT email,account_id FROM professional_portal_links
                WHERE tenant_id=:t AND professional_id=:p"""),
                {"t":d["tenant"],"p":d["pro"]})).one()
            assert stored==(d["guest_email"],None)
        cleared=await d["client"].put(path,json={"email":None})
        assert cleared.status_code==200,cleared.text
        async with d["migrator"].connect() as db:
            count=(await db.execute(text("""SELECT count(*) FROM professional_portal_links
                WHERE tenant_id=:t AND professional_id=:p"""),
                {"t":d["tenant"],"p":d["pro"]})).scalar_one()
            assert count==0
    finally:
        d["selected"]["actor"]=owner
        await cleanup_portal(d)

@pytest.mark.asyncio
async def test_professional_creates_real_booking_only_for_own_calendar(booking_db,monkeypatch):
    """Real booking engine and RLS, with two staff and a verified account."""
    from app.main import app
    from app.catalog_api import authenticate
    from app.booking_service import book_appointment, confirm_hold
    from tests.integration.test_booking import start_at, phone
    from fastapi import HTTPException
    import httpx

    d=booking_db
    monkeypatch.setenv("BUSINESS_PROFILES_ENABLED","1")
    guest=uuid4()
    email=f"professional-{guest}@example.invalid"
    async with d["migrator"].begin() as db:
        await db.execute(text("""INSERT INTO panel_accounts(id,name,email,password_hash,verified_at)
            VALUES(:id,'Barbeiro isolado',:email,'synthetic-not-real',now())"""),
            {"id":guest,"email":email})
        await db.execute(text("""INSERT INTO tenant_memberships
            (tenant_id,account_id,role) VALUES(:t,:a,'professional')"""),
            {"t":d["a"],"a":guest})
        await db.execute(text("""INSERT INTO professional_portal_links
            (tenant_id,professional_id,email,account_id,active)
            VALUES(:t,:p,:email,:a,true)"""),
            {"t":d["a"],"p":d["pro"],"email":email,"a":guest})
    app.dependency_overrides[authenticate]=lambda: Actor("member",account_id=guest)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                      base_url="http://localhost") as client:
            services=await client.get("/api/v1/professional/services")
            assert services.status_code==200, services.text
            assert any(row["id"]==str(d["cut"]) for row in services.json()["items"])
            assert not any(row.get("professional_id")==str(d["pro2"])
                           for row in services.json()["items"])

            slots=await client.get("/api/v1/professional/availability",params={
                "day":d["day"].isoformat(),"service_ids":str(d["cut"])})
            assert slots.status_code==200,slots.text
            grid=slots.json()["slots"]
            assert grid
            start=grid[0]
            payload={"service_ids":[str(d["cut"])],"customer_name":"Cliente do profissional",
                "customer_phone":phone(7),"start_at":start,
                "idempotency_key":str(uuid4())}
            created=await client.post("/api/v1/professional/appointments",json=payload)
            assert created.status_code==201,created.text
            assert created.json()["status"]=="confirmed"
            booking_id=created.json()["id"]
            repeated=await client.post("/api/v1/professional/appointments",json=payload)
            assert repeated.status_code==201,repeated.text
            assert repeated.json()["id"]==booking_id
            own=await client.get("/api/v1/professional/agenda",params={
                "day":d["day"].isoformat()})
            assert own.status_code==200,own.text
            assert booking_id in [row["id"] for row in own.json()["appointments"]]
            assert (await client.get("/api/v1/tenants/"+str(d["a"])+"/appointments",params={
                "day":d["day"].isoformat()})).status_code==403
            forged=await client.post("/api/v1/professional/appointments",
                json={**payload,"professional_id":str(d["pro2"]),"idempotency_key":str(uuid4())})
            assert forged.status_code==422

            # An actual other-professional record cannot be cancelled from this portal.
            async with d["factory"]() as db:
                hold=await book_appointment(db,d["a"],d["pro2"],[d["beard"]],phone(8),
                    "Cliente de outro profissional",start_at(d["day"],10),str(uuid4()))
                other=await confirm_hold(db,d["a"],hold["id"])
            denied=await client.post("/api/v1/professional/appointments/"+str(other["id"])+"/cancel",
                json={"expected_updated_at":other["updated_at"].isoformat(),
                      "idempotency_key":str(uuid4()),"reason":"Tentativa indevida"})
            assert denied.status_code==404,denied.text
            async with d["migrator"].connect() as db:
                status=(await db.execute(text("SELECT status FROM appointments WHERE id=:id"),
                    {"id":other["id"]})).scalar_one()
                assert status=="confirmed"
    finally:
        app.dependency_overrides.pop(authenticate,None)
        async with d["migrator"].begin() as db:
            # Booking confirmation stores idempotency receipts. Remove these
            # before the booking_db fixture deletes its synthetic tenants.
            await db.execute(text("DELETE FROM management_requests WHERE tenant_id=:t"),
                {"t":d["a"]})
            await db.execute(text("DELETE FROM professional_portal_links WHERE tenant_id=:t"),
                {"t":d["a"]})
            await db.execute(text("DELETE FROM tenant_memberships WHERE account_id=:a"),
                {"a":guest})
            await db.execute(text("DELETE FROM panel_accounts WHERE id=:a"),
                {"a":guest})
