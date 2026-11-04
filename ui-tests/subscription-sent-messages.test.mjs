import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {makeSuite,planState,defaultResponse} from './harness.mjs';
import {makeMessages,source,contract,base,accountKey,copy,item,listing,detail,operation,recovery,failure,flush,deferred,tenantA,tenantB} from './subscription-sent-messages-harness.mjs';
const field=name=>'[data-sm-field="'+name+'"]';
const action=name=>'[data-sm-action="'+name+'"]';
const failedId=contract.item_examples.failed.id;
const writes=h=>h.calls.filter(call=>call.method!=='GET');
const permissions=planState('none',{workspace_profile:'subscription_services',features:['account.manage','business.setup','devices.manage','chatbot.manage','templates.manage','sent_messages.manage'],permissions:{'account.manage':true,'business.setup':true,'devices.manage':true,'chatbot.manage':true,'templates.manage':true,'sent_messages.manage':true}});
const tenant={id:tenantA,name:'Empresa sintética',role:'owner',timezone:'America/Cuiaba',workspace_profile:'subscription_services'};
async function confirm(h){h.click('[data-sm-action=resend][data-sm-id="'+failedId+'"]');await flush();assert.equal(h.element('[data-sm-dialog]').dataset.smView,'confirm');}

test('message history has its own subscription permission and never opens legacy APIs',async t=>{
  const h=await makeSuite({initialRoute:'mensagens-enviadas',permissions,bridge:{tenants:[tenant]},configureWindow:window=>window.eval(source),respond:path=>path.startsWith(base+'?')?listing():defaultResponse(path)});t.after(()=>h.close());await flush();
  assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'mensagens-enviadas');assert.equal(h.document.querySelectorAll('[data-sm-row]').length,4);
  assert.deepEqual([...h.document.querySelectorAll('#osNav [data-os-route]')].map(node=>node.dataset.osRoute),['dispositivos','chatbot','testar-chatbot','templates','mensagens-enviadas','novidades','ajuda','member']);
  assert.equal(writes(h).length,0);assert.equal(h.calls.some(call=>/\/inbox|\/devices|\/management\/dashboard/.test(call.path)),false);assert.deepEqual(h.errors,[]);
});

test('a standard niche or missing history permission cannot enter through a direct URL',async t=>{
  for(const [profile,allowed] of [['standard',true],['subscription_services',false]]){
    const h=await makeSuite({initialRoute:'mensagens-enviadas',permissions:planState('elite',{workspace_profile:profile,permissions:{'account.manage':true,'sent_messages.manage':allowed}}),bridge:{tenants:[{...tenant,workspace_profile:profile}]},configureWindow:window=>window.eval(source)});t.after(()=>h.close());await flush();
    assert.equal(h.document.querySelector('#osNav [data-os-route="mensagens-enviadas"]'),null);assert.equal(h.calls.some(call=>call.path.includes('/sent-messages')),false);
  }
});

test('global message and audio cards do not become the filtered page total or invent missing durations',async t=>{
  const h=await makeMessages({data:listing([item()],{counts:{total:840,today:12,last_30_days:210,scope:'message_attempts'}})});t.after(()=>h.close());
  assert.match(h.element('[data-sm-card=total]').textContent,/840/);assert.match(h.element('[data-sm-card=today]').textContent,/12/);assert.match(h.element('[data-sm-card=last_30_days]').textContent,/210/);
  assert.match(h.element('[data-sm-card=asr_total]').textContent,/2 min 5 s/);assert.match(h.element('[data-sm-card=asr_total]').textContent,/1 transcrição.*sem duração/);assert.match(h.element('[data-sm-card=asr_daily]').textContent,/Conta · UTC/);
  assert.deepEqual([...h.element(field('status')).options].map(node=>node.value),['','failed','failed_resendable']);assert.equal(writes(h).length,0);assert.equal(h.external.length,0);
});

test('unknown audio duration is not zero and a partially known total is a lower bound',async t=>{
  for(const [known,label] of [[0,'Não informado'],[125.25,'Pelo menos 2 min 5 s']]){
    const data=listing();data.transcription.total={known_seconds:known,unknown_completions:2,complete:false};data.transcription.month={known_seconds:known,unknown_completions:1,complete:false};
    const h=await makeMessages({data});t.after(()=>h.close());
    for(const key of ['asr_total','asr_month']){assert.equal(h.element('[data-sm-card='+key+']>strong').textContent,label);assert.match(h.element('[data-sm-card='+key+']').textContent,/Histórico incompleto.*sem duração registrada/);}
  }
});

