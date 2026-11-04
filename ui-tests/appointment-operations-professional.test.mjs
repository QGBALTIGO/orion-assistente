import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {JSDOM,VirtualConsole} from 'jsdom';
import {flush,deferred} from './harness.mjs';

const pro='50000000-0000-4000-8000-000000000005';
const serviceId='60000000-0000-4000-8000-000000000006';
const extraId='70000000-0000-4000-8000-000000000007';
const secondExtraId='80000000-0000-4000-8000-000000000008';
const appointmentId='90000000-0000-4000-8000-000000000009';
const stamp='2030-04-05T12:03:04.123456+00:00';
const baseService={id:serviceId,name:'Corte',duration_min:40,price_cents:4000,price_mode:'fixed'};
const extras=[{id:extraId,name:'Hidratação',duration_min:10,price_cents:1500,price_mode:'fixed',recommended:true,add_duration:false},
  {id:secondExtraId,name:'Barba',duration_min:20,price_cents:2500,price_mode:'fixed',recommended:false,add_duration:true}];
const initial=overrides=>({id:appointmentId,customer_name:'Cliente sintético',status:'confirmed',start_at:'2030-04-05T23:00:00Z',end_at:'2030-04-05T23:40:00Z',
  total_cents:4000,price_mode:'fixed',updated_at:stamp,booking_mode:'regular',
  services:[{name:'Corte',duration_min:40}],items:[{service_id:serviceId,name_snapshot:'Corte',duration_snapshot_min:40,price_snapshot_cents:4000,item_origin:'booked'}],
  allowed_actions:['add_services','cancel','reschedule'],...overrides});
const response=(data,status=200)=>({ok:status>=200&&status<300,status,json:async()=>data,headers:new Map()});
const reject=(status,detail)=>({__response:response({detail},status)});
async function portal(t,{override,record=initial(),conflict=false}={}){
  const [html,js,fields]=await Promise.all(['orion-professional.html','orion-professional.js','orion-fields.js'].map(file=>readFile(new URL('../app/static/'+file,import.meta.url),'utf8')));
  const errors=[],calls=[],c=new VirtualConsole();c.on('jsdomError',e=>errors.push(String(e)));
  const dom=new JSDOM(html,{url:'https://orion.test/profissional',runScripts:'outside-only',virtualConsole:c}),w=dom.window;
  t.after(()=>{assert.deepEqual(errors,[]);dom.window.close();});
  Object.defineProperty(w.crypto,'randomUUID',{value:randomUUID,configurable:true});
  w.fetch=async(path,opts={})=>{
    const method=opts.method||'GET',body=opts.body?JSON.parse(opts.body):undefined;calls.push({path,method,body});
    const custom=await override?.(path,method,body,record);if(custom!==undefined)return custom.__response||response(custom);
    if(path==='/auth/me')return response({authenticated:true,role:'member',csrf:'csrf-synthetic'});
    if(path==='/api/v1/professional/me')return response({id:pro,name:'Guilherme',business_name:'Barbearia sintética',timezone:'America/Campo_Grande',role:'professional'});
    if(path==='/api/v1/professional/dashboard')return response({profile:{name:'Guilherme'},date:'2030-04-05',today:{confirmed:1,completed:0,revenue_cents:0,commission_cents:0},goals:[]});
    if(path.includes('/professional/agenda'))return response({date:new URL(path,'https://orion.test').searchParams.get('day')||'2030-04-05',appointments:[record],blocks:[]});
    if(path.endsWith('/professional/services'))return response({items:[baseService]});
    if(path.includes('/availability?'))return response({slots:['2030-04-05T23:00:00Z'],timezone:'America/Campo_Grande'});
    if(path.endsWith('/appointments/manual'))return response({id:appointmentId,status:body.mode==='retroactive'?'completed':'confirmed'});
    if(path.endsWith('/extras-catalog'))return response({appointment:record,services:extras,max_items:6});
    if(path.endsWith('/extras/preview'))return response({appointment:record,total_cents:body.service_ids.includes(secondExtraId)?6500:5500,services_duration_min:60,
      reserved_duration_min:40,new_end_at:'2030-04-06T00:00:00Z',duration_conflict:conflict,conflict_message:conflict?'Há um bloqueio no período adicional.':null});
    if(path.endsWith('/extras'))return response({...record,updated_at:'2030-04-05T23:01:00Z'});
    return response({detail:'Rota inesperada no teste: '+path},404);
  };
  w.eval(fields);w.eval(js);await flush();
  const doc=w.document;
  const click=selector=>{const el=doc.querySelector(selector);assert.ok(el,selector);el.click();return el;};
  const fill=(name,value)=>{const el=doc.querySelector(`[name="${name}"]`);assert.ok(el,name);el.value=value;el.dispatchEvent(new w.Event('input',{bubbles:true}));return el;};
  const change=async(name,value)=>{const el=doc.querySelector(`[name="${name}"]`);assert.ok(el,name);el.value=value;el.dispatchEvent(new w.Event('change',{bubbles:true}));await flush();return el;};
  const submit=()=>doc.getElementById('modalForm').dispatchEvent(new w.Event('submit',{cancelable:true,bubbles:true}));
  click('[data-page=agenda]');await flush();
  return {doc,w,calls,click,fill,change,submit};
}
async function manual(h,{mode='exceptional',walkIn=false}={}){
  h.click('[data-action=new-appointment]');await flush();await h.change('booking_mode',mode);
  h.fill('customer_name','Cliente sintético');
  if(walkIn)h.click('[name=walk_in]');else h.fill('customer_phone','67999999999');
  await h.change('day',mode==='retroactive'?'2020-10-10':'2030-04-05');h.fill('clock','19:00');
  h.click(`[name=service_ids][value="${serviceId}"]`);await flush();
  return h.doc.getElementById('modalForm');
}
async function openExtras(h){h.click('[data-action=extras]');await flush();return h.doc.getElementById('modalForm');}
const posts=h=>h.calls.filter(c=>c.method==='POST');
const saves=h=>posts(h).filter(c=>c.path.endsWith('/extras'));

