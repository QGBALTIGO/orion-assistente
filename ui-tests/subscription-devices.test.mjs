import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {makeSuite,planState,defaultResponse} from './harness.mjs';
import {makeDevices,base,deviceId,operationId,device,ready,listing,settings,operation,copy,contract,failure,flush,deferred,tenantA} from './subscription-devices-harness.mjs';

const writes=h=>h.calls.filter(call=>call.method!=='GET');
const field=path=>'[data-sd-field="'+path+'"]';
const act=name=>'[data-sd-action="'+name+'"]';
const form=name=>'[data-sd-form="'+name+'"]';
const deviceSource=await readFile(new URL('../app/static/orion-subscription-devices.js',import.meta.url),'utf8');
const connectSource=await readFile(new URL('../app/static/orion-connect.js',import.meta.url),'utf8');
const devicePlan=planState('none',{workspace_profile:'subscription_services',features:['account.manage','business.setup','devices.manage'],permissions:{'account.manage':true,'business.setup':true,'devices.manage':true}});

test('only the subscription profile gets the Devices route; every other module stays closed',async t=>{
  const h=await makeSuite({initialRoute:'dispositivos',permissions:devicePlan,
    bridge:{tenants:[{id:tenantA,name:'Empresa sintética',role:'owner',workspace_profile:'subscription_services'}]},
    configureWindow(window){Object.defineProperty(window.document,'hidden',{value:false,configurable:true});window.eval(connectSource);window.eval(deviceSource);},
    respond:path=>path===base?listing():defaultResponse(path)});
  t.after(()=>h.close());await flush();
  assert.deepEqual([...h.document.querySelectorAll('#osNav [data-os-route]')].map(node=>node.dataset.osRoute),['dashboard','dispositivos','chatbot','novidades','ajuda','member']);
  assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'dispositivos');
  assert.ok(h.document.querySelector('[data-sd-card]'));
  assert.equal(writes(h).length,0);
  assert.equal(h.calls.some(call=>call.path.startsWith('/api/v1/qr/')||call.path.includes('/qr-channels')||call.path.includes('/plan-')),false);
  await h.window.OrionSuite.open('inbox');await flush();
  assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'dashboard');
  assert.equal(h.document.querySelector('#osSubscriptionDevices'),null);
  assert.deepEqual(h.errors,[]);
});

test('ordinary businesses cannot open Devices even with an injected feature flag',async t=>{
  const h=await makeSuite({initialRoute:'dispositivos',permissions:planState('elite',{workspace_profile:'standard',permissions:{'devices.manage':true}})});
  t.after(()=>h.close());await flush();
  assert.equal(h.document.querySelector('#osNav [data-os-route=dispositivos]'),null);
  assert.equal(h.calls.some(call=>call.path.includes('/subscription-whatsapp/')),false);
  assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'plano');
  assert.deepEqual(h.errors,[]);
});

test('opening devices reads only the scoped API and shows real verified numbers and metrics',async t=>{
  const item=device({name:'Dispositivo <b>sintético</b>',status:'connected',phone:'5567999999901',phone_verified:true,
    metrics:{available:true,today:3,last_7_days:21,last_30_days:45,total:90,timezone:'America/Sao_Paulo',tracking_since:'2026-10-01T12:00:00Z'},
    features:{transcription:true,call_blocking:false}});
  const h=await makeDevices({data:listing([item])});t.after(()=>h.close());
  assert.equal(h.element('[data-sd-card] h2').textContent,item.name);
  assert.equal(h.document.querySelector('[data-sd-card] h2 b'),null);
  assert.match(h.element('.os-device-phone').textContent,/67/);
  assert.deepEqual([...h.document.querySelectorAll('.os-device-metric strong')].map(node=>node.textContent),['3','21','45','90']);
  assert.deepEqual(h.calls.map(call=>[call.method,call.path]),[['GET',base]]);
  assert.doesNotMatch(h.document.body.textContent,/Plano|upgrade|Telegram|Chatbot|Diagnósticos|Alertas/);
  assert.equal(h.element('.os-devices-usage .os-badge').textContent,'1 de 1');
  assert.deepEqual(h.errors,[]);
});