test('partial contact drafts stay local until Filtrar; automatic text search keeps the applied contact',async t=>{
  const h=await makeMessages();t.after(()=>h.close());const initial=h.calls.length;
  assert.equal(h.element(field('contact')).maxLength,40);assert.equal(h.element(field('q')).maxLength,255);
  h.fill(field('contact'),'55');await h.tick(1000);assert.equal(h.calls.length,initial);assert.equal(h.element(action('reset')).disabled,false);
  h.fill(field('q'),'assinatura');await h.tick(300);let query=new URL(h.calls.at(-1).path,'https://orion.test').searchParams;assert.equal(query.get('q'),'assinatura');assert.equal(query.has('contact'),false);assert.equal(h.element(field('contact')).value,'55');
  h.fill(field('contact'),'+5567999999901');h.submit();await flush();query=new URL(h.calls.at(-1).path,'https://orion.test').searchParams;assert.equal(query.get('contact'),'+5567999999901');assert.equal(query.get('page'),'1');assert.equal(query.has('snapshot'),false);assert.equal(writes(h).length,0);
});

test('loading, empty history, no matches, error and explicit retry remain distinct',async t=>{
  const pending=deferred();let fail=false;
  const h=await makeMessages({wait:false,respond(){if(fail)throw failure(503);return pending.promise;}});t.after(()=>h.close());await flush();assert.match(h.element('[data-sm-results]').textContent,/Carregando/);assert.equal(h.document.querySelector('[data-sm-empty]'),null);
  pending.resolve(listing([]));await h.controller.ready;assert.match(h.element('[data-sm-empty]').textContent,/Nenhuma mensagem registrada/);
  h.fill(field('q'),'Sem correspondência');await h.tick(300);assert.match(h.element('[data-sm-empty]').textContent,/corresponde aos filtros/);
  fail=true;await h.controller.load();assert.equal(h.document.querySelector('[data-sm-empty]'),null);assert.match(h.element('[data-sm-results] [role=alert]').textContent,/Não foi possível consultar/);
  fail=false;h.click(action('retry-read'));await flush();assert.ok(h.document.querySelector('[data-sm-empty]'));assert.equal(writes(h).length,0);
});

test('pagination preserves filters and snapshot in requests and real links; filter changes reset both',async t=>{
  const firstSnapshot='2026-11-04T16:00:00Z',secondSnapshot='2026-11-04T16:05:00Z',device=contract.list_response.devices[0].id;
  const makePage=page=>listing(Array.from({length:page===1?30:5},(_,i)=>item('sent',{id:'81000000-0000-4000-8000-'+String((page-1)*30+i+1).padStart(12,'0')})),{page,filtered_total:35,total_pages:2,snapshot:firstSnapshot});
  const h=await makeMessages({url:'https://orion.test/panel?aba=mensagens-enviadas&status=failed&device_id='+device+'&contact=%2B5567999999901&q=renovar',respond(path){if(!path.startsWith(base+'?'))return;const params=new URL(path,'https://orion.test').searchParams;return makePage(Number(params.get('page')));}});t.after(()=>h.close());
  assert.equal(h.document.querySelectorAll('[data-sm-row]').length,30);const link=new URL(h.element(action('next')).href);for(const [key,value] of [['status','failed'],['device_id',device],['contact','+5567999999901'],['q','renovar'],['snapshot',firstSnapshot],['page','2']])assert.equal(link.searchParams.get(key),value);
  h.click(action('next'));await flush();let query=new URL(h.calls.at(-1).path,'https://orion.test').searchParams;assert.equal(query.get('page'),'2');assert.equal(query.get('snapshot'),firstSnapshot);assert.equal(h.document.querySelectorAll('[data-sm-row]').length,5);assert.match(h.element('[data-sm-pagination]').textContent,/31–35 de 35/);
  h.fill(field('q'),'novo');await h.tick(300);query=new URL(h.calls.at(-1).path,'https://orion.test').searchParams;assert.equal(query.get('page'),'1');assert.equal(query.has('snapshot'),false);assert.equal(query.get('contact'),'+5567999999901');
  h.click(action('reset'));await flush();query=new URL(h.calls.at(-1).path,'https://orion.test').searchParams;for(const key of ['status','device_id','contact','q','snapshot'])assert.equal(query.has(key),false);assert.equal(query.get('page'),'1');assert.equal(writes(h).length,0);
});

