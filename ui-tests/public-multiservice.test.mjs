import test from 'node:test';
import assert from 'node:assert/strict';
import { flush } from './harness.mjs';
import { makePublic, catalogue, available, response, publicService, publicProfessional } from './public-harness.mjs';

const skin='61000000-0000-4000-8000-000000000061';
const other='62000000-0000-4000-8000-000000000062';
const staffTwo='73000000-0000-4000-8000-000000000073';
const makeData=()=>{
  const data=catalogue();
  data.services[0].image_url='https://images.example.invalid/combo.webp';
  data.services.push(
    {id:skin,name:'Limpeza de pele',catalog_kind:'base',category:'Serviços',
      duration_min:35,price_cents:2500,price_mode:'fixed',
      professional_ids:[publicProfessional,staffTwo],image_url:'https://images.example.invalid/skin.webp'},
    {id:other,name:'Hidratação premium',catalog_kind:'base',category:'Serviços',
      duration_min:30,price_cents:3000,price_mode:'from',
      professional_ids:[staffTwo],image_url:'https://images.example.invalid/hair.webp'},
  );
  data.professionals.push({id:staffTwo,name:'Profissional 2'});
  data.services[0].professional_ids=[publicProfessional,staffTwo];
  return data;
};
const selected=h=>[...h.document.querySelectorAll('#booking-service-list .service-card[aria-pressed="true"]')].map(el=>el.dataset.serviceId);

test('selecting individual services updates totals without remounting cards or photos',async t=>{
  const data=makeData();
  const h=await makePublic({data});t.after(()=>h.close());await flush();
  h.click('#services-grid .service-card');
  const list=h.document.getElementById('booking-service-list');
  const photo=list.querySelector('.service-thumb img'),first=list.querySelector('.service-card');
  assert.ok(photo);
  assert.deepEqual(selected(h),[publicService]);
  assert.match(h.document.getElementById('booking-selection-summary').textContent,/50 min/);
  const extra=h.document.querySelector('.addon-option input');
  assert.ok(extra);
  const another=list.querySelector('[data-service-id="'+skin+'"]');
  another.focus();another.click();await flush();
  assert.equal(h.document.getElementById('booking-service-list'),list);
  assert.equal(list.querySelector('.service-thumb img'),photo);
  assert.equal(list.querySelector('.service-card'),first);
  assert.equal(h.document.activeElement,another);
  assert.deepEqual(selected(h),[publicService,skin]);
  assert.match(h.document.getElementById('booking-selected-count').textContent,/2 serviços/);
  assert.match(h.document.getElementById('booking-total').textContent,/1 h 25 min/);
  assert.match(h.document.getElementById('booking-total').textContent,/70,00/);
  assert.ok(!h.document.getElementById('booking-next').disabled);
  another.click();
  assert.deepEqual(selected(h),[publicService]);
  assert.equal(list.querySelector('.service-thumb img'),photo);
  assert.equal(list.querySelector('.service-card'),first);
  assert.equal(h.errors.length,0);
});

test('multiple full services use repeated service_ids; confirmed review uses server totals',async t=>{
  const data=makeData();
  const h=await makePublic({data,respond:(url,opts)=>{
    if(url.includes('/availability?')){
      const qs=new URL(url,'https://orion.test').searchParams;
      assert.deepEqual(qs.getAll('service_ids'),[publicService,skin]);
      assert.equal(qs.has('addon_ids'),false);
      return response({...available(),price_cents:7000,duration_min:85,price_mode:'fixed',
        services:['Corte + barba','Limpeza de pele']});
    }
  }});t.after(()=>h.close());await flush();
  h.click('#services-grid .service-card');
  h.click('[data-service-id="'+skin+'"]');
  h.click('#booking-next');await flush();
  assert.ok(h.document.querySelector('#time-slots .slot'));
  assert.match(h.document.getElementById('booking-total').textContent,/70,00/);
  h.click('#time-slots .slot');h.click('#booking-next');
  assert.match(h.document.querySelector('.review-card').textContent,/Corte \+ barba \+ Limpeza de pele/);
  assert.match(h.document.querySelector('.review-card').textContent,/70,00/);
  h.fill('[name=customer_name]',' Cliente Teste ');
  h.fill('[name=customer_phone]','(11) 99999-9999');
  h.fill('[name=customer_birth_date]','1990-01-15');
  h.click('#booking-next');await flush();
  assert.equal(h.calls.filter(x=>x.options.method==='POST').length,1);
  assert.equal(h.errors.length,0);
});

