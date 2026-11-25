"""Offline browser QA for affiliate automation setup and monitoring.

Run: python ui-tests/affiliate-automations-layout.py [--engine chromium|webkit]
Every request is intercepted. Rule writes update only in-memory fixtures;
delivery, queue and external requests are forbidden. No live account is used.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/affiliate-automations"
spec = importlib.util.spec_from_file_location("affiliate_dashboard_layout", ROOT / "ui-tests/affiliate-dashboard-layout.py")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
ORIGIN, TENANT, EMAIL, API = base.ORIGIN, base.TENANT, base.EMAIL, base.API
ACTIVE, PAUSED = "a" * 32, "b" * 32
DEST = [character * 32 for character in "cdef8"]
TEMPLATE = "9" * 32
CATEGORIES = [
    "Tecnologia e acessórios", "Computadores e periféricos", "Beleza e cuidados pessoais",
    "Casa e decoração", "Cozinha e utilidades domésticas", "Moda feminina", "Moda masculina",
    "Moda infantil", "Calçados femininos", "Calçados masculinos", "Esportes e lazer",
    "Saúde e bem-estar", "Bebês e maternidade", "Animais de estimação", "Brinquedos e jogos",
    "Ferramentas e construção", "Automóveis e motocicletas", "Livros e papelaria",
    "Celulares e acessórios", "Áudio e vídeo", "Relógios e joias", "Bolsas e malas",
    "Eletrodomésticos", "Móveis para escritório", "Jardim e área externa", "Alimentos e bebidas",
    "Instrumentos musicais", "Fotografia e filmagem", "Organização e limpeza",
]


def workspace(state_name="ready"):
    result = base.workspace(empty=True)
    rule = {
        "name": "Achados de tecnologia para acompanhar a sua rotina", "niches": ["category-0", "category-1"],
        "keyword": "", "blocked": ["usado", "recondicionado"], "sort": 2,
        "min_price": None, "max_price": None, "min_rating": 4.5, "min_commission": None,
        "min_sales": None, "min_discount": None, "interval": 30, "daily": 12,
        "repeat_hours": 48, "opens": "09:00", "closes": "21:00", "timezone": "America/Cuiaba",
        "days": [0, 1, 2, 3, 4, 5, 6], "mode": "automatic", "template_id": "default",
        "destinations": [DEST[0], DEST[1]],
    }
    result.update({
        "sending": True, "promotech_automation": False,
        "niches": [{"id": f"category-{index}", "name": name} for index, name in enumerate(CATEGORIES)],
        "sorts": {"2": "Mais vendidos", "4": "Maior comissão", "6": "Maior desconto"},
        "deal_template": "default",
        "template": [
            {"id": "default", "name": "Oferta com card", "brand": "Achados da Orion", "media": "card", "text": "{produto}\n{link}"},
            {"id": TEMPLATE, "name": "Mensagem direta", "brand": "Achados da Orion", "media": "text", "text": "{produto}\n{preco}\n{link}"},
        ],
        "destination": [
            {"id": DEST[0], "name": "Ofertas de tecnologia e acessórios no Telegram", "mode": "telegram", "verified": True, "test": True},
            {"id": DEST[1], "name": "Achados para casa e decoração no WhatsApp", "mode": "whatsapp_channel", "verified": True},
            {"id": DEST[2], "name": "Grupo de oportunidades para a semana", "mode": "whatsapp_group", "verified": True},
            {"id": DEST[3], "name": "Canal Telegram verificado da comunidade", "mode": "telegram", "verified": True, "test": False},
            {"id": DEST[4], "name": "Canal aguardando verificação", "mode": "whatsapp_channel", "verified": False},
        ],
        "rule": [
            {**copy.deepcopy(rule), "id": ACTIVE, "active": True, "next_at": base.EPOCH + 1800},
            {**copy.deepcopy(rule), "id": PAUSED, "name": "Casa, decoração e organização com ofertas selecionadas para todos os dias",
             "active": False, "next_at": None, "mode": "review", "opens": "00:00", "closes": "00:00",
             "niches": ["category-3", "category-4"], "destinations": [DEST[1]], "interval": 90},
        ],
        "decision": [
            {"id": "decision-one", "rule": rule["name"], "rule_id": ACTIVE, "reason": "2 publicações preparadas.",
             "niche": "category-0", "at": base.EPOCH - 300, "executed_at": base.EPOCH - 300,
             "status": "prepared", "jobs_created": 2, "job_ids": ["synthetic-job-0", "synthetic-job-1"]},
            {"id": "decision-two", "rule": rule["name"], "rule_id": ACTIVE, "reason": "Nenhuma oferta passou pelos filtros.",
             "niche": "category-1", "at": base.EPOCH - 2100, "executed_at": base.EPOCH - 2100,
             "status": "skipped", "jobs_created": 0, "job_ids": []},
        ],
        "jobs": [
            {"id": f"synthetic-job-{index}", "rule": ACTIVE, "destination": DEST[index % 2],
             "status": status, "changed": base.EPOCH - index * 300, "due": base.EPOCH + 600,
             "error": "A conexão do destino precisa ser verificada." if status == "failed" else None,
             "receipt": "synthetic-receipt" if status == "published" else None,
             "doc": {"rule": {"id": ACTIVE, "name": rule["name"], "timezone": "America/Cuiaba"},
                     "offer": {"title": "Oferta sintética de teste", "identity": f"shopee:1:{index + 1}"}}}
            for index, status in enumerate(("queued", "published", "failed", "unknown"))
        ],
    })
    if state_name == "empty":
        result.update(rule=[], decision=[], jobs=[])
    elif state_name == "no-destinations":
        result.update(rule=[], decision=[], jobs=[], destination=[])
    result["automation_destinations"] = [
        {**row, "eligible": row["verified"],
         "reason": None if row["verified"] else "Verifique a administração deste destino."}
        for row in result["destination"]
    ]
    for row in result["rule"]:
        row["runtime"] = runtime(row, result)
    if state_name == "blocked":
        for row in result["rule"]:
            row["runtime"].update(status="blocked", can_activate=False,
                                  reason="A conexão da Shopee precisa ser verificada antes de ativar.")
    return result


def runtime(rule, data):
    return {
        "status": "paused" if not rule.get("active") else "review" if rule["mode"] == "review" else "ready",
        "can_activate": True, "reason": None,
        "destinations": [copy.deepcopy(row) for row in data["automation_destinations"]
                         if row["id"] in rule.get("destinations", [])],
    }


def settled(page):
    page.evaluate("() => new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))")


def fill_field(page, name, value):
    field = page.locator(f'[data-form="rule"] [name="{name}"]')
    if field.first.get_attribute("type") == "radio":
        page.locator(f'[data-form="rule"] [name="{name}"][value="{value}"]').check()
    elif field.evaluate("n=>n.tagName") == "SELECT":
        field.select_option(str(value))
    else:
        field.fill(str(value))


def step(page, name):
    page.locator(f'[data-rule-step="{name}"]').click()
    page.locator(f'[data-rule-panel="{name}"]').wait_for(state="visible")
    settled(page)


def layout_metrics(page):
    result = page.evaluate("""() => {
      const root=document.querySelector('.af-automations-workspace');
      const rect=n=>{const r=n.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height}};
      const visible=n=>n.getClientRects().length&&getComputedStyle(n).visibility!=='hidden';
      const cards=[...root.querySelectorAll('[data-automation-card],[data-rule-panel],.af-card')].filter(visible);
      const controls=[...root.querySelectorAll('[data-rule-step],[data-act="new-rule"],[data-act="edit-rule"],[data-act="pause-rule"],[data-act="start-rule"],[data-form="rule"] button[type="submit"]')].filter(visible);
      const clipped=[];
      const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
      const bounds=root.getBoundingClientRect();
      while(walker.nextNode()) {
        const n=walker.currentNode,p=n.parentElement;
        if(!n.nodeValue.trim()||!visible(p)||p.closest('option,.os-sr-only,[aria-hidden="true"]'))continue;
        const range=document.createRange();range.selectNodeContents(n);
        for(const r of range.getClientRects()) if(r.width&&r.height&&(r.left<bounds.left-2||r.right>bounds.right+2))
          clipped.push({text:n.nodeValue.trim().slice(0,100),left:r.left,right:r.right});
      }
      return {viewport:innerWidth,documentWidth:document.documentElement.scrollWidth,
        theme:document.documentElement.dataset.theme,root:rect(root),
        cards:cards.map(n=>({kind:n.className,rect:rect(n),client:n.clientWidth,scroll:n.scrollWidth})),
        controls:controls.map(n=>({label:n.textContent.trim(),rect:rect(n)})),clipped};
    }""")
    assert result["documentWidth"] <= result["viewport"] + 1, result
    assert result["root"]["left"] >= -1 and result["root"]["right"] <= result["viewport"] + 1, result
    assert all(item["scroll"] <= item["client"] + 2 for item in result["cards"]), result
    assert not result["clipped"], result["clipped"]
    for control in result["controls"]:
        assert control["rect"]["height"] >= 39.9, control
        assert control["rect"]["left"] >= -1 and control["rect"]["right"] <= result["viewport"] + 1, control
    return result


def install_routes(context, data, control, calls, errors, blocked, writes):
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

        rule_write = re.fullmatch(re.escape(API) + r"rules/([0-9a-f]{32})(/activate)?", path)
        if method != "GET":
            if not rule_write or (method, rule_write[2]) not in {("PUT", None), ("POST", "/activate")}:
                errors.append(f"Forbidden write: {method} {path}")
                route.fulfill(status=405, body="Only synthetic rule settings are allowed")
                return
            payload = request.post_data_json
            writes.append({"method": method, "path": path, "payload": copy.deepcopy(payload)})
            if control["fail_next_write"]:
                control["fail_next_write"] = False
                fulfill({"detail": "Não foi possível salvar a automação de teste."}, 503)
                return
            if method == "POST" and payload.get("active") and control["fail_activate"]:
                control["fail_activate"] = False
                fulfill({"detail": "A conexão da Shopee precisa ser verificada antes de ativar."}, 400)
                return

            def complete():
                ident = rule_write[1]
                existing = next((row for row in data["rule"] if row["id"] == ident), None)
                if method == "PUT":
                    row = {**payload, "id": ident, "active": False, "next_at": None}
                    if existing:
                        existing.clear()
                        existing.update(row)
                        row = existing
                    else:
                        data["rule"].append(row)
                else:
                    assert existing is not None, ident
                    assert type(payload.get("active")) is bool, payload
                    existing["active"] = payload["active"]
                    existing["next_at"] = base.EPOCH + 1800 if payload["active"] else None
                    row = existing
                row["runtime"] = runtime(row, data)
                fulfill(copy.deepcopy(row))

            if control["hold_writes"]:
                control["held"].append(complete)
            else:
                complete()
            return
        if path == "/panel":
            route.fulfill(status=200, content_type="text/html", body=base.PANEL)
        elif path.startswith(("/panel-assets/", "/brand/")) or path.startswith("/api/v1/afiliados-assets/workspace."):
            name = {"orion-suite-live.js": "orion-suite.js", "workspace.js": "orion-affiliate-workspace.js",
                    "workspace.css": "orion-affiliate-workspace.css"}.get(path.rsplit("/", 1)[-1], path.rsplit("/", 1)[-1])
            asset = ROOT / "app/static" / name
            if asset.is_file():
                mime = {".js": "application/javascript", ".css": "text/css", ".svg": "image/svg+xml",
                        ".woff2": "font/woff2", ".png": "image/png", ".webp": "image/webp"}.get(asset.suffix, "application/octet-stream")
                route.fulfill(status=200, content_type=mime, body=asset.read_bytes())
            else:
                errors.append("Missing local asset: " + path)
                route.fulfill(status=404, body="Missing local asset")
        elif path.startswith("/api/v1/afiliados-assets/brands/"):
            asset = ROOT / "affiliate_pilot/static/brands" / path.rsplit("/", 1)[-1]
            if asset.is_file():
                route.fulfill(status=200, content_type="image/svg+xml" if asset.suffix == ".svg" else "image/png", body=asset.read_bytes())
            else:
                errors.append("Missing brand asset: " + path)
                route.fulfill(status=404, body="Missing brand asset")
        elif path == "/auth/me":
            fulfill({"authenticated": True, "role": "member", "csrf": "synthetic-automation-csrf", "name": "Conta sintética", "email": EMAIL, "verified": True})
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
        elif path == API + "state":
            fulfill(copy.deepcopy(data))
        elif path == API + "analytics":
            fulfill(base.report("month", False))
        elif path.startswith("/api/v1/"):
            fulfill([])
        elif path in ("/favicon.ico", "/manifest.webmanifest") or path.startswith("/pwa/"):
            route.fulfill(status=204, body="")
        else:
            errors.append("Unexpected local request: " + path)
            route.fulfill(status=404, body="Unknown local path")

    context.route("**/*", respond)


def save_button(page, activate=False):
    return page.locator("#af-rule-save-active" if activate else "#af-rule-save-paused")


def confirm_if_open(page, accept=True):
    dialog = page.locator("#osAffiliateDialog")
    if dialog.is_visible():
        dialog.locator("#confirm-action" if accept else 'button[value="cancel"]').click()
        dialog.wait_for(state="hidden")


def capture_draft(page):
    return page.locator('[data-form="rule"]').evaluate("""form=>{
      const fields=new FormData(form);
      return {name:fields.get('name'),keyword:fields.get('keyword'),days:fields.getAll('days'),
        niches:fields.getAll('niches'),destinations:fields.getAll('destinations'),
        interval:fields.get('interval'),daily:fields.get('daily'),template:fields.get('template_id')};
    }""")


def prepare_draft(page, name):
    fill_field(page, "name", name)
    step(page, "products")
    fill_field(page, "keyword", "mouse e acessórios")
    page.locator('[data-select-niches]').check()
    assert page.locator('[name="niches"]:checked').count() == len(CATEGORIES)
    page.locator('[data-select-niches]').uncheck()
    assert page.locator('[name="niches"]:checked').count() == 0
    page.locator('[data-niche-filter]').fill("beleza")
    page.locator('[name="niches"][value="category-2"]').check()
    assert page.locator('[name="niches"]:checked').count() == 1
    page.locator('[data-niche-filter]').fill("")
    page.locator('[name="niches"][value="category-0"]').check()
    step(page, "destinations")
    page.locator(f'[name="destinations"][value="{DEST[0]}"]').check()
    page.locator(f'[name="destinations"][value="{DEST[2]}"]').check()
    assert page.locator(f'[name="destinations"][value="{DEST[4]}"]:not(:disabled)').count() == 0
    step(page, "schedule")
    for field, value in {"opens": "08:30", "closes": "20:00", "interval": "30", "daily": "5", "repeat_hours": "48"}.items():
        fill_field(page, field, value)
    for index in range(7):
        page.locator(f'[name="days"][value="{index}"]').set_checked(index in (0, 2, 4))
    step(page, "template")
    fill_field(page, "template_id", TEMPLATE)
    mode = page.locator('[data-form="rule"] [name="mode"]').first
    if mode.is_visible():
        fill_field(page, "mode", "review")


def exercise(page, data, control, writes, output, name):
    records = []
    card = page.locator(f'[data-automation-card="{ACTIVE}"]')
    card.locator('[data-act="pause-rule"]').click()
    assert page.locator("#osAffiliateDialog").is_visible()
    confirm_if_open(page, accept=False)
    assert not writes and data["rule"][0]["active"]

    card.locator('[data-act="pause-rule"]').click()
    control["hold_writes"] = True
    with page.expect_request(lambda request: request.method == "POST" and request.url.endswith(f"rules/{ACTIVE}/activate")):
        confirm_if_open(page)
    settled(page)
    assert len(writes) == 1 and control["held"]
    assert data["rule"][0]["active"] is True, "A pending write must not mutate the synthetic server"
    card.locator('[data-act="pause-rule"]').evaluate("button=>button.click()")
    settled(page)
    assert len(writes) == 1, "Repeated clicks must not submit a second pause request"
    prior_pause = card.evaluate("""node=>{
      window.__automationPauseCard=node;
      return {scrollY,top:node.getBoundingClientRect().top};
    }""")
    for complete in control["held"]:
        complete()
    control["held"].clear()
    control["hold_writes"] = False
    card.locator('[data-act="start-rule"]').wait_for()
    settled(page)
    after_pause = card.evaluate("""node=>({scrollY,top:node.getBoundingClientRect().top,
      sameCard:node===window.__automationPauseCard})""")
    assert after_pause["sameCard"], "Pausing should update the existing card"
    assert abs(after_pause["top"] - prior_pause["top"]) <= 3, (prior_pause, after_pause)
    assert abs(after_pause["scrollY"] - prior_pause["scrollY"]) <= 3, (prior_pause, after_pause)
    assert data["rule"][0]["active"] is False
    assert writes[-1]["payload"] == {"active": False}
    records.append({"step": "pause-requires-confirmation-and-current-response", "writes": len(writes),
                    "before": prior_pause, "after": after_pause})

    card.locator('[data-act="start-rule"]').click()
    confirm_if_open(page)
    card.locator('[data-act="pause-rule"]').wait_for()
    assert data["rule"][0]["active"] is True
    assert writes[-1]["payload"] == {"active": True}
    records.append({"step": "explicit-activation-updates-the-rule-without-delivery", "writes": len(writes)})

    page.locator('[data-act="new-rule"]').first.click()
    page.locator('[data-form="rule"]').wait_for()
    page.evaluate("""() => {
      window.__automationDraftNodes={form:document.querySelector('[data-form="rule"]'),
        name:document.querySelector('[data-form="rule"] [name="name"]'),
        keyword:document.querySelector('[data-form="rule"] [name="keyword"]')};
    }""")
    prepare_draft(page, "Rotina de QA com acentuação e múltiplos destinos")
    before = capture_draft(page)
    for section in ("products", "destinations", "schedule", "template"):
        step(page, section)
    assert capture_draft(page) == before
    assert page.evaluate("""() => Object.entries(window.__automationDraftNodes).every(([key,node])=>
      node.isConnected&&node===(key==='form'?document.querySelector('[data-form="rule"]'):
      document.querySelector('[data-form="rule"] [name="'+key+'"]')))""")
    records.append({"step": "four-steps-preserve-draft-and-field-nodes", "draft": before})

    step(page, "products")
    page.locator('[data-niche-filter]').fill("beleza")
    step(page, "template")
    before_refresh = capture_draft(page)
    with page.expect_response(lambda response: response.url == ORIGIN + API + "state"):
        page.locator('[data-act="refresh-automations"]').evaluate("button=>button.click()")
    settled(page)
    assert capture_draft(page) == before_refresh
    assert page.locator('[data-niche-filter]').input_value() == "beleza"
    assert page.locator('[data-rule-panel="template"]').is_visible()
    assert page.evaluate("window.__automationDraftNodes.form===document.querySelector('[data-form=rule]')")
    records.append({"step": "refresh-preserves-unsaved-fields-filter-and-step"})

    control["fail_next_write"] = True
    save_button(page).click()
    confirm_if_open(page)
    page.get_by_text("Não foi possível salvar a automação de teste.", exact=False).first.wait_for()
    assert capture_draft(page) == before
    assert len(data["rule"]) == 2
    page.screenshot(path=str(output / (name + "-save-error.png")), full_page=True, animations="disabled")
    records.append({"step": "failed-save-preserves-complete-draft", "layout": layout_metrics(page)})

    control["hold_writes"] = True
    save = save_button(page)
    prior_count = len(writes)
    with page.expect_request(lambda request: request.method == "PUT" and "/rules/" in request.url):
        save.click()
    settled(page)
    save.evaluate("button=>button.click()")
    settled(page)
    assert len(writes) == prior_count + 1, "A second click while saving must not duplicate the write"
    assert capture_draft(page) == before
    assert control["held"]
    for complete in control["held"]:
        complete()
    control["held"].clear()
    control["hold_writes"] = False
    page.locator('[data-form="rule"]').wait_for(state="hidden")
    created = next(row for row in data["rule"] if row["name"] == before["name"])
    assert created["active"] is False
    assert set(created["niches"]) == {"category-0", "category-2"}
    assert set(created["destinations"]) == {DEST[0], DEST[2]}
    assert created["days"] == [0, 2, 4]
    assert created["interval"] == 30 and created["daily"] == 5 and created["template_id"] == TEMPLATE
    records.append({"step": "save-once-keeps-values-and-starts-paused", "id": created["id"]})

    created_card = page.locator(f'[data-automation-card="{created["id"]}"]')
    created_card.locator('[data-act="edit-rule"]').click()
    page.locator('[data-form="rule"]').wait_for()
    assert capture_draft(page) == before
    fill_field(page, "name", "Rotina revisada de QA")
    step(page, "template")
    control["fail_activate"] = True
    before_total = len(data["rule"])
    save_button(page, activate=True).click()
    confirm_if_open(page)
    page.get_by_text("A conexão da Shopee precisa ser verificada antes de ativar.", exact=False).first.wait_for()
    assert len(data["rule"]) == before_total
    stored = next(row for row in data["rule"] if row["id"] == created["id"])
    assert stored["active"] is False and stored["name"] == "Rotina revisada de QA"
    assert page.locator('[data-form="rule"]').is_visible()
    assert page.locator('[data-form="rule"] [name="name"]').input_value() == "Rotina revisada de QA"
    records.append({"step": "save-and-activate-failure-keeps-saved-rule-paused", "id": stored["id"]})

    save_button(page, activate=True).click()
    confirm_if_open(page)
    page.locator('[data-form="rule"]').wait_for(state="hidden")
    assert len(data["rule"]) == before_total
    assert stored["active"] is True
    assert page.locator(f'[data-automation-card="{stored["id"]}"] [data-act="pause-rule"]').is_visible()
    records.append({"step": "activation-retry-reuses-the-saved-rule", "id": stored["id"]})
    (output / (name + "-interactions.json")).write_text(json.dumps(records, ensure_ascii=False, indent=2))
    return records


def run(context, width, theme, layout, engine, output, state_name="ready"):
    data = workspace(state_name)
    control = {"hold_writes": False, "held": [], "fail_next_write": False, "fail_activate": False}
    calls, errors, blocked, writes = [], [], [], []
    install_routes(context, data, control, calls, errors, blocked, writes)
    context.add_init_script('localStorage.setItem("orion-theme", ' + json.dumps(theme) + ');' +
        "localStorage.setItem(" + json.dumps(f"orion-navigation-v1:{TENANT}:{EMAIL}") + "," + json.dumps(layout) + ");")
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    name = f"{state_name}-{layout}-{width}-{theme}"
    result = {"engine": engine, "width": width, "theme": theme, "layout": layout, "state": state_name}
    try:
        page.goto(ORIGIN + "/panel?aba=afiliados-automacoes", wait_until="domcontentloaded")
        page.locator(".af-automations-workspace").wait_for()
        page.evaluate("document.fonts.ready")
        page.evaluate("""async()=>{
          await Promise.all((document.querySelector('#appView > .sidebar')?.getAnimations()||[]).map(a=>a.finished.catch(()=>{})));
          await new Promise(resolve=>requestAnimationFrame(resolve));
        }""")
        assert page.locator(".af-automations-workspace h1").inner_text() == "Automações"
        assert page.locator('#appView').evaluate("node=>node.classList.contains('os-layout-app')") == (layout == "app")
        result["initial"] = layout_metrics(page)
        assert result["initial"]["theme"] == theme
        if state_name == "ready":
            deliveries = page.locator(f'[data-automation-card="{ACTIVE}"] .af-auto-deliveries').inner_text()
            assert "1 publicada" in deliveries, deliveries
            assert "1 na fila" in deliveries and "2 ocorrências" in deliveries, deliveries
            assert "2 publicadas" not in deliveries, "Prepared offers must not be counted as delivered"
        page.screenshot(path=str(output / (name + "-list.png")), full_page=True, animations="disabled")
        if state_name == "blocked":
            paused = page.locator(f'[data-automation-card="{PAUSED}"]')
            assert "precisa ser verificada" in paused.inner_text()
            assert paused.locator('[data-act="start-rule"]:not(:disabled):not([aria-disabled="true"])').count() == 0
            assert not writes
        else:
            page.locator('[data-act="new-rule"]').first.click()
            page.locator('[data-form="rule"]').wait_for()
            result["panels"] = {}
            for section in ("products", "destinations", "schedule", "template"):
                step(page, section)
                result["panels"][section] = layout_metrics(page)
                page.screenshot(path=str(output / (name + "-" + section + ".png")), full_page=True, animations="disabled")
            if state_name == "no-destinations":
                step(page, "destinations")
                assert page.locator('[name="destinations"]:not(:disabled)').count() == 0
                assert page.locator('.af-automations-workspace a[href="#conexoes"]').count() > 0
                assert not writes
            page.locator('[data-act="close-rule"]').first.click()
            confirm_if_open(page)
            page.locator('[data-form="rule"]').wait_for(state="hidden")
            full_flow = state_name == "ready" and ((width == 320 and theme == "dark" and layout == "app") or
                        (width == 430 and theme == "light" and layout == "app") or
                        (width == 1280 and theme == "light" and layout == "classic"))
            if full_flow:
                result["interactions"] = exercise(page, data, control, writes, output, name)
        page.evaluate("scrollTo({top:document.documentElement.scrollHeight,behavior:'instant'})")
        settled(page)
        result["bottom"] = page.evaluate("""()=>{
          const root=document.querySelector('.af-automations-workspace'),dock=document.querySelector('#osAppDock');
          const end=root.getBoundingClientRect(),d=dock?.getBoundingClientRect();
          const overlaps=dock?.getClientRects().length&&d.height>0&&d.right>end.left&&d.left<end.right;
          return {end:end.bottom,dock:overlaps?d.top:innerHeight};
        }""")
        assert result["bottom"]["end"] <= result["bottom"]["dock"] + 2, result["bottom"]
        page.screenshot(path=str(output / (name + "-bottom.png")), animations="disabled")
        assert not blocked, blocked
        assert not errors, errors
        assert all(re.fullmatch(re.escape(API) + r"rules/[0-9a-f]{32}(?:/activate)?", write["path"]) for write in writes)
        result.update(synthetic_rule_writes=len(writes), delivery_requests=0, external_requests=0, requests=len(calls))
        (output / (name + ".json")).write_text(json.dumps(result, ensure_ascii=False, indent=2))
        return result
    except Exception:
        page.screenshot(path=str(output / (name + "-failure.png")), full_page=True, animations="disabled")
        (output / (name + "-failure.json")).write_text(json.dumps({"result": result, "calls": calls, "writes": writes, "errors": errors, "blocked": blocked}, ensure_ascii=False, indent=2))
        raise
    finally:
        page.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=["chromium", "webkit"], default="chromium")
    parser.add_argument("--widths", type=int, nargs="+", default=[320, 430, 768, 1280])
    parser.add_argument("--themes", nargs="+", choices=["light", "dark"], default=["light", "dark"])
    parser.add_argument("--layouts", nargs="+", choices=["classic", "app"], default=["classic", "app"])
    parser.add_argument("--states", nargs="+", choices=["ready", "empty", "no-destinations", "blocked"], default=["ready"])
    args = parser.parse_args()
    output = OUT / args.engine
    output.mkdir(parents=True, exist_ok=True)
    measurements = []
    with sync_playwright() as playwright:
        engine = getattr(playwright, args.engine)
        bundled = Path(engine.executable_path)
        executable = str(bundled) if bundled.is_file() else None
        if not executable and args.engine == "chromium":
            executable = os.getenv("ORION_TEST_BROWSER") or shutil.which("chromium") or shutil.which("google-chrome")
        browser = engine.launch(headless=True, executable_path=executable)
        try:
            for width in args.widths:
                for theme in args.themes:
                    for layout in args.layouts:
                        for state_name in args.states:
                            context = browser.new_context(viewport={"width": width, "height": 900},
                                is_mobile=width < 768, has_touch=width < 768, device_scale_factor=1,
                                service_workers="block")
                            try:
                                measurements.append(run(context, width, theme, layout, args.engine, output, state_name))
                                print(f"PASS {args.engine} {width}px {theme} {layout} {state_name}", flush=True)
                            finally:
                                context.close()
        finally:
            browser.close()
            (output / "measurements.json").write_text(json.dumps(measurements, ensure_ascii=False, indent=2))
    print(f"PASS: {len(measurements)} states; no delivery or external requests; screenshots in {output}")


if __name__ == "__main__":
    main()
