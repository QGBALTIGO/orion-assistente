"""Regression tests for the Orion daily, weekly and monthly agenda."""
from datetime import date
import json
from pathlib import Path
import shutil
import subprocess

import pytest
from fastapi import HTTPException

from app.booking_api import appointment_period


ROOT = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")


def test_appointment_period_accepts_day_week_and_full_month():
    assert appointment_period(date(2026, 11, 13), None, None) == (date(2026, 11, 13),) * 2
    assert appointment_period(None, date(2026, 11, 8), date(2026, 11, 14)) == (
        date(2026, 11, 8), date(2026, 11, 14)
    )
    assert appointment_period(None, date(2026, 11, 1), date(2026, 11, 30)) == (
        date(2026, 11, 1), date(2026, 11, 30)
    )
    assert appointment_period(None, date(2028, 2, 1), date(2028, 2, 29)) == (
        date(2028, 2, 1), date(2028, 2, 29)
    )


@pytest.mark.parametrize(("day", "start", "end"), [
    (None, None, None),
    (None, date(2026, 11, 1), None),
    (None, None, date(2026, 11, 30)),
    (date(2026, 11, 13), date(2026, 11, 1), date(2026, 11, 30)),
    (None, date(2026, 11, 14), date(2026, 11, 8)),
    (None, date(2026, 11, 1), date(2026, 12, 2)),
    (date(1899, 12, 31), None, None),
    (None, date(2100, 12, 1), date(2101, 1, 1)),
])
def test_appointment_period_fails_closed(day, start, end):
    with pytest.raises(HTTPException) as caught:
        appointment_period(day, start, end)
    assert caught.value.status_code == 422


@pytest.mark.skipif(not NODE, reason="Node.js needed for actual client-controller tests")
def test_agenda_browser_range_calculations_and_month_grid():
    script = (ROOT / "app/static/orion-suite.js").read_text(encoding="utf-8")
    helpers = script[script.index("  const agendaPeriod="):script.index("  function agendaFrame(")]
    renderer = script[script.index("  function agendaRangeView("):script.index("  function renderAgenda(){")]
    source = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');
const helpers=EXTRACTED_HELPERS,renderer=EXTRACTED_RENDERER;
function inspect(day,period,withList=false){
  const ctx={
    state:{filters:{agendaPeriod:period,agendaDay:day,agendaRangeList:withList}},
    dateKey(value){return value?new Date(value).toISOString().slice(0,10):'2026-11-13';},
    dayDate(day){return new Date((day||'2026-11-13')+'T12:00:00Z');},
    shiftDay(day,n){const v=new Date(day+'T12:00:00Z');v.setUTCDate(v.getUTCDate()+n);return v.toISOString().slice(0,10);},
    fmtDate(day){return day.slice(8,10)+'/'+day.slice(5,7)+'/'+day.slice(0,4);},
    longDay(day){return day;},esc(value){return String(value);},
    number(value){return Number(value)||0;},
    appointmentRow(item){return '<article>'+item.id+'</article>';},
    agendaPagination(){return '<nav>pages</nav>';},
    agendaEmpty(){return '<div>empty</div>';},
    Date,Intl
  };
  const code=helpers+renderer+';
    globalThis.out={
      bounds:agendaBounds(),label:agendaPeriodLabel(),
      next:agendaShiftMonth(state.filters.agendaDay,1),prev:agendaShiftMonth(state.filters.agendaDay,-1),
      html:agendaRangeView([],{
        days:[{day:"2026-11-13",total:14,confirmed:12,completed:1,cancelled:1,no_show:0,hold:0}],
        total:14,has_more:false
      })
    };';
  vm.runInNewContext(code,ctx);
  return ctx.out;
}
const month=inspect('2026-11-13','month'),
      week=inspect('2026-11-13','week'),
      cross=inspect('2027-01-01','week'),
      leap=inspect('2028-02-13','month');
assert.deepEqual(JSON.parse(JSON.stringify(month.bounds)),{start:'2026-11-01',end:'2026-11-30'});
assert.deepEqual(JSON.parse(JSON.stringify(week.bounds)),{start:'2026-11-08',end:'2026-11-14'});
assert.deepEqual(JSON.parse(JSON.stringify(cross.bounds)),{start:'2026-12-27',end:'2027-01-02'});
assert.equal(leap.bounds.end,'2028-02-29');
assert.equal(month.next,'2026-12-01');
assert.equal(month.prev,'2026-10-01');
assert.equal((month.html.match(/data-os-open-day=/g)||[]).length,30);
assert.equal((week.html.match(/data-os-open-day=/g)||[]).length,7);
assert.match(month.html,/Ver todos em lista/);
assert.match(month.html,/14 agendamentos/);
assert.ok(!month.html.includes('undefined'));
console.log('Day/week/month calendar and drill-down: OK');
""".replace("EXTRACTED_HELPERS", json.dumps(helpers)).replace(
        "EXTRACTED_RENDERER", json.dumps(renderer)
    )
    # Replace the split string with an explicit newline to keep the Node source valid.
    source = source.replace("const code=helpers+renderer+';\n    globalThis.out=", "const code=helpers+renderer+`;`+'globalThis.out=")
    proc = subprocess.run([NODE, "-e", source], cwd=ROOT, text=True, capture_output=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK" in proc.stdout
