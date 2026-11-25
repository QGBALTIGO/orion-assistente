import test from 'node:test';
import assert from 'node:assert/strict';
import {makePanel, jsonResponse} from './panel-harness.mjs';
import {deferred, flush, planState, tenantA} from './harness.mjs';
import {entry} from './onboarding-harness.mjs';

// Complete panel + affiliate controller. Every response is synthetic, and this
// suite rejects all mutations before they could reach an external service.
const apiRoot = `/api/v1/afiliados/${tenantA}/api/`;
const business = {id:tenantA, name:'Ofertas de teste', role:'owner', status:'active',
  slug:'ofertas-de-teste', timezone:'America/Campo_Grande', workspace_profile:'affiliate'};
const permissions = planState('none', {workspace_profile:'affiliate', layout_modes:['classic','app']});
const periodLabels = {today:'Hoje', '7d':'7 dias', '30d':'30 dias', month:'Este mês'};
const epoch = Date.parse('2026-11-25T15:00:00Z') / 1000;

function workspace({empty=false}={}) {
  const destination = {id:'channel-synthetic', name:'Canal de ofertas de teste', mode:'telegram',
    test:true, verified:true};
  return {company:business.name, niches:[], offer:[], template:[], decision:[], product_flag:[],
    sorts:{2:'Mais vendidos'},
    connection:[{id:'shopee', status:'connected', account:'Conta sintética', verified:epoch}],
    destination:empty ? [] : [destination],
    rule:empty ? [] : [{id:'rule-synthetic', name:'Ofertas da tarde', active:true,
      next_at:epoch+3600, timezone:business.timezone, destinations:[destination.id]}],
    jobs:empty ? [] : [
      {id:'published-synthetic', status:'published', changed:epoch-600, destination:destination.id,
        doc:{offer:{title:'Oferta publicada de teste'}}},
      {id:'queued-synthetic', status:'queued', changed:epoch, due:epoch+1800, destination:destination.id,
        doc:{offer:{title:'Oferta programada de teste'}}},
    ]};
}

function report(period='month', {ready=false, empty=false, commission=12400, validated=null}={}) {
  const dates = period==='today' ? ['2026-11-25'] : ['2026-11-23','2026-11-24','2026-11-25'];
  const data = {
    period:{key:period, label:periodLabels[period], start_date:dates[0], end_date:dates.at(-1), timezone:business.timezone},
    updated_at:'2026-11-25T15:00:00+00:00',
    sources:{
      sales:{status:ready?'ready':'unavailable', updated_at:ready?'2026-11-25T14:30:00+00:00':null,
        reason:ready?null:'O relatório de vendas ainda não foi integrado.'},
      clicks:{status:'unavailable', updated_at:null, reason:'Consulte o relatório de cliques da Shopee.'},
      publications:{status:ready?'ready':'partial', complete:ready, scope:ready?'period':'latest_120',
        updated_at:'2026-11-25T15:00:00+00:00'},
    },
    summary:{sales_cents:ready?149990:null, commission_cents:ready?commission:null,
      validated_commission_cents:ready?validated:null, orders:ready?11:null, items:ready?14:null,
      clicks:null, conversion_rate:null, published:ready?18:null,
      pending:empty?0:1, failed:0, active_rules:empty?0:1, verified_destinations:empty?0:1},
    operation:{published_snapshot:empty?0:1, pending_snapshot:empty?0:1, failed_snapshot:0,
      sample_size:empty?0:2, sample_limit:120},
    series:dates.map((date,index)=>({date,
      commission_cents:ready?(dates.length===1?commission:index===1?null:index===0?4600:7800):null,
      sales_cents:ready?(index===1?null:74995):null,
      validated_commission_cents:null, orders:ready?(index===1?null:index===0?4:7):null,
      items:ready?(index===1?null:7):null, clicks:null, published:ready?[5,6,7][index]:null})),
    upcoming:empty?[]:[{id:'queued-synthetic', title:'Oferta programada de teste', status:'queued',
      destination_id:'channel-synthetic', destination_name:'Canal de ofertas de teste', due:epoch+1800, changed:epoch}],
    recent:empty?[]:[{id:'published-synthetic', title:'Oferta publicada de teste', status:'published',
      destination_id:'channel-synthetic', destination_name:'Canal de ofertas de teste', changed:epoch-600}],
    destinations:empty?[]:[{id:'channel-synthetic', name:'Canal de ofertas de teste', mode:'telegram',
      verified:true, published_snapshot:1, pending_snapshot:1, failed_snapshot:0,
      orders:ready?11:null, commission_cents:ready?commission:null}],
    products:ready?[{id:'product-synthetic', title:'Produto de teste', orders:11, commission_cents:commission}]:[],
  };
  return data;
}

