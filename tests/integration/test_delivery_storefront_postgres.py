"""Verify opt-in store visibility, delivery quotes and atomic checkout in Postgres."""

import asyncio
from datetime import datetime, timedelta
import os
from pathlib import Path
import socket
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from app import delivery_address, delivery_api, delivery_storefront as storefront
from app.catalog_api import Actor, authenticate
from app.delivery_api import router as delivery_router
from test_delivery_catalog_postgres import business, complete_delivery_setup, new_product
from test_onboarding_postgres import onboarding_db  # noqa: F401


pytestmark = pytest.mark.integration


def client(actor):
    app = FastAPI()
    app.include_router(delivery_router)
    app.include_router(storefront.router)
    app.dependency_overrides[authenticate] = lambda: actor
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://orion.test")


@pytest.fixture
async def storefront_setup(onboarding_db, monkeypatch):
    tenant, actor = await business(onboarding_db, monkeypatch)
    await complete_delivery_setup(onboarding_db, tenant)
    monkeypatch.setenv("ORION_DELIVERY_PUBLIC_TENANTS", str(tenant))
    monkeypatch.setenv("PUBLIC_SITE_SIGNING_KEY", "synthetic-storefront-test-signing-key-" + uuid4().hex)

    async def no_remote_rate_limit(*args, **kwargs):
        pass

    # This suite tests real data and locks. The Redis limiter is exercised by
    # the public booking suite and the public page browser test when available.
    monkeypatch.setattr(storefront, "public_limit", no_remote_rate_limit)
    return onboarding_db, tenant, actor


async def set_up_items(http, tenant, slug):
    admin = f"/api/v1/tenants/{tenant}/delivery"
    for i, product in enumerate(("Água de avaliação", "Estoque privado", "Botijão GLP P-13")):
        body = new_product(product)
        if i == 0:
            body["profile"] = {"brand": "Marca sintética", "preset_image": "agua-20l"}
        if i:
            body["variant"]["sku"] += str(i)
            body["details"]["barcode"] += str(i)
        if i == 2:
            body["details"]["category"] = "GLP"
        result = await http.post(admin + "/catalog", json=body)
        assert result.status_code == 201, result.text
    result = await http.get(admin + "/storefront/items")
    assert result.status_code == 200, result.text
    items = {row["name"]: row for row in result.json()["items"]}

    def listing(key):
        return {**{field: items[key][field] for field in ("variant_id", "product_revision", "variant_revision")},
                "label": key, "kind": "water"}

    return admin, {
        "revision": 0, "slug": slug, "title": "Loja sintética & Cia", "allow_pickup": True,
        "pickup_details": "Rua do Teste, 12", "payment_methods": ["cash"],
        "enabled": True, "confirm_catalogue": True,
        "items": [listing("Água de avaliação")],
    }, listing("Botijão GLP P-13"), listing("Estoque privado")


