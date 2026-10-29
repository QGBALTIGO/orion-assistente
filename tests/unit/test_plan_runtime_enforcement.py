"""Plan checks use the real resolver with synthetic storage, never providers."""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
import pytest

from app import plan_limits, plan_permissions as permissions
from app.automation_policy import plan_feature
from app.plan_execution import requires_plan


class Result:
    def __init__(self, value=None, rows=()):
        self.value, self.rows = value, rows
    def scalar_one(self): return self.value
    def scalar_one_or_none(self): return self.value
    def mappings(self): return self
    def all(self): return self.rows
    def one_or_none(self): return self.value
    def one(self): return self.value


class PlanDB:
    def __init__(self, code="essencial", used=0, platform=False):
        self.tenant = uuid4()
        self.code, self.platform = code, platform
        self.professionals = {uuid4(): True for _ in range(used)}
        self.queries = []
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    @asynccontextmanager
    async def begin(self): yield self
    def snapshot(self):
        now = datetime.now(timezone.utc)
        return {"tenant_id": str(self.tenant), "tenant_status": "active",
            "platform_workspace": self.platform,
            "manual": {"plan_code": self.code, "started_at": now-timedelta(days=1),
                       "expires_at": None} if self.code else None,
            "active_professionals": sum(self.professionals.values())}
    async def execute(self, query, params=None):
        sql, params = str(query), params or {}
        self.queries.append((sql, params))
        if "pg_advisory_xact_lock" in sql: return Result()
        if "orion_plan_snapshot" in sql: return Result(self.snapshot())
        if "SELECT active FROM professionals" in sql:
            return Result(self.professionals.get(params["id"]))
        if "SELECT id,active FROM professionals" in sql:
            return Result(rows=[{"id": key, "active": active}
                for key, active in self.professionals.items() if key in params["ids"]])
        raise AssertionError("Unmodeled SQL: " + sql)


@pytest.mark.asyncio
@pytest.mark.parametrize("code,maximum", [("essencial",2),("profissional",6),("elite",10)])
async def test_concurrent_write_entrypoint_takes_exclusive_lock_before_capacity_read(code, maximum):
    db = PlanDB(code, maximum)
    with pytest.raises(HTTPException) as error:
        await plan_limits.ensure_professional_activation(db, db.tenant, True)
    assert error.value.status_code == 409 and error.value.detail == plan_limits.LIMIT_MESSAGE
    assert "pg_advisory_xact_lock(" in db.queries[0][0]
    assert "shared" not in db.queries[0][0]


@pytest.mark.asyncio
async def test_downgrade_keeps_excess_active_staff_editable_but_blocks_reactivation():
    db = PlanDB("essencial", 8)
    active = next(iter(db.professionals))
    inactive = uuid4()
    db.professionals[inactive] = False
    await plan_limits.ensure_professional_activation(db, db.tenant, True, active)
    await plan_limits.ensure_professional_activation(db, db.tenant, False, active)
    await plan_limits.ensure_professional_activation(db, db.tenant, False)
    with pytest.raises(HTTPException) as error:
        await plan_limits.ensure_professional_activation(db, db.tenant, True, inactive)
    assert error.value.status_code == 409
    assert sum(db.professionals.values()) == 8
    assert not any("UPDATE" in sql or "DELETE" in sql for sql, _ in db.queries)


@pytest.mark.asyncio
async def test_platform_workspace_has_no_commercial_professional_limit():
    db = PlanDB(None, 12, platform=True)
    await plan_limits.ensure_professional_activation(db, db.tenant, True)


@pytest.mark.asyncio
async def test_new_company_without_grant_cannot_activate_staff():
    db = PlanDB(None)
    with pytest.raises(HTTPException) as error:
        await plan_limits.ensure_professional_activation(db, db.tenant, True)
    assert error.value.status_code == 403
    assert error.value.detail["code"] == "PLAN_FEATURE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_new_company_may_prepare_only_inactive_server_owned_onboarding_drafts():
    db = PlanDB(None)
    await plan_limits.ensure_professional_activation(db,db.tenant,False,setup_draft=True)
    inactive,active=uuid4(),uuid4()
    db.professionals.update({inactive:False,active:True})
    await plan_limits.ensure_professional_activation(db,db.tenant,False,inactive,setup_draft=True)
    for wanted,identity in ((True,None),(True,inactive),(False,active)):
        with pytest.raises(HTTPException) as error:
            await plan_limits.ensure_professional_activation(db,db.tenant,wanted,identity,setup_draft=True)
        assert error.value.status_code == 403
    assert db.professionals == {inactive:False,active:True}


