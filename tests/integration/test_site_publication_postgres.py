"""Self-service publication with real isolated PostgreSQL roles and transactions."""
import asyncio
from uuid import uuid4

from fastapi import Request
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError

from app.catalog_api import Actor, authenticate
from test_public_catalog import public_db, save_site, publish, availability, book_body, combo

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_locked_branding_ignores_old_client_toggle_without_blocking_other_edits(public_db):
    d = public_db; c = d["client"]; base = d["base"]
    initial = await save_site(d, hide_orion_brand=True)
    assert initial["hide_orion_brand"] is False and initial["hide_orion_brand_locked"] is True
    for role in ("owner", "editor", "support"):
        current = (await c.get(base + "/site", headers={"x-test-role": role})).json()
        result = await c.put(base + "/site", headers={"x-test-role": role}, json={
            "expected_version": current["version"], "slug": d["slug"],
            "title": "Edição permitida " + role, "hide_orion_brand": True,
        })
        assert result.status_code == 200, result.text
        assert result.json()["title"] == "Edição permitida " + role
        assert result.json()["hide_orion_brand"] is False
        assert result.json()["hide_orion_brand_locked"] is True
    async with d["migrator"].connect() as db:
        stored = (await db.execute(text("SELECT hide_orion_brand FROM management_site_preferences WHERE tenant_id=:tenant"),
                                   {"tenant": d["a"]})).scalar_one()
        assert stored is False
    await publish(d)
    assert (await c.get(d["public"])).json()["site"]["hide_orion_brand"] is False


@pytest.mark.asyncio
async def test_locked_branding_preserves_legacy_choices_and_restores_with_credit_visible(public_db):
    d = public_db; c = d["client"]; base = d["base"]
    await publish(d)
    # Model an existing installation without erasing the choice during rollout.
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE management_site_preferences SET hide_orion_brand=true WHERE tenant_id=:tenant"),
                         {"tenant": d["a"]})
    current = (await c.get(base + "/site")).json()
    assert current["hide_orion_brand"] is False and current["has_unpublished_changes"] is False
    async with d["migrator"].begin() as db:
        await db.execute(text("""UPDATE management_site_preferences
            SET public_snapshot=jsonb_set(public_snapshot,'{hide_orion_brand}','true'::jsonb)
            WHERE tenant_id=:tenant"""), {"tenant": d["a"]})
    for path in (base + "/site/preview", d["public"]):
        assert (await c.get(path)).json()["site"]["hide_orion_brand"] is False
    for supplied in (False, True):
        saved = await save_site(d, hide_orion_brand=supplied)
        assert saved["hide_orion_brand"] is False and saved["has_unpublished_changes"] is False
    disabled = await c.post(base + "/site/publication", json={"expected_version": saved["version"], "enabled": False})
    assert disabled.status_code == 200, disabled.text
    restored = await c.post(base + "/site/publication", json={
        "expected_version": disabled.json()["version"], "enabled": True, "restore_previous": True,
    })
    assert restored.status_code == 200, restored.text
    assert restored.json()["has_unpublished_changes"] is False
    assert (await c.get(d["public"])).json()["site"]["hide_orion_brand"] is False
    async with d["migrator"].connect() as db:
        stored = (await db.execute(text("""SELECT hide_orion_brand,public_snapshot FROM management_site_preferences
            WHERE tenant_id=:tenant"""), {"tenant": d["a"]})).mappings().one()
        restored_snapshot = (await db.execute(text("""SELECT snapshot FROM public_site_publications
            WHERE tenant_id=:tenant AND action='restore'"""), {"tenant": d["a"]})).scalar_one()
        assert stored["hide_orion_brand"] is True and stored["public_snapshot"]["hide_orion_brand"] is True
        assert restored_snapshot["hide_orion_brand"] is True


