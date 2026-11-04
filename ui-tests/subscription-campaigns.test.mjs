import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {makeSuite,planState,defaultResponse} from './harness.mjs';
import {makePanel,jsonResponse} from './panel-harness.mjs';
import {makeCampaigns,syntheticServer,contract,copy,base,campaignId,groupId,recovery,install,accountScope,accountKey,failure,writes,flush,deferred,tenantA,tenantB} from './subscription-campaigns-harness.mjs';

const action=name=>'[data-cp-action="'+name+'"]';
const field=name=>'[data-cp-field="'+name+'"]';
const rowAction=(name,id=campaignId)=>'[data-cp-row="'+id+'"] '+action(name);
const business={id:tenantA,name:'Empresa sintética',role:'owner',timezone:'America/Sao_Paulo',workspace_profile:'subscription_services'};
const permissions=planState('none',{workspace_profile:'subscription_services',features:['account.manage','business.setup','campaigns.manage'],permissions:{'account.manage':true,'business.setup':true,'campaigns.manage':true}});
async function editor(h){h.click(action('new'));await flush();assert.ok(h.document.querySelector('[data-cp-form=campaign]'));}
function fillCampaign(h){const c=contract.campaign_input;for(const key of ['title','text','start_date','start_time','timezone','window_start','window_end','interval_seconds'])h.fill(field(key),String(c[key]));h.click(action('devices-all'));h.click(action('groups-all'));}
async function review(h){h.click(rowAction('review'));await flush();assert.equal(h.element('[data-cp-dialog]').dataset.cpView,'review');}
async function acknowledge(h){h.click(action('acknowledge'));await flush();}
const apiCalls=h=>h.calls.filter(c=>c.path.startsWith(base));
function assertPrivateHistory(h){const saved=h.window.history.state?.orionCampaignRecovery;if(saved)assert.deepEqual(Object.keys(saved).sort(),['account_scope','action','campaign_id','group_id','idempotency_key','kind','tenant_id']);assert.doesNotMatch(JSON.stringify(h.window.history.state),/Cliente sintético|Olá|556799|evidence|members|text|phone/);assert.equal(h.window.sessionStorage.length,0);assert.equal(h.window.localStorage.length,0);}

test('opening and refreshing campaigns only reads the scoped list and options',async t=>{
  const h=await makeCampaigns();t.after(()=>h.close());assert.match(h.element('#campaigns').textContent,/Campanhas/);assert.ok(h.document.querySelector('[data-cp-row]'));
  assert.deepEqual(h.calls.map(c=>c.method),['GET','GET']);assert.match(h.element('[data-cp-row]').textContent,/0%.*0 \/ 0/);
  h.click(action('refresh'));await flush();assert.equal(writes(h).length,0);assert.equal(h.server.requests.size,0);assert.equal(h.server.confirmations.length,0);assert.deepEqual(h.external,[]);assert.deepEqual(h.errors,[]);
});

test('the editor preserves all observed scheduling fields and starts without selected devices or groups',async t=>{
  const h=await makeCampaigns();t.after(()=>h.close());await editor(h);
  for(const key of ['title','message_type','text','start_date','start_time','timezone','window_start','window_end','interval_seconds'])assert.ok(h.element(field(key)));
  assert.equal(h.element(field('timezone')).value,'America/Sao_Paulo');assert.equal(h.element(field('window_start')).value,'08:00');assert.equal(h.element(field('window_end')).value,'18:00');assert.equal(h.element(field('interval_seconds')).value,'60');
  assert.equal(h.document.querySelectorAll('[name=device_ids]:checked,[name=group_ids]:checked').length,0);
  assert.deepEqual([...h.document.querySelectorAll('[name=weekdays]:checked')].map(n=>Number(n.value)),[1,2,3,4,5]);
  h.click('[data-cp-days=weekend]');assert.deepEqual([...h.document.querySelectorAll('[name=weekdays]:checked')].map(n=>Number(n.value)),[0,6]);h.click('[data-cp-days=none]');assert.equal(h.document.querySelectorAll('[name=weekdays]:checked').length,0);h.click('[data-cp-days=all]');assert.equal(h.document.querySelectorAll('[name=weekdays]:checked').length,7);
  assert.equal(h.element(field('text')).maxLength,3500);assert.equal(h.element(field('interval_seconds')).min,'30');assert.equal(h.element(field('interval_seconds')).max,'60');assert.match(h.element('#campaigns').textContent,/10 contatos.*por minuto/);assert.equal(writes(h).length,0);
});

test('unsupported timezone aliases do not discard usable scheduling options or device and template choices',async t=>{
  const server=syntheticServer();server.options.timezones.push('Factory','localtime','Unsupported/ClientAlias');const h=await makeCampaigns({server});t.after(()=>h.close());await editor(h);
  const supported=contract.options_response.timezones.filter(zone=>{try{new Intl.DateTimeFormat('pt-BR',{timeZone:zone});return true;}catch{return false;}});assert.ok(contract.options_response.timezones.length>500);assert.deepEqual([...h.element(field('timezone')).options].map(option=>option.value),supported);assert.equal(h.element(field('timezone')).value,'America/Sao_Paulo');assert.equal(h.document.querySelectorAll('[name=device_ids]').length,contract.options_response.devices.length);h.change(field('message_type'),'template');assert.equal(h.element(field('template_id')).options.length,9);assert.equal(writes(h).length,0);assert.deepEqual(h.errors,[]);
});