@pytest.mark.asyncio
async def test_onboarding_batch_checks_final_capacity_before_any_activation():
    db = PlanDB("essencial", 1)
    a, b = uuid4(), uuid4()
    db.professionals.update({a: False, b: False})
    with pytest.raises(HTTPException) as error:
        await plan_limits.ensure_professional_batch(db, db.tenant, {str(a):True, str(b):True})
    assert error.value.status_code == 409
    await plan_limits.ensure_professional_batch(db, db.tenant, {str(a):True})
    assert sum(db.professionals.values()) == 1


@pytest.mark.asyncio
async def test_onboarding_batch_can_reduce_existing_excess_without_a_new_activation():
    db = PlanDB("essencial", 8)
    await plan_limits.ensure_professional_batch(db, db.tenant,
        {str(identity):False for identity in list(db.professionals)[:6]})
    assert sum(db.professionals.values()) == 8


@pytest.mark.parametrize("event,feature", [
    ("booking_confirmed","notifications.basic"),("cancelled","notifications.basic"),
    ("rescheduled","notifications.basic"),("reminder_24h","notifications.basic"),
    ("reminder_2h","notifications.basic"),("reminder_1h","notifications.basic"),
    ("after_service","notifications.basic"),("campaign","marketing.campaigns"),
    ("loyalty_reward","loyalty.manage"),("birthday_7d","relationships.manage"),
    ("birthday_2d","relationships.manage"),("inactive_60d","relationships.manage"),
    ("cancelled_7d","relationships.manage"),("untrusted_event",None),
])
def test_automation_feature_comes_from_persisted_event(event, feature):
    assert plan_feature(event) == feature


@pytest.mark.asyncio
async def test_background_generation_denied_before_provider_and_scope_is_restored(monkeypatch):
    from app import catalog_api
    db = PlanDB("essencial")
    monkeypatch.setattr(catalog_api, "support_session_factory", lambda: lambda: db)
    calls = []
    @requires_plan("reception.ai")
    async def generate(tenant):
        calls.append(tenant)
        return {"answer":"must never be generated"}
    with pytest.raises(HTTPException) as error:
        await generate(db.tenant)
    assert error.value.status_code == 403 and not calls
    assert permissions._scope.get() is None


@pytest.mark.asyncio
async def test_downgrade_during_generation_suppresses_output_and_resets_scope(monkeypatch):
    from app import catalog_api
    db = PlanDB("elite")
    monkeypatch.setattr(catalog_api, "support_session_factory", lambda: lambda: db)
    @requires_plan("reception.ai")
    async def generate(tenant):
        assert permissions._scope.get().tenant_id == tenant
        db.code = "essencial"
        return {"answer":"obsolete advanced output"}
    with pytest.raises(HTTPException) as error:
        await generate(db.tenant)
    assert error.value.status_code == 403
    assert permissions._scope.get() is None


@pytest.mark.asyncio
async def test_queued_campaign_after_downgrade_is_cancelled_without_sender(monkeypatch):
    from app import automation_runtime as runtime
    db = PlanDB("essencial")
    job = {"id": uuid4(), "event":"campaign", "status":"pending", "channel_id":uuid4()}
    original = db.execute
    async def execute(query, params=None):
        sql = str(query)
        if "SELECT * FROM automation_jobs" in sql:
            class JobResult(Result):
                def one_or_none(self): return dict(job) if job["status"] == "pending" else None
            return JobResult()
        if "SET status='cancelled'" in sql:
            job.update(status="cancelled", last_error=params["reason"])
            return Result()
        return await original(query, params)
    db.execute = execute
    @asynccontextmanager
    async def transaction(session, tenant): yield session
    async def settings(*args, **kwargs): return {}
    async def forbidden(*args): pytest.fail("downgraded job invoked real/synthetic sender")
    monkeypatch.setenv("MARKETING_EXECUTION_ENABLED", "1")
    monkeypatch.setattr(runtime, "get_session_factory", lambda: lambda: db)
    monkeypatch.setattr(runtime, "tenant_transaction", transaction)
    monkeypatch.setattr(runtime, "settings_for", settings)
    assert await runtime.process_automation_job({"automation_sender":forbidden}, str(db.tenant),str(job["id"])) == "plan_feature_unavailable"
    assert job == {**job, "status":"cancelled", "last_error":"plan_feature_unavailable"}