async def test_owner_publication_filters_draft_and_restricted_products(storefront_setup, monkeypatch):
    ctx, tenant, actor = storefront_setup
    slug = "loja-teste-" + uuid4().hex[:8]
    public = "/api/v1/delivery-store/" + slug
    async with client(actor) as http:
        admin, payload, restricted, hidden = await set_up_items(http, tenant, slug)
        mutation = admin + "/storefront"
        assert (await http.get(public)).status_code == 404
        async with ctx.migration.begin() as db:
            await db.execute(text("INSERT INTO tenant_memberships(tenant_id,account_id,role) VALUES(:t,:a,'editor')"),
                             {"t": tenant, "a": ctx.other})
        async with client(Actor("member", account_id=ctx.other)) as editor:
            assert (await editor.put(mutation, json=payload)).status_code == 403
        monkeypatch.delenv("ORION_DELIVERY_PUBLIC_TENANTS")
        assert (await http.put(mutation, json=payload)).status_code == 403
        draft = await http.put(mutation, json={**payload, "enabled": False})
        assert draft.status_code == 200, draft.text
        payload["revision"] = draft.json()["revision"]
        preview = draft.json()["preview_url"]
        assert (await http.get(preview)).status_code == 200
        assert (await http.get(preview + "/catalogo")).json()["items"][0]["image_url"].endswith("agua-20l.webp")
        async with client(Actor("member", account_id=ctx.other)) as editor:
            assert (await editor.get(preview + "/catalogo")).status_code == 200
        async with client(Actor("member", account_id=uuid4())) as outsider:
            forbidden = await outsider.get(preview + "/catalogo")
            assert forbidden.status_code == 403, forbidden.text
        assert (await http.get("/delivery-store-assets/products/agua-20l.webp")).status_code == 200
        assert (await http.get("/delivery-store-assets/products/invalid.webp")).status_code == 404
        monkeypatch.setenv("ORION_DELIVERY_PUBLIC_TENANTS", str(tenant))
        assert (await http.put(mutation, json={**payload, "items": [restricted]})).status_code == 422
        approved = await http.put(mutation, json=payload)
        assert approved.status_code == 200, approved.text
        assert approved.json()["public_url"].endswith("/loja/" + slug)
        assert (await http.put(mutation, json=payload)).status_code == 409
        view = (await http.get(public)).json()
        item, = view["items"]
        assert item["variant_id"] == payload["items"][0]["variant_id"] and item["label"] == "Água de avaliação"
        assert item["kind"] == "water" and item["unit_price_cents"] == 1250
        assert item["available"] and item["available_quantity"] == 5
        assert item["image_url"].endswith("agua-20l.webp") and item["brand"] == "Marca sintética"
        assert "tenant_id" not in view and "max_open_orders" not in view
        page = await http.get("/loja/" + slug)
        assert page.status_code == 200 and "Loja sintética &amp; Cia" in page.text
        assert page.headers["cache-control"] == "no-store"
        hidden_quote = {"items": [{"variant_id": hidden["variant_id"], "quantity": 1}], "mode": "pickup", "payment_preference": "cash"}
        assert (await http.post(public + "/quote", json=hidden_quote)).status_code == 409
        closed = await http.post(mutation + "/unpublish", json={"revision": approved.json()["revision"]})
        assert closed.status_code == 200 and closed.json()["enabled"] is False
        assert (await http.get(public)).status_code == 404


async def test_checkout_reserves_only_once_without_claiming_payment_or_crm_identity(storefront_setup):
    ctx, tenant, actor = storefront_setup
    slug = "pedidos-" + uuid4().hex[:10]
    public = "/api/v1/delivery-store/" + slug
    async with client(actor) as http:
        admin, payload, _, _ = await set_up_items(http, tenant, slug)
        approved = await http.put(admin + "/storefront", json={**payload, "max_open_orders": 1})
        assert approved.status_code == 200, approved.text
        line = {"variant_id": payload["items"][0]["variant_id"], "quantity": 2}
        intent = {"items": [line], "mode": "pickup", "payment_preference": "cash", "change_for_cents": 3000}
        assert (await http.post(public+"/quote", json={**intent,"change_for_cents":2400})).status_code == 422
        quote = await http.post(public + "/quote", json=intent)
        assert quote.status_code == 200, quote.text
        assert quote.json()["change_cents"] == 500
        confirm = {**intent, "customer_name": "Pessoa sintética", "customer_phone": "+5567999999902",
                   "buyer_note": "Pedido de homologação",
                   "confirmed": True, "quote": quote.json()["quote"], "idempotency_key": str(uuid4())}
        tampered = {**confirm, "items": [{**line, "quantity": 1}]}
        assert (await http.post(public + "/orders", json=tampered)).status_code == 409
        one, two = await asyncio.gather(*(http.post(public + "/orders", json=confirm) for _ in range(2)))
        assert one.status_code == 201 and two.status_code == 201, (one.text, two.text)
        assert one.json() == two.json()
        order = one.json()
        new_quote = await http.post(public + "/quote", json=intent)
        assert new_quote.status_code == 200, new_quote.text
        overflow = await http.post(public + "/orders", json={**confirm, "quote": new_quote.json()["quote"],
                                                            "idempotency_key": str(uuid4())})
        assert overflow.status_code == 429
        progress = await http.post(public + "/status", json={"tracking_code": order["tracking_code"]})
        assert progress.json() == {"order_code": order["order_code"], "status": "confirmed",
                                   "total_cents": 2500, "payment_status": "not_verified"}
        assert (await http.post(public + "/status", json={"tracking_code": "X" * 43})).status_code == 404
        stock = (await http.get(admin + "/catalog", params={"search": "Água de avaliação"})).json()["items"][0]
        assert stock["quantity_on_hand"] == 5 and stock["reserved_quantity"] == 2
        orders = (await http.get(admin + "/orders")).json()["items"]
        assert len(orders) == 1 and orders[0]["customer_id"] is None
        order_path = admin + "/orders/" + orders[0]["id"]
        contact = await http.get(admin + "/storefront/orders/" + orders[0]["id"])
        assert contact.json() == {"customer_name": "Pessoa sintética", "contact_e164": "+5567999999902",
                                  "payment_preference": "cash","change_for_cents":3000,"buyer_note":"Pedido de homologação"}
        async with client(Actor("member", account_id=ctx.other)) as outsider:
            assert (await outsider.get(admin + "/storefront/orders/" + orders[0]["id"])).status_code == 403
        async with ctx.migration.begin() as db:
            assert not (await db.execute(text("SELECT 1 FROM customers WHERE tenant_id=:t"), {"t": tenant})).first()
            assert not (await db.execute(text("SELECT 1 FROM financial_entries WHERE tenant_id=:t"), {"t": tenant})).first()
        done = await http.post(order_path + "/transitions", json={"action": "fulfill", "expected_revision": 1,
                                                                 "payment_method": "cash", "idempotency_key": uuid4().hex})
        assert done.status_code == 200, done.text
        update = await http.post(public + "/status", json={"tracking_code": order["tracking_code"]})
        assert update.json()["payment_status"] == "received"
        finished = (await http.get(order_path)).json()
        assert finished["customer_id"] and finished["items"][0]["image_url"].endswith("agua-20l.webp")


