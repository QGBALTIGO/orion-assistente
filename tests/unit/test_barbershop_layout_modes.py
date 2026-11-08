"""Only barbershops may see the optional application navigation shell."""
from app.plan_permissions import derive_plan


def snapshot(niche, *, platform=False):
    return {
        "tenant_id": "10000000-0000-4000-8000-000000000001",
        "tenant_status": "active",
        "niche": niche,
        "platform_workspace": platform,
        "active_professionals": 1,
    }


def test_barbershop_offers_classic_and_app_without_changing_entitlements():
    result = derive_plan(snapshot("barbearia"))
    assert result["layout_modes"] == ["classic", "app"]
    assert result["workspace_profile"] == "standard"
    assert result["status"] == "no_access"


def test_other_businesses_and_unknown_niches_keep_classic_only():
    for niche in ("odontologia", "clinica", "salao", None, "", "legacy"):
        result = derive_plan(snapshot(niche))
        assert result["layout_modes"] == ["classic"]


def test_platform_admin_must_not_acquire_barbershop_layout():
    assert derive_plan(snapshot("barbearia", platform=True))["layout_modes"] == ["classic"]


def test_layout_eligibility_is_reflected_in_plan_revision():
    assert derive_plan(snapshot("barbearia"))["revision"] != derive_plan(snapshot("clinica"))["revision"]


def test_subscription_services_receives_app_layout_without_gaining_extra_permissions():
    result = derive_plan(snapshot("servico_assinaturas"))
    assert result["layout_modes"] == ["classic", "app"]
    assert result["workspace_profile"] == "subscription_services"
    assert result["permissions"].get("finance.basic") is False
    assert result["permissions"].get("chatbot.manage") is True
    assert result["permissions"].get("templates.manage") is True
    assert result["permissions"].get("direct_messages.manage") is True
    assert result["permissions"].get("diagnostics.manage") is True


def test_platform_admin_in_subscription_niche_still_uses_classic_only():
    assert derive_plan(snapshot("servico_assinaturas", platform=True))["layout_modes"] == ["classic"]
