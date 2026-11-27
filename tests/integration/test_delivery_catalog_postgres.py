"""Exercise the authorized delivery API against the real Postgres role and RLS."""
import asyncio
from datetime import date, timedelta
import os
from pathlib import Path
import re
import socket
import subprocess
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import FastAPI
import httpx
import pytest
from sqlalchemy import text

from app.catalog_api import Actor, authenticate
from app.delivery_api import router
from app.onboarding_schemas import CreateBusiness
from app.onboarding_service import create_business
from app.plan_permissions import resolve_plan
from test_onboarding_postgres import onboarding_db  # noqa: F401


pytestmark = pytest.mark.integration


async def business(ctx, monkeypatch):
    actor = ctx.new_actor
    state = await create_business(actor, CreateBusiness(
        name="Distribuidora de avaliação", timezone="America/Cuiaba", niche="delivery_distribuicao",
        activity="products", delivery_offering="distribuidora", contact_phone="67999999901",
        idempotency_key=uuid4().hex,
    ))
    tenant = UUID(state["tenant_id"])
    assert (await resolve_plan(tenant)).get("delivery_catalog_preview") is None
    monkeypatch.setenv("ORION_DELIVERY_PREVIEW_TENANTS", str(tenant))
    assert (await resolve_plan(tenant))["delivery_catalog_preview"] is True
    return tenant, actor


def client(actor):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[authenticate] = lambda: actor
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://orion.test")


def new_product(name="Água de avaliação"):
    return {"product": {"name": name, "synonyms": ["Água de teste"], "active": True},
            "variant": {"name": "Padrão", "sku": "agua-teste", "price_kind": "fixed", "unit_price_cents": 1250, "active": True},
            "details": {"category": "Água", "barcode": "000123", "purchase_cost_cents": 625, "minimum_quantity": 3},
            "initial_quantity": 5, "idempotency_key": uuid4().hex}


async def complete_delivery_setup(ctx, tenant):
    async with ctx.migration.begin() as db:
        await db.execute(text("UPDATE tenants SET status='active' WHERE id=:t"), {"t":tenant})
        await db.execute(text("""
            UPDATE business_onboarding SET completed_at=now(),
                configuration=configuration || '{"modality":"online"}'::jsonb WHERE tenant_id=:t
        """), {"t":tenant})


async def test_catalog_and_ledger_roundtrip(onboarding_db, monkeypatch):
    tenant, actor = await business(onboarding_db, monkeypatch)
    root = f"/api/v1/tenants/{tenant}/delivery/catalog"
    payload = new_product()
    async with client(actor) as http:
        assert (await http.get(root)).json()["items"] == []
        created = await http.post(root, json=payload)
        assert created.status_code == 201, created.text
        assert (await http.post(root, json=payload)).json() == created.json()
        assert (await http.post(root, json={**payload, "initial_quantity": 8})).status_code == 409
        duplicate = new_product("Outro produto")
        duplicate["variant"]["sku"] = "outro"
        assert (await http.post(root, json=duplicate)).status_code == 409
        row = (await http.get(root, params={"search":"000123", "category":"Água"})).json()["items"][0]
        assert row["quantity_on_hand"] == 5 and row["purchase_cost_cents"] == 625
        assert row["synonyms"] == ["Água de teste"]
        assert len((await http.get(root)).json()["items"]) == 1
        url = root + f"/variants/{row['variant_id']}/adjustments"
        out = {"movement":"out", "quantity":2, "expected_revision":row["inventory_revision"], "idempotency_key":uuid4().hex}
        adjusted = await http.post(url, json=out)
        assert adjusted.status_code == 200, adjusted.text
        assert adjusted.json()["quantity_on_hand"] == 3
        assert (await http.post(url, json=out)).json() == adjusted.json()
        assert (await http.post(url, json={**out, "idempotency_key":uuid4().hex})).status_code == 409
        assert len((await http.get(root, params={"low_stock":True})).json()["items"]) == 1
        history = (await http.get(root + "/history")).json()["items"]
        assert [(x["details"]["movement"],x["details"]["after"]) for x in history] == [("out",3),("initial",5)]
        assert (await http.get(root + "/history", params={"start":"2000-01-01", "end":"2000-01-02"})).json()["items"] == []
        assert (await http.get(root + "/history", params={"start":"2000-01-03", "end":"2000-01-02"})).status_code == 422
        edit = {"product":{**payload["product"],"name":"Água atualizada", "active":False},
                "variant":payload["variant"], "details":payload["details"],
                "product_revision":row["product_revision"],"variant_revision":row["variant_revision"]}
        edit_url = root + f"/variants/{row['variant_id']}"
        assert (await http.put(edit_url,json=edit)).status_code == 200
        assert (await http.put(edit_url,json=edit)).status_code == 409
        assert (await http.get(root,params={"state":"active"})).json()["items"] == []
        assert (await http.get(root,params={"state":"inactive"})).json()["items"][0]["name"] == "Água atualizada"


