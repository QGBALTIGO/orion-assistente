import test from 'node:test';
import assert from 'node:assert/strict';
import {
  makeSchedules,syntheticServer,contract,copy,base,scheduleId,runId,accountScope,
  recovery,writes,fillSchedule,failure,flush,deferred,tenantB,
} from './subscription-scheduled-messages-harness.mjs';

const action=name=>'[data-sc-action="'+name+'"]';
const field=name=>'[data-sc-field="'+name+'"]';
const dialog=h=>h.element('[data-sc-dialog]');
async function review(h,options){await fillSchedule(h,options);h.submit();await flush();assert.equal(dialog(h).dataset.scView,'review');}
async function confirm(h){h.change('[data-sc-consent]',true);h.click(action('confirm'));await flush();}
const publicHistory=h=>copy(h.window.history.state.orionScheduleRecovery);

test('list, counters and tabs only read; search carries across tabs and Clear returns to Active',async t=>{
  const h=await makeSchedules();t.after(()=>h.close());
  assert.equal(h.document.querySelectorAll('[data-sc-metric]').length,4);
  assert.equal(h.element('[data-sc-metric="upcoming"] strong').textContent,'1');
  assert.equal(h.document.querySelectorAll('[data-sc-row]').length,1);
  h.fill(field('q'),'renovação');h.submit('filters');await flush();
  h.click('[data-sc-tab="history"]');await flush();
  assert.equal(h.element(field('q')).value,'renovação');
  assert.ok(h.calls.at(-1).path.includes('tab=history'));
  assert.ok(!h.document.querySelector('[data-sc-status]'));
  h.click('[data-sc-tab="active"]');await flush();
  h.click('[data-sc-status="attention"]');await flush();
  assert.ok(h.calls.at(-1).path.includes('status=attention'));
  h.click(action('reset'));await flush();
  assert.equal(h.element(field('q')).value,'');
  assert.equal(h.element('[data-sc-tab="active"]').getAttribute('aria-selected'),'true');
  assert.equal(writes(h).length,0);assert.equal(h.server.effects.length,0);assert.equal(h.external.length,0);assert.deepEqual(h.errors,[]);
  assert.ok(!h.window.location.href.includes('renova'));
});

test('editor defaults, one device, tenant date/timezone and exactly observed intervals',async t=>{
  const h=await makeSchedules();t.after(()=>h.close());h.click(action('new'));await flush();
  assert.equal(h.element(field('start_date')).value,contract.options_response.defaults.start_date);
  assert.equal(h.element(field('start_date')).min,contract.options_response.today);
  assert.equal(h.element(field('start_time')).value,'14:00');assert.equal(h.element(field('start_time')).step,'60');
  assert.equal(h.element(field('timezone')).value,'America/Sao_Paulo');assert.ok(h.element(field('timezone')).readOnly);
  assert.equal(h.element(field('device_id')).multiple,false);assert.equal(h.element(field('device_id')).value,'');
  assert.deepEqual([...h.element(field('interval_seconds')).options].map(n=>n.value),['10','20','30','40','50','60']);
  assert.equal(h.element(field('interval_seconds')).value,'60');assert.equal(h.element(field('repeat_every_30_days')).value,'false');
  assert.equal(h.element(field('text')).maxLength,1000);assert.equal(h.document.querySelectorAll('[data-sc-group]:checked,[data-sc-contact]:checked').length,0);
  assert.match(h.element('[data-sc-estimate]').textContent,/—/);assert.equal(writes(h).length,0);
});

test('group/direct union and select all deduplicate; estimate is server IANA time and crosses midnight',async t=>{
  const h=await makeSchedules();t.after(()=>h.close());await fillSchedule(h);
  assert.match(h.element('[data-sc-estimate]').textContent,/2 destinatário/);assert.match(h.element('[data-sc-estimate]').textContent,/14:02/);
  h.click(action('groups-all'));await flush();assert.match(h.element('[data-sc-estimate]').textContent,/2 destinatário/);assert.match(h.element('[data-sc-estimate]').textContent,/2 repetição/);
  h.change(field('start_time'),'23:59');await flush();assert.match(h.element('[data-sc-estimate]').textContent,/11\/01\/2030, 00:01/);
  h.click(action('contacts-all'));await flush();assert.match(h.element('[data-sc-estimate]').textContent,/3 destinatário/);assert.match(h.element('[data-sc-estimate]').textContent,/00:02/);
  const audience=h.calls.filter(c=>c.path.endsWith('/audience')).at(-1);
  assert.equal(audience.data.all_contacts,true);assert.deepEqual(audience.data.contact_keys,[]);assert.ok(!Object.hasOwn(audience.data,'timezone'));
  h.click(action('contacts-none'));h.click(action('groups-none'));await flush();assert.match(h.element('[data-sc-estimate]').textContent,/—/);
  assert.equal(writes(h).length,0);assert.equal(h.server.requests.size,0);
});

