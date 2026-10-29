"""Explicit entitlement storage double for existing synthetic domain fixtures.

Not autouse: tests opt in to the plan their synthetic company owns. The real
feature matrix, resolver and denial predicates remain in use. PostgreSQL tests
and plan-runtime enforcement tests do not use this storage double.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace


def install_plan_fixture(monkeypatch, code="elite"):
    from app import plan_permissions as permissions, plan_routing
    state = SimpleNamespace(code=code, overrides={}, counts={}, locks=[])
    async def lock(db, tenant_id, exclusive=False):
        state.locks.append((tenant_id,exclusive))
    async def snapshot(db,tenant_id,lock="shared"):
        now=datetime.now(timezone.utc)
        selected=state.overrides.get(tenant_id,state.code)
        return permissions.derive_plan({"tenant_id":str(tenant_id),"tenant_status":"active",
            "active_professionals":state.counts.get(tenant_id,0),
            "manual":{"plan_code":selected,"started_at":now-timedelta(days=1),"expires_at":None}
                if selected else None},now=now)
    async def require(tenant_id,feature):
        return await permissions.require_feature(None,tenant_id,feature)
    async def has(tenant_id,feature):
        return await permissions.has_feature(None,tenant_id,feature)
    monkeypatch.setattr(permissions,"plan_state",snapshot)
    monkeypatch.setattr(permissions,"lock_plan",lock)
    monkeypatch.setattr(permissions,"require_tenant_feature",require)
    monkeypatch.setattr(plan_routing,"require_tenant_feature",require)
    monkeypatch.setattr(permissions,"tenant_has_feature",has)
    return state