test('profissional cria excepcional após revisar, no fuso da empresa, sem aceitar outro profissional',async t=>{
  const h=await portal(t);await manual(h);h.submit();await flush();
  assert.equal(posts(h).length,0);assert.match(h.doc.getElementById('modalReview').textContent,/Confira o registro/);
  h.submit();await flush();const call=posts(h)[0];assert.ok(call);assert.equal(call.path,'/api/v1/professional/appointments/manual');
  assert.equal(call.body.start_at,'2030-04-05T23:00:00.000Z');assert.equal(call.body.mode,'exceptional');assert.equal(call.body.customer_phone,'+5567999999999');
  assert.equal(call.body.professional_id,undefined);assert.equal(call.body.customer_id,undefined);assert.equal(h.calls.some(c=>c.path.includes('/availability?')),false);
  assert.ok(h.calls.every(c=>c.path==='/auth/me'||c.path.startsWith('/api/v1/professional/')));assert.equal(h.doc.getElementById('modal').hidden,true);
});

test('profissional registra avulso retroativo com valor final e sem mensagens',async t=>{
  const h=await portal(t);const form=await manual(h,{mode:'retroactive',walkIn:true});h.fill('final_total','85,00');h.fill('actual_duration_min','70');
  form.elements.appointment_messages_opt_in.checked=true;
  assert.equal(form.elements.appointment_messages_opt_in.disabled,true);assert.equal(form.elements.customer_phone.disabled,true);
  h.submit();await flush();assert.match(h.doc.getElementById('modalReview').textContent,/recebimento pendente/);assert.equal(posts(h).length,0);
  h.submit();await flush();const body=posts(h)[0].body;
  assert.equal(body.mode,'retroactive');assert.equal(body.walk_in,true);assert.equal(body.customer_phone,null);assert.equal(body.final_total_cents,8500);assert.equal(body.actual_duration_min,70);
  assert.equal(body.appointment_messages_opt_in,false);assert.equal(body.start_at,'2020-10-10T23:00:00.000Z');
  assert.match(h.doc.getElementById('toast').textContent,/Recebimento pendente/);
});

