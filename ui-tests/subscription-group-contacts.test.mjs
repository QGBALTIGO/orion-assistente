import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {webcrypto} from 'node:crypto';
import {JSDOM} from 'jsdom';
import {makeSuite,planState,defaultResponse} from './harness.mjs';
import {makeGroupContacts,syntheticServer,contract,source,base,deviceId,groupId,remoteId,uid,copy,flush,deferred,failure,writes,recovery,accountScope,tenantA,tenantB,field,action,loadMembers} from './subscription-group-contacts-harness.mjs';

const saved=h=>h.window.history.state?.orionGroupContactsRecovery;
async function setup(t,options){const h=await makeGroupContacts(options);t.after(()=>h.close());return h;}
const selected=h=>[...h.document.querySelectorAll('[data-gc-member]:checked')].map(node=>node.dataset.gcMember);
const result=h=>h.element('[data-gc-result]').textContent;

test('initial loading lists the one device and its groups with GET only, without loading or saving members',async t=>{
  const h=await setup(t);assert.equal(h.element('h1').textContent,'Extrator de contatos de grupos');
  assert.equal(h.element(field('device_id')).value,deviceId);assert.equal(h.element(field('group_id')).options.length,3);
  assert.equal(h.element(action('load-members')).disabled,true);assert.equal(h.document.querySelector('[data-gc-member]'),null);
  assert.deepEqual(h.calls.map(call=>[call.path,call.method]),[[base+'/options','GET'],[base+'/groups?device_id='+deviceId,'GET']]);
  assert.equal(writes(h).length,0);assert.deepEqual(h.external,[]);assert.deepEqual(h.errors,[]);
});

test('loading selects every available number, shows actual roles and leaves missing names uninvented',async t=>{
  const h=await setup(t);await loadMembers(h);
  assert.deepEqual(selected(h),contract.members.members.map(row=>row.phone));assert.equal(h.element('[data-gc-select-all]').checked,true);
  assert.deepEqual([...h.document.querySelectorAll('thead th')].map(node=>node.textContent),['','WhatsApp','Nome','Função']);
  assert.match(h.element('tbody').textContent,/Proprietário/);assert.match(h.element('tbody').textContent,/Administrador/);assert.match(h.element('tbody').textContent,/Não informado/);
  assert.match(h.element('[data-gc-count]').textContent,/3 de 3/);assert.equal(h.element(field('target_group_id')).value,'new');
  assert.equal(h.element(field('new_group_name')).value,'Grupo: Grupo sintético');assert.equal(writes(h).length,0);
});

test('select all can clear the selection and saving a subset sends exactly those phones with the new-group union',async t=>{
  const h=await setup(t);await loadMembers(h);h.change('[data-gc-select-all]',false);
  assert.equal(h.element(action('import')).disabled,true);assert.deepEqual(selected(h),[]);
  const row=h.element('[data-gc-member="+5567999999902"]');row.focus();h.change('[data-gc-member="+5567999999902"]',true);
  assert.equal(h.document.activeElement.dataset.gcMember,'+5567999999902');assert.equal(h.element('[data-gc-select-all]').indeterminate,true);
  assert.equal(h.element(action('import')).textContent,'Salvar 1 contatos');h.click(action('import'));await flush();
  assert.equal(writes(h).length,1);const payload=writes(h)[0].data;
  assert.deepEqual(payload.phones,['+5567999999902']);assert.equal(payload.target_group_id,null);assert.equal(payload.expected_version,null);assert.equal(payload.new_group_name,'Grupo: Grupo sintético');
  assert.equal(h.server.imports.length,1);assert.match(result(h),/Selecionados: 1/);assert.match(result(h),/Adicionados: 1/);
  assert.deepEqual(Object.keys(saved(h)).sort(),['account_scope','idempotency_key','tenant_id']);assert.equal(saved(h).account_scope,accountScope);
  assert.equal(JSON.stringify(h.window.history.state).includes(payload.preview_token),false);assert.equal(JSON.stringify(h.window.history.state).includes('5567'),false);
});

