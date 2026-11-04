import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {makeSuite,planState,defaultResponse} from './harness.mjs';
import {makeDiagnostics,source,contract,base,device,accountKey,copy,report,listing,operation,recovery,failure,flush,deferred,tenantA,tenantB,selectedURL} from './subscription-diagnostics-harness.mjs';
const action=name=>'[data-dg-action="'+name+'"]';
const field=name=>'[data-dg-field="'+name+'"]';
const writes=h=>h.calls.filter(call=>call.method!=='GET');
const confirms=h=>writes(h).filter(call=>call.path.endsWith('/confirm'));
const permissions=planState('none',{workspace_profile:'subscription_services',features:['account.manage','business.setup','devices.manage','chatbot.manage','templates.manage','sent_messages.manage','diagnostics.manage'],permissions:{'account.manage':true,'business.setup':true,'devices.manage':true,'chatbot.manage':true,'templates.manage':true,'sent_messages.manage':true,'diagnostics.manage':true}});
const tenant={id:tenantA,name:'Empresa sintética',role:'owner',timezone:'America/Cuiaba',workspace_profile:'subscription_services'};
async function prepare(h,phone=''){h.fill(field('phone'),phone);h.submit();await flush();assert.equal(h.element('[data-dg-dialog]').dataset.dgView,'confirm');}
function crypto(window){Object.defineProperty(window.crypto,'subtle',{value:webcrypto.subtle});window.TextEncoder=TextEncoder;window.eval(source);}

test('opening diagnostics requires a deliberate device selection and performs only the SQL list read',async t=>{
  const h=await makeDiagnostics({url:'https://orion.test/panel?aba=diagnosticos'});t.after(()=>h.close());
  assert.deepEqual(h.calls.map(c=>[c.method,c.path]),[['GET',base]]);assert.match(h.element('[data-dg-empty]').textContent,/Selecione um dispositivo/);assert.equal(h.document.querySelector('[data-dg-section]'),null);assert.equal(h.element(field('device')).value,'');assert.deepEqual(h.errors,[]);
});

test('selection and repeated Atualizar never prepare, confirm, probe or contact other APIs',async t=>{
  const h=await makeDiagnostics({url:'https://orion.test/panel?aba=diagnosticos'});t.after(()=>h.close());h.change(field('device'),device);await flush();
  for(let n=0;n<3;n++){h.click(action('refresh'));await flush();}
  assert.equal(h.document.querySelectorAll('[data-dg-row]').length,3);assert.equal(writes(h).length,0);assert.ok(h.calls.every(c=>c.path===base||c.path===base+'/'+device));assert.equal(h.external.length,0);assert.deepEqual(h.errors,[]);
});

test('empty device list and empty recent messages have distinct readable states',async t=>{
  const empty=await makeDiagnostics({devices:listing({devices:[]}),url:'https://orion.test/panel?aba=diagnosticos'});t.after(()=>empty.close());assert.match(empty.element('[data-dg-empty]').textContent,/Nenhum dispositivo/);
  const h=await makeDiagnostics({data:report({recent_messages:{limit:3,items:[]}})});t.after(()=>h.close());assert.match(h.element('[data-dg-empty-messages]').textContent,/Nenhuma mensagem/);assert.equal(h.document.querySelector('[data-dg-error]'),null);
});

test('the report retains the observed areas, escapes all history and provides no resend or unrelated metrics',async t=>{
  const h=await makeDiagnostics();t.after(()=>h.close());assert.deepEqual([...h.document.querySelectorAll('[data-dg-section]')].map(n=>n.dataset.dgSection),['connection','server','recent','proxy','message']);
  assert.match(h.element('[data-dg-section=recent]').textContent,/<script>alert\(1\)<\/script>/);assert.equal(h.document.querySelectorAll('img,script,iframe').length,0);assert.equal(h.document.querySelector('[data-dg-action=resend]'),null);assert.doesNotMatch(h.document.body.textContent,/Webhook|ASR|Reservado|Fila de/);assert.match(h.element('[data-dg-section=recent]').textContent,/Recebimento não confirmado/);
});

