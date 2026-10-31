"""Short booking aliases are public identifiers, never access capabilities.

Covers all public routes, dummy unknown codes, no-OTP disclosure, access
segregation and the trusted QR->OTP->browser handshake. Real PostgreSQL
collision tests live in the companion integration file.
"""
from urllib.parse import parse_qs,urlsplit
from uuid import uuid4
import asyncio
import json
import os
import re

import httpx
import pytest
from fastapi import HTTPException

from app import short_appointment_api as short
from app import appointment_access
from app import appointment_message_templates as messages

SAMPLE="AG-4NNSY2"
CUSTOMER_PHONE="5567999999921"
PROFESSIONAL_PHONE="5567999999923"


def test_code_format_and_link_sanitizer_blocks_external_hosts():
    assert short.normalized("ag-4nnsy2")==SAMPLE
    assert short.normalized(SAMPLE+"-E1721F7D")==SAMPLE+"-E1721F7D"
    for bad in ("AG-AAAAA","AG-AAAAAAA","../AG-AAAAAA","AG-AAAAA1%2Flogin",
                "http://example.com","AG-OOOOOO","AG-123456#secret"):
        with pytest.raises(HTTPException):
            short.normalized(bad)
    from app.public_origins import canonical_public_origin
    origin=canonical_public_origin()
    for url in (origin+"/"+SAMPLE,origin+"/p/"+SAMPLE,
                origin+"/"+SAMPLE+"-E1721F7D",origin+"/a#"+str(uuid4())+"."+"Z"*43):
        assert messages.safe_link(url)==url
    for bad in (origin+"/AG-AAAAAA@other.tld",
                origin+"/AG-AAAAAA?access=secret",
                "https://orionagenda.pro.attacker.invalid/"+SAMPLE,
                origin+"/p/"+SAMPLE+"#token","https://other.example/"+SAMPLE):
        assert messages.safe_link(bad) is None


def test_protocol_parser_does_not_treat_a_public_code_as_a_password():
    assert not hasattr(short.Start,"token")
    assert not hasattr(short.Start,"phone")
    assert "action" not in short.Start.model_fields
    assert short.Start.model_fields["role"].default=="customer"
    assert short.TTL==300
    assert short.CHALLENGE.fullmatch("x"*22)
    assert short.OTP.fullmatch("123456")
    assert not short.OTP.fullmatch("12345")
    assert not short.OTP.fullmatch("0000000")


@pytest.mark.asyncio
async def test_public_html_no_data_and_otp_start_same_for_unknown_code(monkeypatch):
    monkeypatch.setenv("APPOINTMENT_COMMUNICATIONS_ENABLED","1")
    monkeypatch.setenv("ORION_SHORT_APPOINTMENT_LINKS_ENABLED","1")
    monkeypatch.setenv("ORION_CENTRAL_CONFIRMATION_REPLY_ENABLED","1")
    monkeypatch.setenv("INTEGRATION_FERNET_KEY","synthetic-"+"B"*56)
    async def same_limit(*args,**kwargs):return None
    async def fake_phone():return "5567999999964"
    async def fake_redis(fn):
        class Stub:
            async def set(self,*args,**kwargs):
                return True
        return await fn(Stub())
    from app import appointment_confirmation
    monkeypatch.setattr(short,"public_limit",same_limit)
    monkeypatch.setattr(short,"with_redis",fake_redis)
    monkeypatch.setattr(appointment_confirmation,"central_admin_phone",fake_phone)
    from app.main import app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                base_url="https://orionagenda.pro") as client:
        for code in (SAMPLE,"AG-AAAAAA"):
            resp=await client.get("/"+code)
            assert resp.status_code==200
            assert "Meu agendamento" in resp.text
            assert CUSTOMER_PHONE not in resp.text
            assert "AG-"+code not in resp.text
        pages=[await client.get("/"+value) for value in (SAMPLE,"AG-AAAAAA")]
        assert pages[0].content==pages[1].content
        unknown=await client.post("/api/short-appointment/start",
                    json={"code":"AG-AAAAAA","role":"customer"},
                    headers={"Origin":"https://orionagenda.pro"})
        known=await client.post("/api/short-appointment/start",
                    json={"code":SAMPLE,"role":"customer"},
                    headers={"Origin":"https://orionagenda.pro"})
        assert known.status_code==unknown.status_code==200
        for r in (known,unknown):
            data=r.json()
            assert set(data)=={"status","challenge","whatsapp_url"}
            assert data["status"]=="requested"
            assert data["whatsapp_url"].startswith("https://wa.me/5567999999964?text=")
            assert short.CHALLENGE.fullmatch(data["challenge"])
            assert "tenant" not in r.text
            assert "appointment_id" not in r.text
            assert r.headers["cache-control"].startswith("no-store")
            assert r.headers["referrer-policy"]=="no-referrer"
        p=await client.get("/p/"+SAMPLE)
        assert p.status_code==200
        assert "Meu agendamento" in p.text
        assert "X-Robots-Tag" not in p.text
        assert p.headers["x-robots-tag"].startswith("noindex")
        assert (await client.get("/login")).status_code != 200 or (
            await client.get("/login")).text != pages[0].text


@pytest.mark.asyncio
async def test_inbound_rejects_non_whatsapp_contact_and_never_authenticates_browser(monkeypatch):
    monkeypatch.setenv("APPOINTMENT_COMMUNICATIONS_ENABLED","1")
    monkeypatch.setenv("ORION_SHORT_APPOINTMENT_LINKS_ENABLED","1")
    monkeypatch.setenv("ORION_CENTRAL_CONFIRMATION_REPLY_ENABLED","1")
    nonce="K"*22
    invalid=await short.inbound_otp_from_verified_phone(
        "ORION VERIFICAR C "+SAMPLE+" "+nonce,"+5567999999921")
    assert invalid["state"]=="unrecognized"
    assert "token" not in json.dumps(invalid).lower()
    assert short.INBOUND.fullmatch("ORION VERIFICAR C "+SAMPLE+" "+nonce)
    assert short.INBOUND.fullmatch("ORION VERIFICAR P "+SAMPLE+" "+nonce)
    assert not short.INBOUND.fullmatch("ORION VERIFICAR A "+SAMPLE+" "+nonce)