test('unknown metrics and unverified phone never become fabricated data',async t=>{
  const h=await makeDevices({data:listing([device({status:'connected',phone:'5567999999901',phone_verified:false})])});t.after(()=>h.close());
  assert.equal(h.document.querySelector('.os-device-phone'),null);
  assert.deepEqual([...h.document.querySelectorAll('.os-device-metric strong')].map(node=>node.textContent),['—','—','—','—']);
  assert.doesNotMatch(JSON.stringify(h.controller.getState()),/5567999999901/);
});

test('the one-device limit explains the limit without offering plans or attempting creation',async t=>{
  const h=await makeDevices();t.after(()=>h.close());
  h.click(act('create'));await flush();
  assert.match(h.element('[data-sd-feedback]').textContent,/limite de 1 dispositivo/);
  assert.equal(h.document.querySelector('dialog'),null);
  assert.equal(writes(h).length,0);
});

test('creation is explicit, idempotent and never connects or enables features',async t=>{
  const h=await makeDevices({data:listing([]),respond(path,method,payload){
    if(path===base&&method==='POST')return {device:device({name:payload.name})};
  }});t.after(()=>h.close());
  h.click(act('create'));await flush();assert.equal(writes(h).length,0);
  h.fill(field('new_name'),'Atendimento sintético');h.submit(form('dialog'));await flush();
  assert.equal(writes(h).length,1);
  assert.deepEqual(Object.keys(writes(h)[0].data).sort(),['idempotency_key','name']);
  assert.match(writes(h)[0].data.idempotency_key,/^[a-f0-9-]{36}$/);
  assert.equal(h.element('[data-sd-card] h2').textContent,'Atendimento sintético');
  assert.equal(h.controller.getState().view,'list');
  assert.equal(h.calls.some(call=>/\/actions|\/pair|\/qr$/.test(call.path)),false);
  assert.deepEqual(h.errors,[]);
});

test('an uncertain create never repeats automatically and an explicit retry keeps the original key',async t=>{
  let attempts=0;
  const h=await makeDevices({data:listing([]),respond(path,method,payload){
    if(path===base&&method==='POST'){if(++attempts===1)throw failure(503);return {device:device({name:payload.name})};}
  }});t.after(()=>h.close());
  h.click(act('create'));h.submit(form('dialog'));await flush();
  assert.equal(h.controller.getState().pending,'create');await h.tick(30000);
  assert.equal(writes(h).length,1);
  h.click(act('retry-pending'));await flush();
  assert.equal(writes(h).length,2);assert.deepEqual(writes(h)[0].data,writes(h)[1].data);
  assert.equal(h.controller.getState().pending,null);
});

test('connection and its tutorial are read only until the explicit prepare confirmation',async t=>{
  let polls=0;
  const h=await makeDevices({respond(path,method,payload,server){
    if(path.endsWith('/actions')&&method==='POST')return operation(payload.action,device(),'pending');
    if(path.endsWith('/operations/'+operationId)){polls++;server.data.items=[ready()];return operation('connect',ready(),'succeeded');}
  }});t.after(()=>h.close());
  h.click(act('open-connect'));await flush();assert.equal(writes(h).length,0);
  h.click(act('connect'));await flush();assert.equal(writes(h).length,0);
  h.submit(form('dialog'));await flush();assert.equal(writes(h).length,1);
  assert.equal(writes(h)[0].data.action,'connect');assert.equal(h.controller.getState().operationStatus,'pending');
  await h.tick(2000);assert.equal(polls,1);assert.equal(h.controller.getState().pending,null);
  assert.equal(h.document.querySelectorAll('.wizard-timeline-step').length,7);
  assert.equal(h.calls.some(call=>/\/qr$|\/pair$/.test(call.path)),false);
  assert.deepEqual(h.errors,[]);
});

