import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {webcrypto} from 'node:crypto';
import {JSDOM} from 'jsdom';
import {makeSuite,planState,defaultResponse} from './harness.mjs';
import {makeGroupAdd,syntheticServer,contract,source,base,deviceId,localGroupId,destinationId,noAdminId,sourceGroupId,uid,copy,flush,deferred,failure,mutations,previews,recovery,accountScope,tenantA,tenantB,field,action,loadContacts} from './subscription-group-add-harness.mjs';

const saved=h=>h.window.history.state?.orionGroupAddRecovery;
const selected=h=>[...h.document.querySelectorAll('[data-ga-member]:checked')].map(node=>node.dataset.gaMember);
const result=h=>h.element('[data-ga-result]').textContent;
async function setup(t,options){const h=await makeGroupAdd(options);t.after(()=>h.close());return h;}

test('connected initialization only reads options and groups through the native form',async t=>{
  const h=await setup(t);assert.equal(h.element('h1').textContent,'Adicionar contatos ao grupo');assert.equal(h.element(field('device_id')).value,deviceId);
  assert.equal(h.element(field('source')).value,'contacts');assert.equal(h.element(field('destination_group_id')).value,'');
  assert.deepEqual(h.calls.map(row=>[row.path,row.method]),[[base+'/options','GET'],[base+'/groups?device_id='+deviceId,'GET']]);
  assert.equal(h.document.querySelector('[data-ga-member]'),null);assert.equal(h.document.querySelector('dialog,iframe,style'),null);assert.deepEqual(h.errors,[]);assert.deepEqual(h.external,[]);
});

test('the producer-tested disconnected options show the actual reason and never query remote groups',async t=>{
  const server=syntheticServer();server.options=copy(contract.options_disconnected);const h=await setup(t,{server,canOpenDevices:true});
  assert.equal(contract.options_disconnected.devices[0].available,false);assert.equal(contract.options_disconnected.devices[0].status,'disconnected');
  assert.match(h.element('[data-ga-device-state]').textContent,/Desconectado/);assert.ok(h.element('[data-ga-device-state]').textContent.includes(contract.options_disconnected.devices[0].blocked_reason));
  assert.ok(h.document.querySelector('[data-ga-device-state] [data-os-route="dispositivos"]'));assert.equal(h.element(action('load')).disabled,true);
  assert.deepEqual(h.calls.map(row=>row.path),[base+'/options']);assert.equal(mutations(h).length,0);assert.equal(previews(h).length,0);
});

test('a legacy available=true cannot authorize a disconnected read or show an unpermitted Devices shortcut',async t=>{
  const server=syntheticServer();Object.assign(server.options.devices[0],{available:true,status:'disconnected',blocked_reason:null});const h=await setup(t,{server});
  assert.equal(h.calls.length,1);assert.equal(h.element(action('load')).disabled,true);assert.equal(h.document.querySelector('[data-os-route="dispositivos"]'),null);
});

test('saved contacts can be previewed without a destination but cannot create work',async t=>{
  const h=await setup(t);h.click(action('load'));await flush();assert.deepEqual(previews(h)[0].data,{...contract.preview_request_contacts,destination_group_id:null});
  assert.equal(selected(h).length,3);assert.equal(h.element(action('add')).disabled,true);assert.match(h.document.body.textContent,/Escolha o grupo de destino/);assert.equal(mutations(h).length,0);
});

test('a non-administrator destination keeps the members visible and the Add action disabled',async t=>{
  const h=await setup(t);await loadContacts(h,noAdminId);assert.ok(h.element('[data-ga-destination-state]').textContent.includes(contract.preview_no_admin.destination.blocked_reason));
  assert.equal(selected(h).length,2);assert.equal(h.element(action('add')).disabled,true);h.click(action('add'));assert.equal(mutations(h).length,0);
});

test('only ready members are preselected; existing and blocked members cannot be added',async t=>{
  const h=await setup(t);await loadContacts(h);assert.deepEqual(selected(h),contract.create_request.phones);
  const existing=contract.preview_contacts.members.find(row=>row.status==='already_member'),blocked=contract.preview_contacts.members.find(row=>row.status==='blocked');
  assert.equal(h.element('[data-ga-member="'+existing.phone+'"]').disabled,true);assert.equal(h.element('[data-ga-member="'+blocked.phone+'"]').disabled,true);
  assert.match(h.element('tbody').textContent,/Já está no grupo/);assert.match(h.element('tbody').textContent,/Não informado/);
  assert.equal(h.element('[data-ga-select-all]').closest('table'),null);assert.equal(h.element('[data-ga-select-all]').checked,true);
  h.change('[data-ga-member="'+existing.phone+'"]',true);assert.equal(h.controller.getState().selectedCount,2);
});

