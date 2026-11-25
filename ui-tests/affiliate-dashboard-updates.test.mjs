import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM} from 'jsdom';
import {deferred, flush, tenantA} from './harness.mjs';

// Exercise the production mount API. All requests are synthetic reads; this
// suite has no fetch, browser session, publishing action or real polling clock.
const source = await readFile(new URL('../app/static/orion-affiliate-workspace.js', import.meta.url), 'utf8');
const apiRoot = `/api/v1/afiliados/${tenantA}/api/`;
const epoch = Date.parse('2026-11-25T15:00:00Z') / 1000;

function workspace() {
  return {
    company:'Ofertas de teste', niches:[], offer:[], decision:[], product_flag:[],
    template:[{id:'default', name:'Modelo sintético', brand:'Teste', layout:'simple',
      media:'card', style:'plain', text:'{produto}'}], deal_template:'default',
    sorts:{2:'Mais vendidos'},
    connection:[{id:'shopee', status:'connected', version:'synthetic-account', verified:epoch}],
    destination:[{id:'channel', name:'Canal sintético', mode:'telegram', test:true, verified:true}],
    rule:[{id:'rule', name:'Ofertas da tarde', active:true, next_at:epoch+3600,
      timezone:'America/Campo_Grande', destinations:['channel']}],
    jobs:[
      {id:'published', status:'published', changed:epoch-600, destination:'channel',
        doc:{offer:{title:'Oferta publicada'}}},
      {id:'queued', status:'queued', changed:epoch, due:epoch+1800, destination:'channel',
        doc:{offer:{title:'Oferta programada'}}},
    ],
  };
}

function report(period='month', {commission=12400, status='ready'}={}) {
  const dates = period==='today' ? ['2026-11-25'] : ['2026-11-23','2026-11-24','2026-11-25'];
  return {
    period:{key:period, label:period, start_date:dates[0], end_date:dates.at(-1), timezone:'America/Campo_Grande'},
    updated_at:'2026-11-25T15:00:00+00:00',
    sources:{
      sales:{status, scope:'account', updated_at:'2026-11-25T14:30:00+00:00'},
      clicks:{status:'unavailable'},
      publications:{status:'ready', complete:true, scope:'period'},
    },
    summary:{sales_cents:149990, commission_cents:commission, validated_commission_cents:null,
      orders:11, items:14, clicks:null, conversion_rate:null, published:18, pending:1,
      failed:0, active_rules:1, verified_destinations:1},
    operation:{published_snapshot:1, pending_snapshot:1, failed_snapshot:0, sample_size:2, sample_limit:120},
    series:dates.map((date,index)=>({date,
      commission_cents:dates.length===1?commission:[commission-7800,0,7800][index],
      sales_cents:[74995,0,74995][index], orders:[4,0,7][index], items:[7,0,7][index],
      clicks:null, published:[5,6,7][index]})),
    upcoming:[{id:'queued', title:'Oferta programada', status:'queued', destination_id:'channel',
      destination_name:'Canal sintético', due:epoch+1800, changed:epoch}],
    recent:[{id:'published', title:'Oferta publicada', status:'published', destination_id:'channel',
      destination_name:'Canal sintético', changed:epoch-600}],
    destinations:[{id:'channel', name:'Canal sintético', mode:'telegram', verified:true, published:18,
      published_snapshot:1, pending_snapshot:1, failed_snapshot:0, orders:11, commission_cents:12400}],
    products:[{id:'product', title:'Produto de teste', orders:11, commission_cents:12400}],
  };
}