test('QR is shown only after its tutorial and expiry never fetches a replacement',async t=>{
  const h=await makeDevices({data:listing([ready()])});t.after(()=>h.close());
  h.click(act('open-connect'));await flush();
  h.click('[data-sd-method=qr]');await flush();
  assert.equal(h.calls.some(call=>call.path.endsWith('/qr')),false);
  h.click(act('tutorial-next'));await flush();
  assert.ok(h.element('[data-sd-qr] img').src.startsWith('data:image/png;base64,'));
  assert.equal(h.calls.filter(call=>call.path.endsWith('/qr')).length,1);
  await h.tick(45000);
  assert.equal(h.document.querySelector('[data-sd-qr]'),null);
  assert.equal(h.calls.filter(call=>call.path.endsWith('/qr')).length,1);
  assert.equal(writes(h).length,0);
  assert.deepEqual(h.errors,[]);
});

test('pairing normalizes the number; hidden codes retain TTL and survive status capability changes',async t=>{
  const h=await makeDevices({data:listing([ready()]),respond(path,method,payload,server){
    if(path.endsWith('/pair')&&method==='POST'){
      server.data.items=[ready({status:'pairing',capabilities:{...ready().capabilities,pair:false,qr:false}})];
      return {...copy(contract.pair_response),code:'abcd1234'};
    }
  }});t.after(()=>h.close());
  h.click(act('open-connect'));await flush();h.click(act('tutorial-next'));await flush();
  h.fill(field('pair_phone'),'(67) 99999-9901');h.submit(form('pair'));await flush();
  assert.equal(writes(h)[0].data.phone,'5567999999901');
  assert.equal(h.element('[data-sd-code]').textContent,'ABCD-1234');
  await h.tick(5000);assert.equal(h.element('[data-sd-code]').textContent,'ABCD-1234');
  h.click(act('copy-code'));await flush();assert.deepEqual(h.copied,['ABCD-1234']);
  h.click(act('hide-code'));await flush();assert.equal(h.document.querySelector('[data-sd-code]'),null);
  h.click(act('show-code'));await flush();assert.equal(h.element('[data-sd-code]').textContent,'ABCD-1234');
  assert.equal(h.document.activeElement.dataset.sdAction,'hide-code');
  assert.doesNotMatch(JSON.stringify(h.controller.getState()),/ABCD|556799/);
  assert.equal(h.window.localStorage.length,0);assert.equal(h.window.sessionStorage.length,0);
  await h.tick(85000);assert.equal(h.document.querySelector('[data-sd-code]'),null);
  assert.equal(writes(h).length,1);
  assert.deepEqual(h.errors,[]);
});

test('an uncertain action is reconciled by reads and is not automatically submitted again',async t=>{
  const h=await makeDevices({data:listing([ready()]),respond(path,method){
    if(path.endsWith('/actions')&&method==='POST')throw failure(503);
  }});t.after(()=>h.close());
  h.click(act('restart'));h.submit(form('dialog'));await flush();
  assert.equal(h.controller.getState().pending,'action');await h.tick(30000);
  assert.equal(writes(h).length,1);
  h.click(act('reconcile'));await flush();assert.equal(writes(h).length,1);
  h.click(act('retry-pending'));await flush();
  assert.equal(writes(h).length,2);assert.deepEqual(writes(h)[0].data,writes(h)[1].data);
});

