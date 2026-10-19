import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {makeSuite, flush, deferred, defaultResponse, tenantA, tenantB, customerId} from './harness.mjs';

const professionalId='50000000-0000-4000-8000-000000000005';
const serviceId='60000000-0000-4000-8000-000000000006';
const appointmentId='90000000-0000-4000-8000-000000000009';
const version='2030-04-05T12:03:04.123456+00:00';
const plain=value=>JSON.parse(JSON.stringify(value));
const googleOff={configured:false,connected:false,sync_enabled:false,sync_status:'not_configured',capabilities:{can_connect:false,can_disconnect:false,can_select_calendar:false,can_retry:false}};
const googleOn={configured:true,connected:true,sync_enabled:true,sync_status:'idle',calendar_id:'team@example.invalid',calendar_name:'Equipe sintética',pending:0,failed:0,sent:0,capabilities:{can_connect:true,can_disconnect:true,can_select_calendar:false,can_retry:false}};
function record(overrides={}){return {id:appointmentId,customer_id:customerId,customer_name:'Cliente WhatsApp',customer_display_name:'Pessoa sintética',customer_phone:'+5511999990000',professional_id:professionalId,professional_name:'Equipe sintética',professional_color:'#336699',status:'confirmed',start_at:'2030-04-05T12:00:00Z',end_at:'2030-04-05T12:30:00Z',timezone:'America/Sao_Paulo',updated_at:version,total_cents:5000,original_total_cents:5000,discount_cents:0,protocol:'ORION-SYNTHETIC',items:[{service_id:serviceId,name_snapshot:'Atendimento sintético',duration_snapshot_min:30,price_snapshot_cents:5000}],allowed_actions:['cancel','reschedule','complete','no_show'],history:[{id:'event-synthetic',event:'confirmed',occurred_at:'2030-04-05T11:00:00Z',requested_by:'business',source:'panel',actor:{role:'owner'}}],history_total:1,history_has_more:false,cancellation:null,...overrides};}
function responder({items=[record()],google=googleOff,on}={}){return (path,method='GET',data)=>{
  const custom=on?.(path,method,data);if(custom!==undefined)return custom;
  const url=new URL(path,'https://orion.test');
  if(url.pathname.endsWith('/google/status'))return google;
  if(/\/google\/appointments\/.+\/status$/.test(url.pathname))return {status:'not_queued',sync_enabled:false,connection_status:'not_connected'};
  if(url.pathname.endsWith('/appointments')){const offset=Number(url.searchParams.get('offset')||0),limit=Number(url.searchParams.get('limit')||50);return {items:items.slice(offset,offset+limit),total:items.length,offset,limit,has_more:offset+limit<items.length};}
  if(/\/appointments\/[a-zA-Z0-9-]+$/.test(url.pathname))return items.find(item=>url.pathname.endsWith('/'+item.id))||record();
  if(url.pathname.endsWith('/professionals'))return [{id:professionalId,name:'Equipe sintética',active:true,accepts_appointments:true,color:'#336699'}];
  if(url.pathname.endsWith('/working_hours'))return Array.from({length:7},(_,weekday)=>({id:'hours-'+weekday,professional_id:professionalId,weekday,start_time:'09:00',end_time:'18:00'}));
  if(url.pathname.endsWith('/management/loyalty/coupons'))return {items:[],total:0};
  if(url.pathname.endsWith('/availability'))return {slots:['2030-04-05T12:00:00Z','2030-04-05T13:00:00Z'],timezone:'America/Sao_Paulo'};
  return defaultResponse(path);
};}
async function change(h,selector,value){const input=h.document.querySelector(selector);assert.ok(input,selector);input.value=value;input.dispatchEvent(new h.window.Event('change',{bubbles:true}));await flush();return input;}
async function openDetail(h,id=appointmentId){h.click(`[data-os-appointment="${id}"]`);await flush();}
async function fixture(t,options={}){const h=await makeSuite({initialRoute:'agenda',...options});t.after(()=>h.close());await flush();return h;}
const posts=h=>h.calls.filter(call=>call.method==='POST');

test('agenda displays actual names and distinct lifecycle labels without guessing cancellation origin',async t=>{
  const states=[['confirmed',null],['completed',null],['no_show',null],['cancelled','customer'],['cancelled','professional'],['cancelled','business'],['cancelled','unknown']];
  const items=states.map(([status,requested_by],i)=>record({id:'appointment-'+i,status,cancellation:requested_by?{requested_by}:null}));
  const h=await fixture(t,{respond:responder({items})});const text=h.document.querySelector('.os-agenda-list').textContent;
  for(const label of ['Confirmado','Finalizado','Falta','Cancelado pelo cliente','Cancelado pelo profissional','Cancelado pela empresa'])assert.match(text,new RegExp(label));
  assert.equal(h.document.querySelectorAll('.os-agenda-item').length,7);
  assert.match(text,/Pessoa sintética/);assert.doesNotMatch(text,/Cliente WhatsApp/);
  assert.equal(h.document.querySelector('[data-os-appointment=appointment-6] .os-appointment-status').textContent,'Cancelado');
  assert.equal(posts(h).length,0);
});

