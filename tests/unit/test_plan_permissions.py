from datetime import datetime, timedelta, timezone
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi import HTTPException
from app.plan_catalog import PLAN_FEATURES, RECOVERY_FEATURES
from app.plan_permissions import derive_plan, bind_feature, reset_feature, plan_transaction_guard

NOW = datetime(2026, 10, 29, 22, tzinfo=timezone.utc)


def snapshot(code=None, **extra):
    return {"tenant_id": str(uuid4()), "tenant_status": "active", "version": 1,
        "active_professionals": 0, "manual": {"plan_code": code, "started_at": (NOW-timedelta(days=1)).isoformat(), "expires_at": None} if code else None,
        **extra}


def commercial(code="essencial", **extra):
    return {"code": "orion_"+code, "status": "active", "cakto_bound": True,
        "last_verified_payment_at": (NOW-timedelta(days=1)).isoformat(), **extra}


@pytest.mark.parametrize("code,limit", [("essencial", 2), ("profissional", 6), ("elite", 10)])
def test_exact_tier_matrix_and_overage_preserve_counts(code, limit):
    result=derive_plan(snapshot(code, active_professionals=12), NOW)
    assert set(result["features"]) == PLAN_FEATURES[code] | RECOVERY_FEATURES
    assert result["limits"] == {"max_professionals":limit,"active_professionals":12,"excess_professionals":12-limit}
    assert result["source"]=="manual"


def test_only_essencial_swaps_whatsapp_manual_for_performance_without_losing_notifications():
    # Covers manual, paid and trial plans using the same authoritative catalogue.
    essential = derive_plan(snapshot("essencial"), NOW)
    assert essential["permissions"]["performance.advanced"] is True
    assert essential["permissions"]["conversations.manual"] is False
    assert essential["permissions"]["notifications.basic"] is True
    assert essential["permissions"]["settings.manage"] is True
    assert essential["permissions"]["calendar.manage"] is True
    assert essential["permissions"]["goals.manage"] is False
    assert essential["permissions"]["commissions.advanced"] is False
    paid = derive_plan(snapshot(commercial=commercial()), NOW)
    trial = derive_plan(snapshot(trial={"status":"active",
        "started_at":(NOW-timedelta(hours=1)).isoformat(),
        "ends_at":(NOW+timedelta(days=1)).isoformat()}), NOW)
    for state in (paid, trial):
        assert state["code"] == "essencial"
        assert state["permissions"]["performance.advanced"]
        assert not state["permissions"]["conversations.manual"]
    for code in ("profissional", "elite"):
        state = derive_plan(snapshot(code), NOW)
        assert state["permissions"]["performance.advanced"]
        assert state["permissions"]["conversations.manual"]
        assert state["permissions"]["goals.manage"]
        assert state["permissions"]["commissions.advanced"]


def test_manual_priority_expiration_then_cakto_then_trial_then_no_access():
    data=snapshot("elite", commercial=commercial(), trial={"status":"active", "started_at":(NOW-timedelta(days=1)).isoformat(), "ends_at":(NOW+timedelta(days=1)).isoformat()})
    assert derive_plan(data,NOW)["code"]=="elite"
    data["manual"]["expires_at"]=NOW.isoformat()
    result=derive_plan(data,NOW)
    assert (result["source"],result["code"])==("cakto","essencial")
    assert result["manual_assignment"]["active"] is False
    data["commercial"]["status"]="canceled"
    assert derive_plan(data,NOW)["source"]=="trial"
    data["trial"]["ends_at"]=NOW.isoformat()
    result=derive_plan(data,NOW)
    assert result["source"]=="none"
    assert set(result["features"])==RECOVERY_FEATURES
    assert not result["permissions"]["settings.manage"]


