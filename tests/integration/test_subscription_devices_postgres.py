"""Actual member auth, PostgreSQL/RLS, broker fencing and durable device effects.

Only the external WhatsApp transport and paid ASR are replaced. The fake bridge
uses the real private HTTP broker, channel credentials, lease and SQL ledgers.
No real channel is connected and no provider request or message is sent.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
from fastapi import HTTPException
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import catalog_api, qr_channels as channels, subscription_devices as devices
from app import subscription_device_audio
from app.database import tenant_transaction
from app.plan_routing import include_plan_router
from test_onboarding_completion_plan_postgres import (
    completion, disposable_environment, new_owner_http, onboarding_db, path, ready,
)  # noqa: F401 - real PostgreSQL + Redis/session fixtures

pytestmark = pytest.mark.integration


class Bridge:
    def __init__(self, case, private):
        self.case, self.private = case, private
        self.calls, self.results, self.connections = [], {}, {}
        self.owner = uuid4()
        self.lose_finish = False
        self.timeout_after_result = False

    def headers(self, row, *, live=False):
        result = {"X-Orion-Channel-Key": channels._cipher().decrypt(row["credential_encrypted"].encode()).decode()}
        if live:
            result.update({"X-Orion-Channel-Owner": str(self.owner),
                           "X-Orion-Channel-Fence": str(self.connections[str(row["id"])]["fence"])})
        return result

    async def transport(self, row, action, method, payload=None):
        self.calls.append((str(row["id"]), action, method))
        prefix = f"/internal/qr/channels/{row['id']}"
        if action.startswith("device/operations/"):
            key = action.rsplit("/", 1)[-1]
            return self.results.get(key, {"operation_id": key, "status": "uncertain", "recorded": False})
        if method == "GET":
            connection = self.connections.get(str(row["id"]))
            if not connection:
                raise HTTPException(503, "Synthetic unavailable transport")
            check = await self.private.post(prefix + "/lease/check", headers=self.headers(row),
                json={"owner": str(self.owner), "fence": connection["fence"]})
            assert check.status_code == 200, check.text
            snapshot = check.json()["device"]
            scope = {"device_id": snapshot["id"], "generation": snapshot["generation"],
                     "settings_revision": snapshot["settings_revision"]}
            if action == "device/qr":
                return {**scope, "image": "data:image/png;base64,iVBORw0KGgo=", "expires_in_ms": 30000}
            return {**scope, "status": "qr_ready", "phone": None, "phone_verified": False,
                    "applied_revision": snapshot["settings_revision"]}
        command = action.removeprefix("device/")
        request = {key: payload[key] for key in ("operation_id", "device_id", "generation", "settings_revision")}
        for _ in range(2):  # Actual broker permits may be rechecked before I/O.
            permit = await self.private.post(prefix + "/device/control/authorize", json=request, headers=self.headers(row))
            assert permit.status_code == 200 and permit.json()["authorized"], permit.text
            assert permit.json()["action"] == command and permit.json()["channel_id"] == str(row["id"])
        if command in {"connect", "restart", "relink"}:
            old = self.connections.get(str(row["id"]))
            if old and command != "connect":
                released = await self.private.post(prefix + "/lease/release", headers=self.headers(row),
                    json={"owner": str(self.owner), "fence": old["fence"]})
                assert released.status_code == 200, released.text
            lease = await self.private.post(prefix + "/lease/claim", headers=self.headers(row), json={"owner": str(self.owner)})
            assert lease.status_code == 200, lease.text
            self.connections[str(row["id"])] = lease.json()
            assert lease.json()["allowed_senders"] == [] and lease.json()["assistant_enabled"] is False
            assert lease.json()["device"]["capabilities"]["webhook"] is True
            assert lease.json()["device"]["capabilities"]["proxy"] is True
        if command == "remove":
            connection = self.connections.get(str(row["id"]))
            if connection:
                denied = await self.private.post(prefix + "/lease/check", headers=self.headers(row),
                    json={"owner": str(self.owner), "fence": connection["fence"]})
                assert denied.status_code == 409
            self.connections.pop(str(row["id"]), None)
        result = {**request, "action": command, "status": "succeeded"}
        self.results[request["operation_id"]] = result
        if not self.lose_finish:
            finished = await self.private.post(prefix + "/device/control/finish", headers=self.headers(row),
                                               json={**request, "status": "succeeded"})
            assert finished.status_code == 200, finished.text
        if self.timeout_after_result:
            raise HTTPException(503, "Synthetic lost reply")
        return {**result, **({"status": "pairing", "code": "ABCD-EFGH", "expires_in_ms": 90000} if command == "pair" else {})}


@pytest_asyncio.fixture
async def device_case(new_owner_http, monkeypatch):
    case = new_owner_http
    monkeypatch.setenv("QR_CHANNELS_ENABLED", "1")
    monkeypatch.setenv("INTEGRATION_FERNET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("SUBSCRIPTION_DEVICE_ASR_DAILY_SECONDS", "21600")
    monkeypatch.setenv("SUBSCRIPTION_DEVICE_ASR_GLOBAL_DAILY_SECONDS", "86400")
    monkeypatch.setattr(subscription_device_audio, "audio_available", lambda: True)
    # The production app puts explicitly classified modules before the generic
    # catalog routes. Keep the same ordering in this real-auth fixture.
    generic = [route for route in case.app.router.routes
               if getattr(route, "original_router", None) is catalog_api.router
               or "{resource}" in getattr(route, "path", "")]
    case.app.router.routes[:] = [route for route in case.app.router.routes if route not in generic]
    include_plan_router(case.app, channels.router)
    include_plan_router(case.app, devices.router)
    case.app.router.routes.extend(generic)
    state = await ready(case, offering="both")
    done = await case.client.post(path(state) + "/complete", json=completion(state))
    assert done.status_code == 200, done.text
    case.tenant = UUID(done.json()["tenant_id"])
    case.prefix = f"/api/v1/tenants/{case.tenant}/subscription-whatsapp/devices"
    case.state = done.json()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=case.app), base_url="https://test") as private:
        case.private = private
        case.bridge = Bridge(case, private)
        monkeypatch.setattr(channels, "_bridge_control", case.bridge.transport)
        try:
            yield case
        finally:
            async with case.ctx.migration.begin() as db:
                for tenant in (case.tenant, case.ctx.a, case.ctx.b):
                    for table in ("subscription_device_cooldowns", "subscription_device_effects", "subscription_device_events",
                                  "subscription_device_operations", "subscription_devices", "subscription_device_audio_usage"):
                        await db.execute(text(f"DELETE FROM {table} WHERE tenant_id=:t"), {"t": tenant})
                    await db.execute(text("UPDATE tenants SET whatsapp_channel_id=NULL WHERE id=:t"), {"t": tenant})
                    await db.execute(text("DELETE FROM qr_channels WHERE tenant_id=:t"), {"t": tenant})


async def create(case, *, key=None, name="WhatsApp sintético"):
    response = await case.client.post(case.prefix, json={"name": name, "idempotency_key": str(key or uuid4())})
    assert response.status_code == 201, response.text
    case.device = response.json()["device"]
    async with case.ctx.migration.connect() as db:
        case.row = dict((await db.execute(text("SELECT * FROM qr_channels WHERE tenant_id=:t"), {"t": case.tenant})).mappings().one())
        case.saved = dict((await db.execute(text("SELECT * FROM subscription_devices WHERE tenant_id=:t AND removed_at IS NULL"),
                                         {"t": case.tenant})).mappings().one())
    return case.device


async def settings(case, edit):
    response = await case.client.get(case.prefix + f"/{case.device['id']}/settings")
    assert response.status_code == 200, response.text
    current = response.json()
    value = current["settings"]
    value["proxy"].pop("password_configured")
    edit(value)
    updated = await case.client.put(case.prefix + f"/{case.device['id']}/settings", json={
        "expected_revision": current["revision"], "settings": value})
    assert updated.status_code == 200, updated.text
    case.device["revision"] = updated.json()["revision"]
    return updated.json()


async def action(case, name, *, key=None, expected=None):
    response = await case.client.post(case.prefix + f"/{case.device['id']}/actions", json={
        "action": name, "expected_revision": expected or case.device["revision"], "idempotency_key": str(key or uuid4())})
    assert response.status_code == 202, response.text
    value = response.json()
    if value.get("device"):
        case.device["revision"] = value["device"]["revision"]
    return value


def scope(case, revision=None):
    return {"device_id": case.device["id"], "generation": str(case.saved["generation"]),
            "settings_revision": revision or case.device["revision"]}


async def internal(case, suffix, payload=None, *, method="POST", headers=None):
    return await case.private.request(method, f"/internal/qr/channels/{case.row['id']}/device" + suffix,
        headers=headers or case.bridge.headers(case.row, live=True), json=payload)


async def event(case, *, kind="text", event_id=None, phone="5567999999901", revision=None, **extra):
    payload = {**scope(case, revision), "event_id": event_id or str(uuid4()), "kind": kind,
               "occurred_at": datetime.now(timezone.utc).isoformat(), **({"phone": phone} if phone else {}), **extra}
    response = await internal(case, "/event", payload)
    assert response.status_code == 200, response.text
    return response.json()


async def permit(case, effect, *, revision=None):
    response = await internal(case, "/effects/authorize", {**scope(case, revision), "operation_id": effect["operation_id"]})
    assert response.status_code == 200, response.text
    return response.json()


async def finish(case, effect, state, *, revision=None, **extra):
    response = await internal(case, "/effects/finish", {**scope(case, revision), "operation_id": effect["operation_id"],
                                                        "state": state, **extra})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_real_owner_limit_and_idempotency_create_no_socket_plan_or_ai(device_case):
    c = device_case
    key = uuid4()
    requests = await asyncio.gather(*(c.client.post(c.prefix, json={"name": "Único", "idempotency_key": str(key)}) for _ in range(2)))
    assert sorted(r.status_code for r in requests) == [200, 201], [r.text for r in requests]
    assert requests[0].json()["device"]["id"] == requests[1].json()["device"]["id"]
    assert c.bridge.calls == []
    listed = await c.client.get(c.prefix)
    assert listed.status_code == 200 and listed.json()["usage"] == {"used": 1, "limit": 1}
    assert listed.json()["capabilities"]["manage"] and not listed.json()["can_create"]
    assert listed.json()["items"][0]["status"] == "disabled"
    assert not listed.json()["items"][0]["phone_verified"]
    denied = await c.client.post(c.prefix, json={"name": "Segundo", "idempotency_key": str(uuid4())})
    assert denied.status_code == 409 and denied.json()["detail"]["code"] == "DEVICE_LIMIT_REACHED"
    assert "credential" not in json.dumps(listed.json())
    async with c.ctx.migration.connect() as db:
        channel = (await db.execute(text("SELECT enabled,assistant_enabled,allowed_senders FROM qr_channels WHERE tenant_id=:t"), {"t": c.tenant})).mappings().one()
        assert channel == {"enabled": False, "assistant_enabled": False, "allowed_senders": []}
        assert (await db.execute(text("SELECT count(*) FROM tenant_plan_controls WHERE tenant_id=:t"), {"t": c.tenant})).scalar_one() == 0


@pytest.mark.asyncio
async def test_real_auth_csrf_membership_niche_and_rls_are_not_bypassed(device_case):
    c = device_case
    await create(c)
    no_csrf = await c.client.post(c.prefix, json={"name": "Blocked", "idempotency_key": str(uuid4())}, headers={"x-orion-csrf": ""})
    assert no_csrf.status_code == 403
    anonymous = await c.private.get(c.prefix)
    assert anonymous.status_code == 401
    foreign = await c.client.get(c.prefix.replace(str(c.tenant), str(c.ctx.a)))
    assert foreign.status_code == 403
    legacy = await c.client.get(f"/api/v1/tenants/{c.tenant}/qr-channels")
    assert legacy.status_code == 403
    async with c.ctx.runtime() as db:
        async with tenant_transaction(db, c.tenant):
            assert (await db.execute(text("SELECT id FROM subscription_devices"))).scalars().all() == [UUID(c.device["id"])]
    async with c.ctx.runtime() as db:
        async with tenant_transaction(db, c.ctx.a):
            assert (await db.execute(text("SELECT id FROM subscription_devices"))).scalars().all() == []
    async with c.ctx.runtime() as db:
        with pytest.raises(DBAPIError):
            async with tenant_transaction(db, c.tenant):
                await db.execute(text("SELECT proxy_secret_encrypted,settings FROM subscription_devices"))
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE tenant_memberships SET active=false WHERE tenant_id=:t AND account_id=:a"),
                         {"t": c.tenant, "a": c.ctx.actor.account_id})
    revoked = await c.client.get(c.prefix)
    assert revoked.status_code == 403
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE tenant_memberships SET active=true WHERE tenant_id=:t AND account_id=:a"),
                         {"t": c.tenant, "a": c.ctx.actor.account_id})
        await db.execute(text("UPDATE business_onboarding SET configuration=jsonb_set(configuration,'{niche}','\"barbearia\"'::jsonb) WHERE tenant_id=:t"), {"t": c.tenant})
    standard = await c.client.get(c.prefix)
    assert standard.status_code == 403


@pytest.mark.asyncio
async def test_settings_revision_proxy_secret_preservation_and_stale_permit(device_case):
    c = device_case
    await create(c)
    updated = await settings(c, lambda value: value["proxy"].update(enabled=True, url="https://proxy.example.test:8080",
        username="fixture", password="never-public-fixture"))
    assert updated["settings"]["proxy"]["password_configured"] and "never-public-fixture" not in json.dumps(updated)
    async with c.ctx.migration.connect() as db:
        encrypted = (await db.execute(text("SELECT proxy_secret_encrypted FROM subscription_devices WHERE id=:d"), {"d": UUID(c.device["id"])})).scalar_one()
        assert encrypted != "never-public-fixture"
    await settings(c, lambda value: value.update(name="Nome atualizado"))
    async with c.ctx.migration.connect() as db:
        assert (await db.execute(text("SELECT proxy_secret_encrypted FROM subscription_devices WHERE id=:d"), {"d": UUID(c.device["id"])})).scalar_one() == encrypted
    stale = await c.client.put(c.prefix + f"/{c.device['id']}/settings", json={"expected_revision": 1,
        "settings": devices.DeviceSettings().model_dump(mode="json")})
    assert stale.status_code == 409
    await settings(c, lambda value: value["proxy"].update(clear_password=True, enabled=False))
    assert not (await c.client.get(c.prefix + f"/{c.device['id']}/settings")).json()["settings"]["proxy"]["password_configured"]
    await action(c, "connect")
    old_revision = c.device["revision"]
    await settings(c, lambda value: value["calls"].update(reject_enabled=True))
    old = await internal(c, "/event", {**scope(c, old_revision), "event_id": "stale", "kind": "text",
        "occurred_at": datetime.now(timezone.utc).isoformat(), "phone": "5567999999901"})
    assert old.status_code == 409


@pytest.mark.asyncio
async def test_affiliate_whatsapp_pairing_uses_isolated_device_without_inbound_effects(device_case):
    c = device_case
    async with c.ctx.migration.begin() as db:
        await db.execute(text("""UPDATE business_onboarding
            SET configuration=jsonb_set(configuration,'{niche}',to_jsonb(CAST(:niche AS text))) WHERE tenant_id=:t"""),
            {"niche": "afiliados", "t": c.tenant})
    await create(c)
    assert not c.device["capabilities"]["settings"]
    assert c.device["metrics"] == {"available": False}
    assert (await c.client.get(c.prefix + f"/{c.device['id']}/settings")).status_code == 403
    connected = await action(c, "connect")
    assert connected["operation"]["status"] == "succeeded"
    qr = await c.client.get(c.prefix + f"/{c.device['id']}/qr")
    assert qr.status_code == 200 and qr.json()["image"].startswith("data:image/png;base64,")
    paired = await c.client.post(c.prefix + f"/{c.device['id']}/pair",
        json={"expected_revision": c.device["revision"], "phone": "5567999999901"})
    assert paired.status_code == 200 and paired.json()["code"] == "ABCD-EFGH"
    incoming = await event(c, text="Mensagem sintética", message_id="synthetic-affiliate-input")
    assert incoming["effects"] == []
    pending = await internal(c, "/effects/pending", method="GET")
    assert pending.json() == {"effects": [], "reconcile": []}
    async with c.ctx.migration.connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM subscription_device_events WHERE tenant_id=:t"),
            {"t": c.tenant})).scalar_one() == 0


@pytest.mark.asyncio
async def test_delivery_whatsapp_connection_is_preview_owner_only_and_has_no_automation(device_case, monkeypatch):
    c = device_case
    async with c.ctx.migration.begin() as db:
        await db.execute(text("""UPDATE business_onboarding
            SET configuration=jsonb_set(configuration,'{niche}',to_jsonb(CAST(:niche AS text))) WHERE tenant_id=:t"""),
            {"niche": "delivery_distribuicao", "t": c.tenant})
    monkeypatch.setenv("ORION_DELIVERY_PREVIEW_TENANTS", "")
    assert (await c.client.get(c.prefix)).status_code == 403
    monkeypatch.setenv("ORION_DELIVERY_PREVIEW_TENANTS", str(c.tenant))
    await create(c)
    assert c.device["capabilities"]["settings"] is False
    assert c.device["metrics"] == {"available": False}
    assert (await c.client.get(c.prefix + f"/{c.device['id']}/settings")).status_code == 403
    assert (await c.client.get(c.prefix.replace(str(c.tenant), str(c.ctx.a)))).status_code == 403
    assert (await action(c, "connect"))["operation"]["status"] == "succeeded"
    qr = await c.client.get(c.prefix + f"/{c.device['id']}/qr")
    assert qr.status_code == 200 and qr.json()["image"].startswith("data:image/png;base64,")
    paired = await c.client.post(c.prefix + f"/{c.device['id']}/pair",
        json={"expected_revision": c.device["revision"], "phone": "5567999999901"})
    assert paired.status_code == 200 and paired.json()["code"] == "ABCD-EFGH"
    lease = c.bridge.connections[str(c.row["id"])]["device"]
    assert lease["chatbot"] is None and not lease["settings"]["webhook"]["enabled"]
    assert not lease["capabilities"]["transcription_available"]
    result = await event(c, text="Mensagem sintética de teste", message_id="synthetic-delivery-input")
    assert result["effects"] == []
    pending = await internal(c, "/effects/pending", method="GET")
    assert pending.status_code == 200 and pending.json() == {"effects": [], "reconcile": []}
    async with c.ctx.migration.connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM subscription_device_events WHERE tenant_id=:t"),
            {"t": c.tenant})).scalar_one() == 0
    monkeypatch.setenv("ORION_DELIVERY_PREVIEW_TENANTS", "")
    assert (await c.client.get(c.prefix)).status_code == 403
    assert (await internal(c, "/effects/pending", method="GET")).status_code == 403


@pytest.mark.asyncio
async def test_delivery_whatsapp_connection_checkout_with_same_device_and_explicit_confirmation(device_case, monkeypatch):
    """The real broker, checkout, and database are used. No WhatsApp I/O."""
    from app import delivery_catalog as catalog, delivery_storefront as store
    from app.tenant_access import TenantAccess
    from test_delivery_catalog_postgres import new_product, complete_delivery_setup
    c=device_case
    async with c.ctx.migration.begin() as db:
        await db.execute(text("""UPDATE business_onboarding SET configuration=configuration ||
          '{"niche":"delivery_distribuicao","delivery_offering":"gas_agua","activity":"products","modality":"online"}'::jsonb
          WHERE tenant_id=:t"""),{"t":c.tenant})
    monkeypatch.setenv("ORION_DELIVERY_PREVIEW_TENANTS",str(c.tenant))
    monkeypatch.setenv("PUBLIC_SITE_SIGNING_KEY",uuid4().hex+uuid4().hex)
    await complete_delivery_setup(c.ctx,c.tenant)
    await create(c)
    assert (await action(c,"connect"))["operation"]["status"]=="succeeded"
    config=c.prefix+"/delivery-assistant"
    status=(await c.client.get(config)).json()
    assert status["enabled"] is False and status["eligible_products"]==0
    assert (await c.client.put(config,json={"enabled":True,"expected_revision":status["revision"]})).status_code==409
    access=TenantAccess("owner",c.ctx.actor.account_id)
    async with c.ctx.runtime() as db:
        async with tenant_transaction(db,c.tenant):
            created=await catalog.create_item(db,c.tenant,access,catalog.ProductCreate.model_validate(new_product("Água mineral 20 L")))
            items=(await store.operator_items(db,c.tenant,"",0))["items"]
            selected=next(x for x in items if str(x["variant_id"])==str(created["variant_id"]))
            await store.save_store(db,c.tenant,access,store.Storefront(
                revision=0,slug="wa-"+uuid4().hex[:12],title="Distribuidora teste",enabled=False,
                allow_pickup=True,pickup_details="Depósito",payment_methods=["cash","pix"],
                items=[store.PublicItem(variant_id=selected["variant_id"],product_revision=selected["product_revision"],
                    variant_revision=selected["variant_revision"],label="Água mineral 20 L",kind="water")]))
    status=(await c.client.get(config)).json()
    assert status["eligible_products"]==1
    activated=await c.client.put(config,json={"enabled":True,"expected_revision":status["revision"]})
    assert activated.status_code==200,activated.text
    c.device["revision"]=activated.json()["revision"]
    assert activated.json()["enabled"] is True
    phone="5567999999902"

    async def talk(body=None, *, contact=phone, message=None, accepted=True, selection=None, quoted=None, full=False):
        data={"text":body} if selection is None else {"selection_id":selection,"quoted_message_id":quoted}
        result=await event(c,phone=contact,message_id=message or uuid4().hex,**data)
        assert len(result["effects"])==1,result
        effect=result["effects"][0]
        grant=await permit(c,effect)
        assert grant["authorized"] and grant["payload"]["phone"]==contact,grant
        provider_id=uuid4().hex
        if accepted:
            await finish(c,effect,"accepted",provider_id=provider_id)
        return (grant["payload"],provider_id) if full else grant["payload"]["text"]

    async def orders():
        async with c.ctx.migration.connect() as db:
            return (await db.execute(text("""SELECT d.intake_channel,q.total_cents,o.status
                FROM delivery_order_details d JOIN commercial_orders o ON (o.tenant_id,o.id)=(d.tenant_id,d.order_id)
                JOIN commercial_quotes q ON (q.tenant_id,q.id)=(o.tenant_id,o.quote_id) WHERE d.tenant_id=:t"""),
                {"t":c.tenant})).all()

    assert "equipe" in await talk("Quero gás P13")
    assert "desconto" in (await talk("Tem desconto? Posso retirar?")).lower()
    assert "retirar" in (await talk("Posso retirar?")).lower()
    assert "não estamos fazendo entregas" in (await talk("Quanto tempo leva a entrega?")).lower()
    assert "R$ 12,50" in await talk("Quanto custa a água?")
    assert "nome" in (await talk("Quero duas águas")).lower()
    assert "retirada" in await talk("Cliente de teste")
    assert "pagar" in await talk("retirada")
    summary, first_provider=await talk("dinheiro, troco pra 50 reais",full=True)
    assert "R$ 25,00" in summary["text"] and "CONFIRMO" in summary["text"],summary
    assert "Troco previsto: R$ 25,00" in summary["text"]
    first_buttons=summary["delivery_presentation"]["action"]["buttons"]
    assert [b["title"] for b in first_buttons]==["Confirmar pedido","Cancelar"]
    assert await orders()==[]
    questions, second_provider = await talk("Aceita Pix?",full=True)
    assert "Formas de pagamento" in questions["text"] and "CONFIRMO" in questions["text"]
    assert await orders()==[]
    assert "não corresponde" in await talk(selection=first_buttons[0]["id"],quoted=first_provider,contact="5567999999903")
    assert await orders()==[]
    stale, current_provider=await talk(selection=first_buttons[0]["id"],quoted=first_provider,full=True)
    assert "não corresponde" in stale["text"] and "CONFIRMO" in stale["text"]
    assert await orders()==[]
    current_buttons=stale["delivery_presentation"]["action"]["buttons"]
    assert current_buttons[0]["id"]!=questions["delivery_presentation"]["action"]["buttons"][0]["id"]
    identity=uuid4().hex
    assert "registrado" in await talk(selection=current_buttons[0]["id"],quoted=current_provider,message=identity)
    assert "não corresponde" in await talk(selection=current_buttons[0]["id"],quoted=current_provider)
    assert await orders()==[("whatsapp",2500,"confirmed")]
    repeated=await event(c,phone=phone,text="confirmo",message_id=identity)
    assert repeated["duplicate"] and repeated["effects"]==[]
    assert await orders()==[("whatsapp",2500,"confirmed")]
    async with c.ctx.migration.connect() as db:
        # CRM identity and open history must exist before fulfillment.
        customer=(await db.execute(text("""SELECT id,name FROM customers
            WHERE tenant_id=:t AND whatsapp_e164=:phone AND active AND deleted_at IS NULL"""),
            {"t":c.tenant,"phone":"+"+phone})).mappings().one()
        assert customer["name"]=="Cliente de teste"
        linked=await db.scalar(text("""SELECT customer_id FROM delivery_order_details
            WHERE tenant_id=:t AND intake_channel='whatsapp'"""),{"t":c.tenant})
        assert linked==customer["id"]
        inventory=(await db.execute(text("""SELECT quantity_on_hand,reserved_quantity FROM commercial_inventory
            WHERE tenant_id=:t AND variant_id=:v"""),{"t":c.tenant,"v":created["variant_id"]})).one()
        assert inventory==(5,2)
    async with c.ctx.runtime() as db:
        async with tenant_transaction(db,c.tenant):
            from app import delivery_crm
            profile=await delivery_crm.profile(db,c.tenant,customer["id"])
            assert profile["summary"]["orders"]==1 and profile["summary"]["open_orders"]==1
    assert "aguardando atendimento" in await talk("meu pedido")
    assert "retirada" in await talk("Quero uma água")
    assert "pagar" in await talk("retirada")
    draft, provider=await talk("dinheiro, sem troco",full=True)
    cancel=draft["delivery_presentation"]["action"]["buttons"][1]["id"]
    assert "Cancelei o resumo" in await talk(selection=cancel,quoted=provider)
    assert await orders()==[("whatsapp",2500,"confirmed")]
    async with c.ctx.migration.connect() as db:
        assert await db.scalar(text("""SELECT count(*) FROM customers WHERE tenant_id=:t
            AND whatsapp_e164=:phone"""),{"t":c.tenant,"phone":"+"+phone})==1
    # Disabled ASR must respond to a real audio event without a provider call.
    disabled_audio=await event(c,kind="audio",phone=phone,message_id=uuid4().hex,
        audio={"seconds":3,"size":10,"media_type":"audio/ogg"})
    assert disabled_audio["delivery_audio_skipped"] is True
    fallback,=disabled_audio["effects"]
    assert "Recebi seu áudio" in (await permit(c,fallback))["payload"]["text"]
    await finish(c,fallback,"accepted",provider_id=uuid4().hex)

    # Pilot reuses the authenticated ASR endpoint and actual durable quota.
    from app import subscription_devices, subscription_device_audio
    monkeypatch.setenv("ORION_DELIVERY_AUDIO_PREVIEW_TENANTS",str(c.tenant))
    monkeypatch.setattr(subscription_devices,"audio_capable",lambda:True)
    spoken=["Quero uma água para retirar","confirmo"]
    async def synthetic_audio(request, *, max_minutes, authorize):
        assert max_minutes==2
        await authorize(3,phase="before_provider")
        phrase=spoken.pop(0)
        await authorize(3,phase="after_provider")
        return {"text":phrase,"duration_seconds":3}
    monkeypatch.setattr(subscription_device_audio,"transcribe_audio",synthetic_audio)

    async def speak():
        admitted=await event(c,kind="audio",phone=phone,message_id=uuid4().hex,
            audio={"seconds":3,"size":10,"media_type":"audio/ogg"})
        asr,=admitted["effects"]
        grant=await permit(c,asr)
        assert grant["authorized"] and grant["type"]=="transcribe"
        headers={**c.bridge.headers(c.row,live=True),"Content-Type":"audio/ogg",
            "X-Orion-Device-Id":c.device["id"],"X-Orion-Device-Generation":str(c.saved["generation"]),
            "X-Orion-Device-Revision":str(c.device["revision"]),"X-Orion-Device-Effect":asr["operation_id"]}
        endpoint=f"/internal/qr/channels/{c.row['id']}/device/audio"
        response=await c.private.post(endpoint,headers=headers,content=b"synthetic")
        assert response.status_code==200,response.text
        child,=response.json()["effects"]
        reply=(await permit(c,child))["payload"]["text"]
        await finish(c,child,"accepted",provider_id=uuid4().hex)
        assert (await c.private.post(endpoint,headers=headers,content=b"duplicate")).status_code==409
        return reply

    assert "pagar" in await speak()
    assert "CONFIRMO" in await talk("dinheiro, sem troco")
    assert "pelo botão ou por texto" in await speak()
    assert await orders()==[("whatsapp",2500,"confirmed")]
    assert "Cancelei o resumo" in await talk("não")
    failed=(await event(c,kind="audio",phone=phone,message_id=uuid4().hex,
        audio={"seconds":3,"size":10,"media_type":"audio/ogg"}))["effects"][0]
    assert (await permit(c,failed))["authorized"]
    terminal=await finish(c,failed,"not_sent",error_code="DEVICE_AUDIO_FAILED")
    assert terminal["delivery_audio_handled"] is True
    fallback,=terminal["effects"]
    assert "Recebi seu áudio" in (await permit(c,fallback))["payload"]["text"]
    await finish(c,fallback,"accepted",provider_id=uuid4().hex)
    assert await orders()==[("whatsapp",2500,"confirmed")]
    danger=await talk("Meu gás está vazando")
    assert "193" in danger and "local seguro" in danger
    assert await orders()==[("whatsapp",2500,"confirmed")]
    assert (await event(c,phone=phone,text="oi",message_id=uuid4().hex))["effects"]==[]
    await talk("retomar atendimento")
    assert "Pausei" in await talk("atendente")
    silent=await event(c,phone=phone,text="oi",message_id=uuid4().hex)
    assert silent["effects"]==[]
    paused=await c.client.put(config,json={"enabled":False,"expected_revision":c.device["revision"]})
    assert paused.status_code==200,paused.text
    c.device["revision"]=paused.json()["revision"]
    assert (await event(c,phone=phone,text="Quero água",message_id=uuid4().hex))["effects"]==[]


@pytest.mark.asyncio
async def test_control_replay_pair_and_lost_finish_remove_reconcile_keep_history(device_case):
    c = device_case
    await create(c)
    original = (c.row["id"], c.saved["generation"], c.device["id"])
    key = uuid4()
    first = await action(c, "connect", key=key)
    assert first["operation"]["status"] == "succeeded"
    count = len(c.bridge.calls)
    again = await action(c, "connect", key=key)
    assert again["operation"] == first["operation"] and len(c.bridge.calls) == count
    qr = await c.client.get(c.prefix + f"/{c.device['id']}/qr")
    assert qr.status_code == 200 and qr.json()["image"].startswith("data:image/png;base64,")
    paired = await c.client.post(c.prefix + f"/{c.device['id']}/pair", json={"expected_revision": c.device["revision"], "phone": "5567999999901"})
    assert paired.status_code == 200 and paired.json()["code"] == "ABCD-EFGH", paired.text
    pair_record = next(value for value in c.bridge.results.values() if value["action"] == "pair")
    pair_read = await c.client.get(c.prefix + f"/{c.device['id']}/operations/{pair_record['operation_id']}")
    assert pair_read.status_code == 200 and "ABCD-EFGH" not in pair_read.text and "code" not in pair_read.json()["operation"]
    await action(c, "relink")
    async with c.ctx.migration.connect() as db:
        assert (await db.execute(text("SELECT generation FROM subscription_devices WHERE id=:d"), {"d": UUID(c.device["id"])})).scalar_one() == original[1]
    c.bridge.lose_finish = c.bridge.timeout_after_result = True
    removed = await action(c, "remove")
    assert removed["operation"]["status"] == "uncertain" and removed["device"] is not None
    assert removed["device"]["status"] == "removing" and not removed["device"]["capabilities"]["remove"]
    result = await c.client.get(c.prefix + f"/{c.device['id']}/operations/{removed['operation']['id']}")
    assert result.status_code == 200 and result.json()["operation"]["status"] == "succeeded" and result.json()["device"] is None
    assert (await c.client.get(c.prefix)).json()["usage"] == {"used": 0, "limit": 1}
    c.bridge.lose_finish = c.bridge.timeout_after_result = False
    await create(c, name="Nova identidade")
    assert c.row["id"] == original[0] and c.saved["generation"] != original[1] and c.device["id"] != original[2]
    assert c.device["status"] == "disabled" and not c.device["phone_verified"]


@pytest.mark.asyncio
async def test_daily_notice_reservation_is_atomic_and_test_phone_bypasses_only_cooldown(device_case):
    c = device_case
    await create(c)
    await settings(c, lambda value: value["business_hours"].update(enabled=True, outside_message="Fora do horário"))
    await action(c, "connect")
    first = await event(c, event_id="message-1", message_id="M1")
    assert len(first["effects"]) == 1
    duplicate = await event(c, event_id="message-1", message_id="M1")
    assert duplicate == {"effects": [], "duplicate": True}
    second = await event(c, message_id="M2")
    answers = await asyncio.gather(permit(c, first["effects"][0]), permit(c, second["effects"][0]))
    assert sum(answer["authorized"] for answer in answers) == 1
    await settings(c, lambda value: value.update(test_phone_e164="+5567999999901"))
    for message in ("M3", "M4"):
        current = await event(c, message_id=message)
        assert (await permit(c, current["effects"][0]))["authorized"]
    wrong = await internal(c, "/effects/authorize", {**scope(c), "operation_id": str(uuid4())})
    assert wrong.status_code == 404


@pytest.mark.asyncio
async def test_hidden_calls_and_unique_message_metrics_never_count_receipts_twice(device_case):
    c = device_case
    await create(c)
    await settings(c, lambda value: value["calls"].update(reject_enabled=True, reply_enabled=True))
    await action(c, "connect")
    result = await event(c, kind="call", phone=None, call_id="CALL-1", call_from="123456789012345@lid")
    assert [item["type"] for item in result["effects"]] == ["reject_call"]
    allowed = await permit(c, result["effects"][0])
    assert allowed["authorized"] and allowed["payload"]["phone"] is None
    known = await event(c, kind="call", call_id="CALL-2", call_from="5567999999901@s.whatsapp.net")
    reject, reply = known["effects"]
    assert not (await permit(c, reply))["authorized"]
    assert (await permit(c, reject))["authorized"]
    await finish(c, reject, "accepted")
    assert (await permit(c, reply))["authorized"]
    for kind, message, state in (("text", "IN1", None), ("outbound", "OUT1", "delivered"), ("outbound", "OUT1", "read")):
        await event(c, kind=kind, message_id=message, **({"state": state} if state else {}))
    listed = await c.client.get(c.prefix)
    assert listed.status_code == 200
    stats = listed.json()["items"][0]["metrics"]
    assert stats["total"] == stats["today"] == 2 and stats["scope"] == "orion_events"


@pytest.mark.asyncio
async def test_webhook_retry_is_durable_and_disables_only_after_third_failed_attempt(device_case):
    c = device_case
    await create(c)
    await settings(c, lambda value: value["webhook"].update(enabled=True, url="https://webhook.example.test/receive", disable_on_failure=True))
    await action(c, "connect")
    result = await event(c, message_id="WEB1", text="Evento sintético")
    effect = result["effects"][0]
    assert effect["type"] == "webhook"
    for attempt in range(1, 4):
        permission = await permit(c, effect)
        assert permission["authorized"] and permission["payload"]["body"]["data"]["text"] == "Evento sintético"
        await finish(c, effect, "failed", error_code="DEVICE_WEBHOOK_FAILED")
        async with c.ctx.migration.begin() as db:
            saved = (await db.execute(text("SELECT settings,revision FROM subscription_devices WHERE id=:d"), {"d": UUID(c.device["id"])})).mappings().one()
            assert saved["settings"]["webhook"]["enabled"] is (attempt < 3)
            if attempt < 3:
                await db.execute(text("UPDATE subscription_device_effects SET due_at=clock_timestamp()-interval '1 second' WHERE id=:id"), {"id": UUID(effect["operation_id"])})
    current = await c.client.get(c.prefix + f"/{c.device['id']}/settings")
    assert current.json()["revision"] == c.device["revision"] + 1
    assert not current.json()["settings"]["webhook"]["enabled"]


@pytest.mark.asyncio
async def test_stale_leases_and_niche_changes_block_real_broker_before_effects(device_case):
    c = device_case
    await create(c)
    await settings(c, lambda value: value["calls"].update(reject_enabled=True))
    await action(c, "connect")
    result = await event(c, kind="call", call_id="C1", call_from="5567999999901@s.whatsapp.net")
    old = c.bridge.headers(c.row, live=True)
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE qr_channels SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=:c"), {"c": c.row["id"]})
    denied = await internal(c, "/effects/authorize", {**scope(c), "operation_id": result["effects"][0]["operation_id"]}, headers=old)
    assert denied.status_code == 409
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE business_onboarding SET configuration=jsonb_set(configuration,'{niche}','\"outro\"'::jsonb) WHERE tenant_id=:t"), {"t": c.tenant})
    claim = await c.private.post(f"/internal/qr/channels/{c.row['id']}/lease/claim", headers=c.bridge.headers(c.row), json={"owner": str(uuid4())})
    assert claim.status_code == 403
    async with c.ctx.migration.connect() as db:
        assert (await db.execute(text("SELECT attempts FROM subscription_device_effects WHERE id=:o"), {"o": UUID(result["effects"][0]["operation_id"])})).scalar_one() == 0


@pytest.mark.asyncio
async def test_audio_quota_single_attempt_and_complete_ordered_parts_reconcile_without_resend(device_case, monkeypatch):
    c = device_case
    await create(c)
    await settings(c, lambda value: value["transcription"].update(enabled=True, max_minutes=30, reply_enabled=False, template="{transcript}"))
    await action(c, "connect")
    result = await event(c, kind="audio", message_id="AUDIO1", audio={"seconds": 1, "size": 10, "media_type": "audio/ogg"})
    effect = result["effects"][0]
    assert (await permit(c, effect))["authorized"]
    # An ASR request may spend 30s downloading plus 120s decoding/provider and
    # transport overhead. The outbox must not abandon its live attempt at150s.
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE subscription_device_effects SET authorized_at=clock_timestamp()-interval '160 seconds' WHERE id=:o"), {"o": UUID(effect["operation_id"])})
    assert (await internal(c, "/effects/pending", method="GET")).status_code == 200
    async with c.ctx.migration.connect() as db:
        assert (await db.execute(text("SELECT status FROM subscription_device_effects WHERE id=:o"), {"o": UUID(effect["operation_id"])})).scalar_one() == "running"
    source = ("áudio completo " * 7000).strip()
    provider_calls = []
    async def synthetic_audio(request, *, max_minutes, authorize):
        assert max_minutes == 30
        await authorize(1799.1, phase="before_provider")
        provider_calls.append("single-attempt")
        await authorize(1799.1, phase="after_provider")
        return {"text": source, "duration_seconds": 1799.1}
    monkeypatch.setattr(subscription_device_audio, "transcribe_audio", synthetic_audio)
    headers = {**c.bridge.headers(c.row, live=True), "Content-Type": "audio/ogg",
        "X-Orion-Device-Id": c.device["id"], "X-Orion-Device-Generation": str(c.saved["generation"]),
        "X-Orion-Device-Revision": str(c.device["revision"]), "X-Orion-Device-Effect": effect["operation_id"]}
    endpoint = f"/internal/qr/channels/{c.row['id']}/device/audio"
    transcript = await c.private.post(endpoint, headers=headers, content=b"synthetic-decoder-isolated")
    assert transcript.status_code == 200 and transcript.json()["text"] == source and transcript.json()["truncated"] is False, transcript.text
    assert provider_calls == ["single-attempt"]
    duplicate = await c.private.post(endpoint, headers=headers, content=b"must-not-charge-again")
    assert duplicate.status_code == 409 and provider_calls == ["single-attempt"]
    async with c.ctx.migration.connect() as db:
        usage = (await db.execute(text("SELECT reserved_seconds,attempts FROM subscription_device_audio_usage WHERE tenant_id=:t"), {"t": c.tenant})).one()
        assert tuple(usage) == (1800, 1)
        rows = (await db.execute(text("SELECT id,previous_effect,payload FROM subscription_device_effects WHERE device_id=:d AND purpose='transcription_reply' ORDER BY effect_key"),
                                 {"d": UUID(c.device["id"])})).mappings().all()
    assert "".join(row["payload"]["text"] for row in rows) == source and len(rows) == 4
    first, second = ({"operation_id": str(row["id"]), "type": "send_text"} for row in rows[:2])
    assert not (await permit(c, second))["authorized"]
    assert (await permit(c, first))["authorized"]
    async with c.ctx.migration.begin() as db:
        await db.execute(text("UPDATE subscription_device_effects SET authorized_at=clock_timestamp()-interval '181 seconds' WHERE id=:o"), {"o": UUID(first["operation_id"])})
    pending = await internal(c, "/effects/pending", method="GET")
    assert pending.status_code == 200 and any(row["operation_id"] == first["operation_id"] for row in pending.json()["reconcile"])
    await finish(c, first, "accepted", reconcile=True, provider_id="SENT-ONCE")
    assert not (await permit(c, first))["authorized"]
    assert (await permit(c, second))["authorized"]


@pytest.mark.asyncio
async def test_audio_budget_exhaustion_never_calls_provider_or_releases_ambiguous_reservation(device_case, monkeypatch):
    c = device_case
    await create(c)
    await settings(c, lambda value: value["transcription"].update(enabled=True, max_minutes=30))
    await action(c, "connect")
    monkeypatch.setenv("SUBSCRIPTION_DEVICE_ASR_DAILY_SECONDS", "10")
    effect = (await event(c, kind="audio", message_id="A2", audio={"seconds": 1, "size": 8, "media_type": "audio/ogg"}))["effects"][0]
    assert (await permit(c, effect))["authorized"]
    called = []
    async def too_long(request, *, max_minutes, authorize):
        await authorize(10.1, phase="before_provider")
        called.append(True)
        pytest.fail("Quota must reject before paid provider")
    monkeypatch.setattr(subscription_device_audio, "transcribe_audio", too_long)
    headers = {**c.bridge.headers(c.row, live=True), "Content-Type": "audio/ogg", "X-Orion-Device-Id": c.device["id"],
        "X-Orion-Device-Generation": str(c.saved["generation"]), "X-Orion-Device-Revision": str(c.device["revision"]),
        "X-Orion-Device-Effect": effect["operation_id"]}
    response = await c.private.post(f"/internal/qr/channels/{c.row['id']}/device/audio", headers=headers, content=b"synthetic")
    assert response.status_code == 429 and called == []
    async with c.ctx.migration.connect() as db:
        assert (await db.execute(text("SELECT count(*) FROM subscription_device_audio_usage WHERE tenant_id=:t"), {"t": c.tenant})).scalar_one() == 0


@pytest.mark.asyncio
async def test_platform_operator_device_binding_is_readonly_and_current_with_real_postgres(device_case):
    """The server chooses the active tenant/channel and actual encrypted key."""
    from app import qr_ingress_review as review
    c = device_case
    await create(c)
    device_id = UUID(c.device["id"])
    # No leased device may be reviewed by guessing its identifier.
    with pytest.raises(HTTPException) as missing:
        await review._device_binding(device_id)
    assert missing.value.status_code == 409
    await action(c, "connect")
    before = {}
    async with c.ctx.migration.connect() as db:
        for table in ("qr_channels", "subscription_devices"):
            before[table] = (await db.execute(text(f"""
                SELECT to_jsonb(x) FROM {table} x WHERE tenant_id=:t
                ORDER BY to_jsonb(x)::text"""), {"t": c.tenant})).scalars().all()
    binding, channel_key = await review._device_binding(device_id)
    assert binding["tenant_id"] == str(c.tenant)
    assert binding["channel_id"] == str(c.row["id"])
    assert binding["device_id"] == c.device["id"]
    assert binding["generation"] == str(c.saved["generation"])
    assert binding["settings_revision"] == c.device["revision"]
    assert binding["owner"] == str(c.bridge.owner)
    assert channel_key == channels._cipher().decrypt(c.row["credential_encrypted"].encode()).decode()
    assert "channel_key" not in binding and "credential" not in binding
    async with c.ctx.migration.connect() as db:
        for table in ("qr_channels", "subscription_devices"):
            after = (await db.execute(text(f"""
                SELECT to_jsonb(x) FROM {table} x WHERE tenant_id=:t
                ORDER BY to_jsonb(x)::text"""), {"t": c.tenant})).scalars().all()
            assert after == before[table], f"Read-only binding changed {table}"
    async with c.ctx.migration.begin() as db:
        await db.execute(text("""UPDATE qr_channels
            SET lease_expires_at=clock_timestamp()-interval '1 second'
            WHERE id=:id"""), {"id": c.row["id"]})
    with pytest.raises(HTTPException) as expired:
        await review._device_binding(device_id)
    assert expired.value.status_code == 409


@pytest.mark.asyncio
async def test_delivery_whatsapp_connection_address_memory_is_contact_scoped(device_case):
    """Real PostgreSQL address book; no transport and no third-party messages."""
    from app import delivery_whatsapp as wa, delivery_customer_addresses as book
    from app.customer_identity import resolve_customer
    from app.tenant_access import TenantAccess
    from app.onboarding_schemas import Address
    c=device_case
    phone="+5567999999902"
    other="+5567999999903"
    address=Address(street="Rua sintética",number="12",neighborhood="Centro",city="Campo Grande",state="MS")
    second=Address(street="Avenida de teste",number="45",neighborhood="Centro",city="Campo Grande",state="MS")
    access=TenantAccess("owner",c.ctx.actor.account_id)
    async with c.ctx.runtime() as db:
        async with tenant_transaction(db,c.tenant):
            customer,created=await resolve_customer(db,c.tenant,phone,"Cliente de teste")
            assert created
            for label, item in (("Casa",address),("Trabalho",second)):
                await book.save_address(db,c.tenant,access,customer["id"],uuid4(),
                    book.AddressEdit(label=label,address=item,expected_revision=0))
    async with c.ctx.runtime() as db:
        async with tenant_transaction(db,c.tenant):
            state={"customer_name":"Cliente de teste"}
            assert await wa.recover_saved_address(db,c.tenant,phone,state,("label","casa"))==address.model_dump(mode="json")
            assert await wa.recover_saved_address(db,c.tenant,phone,state,("label","trabalho"))==second.model_dump(mode="json")
            assert await wa.recover_saved_address(db,c.tenant,other,state,("label","casa")) is None
            assert await wa.recover_saved_address(db,c.tenant,phone,{"customer_name":"Pessoa diferente"},("label","casa")) is None
            assert await wa.recover_saved_address(db,c.tenant,phone,state,("label","inexistente")) is None
            assert "primeiro confirme" in await wa.save_last_delivery_address(db,c.tenant,phone,{}, "Outro")
            assert len((await book.list_addresses(db,c.tenant,customer["id"]))["items"])==2


@pytest.mark.asyncio
async def test_delivery_whatsapp_connection_save_confirmed_delivery_address(device_case, monkeypatch):
    """The complete confirmed Delivery checkout and address book, on disposable DB."""
    from app import delivery_whatsapp as wa, delivery_customer_addresses as book
    from app import delivery_catalog as catalog, delivery_storefront as store, delivery_logistics as logistics
    from app.customer_identity import resolve_customer
    from app.tenant_access import TenantAccess
    from app.onboarding_schemas import Address
    from test_delivery_catalog_postgres import new_product, complete_delivery_setup
    c=device_case
    phone="+5567999999902"
    other="+5567999999903"
    address=Address(street="Rua sintética",number="12",neighborhood="Centro",city="Campo Grande",state="MS")
    second=Address(street="Avenida de teste",number="45",neighborhood="Centro",city="Campo Grande",state="MS")
    access=TenantAccess("owner",c.ctx.actor.account_id)
    async with c.ctx.migration.begin() as db:
        await db.execute(text("""UPDATE business_onboarding SET configuration=configuration ||
            '{"niche":"delivery_distribuicao","delivery_offering":"gas_agua","activity":"products","modality":"online"}'::jsonb
            WHERE tenant_id=:t"""),{"t":c.tenant})
    monkeypatch.setenv("ORION_DELIVERY_PREVIEW_TENANTS",str(c.tenant))
    monkeypatch.setenv("PUBLIC_SITE_SIGNING_KEY",uuid4().hex+uuid4().hex)
    await complete_delivery_setup(c.ctx,c.tenant)
    async with c.ctx.runtime() as db:
        async with tenant_transaction(db,c.tenant):
            customer,created=await resolve_customer(db,c.tenant,phone,"Cliente de teste")
            assert created
            await book.save_address(db,c.tenant,access,customer["id"],uuid4(),
                book.AddressEdit(label="Trabalho",address=second,expected_revision=0))
            await logistics.save_policy(db,c.tenant,access,logistics.PolicyUpdate(
                expected_revision=0,coverage_mode="areas",areas=[
                    logistics.Area(city="Campo Grande",state="MS",neighborhood="Centro",fee_cents=450)]))
            item=await catalog.create_item(db,c.tenant,access,catalog.ProductCreate.model_validate(new_product("Água mineral 20 L")))
            rows=(await store.operator_items(db,c.tenant,"",0))["items"]
            selected=next(x for x in rows if str(x["variant_id"])==str(item["variant_id"]))
            await store.save_store(db,c.tenant,access,store.Storefront(
                revision=0,slug="memory-"+uuid4().hex[:12],title="Distribuidora sintética",enabled=False,
                allow_delivery=True,allow_pickup=True,pickup_details="Depósito",payment_methods=["cash"],
                items=[store.PublicItem(variant_id=selected["variant_id"],product_revision=selected["product_revision"],
                    variant_revision=selected["variant_revision"],label="Água mineral 20 L",kind="water")]))

    async def talk(body,state,delivered=False):
        async with c.ctx.runtime() as db:
            async with tenant_transaction(db,c.tenant):
                return await wa.answer(db,c.tenant,phone,body,state,delivered)

    _,state=await talk("Quero uma água para entrega",{})
    assert state["stage"]=="address" and state["customer_name"]=="Cliente de teste"
    _,state=await talk("Rua sintética, 12, Centro, Campo Grande, MS",state)
    assert state["stage"]=="payment"
    review,state=await talk("dinheiro, troco pra 50 reais",state)
    assert "R$ 17,00" in review and "Troco previsto: R$ 33,00" in review
    response,state=await talk("confirmo",state,True)
    assert "registrado" in response and "salvar endereço como Casa" in response and state.get("last_order")
    async with c.ctx.runtime() as db:
        async with tenant_transaction(db,c.tenant):
            assert len((await book.list_addresses(db,c.tenant,customer["id"]))["items"])==1
    response,state=await talk("salvar endereço como Trabalho",state)
    assert "Escolha outro nome" in response
    response,state=await talk("salvar endereço como Casa",state)
    assert "Endereço salvo" in response
    response,state=await talk("salvar endereço como Casa",state)
    assert "já está salvo" in response
    async with c.ctx.runtime() as db:
        async with tenant_transaction(db,c.tenant):
            saved=(await book.list_addresses(db,c.tenant,customer["id"]))["items"]
            assert len(saved)==2
            assert next(r for r in saved if r["label"]=="Trabalho")["address"]["street"]==second.street
            assert await wa.recover_saved_address(db,c.tenant,phone,{"customer_name":"Cliente de teste"},("label","casa"))==address.model_dump(mode="json")
            assert "verificar seu cadastro" in await wa.save_last_delivery_address(db,c.tenant,other,state,"Invasão")
            # The original order retains its address even if a label is edited.
            original=(await db.execute(text("""SELECT address FROM delivery_order_details
                WHERE tenant_id=:t AND customer_id=:c AND intake_channel='whatsapp'"""),
                {"t":c.tenant,"c":customer["id"]})).scalar_one()
            assert original["street"]==address.street
    _,second_state=await talk("Quero uma água para entrega",{})
    assert second_state["stage"]=="address"
    _,second_state=await talk("usar endereço Casa",second_state)
    assert second_state["address"]["street"]==address.street and second_state["stage"]=="payment"


@pytest.mark.asyncio
async def test_delivery_whatsapp_connection_five_contacts_compete_for_last_unit(device_case, monkeypatch):
    """Five independent WhatsApp contacts, real Postgres, one remaining unit."""
    from app import delivery_catalog as catalog, delivery_storefront as store
    from app.tenant_access import TenantAccess
    from test_delivery_catalog_postgres import new_product, complete_delivery_setup
    c=device_case
    async with c.ctx.migration.begin() as db:
        await db.execute(text("""UPDATE business_onboarding SET configuration=configuration ||
            '{"niche":"delivery_distribuicao","delivery_offering":"gas_agua","activity":"products","modality":"online"}'::jsonb
            WHERE tenant_id=:t"""),{"t":c.tenant})
    monkeypatch.setenv("ORION_DELIVERY_PREVIEW_TENANTS",str(c.tenant))
    monkeypatch.setenv("PUBLIC_SITE_SIGNING_KEY",uuid4().hex+uuid4().hex)
    await complete_delivery_setup(c.ctx,c.tenant)
    await create(c)
    assert (await action(c,"connect"))["operation"]["status"]=="succeeded"
    access=TenantAccess("owner",c.ctx.actor.account_id)
    async with c.ctx.runtime() as db:
        async with tenant_transaction(db,c.tenant):
            body=new_product("Água mineral 20 L")
            body["initial_quantity"]=1
            product=await catalog.create_item(db,c.tenant,access,catalog.ProductCreate.model_validate(body))
            selected=next(row for row in (await store.operator_items(db,c.tenant,"",0))["items"]
                          if str(row["variant_id"])==str(product["variant_id"]))
            await store.save_store(db,c.tenant,access,store.Storefront(
                revision=0,slug="race-"+uuid4().hex[:12],title="Distribuidora sintética",enabled=False,
                allow_pickup=True,pickup_details="Depósito",payment_methods=["cash","pix"],
                items=[store.PublicItem(variant_id=selected["variant_id"],product_revision=selected["product_revision"],
                    variant_revision=selected["variant_revision"],label="Água mineral 20 L",kind="water")]))
    config=c.prefix+"/delivery-assistant"
    status=(await c.client.get(config)).json()
    response=await c.client.put(config,json={"enabled":True,"expected_revision":status["revision"]})
    assert response.status_code==200,response.text
    c.device["revision"]=response.json()["revision"]
    contacts=[(f"55679999999{index:02d}",name) for index,name in enumerate(
        ["Alice","Bruno","Carla","Davi","Elisa"],start=11)]

    async def talk(contact,body):
        result=await event(c,phone=contact,text=body,message_id=uuid4().hex)
        effect,=result["effects"]
        grant=await permit(c,effect)
        assert grant["authorized"] and grant["payload"]["phone"]==contact,grant
        await finish(c,effect,"accepted",provider_id=uuid4().hex)
        return grant["payload"]["text"]

    replies=await asyncio.gather(*(talk(phone,"Quero uma água para retirar") for phone,_ in contacts))
    assert all("nome" in reply.lower() for reply in replies)
    replies=await asyncio.gather(*(talk(phone,name) for phone,name in contacts))
    assert all("pagar" in reply.lower() for reply in replies)
    replies=await asyncio.gather(*(talk(phone,"dinheiro, troco pra 50 reais") for phone,_ in contacts))
    for (_,name),reply in zip(contacts,replies):
        assert name in reply and "R$ 12,50" in reply and "R$ 37,50" in reply and "CONFIRMO" in reply
        assert all(other not in reply for _,other in contacts if other!=name)
    async with c.ctx.migration.connect() as db:
        assert await db.scalar(text("SELECT count(*) FROM delivery_order_details WHERE tenant_id=:t"),
                               {"t":c.tenant})==0

    outcomes=await asyncio.gather(*(talk(phone,"confirmo") for phone,_ in contacts))
    winners=[i for i,reply in enumerate(outcomes) if "registrado!*" in reply]
    assert len(winners)==1,outcomes
    winner_phone,winner_name=contacts[winners[0]]
    async with c.ctx.migration.connect() as db:
        rows=(await db.execute(text("""SELECT d.customer_id,q.contact_e164,q.customer_name,q.total_cents,o.status
            FROM delivery_order_details d
            JOIN commercial_orders o ON (o.tenant_id,o.id)=(d.tenant_id,d.order_id)
            JOIN commercial_quotes q ON (q.tenant_id,q.id)=(o.tenant_id,o.quote_id)
            WHERE d.tenant_id=:t"""),{"t":c.tenant})).mappings().all()
        row,=rows
        assert row["contact_e164"]=="+"+winner_phone and row["customer_name"]==winner_name
        assert row["total_cents"]==1250 and row["status"]=="confirmed" and row["customer_id"]
        assert await db.scalar(text("""SELECT count(*) FROM customers WHERE tenant_id=:t"""),{"t":c.tenant})==1
        inventory=(await db.execute(text("""SELECT quantity_on_hand,reserved_quantity FROM commercial_inventory
            WHERE tenant_id=:t AND variant_id=:v"""),{"t":c.tenant,"v":product["variant_id"]})).one()
        assert inventory==(1,1)


@pytest.mark.asyncio
async def test_delivery_whatsapp_connection_multimodal_scoped_media_and_checkout(device_case,monkeypatch):
    """Real broker, migration, encrypted state, media decode and orders; provider is synthetic."""
    import io
    from PIL import Image,ImageDraw
    from app import delivery_catalog as catalog,delivery_storefront as store,delivery_logistics as logistics
    from app import delivery_whatsapp_vision as vision
    from app.tenant_access import TenantAccess
    from test_delivery_catalog_postgres import new_product,complete_delivery_setup
    c=device_case
    async with c.ctx.migration.begin() as db:
        await db.execute(text("""UPDATE business_onboarding SET configuration=configuration ||
            '{"niche":"delivery_distribuicao","delivery_offering":"gas_agua","activity":"products","modality":"online"}'::jsonb
            WHERE tenant_id=:t"""),{"t":c.tenant})
    monkeypatch.setenv("ORION_DELIVERY_PREVIEW_TENANTS",str(c.tenant))
    monkeypatch.setenv("ORION_DELIVERY_VISION_PREVIEW_TENANTS",str(c.tenant))
    monkeypatch.setenv("GROQ_API_KEY","synthetic-test-key")
    monkeypatch.setenv("PUBLIC_SITE_SIGNING_KEY",uuid4().hex+uuid4().hex)
    await complete_delivery_setup(c.ctx,c.tenant)
    await create(c)
    assert (await action(c,"connect"))["operation"]["status"]=="succeeded"
    access=TenantAccess("owner",c.ctx.actor.account_id)
    async with c.ctx.runtime() as db:
        async with tenant_transaction(db,c.tenant):
            await logistics.save_policy(db,c.tenant,access,logistics.PolicyUpdate(
                expected_revision=0,coverage_mode="areas",areas=[
                    logistics.Area(city="Campo Grande",state="MS",neighborhood="Centro",fee_cents=450)]))
            created=await catalog.create_item(db,c.tenant,access,catalog.ProductCreate.model_validate(new_product("Água mineral 20 L")))
            selected=next(row for row in (await store.operator_items(db,c.tenant,"",0))["items"]
                          if str(row["variant_id"])==str(created["variant_id"]))
            await store.save_store(db,c.tenant,access,store.Storefront(
                revision=0,slug="media-"+uuid4().hex[:12],title="Distribuidora sintética",enabled=False,
                allow_delivery=True,allow_pickup=True,pickup_details="Depósito",payment_methods=["cash","pix"],
                items=[store.PublicItem(variant_id=selected["variant_id"],product_revision=selected["product_revision"],
                    variant_revision=selected["variant_revision"],label="Água mineral 20 L",kind="water")]))
    config=c.prefix+"/delivery-assistant"
    status=(await c.client.get(config)).json()
    response=await c.client.put(config,json={"enabled":True,"expected_revision":status["revision"]})
    assert response.status_code==200,response.text
    c.device["revision"]=response.json()["revision"]
    phone="5567999999902"
    other="5567999999903"

    async def talk(body=None, *, contact=phone, **kwargs):
        result=await event(c,phone=contact,message_id=uuid4().hex,text=body,**kwargs)
        if not result["effects"]:
            return None,result
        effect,=result["effects"]
        assert effect["type"]=="send_text",effect
        grant=await permit(c,effect)
        assert grant["authorized"] and grant["payload"]["phone"]==contact
        await finish(c,effect,"accepted",provider_id=uuid4().hex)
        return grant["payload"]["text"],result

    # Calls are replied to by text, once per cooldown, and only for a verified contact.
    call={"kind":"call","call_id":"qa-call1","call_from":phone+"@s.whatsapp.net"}
    notice=await event(c,phone=phone,**call)
    effect,=notice["effects"]
    grant=await permit(c,effect)
    assert grant["authorized"] and "Vi sua ligação" in grant["payload"]["text"]
    await finish(c,effect,"accepted",provider_id=uuid4().hex)
    assert not (await event(c,phone=phone,**{**call,"call_id":"qa-call2"}))["effects"]
    assert not (await event(c,phone=None,**{**call,"call_id":"qa-hidden"}))["effects"]

    # Real PNG decoding before the synthetic vision boundary. One grant, one provider call.
    image=Image.new("RGB",(200,200),"white")
    draw=ImageDraw.Draw(image)
    draw.rectangle((55,40,145,185),fill="lightblue")
    draw.text((80,100),"20 L",fill="black")
    stream=io.BytesIO();image.save(stream,format="PNG");raw=stream.getvalue()
    calls=[]
    async def recognize(jpeg):
        with Image.open(io.BytesIO(jpeg)) as decoded:
            assert decoded.format=="JPEG" and decoded.size==(200,200)
        calls.append("provider")
        return vision.PhotoAnalysis(category="water",volume_ml=20000)
    monkeypatch.setattr(vision,"provider_analysis",recognize)
    media={"kind":"image","size":len(raw),"media_type":"image/png"}
    incoming=await event(c,phone=phone,message_id="qa-photo",delivery_media=media)
    photo,=incoming["effects"]
    assert photo["type"]=="analyze_image"
    assert (await permit(c,photo))["authorized"]
    headers={**c.bridge.headers(c.row,live=True),"Content-Type":"image/png",
        "X-Orion-Phone":phone,"X-Orion-Device-Id":c.device["id"],"X-Orion-Device-Generation":str(c.saved["generation"]),
        "X-Orion-Device-Revision":str(c.device["revision"]),"X-Orion-Device-Effect":photo["operation_id"]}
    endpoint=f"/internal/qr/channels/{c.row['id']}/device/image"
    denied=await c.private.post(endpoint,headers={**headers,"X-Orion-Phone":other},content=raw)
    assert denied.status_code==409 and not calls
    analyzed=await c.private.post(endpoint,headers=headers,content=raw)
    assert analyzed.status_code==200,analyzed.text
    child,=analyzed.json()["effects"]
    grant=await permit(c,child)
    assert grant["authorized"] and "20 L" in grant["payload"]["text"]
    await finish(c,child,"accepted",provider_id=uuid4().hex)
    assert calls==["provider"]
    assert (await c.private.post(endpoint,headers=headers,content=raw)).status_code==409
    duplicate=await event(c,phone=phone,message_id="qa-photo",delivery_media=media)
    assert duplicate["duplicate"] and not duplicate["effects"] and calls==["provider"]
    receipt,_=await talk("Comprovante Pix",delivery_media=media)
    assert "não" in receipt.lower() and "conferido" in receipt and calls==["provider"]

    # The location is part of the order's immutable address snapshot.
    pin={"kind":"location","latitude":-20.123456,"longitude":-55.123456}
    text_reply,_=await talk(delivery_media=pin)
    assert "localização" in text_reply
    await talk("Quero uma água para entrega")
    await talk("Mariana")
    await talk("Rua Teste, 12, Centro, Campo Grande, MS")
    summary,_=await talk("dinheiro, sem troco")
    assert "R$ 17,00" in summary and "-20.123456%2C-55.123456" in summary
    assert "registrado" in (await talk("confirmo"))[0]
    async with c.ctx.migration.connect() as db:
        row=(await db.execute(text("""SELECT address FROM delivery_order_details
            WHERE tenant_id=:t AND intake_channel='whatsapp'"""),{"t":c.tenant})).scalar_one()
        assert row["location"]["kind"]=="location" and row["location"]["latitude"]==-20.123456

    # Independent contact and live updates. The first confirmation after an
    # update is a review, not an automatic second order or destination change.
    await talk("Quero uma água para entrega",contact=other)
    await talk("Alice",contact=other)
    live={"kind":"live_location","source_id":"qa-live","sequence":1,"latitude":-20.2,"longitude":-55.2}
    assert "ponto" in (await talk(contact=other,delivery_media=live))[0]
    await talk("Rua Teste, 34, Centro, Campo Grande, MS",contact=other)
    summary,_=await talk("dinheiro, sem troco",contact=other)
    assert "Último ponto recebido ao vivo" in summary
    updated={**live,"sequence":2,"latitude":-20.3,"update":True}
    assert (await talk(contact=other,delivery_media=updated))[0] is None
    assert (await talk(contact=other,delivery_media={**live,"update":True}))[0] is None
    assert (await talk(contact=other,delivery_media={**updated,"source_id":"other-live"}))[0] is None
    review,_=await talk("confirmo",contact=other)
    assert "-20.300000%2C-55.200000" in review
    async with c.ctx.migration.connect() as db:
        assert await db.scalar(text("SELECT count(*) FROM delivery_order_details WHERE tenant_id=:t"),{"t":c.tenant})==1
    assert "registrado" in (await talk("confirmo",contact=other))[0]
    assert (await talk(contact=other,delivery_media={**updated,"sequence":3,"latitude":-20.4}))[0] is None
    async with c.ctx.migration.connect() as db:
        rows=(await db.execute(text("""SELECT address FROM delivery_order_details
            WHERE tenant_id=:t AND intake_channel='whatsapp'"""),{"t":c.tenant})).scalars().all()
        assert {a["location"]["latitude"] for a in rows}=={-20.123456,-20.3}
        assert await db.scalar(text("""SELECT count(*) FROM subscription_device_effects
            WHERE tenant_id=:t AND type='analyze_image' AND image_started_at IS NOT NULL"""),{"t":c.tenant})==1