test('calendar keeps cancellations and no-shows outside occupied slots and leaves them actionable for consultation',async t=>{
  const items=[record(),record({id:'cancelled-id',status:'cancelled',cancellation:{requested_by:'customer'}}),record({id:'missing-id',status:'no_show'})];
  const h=await fixture(t,{respond:responder({items})});await change(h,'#osAgendaDatePicker','2030-04-05');h.click('[data-os-agenda-mode=calendar]');await flush();
  assert.equal(h.document.querySelectorAll('.os-calendar-booking').length,1);
  assert.equal(h.document.querySelectorAll('.os-agenda-history-list [data-os-appointment]').length,2);
  assert.equal(h.document.querySelector('.os-calendar-booking').dataset.osAppointment,appointmentId);
  assert.equal(h.document.querySelectorAll('.os-calendar-cell:not(:disabled)').length>0,true);
  h.click('[data-os-appointment=cancelled-id]');await flush();assert.match(h.document.getElementById('osDialog').textContent,/Solicitado pelo cliente/);
});

test('filter requests use SQL pagination, professional, cancellation requester, and name or phone query',async t=>{
  const h=await fixture(t,{respond:responder()});await change(h,'#osAgendaProfessional',professionalId);await change(h,'#osAgendaStatus','cancelled_professional');
  h.fill('#osAgendaSearch','(11) 99999-0000');h.submit('#osAgendaFilters');await flush();
  const path=h.calls.filter(call=>/\/appointments\?/.test(call.path)).at(-1).path,q=new URL(path,'https://orion.test').searchParams;
  assert.equal(q.get('paged'),'true');assert.equal(q.get('professional_id'),professionalId);assert.equal(q.get('status'),'cancelled');assert.equal(q.get('cancellation_requested_by'),'professional');assert.equal(q.get('q'),'(11) 99999-0000');assert.equal(q.get('limit'),'50');assert.equal(q.get('offset'),'0');
  h.click('[data-os-agenda-mode=calendar]');await flush();assert.equal(h.document.querySelectorAll('.os-calendar-cell:not(:disabled)').length,0,'a filtered grid cannot imply that hidden bookings are free slots');
});

test('a stale agenda response cannot replace newer search results',async t=>{
  const old=deferred();let initial=true;
  const h=await fixture(t,{respond:responder({on:path=>{if(path.includes('/appointments?')){if(initial){initial=false;return undefined;}const q=new URL(path,'https://orion.test').searchParams.get('q');if(q==='old')return old.promise;if(q==='new')return {items:[record({customer_display_name:'Resultado atual'})],total:1,limit:50,offset:0,has_more:false};}}})});
  h.fill('#osAgendaSearch','old');h.submit('#osAgendaFilters');await flush();h.fill('#osAgendaSearch','new');h.submit('#osAgendaFilters');await flush();old.resolve({items:[record({customer_display_name:'Resultado antigo'})],total:1,limit:50,offset:0,has_more:false});await flush();
  assert.match(h.document.querySelector('.os-agenda-list').textContent,/Resultado atual/);assert.doesNotMatch(h.document.querySelector('.os-agenda-list').textContent,/Resultado antigo/);
});

test('calendar loads complete batches and disables grid creation at its explicit 1000 item limit',async t=>{
  const items=Array.from({length:1001},(_,i)=>record({id:'synthetic-'+i,status:'cancelled',cancellation:{requested_by:'unknown'}}));
  const h=await fixture(t,{respond:responder({items})});h.click('[data-os-agenda-mode=calendar]');await flush();
  const reads=h.calls.filter(call=>call.path.includes('/appointments?')).map(call=>new URL(call.path,'https://orion.test').searchParams);
  assert.deepEqual(reads.filter(q=>q.get('limit')==='200').map(q=>q.get('offset')),['0','200','400','600','800']);
  assert.match(h.document.getElementById('osPageBody').textContent,/mais de 1\.000/);assert.equal(h.document.querySelectorAll('.os-calendar-cell:not(:disabled)').length,0);
});

test('list pagination preserves an honest total and moves by the returned page limit',async t=>{
  const items=Array.from({length:51},(_,i)=>record({id:'item-'+i}));const h=await fixture(t,{respond:responder({items})});
  assert.match(h.document.getElementById('osAgendaCount').textContent,/1 a 50 de 51/);h.click('[data-os-action=agenda-page-next]');await flush();
  assert.equal(h.document.querySelectorAll('.os-agenda-item').length,1);assert.match(h.document.getElementById('osAgendaCount').textContent,/51 a 51 de 51/);assert.equal(h.document.querySelector('[data-os-action=agenda-page-next]').disabled,true);
});

test('opening a cached appointment always obtains fresh detail and safely escapes its content',async t=>{
  let detailReads=0;const a=record({customer_display_name:'<script>Private synthetic</script>',discount_cents:1000,total_cents:4000});
  const h=await fixture(t,{respond:responder({on:(path,method)=>path.endsWith('/appointments/'+appointmentId)&&method==='GET'?(detailReads++,a):undefined})});
  await openDetail(h);assert.equal(detailReads,1);assert.equal(h.document.getElementById('osDialog').querySelectorAll('script').length,0);assert.match(h.document.getElementById('osDialog').textContent,/<script>Private synthetic<\/script>/);assert.match(h.document.getElementById('osDialog').textContent,/Valor dos serviços/);assert.match(h.document.getElementById('osDialog').textContent,/Desconto/);assert.equal(h.document.activeElement.id,'osDialogTitle');
  h.click('[data-os-action=close-dialog]');await openDetail(h);assert.equal(detailReads,2);
});