test('delete has cancel and repair alternatives and removes the card only after a terminal success',async t=>{
  const item=ready({status:'connected',phone:'5567999999901',phone_verified:true});
  const h=await makeDevices({data:listing([item]),respond(path,method,payload,server){
    if(path.endsWith('/actions')&&method==='POST')return operation(payload.action,item,'running');
    if(path.endsWith('/operations/'+operationId)){server.data=listing([]);return operation('remove',null,'succeeded');}
  }});t.after(()=>h.close());
  h.click(act('remove'));await flush();
  assert.ok(h.element(act('alternative-settings')));assert.ok(h.element(act('alternative-relink')));
  h.click(act('close-dialog'));assert.equal(writes(h).length,0);
  h.click(act('remove'));h.submit(form('dialog'));await flush();
  assert.ok(h.element('[data-sd-card]'));assert.equal(writes(h)[0].data.action,'remove');
  await h.tick(2000);assert.equal(h.document.querySelector('[data-sd-card]'),null);
  assert.equal(h.element('.os-devices-usage .os-badge').textContent,'0 de 1');
  assert.equal(writes(h).length,1);assert.deepEqual(h.errors,[]);
});

test('failed, uncertain and malformed terminal removal keep the existing device',async t=>{
  for(const status of ['failed','uncertain','succeeded']){
    const h=await makeDevices({respond(path,method,payload){
      if(path.endsWith('/actions')&&method==='POST')return operation(payload.action,device(),status);
      if(path.endsWith('/operations/'+operationId))return operation('remove',device(),status);
    }});t.after(()=>h.close());
    h.click(act('remove'));h.submit(form('dialog'));await flush();await h.tick(15000);
    assert.ok(h.element('[data-sd-card]'),status);assert.equal(writes(h).length,1);
    if(status!=='failed')assert.equal(h.element(act('remove')).disabled,true);
  }
});

test('a pending pairing snapshot survives reload and is reconciled by reads only',async t=>{
  const pending=operation('pair',device(),'uncertain').operation;
  const item=ready({status:'pairing',pending_operation:pending,capabilities:{connect:false,qr:false,pair:false,restart:false,relink:false,remove:false,settings:false}});
  const h=await makeDevices({data:listing([item]),respond(path,method){
    if(path.endsWith('/operations/'+operationId))return operation('pair',ready({status:'connected',pending_operation:null}),'succeeded');
  }});t.after(()=>h.close());
  assert.equal(h.controller.getState().pending,'pair');
  h.click(act('reconcile'));await flush();
  assert.equal(h.controller.getState().pending,null);
  assert.equal(writes(h).length,0);
  assert.equal(h.document.querySelector('[data-sd-code]'),null);
  assert.match(h.element('[data-sd-feedback]').textContent,/Solicitação de vínculo confirmada/);
});

