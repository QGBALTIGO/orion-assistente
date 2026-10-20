import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { JSDOM } from 'jsdom';
import { makeSuite, flush, deferred, defaultResponse, tenantB } from './harness.mjs';
import { makePanel } from './panel-harness.mjs';

// HTTP is replaced in each harness. No bridge, account, model or WhatsApp
// connection is created by these regressions.
const writes=h=>h.calls.filter(call=>call.method!=='GET');
const professionalId='60000000-0000-4000-8000-000000000006';
const campaignId='70000000-0000-4000-8000-000000000007';
const professional={id:professionalId,name:'Profissional sintético',active:true,accepts_appointments:true};

test('legacy QR page confirms the link without promising that existing automation is disabled',async t=>{
  const html=await readFile(new URL('../app/static/qr_connect.html',import.meta.url),'utf8');
  const dom=new JSDOM(html,{url:'https://orion.test/qr',runScripts:'outside-only'});
  t.after(()=>dom.window.close());const {window}=dom,calls=[];
  window.setInterval=()=>1;window.clearInterval=()=>{};
  window.fetch=async(path,options)=>{calls.push({path,options});assert.equal(path,'/api/v1/qr/image');assert.equal(options.method,'GET');return new Response(JSON.stringify({status:'connected'}));};
  window.eval([...html.matchAll(/<script>([\s\S]*?)<\/script>/g)][0][1]);
  window.document.querySelector('#token').value='synthetic-only-qr-support-token-000000';
  window.document.querySelector('#start').click();await flush();
  const message=window.document.querySelector('#message').textContent;
  assert.match(message,/Vínculo confirmado/);assert.match(message,/configuração existente do canal/);
  assert.doesNotMatch(window.document.body.textContent,/Nenhuma mensagem é enviada automaticamente|não lê ou envia mensagens/);
  assert.equal(calls.length,1);assert.equal(window.document.querySelector('#token').value,'');
  assert.equal(window.document.querySelector('#image').hasAttribute('src'),false);
});