@pytest.mark.asyncio
async def test_basic_notifications_and_manual_conversations_survive_essential_downgrade():
    db = PlanDB("essencial")
    assert await permissions.has_feature(db, db.tenant, "notifications.basic")
    assert await permissions.has_feature(db, db.tenant, "conversations.manual")
    assert not await permissions.has_feature(db, db.tenant, "reception.ai")
    assert not await permissions.has_feature(db, db.tenant, "marketing.campaigns")


@pytest.mark.asyncio
async def test_basic_completion_does_not_create_new_loyalty_awards_after_downgrade():
    from app import loyalty_rewards
    db = PlanDB("essencial")
    assert await loyalty_rewards.award_completion(db,db.tenant,{"customer_id":uuid4()},datetime.now(timezone.utc)) == {"points_earned":0,"coupon":None}
    assert not any("INSERT" in sql or "UPDATE" in sql for sql,_ in db.queries)
    assert await loyalty_rewards.completion_discount(db,db.tenant,{},None,datetime.now(timezone.utc)) == 0
    with pytest.raises(HTTPException) as error:
        await loyalty_rewards.completion_discount(db,db.tenant,{},uuid4(),datetime.now(timezone.utc))
    assert error.value.status_code == 403


@pytest.fixture
def preference_db():
    import json
    from app.management_preferences import BusinessPreferences,AutomationPreferences,AutomationRule
    db=PlanDB("essencial")
    db.preferences={"business":BusinessPreferences(monthly_goal_cents=10000,weekly_summary_requested=True).model_dump(),
        "automations":AutomationPreferences(rules=[AutomationRule(event="birthday_7d",requested_enabled=True,template="Olá {cliente}")]).model_dump()}
    db.version=3
    original=db.execute
    async def execute(query,params=None):
        sql,params=str(query),params or {}
        if "SELECT preferences,version,updated_at" in sql:
            return Result({"preferences":db.preferences,"version":db.version,"updated_at":None})
        if "INSERT INTO management_preferences" in sql:
            db.preferences=json.loads(params["content"])
            db.version+=1
            return Result()
        if "INSERT INTO tenant_audit" in sql:return Result()
        return await original(query,params)
    db.execute=execute
    return db