test('a saved campaign and tenant timezone remain exact when browser ICU cannot format that zone',async t=>{
  const h=await makeCampaigns({configureWindow(window){const Native=window.Intl.DateTimeFormat;window.Intl.DateTimeFormat=class extends Native{constructor(locale,options){if(options?.timeZone==='America/Sao_Paulo')throw new RangeError('Synthetic older ICU');super(locale,options);}};}});t.after(()=>h.close());
  assert.match(h.element('[data-cp-row]').textContent,/UTC \(fuso America\/Sao_Paulo sem suporte neste navegador\)/);h.click(rowAction('edit'));await flush();assert.equal(h.element(field('timezone')).value,'America/Sao_Paulo');assert.match(h.element(field('timezone')).selectedOptions[0].textContent,/formatação indisponível/);assert.match(h.element('[data-cp-form=campaign]').textContent,/fuso America\/Sao_Paulo foi preservado/);h.submit();await flush();assert.equal(writes(h)[0].data.campaign.timezone,'America/Sao_Paulo');assert.equal(h.element('[data-cp-dialog]').dataset.cpView,'result');assert.equal(h.server.confirmations.length,0);
});

test('saving a draft carries every field and never reviews, schedules or sends',async t=>{
  const h=await makeCampaigns();t.after(()=>h.close());await editor(h);fillCampaign(h);h.submit();await flush();
  assert.equal(writes(h).length,1);const call=writes(h)[0];assert.equal(call.path,base);assert.equal(call.method,'POST');assert.deepEqual(call.data.campaign,contract.campaign_input);assert.ok(call.data.idempotency_key);
  assert.equal(h.element('[data-cp-dialog]').dataset.cpView,'result');assert.equal(h.server.confirmations.length,0);assert.equal(h.server.effects.length,0);assertPrivateHistory(h);
  await acknowledge(h);assert.equal(h.document.querySelector('[data-cp-dialog]'),null);assert.match(h.element('#campaigns').textContent,/Rascunho/);assert.equal(h.window.history.state.orionCampaignRecovery,undefined);
});

for(const template of contract.options_response.templates)test('template '+template.kind+' remains selectable and mutually exclusive with text',async t=>{
  const h=await makeCampaigns();t.after(()=>h.close());await editor(h);fillCampaign(h);h.change(field('message_type'),'template');
  assert.equal(h.document.querySelector(field('text')),null);assert.equal(h.element(field('template_id')).options.length,9);h.change(field('template_id'),template.id);h.submit();await flush();
  const payload=writes(h)[0].data.campaign;assert.equal(payload.message_type,'template');assert.equal(payload.template_id,template.id);assert.equal(payload.text,null);assert.equal(h.server.confirmations.length,0);
});

test('title filtering, status and pagination are real requests without putting private queries in history',async t=>{
  const server=syntheticServer();for(let n=0;n<64;n++){const c=copy(contract.detail_response.campaign);c.id=randomUUID();c.title='Página sintética '+String(n).padStart(2,'0');server.campaigns.set(c.id,c);}
  const h=await makeCampaigns({server});t.after(()=>h.close());assert.equal(h.document.querySelectorAll('[data-cp-row]').length,30);h.click('[data-cp-scope=list]'+action('next'));await flush();assert.match(h.calls.at(-1).path,/page=2/);
  h.fill(field('q'),'Página sintética 61');h.submit('filters');await flush();assert.equal(h.document.querySelectorAll('[data-cp-row]').length,1);assert.match(h.element('[data-cp-row]').textContent,/61/);assert.equal(new URL(h.window.location.href).searchParams.get('q'),null);assert.equal(writes(h).length,0);
  h.change(field('status'),'completed');h.submit('filters');await flush();assert.ok(h.document.querySelector('[data-cp-empty]'));h.click(action('reset'));await flush();assert.equal(h.document.querySelectorAll('[data-cp-row]').length,30);
});

test('detail keeps sent, pending, failed, uncertain and cancelled recipients separate with partial parts',async t=>{
  const server=syntheticServer();server.campaigns.set(campaignId,copy(contract.scheduled_detail_response.campaign));server.recipients=copy(Object.values(contract.recipient_examples));server.recipients[0].message='<img src=x onerror=alert(1)>Texto';server.recipients[2].parts=[{type:'image',status:'accepted',text:null},{type:'text',status:'not_sent',text:'Legenda não enviada'}];
  const h=await makeCampaigns({server});t.after(()=>h.close());h.click(rowAction('detail'));await flush();
  for(const status of Object.keys(contract.recipient_examples)){h.click('[data-cp-recipient-tab='+status+']');await flush();const id=contract.recipient_examples[status].id;assert.equal(h.document.querySelectorAll('[data-cp-recipient]').length,1);assert.equal(h.element('[data-cp-recipient]').dataset.cpRecipient,id);}
  h.click('[data-cp-recipient-tab=sent]');await flush();assert.match(h.element('[data-cp-recipient]').textContent,/<img src=x/);assert.equal(h.document.querySelector('[data-cp-recipients] img, [data-cp-recipient] img'),null);assert.match(h.element('#campaigns').textContent,/não comprova entrega ou leitura/);
  h.click('[data-cp-recipient-tab=failed]');await flush();assert.match(h.element('[data-cp-recipient] details').textContent,/Imagem · Aceito pelo transporte.*Texto · Não enviado.*Legenda não enviada/);assert.match(h.element('[data-cp-recipient]').lastElementChild.textContent,/Falhou/);assert.equal(writes(h).length,0);
});

