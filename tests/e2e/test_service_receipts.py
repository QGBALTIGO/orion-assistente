"""Receipt configuration, cash confirmation and Agenda integration on real browsers."""
import os
import re
from pathlib import Path
from urllib.parse import urlsplit
import pytest
from scripts.plan_fixture import synthetic_plan_state

sync_playwright=pytest.importorskip('playwright.sync_api').sync_playwright
ROOT=Path(__file__).resolve().parents[2]
ARTIFACTS=ROOT/'test-artifacts'/'service-receipts'
HTML='''<!doctype html><html lang="pt-BR" data-theme="dark"><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>*{box-sizing:border-box}html,body{margin:0}.hidden{display:none!important}</style></head><body><div id="appView" class="app"><aside class="sidebar"><div class="brand">Orion</div></aside><main class="content"><section id="page-original" class="page"></section></main></div></body></html>'''


@pytest.mark.parametrize('browser_name,width,height,theme',[
 ('webkit',320,693,'dark'),('webkit',375,812,'dark'),('webkit',430,932,'light'),
 ('chromium',360,800,'light'),('chromium',768,1024,'dark'),
 ('chromium',1024,768,'light'),('chromium',1366,900,'dark')])
def test_receipts(browser_name,width,height,theme):
 with sync_playwright() as p:
  browser=getattr(p,browser_name).launch()
  context=browser.new_context(viewport={'width':width,'height':height},is_mobile=width<768,has_touch=width<768)
  page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
  try:
   page.route('**/*',lambda route:route.abort())
   def asset(route):
    name=urlsplit(route.request.url).path.rsplit('/',1)[-1]
    path=ROOT/'app/static'/name
    if path.is_file() and name in ['figtree-latin.woff2','outfit-latin.woff2','orion-brand-dark.svg','orion-brand-light.svg']:
     route.fulfill(status=200,body=path.read_bytes(),content_type='font/woff2' if name.endswith('woff2') else 'image/svg+xml')
    else:route.abort()
   page.route('https://orion.test/brand/*',asset)
   page.route('https://orion.test/panel',lambda route:route.fulfill(status=200,body=HTML,content_type='text/html'))
   page.goto('https://orion.test/panel',wait_until='domcontentloaded')
   page.evaluate('theme=>document.documentElement.dataset.theme=theme',theme)
   page.add_style_tag(content='\n'.join(re.findall(r'<style[^>]*>([\s\S]*?)</style>',(ROOT/'app/static/panel.html').read_text())))
   for name in ['orion-suite.css','orion-fields.css']:page.add_style_tag(content=(ROOT/'app/static'/name).read_text())
   page.add_script_tag(content=(ROOT/'app/static/orion-fields.js').read_text())
   page.evaluate('()=>OrionFields.bind(document)')
   page.add_script_tag(content=(ROOT/'app/static/orion-suite.js').read_text())
   page.add_script_tag(content=(ROOT/'tests/e2e/fixtures/service_receipts.js').read_text())
   page.evaluate('plan=>mountReceiptFixture(plan)',synthetic_plan_state('10000000-0000-4000-8000-000000000001'))
   page.wait_for_selector('[data-os-receipt]');page.evaluate('()=>document.fonts.ready')
   ARTIFACTS.mkdir(parents=True,exist_ok=True);stem=f'{browser_name}-{width}x{height}-{theme}'
   def contained(selector='.os-main'):
    for item in page.locator(selector).evaluate_all('nodes=>nodes.map(el=>{const r=el.getBoundingClientRect();return {x:r.x,right:r.right,scroll:el.scrollWidth,width:el.clientWidth}})'):
     assert item['x']>=-2 and item['right']<=width+2,item
     assert item['scroll']<=item['width']+2,item
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
   contained();page.screenshot(path=str(ARTIFACTS/f'{stem}-finance.png'),full_page=width<768)
   page.locator('[data-os-action=finance-receivables]').click()
   page.wait_for_selector('.os-receipt-scope')
   assert page.evaluate("receiptFixtureCalls.some(c=>c.path.includes('receivables=true'))")
   page.locator('[data-os-action=receipt-settings]').click();page.wait_for_selector('[name=receipt_mode]')
   page.select_option('[name=receipt_mode]','after_24h')
   page.locator('[data-os-form=receipt-settings] [type=submit]').click()
   page.wait_for_function("receiptFixtureCalls.some(c=>c.method==='PUT'&&c.data.mode==='after_24h')")
   page.wait_for_function("document.querySelector('[name=receipt_mode]')?.value==='after_24h'")
   contained();page.screenshot(path=str(ARTIFACTS/f'{stem}-settings.png'),full_page=width<768)
   page.evaluate("()=>OrionSuite.open('financeiro')");page.wait_for_selector('[data-os-receipt]')
   page.locator('[data-os-receipt]').click();page.wait_for_selector('[name=receipt_method]')
   contained('#osDialog');page.screenshot(path=str(ARTIFACTS/f'{stem}-confirm.png'),full_page=False)
   page.locator('[data-os-action=receipt-hold]').click();page.locator('[data-os-form=service-receipt] [type=submit]').click()
   page.wait_for_function("!document.querySelector('#osDialog').open")
   page.locator('[data-os-receipt]').click();page.wait_for_selector('[data-os-action=receipt-resume]')
   page.locator('[data-os-action=receipt-resume]').click();page.locator('[data-os-form=service-receipt] [type=submit]').click()
   page.wait_for_function("!document.querySelector('#osDialog').open")
   page.locator('[data-os-receipt]').click();page.wait_for_selector('[name=receipt_method]')
   page.select_option('[name=receipt_method]','pix');page.locator('[data-os-form=service-receipt] [type=submit]').click()
   page.wait_for_function("!document.querySelector('#osDialog').open")
   page.locator('[data-os-receipt]').click();page.wait_for_selector('[data-os-action=receipt-reopen]')
   page.locator('[data-os-action=receipt-reopen]').click();page.locator('[data-os-form=service-receipt] [type=submit]').click()
   page.wait_for_function("!document.querySelector('#osDialog').open")
   page.evaluate("()=>OrionSuite.open('agenda')");page.wait_for_selector('[data-os-appointment]')
   page.locator('[data-os-appointment]').first.click();page.wait_for_selector('[data-os-action=appointment-receipt]')
   contained('#osDialog');page.screenshot(path=str(ARTIFACTS/f'{stem}-agenda.png'),full_page=False)
   page.locator('[data-os-action=appointment-receipt]').click();page.wait_for_selector('[name=receipt_method]')
   page.select_option('[name=receipt_method]','cash');page.locator('[data-os-form=service-receipt] [type=submit]').click()
   page.wait_for_selector('[data-os-action=appointment-receipt]')
   assert 'Confirmado pela equipe' in page.locator('#osAppointmentReceipts').inner_text()
   assert not errors,errors
  finally:
   context.close();browser.close()