test('editar registro manual depois da revisão exige uma nova revisão antes de gravar',async t=>{
  const h=await portal(t);await manual(h);h.submit();await flush();h.fill('clock','20:00');h.submit();await flush();
  assert.equal(posts(h).length,0);assert.match(h.doc.getElementById('modalReview').textContent,/20:00/);
  h.submit();await flush();assert.equal(posts(h)[0].body.start_at,'2030-04-06T00:00:00.000Z');
});

test('profissional repete registro incerto com intenção original, sem gerar outro atendimento',async t=>{
  let attempts=0;const h=await portal(t,{override:path=>{if(path.endsWith('/appointments/manual')&&++attempts===1)throw Error('Resposta interrompida.');}});
  const form=await manual(h,{mode:'retroactive',walkIn:true});h.fill('final_total','45,00');h.submit();await flush();h.submit();await flush();
  assert.equal(form.elements.clock.disabled,true);form.elements.clock.value='22:00';form.elements.final_total.value='999,00';form.elements.walk_in.checked=false;
  h.submit();await flush();assert.equal(posts(h).length,2);assert.deepEqual(posts(h)[0].body,posts(h)[1].body);assert.equal(h.doc.getElementById('modal').hidden,true);
});

test('fechar modal durante registro em andamento mantém a verificação visível',async t=>{
  const late=deferred();const h=await portal(t,{override:path=>path.endsWith('/appointments/manual')?late.promise:undefined});
  await manual(h);h.submit();await flush();h.submit();await flush();h.click('#modalClose');
  assert.equal(h.doc.getElementById('modal').hidden,false);assert.equal(posts(h).length,1);
  late.resolve({id:appointmentId,status:'confirmed'});await flush();assert.equal(h.doc.getElementById('modal').hidden,true);
});

test('extras profissionais mantêm itens originais e precisam de prévia antes de gravar',async t=>{
  const h=await portal(t);const form=await openExtras(h);
  assert.equal(form.elements.actual_duration_min,undefined,'futuro não permite declarar duração realizada');
  assert.match(h.doc.getElementById('modalFields').textContent,/Serviço original/);assert.match(h.doc.getElementById('modalFields').textContent,/sem tempo extra/);
  assert.equal(h.doc.querySelector('[name=extra_service_ids]').value,extraId);
  h.click(`[name=extra_service_ids][value="${extraId}"]`);h.submit();await flush();
  assert.equal(saves(h).length,0);assert.match(h.doc.getElementById('modalExtraPreview').textContent,/55,00/);
  h.submit();await flush();assert.equal(saves(h).length,1);const body=saves(h)[0].body;
  assert.deepEqual(body.service_ids,[extraId]);assert.equal(body.expected_updated_at,stamp);assert.equal(body.preserve_reserved_time,false);assert.equal(body.professional_id,undefined);
  assert.equal(h.doc.getElementById('toast').textContent,'Serviços adicionados ao atendimento. Total atualizado.');
});

test('profissional precisa autorizar manter horário quando duração extra conflita',async t=>{
  const h=await portal(t,{conflict:true,record:initial({start_at:'2020-10-10T12:00:00Z',end_at:'2020-10-10T12:40:00Z'})});const form=await openExtras(h);
  h.click(`[name=extra_service_ids][value="${secondExtraId}"]`);h.fill('actual_duration_min','65');h.submit();await flush();
  assert.equal(form.elements.preserve_reserved_time.required,true);assert.equal(form.elements.preserve_reserved_time.checked,false);
  assert.equal(form.elements.preserve_reserved_time.getAttribute('aria-describedby'),'modalExtraConflictMessage');
  assert.equal(h.doc.getElementById('modalExtraConflictMessage').getAttribute('aria-live'),'polite');
  h.submit();await flush();assert.equal(saves(h).length,0);
  h.click('[name=preserve_reserved_time]');h.submit();await flush();assert.equal(saves(h).length,1);
  assert.equal(saves(h)[0].body.preserve_reserved_time,true);assert.equal(saves(h)[0].body.actual_duration_min,65);
  assert.equal(h.doc.getElementById('toast').textContent,'Serviços adicionados. Total atualizado e horário reservado mantido.');
});

