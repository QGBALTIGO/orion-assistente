import test from 'node:test';
import assert from 'node:assert/strict';
import {makeSuite,flush,defaultResponse,planState,deferred} from './harness.mjs';

const people=[{id:'p1',name:'Peixoto',active:true},{id:'p2',name:'Guilherme',active:false}];
const daily=[
  {date:'2026-10-31',professional_id:'p1',completed:2,revenue_cents:10000,commission_cents:3000},
  {date:'2026-11-02',professional_id:'p1',completed:1,revenue_cents:6000,commission_cents:1800},
  {date:'2026-11-02',professional_id:'p2',completed:1,revenue_cents:4000,commission_cents:1200},
  {date:'2026-11-09',professional_id:'p2',completed:1,revenue_cents:5000,commission_cents:1500},
  {date:'2026-11-15',professional_id:'p1',completed:1,revenue_cents:8000,commission_cents:2400}
];
const change=(h,selector,value)=>{const el=h.document.querySelector(selector);assert.ok(el,selector);el.value=value;el.dispatchEvent(new h.window.Event('change',{bubbles:true}));};
const columns=h=>[...h.document.querySelectorAll('[data-os-performance-point]')];
const metric=(h,index)=>h.document.querySelectorAll('.os-performance-metrics .os-metric>strong')[index].textContent;
const centsText=value=>new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'}).format(value/100);
const clock=window=>{const RealDate=window.Date;window.Date=class extends RealDate{constructor(...args){super(...(args.length?args:['2026-11-15T15:00:00Z']));}static now(){return new RealDate('2026-11-15T15:00:00Z').getTime();}};};
async function suite(t,{plan='elite',respond=()=>undefined}={}){
  const h=await makeSuite({initialRoute:'desempenho',permissions:planState(plan),configureWindow:clock,respond:(path,method,data)=>{
    const result=respond(path,method,data);if(result!==undefined)return result;
    if(method!=='GET')return {};
    const url=new URL(path,'https://orion.test'),from=url.searchParams.get('start_date'),to=url.searchParams.get('end_date'),items=daily.filter(r=>r.date>=from&&r.date<=to);
    if(path.includes('/performance/daily?'))return {items};
    if(path.includes('/performance?'))return {items:people.map(p=>({...p,cancelled:1,no_show:0,completed:items.filter(r=>r.professional_id===p.id).reduce((n,r)=>n+r.completed,0),revenue_cents:items.filter(r=>r.professional_id===p.id).reduce((n,r)=>n+r.revenue_cents,0)}))};
    if(path.endsWith('/professionals'))return people;
    if(path.includes('/finance/commissions?'))return {items:people.map(p=>({...p,earned_cents:items.filter(r=>r.professional_id===p.id).reduce((n,r)=>n+r.commission_cents,0),advances_cents:1000,payable_cents:5000}))};
    if(path.includes('/goals?'))return {items:[],templates:[]};
    return defaultResponse(path);
  }});t.after(()=>h.close());await flush();return h;
}
async function period(h,from,to){
  h.click('[data-os-action=custom-period]');h.fill('#osPeriodStart',from);h.fill('#osPeriodEnd',to);h.submit('#osPeriodForm');await flush();
}

test('daily chart preserves calendar gaps and sums every professional exactly once',async t=>{
  const h=await suite(t);await period(h,'2026-10-31','2026-11-10');
  assert.equal(columns(h).length,11);
  assert.match(columns(h)[1].getAttribute('aria-label'),/01 de nov.*R\$.*0,00.*0 atendimentos/);
  assert.match(columns(h)[2].getAttribute('aria-label'),/02 de nov.*R\$.*100,00.*2 atendimentos/);
  assert.equal(metric(h,0),centsText(25000));assert.equal(metric(h,1),'5');assert.equal(metric(h,2),centsText(5000));
  assert.equal(h.document.querySelector('.os-performance-details tfoot td:nth-of-type(2)').textContent,centsText(25000));
  assert.deepEqual(h.errors,[]);
});