test('an existing-list import sends its version and preserves additive result counts, old names and unrelated contacts',async t=>{
  const h=await setup(t);await loadMembers(h);h.change(field('target_group_id'),groupId);
  assert.equal(h.document.querySelector(field('new_group_name')),null);h.click(action('import'));await flush();
  const payload=writes(h)[0].data;assert.equal(payload.target_group_id,groupId);assert.equal(payload.expected_version,2);assert.equal(payload.new_group_name,null);
  assert.deepEqual(payload.phones,contract.import_request.phones);assert.match(result(h),/Adicionados: 1/);assert.match(result(h),/Já existentes: 1/);assert.match(result(h),/Restaurados: 1/);
  assert.equal(h.server.local.get(groupId).get('+5567999999902').name,'Nome local confirmado');
  assert.equal(h.server.local.get(groupId).get('+5567999999903').name,'Nome local anterior');
  assert.equal(h.server.local.get(groupId).get('+5567999999904').active,true);assert.equal(h.server.local.get(groupId).get('+5567999999904').blocked,true);
  assert.equal(h.calls.some(call=>/confirm|send|bulk-message|campaigns/.test(call.path)),false);
});

test('changing the group drops its prior members, selection and target before another explicit load',async t=>{
  const h=await setup(t);await loadMembers(h);h.change(field('target_group_id'),groupId);h.change(field('group_id'),h.server.groups[1].id);
  assert.equal(h.document.querySelector('[data-gc-member]'),null);assert.equal(h.document.querySelector(action('import')),null);
  assert.equal(h.calls.filter(call=>call.path.includes('/members?')).length,1);
  h.click(action('load-members'));await flush();assert.deepEqual(selected(h),['+5567999999905']);
  assert.equal(h.element(field('target_group_id')).value,'new');assert.equal(h.element(field('new_group_name')).value,'Grupo: Outro grupo sintético');assert.equal(writes(h).length,0);
});

test('a late member response cannot repopulate a different device and read cancellation never aborts writes',{timeout:10000},async t=>{
  const pending=deferred(),entered=deferred(),server=syntheticServer();server.options.devices.push({...copy(server.options.devices[0]),id:uid(17),name:'Outro dispositivo'});
  const h=await setup(t,{server,respond:async(path,method,payload,s)=>{if(path.includes('/members?')){const value=await s.execute(path,method,payload);entered.resolve();await pending.promise;return value;}}});
  h.change(field('device_id'),deviceId);await flush();h.change(field('group_id'),remoteId);h.click(action('load-members'));await entered.promise;
  const signal=h.signals.find(row=>row.path.includes('/members?')).signal;
  h.change(field('device_id'),uid(17));await flush();assert.equal(signal.aborted,true);pending.resolve();await flush();
  assert.equal(h.element(field('device_id')).value,uid(17));assert.equal(h.document.querySelector('[data-gc-member]'),null);assert.equal(h.controller.getState().selectedCount,0);assert.equal(writes(h).length,0);
});

test('a late member response also stays discarded after changing only the selected remote group',{timeout:10000},async t=>{
  const pending=deferred(),entered=deferred();const h=await setup(t,{respond:async(path,method,payload,s)=>{if(path.includes('/members?')){const value=await s.execute(path,method,payload);entered.resolve();await pending.promise;return value;}}});
  h.change(field('group_id'),remoteId);h.click(action('load-members'));await entered.promise;h.change(field('group_id'),h.server.groups[1].id);pending.resolve();await flush();
  assert.equal(h.document.querySelector('[data-gc-member]'),null);assert.equal(h.element(field('group_id')).value,h.server.groups[1].id);assert.equal(writes(h).length,0);
});

