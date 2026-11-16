import test from 'node:test';
import assert from 'node:assert/strict';
import { makeSuite, flush, defaultResponse, tenantA } from './harness.mjs';

const professionalId='51000000-0000-4000-8000-000000000001';
const mondayId='51000000-0000-4000-8000-000000000002';
const laterMondayId='51000000-0000-4000-8000-000000000003';
const wednesdayId='51000000-0000-4000-8000-000000000004';
const sundayId='51000000-0000-4000-8000-000000000005';
const pauseId='51000000-0000-4000-8000-000000000006';

function scheduleFixture(role='owner',withLegacyDuplicate=false,options={}){
  let hours=[
    {id:sundayId,professional_id:professionalId,weekday:6,start_time:'10:00:00',end_time:'13:00:00'},
    {id:wednesdayId,professional_id:professionalId,weekday:2,start_time:'14:00:00',end_time:'20:00:00'},
    ...(withLegacyDuplicate?[{id:laterMondayId,professional_id:professionalId,weekday:0,start_time:'19:00:00',end_time:'21:00:00'}]:[]),
    {id:mondayId,professional_id:professionalId,weekday:0,start_time:'09:00:00',end_time:'18:00:00'},
  ];
  let breaks=[{id:pauseId,working_hour_id:mondayId,start_time:'12:00:00',end_time:'13:00:00'}];
  const professionals=options.professionals||[{id:professionalId,name:'Guilherme',active:true,commission_percent:0,specialty:'Barbeiro'}];
  hours.push(...(options.extraHours||[]));
  const policiesByProfessional=Object.fromEntries(professionals.map(person=>[person.id,Array.from({length:7},(_,weekday)=>({weekday,mode:'automatic',start_times:[],version:0,...(options.professionalPolicies?.[person.id]||options.policies||[]).find(p=>p.weekday===weekday)}))]));
  const respond=(path,method,payload)=>{
    if(path.endsWith('/professionals'))return structuredClone(professionals);
    if(path.endsWith('/start-policies')){
      if(options.readError)throw options.readError;
      const id=path.split('/').at(-2);
      return {professional_id:id,policies:structuredClone(policiesByProfessional[id])};
    }
    if(/\/start-policies\/\d$/.test(path)&&method==='PUT'){
      if(options.writeError)throw options.writeError;
      const weekday=Number(path.split('/').at(-1)),id=path.split('/').at(-3),previous=policiesByProfessional[id][weekday];
      if(payload.expected_version!==previous.version)throw Object.assign(Error('A configuração mudou. Recarregue os horários salvos.'),{status:409});
      const saved={weekday,mode:payload.mode,start_times:payload.start_times??previous.start_times,version:previous.version+1};
      policiesByProfessional[id][weekday]=saved;return structuredClone(saved);
    }
    if(path.endsWith('/working_hours')){
      if(method==='POST'){hours=[...hours,{...payload,id:'51000000-0000-4000-8000-000000000007'}];return {id:hours.at(-1).id};}
      return structuredClone(hours);
    }
    if(path.endsWith('/working_breaks')){
      if(method==='POST'){breaks=[...breaks,{...payload,id:'51000000-0000-4000-8000-000000000008'}];return {id:breaks.at(-1).id};}
      return structuredClone(breaks);
    }
    if(path.includes('/working_hours/')&&method==='PUT'){
      const id=path.split('/').at(-1);
      hours=hours.map(hour=>hour.id===id?{...hour,...payload}:hour);
      return {id,...payload};
    }
    if(path.includes('/working_breaks/')&&method==='PUT'){
      const id=path.split('/').at(-1);
      breaks=breaks.map(item=>item.id===id?{...item,...payload}:item);
      return {id,...payload};
    }
    return defaultResponse(path);
  };
  return makeSuite({role,initialRoute:'funcionarios',respond});
}