test('settings save exact scoped values, schedule copies and write-only proxy secrets',async t=>{
  const configuration=settings({capabilities:{webhook:true,proxy:true,transcription:true,transcription_max_minutes:30}});
  configuration.settings.proxy.password_configured=true;
  const h=await makeDevices({configuration,respond(path,method,payload,server){
    if(path.endsWith('/settings')&&method==='PUT'){
      const saved=copy(payload.settings);delete saved.proxy.password;delete saved.proxy.clear_password;
      saved.proxy.password_configured=true;
      server.settings={...server.settings,revision:payload.expected_revision+1,settings:saved};
      return server.settings;
    }
  }});t.after(()=>h.close());
  h.click(act('settings'));await flush();
  h.fill(field('name'),'Dispositivo revisado');h.fill(field('test_phone_e164'),'(67) 99999-9901');
  h.check(field('proxy.enabled'));h.fill(field('proxy.url'),'https://proxy.example.invalid:8080');h.fill(field('proxy.username'),'synthetic-user');
  h.fill('[data-sd-secret]','synthetic-secret');
  h.check(field('webhook.enabled'));h.fill(field('webhook.url'),'https://hooks.example.invalid/events');
  h.check(field('transcription.enabled'));h.fill(field('transcription.max_minutes'),'30');h.check(field('transcription.reply_enabled'));
  h.check(field('calls.reject_enabled'));h.check(field('calls.schedule_enabled'));h.check(field('calls.holiday_off'));
  h.fill(field('calls.whitelist'),'(67) 99999-9902\n+351 912345678');
  const callDay='[data-sd-schedule="calls"][data-sd-weekday="0"]';
  h.check(callDay+' [data-sd-day-enabled]');h.change(callDay+' [data-sd-copy]','weekdays');
  h.check(field('business_hours.enabled'));
  const day='[data-sd-schedule="business_hours"][data-sd-weekday="0"]';
  h.check(day+' [data-sd-day-enabled]');h.fill(day+' [data-sd-time="break_start"]','12:00');h.fill(day+' [data-sd-time="break_end"]','13:00');
  h.change(day+' [data-sd-copy]','weekdays');h.check(field('business_hours.holiday_off'));h.check(field('business_hours.block_replies_outside_hours'));
  h.fill(field('business_hours.outside_message'),'Retornaremos no próximo expediente.');
  h.fill(field('business_hours.break_message'),'Voltamos após a pausa.');
  h.submit(form('settings'));await flush();
  assert.equal(writes(h).length,1);
  const payload=writes(h)[0].data;
  assert.equal(payload.expected_revision,1);
  assert.equal(payload.settings.name,'Dispositivo revisado');assert.equal(payload.settings.test_phone_e164,'+5567999999901');
  assert.equal(payload.settings.proxy.password,'synthetic-secret');assert.equal(Object.hasOwn(payload.settings.proxy,'password_configured'),false);
  assert.deepEqual(payload.settings.calls.whitelist,['5567999999902','351912345678']);
  assert.equal(payload.settings.calls.days.filter(day=>day.enabled).length,5);
  assert.equal(payload.settings.business_hours.days[4].break_start,'12:00');
  assert.equal(payload.settings.business_hours.days[4].break_end,'13:00');
  assert.equal(payload.settings.transcription.max_minutes,30);
  assert.equal(h.element('[data-sd-secret]').value,'');
  assert.match(h.element(field('calls.whitelist')).value,/\+351/);
  h.submit(form('settings'));await flush();
  assert.deepEqual(writes(h)[1].data.settings.calls.whitelist,['5567999999902','351912345678']);
  assert.match(h.element('[data-sd-feedback]').textContent,/Configurações salvas/);
  assert.doesNotMatch(JSON.stringify(h.controller.getState()),/synthetic-secret/);
  assert.deepEqual(h.errors,[]);
});

test('proxy password preserves, replaces or clears explicitly and never remains after saving',async t=>{
  for(const change of ['preserve','replace','clear']){
    const configuration=settings({capabilities:{webhook:true,proxy:true,transcription:true,transcription_max_minutes:30}});
    configuration.settings.proxy.password_configured=true;
    configuration.settings.proxy.username='synthetic-user';
    const h=await makeDevices({configuration,respond(path,method,payload){
      if(path.endsWith('/settings')&&method==='PUT')return {...configuration,revision:2};
    }});t.after(()=>h.close());
    h.click(act('settings'));await flush();
    if(change==='clear')h.check('[data-sd-clear-password]');
    if(change==='replace')h.fill('[data-sd-secret]','replacement-synthetic-secret');
    h.submit(form('settings'));await flush();
    assert.equal(writes(h)[0].data.settings.proxy.password,change==='replace'?'replacement-synthetic-secret':undefined);
    assert.equal(writes(h)[0].data.settings.proxy.clear_password,change==='clear'?true:undefined);
    assert.equal(h.element('[data-sd-secret]').value,'');
  }
});

