"""Shipped Orion panel: six viewport sizes, two themes and isolated plans only.

Chromium/WebKit emulation is not physical-device, Safari or virtual-keyboard proof.
The HTTP fixture has no database, billing client, worker or external provider.
"""
from __future__ import annotations

import json
import hashlib
import os
import struct
from pathlib import Path
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright
import pytest

from scripts.plans_preview import Handler, PLAN_PATH, COMPANY_PLAN_PATH, UNASSIGNED_COMPANY

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = Path(os.getenv('PLAN_EVIDENCE_DIR', str(ROOT.parent / 'orion-plans-evidence-20261029' / 'browser')))
SIZES = [(1440, 900), (1366, 768), (768, 1024), (1024, 768), (390, 844), (360, 800)]


@pytest.fixture(scope='module')
def fixture_url():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown();server.server_close();thread.join()


@pytest.fixture(scope='module', params=['chromium', 'webkit'])
def browser(request):
    with sync_playwright() as playwright:
        instance = getattr(playwright, request.param).launch()
        yield request.param, instance
        instance.close()


@pytest.mark.parametrize('size', SIZES, ids=lambda size: f'{size[0]}x{size[1]}')
@pytest.mark.parametrize('theme', ['light', 'dark'])
@pytest.mark.parametrize('scenario', ['admin', 'essencial', 'profissional', 'elite', 'none', 'unassigned'])
def test_shipped_panel_plan_permissions(browser, fixture_url, size, theme, scenario):
    engine, instance = browser
    context = instance.new_context(viewport={'width': size[0], 'height': size[1]},
        color_scheme=theme, locale='pt-BR', has_touch=size[0] <= 390,
        is_mobile=engine == 'webkit' and size[0] <= 390, service_workers='block')
    allowed = urlsplit(fixture_url)
    unexpected_network, console_errors = [], []
    def route(request_route):
        url = urlsplit(request_route.request.url)
        if (url.scheme in {'http', 'https'} and (url.hostname, url.port) != (allowed.hostname, allowed.port)):
            unexpected_network.append(request_route.request.url);return request_route.abort()
        return request_route.continue_()
    context.route('**/*', route)
    context.add_init_script('localStorage.setItem("orion-theme", '+json.dumps(theme)+');')
    page = context.new_page();page.on('pageerror', lambda error: console_errors.append(str(error)))
    label = f'{engine}-{size[0]}x{size[1]}-{theme}-{scenario}'
    evidence={}
    source_files=['panel.html','orion-suite.js','orion-admin-center.js','orion-plans.js','orion-appointment-communications.js','orion-suite.css','orion-plans.css']
    source_hashes={name:hashlib.sha256((ROOT/'app'/'static'/name).read_bytes()).hexdigest() for name in source_files}
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    try:
        role, plan, tab = ('admin', 'essencial', 'plano' if scenario == 'unassigned' else 'member') if scenario in {'admin','unassigned'} else ('owner', scenario, 'plano')
        extra = '&fixtureUnassigned=1&fixtureDelay=1' if scenario == 'unassigned' else ''
        page.goto(f'{fixture_url}/panel?fixtureRole={role}&fixturePlan={plan}&aba={tab}{extra}')
        if scenario == 'unassigned':
            expect(page.locator('[data-op-effective-plan]')).to_be_visible()
            assert not any(call['path'].startswith('/api/v1/admin/accounts') for call in page.request.get(fixture_url+'/__fixture/calls').json()['calls'])
            destination=page.locator('#osNav [data-os-route=member]')
            if not destination.is_visible():
                page.locator('#osMenu').click()
            destination.click()
        if scenario in {'admin','unassigned'}:
            expect(page.locator('#adminClientCenter')).to_be_visible()
            expect(page.locator('#orionSetupAccess')).not_to_be_visible()
            assert page.locator('#orionSetupAccess').evaluate('n=>getComputedStyle(n).display')=='none'
            expect(page.locator('#adminAccountsCount')).to_contain_text('1 conta')
            page.locator('#adminClientCenter [data-admin-control=view]:visible').first.click()
            page.locator('#osDialog [data-admin-control=plan]').click()
            expect(page.locator('#adminManualPlan')).to_be_visible()
            page.select_option('#adminManualPlan', 'elite')
            page.select_option('#adminManualAccess', 'temporary')
            page.fill('#adminManualExpires', '2099-11-01T12:00')
            page.fill('#adminManualReason', 'Liberação sintética para validação visual')
            evidence['form']={'geometry':assert_geometry(page,size,modal=True),'capture':capture(page,ARTIFACTS/f'{label}-form.png',size)}
            page.locator('#osDialog').evaluate('n=>n.scrollTo(0,0)')
            evidence['form_top']={'geometry':assert_geometry(page,size,modal=True),'capture':capture(page,ARTIFACTS/f'{label}-form-top.png',size)}
            page.locator('[data-admin-form=plan] [type=submit]').click()
            expect(page.locator('[data-admin-form=plan-confirm]')).to_be_visible()
            snapshot = page.request.get(fixture_url + '/__fixture/calls').json()
            assert not [call for call in snapshot['calls'] if call['method'] != 'GET']
            page.locator('[data-admin-control=plan-back]').click()
            expect(page.locator('#adminManualReason')).to_have_value('Liberação sintética para validação visual')
            expect(page.locator('#adminManualExpires')).to_have_value('2099-11-01T12:00')
            page.locator('[data-admin-form=plan] [type=submit]').click()
            evidence['review']={'geometry':assert_geometry(page,size,modal=True),'capture':capture(page,ARTIFACTS/f'{label}-review.png',size)}
            page.locator('[data-admin-form=plan-confirm] [type=submit]').click()
            expect(page.locator('#osDialog')).not_to_be_visible()
            expect(page.locator('#adminAccountsResults')).to_contain_text('Elite')
            page.goto(fixture_url+'/panel?aba=member')
            expect(page.locator('#adminClientCenter')).to_be_visible()
            expect(page.locator('#adminAccountsResults')).to_contain_text('Elite')
            evidence['granted']={'geometry':assert_geometry(page,size),'capture':capture(page,ARTIFACTS/f'{label}-granted.png',size)}
            snapshot = page.request.get(fixture_url + '/__fixture/calls').json()
            writes = [call for call in snapshot['calls'] if call['method'] != 'GET']
            assert len(writes) == 1 and writes[0]['path'] == PLAN_PATH and writes[0]['method'] == 'PUT'
            origin=page.locator('#adminClientCenter [data-admin-control=view]:visible').first
            origin.click();page.locator('#osDialog [data-admin-control=plan]').click()
            history=page.locator('#osDialog details.os-more-fields').last
            history.locator('summary').click()
            expect(history.locator('.os-disclosure-body')).to_be_visible()
            expect(history).to_contain_text('Liberação sintética para validação visual')
            expect(history).to_contain_text('Orion Elite')
            history_data=page.request.get(fixture_url+PLAN_PATH).json()['history'][0]
            assert history_data['actor_id'] and not history_data.get('actor_name')
            expect(history).to_contain_text('Administrador: Administrador geral')
            expect(history).not_to_contain_text(history_data['actor_id'])
            administrator=history.locator('.os-help').last
            administrator.scroll_into_view_if_needed()
            expect(administrator).to_be_in_viewport(ratio=1)
            evidence['history']={'geometry':assert_geometry(page,size,modal=True),'capture':capture(page,ARTIFACTS/f'{label}-history.png',size)}
            page.keyboard.press('Escape')
            expect(page.locator('#osDialog')).not_to_be_visible()
            expect(origin).to_be_focused()
            origin.click();page.locator('#osDialog [data-admin-control=plan]').click()
            page.locator('[data-admin-control=remove-plan]').click()
            expect(page.locator('#osDialog')).to_contain_text('recalculado pela assinatura comercial')
            snapshot=page.request.get(fixture_url+'/__fixture/calls').json()
            assert len([call for call in snapshot['calls'] if call['method']!='GET'])==1
            evidence['removal_review']={'geometry':assert_geometry(page,size,modal=True),'capture':capture(page,ARTIFACTS/f'{label}-removal.png',size)}
            page.locator('[data-admin-form=plan-confirm] [type=submit]').click()
            expect(page.locator('#osDialog')).not_to_be_visible()
            expect(page.locator('#adminAccountsResults')).to_contain_text('Orion Essencial')
            snapshot=page.request.get(fixture_url+'/__fixture/calls').json()
            writes=[call for call in snapshot['calls'] if call['method']!='GET']
            assert len(writes)==2 and writes[-1]['path']==PLAN_PATH and writes[-1]['method']=='DELETE'
            assert page.request.get(fixture_url+'/auth/me').json()['role']=='admin'
            if scenario == 'unassigned':
                snapshot=exercise_unassigned_company(page,fixture_url,size,evidence,label)
        else:
            expect(page.locator('[data-op-effective-plan]')).to_be_visible()
            for target in ['marketing', 'desempenho', 'consultor', 'fidelizacao']:
                role_allowed = scenario in {'profissional', 'elite'} or scenario == 'essencial' and target == 'desempenho'
                assert page.locator(f'#osNav [data-os-route={target}]').count() == (1 if role_allowed else 0)
            if scenario == 'essencial':
                assert page.locator('#osNav [data-os-route=inbox]').count() == 0
                assert page.locator('#osNav [data-os-action=managed-whatsapp]').count() == 0
            if scenario in {'profissional','elite'}:
                assert page.locator('#osNav [data-os-route=inbox]').count() == 1
                assert page.locator('#osNav [data-os-action=managed-whatsapp]').count() == 1
            if scenario == 'none':
                assert page.locator('#osNav [data-os-route=agenda]').count() == 0
                assert page.locator('#osNav [data-os-route=configuracoes]').count() == 1
            else:
                expect(page.locator('[data-op-effective-plan]')).to_contain_text('Liberação manual')
                for button in page.locator('[data-op-checkout]').all():
                    expect(button).to_be_disabled()
            evidence['plan']={'geometry':assert_geometry(page,size),'capture':capture(page,ARTIFACTS/f'{label}.png',size)}
            if scenario in {'profissional', 'elite'}:
                companion=context.new_page();companion.goto(fixture_url+'/panel?aba=plano')
                expect(companion.locator('[data-op-effective-plan]')).to_be_visible()
                page.request.post(fixture_url + '/__fixture/plan', data={'plan': 'essencial'})
                page.evaluate('''() => {const channel=new BroadcastChannel('orion-plan-access');channel.postMessage({type:'changed',tenantId:'10000000-0000-4000-8000-000000000001'});channel.close();}''')
                expect(companion.locator('#osNav [data-os-route=consultor]')).to_have_count(0)
                companion.close()
                expect(page.locator('#osNav [data-os-route=consultor]')).to_have_count(0)
                expect(page.locator('[data-op-effective-plan]')).to_contain_text('Essencial')
                evidence['downgrade']={'geometry':assert_geometry(page,size),'capture':capture(page,ARTIFACTS/f'{label}-downgrade.png',size)}
            snapshot = page.request.get(fixture_url + '/__fixture/calls').json()
            writes = [call for call in snapshot['calls'] if call['method'] != 'GET' and call['path'] != '/__fixture/plan']
            assert not writes
        assert not unexpected_network, unexpected_network
        assert not console_errors, console_errors
        assert not snapshot['unexpected'], snapshot['unexpected']
        assert source_hashes=={name:hashlib.sha256((ROOT/'app'/'static'/name).read_bytes()).hexdigest() for name in source_files},'UI assets changed during the evidence capture'
        (ARTIFACTS / f'{label}.json').write_text(json.dumps({'engine': engine, 'viewport': size, 'theme': theme,
            'scenario': scenario, 'source_hashes':source_hashes, 'measured_states':evidence, 'calls': snapshot['calls'], 'external_requests': unexpected_network,
            'limitations': ['synthetic state only', 'viewport emulation', 'no physical device or keyboard', 'no provider or payment']}, indent=2), encoding='utf-8')
    finally:
        context.close()


