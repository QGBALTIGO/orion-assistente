"""Offline browser QA for the affiliate queue/history in the complete Orion panel.

Run: python ui-tests/affiliate-history-layout.py [--engine chromium|webkit]
All requests are intercepted. Approval/cancellation only change fixtures in memory;
no real account, publication, transport or external resource is accessed.
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
OUT = ROOT / "artifacts/affiliate-history"
spec = importlib.util.spec_from_file_location("affiliate_automation_qa", ROOT / "ui-tests/affiliate-automations-layout.py")
shell = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shell)
base = shell.base
ORIGIN, TENANT, EMAIL, API = base.ORIGIN, base.TENANT, base.EMAIL, base.API
STATES = ["approval", "queued", "preparing", "dispatching", "published", "paused", "failed", "expired", "cancelled", "unknown"]
DEST = ["b" * 32, "c" * 32]
PRODUCT_IMAGE = b'<svg xmlns="http://www.w3.org/2000/svg" width="240" height="240"><rect width="240" height="240" rx="24" fill="#e8eef8"/><path d="M65 66h95v100H65zM160 85h21v53h-21M80 184h66" fill="none" stroke="#357cf5" stroke-width="12" stroke-linejoin="round"/><path d="M85 42v12m30-12v12m30-12v12" stroke="#94a3b8" stroke-width="8"/></svg>'


def job_id(index):
    return f"{index + 1:032x}"


def workspace(state_name="ready"):
    data = base.workspace(empty=True)
    data.update({"sending": False, "destination": [
        {"id": DEST[0], "name": "GeekHunter — achados de tecnologia e acessórios no Telegram", "mode": "telegram", "verified": True},
        {"id": DEST[1], "name": "Promoções para casa e decoração no WhatsApp", "mode": "whatsapp_channel", "verified": True},
    ], "jobs": []})
    if state_name == "empty":
        return data
    titles = [
        "Cafeteira elétrica com jarra de aço, filtro permanente e programação para acompanhar todas as manhãs",
        "Fone de ouvido Bluetooth com cancelamento de ruído, estojo de recarga e longa duração de bateria",
        "Luminária de mesa articulada para leitura, escritório e estudos com ajuste de intensidade",
        "Organizador multiuso para cozinha com divisórias removíveis e acabamento resistente",
        "Oferta de garrafa térmica de aço inox com visor de temperatura e tampa vedada",
    ]
    for index in range(35):
        status = STATES[index % len(STATES)]
        data["jobs"].append({
            "id": job_id(index), "destination": DEST[index % 2], "status": status,
            "due": base.EPOCH + 3600, "changed": base.EPOCH - index * 60,
            "published_at": base.EPOCH - index * 60 - 120 if status == "published" else None,
            "attempts": 1 if status == "published" else 0,
            "receipt": {"message_id": "synthetic-private-receipt"} if status == "published" else None,
            "error": "A conexão do destino precisa ser verificada antes de continuar." if status == "failed" else None,
            "doc": {"automation": {"rule_id": "d" * 32, "mode": "review"},
                "rule": {"name": "Achados da tarde", "timezone": "America/Campo_Grande"},
                "template": {"name": "Oferta completa com card", "media": "card"},
                "offer": {"title": titles[index % len(titles)], "image": ORIGIN + "/qa-product.svg" if index % 3 == 0 else None,
                    "price": "89.90", "currency": "BRL", "retailer_name": "Shopee", "identity": "shopee:1:" + str(index),
                    "affiliate_url": "https://s.shopee.com.br/synthetic-" + str(index)}},
        })
    return data


def settled(page):
    page.evaluate("() => new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))")


def install_routes(context, data, control, calls, errors, blocked, writes):
    # Reuse only the complete-panel fixture router; intercept history-specific
    # state and writes before it. It blocks all unrecognized/external requests.
    shell.install_routes(context, data, control, calls, errors, blocked, writes)

    def history_route(route):
        request, url = route.request, urlsplit(route.request.url)
        if f"{url.scheme}://{url.netloc}" != ORIGIN:
            route.fallback()
            return
        if url.path == "/qa-product.svg":
            calls.append((request.method, url.path))
            assert request.method == "GET"
            route.fulfill(status=200, content_type="image/svg+xml", body=PRODUCT_IMAGE)
            return
        if request.method == "GET" and url.path == API + "state":
            calls.append((request.method, url.path))
            snapshot = copy.deepcopy(data)
            if control["fail_next_read"]:
                control["fail_next_read"] = False
                route.fulfill(status=503, content_type="application/json", body=json.dumps({"detail": "Atualização temporariamente indisponível."}))
            elif control["hold_reads"]:
                control["held_reads"].append(lambda: route.fulfill(status=200, content_type="application/json", body=json.dumps(snapshot, ensure_ascii=False)))
            else:
                route.fulfill(status=200, content_type="application/json", body=json.dumps(snapshot, ensure_ascii=False))
            return
        match = re.fullmatch(re.escape(API) + r"jobs/([0-9a-f]{32})", url.path)
        if request.method == "POST" and match:
            calls.append((request.method, url.path))
            payload = request.post_data_json
            assert payload == {"action": "approve"} or payload == {"action": "cancel"}, payload
            row = next(job for job in data["jobs"] if job["id"] == match[1])
            assert row["status"] == "approval" if payload["action"] == "approve" else row["status"] in {"approval", "queued", "paused", "failed", "expired"}
            writes.append({"method": request.method, "path": url.path, "payload": copy.deepcopy(payload)})

            def complete():
                row["status"] = "queued" if payload["action"] == "approve" else "cancelled"
                route.fulfill(status=200, content_type="application/json", body='{"ok":true}')

            if control["hold_writes"]:
                control["held"].append(complete)
            else:
                complete()
            return
        route.fallback()

    context.route("**/*", history_route)


def layout_metrics(page):
    result = page.evaluate("""() => {
      const root=document.querySelector('.af-history-workspace');
      const rect=n=>{const r=n.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height}};
      const visible=n=>n.getClientRects().length&&getComputedStyle(n).visibility!=='hidden';
      const bounds=root.getBoundingClientRect();
      const cards=[...root.querySelectorAll('[data-history-job],#history-list')].filter(visible);
      const controls=[...root.querySelectorAll('button,input')].filter(visible);
      const input=root.querySelector('#filter-history'),icon=input.parentElement.querySelector('svg');
      const inputStyle=getComputedStyle(input),inputRect=input.getBoundingClientRect();
      const iconRect=icon?.getBoundingClientRect();
      const clipped=[],walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
      while(walker.nextNode()) {
        const n=walker.currentNode,p=n.parentElement;
        if(!n.nodeValue.trim()||!visible(p)||p.closest('option,.os-sr-only,[aria-hidden="true"]'))continue;
        const range=document.createRange();range.selectNodeContents(n);
        for(const r of range.getClientRects()) if(r.width&&r.height&&(r.left<bounds.left-2||r.right>bounds.right+2))
          clipped.push({text:n.nodeValue.trim().slice(0,100),left:r.left,right:r.right});
      }
      return {viewport:innerWidth,documentWidth:document.documentElement.scrollWidth,theme:document.documentElement.dataset.theme,
        root:rect(root),cards:cards.map(n=>({kind:n.className,rect:rect(n),client:n.clientWidth,scroll:n.scrollWidth})),
        controls:controls.map(n=>({label:n.getAttribute('aria-label')||n.textContent.trim(),rect:rect(n)})),clipped,
        search:{font:parseFloat(inputStyle.fontSize),height:inputRect.height,textStart:inputRect.left+parseFloat(inputStyle.paddingLeft),
          iconRight:iconRect?.right,iconWidth:iconRect?.width,label:document.querySelector('label[for="filter-history"]')?.textContent.trim()}};
    }""")
    assert result["documentWidth"] <= result["viewport"] + 1, result
    assert result["root"]["left"] >= -1 and result["root"]["right"] <= result["viewport"] + 1, result
    assert all(row["scroll"] <= row["client"] + 2 for row in result["cards"]), result
    assert not result["clipped"], result["clipped"]
    assert result["search"]["label"] == "Buscar oferta ou destino", result["search"]
    assert result["search"]["font"] >= (16 if result['viewport'] < 768 else 13), result["search"]
    assert result["search"]["height"] >= 43.9, result["search"]
    assert result["search"]["iconWidth"] > 0, result["search"]
    assert result["search"]["textStart"] >= result["search"]["iconRight"] + 5, result["search"]
    for control in result["controls"]:
        assert control["rect"]["height"] >= (39.9 if result['viewport'] < 768 else 31.9), control
        assert control["rect"]["left"] >= -1 and control["rect"]["right"] <= result["viewport"] + 1, control
    return result


def visible_jobs(page):
    return page.locator('[data-history-job]:visible')


def confirm(page, accept=True):
    dialog = page.locator("#osAffiliateDialog")
    dialog.wait_for(state="visible")
    dialog.locator("#confirm-action" if accept else 'button[value="cancel"]').click()
    dialog.wait_for(state="hidden")


def exercise(page, data, control, calls, writes, output, name):
    records = []
    field = page.locator("#filter-history")
    page.evaluate("""() => {
      window.__historyQA={root:document.querySelector('.af-history-workspace'),field:document.querySelector('#filter-history'),
        first:document.querySelector('[data-history-job]'),navigation:performance.getEntriesByType('navigation').length};
    }""")
    assert visible_jobs(page).count() == 20
    page.locator('[data-history-more]').click()
    assert visible_jobs(page).count() == 35
    assert len(set(visible_jobs(page).evaluate_all('nodes=>nodes.map(n=>n.dataset.historyJob)'))) == 35
    assert page.evaluate("window.__historyQA.first===document.querySelector('[data-history-job]')")
    records.append({"step": "pagination-appends-without-duplicates", "rendered": 35})

    page.locator('#history-destination').select_option(DEST[1])
    assert visible_jobs(page).count() == 17
    page.locator('#history-destination').select_option('')

    field.fill("cafe")
    assert visible_jobs(page).count() == 7
    field.fill("promocoes")
    assert visible_jobs(page).count() == 17
    page.locator('[data-history-filter="attention"]').click()
    assert visible_jobs(page).count() == 9
    assert page.locator('[data-history-filter="attention"]').get_attribute('aria-pressed') == 'true'
    page.screenshot(path=str(output / (name + "-filtered.png")), full_page=True, animations="disabled")
    records.append({"step": "accent-insensitive-search-combines-with-status", "matches": visible_jobs(page).count()})
    field.fill('nenhum produto corresponde')
    assert visible_jobs(page).count() == 0 and page.locator('#history-empty').is_visible()
    page.screenshot(path=str(output / (name + '-no-results.png')), full_page=True, animations='disabled')
    page.locator('[data-history-reset]').click()
    assert field.input_value() == ""
    assert page.locator('[data-history-filter="all"]').get_attribute('aria-pressed') == 'true'

    # While enough records remain to keep the page height stable, applying the
    # queue filter must not reconstruct the screen or reset its scroll.
    page.evaluate("scrollTo({top:180,behavior:'instant'})")
    settled(page)
    before_filter = page.evaluate("({y:scrollY,top:document.querySelector('#filter-history').getBoundingClientRect().top})")
    page.locator('[data-history-filter="pending"]').evaluate('node=>node.click()')
    settled(page)
    after_filter = page.evaluate("({y:scrollY,top:document.querySelector('#filter-history').getBoundingClientRect().top})")
    assert abs(after_filter['y'] - before_filter['y']) <= 3, (before_filter, after_filter)
    assert page.evaluate("window.__historyQA.root===document.querySelector('.af-history-workspace')&&window.__historyQA.field===document.querySelector('#filter-history')")
    records.append({"step": "filter-preserves-screen-and-position", "before": before_filter, "after": after_filter})

    field.fill("cafe")
    field.evaluate("node=>{node.focus({preventScroll:true});node.setSelectionRange(1,3);}")
    control["hold_reads"] = True
    before_reads = len([call for call in calls if call == ("GET", API + "state")])
    with page.expect_request(lambda request: request.url == ORIGIN + API + "state"):
        page.locator('[data-act="refresh-history"]').evaluate('node=>node.click()')
    settled(page)
    page.locator('[data-act="refresh-history"]').evaluate('node=>node.click()')
    field.fill("tecnologia")
    field.evaluate("node=>{node.focus({preventScroll:true});node.setSelectionRange(2,6);}")
    before = field.evaluate("node=>({y:scrollY,top:node.getBoundingClientRect().top})")
    assert control["held_reads"]
    for complete in control["held_reads"]:
        complete()
    control["held_reads"].clear()
    control["hold_reads"] = False
    page.locator('[data-act="refresh-history"][aria-busy="false"]').wait_for()
    settled(page)
    after = field.evaluate("node=>({y:scrollY,top:node.getBoundingClientRect().top,start:node.selectionStart,end:node.selectionEnd,focused:document.activeElement===node})")
    assert field.input_value() == "tecnologia"
    assert after['focused'] and (after['start'], after['end']) == (2, 6), after
    assert abs(after['y'] - before['y']) <= 3, (before, after)
    assert page.locator('[data-history-filter="pending"]').get_attribute('aria-pressed') == 'true'
    assert len([call for call in calls if call == ("GET", API + "state")]) == before_reads + 1
    assert page.evaluate("window.__historyQA.field===document.querySelector('#filter-history')")
    records.append({"step": "slow-refresh-preserves-new-input-focus-selection-and-scroll", "before": before, "after": after})

    control["fail_next_read"] = True
    with page.expect_response(lambda response: response.url == ORIGIN + API + "state" and response.status == 503):
        page.locator('[data-act="refresh-history"]').evaluate('node=>node.click()')
    page.get_by_text("Atualização temporariamente indisponível.", exact=False).first.wait_for()
    assert field.input_value() == 'tecnologia' and visible_jobs(page).count() > 0
    assert page.evaluate("window.__historyQA.field===document.querySelector('#filter-history')")
    records.append({"step": "refresh-error-keeps-loaded-history"})
    field.fill('')
    page.locator('[data-history-filter="all"]').click()

    published = page.locator('[data-history-job="' + job_id(4) + '"]')
    published.locator('[data-history-details]').click()
    assert published.locator('[data-history-details]').get_attribute('aria-expanded') == 'true'
    assert published.locator('[data-history-detail]').is_visible()
    assert 'synthetic-private-receipt' not in page.locator('.af-history-workspace').inner_text()
    page.screenshot(path=str(output / (name + "-details.png")), full_page=True, animations="disabled")
    records.append({"step": "publication-details-show-human-confirmation"})

    approval = page.locator('[data-history-job="' + job_id(0) + '"]')
    approval.locator('[data-act="approve"]').click()
    confirm(page, accept=False)
    assert not writes
    approval.locator('[data-act="approve"]').click()
    control["hold_writes"] = True
    with page.expect_request(lambda request: request.method == "POST" and request.url.endswith("/jobs/" + job_id(0))):
        confirm(page)
    approval.locator('[data-act="approve"]').evaluate('node=>node.click()')
    settled(page)
    assert len(writes) == 1
    for complete in control["held"]:
        complete()
    control["held"].clear()
    control["hold_writes"] = False
    page.locator('[data-history-job="' + job_id(0) + '"][data-history-status="queued"]').wait_for()
    assert writes[0]['payload'] == {'action': 'approve'}
    assert not page.locator('#osAffiliateDialog').is_visible()
    records.append({"step": "approval-requires-confirmation-and-prevents-duplicate-post", "writes": len(writes)})

    queued = page.locator('[data-history-job="' + job_id(1) + '"]')
    queued.locator('[data-act="cancel-job"]').click()
    confirm(page)
    page.locator('[data-history-job="' + job_id(1) + '"][data-history-status="cancelled"]').wait_for()
    assert writes[-1]['payload'] == {'action': 'cancel'}
    for index in (3, 4, 9):
        row = page.locator('[data-history-job="' + job_id(index) + '"]')
        assert row.locator('[data-act="approve"],[data-act="cancel-job"],[data-act="retry"],[data-act="resend"]').count() == 0
    assert page.evaluate("window.__historyQA.root===document.querySelector('.af-history-workspace')&&performance.getEntriesByType('navigation').length===window.__historyQA.navigation")
    records.append({"step": "cancellation-updates-existing-screen-without-resending-confirmed-or-ambiguous-jobs"})
    (output / (name + "-interactions.json")).write_text(json.dumps(records, ensure_ascii=False, indent=2))
    return records


def run(context, width, theme, layout, engine, output, state_name):
    data = workspace(state_name)
    control = {"hold_reads": False, "held_reads": [], "fail_next_read": False,
        "hold_writes": False, "held": [], "fail_next_write": False, "fail_activate": False}
    calls, errors, blocked, writes = [], [], [], []
    install_routes(context, data, control, calls, errors, blocked, writes)
    context.add_init_script('localStorage.setItem("orion-theme", ' + json.dumps(theme) + ');' +
        "localStorage.setItem(" + json.dumps(f"orion-navigation-v1:{TENANT}:{EMAIL}") + "," + json.dumps(layout) + ");")
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    name = f"{state_name}-{layout}-{width}-{theme}"
    result = {"engine": engine, "width": width, "theme": theme, "layout": layout, "state": state_name}
    try:
        page.goto(ORIGIN + "/panel?aba=afiliados-fila", wait_until="domcontentloaded")
        page.locator('.af-history-workspace').wait_for()
        page.evaluate('document.fonts.ready')
        page.evaluate("""async()=>{
          await Promise.all((document.querySelector('#appView > .sidebar')?.getAnimations()||[]).map(a=>a.finished.catch(()=>{})));
          await new Promise(resolve=>requestAnimationFrame(resolve));
        }""")
        assert page.locator('.af-history-workspace h1').inner_text() == 'Fila e histórico'
        assert page.locator('#appView').evaluate("node=>node.classList.contains('os-layout-app')") == (layout == "app")
        result['initial'] = layout_metrics(page)
        assert result['initial']['theme'] == theme
        page.screenshot(path=str(output / (name + '-list.png')), full_page=True, animations='disabled')
        if state_name == 'empty':
            assert visible_jobs(page).count() == 0 and page.locator('#history-empty').is_visible()
            assert not writes
        else:
            assert visible_jobs(page).count() == 20
            full_flow = (width == 320 and theme == 'dark' and layout == 'app') or (width == 1280 and theme == 'light' and layout == 'classic')
            if full_flow:
                result['interactions'] = exercise(page, data, control, calls, writes, output, name)
            else:
                page.locator('[data-history-details]').first.click()
                settled(page)
                result['details'] = layout_metrics(page)
                page.screenshot(path=str(output / (name + '-details.png')), full_page=True, animations='disabled')
        page.evaluate("scrollTo({top:document.documentElement.scrollHeight,behavior:'instant'})")
        settled(page)
        result['bottom'] = page.evaluate("""()=>{
          const root=document.querySelector('.af-history-workspace'),dock=document.querySelector('#osAppDock');
          const end=root.getBoundingClientRect(),d=dock?.getBoundingClientRect();
          const overlaps=dock?.getClientRects().length&&d.height>0&&d.right>end.left&&d.left<end.right;
          return {end:end.bottom,dock:overlaps?d.top:innerHeight};
        }""")
        assert result['bottom']['end'] <= result['bottom']['dock'] + 2, result['bottom']
        page.screenshot(path=str(output / (name + '-bottom.png')), animations='disabled')
        assert not errors, errors
        assert not blocked, blocked
        assert all(re.fullmatch(re.escape(API) + r'jobs/[0-9a-f]{32}', write['path']) for write in writes)
        result.update(synthetic_queue_actions=len(writes), transport_requests=0, external_requests=0, requests=len(calls))
        (output / (name + '.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2))
        return result
    except Exception:
        page.screenshot(path=str(output / (name + '-failure.png')), full_page=True, animations='disabled')
        (output / (name + '-failure.json')).write_text(json.dumps({'result': result, 'calls': calls, 'writes': writes, 'errors': errors, 'blocked': blocked}, ensure_ascii=False, indent=2))
        raise
    finally:
        page.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', choices=['chromium', 'webkit'], default='chromium')
    parser.add_argument('--widths', type=int, nargs='+', default=[320, 430, 768, 1280])
    parser.add_argument('--themes', choices=['light', 'dark'], nargs='+', default=['light', 'dark'])
    parser.add_argument('--layouts', choices=['classic', 'app'], nargs='+', default=['classic', 'app'])
    parser.add_argument('--states', choices=['ready', 'empty'], nargs='+', default=['ready'])
    args = parser.parse_args()
    output = OUT / args.engine
    output.mkdir(parents=True, exist_ok=True)
    measurements = []
    with sync_playwright() as playwright:
        engine = getattr(playwright, args.engine)
        bundled = Path(engine.executable_path)
        executable = str(bundled) if bundled.is_file() else None
        if not executable and args.engine == 'chromium':
            executable = os.getenv('ORION_TEST_BROWSER') or shutil.which('chromium') or shutil.which('google-chrome')
        browser = engine.launch(headless=True, executable_path=executable)
        try:
            for width in args.widths:
                for theme in args.themes:
                    for layout in args.layouts:
                        for state_name in args.states:
                            context = browser.new_context(viewport={'width': width, 'height': 900}, is_mobile=width < 768,
                                has_touch=width < 768, device_scale_factor=1, service_workers='block')
                            try:
                                measurements.append(run(context, width, theme, layout, args.engine, output, state_name))
                                print(f'PASS {args.engine} {width}px {theme} {layout} {state_name}', flush=True)
                            finally:
                                context.close()
        finally:
            browser.close()
            (output / 'measurements.json').write_text(json.dumps(measurements, ensure_ascii=False, indent=2))
    print(f'PASS: {len(measurements)} history layouts; all publication actions stayed in memory', flush=True)


if __name__ == '__main__':
    main()