async function dashboard(t, {analytics=period=>report(period), state=workspace()}={}) {
  const h = await makePanel({role:'member', initialRoute:'dashboard', permissions,
    respond(path, method) {
      assert.equal(method, 'GET', 'Dashboard interactions must not publish or mutate data');
      if (path==='/api/v1/account/businesses') return [business];
      if (path==='/api/v1/onboarding/entry') return entry({kind:'affiliate', create_allowed:false, businesses:[business]});
      if (path==='/api/v1/account/business-identity') return {name:business.name};
      if (path===apiRoot+'state') return structuredClone(state);
      if (path.startsWith(apiRoot+'analytics?')) {
        const period = new URL(path, 'https://orion.test').searchParams.get('period');
        assert.ok(Object.hasOwn(periodLabels, period));
        return analytics(period);
      }
    },
  });
  t.after(()=>{
    try {
      assert.deepEqual(h.errors, []);
      assert.ok(h.calls.every(call=>call.method==='GET'), 'Only mocked read requests are allowed');
    } finally { h.close(); }
  });
  await flush();
  h.element('.af-dashboard');
  return h;
}

const kpi = (h,key)=>h.element(`[data-dashboard-kpi="${key}"]`).textContent;
const chosenPeriod = h=>h.element('[data-dashboard-period][aria-pressed="true"]').dataset.dashboardPeriod;
function key(h, selector, value) {
  h.element(selector).dispatchEvent(new h.window.KeyboardEvent('keydown', {key:value, bubbles:true, cancelable:true}));
}

test('Afiliados informa indisponibilidade financeira sem inventar zeros ou histórico diário', async t=>{
  const h = await dashboard(t);
  assert.equal(h.element('.af-dashboard h1').textContent, 'Dashboard');
  assert.equal(chosenPeriod(h), 'month');
  assert.match(h.element('.af-dashboard-revenue').textContent, /relatório de vendas ainda não integrado/i);
  assert.equal(h.document.querySelector('.af-dashboard-revenue-value'), null);
  assert.equal(h.document.querySelector('[data-dashboard-kpi="sales_cents"]'), null);
  assert.doesNotMatch(h.element('.af-dashboard').textContent, /R\$\s*0,00/);
  assert.match(h.element('.af-dashboard-kpis').textContent, /Últimas 120 publicações/);
  assert.match(h.element('.af-dashboard-kpis').textContent, /Publicadas\s*1/);
  assert.match(h.element('.af-dashboard-kpis').textContent, /Na fila\s*1/);
  assert.equal(h.document.querySelectorAll('[data-dashboard-point]').length, 0);
  h.click('[data-dashboard-metric="commission_cents"]');
  assert.match(h.element('.af-dashboard-performance').textContent, /direto da fonte/i);
  assert.equal(h.document.querySelector('.af-dashboard-chart-total'), null);
  for (const link of h.document.querySelectorAll('.af-dashboard a[target="_blank"]')) {
    assert.equal(link.href, 'https://affiliate.shopee.com.br/');
    assert.ok(link.relList.contains('noopener'));
  }
});

