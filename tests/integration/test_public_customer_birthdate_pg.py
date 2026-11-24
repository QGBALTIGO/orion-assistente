"""Phone + DOB for barbershops, no messaging, synthetic production-equivalent DB."""
from datetime import date
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from starlette.requests import Request

from app import public_customer_area as area
from tests.integration.test_public_catalog import public_db, publish, availability, book_body

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_first_booking_creates_customer_area_and_remembered_session(public_db, monkeypatch):
    d = public_db
    d["app"].include_router(area.router)
    monkeypatch.setenv("APPOINTMENT_COMMUNICATIONS_ENABLED", "0")
    monkeypatch.setenv("ORION_CUSTOMER_PORTAL_ENABLED", "1")
    monkeypatch.setenv("INTEGRATION_FERNET_KEY", Fernet.generate_key().decode())
    await publish(d)
    number = "+5511987654321"
    dob = "1990-07-06"
    root = d["public"] + "/account"
    base = "https://orionagenda.pro"
    headers = {"Origin": base}
    async with d["migrator"].begin() as db:
        await db.execute(text("""INSERT INTO business_onboarding(tenant_id,is_new,configuration,completed_at)
            VALUES(:t,false,CAST(:c AS jsonb),now())"""),
            {"t":d["a"],"c":'{"niche":"barbearia","activity":"services","modality":"onsite"}'})
        # Existing record with a DOB but no completed or confirmed appointments.
        await db.execute(text("""UPDATE customers SET birth_date=DATE '1990-07-06'
            WHERE tenant_id=:t AND id=:c"""), {"t":d["a"],"c":d["private_customer"]})
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=d["app"]), base_url=base, headers=headers) as client:
            start = await client.get(root)
            assert start.json() == {"available": True, "authenticated": False, "login_method":"birth_date"}
            unknown = await client.post(root+"/enter",json={"phone":"+5511987654398","birth_date":dob})
            before = await client.post(root+"/enter",json={"phone":"+5511999990000","birth_date":dob})
            assert unknown.status_code == before.status_code == 403
            assert unknown.json() == before.json()
            assert "set-cookie" not in unknown.headers and "profile" not in unknown.json()
            assert (await client.post(root+"/enter",json={"phone":number})).json() == {"authenticated":False,"birth_date_required":True}
            assert (await client.get(root+"/appointments")).status_code == 401

            # First real appointment; no separate account or link, no WhatsApp.
            slot = await availability(d)
            body = {**book_body(slot,number),"customer_birth_date":dob,"customer_portal_remember":True}
            booking = await client.post(d["public"]+"/appointments",json=body)
            assert booking.status_code==201,booking.text
            assert booking.json()["customer_area_connected"] is True,booking.text
            assert "customer_birth_date" not in booking.json() and "profile" not in booking.json()
            assert "httponly" in booking.headers["set-cookie"].lower()
            assert "max-age=2592000" in booking.headers["set-cookie"].lower()
            assert "secure" in booking.headers["set-cookie"].lower()
            auto = await client.get(root)
            assert auto.status_code==200,auto.text
            assert auto.json()["authenticated"] and auto.json()["profile"]["birth_date"]==dob
            assert auto.json()["login_method"]=="birth_date"
            assert auto.json()["stats"]["appointments"]==1
            assert len(auto.json()["upcoming"]["items"])==1
            assert "no-store" in auto.headers["cache-control"]
            token=client.cookies[area.cookie_name(d["a"])]
            forged=Request({"type":"http","headers":[(b"cookie",(area.cookie_name(d["b"])+"="+token).encode())]})
            assert await area.read_session(forged,d["b"]) is None
            await client.post(root+"/logout")
            assert (await client.get(root+"/appointments")).status_code==401

            wrong=await client.post(root+"/enter",json={"phone":number,"birth_date":"1990-07-07"})
            assert wrong.status_code==403 and wrong.json()==unknown.json()
            assert "set-cookie" not in wrong.headers
            # Replaying the booking with another DOB cannot change the CRM
            # record or establish a session with the incorrect date.
            replay=await client.post(d["public"]+"/appointments",json={**body,"customer_birth_date":"1990-07-07"})
            assert replay.status_code==201,replay.text
            assert replay.json()["customer_area_connected"] is False
            assert "set-cookie" not in replay.headers
            manual=await client.post(root+"/enter",json={"phone":number,"birth_date":dob,"remember":False})
            assert manual.status_code==200,manual.text
            assert manual.json()["profile"]["birth_date"]==dob
            assert "max-age=" not in manual.headers["set-cookie"].lower()
            history=await client.get(root+"/appointments?kind=all")
            assert history.status_code==200,history.text
            assert len(history.json()["items"])==1
            assert "customer_id" not in history.text and "notes" not in history.text
            # Changing to a clinical niche revokes simplified-login access.
            async with d["migrator"].begin() as db:
                await db.execute(text("""UPDATE business_onboarding SET configuration=
                    jsonb_set(configuration,'{niche}','"odontologia"'::jsonb)
                    WHERE tenant_id=:t"""),{"t":d["a"]})
            assert (await client.get(root)).json()["authenticated"] is False
            assert (await client.get(root+"/appointments")).status_code==401
            forbidden=await client.post(root+"/enter",json={"phone":number,"birth_date":dob})
            assert forbidden.status_code==403 and "set-cookie" not in forbidden.headers
            # The known customer's attempt budget is exhausted even with a fresh browser.
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=d["app"]),base_url=base,headers=headers) as fresh:
                blocked=await fresh.post(root+"/enter",json={"phone":number,"birth_date":dob})
                assert blocked.status_code==429
            await client.post(root+"/logout")
    finally:
        async with d["migrator"].begin() as db:
            await db.execute(text("DELETE FROM business_onboarding WHERE tenant_id=:t"),{"t":d["a"]})
