import test from 'node:test';
import assert from 'node:assert/strict';
import {makeChannels,item,listing,runtime,tenantA,tenantB,channelA,channelB,png,flush,deferred} from './channels-harness.mjs';
const base='/api/v1/tenants/'+tenantA+'/qr-channels';
const writes=h=>h.calls.filter(call=>call.method!=='GET');
const state=(row,status='stopped',extra={})=>({...runtime(row,status),...extra});
const openConfirm=h=>{h.click('[data-qc-action=connect]');h.submit();};

function connectServer({missing=false,loseProvision=false}={}){
  const row=item({assistant_activation_allowed:false,allowed_senders:[]});let provisioned=!missing,provisions=0;
  return {row,respond(path,method,data,server){
    if(path.endsWith('/provision')&&method==='POST'){provisions++;provisioned=true;server.data=listing([row]);if(loseProvision&&provisions===1)throw Error('lost response');return server.data;}
    if(method==='PATCH'){Object.assign(row,{enabled:data.enabled??row.enabled,assistant_enabled:data.assistant_enabled??row.assistant_enabled,revision:row.revision+1});server.data=listing([row]);return row;}
    if(path.endsWith('/start')&&method==='POST')return state(row,'connecting',{capabilities:{pair:true}});
    if(path.endsWith('/status'))return state(row,row.enabled?'stopped':'disabled');
  }};
}

test('opening the single canonical connection is read only and never exposes a device list or private policy',async t=>{
  const row=item({label:'Nome técnico privado',allowed_senders:['5567999999901']}),h=await makeChannels({data:listing([row])});t.after(()=>h.close());
  assert.ok(h.element('[data-qc-single]'));assert.equal(h.element('[data-qc-single]').dataset.qcCanonicalState,'ready');assert.deepEqual(writes(h),[]);
  for(const selector of ['[data-qc-channel]','[data-qc-action=new]','[data-qc-form=create]','[name=label]','[name=allowed_senders]','.qc-list','.qc-count'])assert.equal(h.document.querySelector(selector),null,selector);
  assert.doesNotMatch(h.element('#channels').textContent,/Nome técnico privado|5567999999901|Seus dispositivos|principal|teste/i);assert.equal(JSON.stringify(h.controller.getState()).includes('allowed_senders'),false);
});

test('a company without a connection only provisions after explicit confirmation, then enables and starts its one canonical record',async t=>{
  const server=connectServer({missing:true}),h=await makeChannels({data:listing([]),respond:server.respond});t.after(()=>h.close());
  assert.ok(h.element('[data-qc-empty]'));assert.deepEqual(writes(h),[]);h.click('[data-qc-action=connect]');h.click('[data-qc-action=cancel-confirm]');assert.deepEqual(writes(h),[]);
  h.click('[data-qc-action=connect]');const form=h.element('[data-qc-form=confirm]');form.dispatchEvent(new h.window.Event('submit',{bubbles:true,cancelable:true}));form.dispatchEvent(new h.window.Event('submit',{bubbles:true,cancelable:true}));await flush();const calls=writes(h);assert.equal(calls.length,3);assert.equal(calls[0].path,base+'/provision');assert.deepEqual(Object.keys(calls[0].data),['idempotency_key']);assert.match(calls[0].data.idempotency_key,/^[a-f0-9-]{36}$/);
  assert.deepEqual(calls[1],{path:base+'/'+channelA,method:'PATCH',data:{expected_revision:1,enabled:true}});assert.deepEqual(calls[2],{path:base+'/'+channelA+'/start',method:'POST',data:{expected_revision:2}});
  assert.equal(h.controller.getState().connection.assistant_enabled,false);assert.equal(h.controller.getState().connection.id,channelA);assert.equal(h.controller.getState().runtime.status,'connecting');assert.equal(h.document.querySelector('[data-qc-empty]'),null);assert.ok(h.element('[data-qc-assistant-reason]'));
  assert.equal(calls.some(call=>'allowed_senders' in call.data||'phone' in call.data||'assistant_enabled' in call.data),false);
});