async function mountDashboard(t, {state=()=>workspace(), analytics=period=>report(period), catalog=()=>[]}={}) {
  const dom = new JSDOM('<main id="host"></main>', {
    url:'https://orion.test/panel?aba=dashboard', runScripts:'outside-only', pretendToBeVisual:true,
  });
  const {window} = dom;
  const {document} = window;
  const host = document.querySelector('#host');
  const calls = [], notices = [], errors = [], timers = new Map();
  let timerId = 0, stateRequests = 0, analyticsRequests = 0, catalogRequests = 0, controller;
  window.addEventListener('error', event=>errors.push(event.error?.message || event.message));
  window.HTMLElement.prototype.scrollIntoView = function() {};
  window.CSS = {escape:value=>String(value).replace(/[^a-zA-Z0-9_-]/g,char=>'\\'+char)};
  window.setTimeout = (callback,delay,...args)=>{
    const id = ++timerId;
    timers.set(id,{callback,delay,args});
    return id;
  };
  window.clearTimeout = id=>timers.delete(id);
  window.eval(source.replace('export function mount(', 'function mountAffiliates(')
    + '\nwindow.mountAffiliates=mountAffiliates;');
  controller = window.mountAffiliates({
    host, tenantId:tenantA, view:'dashboard', whatsappAvailable:false,
    icon:name=>`<svg aria-hidden="true" data-test-icon="${name}"></svg>`,
    open:async route=>controller.setView(route),
    showNotice:(message,error)=>notices.push({message,error}),
    request:async (path,method,payload)=>{
      assert.equal(method,'GET','Dashboard update tests must never mutate or publish');
      assert.equal(payload,undefined);
      calls.push(path);
      if (path===apiRoot+'state') return state(++stateRequests);
      if (path.startsWith(apiRoot+'analytics?')) {
        const period = new URL(path,'https://orion.test').searchParams.get('period');
        return analytics(period,++analyticsRequests);
      }
      if (path.startsWith(apiRoot+'catalog?')) {
        return catalog(new URL(path,'https://orion.test').searchParams,++catalogRequests);
      }
      throw new Error(`Unexpected request: ${method} ${path}`);
    },
  });
  t.after(()=>{
    try {
      controller.unmount();
      assert.equal(timers.size,0,'unmount must cancel scheduled analytics checks');
      assert.deepEqual(errors,[]);
    } finally { dom.window.close(); }
  });
  const h = {
    window, document, host, calls, notices, timers, controller,
    element(selector) {
      const node = host.querySelector(selector);
      assert.ok(node,`Missing element: ${selector}`);
      return node;
    },
    click(selector) { this.element(selector).click(); },
    submit(selector) {
      const form = this.element(selector);
      form.dispatchEvent(new window.SubmitEvent('submit',{
        bubbles:true, cancelable:true, submitter:form.querySelector('button[type="submit"]'),
      }));
    },
    hidden(value) {
      Object.defineProperty(document,'hidden',{configurable:true,value});
      document.dispatchEvent(new window.Event('visibilitychange'));
    },
    async poll() {
      assert.equal(timers.size,1,'only one analytics polling timer may be active');
      const [id,timer] = timers.entries().next().value;
      timers.delete(id);
      timer.callback(...timer.args);
      await flush();
    },
  };
  await flush();
  h.element('.af-dashboard');
  return h;
}

function remember(h, selectors) {
  return selectors.map(selector=>({selector,node:h.element(selector)}));
}

function unchanged(h, references) {
  for (const {selector,node} of references) {
    assert.equal(h.element(selector),node,`${selector} must keep its DOM identity`);
  }
}

const kpi = (h,key)=>h.element(`[data-dashboard-kpi="${key}"]`).textContent;
const baseSections = ['.af-dashboard','.af-dashboard-heading','.af-dashboard-performance',
  '.af-dashboard-operation','.af-dashboard-publications'];
const otherContent = ['.af-dashboard-heading-actions a','.af-dashboard-operation-row',
  '.af-dashboard-destination-list li','.af-dashboard-job-list li'];

test('métrica atualiza o gráfico sem reconstruir controles, indicadores ou outras seções', async t=>{
  const h = await mountDashboard(t);
  const refs = remember(h,[...baseSections,...otherContent,
    '[data-dashboard-kpi="commission_cents"]','[data-dashboard-period="month"]',
    '[data-dashboard-metric="orders"]','[data-dashboard-publications="recent"]']);
  const metric = h.element('[data-dashboard-metric="orders"]');
  const requests = h.calls.length;
  metric.focus();
  metric.click();
  await flush();
  unchanged(h,refs);
  assert.equal(h.document.activeElement,metric);
  assert.equal(metric.getAttribute('aria-pressed'),'true');
  assert.equal(h.element('[data-dashboard-point-value]').textContent,'7');
  assert.ok(h.element('.af-dashboard-chart-bar'));
  assert.equal(h.calls.length,requests,'local metric changes need no network request');
});