test('selecting a single contact creates exactly one explicit intent and stores only its scoped identity',async t=>{
  const h=await setup(t);await loadContacts(h);h.change('[data-ga-select-all]',false);assert.equal(h.element(action('add')).disabled,true);
  const phone=contract.create_request.phones[1];h.element('[data-ga-member="'+phone+'"]').focus();h.change('[data-ga-member="'+phone+'"]',true);
  assert.equal(h.document.activeElement.dataset.gaMember,phone);assert.equal(h.element('[data-ga-select-all]').indeterminate,true);assert.equal(h.element(action('add')).textContent,'Adicionar 1 membro');assert.match(h.element('[data-ga-count]').textContent,/^1 contato selecionado /);
  h.click(action('add'));await flush();assert.equal(mutations(h).length,1);assert.deepEqual(mutations(h)[0].data.phones,[phone]);
  assert.deepEqual(Object.keys(mutations(h)[0].data).sort(),['idempotency_key','phones','preview_token']);assert.match(result(h),/0 de 1 contatos processados/);
  assert.deepEqual(Object.keys(saved(h)).sort(),['account_scope','idempotency_key','tenant_id']);assert.equal(saved(h).account_scope,accountScope);
  const stored=JSON.stringify(h.window.history.state);assert.equal(stored.includes(phone),false);assert.equal(stored.includes(mutations(h)[0].data.preview_token),false);
  assert.equal(h.window.localStorage.length,0);assert.equal(h.window.sessionStorage.length,0);assert.equal(new URL(h.window.location).searchParams.size,1);
  assert.equal(h.signals.find(row=>row.path===base+'/batches').signal,undefined);
});

test('contact list, ungrouped, search and date filters use the flat contract without hidden group-source fields',async t=>{
  const h=await setup(t);h.change(field('destination_group_id'),destinationId);h.change(field('contact_group_id'),localGroupId);h.fill(field('search'),'  Contato  ');h.fill(field('created_from'),'2026-10-01');h.fill(field('created_to'),'2026-11-05');h.click(action('load'));await flush();
  assert.deepEqual(previews(h).at(-1).data,{...contract.preview_request_contacts,contact_group_id:localGroupId,search:'Contato',created_from:'2026-10-01',created_to:'2026-11-05'});
  h.change(field('contact_group_id'),'ungrouped');assert.equal(h.document.querySelector('[data-ga-member]'),null);h.click(action('load'));await flush();assert.equal(previews(h).at(-1).data.contact_group_id,null);assert.equal(previews(h).at(-1).data.without_group,true);
  for(const name of ['created_from','created_to']){const node=h.element(field(name));assert.equal(node.type,'date');assert.equal(node.hasAttribute('min'),false);assert.equal(node.hasAttribute('max'),false);assert.equal(node.required,false);}
  assert.equal(mutations(h).length,0);
});

test('an empty search result exposes zero selected contacts and cannot enqueue anything',async t=>{
  const h=await setup(t);h.fill(field('search'),'orion-verificacao-sem-contato-20261105');await loadContacts(h);
  assert.match(h.element('#osGaMembers').textContent,/\(0\)/);assert.match(h.element('tbody').textContent,/Nenhum contato corresponde a esses filtros/);assert.equal(h.element(action('add')).disabled,true);assert.equal(h.element('[data-ga-select-all]').disabled,true);assert.equal(mutations(h).length,0);
});

test('reversed dates, invalid Unicode and excessive search strings cannot issue a preview',async t=>{
  const h=await setup(t);h.fill(field('created_from'),'2026-11-06');h.fill(field('created_to'),'2026-11-05');h.click(action('load'));await flush();assert.match(h.element('[data-ga-error]').textContent,/data inicial/);
  h.fill(field('created_from'),'');h.fill(field('created_to'),'');
  for(const search of ['a'.repeat(256),'😀'.repeat(128),'a\u0000b','a\ud800b']){h.fill(field('search'),search);h.click(action('load'));await flush();assert.ok(h.document.querySelector('[data-ga-error]'));}
  assert.equal(previews(h).length,0);assert.equal(mutations(h).length,0);
});

