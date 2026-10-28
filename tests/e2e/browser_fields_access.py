"""Real access/onboarding/landing/invitation HTML with finite synthetic HTTP.

The six CSS viewports run in Chromium and emulated WebKit, in both system color
schemes. This is not physical Safari/iPhone validation. Every request is routed:
only repository assets and the explicit fixtures below can receive a response.
No account, lead, invitation, email, WhatsApp, or booking reaches a real service.

Requires the UI test dependencies installed by ``npm ci --prefix ui-tests``;
onboarding fixtures are imported from the existing DOM harness, not duplicated.
"""
import copy
import json
import mimetypes
from pathlib import Path
import subprocess
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "test-artifacts" / "fields-access"
ORIGIN = "https://orion.test"
SIZES = [(360, 800), (390, 844), (430, 932), (768, 1024), (1024, 768), (1440, 900)]
INVITATION = "a" * 43
EMAIL = "synthetic@example.invalid"
# These are readable lower bounds, not a required layout. A field can fit the
# viewport and still be unusable when the panel sidebar plus wizard navigation
# leave it only 77 px. Preserve the real panel chrome when checking these bounds.
ONBOARDING_MIN_WIDTH = {
    "name": 180, "contact_phone": 180, "postal_code": 128,
    "street": 180, "number": 100, "complement": 160,
    "neighborhood": 160, "city": 160, "state": 85,
    "price": 160, "duration_min": 100, "prep_min": 100, "cleanup_min": 100,
    "start_time": 128, "end_time": 128, "break_start": 128, "break_end": 128,
    "niche": 160, "other_niche": 160, "activity": 160, "timezone": 160,
    "modality": 160, "professional_id": 160, "assistant_name": 160,
    "style": 160, "length": 160, "emoji": 160,
}


def onboarding_fixtures():
    result = subprocess.run(
        ["node", "--input-type=module", "-e",
         "import {fullState,entry} from './ui-tests/onboarding-harness.mjs';"
         "console.log(JSON.stringify({state:fullState(),entry:entry()}));"],
        cwd=ROOT, check=True, capture_output=True, text=True, timeout=30,
    )
    return json.loads(result.stdout)


ONBOARDING = onboarding_fixtures()
TENANT = ONBOARDING["state"]["tenant_id"]
BASE = f"/api/v1/tenants/{TENANT}"


