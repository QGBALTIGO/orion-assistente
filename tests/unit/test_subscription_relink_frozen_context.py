"""A physical relink invalidates previously frozen outbound consent.

The SQL relink regression owns the revision transition. These tests exercise
the existing three admission guards, including work not materialized as an
effect yet, so cancelling current pending effects is not the only protection.
"""
from uuid import uuid4

from fastapi import HTTPException
import pytest

from app import subscription_campaign_runtime as campaigns
from app import subscription_schedule_runtime as schedules
from app import subscription_bulk_runtime as bulk


@pytest.mark.asyncio
@pytest.mark.parametrize("module,expected_code", [
    (campaigns, "CAMPAIGN_CONTEXT_CHANGED"),
    (schedules, "SCHEDULE_CONTEXT_CHANGED"),
    (bulk, "BULK_CONTEXT_CHANGED"),
])
async def test_frozen_future_work_refuses_relink_revision_even_when_logical_identity_is_preserved(
    monkeypatch, module, expected_code
):
    tenant, device_id, generation, channel_id, owner, identity = (uuid4() for _ in range(6))
    device = {"id": device_id, "generation": generation, "channel_id": channel_id,
              "owner_account_id": owner, "revision": 7, "status": "connected"}
    frozen = {"id": str(device_id), "generation": str(generation), "channel_id": str(channel_id),
              "owner_account_id": str(owner), "settings_revision": 7}
    consent = {"confirmed": True, "actor_account_id": str(owner)}
    observed = []

    async def current_device(db, actual_tenant, actual_id, *, actor_id=None, **kwargs):
        observed.append((actual_tenant, actual_id, actor_id))
        return dict(device), {"enabled": True}

    monkeypatch.setattr(module.store.shared, "check_device", current_device)
    if module is campaigns:
        campaign = {"tenant_id": tenant, "owner_account_id": owner, "template_id": None,
                    "snapshot": {"devices": [frozen], "consent": consent}}

        async def check():
            return await campaigns.check_frozen_context(object(), campaign)

    elif module is schedules:
        schedule = {"id": identity, "tenant_id": tenant, "owner_account_id": owner,
                    "device_id": device_id, "version": 3, "run_version": "frozen-run", "template_id": None}
        run = {"tenant_id": tenant, "schedule_id": identity, "schedule_version": 3,
               "run_version": "frozen-run", "snapshot": {"device": frozen, "consent": consent}}

        async def check():
            return await schedules.check_frozen_context(object(), schedule, run)

    else:
        batch = {"id": identity, "tenant_id": tenant, "owner_account_id": owner,
                 "run_version": "frozen-run", "snapshot": {"consent": consent}}
        recipient = {"tenant_id": tenant, "bulk_id": identity, "run_version": "frozen-run",
                     "device_id": device_id, "generation": generation, "settings_revision": 7,
                     "source": "manual", "manual_consent": True, "template_id": None,
                     "context": {"device": frozen}}

        async def check():
            return await bulk.check_frozen_context(object(), batch, recipient)

    assert await check() is None
    device["revision"] = 8  # Relink changes only the authority revision here.
    with pytest.raises(HTTPException) as failure:
        await check()
    assert failure.value.status_code == 409
    assert failure.value.detail["code"] == expected_code
    assert observed == [(tenant, device_id, owner)] * 2
    assert device["id"] == device_id and device["generation"] == generation