test('409 estruturado de conflito novo pede decisão profissional, sem reenvio automático',async t=>{
  let attempts=0;const h=await portal(t,{override:path=>path.endsWith('/extras')&&++attempts===1?reject(409,{code:'extra_duration_conflict',message:'O intervalo acabou de ser ocupado.'}):undefined});
  const form=await openExtras(h);h.click(`[name=extra_service_ids][value="${secondExtraId}"]`);h.submit();await flush();h.submit();await flush();
  assert.equal(saves(h).length,1);assert.equal(form.elements.preserve_reserved_time.required,true);assert.equal(form.elements.preserve_reserved_time.checked,false);
  assert.match(h.doc.getElementById('modalExtraConflictMessage').textContent,/acabou de ser ocupado/);
  h.click('[name=preserve_reserved_time]');h.submit();await flush();assert.equal(saves(h).length,2);assert.equal(saves(h)[1].body.preserve_reserved_time,true);
});

test('extras profissionais com resposta incerta repetem o mesmo corpo e versão',async t=>{
  let attempts=0;const h=await portal(t,{override:path=>{if(path.endsWith('/extras')&&++attempts===1)throw Error('Falha de conexão.');}});
  const form=await openExtras(h);h.click(`[name=extra_service_ids][value="${extraId}"]`);h.submit();await flush();h.submit();await flush();
  assert.equal(form.querySelector('[name=extra_service_ids]').disabled,true);
  form.querySelector(`[value="${secondExtraId}"]`).checked=true;h.submit();await flush();
  assert.equal(saves(h).length,2);assert.deepEqual(saves(h)[1].body,saves(h)[0].body);
});

test('agenda profissional identifica retroativo concluído sem liberar extras',async t=>{
  const h=await portal(t,{record:initial({status:'completed',allowed_actions:[],booking_mode:'retroactive',created_at:'2030-04-06T12:00:00Z',actual_duration_min:70})});
  assert.match(h.doc.getElementById('content').textContent,/Registrado depois/);assert.match(h.doc.getElementById('content').textContent,/Duração realizada: 70 min/);
  assert.equal(h.doc.querySelector('[data-action=extras]'),null);assert.equal(posts(h).length,0);
});


test('modal profissional mantém navegação por Tab dentro do formulário e devolve foco ao fechar',async t=>{
  const h=await portal(t),trigger=h.doc.querySelector('[data-action=new-appointment]');trigger.focus();trigger.click();await flush();
  assert.equal(h.doc.activeElement,h.doc.querySelector('[name=booking_mode]'));
  const last=h.doc.getElementById('modalSubmit'),first=h.doc.getElementById('modalClose');last.focus();
  last.dispatchEvent(new h.w.KeyboardEvent('keydown',{key:'Tab',bubbles:true,cancelable:true}));
  assert.equal(h.doc.activeElement,first);
  first.dispatchEvent(new h.w.KeyboardEvent('keydown',{key:'Tab',shiftKey:true,bubbles:true,cancelable:true}));
  assert.equal(h.doc.activeElement,last);
  last.dispatchEvent(new h.w.KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));
  assert.equal(h.doc.getElementById('modal').hidden,true);assert.equal(h.doc.activeElement,trigger);
});

test('catálogo profissional assíncrono coloca foco no modal durante a consulta e no primeiro serviço ao carregar',async t=>{
  const late=deferred();const h=await portal(t,{override:path=>path.endsWith('/extras-catalog')?late.promise:undefined});
  const trigger=h.doc.querySelector('[data-action=extras]');trigger.focus();trigger.click();
  assert.equal(h.doc.activeElement,h.doc.getElementById('modalClose'));
  late.resolve({appointment:initial(),services:extras,max_items:6});await flush();
  assert.equal(h.doc.activeElement,h.doc.querySelector('[name=extra_service_ids]'));
  h.click('#modalCancel');assert.equal(h.doc.activeElement,trigger);
});
