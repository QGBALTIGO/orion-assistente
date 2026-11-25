"""Isolated visual and keyboard journey using a temporary pilot database."""
from __future__ import annotations

import os
import sqlite3
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from contextlib import closing

from cryptography.fernet import Fernet
from playwright.sync_api import sync_playwright

from affiliate_pilot.niches import PUBLIC_NICHES
from affiliate_pilot.store import DEAL_TEMPLATE, DEAL_TEMPLATE_ID, Store


DESTINATION = "a" * 32
SAMPLE_RULE = "b" * 32


def seed(path, key, password):
    store = Store(Path(path), key)
    store.bootstrap("piloto", password, "PromoTech de teste")
    ident = store.user(store.authenticate("piloto", password))["id"]
    session = store.save_connection(ident, "telegram", {}, {"status":"connected","username":"bot_exemplo"})
    store.save_connection(ident, "shopee", {}, {"status":"connected","account":"••••1234","verified":int(time.time())})
    store.put(ident,"destination",DESTINATION,{"name":"Canal de teste","mode":"telegram","test":True,
              "verified":True,"connection_version":session,"checked_at":int(time.time())})
    store.ensure_deal_templates()
    now = int(time.time())
    rule = store.put(ident,"rule",SAMPLE_RULE,{
        "active":True,"name":"PromoTech • Shopee a cada 30 min","interval":30,"daily":48,"repeat_hours":48,
        "opens":"00:00","closes":"00:00","days":list(range(7)),"timezone":"America/Cuiaba","mode":"automatic",
        "niches":list(PUBLIC_NICHES),"destinations":[DESTINATION],"template_id":DEAL_TEMPLATE_ID,"next_at":now+1800,
    })
    job, _ = store.enqueue(ident, DESTINATION, {"identity":"sample:1","title":"Câmera compacta de demonstração"},
                           DEAL_TEMPLATE, now-60, approval=True, rule=rule)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE job SET status='published', receipt='123' WHERE id=?", (job,))


def wait_for(predicate):
    end = time.monotonic() + 9
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.15)
    raise AssertionError("An expected browser asset did not finish loading")