test('server estimate is respected even when browser timezone differs',async t=>{
  const h=await makeSchedules({respond:async(path,method,payload,server)=>{if(path.endsWith('/audience'))return {...await server.execute(path,method,payload),timezone:'Pacific/Auckland',estimated_end_at:'2030-01-11T10:12:00Z'};}});t.after(()=>h.close());
  await fillSchedule(h);assert.match(h.element('[data-sc-estimate]').textContent,/11\/01\/2030, 23:12/);
});

test('review does not create schedule; fresh consent and explicit confirmation create exactly once',async t=>{
  const gate=deferred();let confirming=false;
  const h=await makeSchedules({respond:async(path,method,payload,server)=>{if(path===base+'/confirm'){confirming=true;await gate.promise;return server.execute(path,method,payload);}}});t.after(()=>h.close());
  await review(h);const request=writes(h)[0],before=h.server.runs.size;
  assert.equal(request.path,base+'/review');assert.deepEqual(request.data.schedule,contract.schedule_input);
  assert.equal(h.server.runs.size,1);assert.equal(h.server.confirmations.length,0);assert.equal(h.element('[data-sc-consent]').checked,false);assert.ok(h.element(action('confirm')).disabled);
  assert.match(dialog(h).textContent,/2Distintos/);assert.match(dialog(h).textContent,/{name}/);
  h.change('[data-sc-consent]',true);h.click(action('confirm'));h.click(action('recover'));await flush();
  assert.ok(confirming);assert.equal(writes(h).length,2);assert.equal(dialog(h).dataset.scView,'loading');
  dialog(h).dispatchEvent(new h.window.Event('cancel',{cancelable:true}));assert.ok(h.document.querySelector('[data-sc-dialog]'));
  gate.resolve();await flush();assert.equal(h.server.runs.size,before+1);assert.equal(h.server.confirmations.length,1);assert.equal(dialog(h).dataset.scView,'result');
  assert.equal(writes(h)[1].data.idempotency_key,request.data.idempotency_key);assert.equal(writes(h)[1].data.confirmation,'AGENDAR');assert.equal(writes(h)[1].data.consent_confirmed,true);
  assert.equal(h.signals.find(c=>c.path===base+'/confirm').signal,undefined);
  h.click(action('acknowledge'));await flush();assert.equal(h.controller.getState().view,'detail');assert.ok(h.document.querySelector('[data-sc-recipient]'));assert.equal(h.window.history.state.orionScheduleRecovery,undefined);assert.equal(h.server.effects.length,0);
});

test('closing a prepared review leaves no schedule and a new review requires fresh consent',async t=>{
  const h=await makeSchedules();t.after(()=>h.close());await review(h);h.change('[data-sc-consent]',true);h.click(action('cancel-review'));await flush();
  assert.equal(h.document.querySelector('[data-sc-dialog]'),null);assert.equal(h.controller.getState().pending,false);assert.equal(h.server.runs.size,1);
  h.submit();await flush();assert.equal(dialog(h).dataset.scView,'review');assert.equal(h.element('[data-sc-consent]').checked,false);assert.equal(writes(h).length,2);assert.notEqual(writes(h)[0].data.idempotency_key,writes(h)[1].data.idempotency_key);
});

test('all eight existing template kinds replace text without a parallel message body',async t=>{
  const h=await makeSchedules();t.after(()=>h.close());await fillSchedule(h);h.change(field('message_type'),'template');
  assert.equal(h.document.querySelector(field('text')),null);assert.equal(h.element(field('template_id')).options.length,9);
  for(const template of contract.options_response.templates){h.change(field('template_id'),template.id);h.submit();await flush();assert.equal(dialog(h).dataset.scView,'review');const data=writes(h).at(-1).data.schedule;assert.equal(data.message_type,'template');assert.equal(data.text,null);assert.equal(data.template_id,template.id);assert.match(dialog(h).textContent,new RegExp(template.name));h.click(action('cancel-review'));await flush();}
  assert.equal(h.server.confirmations.length,0);assert.equal(h.server.effects.length,0);
});

