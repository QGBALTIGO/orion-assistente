import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import {defaultResponse,flush,makeSuite,planState} from './harness.mjs';

const sources=await Promise.all(['orion-delivery-orders.js','orion-delivery-reports.js'].map(file=>
  readFile(new URL('../app/static/'+file,import.meta.url),'utf8')));
const permissions=planState('none',{delivery_catalog_preview:true});
const period={start_date:'2026-11-01',end_date:'2026-11-27',timezone:'America/Cuiaba'};

test('report tabs preserve criteria and export the filtered set without pagination',async t=>{
  const h=await makeSuite({
    initialRoute:'relatorio-delivery',
    permissions,
    configureWindow:window=>sources.forEach(source=>window.eval(source)),
    respond:path=>{
      if(path.includes('/delivery/reports/sales?')) {
        return {period,items:[],offset:0,limit:25,summary:{orders:0,cancelled:0,paid_cents:0,pending_cents:0}};
      }
      if(path.includes('/delivery/reports/operational/')) {
        const params=new URL(path,'https://orion.test').searchParams;
        const offset=Number(params.get('offset')||0);
        return {
          period:path.includes('/reorder?')?null:period,columns:[{key:'product',label:'Produto',style:'text'}],
          items:[{product:params.get('search')||'Água sintética'}],
          summary:[{label:'Variações vendidas',style:'number',value:1,icon:'box'}],
          limit:25,offset,has_more:offset===0,
        };
      }
      return defaultResponse(path);
    },
  });
  t.after(()=>h.close());
  await flush();
  assert.match(h.document.querySelector('#osDeliveryReportPanel').textContent,/Relatório de vendas/);
  h.click('[data-delivery-report-tab="products"]');
  await flush();
  assert.match(h.document.querySelector('#osDeliveryReportPanel').textContent,/Água sintética/);
  h.fill('#osDeliveryReportPanel [name="search"]','<Caixa de teste>');
  h.fill('#osDeliveryReportPanel [name="mode"]','pickup');
  h.submit('#osDeliveryReportPanel form');
  await flush();
  assert.equal(h.document.querySelector('#osDeliveryReportPanel tbody td').textContent,'<Caixa de teste>');
  assert.equal(h.document.querySelector('#osDeliveryReportPanel tbody td').children.length,0);
  const link=h.document.querySelector('#osDeliveryReportPanel a[download]');
  assert.match(link.pathname,/\/reports\/operational\/products\/csv$/);
  const criteria=new URL(link.href).searchParams;
  assert.equal(criteria.get('search'),'<Caixa de teste>');
  assert.equal(criteria.get('mode'),'pickup');
  assert.equal(criteria.get('start'),period.start_date);
  assert.equal(criteria.has('offset'),false);

  h.click('#osDeliveryReportPanel [data-os-action="delivery-ops-next"]');
  await flush();
  const last=h.calls.filter(call=>call.path.includes('/reports/operational/products?')).at(-1);
  assert.equal(new URL(last.path,'https://orion.test').searchParams.get('offset'),'25');
  h.document.querySelector('[data-delivery-report-tab="products"]')
    .dispatchEvent(new h.window.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));
  await flush();
  assert.equal(h.document.querySelector('[role="tab"][aria-selected="true"]').textContent,'Clientes');
  h.click('[data-delivery-report-tab="products"]');
  await flush();
  assert.equal(h.document.querySelector('#osDeliveryReportPanel [name="search"]').value,'<Caixa de teste>');
  h.click('[data-delivery-report-tab="activity"]');
  await flush();
  assert.match(h.document.querySelector('#osDeliveryReportPanel .od-report-heading h2').textContent,/Rastro de atividades/);
  h.fill('#osDeliveryReportPanel [name="status"]','orders');
  h.submit('#osDeliveryReportPanel form');
  await flush();
  const activity=h.document.querySelector('#osDeliveryReportPanel a[download]');
  assert.equal(new URL(activity.href).searchParams.get('status'),'orders');
  h.click('[data-delivery-report-tab="payments"]');await flush();
  assert.match(h.document.querySelector('#osDeliveryReportPanel .od-report-heading h2').textContent,/Recebimentos/);
  h.fill('#osDeliveryReportPanel [name=status]','cash');h.submit('#osDeliveryReportPanel form');await flush();
  assert.equal(new URL(h.document.querySelector('#osDeliveryReportPanel a[download]').href).searchParams.get('status'),'cash');
  h.click('[data-delivery-report-tab="reorder"]');await flush();
  assert.match(h.document.querySelector('#osDeliveryReportPanel .od-report-heading h2').textContent,/recompra/);
  assert.deepEqual(h.errors,[]);
});