test('recipient tabs show only their completed filtered read and distinguish a failed read from an empty result',async t=>{
  const initial=deferred(),changed=deferred(),server=syntheticServer();server.campaigns.set(campaignId,copy(contract.scheduled_detail_response.campaign));server.recipients=copy(Object.values(contract.recipient_examples));let reads=0;
  const h=await makeCampaigns({server,respond(path,method,payload,current){if(path.includes('/recipients?')){reads++;if(reads===1)return initial.promise.then(()=>current.execute(path,method,payload));if(reads===2)return changed.promise.then(()=>{throw failure(503);});}}});t.after(()=>h.close());
  h.click(rowAction('detail'));await flush();assert.match(h.element('.os-cp-recipients').textContent,/Carregando destinatários/);assert.equal(h.document.querySelector('[data-cp-recipient]'),null);assert.equal(h.document.querySelector('[data-cp-recipient-empty]'),null);
  initial.resolve();await flush();assert.equal(h.element('[data-cp-recipient]').dataset.cpRecipient,contract.recipient_examples.sent.id);
  h.click('[data-cp-recipient-tab=failed]');await flush();assert.equal(h.element('[data-cp-recipient-tab=failed]').getAttribute('aria-selected'),'true');assert.match(h.element('.os-cp-recipients').textContent,/Carregando destinatários/);assert.equal(h.document.querySelector('[data-cp-recipient]'),null);assert.equal(h.document.querySelector('[data-cp-recipient-empty]'),null);
  changed.resolve();await flush();assert.match(h.element('.os-cp-recipients [role=alert]').textContent,/Não foi possível consultar/);assert.equal(h.document.querySelector('[data-cp-recipient-empty]'),null);assert.equal(h.document.querySelector('[data-cp-recipient]'),null);
  h.submit('recipients');await flush();assert.equal(h.element('[data-cp-recipient]').dataset.cpRecipient,contract.recipient_examples.failed.id);assert.equal(writes(h).length,0);
});

test('review persists only a preview and requires a fresh explicit authorization checkbox',async t=>{
  const h=await makeCampaigns();t.after(()=>h.close());await review(h);assert.equal(writes(h).length,1);assert.match(writes(h)[0].path,/\/review$/);assert.equal(h.server.confirmations.length,0);
  assert.equal(h.element('[data-cp-confirm-consent]').checked,false);assert.equal(h.element(action('confirm')).disabled,true);assert.match(h.element('[data-cp-preview]').textContent,/\{name\}/);assert.match(h.element('[data-cp-dialog]').textContent,/America\/Sao_Paulo/);assertPrivateHistory(h);
  h.click(action('cancel-review'));await flush();assert.equal(h.document.querySelector('[data-cp-dialog]'),null);assert.equal(h.window.history.state.orionCampaignRecovery,undefined);assert.equal(h.server.confirmations.length,0);assert.equal(writes(h).length,1);
  assert.equal(h.document.activeElement.dataset.cpAction,'review');assert.equal(h.document.activeElement.dataset.cpId,campaignId);
});

test('confirmation carries only frozen review identity and admits once despite duplicate input or cancel',async t=>{
  const wait=deferred(),entered=deferred();const h=await makeCampaigns({respond(path,method,payload,server){if(path.endsWith('/confirm')){entered.resolve();return wait.promise.then(()=>server.execute(path,method,payload));}}});t.after(()=>h.close());await review(h);const key=writes(h)[0].data.idempotency_key,version=h.server.requests.get(key).operation.preview.review_version;
  h.change('[data-cp-confirm-consent]',true);const confirm=h.element(action('confirm'));confirm.click();await entered.promise;confirm.click();assert.equal(h.document.querySelector(action('confirm')),null);
  const event=new h.window.Event('cancel',{cancelable:true});h.element('[data-cp-dialog]').dispatchEvent(event);assert.equal(event.defaultPrevented,true);assert.equal(h.controller.getState().busy,true);
  assert.equal(writes(h).filter(c=>c.path.endsWith('/confirm')).length,1);assert.deepEqual(writes(h).at(-1).data,{idempotency_key:key,review_version:version,confirmation:'AGENDAR',consent_confirmed:true});assert.equal(h.window.history.state.orionCampaignRecovery.idempotency_key,key);
  wait.resolve();await flush();assert.equal(h.server.confirmations.length,1);assert.equal(h.server.effects.length,0);assert.equal(h.element('[data-cp-dialog]').dataset.cpView,'result');
});

