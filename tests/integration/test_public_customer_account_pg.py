"""Real PostgreSQL portal: existing CRM identity, tenant RLS and booking mutations.

No messaging provider is needed. Existing private booking links are checked
against real PostgreSQL RLS with separate runtime/migration roles.
"""
from uuid import uuid4
import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from tests.integration.test_booking import booking_db, phone, start_at
from app.booking_service import book_appointment, confirm_hold, cancel_appointment
from app import public_customer_account_api as portal
from app import public_customer_auth as auth
from app.main import app
from app.database import tenant_transaction

pytestmark=pytest.mark.integration


class RedisSession:
    def __init__(self):
        self.cache={}
    async def get(self,key):return self.cache.get(key)
    async def set(self,key,value,ex):self.cache[key]=value
    async def delete(self,key):self.cache.pop(key,None)
    async def aclose(self):pass


@pytest.mark.asyncio
async def test_real_customer_portal_only_reads_and_modifies_own_tenant_bookings(booking_db,monkeypatch):
    d=booking_db;identity=phone(920);different=phone(921)
    from app import booking_service
    async with d["factory"]() as db:
        first=await book_appointment(db,d["a"],d["pro"],[d["cut"]],
            identity,"Cliente real sintético",start_at(d["day"],10),"self-service-"+str(uuid4()))
        first=await confirm_hold(db,d["a"],first["id"])
        cancelled=await book_appointment(db,d["a"],d["pro"],[d["cut"]],
            identity,"Cliente real sintético",start_at(d["day"],12),"self-service-"+str(uuid4()))
        cancelled=await confirm_hold(db,d["a"],cancelled["id"])
        foreign=await book_appointment(db,d["a"],d["pro"],[d["cut"]],
            different,"Outro cliente",start_at(d["day"],14),"self-service-"+str(uuid4()))
        foreign=await confirm_hold(db,d["a"],foreign["id"])
    async with d["factory"]() as db:
        await cancel_appointment(db,d["a"],cancelled["id"])
    monkeypatch.setenv("ORION_CUSTOMER_PORTAL_ENABLED","1")
    monkeypatch.setenv("INTEGRATION_FERNET_KEY",Fernet.generate_key().decode())
    monkeypatch.setenv("PANEL_PUBLIC_URL","https://orionagenda.pro")
    monkeypatch.setenv("REDIS_URL","redis://localhost:6379/15")
    monkeypatch.setenv("APPOINTMENT_COMMUNICATIONS_ENABLED","1")
    monkeypatch.setenv("APPOINTMENT_COMMUNICATIONS_PILOT_ONLY","0")
    store=RedisSession()
    monkeypatch.setattr(auth,"_cache",lambda:store)
    monkeypatch.setattr(portal,"get_session_factory",lambda:d["factory"])
    async def resolve(slug):
        return d["b"] if slug=="outro-negocio" else d["a"]
    monkeypatch.setattr(portal,"resolve_public",resolve)
    async def published(db,tenant,slug):
        return {"public_enabled": True,"public_slug":slug}
    monkeypatch.setattr(portal,"published_guard",published)
    async def rate(*args,**kwargs):return None
    monkeypatch.setattr(portal,"public_limit",rate)
    from app import appointment_access as old_link_access
    async with d["factory"]() as db:
        async with tenant_transaction(db,d["a"]):
            private_link = await old_link_access.issue(db,d["a"],first["id"])
    url="/api/v1/public/sites/test-business/account"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                base_url="https://orionagenda.pro") as client:
        anonymous=await client.get(url)
        assert anonymous.status_code==200 and anonymous.json()=={"available":True,"authenticated":False}
        assert (await client.get(url+"/appointments")).status_code==401
        unknown=await client.post(url+"/enter",json={"phone":identity})
        assert unknown.status_code==200 and unknown.json()=={"authenticated":False,"proof_required":True}
        assert (await client.get(url+"/appointments")).status_code==401
        wrong_phone=await client.post(url+"/link-login",json={"phone":different,"booking_link":private_link})
        assert wrong_phone.status_code==403
        verified=await client.post(url+"/link-login",json={"phone":identity,"booking_link":private_link})
        assert verified.status_code==200,verified.text
        repeat=await client.post(url+"/enter",json={"phone":identity})
        assert repeat.status_code==200 and repeat.json()["authenticated"] is True
        cookie=client.cookies.get(auth.COOKIE)
        assert cookie and cookie not in verified.text
        account=await client.get(url)
        assert account.status_code==200 and account.json()["profile"]["name"]=="Cliente real sintético"
        upcoming=await client.get(url+"/appointments?kind=upcoming")
        assert upcoming.status_code==200,upcoming.text
        assert [x["id"] for x in upcoming.json()["items"]]==[str(first["id"])]
        assert upcoming.json()["items"][0]["protocol"]==first["protocol"]
        history=await client.get(url+"/appointments?kind=history")
        assert history.status_code==200
        assert [x["id"] for x in history.json()["items"]]==[str(cancelled["id"])]
        assert history.json()["items"][0]["status"]=="cancelled"
        assert (await client.get("/api/v1/public/sites/outro-negocio/account/appointments")).status_code==401
        wrong=await client.post(url+"/appointments/"+str(foreign["id"])+"/cancel",json={
            "expected_updated_at":foreign["updated_at"].isoformat(),
            "idempotency_key":str(uuid4())})
        assert wrong.status_code==404
        wrong_slot=await client.get(url+"/appointments/"+str(foreign["id"])+"/slots?day="+d["day"].isoformat())
        assert wrong_slot.status_code==404
        slots=await client.get(url+"/appointments/"+str(first["id"])+"/slots?day="+d["day"].isoformat())
        assert slots.status_code==200,slots.text
        assert slots.json()["slots"]
        change=await client.patch(url+"/profile",json={
            "expected_version":verified.json()["profile"]["version"],
            "instagram":"@novousuario","email":"novo@example.test"})
        assert change.status_code==200,change.text
        assert change.json()["profile"]["instagram"]=="@novousuario"
        new_cookie=client.cookies.get(auth.COOKIE)
        assert new_cookie!=cookie
        from app.public_customer_auth import session_claims
        assert await session_claims(cookie,d["a"]) is None
        assert await session_claims(new_cookie,d["b"]) is None
        profile=await client.get(url)
        assert profile.status_code==200 and profile.json()["profile"]["email"]=="novo@example.test"
        bad=await client.patch(url+"/profile",json={"expected_version":profile.json()["profile"]["version"],"notes":"secret"})
        assert bad.status_code==422
        reschedule=await client.post(url+"/appointments/"+str(first["id"])+"/reschedule",json={
            "expected_updated_at":first["updated_at"].isoformat(),
            "idempotency_key":str(uuid4()),
            "start_at":start_at(d["day"],15).isoformat()})
        assert reschedule.status_code==200,reschedule.text
        assert reschedule.json()["item"]["status"]=="confirmed"
        assert (await client.post(url+"/logout")).status_code==200
        assert (await client.get(url+"/appointments")).status_code==401
    async with d["factory"]() as db:
        async with tenant_transaction(db,d["a"]):
            updated=(await db.execute(text("SELECT instagram,email FROM customers WHERE tenant_id=:t AND whatsapp_e164=:p"),
                {"t":d["a"],"p":identity})).mappings().one()
            assert updated["instagram"]=="@novousuario" and updated["email"]=="novo@example.test"
            still=(await db.execute(text("SELECT status FROM appointments WHERE tenant_id=:t AND id=:a"),
                {"t":d["a"],"a":foreign["id"]})).scalar_one()
            assert still=="confirmed"
