"""Stage 18: explicit owner authorization, phone format and result validation.
All numbers and sessions in these tests are synthetic.
"""
from uuid import UUID

import pytest
from pydantic import ValidationError

from fastapi import HTTPException
from redis.exceptions import RedisError

from app import subscription_number_check as api
from app import subscription_number_check_schemas as schemas
from app import plan_routing, plan_catalog, workspace_policy

DEVICE = UUID("00000000-0000-4000-8000-000000000018")
GEN = UUID("00000000-0000-4000-8000-000000000019")
PHONE = "+5567999999901"


def sample(**changes):
    return {"device_id": str(DEVICE), "phones": [PHONE],
            "confirmed_authorization": True} | changes


def test_correct_authorized_phone():
    value = schemas.CheckRequest.model_validate(sample())
    assert value.phones == [PHONE] and value.confirmed_authorization is True


@pytest.mark.parametrize("phones", [
    [], [PHONE] * 2, [PHONE] * 6, ["5567999999901"], ["+01234567890"],
    ["+595992820210", "not-a-phone"], ["+5567999999901\n+595992820210"]
])
def test_invalid_and_duplicate_phone_lists_refused(phones):
    with pytest.raises(ValidationError):
        schemas.CheckRequest.model_validate(sample(phones=phones))


@pytest.mark.parametrize("consent", [False, "true", 1, None])
def test_no_implicit_authorization(consent):
    with pytest.raises(ValidationError):
        schemas.CheckRequest.model_validate(sample(confirmed_authorization=consent))


def test_unknown_fields_denied():
    with pytest.raises(ValidationError):
        schemas.CheckRequest.model_validate(sample(internal_permission="ignore"))


def test_verified_bridge_schema_requires_exact_scope_and_only_three_statuses():
    result = {
        "device_id": str(DEVICE), "generation": str(GEN),
        "settings_revision": 2, "items": [{"phone": PHONE, "status": "registered"}]
    }
    assert schemas.BridgeResult.model_validate(result).items[0].status == "registered"
    for bad in [
        result | {"items": [{"phone": PHONE, "status": "fake"}]},
        result | {"items": [{"phone": PHONE, "status": "registered"}] * 2},
        result | {"items": [{"phone": "5567999999901", "status": "registered"}]},
        result | {"device_id": "not-a-uuid"},
    ]:
        with pytest.raises(ValidationError):
            schemas.BridgeResult.model_validate(bad)


def test_feature_only_in_subscription_and_fail_closed_path():
    name = "number_check.manage"
    assert name in plan_catalog.ALL_FEATURES
    assert name in workspace_policy.SUBSCRIPTION_WORKSPACE_FEATURES
    assert name not in plan_catalog.BASE_FEATURES
    assert name not in plan_catalog.ELITE_FEATURES
    assert plan_routing.feature_for_path("/subscription-whatsapp/number-check/check", "POST") == name
    assert plan_routing.subscription_workspace_route("/subscription-whatsapp/number-check/check", "POST", {}) is True


class FakeQuota:
    def __init__(self):
        self.count = 0
        self.calls = []
        self.closed = False
    async def eval(self, script, keys, key, amount, maximum, ttl):
        assert "INCRBY" in script and keys == 1
        assert key.startswith("orion:subscription:number-check:v1:")
        assert maximum == 30 and ttl == 3_600_000
        self.calls.append((key, amount))
        self.count += amount
        return 1 if self.count <= maximum else 0
    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_durable_quota_reservation_shared_by_requests_and_closes_owned_client(monkeypatch):
    redis = FakeQuota()
    monkeypatch.setattr(api, "_request_redis", lambda request: (redis, True))
    for _ in range(6):
        await api.reserve_quota(None, DEVICE, GEN, 5)
    with pytest.raises(HTTPException) as error:
        await api.reserve_quota(None, DEVICE, GEN, 1)
    assert error.value.status_code == 429
    assert redis.count == 31 and len(redis.calls) == 7
    assert redis.closed is True
    assert len({row[0] for row in redis.calls}) == 1


@pytest.mark.asyncio
async def test_quota_backend_failure_fails_closed(monkeypatch):
    class Offline:
        async def eval(self, *args):
            raise RedisError("secret internals")
        async def aclose(self): pass
    monkeypatch.setattr(api, "_request_redis", lambda request: (Offline(), True))
    with pytest.raises(HTTPException) as error:
        await api.reserve_quota(None, DEVICE, GEN, 1)
    assert error.value.status_code == 503
    assert "secret" not in str(error.value.detail)