test('a late detail response cannot replace a different appointment or reopen a closed drawer',async t=>{
  const late=deferred(),b=record({id:'appointment-b',customer_display_name:'Outra pessoa sintética'});
  const h=await fixture(t,{respond:responder({items:[record(),b],on:path=>path.endsWith('/appointments/'+appointmentId)?late.promise:undefined})});
  h.click(`[data-os-appointment="${appointmentId}"]`);await flush();h.click('[data-os-action=close-dialog]');await openDetail(h,b.id);late.resolve(record({customer_display_name:'Resposta antiga privada'}));await flush();assert.match(h.document.getElementById('osDialog').textContent,/Outra pessoa sintética/);assert.doesNotMatch(h.document.getElementById('osDialog').textContent,/Resposta antiga privada/);
});

test('changing tenant discards both old detail and old search timer',async t=>{
  const late=deferred();const h=await fixture(t,{respond:responder({on:path=>path.endsWith('/appointments/'+appointmentId)&&path.includes(tenantA)?late.promise:undefined})});
  h.fill('#osAgendaSearch','Privado A');h.click(`[data-os-appointment="${appointmentId}"]`);await flush();h.window.OrionSuite.sync({tenantId:tenantB});await flush();late.resolve(record({customer_display_name:'Segredo A'}));await flush();await new Promise(resolve=>setTimeout(resolve,280));await flush();
  assert.equal(h.document.getElementById('osDialog').open,false);assert.equal(h.document.getElementById('osAgendaSearch').value,'');assert.doesNotMatch(h.document.getElementById('page-suite').textContent,/Segredo A/);assert.equal(h.calls.filter(call=>call.path.includes(tenantB+'/appointments?')).length,1);
});

test('failed detail has only a retry and never exposes stale mutation actions',async t=>{
  const h=await fixture(t,{respond:responder({on:path=>{if(path.endsWith('/appointments/'+appointmentId))throw Object.assign(new Error('Consulta temporariamente indisponível'),{status:503});}})});await openDetail(h);
  assert.match(h.document.getElementById('osDialog').textContent,/Consulta temporariamente indisponível/);assert.equal(h.document.querySelector('[data-os-action=cancel-appointment]'),null);assert.ok(h.document.querySelector('[data-os-action=appointment-back]'));assert.equal(posts(h).length,0);
});

test('viewer and absent server capabilities prevent appointment mutations',async t=>{
  for(const role of ['viewer','owner']){const h=await fixture(t,{role,respond:responder({items:[record(role==='owner'?{allowed_actions:[]}: {})]})});await openDetail(h);for(const action of ['confirm-appointment','cancel-appointment','complete-appointment','no-show-appointment','reschedule-appointment'])assert.equal(h.document.querySelector('[data-os-action='+action+']'),null);assert.equal(posts(h).length,0);}
});

for(const requestedBy of ['customer','professional','business'])test('cancellation records explicit '+requestedBy+' origin with exact version, reason, and one operation',async t=>{
  let a=record();const pending=deferred();const h=await fixture(t,{respond:responder({on:(path,method,data)=>{if(path.endsWith('/appointments/'+appointmentId)&&method==='GET')return a;if(path.endsWith('/cancel')&&method==='POST')return pending.promise;}})});await openDetail(h);h.click('[data-os-action=cancel-appointment]');await flush();
  assert.equal(h.document.querySelector('[name=requested_by]:checked'),null,'do not infer requester from operator role');h.click('[name=requested_by][value='+requestedBy+']');h.fill('[name=reason]','Motivo sintético <seguro>');h.submit('[data-os-form=appointment-action]');h.submit('[data-os-form=appointment-action]');await flush();
  const writes=posts(h);assert.equal(writes.length,1);assert.equal(writes[0].data.expected_updated_at,version);assert.equal(writes[0].data.requested_by,requestedBy);assert.equal(writes[0].data.reason,'Motivo sintético <seguro>');assert.match(writes[0].data.idempotency_key,/^[a-f0-9-]{36}$/);assert.equal(h.document.querySelector('.os-close').disabled,true);
  a=record({status:'cancelled',allowed_actions:[],cancellation:{requested_by:requestedBy,reason:writes[0].data.reason},updated_at:'2030-04-05T12:10:00.123456Z'});pending.resolve(a);await flush();assert.match(h.document.getElementById('osDialog').textContent,/Cancelamento registrado/);assert.equal(posts(h).length,1);assert.equal(h.document.getElementById('osDialog').querySelector('seguro'),null);
});

test('conflict reloads fresh details and requires another deliberate review without repeating the operation',async t=>{
  let reads=0;const h=await fixture(t,{respond:responder({on:(path,method)=>{if(path.endsWith('/appointments/'+appointmentId)&&method==='GET')return ++reads>1?record({status:'cancelled',allowed_actions:[],cancellation:{requested_by:'professional'}}):record();if(path.endsWith('/cancel')&&method==='POST')throw Object.assign(new Error('O agendamento mudou'),{status:409});}})});await openDetail(h);h.click('[data-os-action=cancel-appointment]');h.click('[name=requested_by][value=customer]');h.submit('[data-os-form=appointment-action]');await flush();
  assert.equal(posts(h).length,1);assert.equal(reads,2);assert.match(h.document.getElementById('osDialog').textContent,/Os detalhes foram atualizados/);assert.match(h.document.getElementById('osDialog').textContent,/Cancelado pelo profissional/);assert.equal(h.document.querySelector('[data-os-form=appointment-action]'),null);
});