test('calendar weeks are clipped to the selected period and show both boundary dates',async t=>{
  const h=await suite(t);await period(h,'2026-10-31','2026-11-10');
  h.click('[data-os-performance-group=week]');
  assert.equal(columns(h).length,3);
  const labels=columns(h).map(el=>el.getAttribute('aria-label'));
  assert.match(labels[0],/31 de out.*01 de nov.*Semana parcial.*100,00/);
  assert.match(labels[1],/02 de nov.*08 de nov.*100,00/);assert.doesNotMatch(labels[1],/parcial/);
  assert.match(labels[2],/09 de nov.*10 de nov.*Semana parcial.*50,00/);
  assert.equal(metric(h,0),centsText(25000));
  assert.equal(h.document.activeElement.dataset.osPerformanceGroup,'week');
});

test('empty weeks remain visible, including ranges crossing the year',async t=>{
  const h=await suite(t);await period(h,'2026-12-30','2027-01-12');h.click('[data-os-performance-group=week]');
  assert.equal(columns(h).length,3);
  assert.match(columns(h)[0].getAttribute('aria-label'),/30 de dez.*03 de jan/);
  assert.match(columns(h)[2].getAttribute('aria-label'),/11 de jan.*12 de jan/);
  assert.ok(h.document.querySelector('.os-performance-no-results'));
  assert.equal(h.document.querySelectorAll('.os-performance-bar[data-empty=true]').length,3);
  assert.notEqual(h.document.querySelector('.os-performance-y-axis span').textContent,h.document.querySelector('.os-performance-y-axis span:last-child').textContent);
});

test('Essencial never opens advanced performance when changing grouping or indicator',async t=>{
  const h=await suite(t,{plan:'essencial'});
  for(const group of ['week','day','week'])h.click('[data-os-performance-group='+group+']');
  change(h,'#osPerformanceMetric','completed');
  assert.equal(h.document.querySelector('[data-os-action=new-goal]'),null);
  assert.equal(h.document.querySelector('.os-performance-money'),null);
  assert.equal(h.document.querySelector('#osPerformanceMetric option[value=commission_cents]'),null);
  assert.doesNotMatch(h.document.querySelector('#osPageBody').textContent,/Comissão|Comissões/);
  assert.equal(h.calls.some(c=>/\/goals|\/finance\/commissions|\/finance\/advances/.test(c.path)),false);
  assert.match(h.document.querySelector('.os-performance-chart-scroll').getAttribute('aria-label'),/Atendimentos/);
});

test('touch and keyboard selection reveal exact values without rebuilding the chart',async t=>{
  const h=await suite(t);await period(h,'2026-10-31','2026-11-10');
  const graph=h.document.querySelector('.os-performance-plot'),requests=h.calls.length;
  columns(h)[1].click();assert.equal(columns(h)[1].getAttribute('aria-pressed'),'true');
  assert.match(h.document.querySelector('#osPerformanceSelection').textContent,/01 de nov/);
  assert.equal(h.document.querySelectorAll('[data-os-performance-point][tabindex="0"]').length,1);
  columns(h)[1].dispatchEvent(new h.window.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true,cancelable:true}));
  assert.equal(h.document.activeElement,columns(h)[2]);assert.match(h.document.querySelector('#osPerformanceSelection').textContent,/100,00/);
  columns(h)[2].dispatchEvent(new h.window.KeyboardEvent('keydown',{key:'End',bubbles:true,cancelable:true}));
  assert.equal(h.document.activeElement,columns(h).at(-1));
  assert.equal(h.document.querySelector('.os-performance-plot'),graph);assert.equal(h.calls.length,requests);
});

