import test from 'node:test';
import assert from 'node:assert/strict';
import {flush,deferred} from './harness.mjs';
import {makePublic,catalogue,available,response,publicProfessional,firstStart,quote} from './public-harness.mjs';

const secondProfessional='70000000-0000-4000-8000-000000000027';
const secondStart='2030-01-08T11:15:00-03:00';

function shop(){
  const data=catalogue();
  data.professionals[0].name='Guilherme Cardoso de Oliveira Neto';
  data.professionals[0].image_url='https://images.example.invalid/guilherme.webp';
  data.professionals.push({
    id:secondProfessional,
    name:'Maria Eduarda dos Santos de Albuquerque Fernandes Oliveira Filho',
    image_url:'https://images.example.invalid/maria.webp',color:'#456789'
  });
  data.services[0].professional_ids.push(secondProfessional);
  return data;
}
function photo(h,id){
  return h.document.querySelector('.professional-choice[data-professional-id="'+id+'"] .mini-portrait img');
}
function picker(h){return h.document.querySelector('.professional-picker');}
function days(h){return [...h.document.querySelectorAll('#booking-day-strip .day-button')];}
function times(h){return [...h.document.querySelectorAll('#time-slots .slot')];}

test('full-size professional photos and long names persist after day/hour/person changes',async t=>{
  const data=shop();
  const h=await makePublic({data,respond:url=>{
    if(url.includes('/availability?')){
      const query=new URL(url,'https://orion.test').searchParams;
      return response(available(query.get('day')==='2030-01-08'?secondStart:firstStart,
        quote+query.get('professional_id')));
    }
  }});
  t.after(()=>h.close());await flush();
  h.click('#services-grid .service-card');
  h.click('#booking-next');await flush();
  const list=picker(h),first=photo(h,publicProfessional),second=photo(h,secondProfessional);
  assert.ok(first&&second);
  assert.ok(first.closest('.professional-photo'));
  assert.deepEqual([...list.querySelectorAll('.professional-name')].map(n=>n.textContent),
    data.professionals.map(p=>p.name));
  assert.equal(list.querySelectorAll('.professional-choice').length,2);
  assert.equal(first.alt,'','Visible names are used for accessible button labels');
  assert.equal(list.querySelectorAll('.professional-choice[aria-pressed="true"]').length,1);

  const dayNodes=days(h),firstDate=dayNodes[0],secondDate=dayNodes[1];
  assert.ok(firstDate&&secondDate);
  secondDate.focus();secondDate.click();await flush();
  assert.equal(first,photo(h,publicProfessional));
  assert.equal(second,photo(h,secondProfessional));
  assert.equal(picker(h),list);
  assert.equal(days(h)[0],firstDate);
  assert.equal(days(h)[1],secondDate);
  assert.equal(h.document.activeElement,secondDate);
  assert.equal(secondDate.getAttribute('aria-pressed'),'true');
  assert.equal(h.document.getElementById('booking-day-input').value,'2030-01-08');
  assert.ok(times(h).length>0);
  const currentSlot=times(h)[0];
  currentSlot.focus();currentSlot.click();
  assert.equal(times(h)[0],currentSlot);
  assert.equal(h.document.activeElement,currentSlot);
  assert.equal(currentSlot.getAttribute('aria-pressed'),'true');

  const staff=h.document.querySelector('.professional-choice[data-professional-id="'+secondProfessional+'"]');
  staff.click();await flush();
  assert.equal(first,photo(h,publicProfessional));
  assert.equal(second,photo(h,secondProfessional));
  assert.equal(picker(h),list);
  assert.equal(staff.getAttribute('aria-pressed'),'true');
  assert.equal(times(h).length,1);
  assert.equal(h.errors.length,0);
});

