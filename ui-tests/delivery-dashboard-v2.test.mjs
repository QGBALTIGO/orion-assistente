import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {makeSuite,defaultResponse,planState,flush,deferred} from './harness.mjs';

const sources=await Promise.all(['orion-delivery-orders.js','orion-delivery-dashboard.js'].map(file=>
  readFile(new URL('../app/static/'+file,import.meta.url),'utf8')));
const permissions=planState('none',{delivery_catalog_preview:true});
const day='2026-11-28',orderId='a0000000-0000-4000-8000-000000000001';
const daily={date:day,today:day,metrics:{orders:3,fulfilled:1,open_deliveries:1,sales_cents:1750,paid_cents:1250,credit_cents:500},
  operations:{in_route:1,awaiting_dispatch:0,average_ticket_cents:1750},
  recent:[{id:orderId,created_at:day+'T14:00:00Z',customer_name:'<Cliente & teste>',status:'fulfilled',mode:'pickup',total_cents:1750}],
  deliveries:[],credit_customers:[],low_stock:[]};
const totals={orders:3,fulfilled:1,cancelled:1,sales_cents:1750,income_cents:1250,refunded_cents:0,average_ticket_cents:1750};
const basePerf={start:'2026-11-22',end:day,previous_start:'2026-11-15',previous_end:'2026-11-21',granularity:'day',
  current:totals,previous:{...totals,sales_cents:0,income_cents:0,orders:0},
  series:Array.from({length:7},(_,i)=>({date:'2026-11-'+(22+i),orders:i===6?3:0,sales_cents:i===6?1750:0,income_cents:i===6?1250:0})),
  order_status:{confirmed:0,dispatched:1,fulfilled:1,cancelled:1,other:0},
  hourly_orders:Array.from({length:24},(_,hour)=>({hour,orders:hour===14?3:0})),
  top_products:[{id:'variant',product:'<Água>',variant:'20 L',quantity:1,revenue_cents:1250}]};
const single={...basePerf,start:day,end:day,granularity:'hour',
  series:Array.from({length:24},(_,hour)=>({date:day,hour,orders:hour===14?3:0,sales_cents:hour===14?1750:0,income_cents:hour===14?1250:0}))};
const load=(t,respond,role='owner')=>makeSuite({initialRoute:'dashboard-delivery',permissions,role,configureWindow:w=>sources.forEach(s=>w.eval(s)),respond:path=>{
  if(path.includes('/delivery/overview/performance?'))return respond(path);
  if(path.includes('/delivery/overview?'))return structuredClone(daily);
  return defaultResponse(path);
}}).then(h=>{t.after(()=>h.close());return h;});
const perfCalls=h=>h.calls.filter(c=>c.path.includes('/delivery/overview/performance?'));
test('dashboard renders real KPIs, ranking, and status without duplicate navigation',async t=>{
  const h=await load(t,()=>structuredClone(basePerf));await flush();
  assert.equal(h.document.querySelectorAll('.os-delivery-dashboard-metric').length,6);
  assert.equal(h.document.querySelectorAll('.os-delivery-dashboard-dock').length,0);
  assert.equal(h.document.querySelectorAll('[data-os-action="delivery-dashboard-new"]').length,1);
  assert.match(h.document.querySelector('.odd-ring').textContent,/3Pedidos/);
  assert.equal(h.document.querySelectorAll('.odd-ring-part').length,3);
  assert.match(h.document.querySelector('.odd-peak-head').textContent,/14h/);
  assert.equal(h.document.querySelector('.odd-rank-product strong').textContent,'<Água>');
  assert.equal(h.document.querySelector('.odd-rank-product strong').children.length,0);
  assert.equal(h.document.querySelector('.os-delivery-dashboard-row-copy strong').textContent,'<Cliente & teste>');
  assert.match(h.document.querySelector('.odd-period-stats').textContent,/Sem base no período anterior/);
  assert.equal(perfCalls(h).length,1);
  const heading=h.document.querySelector('.odd-heading h1');
  h.click('[data-dashboard-metric="orders"]');
  assert.match(h.document.querySelector('[data-chart-readout]').textContent,/3/);
  assert.equal(perfCalls(h).length,1);
  const slider=h.document.querySelector('[data-dashboard-scrub]');slider.value='0';
  slider.dispatchEvent(new h.window.Event('input',{bubbles:true}));
  assert.match(h.document.querySelector('[data-chart-readout]').textContent,/· 0$/);
  assert.equal(heading,h.document.querySelector('.odd-heading h1'));
  assert.equal(h.errors.length,0);
});
test('period shortcuts refresh only analytics; late response cannot overwrite the newest period',async t=>{
  const old=deferred();const h=await load(t,path=>{
    const period=new URL(path,'https://orion.test').searchParams.get('period');
    return period==='7d'?old.promise:structuredClone(single);
  });
  await flush();
  const heading=h.document.querySelector('.odd-heading h1'),dayMetrics=h.document.querySelector('.os-delivery-dashboard-metrics');
  h.click('[data-dashboard-period="today"]');await flush();
  assert.equal(h.document.querySelectorAll('.odd-plot-bar').length,24);
  assert.equal(h.document.querySelector('[data-dashboard-period="today"]').getAttribute('aria-pressed'),'true');
  old.resolve(structuredClone(basePerf));await flush();
  assert.equal(h.document.querySelectorAll('.odd-plot-bar').length,24);
  assert.equal(heading,h.document.querySelector('.odd-heading h1'));
  assert.equal(dayMetrics,h.document.querySelector('.os-delivery-dashboard-metrics'));
  assert.equal(h.errors.length,0);
});
test('no activity produces an honest zero chart and neutral status ring; viewer cannot create orders',async t=>{
  const zero={...basePerf,current:{...totals,orders:0,sales_cents:0,income_cents:0},previous:{...totals,orders:0,sales_cents:0,income_cents:0},
    series:basePerf.series.map(row=>({...row,orders:0,sales_cents:0,income_cents:0})),
    order_status:{confirmed:0,dispatched:0,fulfilled:0,cancelled:0,other:0},
    hourly_orders:basePerf.hourly_orders.map(row=>({...row,orders:0})),top_products:[]};
  const h=await load(t,()=>zero,'viewer');await flush();
  assert.equal(h.document.querySelectorAll('.odd-ring-part').length,0);
  assert.match(h.document.querySelector('.odd-no-movement').textContent,/Nenhum movimento/);
  assert.match(h.document.querySelector('.odd-analytics-grid').textContent,/Nenhum produto vendido/);
  assert.equal(h.document.querySelectorAll('[data-os-action="delivery-dashboard-new"]').length,0);
  assert.equal(h.errors.length,0);
});
