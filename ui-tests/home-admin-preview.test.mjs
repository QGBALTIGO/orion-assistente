import test from 'node:test';
import assert from 'node:assert/strict';
import {makeSuite,flush,deferred,defaultResponse,tenantA} from './harness.mjs';
const today=()=>new Intl.DateTimeFormat('en-CA',{timeZone:'America/Sao_Paulo',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
const admin={role:'admin',name:'Kayky Teste',account_id:'90000000-0000-4000-8000-000000000001'};
function respond(path){
 const u=new URL(path,'https://orion.test'),period={start_date:u.searchParams.get('start_date'),end_date:u.searchParams.get('end_date'),timezone:'America/Sao_Paulo'};
 if(u.pathname.endsWith('/management/dashboard'))return {period,summary:{appointments:12,completed:6,confirmed:4,cancelled:1,no_show:1,customers:48,completed_revenue_cents:43000,average_ticket_cents:7166},professionals:[{id:'80000000-0000-4000-8000-000000000001',name:'Profissional <sintético>',active:true,appointments:12,completed:6,revenue_cents:43000}]};
 if(u.pathname.endsWith('/performance/daily'))return {period,items:[{date:period.end_date,professional_id:'80000000-0000-4000-8000-000000000001',revenue_cents:43000,completed:6}]};
 if(u.pathname.endsWith('/goals'))return {items:[]};
 if(u.pathname.endsWith('/appointments'))return {items:[],total:0,limit:50,offset:0,has_more:false};
 return defaultResponse(path);
}
const setup=opts=>makeSuite({initialRoute:'dashboard',bridge:{user:admin},respond,...opts});
const writes=h=>h.calls.filter(c=>c.method!=='GET');
async function liveSetup(opts={}){
 const timers=new Map();let next=100,visibility='visible';
 const h=await setup({...opts,configureWindow(w){
  Object.defineProperty(w.document,'visibilityState',{get:()=>visibility,configurable:true});
  w.setInterval=(callback,ms)=>{const id=next++;timers.set(id,{callback,ms});return id;};
  w.clearInterval=id=>timers.delete(id);opts.configureWindow?.(w);
 }});
 return Object.assign(h,{timers,setVisibility(value){visibility=value;},async tick(){for(const timer of [...timers.values()])if(timer.ms===5000)timer.callback();await flush();}});
}
const reads=h=>h.calls.filter(c=>c.path.includes('/management/dashboard?')).length;
const bookingData=path=>path.endsWith('/services')?[{id:'service-test',name:'Corte + Barba sintético',active:true,price_cents:6000,price_mode:'fixed',duration_min:60}]:path.endsWith('/professionals')?[{id:'professional-test',name:'Profissional sintético',active:true,accepts_appointments:true}]:path.endsWith('/professional_services')?[{professional_id:'professional-test',service_id:'service-test'}]:respond(path);


test('platform administrator sees the live-data Home pilot inside the original shell',async t=>{
 const h=await setup();t.after(()=>h.close());await flush();
 assert.ok(h.document.querySelector('.os-home-preview'));
 assert.match(h.document.querySelector('.os-home-heading h1').textContent,/Kayky/);
 assert.equal(h.document.querySelector('#osNav [data-os-route=dashboard]').textContent,'Início');
 assert.match(h.document.querySelector('#osHomeRevenue').textContent,/430,00/);
 assert.deepEqual([...h.document.querySelectorAll('.os-home-metric>strong')].map(n=>n.textContent),['6 / 12','48','4','R$\u00a071,67']);
 assert.match(h.document.querySelector('.os-home-metrics').textContent,/carteira ativa da empresa/);
 assert.ok(h.document.querySelector('.os-home-team-row strong').textContent.includes('<sintético>'));
 assert.equal(h.document.querySelector('.os-home-team-row sintético'),null);
 assert.equal(h.document.querySelectorAll('#osHomeTrend svg rect').length,7);
 assert.equal(h.document.querySelector('.os-home-metrics').hasAttribute('aria-busy'),false);
 assert.equal(h.document.querySelector('.mobile-nav'),null,'no prototype navigation is introduced into the real shell');
 assert.deepEqual(writes(h),[]);assert.deepEqual(h.errors,[]);
});

for(const role of ['member','support'])test(role+' cannot enable Home through URL, storage or a forged period control',async t=>{
 const h=await setup({bridge:{user:{role,name:'Conta sintética'}},url:'https://orion.test/painel?home_preview=1&admin=true',configureWindow:w=>w.localStorage.setItem('orion-home-preview','true')});t.after(()=>h.close());await flush();
 assert.equal(h.document.querySelector('.os-home-preview'),null);
 assert.ok(h.document.querySelector('.os-dashboard-today'));
 h.document.querySelector('#osPageBody').insertAdjacentHTML('beforeend','<button data-os-action="home-period" data-os-period="week">Forjado</button>');
 const count=h.calls.filter(c=>c.path.includes('/management/dashboard')).length;
 h.click('[data-os-action=home-period]');await flush();
 assert.equal(h.calls.filter(c=>c.path.includes('/management/dashboard')).length,count);
 assert.equal(h.document.querySelector('.os-home-preview'),null);
 assert.equal(h.calls.some(c=>c.path.includes('/performance/daily?')),false);
 assert.deepEqual(writes(h),[]);assert.deepEqual(h.errors,[]);
});

test('maintenance impersonation keeps the business Home',async t=>{
 const h=await setup({bridge:{user:{...admin,maintenance:{active:true}}}});t.after(()=>h.close());await flush();
 assert.equal(h.document.querySelector('.os-home-preview'),null);assert.ok(h.document.querySelector('.os-dashboard-today'));
});

test('today/yesterday/week update real request dates without touching Agenda or creating data',async t=>{
 const h=await setup();t.after(()=>h.close());await flush();
 for(const period of ['yesterday','week','today']){
  h.click('[data-os-period='+period+']');await flush();
  const u=new URL(h.calls.filter(c=>c.path.includes('/management/dashboard?')).at(-1).path,'https://orion.test');
  const expected=new Date(today()+'T12:00:00Z');expected.setUTCDate(expected.getUTCDate()+(period==='week'?-6:period==='yesterday'?-1:0));
  assert.equal(u.searchParams.get('start_date'),expected.toISOString().slice(0,10));
  assert.equal(u.searchParams.get('end_date'),period==='yesterday'?expected.toISOString().slice(0,10):today());
  assert.equal(h.document.querySelector('[data-os-period='+period+']').getAttribute('aria-pressed'),'true');
  const agenda=new URL(h.calls.filter(c=>c.path.includes('/appointments?')).at(-1).path,'https://orion.test');
  assert.equal(agenda.searchParams.get('day'),today(),'agenda preview explicitly stays on today');
 }
 assert.deepEqual(writes(h),[]);assert.deepEqual(h.errors,[]);
});

test('missing revenue is an error, never a fake zero; chart failure does not erase confirmed totals',async t=>{
 let failSummary=true;
 const h=await setup({respond:path=>{const d=respond(path);if(path.includes('/management/dashboard')&&failSummary)delete d.summary.completed_revenue_cents;if(path.includes('/performance/daily?'))throw Error('trend offline');return d;}});t.after(()=>h.close());await flush();
 assert.match(h.document.querySelector('#osHomeRevenue').textContent,/Não foi possível/);
 assert.doesNotMatch(h.document.querySelector('#osHomeRevenue').textContent,/R\$/);
 assert.match(h.document.querySelector('#osHomeMetrics').textContent,/Indicadores indisponíveis/);
 failSummary=false;h.window.dispatchEvent(new h.window.Event('focus'));await flush();
 assert.match(h.document.querySelector('#osHomeRevenue').textContent,/430,00/);
 assert.match(h.document.querySelector('#osHomeTrend').textContent,/Evolução indisponível/);
 assert.equal(h.document.querySelectorAll('.os-home-metric').length,4);assert.deepEqual(h.errors,[]);
});

test('empty confirmed results show honest zeroes and team setup',async t=>{
 const h=await setup({respond:path=>{const d=respond(path);if(path.includes('/management/dashboard')){Object.keys(d.summary).forEach(k=>d.summary[k]=0);d.professionals=[];}if(path.includes('/performance/daily?'))d.items=[];return d;}});t.after(()=>h.close());await flush();
 assert.match(h.document.querySelector('#osHomeRevenue').textContent,/0,00/);
 assert.match(h.document.querySelector('#osHomeTeam').textContent,/Cadastrar profissional/);
 assert.match(h.document.querySelector('#osHomeTrend').textContent,/Sem conclusões/);assert.deepEqual(h.errors,[]);
});

test('a late old-period response cannot overwrite the newly selected period or another route',async t=>{
 const first=deferred();let count=0;
 const h=await setup({respond:path=>{if(path.includes('/management/dashboard')&&++count===1)return first.promise;return respond(path);}});t.after(()=>h.close());await flush();
 h.click('[data-os-period=yesterday]');await flush();
 assert.match(h.document.querySelector('#osHomeRevenueTitle').textContent,/ontem/);
 const d=respond('/api/v1/tenants/'+tenantA+'/management/dashboard?start_date='+today()+'&end_date='+today());d.summary.completed_revenue_cents=999999;
 first.resolve(d);await flush();
 assert.doesNotMatch(h.document.querySelector('#osHomeRevenue').textContent,/9\.999/);
 h.click('[data-os-action=dashboard-agenda-full]');await flush();
 assert.equal(h.document.querySelector('.os-home-preview'),null);
 assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'agenda');assert.deepEqual(h.errors,[]);
});

test('large safe integer totals keep exact cent formatting and all labels remain escaped',async t=>{
 const h=await setup({respond:path=>{const d=respond(path);if(path.includes('/management/dashboard')){d.summary.completed_revenue_cents=Number.MAX_SAFE_INTEGER;d.summary.completed=1;}return d;},bridge:{user:{...admin,name:'<img src=x onerror=alert(1)>'}}});t.after(()=>h.close());await flush();
 assert.match(h.document.querySelector('.os-home-revenue-value').textContent,/,91$/);
 assert.equal(h.document.querySelector('.os-home-heading img'),null);
 assert.ok(h.document.querySelector('.os-home-long-value'));assert.deepEqual(h.errors,[]);
});


test('Home has one booking action and no manual refresh or duplicate empty-state CTA',async t=>{
 const h=await liveSetup();t.after(()=>h.close());await flush();
 assert.equal(h.document.querySelectorAll('.os-home-preview [data-os-action=new-appointment]').length,1);
 assert.equal(h.document.querySelectorAll('.os-home-quick').length,2);
 assert.equal([...h.document.querySelectorAll('.os-home-preview button')].some(b=>/Atualizar|Tentar novamente|Agendar cliente/.test(b.textContent)),false);
 assert.equal(h.document.querySelector('.os-home-agenda-empty button'),null);
 assert.match(h.document.querySelector('.os-home-agenda-empty').textContent,/automaticamente/);
 assert.equal([...h.timers.values()].filter(t=>t.ms===5000).length,1);
 assert.deepEqual(writes(h),[]);
});

test('background synchronization updates changed values without remounting Home or unchanged content',async t=>{
 let revenue=43000;
 const h=await liveSetup({respond:path=>{const d=respond(path);if(path.includes('/management/dashboard?'))d.summary.completed_revenue_cents=revenue;return d;}});t.after(()=>h.close());await flush();
 const page=h.document.querySelector('.os-home-preview'),metrics=h.document.querySelector('.os-home-metric'),agenda=h.document.querySelector('.os-home-agenda-empty');metrics.focus();
 await h.tick();assert.equal(reads(h),2);
 assert.equal(h.document.querySelector('.os-home-preview'),page);
 assert.equal(h.document.querySelector('.os-home-metric'),metrics);assert.equal(h.document.activeElement,metrics);
 assert.equal(h.document.querySelector('.os-home-agenda-empty'),agenda);
 revenue=54000;await h.tick();
 assert.match(h.document.querySelector('#osHomeRevenue').textContent,/540,00/);
 assert.equal(h.document.querySelector('.os-home-preview'),page);
 assert.equal(h.document.activeElement.dataset.osRoute,'agenda');assert.equal(h.document.querySelector('.os-home-agenda-empty'),agenda);
 assert.deepEqual(h.errors,[]);
});

test('concurrent timer ticks are deduplicated and all four sections commit together',async t=>{
 const pending=deferred();let hold=false;
 const h=await liveSetup({respond:path=>{const d=respond(path);if(hold&&path.includes('/management/dashboard?'))d.summary.completed_revenue_cents=78000;if(hold&&path.includes('/appointments?'))return pending.promise;return d;}});t.after(()=>h.close());await flush();
 hold=true;await h.tick();await h.tick();await h.tick();assert.equal(reads(h),2);
 assert.match(h.document.querySelector('#osHomeRevenue').textContent,/430,00/);
 pending.resolve(respond('/appointments'));await flush();
 assert.match(h.document.querySelector('#osHomeRevenue').textContent,/780,00/);assert.deepEqual(h.errors,[]);
});

test('temporary outage retains confirmed values and automatically recovers on return to the app',async t=>{
 let offline=false;
 const h=await liveSetup({respond:path=>{if(offline&&/dashboard\?|daily\?|goals\?|appointments\?/.test(path))throw Error('network offline');return respond(path);}});t.after(()=>h.close());await flush();
 const value=h.document.querySelector('.os-home-revenue-value');offline=true;await h.tick();
 assert.equal(h.document.querySelector('.os-home-revenue-value'),value);
 assert.match(h.document.querySelector('#osHomeSync').textContent,/reconectando/);
 offline=false;h.window.dispatchEvent(new h.window.Event('focus'));await flush();
 assert.equal(h.document.querySelector('#osHomeSync').textContent,'');assert.equal(h.document.querySelector('.os-home-revenue-value'),value);assert.equal(reads(h),3);
 assert.deepEqual(h.errors,[]);
});

test('hidden pages pause synchronization and leaving Home or destroying clears its timer',async t=>{
 const h=await liveSetup();t.after(()=>h.close());await flush();h.setVisibility('hidden');await h.tick();assert.equal(reads(h),1);
 h.setVisibility('visible');h.document.dispatchEvent(new h.window.Event('visibilitychange'));await flush();assert.equal(reads(h),2);
 h.click('[data-os-action=dashboard-agenda-full]');await flush();assert.equal([...h.timers.values()].some(t=>t.ms===5000),false);
 h.click('#osNav [data-os-route=dashboard]');await flush();assert.equal([...h.timers.values()].filter(t=>t.ms===5000).length,1);
 h.window.OrionSuite.destroy();assert.equal(h.timers.size,0);assert.deepEqual(h.errors,[]);
});

test('an open booking form keeps its fields, focus and scroll while Home synchronizes',async t=>{
 const h=await liveSetup({respond:bookingData});t.after(()=>h.close());await flush();
 h.click('.os-home-preview [data-os-action=new-appointment]');await flush();
 const dialog=h.document.querySelector('#osDialog');assert.ok(dialog.classList.contains('os-home-booking'));
 assert.equal(dialog.querySelector('.os-home-booking-header h2').textContent,'Novo agendamento');
 assert.ok(dialog.querySelector('.os-home-booking-header .os-close'));
 const form=dialog.querySelector('form'),field=h.fill('#osBookingCustomer','Cliente em preenchimento'),scroll=dialog.querySelector('.os-home-booking-scroll');
 assert.equal(form.querySelector('.os-form-actions').parentElement,form);
 assert.equal(form.querySelector('fieldset').parentElement,scroll);
 assert.equal(field.placeholder,'Buscar nome ou telefone');field.focus();scroll.scrollTop=220;
 await h.tick();assert.equal(field.value,'Cliente em preenchimento');assert.equal(h.document.activeElement,field);assert.equal(scroll.scrollTop,220);
 assert.equal(dialog.querySelector('form'),form);assert.equal(reads(h),2);assert.deepEqual(writes(h),[]);assert.deepEqual(h.errors,[]);
});

test('the booking layout remains unchanged for ordinary company accounts',async t=>{
 const h=await liveSetup({bridge:{user:{role:'member',name:'Conta comum'}},respond:bookingData});t.after(()=>h.close());await flush();
 h.click('[data-os-action=new-appointment]');await flush();
 assert.ok(h.document.querySelector('#osDialog.os-booking-dialog'));
 assert.equal(h.document.querySelector('#osDialog.os-home-booking'),null);
 assert.equal([...h.timers.values()].some(t=>t.ms===5000),false);
 assert.deepEqual(writes(h),[]);assert.deepEqual(h.errors,[]);
});

test('late Home reads cannot overwrite another company after switching tenant',async t=>{
 const pending=deferred();let wait=false;
 const h=await liveSetup({respond:path=>wait&&path.includes('/management/dashboard?')?pending.promise:respond(path)});t.after(()=>h.close());await flush();
 wait=true;await h.tick();h.window.OrionSuite.destroy();
 pending.resolve(respond('/management/dashboard?start_date='+today()+'&end_date='+today()));await flush();
 assert.equal(h.document.querySelector('.os-home-preview'),null);assert.equal(h.timers.size,0);assert.deepEqual(h.errors,[]);
});

test('saving a customer immediately queues a fresh read after an in-flight sync without losing Home',async t=>{
 const pending=deferred();let hold=false,saved=false;
 const h=await liveSetup({respond:(path,method)=>{
  if(method==='POST'&&path.endsWith('/management/customers')){saved=true;return {id:'new-synthetic-customer'};}
  const d=respond(path);if(path.includes('/management/dashboard?')){if(saved)d.summary.customers=49;if(hold&&!saved)return pending.promise;}return d;
 }});t.after(()=>h.close());await flush();const home=h.document.querySelector('.os-home-preview');
 hold=true;await h.tick();h.click('[data-os-action=new-customer]');await flush();h.fill('[data-os-form=customer] [name=name]','Cliente sintético');h.submit('[data-os-form=customer]');await flush();
 assert.equal(saved,true);assert.equal(h.document.querySelector('#osDialog').open,false);assert.equal(reads(h),2);
 pending.resolve(respond('/management/dashboard?start_date='+today()+'&end_date='+today()));await flush();
 assert.equal(reads(h),3);assert.equal(h.document.querySelector('.os-home-preview'),home);assert.equal(h.document.querySelectorAll('.os-home-metric>strong')[1].textContent,'49');assert.equal(writes(h).length,1);assert.deepEqual(h.errors,[]);
});
