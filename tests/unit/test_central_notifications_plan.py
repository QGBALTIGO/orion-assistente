"""An Essencial-or-higher active entitlement is mandatory for central notices."""
from uuid import uuid4

import pytest

from app import appointment_notifications as notifications, plan_permissions


class ForbiddenQueries:
    async def execute(self, *args, **kwargs):
        raise AssertionError("No notification SQL should run for a tenant without entitlement")


@pytest.mark.asyncio
async def test_central_sender_cannot_queue_when_plan_has_no_notifications(monkeypatch):
    for key in ("APPOINTMENT_COMMUNICATIONS_ENABLED",
                "ORION_CENTRAL_APPOINTMENT_WHATSAPP_ENABLED",
                "ORION_CENTRAL_NOTIFICATIONS_ENABLED"):
        monkeypatch.setenv(key, "1")
    tenant, appointment = uuid4(), uuid4()

    async def denied(_db, tenant_id, feature):
        assert tenant_id == tenant
        assert feature == "notifications.basic"
        return False

    monkeypatch.setattr(plan_permissions, "has_feature", denied)
    assert notifications.central_enabled()
    await notifications.enqueue_event(
        ForbiddenQueries(), tenant, appointment, "confirmed", {"requested_by": "customer"}
    )


@pytest.mark.asyncio
async def test_central_sender_cannot_send_after_entitlement_revoked(monkeypatch):
    for key in ("APPOINTMENT_COMMUNICATIONS_ENABLED",
                "ORION_CENTRAL_APPOINTMENT_WHATSAPP_ENABLED",
                "ORION_CENTRAL_NOTIFICATIONS_ENABLED"):
        monkeypatch.setenv(key, "1")
    tenant, admin, channel = uuid4(), uuid4(), uuid4()
    monkeypatch.setattr(notifications, "central_channel", lambda: (admin, channel))

    async def denied(_db, tenant_id, feature):
        assert tenant_id == tenant
        assert feature == "notifications.basic"
        return False

    monkeypatch.setattr(plan_permissions, "has_feature", denied)
    reason, row, phone, settings = await notifications.eligibility(
        ForbiddenQueries(), tenant, {"central_channel_id": channel}
    )
    assert reason == "plan_feature_unavailable"
    assert row is phone is settings is None