test('an uncertain mutation freezes its intent and retries the identical version and idempotency key',async t=>{
  let attempts=0,a=record();const h=await fixture(t,{respond:responder({on:(path,method)=>{if(path.endsWith('/appointments/'+appointmentId)&&method==='GET')return a;if(path.endsWith('/cancel')&&method==='POST'){if(++attempts===1)throw new Error('Resposta perdida');a=record({status:'cancelled',allowed_actions:[],cancellation:{requested_by:'customer'}});return a;}}})});await openDetail(h);h.click('[data-os-action=cancel-appointment]');h.click('[name=requested_by][value=customer]');h.fill('[name=reason]','Primeira intenção');h.submit('[data-os-form=appointment-action]');await flush();
  assert.equal(h.document.querySelector('[data-os-form=appointment-action] fieldset').disabled,true);h.fill('[name=reason]','Texto alterado por script');h.submit('[data-os-form=appointment-action]');await flush();assert.equal(posts(h).length,2);assert.deepEqual(plain(posts(h)[0].data),plain(posts(h)[1].data));assert.equal(posts(h)[1].data.reason,'Primeira intenção');
});

test('rescheduling excludes only the current booking from availability and submits the selected server slot',async t=>{
  let a=record();const h=await fixture(t,{respond:responder({on:(path,method,data)=>{if(path.endsWith('/appointments/'+appointmentId)&&method==='GET')return a;if(path.endsWith('/reschedule')&&method==='POST'){a={...a,start_at:data.start_at,end_at:'2030-04-05T13:30:00Z'};return a;}}})});await openDetail(h);h.click('[data-os-action=reschedule-appointment]');await flush();
  const request=h.calls.find(call=>call.path.includes('/availability?')),q=new URL(request.path,'https://orion.test').searchParams;assert.equal(q.get('appointment_id'),appointmentId);assert.equal(q.get('professional_id'),professionalId);assert.deepEqual(q.getAll('service_ids'),[serviceId]);assert.equal([...h.document.querySelector('#osRescheduleSlot').options].some(option=>option.value==='2030-04-05T12:00:00Z'),false);
  h.fill('#osRescheduleSlot','2030-04-05T13:00:00Z');h.submit('[data-os-form=appointment-action]');await flush();assert.equal(posts(h).length,1);assert.equal(posts(h)[0].data.start_at,'2030-04-05T13:00:00Z');assert.equal(posts(h)[0].data.expected_updated_at,version);assert.equal('professional_id' in posts(h)[0].data,false);
});

test('a stale availability response cannot restore slots for a previous reschedule date',async t=>{
  const pending=deferred();const h=await fixture(t,{respond:responder({on:path=>{if(path.includes('/availability?')){const day=new URL(path,'https://orion.test').searchParams.get('day');if(day==='2030-04-05')return pending.promise;return {slots:['2030-04-06T14:00:00Z'],timezone:'America/Sao_Paulo'};}}})});await openDetail(h);h.click('[data-os-action=reschedule-appointment]');await flush();await change(h,'[data-os-form=appointment-action] [name=day]','2030-04-06');pending.resolve({slots:['2030-04-05T13:00:00Z']});await flush();assert.deepEqual([...h.document.querySelector('#osRescheduleSlot').options].map(option=>option.value),['','2030-04-06T14:00:00Z']);
});

for(const [action,status] of [['no-show','no_show'],['complete','completed']])test(action+' uses lifecycle guard while completion remains separate from payment',async t=>{
  let a=record();const h=await fixture(t,{respond:responder({on:(path,method)=>{if(path.endsWith('/appointments/'+appointmentId)&&method==='GET')return a;if(path.endsWith('/'+action)&&method==='POST'){a=record({status,allowed_actions:[]});return a;}}})});await openDetail(h);h.click('[data-os-action='+(action==='complete'?'complete-appointment':'no-show-appointment')+']');await flush();h.submit('[data-os-form='+(action==='complete'?'complete-appointment':'appointment-action')+']');await flush();const write=posts(h)[0];assert.equal(write.data.expected_updated_at,version);assert.equal(write.data.expected_status,'confirmed');assert.match(write.path,new RegExp('/management/appointments/'+appointmentId+'/'+action+'$'));assert.equal(h.calls.some(call=>call.path.includes('/finance')&&call.method!=='GET'),false);if(action==='complete')assert.match(h.document.getElementById('osDialog').textContent,/recebimento.*separadamente/s);
});

test('closing the drawer restores focus and Escape is blocked only while a mutation is in flight',async t=>{
  const pending=deferred();const h=await fixture(t,{respond:responder({on:(path,method)=>path.endsWith('/cancel')&&method==='POST'?pending.promise:undefined})});
  const row=h.document.querySelector('[data-os-appointment]');row.focus();await openDetail(h);let cancel=new h.window.Event('cancel',{cancelable:true});h.document.getElementById('osDialog').dispatchEvent(cancel);assert.equal(h.document.getElementById('osDialog').open,false);assert.equal(h.document.activeElement,row);
  await openDetail(h);h.click('[data-os-action=cancel-appointment]');h.click('[name=requested_by][value=customer]');h.submit('[data-os-form=appointment-action]');await flush();h.document.getElementById('osDialog').dispatchEvent(new h.window.Event('cancel',{cancelable:true}));assert.equal(h.document.getElementById('osDialog').open,true);pending.reject(Object.assign(new Error('Não permitido'),{status:422}));await flush();h.document.getElementById('osDialog').dispatchEvent(new h.window.Event('cancel',{cancelable:true}));assert.equal(h.document.getElementById('osDialog').open,false);
});