for(const phase of ['review','confirm'])test('lost '+phase+' response recovers by GET with no automatic repeat',async t=>{
  let lost=false;const h=await makeCampaigns({async respond(path,method,payload,server){if(path.endsWith('/'+phase)&&!lost){lost=true;await server.execute(path,method,payload);throw failure(503);}}});t.after(()=>h.close());
  if(phase==='review'){h.click(rowAction('review'));await flush();}else{await review(h);h.change('[data-cp-confirm-consent]',true);h.click(action('confirm'));await flush();}
  assert.equal(h.element('[data-cp-dialog]').dataset.cpView,'pending');const before=copy(writes(h));h.click('[data-cp-dialog] '+action('recover'));await flush();assert.deepEqual(writes(h),before);assert.equal(h.element('[data-cp-dialog]').dataset.cpView,phase==='review'?'review':'result');assert.equal(h.server.confirmations.length,phase==='review'?0:1);assertPrivateHistory(h);
});

test('an ambiguous missing draft can repeat only the identical payload and key after an explicit click',async t=>{
  let first=true;const h=await makeCampaigns({respond(path,method){if(path===base&&method==='POST'&&first){first=false;throw failure(503);}}});t.after(()=>h.close());await editor(h);fillCampaign(h);h.submit();await flush();const original=copy(writes(h)[0]);
  h.click('[data-cp-dialog] '+action('recover'));await flush();assert.equal(h.element('[data-cp-dialog]').dataset.cpView,'missing');assert.equal(writes(h).length,1);h.click(action('retry'));await flush();assert.deepEqual(writes(h),[original,original]);assert.equal(h.server.requests.size,1);assert.equal(h.server.confirmations.length,0);
});

for(const kind of ['create','update','group_create','group_update'])test('422 for '+kind+' allows explicit correction with the same key while other operations remain locked',async t=>{
  let invalid=true;const suffix=kind==='create'?'':kind==='update'?'/'+campaignId:kind==='group_create'?'/groups':'/groups/'+groupId;
  const method=kind.endsWith('update')?'PATCH':'POST';
  const h=await makeCampaigns({respond(path,verb){if(path===base+suffix&&verb===method&&invalid){invalid=false;throw failure(422);}}});t.after(()=>h.close());
  if(kind.startsWith('group_')){h.click(action('groups'));await flush();h.click(kind==='group_create'?action('new-group'):'[data-cp-group="'+groupId+'"] '+action('group'));await flush();h.fill(field('group_name'),'Grupo original');h.fill(field('members'),'Contato sintético;+5567999999917');h.submit('group');}
  else{if(kind==='create'){await editor(h);fillCampaign(h);}else{h.click(rowAction('edit'));await flush();}h.submit();}
  await flush();const original=copy(writes(h)[0]);assert.equal(h.element('[data-cp-dialog]').dataset.cpView,'pending');h.click(action('correct'));assert.equal(writes(h).length,1);assert.equal(h.window.history.state.orionCampaignRecovery.idempotency_key,original.data.idempotency_key);
  if(kind.startsWith('group_')){h.fill(field('group_name'),'Grupo corrigido');assert.equal(h.document.querySelector('[data-cp-blocked]'),null);h.submit('group');}else{h.fill(field('title'),'Título corrigido');h.submit();}
  await flush();assert.equal(writes(h).length,2);assert.equal(writes(h)[1].data.idempotency_key,original.data.idempotency_key);if('expected_version' in original.data)assert.equal(writes(h)[1].data.expected_version,original.data.expected_version);assert.equal(h.server.requests.size,1);assert.equal(h.server.confirmations.length,0);assert.equal(h.element('[data-cp-dialog]').dataset.cpView,'result');
});

test('correction cannot replace an operation that was already recorded under the same key',async t=>{
  let first=true;const h=await makeCampaigns({async respond(path,method,payload,server){if(path===base&&method==='POST'){if(first){first=false;await server.execute(path,method,payload);throw failure(422);}throw failure(409,{code:'CAMPAIGN_IDEMPOTENCY_CONFLICT'});}}});t.after(()=>h.close());await editor(h);fillCampaign(h);h.submit();await flush();const key=writes(h)[0].data.idempotency_key;h.click(action('correct'));h.fill(field('title'),'Correção explícita');h.submit();await flush();assert.equal(h.element('[data-cp-dialog]').dataset.cpView,'pending');assert.equal(h.window.history.state.orionCampaignRecovery.idempotency_key,key);h.click('[data-cp-dialog] '+action('recover'));await flush();assert.equal(h.element('[data-cp-dialog]').dataset.cpView,'result');assert.equal(h.server.requests.size,1);assert.equal(h.server.requests.get(key).operation.result.campaign.title,contract.campaign_input.title);
});

test('validation errors on review, confirmation and authorization never enable draft correction',async t=>{
  const h=await makeCampaigns({respond(path){if(path.endsWith('/review'))throw failure(422);}});t.after(()=>h.close());h.click(rowAction('review'));await flush();assert.equal(h.document.querySelector(action('correct')),null);assert.ok(h.window.history.state.orionCampaignRecovery);
});

