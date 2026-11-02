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

        d["selected"]["actor"]=owner
        revoked=await d["client"].patch(base+"/access",json={
            "action":"revoke","expected_updated_at":revived.json()["updated_at"]})
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