test('text limit is UTF-16 and only the observed name substitution is accepted',async t=>{
  const h=await makeSchedules();t.after(()=>h.close());await fillSchedule(h);
  h.fill(field('text'),'😀'.repeat(501));h.submit();await flush();assert.equal(writes(h).length,0);assert.match(h.element('[role=alert]').textContent,/1.000/);
  h.fill(field('text'),'Olá, {phone}');h.submit();await flush();assert.equal(writes(h).length,0);assert.match(h.element('[role=alert]').textContent,/somente \{name\}/);
  h.fill(field('text'),'😀'.repeat(500));h.submit();await flush();assert.equal(writes(h).length,1);assert.equal(writes(h)[0].data.schedule.text.length,1000);assert.equal(dialog(h).dataset.scView,'review');
});

test('contact search is paged and stale responses cannot replace a newer selection',async t=>{
  const slow=deferred();let slowStarted=false;
  const h=await makeSchedules({respond:async(path)=>{if(path.includes('/contacts?')&&new URL(path,'https://orion.test').searchParams.get('q')==='lento'){slowStarted=true;return slow.promise;}}});t.after(()=>h.close());await fillSchedule(h);
  h.fill(field('contact_q'),'lento');h.click(action('contacts-search'));await flush();assert.ok(slowStarted);assert.equal(h.document.querySelectorAll('[data-sc-contact]').length,0);assert.match(h.element('.os-sc-contacts').textContent,/Pesquisando/);
  h.fill(field('contact_q'),'inexistente');h.click(action('contacts-search'));await flush();assert.ok(h.document.querySelector('[data-sc-contact-empty]'));
  const slowSignal=h.signals.find(c=>c.path.includes('q=lento')).signal;assert.ok(slowSignal.aborted);
  slow.resolve(contract.contacts_response);await flush();assert.ok(h.document.querySelector('[data-sc-contact-empty]'));assert.equal(h.document.querySelectorAll('[data-sc-contact]').length,0);assert.match(h.element('.os-sc-contact-selection').textContent,/Contato sintético 1/);
  assert.ok(!h.window.location.href.includes('inexistente'));assert.ok(!JSON.stringify(h.window.history.state).includes('556799'));
});

test('explicit editing reviews exactly the current occurrence versions',async t=>{
  const h=await makeSchedules();t.after(()=>h.close());h.click('[data-sc-action="edit"][data-sc-run="'+runId+'"]');await flush();
  assert.equal(h.element(field('title')).value,contract.schedule_input.title);h.fill(field('title'),'Título corrigido');h.submit();await flush();
  const call=writes(h)[0];assert.equal(call.path,base+'/'+scheduleId+'/review');assert.equal(call.data.run_id,runId);assert.equal(call.data.expected_version,1);assert.equal(call.data.expected_run_version,1);assert.equal(h.server.runs.get(runId).schedule.title,contract.schedule_input.title);
  await confirm(h);assert.equal(h.server.runs.get(runId).schedule.title,'Título corrigido');assert.equal(h.server.runs.get(runId).schedule.version,2);
});

test('if admission removed edit after listing, the new editor cannot submit an edit',async t=>{
  const h=await makeSchedules();t.after(()=>h.close());h.server.runs.get(runId).schedule.actions=['cancel_series'];h.click('[data-sc-action="edit"][data-sc-run="'+runId+'"]');await flush();
  assert.equal(h.document.querySelector('[data-sc-form="schedule"]'),null);assert.equal(writes(h).length,0);assert.match(h.element('[role=alert]').textContent,/mudaram/);
});

test('editing a future recurring occurrence starts from its local date rather than its original anchor',async t=>{
  const server=syntheticServer(),detail=server.runs.get(runId);detail.schedule.repeat_every_30_days=true;detail.definition.repeat_every_30_days=true;detail.schedule.occurrence_index=1;detail.schedule.local_date='2030-02-09';detail.schedule.scheduled_at='2030-02-09T17:00:00Z';detail.schedule.estimated_end_at='2030-02-09T17:02:00Z';
  const h=await makeSchedules({server});t.after(()=>h.close());h.click('[data-sc-action="edit"][data-sc-run="'+runId+'"]');await flush();assert.equal(h.element(field('start_date')).value,'2030-02-09');h.submit();await flush();assert.equal(writes(h)[0].data.schedule.start_date,'2030-02-09');assert.equal(server.runs.get(runId).definition.start_date,'2030-01-10');
});