test('empty selections and invalid or excessive UTF-16 names never create an import request',async t=>{
  const h=await setup(t);await loadMembers(h);
  for(const name of ['','   ','😀'.repeat(51),'a\u0000b']){h.fill(field('new_group_name'),name);assert.equal(h.element(action('import')).disabled,true);h.click(action('import'));}
  h.fill(field('new_group_name'),'😀'.repeat(50));assert.equal(h.element(action('import')).disabled,false);
  h.change('[data-gc-select-all]',false);assert.equal(h.element(action('import')).disabled,true);assert.equal(writes(h).length,0);
});

test('default list-name truncation keeps a complete Unicode character at the 100-unit boundary',async t=>{
  const server=syntheticServer();server.groups[0].name='😀'.repeat(50);const h=await setup(t,{server});await loadMembers(h);
  const name=h.element(field('new_group_name')).value;assert.ok(name.length<=100);assert.ok(name.endsWith('😀'));assert.equal(/^[\s\S]*[\ud800-\udbff]$/.test(name),false);assert.equal(h.element(action('import')).disabled,false);
});

test('a long opaque preview token remains supported only in memory and in the explicit import payload',async t=>{
  const h=await setup(t,{respond:async(path,method,payload,s)=>{if(path.includes('/members?')){const value=await s.execute(path,method,payload);value.preview_token='A'.repeat(12000);s.previews.set(value.preview_token,copy(value));return value;}}});
  await loadMembers(h);assert.equal(h.element(action('import')).disabled,false);assert.equal(h.document.body.textContent.includes('A'.repeat(100)),false);
  h.click(action('import'));await flush();assert.equal(writes(h)[0].data.preview_token.length,12000);assert.equal(JSON.stringify(saved(h)).includes('A'.repeat(100)),false);
});

test('preview expiry prevents an import while permitting a fresh explicit member load',async t=>{
  const h=await setup(t,{respond:async(path,method,payload,s)=>{if(path.includes('/members?')){const value=await s.execute(path,method,payload);value.expires_at=new Date(Date.now()-1000).toISOString();return value;}}});
  await loadMembers(h);assert.equal(h.element(action('import')).disabled,true);assert.match(h.document.body.textContent,/consulta expirou/);assert.equal(h.element(action('load-members')).disabled,false);assert.equal(writes(h).length,0);
});

test('a lost POST response is recovered with GET and never admits the same import twice',async t=>{
  let lost=true,unavailable=true;
  const h=await setup(t,{respond:async(path,method,payload,s)=>{if(method==='POST'&&lost){lost=false;await s.execute(path,method,payload);throw failure(504);}if(path.includes('/requests/')&&unavailable){unavailable=false;throw failure(503);}}});
  await loadMembers(h);h.click(action('import'));await flush();assert.ok(h.document.querySelector('[data-gc-pending]'));assert.equal(writes(h).length,1);
  assert.equal(h.signals.find(row=>row.method==='POST').signal,undefined);h.click(action('recover'));await flush();
  assert.match(result(h),/Contatos salvos/);assert.equal(writes(h).length,1);assert.equal(h.server.imports.length,1);
});

test('reload recovers only the scoped identity with GET, removing any extra fields from that record',async t=>{
  let lost=true;const server=syntheticServer();
  const first=await setup(t,{server,respond:async(path,method,payload,s)=>{if(method==='POST'){await s.execute(path,method,payload);throw failure(504);}if(path.includes('/requests/')&&lost)throw failure(503);}});
  await loadMembers(first);first.click(action('import'));await flush();const record=copy(saved(first));first.close();lost=false;
  const h=await setup(t,{server,historyState:{orionGroupContactsRecovery:{...record,phones:['private'],preview_token:'discard'}}});
  assert.ok(h.document.querySelector('[data-gc-result]'));assert.ok(h.calls.every(call=>call.method==='GET'&&call.path===base+'/requests/'+record.idempotency_key));assert.equal(server.imports.length,1);
  assert.deepEqual(copy(saved(h)),record);
});