async def test_public_delivery_uses_current_coverage_and_server_price(storefront_setup, monkeypatch):
    ctx, tenant, actor = storefront_setup
    slug = "entrega-" + uuid4().hex[:10]
    public = "/api/v1/delivery-store/" + slug
    lookups = []

    def provider(request):
        lookups.append(request.url.path)
        assert request.url.host == "viacep.com.br"
        if request.url.path == "/ws/99999999/json/":
            return httpx.Response(200, json={"erro":True})
        return httpx.Response(200,json={"cep":"78005-000","logradouro":"Rua de teste",
                                        "bairro":"Centro","localidade":"Cuiabá","uf":"MT"})

    async def local_limit(*_args, **_kwargs):
        pass

    monkeypatch.setattr(delivery_api,"public_limit",local_limit)
    monkeypatch.setattr(delivery_address,"AsyncClient",lambda **opts: httpx.AsyncClient(
        transport=httpx.MockTransport(provider),**opts))
    async with client(actor) as http:
        admin, payload, _, _ = await set_up_items(http, tenant, slug)
        settings = {**payload, "allow_delivery": True, "allow_pickup": False}
        assert (await http.put(admin + "/storefront", json=settings)).status_code == 422
        area = {"city": "Cuiabá", "state": "MT", "neighborhood": "Centro", "fee_cents": 450}
        policy = {"expected_revision": 0, "coverage_mode": "areas", "areas": [area]}
        saved = await http.put(admin + "/logistics", json=policy)
        assert saved.status_code == 200, saved.text
        live = await http.put(admin + "/storefront", json=settings)
        assert live.status_code == 200, live.text
        postcode = (await http.get(public+"/cep/78005000")).json()
        assert postcode == {"postal_code":"78005-000","street":"Rua de teste","neighborhood":"Centro","city":"Cuiabá","state":"MT"}
        assert (await http.get(admin+"/cep/78005-000")).json() == postcode
        assert (await http.get(public+"/cep/99999999")).status_code == 404
        assert (await http.get(public+"/cep/invalido")).status_code == 422
        async with client(Actor("member",account_id=ctx.other)) as outsider:
            assert (await outsider.get(admin+"/cep/78005000")).status_code == 403
        assert lookups == ["/ws/78005000/json/","/ws/78005000/json/","/ws/99999999/json/"]
        address = {"street": "Rua de teste", "number": "12", "state": "MT", "city": "Cuiaba", "neighborhood": "Centro"}
        intent = {"items": [{"variant_id": payload["items"][0]["variant_id"], "quantity": 2}],
                  "mode": "delivery", "address": address, "payment_preference": "cash"}
        unavailable = {**intent, "address": {**address, "neighborhood": "Fora da área"}}
        assert (await http.post(public + "/quote", json=unavailable)).status_code == 422
        quote = (await http.post(public + "/quote", json=intent)).json()
        assert quote["delivery_fee_cents"] == 450 and quote["total_cents"] == 2950
        receipt = {**intent, "customer_name": "Pessoa sintética", "customer_phone": "+5567999999902", "confirmed": True,
                   "quote": quote["quote"], "idempotency_key": str(uuid4())}
        policy["expected_revision"] = saved.json()["revision"]
        area["fee_cents"] = 500
        changed = await http.put(admin + "/logistics", json=policy)
        assert changed.status_code == 200, changed.text
        assert (await http.post(public + "/orders", json=receipt)).status_code == 409
        revision = (await http.post(public + "/quote", json=intent)).json()
        created = await http.post(public + "/orders", json={**receipt, "quote": revision["quote"]})
        assert created.status_code == 201, created.text
        async with ctx.migration.begin() as db:
            routed = (await db.execute(text("SELECT delivery_fee_cents,logistics_snapshot FROM delivery_order_details WHERE tenant_id=:t"),
                                       {"t": tenant})).mappings().one()
        assert routed["delivery_fee_cents"] == 500 and routed["logistics_snapshot"]["area"]["neighborhood"] == "Centro"