test('translated attention and recipient explanations are used and multipart previews are not duplicated',async t=>{
  const server=syntheticServer(),detail=server.runs.get(runId);detail.schedule.active_filter='attention';detail.schedule.attention_reason='SCHEDULE_DEVICE_UNAVAILABLE';detail.schedule.attention_message='Reconecte o dispositivo para continuar.';detail.recipients.items[0].error_code='SCHEDULE_OPTED_OUT';detail.recipients.items[0].error_message='O contato retirou a autorização.';detail.message_preview={type:'image',text:null,caption:'Legenda única',followup:'Complemento único',template_name:'Imagem com complemento',parts:[{type:'image',text:'Legenda única'},{type:'text',text:'Complemento único'}]};
  const h=await makeSchedules({server});t.after(()=>h.close());assert.match(h.document.body.textContent,/Reconecte o dispositivo/);assert.ok(!h.document.body.textContent.includes('SCHEDULE_DEVICE_UNAVAILABLE'));h.click('[data-sc-action="detail"][data-sc-run="'+runId+'"]');await flush();assert.match(h.element('.os-sc-recipients').textContent,/retirou a autorização/);assert.deepEqual([...h.document.querySelectorAll('[data-sc-preview] .os-sc-message')].map(node=>node.textContent),['Legenda única','Complemento único']);
});

test('global pending review excludes future, running and uncertain and requires its own confirmation',async t=>{
  const server=syntheticServer(),due=copy(contract.detail_response);due.schedule.run_id='7d7a1310-88af-4d4d-8d60-000000000201';due.schedule.schedule_id='7d7a1310-88af-4d4d-8d60-000000000200';due.schedule.scheduled_at='2020-01-10T17:00:00Z';due.schedule.bucket='past_due';due.schedule.active_filter=null;due.schedule.status_label='Pendência passada';server.runs.set(due.schedule.run_id,due);
  for(const [suffix,status] of [[202,'running'],[203,'uncertain']]){const d=copy(due);d.schedule.run_id='7d7a1310-88af-4d4d-8d60-'+String(suffix).padStart(12,'0');d.schedule.status=status;server.runs.set(d.schedule.run_id,d);}
  const h=await makeSchedules({server});t.after(()=>h.close());h.click(action('pending-review'));await flush();assert.equal(dialog(h).dataset.scView,'review');assert.match(dialog(h).textContent,/1 ocorrência/);assert.equal(server.releases.length,0);
  h.change('[data-sc-consent]',true);h.click(action('confirm'));await flush();const call=writes(h).at(-1);assert.equal(call.path,base+'/pending/confirm');assert.equal(call.data.confirmation,'ENVIAR PENDENTES');assert.equal(server.releases[0].length,1);assert.equal(server.releases[0][0].run_id,due.schedule.run_id);assert.equal(server.confirmations.length,0);assert.equal(server.effects.length,0);
});

test('empty pending review disables confirmation',async t=>{
  const h=await makeSchedules();t.after(()=>h.close());h.click(action('pending-review'));await flush();h.change('[data-sc-consent]',true);assert.ok(h.element(action('confirm')).disabled);assert.equal(h.server.releases.length,0);h.click(action('cancel-review'));assert.equal(h.controller.getState().pending,false);
});

for(const verb of ['cancel_run','cancel_series'])test('explicit '+verb+' preserves the historical occurrence and never sends',async t=>{
  const h=await makeSchedules();t.after(()=>h.close());h.click('[data-sc-action="'+verb+'"][data-sc-run="'+runId+'"]');await flush();assert.equal(writes(h).length,0);assert.equal(dialog(h).dataset.scView,'cancel');
  h.click(action('commit-cancel'));await flush();const call=writes(h)[0];assert.equal(call.path,base+'/'+scheduleId+'/actions');assert.equal(call.data.action,verb);assert.equal(call.data.confirmation,'CANCELAR');assert.equal(call.data.run_id,verb==='cancel_run'?runId:null);assert.equal(call.data.expected_run_version,verb==='cancel_run'?1:null);assert.equal(h.server.runs.size,1);assert.equal(h.server.runs.get(runId).schedule.bucket,'history');assert.equal(h.server.effects.length,0);assert.equal(dialog(h).dataset.scView,'result');
});

