import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {makeSuite,defaultResponse,flush,planState} from './harness.mjs';
const preview=planState('none',{delivery_catalog_preview:true});
const orderSource=await readFile(new URL('../app/static/orion-delivery-orders.js',import.meta.url),'utf8');
const catalogSource=await readFile(new URL('../app/static/orion-delivery-catalog.js',import.meta.url),'utf8');
const orderId='54000000-0000-4000-8000-000000000013',variant='54000000-0000-4000-8000-000000000011';
test('orders show real channel, filtered totals, safe contact and preserved secondary actions',async t=>{
  const row={id:orderId,status:'confirmed',revision:1,mode:'delivery',created_at:'2026-11-28T14:00:00Z',customer_name:'<Cliente>',
    sales_channel:'whatsapp',total_cents:1750};
  const h=await makeSuite({initialRoute:'pedidos-delivery',permissions:preview,configureWindow:w=>w.eval(orderSource),
    respond:path=>{
      if(path.includes('/delivery/orders?'))return {items:[row],offset:0,limit:25,has_more:false,summary:{orders:7,whatsapp:3,confirmed:4,dispatched:2,fulfilled:1,cancelled:0}};
      if(path.endsWith('/orders/'+orderId))return {...row,storefront:{customer_name:'<Cliente>',contact_e164:'+5567999999902'},items:[],history:[]};
      return defaultResponse(path);
    }});
  t.after(()=>h.close());await flush();
  assert.equal(h.document.querySelector('.os-delivery-order-head h3').textContent,'<Cliente>');
  assert.equal(h.document.querySelector('.os-delivery-channel').textContent,'WhatsApp');
  assert.equal(h.document.querySelector('.os-delivery-list-summary .os-metric strong').textContent,'7');
  assert.ok(h.document.querySelector('.os-delivery-title .os-delivery-kicker svg'));
  assert.equal(h.document.querySelector('.os-delivery-extra-actions').open,false);
  h.click('.os-delivery-extra-actions summary');
  assert.equal(h.document.querySelector('.os-delivery-extra-actions').open,true);
  assert.ok(h.document.querySelector('[data-os-action=delivery-order-cancel]'));
  const host=h.document.querySelector('#osPageBody');
  assert.equal(h.document.querySelector('#osDeliveryOrderFilters .od-advanced').open,false);
  h.click('#osDeliveryOrderFilters .od-advanced summary');
  assert.equal(h.document.querySelector('#osDeliveryOrderFilters .od-advanced').open,true);
  h.fill('[name=channel]','whatsapp');h.submit('#osDeliveryOrderFilters');await flush();
  assert.equal(host,h.document.querySelector('#osPageBody'));
  assert.ok(h.calls.some(c=>c.path.includes('channel=whatsapp')&&c.path.includes('include_summary=true')));
  h.click('[data-os-action=delivery-order-clear]');await flush();
  assert.equal(h.document.querySelector('[name=channel]').value,'all');
  assert.equal(h.document.querySelector('.od-advanced').open,false);
  assert.ok(h.calls.at(-1).path.includes('include_summary=true'));
  h.click('[data-os-action=delivery-order-detail]');await flush();
  const link=h.document.querySelector('#osDialog .os-delivery-contact a');
  assert.equal(link.href,'https://wa.me/5567999999902');
  assert.ok(link.rel.includes('noopener'));
  assert.equal(h.errors.length,0);
});
test('catalogue displays server-confirmed approval without assuming active device',async t=>{
  const rows=['ready','review','not_selected','stock_unavailable'].map((status,i)=>({variant_id:variant+'-'+i,product_id:variant,name:'Água '+i,variant_name:'20 L',
    product_active:true,variant_active:true,price_kind:'fixed',unit_price_cents:1250,quantity_on_hand:5,reserved_quantity:2,
    mode:'tracked',valid_until:'2099-01-01T00:00:00Z',minimum_quantity:1,unit:'un',whatsapp_catalogue_status:status}));
  const h=await makeSuite({initialRoute:'produtos-delivery',permissions:preview,configureWindow:w=>w.eval(catalogSource),
    respond:path=>path.includes('/delivery/catalog?')?{items:rows,categories:[],offset:0,limit:25,has_more:false}:defaultResponse(path)});
  t.after(()=>h.close());await flush();
  assert.deepEqual([...h.document.querySelectorAll('.os-delivery-catalog-channel strong')].map(e=>e.textContent),
    ['Apto no catálogo','Revisar aprovação','Não selecionado','Sem estoque válido']);
  assert.equal(h.document.querySelector('.os-delivery-stock-grid > div:last-child strong').textContent,'3');
  assert.ok(h.document.querySelector('#osPageBody [data-os-route="loja-delivery"]'));
  assert.equal(h.errors.length,0);
});
test('Delivery theme is not enabled on a different niche',async t=>{
  const h=await makeSuite({initialRoute:'clientes',permissions:planState()});
  t.after(()=>h.close());await flush();
  assert.equal(h.document.querySelector('#appView').classList.contains('os-delivery-workspace'),false);
  assert.equal(h.document.querySelector('.os-delivery-kicker'),null);
});