function openStartPolicy(h,weekday=0){
  h.click('[data-os-start-policy-toggle="'+weekday+'"]');
  return h.document.querySelector('[data-os-form="hours-start-policy"]');
}
function selectStartMode(h,mode){h.click('[data-os-start-policy-mode][value="'+mode+'"]');}
function addStart(h,time){h.fill('[data-os-start-time-input]',time);h.click('[data-os-start-time-add]');}
function addedStarts(h){return [...h.document.querySelectorAll('[data-os-start-time-remove]')].map(button=>button.dataset.osStartTimeRemove);}
function policyWrites(h){return h.calls.filter(c=>c.method==='PUT'&&c.path.includes('/start-policies/'));}

async function openSchedule(h){
  await flush();
  h.click('[data-os-hours="'+professionalId+'"]');
  await flush();
  return h.document.querySelector('#osDialog');
}

test('hours are Monday to Sunday and same-day shifts ordered by start',async t=>{
  const h=await scheduleFixture('owner',true);t.after(()=>h.close());
  const dialog=await openSchedule(h);
  assert.deepEqual([...dialog.querySelectorAll('.os-hours-day-name')].map(x=>x.textContent),
    ['Segunda-feira','Terça-feira','Quarta-feira','Quinta-feira','Sexta-feira','Sábado','Domingo']);
  assert.deepEqual([...dialog.querySelectorAll('[data-os-hours-weekday="0"] .os-hours-clock')].map(x=>x.textContent.trim().replace(/\s+/g,' ')),
    ['09:00 – 18:00','19:00 – 21:00']);
  assert.match(dialog.querySelector('[data-os-hours-weekday="1"]').textContent,/Fechado/);
  assert.ok(dialog.classList.contains('os-hours-dialog'));
  assert.equal(dialog.querySelectorAll('[data-os-edit-hour]').length,4);
  assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
  assert.equal(h.errors.length,0);
});

test('professional schedule cards stay isolated from legacy opening-hours overview classes',async t=>{
  const h=await scheduleFixture();t.after(()=>h.close());
  const dialog=await openSchedule(h);
  const card=dialog.querySelector('[data-os-hours-weekday="0"]');
  assert.ok(card.classList.contains('os-staff-hours-day'));
  assert.equal(card.classList.contains('os-hours-day'),false,'legacy two-column settings rule must never target professional cards');
  assert.ok(card.classList.contains('has-hours'));
  const closed=dialog.querySelector('[data-os-hours-weekday="1"]');
  assert.ok(closed.classList.contains('is-closed'));
  assert.equal(closed.querySelector('.os-hours-shift'),null);
  assert.match(closed.textContent,/Fechado/);
  assert.match(dialog.querySelector('[data-os-hours-break="'+pauseId+'"] .os-hours-break-caption').textContent,/Pausa/);
  assert.equal(h.errors.length,0);
});

test('edit shift in place, refresh without leaving modal, and change weekday',async t=>{
  const h=await scheduleFixture();t.after(()=>h.close());
  await openSchedule(h);
  h.click('[data-os-edit-hour="'+mondayId+'"]');
  const form=h.document.querySelector('[data-os-form="hours-edit"]');
  assert.ok(form);assert.equal(form.elements.start_time.value,'09:00');assert.equal(form.elements.weekday.value,'0');
  h.fill('[data-os-form="hours-edit"] [name="start_time"]','10:00');
  h.submit('[data-os-form="hours-edit"]');await flush();
  const saved=h.calls.filter(c=>c.method==='PUT');
  assert.equal(saved.length,1);
  assert.equal(saved[0].path,`/api/v1/tenants/${tenantA}/working_hours/${mondayId}`);
  assert.deepEqual(JSON.parse(JSON.stringify(saved[0].data)),{professional_id:professionalId,weekday:0,start_time:'10:00',end_time:'18:00'});
  assert.ok(h.document.querySelector('#osDialog').open,'schedule stays open for consecutive edits');
  assert.match(h.document.querySelector('[data-os-hours-period="'+mondayId+'"] .os-hours-clock').textContent,/10:00/);

  h.click('[data-os-edit-hour="'+mondayId+'"]');
  h.document.querySelector('[data-os-form=hours-edit] [name=weekday]').value='1';
  h.submit('[data-os-form=hours-edit]');await flush();
  assert.equal(h.calls.filter(c=>c.method==='PUT').length,2);
  assert.ok(h.document.querySelector('[data-os-hours-weekday="1"] [data-os-hours-period="'+mondayId+'"]'));
  assert.equal(h.errors.length,0);
});

