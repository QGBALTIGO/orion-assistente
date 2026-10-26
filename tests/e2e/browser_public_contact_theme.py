"""Public Orion social icons + light/dark/system UI tested in real browsers.

Fixtures are synthetic and all outgoing API/maps/social requests are blocked.
"""
import json
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[2]
HTML=(ROOT/"app/static/orion-public.html").read_text(encoding="utf-8")
CSS=(ROOT/"app/static/orion-public.css").read_text(encoding="utf-8")
JS=(ROOT/"app/static/orion-public.js").read_text(encoding="utf-8")
URL="https://orion.test/s/empresa-sintetica"

def data():
    return {
        "timezone":"America/Sao_Paulo","today":"2030-01-07","booking_horizon_days":30,
        "opening_hours":[{"weekday":day,"intervals":[
            {"open":"09:00","close":"12:00"},{"open":"13:00","close":"18:00"}
        ] if day<5 else []} for day in range(7)],
        "site":{
            "title":"Barbearia sintética","theme_color":"#ffd119",
            "show_prices":True,"hide_orion_brand":False,
            "description":"Barbearia de teste para navegadores",
            "address":"Rua Exemplo, 100","city":"Montes Claros","state":"MG",
            "maps_url":"https://www.google.com/maps",
            "whatsapp_phone":"+5511999999999",
            "contact_phone":"+551133334444",
            "instagram_url":"https://www.instagram.com/barbearia_sintetica/",
            "tiktok_url":"https://www.tiktok.com/@barbearia_sintetica",
            "facebook_url":"https://www.facebook.com/barbearia_sintetica",
            "external_url":"https://barbearia.example/"
        },
        "services":[{
            "id":"50000000-0000-4000-8000-000000000005","name":"Corte",
            "catalog_kind":"base","category":"Serviços","duration_min":40,
            "price_cents":4000,"professional_ids":["70000000-0000-4000-8000-000000000007"]
        }],
        "professionals":[{
            "id":"70000000-0000-4000-8000-000000000007",
            "name":"Barbeiro de teste","specialty":"Corte"
        }]
    }


@pytest.mark.parametrize("browser_name",["chromium","webkit"])
@pytest.mark.parametrize("width",[320,375,390,768,1024])
def test_public_contact_icons_theme_and_hours_look_correct(browser_name,width):
    with sync_playwright() as pw:
        browser=getattr(pw,browser_name).launch()
        page=browser.new_page(viewport={"width":width,"height":850},
            device_scale_factor=2,locale="pt-BR",
            is_mobile=(browser_name=="webkit" and width<640),has_touch=True,
            color_scheme="light")
        try:
            page.route("**/*",lambda r:r.abort())
            page.route(URL,lambda r:r.fulfill(status=200,content_type="text/html",body=HTML))
            page.route("**/api/v1/public/sites/empresa-sintetica",
                       lambda r:r.fulfill(status=200,content_type="application/json",
                                          body=json.dumps(data())))
            page.goto(URL,wait_until="domcontentloaded")
            page.add_style_tag(content=CSS)
            page.add_script_tag(content=JS)
            contact=page.locator("#contact-section")
            contact.wait_for(state="visible",timeout=12000)
            social=contact.locator(".contact-channel")
            assert social.count()==6
            assert social.evaluate_all("(items)=>items.map(x=>x.dataset.channel)") == [
                "whatsapp","phone","instagram","tiktok","facebook","website"]
            assert contact.locator(".contact-links").inner_text().strip()==""
            assert "Barbearia" not in contact.locator(".contact-links").inner_text()
            first_box=None
            for index in range(6):
                item=social.nth(index)
                box=item.bounding_box()
                assert box and box["width"]>=44 and box["height"]>=44
                assert box["width"]<=52 and box["height"]<=52
                if first_box is None:first_box=box
                assert abs(box["width"]-first_box["width"])<=3
                assert item.locator("svg.contact-glyph").count()==1
                assert item.get_attribute("aria-label")
            assert contact.evaluate("(el)=>el.scrollWidth<=el.clientWidth+2")
            assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth+2")

            hours=page.locator("#hours-list")
            assert "Intervalo 12:00–13:00" in hours.inner_text()
            assert hours.locator(".hours-shift").count()==10
            assert page.locator("#map-preview .map-provider").count()==0
            assert page.locator("#map-preview iframe").count()==1
            assert page.locator("#maps-link").is_visible()

            # Theme control on header and segmented choices in footer.
            toggle=page.locator("#appearance-trigger")
            toggle.click()
            menu=page.locator("#appearance-menu")
            assert menu.is_visible()
            menu.locator('[data-public-theme-choice="dark"]').click()
            assert page.locator("html").get_attribute("data-public-theme")=="dark"
            assert page.locator("html").get_attribute("data-public-theme-preference")=="dark"
            assert page.evaluate("localStorage.getItem('orion-theme')")=="dark"
            dark_bg=contact.evaluate("(el)=>getComputedStyle(el).backgroundColor")
            assert dark_bg=="rgb(24, 34, 53)",dark_bg
            assert page.locator(".booking-dialog").evaluate(
                "(el)=>getComputedStyle(el).backgroundColor")=="rgb(24, 34, 53)"
            assert contact.evaluate("(el)=>el.scrollWidth<=el.clientWidth+2")
            assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth+2")

            appearance=page.locator(".appearance-segmented")
            appearance.locator('[data-public-theme-choice="light"]').click()
            assert page.locator("html").get_attribute("data-public-theme")=="light"
            assert page.evaluate("localStorage.getItem('orion-theme')")=="light"
            assert contact.evaluate("(el)=>getComputedStyle(el).backgroundColor")=="rgb(255, 255, 255)"
            appearance.locator('[data-public-theme-choice="system"]').click()
            assert page.locator("html").get_attribute("data-public-theme")=="light"
            page.emulate_media(color_scheme="dark")
            assert page.locator("html").get_attribute("data-public-theme")=="dark"
            assert page.evaluate("localStorage.getItem('orion-theme')")=="auto"
            page.emulate_media(color_scheme="light")
            assert page.locator("html").get_attribute("data-public-theme")=="light"

            # Direct icon clicks must not trigger any booking action.
            page.evaluate("""() => {
              window.__networkClicks=0;
              document.querySelectorAll('#contact-links a').forEach(a=>{
                a.addEventListener('click',event=>{
                  event.preventDefault();window.__networkClicks++;
                });
              });
            }""")
            social.nth(2).click()
            assert page.evaluate("window.__networkClicks")==1
            assert not page.locator("#booking-dialog").evaluate("(el)=>el.open")
            assert contact.evaluate("(el)=>el.scrollWidth<=el.clientWidth+2")
        finally:
            browser.close()