@pytest.mark.asyncio
async def test_owner_publication_status_history_and_restore_preserve_private_draft(public_db):
    d = public_db; c = d["client"]; base = d["base"]
    initial = (await c.get(base + "/site/publication")).json()
    assert initial["state"] == "not_published" and initial["can_publish"]
    draft = await save_site(d)
    ready = (await c.get(base + "/site/publication")).json()
    assert ready["ready"] and ready["state"] == "ready"
    assert draft["has_unpublished_changes"] is False
    publication = await publish(d)
    assert publication["public_version"] > 0
    assert (await c.get(base + "/site/publication")).json()["state"] == "published"
    for role in ("editor", "viewer", "support"):
        permissions = (await c.get(base + "/site/publication", headers={"x-test-role": role})).json()
        assert permissions["can_publish"] is False
        assert permissions["can_edit"] is (role in ("editor", "support"))
    disabled = await c.post(base + "/site/publication", json={"expected_version": publication["version"], "enabled": False})
    assert disabled.status_code == 200, disabled.text
    assert (await c.get(d["public"])).status_code == 404
    draft = await save_site(d, title="", slug=d["slug"] + "-private")
    assert (await c.get(base + "/site/publication")).json()["state"] == "unpublished"
    restored = await c.post(base + "/site/publication", json={"expected_version": draft["version"], "enabled": True, "restore_previous": True})
    assert restored.status_code == 200, restored.text
    assert restored.json()["title"] == "" and restored.json()["has_unpublished_changes"]
    assert (await c.get(d["public"])).json()["site"]["title"] == "Barbearia sintética pública"
    history = (await c.get(base + "/site/publication")).json()["history"]
    assert [item["action"] for item in history] == ["restore", "unpublish", "publish"]
    with pytest.raises(DBAPIError):
        async with d["runtime"].begin() as db:
            await db.execute(text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(d["a"])})
            await db.execute(text("UPDATE public_site_publications SET action='publish' WHERE tenant_id=:tenant"), {"tenant": d["a"]})


@pytest.mark.asyncio
async def test_slug_namespace_retains_alias_and_prevents_foreign_draft_claim(public_db):
    d = public_db; c = d["client"]; base = d["base"]
    published = await publish(d)
    original = d["slug"]; renamed = original + "-new"
    await save_site(d, slug=renamed)
    # Other tenant sees only unavailability, never the owner identity.
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE tenant_memberships SET tenant_id=:b WHERE tenant_id=:a"), {"a": d["a"], "b": d["b"]})
    other_base = f"/api/v1/tenants/{d['b']}/management"
    for slug in (original, renamed):
        response = await c.get(other_base + "/site/slug-availability", params={"slug": slug})
        assert response.status_code == 200 and response.json()["available"] is False
        assert str(d["a"]) not in response.text
    conflict = await c.put(other_base + "/site", json={"expected_version": 0, "title": "Outra empresa", "slug": original})
    assert conflict.status_code == 409, conflict.text
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE tenant_memberships SET tenant_id=:a WHERE tenant_id=:b"), {"a": d["a"], "b": d["b"]})
    renewed = await publish(d)
    assert renewed["public_url"] == "/s/" + renamed
    assert (await c.get(d["public"])).status_code == 200
    async with d["runtime"].begin() as db:
        await db.execute(text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(d["a"])})
        rows = (await db.execute(text("SELECT slug,kind FROM public_site_slugs WHERE tenant_id=:tenant"), {"tenant": d["a"]})).all()
        assert (original, "alias") in rows and (renamed, "canonical") in rows


@pytest.mark.asyncio
async def test_simultaneous_publication_same_draft_has_one_winner(public_db):
    d = public_db; draft = await save_site(d)
    async def attempt():
        return await d["client"].post(d["base"] + "/site/publication", json={"expected_version": draft["version"], "enabled": True})
    responses = await asyncio.gather(attempt(), attempt())
    assert sorted(r.status_code for r in responses) == [200, 409]
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM public_site_publications WHERE tenant_id=:tenant"), {"tenant": d["a"]})).scalar_one() == 1


@pytest.mark.asyncio
async def test_inactive_business_and_nonfitting_hours_fail_without_publication(public_db):
    d = public_db; draft = await save_site(d)
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE tenants SET status='paused' WHERE id=:tenant"), {"tenant": d["a"]})
    result = await d["client"].post(d["base"] + "/site/publication", json={"expected_version": draft["version"], "enabled": True})
    assert result.status_code == 409 and result.json()["detail"]["issues"][0]["code"] == "business_inactive"
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE tenants SET status='active' WHERE id=:tenant"), {"tenant": d["a"]})
        await db.execute(text("UPDATE working_hours SET end_time='08:05' WHERE tenant_id=:tenant"), {"tenant": d["a"]})
    result = await d["client"].post(d["base"] + "/site/publication", json={"expected_version": draft["version"], "enabled": True})
    assert result.status_code == 409 and "hours_required" in str(result.json())
    assert (await d["client"].get(d["public"])).status_code == 404


