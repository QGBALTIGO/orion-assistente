"""Auth UI contracts and privileged account gating, without a browser dependency."""
from pathlib import Path
import re
import pytest
import httpx

from app.main import app
from app.account_auth import Register, EmailMixin


def test_auth_panel_has_all_screens_without_google_or_hardcoded_owner():
    html=(Path(__file__).resolve().parents[2]/"app/static/panel.html").read_text("utf-8")
    for identifier in (
        "loginForm","registerForm","forgotForm","resetForm","rememberMe",
        "authTabLogin","authTabRegister","authGlobalMessage",
        "page-member","memberProfileName","memberProfileEmail","authSubmitRegister",
    ):
        assert re.search(r'\bid="'+identifier+'"',html),identifier
    assert "Continuar com o Google" not in html
    assert "kaykys468@gmail.com" not in html
    assert "prefers-reduced-motion" in html
    assert 'html[data-theme="light"]' in html
    assert 'prefers-color-scheme: dark' in html
    css=(Path(__file__).resolve().parents[2]/"app/static/orion-auth.css").read_text()
    assert "max-width:900px" in css and "max-width:480px" in css
    assert "orion-brand-dark.svg" in html and "orion-brand-light.svg" in html


def test_account_email_and_password_validation():
    with pytest.raises(ValueError):
        Register(name="A",email="bad@",password="short")
    value=Register(name="  Pessoa   Teste  ",email="  Person@Example.COM  ",
                   password="Twelve#Chars123")
    assert value.email=="person@example.com"
    assert value.name=="Pessoa Teste"
    with pytest.raises(ValueError):
        Register(name="Valid Name",email="person@invalid",password="Twelve#Chars123")


@pytest.mark.asyncio
async def test_assets_are_allowlisted_and_svg(monkeypatch):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="https://test") as client:
        for asset in ("orion-symbol.svg","orion-brand-dark.svg","orion-brand-light.svg"):
            r=await client.get("/brand/"+asset)
            assert r.status_code==200
            assert "image/svg+xml" in r.headers["content-type"]
            assert b"<svg" in r.content
        assert (await client.get("/brand/../panel.html")).status_code in (404,405)
        assert (await client.get("/brand/untrusted.svg")).status_code==404


def test_login_mobile_does_not_show_turquoise_header_block():
    css=(Path(__file__).resolve().parents[2]/"app/static/orion-auth.css").read_text()
    assert "@media(max-width:900px)" in css
    assert "#loginView .auth-visual{display:none}" in css
    assert "min-height:100dvh" in css
    assert "min-width:0" in css
    assert "auth-mobile-brand" in css


def test_login_checkbox_uses_native_semantics_and_correct_color_scheme():
    html=(Path(__file__).resolve().parents[2]/"app/static/panel.html").read_text()
    css=(Path(__file__).resolve().parents[2]/"app/static/orion-auth.css").read_text()
    assert 'id="rememberMe" type="checkbox"' in html
    assert 'accent-color:var(--os-primary)' in css
    assert 'appearance:auto' in css
    assert 'color-scheme:light' in css and 'color-scheme:dark' in css
    assert ':focus-visible' in css


def test_login_uses_the_panel_tokens_fonts_and_not_an_independent_palette():
    html=(Path(__file__).resolve().parents[2]/"app/static/panel.html").read_text()
    css=(Path(__file__).resolve().parents[2]/"app/static/orion-auth.css").read_text()
    assert html.index('/panel-assets/orion-auth.css') > html.index('/panel-assets/orion-suite.css')
    for token in ('--os-bg','--os-card','--os-primary','--os-muted','--os-border','--os-font','--os-display'):
        assert 'var('+token+')' in css
    for forbidden in ('linear-gradient','auth-orbit','#063642','#14b8a6','#20c997'):
        assert forbidden not in css
    assert 'prefers-reduced-motion:reduce' in css
    assert 'class="auth-orbit"' not in html


