import test from 'node:test';
import assert from 'node:assert/strict';
import { flush } from './harness.mjs';
import { catalogue, makePublic } from './public-harness.mjs';

test('Barbearia Peixoto shows a concise two-line address without a dangling separator',async t=>{
 const data=catalogue();
 Object.assign(data.site,{
   title:'Barbearia Peixoto',address:'Rua Canuto de Quadros',
   address_display:'Rua Canuto de Quadros, 122 — São Judas Tadeu',
   address_number:'122',neighborhood:'São Judas Tadeu',
   city:'Montes Claros',state:'MG',postal_code:'39402-366'
 });
 const h=await makePublic({data});t.after(()=>h.close());await flush();
 const root=h.document.getElementById('hero-address');
 assert.equal(root.hidden,false);
 assert.equal(h.document.getElementById('hero-address-street').textContent,'Rua Canuto de Quadros, 122');
 assert.equal(h.document.getElementById('hero-address-locality').textContent,'Montes Claros · MG');
 assert.equal(root.getAttribute('aria-label'),
   'Rua Canuto de Quadros, 122 — São Judas Tadeu, Montes Claros, MG');
 assert.equal(root.title,root.getAttribute('aria-label'));
 assert.equal(root.querySelectorAll('span').length,2);
 assert.equal(root.textContent.includes('São Judas Tadeu'),false);
 assert.doesNotMatch(root.textContent,/·\s*$/);
 assert.match(h.document.getElementById('full-address').textContent,/Rua Canuto de Quadros, 122 — São Judas Tadeu/);
 assert.match(h.document.getElementById('full-address').textContent,/39402-366/);
 assert.equal(h.errors.length,0);
});

test('Legacy full street, city-only and absence of address do not show orphan punctuation',async t=>{
 for(const [site,street,locality] of [
   [{address:'Rua Antiga, 42',city:'Aquidauana',state:'MS'},'Rua Antiga, 42','Aquidauana · MS'],
   [{address:'Avenida Modelo',city:'Campo Grande',state:''},'Avenida Modelo','Campo Grande'],
   [{address:'',city:'Montes Claros',state:'MG'},'','Montes Claros · MG'],
   [{address:'',city:'',state:''},'',''],
   [{address:'Rua Sem Número',address_no_number:true,address_display:'Rua Sem Número, s/n — Centro',city:'Rio',state:'RJ'},'Rua Sem Número, s/n','Rio · RJ'],
 ]) {
   const data=catalogue();
   Object.assign(data.site,{address:'',address_display:null,city:'',state:''},site);
   const h=await makePublic({data});await flush();
   const root=h.document.getElementById('hero-address');
   assert.equal(h.document.getElementById('hero-address-street').textContent,street);
   assert.equal(h.document.getElementById('hero-address-locality').textContent,locality);
   assert.equal(root.hidden,!street&&!locality);
   assert.equal(h.document.getElementById('hero-address-street').hidden,!street);
   assert.equal(h.document.getElementById('hero-address-locality').hidden,!locality);
   assert.ok(!root.textContent.endsWith('·'));
   assert.equal(h.errors.length,0);
   h.close();
 }
});

test('Untrusted street text stays text-only while Google Maps still uses complete destination',async t=>{
 const data=catalogue();
 Object.assign(data.site,{
   address:'Rua <img src=x onerror=alert(1)>',
   address_display:'Rua <img src=x onerror=alert(1)> — Bairro Teste',
   neighborhood:'Bairro Teste',city:'Teste',state:'SP'
 });
 const h=await makePublic({data});t.after(()=>h.close());await flush();
 assert.equal(h.document.querySelector('#hero-address img'),null);
 assert.equal(h.document.getElementById('hero-address-street').textContent,
  'Rua <img src=x onerror=alert(1)>');
 assert.match(h.document.getElementById('full-address').textContent,/Bairro Teste/);
 assert.ok(h.document.getElementById('maps-link').href.startsWith('https://www.google.com/maps/dir/'));
 assert.equal(h.errors.length,0);
});