test('server availability never turns a stale stored WhatsApp state into a current connection',async t=>{
  const data=report();data.connection={...data.connection,freshness:'stale',source:'stored'};const h=await makeDiagnostics({data});t.after(()=>h.close());
  assert.match(h.element('[data-dg-section=server]').textContent,/Disponível/);assert.match(h.element('[data-dg-section=connection]').textContent,/Último estado: Conectado/);assert.match(h.element('[data-dg-section=connection]').textContent,/sem confirmação atual/);assert.equal(h.document.querySelector('[data-dg-section=connection] .os-dg-badge-success'),null);
});

test('unknown measurements stay absent and a genuine measured zero remains zero',async t=>{
  for(const [value,state,label] of [[null,'unknown','Não medida'],[0,'available','0 ms']]){
    const data=report();data.server={...data.server,state,latency_ms:value};const h=await makeDiagnostics({data});t.after(()=>h.close());assert.match(h.element('[data-dg-section=server]').textContent,new RegExp(label));
  }
});

test('malformed reports fail closed instead of fabricating a diagnosis or exposing provider errors',async t=>{
  for(const mutate of [d=>d.server.latency_ms=-1,d=>d.server.latency_ms='NaN',d=>d.server.state='online',d=>d.recent_messages.items.push(copy(d.recent_messages.items[0])),d=>d.test_message.requires_confirmation=false,d=>d.proxy.port=0]){
    const data=report();mutate(data);const h=await makeDiagnostics({data});t.after(()=>h.close());assert.match(h.element('[data-dg-error]').textContent,/Não foi possível/);assert.equal(h.document.querySelector(action('prepare')),null);assert.equal(writes(h).length,0);
  }
});

test('a failed read supports explicit retry without executing any test',async t=>{
  let fail=true;const h=await makeDiagnostics({respond(path){if(fail&&path===base+'/'+device)throw failure(503,'secret password');}});t.after(()=>h.close());assert.doesNotMatch(h.document.body.textContent,/secret password/);fail=false;h.click(action('refresh'));await flush();assert.equal(h.document.querySelector('[data-dg-error]'),null);assert.ok(h.element('[data-dg-section=connection]'));assert.equal(writes(h).length,0);
});

test('late device responses cannot repopulate a different selection',async t=>{
  const wait=deferred();const h=await makeDiagnostics({wait:false,respond(path){if(path===base+'/'+device)return wait.promise;}});t.after(()=>h.close());h.change(field('device'),'');await flush();wait.resolve(report());await h.controller.ready;await flush();assert.equal(h.element(field('device')).value,'');assert.equal(h.document.querySelector('[data-dg-section]'),null);assert.equal(writes(h).length,0);
});

test('prepare resolves self only on the server and cancellation admits no message',async t=>{
  const h=await makeDiagnostics();t.after(()=>h.close());await prepare(h);assert.equal(writes(h).length,1);assert.equal(writes(h)[0].data.phone,null);assert.match(h.element('[data-dg-preview-recipient]').textContent,/\+5567999999902/);assert.equal(confirms(h).length,0);
  h.click(action('cancel'));await flush();assert.equal(h.document.querySelector('[data-dg-dialog]'),null);assert.equal(h.window.history.state.orionDiagnosticRecovery,undefined);assert.equal(confirms(h).length,0);assert.equal(h.element(action('prepare')).disabled,false);assert.equal(h.element(field('device')).disabled,false);
});

test('confirmation freezes the server preview and sends only confirmation identity once',async t=>{
  const wait=deferred();const h=await makeDiagnostics({respond(path,method,data,server){if(path.endsWith('/confirm')){const value=operation('confirm_response',{idempotency_key:data.idempotency_key,preview:copy(server.requests.get(data.idempotency_key).preview)});return wait.promise.then(()=>value);}}});t.after(()=>h.close());await prepare(h,'+5567999999917');
  h.element(field('phone')).value='+5567999999928';h.click(action('confirm'));h.click(action('confirm'));await flush();assert.equal(confirms(h).length,1);assert.deepEqual(Object.keys(confirms(h)[0].data).sort(),['confirmation','confirmation_version','idempotency_key']);assert.equal(confirms(h)[0].data.idempotency_key,writes(h)[0].data.idempotency_key);assert.equal(confirms(h)[0].data.confirmation,'ENVIAR');
  h.element('[data-dg-dialog]').dispatchEvent(new h.window.Event('cancel',{cancelable:true}));assert.ok(h.document.querySelector('[data-dg-dialog]'));wait.resolve();await flush();assert.equal(h.element('[data-dg-dialog]').dataset.dgView,'pending');assert.match(h.element('[data-dg-dialog]').textContent,/aguarda o resultado/);assert.match(h.element('[data-dg-preview-recipient]').textContent,/\+5567999999917/);assert.equal(confirms(h).length,1);
});