test('waiting on a day change preserves disabled old times, then updates them in place',async t=>{
  const data=shop(),slow=deferred();
  const h=await makePublic({data,respond:url=>{
    if(!url.includes('/availability?'))return undefined;
    const qs=new URL(url,'https://orion.test').searchParams;
    if(qs.get('day')==='2030-01-08')return slow.promise;
    return response(available(firstStart,quote));
  }});
  t.after(()=>h.close());await flush();
  h.click('#services-grid .service-card');h.click('#booking-next');await flush();
  const person=photo(h,publicProfessional),hour=times(h)[0],group=h.document.querySelector('.slots-grid');
  h.click('#booking-day-strip .day-button:nth-child(2)');await flush();
  assert.equal(person,photo(h,publicProfessional));
  assert.equal(group,h.document.querySelector('.slots-grid'));
  assert.equal(hour,times(h)[0]);
  assert.equal(hour.disabled,true);
  assert.match(h.document.querySelector('[data-slot-status]').textContent,/Atualizando horários/);
  assert.equal(h.document.getElementById('booking-next').disabled,true);
  slow.resolve(response(available(secondStart,quote+'-changed')));await flush();
  assert.equal(group,h.document.querySelector('.slots-grid'));
  assert.equal(h.document.querySelector('[data-slot-status]'),null);
  assert.equal(times(h).length,1);
  assert.equal(times(h)[0].dataset.slotStart,secondStart);
  assert.equal(times(h)[0].disabled,false);
  assert.equal(person,photo(h,publicProfessional));
  assert.equal(h.errors.length,0);
});

test('date picker beyond visible fortnight regenerates only dates, not professional photos',async t=>{
  const h=await makePublic({data:shop()});t.after(()=>h.close());await flush();
  h.click('#services-grid .service-card');h.click('#booking-next');await flush();
  const person=photo(h,publicProfessional),other=photo(h,secondProfessional),oldDates=days(h);
  const input=h.document.querySelector('#booking-day-input');
  input.value='2030-02-12';input.dispatchEvent(new h.window.Event('change',{bubbles:true}));
  await flush();
  assert.equal(person,photo(h,publicProfessional));
  assert.equal(other,photo(h,secondProfessional));
  assert.notEqual(days(h)[0],oldDates[0]);
  assert.equal(input.value,'2030-02-12');
  assert.equal(days(h)[0].dataset.day,'2030-02-12');
  assert.equal(days(h)[0].getAttribute('aria-pressed'),'true');
  assert.match(h.document.getElementById('booking-month-heading').textContent,/fevereiro de 2030/);
  assert.equal(h.errors.length,0);
});

test('returning from confirmation reuses the exact same staff photo nodes',async t=>{
  const h=await makePublic({data:shop()});t.after(()=>h.close());await flush();
  h.click('#services-grid .service-card');h.click('#booking-next');await flush();
  const photoFirst=photo(h,publicProfessional),photoSecond=photo(h,secondProfessional);
  const day=h.document.querySelector('#booking-day-strip .day-button:nth-child(1)');
  const slot=times(h)[0];slot.click();h.click('#booking-next');
  assert.ok(h.document.getElementById('customer-form'));
  h.click('#booking-back');
  assert.equal(photoFirst,photo(h,publicProfessional));
  assert.equal(photoSecond,photo(h,secondProfessional));
  assert.equal(day,h.document.querySelector('#booking-day-strip .day-button:nth-child(1)'));
  assert.equal(slot,times(h)[0]);
  assert.equal(h.errors.length,0);
});

test('preview changes date and professional without refetching or reloading images',async t=>{
  const h=await makePublic({data:shop(),path:'/app/site-preview/10000000-0000-4000-8000-000000000001'});
  t.after(()=>h.close());await flush();
  h.click('#services-grid .service-card');h.click('#booking-next');await flush();
  const p1=photo(h,publicProfessional),p2=photo(h,secondProfessional);
  h.click('#booking-day-strip .day-button:nth-child(2)');
  h.click('.professional-choice[data-professional-id="'+secondProfessional+'"]');
  assert.equal(p1,photo(h,publicProfessional));
  assert.equal(p2,photo(h,secondProfessional));
  assert.match(h.document.querySelector('#time-slots .preview-note').textContent,/Esta é uma prévia/);
  assert.equal(h.calls.length,1,'preview never queries a live availability endpoint');
  assert.equal(h.errors.length,0);
});