test('Afiliados distingue valor vendido, comissão estimada e comissão validada explicitamente informada', async t=>{
  const h = await dashboard(t, {analytics:period=>report(period, {ready:true, validated:8400})});
  assert.match(kpi(h,'commission_cents'), /124,00/);
  assert.match(kpi(h,'sales_cents'), /1\.499,90/);
  assert.match(kpi(h,'validated_commission_cents'), /84,00/);
  assert.match(kpi(h,'orders'), /11/);
  assert.match(kpi(h,'published'), /18/);
  assert.match(kpi(h,'clicks'), /Na Shopee/);
  assert.doesNotMatch(kpi(h,'clicks'), /\b0\b/);
  assert.match(h.element('.af-dashboard-revenue-validation').textContent, /não representa valor recebido/);
  assert.match(h.element('.af-dashboard-footnote').textContent, /receita de afiliado é a comissão/);
  assert.equal(h.document.querySelectorAll('[data-dashboard-point]').length, 3);
});

test('Afiliados mantém comissão validada desconhecida e representa zero apenas quando a fonte o informa', async t=>{
  const data = report('month', {ready:true, commission:0});
  data.summary.sales_cents=0;
  data.summary.orders=0;
  data.summary.items=0;
  data.series.forEach(row=>{ row.commission_cents=0; row.sales_cents=0; row.orders=0; row.items=0; });
  data.products=[];
  const h = await dashboard(t, {analytics:()=>data});
  assert.match(kpi(h,'commission_cents'), /0,00/);
  assert.match(kpi(h,'sales_cents'), /0,00/);
  assert.match(kpi(h,'validated_commission_cents'), /—/);
  assert.doesNotMatch(kpi(h,'validated_commission_cents'), /0,00/);
  assert.match(h.element('.af-dashboard-revenue-validation').textContent, /Ainda não informada/);
  assert.match(h.element('.af-dashboard-performance').textContent, /Nenhum resultado registrado/);
});

test('Afiliados troca os quatro períodos e ignora uma resposta que chegou depois de outra seleção', async t=>{
  const delayed = deferred();
  const values = {month:12400, today:1100, '7d':7700, '30d':30300};
  const h = await dashboard(t, {analytics:period=>period==='7d'?delayed.promise:report(period, {ready:true, commission:values[period]})});
  h.click('[data-dashboard-period="7d"]');
  await flush();
  assert.equal(chosenPeriod(h), '7d');
  h.click('[data-dashboard-period="today"]');
  await flush();
  assert.equal(chosenPeriod(h), 'today');
  assert.match(kpi(h,'commission_cents'), /11,00/);
  delayed.resolve(report('7d', {ready:true, commission:7700}));
  await flush();
  assert.equal(chosenPeriod(h), 'today');
  assert.match(kpi(h,'commission_cents'), /11,00/);
  assert.doesNotMatch(kpi(h,'commission_cents'), /77,00/);
  h.click('[data-dashboard-period="30d"]');
  await flush();
  assert.equal(chosenPeriod(h), '30d');
  assert.match(kpi(h,'commission_cents'), /303,00/);
  h.click('[data-dashboard-period="month"]');
  await flush();
  assert.equal(chosenPeriod(h), 'month');
  assert.match(kpi(h,'commission_cents'), /124,00/);
  const requested = new Set(h.calls.filter(call=>call.path.startsWith(apiRoot+'analytics?'))
    .map(call=>new URL(call.path,'https://orion.test').searchParams.get('period')));
  assert.deepEqual(requested, new Set(['month','7d','today','30d']));
});

