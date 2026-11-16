"""Odontology presentation is server-derived and never grants clinical rights."""
from app.plan_permissions import derive_plan


def snapshot(niche, *, platform=False):
    return {
        "tenant_id": "10000000-0000-4000-8000-000000000001",
        "tenant_status": "active",
        "niche": niche,
        "platform_workspace": platform,
        "active_professionals": 1,
    }


def test_only_verified_dentistry_niche_enables_the_dental_shell():
    dental = derive_plan(snapshot("odontologia"))
    assert dental["business_niche"] == "odontologia"
    assert dental["workspace_profile"] == "standard"
    assert dental["layout_modes"] == ["classic"]
    for other in ("barbearia", "clinica", "salao", "servico_assinaturas", None, ""):
        assert derive_plan(snapshot(other))["business_niche"] is None
    assert derive_plan(snapshot("odontologia", platform=True))["business_niche"] is None


def test_dental_label_does_not_increase_access_or_change_plan():
    dental = derive_plan(snapshot("odontologia"))
    clinic = derive_plan(snapshot("clinica"))
    assert dental["features"] == clinic["features"]
    assert dental["permissions"] == clinic["permissions"]
    assert dental["limits"] == clinic["limits"]
    assert dental["source"] == clinic["source"]
    assert dental["status"] == clinic["status"]
    assert dental["revision"] != clinic["revision"]
