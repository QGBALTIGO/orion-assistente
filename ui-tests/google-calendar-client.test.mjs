import test from 'node:test';
import assert from 'node:assert/strict';
import {flush} from './harness.mjs';
import {makePublic, confirmed, response} from './public-harness.mjs';

async function book(h){
  await flush();
  h.click('#services-grid .service-card');
  h.click('#booking-next'); await flush();
  h.click('#time-slots .slot');
  h.click('#booking-next');
  h.fill('[name=customer_name]', 'Cliente sintético');
  h.fill('[name=customer_phone]', '86972676282');
  h.fill('[name=customer_birth_date]', '1990-01-01');
  h.click('#booking-next'); await flush();
}

test('cliente adiciona reserva confirmada diretamente ao Google Agenda com horário UTC e fuso', async t=>{
  const h=await makePublic();t.after(()=>h.close());await book(h);
  const link=h.document.querySelector('.success a[href^="https://calendar.google.com/calendar/render?"]');
  assert.ok(link,'deve mostrar uma ação direta após confirmação');
  const url=new URL(link.href);
  assert.equal(url.hostname,'calendar.google.com');
  assert.equal(url.pathname,'/calendar/render');
  assert.equal(url.searchParams.get('action'),'TEMPLATE');
  assert.equal(url.searchParams.get('dates'),'20300107T120000Z/20300107T125000Z');
  assert.equal(url.searchParams.get('ctz'),'America/Sao_Paulo');
  assert.match(url.searchParams.get('text'),/Empresa sintética/);
  assert.match(url.searchParams.get('details'),/Profissional sintético/);
  assert.match(url.searchParams.get('details'),/SYNTHETIC-1/);
  assert.match(url.searchParams.get('location'),/São Paulo/);
  assert.doesNotMatch(url.href,/86972676282|customer_phone|birth_date|\/a#/);
  assert.equal(link.target,'_blank');
  assert.equal(link.rel,'noopener noreferrer');
  assert.equal(link.referrerPolicy,'no-referrer');
  assert.ok([...h.document.querySelectorAll('.success button')].some(x=>/outros calendários \(\.ics\)/.test(x.textContent)),'ICS continua disponível');
  assert.equal(h.errors.length,0);
});

test('horários inválidos não produzem um link Google enganoso',async t=>{
  const h=await makePublic({respond:url=>url.endsWith('/appointments')
    ? response({...confirmed(),end_at:'2030-01-07T08:50:00-03:00'},201) : undefined});
  t.after(()=>h.close());await book(h);
  assert.equal(h.document.querySelector('.success a[href^="https://calendar.google.com/"]'),null);
  assert.equal(h.errors.length,0);
});