test('an older search or page response cannot overwrite the current filter result',async t=>{
  const old=deferred();const h=await makeMessages({respond(path){if(path.includes('q=antigo'))return old.promise;if(path.includes('q=novo'))return listing([]);}});t.after(()=>h.close());
  h.fill(field('q'),'antigo');await h.tick(300);h.fill(field('q'),'novo');await h.tick(300);old.resolve(listing([item('sent',{recipient:{display_name:'Contato antigo privado',phone_e164:null}})]));await flush();assert.equal(h.element(field('q')).value,'novo');assert.doesNotMatch(h.document.body.textContent,/Contato antigo privado/);assert.equal(h.document.querySelectorAll('[data-sm-row]').length,0);
});

test('received and sent content stay separate, escaped and inert, including media metadata',async t=>{
  const attack='<img src=https://outside.invalid/pixel onerror=alert(1)>',record=item('sent',{received:{type:'text',text:'RECEBIDO '+attack,truncated:true},sent:{type:'document',type_label:'Documento',text:'ENVIADO '+attack,caption:'LEGENDA '+attack,media:{id:'85000000-0000-4000-8000-000000000001',kind:'document',mime:'application/pdf',size:20,file_name:attack},truncated:false}});
  const h=await makeMessages({data:listing([record])});t.after(()=>h.close());h.click(action('detail'));await flush();assert.equal(h.document.querySelectorAll('img,iframe,video,audio,script').length,0);assert.equal(h.external.length,0);assert.equal(writes(h).length,0);
  const content=[...h.document.querySelectorAll('.os-sent-content')];assert.match(content[0].textContent,/RECEBIDO/);assert.doesNotMatch(content[0].textContent,/ENVIADO/);assert.match(content[1].textContent,/ENVIADO/);assert.match(content[1].textContent,/LEGENDA/);assert.match(h.element('[data-sm-dialog]').textContent,/Exibição parcial/);
});

test('absolute and relative time use the server reference and business timezone without a delivery promise',async t=>{
  const record=item('sent',{created_at:'2026-11-04T14:00:00Z',finished_at:'2026-11-04T14:00:00Z'}),h=await makeMessages({data:listing([record],{timezone:'America/Cuiaba',generated_at:'2026-11-04T16:00:00Z'})});t.after(()=>h.close());
  assert.match(h.element('time').textContent,/10:00/);assert.equal(h.element('time').getAttribute('datetime'),'2026-11-04T14:00:00Z');assert.match(h.element('.os-sent-time').textContent,/há 2 horas/);assert.match(h.element('[data-sm-row]').textContent,/Aceito pelo transporte; recebimento não confirmado/);assert.equal(h.element('.os-sent-actions').children.length,0);
});

test('uncertain and pending messages cannot offer resend even if malformed action metadata suggests it',async t=>{
  const items=['uncertain','pending','sent'].map(status=>item(status,{actions:[{type:'resend',label:'Reenviar'}],resend:{eligible:true,blocked_reason:null}}));
  const h=await makeMessages({data:listing(items)});t.after(()=>h.close());assert.equal(h.document.querySelector(action('resend')),null);assert.match(h.document.body.textContent,/Resultado incerto/);assert.equal(writes(h).length,0);
});

test('resend requires a fresh authorized detail and cancellation restores focus without a write',async t=>{
  const h=await makeMessages();t.after(()=>h.close());const trigger=h.element('[data-sm-action=resend][data-sm-id="'+failedId+'"]');trigger.focus();await confirm(h);
  assert.equal(h.calls.at(-1).path,base+'/'+failedId);assert.equal(h.calls.at(-1).method,'GET');assert.match(h.element('[data-sm-dialog]').textContent,/nova tentativa de envio/);assert.match(h.element('[data-sm-dialog]').textContent,/Contato sintético/);
  h.click('[data-sm-dialog] '+action('close-dialog'));assert.equal(h.document.querySelector('[data-sm-dialog]'),null);assert.equal(h.document.activeElement,trigger);assert.equal(writes(h).length,0);
});

