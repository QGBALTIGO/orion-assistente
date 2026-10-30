"""Regression checks for the public booking confirmation action group.

The three controls share a responsive Orion component; their routes,
clipboard and calendar logic remain available without any remote assets.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "app/static/orion-public.js"
STYLES = ROOT / "app/static/orion-public.css"


def actions_source():
    text = SCRIPT.read_text(encoding="utf-8")
    return text.split("  function renderSuccess(body) {", 1)[1].split(
        "  function downloadCalendar() {", 1)[0]


def test_success_actions_share_one_group_and_existing_handlers():
    source = actions_source()
    assert "const actions = el('div', 'success-actions');" in source
    assert "actions.setAttribute('role', 'group');" in source
    assert "success.append(actions);" in source
    assert "el('a', 'primary success-action success-action-primary')" in source
    assert "wa.href = whatsappURL;" in source
    assert "wa.rel = 'noopener noreferrer';" in source
    assert "CONTACT_ICONS.whatsapp" in source
    assert "button('Copiar protocolo', 'outline success-action success-action-secondary'" in source
    assert "await navigator.clipboard.writeText(booking.result.protocol);" in source
    assert "SUCCESS_ACTION_ICONS.copy" in source
    assert "'outline success-action success-action-secondary', downloadCalendar" in source
    assert "SUCCESS_ACTION_ICONS.calendar" in source
    assert "el('a', 'pill', 'Abrir WhatsApp novamente')" not in source


def test_copy_feedback_is_a_success_not_a_red_error():
    source = actions_source()
    assert "label.textContent = 'Protocolo copiado!';" in source
    assert "label.textContent = 'Copiar protocolo';" in source
    assert "showError('Protocolo copiado" not in source
    assert "showError('Não foi possível copiar." in source


def test_success_actions_are_full_width_and_wrappable_on_mobile():
    css = STYLES.read_text(encoding="utf-8")
    rule = re.search(r"#booking-dialog \.success \.success-action\s*\{([^}]+)\}", css)
    assert rule, "Success action shared sizing rule missing"
    compact = re.sub(r"\s+", "", rule.group(1))
    for expected in ("width:100%", "min-width:0", "min-height:52px",
                     "white-space:normal", "overflow-wrap:anywhere",
                     "justify-content:center", "gap:10px"):
        assert expected in compact
    assert "#booking-dialog .success-actions{grid-template-columns:repeat(2,minmax(0,1fr))}" in css
    assert "#booking-dialog .success-action-primary{grid-column:1/-1}" in css
    assert "font-size:13px;min-height:48px" in css


def test_primary_and_secondary_colors_follow_the_site_theme():
    css = STYLES.read_text(encoding="utf-8")
    primary = re.search(r"#booking-dialog \.success \.success-action-primary\s*\{([^}]+)\}", css)
    secondary = re.search(r"#booking-dialog \.success \.success-action-secondary\s*\{([^}]+)\}", css)
    assert primary and secondary
    assert "background:var(--primary)" in primary.group(1)
    assert "color:var(--primary-ink)" in primary.group(1)
    assert "background:var(--card)" in secondary.group(1)
    assert "border:1px solid var(--border)" in secondary.group(1)