test('inline editor refuses overlap and breaks outside a shorter working shift',async t=>{
  const h=await scheduleFixture();t.after(()=>h.close());
  await openSchedule(h);
  h.click('[data-os-edit-hour="'+mondayId+'"]');
  h.document.querySelector('[data-os-form=hours-edit] [name=weekday]').value='2';
  h.submit('[data-os-form=hours-edit]');await flush();
  assert.match(h.document.querySelector('[data-os-form=hours-edit] .os-form-message').textContent,/Já existe/);
  assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
  h.document.querySelector('[data-os-form=hours-edit] [name=weekday]').value='0';
  h.fill('[data-os-form=hours-edit] [name=start_time]','13:30');
  h.submit('[data-os-form=hours-edit]');await flush();
  assert.match(h.document.querySelector('[data-os-form=hours-edit] .os-form-message').textContent,/pausas/);
  assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
  h.click('[data-os-hours-cancel]');await flush();
  assert.equal(h.document.querySelector('[data-os-form=hours-edit]'),null);
});

test('edit existing break, validate bounds and create a new break within a shift',async t=>{
  const h=await scheduleFixture();t.after(()=>h.close());
  await openSchedule(h);
  h.click('[data-os-edit-break="'+pauseId+'"]');
  const form=h.document.querySelector('[data-os-form=pause-edit]');
  assert.equal(form.elements.start_time.value,'12:00');
  h.fill('[data-os-form=pause-edit] [name=start_time]','12:30');
  h.fill('[data-os-form=pause-edit] [name=end_time]','13:30');
  h.submit('[data-os-form=pause-edit]');await flush();
  const writes=h.calls.filter(c=>c.method==='PUT');
  assert.equal(writes.length,1);assert.equal(writes[0].path,`/api/v1/tenants/${tenantA}/working_breaks/${pauseId}`);
  assert.deepEqual(JSON.parse(JSON.stringify(writes[0].data)),{working_hour_id:mondayId,start_time:'12:30',end_time:'13:30'});
  assert.match(h.document.querySelector('[data-os-hours-break="'+pauseId+'"]').textContent,/12:30/);
  h.click('[data-os-pause="'+wednesdayId+'"]');
  const add=h.document.querySelector('[data-os-form=pause]');
  assert.ok(add,'adding pause must be accessible without closing schedule');
  h.fill('[data-os-form=pause] [name=start_time]','16:00');
  h.fill('[data-os-form=pause] [name=end_time]','17:00');
  h.submit('[data-os-form=pause]');await flush();
  const created=h.calls.filter(c=>c.method==='POST');
  assert.equal(created.length,1);
  assert.deepEqual(JSON.parse(JSON.stringify(created[0].data)),{working_hour_id:wednesdayId,start_time:'16:00',end_time:'17:00'});
  assert.equal(h.document.querySelector('[data-os-form=pause]'),null);
  assert.equal(h.errors.length,0);
});


