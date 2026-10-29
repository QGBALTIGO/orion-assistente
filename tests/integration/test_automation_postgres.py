"""Isolated synthetic PostgreSQL jobs; the transport is always an in-memory spy."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import automation_runtime as runtime, automation_api as api, management_api
from app.catalog_api import Actor, support_session_factory
from app.database import tenant_transaction

pytestmark = pytest.mark.integration
PHONE = "+5567999999901"


@pytest_asyncio.fixture
async def automation_db(monkeypatch):
    urls = [os.getenv(k) for k in ("APP_DATABASE_URL", "MIGRATION_DATABASE_URL", "SUPPORT_DATABASE_URL")]
    if not all(urls):
        if os.getenv("REQUIRE_POSTGRES_TESTS") == "1": pytest.fail("Real runtime/support/migrator PostgreSQL required")
        pytest.skip("Real PostgreSQL migrations 0020–0022 required")
    runtime_engine = create_async_engine(urls[0].replace("postgresql://", "postgresql+asyncpg://", 1), poolclass=NullPool)
    migration = create_async_engine(urls[1].replace("postgresql+psycopg://", "postgresql+asyncpg://", 1).replace("postgresql://", "postgresql+asyncpg://", 1), poolclass=NullPool)
    factory = async_sessionmaker(runtime_engine, expire_on_commit=False)
    tenant, other, account, channel, customer, campaign = (uuid4() for _ in range(6))
    plan_admin = uuid4()
    offset = datetime.now(timezone.utc).hour-14
    zone = "Etc/GMT"+("+"+str(offset) if offset>0 else str(offset) if offset else "")
    for flag in ("MARKETING_EXECUTION_ENABLED", "BUSINESS_PROFILES_ENABLED"):
        monkeypatch.setenv(flag, "1")
    monkeypatch.setattr(runtime, "get_session_factory", lambda: factory)
    monkeypatch.setattr(management_api, "get_session_factory", lambda: factory)
    support_session_factory.cache_clear()
    preferences = {"automations": {"rules": [{"event": "loyalty_reward", "requested_enabled": True, "template": "Seu cupom: {cupom}"}]}, "business": {}}
    async with migration.begin() as db:
        await db.execute(text("INSERT INTO panel_accounts(id,name,email,password_hash,role) VALUES(:id,'Admin de plano sintético',:email,'unused-synthetic','admin')"),
                         {"id":plan_admin,"email":f"plan-admin-{plan_admin}@example.test"})
        await db.execute(text("INSERT INTO panel_accounts(id,name,email,password_hash) VALUES(:id,'Pessoa sintética',:email,'unused-synthetic')"), {"id": account, "email": f"automation-{account}@example.test"})
        for t in (tenant, other):
            await db.execute(text("INSERT INTO tenants(id,name,slug,timezone) VALUES(:id,'Empresa sintética',:slug,:zone)"), {"id": t, "slug": "automation-"+t.hex, "zone": zone})
            await db.execute(text("""INSERT INTO tenant_plan_controls
                (tenant_id,manual_plan_code,manual_started_at,manual_actor_id)
                VALUES(:t,'profissional',clock_timestamp()-interval '1 day',:admin)"""),
                {"t":t,"admin":plan_admin})
        await db.execute(text("INSERT INTO tenant_memberships(tenant_id,account_id,role) VALUES(:t,:a,'owner')"), {"t": tenant, "a": account})
        await db.execute(text("""INSERT INTO qr_channels(id,tenant_id,owner_account_id,provider,label,credential_hash,credential_encrypted,
            allowed_senders,enabled,idempotency_key,request_fingerprint) VALUES(:id,:t,:a,'baileys','Canal sintético',:hash,'unused',CAST(:senders AS jsonb),true,:key,:hash)"""),
            {"id": channel, "t": tenant, "a": account, "hash": "a"*64, "senders": json.dumps([PHONE.lstrip("+")]), "key": uuid4()})
        await db.execute(text("INSERT INTO customers(id,tenant_id,name,whatsapp_e164) VALUES(:id,:t,'Cliente sintético',:phone)"), {"id": customer, "t": tenant, "phone": PHONE})
        await db.execute(text("INSERT INTO contact_consents(tenant_id,contact_e164,marketing_opt_in,operational_opt_in) VALUES(:t,:phone,true,true)"), {"t": tenant, "phone": PHONE})
        await db.execute(text("INSERT INTO management_preferences(tenant_id,preferences) VALUES(:t,CAST(:p AS jsonb))"), {"t": tenant, "p": json.dumps(preferences)})
        await db.execute(text("INSERT INTO automation_execution(tenant_id,enabled,channel_id,approved_by,approved_auth_version,approved_at,reviewed_preferences_version) VALUES(:t,true,:c,:a,1,now(),1)"), {"t": tenant, "c": channel, "a": account})
        await db.execute(text("INSERT INTO campaigns(id,tenant_id,name,template_name,message,status,version) VALUES(:id,:t,'Campanha sintética','manual','Olá {cliente}','approved',2)"), {"id": campaign, "t": tenant})
    ctx = SimpleNamespace(tenant=tenant, other=other, account=account, channel=channel, customer=customer, campaign=campaign,
                          actor=Actor("member", account_id=account), migration=migration, factory=factory, sent=[])
    async def sender(t, c, phone, body, operation):
        assert (t, c, phone) == (tenant, channel, PHONE)
        ctx.sent.append(operation)
        return operation
    ctx.sender = sender
    try:
        yield ctx
    finally:
        async with migration.begin() as db:
            for t in (tenant, other):
                await db.execute(text("UPDATE tenants SET whatsapp_channel_id=NULL WHERE id=:t"),{"t":t})
                for table in ("communication_pending", "automation_jobs", "automation_contact_state", "automation_execution", "loyalty_coupons", "loyalty_rules",
                              "campaign_recipients", "campaigns", "contact_consents", "management_requests", "management_preferences",
                              "tenant_audit", "qr_channels", "tenant_memberships", "appointment_history", "calendar_outbox",
                              "appointment_items", "appointments", "professionals", "customers", "tenant_plan_audit", "tenant_plan_controls"):
                    await db.execute(text(f"DELETE FROM {table} WHERE tenant_id=:t"), {"t": t})
                await db.execute(text("DELETE FROM tenants WHERE id=:t"), {"t": t})
            await db.execute(text("DELETE FROM panel_accounts WHERE id=:a"), {"a": account})
            await db.execute(text("DELETE FROM panel_accounts WHERE id=:a"), {"a": plan_admin})
        await runtime_engine.dispose(); await migration.dispose(); support_session_factory.cache_clear()


async def queue(ctx, key=None, *, event="campaign", facts=None, labels=None):
    async with ctx.factory() as db:
        async with tenant_transaction(db, ctx.tenant):
            settings = await runtime.settings_for(db, ctx.tenant)
            preference = (await db.execute(text("SELECT preferences,version FROM management_preferences WHERE tenant_id=:t"), {"t": ctx.tenant})).mappings().one()
            customer = (await db.execute(text("SELECT * FROM customers WHERE tenant_id=:t AND id=:id"), {"t": ctx.tenant, "id": ctx.customer})).mappings().one()
            key = key or str(uuid4())
            details = {"message": "Olá {cliente}", "scheduled_at": datetime.now(timezone.utc)-timedelta(seconds=1), "campaign_version": 2, **(facts or {})}
            created = await runtime.queue_notification(db, ctx.tenant, settings, preference, customer, event, key,
                details, labels or {"cliente": "Cliente sintético"}, campaign_id=ctx.campaign if event == "campaign" else None)
            item = (await db.execute(text("SELECT * FROM automation_jobs WHERE tenant_id=:t AND event_key=:key"), {"t": ctx.tenant, "key": key})).mappings().one_or_none()
            return created, dict(item) if item else None


async def process(ctx, item, sender=None):
    return await runtime.process_automation_job({"automation_sender": sender or ctx.sender}, str(ctx.tenant), str(item["id"]))


@pytest.mark.asyncio
async def test_persisted_campaign_downgrade_cancels_job_without_outbound_attempt(automation_db):
    ctx = automation_db
    created,item = await queue(ctx,"synthetic-plan-downgrade")
    assert created and item["status"] == "pending"
    async with ctx.migration.begin() as db:
        await db.execute(text("""UPDATE tenant_plan_controls SET manual_plan_code='essencial',
            version=version+1,updated_at=clock_timestamp() WHERE tenant_id=:t"""),{"t":ctx.tenant})
    assert await process(ctx,item) == "plan_feature_unavailable"
    assert not ctx.sent
    async with ctx.migration.connect() as db:
        row=(await db.execute(text("SELECT status,last_error,attempted_at FROM automation_jobs WHERE tenant_id=:t AND id=:id"),
            {"t":ctx.tenant,"id":item["id"]})).mappings().one()
    assert row["status"] == "cancelled" and row["last_error"] == "plan_feature_unavailable"
    assert row["attempted_at"] is None


@pytest.mark.asyncio
async def test_actual_runtime_role_owner_function_and_cross_tenant_rls(automation_db):
    ctx = automation_db
    async with ctx.factory() as db:
        async with tenant_transaction(db, ctx.tenant):
            assert (await runtime.settings_for(db, ctx.tenant))["owner_active"] is True
            assert not (await db.execute(text("SELECT public.automation_owner_active(:t,:a,1)"), {"t": ctx.other, "a": ctx.account})).scalar_one()
    async with ctx.factory() as db:
        with pytest.raises(DBAPIError):
            async with tenant_transaction(db, ctx.tenant):
                await db.execute(text("SELECT id FROM panel_accounts"))
    await queue(ctx)
    async with ctx.factory() as db:
        async with tenant_transaction(db, ctx.other):
            assert (await db.execute(text("SELECT id FROM automation_jobs"))).all() == []


@pytest.mark.asyncio
async def test_real_planner_queries_do_not_fabricate_events_or_jobs(automation_db):
    ctx = automation_db
    assert await runtime.plan_tenant(ctx.tenant) == 0
    assert not ctx.sent


@pytest.mark.asyncio
async def test_job_dedupe_and_concurrent_worker_only_one_accepted_attempt(automation_db):
    ctx = automation_db
    created, item = await queue(ctx, "synthetic-deduplication")
    assert created and not (await queue(ctx, "synthetic-deduplication"))[0]
    entered, release = asyncio.Event(), asyncio.Event()
    async def sender(*args):
        entered.set(); await release.wait(); return await ctx.sender(*args)
    task = asyncio.create_task(process(ctx, item, sender))
    await asyncio.wait_for(entered.wait(), 5)
    try: assert await process(ctx, item) == "already_claimed"
    finally: release.set()
    assert await task == "sent" and ctx.sent == [str(item["id"])]
    async with ctx.migration.connect() as db:
        row = (await db.execute(text("SELECT status,delivery_status FROM automation_jobs WHERE id=:id"), {"id": item["id"]})).mappings().one()
        assert row == {"status": "sent", "delivery_status": "unknown"}


@pytest.mark.asyncio
@pytest.mark.parametrize("change,reason", [("consent", "consent_changed"), ("owner", "approval_revoked"),
    ("auth_version", "approval_revoked"), ("customer", "customer_unavailable"), ("campaign", "campaign_paused"), ("preference", "configuration_changed")])
async def test_current_fact_changes_cancel_without_transport(automation_db, change, reason):
    ctx = automation_db
    _, item = await queue(ctx)
    sql = {"consent": "UPDATE contact_consents SET marketing_opt_in=false WHERE tenant_id=:t",
           "owner": "UPDATE tenant_memberships SET active=false WHERE tenant_id=:t",
           "auth_version": "UPDATE panel_accounts SET auth_version=auth_version+1 WHERE id=:a",
           "customer": "UPDATE customers SET name='Alterado',version=version+1 WHERE tenant_id=:t",
           "campaign": "UPDATE campaigns SET version=version+1 WHERE tenant_id=:t",
           "preference": "UPDATE management_preferences SET version=version+1 WHERE tenant_id=:t"}[change]
    async with ctx.migration.begin() as db:
        await db.execute(text(sql), {"t": ctx.tenant, "a": ctx.account})
    assert await process(ctx, item) == reason and not ctx.sent


@pytest.mark.asyncio
async def test_conversation_change_and_manual_pause_never_opt_in_or_recreate(automation_db):
    ctx = automation_db
    _, item = await queue(ctx)
    await runtime.interaction(ctx.tenant, PHONE, human=True)
    assert await process(ctx, item) == "already_claimed"
    assert not (await queue(ctx))[0]
    response = await api.contact_pause(ctx.tenant, api.ContactPause(contact_e164=PHONE, paused=False), ctx.actor)
    assert response["recorded"] and response["paused"] is False
    assert await process(ctx, item) == "already_claimed" and not ctx.sent
    assert (await api.get_consent(ctx.tenant, PHONE, ctx.actor))["marketing_opt_in"] is True


@pytest.mark.asyncio
async def test_uncertain_attempt_reconciles_read_only_after_pause_and_reset(automation_db, monkeypatch):
    ctx = automation_db
    _, item = await queue(ctx)
    async def timeout(*args): ctx.sent.append(args[-1]); raise TimeoutError("synthetic-secret-must-not-persist")
    assert await process(ctx, item, timeout) == "uncertain"
    assert await process(ctx, item) == "already_claimed"
    monkeypatch.setenv("MARKETING_EXECUTION_ENABLED", "0"); monkeypatch.setenv("MARKETING_RECONCILIATION_ENABLED", "1")
    async def reader(t, c, operation): return {"operation_id": operation, "state": "accepted", "accepted": True, "delivery": "read"}
    assert await runtime.reconcile_automation_job({"automation_inspector": reader}, str(ctx.tenant), str(item["id"])) == "read"
    async def unknown(t, c, operation): return {"operation_id": operation, "state": "unknown", "accepted": False, "delivery": "unknown"}
    await runtime.reconcile_automation_job({"automation_inspector": unknown}, str(ctx.tenant), str(item["id"]))
    async with ctx.migration.connect() as db:
        row = (await db.execute(text("SELECT status,delivery_status,last_error FROM automation_jobs WHERE id=:id"), {"id": item["id"]})).mappings().one()
        assert row == {"status": "sent", "delivery_status": "read", "last_error": None}
    assert ctx.sent == [str(item["id"])]


@pytest.mark.asyncio
async def test_daily_limit_uses_committed_attempts_across_jobs(automation_db):
    ctx = automation_db
    async with ctx.migration.begin() as db:
        await db.execute(text("UPDATE automation_execution SET daily_limit=1 WHERE tenant_id=:t"), {"t": ctx.tenant})
    _, first = await queue(ctx); assert await process(ctx, first) == "sent"
    _, second = await queue(ctx); assert await process(ctx, second) == "daily_limit"
    assert len(ctx.sent) == 1


@pytest.mark.asyncio
async def test_campaign_review_tracks_recipient_facts_and_fresh_approval_dedupes(automation_db):
    ctx = automation_db
    async with ctx.migration.begin() as db:
        await db.execute(text("UPDATE campaigns SET status='draft',version=1 WHERE id=:id"), {"id": ctx.campaign})
    initial = await api.review(ctx.tenant, ctx.campaign, ctx.actor)
    async with ctx.migration.begin() as db:
        await db.execute(text("UPDATE customers SET name='Novo nome',version=version+1 WHERE id=:id"), {"id": ctx.customer})
    request = api.CampaignApproval(expected_version=1, expected_snapshot=initial["snapshot"], idempotency_key=str(uuid4()), acknowledged=True)
    with pytest.raises(HTTPException): await api.approve(ctx.tenant, ctx.campaign, request, ctx.actor)
    current = await api.review(ctx.tenant, ctx.campaign, ctx.actor)
    fresh = request.model_copy(update={"expected_snapshot": current["snapshot"], "idempotency_key": str(uuid4())})
    assert (await api.approve(ctx.tenant, ctx.campaign, fresh, ctx.actor))["recipients"] == 1
    assert (await api.approve(ctx.tenant, ctx.campaign, fresh, ctx.actor))["replayed"] is True
    assert not ctx.sent


@pytest.mark.asyncio
async def test_real_coupon_expiry_blocks_notification_without_modifying_reward(automation_db):
    ctx = automation_db
    rule, coupon = uuid4(), uuid4()
    async with ctx.migration.begin() as db:
        await db.execute(text("INSERT INTO loyalty_rules(id,tenant_id,name,reward_description) VALUES(:id,:t,'Programa sintético','Recompensa sintética')"), {"id": rule, "t": ctx.tenant})
        await db.execute(text("""INSERT INTO loyalty_coupons(id,tenant_id,customer_id,rule_id,rule_version,code,reward_type,reward_value_cents,
            description,points_spent,issued_at,expires_at) VALUES(:id,:t,:c,:r,1,'SYNTHETIC','fixed',100,'Recompensa sintética',10,now(),now()+interval '1 day')"""),
            {"id": coupon, "t": ctx.tenant, "c": ctx.customer, "r": rule})
    _, item = await queue(ctx, event="loyalty_reward", facts={"occurred_at": datetime.now(timezone.utc), "coupon_id": coupon, "coupon_version": 1}, labels={"cupom": "SYNTHETIC"})
    async with ctx.migration.begin() as db:
        await db.execute(text("UPDATE loyalty_coupons SET issued_at=now()-interval '2 days',expires_at=now()-interval '1 second' WHERE id=:id"), {"id": coupon})
    assert await process(ctx, item) == "coupon_changed" and not ctx.sent


@pytest.mark.asyncio
async def test_preferences_edit_does_not_activate_new_content_without_fresh_owner_review(automation_db, monkeypatch):
    ctx = automation_db
    rule, coupon = uuid4(), uuid4()
    async with ctx.migration.begin() as db:
        await db.execute(text("INSERT INTO loyalty_rules(id,tenant_id,name,reward_description) VALUES(:id,:t,'Programa sintético','Recompensa sintética')"), {"id": rule, "t": ctx.tenant})
        await db.execute(text("""INSERT INTO loyalty_coupons(id,tenant_id,customer_id,rule_id,rule_version,code,reward_type,reward_value_cents,
            description,points_spent,issued_at,expires_at) VALUES(:id,:t,:c,:r,1,'REVIEW-SYNTHETIC','fixed',100,'Recompensa sintética',10,now()-interval '1 minute',now()+interval '1 day')"""),
            {"id": coupon, "t": ctx.tenant, "c": ctx.customer, "r": rule})
        await db.execute(text("UPDATE management_preferences SET version=version+1 WHERE tenant_id=:t"), {"t": ctx.tenant})
    status = await api.get_execution(ctx.tenant, ctx.actor)
    assert status["requested_enabled"] and not status["enabled"] and status["preferences_version"] == 2
    assert await runtime.plan_tenant(ctx.tenant) == 0
    assert not (await queue(ctx))[0]
    async def checked(*args): return {}
    monkeypatch.setattr(api, "checked_channel", checked)
    stale = api.ExecutionSave(expected_version=1, expected_preferences_version=1, idempotency_key=str(uuid4()),
        enabled=True, channel_id=ctx.channel, acknowledged=True)
    with pytest.raises(HTTPException) as rejected:
        await api.save_execution(ctx.tenant, stale, ctx.actor)
    assert rejected.value.status_code == 409
    fresh = stale.model_copy(update={"expected_preferences_version": 2, "idempotency_key": str(uuid4())})
    assert (await api.save_execution(ctx.tenant, fresh, ctx.actor))["enabled"]
    # A fresh approval starts a new cutoff; it does not backfill this old coupon.
    assert await runtime.plan_tenant(ctx.tenant) == 0
    async with ctx.migration.begin() as db:
        await db.execute(text("UPDATE loyalty_coupons SET issued_at=clock_timestamp() WHERE id=:id"), {"id":coupon})
    assert await runtime.plan_tenant(ctx.tenant) == 1
    assert not ctx.sent


@pytest.mark.asyncio
async def test_approved_inactive_campaign_stops_when_customer_books_before_send(automation_db):
    ctx = automation_db
    pro = uuid4()
    now = datetime.now(timezone.utc)
    async with ctx.migration.begin() as db:
        await db.execute(text("INSERT INTO professionals(id,tenant_id,name) VALUES(:id,:t,'Profissional sintético')"), {"id": pro, "t": ctx.tenant})
        await db.execute(text("UPDATE campaigns SET audience='inactive_customers',status='draft',version=1 WHERE id=:id"), {"id": ctx.campaign})
        await db.execute(text("""INSERT INTO appointments(tenant_id,customer_id,professional_id,protocol,idempotency_key,request_fingerprint,
            start_at,end_at,occupied_from,occupied_until,status,total_cents) VALUES(:t,:c,:p,'SYNTHOLD',:key,:fp,
            :start,:end,:start,:end,'completed',3500)"""), {"t": ctx.tenant, "c": ctx.customer, "p": pro,
            "key": str(uuid4()), "fp": "b"*64, "start": now-timedelta(days=70), "end": now-timedelta(days=70)+timedelta(minutes=30)})
    review = await api.review(ctx.tenant, ctx.campaign, ctx.actor)
    assert review["eligible_count"] == 1
    result = await api.approve(ctx.tenant, ctx.campaign, api.CampaignApproval(expected_version=1,
        expected_snapshot=review["snapshot"], idempotency_key=str(uuid4()), acknowledged=True), ctx.actor)
    assert result["recipients"] == 1
    async with ctx.migration.begin() as db:
        item = dict((await db.execute(text("SELECT * FROM automation_jobs WHERE tenant_id=:t AND campaign_id=:id"), {"t": ctx.tenant, "id": ctx.campaign})).mappings().one())
        await db.execute(text("""INSERT INTO appointments(tenant_id,customer_id,professional_id,protocol,idempotency_key,request_fingerprint,
            start_at,end_at,occupied_from,occupied_until,status,total_cents) VALUES(:t,:c,:p,'SYNTHNEW',:key,:fp,
            :start,:end,:start,:end,'confirmed',3500)"""), {"t": ctx.tenant, "c": ctx.customer, "p": pro,
            "key": str(uuid4()), "fp": "c"*64, "start": now+timedelta(days=2), "end": now+timedelta(days=2, minutes=30)})
    assert await process(ctx, item) == "new_booking" and not ctx.sent
    async with ctx.migration.connect() as db:
        row = (await db.execute(text("SELECT status,attempted_at,last_error FROM automation_jobs WHERE id=:id"), {"id": item["id"]})).mappings().one()
        assert row == {"status": "cancelled", "attempted_at": None, "last_error": "new_booking"}
        assert (await db.execute(text("SELECT count(*) FROM appointments WHERE tenant_id=:t"), {"t": ctx.tenant})).scalar_one() == 2