test('double submission while preparing yields one durable intent',async t=>{
  const wait=deferred();let payload;const h=await makeDiagnostics({respond(path,method,data){if(path.endsWith('/prepare')){payload=data;return wait.promise;}}});t.after(()=>h.close());h.submit();h.submit();await flush();assert.equal(writes(h).length,1);wait.resolve(operation('prepare_response',{idempotency_key:payload.idempotency_key}));await flush();assert.equal(h.element('[data-dg-dialog]').dataset.dgView,'confirm');assert.equal(confirms(h).length,0);
});

test('a lost prepare response recovers the preview by GET and never auto-confirms',async t=>{
  let key;const h=await makeDiagnostics({respond(path,method,data){if(path.endsWith('/prepare')){key=data.idempotency_key;throw failure(503);}if(path.endsWith('/requests/'+key))return operation('prepare_response',{idempotency_key:key});}});t.after(()=>h.close());h.submit();await flush();assert.equal(h.element('[data-dg-dialog]').dataset.dgView,'pending');assert.match(h.element('[data-dg-dialog]').textContent,/nenhum novo teste/);h.click('[data-dg-dialog] '+action('recover'));await flush();assert.equal(h.element('[data-dg-dialog]').dataset.dgView,'confirm');assert.equal(writes(h).length,1);assert.equal(confirms(h).length,0);
});

test('a lost confirm response recovers the existing operation without another POST',async t=>{
  let key;const h=await makeDiagnostics({respond(path,method,data){if(path.endsWith('/confirm')){key=data.idempotency_key;throw failure(503);}if(path.endsWith('/requests/'+key))return operation('confirm_response',{idempotency_key:key});}});t.after(()=>h.close());await prepare(h);h.click(action('confirm'));await flush();h.click('[data-dg-dialog] '+action('recover'));await flush();assert.equal(confirms(h).length,1);assert.equal(h.element('[data-dg-dialog]').dataset.dgView,'pending');assert.ok(h.window.history.state.orionDiagnosticRecovery);assert.equal(h.document.querySelector(action('confirm')),null);
});

test('recovery history contains only bounded identity and never phone, preview, message or raw error',async t=>{
  const h=await makeDiagnostics({respond(path){if(path.endsWith('/prepare'))throw failure(503,'private backend payload');}});t.after(()=>h.close());h.fill(field('phone'),'+5567999999917');h.submit();await flush();const saved=copy(h.window.history.state.orionDiagnosticRecovery);
  assert.deepEqual(Object.keys(saved).sort(),['account_scope','device_id','expected_version','idempotency_key','kind','tenant_id']);assert.doesNotMatch(JSON.stringify(h.window.history.state),/5567999999917|Mensagem de teste|backend payload/);assert.equal(h.window.sessionStorage.length,0);assert.equal(h.window.localStorage.length,0);assert.doesNotMatch(h.window.location.href,/phone|5567999999917/);
});

test('reload recovers only the same account and tenant using GET without opening or sending a preview',async t=>{
  for(const override of [{},{tenant_id:tenantB},{account_scope:'b'.repeat(64)}]){
    const saved=recovery(override);const h=await makeDiagnostics({historyState:{orionDiagnosticRecovery:saved},respond(path){if(path.includes('/requests/'))return operation();}});t.after(()=>h.close());assert.equal(h.calls.filter(c=>c.path.includes('/requests/')).length,Object.keys(override).length?0:1);assert.equal(writes(h).length,0);assert.equal(h.document.querySelector('[data-dg-dialog]'),null);if(Object.keys(override).length)assert.equal(h.window.history.state.orionDiagnosticRecovery,undefined);else assert.match(h.element('[data-dg-operation]').textContent,/mensagem está preparada/);
  }
});

