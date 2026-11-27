import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import {defaultResponse, flush, makeSuite, planState} from './harness.mjs';

const moduleSource=await readFile(new URL('../app/static/orion-delivery-logistics.js',import.meta.url),'utf8');
const preview=planState('none',{delivery_catalog_preview:true});
const revisionZero=()=>({revision:0,timezone:'America/Cuiaba',coverage_mode:'manual',areas:[],windows:[],
  scheduling_enabled:false,lead_minutes:60,horizon_days:7});

test('configures coverage and windows and adds a courier in Orion dialogs',async t=>{
  let config=revisionZero();
  let couriers=[];
  const h=await makeSuite({initialRoute:'logistica-delivery',permissions:preview,
    configureWindow:window=>window.eval(moduleSource),
    respond:(url,method,data)=>{
      if (url.includes('/delivery/logistics/couriers')) {
        if (method==='POST') {
          const newCourier={id:'23000000-0000-4000-8000-000000000001',name:data.name,
            active:data.active,phone:data.phone,revision:1};
          couriers=[newCourier];
          return newCourier;
        }
        return {items:couriers,offset:0,limit:25,has_more:false};
      }
      if (url.split('?')[0].endsWith('/delivery/logistics')) {
        if (method==='PUT') {
          assert.equal(data.expected_revision,config.revision);
          const {expected_revision,...settings}=data;
          config={...settings,revision:expected_revision+1,timezone:'America/Cuiaba'};
        }
        return config;
      }
      return defaultResponse(url);
    },
  });
  t.after(()=>h.close());
  await flush();
  const click=action=>h.click('[data-os-action='+action+']');
  const submit=async()=>{
    h.document.querySelector('#osDialog button[type=submit]').click();
    await flush();
  };
  assert.ok(h.document.querySelector('#osNav [data-os-route=logistica-delivery]'));
  click('logistics-new-area');
  h.fill('#osDialog [name=logistics_city]','Campo Grande');
  h.fill('#osDialog [name=logistics_state]','MS');
  h.fill('#osDialog [name=logistics_neighborhood]','Vila São João');
  h.fill('#osDialog [name=logistics_fee]','7,50');
    h.fill('#osDialog [name=logistics_minimum]','10,00');
    h.fill('#osDialog [name=logistics_free_from]','30,00');
    h.fill('#osDialog [name=logistics_eta]','25');
  await submit();
  assert.equal(config.areas[0]?.fee_cents,750);
    assert.equal(config.areas[0].minimum_cents,1000);
    assert.equal(config.areas[0].free_from_cents,3000);
    assert.equal(config.areas[0].eta_minutes,25);
  assert.match(h.document.querySelector('#osDeliveryLogisticsSections').textContent,/Vila São João/);
  click('logistics-coverage');
  h.fill('#osDialog [name=logistics_mode]','areas');
  await submit();
  assert.equal(config.coverage_mode,'areas');
  click('logistics-new-window');
  h.fill('#osDialog [name=logistics_weekday]','5');
  h.fill('#osDialog [name=logistics_start]','08:30');
  h.fill('#osDialog [name=logistics_end]','14:30');
  await submit();
  assert.equal(config.windows[0]?.weekday,5);
  assert.match(h.document.querySelector('#osDeliveryLogisticsSections').textContent,/Sábado/);
  click('logistics-new-courier');
  h.fill('#osDialog [name=logistics_courier_name]','Entregadora de teste');
  await submit();
  assert.equal(couriers[0]?.name,'Entregadora de teste');
  assert.match(h.document.querySelector('#osDeliveryLogisticsSections').textContent,/Entregadora de teste/);
  assert.deepEqual(h.errors,[]);
});

test('a viewer can read Delivery logistics without editing settings or couriers',async t=>{
  const h=await makeSuite({initialRoute:'logistica-delivery',role:'viewer',permissions:preview,
    configureWindow:window=>window.eval(moduleSource),
    respond:url=>url.includes('/delivery/logistics/couriers')?{items:[],offset:0,limit:25,has_more:false}:
      url.split('?')[0].endsWith('/delivery/logistics')?revisionZero():defaultResponse(url)});
  t.after(()=>h.close());
  await flush();
  assert.match(h.document.querySelector('#osDeliveryLogisticsSections').textContent,/Entregadores/);
  assert.equal(h.document.querySelector('[data-os-action=logistics-new-area]'),null);
  assert.equal(h.document.querySelector('[data-os-action=logistics-new-courier]'),null);
});

test('an editor can manage the courier roster while the owner controls public rates and windows',async t=>{
  const h=await makeSuite({initialRoute:'logistica-delivery',role:'editor',permissions:preview,
    configureWindow:window=>window.eval(moduleSource),
    respond:url=>url.includes('/delivery/logistics/couriers')?{items:[],offset:0,limit:25,has_more:false}:
      url.split('?')[0].endsWith('/delivery/logistics')?revisionZero():defaultResponse(url)});
  t.after(()=>h.close());
  await flush();
  assert.ok(h.document.querySelector('[data-os-action=logistics-new-courier]'));
  assert.equal(h.document.querySelector('[data-os-action=logistics-new-area]'),null);
  assert.equal(h.document.querySelector('[data-os-action=logistics-schedule]'),null);
});

test('owner sees real dispatch counts and stale position only on demand',async t=>{
  const h=await makeSuite({initialRoute:'logistica-delivery',permissions:preview,
    configureWindow:window=>window.eval(moduleSource),
    respond:url=>{
      if(url.endsWith('/delivery/logistics/positions'))return {summary:{waiting_dispatch:2,waiting_acceptance:1,on_route:1,
        scheduled:3,scheduled_past_due:1,unlinked_on_route:0,in_shift:1,available:1,fresh_positions:0,
        observed_at:'2026-11-27T19:00:00Z'},items:[{name:'Entregador teste',available:true,on_route:1,waiting_acceptance:1,
        shift_started_at:'2026-11-27T17:00:00Z',location_at:'2026-11-27T18:45:00Z',stale:true,latitude:-20.4,longitude:-55.7}]};
      if(url.includes('/delivery/logistics/couriers'))return {items:[],offset:0,limit:25,has_more:false};
      if(url.split('?')[0].endsWith('/delivery/logistics'))return revisionZero();
      return defaultResponse(url);
    }});
  t.after(()=>h.close());await flush();
  assert.equal(h.calls.some(x=>x.path.endsWith('/logistics/positions')),false);
  h.click('[data-os-action=logistics-positions]');await flush();
  const region=h.document.querySelector('[data-logistics-positions]');
  assert.match(region.textContent,/Aguardando despacho/);
  assert.match(region.textContent,/Desatualizada/);
  assert.match(region.textContent,/Entregador teste/);
  assert.equal(region.querySelector('a[href^="https://www.openstreetmap.org"]').getAttribute('href'),
    'https://www.openstreetmap.org/?mlat=-20.4&mlon=-55.7');
  assert.deepEqual(h.errors,[]);
});
