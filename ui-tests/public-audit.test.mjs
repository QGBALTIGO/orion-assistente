import test from 'node:test';
import assert from 'node:assert/strict';
import {makePublic,catalogue,publicProfessional,publicService} from './public-harness.mjs';
import {flush} from './harness.mjs';

test('PUB-01: returning through services preserves the eligible chosen professional',async t=>{
  const data=catalogue(),second='70000000-0000-4000-8000-000000000027';
  data.professionals.push({id:second,name:'Segundo profissional'});
  data.services[0].professional_ids.push(second);
  const h=await makePublic({data});t.after(()=>h.close());await flush();
  h.click('#hero-book');h.click('#booking-service-list button');h.click('#booking-next');await flush();
  h.click(`[data-professional-id="${second}"]`);await flush();
  h.click('#time-slots .slot');
  h.click('#booking-back');h.click('#booking-next');await flush();
  assert.equal(h.document.querySelector(`[data-professional-id="${second}"]`).getAttribute('aria-pressed'),'true');
  assert.equal(h.document.querySelector(`[data-professional-id="${publicProfessional}"]`).getAttribute('aria-pressed'),'false');
  assert.match(h.calls.filter(c=>c.url.includes('/availability?')).at(-1).url,new RegExp(second));
  assert.equal(h.calls.filter(c=>c.options.method==='POST').length,0);
});

test('PUB-09: required data and birthday purpose are explained and associated with the form',async t=>{
  const h=await makePublic();t.after(()=>h.close());await flush();
  h.click('#hero-book');h.click('#booking-service-list button');h.click('#booking-next');await flush();
  h.click('#time-slots .slot');h.click('#booking-next');
  const form=h.document.querySelector('#customer-form');
  const note=h.document.getElementById(form.getAttribute('aria-describedby'));
  assert.match(note?.textContent||'',/Todos os campos são obrigatórios/);
  assert.match(note.textContent,/aniversário.*cadastro/);
  assert.ok([...form.querySelectorAll('input')].every(e=>e.required));
});

test('PUB-07: lower-page booking opens the same wizard and directions stay secondary',async t=>{
  const h=await makePublic();t.after(()=>h.close());await flush();
  h.click('#footer-book');
  assert.ok(h.document.querySelector('#booking-dialog').hasAttribute('open'));
  assert.ok(h.document.querySelector('#booking-service-list'));
  assert.ok(h.document.querySelector('#maps-link').classList.contains('outline'));
  assert.ok(!h.document.querySelector('#maps-link').classList.contains('primary'));
  assert.equal(h.calls.filter(c=>c.options.method==='POST').length,0);
});

test('PUB-06: review carries authoritative duration and expected finish',async t=>{
  const h=await makePublic();t.after(()=>h.close());await flush();
  h.click('#hero-book');h.click('#booking-service-list button');h.click('#booking-next');await flush();
  h.click('#time-slots .slot');h.click('#booking-next');
  const review=h.document.querySelector('.review-card').textContent;
  assert.match(review,/Duração50 min/);
  assert.match(review,/Término previsto09:50/);
  assert.equal(h.document.querySelectorAll('.review-row.total').length,1);
});

test('PUB-05: explicit day navigation respects bounds and labels months',async t=>{
  const h=await makePublic();t.after(()=>h.close());await flush();
  h.click('#hero-book');h.click('#booking-service-list button');h.click('#booking-next');await flush();
  assert.equal(h.document.querySelector('#booking-day-prev')?.disabled,true);
  h.click('#booking-day-next');await flush();
  assert.equal(h.document.querySelector('#booking-day-input').value,'2030-01-08');
  assert.equal(h.document.querySelector('#booking-day-prev').disabled,false);
  assert.match(h.document.querySelector('.day-month').textContent,/jan/);
  h.click('#booking-day-prev');await flush();
  assert.equal(h.document.querySelector('#booking-day-input').value,'2030-01-07');
});

test('PUB-02: conflict describes the selected individual service and lives above the list',async t=>{
  const data=catalogue();
  data.services[0].catalog_kind='base';data.services[0].name='Corte Masculino';
  data.services.push({...data.services[0],id:'60000000-0000-4000-8000-000000000022',
    name:'Corte + Barba',catalog_kind:'combo',components:[{service_id:publicService}]});
  const h=await makePublic({data});t.after(()=>h.close());await flush();
  h.click('#hero-book');h.click('#booking-service-list button');
  h.click('#booking-service-list button:nth-child(2)');
  const error=h.document.querySelector('#booking-error');
  assert.match(error.textContent,/combo.*já inclui Corte Masculino/i);
  assert.ok(error.compareDocumentPosition(h.document.querySelector('#booking-body'))&4);
  assert.equal(h.document.querySelectorAll('#booking-service-list [aria-pressed=true]').length,1);
});
