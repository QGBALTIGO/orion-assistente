import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import {defaultResponse, flush, makeSuite, planState} from './harness.mjs';

const moduleSource=await readFile(new URL('../app/static/orion-delivery-catalog.js',import.meta.url),'utf8');
const editorSource=await readFile(new URL('../app/static/orion-delivery-editor.js',import.meta.url),'utf8');
const preview=planState('none',{delivery_catalog_preview:true});
const variant='54000000-0000-4000-8000-000000000001';
const product='54000000-0000-4000-8000-000000000002';

test('catalogue uses the product page, saves the model and opens stock history in-place',async t=>{
  let saved=null;
  const plan=preview;
  const h=await makeSuite({initialRoute:'produtos-delivery',permissions:plan,
    configureWindow:window=>{window.eval(editorSource);window.eval(moduleSource);},
    respond:(url,method,payload)=>{
      if (!url.includes('/delivery/catalog')) return defaultResponse(url);
      if (method==='POST'&&url.endsWith('/adjustments')) {
        Object.assign(saved,{quantity_on_hand:8,inventory_revision:3});
        return {variant_id:variant,quantity_on_hand:8,revision:3};
      }
      if (method==='POST') {
        saved={variant_id:variant,product_id:product,name:payload.product.name,variant_name:payload.variant.name,
          price_kind:'fixed',unit_price_cents:payload.variant.unit_price_cents,product_active:true,variant_active:true,
          mode:'tracked',valid_until:new Date(Date.now()+60000).toISOString(),
          quantity_on_hand:payload.initial_quantity,reserved_quantity:0,inventory_revision:2,...payload.details};
        return {product_id:product,variant_id:variant};
      }
      if (url.includes('/history')) return {items:[{id:'evt',created_at:new Date().toISOString(),name:saved.name,
        variant_name:saved.variant_name,action:'observed',details:{movement:'purchase',quantity:3,after:8}}],offset:0,limit:25,has_more:false};
      return {items:saved&&!url.includes('search=Inexistente')?[saved]:[],categories:saved?['Água']:[],offset:0,limit:25,has_more:false};
    },
  });
  t.after(()=>h.close());
  await flush();
  assert.ok(h.document.querySelector('#osNav [data-os-route=produtos-delivery]'));
  assert.match(h.document.querySelector('#osDeliveryResults').textContent,/Nenhum produto/);
  h.click('[data-os-action=delivery-create]');
  const form=h.document.querySelector('#osDeliveryProductForm');
  assert.equal(h.document.querySelector('#osPageBody h1').textContent,'Novo produto');
  assert.equal(form.querySelector('details').open,false);
  form.elements.product_model.value='agua-20l';
  form.elements.product_model.dispatchEvent(new h.window.Event('change',{bubbles:true}));
  assert.equal(form.elements.name.value,'Água mineral 20 L');
  assert.equal(form.elements.editor_category.value,'Água');
  assert.equal(form.elements.price.value,'');
  assert.equal(form.querySelector('[data-delivery-image=agua-20l]').getAttribute('aria-pressed'),'true');
  h.fill('#osDeliveryProductForm [name=name]','Água de avaliação');
  h.fill('#osDeliveryProductForm [name=price]','12,50');
  h.fill('#osDeliveryProductForm [name=initial]','5');
  h.click('#osDeliveryProductForm [name=allow_storefront]');
  form.querySelector('button[type=submit]').click();
  await flush();
  const created=h.calls.find(x=>x.method==='POST');
  assert.equal(created.data.product.name,'Água de avaliação');
  assert.equal(created.data.variant.unit_price_cents,1250);
  assert.equal(created.data.variant.name,'Unidade');
  assert.equal(created.data.profile.preset_image,'agua-20l');
  assert.equal(created.data.details.allow_storefront,true);
  assert.equal(created.data.initial_quantity,5);
  assert.ok(created.data.idempotency_key);
  h.click('[data-os-action=delivery-adjust]');
  h.fill('#osDialog [name=quantity]','3');
  h.document.querySelector('#osDialog button[type=submit]').click();
  await flush();
  assert.match(h.document.querySelector('#osDeliveryResults').textContent,/8 un em estoque/);
  const host=h.document.getElementById('osPageBody');
  h.fill('#osDeliveryFilters [name=search]','Inexistente');
  h.click('#osDeliveryFilters button[type=submit]');
  await flush();
  assert.match(h.document.querySelector('#osDeliveryResults').textContent,/Nenhum produto/);
  assert.equal(h.document.getElementById('osPageBody'),host);
  h.click('[data-delivery-tab=history]');
  await flush();
  assert.match(h.document.querySelector('#osDeliveryResults').textContent,/Entrada/);
  h.click('[data-delivery-tab=catalog]');
  await flush();
  assert.equal(h.document.querySelector('#osDeliveryFilters [name=search]').value,'Inexistente');
  assert.equal(h.document.getElementById('osPageBody'),host);
  assert.deepEqual(h.errors,[]);
});

test('delivery requires a server entitlement and viewers get read-only navigation',async t=>{
  const unopened=await makeSuite({initialRoute:'produtos-delivery'});
  t.after(()=>unopened.close());
  await flush();
  assert.equal(unopened.document.querySelector('#osNav [data-os-route=produtos-delivery]'),null);
  assert.equal(unopened.calls.some(x=>x.path?.includes('/delivery/')),false);
  const viewer=await makeSuite({initialRoute:'produtos-delivery',role:'viewer',permissions:preview,
    configureWindow:window=>window.eval(moduleSource),respond:url=>url.includes('/delivery/catalog')
      ? {items:[],categories:[],offset:0,limit:25,has_more:false} : defaultResponse(url)});
  t.after(()=>viewer.close());
  await flush();
  assert.ok(viewer.document.querySelector('#osNav [data-os-route=produtos-delivery]'));
  assert.equal(viewer.document.querySelector('[data-os-action=delivery-create]'),null);
});
