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


async def business(ctx, monkeypatch, name="Distribuidora de avaliação"):
    actor = ctx.new_actor
    state = await create_business(actor, CreateBusiness(
        name=name, timezone="America/Cuiaba", niche="delivery_distribuicao",
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


async def test_supplier_receipt_retries_and_updates_stock_and_payable_atomically(onboarding_db, monkeypatch):
    ctx = onboarding_db
    tenant, actor = await business(ctx, monkeypatch)
    await complete_delivery_setup(ctx, tenant)
    root = f"/api/v1/tenants/{tenant}/delivery"
    async with client(actor) as http:
        vendor = {"name":"Fornecedor de avaliação","brands":"Mineral","idempotency_key":uuid4().hex}
        response = await http.post(root+"/suppliers",json=vendor)
        assert response.status_code == 201, response.text
        supplier = response.json()["id"]
        assert (await http.post(root+"/suppliers",json=vendor)).json()["id"] == supplier
        record = (await http.get(root+"/suppliers",params={"search":"mineral"})).json()["items"][0]
        edit = {"name":record["name"],"expected_revision":1,"active":False}
        assert (await http.put(root+"/suppliers/"+supplier,json=edit)).status_code == 200
        assert (await http.put(root+"/suppliers/"+supplier,json=edit)).status_code == 409
        variant = (await http.post(root+"/catalog",json=new_product())).json()["variant_id"]
        line = {"variant_id":variant,"quantity":4,"unit_cost_cents":325}
        receipt = {"reference":"Entrada 1","supplier_id":supplier,"items":[line],"due_on":"2027-01-15",
                   "idempotency_key":uuid4().hex}
        assert (await http.post(root+"/purchases",json=receipt)).status_code == 404
        assert (await http.put(root+"/suppliers/"+supplier,json={**edit,"active":True,"expected_revision":2})).status_code == 200
        bad = {**receipt,"items":[line,{**line,"variant_id":"ffffffff-ffff-4fff-8fff-ffffffffffff"}]}
        assert (await http.post(root+"/purchases",json=bad)).status_code == 404
        assert (await http.get(root+"/purchases")).json()["items"] == []
        assert (await http.get(root+"/catalog")).json()["items"][0]["quantity_on_hand"] == 5
        one,two = await asyncio.gather(http.post(root+"/purchases",json=receipt),http.post(root+"/purchases",json=receipt))
        assert one.status_code == two.status_code == 201, (one.text,two.text)
        assert one.json() == two.json() and one.json()["amount_cents"] == 1300
        duplicate = {**receipt,"reference":"entrada 1","idempotency_key":uuid4().hex}
        assert (await http.post(root+"/purchases",json=duplicate)).status_code == 409
        assert (await http.get(root+"/catalog")).json()["items"][0]["quantity_on_hand"] == 9
        history = (await http.get(root+"/catalog/history")).json()["items"]
        assert history[0]["details"]["purchase_id"] == one.json()["id"]
        purchase = (await http.get(root+"/purchases",params={"search":"Entrada 1"})).json()["items"][0]
        assert purchase["payment_status"] == "pending" and purchase["due_on"] == "2027-01-15"
        assert purchase["items"][0]["unit_cost_cents"] == 325
        async with ctx.migration.begin() as db:
            rows = (await db.execute(text("SELECT amount_cents,status,kind FROM financial_entries WHERE tenant_id=:t"),
                                     {"t":tenant})).all()
            assert rows == [(1300,"pending","expense")]
            await db.execute(text("INSERT INTO tenant_memberships(tenant_id,account_id,role) VALUES(:t,:a,'viewer')"),
                             {"t":tenant,"a":ctx.other})
        from app.management_models import update_row
        from app.database import tenant_transaction
        from fastapi import HTTPException
        entry = UUID(one.json()["financial_entry_id"])
        async with ctx.runtime() as db:
            async with tenant_transaction(db,tenant):
                with pytest.raises(HTTPException) as denied:
                    await update_row(db,tenant,"financial_entries",entry,1,{"amount_cents":1})
                assert denied.value.status_code == 409
                await update_row(db,tenant,"financial_entries",entry,1,{"status":"paid","payment_method":"pix"})
        assert (await http.get(root+"/purchases")).json()["items"][0]["payment_status"] == "paid"
    async with client(Actor("member",account_id=ctx.other)) as viewer:
        assert (await viewer.get(root+"/purchases")).status_code == 403
        assert (await viewer.post(root+"/purchases",json=receipt)).status_code == 403
    async with client(ctx.actor) as outsider:
        assert (await outsider.get(root+"/suppliers")).status_code == 403


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
    from test_delivery_orders_postgres import order_payload

    ctx = onboarding_db
    redis = urlsplit(os.environ["REDIS_URL"])
    assert redis.hostname in {"127.0.0.1", "localhost"} and redis.path == "/15"
    company_name = "Distribuidora de Água e Gás São José Comércio e Distribuição de Bebidas e Produtos para toda a Região Metropolitana"
    tenant, actor = await business(ctx, monkeypatch, name=company_name)
    password = uuid4().hex
    driver_password = uuid4().hex
    driver_email = f"onboarding-{ctx.other}@example.test"
    email = f"onboarding-{actor.account_id}@example.test"
    monkeypatch.setenv("PANEL_ADMIN_PASSWORD_HASH", "disabled-in-browser-fixture")
    async with ctx.migration.begin() as db:
        await db.execute(text("UPDATE panel_accounts SET verified_at=now(),password_hash=:password WHERE id=:a"),
                         {"a":actor.account_id,"password":PasswordHasher().hash(password)})
        customer = await db.scalar(text(
            "INSERT INTO customers(tenant_id,name) VALUES(:t,'Cliente de homologação') RETURNING id"
        ), {"t": tenant})
        await db.execute(text("UPDATE panel_accounts SET verified_at=now(),password_hash=:password WHERE id=:a"),
                         {"a":ctx.other,"password":PasswordHasher().hash(driver_password)})
    await complete_delivery_setup(ctx, tenant)

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    # Match the production transport for the isolated loopback browser fixture.
    key, cert = tmp_path/"localhost-key.pem", tmp_path/"localhost-cert.pem"
    subprocess.run(["openssl","req","-x509","-newkey","rsa:2048","-nodes","-days","1",
                    "-subj","/CN=127.0.0.1","-addext","subjectAltName=IP:127.0.0.1",
                    "-keyout",str(key),"-out",str(cert)], check=True, capture_output=True)
    origin = f"https://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(
        app, log_level="warning", access_log=False, ssl_keyfile=str(key), ssl_certfile=str(cert),
        timeout_graceful_shutdown=5,
    ))
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
            for (const form of root.querySelectorAll('.od-compact-filters')) {
              if (!visible(form)) continue;
              const bounds = rect(form), style = getComputedStyle(form);
              const left = bounds.left + parseFloat(style.borderLeftWidth) + parseFloat(style.paddingLeft);
              const right = bounds.right - parseFloat(style.borderRightWidth) - parseFloat(style.paddingRight);
              const full = (el,label) => {
                if(!el || !visible(el)) { failures.push('Missing compact filter: '+label); return; }
                const r = rect(el);
                if(Math.abs(r.left-left)>2 || Math.abs(r.right-right)>2)
                  failures.push('Compact filter not aligned: '+label+' '+[r.left,r.right,left,right].join(','));
              };
              const search = form.querySelector('.od-search-line'), advanced = form.querySelector('.od-advanced');
              const input = search?.querySelector('input[type=search]'), button = search?.querySelector('button[type=submit]');
              const summary = advanced?.querySelector('summary'), filterIcon = summary?.querySelector('[data-delivery-filter-icon]');
              full(search,'search row'); full(advanced,'advanced section'); full(summary,'advanced summary');
              if(input&&button) {
                const a=rect(input), b=rect(button);
                if(Math.abs(a.left-left)>2 || Math.abs(b.right-right)>2 ||
                    Math.abs(a.top-b.top)>2 || Math.abs(a.bottom-b.bottom)>2 ||
                    a.right>b.left+1 || a.width<60)
                  failures.push('Misaligned input or filter button');
              }else failures.push('Missing search input or button');
              if(!filterIcon||filterIcon.querySelector('path')?.getAttribute('d')!=='M3 5h18M6 12h12M10 19h4')
                failures.push('Incorrect filter icon');
              else if(Math.abs(rect(filterIcon).left-left)>2)
                failures.push('Filter icon not aligned to the left');
            }
            for (const grid of root.querySelectorAll('.os-filter-row,.os-form-grid,.os-pill-tabs,.os-delivery-hours-row,.os-delivery-hours-date,.os-delivery-dashboard-metrics,.os-delivery-dashboard-grid,.os-delivery-dashboard-dock,.os-delivery-dashboard-toolbar')) {
              if (!visible(grid) || grid.closest('.os-table-scroll')) continue;
              const items = [...grid.children].filter(visible);
              const bounds = rect(grid);
              // These named tab strips intentionally scroll horizontally. Test
              // every tab's reachability, not just the currently clipped view.
              const strip = grid.classList.contains('od-tab-strip');
              const scrollable = strip && ['auto','scroll'].includes(getComputedStyle(grid).overflowX);
              if (scrollable) {
                const initial = grid.scrollLeft;
                const left = bounds.left + grid.clientLeft, right = left + grid.clientWidth;
                for (const item of items) {
                  const a = rect(item);
                  if (a.width > grid.clientWidth + 2) failures.push('Oversized tab: ' + name(item));
                  grid.scrollLeft += a.left - left;
                  const reached = rect(item);
                  if (reached.left < left - 2 || reached.right > right + 2)
                    failures.push('Unreachable tab: ' + name(item));
                }
                grid.scrollLeft = initial;
              }
              for (let i = 0; i < items.length; i++) {
                const a = rect(items[i]);
                if (!scrollable && (a.left < bounds.left - 2 || a.right > bounds.right + 2))
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
                    await product_form.locator('input[name="allow_storefront"]').check()
                    await product_form.get_by_role("button",name="Salvar produto").click()
                    await expect(product_form).to_be_hidden()
                    await page.get_by_role("button",name="Ajustar estoque").click()
                    dialog = page.get_by_role("dialog")
                    await dialog.get_by_label("Quantidade").fill("2")
                    await dialog.get_by_role("button",name="Salvar").click()
                    await expect(page.locator(".os-delivery-stock-grid > div").first).to_contain_text("7")
                for width in widths:
                    await page.set_viewport_size({"width":width,"height":900})
                    await expect(page.get_by_role("heading",name="Água de homologação")).to_be_visible()
                    await assert_delivery_layout(page, (theme,width))
                    advanced = page.locator(".od-advanced")
                    was_open = await advanced.evaluate("(el) => el.open")
                    await advanced.locator("summary").click()
                    await assert_delivery_layout(page, ("catalog filters",theme,width,"expanded/collapsed"))
                    await advanced.locator("summary").click()
                    assert await advanced.evaluate("(el) => el.open")==was_open
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
                    await expect(page.get_by_role("navigation", name="Atalhos de Delivery")).to_have_count(0)
                    await page.get_by_role("button", name="Novo pedido", exact=True).click()
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
                    await page.get_by_label("Período do desempenho").select_option("today")
                    await page.get_by_text("Valores por hora", exact=True).click()
                    await expect(weekly_values).to_have_count(24)
                    await page.get_by_text("Comparar indicadores", exact=True).click()
                    await expect(page.get_by_role("row",name=re.compile("Ticket médio"))).to_be_visible()
                    await page.get_by_role("button", name="Ver saldos", exact=True).click()
                    await page.get_by_role("button", name="Registrar recebimento", exact=True).click()
                    await dialog.get_by_label("Forma de pagamento").select_option("cash")
                    await dialog.get_by_label("Confirmo que recebi o pagamento").check()
                    await dialog.get_by_role("button", name="Registrar recebimento", exact=True).click()
                    await expect(dialog).to_be_hidden()
                    await expect(orders.get_by_text("Nenhuma venda a prazo encontrada", exact=True)).to_be_visible()
                    if not await order_filters.locator(".od-advanced").evaluate("(el) => el.open"):
                        await order_filters.locator(".od-advanced summary").click()
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
                    await page.get_by_text("Mais ações",exact=True).first.click()
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
                    client_balances = page.locator("#osContainerResults .od-vessel-stock > div").nth(1).locator("strong")
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
                    ("dashboard-delivery", company_name, "heading", "Pedidos do dia"),
                    ("clientes", "Clientes", "button", "Novo cliente"),
                    ("financeiro", "Financeiro", None, "Ticket médio"),
                    ("pedidos-delivery", "Pedidos", "button", "Novo pedido"),
                    ("entregas-delivery", "Entregas", "button", "Novo pedido"),
                    ("vasilhames-delivery", "Vasilhames", "button", "Novo vasilhame"),
                    ("vendas-prazo-delivery", "Vendas a prazo", "button", "Ver pedidos"),
                    ("relatorio-delivery", "Relatórios", "link", "Exportar CSV"),
                    ("logistica-delivery", "Logística", "heading", "Cobertura e taxas"),
                    ("loja-delivery", "Loja rápida", "heading", "Produtos na vitrine"),
                )
                for route, heading, ready_role, ready_name in panels:
                    await page.goto(origin+"/painel?aba="+route)
                    await expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible()
                    ready = (page.get_by_role(ready_role, name=ready_name, exact=True) if ready_role
                             else page.get_by_text(ready_name, exact=True))
                    await expect(ready).to_be_visible()
                    if route == "loja-delivery":
                        await expect(page.locator("#osStoreItems table")).to_be_visible()
                    for width in widths:
                        await page.set_viewport_size({"width":width,"height":900})
                        await assert_delivery_layout(page, (route,theme,width))
                        await expect(page.locator("#appView")).to_have_class(re.compile("os-delivery-workspace"))
                        if route in ("pedidos-delivery","entregas-delivery","vendas-prazo-delivery"):
                            await expect(page.locator(".od-search-line input")).to_be_visible()
                            advanced = page.locator(".od-advanced")
                            assert await advanced.count()==1
                            was_open = await advanced.evaluate("(el) => el.open")
                            await advanced.locator("summary").click()
                            await assert_delivery_layout(page, (route,theme,width,"expanded/collapsed"))
                            await advanced.locator("summary").click()
                            assert await advanced.evaluate("(el) => el.open")==was_open
                        if route == "dashboard-delivery":
                            shortcuts = page.get_by_role("navigation", name="Atalhos de Delivery")
                            await expect(shortcuts).to_have_count(0)
                            await expect(page.locator(".odd-heading h1")).to_have_text(company_name)
                            await expect(page.locator(".odd-eyebrow")).to_have_text("Dashboard")
                            title_layout = await page.locator(".odd-heading h1").evaluate("""el => {
                                const title = el.getBoundingClientRect();
                                const header = el.closest("header");
                                const art = header.querySelector(".odd-hero-art").getBoundingClientRect();
                                const button = header.querySelector(".os-delivery-dashboard-toolbar").getBoundingClientRect();
                                const overlaps = r => Math.min(title.right,r.right)-Math.max(title.left,r.left)>2 && Math.min(title.bottom,r.bottom)-Math.max(title.top,r.top)>2;
                                const range = document.createRange();range.selectNodeContents(el);
                                const textRects = Array.from(range.getClientRects());
                                return {valid:el.scrollWidth<=el.clientWidth+1 && el.scrollHeight<=el.clientHeight+1 && !overlaps(art) && !overlaps(button),
                                    title:title.toJSON(),art:art.toJSON(),button:button.toJSON(),scroll:[el.scrollWidth,el.scrollHeight],client:[el.clientWidth,el.clientHeight],
                                    grid:getComputedStyle(header).gridTemplateAreas, font:getComputedStyle(el).font, textRects:textRects.map(r=>r.toJSON())};
                            }""")
                            assert title_layout["valid"], (route, theme, width, title_layout)
                            await expect(page.locator(".os-delivery-dashboard-metrics")).to_be_visible()
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
                        for tab in ("Vendas","Produtos","Clientes","Recebimentos","Recompra","Estoque","Movimentações","A receber","Vasilhames","Atividades"):
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
            async with client(actor) as manager:
                delivery_root = f"/api/v1/tenants/{tenant}/delivery"
                couriers_path = delivery_root + "/logistics/couriers"
                courier = await manager.post(couriers_path, json={
                    "name": "Entregadora de homologação", "idempotency_key": uuid4().hex,
                })
                assert courier.status_code == 201, courier.text
                courier_id = courier.json()["id"]
                grant = await manager.put(couriers_path + "/" + courier_id + "/portal", json={
                    "email": driver_email, "expected_revision": 0,
                })
                assert grant.status_code == 200, grant.text
                product = new_product("Água da entrega de homologação")
                product["variant"]["sku"] = "browser-entrega-" + uuid4().hex[:12]
                created = await manager.post(delivery_root + "/catalog", json=product)
                assert created.status_code == 201, created.text
                draft = await order_payload(
                    manager, delivery_root, created.json()["variant_id"], 1, mode="delivery",
                    customer_id=str(customer), delivery_fee_cents=100,
                    address={"street": "Rua da entrega de homologação", "number": "12",
                             "city": "Cuiabá", "state": "MT", "neighborhood": "Centro"},
                    tender_preference="cash", change_for_cents=2000,
                )
                order = await manager.post(delivery_root + "/orders", json=draft)
                assert order.status_code == 201, order.text
                order_path = delivery_root + "/orders/" + order.json()["id"]
            mobile = await browser.new_context(
                viewport={"width": 375, "height": 812}, locale="pt-BR", color_scheme="dark",
                is_mobile=True, has_touch=True, ignore_https_errors=True, service_workers="block",
            )
            await mobile.route("**/*",safe_route)
            portal=await mobile.new_page()
            portal.on("pageerror", lambda error: errors.append(str(error)))
            await portal.goto(origin+grant.json()["portal_path"]+"/")
            await portal.get_by_label("E-mail da conta").fill(driver_email)
            await portal.get_by_label("Senha",exact=True).fill(driver_password)
            await portal.get_by_role("button",name="Entrar",exact=True).click()
            await portal.get_by_role("button",name="Iniciar expediente").click()
            await expect(portal.get_by_text("Disponível para pedidos",exact=True)).to_be_visible()
            async with client(actor) as manager:
                offer = await manager.post(order_path + "/transitions", json={
                    "action": "offer", "courier_id": courier_id, "expected_revision": order.json()["revision"],
                    "idempotency_key": uuid4().hex,
                })
                assert offer.status_code == 200, offer.text
            await portal.reload()
            delivery = portal.locator("#queue article")
            await expect(delivery.get_by_text("Aguardando seu aceite")).to_be_visible()
            await expect(delivery.get_by_text("Rua da entrega de homologação", exact=False)).to_have_count(0)
            await delivery.get_by_role("button", name="Aceitar", exact=True).click()
            await expect(delivery.get_by_text("Em rota", exact=True)).to_be_visible()
            await expect(delivery.get_by_text("Rua da entrega de homologação", exact=False)).to_be_visible()
            await expect(delivery.get_by_text("de troco para", exact=False)).to_be_visible()
            await expect(delivery.get_by_role("link", name="Abrir rota")).to_be_visible()
            await delivery.get_by_role("button", name="Informar chegada", exact=True).click()
            await expect(delivery.get_by_text("No endereço", exact=True)).to_be_visible()
            await delivery.get_by_text("Concluir entrega", exact=True).first.click()
            confirmation = delivery.locator("form[data-delivery-confirm]")
            await confirmation.get_by_label("Forma recebida").select_option("cash")
            await confirmation.get_by_label("Confirmo o recebimento de", exact=False).check()
            assert await portal.evaluate("document.documentElement.scrollWidth <= innerWidth")
            await portal.screenshot(path=artifacts/"entregador-mobile-375.png")
            await confirmation.get_by_role("button", name="Concluir entrega", exact=True).click()
            await expect(portal.get_by_text("Nenhuma entrega atribuída.")).to_be_visible()
            async with client(actor) as manager:
                finished = await manager.get(order_path)
                assert finished.status_code == 200, finished.text
                assert finished.json()["status"] == "fulfilled"
                assert finished.json()["collected_cents"] == 1350
            await portal.get_by_role("button",name="Encerrar expediente").click()
            await expect(portal.get_by_text("Fora do expediente",exact=True)).to_be_visible()
            await mobile.close()
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
                    if route == "loja-delivery":
                        await expect(page.locator("#osStoreItems table")).to_be_visible()
                    for width in (320,375,390,430,768,1280):
                        await page.set_viewport_size({"width":width,"height":900})
                        await assert_delivery_layout(page, ("WebKit",route,width))
                        if route == "loja-delivery" and width == 320:
                            await page.screenshot(path=artifacts/"webkit-loja-delivery-320.png", animations="disabled")
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
        try:
            await asyncio.wait_for(server_task, timeout=10)
        finally:
            listener.close()