async def test_store_pause_minimum_and_exchange_flow(storefront_setup):
    ctx, tenant, actor = storefront_setup
    slug = "troca-" + uuid4().hex[:12]
    public = "/api/v1/delivery-store/" + slug
    admin = f"/api/v1/tenants/{tenant}/delivery"
    async with client(actor) as http:
        packaging = await http.post(admin+"/containers", json={
            "name":"Garrafão de teste 20 L","kind":"jug","quantity":0,"idempotency_key":uuid4().hex,
        })
        assert packaging.status_code == 201, packaging.text
        container = packaging.json()["id"]
        product = new_product()
        product["variant"]["name"] = "Com troca"
        product["details"]["exchange_container_id"] = container
        created = await http.post(admin+"/catalog", json=product)
        assert created.status_code == 201, created.text
        candidate = (await http.get(admin+"/storefront/items")).json()["items"][0]
        listing = {**{key:candidate[key] for key in ("variant_id","product_revision","variant_revision")},
                   "label":"Água com troca","kind":"water"}
        store = {"revision":0,"slug":slug,"title":"Loja de troca","pickup_details":"Balcão de avaliação",
                 "enabled":True,"confirm_catalogue":True,"accepting_orders":False,"minimum_subtotal_cents":2000,
                 "items":[listing],"payment_methods":["cash"],"allow_delivery":True}
        now = storefront.commerce.now_utc()
        local = now.astimezone(storefront.ZoneInfo("America/Cuiaba"))
        day = local.date() + timedelta(days=1)
        another_day = day + timedelta(days=1)
        policy = {"expected_revision":0,"coverage_mode":"areas",
                  "areas":[{"city":"Cuiabá","state":"MT","neighborhood":"Centro","fee_cents":450}],
                  "scheduling_enabled":True,"windows":[
                      {"weekday":when.weekday(),"start":"09:00","end":"10:00"} for when in (day,another_day)]}
        response = await http.put(admin+"/logistics", json=policy)
        assert response.status_code==200, response.text
        store["closed_dates"]=[day.isoformat()]
        saved = await http.put(admin+"/storefront", json=store)
        assert saved.status_code == 200, saved.text
        assert saved.json()["public_url"] and not saved.json()["open"]
        intent = {"mode":"pickup","payment_preference":"cash","items":[{"variant_id":listing["variant_id"],"quantity":2}]}
        assert (await http.post(public+"/quote", json=intent)).status_code == 409
        store["revision"]=saved.json()["revision"]
        saved = await http.put(admin+"/storefront", json={**store,"accepting_orders":True,"replace_items":False})
        assert saved.status_code == 200, saved.text
        view = (await http.get(public)).json()
        assert view["items"][0]["requires_exchange"]
        assert [datetime.fromisoformat(slot["when"]).astimezone(local.tzinfo).date()
                for slot in view["schedule_options"]] == [another_day,another_day]
        assert (await http.post(public+"/quote", json=intent)).status_code == 422
        below_minimum = {**intent,"accept_exchange":True,"items":[{**intent["items"][0],"quantity":1}]}
        assert (await http.post(public+"/quote", json=below_minimum)).status_code == 422
        intent["accept_exchange"]=True
        quote = await http.post(public+"/quote", json=intent)
        assert quote.status_code == 200, quote.text
        confirmed = await http.post(public+"/orders", json={
            **intent,"quote":quote.json()["quote"],"confirmed":True,"customer_name":"Pessoa sintética",
            "customer_phone":"67999999904","idempotency_key":str(uuid4()),
        })
        assert confirmed.status_code == 201, confirmed.text
        order, = (await http.get(admin+"/orders")).json()["items"]
        target = admin+"/orders/"+order["id"]
        details = (await http.get(target)).json()
        exchange, = details["container_plan"]
        assert exchange["container_id"] == container and exchange["exchange_quantity"] == 2
        action = {"action":"fulfill","expected_revision":1,"payment_method":"cash","idempotency_key":uuid4().hex}
        assert (await http.post(target+"/transitions", json=action)).status_code == 422
        done = await http.post(target+"/transitions", json={**action,"containers_confirmed":True})
        assert done.status_code == 200, done.text
        row, = (await http.get(admin+"/containers")).json()["items"]
        assert row["empty_quantity"] == 2
        async with ctx.migration.begin() as db:
            stock = (await db.execute(text("SELECT quantity_on_hand,reserved_quantity FROM commercial_inventory WHERE tenant_id=:t"),
                                      {"t":tenant})).one()
        assert tuple(stock) == (3,0)


