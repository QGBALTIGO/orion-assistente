"""Public Orion availability: three columns on phones and separate local periods.

Mocked public catalogue/availability. Never touches real reservations or tenant data.
"""
import json
import os
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
FIELDS_CSS=(ROOT/'app/static/orion-fields.css').read_text(encoding='utf-8')
FIELDS_JS=(ROOT/'app/static/orion-fields.js').read_text(encoding='utf-8')
HTML = (ROOT / "app/static/orion-public.html").read_text(encoding="utf-8")
CSS = (ROOT / "app/static/orion-public.css").read_text(encoding="utf-8")
JS = (ROOT / "app/static/orion-public.js").read_text(encoding="utf-8")
THEME = (ROOT / "app/static/orion-public-theme.js").read_text(encoding="utf-8")
URL = "https://orion.test/s/empresa-sintetica"
PRO = "70000000-0000-4000-8000-000000000007"
SERVICE = "50000000-0000-4000-8000-000000000005"

DATA = {
    "timezone": "America/Campo_Grande", "today": "2030-01-07",
    "booking_horizon_days": 45, "opening_hours": [],
    "site": {"title": "Barbearia sintética", "theme_color": "#2e7bf6",
             "show_prices": True, "description": "Agende seu atendimento."},
    "services": [{"id": SERVICE, "name": "Corte", "catalog_kind": "base",
                  "price_cents": 4000, "price_mode": "fixed",
                  "duration_min": 40, "professional_ids": [PRO]}],
    "professionals": [{"id": PRO, "name": "Guilherme", "color": "#3c74ce"}]
}
SLOTS = [
    f"2030-01-07T{hour}:00-04:00" for hour in
    ["19:00", "12:00", "08:30", "09:00", "14:15", "18:00",
     "11:45", "09:15", "17:45", "09:30"]
]

@pytest.mark.parametrize("browser_type,width,theme", [
    ("webkit", 320, "dark"),
    ("webkit", 390, "dark"),
    ("webkit", 430, "light"),
    ("chromium", 360, "light"),
    ("chromium", 640, "dark"),
    ("chromium", 900, "light")
])
def test_public_periods_keep_three_mobile_columns_and_booking(browser_type, width, theme):
    with sync_playwright() as playwright:
        browser = getattr(playwright, browser_type).launch(
            headless=True, **({"channel": "chrome"} if browser_type=="chromium"
                and os.environ.get("ORION_PUBLIC_TEST_CHROME")=="1" else {}))
        page = browser.new_page(viewport={"width": width, "height": 800},
                                device_scale_factor=2, locale="pt-BR",
                                has_touch=width<=430,
                                is_mobile=browser_type=="webkit" and width<=430,
                                color_scheme=theme)
        errors=[]
        page.on("pageerror", lambda err: errors.append(str(err)))
        calls=[]
        try:
            page.route("**/*", lambda route: route.abort())
            page.route(URL, lambda route: route.fulfill(
                status=200, content_type="text/html", body=HTML))
            page.route("**/api/v1/public/sites/empresa-sintetica",
                lambda route: route.fulfill(status=200, content_type="application/json",
                    body=json.dumps(DATA)))
            def availability(route):
                calls.append(route.request.url)
                route.fulfill(status=200, content_type="application/json",
                    body=json.dumps({"quote": "q"*55, "price_cents":4000,
                        "duration_min":40, "services":["Corte"], "timezone":DATA["timezone"],
                        "slots": SLOTS}))
            page.route("**/api/v1/public/sites/empresa-sintetica/availability?*",
                       availability)
            page.goto(URL, wait_until="domcontentloaded")
            page.add_style_tag(content=CSS)
            page.add_style_tag(content=FIELDS_CSS)
            page.add_script_tag(content=THEME)
            page.add_script_tag(content=FIELDS_JS)
            page.add_script_tag(content=JS)
            page.locator("#site-content").wait_for(state="visible")
            page.locator("#hero-book").click()
            page.locator("#booking-service-list .service-card").click()
            page.locator("#booking-next").click()
            page.locator("#time-slots .slot-period").first.wait_for()
            names = page.locator(".slot-period-title").all_text_contents()
            assert names == ["Manhã", "Tarde", "Noite"]
            assert page.locator(".slot-period-grid").count()==3
            assert page.locator("#time-slots .slot").count()==len(SLOTS)
            assert page.locator('[data-slot-jump]').count()==3
            assert page.locator(".booking-slots-heading").inner_text()=="Horários disponíveis"

            boxes=page.evaluate("""() => {
                const dialog=document.querySelector('#booking-dialog').getBoundingClientRect();
                const groups=[...document.querySelectorAll('.slot-period-grid')].map(grid=>{
                    const r=grid.getBoundingClientRect();
                    return {cols:getComputedStyle(grid).gridTemplateColumns.split(' ').length,
                       left:r.left,right:r.right,gridWidth:r.width,
                       slots:[...grid.querySelectorAll('.slot')].map(btn=>{
                           const b=btn.getBoundingClientRect();
                           return {width:b.width,height:b.height,left:b.left,right:b.right};
                       })};
                });
                return {viewport:innerWidth,doc:document.documentElement.scrollWidth,
                    dialog:{left:dialog.left,right:dialog.right},groups};
            }""")
            assert boxes["doc"]<=width+2, boxes
            columns=3 if width<640 else 4 if width<900 else 5
            for group in boxes["groups"]:
                assert group["cols"]==columns, (columns,group)
                assert group["left"]>=boxes["dialog"]["left"]-1,boxes
                assert group["right"]<=boxes["dialog"]["right"]+1,boxes
                for btn in group["slots"]:
                    assert btn["height"]>=44 and btn["width"]>=50,btn
                    assert btn["left"]>=group["left"]-1 and btn["right"]<=group["right"]+1,btn

            page.locator('[data-slot-jump=evening]').click()
            assert page.locator('[data-slot-jump=evening]').get_attribute('aria-current')=="location"
            page.locator('.slot-period[data-slot-period=afternoon] .slot').first.click()
            picked=page.locator('.slot[aria-pressed=true]')
            assert picked.count()==1
            assert picked.first.inner_text()=="12:00"
            assert page.locator('[data-slot-jump=afternoon]').get_attribute('aria-current')=="location"
            assert page.locator('#booking-next').is_enabled()
            assert "40 min" in page.locator('#booking-total').inner_text()
            page.locator('#booking-next').click()
            assert page.locator('#customer-form').count()==1
            page.locator('#booking-back').click()
            assert page.locator('.slot[aria-pressed=true]').count()==1
            assert len(calls)==1, calls
            assert not errors, errors
        finally:
            browser.close()
