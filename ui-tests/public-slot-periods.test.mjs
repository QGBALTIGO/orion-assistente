import test from 'node:test';
import assert from 'node:assert/strict';
import { flush, deferred } from './harness.mjs';
import { makePublic, catalogue, available, response, publicProfessional } from './public-harness.mjs';

const local = time => '2030-01-07T'+time+':00-04:00';
const times = [
  local('19:15'),local('12:00'),local('09:00'),local('18:00'),local('08:30'),
  local('17:45'),local('11:45'),local('13:30'),local('09:00')
];
const stamp=local('09:00');
const data=()=>{
  const item=catalogue();item.timezone='America/Campo_Grande';
  return item;
};
const listed=h=>[...h.document.querySelectorAll('#time-slots .slot')];
const groups=h=>[...h.document.querySelectorAll('#time-slots .slot-period')];
const groupKeys=h=>groups(h).map(x=>x.dataset.slotPeriod);
const slotsAnswer=(slots=times)=>({...available(slots[0]),slots});

async function stage(h){
  await flush();
  h.click('#services-grid .service-card');
  h.click('#booking-next');
  await flush();
}

test('morning/afternoon/evening are local-time, chronological and de-duplicated',async t=>{
  const h=await makePublic({data:data(),respond:url=>{
    if(url.includes('/availability?'))return response(slotsAnswer());
  }});t.after(()=>h.close());
  await stage(h);
  assert.match(h.document.querySelector('.booking-slots-heading').textContent,/Horários disponíveis/);
  assert.deepEqual(groupKeys(h),['morning','afternoon','evening']);
  assert.deepEqual([...h.document.querySelectorAll('[data-slot-jump]')].map(el=>el.textContent),
    ['Manhã','Tarde','Noite']);
  assert.deepEqual(groups(h).map(section=>[
    section.querySelector('.slot-period-title').textContent,
    section.querySelector('.slot-period-count').textContent,
  ]),[
    ['Manhã','3 horários'],['Tarde','3 horários'],['Noite','2 horários']
  ]);
  assert.deepEqual(groups(h).map(section=>[...section.querySelectorAll('.slot')].map(x=>x.textContent)),
    [['08:30','09:00','11:45'],['12:00','13:30','17:45'],['18:00','19:15']]);
  assert.equal(listed(h).length,8);
  assert.equal(h.document.getElementById('booking-next').disabled,true);
  assert.equal(h.errors.length,0);
});

test('the correct local period wins when an offset crosses a UTC boundary',async t=>{
  const outside=['2030-01-07T15:59:00Z','2030-01-07T16:00:00Z',
    '2030-01-07T21:59:00Z','2030-01-07T22:00:00Z'];
  const h=await makePublic({data:data(),respond:url=>{
    if(url.includes('/availability?'))return response(slotsAnswer(outside));
  }});t.after(()=>h.close());await stage(h);
  assert.deepEqual(groups(h).map(section=>[
    section.dataset.slotPeriod,
    [...section.querySelectorAll('.slot')].map(x=>x.textContent)
  ]),[
    ['morning',['11:59']],['afternoon',['12:00','17:59']],['evening',['18:00']]
  ]);
  assert.equal(h.errors.length,0);
});

test('chips navigate, a selected slot stays focused and booking summary stays intact',async t=>{
  const h=await makePublic({data:data(),respond:url=>{
    if(url.includes('/availability?'))return response(slotsAnswer());
  }});t.after(()=>h.close());await stage(h);
  const root=h.document.getElementById('time-slots');
  const list=root.querySelector('.slots-grid');
  const chip=h.document.querySelector('[data-slot-jump=afternoon]');
  chip.click();
  assert.equal(chip.getAttribute('aria-current'),'location');
  const target=h.document.querySelector('.slot-period[data-slot-period=afternoon] .slot');
  target.focus();target.click();
  assert.equal(target.getAttribute('aria-pressed'),'true');
  assert.equal(h.document.activeElement,target);
  assert.equal(chip.getAttribute('aria-current'),'location');
  assert.equal(h.document.getElementById('booking-next').disabled,false);
  assert.equal(root.querySelector('.slots-grid'),list);
  assert.match(h.document.getElementById('booking-total').textContent,/50 min/);
  h.click('#booking-next');assert.ok(h.document.querySelector('#customer-form'));
  h.click('#booking-back');
  assert.equal(root.querySelector('.slots-grid'),list);
  assert.equal(list.querySelector('.slot[data-slot-start="'+target.dataset.slotStart+'"]'),target);
  assert.equal(target.getAttribute('aria-pressed'),'true');
  assert.equal(h.errors.length,0);
});

test('only nonempty periods render and buttons survive a pending update without photo flicker',async t=>{
  const next=deferred();
  const h=await makePublic({data:data(),respond:url=>{
    if(!url.includes('/availability?'))return undefined;
    if(url.includes('day=2030-01-08'))return next.promise;
    return response(slotsAnswer([local('09:00'),local('10:00'),local('19:00')]));
  }});t.after(()=>h.close());await stage(h);
  assert.deepEqual(groupKeys(h),['morning','evening']);
  const photo=h.document.querySelector('.professional-choice .professional-photo');
  const grid=h.document.querySelector('.slots-grid');
  const first=listed(h)[0],chip=h.document.querySelector('[data-slot-jump=morning]');
  h.click('#booking-day-strip .day-button:nth-child(2)');
  await flush();
  assert.equal(h.document.querySelector('.slots-grid'),grid);
  assert.equal(first.disabled,true);
  assert.equal(chip,h.document.querySelector('[data-slot-jump=morning]'));
  assert.equal(photo,h.document.querySelector('.professional-choice .professional-photo'));
  assert.match(h.document.querySelector('[data-slot-status]').textContent,/Atualizando horários/);
  next.resolve(response(slotsAnswer(['2030-01-08T12:00:00-04:00'])));
  await flush();
  assert.deepEqual(groupKeys(h),['afternoon']);
  assert.equal(h.document.querySelector('.slots-grid'),grid);
  assert.equal(h.document.querySelector('[data-slot-jump=morning]'),null);
  assert.equal(h.document.querySelector('.slot').disabled,false);
  assert.equal(h.document.querySelector('[data-slot-status]'),null);
  assert.equal(photo,h.document.querySelector('.professional-choice .professional-photo'));
  assert.equal(h.errors.length,0);
});

test('no availability clears grouped sections and a preview never queries times',async t=>{
  const h=await makePublic({data:data(),respond:url=>{
    if(!url.includes('/availability?'))return undefined;
    return response({...slotsAnswer([]),availability_reason:'no_working_hours'});
  }});t.after(()=>h.close());await stage(h);
  assert.equal(groups(h).length,0);
  assert.equal(h.document.querySelector('.slot-period-nav'),null);
  assert.match(h.document.getElementById('time-slots').textContent,/não tem expediente/);
  assert.equal(h.document.getElementById('booking-next').disabled,true);
  assert.equal(h.errors.length,0);

  const preview=await makePublic({data:data(),path:'/app/site-preview/10000000-0000-4000-8000-000000000001'});
  t.after(()=>preview.close());await stage(preview);
  assert.equal(preview.document.querySelector('.slot-period'),null);
  assert.match(preview.document.getElementById('time-slots').textContent,/Esta é uma prévia/);
  assert.equal(preview.calls.filter(call=>call.url.includes('/availability?')).length,0);
});