def test_desktop_login_has_two_fluid_columns_and_never_fixes_page_height():
    css=(Path(__file__).resolve().parents[2]/"app/static/orion-auth.css").read_text()
    assert 'grid-template-columns:minmax(0,1fr) minmax(0,1fr)' in css
    assert 'max-width:440px' in css
    assert 'min-width:0' in css
    assert 'position:fixed' not in css and 'height:100vh;' not in css
    assert '@media(max-width:900px)' in css and '@media(max-width:480px)' in css
    assert '#loginView .auth-shell{display:block' in css


def test_signup_is_immediate_and_otp_is_only_for_recovery():
    html=(Path(__file__).resolve().parents[2]/"app/static/panel.html").read_text("utf-8")
    for identity in ("registerForm","resetEmail","resetCode","forgotForm"):
        assert f'id="{identity}"' in html
    for identity in ("verifyForm","authSubmitVerify","authResendCode"):
        assert f'id="{identity}"' not in html
    for route in ('"/auth/register"','"/auth/password/forgot"','"/auth/password/reset"'):
        assert route in html
    assert 'autocomplete="one-time-code"' in html
    assert 'pattern="[0-9]{6}"' in html
    assert 'Conta criada. Faça login com seu e-mail e senha.' in html
    assert 'authScreenShow("verify")' not in html
    assert 'token:resetToken' not in html
    assert 'Enviaremos um link seguro' not in html


def test_otp_requires_server_secret_and_is_validated():
    from app import account_auth
    from pydantic import ValidationError
    import os
    import pytest
    assert not account_auth.mail_ready() or len(os.getenv("PANEL_OTP_SECRET",""))>=32
    for wrong in ("123", "12345a", "1234567", ""):
        with pytest.raises(ValidationError):
            account_auth.OneTimeCode(email="test@example.com",code=wrong)
    good=account_auth.OneTimeCode(email="TeSt@Example.com",code="001289")
    assert good.email=="test@example.com"
    assert good.code=="001289"


def test_registration_and_password_recovery_require_eight_characters():
    from app.account_auth import Register, ResetPassword
    from pydantic import ValidationError

    # Eight characters are accepted on both routes.
    registration=Register(name="Pessoa Teste",email="test@example.com",password="Abc12345")
    reset=ResetPassword(email="test@example.com",code="123456",password="Abc12345")
    assert len(registration.password)==8 and len(reset.password)==8
    # Shorter passwords remain invalid on server models.
    with pytest.raises(ValidationError):
        Register(name="Pessoa Teste",email="test@example.com",password="Abc1234")
    with pytest.raises(ValidationError):
        ResetPassword(email="test@example.com",code="123456",password="Abc1234")


def test_signup_has_no_redundant_notice_and_matches_password_minimum():
    html=(Path(__file__).resolve().parents[2]/"app/static/panel.html").read_text("utf-8")
    assert 'registrationHint' not in html
    assert "A conta é criada imediatamente. Recuperação de senha em teste" not in html
    assert "mínimo de 8 caracteres" in html
    assert 'Mínimo de 8 caracteres' in html
    for name in ("registerPassword","registerConfirm","resetPassword","resetConfirm"):
        assert re.search(r'id="'+name+r'"[^>]*minlength="8"',html),name


def test_member_logs_directly_into_normal_dashboard_without_admin_claim():
    html=(Path(__file__).resolve().parents[2]/"app/static/panel.html").read_text("utf-8")
    assert 'id="page-member"' in html
    assert 'data-page="member" data-member-only' in html
    for forbidden in ("ownerClaimForm","ownerLegacyPassword","memberView",
                      "Senha administrativa anterior","Já administra o Orion?",
                      "Validar permissão administrativa"):
        assert forbidden not in html
    assert 'id="memberProfileName"' in html
    assert 'id="memberProfileEmail"' in html
    assert 'id="sidebarAccountName"' in html
    assert 'id="sidebarAvatarInitial"' in html
    assert 'if(!isAdmin){' in html
    assert 'openPage("member");' in html
    member_block=html.split('if(!isAdmin){',1)[1].split('return;',1)[0]
    assert "updateStatus()" not in member_block
    assert "loadTenants()" not in member_block
    # A direct URL, an old bookmarked tab or console invocation can't make
    # a member navigate the privileged pages. Server endpoints enforce role too.
    assert 'if(loginRole!=="support"&&loginRole!=="admin")target="member";' in html
    assert 'document.querySelectorAll("[data-admin-only]")' in html


