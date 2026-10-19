"""Auth UI contracts and privileged account gating, without a browser dependency."""
from pathlib import Path
import re
import pytest
import httpx

from app.main import app
from app.account_auth import Register, EmailMixin


def auth_styles():
    return (Path(__file__).resolve().parents[2]/"app/static/orion-auth.css").read_text("utf-8")


def compact_auth_styles():
    return re.sub(r"\s+", "", auth_styles())


def test_auth_panel_has_all_screens_without_google_or_hardcoded_owner():
    html=(Path(__file__).resolve().parents[2]/"app/static/panel.html").read_text("utf-8")
    for identifier in (
        "loginForm","registerForm","forgotForm","resetForm","rememberMe",
        "authTabLogin","authTabRegister","authGlobalMessage",
        "page-member","memberProfileName","memberProfileEmail","authSubmitRegister",
        "authTitle","authSubtitle","authRegisterAvailability","authForgotAvailability",
    ):
        assert re.search(r'\bid="'+identifier+'"',html),identifier
    assert "Continuar com o Google" not in html
    assert "kaykys468@gmail.com" not in html
    assert "prefers-reduced-motion" in html
    assert 'html[data-theme="light"]' in html
    assert 'prefers-color-scheme: dark' in html
    styles=compact_auth_styles()
    assert "max-width:900px" in styles and "max-width:560px" in styles
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
        stylesheet=await client.get("/panel-assets/orion-auth.css")
        assert stylesheet.status_code==200
        assert "text/css" in stylesheet.headers["content-type"]
        assert b"#loginView" in stylesheet.content
        assert (await client.get("/panel-assets/untrusted.css")).status_code==404


def test_login_mobile_does_not_show_turquoise_header_block():
    """Mobile/tablet uses a single clean auth layout without the marketing strip."""
    html=(Path(__file__).resolve().parents[2]/"app/static/panel.html").read_text("utf-8")
    styles=compact_auth_styles()
    mobile=styles.split("@media(max-width:900px)",1)[1].split("@media",1)[0]
    assert "#loginView.auth-shell{display:flex;flex-direction:column;" in mobile
    assert "#loginView.auth-visual{display:none;" in mobile
    assert "#loginView.auth-right" in mobile
    assert "min-height:100svh" in styles
    assert "auth-mobile-brand" in html


def test_login_checkbox_has_real_unchecked_and_checked_appearance():
    """Custom checkbox avoids native browser black fill for unchecked light theme."""
    html=(Path(__file__).resolve().parents[2]/"app/static/panel.html").read_text("utf-8")
    styles=compact_auth_styles()
    assert 'id="rememberMe" type="checkbox"' in html
    unchecked=styles.split("#loginView.auth-checkinput[type=checkbox]{",1)[1].split("}",1)[0]
    checked=styles.split("#loginView.auth-checkinput[type=checkbox]:checked{",1)[1].split("}",1)[0]
    assert "appearance:none" in unchecked and "-webkit-appearance:none" in unchecked
    assert "background:var(--os-bg)" in unchecked
    assert "background-color:var(--os-primary)" in checked
    assert "background-image:url(" in checked
    assert "#loginView:is(button,a,input):focus-visible" in styles


def test_login_reuses_the_dashboard_fonts_colors_and_motion():
    """The login must consume the dashboard design system rather than retain its old palette."""
    html=(Path(__file__).resolve().parents[2]/"app/static/panel.html").read_text("utf-8")
    styles=auth_styles()
    suite=(Path(__file__).resolve().parents[2]/"app/static/orion-suite.css").read_text("utf-8")
    assert html.index('href="/panel-assets/orion-suite.css"') < html.index('href="/panel-assets/orion-auth.css"')
    used=set(re.findall(r"var\((--os-[\w-]+)",styles))
    declared=set(re.findall(r"(--os-[\w-]+)\s*:",suite))
    assert used <= declared, f"Unknown dashboard design tokens: {used-declared}"
    for token in ("--os-font","--os-display","--os-bg","--os-card","--os-text",
                  "--os-muted","--os-primary","--os-border","--os-danger"):
        assert token in used
    assert "@font-face" not in styles, "Authentication must not introduce a second font bundle"
    assert "auth-primary os-button os-full" in html
    assert "auth-tabs os-tabs" in html
    assert "animation:os-toast-in" in compact_auth_styles()
    assert "@media(prefers-reduced-motion:reduce)" in compact_auth_styles()
    for obsolete in ("#063642","#1ac6b1","#23c6ab","auth-orbit"):
        assert obsolete not in styles


def test_desktop_login_uses_the_viewport_and_equal_columns_without_clipping():
    """Preserves the full desktop split, a bounded form and vertical overflow for short screens."""
    styles=compact_auth_styles()
    desktop=styles.split("@media(min-width:901px)",1)[1].split("@media",1)[0]
    assert "position:fixed;inset:0" in desktop
    assert "overflow-x:hidden;overflow-y:auto" in desktop
    assert "grid-template-columns:repeat(2,minmax(0,1fr))" in styles
    assert "width:100%" in styles and "min-width:0" in styles
    assert "max-width:466px" in styles
    assert "#loginView.auth-visual{display:none;" in styles
    assert "@media(max-width:900px)" in styles


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
    assert html.count('data-theme-choice="')==6, "Login and administration both expose the same three preferences"
    assert 'button.setAttribute("aria-pressed",String(button.dataset.themeChoice===safe))' in html
    assert "@media(max-width:790px)" in html
    assert ".sidebar-bottom{display:flex" in html
    assert ".sidebar-bottom{display:grid" in html
    assert '.theme-choice:focus-visible' in html or 'button:focus-visible' in html
    assert "document.documentElement.dataset.themePreference" in html
