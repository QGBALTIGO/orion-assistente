"""Real PostgreSQL gates for recurrence, reviewed membership and AI reservations."""
import asyncio
from datetime import datetime, timedelta, timezone
import os
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

from fastapi import FastAPI
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.catalog_api import Actor, authenticate, router as catalog_router, support_session_factory
from app.database import get_engine
from app import management_ai as ai
from app.management_blocks import router as blocks_router
from app.management_preferences import router as preferences_router
from app.management_team import router as team_router, invitation_router

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def extras_db(monkeypatch):
    if not all(os.getenv(key) for key in ("APP_DATABASE_URL", "MIGRATION_DATABASE_URL", "SUPPORT_DATABASE_URL")):
        if os.getenv("REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("As verificações de gestão exigem PostgreSQL real")
        pytest.skip("Requires PostgreSQL at migration 0016 or later")
    monkeypatch.setenv("BUSINESS_PROFILES_ENABLED", "1")
    get_engine.cache_clear()
    support_session_factory.cache_clear()
    migrator = create_async_engine(os.environ["MIGRATION_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql+asyncpg://", 1))
    runtime = create_async_engine(os.environ["APP_DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://", 1))
    tenant, other, pro, other_pro, owner, guest = [uuid4() for _ in range(6)]
    account_email = str(guest) + "@example.invalid"
    async with migrator.begin() as db:
        for identity in (tenant, other):
            await db.execute(text("INSERT INTO tenants(id,name,slug,timezone) VALUES(:id,'Empresa de teste',:slug,'UTC')"), {"id": identity, "slug": "extras-" + str(identity)})
        for scope, identity in ((tenant, pro), (other, other_pro)):
            await db.execute(text("INSERT INTO professionals(id,tenant_id,name) VALUES(:id,:tenant,'Profissional sintético')"), {"id": identity, "tenant": scope})
        for identity in (owner, guest):
            await db.execute(text("INSERT INTO panel_accounts(id,name,email,password_hash,verified_at) VALUES(:id,'Conta sintética',:email,'test-not-a-password',now())"), {"id": identity, "email": str(identity) + "@example.invalid"})
        await db.execute(text("INSERT INTO tenant_memberships(tenant_id,account_id,role) VALUES(:tenant,:account,'owner')"), {"tenant": tenant, "account": owner})
    selected = {"actor": Actor("member", tenant_id=tenant, account_id=owner, membership_role="owner")}
    async def auth_override():
        return selected["actor"]
    # Exercise the production route order; generic catalog paths must not steal
    # PUT /management/preferences or other specific module routes.
    from app.main import app as main_app
    app = FastAPI()
    app.include_router(main_app.router)
    app.dependency_overrides[authenticate] = auth_override
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test.invalid") as client:
        try:
            yield {"tenant": tenant, "other": other, "pro": pro, "other_pro": other_pro, "owner": owner, "guest": guest,
                   "guest_email": account_email, "selected": selected, "client": client, "migrator": migrator, "runtime": runtime,
                   "base": f"/api/v1/tenants/{tenant}/management", "other_base": f"/api/v1/tenants/{other}/management"}
        finally:
            async with migrator.begin() as db:
                for table in ("management_notification_reads", "management_team_invitations", "management_preferences", "management_ai_runs",
                              "management_block_occurrences", "management_block_series", "management_requests", "schedule_blocks", "time_off",
                              "appointment_history", "appointments", "customers", "professionals", "tenant_audit", "tenant_membership_events", "tenant_memberships"):
                    await db.execute(text(f"DELETE FROM {table} WHERE tenant_id IN (:a,:b)"), {"a": tenant, "b": other})
                await db.execute(text("DELETE FROM tenants WHERE id IN (:a,:b)"), {"a": tenant, "b": other})
                await db.execute(text("DELETE FROM panel_accounts WHERE id IN (:a,:b)"), {"a": owner, "b": guest})
    await migrator.dispose()
    await runtime.dispose()
    await get_engine().dispose()
    get_engine.cache_clear()
    support_session_factory.cache_clear()


def series(d, **changes):
    today = datetime.now(timezone.utc).date() + timedelta(days=10)
    return {"professional_id": str(d["pro"]), "start_date": str(today), "until_date": str(today+timedelta(days=2)),
            "frequency": "daily", "kind": "hours", "start_time": "12:00", "end_time": "13:00", "idempotency_key": str(uuid4()), **changes}


async def create_appointment(d, start):
    appointment, customer, event = uuid4(), uuid4(), uuid4()
    async with d["migrator"].begin() as db:
        await db.execute(text("INSERT INTO customers(id,tenant_id,name) VALUES(:id,:tenant,'Cliente sintético')"), {"id": customer, "tenant": d["tenant"]})
        await db.execute(text("""INSERT INTO appointments(id,tenant_id,customer_id,professional_id,protocol,idempotency_key,request_fingerprint,
            start_at,end_at,occupied_from,occupied_until,status,total_cents)
            VALUES(:id,:tenant,:customer,:pro,'AG-TESTXX',:key,:fingerprint,:start,:stop,:start,:stop,'confirmed',1000)"""),
            {"id": appointment, "tenant": d["tenant"], "customer": customer, "pro": d["pro"], "key": str(uuid4()), "fingerprint": "0"*64,
             "start": start, "stop": start+timedelta(minutes=30)})
        await db.execute(text("INSERT INTO appointment_history(id,tenant_id,appointment_id,event,operator) VALUES(:id,:tenant,:appointment,'confirmed','owner')"),
                         {"id": event, "tenant": d["tenant"], "appointment": appointment})
    return event


@pytest.mark.asyncio
async def test_series_are_atomic_idempotent_and_removed_by_scope(extras_db):
    d = extras_db
    payload = series(d)
    created = await d["client"].post(d["base"] + "/block-series", json=payload)
    assert created.status_code == 201, created.text
    assert created.json()["count"] == 3
    repeated = await d["client"].post(d["base"] + "/block-series", json=payload)
    assert repeated.status_code == 201 and repeated.json()["replayed"]
    identity = created.json()["id"]
    legacy = await d["client"].delete(f'/api/v1/tenants/{d["tenant"]}/schedule_blocks/{created.json()["blocks"][0]["block_id"]}')
    assert legacy.status_code == 409
    removed = await d["client"].delete(d["base"] + "/block-series/" + identity, params={"scope": "one", "occurrence_date": payload["start_date"]})
    assert removed.json()["removed_count"] == 1
    remaining = await d["client"].delete(d["base"] + "/block-series/" + identity, params={"scope": "following", "occurrence_date": payload["start_date"]})
    assert remaining.json()["removed_count"] == 2
    final = await d["client"].get(d["base"] + "/block-series", params={"start_date": payload["start_date"], "end_date": payload["until_date"]})
    assert not any(x["active"] for x in final.json()["items"][0]["occurrences"])
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM schedule_blocks WHERE tenant_id=:tenant"), {"tenant": d["tenant"]})).scalar_one() == 0


@pytest.mark.asyncio
async def test_any_series_conflict_rolls_back_every_occurrence(extras_db):
    d = extras_db
    payload = series(d)
    start = datetime.fromisoformat(payload["until_date"] + "T12:00:00+00:00")
    await create_appointment(d, start)
    response = await d["client"].post(d["base"] + "/block-series", json=payload)
    assert response.status_code == 409, response.text
    async with d["migrator"].connect() as db:
        for table in ("schedule_blocks", "management_block_series", "management_requests"):
            assert (await db.execute(text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"), {"tenant": d["tenant"]})).scalar_one() == 0


@pytest.mark.asyncio
async def test_two_concurrent_overlapping_series_only_one_is_created(extras_db):
    d = extras_db
    a, b = series(d), series(d)
    results = await asyncio.gather(*(d["client"].post(d["base"] + "/block-series", json=x) for x in (a, b)))
    assert sorted(x.status_code for x in results) == [201, 409]
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM schedule_blocks WHERE tenant_id=:tenant"), {"tenant": d["tenant"]})).scalar_one() == 3


@pytest.mark.asyncio
async def test_new_modules_fail_closed_across_tenants_and_without_rls_scope(extras_db):
    d = extras_db
    for path in ("/team", "/ai/history", "/notifications", "/preferences", "/marketing/automations", "/marketing/queue"):
        assert (await d["client"].get(d["other_base"] + path)).status_code == 403
    response = await d["client"].post(d["base"] + "/block-series", json=series(d, professional_id=str(d["other_pro"])))
    assert response.status_code == 404
    async with d["runtime"].connect() as db:
        for table in ("management_preferences", "management_block_series", "management_ai_runs", "management_notification_reads"):
            assert (await db.execute(text("SELECT count(*) FROM " + table))).scalar_one() == 0
        with pytest.raises(DBAPIError):
            await db.execute(text("SELECT * FROM management_team_invitations"))


@pytest.mark.asyncio
async def test_invitation_requires_claim_and_owner_review_and_never_stores_token(extras_db):
    d = extras_db
    request = {"email": d["guest_email"], "role": "editor", "idempotency_key": str(uuid4())}
    result = await d["client"].post(d["base"] + "/team/invitations", json=request)
    assert result.status_code == 201, result.text
    invitation = result.json()
    token = parse_qs(urlsplit(invitation["link"]).fragment)["token"][0]
    repeated = await d["client"].post(d["base"] + "/team/invitations", json=request)
    assert repeated.json()["link"] is None and repeated.json()["replayed"]
    prematurely = await d["client"].patch(d["base"] + "/team/invitations/" + invitation["id"], json={"action": "approve", "expected_version": 1})
    assert prematurely.status_code == 409
    preview = await d["client"].post("/api/v1/team-invitations/preview", json={"token": token})
    assert preview.status_code == 200 and d["guest_email"] not in preview.text and "tenant_id" not in preview.text
    d["selected"]["actor"] = Actor("member", account_id=d["guest"])
    accepted = await d["client"].post("/api/v1/team-invitations/accept", json={"token": token})
    assert accepted.status_code == 200 and accepted.json()["status"] == "claimed"
    assert (await d["client"].get(d["base"] + "/team")).status_code == 403
    d["selected"]["actor"] = Actor("member", account_id=d["owner"])
    approved = await d["client"].patch(d["base"] + "/team/invitations/" + invitation["id"], json={"action": "approve", "expected_version": 2})
    assert approved.status_code == 200, approved.text
    async with d["migrator"].connect() as db:
        stored = (await db.execute(text("SELECT * FROM management_team_invitations WHERE tenant_id=:tenant"), {"tenant": d["tenant"]})).mappings().one()
        assert token not in str(dict(stored)) and len(stored["token_digest"]) == 64
        assert (await db.execute(text("SELECT count(*) FROM tenant_membership_events WHERE tenant_id=:tenant"), {"tenant": d["tenant"]})).scalar_one() == 1
    d["selected"]["actor"] = Actor("member", account_id=d["guest"])
    assert (await d["client"].post(d["base"] + "/team/invitations", json={"role": "viewer", "idempotency_key": str(uuid4())})).status_code == 403


@pytest.mark.asyncio
async def test_invitation_wrong_email_unverified_account_and_expiry_are_enforced(extras_db):
    d = extras_db
    result = await d["client"].post(d["base"] + "/team/invitations", json={"email": "another@example.invalid", "role": "viewer", "idempotency_key": str(uuid4())})
    token = parse_qs(urlsplit(result.json()["link"]).fragment)["token"][0]
    d["selected"]["actor"] = Actor("member", account_id=d["guest"])
    assert (await d["client"].post("/api/v1/team-invitations/accept", json={"token": token})).status_code == 403
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE management_team_invitations SET expires_at=now()-interval '1 hour' WHERE tenant_id=:tenant"), {"tenant": d["tenant"]})
    assert (await d["client"].post("/api/v1/team-invitations/preview", json={"token": token})).status_code == 410


@pytest.mark.asyncio
async def test_preferences_version_and_automation_draft_do_not_enqueue_messages(extras_db):
    d = extras_db
    first = await d["client"].put(d["base"] + "/preferences", json={"expected_version": 0, "business": {"monthly_goal_cents": 120000}})
    assert first.status_code == 200 and first.json()["version"] == 1, first.text
    stale = await d["client"].put(d["base"] + "/preferences", json={"expected_version": 0, "business": {}})
    assert stale.status_code == 409
    automations = await d["client"].put(d["base"] + "/marketing/automations", json={"expected_version": 1, "configuration": {"rules": [{"event": "reminder_1h", "requested_enabled": True, "template": "Mensagem de teste para revisão"}]}})
    assert automations.status_code == 200 and automations.json()["execution_enabled"] is False
    queue = await d["client"].get(d["base"] + "/marketing/queue")
    assert queue.status_code == 200 and queue.json()["items"] == []


@pytest.mark.asyncio
async def test_notifications_use_immutable_events_and_per_account_read_state(extras_db):
    d = extras_db
    event = await create_appointment(d, datetime.now(timezone.utc)+timedelta(days=2))
    result = await d["client"].get(d["base"] + "/notifications")
    assert result.status_code == 200, result.text
    assert result.json()["items"][0]["id"] == str(event) and result.json()["items"][0]["read_at"] is None
    read = await d["client"].post(d["base"] + "/notifications/read", json={"ids": [str(event), str(uuid4())]})
    assert read.status_code == 200
    assert (await d["client"].get(d["base"] + "/notifications?unread_only=true")).json()["items"] == []


@pytest.mark.asyncio
async def test_ai_reservation_prevents_duplicate_provider_cost_and_caches_output(extras_db, monkeypatch):
    d = extras_db
    monkeypatch.setenv("MANAGEMENT_AI_ENABLED", "1")
    monkeypatch.setenv("GROQ_API_KEY", "test-not-a-real-key")
    monkeypatch.setenv("MANAGEMENT_AI_DAILY_LIMIT", "1")
    calls = []
    async def fake_generate(**kwargs):
        calls.append(kwargs)
        assert d["guest_email"] not in str(kwargs["sources"]) and "customer_id" not in str(kwargs["sources"])
        await asyncio.sleep(.05)
        return {"answer": "Não há receita concluída neste período.", "actions": []}, {"total_tokens": 42}
    monkeypatch.setattr(ai, "generate", fake_generate)
    payload = {"question": "Como foi a receita?", "idempotency_key": str(uuid4())}
    results = await asyncio.gather(*(d["client"].post(d["base"] + "/ai/consultant", json=payload) for _ in range(4)))
    assert len(calls) == 1 and any(r.status_code == 200 for r in results)
    assert all(r.status_code in (200, 409) for r in results), [r.text for r in results]
    replay = await d["client"].post(d["base"] + "/ai/consultant", json=payload)
    assert replay.status_code == 200 and replay.json()["replayed"] and replay.json()["usage"]["total_tokens"] == 42
    exceeded = await d["client"].post(d["base"] + "/ai/consultant", json={**payload, "idempotency_key": str(uuid4())})
    assert exceeded.status_code == 429
    different = await d["client"].post(d["base"] + "/ai/consultant", json={**payload, "question": "Outro pedido"})
    assert different.status_code == 409
    status = await d["client"].get(d["base"] + "/ai/status")
    assert status.json()["used_today"] == 1 and "test-not-a-real-key" not in status.text


@pytest.mark.asyncio
async def test_ai_revalidates_membership_after_provider_returns(extras_db, monkeypatch):
    d = extras_db
    monkeypatch.setenv("MANAGEMENT_AI_ENABLED", "1")
    monkeypatch.setenv("GROQ_API_KEY", "test-not-a-real-key")
    async def revoke(**kwargs):
        async with d["migrator"].begin() as db:
            await db.execute(text("UPDATE tenant_memberships SET active=false WHERE tenant_id=:tenant AND account_id=:account"), {"tenant": d["tenant"], "account": d["owner"]})
        return {"answer": "Informação privada", "actions": []}, {}
    monkeypatch.setattr(ai, "generate", revoke)
    response = await d["client"].post(d["base"] + "/ai/consultant", json={"question": "Como foi a receita?", "idempotency_key": str(uuid4())})
    assert response.status_code == 403 and "Informação privada" not in response.text


@pytest.mark.asyncio
async def test_concurrent_reviews_cannot_replace_a_membership_created_by_another_invitation(extras_db):
    d = extras_db
    invitations = []
    for role in ("editor", "viewer"):
        response = await d["client"].post(d["base"] + "/team/invitations",
            json={"email": d["guest_email"], "role": role, "idempotency_key": str(uuid4())})
        assert response.status_code == 201, response.text
        invitations.append(response.json())
    d["selected"]["actor"] = Actor("member", account_id=d["guest"])
    for invitation in invitations:
        token = parse_qs(urlsplit(invitation["link"]).fragment)["token"][0]
        accepted = await d["client"].post("/api/v1/team-invitations/accept", json={"token": token})
        assert accepted.status_code == 200 and accepted.json()["status"] == "claimed", accepted.text
    d["selected"]["actor"] = Actor("member", tenant_id=d["tenant"], account_id=d["owner"], membership_role="owner")
    results = await asyncio.gather(*(d["client"].patch(d["base"] + "/team/invitations/" + invitation["id"],
        json={"action": "approve", "expected_version": 2}) for invitation in invitations))
    assert sorted(response.status_code for response in results) == [200, 409], [response.text for response in results]
    approved = next(response.json() for response in results if response.status_code == 200)
    async with d["migrator"].connect() as db:
        stored = (await db.execute(text("SELECT role,active FROM tenant_memberships WHERE tenant_id=:tenant AND account_id=:account"),
            {"tenant": d["tenant"], "account": d["guest"]})).one()
        assert stored == (approved["role"], True)
        assert (await db.execute(text("SELECT count(*) FROM tenant_membership_events WHERE tenant_id=:tenant AND account_id=:account"),
            {"tenant": d["tenant"], "account": d["guest"]})).scalar_one() == 1
        assert (await db.execute(text("SELECT count(*) FROM management_team_invitations WHERE tenant_id=:tenant AND status='approved'"),
            {"tenant": d["tenant"]})).scalar_one() == 1
