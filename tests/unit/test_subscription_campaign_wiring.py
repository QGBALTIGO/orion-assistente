"""Campaign isolation and its safe projection through existing shared modules."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.plan_permissions import derive_plan
from app.plan_catalog import PLAN_FEATURES, RECOVERY_FEATURES
from app.plan_routing import feature_for_path, subscription_workspace_route
from app import subscription_sent_messages as history
from app.subscription_devices import campaign_effect_descriptor

NOW = datetime(2026, 11, 4, 22, tzinfo=timezone.utc)


@pytest.mark.parametrize("status,expected", [("active", True), ("paused", False), ("suspended", False), ("inactive", False)])
def test_campaign_capability_is_removed_with_subscription_tenant_access(status, expected):
    state = derive_plan({"tenant_id": uuid4(), "niche": "servico_assinaturas", "tenant_status": status}, NOW)
    assert state["permissions"]["campaigns.manage"] is expected
    assert state["permissions"]["marketing.campaigns"] is False
    assert state["permissions"]["conversations.manual"] is False


@pytest.mark.parametrize("niche", [None, "barbearia", "clinica", "odontologia", "outro"])
@pytest.mark.parametrize("plan", ["essencial", "profissional", "elite"])
def test_campaign_grant_does_not_expand_other_niches(niche, plan):
    state = derive_plan({"tenant_id": uuid4(), "niche": niche, "tenant_status": "active",
        "manual": {"plan_code": plan, "started_at": NOW-timedelta(days=1), "expires_at": None}}, NOW)
    assert state["permissions"]["campaigns.manage"] is False
    assert set(state["features"]) == PLAN_FEATURES[plan] | RECOVERY_FEATURES


@pytest.mark.parametrize("suffix", ["", "/options", "/groups", "/groups/{group_id}", "/groups/{group_id}/permissions",
    "/requests/{key}", "/{campaign_id}", "/{campaign_id}/review", "/{campaign_id}/confirm",
    "/{campaign_id}/actions", "/{campaign_id}/recipients"])
def test_campaign_read_and_write_routes_have_their_own_feature(suffix):
    route = "/subscription-whatsapp/campaigns"+suffix
    for method in ("GET", "POST", "PATCH", "DELETE"):
        assert feature_for_path(route, method) == "campaigns.manage"
        assert subscription_workspace_route(route, method, {})
    assert feature_for_path(route+"-similar") == "campaigns.manage" if suffix else feature_for_path(route+"-similar") is None


def campaign_effect(status="not_sent"):
    return {"id": uuid4(), "tenant_id": uuid4(), "device_id": uuid4(), "generation": uuid4(),
        "settings_revision": 1, "event_id": uuid4(), "type": "campaign_action", "purpose": "campaign_action",
        "payload": {"phone": "5567999999901", "action": {"type": "text", "text": "Mensagem revisada da campanha"}},
        "status": status, "error_code": "TRANSPORT_UNAVAILABLE_BEFORE_SEND", "provider_id": None,
        "attempts": 1, "authorized_at": NOW-timedelta(seconds=2), "expires_at": NOW+timedelta(minutes=2),
        "created_at": NOW-timedelta(seconds=3), "finished_at": NOW, "chatbot_revision": None, "result": {},
        "device_name": "WhatsApp sintético", "device_removed_at": None, "device_runtime": {},
        "incoming_payload": {"origin": "campaign", "campaign_id": str(uuid4())}, "incoming_kind": "campaign",
        "run_origin": None, "message_type": "text", "resend_block": None, "due_at": NOW}


@pytest.mark.parametrize("state", ["pending", "running", "accepted", "not_sent", "failed", "uncertain"])
def test_campaign_attempts_never_gain_a_generic_history_resend_action(state):
    row = campaign_effect(state)
    item = history.item_dto(row, NOW, detail=True)
    assert item["origin"] == {"code": "campaign", "label": "Campanha", "is_resend": False}
    assert item["actions"] == [] and item["resend"]["eligible"] is False
    assert "Campanhas" in item["resend"]["blocked_reason"]
    assert item["received"]["text"] is None
    assert item["sent"]["text"] == "Mensagem revisada da campanha"
    if state == "accepted":
        assert item["outcome"] == "accepted" and item["status"] == "sent"
    elif state == "uncertain":
        assert item["outcome"] == "uncertain" and item["status"] == "uncertain"


def test_pending_campaign_descriptor_contains_no_recipient_content_or_media_url():
    row = campaign_effect()
    value = campaign_effect_descriptor(row)
    assert value == {"operation_id": row["id"], "type": "campaign_action", "action_type": "text",
        "device_id": row["device_id"], "generation": row["generation"], "settings_revision": 1, "due_at": NOW}
    assert "phone" not in value and "payload" not in value and "chatbot_revision" not in value