test('a disabled canonical connection is reused without registering another one',async t=>{
  const server=connectServer(),h=await makeChannels({data:listing([server.row]),respond:server.respond});t.after(()=>h.close());openConfirm(h);await flush();assert.equal(writes(h).length,2);assert.equal(writes(h).some(call=>call.path.endsWith('/provision')),false);assert.equal(h.controller.getState().connection.id,channelA);
});

test('an enabled stopped connection starts independently with its current revision',async t=>{
  const row=item({enabled:true}),h=await makeChannels({data:listing([row]),respond:(path,method)=>method==='POST'?state(row,'connecting'):undefined});t.after(()=>h.close());openConfirm(h);await flush();assert.deepEqual(writes(h),[{path:base+'/'+channelA+'/start',method:'POST',data:{expected_revision:1}}]);
});

test('an uncertain provisioning response never repeats automatically and an authoritative lookup can resume the same connection',async t=>{
  const server=connectServer({missing:true,loseProvision:true}),h=await makeChannels({data:listing([]),respond:server.respond});t.after(()=>h.close());openConfirm(h);await flush();assert.equal(h.controller.getState().uncertain,true);await h.tick(20000);assert.equal(writes(h).length,1);
  h.click('[data-qc-action=refresh]');await flush();assert.equal(h.controller.getState().connection.id,channelA);assert.equal(h.controller.getState().uncertain,false);assert.equal(writes(h).length,1);openConfirm(h);await flush();assert.equal(writes(h).filter(call=>call.path.endsWith('/provision')).length,1);assert.equal(writes(h).length,3);
});

test('an explicit provisioning retry reuses the exact idempotent intent instead of adding another connection',async t=>{
  let attempts=0;const row=item({assistant_activation_allowed:false,allowed_senders:[]});
  const h=await makeChannels({data:listing([]),respond:(path,method,data,server)=>{if(path.endsWith('/provision')){if(++attempts===1)throw Error('unknown');server.data=listing([row]);return server.data;}if(method==='PATCH')return item({enabled:true,revision:2,assistant_activation_allowed:false});if(path.endsWith('/start'))return state(item({enabled:true,revision:2}),'connecting');}});t.after(()=>h.close());
  openConfirm(h);await flush();h.click('[data-qc-action=retry-provision]');await flush();const requests=writes(h).filter(call=>call.path.endsWith('/provision'));assert.equal(requests.length,2);assert.deepEqual(requests[0],requests[1]);assert.equal(h.controller.getState().connection.id,channelA);
});

test('uncertain enable or start outcomes cannot automatically repeat commands',async t=>{
  for(const failure of ['enable','start']){const row=item(),h=await makeChannels({data:listing([row]),respond:(path,method)=>{if(method==='PATCH'){if(failure==='enable')throw Error('unknown');return item({enabled:true,revision:2});}if(method==='POST')throw Error('unknown');}});openConfirm(h);await flush();assert.equal(h.controller.getState().uncertain,true);const count=writes(h).length;await h.tick(30000);assert.equal(writes(h).length,count);h.close();}
});

test('a delayed provision result cannot enable or start a connection after the company changes',async t=>{
  const late=deferred(),h=await makeChannels({data:listing([]),respond:(path,method)=>path.endsWith('/provision')?late.promise:path.startsWith('/api/v1/tenants/'+tenantB)?listing([]):undefined});t.after(()=>h.close());openConfirm(h);await flush();await h.controller.setTenant(tenantB);late.resolve(listing([item()]));await flush();assert.equal(writes(h).length,1);assert.equal(h.controller.getState().tenantId,tenantB);assert.equal(h.controller.getState().connection,null);
});

test('hiding the screen while enable is pending cannot start its session afterward',async t=>{
  const late=deferred(),h=await makeChannels({respond:(path,method)=>method==='PATCH'?late.promise:undefined});t.after(()=>h.close());openConfirm(h);await flush();await h.visibility(true);late.resolve(item({enabled:true,revision:2}));await flush();assert.equal(writes(h).length,1);assert.equal(writes(h).some(call=>call.path.endsWith('/start')),false);
});

