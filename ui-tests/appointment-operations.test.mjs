import test from 'node:test';
import assert from 'node:assert/strict';
import {makeSuite,flush,defaultResponse,tenantA,customerId,planState,deferred} from './harness.mjs';

const professionalId='50000000-0000-4000-8000-000000000005';
const serviceId='60000000-0000-4000-8000-000000000006';
const extraId='70000000-0000-4000-8000-000000000007';
const secondExtraId='80000000-0000-4000-8000-000000000008';
const appointmentId='90000000-0000-4000-8000-000000000009';
const stamp='2030-04-05T12:03:04.123456+00:00';
const plain=value=>JSON.parse(JSON.stringify(value));
const baseService={id:serviceId,name:'Corte',active:true,catalog_kind:'base',duration_min:40,price_cents:4000,price_mode:'fixed'};
const extras=[{id:extraId,name:'Hidratação',duration_min:10,price_cents:1500,price_mode:'fixed',recommended:true,add_duration:false},
  {id:secondExtraId,name:'Barba',duration_min:20,price_cents:2500,price_mode:'fixed',recommended:false,add_duration:true}];
const initial=overrides=>({id:appointmentId,customer_id:customerId,customer_name:'Cliente sintético',customer_phone:'+5567999999999',
  professional_id:professionalId,professional_name:'Profissional sintético',status:'confirmed',start_at:'2030-04-05T12:00:00Z',end_at:'2030-04-05T12:40:00Z',
  total_cents:4000,original_total_cents:4000,discount_cents:0,price_mode:'fixed',updated_at:stamp,timezone:'America/Sao_Paulo',booking_mode:'regular',
  protocol:'TESTE-AGENDA',items:[{service_id:serviceId,name_snapshot:'Corte',duration_snapshot_min:40,price_snapshot_cents:4000,price_mode_snapshot:'fixed',item_origin:'booked'}],
  allowed_actions:['add_services','cancel','reschedule'],history:[],...overrides});

async function fixture(t,{override,record=initial(),role='owner',conflict=false}={}){
  const h=await makeSuite({initialRoute:'agenda',role,permissions:planState('essencial'),respond:(path,method='GET',payload)=>{
    const custom=override?.(path,method,payload,record);if(custom!==undefined)return custom;
    if(path.includes('/appointments?'))return {items:[record],total:1,offset:0,limit:50,has_more:false};
    if(path.endsWith('/appointments/'+appointmentId))return record;
    if(path.endsWith('/appointments/manual'))return {id:appointmentId,status:payload.mode==='retroactive'?'completed':'confirmed'};
    if(path.endsWith('/extras-catalog'))return {appointment:record,services:extras,max_items:6};
    if(path.endsWith('/extras/preview'))return {appointment:record,total_cents:record.total_cents+extras.filter(item=>payload.service_ids.includes(item.id)).reduce((sum,item)=>sum+item.price_cents,0),
      services_duration_min:payload.service_ids.includes(secondExtraId)?60:40,reserved_duration_min:40,new_end_at:'2030-04-05T13:00:00Z',duration_conflict:conflict,
      conflict_message:conflict?'Há outro atendimento neste intervalo.':null};
    if(path.endsWith('/extras'))return {...record,updated_at:'2030-04-05T12:05:00Z'};
    if(path.endsWith('/services'))return [baseService];
    if(path.endsWith('/professionals'))return [{id:professionalId,name:'Profissional sintético',active:true,accepts_appointments:true}];
    if(path.endsWith('/professional_services'))return [{professional_id:professionalId,service_id:serviceId}];
    if(path.includes('/management/services/')&&path.endsWith('/addons'))return {items:[]};
    if(path.includes('/management/customers?'))return {items:[{id:customerId,name:'Cliente sem telefone',phone:null,version:1}],total:1};
    if(path.includes('/availability?'))return {slots:['2030-04-05T12:00:00Z'],timezone:'America/Sao_Paulo'};
    if(path.endsWith('/google/status'))return {configured:false,connected:false,sync_enabled:false,capabilities:{}};
    if(path.includes('/google/appointments/'))return {status:'not_queued'};
    return defaultResponse(path);
  }});
  t.after(()=>{assert.deepEqual(h.errors,[]);h.close();});await flush();return h;
}
async function change(h,name,value){const input=h.document.querySelector(`[name="${name}"]`);assert.ok(input,name);input.value=value;input.dispatchEvent(new h.window.Event('change',{bubbles:true}));await flush();return input;}
async function manual(h,{mode='exceptional',walkIn=false}={}){
  h.click('[data-os-action=new-appointment]');await flush();
  await change(h,'booking_mode',mode);
  h.fill('#osBookingCustomer','Cliente sintético');
  if(walkIn)h.click('[name=walk_in]');else h.fill('[name=customer_phone]','67999999999');
  await change(h,'day',mode==='retroactive'?'2020-10-10':'2030-04-05');
  h.fill('[name=clock]','19:00');await change(h,'professional_id',professionalId);
  h.click(`[data-os-book-service="${serviceId}"]`);await flush();
  return h.document.querySelector('[data-os-form=appointment]');
}
async function extrasForm(h){
  h.click(`[data-os-appointment="${appointmentId}"]`);await flush();
  h.click('[data-os-action=appointment-extras]');await flush();
  return h.document.querySelector('[data-os-form=appointment-extras]');
}
const writes=h=>h.calls.filter(c=>c.method==='POST'&&!c.path.endsWith('/preview'));
const extrasWrites=h=>h.calls.filter(c=>c.method==='POST'&&c.path.endsWith('/extras'));

