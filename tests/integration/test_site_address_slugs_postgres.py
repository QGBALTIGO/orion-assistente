"""Address drafts, public snapshots and slug suggestions on isolated PostgreSQL."""
import asyncio
import json
import re
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.management_preferences import BusinessPreferences, AutomationPreferences, save_section
from app.site_slugs import normalize_site_slug
from test_public_catalog import public_db, save_site, publish

pytestmark = pytest.mark.integration


async def preference_section(d, section, value, expected):
    async with d["runtime"].begin() as db:
        await db.execute(text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(d["a"])})
        return await save_section(db, d["a"], section, value, expected)


async def stored_preferences(d):
    async with d["migrator"].connect() as db:
        return (await db.execute(text("SELECT preferences,version FROM management_preferences WHERE tenant_id=:tenant"),
                                 {"tenant": d["a"]})).mappings().one()


@pytest.mark.asyncio
async def test_structured_address_persists_and_legacy_put_does_not_erase_new_fields(public_db):
    d = public_db
    initial = await save_site(d, address="Praça da Sé", address_number="12-A", address_complement="Sala 2",
                              neighborhood="Sé", city="São Paulo", state="SP", postal_code="01001000")
    assert initial["address_number"] == "12-A" and initial["postal_code"] == "01001-000"
    stored = await stored_preferences(d)
    assert stored["version"] == 1  # A previously absent JSON settings row uses its existing DB default.
    assert stored["preferences"]["site_address"]["address_number"] == "12-A"
    # This old-client body knows none of the four new fields.
    legacy = await save_site(d, address="Praça da Sé", title="Título editado por cliente antigo")
    assert (legacy["address_number"], legacy["address_complement"], legacy["neighborhood"]) == ("12-A", "Sala 2", "Sé")
    no_number = await save_site(d, address="Praça da Sé", address_no_number=True)
    assert no_number["address_no_number"] is True and no_number["address_number"] is None
    numbered = await save_site(d, address="Praça da Sé", address_number="7B")
    assert numbered["address_no_number"] is False and numbered["address_number"] == "7B"
    cleared = await save_site(d, address="Praça da Sé", address_number=None, address_complement=None, neighborhood=None)
    assert cleared["address_number"] is None and cleared["address_complement"] is None and cleared["neighborhood"] is None
    stale = await d["client"].put(d["base"] + "/site", json={"expected_version": initial["version"], "slug": d["slug"], "address_number": "999"})
    assert stale.status_code == 409
    assert (await d["client"].get(d["base"] + "/site")).json()["address_number"] is None
    assert (await stored_preferences(d))["version"] == stored["version"]


@pytest.mark.asyncio
async def test_address_namespace_and_business_automation_writes_compose_concurrently(public_db):
    d = public_db
    # This composition scenario edits monthly goals and recovery settings in
    # addition to the Essencial site fields covered by the remaining tests.
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE tenant_plan_controls SET manual_plan_code='profissional',version=version+1 WHERE tenant_id=:tenant"),
                         {"tenant":d["a"]})
    business = BusinessPreferences(monthly_goal_cents=10000, legal_name="Empresa privada").model_dump()
    first = await preference_section(d, "business", business, 0)
    await save_site(d, address="Rua Primeira", address_number="10", neighborhood="Centro")
    assert (await stored_preferences(d))["version"] == first["version"]
    for index, (section, value) in enumerate((
        ("business", BusinessPreferences(monthly_goal_cents=25000, legal_name="Empresa privada atualizada").model_dump()),
        ("automations", AutomationPreferences(recovery_days=45).model_dump()),
    ), start=1):
        settings_version = (await stored_preferences(d))["version"]
        results = await asyncio.gather(
            save_site(d, address="Rua Primeira", address_number=str(10 + index)),
            preference_section(d, section, value, settings_version),
        )
        stored = await stored_preferences(d)
        assert stored["preferences"]["site_address"]["address_number"] == str(10 + index)
        assert stored["preferences"][section] == value
        assert stored["preferences"]["site_address"]["neighborhood"] == "Centro"
        assert stored["version"] == settings_version + 1
        assert results[0]["address_number"] == str(10 + index)
    assert (await stored_preferences(d))["preferences"]["business"]["monthly_goal_cents"] == 25000