async def test_concurrent_adjustments_preserve_reservations_and_tenant_scope(onboarding_db, monkeypatch):
    ctx = onboarding_db
    tenant, actor = await business(ctx, monkeypatch)
    root = f"/api/v1/tenants/{tenant}/delivery/catalog"
    async with client(actor) as http:
        created = (await http.post(root, json=new_product())).json()
        variant = UUID(created["variant_id"])
        async with ctx.migration.begin() as db:
            await db.execute(text("UPDATE commercial_inventory SET reserved_quantity=2 WHERE tenant_id=:t AND variant_id=:v"), {"t":tenant,"v":variant})
        row = (await http.get(root)).json()["items"][0]
        route = root+f"/variants/{variant}/adjustments"
        args = {"movement":"count", "expected_revision":row["inventory_revision"], "quantity":1}
        assert (await http.post(route, json={**args,"idempotency_key":uuid4().hex})).status_code == 409
        first, second = await asyncio.gather(*[
            http.post(route, json={**args,"quantity":qty,"idempotency_key":uuid4().hex}) for qty in (2,3)
        ])
        assert sorted([first.status_code, second.status_code]) == [200,409]
        assert (await http.get(root)).json()["items"][0]["reserved_quantity"] == 2
    async with client(Actor("member", account_id=ctx.other)) as outsider:
        assert (await outsider.get(root)).status_code == 403
    async with ctx.migration.begin() as db:
        await db.execute(text("INSERT INTO tenant_memberships(tenant_id,account_id,role) VALUES(:t,:a,'viewer')"), {"t":tenant,"a":ctx.other})
    async with client(Actor("member", account_id=ctx.other)) as viewer:
        assert (await viewer.get(root)).status_code == 200
        assert (await viewer.post(root,json=new_product())).status_code == 403
    async with client(ctx.actor) as legacy:
        assert (await legacy.get(f"/api/v1/tenants/{ctx.a}/delivery/catalog")).status_code == 403
    monkeypatch.delenv("ORION_DELIVERY_PREVIEW_TENANTS")
    async with client(actor) as disabled:
        assert (await disabled.get(root)).status_code == 403


async def test_product_profile_and_channel_flags_are_tenant_scoped(onboarding_db, monkeypatch):
    tenant, actor = await business(onboarding_db, monkeypatch)
    await complete_delivery_setup(onboarding_db, tenant)
    entitlement = await resolve_plan(tenant)
    assert entitlement["source"] == "none"
    assert entitlement["permissions"]["clients.manage"] and entitlement["permissions"]["finance.basic"]
    assert not entitlement["permissions"]["reception.ai"]
    base = f"/api/v1/tenants/{tenant}/delivery"
    root = base + "/catalog"
    payload = new_product()
    payload["profile"] = {"brand": "Marca de homologação", "preset_image": "agua-20l"}
    flags = {"sell_internally": True, "allow_storefront": False, "allow_delivery": False}
    old_details = payload["details"].copy()
    payload["details"].update(flags)
    async with client(actor) as http:
        remote_photo = {"photo_url": f"/media/{onboarding_db.a}/{uuid4()}.webp"}
        assert (await http.post(root, json={**payload, "profile": remote_photo})).status_code == 422
        created = await http.post(root, json=payload)
        assert created.status_code == 201, created.text
        variant = created.json()["variant_id"]
        assert (await http.get(root, params={"sale_mode":"delivery"})).json()["items"] == []
        items = (await http.get(root, params={"sale_mode":"counter", "search":"Marca de homologação"})).json()["items"]
        row, = items
        assert row["brand"] == "Marca de homologação"
        assert row["image_url"].endswith("/agua-20l.webp")
        update = {"product": payload["product"], "variant": payload["variant"], "details": old_details,
                  "product_revision": row["product_revision"], "variant_revision": row["variant_revision"]}
        edited = await http.put(root+f"/variants/{variant}", json=update)
        assert edited.status_code == 200, edited.text
        preserved = (await http.get(root)).json()["items"][0]
        assert {key: preserved[key] for key in flags} == flags
        assert preserved["preset_image"] == "agua-20l"
        delivery = {"mode": "delivery", "items": [{"variant_id": variant, "quantity": 1}],
                    "address": {"street":"Rua de homologação", "number":"2","neighborhood":"Centro","city":"Cuiabá","state":"MT"}}
        denied = await http.post(base+"/orders/estimate", json=delivery)
        assert denied.status_code == 409, denied.text


