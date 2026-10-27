import test from 'node:test';
import assert from 'node:assert/strict';
import { flush } from './harness.mjs';
import { makePublic, catalogue, publicBase, quote, firstStart } from './public-harness.mjs';

async function confirmStage(h){
  await flush();
  h.click('#services-grid .service-card');
  h.click('#booking-next');await flush();
  h.click('#time-slots .slot');
  h.click('#booking-next');
}

test('confirmation is compact, without duplicate price, long checkbox or privacy paragraph',async t=>{
  const h=await makePublic();t.after(()=>h.close());await confirmStage(h);
  const body=h.document.getElementById('booking-body');
  assert.ok(body.classList.contains('booking-confirmation-body'));
  assert.equal(body.querySelectorAll('.review-card').length,1);
  assert.equal(body.querySelector('h3.booking-contact-heading').textContent,'Seus dados');
  assert.equal(body.querySelector('h3:not(.booking-contact-heading)'),body.querySelector('.review-card h3'));
  assert.equal(body.querySelector('.consent-label'),null);
  assert.equal(body.querySelector('.privacy-note'),null);
  assert.equal(body.querySelector('[name=confirmed]'),null);
  assert.ok(body.querySelector('.review-row.total'));
  assert.match(body.querySelector('.review-row.total').textContent,/52,00/);
  assert.equal(h.document.getElementById('booking-total').hidden,true);
  assert.ok(h.document.querySelector('.booking-footer').classList.contains('booking-footer-confirm'));
  assert.equal(h.document.getElementById('booking-next').textContent,'Confirmar agendamento');
  assert.equal(h.document.getElementById('booking-next').disabled,false);
  assert.deepEqual([...body.querySelectorAll('#customer-form .field')].map(label=>label.textContent.replace(/\s+/g,' ').trim()),
    ['Nome completo','WhatsApp com DDD Ex.: (86) 97267-6282','Data de nascimento']);
  const birth=body.querySelector('[name=customer_birth_date]');
  assert.equal(birth.type,'date');assert.equal(birth.required,true);
  assert.equal(birth.min,'1900-01-01');
  assert.equal(birth.max,catalogue().today);
  h.click('#booking-back');
  assert.equal(h.document.getElementById('booking-total').hidden,false);
  assert.equal(h.document.querySelector('.booking-footer').classList.contains('booking-footer-confirm'),false);
  assert.equal(h.errors.length,0);
});

test('Brazilian WhatsApp is masked while typing, pasting +55 and using 10-digit landlines',async t=>{
  const h=await makePublic();t.after(()=>h.close());await confirmStage(h);
  const input=h.document.querySelector('[name=customer_phone]');
  assert.equal(input.type,'tel');
  assert.equal(input.placeholder,'(86) 97267-6282');
  for(const [typed,shown] of [
    ['86','(86'],['86972','(86) 972'],
    ['86972676282','(86) 97267-6282'],
    ['+55 (86) 97267-6282','(86) 97267-6282'],
    ['8632676282','(86) 3267-6282']
  ]){
    h.fill('[name=customer_phone]',typed);
    assert.equal(input.value,shown);
  }
  assert.equal(h.errors.length,0);
});

test('submits exactly once with full masked WhatsApp, real birth date and affirmative button',async t=>{
  const h=await makePublic();t.after(()=>h.close());await confirmStage(h);
  h.fill('[name=customer_name]',' Cliente sintética ');
  h.fill('[name=customer_phone]','86972676282');
  h.click('#booking-next');await flush();
  assert.equal(h.calls.filter(x=>x.options.method==='POST').length,0,'missing birth date must not submit');
  h.fill('[name=customer_birth_date]','2030-01-08');
  h.click('#booking-next');await flush();
  assert.equal(h.calls.filter(x=>x.options.method==='POST').length,0,'future birthday must not submit');
  h.fill('[name=customer_birth_date]','1994-04-09');
  h.click('#booking-next');await flush();
  const posts=h.calls.filter(x=>x.options.method==='POST');
  assert.equal(posts.length,1);
  assert.equal(posts[0].url,publicBase+'/appointments');
  assert.deepEqual(posts[0].body,{
    quote,start_at:firstStart,customer_name:'Cliente sintética',
    customer_phone:'(86) 97267-6282',customer_birth_date:'1994-04-09',confirmed:true
  });
  assert.equal(h.document.querySelector('[name=customer_birth_date]'),null);
  assert.match(h.document.getElementById('booking-body').textContent,/Agendamento confirmado!/);
  assert.equal(h.errors.length,0);
});

test('form values survive back/forward and return to an unmodified confirmation view',async t=>{
  const h=await makePublic();t.after(()=>h.close());await confirmStage(h);
  h.fill('[name=customer_name]','Cliente visitante');
  h.fill('[name=customer_phone]','86972676282');
  h.fill('[name=customer_birth_date]','1989-07-05');
  h.click('#booking-back');h.click('#booking-next');
  assert.equal(h.document.querySelector('[name=customer_phone]').value,'(86) 97267-6282');
  assert.equal(h.document.querySelector('[name=customer_birth_date]').value,'1989-07-05');
  assert.equal(h.document.querySelector('[name=customer_name]').value,'Cliente visitante');
  assert.equal(h.document.getElementById('booking-total').hidden,true);
  assert.equal(h.errors.length,0);
});
