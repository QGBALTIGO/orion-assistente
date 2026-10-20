"""Compare native composite reads with independent, pre-batching SQL oracles."""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from uuid import UUID, uuid4

from asyncpg.pgproto.pgproto import UUID as DriverUUID
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
import pytest
from sqlalchemy import text

from app import onboarding_service as service
from app.database import tenant_transaction
from app.tenant_access import TenantAccess
from test_onboarding_postgres import onboarding_db, configured, save  # noqa: F401

pytestmark = pytest.mark.integration


async def original_facts(db, tenant):
    business = (await db.execute(text("SELECT id,name,timezone,status FROM tenants WHERE id=:t"),
                                 {"t": tenant})).mappings().first()
    if business is None:
        raise HTTPException(404, "Empresa não encontrada")
    facts = {"business": dict(business)}
    for key, table in (("professionals", "professionals"), ("services", "services"),
                       ("links", "professional_services"), ("hours", "working_hours"),
                       ("breaks", "working_breaks"), ("products", "commercial_products"),
                       ("variants", "commercial_variants")):
        facts[key] = [dict(r) for r in (await db.execute(text(
            f"SELECT * FROM {table} WHERE tenant_id=:t ORDER BY id"), {"t": tenant})).mappings().all()]
    return facts


async def original_details(db, tenant):
    meta = (await db.execute(text("SELECT * FROM business_onboarding WHERE tenant_id=:t"),
                            {"t": tenant})).mappings().first()
    profile = (await db.execute(text("""SELECT s.generation,s.draft_version_id,s.published_version_id,
        d.profile AS draft,p.profile AS published FROM business_profile_state s
        LEFT JOIN business_profile_versions d ON d.tenant_id=s.tenant_id AND d.id=s.draft_version_id
        LEFT JOIN business_profile_versions p ON p.tenant_id=s.tenant_id AND p.id=s.published_version_id
        WHERE s.tenant_id=:t"""), {"t": tenant})).mappings().first()
    site = (await db.execute(text("SELECT public_enabled FROM management_site_preferences WHERE tenant_id=:t"),
                            {"t": tenant})).scalar_one_or_none()
    google = (await db.execute(text("SELECT status FROM google_connections WHERE tenant_id=:t"),
                              {"t": tenant})).scalar_one_or_none()
    whatsapp = (await db.execute(text("SELECT bool_or(status='connected') FROM whatsapp_channels WHERE tenant_id=:t"),
                                {"t": tenant})).scalar_one_or_none()
    operations = [dict(r) for r in (await db.execute(text("""SELECT idempotency_key AS key,step,version,result
        FROM business_onboarding_operations WHERE tenant_id=:t
        ORDER BY created_at DESC,idempotency_key DESC LIMIT 20"""), {"t": tenant})).mappings().all()]
    return {"meta": dict(meta) if meta else None,
            "channels": dict(profile) if profile else {"generation": 0, "draft": None, "published": None},
            "site": site, "google": google, "whatsapp": whatsapp, "operations": operations}


def same_native_tree(actual, expected):
    assert type(actual) is type(expected)
    if isinstance(expected, dict):
        assert list(actual) == list(expected)
        for key in expected:
            same_native_tree(actual[key], expected[key])
    elif isinstance(expected, list):
        assert len(actual) == len(expected)
        for a, b in zip(actual, expected):
            same_native_tree(a, b)
    else:
        assert actual == expected