test('Essencial cria horário excepcional pelo endpoint privado, no fuso da empresa, sem consultar horários públicos',async t=>{
  const h=await fixture(t);const form=await manual(h);
  assert.equal(form.elements.start_at.disabled,true);
  assert.equal(h.calls.some(c=>c.path.includes('/availability?')),false);
  h.submit('[data-os-form=appointment]');await flush();
  const calls=writes(h);assert.equal(calls.length,1);assert.equal(calls[0].path,`/api/v1/tenants/${tenantA}/appointments/manual`);
  assert.equal(calls[0].data.mode,'exceptional');assert.equal(calls[0].data.start_at,'2030-04-05T22:00:00.000Z');
  assert.equal(calls[0].data.professional_id,professionalId);assert.equal(calls[0].data.customer_phone,'+5567999999999');
  assert.equal(calls[0].data.appointment_messages_opt_in,false);assert.equal(calls[0].data.walk_in,false);
  assert.equal(h.document.getElementById('osDialog').open,false);
});

test('retroativo reutiliza cliente sem telefone, preserva valor final e não autoriza mensagens',async t=>{
  const h=await fixture(t);const form=await manual(h,{mode:'retroactive',walkIn:true});
  h.fill('#osBookingCustomer','Cliente sem telefone');h.click(`[data-os-book-customer="${customerId}"]`);
  h.fill('[name=final_total]','85,00');h.fill('[name=actual_duration_min]','65');
  form.elements.appointment_messages_opt_in.checked=true;
  assert.equal(form.elements.customer_phone.required,false);assert.equal(form.elements.customer_phone.disabled,true);
  assert.equal(form.elements.appointment_messages_opt_in.disabled,true);
  h.submit('[data-os-form=appointment]');await flush();
  const call=writes(h)[0];assert.ok(call);
  assert.equal(call.data.mode,'retroactive');assert.equal(call.data.customer_id,customerId);assert.equal(call.data.customer_phone,null);assert.equal(call.data.walk_in,true);
  assert.equal(call.data.final_total_cents,8500);assert.equal(call.data.actual_duration_min,65);assert.equal(call.data.appointment_messages_opt_in,false);
  assert.equal(call.data.start_at,'2020-10-10T22:00:00.000Z');assert.equal(writes(h).length,1);
});