test('reload recovers a prepared review without private content, confirming, or replaying a POST',async t=>{
  const server=syntheticServer(),key=contract.review_request.idempotency_key;await server.execute(base+'/'+campaignId+'/review','POST',{idempotency_key:key,expected_version:1});
  const h=await makeCampaigns({server,historyState:{orionCampaignRecovery:{...recovery(),phone:'+5567999999911',text:'private'},unrelated:'discarded'}});t.after(()=>h.close());
  assert.equal(h.element('[data-cp-dialog]').dataset.cpView,'review');assert.equal(h.element('[data-cp-confirm-consent]').checked,false);assert.equal(writes(h).length,0);assertPrivateHistory(h);assert.equal(h.server.confirmations.length,0);
});

test('404 after reload preserves the intent and offers reads without recreating missing private content',async t=>{
  const saved=recovery({kind:'create',campaign_id:null});const h=await makeCampaigns({historyState:{orionCampaignRecovery:saved}});t.after(()=>h.close());assert.equal(h.element('[data-cp-dialog]').dataset.cpView,'missing');assert.equal(h.document.querySelector(action('retry')),null);assert.equal(h.window.history.state.orionCampaignRecovery.idempotency_key,saved.idempotency_key);assert.equal(writes(h).length,0);h.click(action('close'));assert.ok(h.document.querySelector('[data-cp-operation]'));assert.equal(h.element(action('new')).disabled,true);
});

for(const override of [{tenant_id:tenantB},{account_scope:'f'.repeat(64)}])test('recovery refuses a mismatched '+Object.keys(override)[0],async t=>{
  const h=await makeCampaigns({historyState:{orionCampaignRecovery:recovery(override)}});t.after(()=>h.close());assert.equal(h.calls.some(c=>c.path.includes('/requests/')),false);assert.equal(h.window.history.state.orionCampaignRecovery,undefined);assert.equal(writes(h).length,0);
});

test('a proven stale review releases only its rejected identity and refreshes with reads',async t=>{
  let changed=false;const h=await makeCampaigns({respond(path,method,payload,server){if(path.endsWith('/review')&&!changed){changed=true;server.campaigns.get(campaignId).version++;}}});t.after(()=>h.close());h.click(rowAction('review'));await flush();
  assert.equal(writes(h).length,1);assert.equal(h.server.requests.size,0);assert.equal(h.window.history.state.orionCampaignRecovery,undefined);assert.equal(h.document.querySelector('[data-cp-dialog]'),null);assert.match(h.element('[data-cp-feedback]').textContent,/pedido não foi registrado/);assert.equal(h.element(action('new')).disabled,false);
  const first=writes(h)[0];assert.ok(h.calls.slice(h.calls.indexOf(first)+1).every(c=>c.method==='GET'));h.click(rowAction('review'));await flush();assert.equal(writes(h).length,2);assert.notEqual(writes(h)[1].data.idempotency_key,first.data.idempotency_key);assert.equal(writes(h)[1].data.expected_version,2);
});

test('generic, inconsistent and post-admission conflict proofs never forget a possibly accepted intention',async t=>{
  for(const variant of ['generic','key','kind','target','version','reason','status','after-admission']){
    const h=await makeCampaigns({async respond(path,method,payload,server){if(!path.endsWith('/review'))return;const proof={reason:'stale_version',idempotency_key:payload.idempotency_key,kind:'review',campaign_id:campaignId,group_id:null,expected_version:payload.expected_version};if(variant==='after-admission'){await server.execute(path,method,payload);throw failure(409,{code:'CAMPAIGN_CONFLICT'});}if(variant==='key')proof.idempotency_key=randomUUID();if(variant==='kind')proof.kind='action';if(variant==='target')proof.campaign_id=randomUUID();if(variant==='version')proof.expected_version=9;if(variant==='reason')proof.reason='busy';throw failure(variant==='status'?503:409,{code:'CAMPAIGN_CONFLICT',campaignRejection:variant==='generic'?undefined:proof});}});
    try{h.click(rowAction('review'));await flush();assert.equal(h.element('[data-cp-dialog]').dataset.cpView,'pending',variant);assert.equal(h.window.history.state.orionCampaignRecovery.idempotency_key,writes(h)[0].data.idempotency_key,variant);assert.equal(writes(h).length,1);if(variant==='after-admission'){h.click('[data-cp-dialog] '+action('recover'));await flush();assert.equal(h.element('[data-cp-dialog]').dataset.cpView,'review');assert.equal(writes(h).length,1);}}finally{h.close();}
  }
});