test('ambiguous or foreign canonical data fails closed without choosing a historical record',async t=>{
  const a=item({label:'Principal privado'}),b=item({id:channelB,label:'Teste privado'});
  for(const data of [listing([a,b]),listing([a],{canonical_state:'ambiguous',connection:null,items:[],canonical_id:null}),listing([item({tenant_id:tenantB})])]){
    const h=await makeChannels({data});assert.equal(h.controller.getState().connection,null);assert.equal(h.document.querySelector('[data-qc-action=connect]'),null);assert.equal(h.document.querySelector('[data-qc-number]'),null);assert.doesNotMatch(h.element('#channels').textContent,/Principal privado|Teste privado/);assert.deepEqual(writes(h),[]);h.close();
  }
});

test('a noncanonical compatibility list is rejected rather than selecting its first entry',async t=>{
  const a=item(),b=item({id:channelB}),h=await makeChannels({data:listing([a],{items:[a,b]})});t.after(()=>h.close());assert.equal(h.controller.getState().connection,null);assert.equal(h.controller.getState().unavailable,true);assert.deepEqual(writes(h),[]);
});

test('only a provider-verified connected phone is displayed, once',async t=>{
  const row=item({enabled:true});
  for(const [status,phone,verified,expected] of [['connected','5511987654321',true,1],['connected','5511987654321',false,0],['pairing','5511987654321',true,0],['connected','abc@lid',true,0]]){
    const h=await makeChannels({data:listing([row]),respond:path=>path.endsWith('/status')?state(row,status,{phone,phone_verified:verified}):undefined});assert.equal(h.document.querySelectorAll('[data-qc-number]').length,expected);if(expected)assert.match(h.element('[data-qc-number]').textContent,/98765-4321/);assert.deepEqual(writes(h),[]);h.close();
  }
});

test('a validated connected legacy binding shows the same one number without provisioning or changing session controls',async t=>{
  const row=item({enabled:true,legacy_transport:true,assistant_enabled:true}),h=await makeChannels({data:listing([row]),respond:path=>path.endsWith('/status')?state(row,'connected',{phone:'5511987654321',phone_verified:true}):undefined});t.after(()=>h.close());assert.ok(h.element('[data-qc-number]'));for(const selector of ['[data-qc-action=connect]','[data-qc-action=stop]','[data-qc-connection]','[data-qc-form=pair]'])assert.equal(h.document.querySelector(selector),null);assert.deepEqual(writes(h),[]);
});

test('legacy pairing or QR controls require explicit server capability, never the legacy flag alone',async t=>{
  const row=item({enabled:true,legacy_transport:true});
  const denied=await makeChannels({data:listing([row]),respond:path=>path.endsWith('/status')?state(row,'qr_ready',{capabilities:{pair:true}}):undefined});assert.equal(denied.document.querySelector('[data-qc-connection]'),null);denied.close();
  const h=await makeChannels({data:listing([row],{capabilities:{start:true,qr:true,pair:true,stop:false,change_state:false,change_assistant:true}}),respond:path=>path.endsWith('/status')?state(row,'qr_ready',{capabilities:{pair:true}}):undefined});t.after(()=>h.close());assert.ok(h.element('[data-qc-method=code]'));assert.equal(h.document.querySelector('[data-qc-action=connect]'),null);assert.deepEqual(writes(h),[]);
});

test('a viewer cannot provision or mutate the connection and never receives private policy in the UI',async t=>{
  const row=item({can_manage:false}),h=await makeChannels({data:listing([row],{access:{can_manage:false}})});t.after(()=>h.close());for(const action of ['connect','assistant_on','assistant_off','stop'])assert.equal(h.document.querySelector('[data-qc-action='+action+']'),null);assert.doesNotMatch(h.element('#channels').textContent,/99999-9901/);assert.deepEqual(writes(h),[]);
});

test('a registry refresh that revokes management access removes the verified number without another provider read',async t=>{
  const row=item({enabled:true}),h=await makeChannels({data:listing([row]),respond:path=>path.endsWith('/status')?state(row,'connected',{phone:'5511987654321',phone_verified:true}):undefined});t.after(()=>h.close());assert.ok(h.element('[data-qc-number]'));const before=h.calls.filter(call=>call.path.endsWith('/status')).length;h.server.data=listing([item({enabled:true,can_manage:false})],{access:{can_manage:false},capabilities:{}});h.click('[data-qc-action=refresh]');await flush();assert.equal(h.document.querySelector('[data-qc-number]'),null);assert.equal(h.controller.getState().runtime,null);assert.equal(h.calls.filter(call=>call.path.endsWith('/status')).length,before);assert.deepEqual(writes(h),[]);
});

