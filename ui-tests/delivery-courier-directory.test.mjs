import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import {defaultResponse,flush,makeSuite,planState} from './harness.mjs';
const source=await readFile(new URL('../app/static/orion-delivery-logistics.js',import.meta.url),'utf8');
const preview=planState('none',{delivery_catalog_preview:true});
const ID='23000000-0000-4000-8000-000000000001',PORTAL='/entregador/23000000-0000-4000-8000-000000000002/';

test('owner finds dedicated Entregadores and enrolls verified-email access in one action',async t=>{
  let rows=[],enrollment=null;
  const h=await makeSuite({initialRoute:'entregadores-delivery',permissions:preview,
    configureWindow:w=>w.eval(source),
    respond:(url,method,data)=>{
      const path=url.split('?')[0];
      if(path.endsWith('/logistics/couriers/enroll')&&method==='POST'){
        enrollment=data;const c={id:ID,name:data.name,phone:data.phone,active:true,revision:1,email:data.email,access_revision:1,linked:false,open_orders:0};
        rows=[c];return c;
      }
      if(path.endsWith('/logistics/couriers/access-directory'))return {items:rows,offset:0,limit:25,has_more:false,portal_path:PORTAL,summary:{total:rows.length,active:rows.length,authorized:rows.length,in_shift:0}};
      if(path.endsWith('/logistics/couriers/'+ID+'/portal'))return {email:rows[0].email,revision:1,portal_path:PORTAL};
      return defaultResponse(url);
    }});
  t.after(()=>h.close());await flush();
  assert.ok(h.document.querySelector('#osNav [data-os-route=entregadores-delivery]'));
  assert.equal(h.document.querySelector('#osPageBody h1').textContent,'Entregadores');
  assert.ok(h.document.querySelector('a[href="'+PORTAL+'"]'));
  h.click('[data-os-action=logistics-new-courier]');
  h.fill('#osDialog [name=logistics_courier_name]','Entregador teste');
  h.fill('#osDialog [name=driver_email]','motorista@example.test');
  h.document.querySelector('#osDialog button[type=submit]').click();await flush();
  assert.equal(enrollment.email,'motorista@example.test');
  assert.equal(enrollment.name,'Entregador teste');
  assert.match(h.document.querySelector('#osDeliveryLogisticsSections').textContent,/Acesso liberado/);
  h.click('[data-os-action=logistics-driver-access]');await flush();
  assert.match(h.document.querySelector('#osDialog [name=driver_portal]').value,/\?primeiro-acesso=1$/);
  assert.equal(h.document.querySelector('#osDialog [data-copy-driver]').disabled,false);
  assert.deepEqual(h.errors,[]);
});

test('viewer can see roster but cannot create or view access grants',async t=>{
  const h=await makeSuite({initialRoute:'entregadores-delivery',role:'viewer',permissions:preview,
    configureWindow:w=>w.eval(source),
    respond:url=>url.includes('/logistics/couriers?')?{items:[{id:ID,name:'Pessoa teste',active:true,revision:1}],offset:0,limit:25,has_more:false}:defaultResponse(url)});
  t.after(()=>h.close());await flush();
  assert.match(h.document.querySelector('#osDeliveryLogisticsSections').textContent,/Pessoa teste/);
  assert.equal(h.document.querySelector('[data-os-action=logistics-new-courier]'),null);
  assert.equal(h.document.querySelector('[data-os-action=logistics-driver-access]'),null);
  assert.equal(h.calls.some(c=>c.path.includes('/access-directory')),false);
});
