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
async function financeSuite(role='owner',{entries=ledger,timezone='America/Sao_Paulo'}={}){
 const respond=(path,method,data)=>{
   if(method==='PATCH'&&path.includes('/management/finance/')){
     const record=entries.find(entry=>path.endsWith('/'+entry.id));
     return {...record,...data,version:(record?.version||0)+1};
   }
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
      const selected=entries.filter(row=>!params.get('kind')||params.get('kind')===row.kind);
      return {items:selected,total:selected.length};
   }
   if(path.endsWith('/professionals'))return [{id:professional,name:'Profissional sintético',active:true}];
   return defaultResponse(path);
 };
 const h=await makeSuite({initialRoute:'financeiro',role,respond,bridge:{
   tenants:[{id:tenantA,name:'Empresa A',slug:'empresa-a',timezone,role}]
 }});await flush();return h;
}
function appointmentEntry(overrides={}){
 const id='57000000-0000-4000-8000-000000000010';
 return {...ledger[0],description:'Atendimento AG-QSQ5XW',amount_cents:5000,status:'pending',version:7,
   customer_name:'Kayky',customer_display_name:'Kayky',customer_deleted:false,
   professional_id:professional,professional_name:'Mariana Oliveira dos Santos',appointment_id:id,
   appointment:{id,protocol:'AG-QSQ5XW',start_at:'2026-10-26T14:00:00-04:00',
     end_at:'2026-10-26T15:00:00-04:00',timezone:'America/Campo_Grande',services:[
       {service_id:'service-1',name:'Corte masculino',duration_min:30,price_cents:4000,price_mode_snapshot:'fixed'},
       {service_id:'service-2',name:'Barba e acabamento',duration_min:20,price_cents:3000,price_mode_snapshot:'fixed'},
       {service_id:'service-3',name:'Hidratação e tratamento profundo',duration_min:10,price_cents:2000,price_mode_snapshot:'from'}
     ]},...overrides};
}
async function openFinanceEditor(h,entry){
 h.click('.os-finance-ledger-table [data-os-finance="'+entry.id+'"]');await flush();
 return h.document.querySelector('[data-os-form=finance]');
}
function appointmentFacts(form){
 const summary=form.querySelector('section[aria-label="Informações do atendimento"]');
 assert.ok(summary,'The existing editor must expose the linked appointment context');
 return Object.fromEntries([...summary.querySelectorAll('.os-summary-row')]
   .map(row=>[row.querySelector('span').textContent,row.querySelector('strong').textContent]));
}
test('every financial metric and all five report tabs render without losing financial facts',async t=>{
 const h=await financeSuite();t.after(()=>h.close());
 assert.equal(h.document.querySelectorAll('.os-finance-metrics>.os-metric').length,6);
 const tabs=h.document.querySelectorAll('[data-os-finance-tab]');assert.equal(tabs.length,5);
 assert.deepEqual([...tabs].map(el=>el.textContent),['Lançamentos','DRE','Comissões','Vales & Pagamentos','Histórico']);
 const table=h.document.querySelector('.os-finance-ledger-table');
 assert.ok(table);
 assert.equal(table.querySelector('thead th:last-child').getAttribute('aria-label'),'Ações');
 assert.equal(table.querySelector('thead .os-sr-only'),null,'Absolute-positioned header hint must not expand tablet document width');
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

test('automatic receipts show the client name while preserving the original compact card and pencil action',async t=>{
 const entry=appointmentEntry();
 const h=await financeSuite('owner',{entries:[entry]});t.after(()=>h.close());
 const row=h.document.querySelector('.os-finance-ledger-table tbody tr');
 assert.equal(row.querySelector('.os-finance-primary>strong').textContent,'Atendimento Kayky');
 assert.equal(row.querySelectorAll('button').length,1);
 assert.ok(row.querySelector('.os-icon-button[data-os-finance]'));
 assert.equal(row.querySelector('[data-os-finance-detail]'),null);
 assert.doesNotMatch(row.textContent,/Ver detalhes|AG-QSQ5XW|Mariana|Corte masculino|14:00|15:00/);
 assert.match(row.querySelector('.os-finance-value').textContent,/50,00/);
 assert.equal(row.querySelector('[data-label="Categoria"]').textContent,'Serviços');
 assert.match(row.querySelector('.os-finance-primary small').textContent,/Pix/);
 const callsBefore=h.calls.length;
 const form=await openFinanceEditor(h,entry),facts=appointmentFacts(form);
 assert.equal(h.calls.length,callsBefore,'Opening the pencil must not fan out into appointment requests');
 assert.equal(facts.Cliente,'Kayky');
 assert.equal(facts.Profissional,entry.professional_name);
 assert.equal(facts.Serviços,entry.appointment.services.map(service=>service.name).join(' + '));
 assert.equal(facts['Data do atendimento'],'26/10/2026');
 assert.equal(facts.Horário,'14:00 – 15:00');
 assert.equal(facts.Referência,'AG-QSQ5XW');
 assert.equal(form.elements.description.value,'Atendimento Kayky');
 assert.equal(form.elements.amount.value,'50,00','Snapshots must not replace the partial financial amount');
 assert.equal(form.querySelector('fieldset').disabled,false);
 assert.equal(h.document.getElementById('osDialogTitle').textContent,'Editar lançamento');
 assert.equal(h.calls.filter(call=>call.method!=='GET').length,0);
 h.click('#osDialog [data-os-action=close-dialog]');await flush();
 h.click('[data-os-finance-tab=history]');await flush();
 assert.match(h.document.querySelector('.os-finance-ledger-table').textContent,/Atendimento Kayky/);
 assert.doesNotMatch(h.document.querySelector('.os-finance-ledger-table').textContent,/AG-QSQ5XW/);
 assert.equal(h.errors.length,0);
});

test('a viewer sees appointment facts inside the existing disabled finance form and cannot save',async t=>{
 const entry=appointmentEntry();
 const h=await financeSuite('viewer',{entries:[entry]});t.after(()=>h.close());
 const form=await openFinanceEditor(h,entry),facts=appointmentFacts(form);
 assert.equal(facts.Cliente,'Kayky');
 assert.equal(facts.Profissional,entry.professional_name);
 assert.equal(form.querySelector('fieldset').disabled,true);
 assert.equal(form.querySelector('[type=submit]').disabled,true);
 assert.equal(form.querySelector('[data-os-action=delete-finance]'),null);
 h.submit('[data-os-form=finance]');await flush();
 assert.equal(h.calls.filter(call=>call.method!=='GET').length,0);
 assert.equal(h.errors.length,0);
});

test('manual entries retain their chosen title and do not receive invented appointment information',async t=>{
 const entry={...ledger[1],appointment:null,customer_name:null,customer_display_name:null,
   professional_name:null,notes:'Compra de materiais para reposição.'};
 const h=await financeSuite('owner',{entries:[entry]});t.after(()=>h.close());
 assert.equal(h.document.querySelector('.os-finance-primary>strong').textContent,'Produtos e materiais');
 const form=await openFinanceEditor(h,entry);
 assert.equal(form.querySelector('[aria-label="Informações do atendimento"]'),null);
 assert.equal(form.elements.description.value,entry.description);
 assert.equal(form.elements.notes.value,entry.notes);
 assert.equal(form.elements.day.value,'2026-10-25');
 assert.equal(h.calls.filter(call=>call.method!=='GET').length,0);
});

test('a custom description remains intact even when the entry is linked to multiple services',async t=>{
 const entry=appointmentEntry({kind:'expense',description:'Reembolso de sinal ao cliente',status:'paid'});
 const h=await financeSuite('owner',{entries:[entry]});t.after(()=>h.close());
 assert.equal(h.document.querySelector('.os-finance-primary>strong').textContent,entry.description);
 const form=await openFinanceEditor(h,entry);
 assert.equal(form.elements.description.value,entry.description);
 assert.equal(form.elements.kind.value,'expense');
 assert.equal(appointmentFacts(form).Serviços,entry.appointment.services.map(service=>service.name).join(' + '));
 assert.equal(h.calls.filter(call=>call.method!=='GET').length,0);
 assert.equal(h.errors.length,0);
});

test('appointment time uses its own timezone while the existing card keeps the posting date',async t=>{
 const entry=appointmentEntry({occurred_at:'2026-10-27T04:20:00Z'});
 entry.appointment={...entry.appointment,start_at:'2026-10-27T03:40:00Z',
   end_at:'2026-10-27T04:20:00Z',timezone:'America/Campo_Grande'};
 const h=await financeSuite('owner',{entries:[entry],timezone:'America/Sao_Paulo'});t.after(()=>h.close());
 assert.equal(h.document.querySelector('.os-finance-date').textContent,'27/10/2026');
 const form=await openFinanceEditor(h,entry),facts=appointmentFacts(form);
 assert.equal(facts['Data do atendimento'],'26/10/2026');
 assert.equal(facts.Horário,'23:40 – 27/10/2026 às 00:20');
 assert.equal(form.elements.day.value,'2026-10-27');
 assert.equal(h.errors.length,0);
});

test('deleted customers are not exposed and appointment facts are rendered as text rather than markup',async t=>{
 const unsafe='<img src=x onerror="alert(1)">',secret='Nome residual excluído';
 const entry=appointmentEntry({customer_deleted:true,customer_name:secret,customer_display_name:secret,
   professional_name:'Profissional '+unsafe,notes:'Observação '+unsafe});
 entry.appointment={...entry.appointment,protocol:'AG-X"><script>alert(1)</script>',
   services:[{name:'Corte '+unsafe,duration_min:30},{name:'Barba " especial',duration_min:20}]};
 const h=await financeSuite('owner',{entries:[entry]});t.after(()=>h.close());
 const row=h.document.querySelector('.os-finance-ledger-table tbody tr');
 assert.equal(row.querySelector('.os-finance-primary>strong').textContent,'Atendimento · Cliente excluído');
 assert.doesNotMatch(row.textContent,/Nome residual excluído/);
 const form=await openFinanceEditor(h,entry),facts=appointmentFacts(form);
 assert.equal(facts.Cliente,'Cliente excluído');
 assert.doesNotMatch(form.outerHTML,/Nome residual excluído/);
 assert.ok(facts.Serviços.includes(unsafe));
 assert.ok(facts.Profissional.includes(unsafe));
 assert.equal(facts.Referência,entry.appointment.protocol);
 assert.equal(form.elements.notes.value,entry.notes);
 assert.equal(form.querySelector('img,script,[onerror]'),null);
 assert.equal(h.errors.length,0);
});

test('a phone-only customer gets a neutral title and a formatted phone only inside the editor',async t=>{
 const entry=appointmentEntry({customer_name:'Cliente WhatsApp',customer_display_name:'+5586972676282'});
 const h=await financeSuite('owner',{entries:[entry]});t.after(()=>h.close());
 const row=h.document.querySelector('.os-finance-ledger-table tbody tr');
 assert.equal(row.querySelector('.os-finance-primary>strong').textContent,'Atendimento');
 assert.doesNotMatch(row.textContent,/AG-QSQ5XW|86972676282|Cliente WhatsApp/);
 assert.equal(appointmentFacts(await openFinanceEditor(h,entry)).Cliente,'(86) 97267-6282');
 assert.equal(h.errors.length,0);
});

test('historical entries without appointment details stay editable with explicit missing facts',async t=>{
 const entry=appointmentEntry({appointment:null,customer_name:null,customer_display_name:null,
   professional_name:null,professional_id:null});
 const h=await financeSuite('owner',{entries:[entry]});t.after(()=>h.close());
 assert.equal(h.document.querySelector('.os-finance-primary>strong').textContent,'Atendimento');
 const form=await openFinanceEditor(h,entry),facts=appointmentFacts(form);
 for(const label of ['Cliente','Serviços','Profissional','Data do atendimento','Horário']){
   assert.equal(facts[label],'Não informado');
 }
 assert.equal(form.elements.description.value,'Atendimento');
 assert.equal(form.elements.amount.value,'50,00');
 assert.equal(h.calls.filter(call=>call.method!=='GET').length,0);
 assert.equal(h.errors.length,0);
});

test('marking paid preserves the exact posting timestamp, original stored description and optimistic version',async t=>{
 const entry=appointmentEntry({occurred_at:'2026-10-26T11:48:33.123-04:00',payment_method:'other'});
 const h=await financeSuite('owner',{entries:[entry]});t.after(()=>h.close());
 const form=await openFinanceEditor(h,entry);
 assert.equal(form.elements.description.value,'Atendimento Kayky');
 h.fill('[data-os-form=finance] [name=status]','paid');
 h.fill('[data-os-form=finance] [name=payment_method]','pix');
 h.submit('[data-os-form=finance]');await flush();
 const mutations=h.calls.filter(call=>call.method!=='GET');
 assert.equal(mutations.length,1);
 const update=mutations[0];
 assert.equal(update.method,'PATCH');
 assert.equal(update.path,'/api/v1/tenants/'+tenantA+'/management/finance/'+entry.id);
 assert.equal(update.data.status,'paid');
 assert.equal(update.data.payment_method,'pix');
 assert.equal(update.data.occurred_at,entry.occurred_at);
 assert.equal(update.data.expected_version,7);
 assert.equal(update.data.description,entry.description,'A presentation-only title must not rewrite the stored description');
 assert.equal(update.data.amount_cents,5000);
 assert.equal(Object.hasOwn(update.data,'appointment'),false);
 assert.equal(h.errors.length,0);
});

test('an intentional description change is still saved instead of being replaced by the original code',async t=>{
 const entry=appointmentEntry();
 const h=await financeSuite('owner',{entries:[entry]});t.after(()=>h.close());
 await openFinanceEditor(h,entry);
 h.fill('[data-os-form=finance] [name=description]','Sinal do atendimento de Kayky');
 h.submit('[data-os-form=finance]');await flush();
 const mutations=h.calls.filter(call=>call.method!=='GET');
 assert.equal(mutations.length,1);
 assert.equal(mutations[0].data.description,'Sinal do atendimento de Kayky');
 assert.equal(mutations[0].data.expected_version,7);
 assert.equal(h.errors.length,0);
});