test('agenda tabs provide arrow navigation and a single tab stop per tab group',async t=>{
  const h=await fixture(t,{respond:responder()});const list=h.document.getElementById('osAgendaView-list');list.focus();list.dispatchEvent(new h.window.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true,cancelable:true}));await flush();assert.equal(h.document.getElementById('osAgendaView-calendar').getAttribute('aria-selected'),'true');assert.equal(h.document.querySelectorAll('.os-agenda-views [tabindex="0"]').length,1);
  const selected=h.document.querySelector('.os-week-grid [aria-selected=true]');selected.focus();selected.dispatchEvent(new h.window.KeyboardEvent('keydown',{key:'End',bubbles:true,cancelable:true}));await flush();assert.equal(h.document.querySelectorAll('.os-week-grid [tabindex="0"]').length,1);assert.equal(h.document.activeElement.dataset.osDay,h.document.querySelector('.os-week-grid [aria-selected=true]').dataset.osDay);
});

test('Google failure is independent of agenda and does not trigger connection or retry writes',async t=>{
  const h=await fixture(t,{respond:responder({on:path=>{if(path.endsWith('/google/status'))throw new Error('Integração temporariamente indisponível');}})});assert.ok(h.document.querySelector('[data-os-appointment]'));assert.match(h.document.getElementById('osAgendaGoogleLabel').textContent,/Não foi possível/);h.click('[data-os-action=agenda-google]');await flush();assert.match(h.document.getElementById('osDialog').textContent,/Não foi possível consultar/);assert.equal(posts(h).length,0);
});

test('unconfigured Google and viewer capabilities have no operational controls',async t=>{
  for(const google of [googleOff,{...googleOn,capabilities:{}}]){const h=await fixture(t,{respond:responder({google})});h.click('[data-os-action=agenda-google]');await flush();for(const action of ['google-connect','google-calendars','google-revoke','google-retry'])assert.equal(h.document.querySelector('[data-os-action='+action+']'),null);assert.equal(posts(h).length,0);}
});

test('Google connection is explicit POST only after disclosure and rejects an unexpected authorization host',async t=>{
  const google={...googleOn,connected:false,sync_status:'not_connected'};const h=await fixture(t,{respond:responder({google,on:(path,method)=>path.endsWith('/google/connect')&&method==='POST'?{authorization_url:'https://untrusted.example.invalid/authorize'}:undefined})});h.click('[data-os-action=agenda-google]');await flush();h.click('[data-os-action=google-connect]');await flush();assert.equal(posts(h).length,0);assert.match(h.document.getElementById('osDialog').textContent,/Telefone, observações internas e motivo do cancelamento ficam no Orion/);h.submit('[data-os-form=google-action]');await flush();assert.equal(posts(h).length,1);assert.equal(posts(h)[0].path.endsWith('/google/connect'),true);assert.match(h.document.querySelector('.os-form-message').textContent,/endereço de autorização.*não foi reconhecido/);assert.equal(h.calls.some(call=>call.path.endsWith('/google/connect')&&call.method==='GET'),false);
});

test('Google calendar activation requires explicit selection and preserves a locked canonical calendar',async t=>{
  const google={...googleOn,selection_pending:true,sync_status:'choose_calendar',calendar_change_locked:true,capabilities:{can_select_calendar:true}};
  const h=await fixture(t,{respond:responder({google,on:(path,method)=>{if(path.endsWith('/google/calendars'))return {items:[{id:google.calendar_id,name:'Equipe sintética',selectable:true},{id:'other@example.invalid',name:'Outro calendário',selectable:false}],selection_pending:true,calendar_change_locked:true};if(path.endsWith('/google/calendar')&&method==='POST')return google;}})});h.click('[data-os-action=agenda-google]');await flush();h.click('[data-os-action=google-calendars]');await flush();assert.equal(posts(h).length,0);assert.equal([...h.document.querySelector('[name=calendar_id]').options].some(option=>option.value==='other@example.invalid'),false);assert.match(h.document.getElementById('osDialog').textContent,/calendário já vinculado será mantido/);h.submit('[data-os-form=google-calendar]');await flush();assert.equal(posts(h).length,1);assert.deepEqual(plain(posts(h)[0].data),{calendar_id:google.calendar_id});
});

test('OAuth return for the selected tenant opens calendar choice without activating or connecting automatically',async t=>{
  const google={...googleOn,selection_pending:true,sync_status:'choose_calendar',capabilities:{can_select_calendar:true}};const status=deferred();let reads=0;
  const h=await makeSuite({initialRoute:'agenda',respond:responder({google,on:path=>{if(path.endsWith('/google/status')&&++reads===1)return status.promise;if(path.endsWith('/google/calendars'))return {items:[{id:google.calendar_id,name:'Equipe sintética',selectable:true}],selection_pending:true,calendar_change_locked:false};}})});t.after(()=>h.close());h.window.history.replaceState({},'','/panel?aba=agenda&empresa='+tenantA+'&google=choose');status.resolve(google);await flush();assert.ok(h.document.querySelector('[data-os-form=google-calendar]'));assert.equal(posts(h).length,0);assert.equal(new URL(h.window.location.href).searchParams.has('google'),false);assert.equal(h.document.querySelector('[name=calendar_id]').value,'');
});