test('assistant activation remains off when the private policy is unavailable, including injected actions',async t=>{
  const row=item({enabled:true,assistant_activation_allowed:false,allowed_senders:[]}),h=await makeChannels({data:listing([row])});t.after(()=>h.close());assert.equal(h.element('[data-qc-action=assistant_on]').disabled,true);assert.ok(h.element('[data-qc-assistant-reason]'));const injected=h.document.createElement('button');injected.dataset.qcAction='assistant_on';h.element('#channels').append(injected);injected.click();assert.equal(h.document.querySelector('[data-qc-form=confirm]'),null);assert.deepEqual(writes(h),[]);
});

test('approved assistant activation requires its own acknowledgement and only changes that flag',async t=>{
  const row=item({enabled:true}),h=await makeChannels({data:listing([row]),respond:(path,method,data)=>method==='PATCH'?item({enabled:true,assistant_enabled:data.assistant_enabled,revision:2}):undefined});t.after(()=>h.close());h.click('[data-qc-action=assistant_on]');h.submit();await flush();assert.deepEqual(writes(h),[]);h.check('[name=authorize_assistant]');h.submit();await flush();assert.deepEqual(writes(h)[0].data,{expected_revision:1,assistant_enabled:true});assert.equal(writes(h).some(call=>/start|provision|send/.test(call.path)),false);
});

test('stopping remains explicit and preserves the connection record and assistant preference',async t=>{
  const row=item({enabled:true,assistant_enabled:true}),h=await makeChannels({data:listing([row]),respond:(path,method)=>method==='POST'?state(row,'stopped'):undefined});t.after(()=>h.close());h.click('[data-qc-action=stop]');h.submit();await flush();assert.deepEqual(writes(h),[{path:base+'/'+channelA+'/stop',method:'POST',data:{expected_revision:1}}]);assert.equal(h.controller.getState().connection.assistant_enabled,true);assert.equal(h.controller.getState().connection.enabled,true);assert.equal(writes(h).some(call=>/delete|logout|disconnect/.test(call.path)),false);
});

test('stale revisions block authority without retrying or creating an update loop',async t=>{
  const row=item({enabled:true}),h=await makeChannels({data:listing([row]),respond:path=>path.endsWith('/status')?state(item({enabled:true,revision:2}),'connected'):undefined});t.after(()=>h.close());assert.equal(h.controller.getState().stale,true);const count=h.calls.length;await h.tick(30000);assert.equal(h.calls.length,count);assert.deepEqual(writes(h),[]);
});

test('polling is serialized and stops on hidden screens or unmount',async t=>{
  const h=await makeChannels({data:listing([item({enabled:true})])});t.after(()=>h.close());const count=h.calls.length;await h.tick(10000);assert.equal(h.calls.length,count+2);await h.visibility(true);const hidden=h.calls.length;await h.tick(20000);assert.equal(h.calls.length,hidden);h.controller.unmount();assert.equal(h.timers.size,0);assert.equal(h.element('#channels').childElementCount,0);assert.deepEqual(writes(h),[]);
});

test('invalid status responses back off without inventing a connection or recursively reading',async t=>{
  const h=await makeChannels({data:listing([item({enabled:true})]),respond:path=>path.endsWith('/status')?{channel_id:channelA,status:'connected'}:undefined});t.after(()=>h.close());assert.equal(h.controller.getState().runtime,null);assert.equal(h.calls.length,2);await h.tick(14999);assert.equal(h.calls.length,2);await h.tick(1);assert.equal(h.calls.length,3);assert.deepEqual(writes(h),[]);
});

test('a legacy member with metadata-only access never reads or prepares the shared admin session',async t=>{
  const row=item({enabled:true,legacy_transport:true,can_manage:false}),h=await makeChannels({data:listing([row],{access:{can_manage:false},capabilities:{}})});t.after(()=>h.close());assert.equal(h.calls.length,1);assert.equal(h.document.querySelector('[data-qc-number]'),null);for(const action of ['prepare','recover','connect','assistant_on','stop'])assert.equal(h.document.querySelector('[data-qc-action='+action+']'),null);assert.deepEqual(writes(h),[]);
});

