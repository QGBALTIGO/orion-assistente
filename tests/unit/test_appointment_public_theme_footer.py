"""Cross-page Orion theme/footer contract for the private appointment link.

The receipt must share the public site's appearance preference, accessible
sun/moon icon and branding without moving or exposing private booking data.
"""
from pathlib import Path
from html.parser import HTMLParser
import re

ROOT = Path(__file__).resolve().parents[2] / "app" / "static"
HTML = (ROOT / "orion-appointment.html").read_text(encoding="utf-8")
JS = (ROOT / "orion-appointment.js").read_text(encoding="utf-8")
CSS = (ROOT / "orion-appointment.css").read_text(encoding="utf-8")
PUBLIC_HTML = (ROOT / "orion-public.html").read_text(encoding="utf-8")
SHARED_JS = (ROOT / "orion-public-theme.js").read_text(encoding="utf-8")


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def parse(source):
    parser = Tags()
    parser.feed(source)
    return parser.tags


def test_exact_shared_theme_button_and_script_used_on_receipt():
    receipt = parse(HTML)
    public = parse(PUBLIC_HTML)
    for document in (receipt, public):
        assert any(tag == 'button' and attrs.get('id') == 'public-theme-toggle'
                   and 'appearance-trigger' in attrs.get('class', '')
                   for tag, attrs in document)
        assert any(tag == 'script' and attrs.get('src') == '/public-assets/orion-public-theme.js'
                   for tag, attrs in document)
        assert any(tag == 'svg' and 'appearance-sun' in attrs.get('class', '')
                   for tag, attrs in document)
        assert any(tag == 'svg' and 'appearance-moon' in attrs.get('class', '')
                   for tag, attrs in document)
    assert '<button id="theme"' not in HTML
    assert re.search(r'>Tema</button>', HTML) is None


def test_theme_preference_is_shared_and_system_aware():
    assert "orion-public-theme" in SHARED_JS
    assert "localStorage.getItem(key)" in SHARED_JS
    assert "localStorage.setItem(key, preference)" in SHARED_JS
    assert "prefers-color-scheme: dark" in SHARED_JS
    assert "root.dataset.themePreference = preference;" in SHARED_JS
    assert "root.dataset.theme = current;" in SHARED_JS
    assert "const meta = document.querySelector('meta[name=\"theme-color\"]');" in SHARED_JS
    assert "document.documentElement.dataset.theme=theme" not in JS
    assert "$('theme').onclick" not in JS
    assert '<meta name="theme-color" content="#f5f8fc">' in HTML


def test_receipt_footer_reuses_exact_public_copy_and_safe_business_name():
    assert '<span id="orion-credit">Agendamento com <a href="/" aria-label="Orion">Orion</a></span>' in HTML
    assert 'Organizado com Orion Agenda' not in HTML
    assert '<footer class="public-footer"><span id="footer-title"></span>' in HTML
    assert "'footer-title').textContent=data.company" in JS
    assert 'main > .public-footer' in CSS
    assert 'border-top:1px solid var(--border)' in CSS
    assert 'font-family:Outfit,Figtree,sans-serif;font-size:16px;font-weight:700' in CSS
    assert 'min-width:0;overflow-wrap:anywhere' in CSS


def test_appearance_is_icon_only_borderless_accessible_and_responsive():
    rule = re.search(r'header \.appearance-trigger\s*\{([^}]+)\}', CSS)
    assert rule
    normalized = re.sub(r'\s+', '', rule.group(1))
    for value in ("width:44px", "height:44px", "background:transparent",
                  "border:0", "color:var(--text)", "margin:0", "padding:0"):
        assert value in normalized
    assert 'aria-label="Alternar tema do site"' in HTML
    assert 'header .appearance-trigger:focus-visible' in CSS
    assert ':root[data-theme="dark"] header .appearance-trigger .appearance-moon{display:block}' in CSS
    assert 'main > .public-footer{flex-direction:row;justify-content:space-between;text-align:left}' in CSS
    assert '@media(min-width:640px)' in CSS