test('late Google appointment sync cannot replace another detail and private contact actions only copy',async t=>{
  const late=deferred(),b=record({id:'appointment-second',customer_display_name:'Pessoa B'}),copied=[];
  const h=await fixture(t,{respond:responder({items:[record(),b],on:path=>path.endsWith('/google/appointments/'+appointmentId+'/status')?late.promise:undefined})});h.window.navigator.clipboard.writeText=async value=>copied.push(value);await openDetail(h);h.click('[data-os-action=appointment-copy-phone]');await flush();h.click('[data-os-action=appointment-copy-protocol]');await flush();assert.deepEqual(copied,['+5511999990000','ORION-SYNTHETIC']);h.click('[data-os-action=close-dialog]');await openDetail(h,b.id);late.resolve({status:'failed',last_error:'Erro do primeiro compromisso',sync_enabled:true});await flush();assert.doesNotMatch(h.document.getElementById('osDialog').textContent,/Erro do primeiro compromisso/);assert.equal(posts(h).length,0);
});

test('persisted hold expiration has a distinct label and system cancellation alone does not imply expiration',async t=>{
  const expired=record({id:'expired-id',status:'cancelled',allowed_actions:[],cancellation:{event:'hold_expired',requested_by:'system'}}),cancelled=record({id:'system-id',status:'cancelled',allowed_actions:[],cancellation:{event:'cancelled',requested_by:'system'}});
  const h=await fixture(t,{respond:responder({items:[expired,cancelled]})});assert.match(h.document.querySelector('[data-os-appointment=expired-id]').textContent,/Reserva expirada/);assert.doesNotMatch(h.document.querySelector('[data-os-appointment=system-id]').textContent,/expirada/);await openDetail(h,'expired-id');assert.match(h.document.getElementById('osDialog').textContent,/não foi confirmada dentro do prazo/);
});

test('Google primary alias accepts only canonical calendars explicitly marked selectable by the server',async t=>{
  const google={...googleOn,calendar_id:'primary',selection_pending:true,sync_status:'choose_calendar',calendar_change_locked:true,capabilities:{can_select_calendar:true}};
  const h=await fixture(t,{respond:responder({google,on:(path,method)=>{if(path.endsWith('/google/calendars'))return {calendar_id:'primary',calendar_change_locked:true,items:[{id:'canonical@example.invalid',name:'Meu calendário',primary:true,selectable:true},{id:'blocked@example.invalid',name:'Outro calendário',selectable:false}]};if(path.endsWith('/google/calendar')&&method==='POST')return google;}})});h.click('[data-os-action=agenda-google]');await flush();h.click('[data-os-action=google-calendars]');await flush();const select=h.document.querySelector('[name=calendar_id]');assert.equal(select.value,'canonical@example.invalid');assert.equal([...select.options].some(option=>option.value==='blocked@example.invalid'),false);h.submit('[data-os-form=google-calendar]');await flush();assert.equal(posts(h)[0].data.calendar_id,'canonical@example.invalid');
});

test('Google error codes become bounded Portuguese guidance and never expose an unexpected raw provider error',async t=>{
  const google={...googleOn,sync_status:'attention',last_error:'google_authorization_expired'};
  const h=await fixture(t,{respond:responder({google,on:path=>path.includes('/google/appointments/')?{configured:true,connection_status:'active',status:'failed',sync_enabled:true,last_error:'raw-provider-private-secret'}:undefined})});h.click('[data-os-action=agenda-google]');await flush();assert.match(h.document.getElementById('osDialog').textContent,/A autorização venceu/);assert.doesNotMatch(h.document.getElementById('osDialog').textContent,/google_authorization_expired/);h.click('[data-os-action=close-dialog]');await openDetail(h);assert.doesNotMatch(h.document.getElementById('osDialog').textContent,/raw-provider-private-secret/);assert.match(h.document.getElementById('osDialog').textContent,/Confira a conexão ou peça ao suporte/);
});

test('Google historical exclusion and absent credentials are honest distinct conditions',async t=>{
  for(const data of [{status:'outside_initial_sync',configured:true,sync_enabled:true,connection_status:'active'},{status:'pending',configured:false,sync_enabled:true,connection_status:'not_connected'}]){const h=await fixture(t,{respond:responder({on:path=>path.includes('/google/appointments/')?data:undefined})});await openDetail(h);const text=h.document.getElementById('osAppointmentGoogle').textContent;if(data.status==='outside_initial_sync')assert.match(text,/Anterior à conexão.*não faz parte da sincronização inicial/s);else assert.match(text,/ainda não está configurada/);assert.equal(posts(h).length,0);}
});
test('toque em agendamento preserva o cartão como origem mesmo se Safari mantiver foco na área inteira',async t=>{
  const h=await fixture(t,{respond:responder()});
  const panel=h.document.getElementById('osPageBody');
  const row=h.document.querySelector('[data-os-appointment="'+appointmentId+'"]');
  assert.equal(panel.tabIndex,0,'tabpanel precisa continuar acessível por teclado');
  panel.focus(); // Safari pode não focar o botão no toque, deixando o tabpanel ativo.
  assert.equal(h.document.activeElement,panel);
  h.click('[data-os-appointment="'+appointmentId+'"]');
  await flush();
  assert.equal(h.document.activeElement.id,'osDialogTitle','título do diálogo continua anunciado pelo foco');
  h.click('[data-os-action=close-dialog]');
  await flush();
  assert.equal(h.document.activeElement,row,'foco volta ao botão real, não à área inteira');
  assert.notEqual(h.document.activeElement,panel);
  assert.equal(posts(h).length,0);
});

