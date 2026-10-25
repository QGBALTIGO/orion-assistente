import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {makeSuite, flush, deferred, defaultResponse} from './harness.mjs';

const tz='America/Sao_Paulo';
const today=()=>new Intl.DateTimeFormat('en-CA',{timeZone:tz,year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
const id=index=>'40000000-0000-4000-8000-'+String(index).padStart(12,'0');
const appointment=(index,{status='confirmed',day=today()}={})=>({
  id:id(index),status,professional_id:id(80),professional_name:'Profissional sintético',
  professional_color:'#3976dd',customer_name:'Cliente '+index,
  start_at:day+'T'+String(9+index).padStart(2,'0')+':00:00-03:00',
  end_at:day+'T'+String(9+index).padStart(2,'0')+':40:00-03:00',
  total_cents:5000,items:[{name_snapshot:'Corte e barba'}],
});
const isPreviewRead=path=>path.includes('/appointments?')&&path.includes('paged=true');
const writes=h=>h.calls.filter(x=>x.method!=='GET');
const visibility=node=>!node.classList.contains('hidden');

test('Dashboard reads only the company-local today and reuses the same appointment rows as Agenda',async t=>{
  const records=[appointment(0),appointment(1,{status:'completed'}),appointment(2)];
  const h=await makeSuite({initialRoute:'dashboard',respond:path=>{
    if(isPreviewRead(path))return {items:records,total:records.length,limit:50,offset:0,has_more:false};
    return defaultResponse(path);
  }});
  t.after(()=>h.close());await flush();
  const page=h.document.getElementById('page-suite');
  const calls=h.calls.filter(x=>isPreviewRead(x.path));
  assert.equal(calls.length,1);
  assert.equal(new URL(calls[0].path,'https://orion.test').searchParams.get('day'),today(),'uses business timezone, not the selected day in Agenda');
  assert.equal(new URL(calls[0].path,'https://orion.test').searchParams.get('limit'),'50');
  const section=page.querySelector('section.os-dashboard-today');
  assert.ok(section);
  assert.deepEqual(Array.from(section.querySelectorAll('.os-stat strong'),x=>x.textContent),['0','0','0']);
  const preview=section.querySelector('.os-dashboard-agenda-preview');
  assert.ok(preview,'preview belongs under Hoje no seu negócio');
  assert.match(preview.textContent,/Agendamentos de hoje/);
  assert.match(preview.textContent,/3 agendamentos no dia/);
  assert.deepEqual(Array.from(preview.querySelectorAll('.os-agenda-item'),x=>x.dataset.osAppointment),records.map(x=>x.id));
  assert.ok(preview.querySelector('.os-agenda-name'));
  assert.ok(preview.querySelector('.os-agenda-staff'));
  assert.ok(preview.querySelector('.os-appointment-status'));
  assert.ok(preview.querySelector('[data-os-action=dashboard-agenda-refresh]'));
  assert.equal(preview.querySelector('input[type=date], .os-agenda-week, [data-os-day]'),null);
  assert.equal(preview.querySelector('#osDashboardAgendaContent').hasAttribute('aria-busy'),false);
  assert.deepEqual(writes(h),[]);
  assert.deepEqual(h.errors,[]);
});

test('Dashboard preview is limited to eight verified rows and opens the complete Agenda on today',async t=>{
  const records=Array.from({length:50},(_,i)=>appointment(i%8,{status:i%3===0?'completed':'confirmed'}))
    .map((record,i)=>({...record,id:id(i+10)}));
  const h=await makeSuite({initialRoute:'dashboard',respond:path=>{
    if(isPreviewRead(path))return {items:records,total:87,offset:0,limit:50,has_more:true};
    return defaultResponse(path);
  }});
  t.after(()=>h.close());await flush();
  assert.equal(h.document.querySelectorAll('.os-dashboard-agenda-list .os-agenda-item').length,8);
  assert.match(h.document.getElementById('osDashboardAgendaContent').textContent,/Exibindo 8 de 87/);
  await h.window.OrionSuite.open('agenda'); await flush();
  h.click('[data-os-action=agenda-next]'); await flush();
  // Previously selected dates in the full Agenda must not leak into today's preview.
  await h.window.OrionSuite.open('dashboard'); await flush();
  assert.equal(new URL(h.calls.filter(x=>isPreviewRead(x.path)).at(-1).path,'https://orion.test').searchParams.get('day'),today());
  h.click('[data-os-action=dashboard-agenda-full]');await flush();
  assert.equal(h.document.getElementById('page-suite').dataset.osCurrentRoute,'agenda');
  assert.equal(h.window.location.search.includes('aba=agenda'),true);
  const last=h.calls.filter(x=>x.path.includes('/appointments?')).at(-1);
  assert.equal(new URL(last.path,'https://orion.test').searchParams.get('day'),today());
  assert.deepEqual(writes(h),[]);
});

test('zero appointments renders a compact calm empty state without day controls',async t=>{
  const h=await makeSuite({initialRoute:'dashboard',respond:path=>
    isPreviewRead(path)?{items:[],total:0,limit:50,offset:0,has_more:false}:defaultResponse(path)});
  t.after(()=>h.close());await flush();
  const preview=h.document.querySelector('.os-dashboard-agenda-preview');
  assert.match(preview.textContent,/Nenhum agendamento para hoje/);
  assert.ok(preview.querySelector('[data-os-action=new-appointment]'));
  assert.equal(preview.querySelector('.os-agenda-empty'),null,'avoid the large full-page Agenda empty state');
  assert.equal(preview.querySelector('.os-agenda-item'),null);
  assert.equal(preview.querySelector('input[type=date]'),null);
  assert.deepEqual(writes(h),[]);
});

test('viewer can see the list but cannot create a booking from the empty state',async t=>{
  const h=await makeSuite({initialRoute:'dashboard',role:'viewer',respond:path=>
    isPreviewRead(path)?{items:[],total:0}:defaultResponse(path)});
  t.after(()=>h.close());await flush();
  assert.ok(h.document.querySelector('.os-dashboard-agenda-empty'));
  assert.equal(h.document.querySelector('.os-dashboard-agenda-empty [data-os-action=new-appointment]'),null);
  assert.ok(h.document.querySelector('[data-os-action=dashboard-agenda-full]'));
  assert.deepEqual(writes(h),[]);
});

test('a real backend error is not misrepresented as an empty agenda and the refresh retries safely',async t=>{
  let count=0;
  const h=await makeSuite({initialRoute:'dashboard',respond:path=>{
    if(isPreviewRead(path)){
      if(++count===1)throw new Error('Consulta temporariamente indisponível');
      return {items:[appointment(0)],total:1};
    }
    return defaultResponse(path);
  }});
  t.after(()=>h.close());await flush();
  assert.match(h.document.getElementById('osDashboardAgendaContent').textContent,/Não foi possível carregar/);
  assert.doesNotMatch(h.document.getElementById('osDashboardAgendaContent').textContent,/Nenhum agendamento para hoje/);
  assert.ok(h.document.querySelector('.os-dashboard-agenda-error [data-os-action=dashboard-agenda-refresh]'));
  h.click('.os-dashboard-agenda-error [data-os-action=dashboard-agenda-refresh]');await flush();
  assert.equal(count,2);
  assert.equal(h.document.querySelectorAll('.os-dashboard-agenda-item,.os-dashboard-agenda-list .os-agenda-item').length,1);
  assert.deepEqual(writes(h),[]);
  assert.deepEqual(h.errors,[]);
});

test('late appointment response never overwrites another route or a new company session',async t=>{
  const request=deferred();
  const h=await makeSuite({initialRoute:'dashboard',respond:path=>
    isPreviewRead(path)?request.promise:defaultResponse(path)});
  t.after(()=>h.close());await flush();
  await h.window.OrionSuite.open('clientes');await flush();
  request.resolve({items:[appointment(0)],total:1});
  await flush();
  assert.equal(h.document.getElementById('page-suite').dataset.osCurrentRoute,'clientes');
  assert.equal(h.document.querySelector('.os-dashboard-agenda-preview'),null);
  assert.deepEqual(writes(h),[]);
});

test('corrupt or out-of-day data never shows an inaccurate today-only preview',async t=>{
  const h=await makeSuite({initialRoute:'dashboard',respond:path=>
    isPreviewRead(path)?{items:[appointment(0,{day:'2032-01-01'})],total:1}:defaultResponse(path)});
  t.after(()=>h.close());await flush();
  assert.ok(h.document.querySelector('.os-dashboard-agenda-error'));
  assert.equal(h.document.querySelector('.os-dashboard-agenda-list'),null);
  assert.deepEqual(writes(h),[]);
});

test('today-only preview has Agenda container rules for desktop, tablet and narrow mobile',async()=>{
  const css=await readFile(new URL('../app/static/orion-suite.css',import.meta.url),'utf8');
  assert.match(css,/\.os-dashboard-agenda-preview\{container:agenda \/ inline-size;/);
  assert.match(css,/@container agenda \(max-width:520px\)/);
  assert.match(css,/\.os-dashboard-agenda-controls\{display:grid;grid-template-columns:1fr 1fr/);
  assert.match(css,/\.os-dashboard-agenda-list>/);
});