test('a missing prepare permits only explicit phone re-entry with the exact saved key and version',async t=>{
  let attempts=0;const h=await makeDiagnostics({respond(path,method,data){if(path.endsWith('/prepare')&&++attempts===1)throw failure(503);if(path.includes('/requests/'))throw failure(404);}});t.after(()=>h.close());h.fill(field('phone'),'+5567999999917');h.submit();await flush();const original=copy(writes(h)[0].data);h.click('[data-dg-dialog] '+action('recover'));await flush();assert.equal(writes(h).length,1);h.click(action('repeat-prepare'));await flush();assert.equal(h.element(field('phone')).disabled,false);h.fill(field('phone'),'+5567999999917');h.submit();await flush();assert.equal(writes(h).length,2);assert.deepEqual(writes(h)[1].data,original);assert.equal(h.element('[data-dg-dialog]').dataset.dgView,'confirm');assert.equal(confirms(h).length,0);
});

test('the configured proxy test accepts only its key and revision and remains independent of WhatsApp',async t=>{
  const data=report();data.connection={...data.connection,state:'disconnected',label:'Desconectado'};const h=await makeDiagnostics({data});t.after(()=>h.close());h.click(action('proxy-test'));await flush();assert.equal(writes(h).length,1);assert.deepEqual(Object.keys(writes(h)[0].data).sort(),['expected_version','idempotency_key']);assert.match(h.element('[data-dg-proxy-result]').textContent,/Conexão verificada/);assert.match(h.element('[data-dg-section=connection]').textContent,/Desconectado/);assert.equal(confirms(h).length,0);assert.equal(h.document.querySelector('[data-dg-dialog]'),null);
});

test('direct mode does not fabricate a proxy test and old configuration results remain visibly old',async t=>{
  const data=report();data.proxy={...data.proxy,mode:'direct',host:null,port:null,can_test:false,last_test:{...copy(contract.proxy_response.proxy),configuration_version:'f'.repeat(64)}};const h=await makeDiagnostics({data});t.after(()=>h.close());assert.equal(h.element(action('proxy-test')).disabled,true);assert.match(h.element('[data-dg-section=proxy]').textContent,/Conexão direta/);assert.match(h.element('[data-dg-proxy-result]').textContent,/configuração anterior/);assert.equal(writes(h).length,0);
});

test('lost proxy response is recovered by GET; a missing receipt allows only an explicit same-key probe',async t=>{
  let attempts=0;const h=await makeDiagnostics({respond(path){if(path.endsWith('/proxy-tests')&&++attempts===1)throw failure(503);if(path.includes('/requests/'))throw failure(404);}});t.after(()=>h.close());h.click(action('proxy-test'));await flush();const original=copy(writes(h)[0].data);h.click(action('recover'));await flush();assert.equal(writes(h).length,1);assert.equal(h.element('[data-dg-dialog]').dataset.dgView,'missing');h.click(action('repeat-proxy'));await flush();assert.equal(writes(h).length,2);assert.deepEqual(writes(h)[1].data,original);assert.match(h.element('[data-dg-proxy-result]').textContent,/Conexão verificada/);
});

test('uncertain and expired operations never become success or restart on their own',async t=>{
  for(const status of ['uncertain','expired']){
    const saved=recovery(),result=operation('confirm_response',{status,message:status==='uncertain'?{...copy(contract.confirm_response.message),status:'uncertain',outcome:'uncertain'}:null});
    const h=await makeDiagnostics({historyState:{orionDiagnosticRecovery:saved},respond(path){if(path.includes('/requests/'))return result;}});t.after(()=>h.close());assert.equal(writes(h).length,0);assert.equal(h.element(action('prepare')).disabled,true);h.click(action('recover'));await flush();assert.equal(h.element('[data-dg-dialog]').dataset.dgView,'result');assert.equal(confirms(h).length,0);h.click('[data-dg-dialog] '+action('acknowledge'));assert.equal(h.window.history.state.orionDiagnosticRecovery,undefined);assert.equal(h.element(action('prepare')).disabled,false);assert.equal(writes(h).length,0);
  }
});