class IsolatedHTTP:
    def __init__(self, surface):
        self.surface = surface
        self.calls = []
        self.unexpected = []
        self.verified = False
        self.onboarding = copy.deepcopy(ONBOARDING["state"])

    def matching(self, path, method="POST"):
        return [call for call in self.calls if call["path"] == path and call["method"] == method]

    def json(self, route, value, status=200):
        route.fulfill(status=status, content_type="application/json",
                      headers={"Cache-Control": "no-store"}, body=json.dumps(value))

    def asset(self, route, path):
        filename = path.rsplit("/", 1)[-1]
        if path.startswith("/panel-assets/"):
            if filename == "orion-suite-live.js":
                filename = "orion-suite.js"
            source = ROOT / "app/static" / filename
        elif path.startswith("/landing-assets/"):
            source = (ROOT / "app/static" / filename if filename in ("orion-fields.js", "orion-fields.css")
                      else ROOT / "landing/assets" / filename)
        elif path.startswith("/brand/"):
            source = ROOT / "app/static" / filename
        else:
            return False
        if not source.is_file():
            self.unexpected.append("missing fixture asset: " + path)
            route.abort()
            return True
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        route.fulfill(status=200, content_type=mime, body=source.read_bytes())
        return True

    def __call__(self, route):
        request = route.request
        url = urlparse(request.url)
        path, method = url.path, request.method
        if url.netloc == "viacep.com.br" and path == "/ws/01001000/json/" and method == "GET" and self.surface == "onboarding":
            return self.json(route, {"cep": "01001-000", "logradouro": "Praça da Sé", "bairro": "Sé",
                                     "localidade": "São Paulo", "uf": "SP"})
        if url.scheme != "https" or url.netloc != "orion.test":
            self.unexpected.append(method + " " + request.url)
            return route.abort()
        if method == "GET" and self.asset(route, path):
            return
        if method == "GET" and path in ("/", "/panel", "/convite"):
            source = ROOT / {"/": "landing/index.html", "/panel": "app/static/panel.html",
                             "/convite": "app/static/orion-invite.html"}[path]
            return route.fulfill(status=200, content_type="text/html", body=source.read_text(encoding="utf-8"))
        if path == "/favicon.ico" and method == "GET":
            return route.fulfill(status=204, body="")
        body = request.post_data_json if request.post_data else None
        self.calls.append({"path": path, "method": method, "body": body})

        if path == "/auth/me" and method == "GET":
            if self.surface == "auth":
                return self.json(route, {"detail": "Sessão sintética não autenticada"}, 401)
            if self.surface in ("onboarding", "invite"):
                return self.json(route, {"authenticated": True, "account": True, "csrf": "synthetic-access-csrf",
                                         "role": "member", "name": "Conta sintética", "email": EMAIL,
                                         "email_verified": self.surface == "onboarding" or self.verified})
        if path == "/auth/features" and method == "GET" and self.surface in ("auth", "onboarding"):
            return self.json(route, {"registration": True, "passwordRecovery": True})
        if self.surface == "auth" and method == "POST":
            if path == "/auth/login":
                return self.json(route, {"detail": "Credencial sintética: nenhum login real foi executado"}, 401)
            if path == "/auth/register":
                return self.json(route, {"codeRequested": True, "message": "Cadastro simulado"})
            if path in ("/auth/verify", "/auth/password/forgot", "/auth/password/reset"):
                return self.json(route, {"message": "Etapa confirmada apenas nesta simulação"})
        if self.surface == "invite" and method == "POST":
            if path == "/api/v1/team-invitations/preview":
                return self.json(route, {"business_name": "Empresa sintética", "role": "editor",
                                         "expires_at": "2030-01-01T12:00:00Z", "status": "pending", "email_bound": True})
            if path == "/auth/verify/resend":
                return self.json(route, {"message": "Solicitação simulada. Nenhum e-mail foi enviado."})
            if path == "/auth/verify":
                self.verified = True
                return self.json(route, {"ok": True})
        if self.surface == "landing" and method == "POST" and path == "/api/public/sales-requests":
            return self.json(route, {"ok": True, "request_id": "70000000-0000-4000-8000-000000000007"}, 201)
        if self.surface == "onboarding":
            if method == "GET":
                if path == "/api/v1/account/businesses":
                    return self.json(route, [{"id": TENANT, "name": self.onboarding["name"], "role": "owner",
                                             "slug": "empresa-sintetica", "timezone": "America/Cuiaba"}])
                if path == "/api/v1/onboarding/entry":
                    return self.json(route, ONBOARDING["entry"])
                if path == BASE + "/onboarding":
                    return self.json(route, self.onboarding)
                if path == BASE + "/management/dashboard":
                    return self.json(route, {"summary": {"appointments": 0, "completed": 0, "customers": 0}})
                if path in (BASE + "/appointments", BASE + "/management/goals"):
                    return self.json(route, {"items": [], "total": 0, "templates": []})
                if path == BASE + "/management/finance/summary":
                    return self.json(route, {"summary": {"income_cents": 0, "expense_cents": 0, "balance_cents": 0, "count": 0}})
                if path == BASE + "/business/profile":
                    return self.json(route, {"generation": 0, "draft": None, "published": None,
                                             "draft_template": {"business_name": self.onboarding["name"],
                                                                "assistant_name": "Recepção", "timezone": "America/Cuiaba"},
                                             "supported_capabilities": [], "warnings": [],
                                             "access": {"role": "owner", "can_edit_draft": True, "can_publish": True}})
                if path == BASE + "/business/profile/history":
                    return self.json(route, [])
            if method == "PUT" and path in (BASE + "/onboarding/steps/business", BASE + "/onboarding/steps/catalog"):
                self.onboarding["version"] += 1
                self.onboarding["last_operation_key"] = body["idempotency_key"]
                if path.endswith("/business"):
                    self.onboarding["name"] = body["data"]["name"]
                    self.onboarding["configuration"]["contact_phone"] = body["data"]["contact_phone"]
                else:
                    self.onboarding["catalog"]["services"][0].update(body["data"]["item"])
                return self.json(route, self.onboarding)
        self.unexpected.append(method + " " + path)
        route.abort()


