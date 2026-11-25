"""Offline browser QA for the complete affiliate Offers flow.

All product data, destination permissions, writes and delivery receipts are synthetic.
Every origin and API call is intercepted. No user account or real publication is used.
Run: python ui-tests/affiliate-offers-layout.py [--engine chromium|webkit]
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
from urllib.parse import parse_qs, urlsplit

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from affiliate_pilot.marketplaces import retailer_options
from affiliate_pilot.offers import card_png

spec = importlib.util.spec_from_file_location("affiliate_dashboard_layout", ROOT / "ui-tests/affiliate-dashboard-layout.py")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
ORIGIN, TENANT, EMAIL, API = base.ORIGIN, base.TENANT, base.EMAIL, base.API
OUT = ROOT / "artifacts/affiliate-offers"
OFFER = "a" * 32
ALT = "b" * 32
DEST = ["c" * 32, "d" * 32, "e" * 32, "f" * 32]
PHRASES = [
    {"id": "one", "text": "Um toque de praticidade para a sua rotina.", "source": "fallback"},
    {"id": "two", "text": "Conheça os detalhes e escolha a sua versão favorita.", "source": "fallback"},
    {"id": "three", "text": "Uma opção para deixar o seu dia mais simples.", "source": "fallback"},
    {"id": "four", "text": "Explore as possibilidades deste achado.", "source": "fallback"},
    {"id": "five", "text": "Veja se esta novidade combina com você.", "source": "fallback"},
]


def workspace():
    result = base.workspace(empty=True)
    result.update({
        "offer": [], "retailers": retailer_options(), "deal_template": "default",
        "template": [
            {"id": "default", "name": "Oferta com card", "brand": "Achados da Orion", "media": "card",
             "layout": "simple", "style": "whatsapp", "text": "{frase_ia}\n{produto}\n{preco}\n{link}"},
            {"id": ALT, "name": "Mensagem direta", "brand": "Achados da Orion", "media": "text",
             "layout": "simple", "style": "plain", "text": "{produto}\n{link}"},
        ],
        "destination": [
            {"id": DEST[0], "name": "Ofertas de tecnologia e acessórios", "mode": "telegram", "verified": True, "test": False},
            {"id": DEST[1], "name": "Achados que facilitam a rotina", "mode": "whatsapp_group", "verified": True},
            {"id": DEST[2], "name": "Ofertas para casa e decoração", "mode": "whatsapp_channel", "verified": True},
            {"id": DEST[3], "name": "Canal aguardando verificação", "mode": "whatsapp_channel", "verified": False},
        ],
    })
    return result


def offer(manual=False):
    return {
        "id": OFFER, "title": "Fone sem fio com conexão estável, redução de ruído e estojo de recarga",
        "retailer": "amazon" if manual else "shopee", "retailer_name": "Amazon" if manual else "Shopee",
        "store": "Loja sintética", "price": "19.90" if manual else "149.90", "currency": "USD" if manual else "BRL",
        "image": ORIGIN + "/synthetic-product.png", "phrase": PHRASES[0]["text"],
        "phrase_source": "fallback", "affiliate_url": None if manual else "https://s.shopee.com.br/synthetic-own-link",
        "affiliate_status": "not_converted" if manual else "generated",
        "link_status": "required" if manual else "generated",
    }


def preview(row, template_id, data):
    selected = next(item for item in data["template"] if item["id"] == template_id)
    text = "\n".join(str(value) for value in (row["phrase"], row["title"], row["price"], row["affiliate_url"]) if value)
    ready = bool(row["title"] and row["price"] and row["affiliate_url"] and row["currency"] == "BRL")
    token = hashlib.sha256(json.dumps([row, selected], sort_keys=True).encode()).hexdigest()
    return {
        "offer": copy.deepcopy(row), "text": text,
        "html": None, "media": selected["media"], "template": copy.deepcopy(selected),
        "suggestions": copy.deepcopy(PHRASES),
        "destinations": [{**item, "eligible": item["verified"],
                          "reason": None if item["verified"] else "Verifique a administração deste canal."}
                         for item in data["destination"]],
        "send": {"enabled": ready, "reason": None if ready else
                 "Confirme o preço em reais e adicione seu link de afiliado para enviar.", "preview_token": token},
    }


def product_image():
    image = Image.new("RGB", (760, 500), "#eef4fc")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((230, 260, 530, 415), radius=75, fill="#263752", outline="#526e93", width=4)
    draw.arc((270, 90, 490, 315), 180, 360, fill="#364d6e", width=25)
    draw.rounded_rectangle((258, 170, 318, 290), radius=28, fill="#2e7bf6")
    draw.rounded_rectangle((443, 170, 503, 290), radius=28, fill="#2e7bf6")
    draw.ellipse((365, 320, 395, 350), fill="#75aeff")
    saved = io.BytesIO()
    image.save(saved, "PNG")
    return saved.getvalue()


PHOTO = product_image()


def layout_metrics(page):
    result = page.evaluate("""() => {
      const root=document.querySelector('.af-offers-workspace');
      const rect=node=>{const r=node.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height}};
      const cards=[...root.querySelectorAll('.af-offer-panel,.af-offer-preview,.af-offer-target')].filter(n=>n.getClientRects().length);
      const clipped=[];
      const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
      while(walker.nextNode()){
        const node=walker.currentNode,p=node.parentElement;
        if(!node.nodeValue.trim()||p.closest('option,.os-sr-only,.af-offer-caption'))continue;
        const range=document.createRange();range.selectNodeContents(node);
        const rects=[...range.getClientRects()].filter(r=>r.width&&r.height);
        if(!p.getClientRects().length||getComputedStyle(p).visibility==='hidden')continue;
        for(const r of rects)if(r.left<root.getBoundingClientRect().left-2||r.right>root.getBoundingClientRect().right+2)
          clipped.push({text:node.nodeValue.trim().slice(0,80),left:r.left,right:r.right});
      }
      return {viewport:innerWidth,documentWidth:document.documentElement.scrollWidth,root:rect(root),
        cards:cards.map(n=>({class:n.className,rect:rect(n),client:n.clientWidth,scroll:n.scrollWidth})),clipped};
    }""")
    assert result["documentWidth"] <= result["viewport"] + 1, result
    assert result["root"]["left"] >= -1 and result["root"]["right"] <= result["viewport"] + 1, result
    assert all(card["scroll"] <= card["client"] + 2 for card in result["cards"]), result
    assert not result["clipped"], result["clipped"]
    return result


def exercise(page, control, data, name, output, send_calls):
    records = []
    page.evaluate("""() => {
      const selectors=['.af-offers-workspace','[data-offer-section="composer"]','#preview-template',
        '[data-offer-section="settings"]','[data-offer-section="preview"]','[data-offer-section="targets"]',
        '[data-offer-destination]','#af-offer-url'];
      window.__offerQA={nodes:Object.fromEntries(selectors.map(s=>[s,document.querySelector(s)]))};
    }""")

    def settled():
        page.evaluate("() => new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))")

    def place(selector, top=130):
        page.evaluate("""({selector,top})=>{
          const n=document.querySelector(selector);
          scrollTo({top:scrollY+n.getBoundingClientRect().top-top,behavior:'instant'});
        }""", {"selector": selector, "top": top})
        settled()

    def focus(selector):
        page.locator(selector).evaluate("(n)=>n.focus({preventScroll:true})")
        settled()

    def before(anchor):
        settled()
        return page.evaluate("""anchor=>{
          window.__offerQA.focus=document.activeElement;
          return {scrollY,top:document.querySelector(anchor).getBoundingClientRect().top};
        }""", anchor)

    def after(label, prior, anchor, exact=True, focus_same=True):
        settled()
        result = page.evaluate("""anchor=>{
          const qa=window.__offerQA;
          return {scrollY,top:document.querySelector(anchor).getBoundingClientRect().top,
            focusSame:document.activeElement===qa.focus,
            focus:document.activeElement?.outerHTML.slice(0,350),
            nodes:Object.fromEntries(Object.entries(qa.nodes).map(([s,n])=>[s,n===document.querySelector(s)]))};
        }""", anchor)
        entry = {"step": label, "before": prior, "after": result}
        records.append(entry)
        (output / (name + "-interactions.json")).write_text(json.dumps(records, ensure_ascii=False, indent=2))
        assert prior["scrollY"] > 20, entry
        assert result["scrollY"] > 20, entry
        assert all(result["nodes"].values()), entry
        if focus_same:
            assert result["focusSame"], entry
        assert abs(result["top"] - prior["top"]) <= 3, entry
        if exact:
            assert abs(result["scrollY"] - prior["scrollY"]) <= 3, entry

    settings = '[data-offer-section="settings"]'
    place(settings)
    focus("#preview-template")
    prior = before(settings)
    page.locator("#preview-template").select_option(ALT)
    page.locator('[data-offer-section="preview"][aria-busy="false"]').wait_for()
    assert page.locator(".af-offer-card-image").count() == 0
    after("template-changes-without-replacing-controls", prior, settings)

    phrase = '[data-offer-phrase="two"]'
    focus(phrase)
    prior = before(phrase)
    page.locator(phrase).click()
    page.wait_for_function("document.querySelector('[data-offer-phrase=\"two\"]').getAttribute('aria-pressed')==='true'")
    page.locator('[data-offer-section="preview"][aria-busy="false"]').wait_for()
    after("phrase-choice-keeps-focus-and-position", prior, phrase)

    target = '[data-offer-destination="' + DEST[0] + '"]'
    place('[data-offer-section="targets"]')
    focus(target)
    prior = before(target)
    page.locator(target).check()
    after("destination-selection-is-local", prior, target)
    focus("[data-offer-all]")
    prior = before("[data-offer-all]")
    page.locator("[data-offer-all]").check()
    assert page.locator("[data-offer-destination]:checked").count() == 3
    assert not page.locator('[data-offer-destination="' + DEST[3] + '"]').is_checked()
    after("select-all-excludes-unverified-destination", prior, "[data-offer-all]")

    control["hold_preview"] = True
    place(settings)
    focus("#preview-template")
    prior = before(settings)
    page.locator("#preview-template").select_option("default")
    page.locator('[data-offer-section="preview"][aria-busy="true"]').wait_for()
    assert page.locator(".af-offer-card-image").count() == 0
    after("slow-preview-keeps-last-confirmed-message", prior, settings)
    place('[data-offer-section="targets"]')
    focus(target)
    prior = before(target)
    control["hold_preview"] = False
    for route, payload in control["held"]:
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))
    control["held"].clear()
    page.locator('[data-offer-section="preview"][aria-busy="false"]').wait_for()
    page.wait_for_function("document.querySelector('.af-offer-card-image')?.complete")
    after("late-preview-preserves-where-user-moved", prior, target, exact=False)

    button = '[data-act="offer-send"]'
    place(button, 330)
    focus(button)
    prior = before(button)
    control["fail_send"] = True
    page.locator(button).click()
    page.locator('[data-offer-notice="send"]').wait_for()
    after("failed-send-keeps-selected-destinations", prior, button, exact=False)
    assert len(send_calls) == 1
    control["fail_send"] = False
    page.locator(button).click()
    page.locator("[data-offer-send-result]").wait_for()
    assert len(send_calls) == 2 and send_calls[0] == send_calls[1], send_calls
    assert send_calls[-1]["destinations"] == sorted(DEST[:3])
    assert "na fila" in page.locator("[data-offer-send-result]").inner_text()

    result_link = '[data-offer-send-result] a'
    place("[data-offer-send-result]", 190)
    focus(result_link)
    prior = before(result_link)
    control["batch_final"] = True
    with page.expect_response(lambda r: "/send-batches/" in r.url):
        page.clock.fast_forward(2100)
    page.wait_for_function("document.querySelector('[data-offer-send-result]').textContent.includes('Envio sem confirmação')")
    after("delivery-receipt-keeps-visible-control", prior, result_link, exact=False)
    count = control["batch_reads"]
    page.clock.fast_forward(31000)
    assert control["batch_reads"] == count, "Terminal unknown must stop polling and must never resend"
    assert len(send_calls) == 2
    page.screenshot(path=str(output / (name + "-send-result.png")))

    control["manual"] = True
    page.locator('[data-act="offer-new"]').click()
    page.locator("#af-offer-url").fill("https://www.amazon.com.br/dp/SYNTHETIC")
    page.locator('[data-form="offer"] button[type="submit"]').click()
    page.locator('[data-form="offer-own-link"]').wait_for()
    page.locator('[data-offer-section="preview"][aria-busy="false"]').wait_for()
    assert page.locator("#af-offer-price").input_value() == ""
    assert "US$" in page.locator(".af-offer-product-price").inner_text()
    assert page.locator('[data-offer-disclosure="details"]').get_attribute("open") is not None
    page.screenshot(path=str(output / (name + "-manual-review.png")), full_page=True)
    page.locator("#af-offer-price").fill("189,90")
    page.locator('[data-form="offer-details"] button[type="submit"]').click()
    page.locator('[data-offer-section="preview"][aria-busy="false"]').wait_for()
    page.locator("#af-offer-affiliate").fill("https://amzn.to/synthetic-own-link")
    page.locator('[data-form="offer-own-link"] button[type="submit"]').click()
    page.wait_for_function("!document.querySelector('[data-form=\"offer-own-link\"]')")
    assert data["offer"][0]["currency"] == "BRL"
    assert data["offer"][0]["affiliate_status"] == "provided"
    assert len(send_calls) == 2, "Completing an imported offer must not publish it"
    return records


def run(context, width, theme, layout, engine, output):
    data = workspace()
    control = {"manual": False, "hold_preview": False, "held": [], "fail_send": False,
               "batch_final": False, "batch_reads": 0}
    calls, errors, blocked, send_calls = [], [], [], []
    batches = {}
    page = context.new_page()
    page.clock.install()
    page.on("pageerror", lambda error: errors.append(str(error)))

    def respond(route):
        request = route.request
        url = urlsplit(request.url)
        path, method = url.path, request.method
        calls.append((method, path))
        if f"{url.scheme}://{url.netloc}" != ORIGIN:
            blocked.append(request.url)
            route.abort()
            return

        def fulfill(payload, status=200):
            route.fulfill(status=status, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))

        if path == "/panel":
            route.fulfill(status=200, content_type="text/html", body=base.PANEL)
        elif path == "/synthetic-product.png":
            route.fulfill(status=200, content_type="image/png", body=PHOTO)
        elif path.startswith("/api/v1/afiliados-assets/brands/"):
            asset = ROOT / "affiliate_pilot/static/brands" / path.rsplit("/", 1)[-1]
            if asset.is_file():
                route.fulfill(status=200, content_type="image/svg+xml" if asset.suffix == ".svg" else "image/png", body=asset.read_bytes())
            else:
                errors.append("Missing brand asset: " + path)
                route.fulfill(status=404, body="")
        elif path.startswith(("/panel-assets/", "/brand/")) or path.startswith("/api/v1/afiliados-assets/workspace."):
            name = {"orion-suite-live.js": "orion-suite.js", "workspace.js": "orion-affiliate-workspace.js",
                    "workspace.css": "orion-affiliate-workspace.css"}.get(path.rsplit("/", 1)[-1], path.rsplit("/", 1)[-1])
            asset = ROOT / "app/static" / name
            if not asset.is_file():
                errors.append("Missing local asset: " + path)
                route.fulfill(status=404, body="")
            else:
                mime = {".js": "application/javascript", ".css": "text/css", ".svg": "image/svg+xml",
                        ".woff2": "font/woff2", ".png": "image/png", ".webp": "image/webp"}.get(asset.suffix, "application/octet-stream")
                route.fulfill(status=200, content_type=mime, body=asset.read_bytes())
        elif path == "/auth/me":
            fulfill({"authenticated": True, "role": "member", "csrf": "synthetic-offer-csrf",
                     "name": "Conta sintética", "email": EMAIL, "verified": True})
        elif path == "/auth/features":
            fulfill({"registration": False, "passwordRecovery": False})
        elif path in ("/api/v1/account/businesses", "/api/v1/tenants"):
            fulfill([base.BUSINESS])
        elif path == "/api/v1/onboarding/entry":
            fulfill({"enabled": True, "kind": "affiliate", "create_allowed": False,
                     "account": {"name": "Conta sintética", "email": EMAIL, "verified": True},
                     "businesses": [base.BUSINESS], "invitations": [], "tenant_id": TENANT, "current_step": "business"})
        elif path.endswith("/management/permissions"):
            fulfill({"effective_plan": base.PERMISSIONS})
        elif path == "/api/v1/account/business-identity":
            fulfill({"name": base.BUSINESS["name"]})
        elif path == API + "state" and method == "GET":
            fulfill(data)
        elif path == API + "offers" and method == "POST":
            payload = request.post_data_json
            assert set(payload) == {"url", "note"}, payload
            row = offer(control["manual"])
            data["offer"] = [row]
            fulfill(row)
        elif path.startswith(API + "offers/"):
            suffix = path[len(API + "offers/"):].split("/")
            row = next((item for item in data["offer"] if item["id"] == suffix[0]), None)
            assert row is not None, path
            action = suffix[1] if len(suffix) > 1 else ""
            if action == "preview" and method == "GET":
                payload = preview(row, parse_qs(url.query).get("template", ["default"])[0], data)
                if control["hold_preview"]:
                    control["held"].append((route, payload))
                else:
                    fulfill(payload)
            elif action == "card.png" and method == "GET":
                template_id = parse_qs(url.query).get("template", ["default"])[0]
                template = next(item for item in data["template"] if item["id"] == template_id)
                route.fulfill(status=200, content_type="image/png", body=card_png(row, template, PHOTO))
            elif action == "phrase" and method == "PUT":
                row["phrase"] = request.post_data_json["text"]
                row["phrase_source"] = "manual"
                fulfill(row)
            elif action == "phrases" and method == "POST":
                fulfill({"suggestions": PHRASES})
            elif not action and method == "PUT":
                values = request.post_data_json
                assert set(values) <= {"title", "price", "image", "affiliate_url"}, values
                row.update(values)
                if "price" in values:
                    row["currency"] = "BRL"
                if "affiliate_url" in values:
                    row["affiliate_status"] = "provided"
                fulfill(row)
            elif action == "send" and method == "POST":
                payload = request.post_data_json
                send_calls.append(copy.deepcopy(payload))
                assert set(payload) == {"template", "destinations", "request_id", "preview_token"}, payload
                assert re.fullmatch("[a-f0-9]{32}", payload["request_id"]), payload
                assert re.fullmatch("[a-f0-9]{64}", payload["preview_token"]), payload
                if control["fail_send"]:
                    fulfill({"detail": "Não foi possível confirmar o envio. Tente novamente."}, 503)
                else:
                    batch = {"batch_id": payload["request_id"], "created": True,
                             "jobs": [{"id": str(index + 1) * 32, "destination": dest, "status": "queued", "error": None}
                                      for index, dest in enumerate(payload["destinations"])]}
                    batches[payload["request_id"]] = batch
                    fulfill(batch)
            else:
                errors.append("Unexpected offer request: " + method + " " + path)
                route.fulfill(status=405, body="")
        elif path.startswith(API + "send-batches/") and method == "GET":
            key = path.rsplit("/", 1)[-1]
            control["batch_reads"] += 1
            batch = copy.deepcopy(batches[key])
            if control["batch_final"]:
                for index, job in enumerate(batch["jobs"]):
                    job.update(status="unknown" if index == 2 else "published",
                               error="Confira este destino antes de tentar novamente." if index == 2 else None)
            fulfill(batch)
        elif method != "GET":
            errors.append("Blocked unexpected write: " + method + " " + path)
            route.fulfill(status=405, body="Only the synthetic offer operations are allowed")
        elif path.startswith("/api/v1/"):
            fulfill([])
        elif path in ("/favicon.ico", "/manifest.webmanifest") or path.startswith("/pwa/"):
            route.fulfill(status=204, body="")
        else:
            errors.append("Unexpected local request: " + path)
            route.fulfill(status=404, body="")

    context.route("**/*", respond)
    context.add_init_script('localStorage.setItem("orion-theme", ' + json.dumps(theme) + ');' +
        "localStorage.setItem(" + json.dumps(f"orion-navigation-v1:{TENANT}:{EMAIL}") + "," + json.dumps(layout) + ");")
    name = f"{layout}-{width}-{theme}"
    try:
        page.goto(ORIGIN + "/panel?aba=ofertas", wait_until="domcontentloaded")
        page.locator(".af-offers-workspace").wait_for()
        page.evaluate("document.fonts.ready")
        page.evaluate("""async()=>{
          await Promise.all((document.querySelector('#appView > .sidebar')?.getAnimations()||[]).map(a=>a.finished.catch(()=>{})));
          await new Promise(resolve=>requestAnimationFrame(resolve));
        }""")
        assert page.locator(".af-offers-workspace h1").inner_text() == "Ofertas"
        assert page.locator('[data-offer-section="composer"]').is_hidden()
        assert page.locator('[data-offer-disclosure="drafts"]').get_attribute("open") is None
        assert str(len(retailer_options())) in page.locator('[data-offer-disclosure="stores"] summary').inner_text()
        initial = layout_metrics(page)
        page.screenshot(path=str(output / (name + "-initial.png")), full_page=True)
        page.locator("#af-offer-url").fill("https://s.shopee.com.br/synthetic-short-link")
        page.locator('[data-form="offer"] button[type="submit"]').click()
        page.locator('[data-offer-section="preview"][aria-busy="false"]').wait_for()
        page.wait_for_function("document.querySelector('.af-offer-card-image')?.complete")
        assert page.locator("[data-offer-phrase]").count() == 3
        assert page.locator("[data-offer-destination]:not(:disabled)").count() == 3
        assert not send_calls
        assert page.locator(".af-offer-card-image").evaluate("(image)=>image.naturalWidth") > 500
        page.evaluate("scrollTo({top:0,behavior:'instant'})")
        prepared = layout_metrics(page)
        page.screenshot(path=str(output / (name + "-prepared.png")), full_page=True)
        if (width in (320, 430) and theme == "dark" and layout == "app") or (width == 1280 and theme == "light" and layout == "classic"):
            steps = exercise(page, control, data, name, output, send_calls)
        else:
            steps = []
        page.evaluate("scrollTo({top:document.documentElement.scrollHeight,behavior:'instant'})")
        page.evaluate("() => new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))")
        bottom = page.evaluate("""()=>{
          const end=document.querySelector('.af-offer-drafts'),dock=document.querySelector('#osAppDock');
          return {end:end.getBoundingClientRect().bottom,dock:dock?.getClientRects().length?dock.getBoundingClientRect().top:innerHeight};
        }""")
        assert bottom["end"] <= bottom["dock"] + 2, (name, bottom)
        page.screenshot(path=str(output / (name + "-bottom.png")))
        assert not errors, (name, errors)
        assert not blocked, (name, blocked)
        return {"engine": engine, "width": width, "theme": theme, "layout": layout, "initial": initial,
                "prepared": prepared, "bottom": bottom, "interactions": steps,
                "synthetic_send_requests": len(send_calls)}
    except Exception:
        page.screenshot(path=str(output / (name + "-failure.png")), full_page=True)
        raise
    finally:
        page.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=["chromium", "webkit"], default="chromium")
    parser.add_argument("--widths", type=int, nargs="+", default=[1280, 768, 430, 360, 320])
    parser.add_argument("--themes", nargs="+", choices=["light", "dark"], default=["light", "dark"])
    parser.add_argument("--layouts", nargs="+", choices=["classic", "app"], default=["classic", "app"])
    args = parser.parse_args()
    output = OUT / args.engine
    output.mkdir(parents=True, exist_ok=True)
    measurements = []
    with sync_playwright() as playwright:
        engine = getattr(playwright, args.engine)
        executable = str(Path(engine.executable_path)) if Path(engine.executable_path).is_file() else None
        if not executable and args.engine == "chromium":
            executable = os.getenv("ORION_TEST_BROWSER") or shutil.which("chromium") or shutil.which("google-chrome")
        launch = {"executable_path": executable} if executable else {}
        if args.engine == "chromium":
            launch["args"] = ["--no-sandbox"]
        browser = engine.launch(**launch)
        try:
            for width in args.widths:
                for theme in args.themes:
                    for layout in args.layouts:
                        context = browser.new_context(viewport={"width": width, "height": 900},
                            is_mobile=width < 768, has_touch=width < 768, device_scale_factor=1, service_workers="block")
                        try:
                            measurements.append(run(context, width, theme, layout, args.engine, output))
                            print(f"PASS offers {args.engine} {width}px {theme} {layout}", flush=True)
                        finally:
                            context.close()
        finally:
            browser.close()
            (output / "measurements.json").write_text(json.dumps(measurements, ensure_ascii=False, indent=2))
    print(f"PASS: {len(measurements)} viewports, initial + prepared; all writes mocked; screenshots in {output}")


if __name__ == "__main__":
    main()
