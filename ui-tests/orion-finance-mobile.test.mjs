import test from 'node:test';
import assert from 'node:assert/strict';
import { makeSuite, flush, tenantA, defaultResponse } from './harness.mjs';

const professional='57000000-0000-4000-8000-000000000001';
const ledger=[
 {id:'57000000-0000-4000-8000-000000000002',kind:'income',description:'Corte e hidratação',amount_cents:14000,
  category:'Serviços',payment_method:'pix',status:'paid',occurred_at:'2026-10-26T14:00:00-04:00',version:1},
 {id:'57000000-0000-4000-8000-000000000003',kind:'expense',description:'Produtos e materiais',
  amount_cents:2000,category:'Materiais',payment_method:'cash',status:'paid',
  occurred_at:'2026-10-25T14:00:00-04:00',version:2}
];
const commissions=[{id:professional,name:'Profissional sintético',commission_percent:10,commission_mode:'percent',
  completed:2,revenue_cents:14000,earned_cents:1400,advances_cents:500,payable_cents:900}];
const advances=[{id:'57000000-0000-4000-8000-000000000004',professional_id:professional,
  professional_name:'Profissional sintético',description:'Vale de teste',
  amount_cents:500,allocated_cents:200,pending_cents:300,settlement_status:'partial',
  occurred_at:'2026-10-25T10:00:00-04:00'}];
const settlements=[{id:'57000000-0000-4000-8000-000000000005',professional_name:'Profissional sintético',
  allocated_cents:200,payment_cents:400,settled_at:'2026-10-25T10:00:00-04:00'}];
async function financeSuite(role='owner'){
 const respond=(path,method,data)=>{
   if(path.includes('/management/finance/summary'))return {summary:{income_cents:14000,expense_cents:2000,
     balance_cents:12000,average_ticket_cents:7000,count:2,by_category:[
       {category:'Serviços',income_cents:14000,expense_cents:0},
       {category:'Materiais',income_cents:0,expense_cents:2000}]},
       daily:[{date:'2026-10-26',income_cents:14000,expense_cents:2000}]};
   if(path.includes('/management/finance/dre'))return {summary:{service_revenue_cents:14000,other_income_cents:0,
      gross_revenue_cents:14000,commissions_cents:1400,operating_expenses_cents:2000,net_result_cents:10600}};
   if(path.includes('/management/finance/commissions'))return {items:commissions};
   if(path.includes('/management/finance/advances'))return {items:advances,total:1};
   if(path.includes('/management/finance/settlements'))return {items:settlements,total:1};
   if(path.includes('/management/finance?')){
      const params=new URL(path,'https://orion.test').searchParams;
      const selected=ledger.filter(row=>!params.get('kind')||params.get('kind')===row.kind);
      return {items:selected,total:selected.length};
   }
   if(path.endsWith('/professionals'))return [{id:professional,name:'Profissional sintético',active:true}];
   return defaultResponse(path);
 };
 const h=await makeSuite({initialRoute:'financeiro',role,respond});await flush();return h;
}
test('every financial metric and all five report tabs render without losing financial facts',async t=>{
 const h=await financeSuite();t.after(()=>h.close());
 assert.equal(h.document.querySelectorAll('.os-finance-metrics>.os-metric').length,6);
 const tabs=h.document.querySelectorAll('[data-os-finance-tab]');assert.equal(tabs.length,5);
 assert.deepEqual([...tabs].map(el=>el.textContent),['Lançamentos','DRE','Comissões','Vales & Pagamentos','Histórico']);
 const table=h.document.querySelector('.os-finance-ledger-table');
 assert.ok(table);
 assert.equal(table.querySelectorAll('tbody>tr').length,2);
 assert.match(table.textContent,/Corte e hidratação/);
 assert.match(table.textContent,/140,00/);
 assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
 for(const value of ['dre','commissions','advances','history','entries']){
   h.click('[data-os-finance-tab="'+value+'"]');await flush();
   assert.equal(h.document.querySelector('[data-os-finance-tab="'+value+'"]').getAttribute('aria-selected'),'true');
   if(value==='dre')assert.match(h.document.querySelector('#osPageBody').textContent,/Demonstrativo de resultado/);
   if(value==='commissions')assert.equal(h.document.querySelectorAll('.os-finance-commissions-table tbody tr').length,1);
   if(value==='advances'){
     assert.equal(h.document.querySelectorAll('.os-finance-advances-table tbody tr').length,1);
     assert.equal(h.document.querySelectorAll('.os-finance-settlements-table tbody tr').length,1);
   }
   if(value==='history')assert.equal(h.document.querySelectorAll('.os-finance-ledger-table tbody tr').length,2);
 }
 assert.equal(h.errors.length,0);
});
test('finance tables carry semantic labels for responsive cards and retain real actions',async t=>{
 const h=await financeSuite();t.after(()=>h.close());
 const table=h.document.querySelector('.os-finance-ledger-table');
 assert.deepEqual([...table.querySelectorAll('tbody tr:first-child td')].map(cell=>cell.dataset.label),
   ['Descrição','Data','Categoria','Valor','Status','Ações']);
 const value=table.querySelector('.os-finance-value');
 assert.match(value.textContent,/140,00/);
 h.click('[data-os-finance-tab=commissions]');await flush();
 const commission=h.document.querySelector('.os-finance-commissions-table');
 assert.deepEqual([...commission.querySelectorAll('tbody tr:first-child td')].map(cell=>cell.dataset.label),
   ['Profissional','Atendimentos','Valor vendido','Comissão','Vales no período','Saldo a pagar','Ações']);
 assert.ok(commission.querySelector('[data-os-action=settlement-preview]'));
 h.click('[data-os-finance-tab=advances]');await flush();
 const advance=h.document.querySelector('.os-finance-advances-table');
 assert.match(advance.querySelector('[data-label="Em aberto"]').textContent,/3,00/);
 assert.ok(advance.querySelector('[data-os-finance]'));
 assert.ok(h.document.querySelector('[data-os-settlement]'));
 assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
});
test('finance filter state survives rerender and filtering requires no mutation',async t=>{
 const h=await financeSuite();t.after(()=>h.close());
 h.click('[data-os-action=finance-filters]');await flush();
 const filters=h.document.getElementById('osFinanceFilters');assert.equal(filters.classList.contains('hidden'),false);
 const type=h.document.getElementById('osFinanceKind');type.value='expense';
 type.dispatchEvent(new h.window.Event('change',{bubbles:true}));await flush();
 assert.equal(h.document.getElementById('osFinanceFilters').classList.contains('hidden'),false);
 assert.equal(h.document.getElementById('osFinanceKind').value,'expense');
 assert.equal(h.document.querySelectorAll('.os-finance-ledger-table tbody tr').length,1);
 h.click('[data-os-action=finance-clear-filters]');await flush();
 assert.equal(h.document.getElementById('osFinanceKind').value,'');
 assert.equal(h.document.querySelectorAll('.os-finance-ledger-table tbody tr').length,2);
 assert.equal(h.document.querySelector('[data-os-action=finance-filters]').getAttribute('aria-expanded'),'true');
 assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
});
test('read-only finance retains detail access but never offers editable controls',async t=>{
 const h=await financeSuite('viewer');t.after(()=>h.close());
 assert.equal(h.document.querySelector('[data-os-action=new-expense]').disabled,true);
 h.click('[data-os-finance-tab=commissions]');await flush();
 assert.ok(h.document.querySelector('[data-os-action=settlement-preview]').disabled);
 assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
});