def inspect_form(page, selector, width, stem, state, metrics):
    form = page.locator(selector)
    form.wait_for(state="visible")
    form.scroll_into_view_if_needed()
    value = form.evaluate("""form => ({
      width: innerWidth, document: document.documentElement.scrollWidth,
      effectiveTheme: document.documentElement.dataset.theme || null,
      panelSidebar: (()=>{const el=document.querySelector('#appView .sidebar');
        if(!el)return null;const r=el.getBoundingClientRect();return {width:r.width,left:r.left,right:r.right};})(),
      panelContent: (()=>{const el=document.querySelector('#appView .content');
        if(!el)return null;const r=el.getBoundingClientRect();return {width:r.width,left:r.left,right:r.right};})(),
      controls: [...form.querySelectorAll('input,select,textarea,button')].filter(el => {
        const r=el.getBoundingClientRect();return el.type!=='hidden'&&r.width>0&&r.height>0&&
          getComputedStyle(el).visibility!=='hidden'&&!el.closest('[aria-hidden="true"]');
      }).map(el => {const r=el.getBoundingClientRect(),css=getComputedStyle(el);return {
        name:el.name||el.id,type:el.type,tag:el.tagName,left:r.left,right:r.right,width:r.width,height:r.height,
        font:css.fontSize,disabled:el.matches(':disabled'),invalid:el.getAttribute('aria-invalid'),
        label:!!(el.labels?.length||el.getAttribute('aria-label')||el.getAttribute('aria-labelledby')||
          el.tagName==='BUTTON'&&el.textContent.trim()),
        describedBy:(el.getAttribute('aria-describedby')||'').split(/\\s+/).filter(Boolean).map(id=>!!document.getElementById(id))};}),
      errors:[...form.querySelectorAll('.orion-field-error')].map(el=>{
        const r=el.getBoundingClientRect();return {left:r.left,right:r.right,text:el.textContent};})
    })""")
    value.update(form=selector, state=state)
    metrics.append(value)
    assert value["controls"], value
    assert value["document"] <= width + 2, value
    for control in value["controls"]:
        assert control["left"] >= -2 and control["right"] <= width + 2, (selector, state, control)
        assert control["label"], (selector, "missing accessible label", control)
        assert all(control["describedBy"]), (selector, "missing description target", control)
        if control["tag"] != "BUTTON" and control["type"] not in ("checkbox", "radio", "color", "file"):
            assert control["height"] >= 36, (selector, "undersized field", control)
            if width <= 430:
                assert float(control["font"].removesuffix("px")) >= 16, (selector, "mobile field font", control)
            if selector.startswith("[data-ob-form="):
                minimum = ONBOARDING_MIN_WIDTH.get(control["name"], 120)
                assert control["width"] >= minimum, (selector, "unreadable field after panel/sidebar layout", control, value["panelSidebar"], value["panelContent"])
                if control["tag"] != "TEXTAREA":
                    assert control["height"] <= 64, (selector, "single-line field stretched by adjacent help", control)
    for error in value["errors"]:
        assert error["left"] >= -2 and error["right"] <= width + 2, (selector, error)
    form.screenshot(path=str(ARTIFACTS / f"{stem}-{state}.png"))
    return form


