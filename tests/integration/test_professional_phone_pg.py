"""Real persistence, tenant boundaries and public privacy for professional phones."""
import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import onboarding_schemas, onboarding_service
from app.management_models import request_identity
from app.professional_commissions import ProfessionalConfiguration
from test_management import management_db, customer, appointment
from test_onboarding_postgres import onboarding_db, creation, new_business_context, save as save_onboarding
from test_public_catalog import public_db, publish

pytestmark = pytest.mark.integration


def configuration(d, **overrides):
    return {"name": "Profissional sintético", "service_ids": [str(d["service"])],
            "commission_mode": "percent", "commission_percent": 25,
            "expected_version": 1, **overrides}


def target(d, identity=None):
    return d["base"] + "/professionals/" + str(identity or d["pro"]) + "/configure"


@pytest.mark.asyncio
async def test_phone_is_saved_per_professional_and_follows_existing_booking_identity(management_db):
    d = management_db
    c = d["client"]
    legacy = (await c.get(f"/api/v1/tenants/{d['a']}/professionals")).json()
    assert next(p for p in legacy if p["id"] == str(d["pro"]))["phone_e164"] is None

    first = await c.put(target(d), json=configuration(d, phone_e164="(67) 99876-5432"))
    assert first.status_code == 200, first.text
    assert first.json()["phone_e164"] == "+5567998765432"
    assert first.json()["commission_config_version"] == 2
    payload = configuration(d, name="Outro profissional", phone_e164="+55 (11) 98765-4321",
                            idempotency_key=str(uuid4()))
    payload.pop("expected_version")
    created = await c.post(d["base"] + "/professionals/configure", json=payload)
    assert created.status_code == 201, created.text
    second_id = UUID(created.json()["id"])
    replay = await c.post(d["base"] + "/professionals/configure", json=payload)
    assert replay.status_code == 201 and replay.json()["id"] == str(second_id)
    assert (await c.post(d["base"] + "/professionals/configure",
                         json={**payload, "phone_e164": "(11) 98765-4322"})).status_code == 409

    person = await customer(d)
    visit_a = await appointment(d, person["id"])
    visit_b = await appointment(d, person["id"], professional_id=second_id)
    async with d["migrator"].connect() as db:
        contacts = dict((await db.execute(text("""
            SELECT a.id,p.phone_e164 FROM appointments a JOIN professionals p
              ON p.tenant_id=a.tenant_id AND p.id=a.professional_id
            WHERE a.tenant_id=:tenant AND a.id=ANY(CAST(:ids AS uuid[]))
        """), {"tenant": d["a"], "ids": [UUID(visit_a), UUID(visit_b)]})).all())
        assert contacts == {UUID(visit_a): "+5567998765432", UUID(visit_b): "+5511987654321"}
        # A contact field does not create WhatsApp operator privileges.
        assert (await db.execute(text("SELECT count(*) FROM authorized_staff WHERE tenant_id=:tenant"),
                                 {"tenant": d["a"]})).scalar_one() == 0
    listed = (await c.get(f"/api/v1/tenants/{d['a']}/professionals")).json()
    assert {p["id"]: p["phone_e164"] for p in listed} == {
        str(d["pro"]): "+5567998765432", str(second_id): "+5511987654321"}


@pytest.mark.asyncio
async def test_cached_configuration_omits_phone_without_erasing_it_and_explicit_empty_clears(management_db):
    d = management_db
    c = d["client"]
    original = await c.put(target(d), json=configuration(d, phone_e164="(67) 99876-5432"))
    assert original.status_code == 200, original.text
    # An extra national digit must not turn DDD 55 into a country prefix.
    invalid = await c.put(target(d), json=configuration(d, phone_e164="(55) 99123-45678", expected_version=2))
    assert invalid.status_code == 422
    changed = await c.put(target(d), json=configuration(d, name="Nome atualizado", expected_version=2))
    assert changed.status_code == 200, changed.text
    assert changed.json()["phone_e164"] == "+5567998765432"
    assert (await c.put(target(d), json=configuration(d, phone_e164="(11) 98888-7777",
                                                    expected_version=2))).status_code == 409
    cleared = await c.put(target(d), json=configuration(d, phone_e164="", expected_version=3))
    assert cleared.status_code == 200 and cleared.json()["phone_e164"] is None


