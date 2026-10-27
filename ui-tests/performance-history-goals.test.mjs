import test from 'node:test';
import assert from 'node:assert/strict';
import {makeSuite,flush,deferred,defaultResponse,tenantA,tenantB} from './harness.mjs';

const professional='51000000-0000-4000-8000-000000000001';
const other='51000000-0000-4000-8000-000000000002';
const goalId='52000000-0000-4000-8000-000000000001';
const today=()=>new Intl.DateTimeFormat('en-CA',{timeZone:'America/Sao_Paulo',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
const month=()=>({start_date:today().slice(0,8)+'01',end_date:new Date(Date.UTC(Number(today().slice(0,4)),Number(today().slice(5,7)),0,12)).toISOString().slice(0,10)});
const professionals=[{id:professional,name:'Peixoto',active:true},{id:other,name:'Guilherme',active:false}];
const performance={items:professionals.map(p=>({...p,completed:1,cancelled:1,revenue_cents:7500}))};
const goal=(extra={})=>({id:goalId,version:2,template_version:3,cycle_id:goalId+':'+today().slice(0,7),scope:'professional',professional_id:professional,professional_name:'Peixoto',recurrence:'monthly',metric:'revenue_cents',target_value:100000,actual_value:7500,progress_percent:7.5,active:true,can_edit:true,is_current_cycle:true,...month(),...extra});
const appointment=(extra={})=>({id:'53000000-0000-4000-8000-000000000001',professional_id:professional,professional_name:'Peixoto',customer_name:'Kayky Sousa',customer_phone:'+5511999990000',customer_display_name:'Kayky',customer_deleted:false,protocol:'AG-HISTORY',start_at:'2026-10-27T12:00:00Z',end_at:'2026-10-27T13:00:00Z',status:'completed',history_status:'completed',total_cents:7500,discount_cents:500,price_mode:'fixed',items:[{name_snapshot:'Corte',price_snapshot_cents:5000,duration_snapshot_min:40,price_mode_snapshot:'fixed'},{name_snapshot:'Barba',price_snapshot_cents:3000,duration_snapshot_min:20,price_mode_snapshot:'fixed'}],...extra});
const history=(extra={})=>({professional:professionals[0],period:{...month(),timezone:'America/Sao_Paulo'},items:[appointment()],total:1,limit:25,offset:0,has_more:false,counts:{all:1,completed:1,confirmed:0,cancelled:0,no_show:0,hold:0,expired_hold:0},...extra});
const historyCalls=h=>h.calls.filter(c=>c.path.includes('/performance/professionals/'));
const writes=h=>h.calls.filter(c=>c.method!=='GET');
const change=(h,selector,value)=>{const el=h.document.querySelector(selector);assert.ok(el,selector);el.value=value;el.dispatchEvent(new h.window.Event('change',{bubbles:true}));};
async function suite(t,{goals={items:[goal()],templates:[]},role='owner',respond=()=>undefined}={}){
  const h=await makeSuite({initialRoute:'desempenho',role,respond:(path,method,data)=>{
    const value=respond(path,method,data);if(value!==undefined)return value;
    if(method!=='GET')return {};
    if(path.includes('/performance/professionals/'))return history();
    if(path.includes('/performance/daily?'))return {items:[]};
    if(path.includes('/performance?'))return performance;
    if(path.includes('/goals?'))return goals;
    if(path.endsWith('/professionals'))return professionals;
    return defaultResponse(path);
  }});t.after(()=>h.close());await flush();return h;
}

test('each professional, including inactive, has an accessible history button for a viewer',async t=>{
  const h=await suite(t,{role:'viewer'});
  const buttons=[...h.document.querySelectorAll('[data-os-performance-history]')];
  assert.deepEqual(buttons.map(b=>b.getAttribute('aria-label')),['Ver histórico de Peixoto','Ver histórico de Guilherme']);
  assert.equal(buttons.every(b=>!b.disabled),true);
  assert.ok(h.document.querySelector('[data-os-action=new-goal]').disabled);
  assert.equal(h.document.querySelector('[data-os-goal]'),null);
  h.click('[data-os-performance-history]');await flush();
  assert.match(h.document.querySelector('#osDialog').textContent,/Kayky Sousa/);
  assert.equal(h.document.querySelector('#osDialog [type=submit]'),null);
  assert.deepEqual(writes(h),[]);
});

test('history inherits every report preset and the custom period exactly',async t=>{
  const h=await suite(t);
  for(const value of ['today','week','month','30','90']){
    h.click('[data-os-performance-period="'+value+'"]');await flush();
    const report=new URL(h.calls.filter(c=>c.path.includes('/performance?')).at(-1).path,'https://orion.test');
    h.click('[data-os-performance-history]');await flush();
    const read=new URL(historyCalls(h).at(-1).path,'https://orion.test');
    for(const key of ['start_date','end_date'])assert.equal(read.searchParams.get(key),report.searchParams.get(key),value+' '+key);
    assert.equal(read.searchParams.get('limit'),'25');
    h.click('#osDialog [data-os-action=close-dialog]');
  }
  h.click('[data-os-action=custom-period]');await flush();
  h.fill('#osPeriodForm [name=start_date]','2026-08-05');h.fill('#osPeriodForm [name=end_date]','2026-09-23');h.submit('#osPeriodForm');await flush();
  h.click('[data-os-performance-history]');await flush();
  const read=new URL(historyCalls(h).at(-1).path,'https://orion.test');
  assert.equal(read.searchParams.get('start_date'),'2026-08-05');assert.equal(read.searchParams.get('end_date'),'2026-09-23');
  assert.deepEqual(writes(h),[]);
});

test('history shows service snapshots, actual value, local time and cancellation details safely',async t=>{
  const h=await suite(t,{respond:path=>path.includes('/performance/professionals/')?history({items:[appointment({customer_name:'<img src=x onerror=alert(1)>',items:[{name_snapshot:'Corte <script>x</script>',price_snapshot_cents:5000,duration_snapshot_min:40}]}),appointment({id:'cancelled',status:'cancelled',history_status:'cancelled',cancellation:{event:'cancelled',requested_by:'customer',reason:'Viagem <b>amanhã</b>',occurred_at:'2026-10-26T14:00:00Z'}})],total:2,counts:{all:2,completed:1,cancelled:1}}):undefined});
  h.click('[data-os-performance-history]');await flush();
  const dialog=h.document.querySelector('#osDialog');
  for(const value of ['27/10/2026','09:00 – 10:00','R$\u00a075,00','Corte <script>x</script>','Cancelado pelo cliente','Viagem <b>amanhã</b>','AG-HISTORY','Desconto','Valor previsto'])assert.ok(dialog.textContent.includes(value),value);
  assert.equal(dialog.querySelector('img,script'),null);
  assert.equal(dialog.querySelectorAll('.os-history-item').length,2);
  assert.equal(dialog.querySelectorAll('details summary').length,2);
  assert.deepEqual(h.errors,[]);
});

test('deleted identities stay hidden even when a stale response includes a name and phone',async t=>{
  const h=await suite(t,{respond:path=>path.includes('/performance/professionals/')?history({items:[appointment({customer_deleted:true})]}):undefined});
  h.click('[data-os-performance-history]');await flush();
  const text=h.document.querySelector('#osDialog').textContent;
  assert.match(text,/Cliente excluído/);assert.doesNotMatch(text,/Kayky|5511999990000/);
});

test('history identifies both local dates for an appointment crossing midnight',async t=>{
  const h=await suite(t,{respond:path=>path.includes('/performance/professionals/')?history({items:[appointment({start_at:'2026-10-28T02:30:00Z',end_at:'2026-10-28T04:30:00Z'})]}):undefined});
  h.click('[data-os-performance-history]');await flush();
  const text=h.document.querySelector('.os-history-item-meta').textContent;
  assert.match(text,/27\/10\/2026 a 28\/10\/2026/);
  assert.match(text,/23:30 – 01:30/);
  assert.equal(h.document.activeElement.id,'osDialogTitle');
  h.click('#osDialog [data-os-action=close-dialog]');
  assert.equal(h.document.activeElement.getAttribute('data-os-performance-history'),professional);
});

test('status and paging preserve the report dates and a new status returns to the first page',async t=>{
  const h=await suite(t,{respond:path=>path.includes('/performance/professionals/')?history({has_more:true,total:51,counts:{all:51,completed:30,cancelled:21}}):undefined});
  h.click('[data-os-performance-period="90"]');await flush();h.click('[data-os-performance-history]');await flush();
  const first=new URL(historyCalls(h).at(-1).path,'https://orion.test');
  h.click('[data-os-action=professional-history-next]');await flush();
  let read=new URL(historyCalls(h).at(-1).path,'https://orion.test');assert.equal(read.searchParams.get('offset'),'25');
  change(h,'#osProfessionalHistoryStatus','cancelled');await flush();
  read=new URL(historyCalls(h).at(-1).path,'https://orion.test');
  assert.equal(read.searchParams.get('offset'),'0');assert.equal(read.searchParams.get('status'),'cancelled');
  assert.equal(read.searchParams.get('start_date'),first.searchParams.get('start_date'));assert.equal(read.searchParams.get('end_date'),first.searchParams.get('end_date'));
  assert.match(h.document.querySelector('#osProfessionalHistoryStatus').textContent,/Reservas expiradas/);
  assert.deepEqual(writes(h),[]);
});

test('late history results cannot overwrite another professional or a newer status',async t=>{
  const slow=deferred(),oldStatus=deferred();let count=0;
  const h=await suite(t,{respond:path=>{
    if(!path.includes('/performance/professionals/'))return;
    count++;if(count===1)return slow.promise;if(path.includes('status=cancelled'))return oldStatus.promise;
    return history({items:[appointment({customer_name:'Resposta atual'})]});
  }});
  h.click('[data-os-performance-history]');await flush();h.click('#osDialog [data-os-action=close-dialog]');
  h.click('[data-os-performance-history="'+other+'"]');await flush();
  slow.resolve(history({items:[appointment({customer_name:'Resposta antiga'})]}));await flush();
  assert.match(h.document.querySelector('#osDialogTitle').textContent,/Guilherme/);
  assert.doesNotMatch(h.document.querySelector('#osDialog').textContent,/Resposta antiga/);
  change(h,'#osProfessionalHistoryStatus','cancelled');await flush();change(h,'#osProfessionalHistoryStatus','completed');await flush();
  oldStatus.resolve(history({items:[appointment({customer_name:'Filtro antigo'})]}));await flush();
  assert.doesNotMatch(h.document.querySelector('#osDialog').textContent,/Filtro antigo/);
  assert.equal(h.document.querySelector('#osProfessionalHistoryStatus').value,'completed');
  assert.deepEqual(h.errors,[]);
});

test('closing history restores focus after an in-flight live refresh replaces its trigger',async t=>{
  const pendingRefresh=deferred(),refreshStarted=deferred(),originalTimeout=globalThis.setTimeout;
  let reads=0,accelerated=false;
  // Exercise the real refresh callback without waiting thirty seconds. Only
  // its first scheduling is accelerated; dialog focus and other timers retain
  // their normal ordering, and the global hook is restored before assertions.
  globalThis.setTimeout=(callback,delay,...args)=>originalTimeout(callback,
    delay===30000&&!accelerated?(accelerated=true,20):delay,...args);
  t.after(()=>{globalThis.setTimeout=originalTimeout;});
  const h=await suite(t,{respond:path=>{
    if(path.includes('/performance?')&&++reads===2){refreshStarted.resolve();return pendingRefresh.promise;}
  }});
  let deadline;
  try{
    await Promise.race([refreshStarted.promise,new Promise((_,reject)=>{
      deadline=originalTimeout(()=>reject(Error('Live refresh did not start')),2000);
    })]);
  }finally{clearTimeout(deadline);globalThis.setTimeout=originalTimeout;}
  const trigger=h.document.querySelector('[data-os-performance-history]');
  trigger.focus();trigger.click();await flush();
  assert.equal(h.document.querySelector('#osDialog').open,true);
  pendingRefresh.resolve(performance);await flush();
  assert.equal(trigger.isConnected,false);
  const replacement=h.document.querySelector('[data-os-performance-history="'+professional+'"]');
  assert.ok(replacement);
  assert.equal(h.document.querySelector('#osDialog').open,true);
  h.click('#osDialog [data-os-action=close-dialog]');await flush();
  assert.equal(h.document.activeElement,replacement);
  assert.deepEqual(writes(h),[]);
  assert.deepEqual(h.errors,[]);
});

test('a shrinking status result recovers the last valid page without an impossible range',async t=>{
  let reads=0;
  const h=await suite(t,{respond:path=>{
    if(!path.includes('/performance/professionals/'))return;
    reads++;
    if(reads===1)return history({total:26,has_more:true});
    if(reads===2)return history({items:[],total:1,offset:25,has_more:false});
    return history({items:[appointment({customer_name:'Cliente restante'})],total:1});
  }});
  h.click('[data-os-performance-history]');await flush();h.click('[data-os-action=professional-history-next]');await flush();
  assert.deepEqual(historyCalls(h).map(c=>new URL(c.path,'https://orion.test').searchParams.get('offset')),['0','25','0']);
  assert.match(h.document.querySelector('#osDialog').textContent,/Cliente restante/);
  assert.doesNotMatch(h.document.querySelector('#osDialog').textContent,/0–25|Nenhum atendimento/);
  assert.equal(h.document.querySelector('#osProfessionalHistoryResults').hasAttribute('aria-busy'),false);
});

test('history error can be retried, and an empty filtered page explains the result',async t=>{
  let reads=0;const h=await suite(t,{respond:path=>{if(path.includes('/performance/professionals/')){if(++reads===1)throw Error('Falha de conexão');return history({items:[],total:0,counts:{all:0}});}}});
  h.click('[data-os-performance-history]');await flush();
  assert.match(h.document.querySelector('#osDialog').textContent,/Falha de conexão/);
  h.click('[data-os-action=professional-history-retry]');await flush();
  assert.match(h.document.querySelector('#osDialog').textContent,/Nenhum atendimento no período selecionado/);
  assert.equal(h.document.querySelector('#osProfessionalHistoryResults').hasAttribute('aria-busy'),false);
  assert.equal(h.document.activeElement.id,'osProfessionalHistoryResults');
});

test('company monthly goal creation needs no professional and never sends filter dates',async t=>{
  const h=await suite(t);
  h.click('[data-os-performance-period="90"]');await flush();h.click('[data-os-action=new-goal]');await flush();
  change(h,'#osField-scope','company');change(h,'#osField-metric','revenue_cents');
  assert.equal(h.document.querySelector('[data-os-goal-professional]').hidden,true);
  assert.equal(h.document.querySelector('#osField-professional_id').disabled,true);
  assert.equal(h.document.querySelector('#osField-start_date'),null);
  assert.match(h.document.querySelector('#osDialog').textContent,/No dia 1º, o progresso volta a zero/);
  h.fill('#osField-target','1.000,00');h.submit('[data-os-form=goal]');await flush();
  const write=writes(h)[0];assert.ok(write.path.endsWith('/goals'));assert.equal(write.method,'POST');
  assert.equal(write.data.scope,'company');assert.equal(write.data.professional_id,null);assert.equal(write.data.recurrence,'monthly');assert.equal(write.data.target_value,100000);assert.equal(write.data.active,true);
  assert.equal('start_date' in write.data,false);assert.equal('end_date' in write.data,false);assert.ok(write.data.idempotency_key);
});

test('the month preset advances when the panel remains open across day 01, while custom dates stay fixed',async t=>{
  const h=await suite(t),RealDate=h.window.Date;
  const nextMonth=new RealDate(Date.UTC(Number(today().slice(0,4)),Number(today().slice(5,7)),1,15));
  h.window.Date=class extends RealDate {constructor(...args){super(...(args.length?args:[nextMonth.getTime()]));}static now(){return nextMonth.getTime();}};
  await h.window.OrionSuite.open('desempenho',{keepScroll:true});await flush();
  let read=new URL(h.calls.filter(c=>c.path.includes('/performance?')).at(-1).path,'https://orion.test');
  assert.equal(read.searchParams.get('start_date'),nextMonth.toISOString().slice(0,8)+'01');
  assert.ok(h.document.querySelector('.os-performance-periods').textContent.includes(nextMonth.toLocaleDateString('pt-BR',{timeZone:'America/Sao_Paulo'})));
  h.click('[data-os-action=custom-period]');await flush();h.fill('#osPeriodForm [name=start_date]','2026-01-02');h.fill('#osPeriodForm [name=end_date]','2026-01-25');h.submit('#osPeriodForm');await flush();
  const later=new RealDate(nextMonth);later.setUTCMonth(later.getUTCMonth()+1);
  h.window.Date=class extends RealDate {constructor(...args){super(...(args.length?args:[later.getTime()]));}static now(){return later.getTime();}};
  await h.window.OrionSuite.open('desempenho',{keepScroll:true});await flush();
  read=new URL(h.calls.filter(c=>c.path.includes('/performance?')).at(-1).path,'https://orion.test');
  assert.equal(read.searchParams.get('start_date'),'2026-01-02');assert.equal(read.searchParams.get('end_date'),'2026-01-25');
});

test('individual monthly goals require a professional and a positive integer appointment count',async t=>{
  const h=await suite(t);h.click('[data-os-action=new-goal]');await flush();
  h.fill('#osField-target','10');h.submit('[data-os-form=goal]');await flush();assert.equal(writes(h).length,0);
  change(h,'#osField-professional_id',professional);h.fill('#osField-target','10.5');h.submit('[data-os-form=goal]');await flush();assert.equal(writes(h).length,0);
  h.fill('#osField-target','20');h.submit('[data-os-form=goal]');await flush();
  const write=writes(h)[0];assert.equal(write.data.professional_id,professional);assert.equal(write.data.scope,'professional');assert.equal(write.data.target_value,20);assert.equal(write.data.recurrence,'monthly');
});

test('a current cycle edits the current template version and can pause renewal',async t=>{
  const h=await suite(t);h.click('[data-os-goal]');await flush();
  h.fill('#osField-target','2.000,00');h.document.querySelector('[name=active]').checked=false;h.submit('[data-os-form=goal]');await flush();
  const write=writes(h)[0];assert.equal(write.method,'PATCH');assert.equal(write.data.expected_version,3);assert.equal(write.data.target_value,200000);assert.equal(write.data.active,false);assert.equal('start_date' in write.data,false);
});

test('closed months stay visible and cannot be edited; paused templates remain manageable',async t=>{
  const past=goal({cycle_id:goalId+':2020-02',start_date:'2020-02-01',end_date:'2020-02-29',is_current_cycle:false,can_edit:false});
  const paused=goal({active:false,cycle_id:undefined,actual_value:undefined,can_edit:true});
  const h=await suite(t,{goals:{items:[past],templates:[paused]}});
  const cards=h.document.querySelectorAll('.os-goal-card');assert.equal(cards.length,2);
  assert.equal(cards[0].querySelector('[data-os-goal]'),null);assert.match(cards[0].textContent,/Mês encerrado/);
  h.click('[data-os-goal="template:'+goalId+'"]');await flush();
  assert.equal(h.document.querySelector('[name=active]').checked,false);
  h.document.querySelector('[name=active]').checked=true;h.submit('[data-os-form=goal]');await flush();
  assert.equal(writes(h)[0].data.active,true);
});

test('goal deletion confirms the concrete scope and uses optimistic concurrency',async t=>{
  const h=await suite(t,{goals:{items:[goal({scope:'company',professional_id:null})],templates:[]}});
  h.click('[data-os-goal]');await flush();h.click('[data-os-action=delete-goal]');await flush();
  assert.match(h.document.querySelector('#osDialog').textContent,/Empresa inteira/);assert.match(h.document.querySelector('#osDialog').textContent,/meses anteriores serão preservados/);
  assert.equal(writes(h).length,0);h.submit('[data-os-form=confirm]');await flush();
  assert.equal(writes(h)[0].method,'DELETE');assert.equal(writes(h)[0].data.expected_version,3);
});

test('legacy fixed-period goals retain their date fields and recurrence',async t=>{
  const h=await suite(t,{goals:{items:[goal({recurrence:'none',start_date:'2026-10-07',end_date:'2026-10-23'})]}});
  h.click('[data-os-goal]');await flush();h.submit('[data-os-form=goal]');await flush();
  const data=writes(h)[0].data;assert.equal(data.recurrence,'none');assert.equal(data.start_date,'2026-10-07');assert.equal(data.end_date,'2026-10-23');
});

test('duplicate goal errors preserve the form, and corrected submissions use a new idempotency key',async t=>{
  let attempt=0;const h=await suite(t,{respond:(_path,method)=>{if(method==='POST'&&++attempt===1)throw Error('Já existe uma meta mensal ativa para este objetivo');}});
  h.click('[data-os-action=new-goal]');await flush();change(h,'#osField-scope','company');h.fill('#osField-target','25');h.submit('[data-os-form=goal]');await flush();
  assert.match(h.document.querySelector('.os-form-message').textContent,/Já existe/);assert.equal(h.document.querySelector('#osField-target').value,'25');
  h.fill('#osField-target','30');h.submit('[data-os-form=goal]');await flush();
  assert.notEqual(writes(h)[0].data.idempotency_key,writes(h)[1].data.idempotency_key);
});

test('an in-flight history response cannot reappear after a verified business change',async t=>{
  const pending=deferred();const h=await suite(t,{respond:path=>path.includes('/performance/professionals/')?pending.promise:undefined});
  h.click('[data-os-performance-history]');await flush();
  h.window.OrionSuite.sync({...h.bridge,tenantId:tenantB,tenants:[{id:tenantB,name:'Empresa B',timezone:'America/Sao_Paulo',role:'owner'}]});await flush();
  pending.resolve(history());await flush();
  assert.equal(h.document.querySelector('.os-history-item'),null);assert.equal(h.document.querySelector('#osDialog').open,false);assert.deepEqual(h.errors,[]);
});