def check_auth(page, network, geometry):
    page.goto(ORIGIN + "/panel?aba=dashboard", wait_until="domcontentloaded")
    form = geometry("#loginForm", "login")
    expect(form.locator("#username")).to_have_attribute("type", "text")
    expect(form.locator("#username")).to_have_attribute("autocomplete", "username")
    expect(form.locator("#password")).to_have_attribute("autocomplete", "current-password")
    form.locator("#username").fill("Usuario-Sintético")
    form.locator("#password").fill(" Senha fictícia 9! ")
    form.locator("button[type=submit]").click()
    expect(page.locator("#loginError")).to_contain_text("sintética")
    assert network.matching("/auth/login")[0]["body"]["password"] == " Senha fictícia 9! "
    assert network.matching("/auth/login")[0]["body"]["username"] == "Usuario-Sintético"

    page.locator("#authTabRegister").click()
    page.locator("#registerName").fill("João D'Ávila-Souza")
    page.locator("#registerEmail").fill("email inválido")
    for name in ("registerPassword", "registerConfirm"):
        page.locator("#" + name).fill("Senha fictícia 9!")
    page.locator("#authSubmitRegister").click()
    expect(page.locator("#registerEmail")).to_have_attribute("aria-invalid", "true")
    assert not network.matching("/auth/register")
    geometry("#registerForm", "register-invalid-email")
    page.locator("#registerEmail").fill("Contato+Orion@Sub.Example.invalid")
    geometry("#registerForm", "register-filled")
    page.locator("#authSubmitRegister").click()
    page.locator("#verifyForm").wait_for(state="visible")
    assert network.matching("/auth/register")[0]["body"]["name"] == "João D'Ávila-Souza"
    code = page.locator("#verifyCode")
    expect(code).to_have_attribute("type", "text")
    expect(code).to_have_attribute("autocomplete", "one-time-code")
    code.fill("012 345")
    expect(code).to_have_value("012345")
    geometry("#verifyForm", "verify-code")
    page.locator("#authSubmitVerify").click()
    page.locator("#loginForm").wait_for(state="visible")
    assert network.matching("/auth/verify")[0]["body"]["code"] == "012345"

    page.locator("#authForgotLink").click()
    page.locator("#forgotEmail").fill(EMAIL)
    geometry("#forgotForm", "forgot")
    page.locator("#authSubmitForgot").click()
    page.locator("#resetForm").wait_for(state="visible")
    for name in ("resetPassword", "resetConfirm"):
        page.locator("#" + name).fill("Outra senha fictícia 9!")
    page.locator("#resetCode").fill("01x2345")
    page.locator("#authSubmitReset").click()
    expect(page.locator("#resetCode")).to_have_attribute("aria-invalid", "true")
    assert not network.matching("/auth/password/reset")
    geometry("#resetForm", "reset-invalid-code")
    page.locator("#resetCode").fill("001 234")
    geometry("#resetForm", "reset-code")
    page.locator("#authSubmitReset").click()
    page.locator("#loginForm").wait_for(state="visible")
    assert network.matching("/auth/password/reset")[0]["body"]["code"] == "001234"