test('switching to group source clears the table and resets local filters before its separate preview',async t=>{
  const h=await setup(t);h.fill(field('search'),'Contato');await loadContacts(h);h.change(field('source'),'group');assert.equal(h.document.querySelector('[data-ga-member]'),null);assert.equal(h.document.querySelector(field('search')),null);
  h.change(field('source_group_id'),sourceGroupId);h.click(action('load'));await flush();assert.deepEqual(previews(h).at(-1).data,contract.preview_request_group);
  h.change(field('source_group_id'),noAdminId);assert.equal(h.document.querySelector('[data-ga-member]'),null);assert.equal(previews(h).length,2);
  h.change(field('source_group_id'),destinationId);assert.equal(h.element(action('load')).disabled,true);assert.equal(mutations(h).length,0);
});

test('changing destination preserves the visible subset but disables addition until a fresh preview returns',async t=>{
  const held=deferred(),entered=deferred();const h=await setup(t,{respond:async(path,method,payload,server)=>{if(path===base+'/preview'&&payload.destination_group_id===sourceGroupId){const value=await server.execute(path,method,payload);entered.resolve();await held.promise;return value;}}});
  await loadContacts(h);h.change('[data-ga-select-all]',false);const phone=contract.create_request.phones[0];h.change('[data-ga-member="'+phone+'"]',true);
  h.change(field('destination_group_id'),sourceGroupId);await entered.promise;assert.deepEqual(selected(h),[phone]);assert.equal(h.element(action('add')).disabled,true);assert.equal(h.element('[data-ga-member="'+phone+'"]').disabled,true);h.click(action('add'));assert.equal(mutations(h).length,0);
  held.resolve();await flush();assert.deepEqual(selected(h),[phone]);assert.equal(h.element(action('add')).disabled,false);assert.equal(previews(h).length,2);
});

test('a newly existing member is removed from the preserved selection after changing destination',async t=>{
  const phone=contract.create_request.phones[0];const h=await setup(t,{respond:async(path,method,payload,server)=>{if(path===base+'/preview'&&payload.destination_group_id===sourceGroupId){const value=await server.execute(path,method,payload);value.members.find(row=>row.phone===phone).status='already_member';value.counts.ready--;value.counts.already_member++;return value;}}});
  await loadContacts(h);h.change('[data-ga-select-all]',false);h.change('[data-ga-member="'+phone+'"]',true);h.change(field('destination_group_id'),sourceGroupId);await flush();assert.deepEqual(selected(h),[]);assert.equal(h.element(action('add')).disabled,true);
});

test('a late preview is aborted and ignored after a source change',async t=>{
  const held=deferred(),entered=deferred();const h=await setup(t,{respond:async(path,method,payload,server)=>{if(path===base+'/preview'){const value=await server.execute(path,method,payload);entered.resolve();await held.promise;return value;}}});
  h.change(field('destination_group_id'),destinationId);h.click(action('load'));await entered.promise;const signal=h.signals.find(row=>row.path===base+'/preview').signal;
  h.change(field('source'),'group');assert.equal(signal.aborted,true);held.resolve();await flush();assert.equal(h.document.querySelector('[data-ga-member]'),null);assert.equal(h.controller.getState().source,'group');assert.equal(mutations(h).length,0);
});

test('a late preview cannot populate a changed device or restore its old destination',async t=>{
  const server=syntheticServer(),held=deferred(),entered=deferred();server.options.devices.push({...copy(server.options.devices[0]),id:uid(117),name:'Outro dispositivo'});
  const h=await setup(t,{server,respond:async(path,method,payload,s)=>{if(path===base+'/preview'){const value=await s.execute(path,method,payload);entered.resolve();await held.promise;return value;}}});
  h.change(field('device_id'),deviceId);await flush();h.change(field('destination_group_id'),destinationId);h.click(action('load'));await entered.promise;
  h.change(field('device_id'),uid(117));await flush();held.resolve();await flush();assert.equal(h.element(field('device_id')).value,uid(117));assert.equal(h.element(field('destination_group_id')).value,'');assert.equal(h.document.querySelector('[data-ga-member]'),null);assert.equal(mutations(h).length,0);
});