test('authenticated 404 permits only an explicit identical in-memory retry using the original key',async t=>{
  let first=true;const h=await setup(t,{respond:async(path,method)=>{if(method==='POST'&&first){first=false;throw failure(504);}}});
  await loadMembers(h);h.click(action('import'));await flush();const original=copy(writes(h)[0].data);assert.ok(h.document.querySelector(action('retry')));
  h.click(action('retry'));await flush();assert.deepEqual(writes(h)[1].data,original);assert.equal(h.server.imports.length,1);assert.ok(h.document.querySelector('[data-gc-result]'));
});

test('a missing request after reload never invents the old payload, retries it or clears its identity',async t=>{
  const record=recovery(),h=await setup(t,{historyState:{orionGroupContactsRecovery:record}});
  assert.ok(h.document.querySelector('[data-gc-pending]'));assert.equal(h.document.querySelector(action('retry')),null);assert.equal(h.document.querySelector(action('new')),null);
  h.click(action('recover'));await flush();assert.deepEqual(copy(saved(h)),record);assert.ok(h.calls.every(call=>call.method==='GET'));assert.equal(h.server.imports.length,0);
});

test('a proven stale local-list version clears the rejected intent without overwriting or retrying it',async t=>{
  const h=await setup(t);await loadMembers(h);h.change(field('target_group_id'),groupId);h.server.options.groups[0].version++;
  h.click(action('import'));await flush();assert.match(h.element('[data-gc-error]').textContent,/lista de destino mudou/);assert.equal(saved(h),undefined);assert.equal(h.server.imports.length,0);assert.equal(writes(h).length,1);assert.equal(h.calls.some(call=>call.path.includes('/requests/')),false);
});

test('server-side expiry rejects the old preview without automatically importing another selection',async t=>{
  const h=await setup(t);await loadMembers(h);for(const value of h.server.previews.values())value.expires_at=new Date(Date.now()-1000).toISOString();
  h.click(action('import'));await flush();assert.match(h.element('[data-gc-error]').textContent,/expirou/);assert.equal(saved(h),undefined);assert.equal(h.document.querySelector('[data-gc-member]'),null);assert.equal(writes(h).length,1);assert.equal(h.server.imports.length,0);
});

for(const defect of ['origin','counts','group','identity'])test('malformed import '+defect+' keeps recovery locked instead of claiming success',async t=>{
  const h=await setup(t,{respond:async(path,method,payload,s)=>{if(method==='POST'){const operation=await s.execute(path,method,payload);if(defect==='origin')operation.result.origin='campaigns';if(defect==='counts')operation.result.existing_count++;if(defect==='group')operation.result.group.id=uid(999);if(defect==='identity')operation.idempotency_key=uid(998);s.requests.get(payload.idempotency_key).operation=copy(operation);return operation;}}});
  await loadMembers(h);h.click(action('import'));await flush();assert.equal(h.document.querySelector('[data-gc-result]'),null);assert.ok(h.document.querySelector('[data-gc-pending]'));assert.ok(saved(h));assert.equal(h.document.querySelector(action('new')),null);assert.equal(writes(h).length,1);
});

for(const defect of ['duplicate','device','role','count'])test('malformed members '+defect+' never become a savable selection',async t=>{
  const h=await setup(t,{respond:async(path,method,payload,s)=>{if(path.includes('/members?')){const value=await s.execute(path,method,payload);if(defect==='duplicate')value.members[1].phone=value.members[0].phone;if(defect==='device')value.device.id=uid(999);if(defect==='role')value.members[0].role='unexpected';if(defect==='count')value.group.member_count++;return value;}}});
  await loadMembers(h);assert.equal(h.document.querySelector(action('import')),null);assert.ok(h.document.querySelector('[data-gc-error]'));assert.equal(writes(h).length,0);
});

test('remote names remain text and never inject elements or trigger external requests',async t=>{
  const server=syntheticServer();server.groups[0].name='<img src=x onerror=alert(1)>';server.members.get(remoteId)[0].name='<script>private()</script>';
  const h=await setup(t,{server});await loadMembers(h);assert.equal(h.document.querySelector('img,script,iframe'),null);assert.match(h.element('tbody').textContent,/<script>private/);assert.deepEqual(h.external,[]);assert.deepEqual(h.errors,[]);
});

