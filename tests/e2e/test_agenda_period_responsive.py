"""Responsive browser checks for the Orion day/week/month planner (synthetic data only)."""
from pathlib import Path
import os

import pytest
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[2]
CSS = (ROOT / "app/static/orion-suite.css").read_text(encoding="utf-8")
SCRIPT = (ROOT / "app/static/orion-suite.js").read_text(encoding="utf-8")
HELPERS = SCRIPT[SCRIPT.index("  const agendaPeriod="):SCRIPT.index("  function agendaFrame(")]
RENDERER = SCRIPT[SCRIPT.index("  function agendaRangeView("):SCRIPT.index("  function renderAgenda(){")]

HTML = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<style>
html,body{margin:0;padding:0;min-width:0;width:100%;overflow-x:clip}
*{box-sizing:border-box}
#page-suite{container:agenda / inline-size;max-width:100%;width:1100px;margin:auto;padding:16px}
html{--os-font:system-ui,sans-serif;--os-display:system-ui,sans-serif;--os-text:#132342;--os-muted:#63718a;--os-bg:#f3f6fc;--os-card:#fff;--os-soft:#edf1f7;--os-border:#d6dfed;--os-primary:#2162d0;--os-shadow:0 1px 5px #13234214}
html[data-theme=dark]{--os-text:#e8ecfa;--os-muted:#adbbd2;--os-bg:#111b2d;--os-card:#1b293f;--os-soft:#263650;--os-border:#34435b;--os-primary:#71a3fa;--os-shadow:none;background:#111b2d;color:#e8ecfa}
body{background:var(--os-bg);color:var(--os-text)}
</style></head><body>
<main id="page-suite" class="os-main" data-os-current-route="agenda">
<div class="os-agenda-range-title"><h1>Agenda</h1></div>
<div id="osPageBody"></div>
</main></body></html>"""

RUNTIME = """
const state={filters:{agendaDay:'2026-11-13',agendaPeriod:'month',agendaFocusDay:'',agendaRangeList:false}};
const dateKey=value=>value?new Date(value).toISOString().slice(0,10):'2026-11-13';
const dayDate=day=>new Date((day||'2026-11-13')+'T12:00:00Z');
const shiftDay=(day,count)=>{let d=dayDate(day);d.setUTCDate(d.getUTCDate()+count);return d.toISOString().slice(0,10)};
const fmtDate=day=>day.slice(8,10)+'/'+day.slice(5,7)+'/'+day.slice(0,4);
const longDay=day=>new Intl.DateTimeFormat('pt-BR',{weekday:'long',month:'long',day:'numeric',timeZone:'UTC'}).format(dayDate(day));
const esc=value=>String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const number=value=>Number(value)||0;
const icon=()=>'<span aria-hidden="true"></span>';
const button=(label,action)=>'<button type="button" class="os-button os-outline os-small" data-os-action="'+action+'">'+label+'</button>';
const loading=text=>'<p>'+esc(text)+'</p>';
const appointmentRow=item=>'<button type="button" class="os-agenda-item" data-os-appointment="'+item.id+'">'+esc(item.id)+'</button>';
const agendaEmpty=()=>'<p>Sem agendamentos</p>';
const agendaPagination=()=>'<nav class="os-agenda-pagination"><span>Mais resultados</span></nav>';
""" + HELPERS + RENDERER + """
window.renderPeriod=(period,whole,focus)=>{
  state.filters.agendaPeriod=period;
  state.filters.agendaRangeList=whole;
  state.filters.agendaFocusDay=focus||'';
  const data={
    days:[
      {day:'2026-11-09',total:11,confirmed:8,completed:1,cancelled:1,no_show:1,hold:0},
      {day:'2026-11-13',total:14,confirmed:12,completed:1,cancelled:1,no_show:0,hold:0}
    ],
    total:25,has_more:false,offset:0,limit:100
  };
  const html=agendaRangeView([
    {id:'atendimento-exemplo-1',start_at:'2026-11-09T12:00:00Z'},
    {id:'atendimento-exemplo-2',start_at:'2026-11-13T13:00:00Z'}
  ],data);
  document.getElementById('osPageBody').innerHTML=html;
  return {selected:agendaSelectedDay(),days:document.querySelectorAll('[data-os-open-day]').length};
};
"""


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_agenda_periods_are_usable_across_mobile_tablet_desktop(engine):
    output = ROOT / "test-results" / "agenda-periods"
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = getattr(pw, engine).launch()
        page = browser.new_page(viewport={"width":390,"height":844},device_scale_factor=1,locale="pt-BR")
        try:
            page.set_content(HTML,wait_until="domcontentloaded")
            page.add_style_tag(content=CSS)
            page.add_script_tag(content=RUNTIME)
            for theme in ("light","dark"):
                page.locator("html").evaluate("(el,theme)=>el.dataset.theme=theme",theme)
                for width in (320,390,768,1024,1440):
                    page.set_viewport_size({"width":width,"height":850})
                    for period,whole,day_count in (("week",True,7),("week",False,7),("month",False,30),("month",True,30)):
                        result=page.evaluate("([period,whole])=>window.renderPeriod(period,whole,'')",[period,whole])
                        assert result["days"]==day_count
                        assert page.locator(".os-agenda-range-day").count()==day_count
                        assert page.locator('[aria-pressed=true]').count() <= 1
                        assert page.locator(".os-agenda-range-day").first.is_visible()
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 2"),(
                            f"Horizontal page overflow: {engine=} {theme=} {width=} {period=} {whole=}"
                        )
                        if period=="month" and not whole:
                            assert page.locator(".os-agenda-focus-shell").is_visible()
                            assert page.locator("#osAgendaRangeFocusBody").count()==1
                            assert page.locator(".os-agenda-month-calendar").is_visible()
                            assert page.locator('[data-os-open-day="2026-11-13"]').get_attribute("aria-pressed")=="true"
                        if period=="week" and whole:
                            assert page.locator(".os-agenda-range-all-list").is_visible()
                            assert page.locator("#osAgendaRangeFocusBody").count()==0
                        if period=="week" and not whole:
                            assert page.locator("#osAgendaRangeFocusBody").count()==1
                        if width in (390,1024) and period=="month" and not whole:
                            page.screenshot(path=str(output / f"{engine}-{theme}-{width}-month.png"),full_page=True)
                        if width==390 and period=="week" and whole:
                            page.screenshot(path=str(output / f"{engine}-{theme}-390-week.png"),full_page=True)
        finally:
            browser.close()
