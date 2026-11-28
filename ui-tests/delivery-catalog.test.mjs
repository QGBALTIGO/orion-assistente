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
  assert.equal(h.document.querySelector('.os-delivery-stock-grid > div > strong').textContent,'8');
  assert.ok(h.calls.some(call=>call.path.includes('include_channels=true')));
  assert.ok(h.document.querySelector('#appView').classList.contains('os-delivery-workspace'));
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

test('supplier purchase editor submits a valued item and payable through the inventory entry point',async t=>{
  const vendor='54000000-0000-4000-8000-000000000003';
  const h=await makeSuite({initialRoute:'produtos-delivery',permissions:preview,
    configureWindow:w=>w.eval(moduleSource),respond:(url,method)=>{
      const page={items:[],offset:0,limit:25,has_more:false};
      if (url.includes('/delivery/catalog')) return {...page,categories:[],items:url.includes('state=active')?
        [{variant_id:variant,name:'Água',variant_name:'20 L'}]:[]};
      if (url.includes('/delivery/suppliers')) return {...page,items:[{id:vendor,name:'Fonte',revision:1,active:true}]};
      if (url.includes('/delivery/purchases')) return method==='POST'?{id:product}:page;
      return defaultResponse(url);
    }});
  t.after(()=>h.close());await flush();
  h.click('[data-delivery-tab=suppliers]');await flush();
  assert.match(h.document.querySelector('#osDeliveryResults').textContent,/Fonte/);
  h.click('[data-delivery-tab=purchases]');await flush();h.click('[data-os-action=delivery-purchase]');await flush();
  const form=h.document.querySelector('#osDialog form');
  form.elements.selected_supplier.value=vendor;form.elements.selected_product.value=variant;
  h.fill('#osDialog [name=purchase_reference]','NF 157');
  h.fill('#osDialog [name=line_quantity]','3');h.fill('#osDialog [name=line_cost]','4,25');
  h.click('[data-purchase-add]');assert.match(form.querySelector('[data-purchase-lines]').textContent,/12,75/);
  h.fill('#osDialog [name=purchase_due]','2027-01-15');form.querySelector('[type=submit]').click();await flush();
  const sent=h.calls.find(x=>x.method==='POST'&&x.path.includes('/purchases'));
  assert.equal(sent?.data.reference,'NF 157');assert.equal(sent.data.due_on,'2027-01-15');
  assert.deepEqual(structuredClone(sent.data.items),[{variant_id:variant,quantity:3,unit_cost_cents:425}]);
  assert.ok(sent.data.idempotency_key);assert.deepEqual(h.errors,[]);
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
  // Delivery uses its own semantic icons instead of shared list/send/gear glyphs.
  const expectedDeliveryIcons = {
    "pedidos-delivery": "M5 3l2 2 2-2 3 2 3-2 2 2 2-2v18l-2-2-2 2-3-2-3 2-2-2-2 2V3ZM8 9h8M8 13h8M8 17h5",
    "entregas-delivery": "M3 5h12v13H9M3 5v13h2M15 9h4l3 4v5h-2M15 18h1M9 18a2 2 0 1 1-4 0 2 2 0 0 1 4 0ZM20 18a2 2 0 1 1-4 0 2 2 0 0 1 4 0Z",
    "vasilhames-delivery": "M9 3h6v3H9zM9 6v1l-2 2a4 4 0 0 0-2 3v7a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-7a4 4 0 0 0-2-3l-2-2V6M5 13h14M5 17h14",
    "loja-delivery": "M3 9l2-5h14l2 5v2a3 3 0 0 1-6 0 3 3 0 0 1-6 0 3 3 0 0 1-6 0V9ZM3 9h18M5 14v7h14v-7M9 21v-5h6v5",
    "vendas-prazo-delivery": "M13 21H5a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h9l5 5v3M14 3v5h5M8 9h3M8 13h4M8 17h2M22 17a5 5 0 1 1-10 0 5 5 0 0 1 10 0ZM17 14v3l2 1",
    "logistica-delivery": "M8 18h7a3.5 3.5 0 0 0 0-7H9a2.5 2.5 0 0 1 0-5h7M8 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0ZM22 6a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z"
  };
  const actualPaths=[];
  for (const [route,expectedPath] of Object.entries(expectedDeliveryIcons)) {
    const svg=viewer.document.querySelector('#osNav [data-os-route="'+route+'"] svg');
    assert.ok(svg,route);
    assert.equal(svg.getAttribute('viewBox'),'0 0 24 24');
    assert.equal(svg.getAttribute('aria-hidden'),'true');
    const path=svg.querySelector('path').getAttribute('d');
    assert.equal(path,expectedPath,route);
    actualPaths.push(path);
  }
  assert.equal(new Set(actualPaths).size,actualPaths.length);
  assert.equal(viewer.document.querySelector('[data-os-action=delivery-create]'),null);
});