test('mismatched operation identity is treated as unresolved without rendering its private preview',async t=>{
  const h=await makeDiagnostics({respond(path,method,data){if(path.endsWith('/prepare'))return operation('prepare_response',{idempotency_key:data.idempotency_key,device_id:'40000000-0000-4000-8000-000000000002'});}});t.after(()=>h.close());h.submit();await flush();assert.equal(h.element('[data-dg-dialog]').dataset.dgView,'pending');assert.equal(h.document.querySelector('[data-dg-preview-recipient]'),null);assert.ok(h.window.history.state.orionDiagnosticRecovery);assert.equal(confirms(h).length,0);
});

test('permission revocation during recovery clears all displayed private data and local recovery',async t=>{
  const h=await makeDiagnostics({respond(path){if(path.includes('/requests/'))throw failure(403);}});t.after(()=>h.close());await prepare(h);h.click(action('confirm'));await flush();h.click('[data-dg-dialog] '+action('close'));h.click('[data-dg-operation] '+action('recover'));await flush();assert.equal(h.document.querySelector('[data-dg-dialog]'),null);assert.equal(h.window.history.state.orionDiagnosticRecovery,undefined);assert.equal(h.document.querySelector('[data-dg-section]'),null);assert.doesNotMatch(h.document.body.textContent,/55679999999|Sua assinatura está ativa/);assert.match(h.element('[data-dg-error]').textContent,/não está mais disponível/);assert.equal(confirms(h).length,1);
});

test('read-only access or missing recoverable account identity disables both actions',async t=>{
  for(const options of [{canWrite:false},{identity:null},{cryptoEnabled:false}]){const h=await makeDiagnostics(options);t.after(()=>h.close());assert.equal(h.element(action('prepare')).disabled,true);assert.equal(h.element(action('proxy-test')).disabled,true);h.submit();await flush();assert.equal(writes(h).length,0);}
});

test('unmount invalidates delayed responses and removes the confirmation dialog',async t=>{
  const wait=deferred();let key;const h=await makeDiagnostics({respond(path,method,data){if(path.endsWith('/prepare')){key=data.idempotency_key;return wait.promise;}}});t.after(()=>h.close());h.submit();await flush();h.controller.unmount();wait.resolve(operation('prepare_response',{idempotency_key:key}));await flush();assert.equal(h.document.querySelector('[data-dg-dialog]'),null);assert.equal(h.element('#diagnostics').textContent,'');assert.equal(confirms(h).length,0);
});

test('diagnostics is visible only in the subscription workspace with its own permission',{timeout:10000},async t=>{
  for(const [profile,allowed] of [['subscription_services',true],['standard',true],['subscription_services',false]]){
    const p=copy(permissions),loaded=deferred();p.workspace_profile=profile;p.permissions['diagnostics.manage']=allowed;
    const h=await makeSuite({initialRoute:'diagnosticos',permissions:p,bridge:{tenants:[{...tenant,workspace_profile:profile}]},configureWindow:crypto,respond:path=>{if(path===base){loaded.resolve();return listing();}return path===base+'/'+device?report():defaultResponse(path);}});t.after(()=>h.close());if(profile==='subscription_services'&&allowed)await loaded.promise;await flush();
    assert.equal(!!h.document.querySelector('#osNav [data-os-route=diagnosticos]'),profile==='subscription_services'&&allowed);assert.equal(h.calls.some(c=>c.path===base),profile==='subscription_services'&&allowed);assert.equal(writes(h).length,0);assert.deepEqual(h.errors,[]);
  }
});