@pytest.mark.asyncio
async def test_only_published_address_reaches_public_catalogue_metadata_and_restore(public_db):
    d = public_db
    await save_site(d, address="Rua Publicada", address_number="42", address_complement="Sala 1", neighborhood="Centro")
    await publish(d)
    public = (await d["client"].get(d["public"])).json()["site"]
    assert public["address"] == "Rua Publicada"
    assert public["address_display"] == "Rua Publicada, 42 — Sala 1 — Centro"
    await save_site(d, address="Rua Ainda Privada", address_number="99", address_complement="Fundos", neighborhood="Novo bairro")
    preview = (await d["client"].get(d["base"] + "/site/preview")).json()["site"]
    assert preview["address_display"] == "Rua Ainda Privada, 99 — Fundos — Novo bairro"
    assert (await d["client"].get(d["public"])).json()["site"] == public
    page = await d["client"].get("/s/" + d["slug"])
    assert page.status_code == 200 and "Ainda Privada" not in page.text and "Novo bairro" not in page.text
    structured = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', page.text).group(1))
    assert structured["address"]["streetAddress"] == public["address_display"]
    current = (await d["client"].get(d["base"] + "/site")).json()
    assert current["has_unpublished_changes"] is True
    disabled = await d["client"].post(d["base"] + "/site/publication", json={"expected_version": current["version"], "enabled": False})
    assert disabled.status_code == 200
    restored = await d["client"].post(d["base"] + "/site/publication", json={"expected_version": disabled.json()["version"], "enabled": True, "restore_previous": True})
    assert restored.status_code == 200 and restored.json()["has_unpublished_changes"] is True
    assert (await d["client"].get(d["public"])).json()["site"] == public
    published = await publish(d)
    assert published["has_unpublished_changes"] is False
    assert (await d["client"].get(d["public"])).json()["site"]["address_display"] == preview["address_display"]
    async with d["migrator"].connect() as db:
        snapshots = (await db.execute(text("SELECT snapshot FROM public_site_publications WHERE tenant_id=:tenant ORDER BY id"),
                                     {"tenant": d["a"]})).scalars().all()
        assert snapshots[0]["address_number"] == "42" and snapshots[-1]["address_number"] == "99"
        assert "business" not in snapshots[-1] and "site_address" not in snapshots[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize("empty_catalogue", [False, True])
async def test_old_public_snapshots_receive_address_defaults_without_false_pending_changes(public_db, monkeypatch, empty_catalogue):
    d = public_db
    await publish(d)
    async with d["migrator"].begin() as db:
        await db.execute(text("""UPDATE management_site_preferences SET public_snapshot=public_snapshot
            -'address_number'-'address_complement'-'neighborhood'-'address_no_number'
            WHERE tenant_id=:tenant"""), {"tenant": d["a"]})
        if empty_catalogue:
            await db.execute(text("""UPDATE management_site_preferences
                SET public_snapshot=jsonb_set(public_snapshot,'{_catalogue}','{}'::jsonb) WHERE tenant_id=:tenant"""), {"tenant": d["a"]})
    if empty_catalogue:
        async def no_visuals(*args, **kwargs):
            return {"services": [], "professionals": [], "opening_hours": []}
        monkeypatch.setattr("app.public_site_api.catalogue", no_visuals)
    current = (await d["client"].get(d["base"] + "/site")).json()
    assert current["has_unpublished_changes"] is False
    assert current["address_number"] is None and current["address_no_number"] is False


@pytest.mark.asyncio
async def test_slug_normalization_suggestions_skip_foreign_drafts_aliases_and_canonical_without_reserving(public_db):
    d = public_db
    raw = "São José Café " + uuid4().hex[:10]
    base = normalize_site_slug(raw)
    async with d["migrator"].begin() as db:
        for slug, kind in ((base, "canonical"), (base + "-2", "draft"), (base + "-3", "alias")):
            await db.execute(text("INSERT INTO public_site_slugs(slug,tenant_id,kind) VALUES(:slug,:tenant,:kind)"),
                             {"slug": slug, "tenant": d["b"], "kind": kind})
    response = await d["client"].get(d["base"] + "/site/slug-availability", params={"slug": raw})
    assert response.status_code == 200
    result = response.json()
    assert result["slug"] == base and result["available"] is False
    assert result["suggestions"] == [base + "-4", base + "-5", base + "-6"]
    assert str(d["b"]) not in response.text and "tenant" not in result
    for candidate in result["suggestions"]:
        check = await d["client"].get(d["base"] + "/site/slug-availability", params={"slug": candidate})
        assert check.json()["available"] is True and check.json()["suggestions"] == []
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM public_site_slugs WHERE tenant_id IN (:a,:b)"), {"a": d["a"], "b": d["b"]})).scalar_one() == 3
    saved = await save_site(d, slug="  " + result["suggestions"][0].upper() + "  ")
    assert saved["slug"] == base + "-4"
    own = await d["client"].get(d["base"] + "/site/slug-availability", params={"slug": saved["slug"]})
    assert own.json()["available"] is True
    numbered = await d["client"].get(d["base"] + "/site/slug-availability", params={"slug": base + "-2"})
    assert numbered.json()["suggestions"][0] == base + "-4"  # The current tenant may reuse its own draft.
    reserved = await d["client"].get(d["base"] + "/site/slug-availability", params={"slug": "ÁDMIN"})
    assert reserved.json()["slug"] == "admin" and reserved.json()["available"] is False
    assert len(reserved.json()["suggestions"]) == 3


@pytest.mark.asyncio
async def test_same_suggested_slug_can_only_be_reserved_once_even_after_concurrent_checks(public_db):
    d = public_db
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE tenant_memberships SET tenant_id=:other,role='owner' WHERE account_id=:account"),
                         {"other": d["b"], "account": d["viewer"]})
    raw = "Espaço Simultâneo " + uuid4().hex[:10]
    slug = normalize_site_slug(raw)
    requests = [(d["base"], "owner"), (f"/api/v1/tenants/{d['b']}/management", "viewer")]
    checks = await asyncio.gather(*(d["client"].get(base + "/site/slug-availability", params={"slug": raw}, headers={"x-test-role": role}) for base, role in requests))
    assert all(result.status_code == 200 and result.json()["available"] for result in checks)
    saved = await asyncio.gather(*(d["client"].put(base + "/site", headers={"x-test-role": role},
        json={"expected_version": 0, "title": "Empresa sintética", "slug": raw, "address": "Rua", "address_number": number})
        for number, (base, role) in zip(("10", "20"), requests)))
    assert sorted(response.status_code for response in saved) == [200, 409]
    winner = next(response.json() for response in saved if response.status_code == 200)
    assert winner["slug"] == slug
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM public_site_slugs WHERE slug=:slug"), {"slug": slug})).scalar_one() == 1
        assert (await db.execute(text("SELECT count(*) FROM management_preferences WHERE tenant_id IN (:a,:b)"), {"a": d["a"], "b": d["b"]})).scalar_one() == 1


@pytest.mark.asyncio
async def test_address_and_slug_checks_keep_tenant_and_read_only_boundaries(public_db):
    d = public_db
    await save_site(d, address="Rua Privada", address_number="15")
    denied = await d["client"].put(d["base"] + "/site", headers={"x-test-role": "viewer"},
        json={"expected_version": 1, "address_number": "999"})
    assert denied.status_code == 403
    other = f"/api/v1/tenants/{d['b']}/management"
    assert (await d["client"].get(other + "/site/slug-availability", params={"slug": d["slug"]})).status_code == 403
    assert (await d["client"].get(other + "/site")).status_code == 403
    async with d["runtime"].begin() as db:
        await db.execute(text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(d["b"])})
        assert (await db.execute(text("SELECT count(*) FROM management_preferences WHERE tenant_id=:tenant"), {"tenant": d["a"]})).scalar_one() == 0
    assert (await d["client"].get(d["base"] + "/site")).json()["address_number"] == "15"
