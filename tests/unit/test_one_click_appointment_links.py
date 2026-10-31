"""One-click URL proof checks; no real phone, browser, or customer data."""
import re
from uuid import uuid4
import pytest
from app import appointment_access as access
from app.appointment_message_templates import safe_link
from app.public_origins import canonical_public_origin


def test_one_click_proof_is_role_identity_and_phone_bound(monkeypatch):
    monkeypatch.setenv("ORION_DIRECT_APPOINTMENT_KEY","synthetic-testing-key-"+"x"*48)
    tenant,appointment,customer,professional=[uuid4() for _ in range(4)]
    code="AG-4NNSY2"
    a=access.direct_link_proof(code,"customer",tenant,appointment,customer,
                                "+5567999999922",12)
    assert re.fullmatch(r"[A-Za-z0-9_-]{27}",a)
    assert a==access.direct_link_proof(code,"customer",tenant,appointment,customer,
                                      "+5567999999922",12)
    assert a!=access.direct_link_proof(code,"professional",tenant,appointment,
                                      professional,"+5567999999923")
    assert a!=access.direct_link_proof(code,"customer",tenant,appointment,customer,
                                      "+5567999999922",13)
    assert a!=access.direct_link_proof(code,"customer",tenant,appointment,customer,
                                      "+5567999999924",12)
    assert a!=access.direct_link_proof("AG-4NNSY3","customer",tenant,appointment,
                                      customer,"+5567999999922",12)
    origin=canonical_public_origin()
    assert safe_link(origin+"/"+code+"#"+a)==origin+"/"+code+"#"+a
    assert safe_link(origin+"/p/"+code+"#"+a)==origin+"/p/"+code+"#"+a
    assert safe_link(origin+"/"+code+"#"+a+"extra") is None
    assert safe_link("https://evil.example/"+code+"#"+a) is None


@pytest.mark.asyncio
async def test_short_link_uses_different_one_click_proofs_per_role(monkeypatch):
    monkeypatch.setenv("APPOINTMENT_COMMUNICATIONS_ENABLED","1")
    monkeypatch.setenv("ORION_SHORT_APPOINTMENT_LINKS_ENABLED","1")
    monkeypatch.setenv("ORION_DIRECT_APPOINTMENT_KEY","synthetic-testing-key-"+"x"*48)
    tenant,appointment,customer,professional=[uuid4() for _ in range(4)]
    row={"code":"AG-4NNSY2","customer_id":customer,"professional_id":professional,
         "customer_access_blocked":False,"customer_version":2,
         "customer_phone":"+5567999999922","customer_active":True,
         "customer_deleted":None,"professional_phone":"+5567999999923",
         "professional_active":True}
    class Result:
        def mappings(self):return self
        def first(self):return row
    class Database:
        async def execute(self,*args,**kwargs):return Result()
    customer_link=await access.shared_link(Database(),tenant,appointment,audience="customer")
    professional_link=await access.shared_link(Database(),tenant,appointment,audience="professional")
    origin=canonical_public_origin()
    assert customer_link.startswith(origin+"/AG-4NNSY2#")
    assert professional_link.startswith(origin+"/p/AG-4NNSY2#")
    assert customer_link.split("#")[1]!=professional_link.split("#")[1]
