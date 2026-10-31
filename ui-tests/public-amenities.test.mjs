import test from 'node:test';
import assert from 'node:assert/strict';
import { flush, makeSuite, defaultResponse } from './harness.mjs';
import { catalogue, makePublic } from './public-harness.mjs';

const amenityFields = ['amenity_wifi', 'amenity_parking', 'amenity_accessibility', 'amenity_children'];
const amenityNames = ['Wi-Fi', 'Estacionamento', 'Acessibilidade', 'Atende crianças'];

test('new and legacy sites with no enabled amenities hide the entire block', async t => {
  const h = await makePublic(); t.after(() => h.close()); await flush();
  assert.equal(h.document.getElementById('amenities-section').hidden, true);
  assert.equal(h.document.querySelectorAll('.amenity-tile').length, 0);
  assert.equal(h.document.getElementById('amenities-grid').dataset.count, '0');
  assert.equal(h.document.getElementById('amenity-detail').hidden, true);
  assert.equal(h.document.querySelector('.public-main > #amenities-section'),null);
  assert.equal(h.document.querySelector('.public-main > #servicos')?.id,'servicos');
  assert.equal(h.document.getElementById('amenities-section').closest('#address-card')?.id,'address-card');
  assert.equal(h.errors.length, 0);
});

test('one, two, three or four enabled amenities render only those icons in stable order', async t => {
  for (const count of [1, 2, 3, 4]) {
    await t.test(String(count) + ' enabled', async t => {
      const data = catalogue();
      for (const [index, field] of amenityFields.entries()) data.site[field] = index < count;
      const h = await makePublic({data}); t.after(() => h.close()); await flush();
      assert.equal(h.document.getElementById('amenities-section').hidden, false);
      const tiles = [...h.document.querySelectorAll('#amenities-grid .amenity-tile')];
      assert.equal(tiles.length, count);
      assert.equal(h.document.getElementById('amenities-grid').dataset.count, String(count));
      assert.equal(h.document.getElementById('amenities-title').textContent,'No local');
      assert.equal(h.document.querySelector('.public-main').firstElementChild.id,'servicos');
      assert.equal(h.document.getElementById('address-card').hidden,false);
      assert.equal(h.document.getElementById('amenities-section').parentElement.id,'address-card');
      assert.deepEqual(tiles.map(el => el.title), amenityNames.slice(0, count));
      assert.deepEqual(tiles.map(el => el.querySelector('.amenity-label')?.textContent),amenityNames.slice(0,count));
      assert.ok(tiles.every(el => el.querySelectorAll('svg[aria-hidden="true"]').length === 1));
      assert.ok(tiles.every(el => el.getAttribute('aria-label')?.includes('disponível')));
      assert.ok(tiles.every(el => el.getAttribute('aria-expanded') === 'false'));
      assert.equal(h.errors.length, 0);
    });
  }
});