test('a late refresh from a rejected review cannot unlock or cancel a newer group mutation',async t=>{
  const oldRead=deferred(),refreshStarted=deferred(),groupWrite=deferred(),groupStarted=deferred();let rejected=false,holdRefresh=false;
  const h=await makeCampaigns({respond(path,method,payload,server){
    if(path.endsWith('/review')&&!rejected){rejected=true;holdRefresh=true;server.campaigns.get(campaignId).version++;}
    if(holdRefresh&&method==='GET'&&path.startsWith(base+'?')){holdRefresh=false;refreshStarted.resolve();return oldRead.promise;}
    if(path===base+'/groups'&&method==='POST'){groupStarted.resolve();return groupWrite.promise.then(()=>server.execute(path,method,payload));}
  }});t.after(()=>h.close());h.click(rowAction('review'));await refreshStarted.promise;
  h.click(action('groups'));await flush();h.click(action('new-group'));await flush();h.fill(field('group_name'),'Grupo novo em andamento');h.fill(field('members'),'Cliente sintético;+5567999999917');h.submit('group');await groupStarted.promise;
  const key=writes(h).at(-1).data.idempotency_key;oldRead.resolve(copy(contract.list_response));await flush();
  assert.equal(h.controller.getState().busy,true);assert.equal(h.element('[data-cp-dialog]').getAttribute('aria-busy'),'true');assert.equal(h.element('[data-cp-dialog] '+action('close')).disabled,true);
  const cancel=new h.window.Event('cancel',{cancelable:true});h.element('[data-cp-dialog]').dispatchEvent(cancel);assert.equal(cancel.defaultPrevented,true);assert.equal(h.window.history.state.orionCampaignRecovery.idempotency_key,key);
  assert.equal(writes(h).filter(c=>c.path===base+'/groups').length,1);groupWrite.resolve();await flush();assert.equal(h.element('[data-cp-dialog]').dataset.cpView,'result');assert.equal(h.server.requests.size,1);
});

test('unknown campaign admission keeps group entry points locked and cannot be reset by refresh',async t=>{
  const h=await makeCampaigns({respond(path){if(path.endsWith('/review'))throw failure(503);}});t.after(()=>h.close());h.click(rowAction('review'));await flush();const key=writes(h)[0].data.idempotency_key;h.click(action('close'));
  assert.equal(h.element(action('groups')).disabled,true);assert.equal(h.element(action('new')).disabled,true);h.click(action('refresh'));await flush();assert.equal(h.window.history.state.orionCampaignRecovery.idempotency_key,key);assert.equal(writes(h).length,1);assert.equal(h.document.querySelector('[data-cp-group]'),null);
});

test('even a matching stale marker on confirm keeps recovery rather than releasing an admitted schedule',async t=>{
  const h=await makeCampaigns({async respond(path,method,payload,server){if(path.endsWith('/confirm')){await server.execute(path,method,payload);throw failure(409,{code:'CAMPAIGN_CONFLICT',campaignRejection:{reason:'stale_version',idempotency_key:payload.idempotency_key,kind:'review',campaign_id:campaignId,group_id:null,expected_version:1}});}}});t.after(()=>h.close());await review(h);h.change('[data-cp-confirm-consent]',true);h.click(action('confirm'));await flush();assert.equal(h.element('[data-cp-dialog]').dataset.cpView,'pending');assert.equal(h.server.confirmations.length,1);assert.ok(h.window.history.state.orionCampaignRecovery);h.click('[data-cp-dialog] '+action('recover'));await flush();assert.equal(h.element('[data-cp-dialog]').dataset.cpView,'result');assert.equal(h.server.confirmations.length,1);
});

for(const [verb,status] of [['pause','scheduled'],['resume','paused'],['edit','scheduled'],['archive','draft']])test(verb+' is available only through a fresh explicit action confirmation',async t=>{
  const server=syntheticServer();const c={...copy(contract.status_examples[status]),actions:[verb]};server.campaigns.set(campaignId,c);const h=await makeCampaigns({server});t.after(()=>h.close());h.click(rowAction(verb));await flush();assert.equal(writes(h).length,0);assert.equal(h.element('[data-cp-dialog]').dataset.cpView,'action');h.click(action('commit-action'));await flush();assert.equal(writes(h).length,1);assert.deepEqual(writes(h)[0].data,{idempotency_key:writes(h)[0].data.idempotency_key,expected_version:c.version-1,action:verb});assert.equal(h.server.confirmations.length,0);assert.equal(h.server.effects.length,0);
});

test('draft editing uses the current version and does not edit a campaign whose action was revoked',async t=>{
  const h=await makeCampaigns();t.after(()=>h.close());h.click(rowAction('edit'));await flush();assert.ok(h.document.querySelector('[data-cp-form=campaign]'));assert.equal(writes(h).length,0);h.fill(field('title'),'Título revisado');h.submit();await flush();assert.equal(writes(h)[0].method,'PATCH');assert.equal(writes(h)[0].data.expected_version,1);assert.equal(h.server.confirmations.length,0);
});

test('group manual rows are explicit and saving never grants authorization',async t=>{
  const h=await makeCampaigns();t.after(()=>h.close());h.click(action('groups'));await flush();h.click(action('new-group'));await flush();h.fill(field('group_name'),'Grupo manual sintético');h.fill(field('members'),'Cliente A;+55 (67) 99999-9911\nCliente B;+5567999999912');h.submit('group');await flush();const call=writes(h)[0];assert.equal(call.path,base+'/groups');assert.deepEqual(call.data.members,[{name:'Cliente A',phone:'+55 (67) 99999-9911'},{name:'Cliente B',phone:'+5567999999912'}]);assert.equal(h.calls.some(c=>c.path.endsWith('/permissions')),false);assert.equal(h.server.confirmations.length,0);assertPrivateHistory(h);
});