test('clear day sends a disabled empty row and does not save automatically',async t=>{
  const h=await makeDevices({respond(path,method,payload){
    if(path.endsWith('/settings')&&method==='PUT')return {...settings(),revision:2};
  }});t.after(()=>h.close());
  h.click(act('settings'));await flush();h.check(field('business_hours.enabled'));
  const day='[data-sd-schedule="business_hours"][data-sd-weekday="2"]';
  h.check(day+' [data-sd-day-enabled]');h.click(day+' '+act('clear-day'));await flush();
  assert.equal(writes(h).length,0);assert.equal(h.element(day+' [data-sd-time=start]').value,'');
  h.submit(form('settings'));await flush();
  assert.deepEqual(writes(h)[0].data.settings.business_hours.days[2],{weekday:2,enabled:false,start:null,break_start:null,break_end:null,end:null});
});

test('invalid pauses and unavailable capabilities keep the settings form without writes',async t=>{
  const h=await makeDevices();t.after(()=>h.close());
  h.click(act('settings'));await flush();
  for(const key of ['webhook.enabled','proxy.enabled','transcription.enabled'])assert.equal(h.element(field(key)).disabled,true);
  assert.match(h.element('[data-sd-section="transcription"]').textContent,/não está disponível/);
  h.fill(field('name'),'Rascunho preservado');h.check(field('business_hours.enabled'));
  const day='[data-sd-schedule="business_hours"][data-sd-weekday="0"]';
  h.check(day+' [data-sd-day-enabled]');h.fill(day+' [data-sd-time="break_start"]','12:00');
  h.submit(form('settings'));await flush();
  assert.equal(writes(h).length,0);assert.match(h.element('[data-sd-form-error]').textContent,/início e o fim da pausa/);
  assert.equal(h.element(field('name')).value,'Rascunho preservado');
});

test('settings conflict preserves drafts and requires reloading before a new save',async t=>{
  const h=await makeDevices({respond(path,method){if(path.endsWith('/settings')&&method==='PUT')throw failure(409);}});t.after(()=>h.close());
  h.click(act('settings'));await flush();h.fill(field('name'),'Rascunho em conflito');h.submit(form('settings'));await flush();
  assert.equal(writes(h).length,1);assert.equal(h.element(field('name')).value,'Rascunho em conflito');
  assert.equal(h.element(form('settings')+' button[type=submit]').disabled,true);
  h.click(act('reload-settings'));await flush();assert.match(h.element('dialog').textContent,/substituirão/);
  h.click(act('close-dialog'));assert.equal(h.element(field('name')).value,'Rascunho em conflito');
  assert.equal(writes(h).length,1);assert.deepEqual(h.errors,[]);
});

test('returning to a hidden settings load resumes the read without losing an existing draft',async t=>{
  const pending=deferred();let reads=0;
  const h=await makeDevices({respond(path,method){
    if(path.endsWith('/settings')&&method==='GET'&&++reads===1)return pending.promise;
  }});t.after(()=>h.close());
  h.click(act('settings'));await flush();await h.visibility(true);
  pending.resolve(settings());await flush();
  assert.equal(h.document.querySelector(form('settings')),null);
  await h.visibility(false);assert.ok(h.element(form('settings')));
  h.fill(field('name'),'Rascunho preservado');
  await h.visibility(true);await h.visibility(false);
  assert.equal(h.element(field('name')).value,'Rascunho preservado');
  assert.equal(reads,2);assert.equal(writes(h).length,0);
});

test('late pairing cannot expose a code after hiding or unmount, and hidden screens stop polling',async t=>{
  for(const mode of ['hidden','unmount']){
    const late=deferred();
    const h=await makeDevices({data:listing([ready()]),respond(path,method){
      if(path.endsWith('/pair')&&method==='POST')return late.promise;
    }});t.after(()=>h.close());
    h.click(act('open-connect'));await flush();h.click(act('tutorial-next'));await flush();
    h.fill(field('pair_phone'),'(67) 99999-9901');h.submit(form('pair'));await flush();
    if(mode==='hidden')await h.visibility(true);else h.controller.unmount();
    late.resolve(contract.pair_response);await flush();
    assert.equal(h.document.querySelector('[data-sd-code]'),null);
    const before=h.calls.length;await h.tick(30000);assert.equal(h.calls.length,before);
    assert.equal(writes(h).length,1);
  }
});