@pytest.mark.asyncio
async def test_catalog_create_replace_omit_and_clear_use_the_same_phone_contract(management_db):
    d = management_db
    c = d["client"]
    base = f"/api/v1/tenants/{d['a']}/professionals"
    created = await c.post(base, json={"name": "Cadastro direto", "phone_e164": "(11) 3456-7890"})
    assert created.status_code == 201, created.text
    identity = created.json()["id"]
    assert created.json()["phone_e164"] == "+551134567890"
    renamed = await c.put(base + "/" + identity, json={"name": "Nome atualizado"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["phone_e164"] == "+551134567890"
    cleared = await c.put(base + "/" + identity, json={"name": "Nome atualizado", "phone_e164": None})
    assert cleared.status_code == 200 and cleared.json()["phone_e164"] is None
    malformed = await c.put(base + "/" + identity,
                            json={"name": "Nome atualizado", "phone_e164": "+1 202 555 0199"})
    assert malformed.status_code == 422


@pytest.mark.asyncio
async def test_pre_rollout_creation_receipt_replays_after_phone_field_is_added(management_db):
    d = management_db
    key = "legacy-professional-" + uuid4().hex
    payload = {"name": "Cadastro antigo", "idempotency_key": key}
    # This is the complete request fingerprint recorded by the old schema.
    old_payload = ProfessionalConfiguration(**payload).model_dump(exclude={"idempotency_key", "phone_e164"})
    async with d["migrator"].begin() as db:
        identity, replay = await request_identity(db, d["a"], "professional_configuration", key,
                                                  old_payload, entity_id=d["pro"])
        assert identity == d["pro"] and not replay
    retried = await d["client"].post(d["base"] + "/professionals/configure", json=payload)
    assert retried.status_code == 201, retried.text
    assert retried.json()["id"] == str(d["pro"])
    assert retried.json()["phone_e164"] is None


@pytest.mark.asyncio
async def test_phone_requires_existing_write_access_and_stays_in_its_tenant(management_db):
    d = management_db
    c = d["client"]
    values = configuration(d, phone_e164="(67) 99876-5432")
    viewer = {"Authorization": "Bearer " + os.environ["VIEWER_API_TOKEN"]}
    assert (await c.put(target(d), json=values, headers=viewer)).status_code == 403
    assert (await c.put(target(d, d["other_pro"]), json=values)).status_code == 404
    assert (await c.put(target(d), json=values)).status_code == 200
    foreign = (await c.get(f"/api/v1/tenants/{d['b']}/professionals")).json()
    assert foreign[0]["phone_e164"] is None
    assert (await c.get(f"/api/v1/tenants/{d['b']}/professionals", headers=viewer)).status_code == 403
    async with d["runtime"].begin() as db:
        await db.execute(text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(d["b"])})
        assert (await db.execute(text("SELECT phone_e164 FROM professionals WHERE id=:id"),
                                 {"id": d["pro"]})).first() is None
    async with d["migrator"].begin() as db:
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                await db.execute(text("UPDATE professionals SET phone_e164='invalid' WHERE id=:id"),
                                 {"id": d["pro"]})


@pytest.mark.asyncio
async def test_private_professional_contact_is_not_in_public_catalogue_preview_or_page(public_db):
    d = public_db
    secret = "+5567998765432"
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE professionals SET phone_e164=:phone WHERE tenant_id=:tenant AND id=:id"),
                         {"phone": secret, "tenant": d["a"], "id": d["pro"]})
    await publish(d)
    for path in (d["public"], d["base"] + "/site/preview", "/s/" + d["slug"]):
        response = await d["client"].get(path)
        assert response.status_code == 200, response.text
        assert secret not in response.text and "phone_e164" not in response.text
    public = (await d["client"].get(d["public"])).json()
    assert next(p for p in public["professionals"] if p["id"] == str(d["pro"]))["name"] == "Profissional sintético"


@pytest.mark.asyncio
async def test_inherited_onboarding_schema_keeps_legacy_replay_and_preserves_omitted_phone(onboarding_db):
    ctx = new_business_context(onboarding_db)
    state = await onboarding_service.create_business(ctx.actor, creation())
    tenant = UUID(state["tenant_id"])
    payload = onboarding_schemas.SaveStep(expected_version=state["version"], idempotency_key=str(uuid4()),
                                         data={"item": {"name": "Cadastro antigo"}})
    old_data = onboarding_schemas.Professionals.model_validate(payload.data).model_dump(
        mode="json", exclude={"item": {"phone_e164"}})
    old_fingerprint = onboarding_service.digest({"actor": ctx.actor.account_id, "step": "professionals",
                                                 "data": old_data, "supplied": payload.data})
    state = await onboarding_service.save_step(ctx.actor, tenant, "professionals", payload)
    async with ctx.migration.connect() as db:
        stored = (await db.execute(text("""
            SELECT fingerprint FROM business_onboarding_operations
            WHERE tenant_id=:tenant AND idempotency_key=:key
        """), {"tenant": tenant, "key": payload.idempotency_key})).scalar_one()
        assert stored == old_fingerprint
    replay = await onboarding_service.save_step(ctx.actor, tenant, "professionals", payload)
    assert replay["result"] == state["result"] and replay["version"] == state["version"]
    professional = state["catalog"]["professionals"][0]
    state = await save_onboarding(ctx, state, "professionals", {"item": {
        "id": professional["id"], "name": professional["name"],
        "expected_fingerprint": professional["fingerprint"], "phone_e164": "(67) 99876-5432"}})
    professional = state["catalog"]["professionals"][0]
    assert professional["phone_e164"] == "+5567998765432"
    state = await save_onboarding(ctx, state, "professionals", {"item": {
        "id": professional["id"], "name": "Cadastro atualizado",
        "expected_fingerprint": professional["fingerprint"]}})
    assert state["catalog"]["professionals"][0]["phone_e164"] == "+5567998765432"
