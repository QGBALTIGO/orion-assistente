"""WhatsApp connector qualification using shipped assets and loopback mocks only.

Chromium/WebKit viewport emulation is not a physical-device or virtual-keyboard
test. The fixture cannot reach a database, provider, bridge or real account.
"""
from __future__ import annotations

import json
import re
import struct
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright

from scripts.whatsapp_connect_preview import BASE, CHANNEL, Handler

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / 'test-artifacts' / 'whatsapp-connect'
SIZES = [(1440, 900), (1366, 768), (768, 1024), (1024, 768), (390, 844), (360, 800)]


@pytest.fixture(scope='module')
def fixture_url():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.fixture(scope='module', params=['chromium', 'webkit'])
def browser(request):
    with sync_playwright() as playwright:
        instance = getattr(playwright, request.param).launch()
        yield request.param, instance
        instance.close()


@pytest.fixture(params=SIZES, ids=lambda size: f'{size[0]}x{size[1]}')
def size(request):
    return request.param


@pytest.fixture(params=['light', 'dark'])
def theme(request):
    return request.param


@pytest.fixture
def panel_factory(browser, fixture_url, size, theme, request):
    engine, instance = browser
    contexts = []
    expected_origin = urlsplit(fixture_url)

    def open_panel(role='admin', state='disconnected', *, connector=True):
        width, height = size
        context = instance.new_context(viewport={'width': width, 'height': height},
            color_scheme=theme, locale='pt-BR', has_touch=width <= 390,
            is_mobile=engine == 'webkit' and width <= 390, service_workers='block')
        outside, errors = [], []

        def local_only(route):
            target = urlsplit(route.request.url)
            if (target.scheme, target.netloc) == (expected_origin.scheme, expected_origin.netloc):
                route.continue_()
            else:
                outside.append(route.request.url)
                route.abort('blockedbyclient')

        context.route('**/*', local_only)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        contexts.append((context, page, outside, errors))
        route = 'dispositivo' if role == 'admin' else 'configuracoes'
        page.goto(f'{fixture_url}/panel?fixtureRole={role}&fixtureState={state}&aba={route}')
        expect(page.locator('#appView')).to_be_visible()
        if role == 'admin':
            expect(page.locator('#disconnectedWizard' if state != 'connected' else '#connectedCenter')).to_be_visible()
        else:
            expect(page.locator('#osSettingsSection')).to_be_attached()
            if connector:
                if page.locator('#osSettingsSection').is_visible():
                    page.locator('#osSettingsSection').select_option('whatsapp')
                else:
                    page.locator('[data-os-settings="whatsapp"]').click()
                expect(page.locator('#osManagedChannels [data-qc-channel]')).to_have_count(1)
        page.evaluate('document.fonts.ready')
        return page

    yield open_panel
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    for index, (context, page, outside, errors) in enumerate(contexts):
        try:
            response = context.request.get(fixture_url + '/__fixture/calls')
            report = response.json()
            report.update({'engine': engine, 'viewport': {'width': size[0], 'height': size[1]},
                'theme': theme, 'method': 'browser viewport emulation', 'physical_device': False,
                'virtual_keyboard_tested': False, 'outside_requests': outside, 'page_errors': errors})
            stem = request.node.name.replace('[', '-').replace(']', '') + f'-{index}'
            report['capture'] = capture_current_viewport(page, ARTIFACTS / f'{stem}-last.png')
            (ARTIFACTS / f'{stem}-calls.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            assert not outside, 'A browser request tried to leave the synthetic origin'
            assert not errors, errors
            assert not report['unexpected'], report['unexpected']
            assert all(not call['path'].endswith('/send') for call in report['calls'])
        finally:
            context.close()


def calls(page):
    url = urlsplit(page.url)
    return page.request.get(f'{url.scheme}://{url.netloc}/__fixture/calls').json()['calls']


def writes(page, suffix=None):
    return [call for call in calls(page) if call['method'] != 'GET' and
            (suffix is None or call['path'].endswith(suffix))]


def png_dimensions(image_bytes):
    assert image_bytes[:8] == b'\x89PNG\r\n\x1a\n' and image_bytes[12:16] == b'IHDR'
    width, height = struct.unpack('!II', image_bytes[16:24])
    return {'width': width, 'height': height}


def view_state(page):
    return page.evaluate('''() => ({scroll:{x:scrollX,y:scrollY},
      viewport:{width:innerWidth,height:innerHeight},devicePixelRatio,
      focus:{id:document.activeElement?.id||'',tag:document.activeElement?.tagName||''}})''')


def settle_scroll(page):
    page.evaluate('''() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))''')


def capture_full_page_from_top(page, destination):
    """Capture fixed/sticky UI from document top, then restore the real view."""
    before = view_state(page)
    focus = page.evaluate_handle('document.activeElement')
    try:
        page.evaluate('window.scrollTo({left:0,top:0,behavior:"instant"})')
        settle_scroll(page)
        at_capture = view_state(page)
        assert abs(at_capture['scroll']['x']) <= 1 and abs(at_capture['scroll']['y']) <= 1, at_capture
        image_bytes = page.screenshot(path=str(destination), full_page=True)
        image_size = png_dimensions(image_bytes)
    finally:
        focus.evaluate('''(element,previous) => {
          const target=element?.isConnected?element:previous.id?document.getElementById(previous.id):null;
          if(target && target!==document.activeElement)target.focus({preventScroll:true});
        }''', before['focus'])
        page.evaluate('''scroll => window.scrollTo({left:scroll.x,top:scroll.y,behavior:"instant"})''', before['scroll'])
        settle_scroll(page)
        focus.dispose()
    restored = view_state(page)
    return {'protocol': 'full-page from document top after two animation frames; fixed/sticky CSS unchanged',
            'method': 'browser viewport emulation', 'before': before, 'at_screenshot': at_capture,
            'screenshot_scroll': at_capture['scroll'], 'restored_scroll': restored['scroll'],
            'image_pixels': image_size, 'restored': restored,
            'focus_preserved': before['focus'] == restored['focus']}


def capture_current_viewport(page, destination):
    """The final diagnostic image preserves the actual current scroll position."""
    before = view_state(page)
    image_bytes = page.screenshot(path=str(destination), full_page=False)
    return {'protocol': 'current viewport only; no scroll or CSS changes',
            'method': 'browser viewport emulation', 'at_screenshot': before,
            'image_pixels': png_dimensions(image_bytes), 'after': view_state(page)}


def capture(page, selector, name):
    """Assert real control geometry and save both pixels and measurements."""
    scope = page.locator(selector)
    expect(scope).to_be_visible()
    scope.scroll_into_view_if_needed()
    page.evaluate('document.fonts.ready')
    geometry = scope.evaluate('''root => {
      const visible=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(e).visibility!=='hidden'};
      const rect=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return {
        tag:e.tagName,
        text:(e.getAttribute('aria-label')||e.textContent||'').trim().slice(0,100),
        x:r.x,y:r.y,width:r.width,height:r.height,fontSize:parseFloat(s.fontSize),
        outline:s.outlineStyle,shadow:s.boxShadow,border:s.borderWidth}};
      return {viewport:{width:innerWidth,height:innerHeight},
        horizontalOverflow:document.documentElement.scrollWidth>innerWidth+1,
        root:rect(root),controls:[...root.querySelectorAll('button,input,textarea')].filter(visible).map(rect),
        methods:[...root.querySelectorAll('[data-method],[data-qc-method]')].filter(visible).map(rect),
        headerActions:[...root.querySelectorAll('header [data-qc-action="new"],header [data-qc-action="refresh"]')].filter(visible).map(rect),
        consultantFabVisible:(()=>{const fab=document.querySelector('#osConsultantFab');return !!fab&&visible(fab)})()};
    }''')
    geometry['measurement_state'] = view_state(page)
    assert not geometry['horizontalOverflow'], geometry
    assert geometry['root']['x'] >= -1, geometry
    assert geometry['root']['x'] + geometry['root']['width'] <= geometry['viewport']['width'] + 1, geometry
    for control in geometry['controls']:
        minimum = 43 if control['tag'] == 'BUTTON' else 41 if geometry['viewport']['width'] < 768 else 35
        assert control['height'] >= minimum, control
        assert control['x'] >= -1 and control['x'] + control['width'] <= geometry['viewport']['width'] + 1, control
    for method in geometry['methods']:
        assert method['width'] >= 120, 'Connection methods must remain usable inside the actual available content width: ' + str(method)
    if len(geometry['methods']) == 2:
        assert abs(geometry['methods'][0]['width'] - geometry['methods'][1]['width']) <= 1
    if selector == '#osManagedChannels':
        assert not geometry['consultantFabVisible'], 'The consultant floating action must not overlap the active WhatsApp connector'
        for action in geometry['headerActions']:
            assert abs(action['height'] - 44) <= 1, action
        if len(geometry['headerActions']) == 2:
            assert abs(geometry['headerActions'][0]['height'] - geometry['headerActions'][1]['height']) <= 1
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    engine = page.context.browser.browser_type.name
    theme = page.locator('html').get_attribute('data-theme')
    viewport = geometry['viewport']
    stem = f'{name}-{engine}-{theme}-{viewport["width"]}x{viewport["height"]}'
    geometry['capture'] = capture_full_page_from_top(page, ARTIFACTS / f'{stem}.png')
    (ARTIFACTS / f'{stem}.json').write_text(json.dumps(geometry, ensure_ascii=False, indent=2), encoding='utf-8')
    return geometry


def assert_phone_focus(input_element):
    input_element.focus()
    metrics = input_element.evaluate('''input => {
      const s=getComputedStyle(input),r=input.getBoundingClientRect();
      const decorated=[];
      for(let p=input.parentElement;p&&p.tagName!=='FORM';p=p.parentElement){
        const s=getComputedStyle(p);
        if(s.outlineStyle!=='none'||s.boxShadow!=='none')decorated.push({tag:p.tagName,classes:p.className,outline:s.outlineStyle,shadow:s.boxShadow});
      }
      return {viewportWidth:innerWidth,width:r.width,height:r.height,fontSize:parseFloat(s.fontSize),shadow:s.boxShadow,decorated};
    }''')
    expected_height = 42 if metrics['viewportWidth'] < 768 else 36
    assert abs(metrics['height'] - expected_height) <= 1, metrics
    assert metrics['width'] >= 200, 'The phone field must use the real available column width: ' + str(metrics)
    assert metrics['fontSize'] >= (16 if metrics['viewportWidth'] < 768 else 14), metrics
    # A field may have one focus outline; nested wrappers must not draw another.
    assert not metrics['decorated'], metrics


def inspect_tutorial(guide, method):
    """The original seven-step timeline has icons, titles and descriptions."""
    expect(guide).to_be_visible()
    expect(guide.locator('.wizard-timeline[role="list"]')).to_have_count(1)
    steps = guide.locator('.wizard-timeline-step[role="listitem"]')
    expect(steps).to_have_count(7)
    expect(steps.first).to_be_visible()
    expect(steps.last).to_be_visible()
    expect(guide.locator('.wizard-timeline-dot svg')).to_have_count(7)
    expect(guide.locator('.wizard-timeline-dot.edge')).to_have_count(2)
    for index in range(7):
        step = steps.nth(index)
        expect(step.locator('small')).to_have_text(f'PASSO {index + 1}')
        assert step.locator('strong').inner_text().strip()
        assert step.locator('p').inner_text().strip()
    text = guide.inner_text()
    if method == 'code':
        assert 'Conectar com número de telefone' in text
        assert not re.search(r'câmera|escane|QR Code', text, re.I), text
    else:
        assert re.search(r'câmera', text, re.I) and 'QR Code' in text, text
        assert 'Conectar com número de telefone' not in text, text
    rectangles = steps.evaluate_all('(steps)=>steps.map(s=>{const r=s.getBoundingClientRect();return {top:r.top,bottom:r.bottom}})')
    assert all(rectangles[index]['bottom'] <= rectangles[index + 1]['top'] + 1 for index in range(6)), rectangles


def native_style(element):
    return element.evaluate('''async element => {
      getComputedStyle(element).backgroundColor;
      await new Promise(requestAnimationFrame);
      const animations=element.getAnimations().filter(a=>Number.isFinite(a.effect?.getComputedTiming().endTime));
      await Promise.all(animations.map(a=>a.finished.catch(()=>{})));
      const s=getComputedStyle(element),r=element.getBoundingClientRect();
      return {classes:element.className,fontFamily:s.fontFamily,fontSize:s.fontSize,
        fontWeight:s.fontWeight,lineHeight:s.lineHeight,color:s.color,
        backgroundColor:s.backgroundColor,borderColor:s.borderColor,
        borderRadius:s.borderRadius,boxShadow:s.boxShadow,padding:s.padding,
        minHeight:s.minHeight,height:r.height,width:r.width};
    }''')


def compare_actual_core_components(page, host, panel_factory):
    """Compare shipped UI outside the connector; never submit the client form."""
    host.locator('[data-qc-title]').focus()
    page.mouse.move(0, 0)
    phone = host.locator('[data-qc-pair-phone]')
    submit = host.locator('[data-qc-form="pair"] button[type="submit"]')
    expect(submit).to_have_class(re.compile(r'\bos-button\b'))
    expect(submit).to_have_class(re.compile(r'\bos-large\b'))
    expect(host.locator('[data-qc-form="pair"]')).to_have_class(re.compile(r'\bos-form\b'))
    assert phone.evaluate("input=>!!input.closest('.os-field')")
    connector = {'button': native_style(submit), 'field': native_style(phone),
                 'card': native_style(phone.locator('xpath=ancestor::*[contains(concat(" ",normalize-space(@class)," ")," wizard-card ")][1]'))}

    reference = panel_factory('owner', connector=False)
    expect(reference.locator('#page-suite .os-card:visible').first).to_be_visible()
    core_card = native_style(reference.locator('#page-suite .os-card:visible').first)
    core_field = native_style(reference.locator('#page-suite [data-os-form="business-preferences"] input[name="contact_phone"]'))
    link = reference.locator('#osNav [data-os-route="clientes"]')
    if not link.is_visible():
        reference.locator('#osMenu').click()
    link.click()
    reference.locator('#page-suite [data-os-action="new-customer"]').first.click()
    expect(reference.locator('#osDialog')).to_be_visible()
    reference.mouse.move(0, 0)
    core = {'button': native_style(reference.locator('#osDialog .os-form-actions button[type="submit"]')),
            'field': core_field, 'card': core_card,
            'client_popup_field_variant': native_style(reference.locator('#osDialog #osField-phone'))}
    for key in ('fontFamily', 'fontSize', 'color', 'backgroundColor'):
        assert connector['button'][key] == core['button'][key], (key, connector, core)
    assert abs(connector['button']['height'] - 44) <= 1, connector
    assert core['button']['height'] >= 44, core
    for key in ('fontFamily', 'fontSize', 'fontWeight', 'lineHeight', 'color', 'backgroundColor', 'borderRadius', 'borderColor', 'boxShadow'):
        assert connector['field'][key] == core['field'][key], (key, connector, core)
    assert abs(connector['field']['height'] - core['field']['height']) <= 1, (connector, core)
    # The actual business-preferences field uses the same os-field/os-form core.
    # The client popup intentionally has pill actions and an icon field wrapper;
    # those context variants are recorded instead of imposed on the connector.
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    engine = page.context.browser.browser_type.name
    theme = page.locator('html').get_attribute('data-theme')
    viewport = page.viewport_size
    stem = f'core-components-{engine}-{theme}-{viewport["width"]}x{viewport["height"]}'
    evidence = {'connector': connector, 'actual_core': core,
        'context_variants': ['Customer popup has pill actions, icon field wrapper and a taller button when its label wraps',
            'The original connection wizard-card has its own radius and padding; it is compared to the admin wizard-card, not the generic os-card'],
        'reference_form_submitted': False}
    evidence['capture'] = capture_full_page_from_top(reference, ARTIFACTS / f'{stem}.png')
    (ARTIFACTS / f'{stem}.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    reference.locator('#osDialog .os-form-actions [data-os-action="close-dialog"]').click()
    expect(reference.locator('#osDialog')).to_be_hidden()
    assert not writes(reference), 'Opening and cancelling the actual core reference must not create a client'


def compare_shared_tutorial_components(page, host, panel_factory):
    """Admin and company render the same original method/timeline component."""
    admin = panel_factory()
    admin.mouse.move(0, 0)
    page.mouse.move(0, 0)
    report = {}
    expected_icon_size = 26 if page.viewport_size['width'] <= 640 else 30
    for method in ('qr', 'code'):
        company_method = host.locator(f'[data-qc-method="{method}"]')
        admin_method = admin.locator(f'[data-method="{method}"]')
        left, right = native_style(company_method), native_style(admin_method)
        for key in ('fontFamily', 'fontSize', 'fontWeight', 'lineHeight', 'backgroundColor', 'color',
                    'borderColor', 'borderRadius', 'boxShadow', 'padding', 'minHeight'):
            assert left[key] == right[key], (method, key, left, right)
        assert company_method.locator('strong').inner_text() == admin_method.locator('strong').inner_text()
        assert company_method.locator('span').last.inner_text() == admin_method.locator('span').last.inner_text()
        for item in (company_method, admin_method):
            icon = item.locator('.wizard-method-icon svg').bounding_box()
            assert icon and abs(icon['width'] - expected_icon_size) <= 1 and abs(icon['height'] - expected_icon_size) <= 1, icon
        report[method] = {'company': left, 'admin': right}
    expect(host.locator('[data-qc-method]').first).to_have_attribute('data-qc-method', 'qr')
    expect(admin.locator('[data-method]').first).to_have_attribute('data-method', 'qr')
    inspect_tutorial(host.locator('[data-qc-tutorial]'), 'code')
    inspect_tutorial(admin.locator('#guideSteps'), 'code')
    assert host.locator('.wizard-timeline').inner_text() == admin.locator('#guideSteps .wizard-timeline').inner_text()
    company_card = native_style(host.locator('[data-qc-tutorial]').locator('xpath=ancestor::*[contains(concat(" ",normalize-space(@class)," ")," wizard-card ")][1]'))
    admin_card = native_style(admin.locator('#wizardCard'))
    for key in ('fontFamily', 'backgroundColor', 'borderColor', 'borderRadius', 'boxShadow', 'padding'):
        assert company_card[key] == admin_card[key], (key, company_card, admin_card)
    report['wizard_cards'] = {'company': company_card, 'admin': admin_card}
    engine = page.context.browser.browser_type.name
    theme = page.locator('html').get_attribute('data-theme')
    viewport = page.viewport_size
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    stem = f'shared-tutorial-{engine}-{theme}-{viewport["width"]}x{viewport["height"]}'
    (ARTIFACTS / f'{stem}.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    assert not writes(admin), 'Comparing the admin tutorial must not start a connection'


def start_company(page):
    host = page.locator('#osManagedChannels')
    host.locator('[data-qc-action="connect"]').click()
    expect(host.locator('[data-qc-form="confirm"]')).to_be_visible()
    host.locator('[data-qc-form="confirm"] button[type="submit"]').click()
    expect(host.locator('[data-qc-tutorial]')).to_be_visible(timeout=12000)
    return host


def open_company_code(page, host):
    inspect_tutorial(host.locator('[data-qc-tutorial]'), 'code')
    expect(host.locator('[data-qc-action="tutorial-next"]')).to_have_text('Concluí os passos e estou pronto para prosseguir')
    before = writes(page)
    host.locator('[data-qc-action="tutorial-next"]').click()
    expect(host.locator('[data-qc-pair-phone]')).to_be_visible(timeout=12000)
    assert writes(page) == before, 'Completing the Code tutorial must only open the phone form'
    submit = host.locator('[data-qc-form="pair"] button[type="submit"]')
    expect(submit).to_be_enabled(timeout=12000)
    return host


def test_admin_uses_original_seven_step_tutorial_before_code_or_qr(panel_factory):
    page = panel_factory()
    expect(page.locator('#pairPhone')).to_be_hidden()
    expect(page.locator('#wizardTutorial')).to_be_visible()
    expect(page.locator('[data-method="code"]')).to_have_attribute('aria-pressed', 'true')
    assert not writes(page), 'Opening the connector must not start or reset a session'
    assert not any(call['path'].endswith('/qr/image') for call in calls(page))
    inspect_tutorial(page.locator('#guideSteps'), 'code')
    expect(page.locator('#wizardNext')).to_have_text('Concluí os passos e estou pronto para prosseguir')
    capture(page, '#disconnectedWizard', 'admin-code-tutorial')
    page.locator('#wizardNext').click()
    expect(page.locator('#pairPhone')).to_be_visible()
    assert not writes(page), 'Completing the tutorial must not generate a code'
    assert_phone_focus(page.locator('#pairPhone'))
    capture(page, '#disconnectedWizard', 'admin-code-form')
    page.locator('#pairPhone').fill('11 90000-0001')
    page.locator('#pairForm button[type="submit"]').click()
    expect(page.locator('#pairCode')).to_have_text('TEST-0001')
    expect(page.locator('#wizardSuccess')).to_be_hidden()
    assert len(writes(page, '/pair')) == 1
    capture(page, '#disconnectedWizard', 'admin-code-result')
    page.locator('[data-method="qr"]').click()
    expect(page.locator('#codeResult')).to_be_hidden()
    # Switching method must not issue/reset a pending pairing operation.
    expect(page.locator('#pairCode')).to_have_text('TEST-0001')
    inspect_tutorial(page.locator('#guideSteps'), 'qr')
    assert not any(call['path'].endswith('/qr/image') for call in calls(page))
    capture(page, '#disconnectedWizard', 'admin-qr-guide')
    # A live code blocks QR generation until its expiry or explicit preparation.
    expect(page.locator('#wizardNext')).to_be_disabled()
    page = panel_factory()
    page.locator('[data-method="qr"]').click()
    inspect_tutorial(page.locator('#guideSteps'), 'qr')
    page.locator('#wizardNext').click()
    expect(page.locator('#qrImage')).to_be_visible()
    expect(page.locator('#qrImage')).to_have_attribute('src', re.compile(r'^data:image/png;base64,'))
    capture(page, '#disconnectedWizard', 'admin-qr-image')
    page.locator('#wizardQrBack').click()
    expect(page.locator('#qrImage')).to_be_hidden()
    page.locator('[data-method="code"]').click()
    inspect_tutorial(page.locator('#guideSteps'), 'code')
    page.locator('#wizardNext').click()
    expect(page.locator('#pairPhone')).to_be_visible()
    assert len(writes(page, '/pair')) == 0


def test_company_code_is_scoped_and_uses_the_same_controls(panel_factory):
    page = panel_factory('owner')
    assert not writes(page)
    geometry = capture(page, '#osManagedChannels', 'owner-device-disabled')
    assert len(geometry['headerActions']) == 2, 'Both Add device and Refresh must be measured'
    host = start_company(page)
    compare_shared_tutorial_components(page, host, panel_factory)
    capture(page, '#osManagedChannels', 'owner-code-tutorial')
    open_company_code(page, host)
    compare_actual_core_components(page, host, panel_factory)
    assert_phone_focus(host.locator('[data-qc-pair-phone]'))
    expect(host.locator('[data-qc-method="code"]')).to_have_attribute('aria-pressed', 'true')
    capture(page, '#osManagedChannels', 'owner-code-form')
    host.locator('[data-qc-pair-phone]').fill('11 90000-0001')
    host.locator('[data-qc-form="pair"] button[type="submit"]').click()
    expect(host.locator('[data-qc-pair-code]')).to_have_text('TEST-0001')
    pair = writes(page, '/pair')
    assert len(pair) == 1 and pair[0]['path'] == BASE + '/qr-channels/' + CHANNEL + '/pair', pair
    assert pair[0]['data']['expected_revision'] == 2
    assert not any(call['path'].startswith('/api/v1/qr/') for call in calls(page))
    assert not any(call.get('data', {}).get('assistant_enabled') is True for call in writes(page))
    original_code = host.locator('[data-qc-pair-code]').inner_text()
    expect(host.locator('[data-qc-pair-time]')).to_have_text(re.compile(r'\d+ s'))
    initial_ttl = int(re.search(r'\d+', host.locator('[data-qc-pair-time]').inner_text()).group())
    capture(page, '#osManagedChannels', 'owner-code-result')
    page.wait_for_function('''initial => {
      const value=document.querySelector('#osManagedChannels [data-qc-pair-time]')?.textContent.match(/[0-9]+/);
      return value && Number(value[0])<initial;
    }''', arg=initial_ttl, timeout=5000)
    before_hide_ttl = int(re.search(r'\d+', host.locator('[data-qc-pair-time]').inner_text()).group())
    before_hide_writes = len(writes(page))
    host.locator('[data-qc-action="cancel-pair"]').focus()
    page.keyboard.press('Enter')
    expect(host.locator('[data-qc-action="show-code"]')).to_be_focused()
    expect(host.locator('[data-qc-pair-code]')).to_have_count(0)
    expect(host.locator('[data-qc-pair-hidden]')).to_be_visible()
    assert original_code not in host.inner_text()
    hidden_ttl = int(re.search(r'\d+', host.locator('[data-qc-pair-time]').inner_text()).group())
    assert 0 < hidden_ttl <= before_hide_ttl
    capture(page, '#osManagedChannels', 'owner-code-hidden')
    page.keyboard.press('Enter')
    expect(host.locator('[data-qc-action="cancel-pair"]')).to_be_focused()
    expect(host.locator('[data-qc-pair-code]')).to_have_text(original_code)
    restored_ttl = int(re.search(r'\d+', host.locator('[data-qc-pair-time]').inner_text()).group())
    assert 0 < restored_ttl <= hidden_ttl, 'Revealing the same code must not restart its deadline'
    assert len(writes(page)) == before_hide_writes and len(writes(page, '/pair')) == 1
    capture(page, '#osManagedChannels', 'owner-code-restored')
    host.locator('[data-qc-method="qr"]').click()
    expect(host.locator('[data-qc-pair-code]')).to_have_count(0)
    inspect_tutorial(host.locator('[data-qc-tutorial]'), 'qr')
    host.locator('[data-qc-action="tutorial-next"]').click()
    expect(host.locator('[data-qc-action="qr"]')).to_be_disabled()
    # Displaying QR remains an explicit action; a method switch cannot reset/pair.
    assert len(writes(page, '/pair')) == 1
    capture(page, '#osManagedChannels', 'owner-qr-mode-pending-code')
    host.locator('[data-qc-method="code"]').click()
    expect(host.locator('[data-qc-pair-code]')).to_have_text('TEST-0001')
    assert len(writes(page, '/pair')) == 1
    page = panel_factory('owner')
    host = start_company(page)
    host.locator('[data-qc-method="qr"]').click()
    inspect_tutorial(host.locator('[data-qc-tutorial]'), 'qr')
    host.locator('[data-qc-action="tutorial-next"]').click()
    page.wait_for_function('''() => document.querySelector('#osManagedChannels [data-qc-qr] img') ||
      document.querySelector('#osManagedChannels [data-qc-action="qr"]')''')
    if host.locator('[data-qc-action="qr"]').count():
        expect(host.locator('[data-qc-action="qr"]')).to_be_enabled(timeout=12000)
        host.locator('[data-qc-action="qr"]').click()
    expect(host.locator('[data-qc-qr] img')).to_be_visible()
    capture(page, '#osManagedChannels', 'owner-qr-image')
    host.locator('[data-qc-action="hide-qr"]').click()
    expect(host.locator('[data-qc-qr]')).to_have_count(0)
    host.locator('[data-qc-method="code"]').click()
    open_company_code(page, host)
    expect(host.locator('[data-qc-pair-phone]')).to_be_visible()
    assert not writes(page, '/pair')


def test_expired_code_disappears_and_is_not_automatically_requested_again(panel_factory):
    page = panel_factory('admin', 'expired')
    inspect_tutorial(page.locator('#guideSteps'), 'code')
    page.locator('#wizardNext').click()
    page.locator('#pairPhone').fill('11 90000-0001')
    page.locator('#pairForm button[type="submit"]').click()
    expect(page.locator('#codeResult')).to_be_visible()
    expect(page.locator('#codeResult')).to_be_hidden(timeout=5000)
    expect(page.locator('#pairPhone')).to_be_visible()
    expect(page.locator('#codeStatus')).to_contain_text(re.compile(r'expir', re.I))
    assert len(writes(page, '/pair')) == 1
    capture(page, '#disconnectedWizard', 'admin-code-expired')


def test_company_hidden_code_expires_without_reveal_or_an_automatic_new_pair(panel_factory):
    page = panel_factory('owner', 'expired')
    host = start_company(page)
    open_company_code(page, host)
    host.locator('[data-qc-pair-phone]').fill('11 90000-0001')
    host.locator('[data-qc-form="pair"] button[type="submit"]').click()
    expect(host.locator('[data-qc-pair-code]')).to_have_text('TEST-0001')
    host.locator('[data-qc-action="cancel-pair"]').focus()
    page.keyboard.press('Enter')
    expect(host.locator('[data-qc-action="show-code"]')).to_be_focused()
    expect(host.locator('[data-qc-pair-hidden]')).to_be_visible()
    expect(host.locator('[data-qc-pair-code]')).to_have_count(0)
    assert 'TEST-0001' not in host.inner_text()
    expect(host.locator('[data-qc-pair-hidden]')).to_have_count(0, timeout=5000)
    expect(host.locator('[data-qc-action="show-code"]')).to_have_count(0)
    expect(host.locator('[data-qc-pair-code]')).to_have_count(0)
    expiration_notice = host.locator('[data-qc-form="pair"] [role="status"]')
    expect(expiration_notice).to_have_count(1)
    expect(expiration_notice).to_have_text('O código expirou. Solicite outro quando a conexão estiver pronta.')
    assert len(writes(page, '/pair')) == 1
    assert not any(call['path'].startswith('/api/v1/qr/') for call in calls(page))
    capture(page, '#osManagedChannels', 'owner-hidden-code-expired')


def test_revoked_session_has_clear_recovery_and_no_enabled_generation(panel_factory):
    page = panel_factory('admin', 'logged_out')
    expect(page.locator('#wizardRecover')).to_be_visible()
    assert page.locator('#pairForm button[type="submit"]:visible:enabled').count() == 0
    assert not writes(page)
    capture(page, '#disconnectedWizard', 'admin-revoked')
    page.locator('#wizardRecover').click()
    expect(page.locator('#actionConfirm')).to_be_visible()
    expect(page.locator('#actionConfirmCancel')).to_be_focused()
    page.keyboard.press('Shift+Tab')
    expect(page.locator('#actionConfirmAccept')).to_be_focused()
    page.keyboard.press('Tab')
    expect(page.locator('#actionConfirmCancel')).to_be_focused()
    capture(page, '#actionConfirm', 'admin-recovery-confirmation')
    page.keyboard.press('Escape')
    expect(page.locator('#actionConfirm')).to_be_hidden()
    expect(page.locator('#wizardRecover')).to_be_focused()
    assert not writes(page), 'Inspecting and cancelling recovery must not reset any connection'


@pytest.mark.parametrize('connection_state', ['qr_expired', 'qr_delayed'])
def test_admin_prepares_expired_or_delayed_connection_without_losing_code_form(panel_factory, connection_state):
    """WA-LIVE-01: offer explicit, confirmed preparation without discarding client input."""
    page = panel_factory('admin', connection_state)
    page.locator('[data-method="qr"]').click()
    inspect_tutorial(page.locator('#guideSteps'), 'qr')
    page.locator('[data-method="code"]').click()
    inspect_tutorial(page.locator('#guideSteps'), 'code')
    page.locator('#wizardNext').click()
    phone = page.locator('#pairPhone')
    expect(phone).to_be_visible()
    phone.fill('11 90000-0001')
    entered_phone = phone.input_value()
    generate = page.locator('#pairForm button[type="submit"]')
    prepare = page.locator('#retryCode')
    expect(generate).to_be_disabled()
    expect(prepare).to_be_visible()
    expect(prepare).to_be_enabled()
    expect(page.locator('#codeStatus')).to_contain_text('Use Preparar novo código')
    assert not writes(page), 'Opening an expired/delayed Code form must not prepare or pair automatically'
    capture(page, '#disconnectedWizard', f'admin-{connection_state}-code-preparation')

    prepare.click()
    expect(page.locator('#actionConfirm')).to_be_visible()
    expect(page.locator('#actionConfirmCancel')).to_be_focused()
    page.keyboard.press('Escape')
    expect(page.locator('#actionConfirm')).to_be_hidden()
    expect(prepare).to_be_focused()
    expect(phone).to_have_value(entered_phone)
    expect(page.locator('#wizardTutorial')).to_be_hidden()
    expect(generate).to_be_disabled()
    assert not writes(page), 'Cancelling preparation must preserve the existing connection'

    prepare.click()
    expect(page.locator('#actionConfirm')).to_be_visible()
    before_confirmation = len(calls(page))
    page.locator('#actionConfirmAccept').click()
    expect(page.locator('#actionConfirm')).to_be_hidden()
    expect(phone).to_be_visible()
    expect(phone).to_have_value(entered_phone)
    expect(page.locator('#wizardTutorial')).to_be_hidden()
    expect(generate).to_be_enabled(timeout=12000)
    operations = calls(page)[before_confirmation:]
    reconnects = [index for index, call in enumerate(operations)
                  if call['method'] == 'POST' and call['path'] == '/api/v1/qr/reconnect']
    assert len(reconnects) == 1, operations
    index = reconnects[0]
    assert any(call['method'] == 'GET' and call['path'] == '/api/v1/qr/status' for call in operations[:index]), operations
    assert any(call['method'] == 'GET' and call['path'] == '/api/v1/qr/status' for call in operations[index + 1:]), operations
    assert [call['path'] for call in writes(page)] == ['/api/v1/qr/reconnect']
    assert not writes(page, '/pair') and not writes(page, '/recover')

    # Completing preparation keeps the chosen method and the completed Code tutorial.
    page.locator('[data-method="qr"]').click()
    page.locator('[data-method="code"]').click()
    expect(phone).to_be_visible()
    expect(phone).to_have_value(entered_phone)
    expect(page.locator('#wizardTutorial')).to_be_hidden()
    expect(generate).to_be_enabled()
    assert not writes(page, '/pair') and not writes(page, '/recover')
    assert not any(call['path'].startswith('/api/v1/qr/image') for call in calls(page))
    capture(page, '#disconnectedWizard', f'admin-{connection_state}-code-prepared')


def test_reader_does_not_gain_owner_controls_or_global_access(panel_factory):
    page = panel_factory('reader')
    host = page.locator('#osManagedChannels')
    expect(host.locator('[data-qc-action="new"],[data-qc-action="connect"],[data-qc-form="pair"]')).to_have_count(0)
    assert not writes(page)
    assert not any(call['path'].startswith('/api/v1/qr/') for call in calls(page))
    capture(page, '#osManagedChannels', 'reader-consultation')