test('tapping an amenity reveals matching information, switches and closes accessibly', async t => {
  const data=catalogue();
  Object.assign(data.site,{amenity_wifi:true,amenity_parking:true,amenity_children:true});
  const h=await makePublic({data});t.after(()=>h.close());await flush();
  const [wifi,parking,kids]=h.document.querySelectorAll('.amenity-tile');
  const detail=h.document.getElementById('amenity-detail');
  wifi.click();
  assert.equal(detail.hidden,false);
  assert.equal(wifi.getAttribute('aria-expanded'),'true');
  assert.equal(h.document.getElementById('amenity-detail-title').textContent,'Wi-Fi');
  assert.match(detail.textContent,/Disponível/);
  parking.click();
  assert.equal(wifi.getAttribute('aria-expanded'),'false');
  assert.equal(parking.getAttribute('aria-expanded'),'true');
  assert.equal(h.document.getElementById('amenity-detail-title').textContent,'Estacionamento');
  kids.click();
  assert.equal(h.document.getElementById('amenity-detail-title').textContent,'Atende crianças');
  h.click('#amenity-detail-close');
  assert.equal(detail.hidden,true);
  assert.equal(h.document.activeElement,kids);
  wifi.click();
  h.document.dispatchEvent(new h.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
  assert.equal(detail.hidden,true);
  assert.equal(h.document.activeElement,wifi);
  parking.click();
  h.click('#services-title');
  assert.equal(detail.hidden,true);
  assert.equal(h.errors.length,0);
});

test('My Site settings display four independent switches and save full typed payload', async t => {
  const stored={version:1,title:'Empresa de teste',slug:'empresa-a',
    theme_color:'#2e7bf6',show_prices:true,hide_orion_brand:false,
    amenity_wifi:true,amenity_parking:false,amenity_accessibility:true,amenity_children:false,
    published:true,has_unpublished_changes:false};
  const respond=(path,method,data)=>{
    if(path.endsWith('/management/site')&&method==='GET')return stored;
    if(path.endsWith('/management/site')&&method==='PUT')return {...stored,...data,version:2};
    if(path.endsWith('/management/site/metrics'))return {visits:0,site_bookings:0,daily:[]};
    return defaultResponse(path);
  };
  const h=await makeSuite({initialRoute:'site',respond});t.after(()=>h.close());await flush();
  h.click('[data-os-site-tab="settings"]');await flush();
  const form=h.document.getElementById('osSiteForm');
  assert.ok(form);
  assert.equal(form.querySelectorAll('.os-site-amenity-option').length,4);
  assert.deepEqual(amenityFields.map(k=>form.elements[k].checked),[true,false,true,false]);
  assert.equal(form.querySelector('.os-site-amenities-grid svg')?.getAttribute('aria-hidden'),'true');
  form.elements.amenity_wifi.click();
  form.elements.amenity_parking.click();
  form.elements.amenity_accessibility.click();
  form.elements.amenity_children.click();
  h.submit('#osSiteForm');await flush();
  const update=h.calls.find(c=>c.path.endsWith('/management/site')&&c.method==='PUT');
  assert.ok(update);
  assert.deepEqual(amenityFields.map(k=>update.data[k]),[false,true,false,true]);
  assert.equal(update.data.expected_version,1);
  assert.equal(update.data.title,'Empresa de teste');
  assert.equal(h.errors.length,0);
});

test('staff without write access cannot change My Site amenity switches', async t => {
  const h=await makeSuite({initialRoute:'site',role:'viewer',
    respond:path=>path.endsWith('/management/site')?
      {version:2,title:'Empresa',slug:'empresa-a',amenity_wifi:true}:
      path.endsWith('/management/site/metrics')?{daily:[]}:defaultResponse(path)
  });t.after(()=>h.close());await flush();
  h.click('[data-os-site-tab="settings"]');await flush();
  const fieldset=h.document.querySelector('#osSiteForm fieldset');
  assert.ok(fieldset?.disabled);
  assert.equal(fieldset.querySelector('[name=amenity_wifi]').checked,true);
  assert.equal(h.errors.length,0);
});


test('Amenities stay inside Onde estamos after map/address/route, never above services',async t=>{
  const data=catalogue();Object.assign(data.site,{
    amenity_wifi:true,amenity_parking:true,amenity_children:true,
    address:'Rua Modelo, 123',city:'Cidade de Teste'
  });
  const h=await makePublic({data});t.after(()=>h.close());await flush();
  const card=h.document.getElementById('address-card');
  const section=h.document.getElementById('amenities-section');
  assert.equal(card.hidden,false);
  assert.equal(section.hidden,false);
  assert.equal(section.parentElement,card);
  assert.ok(h.document.getElementById('maps-link').compareDocumentPosition(section) &
    h.window.Node.DOCUMENT_POSITION_FOLLOWING,
    'a linha No local deve vir depois da rota e do endereço');
  assert.equal(h.document.querySelector('.public-main').firstElementChild.id,'servicos');
  assert.equal(h.document.querySelectorAll('.amenities-section').length,1);
  assert.deepEqual([...section.querySelectorAll('.amenity-label')].map(n=>n.textContent),
    ['Wi-Fi','Estacionamento','Atende crianças']);
  assert.equal(h.errors.length,0);
});

test('An enterprise without an address can still show only its published amenities, without a fake map',async t=>{
  const data=catalogue();
  Object.assign(data.site,{
    address:'',address_display:'',city:'',state:'',postal_code:'',
    maps_url:'javascript:alert(1)',amenity_wifi:true,
    amenity_parking:false,amenity_children:false,amenity_accessibility:false
  });
  const h=await makePublic({data});t.after(()=>h.close());await flush();
  assert.equal(h.document.getElementById('address-card').hidden,false);
  assert.equal(h.document.getElementById('location-details').hidden,true);
  assert.equal(h.document.getElementById('map-preview').hidden,true);
  assert.equal(h.document.getElementById('maps-link').hidden,true);
  assert.equal(h.document.getElementById('location-map').hasAttribute('src'),false);
  assert.equal(h.document.getElementById('location-subtitle').textContent,
    'Confira as comodidades disponíveis.');
  assert.equal(h.document.querySelectorAll('.amenity-tile').length,1);
  assert.equal(h.document.querySelector('.amenity-label').textContent,'Wi-Fi');
  assert.equal(h.errors.length,0);
});