test('cancelling a running occurrence keeps uncertain recipients visible without offering a resend',async t=>{
  const server=syntheticServer(),detail=server.runs.get(runId);Object.assign(detail.schedule,{status:'running',active_filter:'processing',status_label:'Enviando',actions:['cancel_run','cancel_series']});detail.recipients.items[0].status='uncertain';detail.recipients.items[0].status_label='Resultado incerto';detail.recipients.items[0].error_code='MESSAGE_OUTCOME_UNCERTAIN';
  const h=await makeSchedules({server,respond:async(path,method,payload)=>{if(path===base+'/'+scheduleId+'/actions'&&method==='POST'){const value=await server.execute(path,method,payload);Object.assign(detail.schedule,{status:'uncertain',active_filter:null,bucket:'history',status_label:'Resultado incerto',actions:['cancel_series'],progress:{total:2,processed:2,sent:0,pending:0,failed:0,uncertain:1,cancelled:1,percent:100}});detail.recipients.items[1].status='cancelled';detail.recipients.items[1].status_label='Cancelado';value.result.schedule=copy(detail.schedule);server.requests.get(payload.idempotency_key).operation.result.schedule=copy(detail.schedule);return value;}}});t.after(()=>h.close());h.click('[data-sc-action="cancel_run"][data-sc-run="'+runId+'"]');await flush();assert.match(dialog(h).textContent,/trabalho ainda pendente/);h.click(action('commit-cancel'));await flush();h.click(action('acknowledge'));await flush();assert.match(h.element('.os-sc-recipients').textContent,/Resultado incerto/);assert.equal(h.document.querySelectorAll('[data-sc-recipient] button').length,0);assert.equal(writes(h).length,1);assert.equal(server.effects.length,0);
});

for(const phase of ['review','confirm'])test('lost '+phase+' response is recovered by GET with the same identity, including reload',async t=>{
  let lost=false;
  const h=await makeSchedules({respond:async(path,method,payload,server)=>{if(method==='POST'&&path===base+'/'+phase&&!lost){lost=true;await server.execute(path,method,payload);throw failure(504);}}});t.after(()=>h.close());
  await fillSchedule(h);h.submit();await flush();if(phase==='confirm')await confirm(h);
  assert.equal(dialog(h).dataset.scView,'unknown');const history=copy(h.window.history.state),saved=publicHistory(h),count=writes(h).length;
  assert.deepEqual(Object.keys(saved).sort(),['tenant_id','account_scope','idempotency_key','kind','schedule_id','run_id','confirming'].sort());assert.equal(saved.account_scope,accountScope);
  assert.ok(!JSON.stringify(history).includes('556799'));assert.ok(!JSON.stringify(history).includes(contract.schedule_input.text));assert.equal(h.window.localStorage.length,0);assert.equal(h.window.sessionStorage.length,0);
  const reloaded=await makeSchedules({server:h.server,historyState:history});t.after(()=>reloaded.close());assert.equal(writes(reloaded).length,0);assert.equal(dialog(reloaded).dataset.scView,phase==='review'?'review':'result');
  if(phase==='review')assert.equal(reloaded.element('[data-sc-consent]').checked,false);
  assert.equal(reloaded.calls.filter(c=>c.path.endsWith('/requests/'+saved.idempotency_key)).length,1);assert.equal(writes(h).length,count);assert.equal(h.server.confirmations.length,phase==='confirm'?1:0);
});

test('a missing operation can only repeat its identical in-memory payload and same key explicitly',async t=>{
  let lost=false;
  const h=await makeSchedules({respond:async(path,method)=>{if(method==='POST'&&path===base+'/review'&&!lost){lost=true;throw failure(504);}}});t.after(()=>h.close());await fillSchedule(h);h.submit();await flush();
  assert.equal(writes(h).length,1);assert.equal(h.document.querySelector(action('retry-same')),null);h.click(action('recover'));await flush();assert.equal(dialog(h).dataset.scView,'unknown');assert.equal(writes(h).length,1);
  h.click(action('retry-same'));await flush();assert.equal(writes(h).length,2);assert.deepEqual(writes(h)[1],writes(h)[0]);assert.equal(dialog(h).dataset.scView,'review');assert.equal(h.server.requests.size,1);
});

test('reload with a missing request does not reconstruct private form data or admit a new key',async t=>{
  const h=await makeSchedules({historyState:{orionScheduleRecovery:recovery()}});t.after(()=>h.close());assert.equal(dialog(h).dataset.scView,'unknown');assert.equal(h.document.querySelector(action('retry-same')),null);assert.equal(h.element(action('new')).disabled,true);assert.equal(writes(h).length,0);assert.equal(publicHistory(h).idempotency_key,contract.review_request.idempotency_key);
});