test('expired previews remain readable but cannot add contacts',async t=>{
  const h=await setup(t,{respond:async(path,method,payload,s)=>{if(path===base+'/preview'){const value=await s.execute(path,method,payload);value.expires_at=new Date(Date.now()-1000).toISOString();return value;}}});
  await loadContacts(h);assert.equal(h.element(action('add')).disabled,true);assert.match(h.document.body.textContent,/seleção expirou/);assert.equal(h.element(action('load')).disabled,false);assert.equal(mutations(h).length,0);
});

for(const [name,mutate]of [
  ['count mismatch',v=>v.counts.ready++],
  ['duplicate phone',v=>v.members[1].phone=v.members[0].phone],
  ['non-phone identifier',v=>v.members[0].phone='opaque@lid'],
  ['foreign device',v=>v.device.id=uid(999)],
  ['wrong destination',v=>v.destination.id=noAdminId],
  ['wrong filters',v=>v.filters.search='other'],
  ['token without administrator authority',v=>v.destination.is_admin=false],
  ['oversized token',v=>v.preview_token='a'.repeat(360001)],
])test('invalid preview fails closed: '+name,async t=>{
  const h=await setup(t,{respond:async(path,method,payload,s)=>{if(path===base+'/preview'){const value=await s.execute(path,method,payload);mutate(value);return value;}}});
  await loadContacts(h);assert.equal(h.document.querySelector('[data-ga-member]'),null);assert.ok(h.document.querySelector('[data-ga-error]'));assert.equal(mutations(h).length,0);
});

test('lost create response recovers the same request by GET and never resubmits membership changes',async t=>{
  const h=await setup(t,{respond:async(path,method,payload,s)=>{if(path===base+'/batches'&&method==='POST'){await s.execute(path,method,payload);throw failure(503);}}});
  await loadContacts(h);h.click(action('add'));await flush();assert.equal(mutations(h).length,1);assert.equal(h.server.creates.length,1);assert.ok(h.document.querySelector('[data-ga-result]'));
  const key=mutations(h)[0].data.idempotency_key;assert.equal(saved(h).idempotency_key,key);assert.ok(h.calls.some(row=>row.path===base+'/requests/'+key&&row.method==='GET'));assert.equal(h.calls.some(row=>/invite|message|retry|cancel/.test(row.path)),false);
});

test('a missing response and missing recovery keep only a lookup action, without an implicit new key or retry',async t=>{
  let failRead=true;const h=await setup(t,{respond:async(path,method,payload,s)=>{if(path===base+'/batches'&&method==='POST'){await s.execute(path,method,payload);throw failure(503);}if(path.includes('/requests/')&&failRead)throw failure(404,{code:'GROUP_ADD_NOT_FOUND'});}});
  await loadContacts(h);h.click(action('add'));await flush();const key=saved(h).idempotency_key;assert.ok(h.document.querySelector('[data-ga-pending]'));assert.equal(h.document.querySelector(action('retry')),null);assert.equal(h.document.querySelector(action('new')),null);assert.equal(h.document.querySelector(action('add')),null);
  failRead=false;h.click(action('recover'));await flush();assert.equal(mutations(h).length,1);assert.equal(saved(h).idempotency_key,key);assert.ok(h.document.querySelector('[data-ga-result]'));assert.equal(h.calls.filter(row=>row.path===base+'/requests/'+key).length,2);
});

test('reload recovery sanitizes extra fields and reads the remembered batch without reusing private payloads',async t=>{
  const record=recovery(),h=await setup(t,{historyState:{orionGroupAddRecovery:{...record,phones:['private'],preview_token:'private'}},respond:async path=>{if(path===base+'/requests/'+record.idempotency_key)return copy(contract.batch_recovered);}});
  assert.deepEqual(copy(saved(h)),record);assert.deepEqual(h.calls.map(row=>[row.path,row.method]),[[base+'/requests/'+record.idempotency_key,'GET']]);assert.ok(h.document.querySelector('[data-ga-result]'));assert.equal(mutations(h).length,0);
});

test('foreign tenant and account recovery records are discarded before any request lookup',async t=>{
  for(const record of [recovery({tenant_id:tenantB}),recovery({account_scope:'b'.repeat(64)})]){const h=await setup(t,{historyState:{orionGroupAddRecovery:record}});assert.equal(saved(h),undefined);assert.equal(h.calls.some(row=>row.path.includes('/requests/')),false);assert.equal(mutations(h).length,0);}
});