test('readonly access cannot create, prepare, edit, remove or read QR credentials',async t=>{
  const item=ready({capabilities:{connect:false,qr:false,pair:false,restart:false,relink:false,remove:false,settings:false}});
  const h=await makeDevices({data:listing([item],{capabilities:{manage:false},can_create:false})});t.after(()=>h.close());
  assert.equal(h.document.querySelector(act('create')),null);assert.equal(h.document.querySelector(act('settings')),null);assert.equal(h.document.querySelector(act('remove')),null);
  h.click(act('open-connect'));await flush();
  assert.equal(h.document.querySelector(act('connect')),null);assert.equal(h.document.querySelector(form('pair')),null);
  assert.equal(h.calls.some(call=>call.path.endsWith('/qr')),false);assert.equal(writes(h).length,0);
  assert.deepEqual(h.errors,[]);
});


test('mensagens retidas mantêm a pausa e não oferecem reinício que não as resolveria',async t=>{
  const original=device();
  const withReview=device({
    status:'needs_attention',
    capabilities:{...original.capabilities,restart:true,settings:true,relink:true},
    connection_health:{
      state:'needs_attention',reason:'ingress_review_required',action:'review_incoming_messages',
      ingress_blocked:4,ingress_pending:0,ingress_storage_unavailable:false,
      transport_observed:false,stale:false
    }
  });
  const h=await makeDevices({data:listing([withReview])});
  t.after(()=>h.close());
  const card=h.element('[data-sd-card]');
  assert.match(card.textContent,/4 mensagens recebidas precisam de revisão/);
  assert.match(card.textContent,/respostas automáticas estão pausadas/);
  assert.match(card.textContent,/Reiniciar a conexão não libera essas mensagens/);
  assert.equal(card.querySelectorAll('[data-sd-action="restart"]').length,0);
  assert.ok(card.querySelector('[data-sd-action="open-connect"]'));
  assert.equal(writes(h).length,0,'mensagem explicativa não pode modificar conexão');
  assert.deepEqual(h.errors,[]);
});

test('respostas degradadas aparecem mesmo com socket aberto e não disparam reenvio',async t=>{
  const item=device({status:'degraded',connection_health:{state:'degraded',reason:'outbound_preparation_failed',
    action:'inspect_outbound_preparation',outbound_preparation_failures:2,outbound_preparation_degraded:true,
    ingress_blocked:0,ingress_pending:0}});
  const h=await makeDevices({data:listing([item])});
  t.after(()=>h.close());
  const card=h.element('[data-sd-card]');
  assert.match(card.textContent,/respostas recentes falharam antes do envio/);
  assert.match(card.textContent,/Mensagens antigas não serão reenviadas automaticamente/);
  assert.equal(writes(h).length,0);
  assert.deepEqual(h.errors,[]);
});

test('um botão antigo de reinício também é rejeitado quando a fila ficou bloqueada',async t=>{
  const item=device({status:'needs_attention',
    connection_health:{state:'needs_attention',reason:'ingress_review_required',
      action:'review_incoming_messages',ingress_blocked:1,ingress_pending:0},
    capabilities:{...device().capabilities,restart:true}});
  const h=await makeDevices({data:listing([item])});
  t.after(()=>h.close());
  const stale=h.document.createElement('button');
  stale.type='button';stale.dataset.sdAction='restart';stale.dataset.sdDevice=deviceId;
  h.element('#devices').append(stale);stale.click();await flush();
  assert.equal(writes(h).length,0,'reinício antigo não deve ser enviado');
  assert.match(h.element('[data-sd-feedback]').textContent,/Reiniciar não remove mensagens/);
  assert.deepEqual(h.errors,[]);
});