test('closed days accept new hours and pause defaults stay within chosen shift',async t=>{
  const h=await scheduleFixture();t.after(()=>h.close());
  await openSchedule(h);
  assert.ok(h.document.querySelector('[data-os-hours-add="1"]'));
  assert.equal(h.document.querySelector('[data-os-hours-add="0"]'),null,'already configured days should not offer duplicate shifts');
  h.click('[data-os-hours-add="1"]');
  const form=h.document.querySelector('[data-os-form=hours]');
  assert.ok(form);assert.equal(form.elements.weekday.value,'1');
  h.fill('[data-os-form=hours] [name=start_time]','10:00');
  h.fill('[data-os-form=hours] [name=end_time]','17:00');
  h.submit('[data-os-form=hours]');await flush();
  const post=h.calls.find(c=>c.method==='POST');
  assert.ok(post);assert.equal(post.data.weekday,1);
  assert.ok(h.document.querySelector('[data-os-hours-weekday="1"] .os-hours-clock'));
  h.click('[data-os-pause="'+wednesdayId+'"]');
  const add=h.document.querySelector('[data-os-form=pause]');
  assert.ok(add);
  assert.ok(add.elements.start_time.value >= '14:00' && add.elements.end_time.value <= '20:00');
  assert.ok(add.elements.start_time.value < add.elements.end_time.value);
  h.click('[data-os-hours-cancel]');await flush();
  assert.equal(h.document.querySelector('[data-os-form=pause]'),null);
});

test('viewers can inspect all days but cannot edit, create or remove schedule periods',async t=>{
  const h=await scheduleFixture('viewer');t.after(()=>h.close());
  const dialog=await openSchedule(h);
  assert.equal(dialog.querySelectorAll('.os-staff-hours-day').length,7);
  assert.equal(dialog.querySelectorAll('[data-os-edit-hour],[data-os-edit-break],[data-os-hours-add],[data-os-delete-hour],[data-os-pause]').length,0);
  assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
});

test('start policies are collapsed, automatic and shared by all legacy periods of one day',async t=>{
  const h=await scheduleFixture('owner',true);t.after(()=>h.close());
  const dialog=await openSchedule(h);
  assert.equal(dialog.querySelectorAll('[data-os-start-policy-toggle]').length,3);
  assert.equal(dialog.querySelectorAll('[data-os-hours-weekday="0"] [data-os-start-policy-toggle]').length,1);
  assert.equal(dialog.querySelector('[data-os-hours-weekday="1"] [data-os-start-policy-toggle]'),null);
  assert.equal(dialog.querySelector('[data-os-form="hours-start-policy"]'),null);
  assert.equal(dialog.querySelector('[data-os-start-policy-toggle="0"]').getAttribute('aria-expanded'),'false');
  assert.match(dialog.querySelector('[data-os-start-policy-toggle="0"]').textContent,/Automático/);
  assert.equal(h.calls.filter(c=>c.path.endsWith('/start-policies')).length,1,'one read returns all weekdays');
  openStartPolicy(h);selectStartMode(h,'manual');addStart(h,'19:40');
  assert.deepEqual(addedStarts(h),['19:40'],'a later legacy period belongs to the same day');
  h.fill('[data-os-start-time-input]','18:30');h.click('[data-os-start-time-add]');
  assert.match(dialog.querySelector('.os-form-message').textContent,/dentro do expediente/);
  assert.equal(policyWrites(h).length,0);
  assert.equal(h.errors.length,0);
});

test('manual starts are a sorted draft, Enter only adds, collapse preserves and Cancel discards',async t=>{
  const h=await scheduleFixture();t.after(()=>h.close());await openSchedule(h);
  openStartPolicy(h);selectStartMode(h,'manual');addStart(h,'11:00');
  const input=h.fill('[data-os-start-time-input]','09:20');
  const enter=new h.window.KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true});
  input.dispatchEvent(enter);
  assert.equal(enter.defaultPrevented,true);
  assert.deepEqual(addedStarts(h),['09:20','11:00']);
  assert.equal(h.document.activeElement,h.document.querySelector('[data-os-start-time-input]'));
  assert.equal(policyWrites(h).length,0);
  h.click('[data-os-start-policy-toggle="0"]');openStartPolicy(h);
  assert.deepEqual(addedStarts(h),['09:20','11:00']);
  h.click('[data-os-start-policy-cancel="0"]');openStartPolicy(h);
  assert.equal(h.document.querySelector('[data-os-start-policy-mode]:checked').value,'automatic');
  selectStartMode(h,'manual');assert.deepEqual(addedStarts(h),[]);
  assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
  assert.equal(h.errors.length,0);
});