def old_digest(value):
    data = json.dumps(jsonable_encoder(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(data).hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["empty_legacy", "configured", "profile_and_history"])
async def test_batched_reads_preserve_native_values_order_hashes_and_entire_http_body(onboarding_db, monkeypatch, kind):
    ctx = onboarding_db
    tenant = ctx.a
    if kind != "empty_legacy":
        state = await configured(ctx, activity="both")
        tenant = UUID(state["tenant_id"])
        async with ctx.migration.begin() as db:
            await db.execute(text("UPDATE professionals SET commission_percent=:value WHERE tenant_id=:t"),
                {"t": tenant, "value": Decimal("12.50")})
            await db.execute(text("UPDATE commercial_products SET created_at=:at WHERE tenant_id=:t"),
                {"t": tenant, "at": datetime(2030, 1, 2, 3, 4, 5, 100000, tzinfo=timezone.utc)})
            await db.execute(text("UPDATE working_hours SET start_time='09:00:00.100000' WHERE tenant_id=:t"), {"t": tenant})
            await db.execute(text("UPDATE commercial_variants SET attributes=CAST(:v AS jsonb) WHERE tenant_id=:t"),
                {"t": tenant, "v": json.dumps({"size": "250 ml", "nested": {"unicode": "ação", "empty": [], "null": None}})})
        if kind == "profile_and_history":
            state = await service.get_state(ctx.actor, tenant)
            state = await save(ctx, state, "channels", {"assistant_name": "Assistente sintética",
                "style_rules": {"style": "direct"}, "capabilities": ["catalog_answers"], "expected_profile_generation": 0})
            for index in range(23):
                state = await save(ctx, state, "location", {"modality": "online"}, key=f"batched-history-{index:02d}")
            async with ctx.migration.begin() as db:
                # A tie makes the second ORDER BY key essential at the LIMIT boundary.
                await db.execute(text("UPDATE business_onboarding_operations SET created_at='2031-01-01T00:00:00Z' WHERE tenant_id=:t"), {"t": tenant})
    async with ctx.runtime() as db:
        async with tenant_transaction(db, tenant):
            expected_facts = await original_facts(db, tenant)
            expected_details = await original_details(db, tenant)
            actual_facts = await service._facts(db, tenant)
            actual_details = await service._state_details(db, tenant)
            same_native_tree(actual_facts, expected_facts)
            same_native_tree(actual_details, expected_details)
            assert old_digest(actual_facts) == old_digest(expected_facts) == service.digest(actual_facts)
            if kind != "empty_legacy":
                assert type(actual_facts["professionals"][0]["id"]) is DriverUUID
                assert actual_facts["professionals"][0]["commission_percent"] == Decimal("12.50")
                assert actual_facts["products"][0]["created_at"].microsecond == 100000
                assert actual_facts["hours"][0]["start_time"].microsecond == 100000
                assert actual_facts["variants"][0]["attributes"]["nested"]["unicode"] == "ação"
            if kind == "profile_and_history":
                assert len(actual_details["operations"]) == 20
                assert [v["key"] for v in actual_details["operations"]] == sorted(
                    [v["key"] for v in actual_details["operations"]], reverse=True)
            access = TenantAccess("owner", ctx.actor.account_id)
            with monkeypatch.context() as old:
                old.setattr(service, "_facts", original_facts)
                old.setattr(service, "_state_details", original_details)
                old.setattr(service, "digest", old_digest)
                old.setattr(service, "_prepare_state_digests", lambda *_: None)
                old.setattr(service, "_state_jsonable", jsonable_encoder)
                expected_state = await service._state(db, tenant, access)
            class CountedSession:
                calls = 0
                async def execute(self, *args, **kwargs):
                    self.calls += 1
                    return await db.execute(*args, **kwargs)
            counted = CountedSession()
            actual_state = await service._state(counted, tenant, access)
            assert counted.calls == 2
            assert JSONResponse(actual_state).body == JSONResponse(expected_state).body
            assert actual_state["snapshot"] == expected_state["snapshot"]
            for key in ("professionals", "services", "products", "hours"):
                assert [r["fingerprint"] for r in actual_state["catalog"][key]] == [r["fingerprint"] for r in expected_state["catalog"][key]]


@pytest.mark.asyncio
async def test_batched_subqueries_remain_scoped_under_rls_and_cross_tenant_reads_do_not_leak(onboarding_db):
    ctx = onboarding_db
    state = await configured(ctx, activity="both")
    tenant = UUID(state["tenant_id"])
    async with ctx.runtime() as db:
        async with tenant_transaction(db, ctx.a):
            for read in (original_facts, service._facts):
                with pytest.raises(HTTPException) as denied:
                    await read(db, tenant)
                assert denied.value.status_code == 404
            same_native_tree(await service._state_details(db, tenant), await original_details(db, tenant))
            details = await service._state_details(db, tenant)
            assert details["meta"] is None and details["operations"] == []
            assert details["channels"] == {"generation": 0, "draft": None, "published": None}
        async with tenant_transaction(db, tenant):
            for read in (original_facts, service._facts):
                with pytest.raises(HTTPException) as missing:
                    await read(db, uuid4())
                assert missing.value.status_code == 404
    with pytest.raises(HTTPException) as denied:
        await service.get_state(ctx.actor, ctx.b)
    assert denied.value.status_code == 403