test('trocar de retroativo avulso para agendamento normal restaura horário e contato obrigatórios',async t=>{
  const h=await fixture(t);const form=await manual(h,{mode:'retroactive',walkIn:true});
  await change(h,'booking_mode','regular');
  assert.equal(form.elements.walk_in.checked,false);assert.equal(form.elements.walk_in.disabled,true);
  assert.equal(form.elements.customer_phone.required,true);assert.equal(form.elements.customer_phone.disabled,false);
  assert.equal(form.elements.clock.disabled,true);assert.equal(form.elements.final_total.disabled,true);
  assert.equal(form.elements.start_at.required,true);assert.equal(form.elements.appointment_messages_opt_in.checked,false);
  assert.equal(form.elements.day.max,'');assert.equal(writes(h).length,0);
});

test('resposta incerta de registro manual preserva o mesmo corpo e chave ao repetir',async t=>{
  let attempts=0;const h=await fixture(t,{override:(path,method)=>{if(path.endsWith('/appointments/manual')&&method==='POST'&&++attempts===1)throw Error('Resposta interrompida.');}});
  const form=await manual(h,{mode:'retroactive',walkIn:true});
  h.fill('[name=final_total]','50,00');h.submit('[data-os-form=appointment]');await flush();
  assert.equal(form.querySelector('fieldset').disabled,true);
  form.elements.customer_name.value='Outro nome';form.elements.booking_mode.value='exceptional';form.elements.final_total.value='999,00';
  h.submit('[data-os-form=appointment]');await flush();
  const requests=writes(h);assert.equal(requests.length,2);assert.deepEqual(plain(requests[1].data),plain(requests[0].data));
  assert.equal(h.document.getElementById('osDialog').open,false);
});

test('recusa explícita de horário mantém o formulário manual editável, sem confirmar outro registro',async t=>{
  const h=await fixture(t,{override:path=>{if(path.endsWith('/appointments/manual'))throw Object.assign(Error('Horário ocupado.'),{status:409});}});
  const form=await manual(h);h.submit('[data-os-form=appointment]');await flush();
  assert.equal(form.querySelector('fieldset').disabled,false);assert.match(form.querySelector('.os-form-message').textContent,/Horário ocupado/);
  assert.equal(writes(h).length,1);assert.equal(h.document.getElementById('osDialog').open,true);
});

test('adicionais recomendados aparecem primeiro; a primeira submissão apenas confere valores',async t=>{
  const h=await fixture(t);const form=await extrasForm(h);
  assert.equal(form.elements.actual_duration_min,undefined,'não se registra duração realizada em atendimento futuro');
  const options=[...h.document.querySelectorAll('[name=extra_service_ids]')];assert.equal(options[0].value,extraId);
  assert.match(h.document.getElementById('osDialog').textContent,/sem tempo extra/);
  h.click(`[data-os-extra-service="${extraId}"]`);h.submit('[data-os-form=appointment-extras]');await flush();
  assert.equal(extrasWrites(h).length,0);assert.equal(h.calls.filter(c=>c.path.endsWith('/extras/preview')).length,1);
  assert.match(h.document.getElementById('osExtraPreview').textContent,/55,00/);
  h.submit('[data-os-form=appointment-extras]');await flush();
  assert.equal(extrasWrites(h).length,1);const data=extrasWrites(h)[0].data;
  assert.deepEqual(plain(data.service_ids),[extraId]);assert.equal(data.expected_updated_at,stamp);assert.equal(data.preserve_reserved_time,false);
  assert.ok(data.idempotency_key);assert.equal(data.customer_phone,undefined);assert.equal(data.total_cents,undefined);
  assert.equal(h.document.querySelector('.os-appointment-notice p').textContent,'Serviços adicionados ao atendimento. Total atualizado.');
});