@pytest.mark.asyncio
async def test_redis_and_postgres_failure_do_not_publish(public_db, monkeypatch):
    d = public_db; draft = await save_site(d)
    class BrokenCache:
        async def ping(self): raise RedisConnectionError("synthetic isolated failure")
        async def aclose(self): pass
    monkeypatch.setattr("app.public_site_api._redis", lambda: BrokenCache())
    result = await d["client"].post(d["base"] + "/site/publication", json={"expected_version": draft["version"], "enabled": True})
    assert result.status_code == 503 and "redis_unavailable" in str(result.json())
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT public_enabled FROM management_site_preferences WHERE tenant_id=:tenant"), {"tenant": d["a"]})).scalar_one() is False
    def unavailable(): raise OperationalError("synthetic isolated failure", {}, Exception())
    monkeypatch.setattr("app.site_publication.get_session_factory", unavailable)
    result = await d["client"].post(d["base"] + "/site/publication", json={"expected_version": draft["version"], "enabled": True})
    assert result.status_code == 503


@pytest.mark.asyncio
async def test_admin_owner_membership_publication_and_cross_tenant_denial(public_db):
    d = public_db; c = d["client"]
    async def admin_actor(request: Request): return Actor("admin", account_id=d["owner"])
    d["app"].dependency_overrides[authenticate] = admin_actor
    await save_site(d)
    own_status = (await c.get(d["base"] + "/site/publication")).json()
    assert own_status["can_publish"] is True
    assert (await publish(d))["published"] is True
    other_base = f"/api/v1/tenants/{d['b']}/management"
    assert (await c.get(other_base + "/site/publication")).json()["can_publish"] is False
    denied = await c.post(other_base + "/site/publication", json={"expected_version": 1, "enabled": True})
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_competing_business_draft_reservations_have_one_winner(public_db):
    d = public_db; c = d["client"]
    async with d["migrator"].begin() as db:
        await db.execute(text("UPDATE tenant_memberships SET tenant_id=:other,role='owner' WHERE account_id=:account"),
                         {"other": d["b"], "account": d["viewer"]})
    async def actor(request: Request):
        from uuid import UUID
        return Actor("member", account_id=UUID(request.headers["x-test-account"]))
    d["app"].dependency_overrides[authenticate] = actor
    slug = "competing-" + uuid4().hex
    async def save(tenant, account):
        return await c.put(f"/api/v1/tenants/{tenant}/management/site", headers={"x-test-account": str(account)},
                           json={"expected_version": 0, "title": "Empresa sintética", "slug": slug})
    result = await asyncio.gather(save(d["a"], d["owner"]), save(d["b"], d["viewer"]))
    assert sorted(response.status_code for response in result) == [200, 409]
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM public_site_slugs WHERE slug=:slug"), {"slug": slug})).scalar_one() == 1


@pytest.mark.asyncio
async def test_breaks_without_sufficient_contiguous_capacity_prevent_publication(public_db):
    d = public_db; draft = await save_site(d)
    async with d["migrator"].begin() as db:
        hours = (await db.execute(text("UPDATE working_hours SET end_time='08:30' WHERE tenant_id=:tenant RETURNING id"), {"tenant": d["a"]})).scalars().all()
        for hour in hours:
            await db.execute(text("INSERT INTO working_breaks(tenant_id,working_hour_id,start_time,end_time) VALUES(:tenant,:hour,'08:05','08:25')"),
                             {"tenant": d["a"], "hour": hour})
    result = await d["client"].post(d["base"] + "/site/publication", json={"expected_version": draft["version"], "enabled": True})
    assert result.status_code == 409 and "hours_required" in str(result.json())