test('Delivery CRM and ledger work with only their basic entitlements, without agenda or staff permissions',async t=>{
  const base=planState('none',{delivery_catalog_preview:true});
  const features=[...base.features,'clients.manage','finance.basic'];
  const plan={...base,features,permissions:Object.fromEntries(features.map(x=>[x,true]))};
  const h=await makeSuite({initialRoute:'clientes',permissions:plan,respond:(path)=>{
    if(path.includes('/management/customers'))return {items:[],total:0};
    if(path.includes('/management/finance/summary'))return {summary:{income_cents:0,expense_cents:0,count:0}};
    if(path.includes('/management/finance?'))return {items:[],total:0};
    if(path.includes('/professionals')||path.includes('/appointments/series')||path.includes('/finance/receipts'))throw Error('Unexpected agenda or professional dependency');
    return defaultResponse(path);
  }});
  t.after(()=>h.close());await flush();
  assert.ok(h.document.querySelector('[data-os-action=new-customer]'));
  assert.equal(h.document.querySelector('[data-os-customer-status=fixed]'),null);
  assert.equal(h.document.querySelector('[data-os-customer-status=at_risk]'),null);
  h.click('[data-os-customer-status=inactive]');await flush();
  assert.match(h.document.querySelector('#osPageBody').textContent,/Nenhum cliente inativo/);
  h.click('#osNav [data-os-route=financeiro]');await flush();
  assert.ok(h.document.querySelector('.os-finance-metrics'));
  assert.ok(h.document.querySelector('#osPageBody [data-os-route=vendas-prazo-delivery]'));
  assert.equal(h.document.querySelector('[data-os-action=receipt-settings]'),null);
  assert.equal(h.calls.some(x=>/\/professionals|\/appointments\/series|\/finance\/receipts/.test(x.path)),false);
  h.click('[data-os-action=new-expense]');await flush();
  assert.ok(h.document.querySelector('#osDialog[open] [data-os-form=finance]'));
  assert.deepEqual(h.errors,[]);
});

test('Delivery customer opens a commercial history without scheduling permissions',async t=>{
  const base=planState('none',{delivery_catalog_preview:true});
  const features=[...base.features,'clients.manage','finance.basic'];
  const plan={...base,features,permissions:Object.fromEntries(features.map(x=>[x,true]))};
  const id='54000000-0000-4000-8000-000000000003';
  const record={id,name:'Cliente de teste',active:true,version:1,created_at:'2026-11-27T10:00:00Z'};
  const profile={summary:{completed:1,purchased_cents:2000,received_cents:1000,outstanding_cents:1000,
    average_ticket_cents:2000,last_purchase_at:'2026-11-27T12:00:00Z',average_interval_days:null,open_orders:0},
    items:[{id,created_at:'2026-11-27T12:00:00Z',status:'fulfilled',mode:'pickup',sales_channel:'phone',total_cents:2000,
      outstanding_cents:1000,receipt_status:'pending'}],offset:0,limit:25,has_more:false,
    favorites:[{product:'Água',variant:'20 L',quantity:2}],containers:[{name:'Garrafão',quantity:1}],address_labels:['Casa']};
  const h=await makeSuite({initialRoute:'clientes',permissions:plan,respond:path=>{
    if(path.includes('/delivery/order-customers/')&&path.includes('/profile?'))return profile;
    if(path.endsWith('/management/customers/'+id))return record;
    if(path.includes('/management/customers?'))return {items:[record],total:1};
    return defaultResponse(path);
  }});
  t.after(()=>h.close());await flush();
  h.click('[data-os-customer="'+id+'"]');await flush();
  const panel=h.document.querySelector('#osDeliveryCustomerProfile');
  assert.ok(panel);
  assert.match(panel.textContent,/Histórico de pedidos/);
  assert.match(panel.textContent,/Garrafão/);
  assert.match(panel.textContent,/Casa/);
  assert.match(panel.textContent,/Saldo a prazo/);
  assert.equal(h.calls.some(x=>x.path.includes('/appointments')),false);
  assert.deepEqual(h.errors,[]);
});

test('Delivery owner opens the connection-only WhatsApp device module, not subscription automation',async t=>{
  const base=planState('none',{delivery_catalog_preview:true});
  const features=[...base.features,'clients.manage','finance.basic','devices.manage'];
  const plan={...base,features,permissions:Object.fromEntries(features.map(x=>[x,true]))};
  const mounts=[];
  let assistant={device_id:'54000000-0000-4000-8000-000000000003',revision:1,enabled:false,eligible_products:1};
  const owner=await makeSuite({initialRoute:'whatsapp-delivery',permissions:plan,
    respond:(path,method,data)=>{
      if(path.endsWith('/devices/delivery-assistant')){
        if(method==='PUT'){assert.equal(data.expected_revision,assistant.revision);assistant={...assistant,enabled:data.enabled,revision:assistant.revision+1};}
        return assistant;
      }
      return defaultResponse(path);
    },
    configureWindow:window=>{window.OrionSubscriptionDevices={mount:args=>mounts.push({host:args.host.id,connectionOnly:args.connectionOnly}),unmount(){}};}});
  t.after(()=>owner.close());await flush();
  assert.ok(owner.document.querySelector('#osNav [data-os-route=whatsapp-delivery]'));
  assert.deepEqual(mounts,[{host:'osSubscriptionDevices',connectionOnly:true}]);
  assert.match(owner.document.querySelector('#osDeliveryAssistant').textContent,/Atendente virtual/);
  owner.click('[data-delivery-assistant-toggle]');await flush();
  assert.equal(assistant.enabled,true);
  assert.match(owner.document.querySelector('#osDeliveryAssistant').textContent,/Pausar atendente/);
  assert.equal(owner.document.querySelector('#osNav [data-os-route=chatbot]'),null);
  const viewer=await makeSuite({initialRoute:'whatsapp-delivery',role:'viewer',permissions:plan});
  t.after(()=>viewer.close());await flush();
  assert.equal(viewer.document.querySelector('#osNav [data-os-route=whatsapp-delivery]'),null);
  assert.deepEqual(owner.errors,[]);
});