def run():
    root = Path(__file__).resolve().parents[2]
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    password = "local-browser-test-password-0000"
    with tempfile.TemporaryDirectory(prefix="orion-affiliate-") as tmp:
        runtime = {key:value for key,value in os.environ.items() if not key.startswith("AFFILIATE_")}
        runtime.update(AFFILIATE_PUBLIC_ORIGIN=origin, AFFILIATE_DATABASE=str(Path(tmp)/"pilot.sqlite3"),
                       AFFILIATE_MASTER_KEY=Fernet.generate_key().decode(), AFFILIATE_BOOTSTRAP_PASSWORD=password,
                       AFFILIATE_ENABLE_TEST_SEND="0")
        seed(runtime["AFFILIATE_DATABASE"], runtime["AFFILIATE_MASTER_KEY"], password)
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "affiliate_pilot.main:app", "--host", "127.0.0.1",
             "--port", str(port), "--no-access-log"],
            cwd=root, env=runtime, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            for _ in range(65):
                assert proc.poll() is None, "Pilot exited before accepting requests"
                try:
                    with urllib.request.urlopen(origin + "/health", timeout=1) as res:
                        assert res.status == 200
                        break
                except OSError:
                    time.sleep(.2)
            else:
                raise AssertionError("Pilot health route did not respond")
            with sync_playwright() as p:
                use_webkit = os.getenv("AFFILIATE_TEST_WEBKIT") == "1"
                browser = p.webkit.launch() if use_webkit else p.chromium.launch(channel="chrome", headless=True)
                cases = [(390,844,"light")] if use_webkit else [(1440,900,"light"),(900,1000,"dark"),
                                                               (390,844,"light"),(320,720,"dark")]
                for width, height, theme in cases:
                    page = browser.new_page(viewport={"width": width, "height": height},color_scheme=theme)
                    page.set_default_timeout(9000)
                    failures = []
                    page.on("pageerror", lambda e: failures.append(str(e)))
                    page.goto(origin)
                    page.get_by_role("heading", name="Bem-vindo ao Orion").wait_for()
                    page.get_by_role("button", name="Mostrar senha").click()
                    assert page.locator("#password").get_attribute("type")=="text"
                    page.get_by_role("button", name="Ocultar senha").click()
                    capture(page, "acesso", width, theme)
                    page.get_by_label("Usuário").fill("piloto")
                    page.get_by_label("Senha", exact=True).fill(password)
                    page.get_by_role("button", name="Entrar").click()
                    page.get_by_role("heading", name="Início", exact=True).wait_for()
                    assert page.locator("html").get_attribute("data-theme")==theme
                    assert page.locator(".os-subdash-stat").count()==4
                    assert page.locator(".os-subdash-activity-list").get_by_text("Câmera compacta de demonstração").is_visible()
                    assert page.locator("#nav [aria-current=page]").count()==1
                    capture(page, "inicio", width, theme)
                    if width>=768:
                        page.get_by_role("button", name="Recolher menu").click()
                        assert page.locator("#appView").evaluate("app => app.classList.contains('os-rail-collapsed')")
                        page.get_by_role("button", name="Expandir menu").click()
                    else:
                        drawer=page.get_by_role("button", name="Abrir menu", exact=True)
                        drawer.click()
                        assert page.locator("#workspace").evaluate("workspace => workspace.inert")
                        page.keyboard.press("Escape")
                        assert drawer.get_attribute("aria-expanded")=="false"
                    checks = [
                        ("#plataformas", "Plataformas"), ("#conexoes", "Conexões e destinos"),
                        ("#ofertas", "Ofertas"), ("#templates", "Templates"),
                        ("#automacoes", "Automações"), ("#fila", "Fila e histórico"),
                    ]
                    for href, title in checks:
                        assert page.evaluate("document.documentElement.scrollWidth") <= width
                        if width < 768:
                            page.get_by_role("button", name="Abrir menu", exact=True).click()
                        page.locator(f"nav a[href='{href}']").click()
                        page.get_by_role("heading", name=title, exact=True).wait_for()
                        capture(page, href[1:], width, theme)
                        if href == "#plataformas" or href == "#conexoes":
                            wait_for(lambda: page.locator(".af-platform img").evaluate_all(
                                "images => images.every(i => i.complete && i.naturalWidth > 0)"
                            ))
                        if href == "#ofertas":
                            page.get_by_role("button",name="Catálogo Shopee").click()
                            assert page.get_by_label("Categoria",exact=True).locator("option").count()==30
                            page.get_by_role("button",name="Por link").click()
                            assert page.get_by_label("Link do produto Shopee").is_visible()
                            page.get_by_role("button", name="Ver exemplo").click()
                            page.get_by_text("Dados de demonstração", exact=True).wait_for()
                            page.locator(".af-card-image").wait_for()
                            wait_for(lambda: page.locator(".af-card-image").evaluate("image => image.naturalWidth") == 900)
                            capture(page, "preview", width, theme)
                        if href == "#templates":
                            page.get_by_role("button",name="Novo template").click()
                            assert page.get_by_label("Nome do template").input_value()==""
                            text=page.get_by_label("Legenda da oferta")
                            page.get_by_role("button",name="{frase_ia}",exact=True).click()
                            assert "{frase_ia}" in text.input_value()
                            if width==1440:
                                page.get_by_label("Nome do template").fill("Modelo de teste")
                                page.get_by_role("button",name="Salvar template").click()
                                page.get_by_text("Template salvo.",exact=True).wait_for()
                                assert page.get_by_text("Modelo de teste",exact=True).is_visible()
                        if href == "#automacoes":
                            promotech=page.locator(".af-automation-card").filter(has_text="PromoTech • Shopee a cada 30 min")
                            assert promotech.get_by_text("A cada 30 min",exact=True).is_visible()
                            assert promotech.get_by_text("Dia inteiro",exact=True).is_visible()
                            promotech.get_by_role("button",name="Pausar",exact=True).click()
                            page.get_by_role("heading",name="Pausar automação?").wait_for()
                            page.keyboard.press("Escape")
                            assert page.get_by_text("Ativa",exact=True).is_visible()
                            promotech.get_by_role("button",name="Editar",exact=True).click()
                            select_all = page.get_by_label("Selecionar todos")
                            options = page.locator("input[name=niches]")
                            assert options.count() == 29
                            assert select_all.is_checked()
                            assert page.get_by_text("29 de 29 selecionadas").is_visible()
                            page.get_by_label("Buscar categoria").fill("camera")
                            assert page.get_by_label("Câmeras e Drones", exact=True).is_visible()
                            assert options.evaluate_all("inputs => inputs.every(i => i.checked)")
                            page.get_by_label("Câmeras e Drones", exact=True).uncheck()
                            assert select_all.evaluate("input => input.indeterminate")
                            select_all.check()
                            select_all.uncheck()
                            assert options.evaluate_all("inputs => inputs.every(i => !i.checked)")
                            page.get_by_role("button",name="Fechar editor").click()
                            assert not select_all.is_visible()
                            page.get_by_role("button",name="Nova automação").click()
                            assert page.get_by_label("Nome da automação").input_value()==""
                            select_all.check()
                            capture(page, "editar-automacao", width, theme)
                            if width==1440:
                                page.get_by_label("Nome da automação").fill("Rotina de teste")
                                page.get_by_role("checkbox",name="Canal de teste Telegram").check()
                                page.get_by_role("button",name="Salvar regra pausada").click()
                                page.get_by_role("heading",name="Rotina de teste",exact=True).wait_for()
                                assert page.get_by_text("Pausada",exact=True).is_visible()
                        if href == "#fila":
                            page.get_by_role("button",name="Publicadas · 1").click()
                            assert page.get_by_text("Câmera compacta de demonstração").is_visible()
                            page.get_by_label("Buscar oferta ou destino").fill("item inexistente")
                            assert page.get_by_role("heading",name="Nenhuma publicação encontrada").is_visible()
                            page.get_by_label("Buscar oferta ou destino").fill("camera")
                            assert page.get_by_text("Câmera compacta de demonstração").is_visible()
                        assert page.evaluate("document.documentElement.scrollWidth") <= width
                        assert not failures, failures
                    page.get_by_role("button",name="Escolher aparência").click()
                    page.get_by_role("button",name="Escuro",exact=True).click()
                    assert page.locator("html").get_attribute("data-theme") == "dark"
                    print(f"{width}×{height} {theme}: login, sete abas, prévia, editor, filtros e tema", flush=True)
                    page.close()
                browser.close()
        finally:
            proc.terminate()
            proc.wait(timeout=12)


def capture(page, name, width, theme):
    if dest := os.getenv("AFFILIATE_SCREENSHOT_DIR"):
        output = Path(dest)
        output.mkdir(parents=True, exist_ok=True)
        page.evaluate("document.fonts.ready")
        page.evaluate("window.scrollTo(0,0)")
        page.screenshot(path=str(output/f"{name}-{width}-{theme}.png"),full_page=True,animations="disabled")


if __name__ == "__main__":
    run()