@pytest.mark.asyncio
async def test_republication_unpublish_and_restore_preserve_existing_appointment(public_db):
    d = public_db; c = d["client"]
    await publish(d)
    quoted = await availability(d)
    booked = await c.post(d["public"] + "/appointments", json=book_body(quoted))
    assert booked.status_code == 201, booked.text
    async def stored():
        async with d["migrator"].connect() as db:
            appointment = (await db.execute(text("SELECT to_jsonb(a) FROM appointments a WHERE tenant_id=:tenant"), {"tenant": d["a"]})).scalar_one()
            items = (await db.execute(text("SELECT to_jsonb(i) FROM appointment_items i WHERE tenant_id=:tenant ORDER BY item_order"), {"tenant": d["a"]})).scalars().all()
            return appointment, items
    before = await stored()
    await save_site(d, title="Nova apresentação sintética")
    publication = await publish(d)
    disabled = await c.post(d["base"] + "/site/publication", json={"expected_version": publication["version"], "enabled": False})
    assert disabled.status_code == 200
    restored = await c.post(d["base"] + "/site/publication", json={"expected_version": disabled.json()["version"], "enabled": True, "restore_previous": True})
    assert restored.status_code == 200, restored.text
    assert await stored() == before


@pytest.mark.asyncio
async def test_quote_issued_before_address_change_still_books_through_retained_alias(public_db):
    d = public_db; c = d["client"]
    await publish(d)
    quoted = await availability(d)
    await save_site(d, slug=d["slug"] + "-renamed")
    await publish(d)
    booked = await c.post(d["public"] + "/appointments", json=book_body(quoted))
    assert booked.status_code == 201 and booked.json()["status"] == "confirmed", booked.text
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM appointments WHERE tenant_id=:tenant"), {"tenant": d["a"]})).scalar_one() == 1


@pytest.mark.asyncio
async def test_address_registry_and_history_enforce_tenant_rls_and_support_acl(public_db):
    d = public_db
    await publish(d)
    async with d["runtime"].begin() as db:
        assert (await db.execute(text("SELECT count(*) FROM public_site_slugs"))).scalar_one() == 0
        assert (await db.execute(text("SELECT count(*) FROM public_site_publications"))).scalar_one() == 0
        await db.execute(text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(d["b"])})
        for table in ("public_site_slugs", "public_site_publications"):
            assert (await db.execute(text(f"SELECT count(*) FROM {table} WHERE tenant_id=:other"), {"other": d["a"]})).scalar_one() == 0
        assert (await db.execute(text("SELECT public.public_site_slug_available(:slug,:tenant)"), {"slug": d["slug"], "tenant": d["a"]})).scalar_one() is False
    for table in ("public_site_slugs", "public_site_publications"):
        with pytest.raises(DBAPIError):
            async with d["support"].begin() as db:
                await db.execute(text(f"SELECT * FROM {table}"))
    async with d["migrator"].connect() as db:
        flags = (await db.execute(text("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname IN ('public_site_slugs','public_site_publications')"))).all()
        assert len(flags) == 2 and all(rls and forced for rls, forced in flags)


@pytest.mark.asyncio
async def test_contact_quota_is_shared_across_old_alias_and_new_address(public_db):
    d = public_db; c = d["client"]
    await publish(d)
    renamed = d["slug"] + "-new"
    await save_site(d, slug=renamed)
    await publish(d)
    aliases = (d["public"], "/api/v1/public/sites/" + renamed)
    for index in range(5):
        path = aliases[index % 2]
        available = await c.get(path + "/availability", params={"professional_id": str(d["pro"]),
                               "service_id": str(d["cut"]), "day": d["day"].isoformat()})
        assert available.status_code == 200, available.text
        booked = await c.post(path + "/appointments", json=book_body(available.json(), "+5511987654321"))
        assert booked.status_code == (201 if index < 4 else 429), booked.text
    async with d["migrator"].connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM appointments WHERE tenant_id=:tenant"), {"tenant": d["a"]})).scalar_one() == 4


@pytest.mark.asyncio
async def test_combo_composition_change_stays_private_until_owner_republishes(public_db):
    from uuid import UUID
    d = public_db; c = d["client"]
    registered, _ = await combo(d)
    await publish(d)
    service = UUID(registered["synthetic_service_id"])
    original = await availability(d, service)
    updated = await c.patch(d["base"] + "/service-packages/" + registered["id"], json={
        "expected_version": registered["version"], "name": "Combo ainda privado",
        "service_ids": [str(d["cut"]), str(d["wash"])], "price_cents": 3900})
    assert updated.status_code == 200, updated.text
    current = await c.get(d["public"])
    assert current.status_code == 200 and str(service) not in str(current.json())
    denied = await c.get(d["public"] + "/availability", params={"professional_id": str(d["pro"]),
                         "service_id": str(service), "day": d["day"].isoformat()})
    assert denied.status_code == 409, denied.text
    stale = await c.post(d["public"] + "/appointments", json=book_body(original))
    assert stale.status_code in {409, 422}, stale.text
    await publish(d)
    published = await availability(d, service)
    assert published["services"] == ["Combo ainda privado"] and published["price_cents"] == 3900