def exercise_unassigned_company(page,fixture_url,size,evidence,label):
    section=page.locator('#adminUnassignedCompanies')
    expect(section).to_be_visible()
    expect(section).to_contain_text('Empresas com vínculo pendente')
    expect(section.locator('[data-admin-company-id]')).to_have_count(5)
    assert section.locator('[data-account-id],[data-admin-control=maintenance],[data-account-action]').count()==0
    section.scroll_into_view_if_needed()
    evidence['company_list']={'geometry':assert_geometry(page,size),'capture':capture(page,ARTIFACTS/f'{label}-companies.png',size)}
    page.locator('#adminAccountsSearch').fill('pausada sintética')
    expect(section.locator('[data-admin-company-id]')).to_have_count(1)
    expect(page.locator('#adminAccountsCount')).to_contain_text('0 contas')
    card=section.locator(f'[data-admin-company-id="{UNASSIGNED_COMPANY}"]')
    expect(card).to_contain_text('Pausada')
    trigger=card.locator('[data-admin-control=plan]')
    trigger.click()
    expect(page.locator('#adminManualPlan')).to_be_visible()
    expect(page.locator('#osDialog')).to_contain_text('Conta de proprietário indisponível')
    expect(page.locator('#osDialog')).to_contain_text('Pausada')
    page.select_option('#adminManualPlan','elite')
    page.fill('#adminManualReason','Liberação sintética da empresa com vínculo pendente')
    page.locator('#osDialog').evaluate('n=>n.scrollTo(0,0)')
    evidence['company_form']={'geometry':assert_geometry(page,size,modal=True),'capture':capture(page,ARTIFACTS/f'{label}-company-form.png',size)}
    page.locator('[data-admin-form=plan] [type=submit]').click()
    expect(page.locator('[data-admin-form=plan-confirm]')).to_be_visible()
    before=page.request.get(fixture_url+'/__fixture/calls').json()
    assert len([c for c in before['calls'] if c['method']!='GET'])==2
    expect(page.locator('#osDialog')).to_contain_text('vínculo do proprietário')
    evidence['company_review']={'geometry':assert_geometry(page,size,modal=True),'capture':capture(page,ARTIFACTS/f'{label}-company-review.png',size)}
    page.locator('[data-admin-form=plan-confirm] [type=submit]').click()
    expect(page.locator('#osDialog')).not_to_be_visible()
    expect(card).to_contain_text('Orion Elite')
    expect(card).to_contain_text('Pausada')
    granted=page.request.get(fixture_url+COMPANY_PLAN_PATH).json()
    assert granted['company']['account_id'] is None and granted['company']['status']=='paused'
    assert granted['effective_plan']['code']=='elite' and granted['effective_plan']['active'] is False
    assert len(granted['history'])==1
    trigger=card.locator('[data-admin-control=plan]');trigger.click()
    history=page.locator('#osDialog details.os-more-fields').last
    history.locator('summary').click()
    expect(history).to_contain_text('Orion Elite')
    expect(history).to_contain_text('Administrador: Administrador geral')
    expect(history).not_to_contain_text(granted['history'][0]['actor_id'])
    history.locator('.os-help').last.scroll_into_view_if_needed()
    expect(history.locator('.os-help').last).to_be_in_viewport(ratio=1)
    evidence['company_history']={'geometry':assert_geometry(page,size,modal=True),'capture':capture(page,ARTIFACTS/f'{label}-company-history.png',size)}
    page.keyboard.press('Escape');expect(page.locator('#osDialog')).not_to_be_visible();expect(trigger).to_be_focused()
    trigger.click();page.locator('[data-admin-control=remove-plan]').click()
    expect(page.locator('[data-admin-form=plan-confirm]')).to_be_visible()
    evidence['company_removal']={'geometry':assert_geometry(page,size,modal=True),'capture':capture(page,ARTIFACTS/f'{label}-company-removal.png',size)}
    page.locator('[data-admin-form=plan-confirm] [type=submit]').click()
    expect(page.locator('#osDialog')).not_to_be_visible()
    expect(card).to_contain_text('Sem plano ativo');expect(card).to_contain_text('Pausada')
    removed=page.request.get(fixture_url+COMPANY_PLAN_PATH).json()
    assert removed['company']['account_id'] is None and removed['company']['status']=='paused'
    assert removed['effective_plan']['code'] is None and removed['effective_plan']['active'] is False
    assert len(removed['history'])==2
    normal=page.request.get(fixture_url+PLAN_PATH).json()
    assert normal['company']['account_id'] and normal['effective_plan']['code']=='essencial'
    # The fixture retains its existing authenticated cookie, company data and
    # delayed reads; direct navigation must not need a second click or Refresh.
    page.goto(fixture_url+'/panel?aba=member')
    expect(page.locator('#adminAccountsCount')).to_contain_text('1 conta')
    expect(page.locator('#adminUnassignedCompanies [data-admin-company-id]')).to_have_count(5)
    expect(page.locator(f'[data-admin-company-id="{UNASSIGNED_COMPANY}"]')).to_contain_text('Sem plano ativo')
    evidence['company_reload']={'geometry':assert_geometry(page,size),'capture':capture(page,ARTIFACTS/f'{label}-company-reload.png',size)}
    snapshot=page.request.get(fixture_url+'/__fixture/calls').json()
    writes=[c for c in snapshot['calls'] if c['method']!='GET']
    assert [(c['method'],c['path']) for c in writes]==[('PUT',PLAN_PATH),('DELETE',PLAN_PATH),('PUT',COMPANY_PLAN_PATH),('DELETE',COMPANY_PLAN_PATH)]
    assert not snapshot['unexpected']
    return snapshot


