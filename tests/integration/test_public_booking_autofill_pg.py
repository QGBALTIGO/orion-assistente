"""Booking-profile access is scoped to a verified customer, never a phone search."""
import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from app import public_customer_area as area
from tests.integration.test_public_catalog import public_db, publish, availability, book_body

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_booking_profile_requires_session_or_exact_birthdate(public_db, monkeypatch):
    d = public_db
    d["app"].include_router(area.router)
    monkeypatch.setenv("APPOINTMENT_COMMUNICATIONS_ENABLED", "0")
    monkeypatch.setenv("ORION_CUSTOMER_PORTAL_ENABLED", "1")
    monkeypatch.setenv("INTEGRATION_FERNET_KEY", Fernet.generate_key().decode())
    await publish(d)
    number, other = "+5511987654321", "+5511987654322"
    dob = "1990-07-06"
    root = d["public"] + "/account"
    profile = root + "/booking-profile"
    base = "https://orionagenda.pro"
    headers = {"Origin": base}
    async with d["migrator"].begin() as db:
        await db.execute(text("""INSERT INTO business_onboarding(tenant_id,is_new,configuration,completed_at)
            VALUES(:t,false,CAST(:c AS jsonb),now())"""),
            {"t":d["a"],"c":'{"niche":"barbearia","activity":"services","modality":"onsite"}'})
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=d["app"]),base_url=base,headers=headers) as client:
            for phone, birth in [(number,dob),(other,"1995-01-02")]:
                slot=await availability(d)
                response=await client.post(d["public"]+"/appointments",json={
                    **book_body(slot,phone),"customer_birth_date":birth,"customer_portal_remember":False})
                assert response.status_code==201,response.text
                assert response.json()["customer_area_connected"] is False
                assert "set-cookie" not in response.headers
            anon=await client.get(profile)
            assert anon.status_code==200,anon.text
            assert anon.json()=={"authenticated":False,"login_method":"birth_date"}
            assert "no-store" in anon.headers["cache-control"]
            assert (await client.get(profile,params={"phone":number})).json()==anon.json()
            assert (await client.post(profile,json={"phone":number})).status_code==422
            invalid=await client.post(profile,json={"phone":number,"birth_date":"1990-07-07"})
            unknown=await client.post(profile,json={"phone":"+5511987654399","birth_date":dob})
            assert invalid.status_code==unknown.status_code==403
            assert invalid.json()==unknown.json()
            assert "profile" not in invalid.json() and "set-cookie" not in invalid.headers
            denied=await client.post(profile,headers={"Origin":"https://foreign.invalid"},
                json={"phone":number,"birth_date":dob})
            assert denied.status_code==403 and "set-cookie" not in denied.headers
            verified=await client.post(profile,json={"phone":number,"birth_date":dob,"remember":True})
            assert verified.status_code==200,verified.text
            value=verified.json()
            assert set(value)=={"authenticated","profile","login_method"}
            assert value["authenticated"] is True and value["login_method"]=="birth_date"
            assert set(value["profile"])=={"name","phone","birth_date"}
            assert value["profile"]["phone"]==number and value["profile"]["birth_date"]==dob
            assert value["profile"]["name"]
            cookie=verified.headers["set-cookie"].lower()
            assert "httponly" in cookie and "secure" in cookie and "max-age=2592000" in cookie
            assert "no-store" in verified.headers["cache-control"]
            again=await client.get(profile)
            assert again.json()==value
            # The URL cannot select somebody else's profile, even for a connected customer.
            assert (await client.get(profile,params={"phone":other})).json()==value
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=d["app"]),base_url=base,headers=headers) as fresh:
                assert (await fresh.get(profile)).json()==anon.json()
            await client.post(root+"/logout")
            assert (await client.get(profile)).json()==anon.json()
            restored=await client.post(profile,json={"phone":number,"birth_date":dob,"remember":False})
            assert restored.status_code==200,restored.text
            assert "max-age=" not in restored.headers["set-cookie"].lower()
            async with d["migrator"].begin() as db:
                await db.execute(text("""UPDATE business_onboarding SET configuration=
                    jsonb_set(configuration,'{niche}','"odontologia"'::jsonb)
                    WHERE tenant_id=:t"""),{"t":d["a"]})
            restricted=await client.get(profile)
            assert restricted.json()["authenticated"] is False
            assert "profile" not in restricted.json()
            assert (await client.post(profile,json={"phone":number,"birth_date":dob})).status_code==403
            # Booking recovery and customer login must share the same daily attempt budget.
            assert (await client.post(root+"/enter",json={"phone":number,"birth_date":dob})).status_code==403
            assert (await client.post(root+"/enter",json={"phone":number,"birth_date":dob})).status_code==429
            await client.post(root+"/logout")
    finally:
        async with d["migrator"].begin() as db:
            await db.execute(text("DELETE FROM business_onboarding WHERE tenant_id=:t"),{"t":d["a"]})