test('switching from an optional addon to an additional complete service clears the addon',async t=>{
  const data=makeData(),h=await makePublic({data});
  t.after(()=>h.close());await flush();
  h.click('#services-grid .service-card');
  h.click('.addon-option input');
  assert.match(h.document.getElementById('booking-total').textContent,/52,00/);
  h.click('[data-service-id="'+skin+'"]');
  assert.equal(h.document.querySelector('.addon-option'),null);
  assert.match(h.document.getElementById('booking-selection-warning').textContent,/Adicionais anteriores foram removidos/);
  assert.match(h.document.getElementById('booking-total').textContent,/70,00/);
  h.click('[data-service-id="'+skin+'"]');
  assert.ok(h.document.querySelector('.addon-option input'));
  assert.equal(h.document.querySelector('.addon-option input').checked,false);
  assert.equal(h.errors.length,0);
});

test('professional intersection blocks incompatible services and restores next after removal',async t=>{
  const data=makeData();
  data.services[0].professional_ids=[publicProfessional];
  const h=await makePublic({data});t.after(()=>h.close());await flush();
  h.click('#services-grid .service-card');
  h.click('[data-service-id="'+other+'"]');
  assert.equal(h.document.getElementById('booking-next').disabled,true);
  assert.match(h.document.getElementById('booking-selection-warning').textContent,/Nenhum profissional/);
  h.click('[data-service-id="'+other+'"]');
  assert.equal(h.document.getElementById('booking-next').disabled,false);
  assert.equal(h.errors.length,0);
});

test('limit six, prevent double counting combo components, and preserve old single addon API',async t=>{
  const data=makeData();
  for(let i=0;i<6;i++)data.services.push({
    id:'80000000-0000-4000-8000-'+String(i).padStart(12,'0'),
    name:'Serviço '+i,catalog_kind:'base',price_cents:300,duration_min:10,
    professional_ids:[publicProfessional,staffTwo]
  });
  const duplicate='90000000-0000-4000-8000-000000000009';
  data.services[0].components=[{service_id:duplicate,name:'Corte'}, {service_id:'90000000-0000-4000-8000-000000000010',name:'Barba'}];
  data.services.push({id:duplicate,name:'Corte já incluso',catalog_kind:'base',
    price_cents:1000,duration_min:10,professional_ids:[publicProfessional,staffTwo]});
  const h=await makePublic({data});t.after(()=>h.close());await flush();
  h.click('#services-grid .service-card');
  h.click('[data-service-id="'+duplicate+'"]');
  assert.deepEqual(selected(h),[publicService]);
  assert.match(h.document.getElementById('booking-error').textContent,/já está incluído/);
  for(let i=0;i<5;i++)h.click('[data-service-id="'+data.services[i+3].id+'"]');
  assert.equal(selected(h).length,6);
  h.click('[data-service-id="'+skin+'"]');
  assert.equal(selected(h).length,6);
  assert.match(h.document.getElementById('booking-error').textContent,/até 6 serviços/);
  // Reduce back to a single service; old addon mode and API remain available.
  for(let i=0;i<5;i++)h.click('[data-service-id="'+data.services[i+3].id+'"]');
  h.click('.addon-option input');h.click('#booking-next');await flush();
  const request=h.calls.find(x=>x.url.includes('/availability?'));
  const qs=new URL(request.url,'https://orion.test').searchParams;
  assert.equal(qs.get('service_id'),publicService);
  assert.equal(qs.getAll('addon_ids').length,1);
  assert.equal(qs.has('service_ids'),false);
  assert.equal(h.errors.length,0);
});