test('saving manual starts includes a pending input and preserves the saved list when returning to automatic',async t=>{
  const h=await scheduleFixture();t.after(()=>h.close());await openSchedule(h);
  openStartPolicy(h);selectStartMode(h,'manual');addStart(h,'10:20');addStart(h,'09:00');
  h.fill('[data-os-start-time-input]','09:40');
  h.submit('[data-os-form="hours-start-policy"]');await flush();
  const first=policyWrites(h)[0];
  assert.equal(first.path,`/api/v1/tenants/${tenantA}/professionals/${professionalId}/start-policies/0`);
  assert.deepEqual(JSON.parse(JSON.stringify(first.data)),{mode:'manual',expected_version:0,start_times:['09:00','09:40','10:20']});
  assert.equal(h.document.querySelector('[data-os-form="hours-start-policy"]'),null);
  assert.match(h.document.querySelector('[data-os-start-policy-toggle="0"]').textContent,/Manual/);
  assert.ok(h.document.querySelector('#osDialog').open);
  openStartPolicy(h);assert.deepEqual(addedStarts(h),['09:00','09:40','10:20']);
  selectStartMode(h,'automatic');h.submit('[data-os-form="hours-start-policy"]');await flush();
  assert.deepEqual(JSON.parse(JSON.stringify(policyWrites(h)[1].data)),{mode:'automatic',expected_version:1});
  assert.match(h.document.querySelector('[data-os-start-policy-toggle="0"]').textContent,/Automático/);
  openStartPolicy(h);selectStartMode(h,'manual');
  assert.deepEqual(addedStarts(h),['09:00','09:40','10:20']);
  assert.equal(h.errors.length,0);
});

test('manual starts reject duplicates, closed intervals and pauses, while allowing the end of a pause',async t=>{
  const h=await scheduleFixture();t.after(()=>h.close());await openSchedule(h);
  openStartPolicy(h);selectStartMode(h,'manual');addStart(h,'09:00');addStart(h,'09:00');
  assert.match(h.document.querySelector('.os-form-message').textContent,/já foi adicionado/);
  assert.deepEqual(addedStarts(h),['09:00']);
  for(const time of ['08:59','18:00']){
    addStart(h,time);assert.match(h.document.querySelector('.os-form-message').textContent,/dentro do expediente/);
  }
  addStart(h,'12:00');assert.match(h.document.querySelector('.os-form-message').textContent,/pausa de 12:00 às 13:00/);
  addStart(h,'12:59');assert.match(h.document.querySelector('.os-form-message').textContent,/pausa/);
  addStart(h,'13:00');assert.deepEqual(addedStarts(h),['09:00','13:00']);
  h.fill('[data-os-start-time-input]','');h.click('[data-os-start-time-add]');
  assert.match(h.document.querySelector('.os-form-message').textContent,/horário válido/);
  assert.equal(policyWrites(h).length,0);assert.equal(h.errors.length,0);
});

test('an empty manual list cannot be saved, including after removing the last start',async t=>{
  const h=await scheduleFixture();t.after(()=>h.close());await openSchedule(h);
  openStartPolicy(h);selectStartMode(h,'manual');
  h.submit('[data-os-form="hours-start-policy"]');await flush();
  assert.match(h.document.querySelector('.os-form-message').textContent,/Adicione pelo menos um horário/);
  addStart(h,'09:00');h.click('[data-os-start-time-remove="09:00"]');
  h.submit('[data-os-form="hours-start-policy"]');await flush();
  assert.match(h.document.querySelector('.os-form-message').textContent,/Adicione pelo menos um horário/);
  assert.match(h.document.querySelector('.os-hours-start-empty').textContent,/Nenhum horário/);
  assert.equal(policyWrites(h).length,0);
});