@pytest.mark.parametrize("patch", [{"code":"invented"}, {"cakto_bound":False}, {"last_verified_payment_at":None},
    {"last_verified_payment_at":(NOW+timedelta(minutes=1)).isoformat()}, {"status":"trial"},
    {"status":"past_due","grace_until":NOW.isoformat()}, {"status":"suspended"}])
def test_unverified_or_invalid_commercial_never_grants_access(patch):
    assert derive_plan(snapshot(commercial=commercial(**patch)),NOW)["active"] is False


def test_past_due_only_with_live_grace_and_canonical_binding():
    data=snapshot(commercial=commercial(status="past_due",grace_until=(NOW+timedelta(seconds=1)).isoformat()))
    assert derive_plan(data,NOW)["source"]=="cakto"
    assert derive_plan(data,NOW+timedelta(seconds=1))["source"]=="none"


@pytest.mark.parametrize("status", ["paused","suspended","canceled"])
def test_inactive_tenant_does_not_gain_operating_rights(status):
    result=derive_plan(snapshot("elite",tenant_status=status),NOW)
    assert result["active"] is False and result["status"]=="tenant_inactive"
    assert set(result["features"])==RECOVERY_FEATURES


def test_platform_has_admin_features_without_commercial_tier_or_staff_cap():
    result=derive_plan(snapshot(platform_workspace=True),NOW)
    assert result["source"]=="platform_admin" and result["code"] is None
    assert result["permissions"]["commerce.admin"]
    assert result["limits"]["max_professionals"] is None


def test_revision_is_stable_but_changes_when_rights_expire_or_count_changes():
    data=snapshot("elite")
    assert derive_plan(data,NOW)["revision"]==derive_plan(deepcopy(data),NOW+timedelta(minutes=1))["revision"]
    first=derive_plan(data,NOW)["revision"]
    data["active_professionals"]=1
    assert derive_plan(data,NOW)["revision"]!=first
    data["manual"]["expires_at"]=(NOW+timedelta(seconds=1)).isoformat()
    assert derive_plan(data,NOW)["revision"]!=derive_plan(data,NOW+timedelta(seconds=1))["revision"]


def test_context_cannot_change_tenant_or_invent_feature():
    token=bind_feature(uuid4(),"agenda.manage")
    try:
        with pytest.raises(HTTPException): bind_feature(uuid4(),"agenda.manage")
    finally: reset_feature(token)
    with pytest.raises(ValueError): bind_feature(uuid4(),"invented")


@pytest.mark.asyncio
async def test_valid_plan_on_paused_business_explains_operational_state_without_selling_upgrade(monkeypatch):
    from app import plan_permissions as permissions
    state=derive_plan(snapshot('essencial',tenant_status='paused'),NOW)
    async def read(*args,**kwargs): return state
    monkeypatch.setattr(permissions,'plan_state',read)
    with pytest.raises(HTTPException) as error:
        await permissions.require_feature(object(),uuid4(),'agenda.manage')
    assert error.value.status_code==409
    assert error.value.detail['code']=='TENANT_INACTIVE'
    assert 'plano' not in error.value.detail['message'].lower()


@pytest.mark.asyncio
async def test_transaction_rechecks_after_work_and_rolls_back_if_expired(monkeypatch):
    from app import plan_permissions as permissions
    tenant=uuid4();values=[True,False];locks=[]
    async def state(db,who,lock=None):
        assert who==tenant
        data=snapshot('elite',tenant_id=str(tenant))
        if not values.pop(0): data['manual']['expires_at']=NOW.isoformat()
        return derive_plan(data,NOW)
    async def lock(db,who,exclusive=False): locks.append(exclusive)
    monkeypatch.setattr(permissions,"plan_state",state)
    monkeypatch.setattr(permissions,"lock_plan",lock)
    token=bind_feature(tenant,"reception.ai",exclusive=True)
    try:
        with pytest.raises(HTTPException) as error:
            async with plan_transaction_guard(object(),tenant): pass
        assert error.value.status_code==403 and locks==[True]
    finally: reset_feature(token)