test('bootstrap sanitizes recovery, waits for the business and checks account binding before GET',async t=>{
  for(const override of [{},{tenant_id:tenantB},{account_scope:'b'.repeat(64)}]){
    const saved=recovery(override),loaded=deferred();const h=await makeSuite({initialRoute:'diagnosticos',url:'https://orion.test/panel?aba=diagnosticos',permissions,bridge:{tenants:[],tenantId:'',businessContextReady:false},configureWindow:window=>{crypto(window);window.history.replaceState({unrelated:'Discard',orionDiagnosticRecovery:{...saved,phone:'+5567999999917',text:'private'}},'',window.location.href);},respond:path=>{if(path===base){loaded.resolve();return listing();}if(path===base+'/'+device)return report();if(path.includes('/requests/'))return operation();return defaultResponse(path);}});t.after(()=>h.close());
    assert.deepEqual(copy(h.window.history.state),{orionDiagnosticRecovery:saved});assert.equal(h.calls.length,0);h.window.OrionSuite.sync({tenants:[tenant],tenantId:tenantA,businessContextReady:true});await loaded.promise;await flush();assert.equal(h.calls.filter(c=>c.path.includes('/requests/')).length,Object.keys(override).length?0:1);assert.equal(writes(h).length,0);assert.equal(h.document.querySelector('[data-dg-dialog]'),null);assert.deepEqual(h.errors,[]);
  }
});

test('leaving diagnostics removes only its selected device URL and keeps same-account recovery available',async t=>{
  const saved=recovery();const h=await makeSuite({initialRoute:'diagnosticos',url:selectedURL,permissions,bridge:{tenants:[tenant]},configureWindow:window=>{crypto(window);window.history.replaceState({orionDiagnosticRecovery:saved},'',window.location.href);},respond:path=>path===base?listing():path===base+'/'+device?report():path.includes('/requests/')?operation():defaultResponse(path)});t.after(()=>h.close());await flush();await h.window.OrionSuite.open('novidades');await flush();assert.equal(new URL(h.window.location.href).searchParams.has('device_id'),false);assert.deepEqual(copy(h.window.history.state.orionDiagnosticRecovery),saved);assert.equal(h.document.querySelector('[data-dg-section]'),null);assert.equal(writes(h).length,0);
});

test('account, business and niche changes clear private diagnostics and recovery before the next context',async t=>{
  for(const next of [{user:{role:'member',email:'another@example.invalid'},tenants:[{...tenant,id:tenantB}],tenantId:tenantB},{tenants:[{...tenant,workspace_profile:'standard'}]}]){
    const saved=recovery();const h=await makeSuite({initialRoute:'diagnosticos',url:selectedURL,permissions,bridge:{tenants:[tenant]},configureWindow:window=>{crypto(window);window.history.replaceState({orionDiagnosticRecovery:saved},'',window.location.href);},respond:path=>path===base?listing():path===base+'/'+device?report():path.includes('/requests/')?operation():defaultResponse(path)});t.after(()=>h.close());await flush();
    assert.ok(h.document.querySelector('[data-dg-row]'));h.window.OrionSuite.sync(next);await flush();assert.equal(h.window.history.state.orionDiagnosticRecovery,undefined);assert.equal(new URL(h.window.location.href).searchParams.has('device_id'),false);assert.equal(h.document.querySelector('[data-dg-row]'),null);assert.equal(h.document.querySelector('[data-dg-dialog]'),null);assert.doesNotMatch(h.document.querySelector('#page-suite').textContent,/5567999999902|Sua assinatura está ativa/);assert.equal(writes(h).length,0);
  }
});

test('destroying the authenticated shell clears diagnostics recovery and any preview',async t=>{
  const saved=recovery();const h=await makeSuite({initialRoute:'diagnosticos',url:selectedURL,permissions,bridge:{tenants:[tenant]},configureWindow:window=>{crypto(window);window.history.replaceState({orionDiagnosticRecovery:saved},'',window.location.href);},respond:path=>path===base?listing():path===base+'/'+device?report():path.includes('/requests/')?operation():defaultResponse(path)});t.after(()=>h.close());await flush();h.window.OrionSuite.destroy();assert.equal(h.window.history.state.orionDiagnosticRecovery,undefined);assert.equal(h.document.querySelector('[data-dg-section]'),null);assert.equal(new URL(h.window.location.href).searchParams.has('device_id'),false);assert.equal(writes(h).length,0);
});
