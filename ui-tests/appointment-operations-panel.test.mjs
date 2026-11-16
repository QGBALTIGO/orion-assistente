import test from 'node:test';
import assert from 'node:assert/strict';
import {flush,planState} from './harness.mjs';
import {jsonResponse,makePanel} from './panel-harness.mjs';

// The complete shipped panel and its HTTP adapter run together. All responses
// are synthetic; no production appointments or messages are created.
test('painel completo preserva conflito estruturado HTTP e exige decisão antes de novo POST',async t=>{
  const appointmentId='90000000-0000-4000-8000-000000000009',serviceId='60000000-0000-4000-8000-000000000006',extraId='70000000-0000-4000-8000-000000000007';
  const record={id:appointmentId,professional_id:'50000000-0000-4000-8000-000000000005',professional_name:'Equipe sintética',customer_name:'Cliente sintético',status:'confirmed',updated_at:'2030-04-05T12:00:00.123456+00:00',
    start_at:'2030-04-05T12:00:00Z',end_at:'2030-04-05T12:40:00Z',price_mode:'fixed',total_cents:4000,allowed_actions:['add_services'],
    items:[{service_id:serviceId,name_snapshot:'Corte',duration_snapshot_min:40,price_snapshot_cents:4000,item_origin:'booked'}],history:[]};
  let saves=0;
  const h=await makePanel({initialRoute:'agenda',permissions:planState('essencial'),respond:(path,method)=>{
    if(path.includes('/appointments?'))return {items:[record],total:1,offset:0,limit:50,has_more:false};
    if(path.endsWith('/appointments/'+appointmentId))return record;
    if(path.endsWith('/professionals'))return [{id:record.professional_id,name:record.professional_name,active:true,accepts_appointments:true}];
    if(path.endsWith('/extras-catalog'))return {appointment:record,max_items:6,services:[{id:extraId,name:'Barba',price_cents:2500,duration_min:20,add_duration:true,price_mode:'fixed'}]};
    if(path.endsWith('/extras/preview'))return {appointment:record,total_cents:6500,services_duration_min:60,reserved_duration_min:40,new_end_at:'2030-04-05T13:00:00Z',duration_conflict:false};
    if(path.endsWith('/extras')&&method==='POST')return ++saves===1?jsonResponse({detail:{code:'extra_duration_conflict',message:'Outro atendimento ocupa o período adicional.'}},409):{...record,total_cents:6500};
    if(path.endsWith('/google/status'))return {configured:false,connected:false,capabilities:{}};
    if(path.includes('/google/appointments/'))return {status:'not_queued'};
  }});t.after(()=>h.close());
  h.click(`[data-os-appointment="${appointmentId}"]`);await flush();h.click('[data-os-action=appointment-extras]');await flush();
  h.click(`[data-os-extra-service="${extraId}"]`);h.submit('[data-os-form=appointment-extras]');await flush();
  h.submit('[data-os-form=appointment-extras]');await flush();
  assert.equal(saves,1);assert.match(h.element('#osExtraConflictMessage').textContent,/Outro atendimento/);
  const preserve=h.element('[name=preserve_reserved_time]');assert.equal(preserve.required,true);assert.equal(preserve.checked,false);
  assert.equal(h.element('#loginView').classList.contains('hidden'),true);
  h.click('[name=preserve_reserved_time]');h.submit('[data-os-form=appointment-extras]');await flush();
  const requests=h.calls.filter(call=>call.method==='POST'&&call.path.endsWith('/extras'));
  assert.equal(requests.length,2);assert.equal(requests[0].data.preserve_reserved_time,false);assert.equal(requests[1].data.preserve_reserved_time,true);
  assert.equal(requests[1].options.headers['X-Orion-CSRF'],'synthetic-mobile-csrf');
  assert.deepEqual(h.errors,[]);
});