test('group edit preserves blocked contacts and a separate authorization needs selection, evidence and confirmation',async t=>{
  const h=await makeCampaigns();t.after(()=>h.close());h.click(action('groups'));await flush();h.click('[data-cp-group="'+groupId+'"] '+action('group'));await flush();assert.ok(h.document.querySelector('[data-cp-blocked]'));
  h.click(action('permissions'));assert.match(h.element('[data-cp-feedback]').textContent,/Selecione os contatos/);assert.equal(writes(h).length,0);
  h.change('[data-cp-blocked]',true);h.click(action('permissions'));assert.equal(h.element(action('commit-permissions')).disabled,true);h.fill(field('evidence'),'Autorização sintética recebida na conversa.');h.change('[data-cp-permission-consent]',true);h.click(action('close'));assert.equal(writes(h).length,0);
  h.click(action('permissions'));h.fill(field('evidence'),'Autorização sintética recebida na conversa.');h.change('[data-cp-permission-consent]',true);h.click(action('commit-permissions'));await flush();const call=writes(h)[0];assert.equal(call.path,base+'/groups/'+groupId+'/permissions');assert.equal(call.data.confirmation,'AUTORIZAR');assert.deepEqual(call.data.contacts,[{phone:'+5567999999913',version:2}]);assert.equal(call.data.expected_version,1);assertPrivateHistory(h);assert.equal(h.server.confirmations.length,0);assert.match(h.element('[data-cp-dialog]').textContent,/Campanhas antigas não foram retomadas/);
});

test('invalid manual rows are blocked locally without discarding the entered form',async t=>{
  const h=await makeCampaigns();t.after(()=>h.close());h.click(action('groups'));await flush();h.click(action('new-group'));await flush();h.fill(field('group_name'),'Grupo sintético');h.fill(field('members'),'Sem separador');h.submit('group');await flush();assert.equal(writes(h).length,0);assert.equal(h.element(field('members')).value,'Sem separador');assert.match(h.element('[data-cp-feedback]').textContent,/linha 1/);
});

test('archiving a group requires confirmation and preserves prior history',async t=>{
  const h=await makeCampaigns();t.after(()=>h.close());h.click(action('groups'));await flush();h.click('[data-cp-group="'+groupId+'"] '+action('archive-group'));await flush();assert.equal(writes(h).length,0);h.click(action('commit-group-archive'));await flush();assert.equal(writes(h)[0].method,'DELETE');assert.equal(writes(h)[0].data.expected_version,1);assert.equal(h.server.groups.get(groupId).group.archived,true);assert.equal(h.server.campaigns.size,1);
});

test('revocation clears private campaign and group content plus recovery',async t=>{
  let revoked=false;const h=await makeCampaigns({respond(){if(revoked)throw failure(403);}});t.after(()=>h.close());await review(h);revoked=true;h.change('[data-cp-confirm-consent]',true);h.click(action('confirm'));await flush();assert.equal(h.window.history.state.orionCampaignRecovery,undefined);assert.equal(h.document.querySelector('[data-cp-dialog]'),null);assert.doesNotMatch(h.element('#campaigns').textContent,/Campanha sintética de renovação|Cliente sintético|556799/);assert.match(h.element('#campaigns').textContent,/dados privados foram removidos/);
});

test('late reads cannot replace a different view and unmount invalidates a late mutation',async t=>{
  const wait=deferred(),entered=deferred();let delay=false;const h=await makeCampaigns({respond(path,method){if(delay&&method==='GET'&&path.includes('?page=')){entered.resolve();return wait.promise;}}});t.after(()=>h.close());delay=true;h.click(action('refresh'));await entered.promise;h.click(action('groups'));await flush();wait.resolve(copy(contract.list_response));await flush();assert.ok(h.document.querySelector('[data-cp-group]'));assert.equal(h.document.querySelector('[data-cp-row]'),null);
  const late=deferred(),h2=await makeCampaigns({respond(path){if(path.endsWith('/review'))return late.promise;}});t.after(()=>h2.close());h2.click(rowAction('review'));await flush();const key=writes(h2)[0].data.idempotency_key;h2.controller.unmount();late.resolve({...copy(contract.review_response),idempotency_key:key,preview:{...copy(contract.review_response.preview),expires_at:new Date(Date.now()+120000).toISOString()},expires_at:new Date(Date.now()+120000).toISOString()});await flush();assert.equal(h2.element('#campaigns').textContent,'');assert.equal(h2.document.querySelector('[data-cp-dialog]'),null);assert.equal(h2.server.confirmations.length,0);
});

test('read-only access and unavailable account identity disable all mutation entry points',async t=>{
  for(const options of [{canWrite:false},{identity:null},{cryptoEnabled:false}]){const h=await makeCampaigns(options);try{assert.equal(h.element(action('new')).disabled,true);assert.equal(h.document.querySelector(rowAction('review')),null);assert.equal(writes(h).length,0);}finally{h.close();}}
});

