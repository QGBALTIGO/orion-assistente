import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM} from 'jsdom';
import {makePublic} from './public-harness.mjs';
import {flush} from './harness.mjs';

async function calendar(){
  const js=await readFile(new URL('../app/static/orion-calendar-links.js',import.meta.url),'utf8');
  const dom=new JSDOM('',{url:'https://orionagenda.pro',runScripts:'outside-only'});
  dom.window.eval(js);
  return {url:dom.window.OrionCalendarLinks.googleEventURL,close:()=>dom.window.close()};
}
const booking={
  status:'confirmed',company:'Barbearia Peixoto',services:['Corte & barba'],
  professional:'Peixoto',start_at:'2026-11-02T09:00:00-03:00',
  end_at:'2026-11-02T09:40:00-03:00',timezone:'America/Sao_Paulo',
  address:'Rua Canuto de Quadros, 122 - Montes Claros, MG'
};

test('Client Google Calendar link has exact UTC instants, no personal account grants',async()=>{
  const c=await calendar();try{
    const result=c.url(booking),u=new URL(result);
    assert.equal(u.origin,'https://calendar.google.com');
    assert.equal(u.pathname,'/calendar/r/eventedit');
    assert.equal(u.searchParams.get('action'),'TEMPLATE');
    assert.equal(u.searchParams.get('dates'),'20261102T120000Z/20261102T124000Z');
    assert.equal(u.searchParams.get('stz'),'America/Sao_Paulo');
    assert.equal(u.searchParams.get('etz'),'America/Sao_Paulo');
    assert.equal(u.searchParams.get('text'),'Barbearia Peixoto · Corte & barba');
    assert.match(u.searchParams.get('details'),/atualize o evento manualmente/);
    assert.ok(!result.includes('customer_phone')&&!result.includes('token'));
    assert.equal(u.searchParams.get('location'),booking.address);
  }finally{c.close();}
});

test('Invalid, altered and canceled appointment cannot produce save link',async()=>{
  const c=await calendar();try{
    for(const patch of [
      {status:'cancelled'},{status:'completed'},{company:''},{start_at:'bad'},
      {end_at:'2026-11-02T08:00:00-03:00'},
      {end_at:'2026-11-04T09:00:00-03:00'}
    ])assert.equal(c.url({...booking,...patch}),null);
    const url=new URL(c.url({...booking,company:'<script>alert(1)</script>',
      services:['Barba & corte'],timezone:'../invalid',address:'Rua & Praca'}));
    assert.equal(url.host,'calendar.google.com');
    assert.equal(url.searchParams.has('stz'),false);
    assert.equal(url.searchParams.has('etz'),false);
    assert.ok(url.searchParams.get('location').includes('&'));
  }finally{c.close();}
});

test('Confirmation has separate one-click Google save and .ics fallback',async t=>{
  const h=await makePublic();t.after(()=>h.close());await flush();
  h.click('#services-grid .service-card');h.click('#booking-next');await flush();
  h.click('#time-slots .slot');h.click('#booking-next');
  h.fill('[name=customer_name]','Cliente sintético');
  h.fill('[name=customer_phone]','11999999999');
  h.fill('[name=customer_birth_date]','1992-04-23');
  h.click('#booking-next');await flush();
  const anchors=[...h.document.querySelectorAll('.success-actions a')];
  const google=anchors.find(x=>x.textContent.includes('Google Agenda'));
  assert.ok(google,'Google event action must render on confirmed receipt');
  const u=new URL(google.href);
  assert.equal(u.origin,'https://calendar.google.com');
  assert.equal(u.searchParams.get('dates'),'20300107T120000Z/20300107T125000Z');
  assert.equal(google.target,'_blank');
  assert.equal(google.rel,'noopener noreferrer');
  assert.equal(google.referrerPolicy,'no-referrer');
  assert.ok([...h.document.querySelectorAll('.success-actions button')].some(x=>x.textContent.includes('Baixar para outras agendas')));
  assert.equal(h.errors.length,0);
});

test('WhatsApp appointment management also includes Google action',async()=>{
  const js=await readFile(new URL('../app/static/orion-appointment.js',import.meta.url),'utf8');
  const html=await readFile(new URL('../app/static/orion-appointment.html',import.meta.url),'utf8');
  const pub=await readFile(new URL('../app/static/orion-public.html',import.meta.url),'utf8');
  assert.ok(js.includes("link('Adicionar ao Google Agenda',url)"));
  assert.ok(js.includes("data.status==='confirmed'&&!reviewMode"));
  assert.ok(html.includes('orion-calendar-links.js'));
  assert.ok(pub.includes('orion-calendar-links.js'));
  assert.ok(html.indexOf('orion-calendar-links.js') < html.indexOf('orion-appointment.js'));
});
