import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {makeSuite, flush, deferred, defaultResponse} from './harness.mjs';

const tz='America/Sao_Paulo';
const today=()=>new Intl.DateTimeFormat('en-CA',{timeZone:tz,year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
const p1='10000000-0000-4000-8000-000000000001';
const p2='20000000-0000-4000-8000-000000000002';
const p3='30000000-0000-4000-8000-000000000003';
const goal=(professional_id,target_value,actual_value,{metric='revenue_cents',active=true,start_date=today(),end_date=today(),id=professional_id}={})=>
  ({professional_id,target_value,actual_value,metric,active,start_date,end_date,id});
const money=value=>new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'}).format(value/100);
const financial=({balance_cents=90000,count=2}={})=>({summary:{balance_cents,count,income_cents:100000,expense_cents:10000}});
const snapshot=path=>path.includes('/finance/summary?')||path.includes('/goals?');
const writes=h=>h.calls.filter(c=>c.method!=='GET');
async function suite(t,{summary=financial(),goals={items:[]},role='owner',respond=null}={}){
  const h=await makeSuite({initialRoute:'dashboard',role,respond:(path,method,data)=>{
    if(respond){const result=respond(path,method,data);if(result!==undefined)return result;}
    if(path.includes('/finance/summary?'))return summary;
    if(path.includes('/goals?'))return goals;
    return defaultResponse(path);
  }});
  t.after(()=>h.close());await flush();return h;
}

test('financial cards use precisely the business-local day and the Finance report values',async t=>{
  const h=await suite(t);
  const page=h.document.getElementById('page-suite');
  const todaySection=page.querySelector('.os-dashboard-today');
  assert.ok(todaySection.querySelector('.os-dashboard-agenda-preview'));
  const preview=todaySection.querySelector('.os-dashboard-finance');
  assert.ok(preview,'finances appear below Agenda in the same business-today section');
  assert.ok(todaySection.querySelector('.os-dashboard-agenda-preview').compareDocumentPosition(preview)&h.window.Node.DOCUMENT_POSITION_FOLLOWING);
  assert.equal(preview.querySelector('#osDashboardFinanceContent').getAttribute('aria-busy'),null);
  assert.equal(preview.querySelector('.os-dashboard-finance-values').children.length,2);
  assert.match(preview.textContent,/Saldo\/Lucro/);
  assert.match(preview.textContent,/Lançamentos/);
  assert.ok(preview.textContent.includes(money(90000)));
  assert.match(preview.textContent,/2/);
  assert.equal(h.document.querySelector('#osDashboardGoals').hidden,true,'no goals: do not invent a bar');
  const reads=h.calls.filter(x=>snapshot(x.path));
  assert.equal(reads.length,2);
  for(const read of reads){
    const url=new URL(read.path,'https://orion.test');
    assert.equal(url.searchParams.get('start_date'),today());
    assert.equal(url.searchParams.get('end_date'),today());
    assert.equal(read.method,'GET');
  }
  assert.deepEqual(writes(h),[]);
  assert.deepEqual(h.errors,[]);
});

test('active team revenue goals of R$ 100 and R$ 300 become one R$ 400 objective',async t=>{
  const h=await suite(t,{goals:{items:[
    goal(p1,10000,8000),goal(p2,30000,22000),
    goal(p3,99900,90000,{active:false}),
    goal('99900000-0000-4000-8000-000000000009',80000,30000,{start_date:'2020-01-01',end_date:'2020-01-02'})
  ]}});
  const section=h.document.querySelector('#osDashboardGoals');
  assert.equal(section.hidden,false);
  assert.match(section.textContent,/Meta da equipe/);
  assert.match(section.textContent,/2 profissionais/);
  assert.ok(section.textContent.includes(money(30000)),'realized sums actual completed appointments for each goal');
  assert.ok(section.textContent.includes(money(40000)),'target sums all active professionals');
  const bar=section.querySelector('[role=progressbar]');
  assert.equal(Number(bar.getAttribute('aria-valuenow')),75);
  assert.equal(bar.querySelector('span').style.width,'75%');
  assert.equal(section.querySelectorAll('[role=progressbar]').length,1);
  assert.deepEqual(writes(h),[]);
});

test('team goals respect their configured periods and never equate daily balance to achievement',async t=>{
  const periodStart=today().slice(0,8)+'01';
  const h=await suite(t,{summary:financial({balance_cents:-10000,count:7}),goals:{items:[
    goal(p1,120000,60000,{start_date:periodStart,end_date:today()}),
    goal(p2,30000,10000,{start_date:today(),end_date:today()})
  ]}});
  const goalSection=h.document.querySelector('#osDashboardGoals');
  assert.match(goalSection.textContent,/Progresso considerando o período definido/);
  assert.ok(goalSection.textContent.includes(money(150000)));
  assert.ok(goalSection.textContent.includes(money(70000)));
  assert.ok(h.document.querySelector('.os-dashboard-finance-value.os-negative>strong').textContent.includes(money(-10000)));
  assert.ok(goalSection.querySelector('[role=progressbar]').getAttribute('aria-valuenow')==='47');
});

test('revenue and appointment goals are never summed into one incompatible unit',async t=>{
  const h=await suite(t,{goals:{items:[
    goal(p1,40000,30000),goal(p2,8,4,{metric:'appointments'}),
    goal(p3,12,3,{metric:'appointments'})
  ]}});
  const section=h.document.querySelector('#osDashboardGoals');
  const bars=section.querySelectorAll('[role=progressbar]');
  assert.equal(bars.length,2);
  assert.equal(Number(bars[0].getAttribute('aria-valuenow')),75);
  assert.equal(Number(bars[1].getAttribute('aria-valuenow')),35);
  assert.match(section.textContent,/20/);
  assert.equal(section.hidden,false);
});

test('a professional with overlapping active revenue goals contributes only the most recent applicable goal',async t=>{
  const activeDay=today();
  const older=goal(p1,10000,5000,{start_date:activeDay.slice(0,8)+'01',end_date:activeDay});
  const newer=goal(p1,30000,12000,{start_date:activeDay,end_date:activeDay});
  const h=await suite(t,{goals:{items:[older,newer,goal(p2,10000,5000)]}});
  assert.ok(h.document.querySelector('#osDashboardGoals').textContent.includes(money(40000)));
  assert.doesNotMatch(h.document.querySelector('#osDashboardGoals').textContent,/2 profissionais.*3 profissionais/);
});

test('goal 150 percent shows precise achievement and caps only the colored bar at 100 percent',async t=>{
  const h=await suite(t,{goals:{items:[goal(p1,10000,15000)]}});
  const section=h.document.querySelector('#osDashboardGoals');
  assert.match(section.textContent,/150%/);
  assert.equal(section.querySelector('[role=progressbar] span').style.width,'100%');
});

test('the Finance summary can fail without inventing zero or hiding available goals',async t=>{
  const h=await suite(t,{respond:path=>{
    if(path.includes('/finance/summary?'))throw new Error('Serviço temporariamente indisponível');
  },goals:{items:[goal(p1,10000,6000)]}});
  assert.equal(h.document.querySelector('.os-dashboard-finance-values'),null);
  assert.match(h.document.querySelector('.os-dashboard-finance-error').textContent,/Não foi possível consultar/);
  assert.ok(h.document.querySelector('#osDashboardGoals [role=progressbar]'));
  assert.deepEqual(writes(h),[]);
});

test('goal lookup failures do not suppress Finance, and a retry restores both sections',async t=>{
  let tries=0;
  const h=await suite(t,{respond:path=>{
    if(path.includes('/goals?')){tries++;if(tries===1)throw new Error('Meta indisponível');return {items:[goal(p1,10000,6000)]};}
  }});
  assert.ok(h.document.querySelector('.os-dashboard-finance-values'));
  assert.match(h.document.querySelector('#osDashboardGoals').textContent,/Não foi possível consultar as metas/);
  h.click('#osDashboardGoals [data-os-action=dashboard-finance-refresh]');await flush();
  assert.equal(tries,2);
  assert.ok(h.document.querySelector('#osDashboardGoals [role=progressbar]'));
  assert.deepEqual(writes(h),[]);
});

test('Finance button opens the full Finance route, and Goals button opens Performance',async t=>{
  const h=await suite(t,{goals:{items:[goal(p1,10000,6000)]}});
  h.click('[data-os-action=dashboard-finance-full]');await flush();
  assert.equal(h.document.getElementById('page-suite').dataset.osCurrentRoute,'financeiro');
  await h.window.OrionSuite.open('dashboard');await flush();
  h.click('[data-os-action=dashboard-goals-full]');await flush();
  assert.equal(h.document.getElementById('page-suite').dataset.osCurrentRoute,'desempenho');
  assert.deepEqual(writes(h),[]);
});

test('viewer may read financial dashboard figures without saving or changing goals',async t=>{
  const h=await suite(t,{role:'viewer',goals:{items:[goal(p1,10000,6000)]}});
  assert.match(h.document.querySelector('.os-dashboard-finance-values').textContent,/Saldo\/Lucro/);
  assert.ok(h.document.querySelector('#osDashboardGoals [role=progressbar]'));
  assert.equal(h.document.querySelector('#osDashboardGoals [data-os-action=new-goal]'),null);
  assert.deepEqual(writes(h),[]);
});

test('outstanding financial promises cannot update the wrong page after navigation',async t=>{
  const later=deferred();
  const h=await suite(t,{respond:path=>{
    if(path.includes('/finance/summary?'))return later.promise;
  }});
  await h.window.OrionSuite.open('clientes');await flush();
  later.resolve(financial());await flush();
  assert.equal(h.document.getElementById('page-suite').dataset.osCurrentRoute,'clientes');
  assert.equal(h.document.querySelector('.os-dashboard-finance'),null);
  assert.deepEqual(h.errors,[]);
});

test('Dashboard finance blocks use the shared Orion colors with mobile-specific sizing',async()=>{
  const css=await readFile(new URL('../app/static/orion-suite.css',import.meta.url),'utf8');
  assert.match(css,/\.os-dashboard-finance-values\{display:grid;grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
  assert.match(css,/@container dashboard-finance \(max-width:520px\)/);
  assert.match(css,/\.os-dashboard-goals\[hidden\]\{display:none!important\}/);
  assert.match(css,/var\(--os-primary\)/);
  assert.match(css,/var\(--os-border\)/);
});
