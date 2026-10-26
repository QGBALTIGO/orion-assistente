import test from 'node:test';
import assert from 'node:assert/strict';
import { makeSuite, flush, defaultResponse, tenantA } from './harness.mjs';

const professionalId='51000000-0000-4000-8000-000000000001';
const mondayId='51000000-0000-4000-8000-000000000002';
const laterMondayId='51000000-0000-4000-8000-000000000003';
const wednesdayId='51000000-0000-4000-8000-000000000004';
const sundayId='51000000-0000-4000-8000-000000000005';
const pauseId='51000000-0000-4000-8000-000000000006';

function scheduleFixture(role='owner'){
  let hours=[
    {id:sundayId,professional_id:professionalId,weekday:6,start_time:'10:00:00',end_time:'13:00:00'},
    {id:wednesdayId,professional_id:professionalId,weekday:2,start_time:'14:00:00',end_time:'20:00:00'},
    {id:laterMondayId,professional_id:professionalId,weekday:0,start_time:'19:00:00',end_time:'21:00:00'},
    {id:mondayId,professional_id:professionalId,weekday:0,start_time:'09:00:00',end_time:'18:00:00'},
  ];
  let breaks=[{id:pauseId,working_hour_id:mondayId,start_time:'12:00:00',end_time:'13:00:00'}];
  const respond=(path,method,payload)=>{
    if(path.endsWith('/professionals'))return [{id:professionalId,name:'Guilherme',active:true,commission_percent:0,specialty:'Barbeiro'}];
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

async function openSchedule(h){
  await flush();
  h.click('[data-os-hours="'+professionalId+'"]');
  await flush();
  return h.document.querySelector('#osDialog');
}

test('hours are Monday to Sunday and same-day shifts ordered by start',async t=>{
  const h=await scheduleFixture();t.after(()=>h.close());
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
  assert.deepEqual(saved[0].data,{professional_id:professionalId,weekday:0,start_time:'10:00',end_time:'18:00'});
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
  h.fill('[data-os-form=hours-edit] [name=end_time]','20:00');
  h.submit('[data-os-form=hours-edit]');await flush();
  assert.match(h.document.querySelector('[data-os-form=hours-edit] .os-form-message').textContent,/sobrepõe/);
  assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
  h.fill('[data-os-form=hours-edit] [name=end_time]','18:00');
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
  assert.deepEqual(writes[0].data,{working_hour_id:mondayId,start_time:'12:30',end_time:'13:30'});
  assert.match(h.document.querySelector('[data-os-hours-break="'+pauseId+'"]').textContent,/12:30/);
  h.click('[data-os-pause="'+wednesdayId+'"]');
  const add=h.document.querySelector('[data-os-form=pause]');
  assert.ok(add,'adding pause must be accessible without closing schedule');
  h.fill('[data-os-form=pause] [name=start_time]','16:00');
  h.fill('[data-os-form=pause] [name=end_time]','17:00');
  h.submit('[data-os-form=pause]');await flush();
  const created=h.calls.filter(c=>c.method==='POST');
  assert.equal(created.length,1);
  assert.deepEqual(created[0].data,{working_hour_id:wednesdayId,start_time:'16:00',end_time:'17:00'});
  assert.equal(h.document.querySelector('[data-os-form=pause]'),null);
  assert.equal(h.errors.length,0);
});

test('viewers can inspect all days but cannot edit, create or remove schedule periods',async t=>{
  const h=await scheduleFixture('viewer');t.after(()=>h.close());
  const dialog=await openSchedule(h);
  assert.equal(dialog.querySelectorAll('.os-hours-day').length,7);
  assert.equal(dialog.querySelectorAll('[data-os-edit-hour],[data-os-edit-break],[data-os-hours-add],[data-os-delete-hour],[data-os-pause]').length,0);
  assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
});