def check_setup_chrome(page, test_theme=False):
    # The wizard uses a dedicated header: the dashboard's rail, menu, bell and
    # assistant stay inaccessible at every step and at every viewport.
    expect(page.locator('#appView.ob-gated')).to_be_visible()
    for target in ('#appView > .sidebar', '#osHeader', '#osMenu',
                   '#osNotificationsToggle', '#osConsultantFab', '#osScrim',
                   '.ob-heading .ob-logo'):
        expect(page.locator(target)).to_be_hidden()
    expect(page.locator('#setupHeader')).to_be_visible()
    expect(page.locator('#setupHeader [data-orion-logo]')).to_be_visible()
    expect(page.locator('#setupThemeToggle')).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
    box = page.locator('#setupHeader').evaluate("""el => {
      const header=el.getBoundingClientRect(),logo=el.querySelector('img').getBoundingClientRect(),
            toggle=el.querySelector('button').getBoundingClientRect();
      return {left:header.left,right:header.right,logoRight:logo.right,
              themeLeft:toggle.left,themeRight:toggle.right};
    }""")
    assert box['left'] >= -2 and box['right'] <= page.viewport_size['width'] + 2, box
    assert box['logoRight'] + 8 <= box['themeLeft'], box
    assert box['themeRight'] <= page.viewport_size['width'] + 2, box
    if not test_theme:
        return
    original = page.evaluate('document.documentElement.dataset.themePreference')
    for mode in ('dark', 'light', 'auto', original):
        page.locator('#setupThemeToggle').click()
        expect(page.locator('#setupThemeMenu')).to_be_visible()
        page.locator('#setupThemeMenu [data-theme-choice='+mode+']').click()
        expect(page.locator('#setupThemeMenu')).to_be_hidden()
        expect(page.locator('#setupThemeToggle')).to_have_attribute('aria-expanded', 'false')
        assert page.evaluate('document.documentElement.dataset.themePreference') == mode
        assert page.evaluate('localStorage.getItem("orion-theme")') == mode
        effective = page.evaluate('document.documentElement.dataset.theme')
        expect(page.locator('#setupHeader [data-orion-logo]')).to_have_attribute(
            'src', '/brand/orion-brand-'+effective+'.svg')