test('422 correction stays on the same key after missing recovery',async t=>{
  let rejected=false;
  const h=await makeSchedules({respond:async(path,method)=>{if(path===base+'/review'&&method==='POST'&&!rejected){rejected=true;throw failure(422);}}});t.after(()=>h.close());await fillSchedule(h);h.submit();await flush();h.click(action('recover'));await flush();h.click(action('correct'));await flush();
  const key=writes(h)[0].data.idempotency_key;assert.equal(h.controller.getState().correcting,true);h.fill(field('title'),'Correção explícita');h.submit();await flush();assert.equal(writes(h)[1].data.idempotency_key,key);assert.equal(writes(h)[1].data.schedule.title,'Correção explícita');assert.equal(dialog(h).dataset.scView,'review');
});

test('a late persisted original review wins over a corrected payload with the same key',async t=>{
  let rejected=false;
  const h=await makeSchedules({respond:async(path,method)=>{if(path===base+'/review'&&method==='POST'&&!rejected){rejected=true;throw failure(422);}}});t.after(()=>h.close());await fillSchedule(h);h.submit();await flush();h.click(action('recover'));await flush();
  const original=copy(writes(h)[0]);await h.server.execute(original.path,original.method,original.data);
  h.click(action('correct'));await flush();h.fill(field('title'),'Correção que chegou depois');h.submit();await flush();assert.equal(dialog(h).dataset.scView,'unknown');assert.equal(h.controller.getState().pending,true);assert.equal(writes(h)[1].data.idempotency_key,original.data.idempotency_key);
  h.click(action('recover'));await flush();assert.equal(dialog(h).dataset.scView,'review');assert.match(dialog(h).textContent,/Aviso sintético de renovação/);assert.ok(!dialog(h).textContent.includes('Correção que chegou depois'));assert.equal(h.server.requests.size,1);
});

test('a removed group can be corrected after an authenticated missing lookup while retaining the key',async t=>{
  let removed=false;
  const h=await makeSchedules({respond:async(path,method,payload,server)=>{if(path===base+'/review'&&method==='POST'&&!removed){removed=true;server.options.groups=server.options.groups.filter(group=>group.id!==contract.schedule_input.group_ids[0]);throw failure(404);}}});t.after(()=>h.close());await fillSchedule(h);h.submit();await flush();h.click(action('recover'));await flush();h.click(action('correct'));await flush();
  assert.equal(h.document.querySelector('[data-sc-group="'+contract.schedule_input.group_ids[0]+'"]'),null);h.submit();await flush();assert.match(h.element('[role=alert]').textContent,/grupo selecionado não está mais disponível/);h.click(action('groups-none'));await flush();h.submit();await flush();assert.equal(dialog(h).dataset.scView,'review');assert.equal(writes(h)[0].data.idempotency_key,writes(h)[1].data.idempotency_key);assert.deepEqual(writes(h)[1].data.schedule.group_ids,[]);
});

test('keyboard focus stays on consent when it changes and returns to review on cancellation',async t=>{
  const h=await makeSchedules();t.after(()=>h.close());await review(h);h.element('[data-sc-consent]').focus();h.change('[data-sc-consent]',true);assert.ok(h.document.activeElement.matches('[data-sc-consent]'));
  h.click(action('cancel-review'));assert.ok(h.document.activeElement.matches('[data-sc-action="review"]'));
});

test('a late list response cannot restore a tab the user has already left',async t=>{
  const gate=deferred();
  const h=await makeSchedules({respond:async(path,method,payload,server)=>{if(method==='GET'&&path.startsWith(base+'?')&&new URL(path,'https://orion.test').searchParams.get('tab')==='history')return gate.promise;}});t.after(()=>h.close());h.click('[data-sc-tab="history"]');await flush();h.click('[data-sc-tab="active"]');await flush();assert.equal(h.document.querySelectorAll('[data-sc-row]').length,1);
  gate.resolve({...copy(contract.list_response),items:[],total:0});await flush();assert.equal(h.element('[data-sc-tab="active"]').getAttribute('aria-selected'),'true');assert.equal(h.document.querySelectorAll('[data-sc-row]').length,1);assert.ok(h.signals.find(c=>c.path.includes('tab=history')).signal.aborted);assert.equal(writes(h).length,0);
});

test('prepared recovery after an uncertain confirm cannot discard its identity',async t=>{
  let lost=false;
  const h=await makeSchedules({respond:async(path,method)=>{if(path===base+'/confirm'&&method==='POST'&&!lost){lost=true;throw failure(504);}}});t.after(()=>h.close());await review(h);await confirm(h);h.click(action('recover'));await flush();assert.equal(dialog(h).dataset.scView,'review');assert.equal(h.document.querySelector(action('cancel-review')),null);assert.equal(publicHistory(h).confirming,true);assert.equal(h.element('[data-sc-consent]').checked,false);
  dialog(h).dispatchEvent(new h.window.Event('cancel',{cancelable:true}));assert.equal(h.controller.getState().pending,true);assert.ok(h.document.querySelector('.os-sc-operation'));assert.ok(h.element('[data-sc-form="schedule"] fieldset').disabled);
});