test('professional selection updates the cards, graph, details and team list together',async t=>{
  const h=await suite(t);await period(h,'2026-10-31','2026-11-10');
  const requests=h.calls.length;change(h,'#osPerformanceProfessional','p1');
  assert.equal(metric(h,0),centsText(16000));assert.equal(metric(h,1),'3');assert.equal(metric(h,2),centsText(5333));
  assert.match(columns(h)[2].getAttribute('aria-label'),/60,00.*1 atendimentos/);
  assert.match(columns(h)[9].getAttribute('aria-label'),/0,00.*0 atendimentos/);
  assert.equal(h.document.querySelectorAll('[data-os-performance-history]').length,1);
  assert.equal(h.document.querySelector('[data-os-performance-history]').dataset.osPerformanceHistory,'p1');
  assert.equal(h.document.querySelector('.os-performance-details tfoot td:nth-of-type(2)').textContent,centsText(16000));
  assert.equal(h.calls.length,requests);change(h,'#osPerformanceProfessional','');assert.equal(metric(h,0),centsText(25000));
});

test('future days are distinguished from zero results and excluded from daily averages',async t=>{
  const h=await suite(t);
  assert.equal(columns(h).length,30);assert.equal(h.document.querySelectorAll('[data-os-performance-point][data-future=true]').length,15);
  columns(h)[19].click();assert.match(h.document.querySelector('#osPerformanceSelection').textContent,/Período futuro/);
  assert.equal([...h.document.querySelectorAll('.os-performance-selected-values strong')].every(el=>el.textContent==='—'),true);
  assert.equal(h.document.querySelectorAll('.os-performance-insights strong')[1].textContent,centsText(Math.round(23000/15)));
  assert.equal(h.document.querySelector('[aria-pressed=true][data-os-performance-point]').dataset.osPerformancePoint,'19');
});

test('custom performance dates survive a separate Finance report period',async t=>{
  const h=await suite(t);await period(h,'2026-10-31','2026-11-10');
  h.click('[data-os-route=financeiro]');await flush();
  change(h,'#osFinanceMonth','2026-09');await flush();
  h.click('[data-os-route=desempenho]');await flush();
  const url=new URL(h.calls.filter(c=>c.path.includes('/performance?')).at(-1).path,'https://orion.test');
  assert.equal(url.searchParams.get('start_date'),'2026-10-31');assert.equal(url.searchParams.get('end_date'),'2026-11-10');
  assert.equal(h.document.querySelector('#osPerformancePeriod').value,'custom');assert.equal(columns(h).length,11);
});

test('period validation accepts 366 days and rejects longer ranges before requesting a report',async t=>{
  const h=await suite(t);await period(h,'2024-01-01','2024-12-31');assert.equal(columns(h).length,366);
  const requests=h.calls.length;await period(h,'2024-01-01','2025-01-01');
  assert.equal(h.calls.length,requests);assert.ok(h.document.querySelector('#osPeriodForm'));
  assert.match(h.document.body.textContent,/até 366 dias/);
});

test('canceling the custom picker preserves the applied period label and results',async t=>{
  const h=await suite(t),count=columns(h).length,requests=h.calls.length;
  change(h,'#osPerformancePeriod','custom');await flush();
  assert.ok(h.document.querySelector('#osPeriodForm'));
  h.click('#osDialog [data-os-action=close-dialog]');await flush();
  assert.equal(h.document.querySelector('#osPerformancePeriod').value,'month');
  assert.equal(columns(h).length,count);assert.equal(h.calls.length,requests);
});

test('a slower previous period cannot replace a refreshed chart',async t=>{
  const pending=deferred();let delay=false;
  const h=await suite(t,{respond:path=>delay&&path.includes('/performance/daily?')&&path.includes('2026-10-31')?pending.promise:undefined});
  delay=true;await period(h,'2026-10-31','2026-11-10');
  h.click('[data-os-performance-period=today]');await flush();
  assert.equal(columns(h).length,1);assert.match(columns(h)[0].getAttribute('aria-label'),/15 de nov.*80,00/);
  pending.resolve({items:daily});await flush();assert.equal(columns(h).length,1);
  assert.equal(metric(h,0),centsText(8000));assert.deepEqual(h.errors,[]);
});