def check_onboarding(page, network, geometry):
    page.goto(ORIGIN + "/panel?aba=dashboard", wait_until="domcontentloaded")
    business = page.locator('[data-ob-form="business"]')
    business.wait_for(state="visible")
    check_setup_chrome(page, test_theme=True)
    assert business.locator('[name=timezone]').count() == 0
    assert page.get_by_text('Os dados confirmados ficam salvos neste negócio.').count() == 0
    assert page.get_by_text('A conexão do WhatsApp será feita depois.').count() == 0
    dimensions = business.evaluate("""form => {
      const visible = [...form.querySelectorAll('.ob-business-fields > .ob-field')]
        .filter(node => node.getBoundingClientRect().height > 0);
      const rect = element => element.getBoundingClientRect();
      const styles = element => getComputedStyle(element);
      const labelGaps = visible.map(field => {
        const label=field.querySelector('label');
        const input=field.querySelector('input,select');
        return {gap:rect(input).top-rect(label).bottom,
                marginTop:parseFloat(styles(label).marginTop),
                marginBottom:parseFloat(styles(label).marginBottom)};
      });
      const fieldGaps = [];
      for(let i=1;i<visible.length;i++){
        const gap=rect(visible[i]).top-rect(visible[i-1]).bottom;
        if(gap>=0)fieldGaps.push(gap);
      }
      const last=visible[visible.length-1];
      const actions=form.querySelector('.ob-actions');
      const button=actions.querySelector('button');
      return {labelGaps,fieldGaps,
        finalGap:rect(actions).top-rect(last).bottom,
        dividerGap:rect(button).top-rect(actions).top,
        totalWidth:document.documentElement.scrollWidth,
        viewport:innerWidth};
    }""")
    assert dimensions['totalWidth'] <= dimensions['viewport']+2, dimensions
    assert all(item['marginTop'] == 0 and item['marginBottom'] == 0
               and 6 <= item['gap'] <= 10 for item in dimensions['labelGaps']), dimensions
    assert all(18 <= gap <= 24 for gap in dimensions['fieldGaps']), dimensions
    assert 16 <= dimensions['finalGap'] <= 22, dimensions
    assert 16 <= dimensions['dividerGap'] <= 24, dimensions
    expect(business.locator('[name=contact_phone]')).to_have_attribute('required', '')
    name_input = business.locator('[name=name]')
    name_input.fill("  Barbearia    Áurea  ")
    business.locator('[name=niche]').click()
    expect(name_input).to_have_value("Barbearia Áurea")
    phone = business.locator("[name=contact_phone]")
    phone.fill("(55) 99123-45678")
    business.locator("button[type=submit]").click()
    expect(phone).to_have_attribute("aria-invalid", "true")
    assert not network.matching(BASE + "/onboarding/steps/business", "PUT")
    geometry('[data-ob-form="business"]', "business-invalid-phone")
    phone.fill("+55 (55) 99123-4567")
    expect(phone).to_have_value("(55) 99123-4567")
    geometry('[data-ob-form="business"]', "business")
    business.locator("button[type=submit]").click()
    expect(page.locator('[data-ob-feedback]')).to_contain_text("salv")
    sent_business = network.matching(BASE + "/onboarding/steps/business", "PUT")[0]["body"]["data"]
    assert sent_business["contact_phone"] == "+5555991234567"
    assert sent_business["name"] == "Barbearia Áurea"
    assert sent_business["timezone"] == "America/Cuiaba"

    page.locator('[data-ob-step="location"]').click()
    location = page.locator('[data-ob-form="location"]')
    location.locator("[name=number]").fill("123A")
    location.locator("[name=complement]").fill("Sala 02")
    location.locator("[name=postal_code]").fill("01001000")
    expect(location.locator("[name=city]")).to_have_value("São Paulo")
    expect(location.locator("[name=postal_code]")).to_have_value("01001-000")
    expect(location.locator("[name=number]")).to_have_value("123A")
    expect(location.locator("[name=complement]")).to_have_value("Sala 02")
    geometry('[data-ob-form="location"]', "location-cep")
    check_setup_chrome(page)

    page.locator('[data-ob-step="catalog"]').click()
    page.locator('[data-ob-action="edit"][data-ob-kind="service"]').first.click()
    service = page.locator('[data-ob-form="service"]')
    service.locator("[name=price]").fill("1.234,56")
    expect(service.locator("[name=price]")).to_have_value("1.234,56")
    service.locator("[name=duration_min]").fill("40,5")
    service.locator("button[type=submit]").click()
    expect(service.locator("[name=duration_min]")).to_have_attribute("aria-invalid", "true")
    assert not network.matching(BASE + "/onboarding/steps/catalog", "PUT")
    geometry('[data-ob-form="service"]', "catalog-invalid-duration")
    service.locator("[name=duration_min]").fill("40")
    geometry('[data-ob-form="service"]', "catalog-service")
    check_setup_chrome(page)
    service.locator("button[type=submit]").click()
    expect(page.locator('[data-ob-feedback]')).to_contain_text("salv")
    assert network.matching(BASE + "/onboarding/steps/catalog", "PUT")[0]["body"]["data"]["item"]["price_cents"] == 123456

    page.locator('[data-ob-step="hours"]').click()
    hours = page.locator('[data-ob-form="hours"]')
    hours.locator("[name=start_time]").fill("09:00")
    hours.locator("[name=end_time]").fill("18:00")
    expect(hours.locator("[name=start_time]")).to_have_attribute("type", "time")
    geometry('[data-ob-form="hours"]', "hours-native")
    check_setup_chrome(page)
    page.locator('[data-ob-step="channels"]').click()
    geometry('[data-ob-form="channels"]', "channels-preferences")
    check_setup_chrome(page)
    for step in ('professionals', 'review'):
        page.locator('[data-ob-step='+step+']').click()
        check_setup_chrome(page)