test('conflito de duração exige escolha explícita para manter o horário reservado',async t=>{
  const h=await fixture(t,{conflict:true,record:initial({start_at:'2020-10-10T12:00:00Z',end_at:'2020-10-10T12:40:00Z'})});const form=await extrasForm(h);
  h.click(`[data-os-extra-service="${secondExtraId}"]`);h.fill('[name=actual_duration_min]','65');
  h.submit('[data-os-form=appointment-extras]');await flush();
  assert.equal(form.elements.preserve_reserved_time.required,true);assert.equal(form.elements.preserve_reserved_time.checked,false);
  assert.equal(form.elements.preserve_reserved_time.getAttribute('aria-describedby'),'osExtraConflictMessage');
  assert.equal(h.document.getElementById('osExtraConflictMessage').getAttribute('aria-live'),'polite');
  assert.equal(h.document.getElementById('osExtraConflict').hidden,false);
  h.submit('[data-os-form=appointment-extras]');await flush();assert.equal(extrasWrites(h).length,0);
  h.click('[name=preserve_reserved_time]');h.submit('[data-os-form=appointment-extras]');await flush();
  assert.equal(extrasWrites(h).length,1);assert.equal(extrasWrites(h)[0].data.preserve_reserved_time,true);assert.equal(extrasWrites(h)[0].data.actual_duration_min,65);
  assert.equal(h.document.querySelector('.os-appointment-notice p').textContent,'Serviços adicionados. Total atualizado e horário reservado mantido.');
});

test('novo conflito após prévia não força gravação: pede novamente escolha explícita',async t=>{
  let attempt=0;const h=await fixture(t,{override:path=>{
    if(path.endsWith('/extras')&&++attempt===1)throw Object.assign(Error('Outro horário foi ocupado.'),{status:409,detail:{code:'extra_duration_conflict'}});
  }});const form=await extrasForm(h);
  h.click(`[data-os-extra-service="${secondExtraId}"]`);h.submit('[data-os-form=appointment-extras]');await flush();
  h.submit('[data-os-form=appointment-extras]');await flush();
  assert.equal(extrasWrites(h).length,1);assert.equal(form.elements.preserve_reserved_time.required,true);assert.equal(form.elements.preserve_reserved_time.checked,false);
  assert.match(h.document.getElementById('osExtraConflictMessage').textContent,/Outro horário/);
  h.click('[name=preserve_reserved_time]');h.submit('[data-os-form=appointment-extras]');await flush();
  assert.equal(extrasWrites(h).length,2);assert.equal(extrasWrites(h)[1].data.preserve_reserved_time,true);
  assert.notEqual(extrasWrites(h)[0].data.idempotency_key,extrasWrites(h)[1].data.idempotency_key);
});

test('alterar seleção após prévia exige nova conferência antes de salvar',async t=>{
  const h=await fixture(t);await extrasForm(h);
  h.click(`[data-os-extra-service="${extraId}"]`);h.submit('[data-os-form=appointment-extras]');await flush();
  h.click(`[data-os-extra-service="${secondExtraId}"]`);h.submit('[data-os-form=appointment-extras]');await flush();
  assert.equal(extrasWrites(h).length,0);assert.equal(h.calls.filter(c=>c.path.endsWith('/extras/preview')).length,2);
  assert.match(h.document.getElementById('osExtraPreview').textContent,/80,00/);
});