@pytest.mark.asyncio
async def test_basic_business_edit_preserves_unavailable_advanced_preferences(preference_db):
    from app.management_preferences import save_section
    db=preference_db
    result=await save_section(db,db.tenant,"business",{"trade_name":"Empresa atualizada"},3)
    assert result["business"]["trade_name"] == "Empresa atualizada"
    assert result["business"]["monthly_goal_cents"] == 10000
    assert result["business"]["weekly_summary_requested"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("changes,feature",[
    ({"monthly_goal_cents":20000},"goals.manage"),
    ({"weekly_summary_requested":False},"finance.advanced"),
])
async def test_basic_business_endpoint_rejects_changed_advanced_fields(preference_db,changes,feature):
    from app.management_preferences import save_section
    db=preference_db
    with pytest.raises(HTTPException) as error:
        await save_section(db,db.tenant,"business",changes,3)
    assert error.value.status_code == 403 and error.value.detail["feature"] == feature
    assert db.version == 3


@pytest.mark.asyncio
async def test_basic_automation_form_preserves_omitted_advanced_rules(preference_db):
    from app.management_preferences import save_section
    db=preference_db
    result=await save_section(db,db.tenant,"automations",{
        "rules":[{"event":"reminder_1h","requested_enabled":True,"template":"Seu atendimento é às {horario}"}]},3)
    assert {rule["event"] for rule in result["automations"]["rules"]} == {"birthday_7d","reminder_1h"}
    assert result["automations"]["rules"][1]["template"] == "Olá {cliente}"


@pytest.mark.asyncio
async def test_basic_automation_endpoint_rejects_new_advanced_rule(preference_db):
    from app.management_preferences import save_section
    db=preference_db
    with pytest.raises(HTTPException) as error:
        await save_section(db,db.tenant,"automations",{
            "rules":[{"event":"birthday_2d","requested_enabled":True,"template":"Parabéns {cliente}"}]},3)
    assert error.value.status_code == 403 and error.value.detail["feature"] == "relationships.manage"
    assert db.version == 3


@pytest.mark.asyncio
async def test_public_booking_denial_has_no_commercial_plan_details(monkeypatch):
    from app import public_site_api as public
    db=PlanDB(None)
    @asynccontextmanager
    async def transaction(session,tenant): yield session
    async def noop(*args,**kwargs): return None
    async def tenant(*args): return db.tenant
    monkeypatch.setattr(public,"_same_origin",lambda request:None)
    monkeypatch.setattr(public,"read_quote",lambda *args:{"nonce":"synthetic"})
    monkeypatch.setattr(public,"public_limit",noop)
    monkeypatch.setattr(public,"resolve_public",tenant)
    monkeypatch.setattr(public,"get_session_factory",lambda:lambda:db)
    monkeypatch.setattr(public,"tenant_transaction",transaction)
    monkeypatch.setattr(public,"published_guard",noop)
    monkeypatch.setattr(public,"confirmed_public_replay",noop)
    payload=public.PublicBooking(quote="a"*40,start_at=datetime.now(timezone.utc)+timedelta(days=1),
        customer_name="Cliente sintético",customer_phone="+5567999999901",confirmed=True)
    with pytest.raises(HTTPException) as error:
        await public._public_book_saved("empresa-sintetica",payload,SimpleNamespace(),None)
    assert error.value.status_code == 409
    assert error.value.detail == "Agendamento temporariamente indisponível. Entre em contato com o estabelecimento."
    assert not any("INSERT" in sql or "UPDATE" in sql for sql,_ in db.queries)


@pytest.fixture
def management_generation(monkeypatch):
    from app import management_ai as ai, catalog_api
    import json
    db = PlanDB("profissional")
    db.run, db.provider_calls = None, 0
    original = db.execute
    async def execute(query, params=None):
        sql, params = str(query), params or {}
        db.queries.append((sql, params))
        if "SELECT * FROM management_ai_runs" in sql: return Result(db.run)
        if "SELECT count(*) FROM management_ai_runs" in sql: return Result(int(db.run is not None))
        if "INSERT INTO management_ai_runs" in sql:
            db.run = {"id":params["id"],"tenant_id":params["tenant"],"fingerprint":params["fingerprint"],
                      "status":"processing","output":None,"usage":{},"error_code":None}
            return Result()
        if "set_config('app.tenant_id'" in sql: return Result()
        if "UPDATE management_ai_runs SET status='failed'" in sql:
            assert params["tenant"] == db.tenant and params["id"] == db.run["id"]
            assert params["fingerprint"] == db.run["fingerprint"]
            if db.run["status"] == "processing":
                db.run.update(status="failed",output=None,usage=json.loads(params["usage"]),error_code=403)
            return Result()
        return await original(query, params)
    db.execute = execute
    @asynccontextmanager
    async def management_session(tenant, actor, **kwargs):
        assert tenant == db.tenant
        async with permissions.plan_transaction_guard(db, tenant): yield db
    async def context(*args): return {"revenue_cents":0}
    async def generate(**kwargs):
        db.provider_calls += 1
        db.code = "essencial"
        return {"answer":"obsolete paid suggestion","actions":[]}, {"total_tokens":39}
    monkeypatch.setattr(ai, "management_session", management_session)
    monkeypatch.setattr(ai, "get_session_factory", lambda: lambda: db)
    monkeypatch.setattr(ai, "configuration", lambda: ({"enabled":True,"configured":True,"daily_limit":10,"model":"synthetic"}, "unused-key"))
    monkeypatch.setattr(ai, "aggregate_context", context)
    monkeypatch.setattr(ai, "generate", generate)
    monkeypatch.setattr(catalog_api, "support_session_factory", lambda: lambda: db)
    return ai, db


@pytest.mark.asyncio
async def test_management_generation_rejected_before_reservation_or_provider(management_generation):
    ai, db = management_generation
    db.code = "essencial"
    with pytest.raises(HTTPException) as error:
        await ai.process(db.tenant, SimpleNamespace(account_id=uuid4()), "consultant",
            ai.ConsultantRequest(question="Como foi o mês?",idempotency_key="synthetic-request-1"))
    assert error.value.status_code == 403
    assert db.run is None and db.provider_calls == 0


@pytest.mark.asyncio
async def test_management_inflight_downgrade_finishes_failed_without_output_or_quota_refund(management_generation):
    ai, db = management_generation
    token = permissions.bind_feature(db.tenant, "consultant.ai")
    try:
        with pytest.raises(HTTPException) as error:
            await ai.process(db.tenant, SimpleNamespace(account_id=uuid4()), "consultant",
                ai.ConsultantRequest(question="Como foi o mês?",idempotency_key="synthetic-request-1"))
    finally:
        permissions.reset_feature(token)
    assert error.value.status_code == 403 and db.provider_calls == 1
    assert db.run["status"] == "failed" and db.run["output"] is None
    assert db.run["usage"] == {"total_tokens":39} and db.run["error_code"] == 403
    assert any("INSERT INTO management_ai_runs" in sql for sql,_ in db.queries)
