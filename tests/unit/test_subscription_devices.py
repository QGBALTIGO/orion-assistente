"""Device DTO, workspace and private/public contract validation."""
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError
import pytest

from app import plan_permissions, plan_routing, subscription_devices as devices
from app.subscription_device_schemas import (ControlFinish, DeviceEvent, DeviceSettings,
    EffectFinish, UpdateSettings, destination)


@pytest.mark.parametrize("url", ["http://127.0.0.1", "http://[::1]", "http://169.254.169.254",
    "http://10.0.0.1", "http://localhost", "http://api.railway.internal", "file:///tmp/x",
    "https://user:secret@example.com", "https://example.com/#x", "https://example.com:99999",
    "https://example.com\n/private", "http://metadata", "http://[::ffff:127.0.0.1]"])
def test_urls_never_accept_private_addresses_credentials_or_non_http(url):
    with pytest.raises(ValueError):
        destination(url)


def test_proxy_has_separate_credentials_and_fixed_http_connect_scheme():
    assert destination("http://proxy.example.com:8080", proxy=True) == "http://proxy.example.com:8080"
    for url in ("https://proxy.example.com", "http://proxy.example.com/path", "http://proxy.example.com?x=1"):
        with pytest.raises(ValueError):
            destination(url, proxy=True)
    settings = DeviceSettings(proxy={"enabled": True, "url": "http://proxy.example.com:8080",
        "username": "synthetic", "password": "synthetic-password"})
    assert "password" not in settings.model_dump()["proxy"]
    assert "synthetic-password" not in json.dumps(settings.model_dump())
    assert settings.proxy.password == "synthetic-password"


def test_no_public_roundtrip_can_return_or_overwrite_encrypted_proxy_secret_implicitly():
    settings = DeviceSettings().model_dump()
    row = {"settings": settings, "proxy_secret_encrypted": "encrypted-fixture"}
    public = devices.public_settings(row)
    assert public["proxy"]["password_configured"] is True
    assert "encrypted-fixture" not in json.dumps(public)
    assert "password" not in public["proxy"]
    assert "password_configured" not in settings["proxy"]
    public["proxy"].pop("password_configured")
    value = UpdateSettings(expected_revision=1, settings=public)
    assert value.settings.proxy.password is None and not value.settings.proxy.clear_password
    with pytest.raises(ValidationError):
        DeviceSettings(proxy={"password": "new", "username": "owner", "clear_password": True})


def test_hours_require_seven_unique_days_and_valid_active_intervals_but_allow_clear():
    defaults = DeviceSettings().model_dump()
    defaults["business_hours"]["days"][2].update(enabled=False, start=None, end=None)
    assert DeviceSettings(**defaults).business_hours.days[2].start is None
    defaults["business_hours"]["days"][2]["enabled"] = True
    with pytest.raises(ValidationError):
        DeviceSettings(**defaults)
    for changes in ({"start": "18:00", "end": "09:00"},
                    {"break_start": "12:00", "break_end": None},
                    {"break_start": "08:00", "break_end": "10:00"}):
        values = DeviceSettings().model_dump()
        values["calls"]["days"][0].update(enabled=True, **changes)
        with pytest.raises(ValidationError):
            DeviceSettings(**values)
    values = DeviceSettings().model_dump()
    values["calls"]["days"][1]["weekday"] = 0
    with pytest.raises(ValidationError):
        DeviceSettings(**values)


@pytest.mark.parametrize("template", ["{unknown}", "{transcript.__class__}", "{transcript!r}", "{transcript:>20}",
    "{transcript}{transcript}", "{footer}", "{transcript"])
def test_transcription_templates_cannot_expand_arbitrary_fields_or_omit_content(template):
    with pytest.raises(ValidationError):
        DeviceSettings(transcription={"template": template})


@pytest.mark.parametrize("url", ["https://evilwhatsapp.net/a.png", "https://whatsapp.net.evil.example/a.png",
    "https://user:secret@whatsapp.net/a.png", "https://whatsapp.net:443/a.png", "https://whatsapp.net/a.png#x"])