test('resposta incerta dos extras congela intenção e evita duplicação no retry',async t=>{
  let attempt=0;const h=await fixture(t,{override:path=>{if(path.endsWith('/extras')&&++attempt===1)throw Error('Conexão interrompida.');}});
  const form=await extrasForm(h);h.click(`[data-os-extra-service="${extraId}"]`);
  h.submit('[data-os-form=appointment-extras]');await flush();h.submit('[data-os-form=appointment-extras]');await flush();
  assert.equal(form.querySelector('fieldset').disabled,true);
  form.querySelector(`[value="${extraId}"]`).checked=false;form.querySelector(`[value="${secondExtraId}"]`).checked=true;
  h.submit('[data-os-form=appointment-extras]');await flush();
  assert.equal(extrasWrites(h).length,2);assert.deepEqual(plain(extrasWrites(h)[1].data),plain(extrasWrites(h)[0].data));
});

test('atendimento concluído exibe extras e origem retroativa, sem ação financeira de adicionar',async t=>{
  const record=initial({booking_mode:'retroactive',created_at:'2030-04-06T14:00:00Z',status:'completed',allowed_actions:[],actual_duration_min:65,services_duration_min:60,
    items:[...initial().items,{service_id:extraId,name_snapshot:'Hidratação',duration_snapshot_min:10,price_snapshot_cents:1500,item_origin:'extra',added_duration_min:0}]});
  const h=await fixture(t,{record});h.click(`[data-os-appointment="${appointmentId}"]`);await flush();
  const text=h.document.getElementById('osDialog').textContent;
  assert.match(text,/Registrado depois/);assert.match(text,/Adicionado no atendimento/);assert.match(text,/Duração realizada/);assert.match(text,/Registrado em/);
  assert.equal(h.document.querySelector('[data-os-action=appointment-extras]'),null);
});

test('leitura de catálogo atrasada não substitui o formulário que o usuário abriu depois',async t=>{
  const late=deferred();const h=await fixture(t,{override:path=>path.endsWith('/extras-catalog')?late.promise:undefined});
  h.click(`[data-os-appointment="${appointmentId}"]`);await flush();h.click('[data-os-action=appointment-extras]');await flush();
  h.click('[data-os-action=close-dialog]');h.click('[data-os-action=new-appointment]');await flush();
  late.resolve({appointment:initial(),services:extras,max_items:6});await flush();
  assert.ok(h.document.querySelector('[data-os-form=appointment]'));assert.equal(h.document.querySelector('[data-os-form=appointment-extras]'),null);
});


test('atualização explícita do telefone do cliente é conferida antes do manual e não se repete após falha',async t=>{
  const customer={id:customerId,name:'Cliente sem telefone',phone:null,version:1};let writesCount=0;
  const h=await fixture(t,{override:(path,method,payload)=>{
    if(path.includes('/management/customers?'))return {items:[customer],total:1};
    if(path.endsWith('/management/customers/'+customerId)&&method==='PATCH'){Object.assign(customer,{phone:payload.phone,version:2});throw Error('Resposta do cadastro interrompida.');}
    if(path.endsWith('/management/customers/'+customerId)&&method==='GET')return {...customer};
    if(path.endsWith('/appointments/manual')&&++writesCount===1)throw Error('Resposta do agendamento interrompida.');
  }});
  await manual(h);h.fill('#osBookingCustomer','Cliente sem telefone');h.click(`[data-os-book-customer="${customerId}"]`);h.fill('[name=customer_phone]','67999999999');
  h.submit('[data-os-form=appointment]');await flush();
  const patch=h.calls.findIndex(call=>call.method==='PATCH'),verify=h.calls.findIndex(call=>call.path.endsWith('/management/customers/'+customerId)&&call.method==='GET'),save=h.calls.findIndex(call=>call.path.endsWith('/appointments/manual'));
  assert.ok(patch>=0&&patch<verify&&verify<save);
  h.submit('[data-os-form=appointment]');await flush();
  assert.equal(h.calls.filter(call=>call.method==='PATCH').length,1);
  const saved=writes(h);assert.equal(saved.length,2);assert.deepEqual(plain(saved[0].data),plain(saved[1].data));
  assert.equal(saved[0].data.customer_id,customerId);assert.equal(saved[0].data.customer_phone,'+5567999999999');
});