test('aba de publicações troca a lista preservando gráfico, foco e demais seções', async t=>{
  const h = await mountDashboard(t);
  const refs = remember(h,[...baseSections,'.af-dashboard-chart-plot svg',
    '[data-dashboard-point="0"]','[data-dashboard-point-value]',
    '[data-dashboard-kpi="commission_cents"]','.af-dashboard-operation-row',
    '[data-dashboard-publications="recent"]']);
  const tab = h.element('[data-dashboard-publications="recent"]');
  const requests = h.calls.length;
  tab.focus();
  tab.click();
  await flush();
  unchanged(h,refs);
  assert.equal(h.document.activeElement,tab);
  assert.equal(tab.getAttribute('aria-pressed'),'true');
  assert.match(h.element('.af-dashboard-job-list').textContent,/Oferta publicada/);
  assert.doesNotMatch(h.element('.af-dashboard-job-list').textContent,/Oferta programada/);
  assert.equal(h.calls.length,requests);
});

test('atualização lenta mantém o botão ocupado e o dia focado enquanto muda os valores no lugar', async t=>{
  const later = deferred();
  let analyticsRequests = 0;
  const h = await mountDashboard(t,{analytics:period=>++analyticsRequests===1?report(period):later.promise});
  const refs = remember(h,[...baseSections,...otherContent,
    '[data-act="refresh"]','[data-dashboard-point="0"]','[data-dashboard-kpi="commission_cents"]']);
  const refresh = h.element('[data-act="refresh"]');
  refresh.focus();
  refresh.click();
  await flush();
  unchanged(h,refs);
  assert.equal(refresh.disabled,true,'one refresh stays busy through state and analytics');
  const requestCount = h.calls.length;
  refresh.click();
  await flush();
  assert.equal(h.calls.length,requestCount,'a pending refresh cannot be started twice');
  const point = h.element('[data-dashboard-point="0"]');
  point.focus();
  assert.match(h.element('[data-dashboard-point-value]').textContent,/46,00/);
  later.resolve(report('month',{commission:17400}));
  await flush();
  unchanged(h,refs);
  assert.equal(refresh.disabled,false);
  assert.equal(h.document.activeElement,point,'a response must not steal the selected chart point');
  assert.equal(point.getAttribute('aria-pressed'),'true');
  assert.match(h.element('[data-dashboard-point-value]').textContent,/96,00/);
  assert.match(kpi(h,'commission_cents'),/174,00/);
});

test('respostas de períodos antigos não alteram valores, foco ou controles do período mais recente', async t=>{
  const stale = deferred();
  let sevenDayRequests = 0;
  const h = await mountDashboard(t,{analytics:period=>period==='7d' && ++sevenDayRequests===1
    ?stale.promise:report(period,{commission:period==='today'?19900:period==='7d'?29400:12400})});
  const refs = remember(h,[...baseSections,...otherContent,
    '[data-dashboard-period="today"]','[data-dashboard-period="7d"]']);
  h.click('[data-dashboard-period="7d"]');
  await flush();
  const today = h.element('[data-dashboard-period="today"]');
  today.focus();
  today.click();
  await flush();
  unchanged(h,refs);
  assert.equal(today.getAttribute('aria-pressed'),'true');
  assert.match(kpi(h,'commission_cents'),/199,00/);
  const sevenDays = h.element('[data-dashboard-period="7d"]');
  sevenDays.focus();
  sevenDays.click();
  await flush();
  assert.match(kpi(h,'commission_cents'),/294,00/);
  stale.resolve(report('7d',{commission:999999}));
  await flush();
  unchanged(h,refs);
  assert.equal(h.document.activeElement,sevenDays);
  assert.equal(sevenDays.getAttribute('aria-pressed'),'true');
  assert.match(kpi(h,'commission_cents'),/294,00/);
  assert.doesNotMatch(h.element('.af-dashboard').textContent,/9\.999,99/);
});

test('resposta de state iniciada no Dashboard não reconstrói o formulário após navegar', async t=>{
  const later = deferred();
  let stateRequests = 0;
  const h = await mountDashboard(t,{state:()=>++stateRequests===1?workspace():later.promise});
  h.click('[data-act="refresh"]');
  await flush();
  h.controller.setView('ofertas');
  const input = h.element('input[name="url"]');
  const form = input.form;
  input.value = 'https://shopee.com.br/product/draft-not-submitted';
  input.focus();
  input.setSelectionRange(8,14);
  const analyticsRequests = h.calls.filter(path=>path.includes('/analytics?')).length;
  later.resolve(workspace());
  await flush();
  assert.equal(h.element('input[name="url"]'),input);
  assert.equal(input.form,form);
  assert.equal(input.value,'https://shopee.com.br/product/draft-not-submitted');
  assert.equal(h.document.activeElement,input);
  assert.equal(input.selectionStart,8);
  assert.equal(input.selectionEnd,14);
  assert.equal(h.host.querySelector('.af-dashboard'),null);
  assert.equal(h.calls.filter(path=>path.includes('/analytics?')).length,analyticsRequests);
  assert.equal(h.timers.size,0);
});