test('fresh eligibility or permission loss disables a previously offered resend',async t=>{
  const denied=item('failed',{actions:[],resend:{eligible:false,blocked_reason:'O dispositivo foi removido.'}}),h=await makeMessages({record:denied});t.after(()=>h.close());h.click('[data-sm-action=resend][data-sm-id="'+failedId+'"]');await flush();assert.equal(h.element('[data-sm-dialog]').dataset.smView,'detail');assert.match(h.element('[data-sm-dialog]').textContent,/dispositivo foi removido/);assert.equal(h.document.querySelector(action('confirm-resend')),null);assert.equal(writes(h).length,0);
  const readonly=await makeMessages({canWrite:false});t.after(()=>readonly.close());assert.equal(readonly.document.querySelector(action('resend')),null);
});

test('a deliberate confirmation sends only the key and fresh version once, then presents pending honestly',async t=>{
  const pending=deferred(),fresh=item('failed',{version:'f'.repeat(64)}),h=await makeMessages({record:fresh,respond(path,method){if(method==='POST')return pending.promise;}});t.after(()=>h.close());await confirm(h);
  h.click(action('confirm-resend'));await flush();assert.equal(writes(h).length,1);assert.deepEqual(Object.keys(writes(h)[0].data).sort(),['expected_version','idempotency_key']);assert.equal(writes(h)[0].data.expected_version,fresh.version);assert.match(writes(h)[0].data.idempotency_key,/^[0-9a-f-]{36}$/);
  h.element('[data-sm-dialog]').dispatchEvent(new h.window.Event('cancel',{cancelable:true}));assert.ok(h.document.querySelector('[data-sm-dialog]'));assert.equal(h.document.querySelector(action('confirm-resend')),null);
  const response=operation(writes(h)[0].data);assert.notEqual(response.request_id,writes(h)[0].data.idempotency_key);pending.resolve(response);await flush();assert.equal(h.element('[data-sm-dialog]').dataset.smView,'result');assert.match(h.element('[data-sm-dialog]').textContent,/pendente de processamento/);assert.equal(h.window.history.state.orionSentRecovery,undefined);assert.equal(writes(h).length,1);assert.deepEqual(h.errors,[]);
  assert.equal(h.document.activeElement,h.element('[data-sm-dialog] h2'));h.click('[data-sm-dialog] '+action('close-dialog'));assert.equal(h.document.activeElement,h.element('[data-sm-action=detail][data-sm-id="'+failedId+'"]'));
});

test('a lost resend response is recovered by GET, without any automatic duplicate POST',async t=>{
  let payload;const h=await makeMessages({respond(path,method,data){if(method==='POST'){payload=data;throw failure(503);}if(path.includes('/resend-requests/'))return operation(payload);}});t.after(()=>h.close());await confirm(h);h.click(action('confirm-resend'));await flush();assert.equal(h.element('[data-sm-dialog]').dataset.smView,'pending');assert.match(h.element('[data-sm-dialog]').textContent,/nenhum novo envio/);assert.equal(writes(h).length,1);
  await h.tick(60000);assert.equal(writes(h).length,1);h.click('[data-sm-dialog] '+action('reconcile'));await flush();assert.ok(h.calls.some(call=>call.method==='GET'&&call.path===base+'/resend-requests/'+payload.idempotency_key));assert.equal(writes(h).length,1);assert.equal(h.element('[data-sm-dialog]').dataset.smView,'result');
});

test('recovery 404 permits only an explicit repeat with the exact original key and version',async t=>{
  let attempts=0;const h=await makeMessages({respond(path,method,payload){if(method==='POST'){if(++attempts===1)throw failure(503);return operation(payload);}if(path.includes('/resend-requests/'))throw failure(404);}});t.after(()=>h.close());await confirm(h);h.click(action('confirm-resend'));await flush();assert.equal(h.document.querySelector(action('retry-resend')),null);
  h.click('[data-sm-dialog] '+action('reconcile'));await flush();assert.equal(writes(h).length,1);assert.ok(h.document.querySelector(action('retry-resend')));h.click(action('retry-resend'));await flush();assert.equal(writes(h).length,2);assert.deepEqual(writes(h)[0].data,writes(h)[1].data);assert.equal(h.element('[data-sm-dialog]').dataset.smView,'result');
});

