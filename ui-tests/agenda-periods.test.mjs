import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync('app/static/orion-suite.js', 'utf8');
const helpers = source.slice(source.indexOf('  const agendaPeriod='), source.indexOf('  function agendaFrame('));
const renderer = source.slice(source.indexOf('  function agendaRangeView('), source.indexOf('  function renderAgenda(){'));
assert.ok(helpers.length > 100 && renderer.length > 100);

function inspect(day, period, withList=false) {
  const ctx = {
    state: { filters: { agendaDay: day, agendaPeriod: period, agendaRangeList: withList } },
    dateKey(value) { return value ? new Date(value).toISOString().slice(0,10) : '2026-11-13'; },
    dayDate(day) { return new Date((day || '2026-11-13') + 'T12:00:00Z'); },
    shiftDay(day,n) { const d = new Date(day + 'T12:00:00Z'); d.setUTCDate(d.getUTCDate()+n); return d.toISOString().slice(0,10); },
    fmtDate(day) { return day.slice(8,10)+'/'+day.slice(5,7)+'/'+day.slice(0,4); },
    longDay(day) { return day; },
    esc(value) { return String(value); },
    number(value) { return Number(value)||0; },
    appointmentRow(item) { return '<article>'+item.id+'</article>'; },
    agendaPagination() { return '<nav>pages</nav>'; },
    agendaEmpty() { return '<div>empty</div>'; },
    Date, Intl,
  };
  vm.runInNewContext(helpers + renderer + `
    globalThis.result = {
      bounds: agendaBounds(), next: agendaShiftMonth(state.filters.agendaDay, 1),
      prev: agendaShiftMonth(state.filters.agendaDay, -1),
      html: agendaRangeView([], {days:[{day:'2026-11-13',total:14,confirmed:12,completed:1,cancelled:1,no_show:0,hold:0}],total:14,has_more:false})
    };
  `, ctx);
  return ctx.result;
}

test('monthly grid shows 30 selectable days and full count', () => {
  const v=inspect('2026-11-13','month');
  assert.deepEqual(JSON.parse(JSON.stringify(v.bounds)),{start:'2026-11-01',end:'2026-11-30'});
  assert.equal((v.html.match(/data-os-open-day=/g)||[]).length,30);
  assert.match(v.html,/14 agendamentos/);
  assert.match(v.html,/Ver todos em lista/);
  assert.equal(v.next,'2026-12-01');
  assert.equal(v.prev,'2026-10-01');
});

test('weekly grid has 7 days and respects year boundaries', () => {
  const v=inspect('2026-11-13','week');
  assert.deepEqual(JSON.parse(JSON.stringify(v.bounds)),{start:'2026-11-08',end:'2026-11-14'});
  assert.equal((v.html.match(/data-os-open-day=/g)||[]).length,7);
  const rollover=inspect('2027-01-01','week');
  assert.deepEqual(JSON.parse(JSON.stringify(rollover.bounds)),{start:'2026-12-27',end:'2027-01-02'});
});

test('leap year February has 29 days', () => {
  const v=inspect('2028-02-13','month');
  assert.equal(v.bounds.end,'2028-02-29');
  assert.equal((v.html.match(/data-os-open-day=/g)||[]).length,29);
});

test('monthly list can be expanded and all labels stay defined', () => {
  const v=inspect('2026-11-13','month',true);
  assert.match(v.html,/Ocultar lista/);
  assert.ok(!v.html.includes('undefined'));
});