test('Afiliados preserva o último relatório do mesmo período na falha e não o reaproveita em outro período', async t=>{
  let unavailable=false;
  const h = await dashboard(t, {analytics:period=>unavailable
    ? jsonResponse({detail:'Relatório temporariamente indisponível'},503)
    : report(period, {ready:true})});
  unavailable=true;
  h.click('.af-dashboard [data-act="refresh"]');
  await flush();
  assert.match(kpi(h,'commission_cents'), /124,00/);
  assert.match(h.element('.af-dashboard-alert[role="status"]').textContent, /Mantivemos os últimos dados confirmados deste período/);
  assert.equal(h.element('.af-dashboard-overview').getAttribute('aria-busy'), 'false');
  h.click('[data-dashboard-period="7d"]');
  await flush();
  assert.equal(chosenPeriod(h), '7d');
  assert.equal(h.document.querySelector('.af-dashboard-revenue-value'), null);
  assert.doesNotMatch(h.element('.af-dashboard').textContent, /124,00/);
  h.click('[data-dashboard-period="month"]');
  await flush();
  assert.match(kpi(h,'commission_cents'), /124,00/);
  assert.ok(h.element('.af-dashboard-alert[role="status"]'));
});

test('Afiliados permite consultar o gráfico com teclado, mantém dias sem dados e alterna métricas e publicações', async t=>{
  const h = await dashboard(t, {analytics:period=>report(period, {ready:true})});
  const active = ()=>'[data-dashboard-point][tabindex="0"]';
  h.element(active()).focus();
  assert.equal(h.document.activeElement.dataset.dashboardPoint, '2');
  assert.match(h.element('[data-dashboard-point-value]').textContent, /78,00/);
  key(h, active(), 'ArrowLeft');
  assert.equal(h.document.activeElement.dataset.dashboardPoint, '1');
  assert.equal(h.element('[data-dashboard-point-value]').textContent, '—');
  assert.match(h.element('[data-dashboard-point="1"]').getAttribute('aria-label'), /sem informação/);
  key(h, active(), 'Home');
  assert.equal(h.document.activeElement.dataset.dashboardPoint, '0');
  assert.match(h.element('[data-dashboard-point-value]').textContent, /46,00/);
  assert.equal(h.element('[data-dashboard-step="-1"]').disabled, true);
  key(h, active(), 'End');
  assert.equal(h.document.activeElement.dataset.dashboardPoint, '2');
  assert.equal(h.element('[data-dashboard-step="1"]').disabled, true);
  h.click('[data-dashboard-step="-1"]');
  assert.equal(h.element('[data-dashboard-point-value]').textContent, '—');
  h.click('[data-dashboard-point="0"]');
  assert.match(h.element('[data-dashboard-point-value]').textContent, /46,00/);
  h.click('[data-dashboard-metric="orders"]');
  assert.equal(h.element('[data-dashboard-metric="orders"]').getAttribute('aria-pressed'), 'true');
  assert.equal(h.element('[data-dashboard-point-value]').textContent, '7');
  h.click('[data-dashboard-metric="published"]');
  assert.equal(h.element('[data-dashboard-point-value]').textContent, '7');
  assert.equal(h.document.querySelectorAll('[data-dashboard-point][tabindex="0"]').length, 1);
  assert.match(h.element('.af-dashboard-job-list').textContent, /Oferta programada de teste/);
  h.click('[data-dashboard-publications="recent"]');
  assert.match(h.element('.af-dashboard-job-list').textContent, /Oferta publicada de teste/);
  h.click('[data-dashboard-publications="upcoming"]');
  assert.match(h.element('.af-dashboard-job-list').textContent, /Oferta programada de teste/);
});

test('Afiliados vazio oferece o próximo passo e navega para preparar a primeira oferta sem publicar', async t=>{
  const h = await dashboard(t, {state:workspace({empty:true}), analytics:period=>report(period, {empty:true})});
  assert.match(h.element('.af-dashboard-revenue').textContent, /Preparar primeira oferta/);
  assert.match(h.element('.af-dashboard-performance').textContent, /Sua primeira publicação começa aqui/);
  assert.match(h.element('.af-dashboard-rankings').textContent, /Adicionar destino/);
  h.click('.af-dashboard-revenue a[href="#ofertas"]');
  await flush();
  assert.equal(new URL(h.window.location.href).searchParams.get('aba'), 'afiliados-ofertas');
  assert.ok(h.element('[data-form="offer"]'));
  assert.equal(h.document.querySelector('.af-dashboard'), null);
});