async def test_catalog_in_real_panel_at_required_widths(onboarding_db, monkeypatch, tmp_path):
    if os.getenv("ORION_DELIVERY_BROWSER_TEST") != "1":
        pytest.skip("Requires an isolated Redis and a Chromium browser")
    import uvicorn
    from argon2 import PasswordHasher
    from playwright.async_api import async_playwright, expect
    from app.main import app

    ctx = onboarding_db
    redis = urlsplit(os.environ["REDIS_URL"])
    assert redis.hostname in {"127.0.0.1", "localhost"} and redis.path == "/15"
    tenant, actor = await business(ctx, monkeypatch)
    password = uuid4().hex
    email = f"onboarding-{actor.account_id}@example.test"
    monkeypatch.setenv("PANEL_ADMIN_PASSWORD_HASH", "disabled-in-browser-fixture")
    async with ctx.migration.begin() as db:
        await db.execute(text("UPDATE panel_accounts SET verified_at=now(),password_hash=:password WHERE id=:a"),
                         {"a":actor.account_id,"password":PasswordHasher().hash(password)})
        await db.execute(text("INSERT INTO customers(tenant_id,name) VALUES(:t,'Cliente de homologação')"), {"t":tenant})
    await complete_delivery_setup(ctx, tenant)

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    # Match the production transport for the isolated loopback browser fixture.
    key, cert = tmp_path/"localhost-key.pem", tmp_path/"localhost-cert.pem"
    subprocess.run(["openssl","req","-x509","-newkey","rsa:2048","-nodes","-days","1",
                    "-subj","/CN=127.0.0.1","-addext","subjectAltName=IP:127.0.0.1",
                    "-keyout",str(key),"-out",str(cert)], check=True, capture_output=True)
    origin = f"https://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False,
                                          ssl_keyfile=str(key), ssl_certfile=str(cert)))
    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    widths = (320, 375, 390, 430, 768, 1024, 1280, 1440, 1920)
    artifacts = Path(os.getenv("DELIVERY_EVIDENCE_DIR", "artifacts/delivery-browser"))
    artifacts.mkdir(parents=True, exist_ok=True)
    errors = []

    async def assert_delivery_layout(page, label):
        # Width alone misses intersecting controls in a responsive grid.
        issues = await page.evaluate("""async () => {
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          const failures = [];
          const roots = [...document.querySelectorAll('#page-suite[data-os-current-route$="-delivery"], #osDialog.os-delivery-dialog[open]')];
          const visible = el => !!el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
          const rect = el => el.getBoundingClientRect();
          const name = el => el.id || el.getAttribute('name') || el.textContent.trim().slice(0, 40);
          for (const root of roots) {
            for (const grid of root.querySelectorAll('.os-filter-row,.os-form-grid,.os-pill-tabs,.os-delivery-hours-row,.os-delivery-hours-date,.os-delivery-dashboard-metrics,.os-delivery-dashboard-grid,.os-delivery-dashboard-dock,.os-delivery-dashboard-toolbar')) {
              if (!visible(grid) || grid.closest('.os-table-scroll')) continue;
              const items = [...grid.children].filter(visible);
              const bounds = rect(grid);
              for (let i = 0; i < items.length; i++) {
                const a = rect(items[i]);
                if (a.left < bounds.left - 2 || a.right > bounds.right + 2)
                  failures.push('Outside grid: ' + name(items[i]));
                for (let j = i + 1; j < items.length; j++) {
                  const b = rect(items[j]);
                  if (Math.min(a.right,b.right)-Math.max(a.left,b.left)>2 &&
                      Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)>2)
                    failures.push('Overlap: ' + name(items[i]) + ' / ' + name(items[j]));
                }
              }
            }
            for (const el of root.querySelectorAll('.os-field input:not([type=hidden]):not([type=checkbox]):not([type=radio]),.os-field select,.os-field textarea')) {
              if (!visible(el) || el.closest('.os-table-scroll')) continue;
              const a = rect(el), b = rect(el.closest('.os-field'));
              if (a.left < b.left - 2 || a.right > b.right + 2)
                failures.push('Field outside wrapper: ' + name(el));
            }
          }
          if (document.documentElement.scrollWidth > innerWidth + 2) failures.push('Page overflow');
          return failures;
        }""")
        assert not issues, (label, issues)
    try:
        async with asyncio.timeout(20):
            while not server.started:
                if server_task.done():
                    await server_task
                    pytest.fail("Orion preview did not start")
                await asyncio.sleep(0.05)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            for theme in ("light", "dark", "system"):
                context = await browser.new_context(locale="pt-BR", color_scheme="dark" if theme=="system" else theme,
                                                    service_workers="block", ignore_https_errors=True)

                async def safe_route(route):
                    if urlsplit(route.request.url).netloc == urlsplit(origin).netloc:
                        await route.continue_()
                    else:
                        await route.abort()

                await context.route("**/*", safe_route)
                await context.add_init_script(f'localStorage.setItem("orion-theme", "{theme}");')
                login = await context.request.post(origin+"/auth/login", data={"username":email,"password":password})
                assert login.status==200, await login.text()
                page = await context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                await page.goto(origin+"/painel?aba=produtos-delivery")
                await expect(page.get_by_role("heading", name="Produtos e estoque")).to_be_visible()
                await expect(page.locator("html")).to_have_attribute("data-theme", "light" if theme=="light" else "dark")
                if theme == "light":
                    await page.set_viewport_size({"width":390,"height":844})
                    await page.get_by_role("button", name="Novo produto").click()
                    product_form = page.locator("#osDeliveryProductForm")
                    await product_form.get_by_label("Preencher com um modelo").select_option("agua-20l")
                    await expect(product_form.get_by_label("Nome do produto")).to_have_value("Água mineral 20 L")
                    await product_form.get_by_label("Nome do produto").fill("Água de homologação")
                    await product_form.get_by_label("Preço de venda").fill("12,50")
                    await product_form.get_by_label("Estoque inicial").fill("5")
                    await product_form.get_by_role("button",name="Salvar produto").click()
                    await expect(product_form).to_be_hidden()
                    await page.get_by_role("button",name="Ajustar estoque").click()
                    dialog = page.get_by_role("dialog")
                    await dialog.get_by_label("Quantidade").fill("2")
                    await dialog.get_by_role("button",name="Salvar").click()
                    await expect(page.get_by_text("7 un em estoque",exact=False)).to_be_visible()
                for width in widths:
                    await page.set_viewport_size({"width":width,"height":900})
                    await expect(page.get_by_role("heading",name="Água de homologação")).to_be_visible()
                    await assert_delivery_layout(page, (theme,width))
                    await page.get_by_role("button",name="Ajustar estoque").click()
                    dialog = page.get_by_role("dialog")
                    await expect(dialog).to_be_visible()
                    bounds = await dialog.bounding_box()
                    assert bounds and bounds["x"] >= 0 and bounds["x"]+bounds["width"] <= width+1, (theme,width,bounds)
                    await assert_delivery_layout(page, ("stock dialog",theme,width))
                    await page.keyboard.press("Escape")
                    await expect(dialog).to_be_hidden()
                    if width in (390,1440):
                        await page.screenshot(path=artifacts/f"catalogo-{theme}-{width}.png", animations="disabled")
                        await page.get_by_role("button",name="Editar",exact=True).click()
                        await expect(page.get_by_role("heading",name="Editar produto",exact=True)).to_be_visible()
                        await expect(page.locator(".os-delivery-current-image")).to_have_attribute("src",re.compile("agua-20l.webp$"))
                        await assert_delivery_layout(page, (theme,width))
                        await page.screenshot(path=artifacts/f"produto-editor-{theme}-{width}.png", animations="disabled")
                        await page.get_by_role("button",name="Voltar para produtos",exact=True).click()
                        await expect(page.get_by_role("heading",name="Água de homologação")).to_be_visible()
                await page.get_by_role("tab", name="Movimentações").click()
                await expect(page.get_by_role("cell", name="Saldo inicial")).to_be_visible()
                await expect(page.get_by_role("cell", name="Entrada", exact=True)).to_be_visible()
                if theme == "dark":
                    await page.goto(origin+"/painel?aba=dashboard-delivery")
                    await page.set_viewport_size({"width":390,"height":844})
                    await expect(page.get_by_text("Nenhum pedido para esta data.")).to_be_visible()
                    await page.get_by_role("button", name="Notificações", exact=True).click()
                    notifications = page.get_by_role("region", name="Notificações")
                    await expect(notifications.get_by_text("Sem notificações", exact=True)).to_be_visible()
                    await expect(notifications).not_to_contain_text("agendamentos")
                    await notifications.get_by_role("button", name="Fechar notificações").click()
                    day = page.get_by_label("Data", exact=True)
                    today = await day.input_value()
                    yesterday = (date.fromisoformat(today)-timedelta(days=1)).isoformat()
                    await day.fill(yesterday)
                    await expect(page.get_by_role("button", name="Hoje", exact=True)).to_be_visible()
                    await expect(page.get_by_text("Nenhum pedido para esta data.")).to_be_visible()
                    dock = page.get_by_role("navigation", name="Atalhos de Delivery")
                    await dock.get_by_role("button", name="Novo pedido", exact=True).click()
                    dialog = page.get_by_role("dialog")
                    await dialog.get_by_role("button",name="Buscar produtos").click()
                    await dialog.get_by_role("button",name="Água de homologação · Unidade").click()
                    await dialog.get_by_role("button",name="Conferir total").click()
                    await expect(dialog.get_by_text("Total",exact=True)).to_be_visible()
                    await page.screenshot(path=artifacts/"pedido-dark-390.png", animations="disabled")
                    await dialog.get_by_role("button",name="Confirmar pedido").click()
                    await expect(dialog).to_be_hidden()
                    await expect(day).to_have_value(today)
                    order_on_dashboard = page.locator('[data-delivery-section="orders"] [data-delivery-dashboard-order]')
                    await expect(order_on_dashboard).to_contain_text("Reservado")
                    await order_on_dashboard.click()
                    await expect(dialog.get_by_text(re.compile("Água de homologação"))).to_be_visible()
                    await page.keyboard.press("Escape")
                    await expect(dialog).to_be_hidden()
                    await page.get_by_role("button", name="Ver pedidos", exact=True).click()
                    await expect(page.get_by_role("heading", name="Pedidos", exact=True)).to_be_visible()
                    order_filters = page.locator("#osDeliveryOrderFilters")
                    await expect(order_filters.get_by_label("De", exact=True)).to_have_value(today)
                    await expect(order_filters.get_by_label("Até", exact=True)).to_have_value(today)
                    orders = page.locator("#osDeliveryOrderList")
                    await expect(orders.get_by_text("Reservado",exact=True)).to_be_visible()
                    await page.get_by_role("button",name="Concluir",exact=True).click()
                    await dialog.get_by_label("Forma de pagamento").select_option("cash")
                    await dialog.get_by_label("Confirmo que recebi o pagamento").check()
                    await dialog.get_by_role("button",name="Concluir pedido").click()
                    await expect(dialog).to_be_hidden()
                    await expect(orders.get_by_text("Concluído",exact=True)).to_be_visible()
                    await page.goto(origin+"/painel?aba=dashboard-delivery")
                    await page.get_by_role("button", name="Ver entregas", exact=True).click()
                    await expect(page.get_by_role("heading", name="Entregas", exact=True)).to_be_visible()
                    await expect(order_filters.get_by_label("Entrega de", exact=True)).to_have_value(today)
                    await expect(order_filters.get_by_label("Entrega até", exact=True)).to_have_value(today)
                    await page.get_by_role("button", name="Novo pedido").click()
                    await dialog.get_by_role("button", name="Novo cliente", exact=True).click()
                    await dialog.get_by_label("Nome do cliente").fill("Cliente novo de avaliação")
                    await dialog.get_by_role("button", name="Salvar cliente").click()
                    await expect(dialog.get_by_text("Cliente: Cliente novo de avaliação", exact=True)).to_be_visible()
                    await dialog.get_by_role("button", name="Buscar produtos").click()
                    await dialog.get_by_role("button", name="Água de homologação · Unidade").click()
                    await dialog.get_by_label("Rua", exact=True).fill("Rua de homologação")
                    await dialog.get_by_label("Número", exact=True).fill("31")
                    await dialog.get_by_label("Bairro", exact=True).fill("Centro")
                    await dialog.get_by_label("Cidade", exact=True).fill("Campo Grande")
                    await dialog.get_by_label("UF", exact=True).select_option("MS")
                    await dialog.get_by_label("Taxa de entrega").fill("5,00")
                    await dialog.get_by_role("button", name="Conferir total").click()
                    await expect(dialog.get_by_text("Total", exact=True)).to_be_visible()
                    await dialog.get_by_role("button", name="Confirmar pedido").click()
                    await expect(dialog).to_be_hidden()
                    await page.get_by_role("button", name="Despachar", exact=True).click()
                    await dialog.get_by_label("Entregador", exact=True).fill("Entregador de avaliação")
                    await dialog.get_by_role("button", name="Despachar pedido").click()
                    await expect(dialog).to_be_hidden()
                    await expect(orders.get_by_text("Em rota", exact=True)).to_be_visible()
                    await page.screenshot(path=artifacts/"entregas-dark-390.png", animations="disabled")
                    await page.get_by_role("button", name="Concluir", exact=True).click()
                    await dialog.get_by_label("Forma de pagamento").select_option("on_account")
                    await dialog.get_by_label("Confirmo que o valor ficará a receber").check()
                    await dialog.get_by_role("button", name="Concluir pedido").click()
                    await expect(dialog).to_be_hidden()
                    await expect(orders.get_by_text("A receber", exact=True)).to_be_visible()
                    await page.goto(origin+"/painel?aba=dashboard-delivery")
                    credit = page.locator('[data-delivery-section="credit"]')
                    await expect(credit.get_by_text("Cliente novo de avaliação", exact=True)).to_be_visible()
                    await expect(credit.get_by_text(re.compile(r"17,50"))).to_be_visible()
                    await page.get_by_text("Valores por dia", exact=True).click()
                    weekly_values = page.locator(".os-delivery-dashboard-values tbody tr")
                    await expect(weekly_values).to_have_count(7)
                    await expect(weekly_values.last).to_contain_text(re.compile(r"30,00.*12,50"))
                    await page.get_by_role("button", name="Ver saldos", exact=True).click()
                    await page.get_by_role("button", name="Registrar recebimento", exact=True).click()
                    await dialog.get_by_label("Forma de pagamento").select_option("cash")
                    await dialog.get_by_label("Confirmo que recebi o pagamento").check()
                    await dialog.get_by_role("button", name="Registrar recebimento", exact=True).click()
                    await expect(dialog).to_be_hidden()
                    await expect(orders.get_by_text("Nenhuma venda a prazo encontrada", exact=True)).to_be_visible()
                    await order_filters.get_by_label("Status", exact=True).select_option("paid")
                    await order_filters.get_by_role("button",name="Filtrar",exact=True).click()
                    await expect(orders.get_by_text("Recebido", exact=True)).to_be_visible()
                    await page.goto(origin+"/painel?aba=relatorio-delivery")
                    await expect(page.get_by_role("heading", name="Relatório de vendas", exact=True)).to_be_visible()
                    await page.get_by_label("Recebimento", exact=True).select_option("paid")
                    await page.get_by_role("button", name="Filtrar", exact=True).click()
                    await expect(page.get_by_role("link", name="Exportar CSV")).to_have_attribute("href", re.compile("receipt=paid"))
                    async with page.expect_download() as download:
                        await page.get_by_role("link", name="Exportar CSV").click()
                    assert (await download.value).suggested_filename == "orion-delivery-vendas.csv"
                    await page.goto(origin+"/painel?aba=entregas-delivery")
                    await page.get_by_role("button", name="Novo pedido").click()
                    await dialog.get_by_label("Buscar cliente").fill("Cliente novo")
                    await dialog.get_by_role("button", name="Buscar clientes").click()
                    await dialog.get_by_role("button", name="Cliente novo de avaliação").click()
                    await dialog.get_by_role("button", name="Usar último endereço").click()
                    await expect(dialog.get_by_label("Rua", exact=True)).to_have_value("Rua de homologação")
                    await page.keyboard.press("Escape")
                    await expect(dialog).to_be_hidden()
                    await page.get_by_role("button", name="Registrar devolução", exact=True).click()
                    await dialog.get_by_label("Confirmo que todos os itens voltaram ao estoque").check()
                    await dialog.get_by_label("Confirmo que já devolvi o valor ao cliente").check()
                    await dialog.get_by_role("button", name="Registrar devolução", exact=True).click()
                    await expect(dialog).to_be_hidden()
                    await expect(orders.get_by_text("Estornado", exact=True)).to_be_visible()
                    await page.goto(origin+"/painel?aba=vasilhames-delivery")
                    await page.get_by_role("button", name="Novo vasilhame").click()
                    await dialog.get_by_label("Tipo").select_option("jug")
                    await dialog.get_by_label("Nome do vasilhame").fill("Garrafão 20 L de avaliação")
                    await dialog.get_by_label("Saldo inicial de vazios").fill("2")
                    await dialog.get_by_role("button", name="Salvar").click()
                    await expect(dialog).to_be_hidden()
                    await page.get_by_role("button", name="Movimentar").click()
                    await dialog.get_by_label("Movimento").select_option("lend")
                    await dialog.get_by_label("Buscar cliente").fill("homologação")
                    await dialog.get_by_role("button", name="Buscar clientes").click()
                    await dialog.get_by_role("button", name="Cliente de homologação").click()
                    await dialog.get_by_label("Quantidade").fill("1")
                    await dialog.get_by_role("button", name="Salvar").click()
                    await expect(dialog).to_be_hidden()
                    client_balances = page.locator("#osContainerResults .os-item-card .os-summary-row").nth(1).locator("strong")
                    await expect(client_balances).to_have_text("1")
                    await page.get_by_role("button", name="Com clientes", exact=True).click()
                    await expect(page.get_by_text("Cliente de homologação", exact=True)).to_be_visible()
                    await page.screenshot(path=artifacts/"vasilhames-clientes-dark-390.png", animations="disabled")
                    await page.get_by_role("button", name="Devolver", exact=True).click()
                    await dialog.get_by_role("button", name="Salvar").click()
                    await expect(dialog).to_be_hidden()
                    await expect(client_balances).to_have_text("0")
                    await page.get_by_role("button", name="Histórico", exact=True).click()
                    await expect(page.get_by_role("cell", name="Devolução", exact=True)).to_be_visible()
                panels = (
                    ("dashboard-delivery", "Dashboard", "heading", "Pedidos do dia"),
                    ("clientes", "Clientes", "button", "Novo cliente"),
                    ("financeiro", "Financeiro", None, "Ticket médio"),
                    ("pedidos-delivery", "Pedidos", "button", "Novo pedido"),
                    ("entregas-delivery", "Entregas", "button", "Novo pedido"),
                    ("vasilhames-delivery", "Vasilhames", "button", "Novo vasilhame"),
                    ("vendas-prazo-delivery", "Vendas a prazo", "button", "Ver pedidos"),
                    ("relatorio-delivery", "Relatório de vendas", "link", "Exportar CSV"),
                    ("logistica-delivery", "Logística", "heading", "Cobertura e taxas"),
                    ("loja-delivery", "Loja rápida", "heading", "Produtos na vitrine"),
                )
                for route, heading, ready_role, ready_name in panels:
                    await page.goto(origin+"/painel?aba="+route)
                    await expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible()
                    ready = (page.get_by_role(ready_role, name=ready_name, exact=True) if ready_role
                             else page.get_by_text(ready_name, exact=True))
                    await expect(ready).to_be_visible()
                    for width in widths:
                        await page.set_viewport_size({"width":width,"height":900})
                        await assert_delivery_layout(page, (route,theme,width))
                        if route == "dashboard-delivery":
                            shortcuts = page.get_by_role("navigation", name="Atalhos de Delivery")
                            if width < 768:
                                await expect(shortcuts).to_be_visible()
                                assert (await shortcuts.bounding_box())["y"] > 810
                            else:
                                await expect(shortcuts).to_be_hidden()
                            await expect(page.get_by_role("button", name="Novo pedido", exact=True)).to_be_visible()
                        if width in (390,1440):
                            await page.screenshot(path=artifacts/f"{route}-{theme}-{width}.png", animations="disabled")
                    if route == "clientes":
                        await expect(page.locator("#osCustomersCount")).to_contain_text("ativo")
                        await expect(page.get_by_role("tab",name="Fixos",exact=True)).to_have_count(0)
                        await expect(page.get_by_role("tab",name="Em risco",exact=True)).to_have_count(0)
                        await page.get_by_role("tab",name="Inativos",exact=True).click()
                        await expect(page.locator("#osPageBody")).to_contain_text("Nenhum cliente inativo")
                        await page.get_by_role("tab",name="Ativos",exact=True).click()
                        await page.get_by_role("button",name="Novo cliente",exact=True).click()
                        await expect(page.locator("#osDialog [data-os-form=customer]")).to_be_visible()
                        await page.keyboard.press("Escape")
                        await expect(page.get_by_role("dialog")).to_be_hidden()
                    if route == "financeiro":
                        await expect(page.locator(".os-finance-metrics")).to_be_visible()
                        await page.get_by_role("button",name="Novo lançamento",exact=True).click()
                        await expect(page.locator("#osDialog [data-os-form=finance]")).to_be_visible()
                        await page.locator("#osDialog .os-close").click()
                    if route == "relatorio-delivery":
                        await page.set_viewport_size({"width":390,"height":844})
                        for tab in ("Vendas","Produtos","Clientes","Estoque","Movimentações","A receber","Vasilhames","Atividades"):
                            await page.get_by_role("tab",name=tab,exact=True).click()
                            await expect(page.locator("#osDeliveryReportPanel a[download]")).to_be_visible()
                            await assert_delivery_layout(page, (route,tab,theme,390))
                    if route == "loja-delivery":
                        await page.get_by_role("button",name="Configurar loja",exact=True).click()
                        settings = page.locator("#osDeliveryShopSettings")
                        await expect(settings.get_by_role("heading",name="Identificação e marca",exact=True)).to_be_visible()
                        if theme == "light":
                            await settings.get_by_label("Nome da loja",exact=True).fill("Loja de homologação")
                            await settings.get_by_label("Mensagem quando fechada").fill("Voltamos no próximo horário.")
                            await settings.get_by_role("button",name="Salvar configurações",exact=True).click()
                            await expect(page.get_by_role("heading",name="Loja de homologação",exact=True)).to_be_visible()
                            async with context.expect_page() as open_preview:
                                await page.get_by_role("link",name="Prévia da loja",exact=True).click()
                            preview = await open_preview.value
                            await expect(preview.get_by_role("heading",name="Loja de homologação",exact=True)).to_be_visible()
                            await expect(preview.get_by_text("Prévia da loja",exact=True)).to_be_visible()
                            await expect(preview.get_by_role("button",name="Continuar pedido")).to_have_count(0)
                            await preview.close()
                        for width in (390,1440):
                            await page.set_viewport_size({"width":width,"height":900})
                            await assert_delivery_layout(page, (route,theme,width))
                            await page.screenshot(path=artifacts/f"loja-configuracoes-{theme}-{width}.png", animations="disabled")
                await context.close()
            await browser.close()
            if os.getenv("ORION_DELIVERY_WEBKIT_TEST") == "1":
                webkit = await playwright.webkit.launch()
                context = await webkit.new_context(locale="pt-BR", color_scheme="dark",
                    viewport={"width":390,"height":844}, is_mobile=True, has_touch=True,
                    ignore_https_errors=True)
                await context.route("**/*", safe_route)
                await context.add_init_script('localStorage.setItem("orion-theme", "dark");')
                login = await context.request.post(origin+"/auth/login", data={"username":email,"password":password})
                assert login.status == 200
                page = await context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                for route, heading, ready_role, ready_name in (
                    ("produtos-delivery","Produtos e estoque","button","Novo produto"), *panels):
                    await page.goto(origin+"/painel?aba="+route)
                    await expect(page.get_by_role("heading",name=heading,exact=True)).to_be_visible()
                    ready = (page.get_by_role(ready_role,name=ready_name,exact=True) if ready_role
                             else page.get_by_text(ready_name,exact=True))
                    await expect(ready).to_be_visible()
                    for width in (320,375,390,430,768,1280):
                        await page.set_viewport_size({"width":width,"height":900})
                        await assert_delivery_layout(page, ("WebKit",route,width))
                    await page.set_viewport_size({"width":390,"height":844})
                    await page.screenshot(path=artifacts/f"webkit-{route}-390.png", animations="disabled")
                    if route == "pedidos-delivery":
                        await page.get_by_role("button",name="Novo pedido",exact=True).click()
                        await expect(page.locator("#osDialog")).to_be_visible()
                        await assert_delivery_layout(page, ("WebKit",route,"new order",390))
                        await page.locator("#osDialog .os-close").click()
                    if route == "loja-delivery":
                        await page.get_by_role("button",name="Configurar loja",exact=True).click()
                        await expect(page.locator("#osDeliveryShopSettings")).to_be_visible()
                        await assert_delivery_layout(page, ("WebKit","store settings",390))
                await context.close()
                await webkit.close()
        assert errors == []
    finally:
        server.should_exit = True
        await asyncio.wait_for(server_task, timeout=10)
        listener.close()
