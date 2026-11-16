import test from 'node:test';
import assert from 'node:assert/strict';
import {makeSuite,flush,defaultResponse} from './harness.mjs';

const id='61000000-0000-4000-8000-000000000001';
const appointment='62000000-0000-4000-8000-000000000001';
async function fixture(t,{role='owner',failOnce=false,paid=false}={}){
 const policy={mode:'manual',version:0,can_save:role==='owner'};
 const row={id,appointment_id:appointment,kind:'income',entry_type:'general',description:'Atendimento AG-TESTE',
  customer_name:'Cliente',amount_cents:5000,version:1,status:paid?'paid':'pending',service_receivable:true,
  occurred_at:'2026-01-10T14:00:00Z',payment_method:'other',keep_pending:false,receipt_history:[],
  receipt_source:paid?'automatic':null,received_at:paid?'2026-01-11T14:00:00Z':null};
 let failed=false;
 const h=await makeSuite({initialRoute:'financeiro',role,respond:async(path,method,data)=>{
  if(path.endsWith('/finance/receipts/settings')){if(method==='PUT')Object.assign(policy,{mode:data.mode,version:policy.version+1});return {...policy};}
  if(path.endsWith('/finance/receipts/summary'))return {count:row.status==='pending'?1:0,amount_cents:5000};
  if(path.endsWith('/finance/receipts/'+id))return {...row,receipt_policy_mode:policy.mode};
  if(path.endsWith('/finance/'+id+'/receipt')){
   if(failOnce&&!failed){failed=true;throw new Error('Falha temporária');}
   if(data.action==='confirm')Object.assign(row,{status:'paid',receipt_source:'manual',received_at:data.received_at,payment_method:data.payment_method});
   if(data.action==='hold')row.keep_pending=true;
   if(data.action==='resume')row.keep_pending=false;
   if(data.action==='reopen')Object.assign(row,{status:'pending',keep_pending:true,received_at:null,receipt_source:null});
   row.version++;return {...row};
  }
  if(path.includes('/finance/summary?'))return {summary:{income_cents:0,expense_cents:0,balance_cents:0,count:1},daily:[]};
  if(path.includes('/finance?'))return {items:[{...row}],total:1};
  if(path.endsWith('/professionals'))return [];
  return defaultResponse(path);
 }});
 t.after(()=>h.close());await flush();return {...h,row,policy};
}

test('owner can choose 24 hours only by saving the company setting',async t=>{
 const h=await fixture(t);h.click('[data-os-action=receipt-settings]');await flush();
 assert.equal(h.document.querySelector('[name=receipt_mode]').value,'manual');
 assert.equal(h.calls.filter(c=>c.method==='PUT').length,0);
 h.fill('[name=receipt_mode]','after_24h');h.submit('[data-os-form=receipt-settings]');await flush();
 const call=h.calls.find(c=>c.method==='PUT');assert.deepEqual(JSON.parse(JSON.stringify(call.data)),{mode:'after_24h',expected_version:0});
 assert.match(h.document.body.textContent,/não verifica Pix ou cartão/);
});

test('editor can read receipt settings but cannot save them',async t=>{
 const h=await fixture(t,{role:'editor'});h.click('[data-os-action=receipt-settings]');await flush();
 assert.equal(h.document.querySelector('[name=receipt_mode]').matches(':disabled'),true);
 assert.equal(h.document.querySelector('[data-os-form=receipt-settings] [type=submit]'),null);
});

test('quick confirmation sends the existing ledger ID, version and chosen method',async t=>{
 const h=await fixture(t);h.click('[data-os-receipt]');await flush();
 assert.match(h.document.getElementById('osDialog').textContent,/50,00/);
 h.fill('[name=receipt_method]','cash');h.submit('[data-os-form=service-receipt]');await flush();
 const call=h.calls.find(c=>c.method==='POST');assert.equal(call.path.endsWith('/finance/'+id+'/receipt'),true);
 assert.equal(call.data.action,'confirm');assert.equal(call.data.payment_method,'cash');assert.equal(call.data.expected_version,1);
 assert.ok(call.data.received_at);assert.ok(call.data.idempotency_key);
 assert.equal(h.calls.filter(c=>c.method==='POST'&&c.path.endsWith('/finance')).length,0);
 assert.match(h.document.body.textContent,/Confirmado pela equipe/);
});

test('a failed confirmation preserves fields and the same idempotency key for retry',async t=>{
 const h=await fixture(t,{failOnce:true});h.click('[data-os-receipt]');await flush();
 h.fill('[name=receipt_method]','pix');h.submit('[data-os-form=service-receipt]');await flush();
 assert.match(h.document.querySelector('.os-form-message').textContent,/Falha temporária/);
 assert.equal(h.document.querySelector('[name=receipt_method]').value,'pix');
 h.submit('[data-os-form=service-receipt]');await flush();
 const calls=h.calls.filter(c=>c.method==='POST');assert.equal(calls.length,2);
 assert.equal(calls[0].data.idempotency_key,calls[1].data.idempotency_key);
});

test('keeping pending requires an explicit second confirmation',async t=>{
 const h=await fixture(t);h.click('[data-os-receipt]');await flush();h.click('[data-os-action=receipt-hold]');await flush();
 assert.equal(h.calls.filter(c=>c.method==='POST').length,0);
 assert.match(h.document.getElementById('osDialog').textContent,/mesmo após 24 horas/);
 h.submit('[data-os-form=service-receipt]');await flush();
 assert.equal(h.row.keep_pending,true);assert.equal(h.row.status,'pending');
});

test('reopening an automatic receipt describes the cash correction and suspends automation',async t=>{
 const h=await fixture(t,{paid:true});h.click('[data-os-receipt]');await flush();
 assert.match(h.document.getElementById('osDialog').textContent,/Baixado automaticamente/);
 h.click('[data-os-action=receipt-reopen]');await flush();
 assert.match(h.document.getElementById('osDialog').textContent,/sairá dos recebimentos pagos/);
 h.submit('[data-os-form=service-receipt]');await flush();
 assert.equal(h.row.status,'pending');assert.equal(h.row.keep_pending,true);
});

test('receivables view requests every month without changing the month summary',async t=>{
 const h=await fixture(t);h.click('[data-os-action=finance-receivables]');await flush();
 assert.ok(h.calls.some(c=>c.path.includes('/finance?')&&c.path.includes('receivables=true')));
 assert.match(h.document.body.textContent,/todos os meses/);
 assert.match(h.document.body.textContent,/resumo acima continua mostrando o mês selecionado/);
});

test('viewer sees a receipt without mutation controls',async t=>{
 const h=await fixture(t,{role:'viewer'});h.click('[data-os-receipt]');await flush();
 assert.equal(h.document.querySelector('[data-os-form=service-receipt]'),null);
 assert.equal(h.document.querySelector('[data-os-action=receipt-hold]'),null);
 assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
});