test('malformed options and mismatched operation identity fail closed without rendering private content',async t=>{
  const h=await makeCampaigns({respond(path){if(path.endsWith('/options'))return {devices:[]};}});t.after(()=>h.close());assert.ok(h.document.querySelector('[data-cp-feedback]'));assert.equal(h.document.querySelector('[data-cp-row]'),null);assert.equal(writes(h).length,0);
  const h2=await makeCampaigns({respond(path){if(path.endsWith('/review'))return {...copy(contract.review_response),campaign_id:randomUUID()};}});t.after(()=>h2.close());h2.click(rowAction('review'));await flush();assert.equal(h2.element('[data-cp-dialog]').dataset.cpView,'pending');assert.equal(h2.document.querySelector('[data-cp-preview]'),null);assert.ok(h2.window.history.state.orionCampaignRecovery);
});

test('the Suite exposes campaigns only with its subscription feature and leaves ordinary Marketing separate',async t=>{
  for(const [profile,allowed] of [['subscription_services',true],['subscription_services',false],['standard',true]]){
    const server=syntheticServer(),loaded=deferred(),p=copy(permissions);p.workspace_profile=profile;p.permissions['campaigns.manage']=allowed;
    const h=await makeSuite({initialRoute:'campanhas',permissions:p,bridge:{tenants:[{...business,workspace_profile:profile}]},configureWindow:install,respond:path=>{if(path.startsWith(base)){loaded.resolve();return server.execute(path);}return defaultResponse(path);}});t.after(()=>h.close());
    if(profile==='subscription_services'&&allowed){await loaded.promise;await flush();assert.ok(h.document.querySelector('#osNav [data-os-route=campanhas]'));assert.ok(h.document.querySelector('#osSubscriptionCampaigns [data-cp-row]'));assert.equal(h.document.querySelector('#osNav [data-os-route=marketing]'),null);}else{await flush();assert.equal(h.document.querySelector('#osNav [data-os-route=campanhas]'),null);assert.equal(apiCalls(h).length,0);}
  }
});

test('Suite bootstrap preserves only bounded recovery while waiting for the business, then reads it',async t=>{
  const server=syntheticServer(),key=contract.review_request.idempotency_key;await server.execute(base+'/'+campaignId+'/review','POST',{idempotency_key:key,expected_version:1});const recovered=deferred();
  const h=await makeSuite({initialRoute:'campanhas',url:'https://orion.test/panel?aba=campanhas',permissions,bridge:{tenants:[],tenantId:'',businessContextReady:false},configureWindow:window=>{install(window);window.history.replaceState({orionCampaignRecovery:{...recovery(),phone:'private',content:'private'}},'',window.location.href);},respond:path=>{if(path.includes('/requests/'))recovered.resolve();return path.startsWith(base)?server.execute(path):defaultResponse(path);}});t.after(()=>h.close());await flush();assert.equal(apiCalls(h).length,0);assert.equal(h.window.history.state.orionCampaignRecovery.phone,undefined);
  h.window.OrionSuite.sync({tenants:[business],tenantId:tenantA,businessContextReady:true});await recovered.promise;await flush();assert.equal(h.document.querySelector('[data-cp-dialog]').dataset.cpView,'review');assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
  h.window.OrionSuite.sync({user:{role:'member',email:'another@example.invalid'},tenants:[business],tenantId:tenantA,businessContextReady:true});await flush();assert.equal(h.window.history.state.orionCampaignRecovery,undefined);assert.equal(h.document.querySelector('[data-cp-preview]'),null);
});

test('the complete panel sends CSRF and preserves exact stale proof through its real HTTP adapter',async t=>{
  const server=syntheticServer(),loaded=deferred();let stale=true;
  const h=await makePanel({role:'member',tenantRole:'owner',initialRoute:'campanhas',permissions,respond:async(path,method,payload,options)=>{
    if(path==='/api/v1/account/businesses')return [business];if(!path.startsWith(base))return;
    if(path.endsWith('/options'))loaded.resolve();
    if(method!=='GET')assert.equal(options.headers['X-Orion-CSRF'],'synthetic-mobile-csrf');
    if(path.endsWith('/review')&&stale){stale=false;server.campaigns.get(campaignId).version++;}
    try{return await server.execute(path,method,payload);}catch(error){return jsonResponse({detail:{code:error.code||'CAMPAIGN_CONFLICT',message:'Dados alterados.',...(error.campaignRejection?{rejection:{...error.campaignRejection,private:'must not be propagated'}}:{})}},error.status||500);}
  }});t.after(()=>h.close());await loaded.promise;await flush();h.click(rowAction('review'));await flush();assert.equal(h.window.history.state.orionCampaignRecovery,undefined);assert.equal(h.document.querySelector('[data-cp-dialog]'),null);assert.match(h.element('[data-cp-feedback]').textContent,/pedido não foi registrado/);assert.equal(h.calls.filter(c=>c.method==='POST').length,1);assert.deepEqual(h.errors,[]);
});
