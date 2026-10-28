import test from 'node:test';
import assert from 'node:assert/strict';
import {makePublic,catalogue,publicProfessional,publicService} from './public-harness.mjs';
import {flush} from './harness.mjs';

test('PUB-14: service duration uses the same notation as the selected total',async t=>{
  const data=catalogue();data.services[0].duration_min=90;
  const h=await makePublic({data});t.after(()=>h.close());await flush();
  assert.equal(h.document.querySelector('#services-grid .service-duration').textContent,'1 h 30 min');
  h.click('#hero-book');h.click('#booking-service-list button');
  assert.equal(h.document.querySelector('#booking-service-list .service-duration').textContent,'1 h 30 min');
  assert.match(h.document.querySelector('#booking-total').textContent,/1 h 30 min/);
});

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

test('PUB-05/PUB-08: scrollable days and native date selection share a compact display',async t=>{
  const h=await makePublic();t.after(()=>h.close());await flush();
  h.click('#hero-book');h.click('#booking-service-list button');h.click('#booking-next');await flush();
  const input=h.document.querySelector('#booking-day-input');
  assert.equal(input.type,'date');
  assert.equal(h.document.getElementById(input.getAttribute('aria-labelledby')).textContent,'Outra data');
  assert.equal(input.min,'2030-01-07');
  assert.equal(h.document.querySelector('.booking-date-value')?.textContent,'07/01/2030');
  assert.equal(h.document.querySelector('#booking-day-prev'),null);
  assert.equal(h.document.querySelector('#booking-day-next'),null);
  h.click('#booking-day-strip .day-button:nth-child(2)');await flush();
  assert.equal(input.value,'2030-01-08');
  assert.equal(h.document.querySelector('.booking-date-value').textContent,'08/01/2030');
  assert.match(h.document.querySelector('.day-month').textContent,/jan/);
  input.value='2030-02-01';input.dispatchEvent(new h.window.Event('change',{bubbles:true}));await flush();
  assert.equal(h.document.querySelector('.booking-date-value').textContent,'01/02/2030');
  assert.equal(h.document.querySelector('#booking-day-strip [aria-pressed=true]').dataset.day,'2030-02-01');
  assert.match(h.document.querySelector('#booking-month-heading').textContent,/fevereiro/);
  h.click('#time-slots .slot');
  assert.equal(h.document.querySelector('#booking-next').disabled,false);
  const availabilityCalls=h.calls.filter(c=>c.url.includes('/availability?')).length;
  input.value='10000-01-01';input.dispatchEvent(new h.window.Event('input',{bubbles:true}));
  input.dispatchEvent(new h.window.Event('change',{bubbles:true}));await flush();
  assert.equal(h.errors.length,0);
  assert.equal(h.calls.filter(c=>c.url.includes('/availability?')).length,availabilityCalls);
  assert.equal(h.document.querySelector('#booking-day-strip [aria-pressed=true]').dataset.day,'2030-02-01');
  assert.equal(input.getAttribute('aria-invalid'),'true');
  assert.equal(h.document.querySelector('.booking-date-value').textContent,'Data inválida');
  assert.equal(h.document.querySelector('#booking-next').disabled,true);
  input.value='';input.dispatchEvent(new h.window.Event('change',{bubbles:true}));
  assert.equal(input.value,'2030-02-01');
  assert.equal(input.checkValidity(),true);
  assert.equal(h.document.querySelector('#booking-next').disabled,false);
  assert.equal(h.document.querySelector('.booking-date-value').textContent,'01/02/2030');
  let opened=0;input.showPicker=()=>{opened++;};
  const enter=new h.window.KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true});
  input.dispatchEvent(enter);
  assert.equal(opened,1);assert.equal(enter.defaultPrevented,true);
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
