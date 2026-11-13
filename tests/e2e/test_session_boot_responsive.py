"""Real browser regression for Orion startup, without private sessions or requests."""
from pathlib import Path
import re

import pytest
from playwright.sync_api import sync_playwright


ROOT=Path(__file__).resolve().parents[2]
HTML=(ROOT/"app/static/panel.html").read_text(encoding="utf-8")
CSS=re.search(r"<style>([\s\S]*?)</style>",HTML).group(1)
BOOT=HTML[HTML.index('<section id="sessionBoot"'):HTML.index('<section id="loginView"')]
SVG_LIGHT=(ROOT/"app/static/orion-brand-light.svg").read_text(encoding="utf-8")
SVG_DARK=(ROOT/"app/static/orion-brand-dark.svg").read_text(encoding="utf-8")
SHELL='https://orion.test/painel'
DOC='''<!doctype html><html lang="pt-BR" data-theme="light"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<style>'''+CSS+'''</style></head><body>'''+BOOT+'''</body></html>'''


@pytest.mark.parametrize("engine",["chromium","webkit"])
@pytest.mark.parametrize("width,height",[(320,720),(390,844),(768,1024),(1280,800)])
@pytest.mark.parametrize("theme",["light","dark"])
def test_boot_splash_is_single_responsive_and_branded(engine,width,height,theme):
    output=ROOT/"test-results"/"orion-boot"
    output.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as pw:
        browser=getattr(pw,engine).launch()
        page=browser.new_page(viewport={"width":width,"height":height},
                              device_scale_factor=1,locale="pt-BR",
                              is_mobile=(engine=="webkit" and width<=390))
        try:
            def route_asset(route):
                url=route.request.url
                if url.endswith("orion-brand-light.svg"):
                    return route.fulfill(status=200,body=SVG_LIGHT,content_type="image/svg+xml")
                if url.endswith("orion-brand-dark.svg"):
                    return route.fulfill(status=200,body=SVG_DARK,content_type="image/svg+xml")
                route.abort()
            page.route("https://orion.test/brand/*",route_asset)
            page.route(SHELL,lambda route:route.fulfill(status=200,body=DOC,content_type="text/html"))
            page.goto(SHELL,wait_until="domcontentloaded")
            page.locator("html").evaluate("(node,theme)=>node.dataset.theme=theme",theme)
            if theme=="dark":
                page.locator(".session-boot-brand").evaluate(
                    "(node)=>node.setAttribute('src','/brand/orion-brand-dark.svg')")
            page.locator(".session-boot-brand").evaluate(
                "(node)=>node.decode?node.decode():Promise.resolve()")
            assert page.locator(".session-boot-brand").evaluate("(node)=>node.naturalWidth>0")
            assert page.locator(".session-boot-progress").count()==1
            assert page.locator("#sessionBootSpinner,.session-boot-symbol").count()==0
            assert page.locator("#sessionBoot h1").inner_text()=="Preparando seu painel"
            assert page.locator("#sessionBootRetry").is_hidden()
            assert page.locator(".session-boot-brand").is_visible()
            assert page.locator(".session-boot-progress").is_visible()
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+2"),(
                f"Horizontal overflow at {engine=} {width=} {theme=}"
            )
            assert page.locator("#sessionBoot").evaluate(
                "(node)=>getComputedStyle(node).getPropertyValue('--boot-bg').trim()") == (
                    "#090f1c" if theme=="dark" else "#f5f8fc")
            assert page.locator(".session-boot-progress").evaluate(
                "(node)=>getComputedStyle(node,'::before').animationName")=="session-boot-progress"
            if width in (390,1280):
                page.screenshot(path=str(output/f"{engine}-{theme}-{width}.png"),full_page=True)
            page.evaluate("""()=>{
                const boot=document.getElementById('sessionBoot');
                boot.classList.add('session-boot-failed');
                boot.setAttribute('aria-busy','false');
                document.getElementById('sessionBootTitle').textContent='Não foi possível abrir o painel';
                document.getElementById('sessionBootRetry').classList.remove('hidden');
            }""")
            assert page.locator(".session-boot-progress").is_hidden()
            assert page.locator("#sessionBootRetry").is_visible()
            assert page.locator("#sessionBootRetry").evaluate(
                "(node)=>node.getBoundingClientRect().height>=44")
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+2")
        finally:
            browser.close()


@pytest.mark.parametrize("engine",["chromium","webkit"])
def test_boot_reduced_motion_has_no_animated_progress(engine):
    with sync_playwright() as pw:
        browser=getattr(pw,engine).launch()
        page=browser.new_page(viewport={"width":390,"height":844},reduced_motion="reduce")
        try:
            page.route("https://orion.test/brand/*",lambda route:
                route.fulfill(status=200,body=SVG_LIGHT,content_type="image/svg+xml"))
            page.route(SHELL,lambda route:
                route.fulfill(status=200,body=DOC,content_type="text/html"))
            page.goto(SHELL)
            assert page.locator(".session-boot-progress").evaluate(
                "(node)=>getComputedStyle(node,'::before').animationName")=="none"
        finally:
            browser.close()