test('permission revocation purges members and pending identities before any subsequent request',async t=>{
  const h=await setup(t);await loadMembers(h);const before=h.calls.length;h.access.allowed=false;h.click(action('refresh'));await flush();
  assert.equal(h.calls.length,before);assert.equal(h.document.querySelector('[data-gc-member]'),null);assert.equal(h.document.body.textContent.includes('5567999999901'),false);assert.equal(saved(h),undefined);assert.equal(h.controller.getState().revoked,true);assert.equal(h.bridge.host.getAttribute('aria-busy'),'false');
});

test('a late import response cannot restore the UI or recovery after the account context was cleared',{timeout:10000},async t=>{
  const pending=deferred(),entered=deferred();const h=await setup(t,{respond:async(path,method,payload,s)=>{if(method==='POST'){const value=await s.execute(path,method,payload);entered.resolve();await pending.promise;return value;}}});
  await loadMembers(h);h.click(action('import'));await entered.promise;assert.ok(saved(h));h.window.OrionSubscriptionGroupContacts.clearContext();pending.resolve();await flush();
  assert.equal(saved(h),undefined);assert.equal(h.document.querySelector('[data-gc-result]'),null);assert.equal(h.document.querySelector('[data-gc-member]'),null);assert.equal(writes(h).length,1);
});

test('read-only and unidentified account contexts cannot save contacts',async t=>{
  for(const options of [{canWrite:false},{identity:null}]){const h=await setup(t,options);await loadMembers(h);assert.equal(h.element(action('import')).disabled,true);h.click(action('import'));assert.equal(writes(h).length,0);if(!options.identity&&options.identity!==undefined)assert.match(h.element('[data-gc-error]').textContent,/identificação da conta/);}
});

test('foreign account or tenant recovery is discarded without querying another private operation',async t=>{
  for(const extra of [{tenant_id:tenantB},{account_scope:'b'.repeat(64)}]){const h=await setup(t,{historyState:{orionGroupContactsRecovery:recovery(extra)}});assert.equal(saved(h),undefined);assert.equal(h.calls.some(call=>call.path.includes('/requests/')),false);assert.equal(writes(h).length,0);}
});

test('acknowledging a successful import starts a fresh read-only extraction and clears the completed identity',async t=>{
  const h=await setup(t);await loadMembers(h);h.click(action('import'));await flush();h.click(action('new'));await flush();
  assert.equal(saved(h),undefined);assert.equal(h.document.querySelector('[data-gc-member]'),null);assert.equal(h.document.querySelector('[data-gc-result]'),null);assert.equal(h.element(field('group_id')).value,'');assert.equal(writes(h).length,1);assert.equal(h.server.imports.length,1);
});

test('Suite exposes extraction only for an active subscription owner with its flag, including no commercial plan',{timeout:10000},async t=>{
  for(const scenario of [{},{profile:'standard'},{allowed:false},{status:'paused'},{status:undefined,missing:true},{role:'viewer'}]){
    const profile=scenario.profile||'subscription_services',grant=scenario.allowed!==false,company={id:tenantA,name:'Empresa sintética',role:scenario.role||'owner',status:scenario.missing?undefined:scenario.status||'active',workspace_profile:profile};
    const permissions=planState('none',{workspace_profile:profile,status:'no_access',features:['account.manage','business.setup',...(grant?['group_contacts.manage']:[])],permissions:{'account.manage':true,'business.setup':true,'group_contacts.manage':grant}});
    const server=syntheticServer(),loaded=deferred();const h=await makeSuite({initialRoute:'extrator-contatos',permissions,bridge:{tenants:[company]},configureWindow:window=>{Object.defineProperty(window.crypto,'subtle',{value:webcrypto.subtle,configurable:true});window.TextEncoder=TextEncoder;window.eval(source);},respond:(path,method,payload)=>{if(path.startsWith(base)){loaded.resolve();return server.execute(path,method,payload);}return defaultResponse(path);}});t.after(()=>h.close());
    const allowed=profile==='subscription_services'&&grant&&company.status==='active'&&company.role==='owner';
    if(allowed){await loaded.promise;await flush();assert.ok(h.document.querySelector('#osSubscriptionGroupContacts [data-gc-form]'));assert.ok(h.document.querySelector('#osNav [data-os-route="extrator-contatos"]'));assert.equal(permissions.active,false);assert.equal(h.document.querySelector('#osNav [data-os-route="inbox"]'),null);}else{await flush();assert.equal(h.document.querySelector('#osNav [data-os-route="extrator-contatos"]'),null);assert.equal(h.calls.some(call=>call.path.startsWith(base)),false);}
    assert.equal(h.calls.some(call=>call.method!=='GET'),false);assert.deepEqual(h.errors,[]);
  }
});