@pytest.mark.skipif(os.getenv("ORION_DELIVERY_BROWSER_TEST") != "1", reason="Requires isolated Redis and Chromium")
async def test_public_store_at_required_widths_and_checkout_in_browser(storefront_setup, monkeypatch):
    import uvicorn
    from playwright.async_api import async_playwright, expect
    from app.main import app
    from app.public_site_api import public_limit

    redis = urlsplit(os.environ["REDIS_URL"])
    assert redis.hostname in {"127.0.0.1", "localhost"} and redis.path == "/15"
    monkeypatch.setattr(storefront, "public_limit", public_limit)
    _, tenant, actor = storefront_setup
    slug = "loja-browser-" + uuid4().hex[:10]
    async with client(actor) as http:
        admin, payload, _, _ = await set_up_items(http, tenant, slug)
        saved = await http.put(admin + "/storefront", json={**payload,"brand_color":"#14514c"})
        assert saved.status_code == 200, saved.text

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    errors = []
    try:
        async with asyncio.timeout(20):
            while not server.started:
                if task.done():
                    await task
                    pytest.fail("A aplicação de homologação não iniciou")
                await asyncio.sleep(.05)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            context = await browser.new_context(locale="pt-BR", color_scheme="light", service_workers="block")

            async def local_only(route):
                if urlsplit(route.request.url).netloc == urlsplit(origin).netloc:
                    await route.continue_()
                else:
                    await route.abort()

            await context.route("**/*", local_only)
            page = await context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.goto(origin + "/loja/" + slug)
            await expect(page.get_by_role("heading", name="Água de avaliação")).to_be_visible()
            await expect(page.locator(".store-product-photo")).to_have_attribute("src","/delivery-store-assets/products/agua-20l.webp")
            await page.locator(".store-product-photo").evaluate("(image) => image.decode()")
            evidence = Path(os.getenv("DELIVERY_EVIDENCE_DIR", "artifacts/delivery-browser"))
            evidence.mkdir(parents=True, exist_ok=True)
            for width in (320, 375, 390, 430, 768, 1024, 1280, 1440, 1920):
                await page.set_viewport_size({"width": width, "height": 900})
                assert await page.evaluate("() => document.documentElement.scrollWidth <= innerWidth + 1"), width
                if width in {390, 1440}:
                    await page.screenshot(path=evidence / f"loja-delivery-{width}.png", animations="disabled")
            await page.set_viewport_size({"width": 390, "height": 844})
            await page.get_by_role("button", name="Adicionar Água de avaliação", exact=True).click()
            await page.get_by_role("link", name="Ver pedido").click()
            await page.get_by_role("button", name="Continuar pedido").click()
            await page.get_by_label("Nome", exact=True).fill("Pessoa sintética")
            await page.get_by_label("Telefone com DDD").fill("67999999903")
            await page.get_by_label("Preciso de troco").check()
            await page.get_by_label("Troco para").fill("20,00")
            await page.get_by_label("Observação para a loja").fill("Deixar na recepção")
            await page.get_by_role("button", name="Conferir valor").click()
            await expect(page.get_by_text("Total no recebimento")).to_be_visible()
            await expect(page.get_by_text("Troco previsto:")).to_be_visible()
            await page.get_by_role("button", name="Confirmar pedido").click()
            await expect(page.get_by_role("heading", name="Pedido recebido")).to_be_visible()
            await page.get_by_role("dialog").get_by_role("button", name="Acompanhar pedido").click()
            await expect(page.get_by_text("Pagamento: A confirmar pela loja", exact=False)).to_be_visible()
            await page.get_by_role("dialog").get_by_role("button", name="Fechar").click()
            await page.get_by_role("button", name="Ativar modo escuro", exact=False).click()
            await expect(page.locator("html")).to_have_attribute("data-theme", "dark")
            await page.screenshot(path=evidence / "loja-delivery-390-escuro.png", animations="disabled")
            await browser.close()
        assert not errors
    finally:
        server.should_exit = True
        await task
        listener.close()
