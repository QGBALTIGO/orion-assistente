"""Complete the private questionnaire with the real, unlicensed plan decision."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
import pytest

from app import onboarding_schemas as schemas, onboarding_service as service
from app import plan_permissions
from app.catalog_api import Actor
from app.tenant_access import TenantAccess


class Result:
    def __init__(self, rows=()):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self.rows)


class CompletionDB:
    def __init__(self, meta):
        self.meta = meta
        self.status = "paused"
        self.updates = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "onboarding_activate_quick_tenant" in sql:
            self.status = "active"
        elif sql.startswith("UPDATE "):
            self.updates.append((sql, params))
        elif "SELECT id,active FROM professionals" in sql:
            return Result([{"id": identity, "active": False} for identity in params["ids"]])
        return Result()


@pytest.fixture
def completion_case(monkeypatch):
    tenant, account = uuid4(), uuid4()
    meta = {"is_new": True, "version": 4, "completed_at": None, "managed_ids": {},
            "current_step": "review", "configuration": {
                "niche": "servico_assinaturas", "activity": "products", "subscription_offering": "both",
                "modality": "online", "business_metrics": {
                    "operating_age": "1_to_3_years", "team_size": "1_2",
                    "monthly_revenue": "prefer_not_to_say", "monthly_volume": "up_to_50"},
                "business_priorities": {"main_challenge": "whatsapp",
                    "desired_outcome": "automate_whatsapp", "respondent_role": "owner"}}}
    db = CompletionDB(meta)
    case = SimpleNamespace(tenant=tenant, actor=Actor("member", tenant, account, "owner"),
                           db=db, plan={})

    @asynccontextmanager
    async def access(actor, scope, *, write=False):
        assert actor == case.actor and scope == tenant and write is True
        yield TenantAccess("owner", account)

    @asynccontextmanager
    async def transaction(session, scope):
        assert session is db and scope == tenant
        saved = deepcopy((db.meta, db.status, db.updates))
        try:
            yield db
        except Exception:
            db.meta, db.status, db.updates = saved
            raise

    async def facts(session, scope):
        assert session is db and scope == tenant
        return {"business": {"id": tenant, "name": "Empresa sintética", "status": db.status},
                **{key: [] for key in ("professionals", "services", "products", "variants",
                                      "hours", "breaks", "links")}}

    async def lock_meta(*_args):
        return db.meta

    async def no_replay(*_args):
        return None

    async def persist(*_args):
        db.meta["version"] += 1

    async def read_state(*_args):
        return {"status": "complete" if db.meta["completed_at"] else "incomplete",
                "version": db.meta["version"]}

    async def read_plan(session, scope, lock="shared"):
        assert session is db and scope == tenant
        return plan_permissions.derive_plan({"tenant_id": str(tenant),
            "tenant_status": db.status, "active_professionals": 0, **case.plan})

    async def lock_plan(session, scope, exclusive=False):
        assert session is db and scope == tenant and exclusive is True

    monkeypatch.setenv("ONBOARDING_ENABLED", "1")
    monkeypatch.setattr(service, "access_session", access)
    monkeypatch.setattr(service, "get_session_factory", lambda: lambda: db)
    monkeypatch.setattr(service, "tenant_transaction", transaction)
    monkeypatch.setattr(service, "_lock_meta", lock_meta)
    monkeypatch.setattr(service, "_replay", no_replay)
    monkeypatch.setattr(service, "_facts", facts)
    monkeypatch.setattr(service, "_persist", persist)
    monkeypatch.setattr(service, "_state", read_state)
    # Storage is synthetic; derive_plan and require_feature remain unchanged.
    monkeypatch.setattr(plan_permissions, "plan_state", read_plan)
    monkeypatch.setattr(plan_permissions, "lock_plan", lock_plan)

    async def finish():
        payload = schemas.Complete(expected_version=db.meta["version"],
            expected_snapshot=service._snapshot(db.meta["configuration"], db.meta["managed_ids"],
                                                await facts(db, tenant)),
            idempotency_key=str(uuid4()), activate_catalog=True, acknowledged=True)
        return await service.complete(case.actor, tenant, payload)

    case.finish = finish
    return case


@pytest.mark.asyncio
@pytest.mark.parametrize("grant", ["none", "expired_manual", "expired_trial"])
async def test_private_profile_completes_without_an_operational_plan(completion_case, grant):
    case = completion_case
    now = datetime.now(timezone.utc)
    if grant == "expired_manual":
        case.plan["manual"] = {"plan_code": "essencial", "started_at": now-timedelta(days=2),
                               "expires_at": now-timedelta(days=1)}
    elif grant == "expired_trial":
        case.plan["trial"] = {"status": "active", "started_at": now-timedelta(days=4),
                              "ends_at": now-timedelta(days=1)}
    done = await case.finish()
    assert done == {"status": "complete", "version": 5}
    assert case.db.status == "active" and case.db.updates == []
    for feature in ("professionals.manage", "agenda.manage", "commerce.admin", "reception.ai"):
        with pytest.raises(HTTPException) as blocked:
            await plan_permissions.require_feature(case.db, case.tenant, feature)
        assert blocked.value.status_code == 403
        assert blocked.value.detail["code"] == "PLAN_FEATURE_UNAVAILABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize("table", ["professionals", "services", "commercial_products", "commercial_variants"])
@pytest.mark.parametrize("requested", [False, True])
async def test_profile_answers_never_authorize_staged_catalog_writes(completion_case, table, requested):
    case = completion_case
    case.db.meta["managed_ids"] = {table: {str(uuid4()): requested}}
    saved = deepcopy(case.db.meta)
    with pytest.raises(HTTPException) as blocked:
        await case.finish()
    assert blocked.value.status_code == 403
    assert blocked.value.detail["code"] == "PLAN_FEATURE_UNAVAILABLE"
    assert blocked.value.detail["feature"] == "professionals.manage"
    assert case.db.meta == saved and case.db.status == "paused" and case.db.updates == []
