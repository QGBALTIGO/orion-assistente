"""Real PostgreSQL + Redis: a phone is never an account credential.

All bookings, secrets and identities are synthetic and confined to CI.
"""
import hashlib
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from starlette.requests import Request

from app import appointment_access as access
from app import public_customer_area as area
from app.database import get_session_factory, tenant_transaction
from tests.integration.test_public_catalog import public_db, publish, availability, book_body

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_private_customer_history_and_phone_enumeration(public_db, monkeypatch):
    d = public_db
    d["app"].include_router(area.router)
    await publish(d)
    number = "+5511987654321"
    other = "+5511987654322"
    monkeypatch.setenv("APPOINTMENT_COMMUNICATIONS_ENABLED", "0")
    # Use the ordinary real booking flow to produce actual price and service snapshots.
    for phone in (number, number, number, other):
        slot = await availability(d)
        payload = {**book_body(slot, phone), "customer_birth_date": "1990-07-06"}
        response = await d["client"].post(d["public"] + "/appointments", json=payload)
        assert response.status_code == 201, response.text
    async with d["migrator"].begin() as db:
        records = (await db.execute(text("""
            SELECT a.id,a.customer_id,c.version
            FROM appointments a JOIN customers c ON c.id=a.customer_id AND c.tenant_id=a.tenant_id
            WHERE a.tenant_id=:t AND c.whatsapp_e164=:phone ORDER BY a.start_at
        """), {"t": d["a"], "phone": number})).mappings().all()
        assert len(records) == 3
        await db.execute(text("UPDATE appointments SET status='completed' WHERE tenant_id=:t AND id=:id"),
                         {"t": d["a"], "id": records[0]["id"]})
        await db.execute(text("UPDATE appointments SET status='cancelled' WHERE tenant_id=:t AND id=:id"),
                         {"t": d["a"], "id": records[1]["id"]})
        await db.execute(text("UPDATE customers SET notes='Nota clínica confidencial de teste' WHERE tenant_id=:t AND id=:c"),
                         {"t": d["a"], "c": records[2]["customer_id"]})
    monkeypatch.setenv("APPOINTMENT_COMMUNICATIONS_ENABLED", "1")
    monkeypatch.setenv("APPOINTMENT_COMMUNICATIONS_PILOT_ONLY", "0")
    monkeypatch.setenv("ORION_CUSTOMER_PORTAL_ENABLED", "1")
    monkeypatch.setenv("INTEGRATION_FERNET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("PANEL_PUBLIC_URL", "https://orionagenda.pro")
    monkeypatch.setenv("ORION_SHORT_APPOINTMENT_LINKS_ENABLED", "1")
    monkeypatch.setenv("ORION_DIRECT_APPOINTMENT_KEY", "synthetic-test-only-key-" + "a" * 64)
    root = d["public"] + "/account"
    origin = "https://orionagenda.pro"
    auth_headers = {"Origin": origin}
    async with get_session_factory()() as db:
        async with tenant_transaction(db, d["a"]):
            legacy = await access.issue(db, d["a"], records[2]["id"])
            direct = await access.shared_link(db, d["a"], records[2]["id"], audience="customer")
            professional_link = None
    assert "#" in direct and "/AG-" in direct
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=d["app"]),
                                     base_url=origin, headers=auth_headers) as client:
            initial = await client.get(root)
            assert initial.status_code == 200, initial.text
            assert initial.json() == {"available": True, "authenticated": False}
            known = await client.post(root + "/enter", json={"phone": number})
            unknown = await client.post(root + "/enter", json={"phone": "+5511987654399"})
            assert known.status_code == unknown.status_code == 200
            assert known.json() == unknown.json() == area.NEEDS_LINK
            assert "set-cookie" not in known.headers
            assert (await client.get(root + "/appointments")).status_code == 401
            assert (await client.post(root + "/enter", headers={"Origin": "https://evil.example"},
                                      json={"phone": number})).status_code == 403
            # A guessed phone, public code, forged proof or a different phone
            # never unlocks any profile or any appointment history.
            bad_links = (direct.split("#")[0], direct.split("#")[0] + "#" + "a" * 27,
                         "https://evil.example/" + direct.split("/")[-1])
            for bad in bad_links:
                denied = await client.post(root + "/link-login",
                                          json={"phone": number, "booking_link": bad})
                assert denied.status_code in (403, 422)
                assert "profile" not in denied.json() and "set-cookie" not in denied.headers
            wrong = await client.post(root + "/link-login", json={"phone": other, "booking_link": direct})
            assert wrong.status_code == 403 and "set-cookie" not in wrong.headers

            signed_in = await client.post(root + "/link-login",
                                          json={"phone": number, "booking_link": direct, "remember": True})
            assert signed_in.status_code == 200, signed_in.text
            data = signed_in.json()
            assert data["authenticated"] is True
            assert data["profile"]["phone"] == number
            assert data["profile"]["birth_date"] == "1990-07-06"
            assert data["stats"]["appointments"] == 3
            assert data["stats"]["completed"] == 1 and data["stats"]["cancelled"] == 1
            assert data["stats"]["completed_cents"] == 3000
            assert len(data["upcoming"]["items"]) == 1
            assert "no-store" in signed_in.headers["cache-control"]
            cookie = signed_in.headers["set-cookie"].lower()
            assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
            assert "max-age=2592000" in cookie
            token = client.cookies[area.cookie_name(d["a"])]
            assert len(token) == 43
            assert (await client.get(root)).json()["authenticated"] is True
            all_history = await client.get(root + "/appointments?kind=all")
            assert all_history.status_code == 200, all_history.text
            items = all_history.json()["items"]
            assert len(items) == 3 and {v["status"] for v in items} == {"completed","cancelled","confirmed"}
            assert all(v["services"] == ["Corte"] for v in items)
            assert all(set(v) == {"status","start_at","end_at","professional","services",
                                 "duration_min","price_cents","price_mode","timezone"} for v in items)
            assert "confidencial" not in all_history.text
            assert len((await client.get(root + "/appointments?kind=all&status=cancelled")).json()["items"]) == 1
            assert (await client.post(root + "/enter",json={"phone":other})).json() == area.NEEDS_LINK
            # Even copying the real token into the other tenant's cookie name
            # cannot change its server-bound identity.
            forged = Request({"type":"http","headers":[(b"cookie",
                (area.cookie_name(d["b"])+"="+token).encode())]})
            assert await area.read_session(forged, d["b"]) is None
            assert (await client.post(root + "/logout")).status_code == 200
            assert (await client.get(root + "/appointments")).status_code == 401
            replay = Request({"type":"http","headers":[(b"cookie",
                (area.cookie_name(d["a"])+"="+token).encode())]})
            assert await area.read_session(replay, d["a"]) is None

            # Older revocable links work too, without WhatsApp, and never
            # survive an administrative revocation or customer version change.
            legacy_login = await client.post(root + "/link-login",
                json={"phone": number, "booking_link": legacy, "remember": False})
            assert legacy_login.status_code == 200, legacy_login.text
            assert "max-age=" not in legacy_login.headers["set-cookie"].lower()
            async with d["migrator"].begin() as db:
                await db.execute(text("UPDATE appointments SET customer_access_blocked=true WHERE tenant_id=:t AND id=:a"),
                                 {"t": d["a"], "a": records[2]["id"]})
            assert (await client.get(root)).json()["authenticated"] is False
            assert (await client.get(root + "/appointments")).status_code == 401
            await client.post(root + "/logout")
    finally:
        async with d["migrator"].begin() as db:
            await db.execute(text("DELETE FROM appointment_access WHERE tenant_id=:t"), {"t": d["a"]})