test('a failed history write and missing identity cannot start membership work',async t=>{
  const h=await setup(t);await loadContacts(h);h.window.history.replaceState=()=>{throw Error('History unavailable');};h.click(action('add'));await flush();assert.equal(mutations(h).length,0);assert.match(h.element('[data-ga-error]').textContent,/identificação deste pedido/);
  const anonymous=await setup(t,{identity:null});await loadContacts(anonymous);assert.equal(anonymous.element(action('add')).disabled,true);assert.equal(mutations(anonymous).length,0);
});

test('confirmed stale context rejection discards the obsolete preview and does not poll or repeat the mutation',async t=>{
  const h=await setup(t,{respond:async(path,method)=>{if(path===base+'/batches'&&method==='POST')throw failure(409,{detail:{code:'GROUP_ADD_CONTEXT_CHANGED'}});}});
  await loadContacts(h);h.click(action('add'));await flush();assert.equal(mutations(h).length,1);assert.equal(saved(h),undefined);assert.equal(h.document.querySelector('[data-ga-member]'),null);assert.match(h.element('[data-ga-error]').textContent,/conexão ou o grupo mudou/);assert.equal(h.calls.some(row=>row.path.includes('/requests/')),false);
});

test('partial and uncertain outcomes remain distinct per contact without invitations or automatic retries',async t=>{
  const h=await setup(t);await loadContacts(h);h.click(action('add'));await flush();const key=saved(h).idempotency_key;
  h.server.outcome(key,['added','not_added'],'partial');h.click(action('refresh'));await flush();assert.match(result(h),/Adicionado/);assert.match(result(h),/Não adicionado/);assert.match(result(h),/2 de 2 contatos processados/);
  h.server.outcome(key,['added','unknown'],'unknown');h.click(action('refresh'));await flush();assert.match(result(h),/Resultado incerto/);assert.match(result(h),/não são repetidos automaticamente/);assert.equal(mutations(h).length,1);assert.equal(h.calls.some(row=>/invite|retry|cancel/.test(row.path)),false);
});

