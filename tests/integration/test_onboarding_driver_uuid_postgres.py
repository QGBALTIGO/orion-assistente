"""Prove SQL text returns the exact driver UUID, with real RLS and factories."""
import hashlib
import json
from uuid import UUID

from asyncpg.pgproto.pgproto import UUID as DriverUUID
from fastapi.encoders import ENCODERS_BY_TYPE, jsonable_encoder
from fastapi.responses import JSONResponse
import pytest
from sqlalchemy import text

from app import onboarding_service as service
from app.database import tenant_transaction
from app.tenant_access import TenantAccess
from test_onboarding_postgres import onboarding_db, configured  # noqa: F401

pytestmark = pytest.mark.integration


def old_digest(value):
    raw = json.dumps(jsonable_encoder(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


@pytest.mark.asyncio
async def test_real_text_driver_uuid_and_full_state_use_native_path_without_changing_historical_contract(onboarding_db, monkeypatch):
    ctx = onboarding_db
    state = await configured(ctx, activity="both")
    tenant = UUID(state["tenant_id"])
    async with ctx.runtime() as db:
        async with tenant_transaction(db, tenant):
            value = (await db.execute(text("SELECT CAST(:id AS uuid)"), {"id": str(tenant)})).scalar_one()
            assert type(value) is DriverUUID
            assert jsonable_encoder(value) == str(value)
            assert service._digest_needs_legacy({"id": value}) is False
            facts = await service._facts(db, tenant)
            assert type(facts["business"]["id"]) is DriverUUID
            assert type(facts["professionals"][0]["id"]) is DriverUUID
            assert service._digest_needs_legacy(facts) is False
            access = TenantAccess("owner", ctx.actor.account_id)
            with monkeypatch.context() as isolated:
                isolated.setattr(service, "digest", old_digest)
                isolated.setattr(service, "_prepare_state_digests", lambda *_: None)
                isolated.setattr(service, "_state_jsonable", jsonable_encoder)
                expected = await service._state(db, tenant, access)
            with monkeypatch.context() as isolated:
                isolated.setattr(service, "jsonable_encoder", lambda value: pytest.fail("Real driver facts fell back"))
                actual = await service._state(db, tenant, access)
            assert actual == expected
            assert JSONResponse(actual).body == JSONResponse(expected).body
            assert actual["snapshot"] == expected["snapshot"]
            for key in ("professionals", "services", "products", "hours"):
                assert [row["fingerprint"] for row in actual["catalog"][key]] == [row["fingerprint"] for row in expected["catalog"][key]]
    assert DriverUUID not in ENCODERS_BY_TYPE
