import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {makeSuite,flush,deferred,defaultResponse,customerId} from './harness.mjs';

const created='2026-10-25T23:42:00+00:00';
const record={
  id:customerId,name:'Cliente de teste',version:3,active:true,
  phone:'+5511912345678',email:null,created_at:created,
  total_appointments:8,completed_appointments:7,total_spent_cents:18000,loyalty_points:5,
};
const makeVisit=(n,services=[{name:'Corte tradicional',duration_min:40,price_cents:4500}])=>({
  id:'40000000-0000-4000-8000-'+String(n).padStart(12,'0'),
  protocol:'AG-TEST'+n,
  start_at:'2026-10-'+String(20-n).padStart(2,'0')+'T13:00:00Z',
  end_at:'2026-10-'+String(20-n).padStart(2,'0')+'T13:40:00Z',
  professional_name:'Barbeiro da equipe',
  total_cents:4500,original_total_cents:4500,discount_cents:0,services,
});
function responder({items=[makeVisit(0)],total=items.length,onHistory}={}){
  return (path,method)=>{
    if(path.includes('/customers/'+customerId+'/services?')){
      onHistory?.(path,method);
      const offset=Number(new URL(path,'https://orion.test').searchParams.get('offset')||0);
      return {items:items.slice(offset,offset+6),total,offset,limit:6};
    }
    if(path.includes('/management/customers?'))return {items:[record],total:1};
    return defaultResponse(path);
  };
}
async function fixture(t,options={}){
  const h=await makeSuite({initialRoute:'clientes',...options});
  t.after(()=>h.close());await flush();return h;
}

test('cadastro exibe data e hora locais, inclusive na prévia, sem alterar o valor original',async t=>{
  const h=await fixture(t,{respond:responder()});
  const item=h.document.querySelector('[data-os-customer="'+customerId+'"]');
  assert.ok(item);
  assert.match(h.document.getElementById('page-suite').textContent,/25\/10\/2026/);
  h.click('[data-os-customer="'+customerId+'"]');
  const createdBox=h.document.querySelector('.os-client-created');
  assert.ok(createdBox,'registro de data logo no início da edição');
  assert.match(createdBox.textContent,/CADASTRADO EM/);
  assert.match(createdBox.textContent,/25\/10\/2026 às 20:42/,'usa America/Sao_Paulo e não a data de hoje do navegador');
  assert.equal(h.document.querySelectorAll('.os-client-stats .os-stat').length,3);
  assert.ok(h.document.querySelector('details.os-client-history'));
  assert.equal(h.calls.filter(x=>x.path.includes('/services?')).length,0,'o histórico não carrega até abrir');
  assert.deepEqual(h.calls.filter(x=>x.method!=='GET'),[]);
  assert.deepEqual(h.errors,[]);
});

test('histórico expansível traz serviços agrupados, valores, desconto, profissional e escapa conteúdo remoto',async t=>{
  const serviceName='<img src=x onerror=alert(1)>';
  const visits=[{...makeVisit(0,[{name:serviceName,duration_min:40,price_cents:6000},
     {name:'Barba',duration_min:15,price_cents:2500}]),discount_cents:1500,
     original_total_cents:8500,total_cents:7000,professional_name:'<svg onload=alert(1)>'}];
  const h=await fixture(t,{respond:responder({items:visits,total:1})});
  h.click('[data-os-customer="'+customerId+'"]');
  const details=h.document.querySelector('#osCustomerServiceHistory');
  assert.equal(details.open,false);
  details.querySelector('summary').click();
  assert.equal(h.calls.filter(x=>x.path.includes('/services?')).length,1,
    'o primeiro toque consulta sem depender do evento toggle tardio');
  await flush();
  assert.equal(details.open,true);
  assert.equal(h.calls.filter(x=>x.path.includes('/services?')).length,1);
  assert.equal(details.querySelectorAll('.os-client-history-visit').length,1);
  assert.equal(details.querySelectorAll('.os-client-visit-services li').length,2);
  assert.match(details.textContent,/Barba/);
  assert.match(details.textContent,/40 min/);
  assert.match(details.textContent,/7.000,00|70,00/);
  assert.match(details.textContent,/Desconto aplicado/);
  assert.match(details.textContent,/Atendimento finalizado/);
  assert.equal(details.querySelector('img[src=x]'),null);
  assert.equal(details.querySelector('[onload]'),null);
  assert.equal(details.querySelectorAll('ul li span.os-client-service-dot').length,2);
  details.querySelector('summary').click();await flush();
  details.querySelector('summary').click();await flush();
  assert.equal(h.calls.filter(x=>x.path.includes('/services?')).length,1,'fechar e reabrir conserva a resposta');
  assert.deepEqual(h.errors,[]);
});