test('CSS diferencia toque de teclado apenas no tabpanel, na contagem e no título do modal',async ()=>{
  const css=await readFile(new URL('../app/static/orion-suite.css',import.meta.url),'utf8');
  for(const selector of [
    '.os-main[data-os-current-route=agenda] #osPageBody',
    '.os-main[data-os-current-route=agenda] #osAgendaCount',
    '.os-appointment-sheet-header #osDialogTitle',
  ]){
    assert.ok(css.includes(selector+':focus:not(:focus-visible)'),'sem anel nativo de toque em '+selector);
    assert.ok(css.includes(selector+':focus-visible'),'anel preservado no teclado em '+selector);
  }
  assert.match(css,/\.os-agenda-item:focus-visible\{outline:3px solid var\(--os-primary\)/,
    'foco de teclado de cada cartão não pode ser removido');
  assert.match(css,/#osDialogTitle:focus-visible\{outline:3px solid var\(--os-primary\);outline-offset:2px\}/);
});

test('busca da agenda reserva espaço para a lupa sem sobrepor o texto',async t=>{
  const h=await fixture(t,{respond:responder()});
  const input=h.document.getElementById('osAgendaSearch');
  const lens=input.closest('.os-agenda-search')?.querySelector('.os-icon');
  assert.ok(lens,'lupa existe');
  assert.equal(lens.getAttribute('aria-hidden'),'true');
  assert.equal(input.getAttribute('placeholder'),'Nome, telefone ou protocolo');
  const css=await readFile(new URL('../app/static/orion-suite.css',import.meta.url),'utf8');
  const svgRule=css.match(/\.os-agenda-search>\.os-icon\{([^}]*)\}/)?.[1];
  const inputRule=css.match(/\.os-main \.os-agenda-search input#osAgendaSearch\{([^}]*)\}/)?.[1];
  assert.ok(svgRule && inputRule,'estilos específicos da agenda presentes');
  for(const part of ['top:50%','left:14px','transform:translateY(-50%)','width:20px','height:20px','pointer-events:none'])
    assert.ok(svgRule.includes(part),'propriedade da lupa: '+part);
  assert.ok(inputRule.includes('padding-left:46px'),'o ID vence o padding do input genérico');
  assert.equal(h.errors.length,0);
});

test('monitor e atualizar têm quadrados 44px, ícones 20px e continuam funcionais',async t=>{
  const h=await fixture(t,{respond:responder()});
  const expand=h.document.querySelector('[data-os-action=agenda-tablet]');
  const refresh=h.document.querySelector('.os-agenda-view-row [data-os-action=agenda-reload]');
  assert.ok(expand && refresh,'controles principais existem');
  assert.equal(expand.getAttribute('aria-label'),'Ampliar agenda');
  assert.equal(refresh.getAttribute('aria-label'),'Atualizar agendamentos');
  assert.equal(expand.querySelector('svg.os-icon').getAttribute('aria-hidden'),'true');
  assert.equal(refresh.querySelector('svg.os-icon').getAttribute('aria-hidden'),'true');
  const css=await readFile(new URL('../app/static/orion-suite.css',import.meta.url),'utf8');
  const start=css.indexOf('@container agenda (max-width:520px){');
  assert.ok(start!==-1,'regras compactas presentes');
  const compact=css.slice(start,css.indexOf('\n}',start));
  const buttonRule=compact.match(/\.os-agenda-expand,\.os-agenda-view-row>\.os-button\[data-os-action=agenda-reload\]\{([^}]*)\}/)?.[1];
  const svgRule=compact.match(/\.os-agenda-expand>\.os-icon,\.os-agenda-view-row>\.os-button\[data-os-action=agenda-reload\]>\.os-icon\{([^}]*)\}/)?.[1];
  assert.ok(buttonRule && svgRule,'as duas ações compartilham escala');
  for(const part of ['width:44px','min-width:44px','max-width:44px','height:44px','min-height:44px','max-height:44px','gap:0','padding:0','align-items:center','justify-content:center'])
    assert.ok(buttonRule.includes(part),'botão compacto: '+part);
  for(const part of ['width:20px','height:20px','flex:0 0 20px'])
    assert.ok(svgRule.includes(part),'SVG compacto: '+part);
  const before=h.calls.length;
  h.click('.os-agenda-view-row [data-os-action=agenda-reload]');
  await flush();
  assert.ok(h.calls.length>before,'atualização consulta novamente a agenda');
  assert.equal(h.calls.some(call=>call.method!=='GET'),false,'atualizar não altera dados');
  h.click('[data-os-action=agenda-tablet]');
  await flush();
  assert.equal(h.document.querySelector('[data-os-action=agenda-tablet-exit]')?.getAttribute('aria-label'),'Sair da tela ampliada');
  h.click('[data-os-action=agenda-tablet-exit]');
  await flush();
  assert.ok(h.document.querySelector('[data-os-action=agenda-tablet]'),'ampliar e sair permanecem disponíveis');
  assert.equal(h.errors.length,0);
});

