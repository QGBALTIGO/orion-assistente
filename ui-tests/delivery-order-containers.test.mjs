import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import {setTimeout as sleep} from 'node:timers/promises';
import {defaultResponse,flush,makeSuite,planState} from './harness.mjs';

const ordersSource=await readFile(new URL('../app/static/orion-delivery-orders.js',import.meta.url),'utf8');
const packagingSource=await readFile(new URL('../app/static/orion-delivery-order-containers.js',import.meta.url),'utf8');
const preview=planState('none',{delivery_catalog_preview:true});
const variant='54000000-0000-4000-8000-000000000011';
const container='54000000-0000-4000-8000-000000000012';
const orderId='54000000-0000-4000-8000-000000000013';

async function waitForField(document, selector) {
  const deadline=Date.now()+3000;
  while (!document.querySelector(selector) && Date.now()<deadline) {
    await sleep(10);
  }
  assert.ok(document.querySelector(selector), 'O editor deve carregar o campo '+selector);
}

test('order editor keeps a vasilhame exchange and requests physical confirmation',async t=>{
  let order=null;
  const h=await makeSuite({
    initialRoute:'pedidos-delivery',permissions:preview,
    configureWindow(window) {
      window.eval(packagingSource);
      window.eval(ordersSource);
    },
    respond(path,method,data) {
      if (path.includes('/delivery/catalog?')) return {
        items:[{variant_id:variant,name:'Água',variant_name:'Garrafão 20 L'}],has_more:false,
      };
      if (path.includes('/delivery/containers?')) return {
        items:[{id:container,name:'Garrafão 20 L'}],has_more:false,limit:25,
      };
      if (path.endsWith('/orders/estimate')) return {
        confirmation_requirements_met:true,total_cents:1250,subtotal_cents:1250,estimate_version:'a'.repeat(64),delivery_fee_cents:0,
      };
      if (path.endsWith('/delivery/orders')&&method==='POST') {
        order={id:orderId,mode:data.mode,status:'confirmed',revision:1,total_cents:1250,
          created_at:'2026-11-27T12:00:00Z',has_container_plan:true};
        return order;
      }
      if (path.endsWith('/transitions')) {
        order={...order,status:'fulfilled',revision:2};
        return order;
      }
      if (path.includes('/delivery/orders?')) return {items:order?[order]:[],offset:0,limit:25,has_more:false};
      return defaultResponse(path);
    },
  });
  t.after(()=>h.close());
  await flush();

  h.click('[data-os-action=delivery-new-order]');
  h.click('[data-os-action=delivery-choose-items]');
  await flush();
  h.click('[data-os-action=delivery-add-item]');
  h.click('[data-container-order] summary');
  const containerSelect='[name=container-order-id-'+variant+']';
  await waitForField(h.document,containerSelect+' option[value="'+container+'"]');
  h.fill(containerSelect,container);
  h.fill('[name=container-order-exchange-'+variant+']','1');
  h.click('[data-os-action=delivery-estimate]');
  await flush();
  h.click('#osDialog button[type=submit]');
  await flush();
  const created=h.calls.find(call=>call.path.endsWith('/delivery/orders')&&call.method==='POST');
  assert.deepEqual(JSON.parse(JSON.stringify(created?.data.container_plan)), [
    {variant_id:variant,container_id:container,exchange_quantity:1,loan_quantity:0},
  ]);

  h.click('[data-os-action=delivery-order-fulfill]');
  h.fill('#osDialog [name=payment_method]','cash');
  h.click('#osDialog [name=received]');
  const checked=h.document.querySelector('#osDialog [name=containers_confirmed]');
  assert.ok(checked);
  h.click('#osDialog button[type=submit]');
  await flush();
  assert.equal(h.calls.some(call=>call.path.endsWith('/transitions')),false);
  checked.click();
  h.click('#osDialog button[type=submit]');
  await flush();
  const fulfilled=h.calls.find(call=>call.path.endsWith('/transitions'));
  assert.equal(fulfilled?.data.containers_confirmed,true);
  assert.match(h.document.getElementById('osDeliveryOrderList').textContent,/Concluído/u);
  assert.deepEqual(h.errors,[]);
});