test('resposta automática altera números e encerra a consulta mantendo o ponto focado e as outras seções', async t=>{
  const later = deferred();
  const h = await mountDashboard(t,{analytics:(period,count)=>count===1
    ?report(period,{status:'syncing'}):later.promise});
  const refs = remember(h,[...baseSections,...otherContent,
    '[data-dashboard-kpi="commission_cents"]','[data-dashboard-point="0"]']);
  const point = h.element('[data-dashboard-point="0"]');
  point.focus();
  await h.poll();
  unchanged(h,refs);
  later.resolve(report('month',{commission:17400}));
  await flush();
  unchanged(h,refs);
  assert.equal(h.document.activeElement,point);
  assert.equal(point.getAttribute('aria-pressed'),'true');
  assert.match(h.element('[data-dashboard-point-value]').textContent,/96,00/);
  assert.match(kpi(h,'commission_cents'),/174,00/);
  assert.equal(h.element('[data-act="refresh"]').getAttribute('aria-busy'),'false');
  assert.equal(h.timers.size,0,'ready reports stop automatic checks');
});

test('janela móvel de sete dias mantém a data selecionada e usa o último dia quando ela sai do período', async t=>{
  function sevenDays(startDay,base,status) {
    const data = report('7d',{status});
    data.series = Array.from({length:7},(_,index)=>({
      date:new Date(Date.UTC(2026,10,startDay+index)).toISOString().slice(0,10),
      commission_cents:base+index*100, sales_cents:10000, orders:1, published:1,
    }));
    data.period.start_date = data.series[0].date;
    data.period.end_date = data.series.at(-1).date;
    data.summary.commission_cents = data.series.reduce((sum,row)=>sum+row.commission_cents,0);
    return data;
  }
  const windows = [sevenDays(19,1000,'syncing'),sevenDays(20,2000,'syncing'),sevenDays(25,3000,'ready')];
  let checks = 0;
  const h = await mountDashboard(t,{analytics:period=>period==='7d'?windows[checks++]:report(period)});
  h.click('[data-dashboard-period="7d"]');
  await flush();
  const point = h.element('[data-dashboard-date="2026-11-24"]');
  point.focus();
  const selectedDateText = h.element('[data-dashboard-point-date]').textContent;
  assert.equal(point.dataset.dashboardPoint,'5');
  assert.equal(point.getAttribute('aria-pressed'),'true');
  assert.match(h.element('[data-dashboard-point-value]').textContent,/15,00/);

  await h.poll();
  assert.equal(h.element('[data-dashboard-date="2026-11-24"]'),point);
  assert.equal(point.dataset.dashboardPoint,'4','its index changes when the rolling window advances');
  assert.equal(h.document.activeElement,point);
  assert.equal(point.getAttribute('aria-pressed'),'true');
  assert.equal(point.tabIndex,0);
  assert.equal(h.element('[data-dashboard-point][aria-pressed="true"]'),point);
  assert.equal(h.element('[data-dashboard-point-date]').textContent,selectedDateText);
  assert.match(h.element('[data-dashboard-point-value]').textContent,/24,00/);

  await h.poll();
  const fallback = h.element('[data-dashboard-date="2026-12-01"]');
  assert.equal(point.isConnected,false);
  assert.equal(h.document.activeElement,fallback);
  assert.equal(h.element('[data-dashboard-point][aria-pressed="true"]'),fallback);
  assert.equal(fallback.tabIndex,0);
  assert.match(h.element('[data-dashboard-point-value]').textContent,/36,00/);
  assert.equal(checks,3);
  assert.equal(h.timers.size,0);
});