test('a reload recovers only IDs scoped to the same account and tenant, using GET before another send',async t=>{
  const saved=recovery(),historyState={orionSentRecovery:saved},h=await makeMessages({historyState,respond(path){if(path.includes('/resend-requests/'))return operation(saved);}});t.after(()=>h.close());assert.equal(writes(h).length,0);assert.ok(h.calls.some(call=>call.path.endsWith('/resend-requests/'+saved.idempotency_key)));assert.equal(h.window.history.state.orionSentRecovery,undefined);assert.match(h.element('[data-sm-feedback]').textContent,/Reenvio registrado/);
  for(const override of [{tenant_id:tenantB},{account_scope:'b'.repeat(64)}]){const foreign=await makeMessages({historyState:{orionSentRecovery:{...saved,...override}}});t.after(()=>foreign.close());assert.equal(foreign.calls.some(call=>call.path.includes('/resend-requests/')),false);assert.equal(foreign.window.history.state.orionSentRecovery,undefined);assert.equal(writes(foreign).length,0);}
});

test('bootstrap preserves sanitized recovery while loading the business, then checks account and tenant before GET',async t=>{
  for(const override of [{},{tenant_id:tenantB},{account_scope:'b'.repeat(64)}]){
    const saved=recovery(override),loaded=deferred();
    const h=await makeSuite({initialRoute:'mensagens-enviadas',url:'https://orion.test/panel?aba=mensagens-enviadas',permissions,bridge:{tenants:[],tenantId:'',businessContextReady:false},configureWindow:window=>{
      Object.defineProperty(window.crypto,'subtle',{value:webcrypto.subtle});window.TextEncoder=TextEncoder;window.eval(source);
      window.history.replaceState({unrelated:'Discard this value',orionSentRecovery:{...saved,unexpected:'Private content must not survive'}},'',window.location.href);
    },respond:path=>{if(path.startsWith(base+'?')){loaded.resolve();return listing();}if(path.endsWith('/resend-requests/'+saved.idempotency_key))return operation(saved);return defaultResponse(path);}});t.after(()=>h.close());
    assert.deepEqual(copy(h.window.history.state),{orionSentRecovery:saved});assert.equal(h.calls.length,0);
    h.window.OrionSuite.sync({tenants:[tenant],tenantId:tenantA,businessContextReady:true});await loaded.promise;await flush();
    const recoveries=h.calls.filter(call=>call.path.includes('/resend-requests/'));
    assert.equal(recoveries.length,Object.keys(override).length?0:1);assert.ok(recoveries.every(call=>call.method==='GET'&&call.path===base+'/resend-requests/'+saved.idempotency_key));
    assert.equal(h.window.history.state.orionSentRecovery,undefined);assert.equal(writes(h).length,0);assert.equal(h.document.querySelector('[data-sm-dialog]'),null);assert.equal(h.document.querySelectorAll('[data-sm-row]').length,4);assert.deepEqual(h.errors,[]);
    if(!Object.keys(override).length)assert.match(h.document.querySelector('[data-sm-feedback]').textContent,/Reenvio registrado/);
  }
});

test('persisted recovery contains no contact, message, device or raw account identity',async t=>{
  const h=await makeMessages({respond(path,method){if(method==='POST')throw failure(503);}});t.after(()=>h.close());await confirm(h);h.click(action('confirm-resend'));await flush();const saved=h.window.history.state.orionSentRecovery;
  assert.deepEqual(Object.keys(saved).sort(),['account_scope','expected_version','idempotency_key','original_effect_id','root_effect_id','tenant_id']);assert.doesNotMatch(JSON.stringify(saved),/synthetic@example|Contato sintético|556799|assinatura/);assert.notEqual(saved.account_scope,accountKey);
});

test('409 requires a new detail review and never silently resends with a new version',async t=>{
  const h=await makeMessages({respond(path,method){if(method==='POST')throw failure(409);}});t.after(()=>h.close());await confirm(h);h.click(action('confirm-resend'));await flush();assert.equal(h.element('[data-sm-dialog]').dataset.smView,'error');assert.equal(h.document.querySelector(action('confirm-resend')),null);assert.equal(h.document.querySelector(action('retry-resend')),null);assert.match(h.element('[data-sm-dialog]').textContent,/situação.*mudou/);await h.tick(60000);assert.equal(writes(h).length,1);assert.equal(h.window.history.state.orionSentRecovery,undefined);
});