@pytest.mark.asyncio
async def test_publication_timestamp_is_after_waiting_for_existing_booking_guard(public_db, monkeypatch):
    from app.site_publication import get_session_factory
    d = public_db; c = d["client"]
    await publish(d)
    draft = await save_site(d, title="Apresentação sintética seguinte")
    factory = get_session_factory()
    publication_waiting = asyncio.Event()
    class TrackingSession:
        def __init__(self): self.session = factory()
        async def __aenter__(self):
            await self.session.__aenter__()
            return self
        async def __aexit__(self, *args): return await self.session.__aexit__(*args)
        def __getattr__(self, key): return getattr(self.session, key)
        async def execute(self, statement, *args, **kwargs):
            if "FROM management_site_preferences" in str(statement) and "FOR UPDATE" in str(statement):
                publication_waiting.set()
            return await self.session.execute(statement, *args, **kwargs)
    monkeypatch.setattr("app.site_publication.get_session_factory", lambda: TrackingSession)
    task = None
    try:
        async with d["migrator"].begin() as booking_guard:
            await booking_guard.execute(text("SELECT * FROM management_site_preferences WHERE tenant_id=:tenant FOR SHARE"), {"tenant": d["a"]})
            task = asyncio.create_task(c.post(d["base"] + "/site/publication", json={"expected_version": draft["version"], "enabled": True}))
            await asyncio.wait_for(publication_waiting.wait(), timeout=10)
            before_release = (await booking_guard.execute(text("SELECT clock_timestamp()"))).scalar_one()
            assert not task.done()
        result = await asyncio.wait_for(task, timeout=10)
        assert result.status_code == 200, result.text
        async with d["migrator"].connect() as db:
            published_at = (await db.execute(text("SELECT public_enabled_at FROM management_site_preferences WHERE tenant_id=:tenant"), {"tenant": d["a"]})).scalar_one()
            receipt_at = (await db.execute(text("SELECT published_at FROM public_site_publications WHERE tenant_id=:tenant ORDER BY id DESC LIMIT 1"), {"tenant": d["a"]})).scalar_one()
            assert receipt_at == published_at and published_at >= before_release
    finally:
        if task is not None and not task.done():
            task.cancel()


@pytest.mark.asyncio
async def test_legacy_site_writes_fail_closed_new_writer_and_tenant_rls_still_work(public_db):
    d = public_db
    draft = await save_site(d)
    # These SQL statements model the preceding API image, without the v2 marker.
    for operation, expected in (("UPDATE management_site_preferences SET title='Legacy rejected' WHERE tenant_id=:tenant", "55000"),
                                ("DELETE FROM management_site_preferences WHERE tenant_id=:tenant", "42501")):
        with pytest.raises(DBAPIError) as exc:
            async with d["runtime"].begin() as db:
                await db.execute(text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(d["a"])})
                await db.execute(text(operation), {"tenant": d["a"]})
        # DELETE already lacks a runtime grant; the guard additionally covers
        # UPDATE/INSERT, which the preceding API legitimately could perform.
        assert getattr(exc.value.orig, "sqlstate", None) == expected
    with pytest.raises(DBAPIError) as exc:
        async with d["runtime"].begin() as db:
            await db.execute(text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(d["b"])})
            await db.execute(text("INSERT INTO management_site_preferences(tenant_id,title) VALUES(:tenant,'Legacy insert rejected')"), {"tenant": d["b"]})
    assert getattr(exc.value.orig, "sqlstate", None) == "55000"
    # The marker is not a tenant capability. RLS continues rejecting a foreign insert.
    with pytest.raises(DBAPIError) as exc:
        async with d["runtime"].begin() as db:
            await db.execute(text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(d["b"])})
            await db.execute(text("SELECT set_config('app.site_publication_v2','on',true)"))
            await db.execute(text("INSERT INTO management_site_preferences(tenant_id,title) VALUES(:tenant,'Foreign insert rejected')"), {"tenant": d["a"]})
    assert getattr(exc.value.orig, "sqlstate", None) == "42501"
    # Ordinary self-service routes automatically set their marker; no operator action.
    saved = await save_site(d, title="Edição autorizada atual")
    assert saved["version"] == draft["version"] + 1
    assert (await publish(d))["published"] is True
    async with d["runtime"].begin() as db:
        assert (await db.execute(text("SELECT current_setting('app.site_publication_v2',true)"))).scalar_one_or_none() in (None, "")