test('real panel bootstrap sanitizes extraction recovery while removing an untrusted tenant URL',async t=>{
  const html=await readFile(new URL('../app/static/panel.html',import.meta.url),'utf8'),prefix=html.match(/async function showApp\([^)]*\)\{[\s\S]*?(?=\n suiteUser=)/)?.[0];assert.ok(prefix);
  const dom=new JSDOM('',{url:'https://orion.test/painel?aba=extrator-contatos&empresa=untrusted',runScripts:'outside-only'});t.after(()=>dom.window.close());dom.window.eval(source);const record=recovery();dom.window.history.replaceState({orionGroupContactsRecovery:{...record,phones:['discard'],preview_token:'discard'},unrelated:'discard'},'');
  dom.window.eval('var authEpoch=1,suiteTenantId="",businessRows=[],businessContextReady=false,businessContextError="";function resetWizard(){};'+prefix+'\n}');await dom.window.showApp('member','Synthetic','synthetic@example.invalid');
  assert.deepEqual(copy(dom.window.history.state),{orionGroupContactsRecovery:record});assert.equal(new URL(dom.window.location).searchParams.has('empresa'),false);assert.equal(new URL(dom.window.location).searchParams.get('aba'),'extrator-contatos');
});

test('real panel adapter forwards GET abort and exposes bounded stale-import proof only on its POST endpoint',async t=>{
  const html=await readFile(new URL('../app/static/panel.html',import.meta.url),'utf8'),actual=html.match(/async function api\(url,method="GET",data,options\)\{[\s\S]*?\n\}/)?.[0];assert.ok(actual);
  const dom=new JSDOM('',{url:'https://orion.test/painel',runScripts:'outside-only'});t.after(()=>dom.window.close());dom.window.eval('var authEpoch=1,csrf="test-csrf",suiteTenantId="test",businessContextError="";function accountBusiness(){return {id:"test"};}function errorMessage(){return "test error";}'+actual);
  const signal=new dom.window.AbortController().signal;dom.window.fetch=async(path,options)=>{assert.equal(options.signal,signal);return {ok:true,status:200,json:async()=>({items:[]})};};await dom.window.api('/api/v1/tenants/test/subscription-whatsapp/group-contacts/options','GET',undefined,{signal});
  const proof={reason:'stale_group_version',idempotency_key:'operation',group_id:'group',expected_version:2};dom.window.fetch=async(path,options)=>{assert.equal(options.signal,undefined);assert.equal(options.headers['X-Orion-CSRF'],'test-csrf');return {ok:false,status:409,json:async()=>({detail:{code:'GROUP_CONTACTS_CONFLICT',rejection:{...proof,private:'discard'}}})};};
  for(const path of ['/api/v1/tenants/test/subscription-whatsapp/group-contacts/import','/api/v1/tenants/test/subscription-whatsapp/campaigns/groups']){await assert.rejects(dom.window.api(path,'POST',{}),error=>{assert.deepEqual(error.groupContactsRejection?copy(error.groupContactsRejection):undefined,path.endsWith('/group-contacts/import')?proof:undefined);return true;});}
});