test('malformed or mismatched operation responses keep the operation uncertain instead of claiming success',async t=>{
  for(const extra of [{request_id:'invalid'},{original_effect_id:contract.item_examples.sent.id},{root_effect_id:contract.item_examples.sent.id},{effect_id:failedId}]){const h=await makeMessages({respond(path,method,payload){if(method==='POST')return operation(payload,extra);}});t.after(()=>h.close());await confirm(h);h.click(action('confirm-resend'));await flush();assert.equal(h.element('[data-sm-dialog]').dataset.smView,'pending');assert.ok(h.window.history.state.orionSentRecovery);assert.equal(writes(h).length,1);}
});

test('late detail and old context responses cannot reopen a closed drawer or survive unmount',async t=>{
  const delayed=deferred(),h=await makeMessages({respond(path){if(path===base+'/'+failedId)return delayed.promise;}});t.after(()=>h.close());h.click('[data-sm-action=detail][data-sm-id="'+failedId+'"]');await flush();h.click('[data-sm-dialog] '+action('close-dialog'));delayed.resolve(detail());await flush();assert.equal(h.document.querySelector('[data-sm-dialog]'),null);
  const late=deferred();let block=false;const second=await makeMessages({respond(path){if(block&&path.startsWith(base+'?'))return late.promise;}});t.after(()=>second.close());block=true;const reading=second.controller.load();second.controller.unmount();late.resolve(listing([item('sent',{recipient:{display_name:'Conteúdo da conta anterior',phone_e164:null}})]));await reading;assert.equal(second.document.querySelector('#messages').textContent,'');assert.equal(second.document.querySelector('[data-sm-dialog]'),null);
});

test('revoked access clears visible private content, recovery and all available resend controls',async t=>{
  let revoked=false;const h=await makeMessages({respond(){if(revoked)throw failure(403);}});t.after(()=>h.close());h.click('[data-sm-action=detail][data-sm-id="'+failedId+'"]');await flush();revoked=true;await h.controller.load();assert.equal(h.document.querySelector('[data-sm-dialog]'),null);assert.equal(h.document.querySelector('[data-sm-row]'),null);assert.equal(h.document.querySelector(action('resend')),null);assert.doesNotMatch(h.document.body.textContent,/Contato sintético|Sua assinatura/);assert.match(h.document.body.textContent,/acesso às mensagens/);assert.equal(writes(h).length,0);
});

test('changing the authenticated business clears the old query and drawer before mounting another context',async t=>{
  const h=await makeSuite({initialRoute:'mensagens-enviadas',url:'https://orion.test/panel?aba=mensagens-enviadas&q=ContatoPrivado',permissions,bridge:{tenants:[tenant]},configureWindow:window=>window.eval(source),respond:path=>path.includes('/sent-messages?')?listing():defaultResponse(path)});t.after(()=>h.close());await flush();
  h.window.OrionSuite.sync({user:{role:'member',email:'another@example.invalid'},tenants:[{...tenant,id:tenantB}],tenantId:tenantB});await flush();assert.equal(new URL(h.window.location.href).searchParams.has('q'),false);assert.equal(h.document.querySelector('[data-sm-dialog]'),null);assert.equal(h.document.querySelector(field('q'))?.value,'');assert.equal(writes(h).length,0);
});

test('invalid history DTOs fail closed without rendering stale rows or fake empty states',async t=>{
  for(const mutate of [value=>{value.per_page=50;},value=>{value.timezone='Invalid/Zone';},value=>{value.items[0].version='bad';},value=>{value.counts.total=null;},value=>{value.transcription.total.known_seconds=null;}]){const value=listing();mutate(value);const h=await makeMessages({data:value});t.after(()=>h.close());assert.equal(h.document.querySelector('[data-sm-row]'),null);assert.equal(h.document.querySelector('[data-sm-empty]'),null);assert.ok(h.document.querySelector('[data-sm-results] [role=alert]'));assert.equal(writes(h).length,0);}
});