@pytest.mark.asyncio
async def test_twenty_independent_businesses_publish_without_operator_approval(public_db):
    d = public_db; c = d["client"]; tenants = []; accounts = []
    async def actor(request: Request):
        from uuid import UUID
        return Actor("member", account_id=UUID(request.headers["x-test-account"]))
    d["app"].dependency_overrides[authenticate] = actor
    try:
        async with d["migrator"].begin() as db:
            for index in range(20):
                tenant, account, pro, service = [uuid4() for _ in range(4)]
                tenants.append(tenant); accounts.append(account)
                await db.execute(text("INSERT INTO tenants(id,name,slug,timezone) VALUES(:tenant,:name,:slug,'America/Sao_Paulo')"), {"tenant": tenant, "name": "Empresa sintética " + str(index), "slug": "synthetic-" + tenant.hex})
                await db.execute(text("INSERT INTO panel_accounts(id,name,email,password_hash) VALUES(:account,'Proprietário sintético',:email,'not-a-real-hash')"), {"account": account, "email": account.hex + "@example.invalid"})
                await db.execute(text("INSERT INTO tenant_memberships(tenant_id,account_id,role) VALUES(:tenant,:account,'owner')"), {"tenant": tenant, "account": account})
                await db.execute(text("INSERT INTO professionals(id,tenant_id,name) VALUES(:id,:tenant,'Profissional sintético')"), {"id": pro, "tenant": tenant})
                await db.execute(text("INSERT INTO services(id,tenant_id,name,duration_min,price_cents) VALUES(:id,:tenant,'Serviço sintético',30,1200)"), {"id": service, "tenant": tenant})
                await db.execute(text("INSERT INTO professional_services(tenant_id,professional_id,service_id) VALUES(:tenant,:pro,:service)"), {"tenant": tenant, "pro": pro, "service": service})
                await db.execute(text("INSERT INTO working_hours(tenant_id,professional_id,weekday,start_time,end_time) VALUES(:tenant,:pro,1,'08:00','17:00')"), {"tenant": tenant, "pro": pro})
        async def business(index):
            base = f"/api/v1/tenants/{tenants[index]}/management/site"
            headers = {"x-test-account": str(accounts[index])}; slug = "twenty-" + tenants[index].hex
            saved = await c.put(base, headers=headers, json={"expected_version": 0, "slug": slug, "title": "Empresa sintética " + str(index)})
            assert saved.status_code == 200, saved.text
            published = await c.post(base + "/publication", headers=headers, json={"expected_version": saved.json()["version"], "enabled": True})
            assert published.status_code == 200, published.text
            public = await c.get("/api/v1/public/sites/" + slug)
            assert public.status_code == 200 and public.json()["site"]["title"] == "Empresa sintética " + str(index)
            assert len(public.json()["services"]) == 1
            foreign = await c.get(f"/api/v1/tenants/{tenants[(index + 1) % 20]}/management/site", headers=headers)
            assert foreign.status_code == 403
        # Bound the synthetic burst to the same admission size as public booking.
        for start in range(0, 20, 5):
            await asyncio.gather(*(business(index) for index in range(start, start + 5)))
    finally:
        if tenants:
            async with d["migrator"].begin() as db:
                for table in ("management_site_preferences", "working_hours", "professional_services", "services", "professionals", "tenant_audit", "tenant_memberships"):
                    await db.execute(text(f"DELETE FROM {table} WHERE tenant_id=ANY(CAST(:ids AS uuid[]))"), {"ids": tenants})
                await db.execute(text("DELETE FROM tenants WHERE id=ANY(CAST(:ids AS uuid[]))"), {"ids": tenants})
                await db.execute(text("DELETE FROM panel_accounts WHERE id=ANY(CAST(:ids AS uuid[]))"), {"ids": accounts})