test('server-proven stale versions release only the exactly rejected edit identity',async t=>{
  let bump=true;
  const h=await makeSchedules({respond:async(path,method,payload,server)=>{if(method==='POST'&&path===base+'/'+scheduleId+'/review'&&bump){bump=false;server.runs.get(runId).schedule.version++;return server.execute(path,method,payload);}}});t.after(()=>h.close());h.click('[data-sc-action="edit"][data-sc-run="'+runId+'"]');await flush();h.submit();await flush();
  assert.equal(h.controller.getState().pending,false);assert.equal(h.window.history.state.orionScheduleRecovery,undefined);assert.match(h.document.body.textContent,/pedido não foi registrado/);assert.equal(writes(h).length,1);
  h.submit();await flush();assert.equal(writes(h)[1].data.expected_version,2);assert.notEqual(writes(h)[0].data.idempotency_key,writes(h)[1].data.idempotency_key);
});

for(const marker of ['generic','wrong-key','wrong-run','future','confirm'])test('inconclusive conflict '+marker+' preserves pending identity',async t=>{
  const h=await makeSchedules({respond:async(path,method,payload,server)=>{
    if(method!=='POST'||!(marker==='confirm'?path===base+'/confirm':path===base+'/'+scheduleId+'/review'))return;
    const proof={reason:'stale_version',idempotency_key:payload.idempotency_key,kind:'review_edit',schedule_id:scheduleId,run_id:runId,expected_version:payload.expected_version,expected_run_version:payload.expected_run_version};
    if(marker==='wrong-key')proof.idempotency_key='7d7a1310-88af-4d4d-8d60-000000000999';if(marker==='wrong-run')proof.run_id=null;if(marker==='future')proof.expected_version=0;
    throw failure(409,{code:'SCHEDULE_CONFLICT',...(marker==='generic'?{}:{scheduleRejection:proof})});
  }});t.after(()=>h.close());h.click('[data-sc-action="edit"][data-sc-run="'+runId+'"]');await flush();h.submit();await flush();if(marker==='confirm')await confirm(h);
  assert.equal(h.controller.getState().pending,true);assert.equal(dialog(h).dataset.scView,'unknown');assert.ok(publicHistory(h).idempotency_key);assert.equal(h.document.querySelector(action('acknowledge')),null);
});

test('expired persisted review is acknowledged before a new explicit review is allowed',async t=>{
  const h=await makeSchedules();t.after(()=>h.close());await review(h);const saved=publicHistory(h);h.server.requests.get(saved.idempotency_key).operation.expires_at='2020-01-01T00:00:00Z';h.click(action('recover'));await flush();assert.equal(dialog(h).dataset.scView,'expired');assert.equal(h.document.querySelector(action('confirm')),null);assert.equal(h.controller.getState().pending,true);
  h.click(action('acknowledge'));await flush();assert.equal(h.controller.getState().pending,false);assert.equal(h.server.confirmations.length,0);assert.equal(writes(h).length,1);
});

test('malformed operation response never unlocks creation or assumes success',async t=>{
  const h=await makeSchedules({respond:async(path,method,payload,server)=>{if(path===base+'/review'&&method==='POST'){const value=await server.execute(path,method,payload);value.preview.review_version='not-a-hash';return value;}}});t.after(()=>h.close());await fillSchedule(h);h.submit();await flush();assert.equal(dialog(h).dataset.scView,'unknown');assert.equal(h.controller.getState().pending,true);h.click(action('recover'));await flush();assert.equal(dialog(h).dataset.scView,'review');assert.equal(writes(h).length,1);
});

test('unmount aborts reads and clears private DOM while already dispatched writes keep their identity',async t=>{
  const gate=deferred(),h=await makeSchedules({respond:async(path,method,payload,server)=>{if(path===base+'/review'&&method==='POST'){await gate.promise;return server.execute(path,method,payload);}}});t.after(()=>h.close());await fillSchedule(h);h.submit();await flush();const key=publicHistory(h).idempotency_key;h.controller.unmount();assert.equal(h.bridge.host.textContent,'');assert.equal(h.document.querySelector('[data-sc-dialog]'),null);assert.equal(h.signals.find(c=>c.path===base+'/review').signal,undefined);gate.resolve();await flush();assert.equal(h.bridge.host.textContent,'');assert.equal(h.window.history.state.orionScheduleRecovery.idempotency_key,key);assert.equal(h.server.requests.size,1);
});