test('progress polling uses real three-second reads and is cleared on unmount',async t=>{
  const timers=new Map();let id=0;const h=await setup(t,{configureWindow:window=>{window.setTimeout=(fn,delay)=>{const next=++id;timers.set(next,{fn,delay});return next;};window.clearTimeout=timer=>timers.delete(timer);}});
  await loadContacts(h);h.click(action('add'));await flush();const key=saved(h).idempotency_key,scheduled=[...timers.values()].find(timer=>timer.delay===3000);assert.ok(scheduled);assert.match(result(h),/0 de 2 contatos processados/);
  h.server.outcome(key,['added','running'],'running');scheduled.fn();await flush();assert.match(result(h),/1 de 2 contatos processados/);assert.equal(mutations(h).length,1);assert.ok(h.calls.some(row=>/\/batches\//.test(row.path)&&row.method==='GET'));
  h.window.OrionSubscriptionGroupAdd.unmount();const count=h.calls.length;for(const timer of timers.values())timer.fn();await flush();assert.equal(h.calls.length,count);
});

test('a stale progress version cannot overwrite a newer known outcome',async t=>{
  let stale=null;const h=await setup(t,{respond:async path=>{if(stale&&path.includes('/batches/'))return stale;}});await loadContacts(h);h.click(action('add'));await flush();const key=saved(h).idempotency_key;
  stale=copy(h.server.requests.get(key));h.server.outcome(key,['added','running'],'running');const fresh=copy(h.server.requests.get(key));stale=null;h.click(action('refresh'));await flush();assert.match(result(h),/1 de 2/);
  stale={...fresh,batch:{...fresh.batch,version:1}};h.click(action('refresh'));await flush();assert.match(result(h),/1 de 2/);assert.ok(h.document.querySelector('[data-ga-error]'));assert.equal(mutations(h).length,1);
});

test('recovery rejects zero ordinals and batch status inconsistent with the actual member outcomes',async t=>{
  const cases=[
    ['zero ordinal',value=>value.members[0].ordinal=0],
    ['completed with uncertainty',value=>{Object.assign(value,copy(contract.batch_unknown));value.batch.status='completed';}],
    ['unknown without uncertainty',value=>value.batch.status='unknown'],
    ['partial with only successes',value=>value.batch.status='partial'],
    ['cancelled with successful contacts',value=>{Object.assign(value,copy(contract.batch_unknown));value.batch.status='cancelled';value.members[0].status='added';value.batch.progress.unknown=0;value.batch.progress.added=1;}],
    ['active with no remaining work',value=>value.batch.status='running'],
  ];
  for(const [name,mutate]of cases){const record=recovery(),value=copy(contract.batch_recovered);mutate(value);const h=await setup(t,{historyState:{orionGroupAddRecovery:record},respond:async path=>{if(path.includes('/requests/'))return value;}});assert.equal(h.document.querySelector('[data-ga-result]'),null,name);assert.ok(h.document.querySelector('[data-ga-pending]'),name);assert.equal(saved(h).idempotency_key,record.idempotency_key);assert.equal(mutations(h).length,0);}
});

test('progress after cold recovery cannot change device, destination, source or member identity',async t=>{
  const changes=[
    value=>value.batch.device.id=uid(999),
    value=>value.batch.destination.id=noAdminId,
    value=>value.batch.source='group',
    value=>{value.members.push({...copy(value.members[0]),id:uid(998),phone:'+551199990009',ordinal:3,status:'pending'});value.batch.total++;value.batch.progress.pending++;},
    value=>value.members[0].phone='+551199990009',
    value=>{value.members[0].ordinal=2;value.members[1].ordinal=1;},
  ];
  for(const mutate of changes){const initial=copy(contract.batch_progress),changed=copy(initial);changed.batch.version++;mutate(changed);const h=await setup(t,{historyState:{orionGroupAddRecovery:recovery()},respond:async path=>path.includes('/requests/')?initial:path.includes('/batches/')?changed:undefined});assert.ok(h.document.querySelector('[data-ga-result]'));h.click(action('refresh'));await flush();assert.match(result(h),/1 de 2 contatos processados/);assert.ok(h.document.querySelector('[data-ga-error]'));assert.equal(mutations(h).length,0);}
});

test('busy before admission releases the rejected intent, while an idempotency conflict only permits lookup',async t=>{
  for(const [code,released]of [['GROUP_ADD_BUSY',true],['GROUP_ADD_IDEMPOTENCY_CONFLICT',false]]){const h=await setup(t,{respond:async(path,method)=>{if(path===base+'/batches'&&method==='POST')throw failure(409,{detail:{code}});}});await loadContacts(h);h.click(action('add'));await flush();assert.equal(mutations(h).length,1);assert.equal(saved(h)===undefined,released);assert.equal(h.calls.some(row=>row.path.includes('/requests/')),!released);assert.equal(h.document.querySelector(action('retry')),null);}
});

test('a malformed create result can only trigger lookup of the original identity',async t=>{
  const h=await setup(t,{respond:async(path,method,payload,s)=>{if(path===base+'/batches'&&method==='POST'){const value=await s.execute(path,method,payload);value.batch.total++;return value;}}});
  await loadContacts(h);h.click(action('add'));await flush();assert.equal(mutations(h).length,1);assert.ok(h.document.querySelector('[data-ga-result]'));assert.equal(h.calls.filter(row=>row.path.includes('/requests/')).length,1);
});

test('loss of access clears selected phones and recovery before another membership action',async t=>{
  const h=await setup(t);await loadContacts(h);h.access.allowed=false;h.click(action('add'));await flush();assert.equal(h.controller.getState().revoked,true);assert.equal(h.controller.getState().selectedCount,0);assert.equal(saved(h),undefined);assert.equal(h.bridge.host.getAttribute('aria-busy'),'false');assert.match(h.document.body.textContent,/acesso a esta empresa mudou/);assert.equal(mutations(h).length,0);
});

test('unmount does not cancel a posted change and its late result cannot repopulate the old page',async t=>{
  const held=deferred(),entered=deferred();const h=await setup(t,{respond:async(path,method,payload,s)=>{if(path===base+'/batches'&&method==='POST'){const value=await s.execute(path,method,payload);entered.resolve();await held.promise;return value;}}});
  await loadContacts(h);h.click(action('add'));await entered.promise;const key=saved(h).idempotency_key;assert.equal(h.signals.find(row=>row.path===base+'/batches').signal,undefined);
  h.window.OrionSubscriptionGroupAdd.unmount();held.resolve();await flush();assert.equal(h.bridge.host.childElementCount,0);assert.equal(saved(h).idempotency_key,key);assert.equal(h.calls.some(row=>row.path.includes('/requests/')),false);
});

test('the real suite request boundary permits active planless owners and denies every other niche or access state',{timeout:10000},async t=>{
  for(const scenario of [{},{profile:'standard'},{allowed:false},{status:'paused'},{role:'viewer'}]){
    const profile=scenario.profile||'subscription_services',grant=scenario.allowed!==false,company={id:tenantA,name:'Empresa sintética',role:scenario.role||'owner',status:scenario.status||'active',workspace_profile:profile};
    const permissions=planState('none',{workspace_profile:profile,status:'no_access',features:['account.manage','business.setup',...(grant?['group_add.manage']:[])],permissions:{'account.manage':true,'business.setup':true,'group_add.manage':grant}});
    const server=syntheticServer(),loaded=deferred();const h=await makeSuite({initialRoute:'adicionar-ao-grupo',permissions,bridge:{tenants:[company]},configureWindow:window=>{Object.defineProperty(window.crypto,'subtle',{value:webcrypto.subtle,configurable:true});window.TextEncoder=TextEncoder;window.eval(source);},respond:(path,method,payload)=>{if(path.startsWith(base)){loaded.resolve();return server.execute(path,method,payload);}return defaultResponse(path);}});t.after(()=>h.close());
    const allowed=profile==='subscription_services'&&grant&&company.status==='active'&&company.role==='owner';
    if(allowed){await loaded.promise;await flush();assert.ok(h.document.querySelector('#osSubscriptionGroupAdd [data-ga-form]'));assert.ok(h.document.querySelector('#osNav [data-os-route="adicionar-ao-grupo"]'));assert.equal(permissions.active,false);assert.equal(h.document.querySelector('#osNav [data-os-route="inbox"]'),null);}
    else{await flush();assert.equal(h.document.querySelector('#osNav [data-os-route="adicionar-ao-grupo"]'),null);assert.equal(h.calls.some(row=>row.path.startsWith(base)),false);}
    assert.equal(h.calls.some(row=>row.method!=='GET'),false);assert.deepEqual(h.errors,[]);
  }
});

test('the actual panel bootstrap preserves only the sanitized addition identity and never a tenant URL',async t=>{
  const html=await readFile(new URL('../app/static/panel.html',import.meta.url),'utf8'),prefix=html.match(/async function showApp\([^)]*\)\{[\s\S]*?(?=\n suiteUser=)/)?.[0];assert.ok(prefix);assert.match(html,/<script src="\/panel-assets\/orion-subscription-group-add\.js"><\/script>/);
  const dom=new JSDOM('',{url:'https://orion.test/painel?aba=adicionar-ao-grupo&empresa=untrusted',runScripts:'outside-only'});t.after(()=>dom.window.close());dom.window.eval(source);const record=recovery();dom.window.history.replaceState({orionGroupAddRecovery:{...record,phones:['discard'],preview_token:'discard'},unrelated:'discard'},'');
  dom.window.eval('var authEpoch=1,suiteTenantId="",businessRows=[],businessContextReady=false,businessContextError="";function resetWizard(){};function showSessionBoot(){};'+prefix+'\n}');await dom.window.showApp('member','Synthetic','synthetic@example.invalid');assert.deepEqual(copy(dom.window.history.state),{orionGroupAddRecovery:record});assert.equal(new URL(dom.window.location).searchParams.has('empresa'),false);assert.equal(new URL(dom.window.location).searchParams.get('aba'),'adicionar-ao-grupo');
});


test('partially available destinations remain selectable with explicit missing-group notice',async t=>{
  const h=await setup(t,{respond:async(path,method,payload,server)=>{
    if(path.includes('/groups?'))return {
      ...await server.execute(path,method,payload),unavailable_groups:1
    };
  }});
  assert.match(h.document.body.textContent,/1 grupo\(s\) não puderam ser exibidos/);
  assert.ok(h.element(field('destination_group_id')).options.length>1);
  assert.equal(mutations(h).length,0);
  assert.equal(previews(h).length,0);
  assert.deepEqual(h.errors,[]);
});

test('untrusted omission counts cannot enable group-add mutations',async t=>{
  const h=await setup(t,{respond:async(path,method,payload,server)=>{
    if(path.includes('/groups?'))return {
      ...await server.execute(path,method,payload),unavailable_groups:1001
    };
  }});
  assert.ok(h.document.querySelector('[data-ga-error]'));
  assert.equal(h.element(field('destination_group_id')).options.length,1);
  assert.equal(mutations(h).length,0);
});