def test_runtime_avatar_requires_exact_provider_host_boundary(url):
    assert devices._safe_runtime({"profile": {"avatar_url": url}})["profile"]["avatar_url"] is None


def test_runtime_read_sanitizes_metadata_and_does_not_fabricate_phone_verification():
    result = devices._safe_runtime({"status": "qr_ready", "phone": "5567999999901",
        "profile": {"name": "Pessoa", "avatar_url": "https://pps.whatsapp.net/a.png"},
        "qr": "private-code", "token": "private-key"})
    assert result["status"] == "qr_ready" and result["phone"] is None and result["phone_verified"] is False
    assert result["profile"]["avatar_url"] == "https://pps.whatsapp.net/a.png"
    assert "qr" not in result and "token" not in result


def test_device_permission_is_not_inherited_by_paid_other_niches_or_inactive_company():
    from app.plan_catalog import PLAN_FEATURES
    now = datetime.now(timezone.utc)
    for niche in ("barbearia", "outro", None):
        state = plan_permissions.derive_plan({"tenant_status": "active", "niche": niche,
            "manual": {"plan_code": "elite", "started_at": now}})
        assert not state["permissions"]["devices.manage"]
    for features in PLAN_FEATURES.values():
        assert "devices.manage" not in features
    subscription = plan_permissions.derive_plan({"tenant_status": "active", "niche": "servico_assinaturas"})
    assert subscription["permissions"]["devices.manage"] and subscription["code"] is None
    inactive = plan_permissions.derive_plan({"tenant_status": "paused", "niche": "servico_assinaturas"})
    assert not inactive["permissions"]["devices.manage"]


def test_new_route_isolated_without_opening_legacy_qr_and_matches_schema_fixture():
    relative = "/subscription-whatsapp/devices/{device_id}/settings"
    assert plan_routing.feature_for_path(relative, "PUT") == "devices.manage"
    assert plan_routing.subscription_workspace_route(relative, "PUT", {})
    assert not plan_routing.subscription_workspace_route("/qr-channels", "POST", {})
    assert plan_routing.feature_for_path("/qr-channels") == "conversations.manual"
    contract = json.loads((Path(__file__).parents[1] / "fixtures/subscription_devices_contract.json").read_text())
    defaults = contract["settings_defaults"]
    defaults["proxy"].pop("password_configured")
    assert DeviceSettings(**defaults).model_dump(mode="json") == DeviceSettings().model_dump(mode="json")


def test_private_node_scope_is_strict_and_call_does_not_invent_hidden_phone():
    scope = {"device_id": uuid4(), "generation": uuid4(), "settings_revision": 4}
    assert ControlFinish(operation_id=uuid4(), **scope, status="succeeded").status == "succeeded"
    assert EffectFinish(operation_id=uuid4(), **scope, state="accepted", reconcile=True).reconcile
    event = DeviceEvent(**scope, event_id="call:001", kind="call", occurred_at=datetime.now(timezone.utc),
        call_id="C1", call_from="123456789012345@lid")
    assert event.phone is None
    for kind in ("text", "audio", "outbound"):
        with pytest.raises(ValidationError):
            DeviceEvent(**scope, event_id="msg:001", kind=kind, occurred_at=datetime.now(timezone.utc))


def test_audio_capability_requires_provider_decoder_and_explicit_positive_budgets(monkeypatch):
    from app import subscription_device_audio
    monkeypatch.setattr(subscription_device_audio, "audio_available", lambda: True)
    monkeypatch.delenv("SUBSCRIPTION_DEVICE_ASR_DAILY_SECONDS", raising=False)
    monkeypatch.delenv("SUBSCRIPTION_DEVICE_ASR_GLOBAL_DAILY_SECONDS", raising=False)
    assert not devices.audio_capable()
    monkeypatch.setenv("SUBSCRIPTION_DEVICE_ASR_DAILY_SECONDS", "21600")
    monkeypatch.setenv("SUBSCRIPTION_DEVICE_ASR_GLOBAL_DAILY_SECONDS", "86400")
    assert devices.audio_capable()
    assert devices.settings_capabilities() == {"manage": True, "transcription": True,
        "transcription_max_minutes": 30, "proxy": True, "webhook": True}
