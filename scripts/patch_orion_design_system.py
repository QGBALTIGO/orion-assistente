"""Migrate existing components to shared Orion UI primitives without replacing feature bundles."""
import re
import sys
from pathlib import Path

SEGMENTS = {
    "af-dashboard-periods": "data-dashboard-period",
    "af-dashboard-metric-tabs": "data-dashboard-metric",
    "af-dashboard-publication-tabs": "data-dashboard-publications",
    "af-offer-tabs": "data-offer-tab",
    "af-auto-filters": "data-rule-filter",
    "af-history-filters": "data-history-filter",
    "af-rule-presets": "data-rule-interval",
}
HEADINGS = ("af-dashboard-heading", "af-offers-heading", "af-auto-heading", "af-history-heading")

def once(source, old, new):
    if source.count(old) != 1:
        raise RuntimeError("Unexpected integration anchor: " + old[:180])
    return source.replace(old, new, 1)

def class_on(source, name, extra):
    count = 0
    def change(match):
        nonlocal count
        classes = match[1].split()
        if name not in classes:
            return match[0]
        count += 1
        for token in extra.split():
            if token not in classes:
                classes.append(token)
        return 'class="' + ' '.join(classes) + '"'
    source = re.sub(r'class="([^"]*)"', change, source)
    if not count:
        raise RuntimeError("Missing component: " + name)
    return source

def patch_affiliate(source):
    for group, attribute in SEGMENTS.items():
        source = class_on(source, group, "os-pill-tabs os-ui-segments")
        pattern = r'<button type="button" (?=' + re.escape(attribute) + r'=)'
        source, count = re.subn(pattern, '<button type="button" class="os-pill-tab" ', source)
        if not count:
            raise RuntimeError("Missing segmented control: " + attribute)
    for name in HEADINGS:
        source = class_on(source, name, "os-title-row os-ui-heading")
    source = class_on(source, "af-heading-actions", "os-actions os-ui-actions")
    source = class_on(source, "af-dashboard-heading-actions", "os-actions os-ui-actions")
    # Subtitle is part of the same title block on every page.
    pattern = r'(<header class="af-dashboard-heading os-title-row os-ui-heading"><div>.*?)</div>(<div class="af-dashboard-heading-actions os-actions os-ui-actions">.*?</div>)(<p>.*?</p>)</header>'
    source, count = re.subn(pattern, r'\1\3</div>\2</header>', source, count=1)
    if count != 1:
        raise RuntimeError("Dashboard heading anchor changed")
    pattern = r'(<header class="af-auto-heading os-title-row os-ui-heading">.*?</p></div>)<div>(<button type="button" class="os-icon-button" data-act="refresh-automations")'
    source, count = re.subn(pattern, r'\1<div class="os-actions os-ui-actions">\2', source, count=1)
    if count != 1:
        raise RuntimeError("Automation actions anchor changed")
    source = once(source, '<div class="os-title-row"><div><h1>${html(title)}</h1><p>${html(sub)}</p></div><div class="af-heading-actions os-actions os-ui-actions">',
                  '<div class="os-title-row os-ui-heading"><div><h1>${html(title)}</h1><p>${html(sub)}</p></div><div class="af-heading-actions os-actions os-ui-actions">')
    return source

def patch_account(source):
    source = once(source, '<nav class="os-settings-menu os-account-menu" aria-label="Seções de Minha conta"></nav>',
                  '<nav class="os-pill-tabs os-ui-segments os-account-menu" aria-label="Seções de Minha conta"></nav>')
    old = "if(!menu.firstElementChild)menu.innerHTML=accountSections.map(([key,symbol,label,description])=>'<button type=\"button\" data-os-account-tab=\"'+key+'\" aria-current=\"false\"><span class=\"os-settings-icon\">'+icon(symbol)+'</span><span><strong>'+label+'</strong><small>'+description+'</small></span></button>').join('');"
    new = "if(!menu.firstElementChild)menu.innerHTML=accountSections.map(([key,,label])=>'<button type=\"button\" class=\"os-pill-tab\" data-os-account-tab=\"'+key+'\" aria-current=\"false\" aria-pressed=\"false\" aria-label=\"'+esc(label)+'\">'+esc(({profile:'Pessoal',business:'Empresa'})[key]||label)+'</button>').join('');"
    source = once(source, old, new)
    old = "menu.querySelectorAll('[data-os-account-tab]').forEach(el=>el.setAttribute('aria-current',String(el.dataset.osAccountTab===tab)));"
    new = """menu.querySelectorAll('[data-os-account-tab]').forEach(el=>{const selected=String(el.dataset.osAccountTab===tab);el.setAttribute('aria-current',selected);el.setAttribute('aria-pressed',selected);});
    const active=menu.querySelector('[aria-pressed="true"]');
    if(active&&menu.scrollWidth>menu.clientWidth){
      const outer=menu.getBoundingClientRect(),inner=active.getBoundingClientRect();
      if(inner.left<outer.left)menu.scrollLeft+=inner.left-outer.left-4;
      else if(inner.right>outer.right)menu.scrollLeft+=inner.right-outer.right+4;
    }"""
    return once(source, old, new)

def patch(root):
    folder = root / "app/static"
    suite = folder / "orion-suite.js"
    affiliate = folder / "orion-affiliate-workspace.js"
    stylesheet = folder / "orion-suite.css"
    css = (folder / "orion-design-system.css").read_text()
    suite_source = patch_account(suite.read_text())
    affiliate_source = patch_affiliate(affiliate.read_text())
    suite_source, count = re.subn(r"workspace\.css\?v=[^']+", "workspace.css?v=orion-ui-system-20261126", suite_source, count=1)
    if count != 1:
        raise RuntimeError("Affiliate CSS cache anchor changed")
    if "workspace.js?v=" in suite_source:
        suite_source = re.sub(r"workspace\.js\?v=[^']+", "workspace.js?v=orion-ui-system-20261126", suite_source, count=1)
    else:
        suite_source = once(suite_source, "afiliados-assets/workspace.js'", "afiliados-assets/workspace.js?v=orion-ui-system-20261126'")
    shortener = folder / "orion-affiliate-shortener.js"
    shortener_source = once(shortener.read_text(), '<header class="os-title-row">', '<header class="os-title-row os-ui-heading">')
    suite_source = suite_source.replace("shortener.js?v=1", "shortener.js?v=orion-ui-system-20261126")
    suite.write_text(suite_source)
    affiliate.write_text(affiliate_source)
    shortener.write_text(shortener_source)
    stylesheet.write_text(stylesheet.read_text().rstrip()+"\n\n"+css)

if __name__ == "__main__":
    patch(Path(sys.argv[1] if len(sys.argv)>1 else "."))