test('consulta automática mantém os elementos e não volta a alterar uma rota ou aba inativa', async t=>{
  const later = deferred();
  let analyticsRequests = 0;
  const h = await mountDashboard(t,{analytics:period=>++analyticsRequests===1
    ?report(period,{status:'syncing'}):later.promise});
  const refs = remember(h,[...baseSections,...otherContent,'[data-dashboard-point="0"]']);
  const point = h.element('[data-dashboard-point="0"]');
  point.focus();
  assert.equal(h.timers.size,1);
  h.hidden(true);
  assert.equal(h.timers.size,0,'hidden documents do not poll');
  h.hidden(false);
  assert.equal(h.timers.size,1);
  await h.poll();
  unchanged(h,refs);
  assert.equal(h.document.activeElement,point);
  assert.equal(analyticsRequests,2);
  h.controller.setView('ofertas');
  const input = h.element('input[name="url"]');
  input.value = 'https://shopee.com.br/product/another-draft';
  input.focus();
  later.resolve(report('month',{commission:17400,status:'syncing'}));
  await flush();
  assert.equal(h.element('input[name="url"]'),input);
  assert.equal(input.value,'https://shopee.com.br/product/another-draft');
  assert.equal(h.document.activeElement,input);
  assert.equal(h.timers.size,0,'a departed Dashboard cannot reschedule its polling');
  h.hidden(true);
  h.hidden(false);
  await flush();
  assert.equal(analyticsRequests,2);
  assert.equal(h.timers.size,0);
});

test('desmontagem cancela polling e ignora a resposta automática que ainda estava em voo', async t=>{
  const later = deferred();
  const h = await mountDashboard(t,{analytics:(period,count)=>count===1
    ?report(period,{status:'syncing'}):later.promise});
  await h.poll();
  h.controller.unmount();
  assert.equal(h.host.childElementCount,0);
  assert.equal(h.timers.size,0);
  later.resolve(report('month',{status:'syncing'}));
  await flush();
  h.hidden(false);
  assert.equal(h.host.childElementCount,0,'late data must not revive an unmounted view');
  assert.equal(h.timers.size,0);
});

test('catálogo mantém a busca mais nova e descarta resultado ou erro de uma rota abandonada', async t=>{
  const first = deferred(), afterNavigation = deferred(), failedAfterNavigation = deferred();
  const h = await mountDashboard(t,{catalog:(params,count)=>{
    if (count===1) return first.promise;
    if (count===2) {
      assert.equal(params.get('keyword'),'busca mais recente');
      return [{identity:'new-product',title:'Resultado mais novo',price:29.90,
        catalog_url:'https://shopee.com.br/product/new-synthetic'}];
    }
    return count===3?afterNavigation.promise:failedAfterNavigation.promise;
  }});
  h.controller.setView('ofertas');
  h.click('[data-offer-tab="catalog"]');
  h.element('[data-form="search"] input[name="keyword"]').value = 'primeira busca';
  h.submit('[data-form="search"]');
  h.element('[data-form="search"] input[name="keyword"]').value = 'busca mais recente';
  h.submit('[data-form="search"]');
  await flush();
  const currentForm = h.element('[data-form="search"]');
  assert.match(h.element('#offer-catalog').textContent,/Resultado mais novo/);
  first.resolve([{identity:'old-product',title:'Resultado ultrapassado',price:19.90}]);
  await flush();
  assert.equal(h.element('[data-form="search"]'),currentForm);
  assert.equal(currentForm.elements.keyword.value,'busca mais recente');
  assert.match(h.element('#offer-catalog').textContent,/Resultado mais novo/);
  assert.doesNotMatch(h.element('#offer-catalog').textContent,/Resultado ultrapassado/);

  h.submit('[data-form="search"]');
  h.controller.setView('templates');
  let draft = h.element('[data-form="template"] textarea');
  draft.value = 'Legenda em edição que deve continuar aqui';
  draft.focus();
  afterNavigation.resolve([]);
  await flush();
  assert.equal(h.element('[data-form="template"] textarea'),draft);
  assert.equal(draft.value,'Legenda em edição que deve continuar aqui');
  assert.equal(h.document.activeElement,draft);

  h.controller.setView('ofertas');
  h.submit('[data-form="search"]');
  h.controller.setView('templates');
  draft = h.element('[data-form="template"] textarea');
  draft.value = 'Outro rascunho preservado';
  draft.focus();
  const noticeCount = h.notices.length;
  failedAfterNavigation.reject(new Error('Falha de uma busca já abandonada'));
  await flush();
  assert.equal(h.element('[data-form="template"] textarea'),draft);
  assert.equal(draft.value,'Outro rascunho preservado');
  assert.equal(h.document.activeElement,draft);
  assert.equal(h.notices.length,noticeCount,'a stale search failure must not notify the new view');
});