def check_landing(page, network, geometry):
    page.goto(ORIGIN + "/", wait_until="domcontentloaded")
    form = page.locator("#sales-request-form")
    form.locator("[name=name]").fill("João D'Ávila-Souza")
    form.locator("[name=email]").fill("Contato+Orion@Sub.Example.invalid")
    form.locator("[name=segment]").select_option("barbearia")
    form.locator("[name=consent]").check()
    phone = form.locator("[name=phone]")
    phone.fill("(55) 99123-45678")
    form.locator("button[type=submit]").click()
    expect(phone).to_have_attribute("aria-invalid", "true")
    assert not network.matching("/api/public/sales-requests")
    geometry("#sales-request-form", "landing-invalid-phone")
    phone.fill("+55 (55) 99123-4567")
    expect(phone).to_have_value("(55) 99123-4567")
    geometry("#sales-request-form", "landing-filled")
    form.locator("button[type=submit]").click()
    expect(page.locator("#lead-success")).to_be_visible()
    assert len(network.matching("/api/public/sales-requests")) == 1
    payload = network.matching("/api/public/sales-requests")[0]["body"]
    assert payload["phone"] == "+5555991234567"
    assert payload["name"] == "João D'Ávila-Souza"
    assert payload["email"] == "Contato+Orion@Sub.Example.invalid"
    assert payload["website"] == ""


def check_invite(page, network, geometry):
    page.goto(ORIGIN + "/convite#token=" + INVITATION, wait_until="domcontentloaded")
    page.locator("#inviteVerifyForm").wait_for(state="visible")
    assert not network.matching("/auth/verify/resend")
    assert not network.matching("/api/v1/team-invitations/accept")
    page.locator("#inviteResend").click()
    expect(page.locator("#inviteVerifyStatus")).to_contain_text("simulada")
    assert len(network.matching("/auth/verify/resend")) == 1
    code = page.locator("#inviteCode")
    code.fill("0123456")
    page.locator("#inviteVerifyForm button[type=submit]").click()
    expect(code).to_have_attribute("aria-invalid", "true")
    expect(code).to_have_value("0123456")
    assert not network.matching("/auth/verify")
    geometry("#inviteVerifyForm", "invite-invalid-code")
    code.fill("012 345")
    expect(code).to_have_value("012345")
    expect(code).to_have_attribute("type", "text")
    expect(code).to_have_attribute("autocomplete", "one-time-code")
    geometry("#inviteVerifyForm", "invite-code")
    page.locator("#inviteVerifyForm button[type=submit]").click()
    expect(page.locator("#inviteAccept")).to_be_visible()
    assert network.matching("/auth/verify")[0]["body"]["code"] == "012345"
    assert not network.matching("/api/v1/team-invitations/accept")


@pytest.mark.parametrize("surface", ["auth", "onboarding", "landing", "invite"])
@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("width,height", SIZES)
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_access_fields_geometry_and_synthetic_payloads(surface, engine, width, height, theme):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    stem = f"{surface}-{engine}-{width}x{height}-{theme}"
    metrics, errors = [], []
    network = IsolatedHTTP(surface)
    with sync_playwright() as playwright:
        browser = getattr(playwright, engine).launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": height}, locale="pt-BR",
                                color_scheme=theme, device_scale_factor=2, has_touch=width <= 430,
                                is_mobile=engine == "webkit" and width <= 430)
        page.set_default_timeout(10000)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/*", network)
        page.add_init_script("localStorage.setItem('orion-theme'," + json.dumps(theme) + ");")
        geometry = lambda selector, state: inspect_form(page, selector, width, stem, state, metrics)
        try:
            {"auth": check_auth, "onboarding": check_onboarding, "landing": check_landing,
             "invite": check_invite}[surface](page, network, geometry)
            assert not network.unexpected, network.unexpected
            assert not errors, errors
        finally:
            report = {"surface": surface, "engine": engine, "width": width, "height": height,
                      "requested_color_scheme": theme, "physical_device": False,
                      "metrics": metrics, "page_errors": errors, "unexpected_requests": network.unexpected,
                      "synthetic_calls": network.calls}
            (ARTIFACTS / (stem + ".json")).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            if not page.is_closed():
                page.screenshot(path=str(ARTIFACTS / (stem + "-viewport.png")))
            browser.close()