def capture(page,path,size):
    raw=page.screenshot(path=str(path),full_page=False)
    assert raw[:8]==b'\x89PNG\r\n\x1a\n' and raw[12:16]==b'IHDR'
    png=struct.unpack('>II',raw[16:24])
    measured=page.evaluate('''() => {const dialog=document.querySelector('#osDialog[open]');return {width:innerWidth,height:innerHeight,scrollX,scrollY,dpr:devicePixelRatio,fonts:document.fonts.status,theme:document.documentElement.dataset.theme,dialog_scroll:dialog?{top:dialog.scrollTop,left:dialog.scrollLeft,height:dialog.scrollHeight,client_height:dialog.clientHeight}:null,focus:{tag:document.activeElement?.tagName,id:document.activeElement?.id,name:document.activeElement?.getAttribute('name')}}}''')
    assert (measured['width'],measured['height'])==size
    assert png==(round(measured['width']*measured['dpr']),round(measured['height']*measured['dpr'])),(png,measured)
    return {'file':str(path),'png_width':png[0],'png_height':png[1],**measured}


def assert_geometry(page, size, modal=False):
    measured = page.evaluate('''() => {
      const d=document.querySelector('#osDialog[open]'),r=d?.getBoundingClientRect();
      return {width:innerWidth,height:innerHeight,scroll:document.documentElement.scrollWidth,
        modal:r?{left:r.left,right:r.right,top:r.top,bottom:r.bottom,scroll:d.scrollWidth,client:d.clientWidth,scrollTop:d.scrollTop,scrollHeight:d.scrollHeight,clientHeight:d.clientHeight}:null,
        inputs:d?[...d.querySelectorAll('input:not([type=checkbox]):not([disabled]),select,textarea')].filter(n=>n.getClientRects().length).map(n=>({width:n.getBoundingClientRect().width,scroll:n.scrollWidth,client:n.clientWidth})):[]};
    }''')
    assert (measured['width'], measured['height']) == size
    assert measured['scroll'] <= size[0] + 1, measured
    if modal:
        rect = measured['modal'];assert rect
        assert rect['left'] >= -1 and rect['right'] <= size[0]+1, measured
        assert rect['top'] >= -1 and rect['bottom'] <= size[1]+1, measured
        assert rect['scroll'] <= rect['client']+1, measured
        assert all(item['width'] >= 200 for item in measured['inputs']), measured
    return measured