test('reactivating an inactive list highlights invalid starts until explicitly removed',async t=>{
  const h=await scheduleFixture('owner',false,{policies:[{weekday:0,mode:'automatic',start_times:['08:00','09:00'],version:3}]});
  t.after(()=>h.close());await openSchedule(h);openStartPolicy(h);selectStartMode(h,'manual');
  assert.match(h.document.querySelector('.os-hours-start-chip.is-invalid').textContent,/08:00/);
  h.submit('[data-os-form="hours-start-policy"]');await flush();
  assert.match(h.document.querySelector('.os-form-message').textContent,/Revise os horários 08:00/);
  assert.equal(policyWrites(h).length,0);
  h.click('[data-os-start-time-remove="08:00"]');h.submit('[data-os-form="hours-start-policy"]');await flush();
  assert.deepEqual(JSON.parse(JSON.stringify(policyWrites(h)[0].data)),{mode:'manual',expected_version:3,start_times:['09:00']});
  assert.equal(h.errors.length,0);
});

test('active starts protect shift and pause edits, while inactive lists do not block normal edits',async t=>{
  const h=await scheduleFixture('owner',false,{policies:[{weekday:0,mode:'manual',start_times:['09:00','10:00'],version:2}]});
  t.after(()=>h.close());await openSchedule(h);
  h.click('[data-os-edit-hour="'+mondayId+'"]');h.fill('[data-os-form="hours-edit"] [name=start_time]','09:30');
  h.submit('[data-os-form="hours-edit"]');await flush();
  assert.match(h.document.querySelector('.os-form-message').textContent,/horários de início 09:00 inválidos/);
  assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
  h.click('[data-os-hours-cancel]');h.click('[data-os-pause="'+mondayId+'"]');
  h.fill('[data-os-form="pause"] [name=start_time]','09:00');h.fill('[data-os-form="pause"] [name=end_time]','09:30');
  h.submit('[data-os-form="pause"]');await flush();
  assert.match(h.document.querySelector('.os-form-message').textContent,/horários de início 09:00 inválidos/);
  assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
  h.click('[data-os-hours-cancel]');openStartPolicy(h);selectStartMode(h,'automatic');
  h.submit('[data-os-form="hours-start-policy"]');await flush();
  h.click('[data-os-edit-hour="'+mondayId+'"]');h.fill('[data-os-form="hours-edit"] [name=start_time]','09:30');
  h.submit('[data-os-form="hours-edit"]');await flush();
  assert.equal(h.calls.filter(c=>c.method==='PUT'&&c.path.includes('/working_hours/')).length,1);
  openStartPolicy(h);selectStartMode(h,'manual');
  assert.deepEqual(addedStarts(h),['09:00','10:00']);
  assert.match(h.document.querySelector('.os-hours-start-chip.is-invalid').textContent,/09:00/);
  assert.equal(h.errors.length,0);
});

test('a version conflict preserves the draft and offers an explicit reload before retrying',async t=>{
  const options={writeError:Object.assign(Error('A configuração mudou. Recarregue os horários salvos.'),{status:409})};
  const h=await scheduleFixture('owner',false,options);t.after(()=>h.close());await openSchedule(h);
  openStartPolicy(h);selectStartMode(h,'manual');addStart(h,'09:40');
  h.submit('[data-os-form="hours-start-policy"]');await flush();
  assert.match(h.document.querySelector('.os-form-message').textContent,/configuração mudou/);
  assert.deepEqual(addedStarts(h),['09:40']);
  assert.ok(h.document.querySelector('[data-os-start-policy-reload="0"]'));
  h.click('[data-os-start-policy-toggle="0"]');openStartPolicy(h);
  assert.deepEqual(addedStarts(h),['09:40']);
  options.writeError=null;h.click('[data-os-start-policy-reload="0"]');await flush();
  assert.equal(h.document.querySelector('[data-os-start-policy-mode]:checked').value,'automatic');
  selectStartMode(h,'manual');assert.deepEqual(addedStarts(h),[]);
  addStart(h,'10:00');h.submit('[data-os-form="hours-start-policy"]');await flush();
  assert.equal(policyWrites(h).length,2);
  assert.match(h.document.querySelector('[data-os-start-policy-toggle="0"]').textContent,/Manual/);
  assert.equal(h.errors.length,0);
});