test('ícone atualizar usa arcos equilibrados e setas do mesmo padrão da suíte',async ()=>{
  const source=await readFile(new URL('../app/static/orion-suite.js',import.meta.url),'utf8');
  const refresh=source.match(/refresh:'([^']+)'/g)||[];
  assert.deepEqual(refresh,[
    "refresh:'M20 11a8.1 8.1 0 0 0-15.5-2M4 4v5h5M4 13a8.1 8.1 0 0 0 15.5 2M20 20v-5h-5'"
  ],'não criar variantes divergentes para o mesmo ícone de atualizar');
  assert.match(source,/const icon = name => '<svg class="os-icon" viewBox="0 0 24 24"/,
    'mantém o SVG e o viewBox padronizados do Orion');
});

test('detalhes usam botões só de ícone sem estilos de botões com texto',async t=>{
  const h=await fixture(t,{respond:responder()});
  await openDetail(h);
  const dialog=h.document.getElementById('osDialog');
  const close=dialog.querySelector('.os-appointment-sheet-header>.os-close');
  const refresh=dialog.querySelector('.os-appointment-section-title>.os-appointment-google-refresh');
  assert.ok(close && refresh,'fechar e atualizar visíveis nos detalhes');
  assert.equal(close.dataset.osAction,'close-dialog');
  assert.equal(refresh.dataset.osAction,'appointment-google-refresh');
  assert.equal(close.getAttribute('aria-label'),'Fechar detalhes do agendamento');
  assert.equal(refresh.getAttribute('aria-label'),'Atualizar sincronização deste agendamento');
  for(const btn of [close,refresh]){
    assert.equal(btn.tagName,'BUTTON');
    assert.equal(btn.type,'button');
    assert.equal(btn.classList.contains('os-icon-button'),true);
    assert.equal(btn.classList.contains('os-button'),false,'não herda padding e estilo de botão de texto');
    assert.equal(btn.querySelector('svg.os-icon')?.getAttribute('aria-hidden'),'true');
  }
  const before=h.calls.filter(c=>c.path.includes('/google/appointments/')).length;
  h.click('.os-appointment-google-refresh');await flush();
  const after=h.calls.filter(c=>c.path.includes('/google/appointments/')).length;
  assert.ok(after>before,'o botão ainda atualiza a sincronização');
  assert.equal(posts(h).length,0,'atualização apenas consulta');
  h.click('.os-appointment-sheet-header>.os-close');await flush();
  assert.equal(dialog.open,false,'fechar permanece funcional');
  assert.equal(posts(h).length,0);
  assert.equal(h.errors.length,0);
});

test('CSS mantém X e atualizar quadrados, centrados e adaptados ao celular',async ()=>{
  const css=await readFile(new URL('../app/static/orion-suite.css',import.meta.url),'utf8');
  const close='.os-dialog.os-appointment-sheet .os-appointment-sheet-header>.os-close';
  const refresh='.os-dialog.os-appointment-sheet .os-appointment-section-title>.os-appointment-google-refresh';
  const base=css.match(/\.os-dialog\.os-appointment-sheet \.os-appointment-sheet-header>\.os-close,\s*\.os-dialog\.os-appointment-sheet \.os-appointment-section-title>\.os-appointment-google-refresh\{([^}]*)\}/)?.[1];
  assert.ok(base,'estilo específico deve superar .os-dialog .os-close');
  for(const rule of ['position:static','display:inline-flex','box-sizing:border-box','align-items:center','justify-content:center','flex:0 0 44px','width:44px','height:44px','min-width:44px','min-height:44px','padding:0','gap:0','border-radius:12px']){
    assert.ok(base.includes(rule),rule);
  }
  const icons=css.match(/\.os-dialog\.os-appointment-sheet \.os-appointment-sheet-header>\.os-close>\.os-icon,\s*\.os-dialog\.os-appointment-sheet \.os-appointment-section-title>\.os-appointment-google-refresh>\.os-icon\{([^}]*)\}/)?.[1];
  for(const rule of ['display:block','flex:0 0 20px','width:20px','height:20px'])
    assert.ok(icons?.includes(rule),'icon size: '+rule);
  const mobile=css.match(/@media\(max-width:639px\)\{\s*\.os-dialog\.os-sheet\.os-appointment-sheet[\s\S]*?\.os-dialog\.os-appointment-sheet \.os-appointment-sheet-header>\.os-close,\s*\.os-dialog\.os-appointment-sheet \.os-appointment-section-title>\.os-appointment-google-refresh\{([^}]*)\}/)?.[1];
  assert.ok(mobile,'regras compactas dos dois botões');
  for(const rule of ['flex-basis:48px','width:48px','height:48px','min-width:48px','min-height:48px'])
    assert.ok(mobile.includes(rule),rule);
  assert.match(css,/\.os-icon-button:focus-visible|\.os-icon-button:focus-visible/,
    'anel de foco por teclado continua disponível');
});
