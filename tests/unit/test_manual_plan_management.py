"""Manual plan authorization, CAS, replay and immutable commercial contracts."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Response
import httpx
import pytest
from pydantic import ValidationError

from app import admin_accounts as admin, plan_management as plans
from app.catalog_api import Actor, authenticate
from app.plan_permissions import derive_plan


class Result:
    def __init__(self, value=None): self.value = value
    def scalar_one_or_none(self): return self.value
    def mappings(self): return self
    def one_or_none(self): return self.value
    def first(self): return self.value
    def all(self): return self.value


class Transaction:
    def __init__(self, db): self.db = db
    async def __aenter__(self):
        self.saved = deepcopy((self.db.control, self.db.audits, self.db.access_updates))
        return self.db
    async def __aexit__(self, kind, *_):
        if kind: self.db.control, self.db.audits, self.db.access_updates = self.saved


class Database:
    def __init__(self):
        self.tenant, self.account, self.operator = uuid4(), uuid4(), uuid4()
        self.control = {"version": 1, "manual": {"plan_code": "essencial",
            "started_at": datetime.now(timezone.utc)-timedelta(days=1), "expires_at": None}}
        self.commercial, self.trial, self.platform = None, None, False
        self.professionals, self.memberships, self.membership_role = 1, 1, "owner"
        self.audits, self.calls, self.access_updates = [], [], []
        self.account_rows = []
    async def __aenter__(self): return self
    async def __aexit__(self, *_): pass
    def begin(self): return Transaction(self)
    def snapshot(self):
        return {"tenant_id": str(self.tenant), "tenant_status": "active", **deepcopy(self.control),
            "platform_workspace": self.platform, "commercial": deepcopy(self.commercial),
            "trial": deepcopy(self.trial), "active_professionals": self.professionals}
    def state(self): return derive_plan(self.snapshot())
    async def execute(self, query, params=None):
        sql = str(query)
        self.calls.append((sql, params))
        if "pg_advisory_xact_lock" in sql: return Result()
        if "AS plan_snapshot" in sql: return Result(self.account_rows)
        if "m.role AS membership_role" in sql:
            if params != {"account": self.account, "tenant": self.tenant}: return Result()
            return Result({"id":self.tenant,"name":"Empresa Sintética","status":"active","account_id":self.account,
                "membership_role":self.membership_role,"company_count":self.memberships,"account_state":"active"})
        if "FROM tenant_plan_audit WHERE tenant_id=:tenant AND operation_id" in sql:
            return Result(next((row for row in self.audits if row["operation_id"] == params["operation"]), None))
        if "INSERT INTO tenant_plan_controls" in sql:
            if self.control["version"] != params["version"]: return Result()
            self.control = {"version":self.control["version"]+1,"manual":{"plan_code":params["code"],
                "started_at":datetime.now(timezone.utc),"expires_at":params["expires"]}}
            return Result(self.control["version"])
        if "UPDATE tenant_plan_controls" in sql:
            if self.control["version"] != params["version"] or self.control["manual"] is None: return Result()
            self.control = {"version":self.control["version"]+1,"manual":None}
            return Result(self.control["version"])
        if "INSERT INTO tenant_plan_audit" in sql:
            self.audits.append({"id":params["id"],"tenant_id":params["tenant"],"actor_id":params["actor"],
                "operation_id":params["operation"],"action":params["action"],
                "previous_plan":json.loads(params["previous"]),"new_plan":json.loads(params["new"]),
                "reason":params["reason"],"access_type":params["access_type"],"expires_at":params["expires"],
                "request_fingerprint":params["fingerprint"],"occurred_at":datetime.now(timezone.utc)})
            return Result()
        if "FROM tenant_plan_audit" in sql: return Result(list(reversed(self.audits)))
        if "FROM subscriptions s JOIN plans" in sql: return Result(self.commercial)
        if "FROM orion_plan_trial_requests WHERE tenant_id" in sql: return Result(self.trial)
        if "UPDATE panel_accounts SET last_access_at" in sql:
            self.access_updates.append(params["account"])
            assert "interval '5 minutes'" in sql and "auth_version" not in sql
            return Result()
        raise AssertionError("Unexpected synthetic database operation: " + sql)


@pytest.fixture
def case(monkeypatch):
    db = Database()
    async def state(session, tenant, lock="shared"):
        assert session is db and tenant == db.tenant
        return db.state()
    async def access(actor, tenant):
        if tenant != db.tenant: raise HTTPException(403)
        return SimpleNamespace(role="owner",account_id=db.account)
    monkeypatch.setattr(admin,"_storage",lambda:lambda:db)
    monkeypatch.setattr(admin,"plan_state",state)
    monkeypatch.setattr(plans,"support_session_factory",lambda:lambda:db)
    monkeypatch.setattr(plans,"plan_state",state)
    monkeypatch.setattr(plans,"resolve_site_access",access)
    return db


def change(db, **kwargs):
    state = db.state()
    return admin.ManualPlanAssignment.model_validate({"expected_version":state["version"],
        "expected_revision":state["revision"],"operation_id":str(uuid4()),"confirmation":"ALTERAR PLANO",
        "plan_code":"elite","access_type":"indefinite",**kwargs})


@pytest.mark.asyncio
async def test_manual_assignment_is_atomic_audited_replayable_and_never_mutates_billing_or_sessions(case):
    db = case
    request = change(db, reason="  Liberação administrativa solicitada  ")
    first = await admin._change_company_plan(db.account,db.tenant,request,db.operator,"manual_assign")
    second = await admin._change_company_plan(db.account,db.tenant,request,db.operator,"manual_assign")
    assert first["effective_plan"]["code"] == "elite" and first["effective_plan"]["source"] == "manual"
    assert first["effective_plan"]["version"] == 2 and second["replayed"] is True
    assert len(db.audits) == 1 and db.audits[0]["reason"] == "Liberação administrativa solicitada"
    assert db.audits[0]["previous_plan"]["code"] == "essencial" and db.audits[0]["new_plan"]["code"] == "elite"
    assert all(not ("UPDATE subscriptions" in sql or "auth_version=" in sql or "DELETE FROM" in sql) for sql,_ in db.calls)
    exclusive = next(i for i,(sql,params) in enumerate(db.calls) if "pg_advisory_xact_lock(" in sql and str(params.get("key","")).startswith("orion-plan-v1:"))
    write = next(i for i,(sql,_) in enumerate(db.calls) if "INSERT INTO tenant_plan_controls" in sql)
    assert exclusive < write


@pytest.mark.asyncio
async def test_stale_cas_or_conflicting_operation_never_overwrites_another_change(case):
    db = case
    one, stale = change(db), change(db,plan_code="profissional")
    await admin._change_company_plan(db.account,db.tenant,one,db.operator,"manual_assign")
    for payload in (stale, change(db,operation_id=one.operation_id,plan_code="essencial")):
        with pytest.raises(HTTPException) as caught:
            await admin._change_company_plan(db.account,db.tenant,payload,db.operator,"manual_assign")
        assert caught.value.status_code == 409
    assert db.state()["code"] == "elite" and len(db.audits) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["foreign_tenant","foreign_account","multiple","editor","platform"])
async def test_only_the_explicit_unique_owner_company_can_be_changed(case, damage):
    db = case
    account, tenant = db.account, db.tenant
    if damage == "foreign_tenant": tenant=uuid4()
    elif damage == "foreign_account": account=uuid4()
    elif damage == "multiple": db.memberships=2
    elif damage == "editor": db.membership_role="editor"
    else: db.platform=True
    with pytest.raises(HTTPException) as caught:
        await admin._change_company_plan(account,tenant,change(db),db.operator,"manual_assign")
    assert caught.value.status_code in {403,404,409}
    assert db.control["version"] == 1 and not db.audits


@pytest.mark.parametrize("extra", [
    {"expected_version":True},{"confirmation":"SIM"},{"plan_code":"inventado"},
    {"access_type":"temporary"},{"access_type":"temporary","expires_at":"2030-01-01T12:00:00"},
    {"expires_at":"2030-01-01T12:00:00Z"},{"owner_account_id":str(uuid4())},
    {"platform_account_id":str(uuid4())},{"auth_version":0},{"expected_revision":"unknown"},
])
def test_manual_payload_rejects_ambiguous_or_injected_authority(extra):
    db=Database()
    with pytest.raises(ValidationError): change(db,**extra)


@pytest.mark.asyncio
async def test_expired_temporary_assignment_does_not_consume_an_operation(case):
    db=case
    payload=change(db,access_type="temporary",expires_at=datetime.now(timezone.utc)-timedelta(seconds=1))
    with pytest.raises(HTTPException) as caught:
        await admin._change_company_plan(db.account,db.tenant,payload,db.operator,"manual_assign")
    assert caught.value.status_code == 422 and db.control["version"] == 1 and not db.audits


@pytest.mark.asyncio
async def test_manual_removal_falls_back_to_verified_commercial_plan_without_touching_it(case):
    db=case
    db.commercial={"id":str(uuid4()),"code":"orion_profissional","name":"Orion Profissional","monthly_cents":8990,
        "max_professionals":6,"status":"active","last_verified_payment_at":datetime.now(timezone.utc),
        "cakto_bound":True,"trial_ends_at":None,"grace_until":None,"updated_at":datetime.now(timezone.utc)}
    before=deepcopy(db.commercial)
    await admin._change_company_plan(db.account,db.tenant,change(db),db.operator,"manual_assign")
    assert db.state()["code"] == "elite" and db.commercial == before
    state=db.state()
    payload=admin.ManualPlanRemoval(expected_version=state["version"],expected_revision=state["revision"],
        operation_id=uuid4(),confirmation="REMOVER ATRIBUIÇÃO")
    result=await admin._change_company_plan(db.account,db.tenant,payload,db.operator,"manual_remove")
    assert result["effective_plan"]["code"] == "profissional" and result["effective_plan"]["source"] == "cakto"
    assert db.commercial == before and db.audits[-1]["action"] == "manual_remove"
    assert (await admin._change_company_plan(db.account,db.tenant,payload,db.operator,"manual_remove"))["replayed"]


@pytest.mark.asyncio
async def test_downgrade_records_excess_without_archiving_or_removing_staff(case):
    db=case
    await admin._change_company_plan(db.account,db.tenant,change(db),db.operator,"manual_assign")
    db.professionals=8
    result=await admin._change_company_plan(db.account,db.tenant,change(db,plan_code="essencial"),db.operator,"manual_assign")
    assert result["effective_plan"]["limits"] == {"max_professionals":2,"active_professionals":8,"excess_professionals":6}
    assert not any("UPDATE professionals" in sql or "DELETE FROM" in sql for sql,_ in db.calls)


@pytest.mark.asyncio
async def test_plain_tenant_owner_cannot_call_manual_plan_api_even_with_valid_payload(case):
    from app.panel_auth import panel_actor
    db=case
    api=FastAPI();api.include_router(admin.router)
    api.dependency_overrides[panel_actor]=lambda:{"role":"member","account_id":str(db.account)}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),base_url="https://test") as client:
        result=await client.put(f"/api/v1/admin/accounts/{db.account}/companies/{db.tenant}/plan",json=change(db).model_dump(mode="json"))
    assert result.status_code == 403 and not db.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("role,maintenance,expected", [("member",False,1),("member",True,0),("admin",False,0),("support",False,0)])
async def test_permissions_are_readable_without_plan_and_only_customer_activity_updates_last_access(case, role, maintenance, expected):
    db=case;db.control={"version":0,"manual":None}
    actor=Actor(role,account_id=db.account,maintenance=maintenance)
    response=Response()
    state=(await plans.permissions(db.tenant,response,actor))["effective_plan"]
    assert state["source"] == "none" and not state["active"]
    assert response.headers["cache-control"] == "no-store" and len(db.access_updates)==expected


@pytest.mark.asyncio
async def test_overview_preserves_commercial_fields_and_uses_the_shared_effective_state(case):
    db=case
    response=Response()
    result=await plans.plan_overview(db.tenant,Actor("member",account_id=db.account),response)
    assert result["effective_plan"] == db.state() and result["subscription"] is None
    assert result["permissions"] == db.state()["permissions"] and result["limits"] == db.state()["limits"]
    assert result["plans"][1]["monthly_cents"] == 8990 and result["feature_catalog"]
    assert response.headers["cache-control"] == "no-store"


def customer_row(snapshot, **extra):
    return {"id":uuid4(),"name":"Cliente Sintético","email":"synthetic@example.test",
        "created_at":datetime.now(timezone.utc),"verified_at":None,"registration_pending":False,
        "auth_version":7,"state":"active","archived_at":None,"purge_after":None,"purged_at":None,"legal_hold":False,
        "company_name":"Empresa Sintética","company_id":UUID(snapshot["tenant_id"]),"company_status":"active",
        "company_role":"owner","company_count":1,"last_access_at":None,"plan_snapshot":snapshot,**extra}


@pytest.mark.asyncio
async def test_admin_filters_use_one_batched_snapshot_and_never_pick_an_ambiguous_company(case):
    db=case
    other=deepcopy(db.snapshot());other.update(tenant_id=str(uuid4()),manual=None,commercial={
        "code":"orion_profissional","status":"active","cakto_bound":True,
        "last_verified_payment_at":datetime.now(timezone.utc),"grace_until":None})
    empty=deepcopy(db.snapshot());empty.update(tenant_id=str(uuid4()),manual=None)
    db.account_rows=[customer_row(db.snapshot()),customer_row(other),customer_row(empty),
        customer_row(empty,company_id=None,company_name=None,company_role=None,company_count=2,plan_snapshot=None)]
    result=await admin.list_customer_accounts(page=1,page_size=20,admin=db.operator,plan="profissional",source="cakto",subscription_status="active")
    assert result["total"]==1 and result["items"][0]["effective_plan"]["code"]=="profissional"
    assert len(db.calls)==1 and "public.orion_plan_snapshot" in db.calls[0][0]
    result=await admin.list_customer_accounts(page=1,page_size=20,admin=db.operator,plan="none")
    assert result["total"]==2
    ambiguous=next(row for row in result["items"] if row["company_membership_count"]==2)
    assert ambiguous["company_id"] is None and ambiguous["can_manage_plan"] is False
    assert all(row["phone"] is None and row["last_access_at"] is None for row in result["items"])


@pytest.mark.asyncio
async def test_admin_search_keeps_wildcards_and_quotes_as_bound_literal_data(case):
    query="empresa\\_%' OR 1=1"
    await admin.list_customer_accounts(q=query,page=1,page_size=20,admin=case.operator)
    sql,params=case.calls[-1]
    assert query not in sql and params["q"]=="%empresa\\\\\\_\\%' OR 1=1%"


@pytest.mark.asyncio
async def test_active_manual_access_cannot_consume_a_trial(case, monkeypatch):
    async def owner(*_): pass
    monkeypatch.setattr(plans,"_ensure_owner",owner)
    request=plans.TrialRequest(full_name="Cliente Sintético",company_name="Empresa Sintética",whatsapp="(67) 99999-1111",privacy_ack=True)
    with pytest.raises(HTTPException) as caught:
        await plans.request_plan_trial(case.tenant,request,Actor("member",account_id=case.account))
    assert caught.value.status_code==409
    assert not any("INSERT INTO orion_plan_trial_requests" in sql for sql,_ in case.calls)
