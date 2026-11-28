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

test('Kanban pages a single lane and repeating an order opens a new unquoted cart',async t=>{
  const row={id:orderId,mode:'delivery',status:'fulfilled',revision:2,total_cents:1250,created_at:new Date().toISOString()};
  const h=await makeSuite({initialRoute:'pedidos-delivery',permissions:preview,configureWindow:w=>w.eval(ordersSource),
    respond:p=>{
      if(p.includes('/delivery/orders?')){
        const params=new URL(p,'https://orion.test').searchParams,state=params.get('state'),offset=Number(params.get('offset'));
        return {items:state==='fulfilled'||state==='all'?[row]:[],offset,limit:25,has_more:state==='fulfilled'&&offset===0};
      }
      if(p.endsWith('/orders/'+orderId))return {...row,address:{street:'Antiga'},items:[{variant_id:variant,product_name:'Água',variant_name:'20L',quantity:2}]};
      return defaultResponse(p);
    }});
  t.after(()=>h.close());await flush();
  h.fill('[name=orderView]','board').dispatchEvent(new h.window.Event('change',{bubbles:true}));await flush();
  assert.equal(h.document.querySelectorAll('.os-delivery-board-lane').length,4);
  h.click('[data-os-action=delivery-board-next]');await flush();
  assert.ok(h.calls.some(c=>c.path.includes('state=fulfilled')&&c.path.includes('offset=25')));
  assert.equal(h.calls.some(c=>c.path.includes('state=confirmed')&&c.path.includes('offset=25')),false);
  h.click('.os-delivery-extra-actions summary');
  h.click('[data-os-action=delivery-order-repeat]');await flush();
  assert.equal(h.document.querySelector('[data-delivery-quantity]').value,'2');
  assert.equal(h.document.querySelector('[name=address_street]').value,'');
  assert.equal(h.calls.some(c=>c.method==='POST'),false);
  assert.deepEqual(h.errors,[]);
});

test('cash and Pix can be confirmed together on a counter sale without a linked customer',async t=>{
  const row={id:orderId,mode:'counter',status:'confirmed',revision:1,total_cents:1250,created_at:new Date().toISOString()};
  const h=await makeSuite({initialRoute:'pedidos-delivery',permissions:preview,configureWindow:w=>w.eval(ordersSource),
    respond:(p,m)=>p.includes('/delivery/orders?')?{items:[row],offset:0,limit:25}:
      p.endsWith('/transitions')&&m==='POST'?{id:orderId,status:'fulfilled'}:defaultResponse(p)});
  t.after(()=>h.close());await flush();
  h.click('[data-os-action=delivery-order-fulfill]');
  h.fill('[name=payment_method]','split').dispatchEvent(new h.window.Event('change',{bubbles:true}));
  assert.equal(h.document.querySelector('[data-delivery-split]').disabled,false);
  h.fill('[name=split_cash]','8,50');h.fill('[name=split_pix]','4,00');
  h.click('[name=received]');h.click('#osDialog button[type=submit]');await flush();
  const sent=h.calls.find(c=>c.path.endsWith('/transitions'));
  assert.deepEqual(structuredClone(sent?.data.tenders),[{method:'cash',amount_cents:850},{method:'pix',amount_cents:400}]);
  assert.equal(sent.data.payment_method,null);assert.deepEqual(h.errors,[]);
});

test('delivery operator explicitly selects a saved address and switching customer clears it',async t=>{
  const person='51000000-0000-4000-8000-000000000001', other='51000000-0000-4000-8000-000000000002';
  const home={id:orderId,label:'Casa',revision:1,address:{number:'18',street:'Rua A',city:'Campo Grande',neighborhood:'Centro',state:'MS'}};
  const h=await makeSuite({initialRoute:'pedidos-delivery',permissions:preview,configureWindow:win=>win.eval(ordersSource),
    respond(path) {
      if(path.includes('/order-customers?')) return {items:[{name:'Ana',id:person},{name:'Bia',id:other}]};
      if(path.endsWith('/addresses')) return {items:[home]};
      if(path.includes('/delivery/orders?')) return {items:[],offset:0,limit:25,has_more:false};
      return defaultResponse(path);
    }});
  t.after(()=>h.close());
  await flush();
  const action=name=>'[data-os-action="delivery-'+name+'"]';
  h.click(action('new-order'));
  h.fill('[name=mode]','delivery').dispatchEvent(new h.window.Event('change',{bubbles:true}));
  h.fill('[name=customer_search]','Ana');
  h.click(action('choose-customer'));await flush();
  h.click('[data-delivery-match="'+person+'"]');
  h.click(action('address-book'));await flush();
  assert.equal(h.document.querySelector('[name=address_street]').value,'');
  h.fill('[name=saved_address]',home.id).dispatchEvent(new h.window.Event('change',{bubbles:true}));
  assert.equal(h.document.querySelector('[name=address_street]').value,'Rua A');
  h.click(action('choose-customer'));await flush();
  h.click('[data-delivery-match="'+other+'"]');
  assert.equal(h.document.querySelector('[name=address_street]').value,'');
  assert.ok(h.document.querySelector('[data-delivery-address-book]').hidden);
  assert.deepEqual(h.errors,[]);
});

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
  h.fill('[name=intake_channel]','phone');
  h.fill('[name=tender_preference]','cash').dispatchEvent(new h.window.Event('change',{bubbles:true}));
  h.fill('[name=cash_change]','20,00');
  h.fill('[name=operator_note]','Portaria');
  h.click('#osDialog button[type=submit]');
  await flush();
  const created=h.calls.find(call=>call.path.endsWith('/delivery/orders')&&call.method==='POST');
  assert.equal(created?.data.change_for_cents,2000);
  assert.equal(created?.data.intake_channel,'phone');
  assert.equal(created?.data.operator_note,'Portaria');
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