test('failed policy reads are explicit and can recover without blocking the existing shift editor',async t=>{
  const options={readError:Object.assign(Error('Não foi possível consultar a configuração.'),{status:503})};
  const h=await scheduleFixture('owner',false,options);t.after(()=>h.close());const dialog=await openSchedule(h);
  assert.equal(dialog.querySelector('[data-os-start-policy-toggle]'),null);
  assert.match(dialog.querySelector('.os-hours-start-section').textContent,/Não foi possível carregar/);
  assert.doesNotMatch(dialog.querySelector('.os-hours-start-section').textContent,/Automático/);
  h.click('[data-os-edit-hour="'+mondayId+'"]');assert.ok(dialog.querySelector('[data-os-form="hours-edit"]'));
  h.click('[data-os-hours-cancel]');options.readError=null;
  h.click('[data-os-start-policy-retry="0"]');await flush();
  assert.match(dialog.querySelector('[data-os-start-policy-toggle="0"]').textContent,/Automático/);
  assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
  assert.equal(h.errors.length,0);
});

test('viewers can read manual starts without receiving an edit form or mutation controls',async t=>{
  const h=await scheduleFixture('viewer',false,{policies:[{weekday:0,mode:'manual',start_times:['09:00','09:40'],version:1}]});
  t.after(()=>h.close());const dialog=await openSchedule(h);openStartPolicy(h);
  assert.match(dialog.querySelector('.os-hours-start-readonly').textContent,/09:00 · 09:40/);
  assert.equal(dialog.querySelector('[data-os-form="hours-start-policy"]'),null);
  assert.equal(dialog.querySelectorAll('[data-os-start-policy-mode],[data-os-start-time-add],[data-os-start-time-remove],[data-os-start-policy-cancel]').length,0);
  assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
  assert.equal(h.errors.length,0);
});

test('settings summarize each selected professional and immediately reflect a saved start policy',async t=>{
  const otherId='51000000-0000-4000-8000-000000000009';
  const h=await scheduleFixture('owner',false,{
    professionals:[{id:professionalId,name:'Guilherme',active:true},{id:otherId,name:'Ana',active:true}],
    extraHours:[{id:'51000000-0000-4000-8000-000000000010',professional_id:otherId,weekday:0,start_time:'09:00:00',end_time:'18:00:00'}],
    professionalPolicies:{[professionalId]:[{weekday:0,mode:'manual',start_times:['09:00'],version:1}]}
  });
  t.after(()=>h.close());await flush();await h.window.OrionSuite.open('configuracoes');h.click('[data-os-settings="hours"]');await flush();
  assert.equal(h.document.querySelector('#osHoursProfessional').value,professionalId);
  assert.match(h.document.querySelector('.os-hours-mode-summary').textContent,/Manual/);
  const select=h.document.querySelector('#osHoursProfessional');select.value=otherId;select.dispatchEvent(new h.window.Event('change',{bubbles:true}));await flush();
  assert.match(h.document.querySelector('.os-hours-mode-summary').textContent,/Automático/);
  h.click('[data-os-hours="'+otherId+'"]');await flush();openStartPolicy(h);selectStartMode(h,'manual');addStart(h,'09:30');
  h.submit('[data-os-form="hours-start-policy"]');await flush();
  assert.equal(policyWrites(h)[0].path,`/api/v1/tenants/${tenantA}/professionals/${otherId}/start-policies/0`);
  assert.match(h.document.querySelector('.os-hours-mode-summary').textContent,/Manual/);
  assert.equal(h.document.querySelector('#osHoursProfessional').value,otherId);
  assert.equal(h.errors.length,0);
});