test('tracking coverage renders the stated MS timezone even on a device whose default is UTC',async t=>{
  const trackingSince='2026-10-20T02:30:00Z';
  const h=await makePanel({deviceStatus:'connected',respond(path){
    if(path==='/api/v1/qr/status')return {status:'connected',automaticRepliesEnabled:true,
      messageStats:{available:true,trackingSince,today:1,last7Days:1,last30Days:1,total:1}};
  }});t.after(()=>h.close());
  const original=h.window.Date.prototype.toLocaleString;
  // A real Intl formatter with a deliberately different default device zone.
  h.window.Date.prototype.toLocaleString=function(locale,options){return original.call(this,locale,{timeZone:'UTC',...options});};
  await h.tickClocks(4500);
  const expected=new Date(trackingSince).toLocaleString('pt-BR',{timeZone:'America/Campo_Grande'});
  assert.match(h.element('#statsCoverage').textContent,new RegExp(expected.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')));
  assert.match(h.element('#statsCoverage').textContent,/horário do MS/);
  assert.match(h.element('#chatMode').textContent,/Respostas automáticas de IA ativadas/);
  assert.deepEqual(writes(h),[]);
});

async function marketing(respond){
  const h=await makeSuite({initialRoute:'marketing',respond(path,method,payload){
    const result=respond?.(path,method,payload);return result===undefined?defaultResponse(path):result;
  }});await flush();return h;
}

test('Meta queue distinguishes an uncertain prior attempt from a confirmed failure',async t=>{
  const h=await marketing(path=>path.includes('/marketing/queue?')?{items:[
    {status:'dead',delivery_state:'verification_required',recipient_suffix:'9001',attempts:1,
      created_at:'2026-10-20T12:00:00Z',error:'Entrega em verificação; não reenviar automaticamente'},
    {status:'dead',recipient_suffix:'9002',attempts:1,created_at:'2026-10-20T12:00:00Z',error:'Falha validada sintética'},
  ],summary:{dead:2}}:undefined);t.after(()=>h.close());
  h.click('[data-os-marketing-tab=queue]');await flush();
  const rows=[...h.document.querySelectorAll('#osPageBody tbody tr')];assert.equal(rows.length,2);
  assert.match(rows[0].textContent,/Entrega em verificação/);assert.equal(rows[0].querySelector('.os-badge').classList.contains('os-warning'),true);
  assert.equal(rows[0].querySelector('.os-badge').classList.contains('os-danger'),false);
  assert.match(rows[1].textContent,/Com falha/);assert.equal(rows[1].querySelector('.os-badge').classList.contains('os-danger'),true);
  assert.match(h.document.querySelector('#osPageBody').textContent,/recibos das campanhas.*Envios revisados/);
  assert.doesNotMatch(h.document.querySelector('#osPageBody').textContent,/campanhas salvas.*continuam em rascunho/);
  assert.deepEqual(writes(h),[]);assert.equal(h.errors.length,0);
});

for(const [status,label] of [['approved','Autorizada'],['paused','Pausada'],['archived','Arquivada']]){
  test(`saved campaign ${status} is displayed by its actual status and remains read-only`,async t=>{
    const h=await marketing(path=>path.includes('/management/marketing?')?{items:[{id:campaignId,name:'Campanha sintética',
      message:'Texto para revisão',status,audience:'all_active'}],total:1}:undefined);t.after(()=>h.close());
    h.click('[data-os-marketing-tab=campaigns]');await flush();h.click(`[data-os-campaign="${campaignId}"]`);
    const dialog=h.document.querySelector('#osDialog');assert.match(dialog.textContent,/Registro da campanha/);
    assert.match(dialog.textContent,new RegExp(label));assert.equal(dialog.querySelector('form'),null);
    if(status!=='archived')assert.doesNotMatch(dialog.textContent,/arquivad[ao]/i);
    assert.deepEqual(writes(h),[]);assert.equal(h.errors.length,0);
  });
}

async function hoursFixture(respond){
  const h=await makeSuite({initialRoute:'funcionarios',respond(path,method,payload){
    if(path.endsWith('/professionals'))return [professional];
    const result=respond(path,method,payload);return result===undefined?defaultResponse(path):result;
  }});await flush();return h;
}

test('late hours failure cannot overwrite a different dialog in the same tenant',async t=>{
  const waiting=deferred();const h=await hoursFixture(path=>path.endsWith('/working_hours')?waiting.promise:undefined);
  t.after(()=>h.close());h.click(`[data-os-hours="${professionalId}"]`);await flush();
  h.click('[data-os-action=close-dialog]');h.click('[data-os-action=new-professional]');await flush();
  h.fill('#osField-name','Edição local preservada');waiting.reject(Error('Falha tardia sintética'));await flush();
  assert.equal(h.document.querySelector('#osField-name').value,'Edição local preservada');
  assert.match(h.document.querySelector('#osDialogTitle').textContent,/Novo funcionário/);
  assert.doesNotMatch(h.document.querySelector('#osDialog').textContent,/Falha tardia/);
  assert.deepEqual(writes(h),[]);assert.equal(h.errors.length,0);
});

test('late hours success cannot overwrite a newly opened hours dialog for the same professional',async t=>{
  const waiting=deferred();let reads=0;const h=await hoursFixture(path=>{
    if(path.endsWith('/working_hours'))return ++reads===1?waiting.promise:[{id:'hour-current',professional_id:professionalId,weekday:0,start_time:'11:00',end_time:'15:00'}];
  });t.after(()=>h.close());h.click(`[data-os-hours="${professionalId}"]`);await flush();
  h.click('[data-os-action=close-dialog]');h.click(`[data-os-hours="${professionalId}"]`);await flush();
  waiting.resolve([{id:'hour-old',professional_id:professionalId,weekday:0,start_time:'01:00',end_time:'02:00'}]);await flush();
  assert.match(h.document.querySelector('#osDialog').textContent,/11:00 – 15:00/);
  assert.doesNotMatch(h.document.querySelector('#osDialog').textContent,/01:00 – 02:00/);
  assert.deepEqual(writes(h),[]);assert.equal(h.errors.length,0);
});

test('late hours failure after a tenant switch cannot reopen the old tenant dialog',async t=>{
  const waiting=deferred();const h=await hoursFixture(path=>path.endsWith('/working_hours')?waiting.promise:undefined);
  t.after(()=>h.close());h.click(`[data-os-hours="${professionalId}"]`);await flush();
  h.window.OrionSuite.sync({tenantId:tenantB});await flush();waiting.reject(Error('Segredo do tenant anterior'));await flush();
  assert.equal(h.document.querySelector('#osDialog').open,false);
  assert.doesNotMatch(h.document.body.textContent,/Segredo do tenant anterior/);
  assert.deepEqual(writes(h),[]);assert.equal(h.errors.length,0);
});

test('saving an automation checkbox preserves custom template whitespace exactly',async t=>{
  const template='  Olá, {cliente}.\nSeu texto revisado.\n  ';
  let configuration={rules:[{event:'booking_confirmed',template,template_source:'custom',requested_enabled:false}],recovery_days:60,vacancy_requested:false};
  const h=await marketing((path,method,payload)=>{
    if(path.endsWith('/marketing/automations')){if(method==='PUT')configuration=payload.configuration;return {configuration,version:5,execution_enabled:false};}
  });t.after(()=>h.close());h.click('[data-os-marketing-tab=automations]');await flush();
  const field=h.document.querySelector('[name=rule_template_booking_confirmed]');assert.equal(field.value,template);
  h.document.querySelector('[name=rule_enabled_booking_confirmed]').checked=true;
  h.submit('[data-os-form=automations]');await flush();
  const write=writes(h);assert.equal(write.length,1);assert.equal(write[0].method,'PUT');
  const saved=write[0].data.configuration.rules.find(rule=>rule.event==='booking_confirmed');
  assert.equal(saved.template,template);assert.equal(saved.template_source,'custom');assert.equal(saved.requested_enabled,true);
  assert.equal(h.calls.some(call=>/\/approve$|\/messages$|\/send$|\/execution$/.test(call.path)&&call.method!=='GET'),false);
  assert.equal(h.errors.length,0);
});