test('the authorized canonical alias prepares explicitly without global APIs, changing enabled or escalating recovery',async t=>{
  const row=item({enabled:true,legacy_transport:true});let phase='qr_expired';
  const caps={prepare:true,recover:true,qr:true,pair:true,start:true,stop:false,change_state:false,change_assistant:true};
  const h=await makeChannels({data:listing([row],{capabilities:caps}),respond:(path,method,data)=>path.endsWith('/status')?state(row,phase,{capabilities:{pair:true}}):path.endsWith('/prepare')?(phase='connecting',state(row,phase)):undefined});t.after(()=>h.close());
  h.click('[data-qc-action=prepare]');assert.deepEqual(writes(h),[]);h.click('[data-qc-action=cancel-confirm]');assert.deepEqual(writes(h),[]);h.click('[data-qc-action=prepare]');h.submit();await flush();assert.deepEqual(writes(h),[{path:base+'/'+channelA+'/prepare',method:'POST',data:{expected_revision:1,mode:'reconnect'}}]);assert.equal(h.controller.getState().runtime.status,'connecting');assert.equal(h.calls.some(call=>call.path.startsWith('/api/v1/qr/')||/recover$/.test(call.path)),false);
});

test('a fresh active, pairing, revoked or uncertain alias state interrupts preparation without a different mutation',async t=>{
  const row=item({enabled:true,legacy_transport:true}),caps={prepare:true,recover:true,qr:true,pair:true,start:true,change_assistant:true};
  for(const next of ['connected','pairing','logged_out','pair_failed','error']){
    let phase='qr_expired';const h=await makeChannels({data:listing([row],{capabilities:caps}),respond:path=>path.endsWith('/status')?state(row,phase,{capabilities:{pair:true}}):undefined});h.click('[data-qc-action=prepare]');phase=next;h.submit();await flush();assert.deepEqual(writes(h),[],next);assert.equal(h.controller.getState().runtime.status,next);if(['logged_out','pair_failed'].includes(next))assert.ok(h.element('[data-qc-action=recover]'));h.close();
  }
});

test('recovery of the alias requires its own capability and confirmation and cannot fall back to reconnect',async t=>{
  const row=item({enabled:true,legacy_transport:true});let phase='logged_out';const caps={prepare:true,recover:true,qr:true,pair:true,start:true,change_assistant:true};
  const h=await makeChannels({data:listing([row],{capabilities:caps}),respond:(path,method)=>path.endsWith('/status')?state(row,phase):path.endsWith('/prepare')?state(row,'connecting'):undefined});t.after(()=>h.close());h.click('[data-qc-action=recover]');phase='qr_ready';h.submit();await flush();assert.deepEqual(writes(h),[]);
  phase='logged_out';h.click('[data-qc-action=refresh]');await flush();h.click('[data-qc-action=recover]');h.submit();await flush();assert.deepEqual(writes(h),[{path:base+'/'+channelA+'/prepare',method:'POST',data:{expected_revision:1,mode:'recover'}}]);
});

test('the legacy assistant uses its specific permission without exposing or changing private allowlists',async t=>{
  const row=item({enabled:true,legacy_transport:true,assistant_enabled:true}),caps={qr:true,pair:true,change_state:false,change_assistant:true};
  const h=await makeChannels({data:listing([row],{capabilities:caps}),respond:(path,method,data)=>method==='PATCH'?(Object.assign(row,{assistant_enabled:data.assistant_enabled,revision:2}),row):path.endsWith('/status')?state(row,'connected'):undefined});t.after(()=>h.close());assert.equal(h.element('[data-qc-action=assistant_off]').disabled,false);h.click('[data-qc-action=assistant_off]');h.submit();await flush();assert.deepEqual(writes(h)[0].data,{expected_revision:1,assistant_enabled:false});assert.equal('allowed_senders' in writes(h)[0].data,false);assert.equal(h.controller.getState().connection.legacy_transport,true);
});
