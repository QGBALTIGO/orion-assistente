"""Browser-only visual geometry regression of Chatbot card icon actions.

Local synthetic fixture, no actual tenant, API, WhatsApp or mutations.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "app" / "static"
CONTRACT = json.loads((ROOT / "tests/fixtures/subscription_chatbot_contract.json").read_text())
ARTIFACTS = ROOT / "test-artifacts" / "chatbot-compact-actions"

fixture = json.loads(json.dumps(CONTRACT))
listing = fixture["list_response"]
second = dict(listing["items"][0])
second.update(id="30000000-0000-4000-8000-000000000002", keyword="Lista",
              preview="Descrição", enabled=False, response_kind="list",
              creation_order=2)
listing["items"] = [listing["items"][0], second]
listing["counts"] = {"total": 2, "active": 1, "inactive": 1}
listing["filtered_total"] = 2

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def send(self, payload: bytes, mime: str):
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = urlsplit(self.path).path
        allowed = {
            "/assets/orion-suite.css": ("orion-suite.css", "text/css"),
            "/assets/orion-subscription-chatbot.css": ("orion-subscription-chatbot.css", "text/css"),
            "/assets/orion-subscription-chatbot.js": ("orion-subscription-chatbot.js", "text/javascript"),
        }
        if path in allowed:
            name, mime = allowed[path]
            return self.send((ASSETS / name).read_bytes(), mime)
        if path != "/":
            self.send_error(404)
            return
        json_data = json.dumps(
            {"listing": listing, "catalog": fixture["catalog_response"]},
            ensure_ascii=False
        ).replace("</", "<\\/")
        html = """<!doctype html><html lang="pt-BR" data-theme="light">
        <head><meta name="viewport" content="width=device-width, initial-scale=1">
        <link rel="stylesheet" href="/assets/orion-suite.css">
        <link rel="stylesheet" href="/assets/orion-subscription-chatbot.css">
        </head><body><main class="os-main"><section id="chatbot"></section></main>
        <script>window.OrionFields={bind(){return ()=>{}},refresh(){}};</script>
        <script src="/assets/orion-subscription-chatbot.js"></script>
        <script>
        const fixture = __FIXTURE__;
        window.OrionSubscriptionChatbot.mount({
          host:document.getElementById('chatbot'),
          tenantId:'10000000-0000-4000-8000-000000000001',
          async request(path,method='GET'){
            window.__calls=(window.__calls||[]).concat([{path,method}]);
            if(method!=='GET')throw Error('Fixture forbids writes');
            if(path.includes('/catalog'))return structuredClone(fixture.catalog);
            if(path.includes('?'))return structuredClone(fixture.listing);
            throw Error('Unexpected fixture read '+path);
          }
        });
        </script></body></html>""".replace("__FIXTURE__", json_data)
        self.send(html.encode("utf-8"), "text/html; charset=utf-8")

@pytest.fixture(scope="module")
def fixture_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        yield instance
        instance.close()

@pytest.mark.parametrize("size", [(320,700),(390,844),(768,1024),(1366,900)])
@pytest.mark.parametrize("theme", ["light","dark"])
def test_card_switch_and_icon_actions_are_compact(browser, fixture_url, size, theme):
    width,height=size
    context=browser.new_context(viewport={"width":width,"height":height},
                                color_scheme=theme,locale="pt-BR",service_workers="block",
                                has_touch=width<=390)
    errors,unexpected=[],[]
    allowed_origin=urlsplit(fixture_url)
    def filter_requests(route):
        parsed=urlsplit(route.request.url)
        if parsed.scheme in {"https","http"} and (parsed.hostname,parsed.port) != (
            allowed_origin.hostname, allowed_origin.port
        ):
            unexpected.append(route.request.url)
            return route.abort()
        return route.continue_()
    context.route("**/*",filter_requests)
    page=context.new_page()
    page.on("pageerror",lambda error:errors.append(str(error)))
    ARTIFACTS.mkdir(parents=True,exist_ok=True)
    ident=f"{width}x{height}-{theme}"
    try:
        page.goto(fixture_url)
        page.evaluate("theme=>document.documentElement.dataset.theme=theme",theme)
        expect(page.locator("[data-sc-card]")).to_have_count(2)
        page.evaluate("document.fonts.ready")
        cards=page.locator("[data-sc-card]")
        measured=page.evaluate("""() => {
          const r=el=>{const b=el.getBoundingClientRect();return {
            x:b.x,y:b.y,width:b.width,height:b.height,left:b.left,right:b.right,top:b.top,bottom:b.bottom};};
          return {
            viewport:innerWidth,scrollWidth:document.documentElement.scrollWidth,
            cards:[...document.querySelectorAll('[data-sc-card]')].map(card=>{
              const head=card.querySelector('.os-chatbot-card-header');
              const sw=card.querySelector('[role=switch]');
              const actionBar=card.querySelector('.os-chatbot-card-actions');
              const actions=[...actionBar.querySelectorAll('button')];
              return {card:r(card),header:r(head),toggle:r(sw),
                checked:sw.getAttribute('aria-checked'),actions:actions.map(a=>({
                  ...r(a),action:a.dataset.scAction,label:a.getAttribute('aria-label'),
                  text:a.textContent.trim(),icon:!!a.querySelector('svg.os-icon'),
                  disabled:a.disabled
                })),group:r(actionBar)};
            })
          };
        }""")
        assert measured["viewport"]==width,measured
        assert measured["scrollWidth"]<=width+1,measured
        assert [c["checked"] for c in measured["cards"]]==["true","false"],measured
        for card in measured["cards"]:
            assert card["card"]["right"]<=width+1,card
            assert card["toggle"]["width"]>=44 and card["toggle"]["height"]>=44,card
            assert card["toggle"]["right"]<=card["header"]["right"]+1,card
            assert card["toggle"]["top"]>=card["header"]["top"]-8,card
            assert [a["action"] for a in card["actions"]]==["edit","copy","delete"],card
            assert all(a["text"]=="" and a["icon"] and a["label"] for a in card["actions"]),card
            assert all(a["width"]>=43 and a["height"]>=43 for a in card["actions"]),card
            assert all(card["group"]["left"]-1<=a["left"] and
                       a["right"]<=card["group"]["right"]+1 for a in card["actions"]),card
            assert card["actions"][-1]["left"]>card["actions"][1]["left"],card
        page.screenshot(path=str(ARTIFACTS/f"{ident}.png"),full_page=True,animations="disabled")
        first=cards.first
        first.locator('[role="switch"]').click()
        expect(page.locator('[data-sc-form="toggle"]')).to_be_visible()
        assert page.evaluate("window.__calls.filter(c=>c.method!=='GET').length")==0
        expect(first.locator('[role="switch"]')).to_have_attribute("aria-checked","true")
        page.locator('[data-sc-form="toggle"] [data-sc-action="close-dialog"]').click()
        expect(page.locator('[data-sc-form="toggle"]')).to_have_count(0)
        expect(first.locator('[role="switch"]')).to_have_attribute("aria-checked","true")
        assert not errors,errors
        assert not unexpected,unexpected
    finally:
        (ARTIFACTS/f"{ident}.json").write_text(json.dumps({
            "theme":theme,"viewport":size,"errors":errors,"unexpected":unexpected,
            "measurements":locals().get("measured",None)
        },ensure_ascii=False,indent=2),encoding="utf-8")
        context.close()