test('paginação traz visitas antigas sem duplicar e funciona com acesso somente leitura',async t=>{
  const items=Array.from({length:7},(_,i)=>makeVisit(i));
  const h=await fixture(t,{role:'viewer',respond:responder({items,total:7})});
  h.click('[data-os-customer="'+customerId+'"]');
  assert.equal(h.document.querySelector('[data-os-form=customer] fieldset').disabled,true);
  h.click('#osCustomerServiceHistory summary');await flush();
  assert.equal(h.document.querySelectorAll('.os-client-history-visit').length,6);
  const more=h.document.querySelector('[data-os-action=customer-history-more]');
  assert.ok(more&&!more.disabled,'leitor pode paginar mesmo com campos de edição desabilitados');
  more.click();await flush();
  assert.equal(h.document.querySelectorAll('.os-client-history-visit').length,7);
  assert.equal(h.document.querySelectorAll('.os-client-history-visit').length,
    new Set([...h.document.querySelectorAll('.os-client-history-visit')].map(x=>x.textContent)).size);
  assert.equal(h.document.querySelector('[data-os-action=customer-history-more]'),null);
  const historyReads=h.calls.filter(x=>x.path.includes('/services?'));
  assert.equal(historyReads.length,2);
  assert.match(historyReads[0].path,/offset=0/);
  assert.match(historyReads[1].path,/offset=6/);
  assert.deepEqual(h.calls.filter(x=>x.method!=='GET'),[]);
  assert.deepEqual(h.errors,[]);
});

test('histórico vazio ou com erro mostra mensagens próprias e recuperação sem alterar cadastro',async t=>{
  let failed=true,requests=0;
  const h=await fixture(t,{respond:(path,method)=>{
    if(path.includes('/services?')){
      requests++;
      if(failed)throw new Error('Falha temporária de consulta');
      return {items:[],total:0,limit:6,offset:0};
    }
    return responder({items:[]})(path,method);
  }});
  h.click('[data-os-customer="'+customerId+'"]');
  h.click('#osCustomerServiceHistory summary');await flush();
  assert.match(h.document.querySelector('#osCustomerHistoryBody').textContent,/Falha temporária/);
  failed=false;
  h.click('[data-os-action=customer-history-retry]');await flush();
  assert.equal(requests,2);
  assert.match(h.document.querySelector('#osCustomerHistoryBody').textContent,/Nenhum serviço finalizado/);
  assert.deepEqual(h.errors,[]);
});

test('resposta atrasada jamais entra em outro cliente ou em tela fechada',async t=>{
  const pending=deferred();
  const h=await fixture(t,{respond:(path,method)=>{
    if(path.includes('/services?'))return pending.promise;
    return responder()(path,method);
  }});
  h.click('[data-os-customer="'+customerId+'"]');
  h.click('#osCustomerServiceHistory summary');await flush();
  h.click('#osDialog [data-os-action=close-dialog]');await flush();
  pending.resolve({items:[makeVisit(0)],total:1,limit:6,offset:0});await flush();
  assert.equal(h.document.getElementById('osDialog').open,false);
  assert.equal(h.document.getElementById('osDialog').querySelector('.os-client-history-visit'),null);
  assert.deepEqual(h.errors,[]);
});

test('apresentação de histórico se adapta a 320px e aos temas do Orion',async()=>{
  const css=await readFile(new URL('../app/static/orion-suite.css',import.meta.url),'utf8');
  const js=await readFile(new URL('../app/static/orion-suite.js',import.meta.url),'utf8');
  assert.match(css,/\.os-client-history\>summary:focus-visible/);
  assert.match(css,/@media\(max-width:420px\)/);
  assert.match(css,/color-mix\(in srgb,var\(--os-tint\)/);
  assert.match(css,/var\(--os-border\)/);
  assert.match(css,/var\(--os-primary\)/);
  assert.match(js,/customerRegisteredAt\(value\)/);
  assert.match(js,/customerHistoryPanel\(record\)/);
  assert.match(js,/customer-history-more/);
});