test('late reads after unmount have an aborted signal and cannot restore private content',async t=>{
  const gate=deferred(),started=deferred();
  const h=await makeSchedules({wait:false,respond:async(path)=>{
    if(path.endsWith('/options')){started.resolve();return gate.promise;}
  }});t.after(()=>h.close());
  // Account hashing is asynchronous; unmount only after this read actually starts.
  await started.promise;
  assert.equal(h.calls.length,1);h.controller.unmount();assert.ok(h.signals[0].signal.aborted);
  gate.resolve(contract.options_response);await h.controller.ready;
  assert.equal(h.bridge.host.textContent,'');assert.equal(h.calls.length,1);
});

test('revocation clears dialog, form, contacts and recovery without another mutation',async t=>{
  const h=await makeSchedules({respond:async(path,method,payload,server)=>{if(path.includes('/requests/')&&server.revoked)throw failure(403);}});t.after(()=>h.close());await review(h);h.server.revoked=true;h.click(action('recover'));await flush();assert.equal(h.document.querySelector('[data-sc-dialog]'),null);assert.equal(h.document.querySelector(field('text')),null);assert.ok(!h.document.body.textContent.includes('Contato sintético'));assert.equal(h.window.history.state.orionScheduleRecovery,undefined);assert.equal(writes(h).length,1);
});

for(const extra of [{tenant_id:tenantB},{account_scope:'f'.repeat(64)}])test('recovery from another tenant/account is ignored before its request is read',async t=>{
  const h=await makeSchedules({historyState:{orionScheduleRecovery:recovery(extra)}});t.after(()=>h.close());assert.ok(!h.calls.some(c=>c.path.includes('/requests/')));assert.equal(h.window.history.state.orionScheduleRecovery,undefined);assert.equal(h.controller.getState().pending,false);
});

test('without account hash support private operations cannot be started',async t=>{
  const h=await makeSchedules({cryptoEnabled:false});t.after(()=>h.close());assert.equal(h.document.querySelector(action('new')),null);assert.equal(h.document.querySelector(action('pending-review')),null);assert.equal(writes(h).length,0);
});

test('recipient failed search clears previous rows and is not presented as a valid empty result',async t=>{
  let failSearch=false;
  const h=await makeSchedules({respond:async(path)=>{if(path.includes('/recipients?')&&failSearch)throw failure(500);}});t.after(()=>h.close());h.click('[data-sc-action="detail"][data-sc-run="'+runId+'"]');await flush();assert.equal(h.document.querySelectorAll('[data-sc-recipient]').length,2);
  failSearch=true;h.fill(field('recipient_q'),'nada');h.submit('recipients');await flush();assert.equal(h.document.querySelectorAll('[data-sc-recipient]').length,0);assert.equal(h.document.querySelector('[data-sc-recipient-empty]'),null);assert.match(h.element('.os-sc-recipients').textContent,/Não foi possível/);assert.ok(!h.window.location.href.includes('nada'));
});

test('private text, names and errors render as text rather than HTML',async t=>{
  const server=syntheticServer();server.runs.get(runId).schedule.title='<img src=x onerror="window.bad=1">';server.runs.get(runId).definition.title=server.runs.get(runId).schedule.title;server.runs.get(runId).message_preview.text='<script>window.bad=1</script>';server.runs.get(runId).message_preview.parts=[{type:'text',text:'<script>window.bad=1</script>'}];
  const h=await makeSchedules({server});t.after(()=>h.close());assert.match(h.document.body.textContent,/<img/);assert.equal(h.document.querySelector('img'),null);h.click('[data-sc-action="detail"][data-sc-run="'+runId+'"]');await flush();assert.equal(h.document.querySelector('script'),null);assert.equal(h.window.bad,undefined);
});

test('DST recurrence with no valid instant stays visible as attention with its reason',async t=>{
  const server=syntheticServer(),item=server.runs.get(runId).schedule;Object.assign(item,{repeat_every_30_days:true,scheduled_at:null,estimated_end_at:null,active_filter:'attention',status_label:'Necessita de atenção',attention_reason:'Horário local inexistente no fuso da empresa.'});
  const h=await makeSchedules({server});t.after(()=>h.close());assert.equal(h.document.querySelectorAll('[data-sc-row]').length,1);assert.match(h.document.body.textContent,/Horário local inexistente/);assert.equal(h.element('[data-sc-metric="attention"] strong').textContent,'1');assert.equal(writes(h).length,0);
});