def test_account_profile_endpoint_keeps_only_its_own_identity():
    from app import panel_auth
    import inspect
    code=inspect.getsource(panel_auth.panel_actor)
    endpoint=inspect.getsource(panel_auth.me)
    assert 'data["name"] = row["name"]' in code
    assert 'data["email"] = row["email"]' in code
    assert 'data["role"]' in code
    assert 'data.get("name","Administrador")' in endpoint


def test_orion_dashboard_themes_share_login_brand_and_svg_navigation():
    html=(Path(__file__).resolve().parents[2]/"app/static/panel.html").read_text("utf-8")
    for value in ("auto","light","dark"):
        assert f'data-theme-choice="{value}"' in html
    for section in ("page-member","page-dispositivo","page-conversas","page-cadastros"):
        assert f'id="{section}"' in html
    assert 'localStorage.setItem("orion-theme",safe)' in html
    assert 'window.matchMedia("(prefers-color-scheme: dark)")' in html
    assert 'data-orion-logo' in html
    assert '/brand/orion-brand-light.svg' in html
    assert '/brand/orion-brand-dark.svg' in html
    assert 'html[data-theme="dark"]' in html
    assert 'html[data-theme="light"]' in html
    assert "--sidebar-bg:" in html and "--control-bg:" in html
    assert "html[data-theme=\"light\"] .device-stat strong" in html
    assert "prefers-reduced-motion:reduce" in html
    assert '<div class="logo">O</div>' not in html
    for icon in ("⌂","▦","✉","⚙"):
        assert f'<span class="nav-icon">{icon}</span>' not in html
    for image in ("orion-brand-dark.svg","orion-brand-light.svg"):
        assert image in html


def test_orion_theme_ui_is_accessible_and_mobile_usable():
    html=(Path(__file__).resolve().parents[2]/"app/static/panel.html").read_text("utf-8")
    for label in ("Tema Automático, seguir o dispositivo","Tema Claro","Tema Escuro"):
        assert f'aria-label="{label}"' in html
    assert "role=\"group\" aria-label=\"Escolher aparência\"" in html
    assert html.count('data-theme-choice="')==6
    assert 'button.setAttribute("aria-pressed",String(button.dataset.themeChoice===safe))' in html
    assert "@media(max-width:790px)" in html
    assert ".sidebar-bottom{display:flex" in html
    assert ".sidebar-bottom{display:grid" in html
    assert '.theme-choice:focus-visible' in html or 'button:focus-visible' in html
    assert "document.documentElement.dataset.themePreference" in html


@pytest.mark.asyncio
async def test_auth_stylesheet_is_available_without_login():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="https://test") as client:
        response=await client.get('/panel-assets/orion-auth.css')
    assert response.status_code==200 and 'text/css' in response.headers['content-type']
    assert '--os-primary' in response.text


def test_auth_keyboard_tabs_caps_lock_and_loading_state_are_accessible():
    html=(Path(__file__).resolve().parents[2]/"app/static/panel.html").read_text()
    assert 'aria-controls="loginForm"' in html and 'aria-controls="registerForm"' in html
    assert 'ArrowRight' in html and 'ArrowLeft' in html
    assert 'getModifierState' in html and 'CapsLock' in html
    assert 'aria-busy' in html and 'Entrando...' in html
