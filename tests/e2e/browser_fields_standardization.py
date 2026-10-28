"""Native fields, conditional units and geometry on the real suite components.

The six agreed CSS viewports run in Chromium and emulated WebKit, light/dark.
All reads and writes are synthetic. WebKit emulation is not a physical iPhone.
"""
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright

from test_finance_responsive import HTML


ROOT=Path(__file__).resolve().parents[2]
ARTIFACTS=ROOT/'test-artifacts'/'fields-standardization'
INITIALIZE=(ROOT/'tests/e2e/fixtures/fields-panel.js').read_text(encoding='utf-8')
SIZES=[(360,800),(390,844),(430,932),(768,1024),(1024,768),(1440,900)]
BRAND={name:ROOT/'app/static'/name for name in ('figtree-latin.woff2','outfit-latin.woff2',
    'orion-brand-light.svg','orion-brand-dark.svg','orion-symbol.svg')}


@pytest.mark.parametrize('engine',['chromium','webkit'])
@pytest.mark.parametrize('width,height',SIZES)
@pytest.mark.parametrize('theme',['light','dark'])
def test_native_fields_units_errors_and_all_visible_control_geometry(engine,width,height,theme):
    ARTIFACTS.mkdir(parents=True,exist_ok=True)
    stem=f'{engine}-{width}x{height}-{theme}'
    metrics=[]
    with sync_playwright() as playwright:
        browser=getattr(playwright,engine).launch(headless=True)
        page=browser.new_page(viewport={'width':width,'height':height},locale='pt-BR',
            color_scheme=theme,device_scale_factor=2,has_touch=width<=430,
            is_mobile=engine=='webkit' and width<=430)
        page.set_default_timeout(10000)
        errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        try:
            page.route('**/*',lambda route:route.abort())
            def brand_asset(route):
                name=urlsplit(route.request.url).path.rsplit('/',1)[-1]
                source=BRAND.get(name)
                if source is None:
                    route.abort()
                else:
                    route.fulfill(status=200,content_type='font/woff2' if name.endswith('.woff2') else 'image/svg+xml',body=source.read_bytes())
            page.route('https://orion.test/brand/*',brand_asset)
            page.route('https://orion.test/panel',lambda route:route.fulfill(status=200,
                content_type='text/html',body=HTML.replace('data-theme="dark"',f'data-theme="{theme}"')))
            page.goto('https://orion.test/panel',wait_until='domcontentloaded')
            page.evaluate('(theme)=>localStorage.setItem("orion-theme",theme)',theme)
            for name in ('orion-suite.css','orion-fields.css'):
                page.add_style_tag(content=(ROOT/'app/static'/name).read_text(encoding='utf-8'))
            for name in ('orion-fields.js','orion-suite.js'):
                page.add_script_tag(content=(ROOT/'app/static'/name).read_text(encoding='utf-8'))
            page.evaluate(INITIALIZE)

            def form(kind):
                node=page.locator(f'[data-os-form="{kind}"]')
                node.wait_for(state='visible')
                return node

            def geometry(kind,state='filled'):
                target=form(kind)
                target.locator('details').evaluate_all('(nodes)=>nodes.forEach(node=>node.open=true)')
                value=target.evaluate('''form=>({
                  width:innerWidth,document:document.documentElement.scrollWidth,
                  controls:[...form.querySelectorAll('input,select,textarea')].filter(el=>{
                    const r=el.getBoundingClientRect();return el.type!=='hidden'&&r.width>0&&r.height>0&&getComputedStyle(el).visibility!=='hidden';
                  }).map(el=>{const r=el.getBoundingClientRect(),css=getComputedStyle(el);return {
                    name:el.name||el.id,type:el.type,left:r.left,right:r.right,width:r.width,height:r.height,
                    font:css.fontSize,disabled:el.disabled,invalid:el.getAttribute('aria-invalid'),
                    label:!!(el.labels?.length||el.getAttribute('aria-label')||el.getAttribute('aria-labelledby'))};}),
                  errors:[...form.querySelectorAll('.orion-field-error')].map(el=>{const r=el.getBoundingClientRect();return {left:r.left,right:r.right,text:el.textContent};})
                })''')
                value.update(form=kind,state=state)
                metrics.append(value)
                assert value['controls'],value
                assert value['document']<=width+2,value
                for control in value['controls']:
                    assert control['left']>=-2 and control['right']<=width+2,(kind,state,control)
                    assert control['label'],(kind,'missing accessible label',control)
                    if width<=430 and control['type'] not in ('checkbox','radio','color','file'):
                        assert float(control['font'].removesuffix('px'))>=16,(kind,control)
                for error in value['errors']:
                    assert error['left']>=-2 and error['right']<=width+2,(kind,error)
                return target

            def route(name):
                if page.locator('#osDialog[open]').count():
                    page.locator('#osDialog [data-os-action="close-dialog"]').first.click()
                page.evaluate('(route)=>window.OrionSuite.open(route)',name)

            page.locator('[data-os-customer]:visible').first.click()
            page.evaluate('document.fonts.ready')
            customer=form('customer')
            phone=customer.locator('[name=phone]')
            expect(phone).to_have_value('(55) 99123-4567')
            phone.fill('(55) 99123-45678')
            customer.locator('button[type=submit]').click()
            expect(phone).to_have_attribute('aria-invalid','true')
            assert page.evaluate('window.__fieldsQA.writes.length')==0
            described=phone.get_attribute('aria-describedby').split()
            assert any(page.locator('[id="'+value+'"]').count() for value in described)
            geometry('customer','invalid-phone')
            birth=customer.locator('[name=birth_date]')
            expect(birth).to_have_attribute('type','date')
            expect(birth).to_have_value('2000-02-29')
            birth.fill('1990-07-06')
            expect(birth).to_have_value('1990-07-06')
            phone.fill('67912345678')
            phone.evaluate('(el)=>el.setSelectionRange(11,11)')
            phone.press('Backspace')
            assert ''.join(filter(str.isdigit,phone.input_value()))=='6791235678'
            phone.fill('+55 (67) 99999-9901')
            expect(phone).to_have_value('(67) 99999-9901')
            geometry('customer')
            customer.locator('button[type=submit]').click()
            expect(page.locator('#osDialog')).not_to_have_attribute('open','')
            written=page.evaluate('window.__fieldsQA.writes[0].data')
            assert written['phone']=='+5567999999901'
            assert written['name']=="Ana D'Ávila-Souza"

            route('servicos')
            page.locator('[data-os-action="new-service"]:visible').first.click()
            service=form('service')
            service.locator('[name=name]').fill('Serviço sintético')
            price=service.locator('[name=price]')
            price.fill('')
            price.press_sequentially('1234')
            expect(price).to_have_value('1.234,00')
            assert price.evaluate('(el)=>el.selectionStart')==5
            price.press_sequentially(',56')
            expect(price).to_have_value('1.234,56')
            geometry('service')
            service.locator('button[type=submit]').click()
            expect(page.locator('#osDialog')).not_to_have_attribute('open','')
            assert page.evaluate('window.__fieldsQA.writes.find(row=>row.path.endsWith("/services")).data.price_cents')==123456

            route('financeiro')
            page.locator('[data-os-action="new-expense"]:visible').first.click()
            finance=form('finance')
            finance.locator('[name=amount]').fill('1.234')
            expect(finance.locator('[name=amount]')).to_have_value('1.234,00')
            geometry('finance')

            route('funcionarios')
            page.locator('[data-os-professional]:visible').first.click()
            professional=form('professional')
            professional.locator('[name=commission_mode]').select_option('per_service')
            commission=professional.locator('[name^=commission_value_]').first
            kind=professional.locator('[name^=commission_kind_]').first
            kind.select_option('fixed')
            commission.fill('1234')
            expect(commission).to_have_value('1.234,00')
            geometry('professional','fixed-commission')
            kind.select_option('percent')
            commission.fill('12,5')
            expect(commission).to_have_value('12,5')
            geometry('professional','percent-commission')

            route('configuracoes')
            preferences=form('business-preferences')
            preferences.locator('[name=tax_document_kind]').select_option('cpf')
            tax=preferences.locator('[name=tax_document]')
            tax.fill('52998224724')
            writes_before=page.evaluate('window.__fieldsQA.writes.length')
            preferences.locator('button[type=submit]').click()
            expect(tax).to_have_attribute('aria-invalid','true')
            assert page.evaluate('window.__fieldsQA.writes.length')==writes_before
            geometry('business-preferences','invalid-cpf')
            tax.fill('01234567890')
            expect(tax).to_have_value('012.345.678-90')
            geometry('business-preferences','cpf')
            preferences.locator('[name=tax_document_kind]').select_option('cnpj')
            tax.fill('12ABC34501DE35')
            expect(tax).to_have_value('12.ABC.345/01DE-35')
            geometry('business-preferences','alphanumeric-cnpj')

            route('site')
            page.locator('[data-os-site-tab="settings"]:visible').click()
            site=form('site')
            site.locator('[name=address_number]').fill('64')
            site.locator('[name=address_complement]').fill('Sala 2')
            site.locator('[name=postal_code]').fill('01001000')
            expect(site.locator('[name=postal_code]')).to_have_value('01001-000')
            expect(site.locator('[name=city]')).to_have_value('São Paulo')
            expect(site.locator('[name=address_number]')).to_have_value('64')
            expect(site.locator('[name=address_complement]')).to_have_value('Sala 2')
            geometry('site','viacep')

            route('fidelizacao')
            page.locator('[data-os-loyalty-tab="settings"]:visible').click()
            loyalty=form('loyalty-inline')
            reward=loyalty.locator('[name=reward_value]')
            loyalty.locator('[name=reward_type]').select_option('fixed')
            reward.fill('1234')
            expect(reward).to_have_value('1.234,00')
            geometry('loyalty-inline','fixed-reward')
            loyalty.locator('[name=reward_type]').select_option('percent')
            reward.fill('12,5')
            expect(reward).to_have_value('12,5')
            geometry('loyalty-inline','percent-reward')
            loyalty.locator('[name=reward_type]').select_option('free_service')
            expect(reward).to_be_hidden()
            expect(reward).to_be_disabled()
            expect(loyalty.locator('[name=reward_service_id]')).to_be_visible()
            geometry('loyalty-inline','free-service')

            route('desempenho')
            page.locator('[data-os-action="new-goal"]:visible').first.click()
            goal=form('goal')
            goal.locator('[name=scope]').select_option('company')
            goal.locator('[name=metric]').select_option('revenue_cents')
            target=goal.locator('[name=target]')
            target.fill('1234')
            expect(target).to_have_value('1.234,00')
            geometry('goal','revenue')
            goal.locator('[name=metric]').select_option('appointments')
            target.fill('1234')
            expect(target).to_have_value('1234')
            geometry('goal','appointment-count')
            assert not errors,errors
        finally:
            (ARTIFACTS/(stem+'.json')).write_text(json.dumps(metrics,ensure_ascii=False,indent=2),encoding='utf-8')
            if not page.is_closed():
                page.screenshot(path=str(ARTIFACTS/(stem+'.png')),full_page=True)
            browser.close()
