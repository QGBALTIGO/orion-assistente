import test from 'node:test';
import assert from 'node:assert/strict';
import {makeChannels,item,listing,runtime,tenantA,tenantB,channelA,channelB,owner,png,copy,flush,deferred} from './channels-harness.mjs';
const writes=h=>h.calls.filter(call=>call.method!=='GET');
const base='/api/v1/tenants/'+tenantA+'/qr-channels';
const secret='synthetic-private-credential-'+'x'.repeat(40);

test('connect enables only the selected device then starts with its new revision, never the assistant',async t=>{
  const row=item(),h=await makeChannels({respond:(path,method,payload)=>method==='PATCH'?item({enabled:true,revision:2}):method==='POST'?runtime(item({enabled:true,revision:2}),'connecting'):undefined});t.after(()=>h.close());
  h.click('[data-qc-action=connect]');assert.equal(writes(h).length,0);h.submit();h.submit();await flush();
  assert.deepEqual(writes(h),[{path:base+'/'+channelA,method:'PATCH',data:{expected_revision:1,enabled:true}},
    {path:base+'/'+channelA+'/start',method:'POST',data:{expected_revision:2}}]);
  assert.equal(h.controller.getState().runtime.status,'connecting');assert.equal(h.controller.getState().items[0].assistant_enabled,false);
});

test('uncertain enable or start never automatically repeats either connection operation',async t=>{
  for(const fail of ['enable','start']){
    const h=await makeChannels({respond:(path,method)=>{
      if(method==='PATCH'){if(fail==='enable')throw Error('uncertain');return item({enabled:true,revision:2});}
      if(method==='POST')throw Error('uncertain');
    }});t.after(()=>h.close());h.click('[data-qc-action=connect]');h.submit();await flush();
    assert.equal(h.controller.getState().uncertain,true);const count=writes(h).length;
    await h.tick(30000);assert.equal(writes(h).length,count);assert.equal(count,fail==='enable'?1:2);
  }
});

test('switching business before enable returns cannot start the old device',async t=>{
  const late=deferred(),h=await makeChannels({respond:(path,method)=>method==='PATCH'?late.promise:path.includes(tenantB)?listing([item({id:channelB,tenant_id:tenantB})]):undefined});t.after(()=>h.close());
  h.click('[data-qc-action=connect]');h.submit();await flush();await h.controller.setTenant(tenantB);
  late.resolve(item({enabled:true,revision:2}));await flush();assert.equal(writes(h).length,1);assert.equal(h.controller.getState().tenantId,tenantB);
});

test('QR is shown automatically only during an explicitly started connection flow',async t=>{
  const row=item({enabled:true}),h=await makeChannels({data:listing([row]),respond:(path,method)=>method==='POST'?runtime(row,'connecting'):path.endsWith('/status')?runtime(row,'qr_ready'):undefined});t.after(()=>h.close());
  assert.equal(h.document.querySelector('[data-qc-qr]'),null);h.click('[data-qc-action=start]');h.submit();await flush();await h.tick(5000);
  assert.ok(h.document.querySelector('[data-qc-qr] img'));assert.equal(writes(h).length,1);
  h.click('[data-qc-action=hide-qr]');await h.tick(5000);assert.equal(h.document.querySelector('[data-qc-qr]'),null);
});

test('opening the list performs no start, activation, send or legacy operation',async t=>{
  const h=await makeChannels();t.after(()=>h.close());assert.deepEqual(writes(h),[]);assert.equal(h.calls.length,1);assert.match(h.element('#channels').textContent,/Desativado/);assert.match(h.element('#channels').textContent,/Fase privada/);assert.match(h.element('#channels').textContent,/Áudio depende/);assert.ok(h.document.getElementById('legacy'));assert.equal(h.calls.some(call=>call.path.includes('/internal/')||call.path.includes('/api/v1/qr/')),false);
});
test('disabled feature shows unavailable without touching the existing connector',async t=>{
  const h=await makeChannels({respond:()=>{throw Object.assign(Error('private debug text'),{status:423});}});t.after(()=>h.close());assert.equal(h.controller.getState().unavailable,true);assert.match(h.element('#channels').textContent,/Recurso ainda não disponível/);assert.doesNotMatch(h.element('#channels').textContent,/private debug/);assert.equal(h.calls.length,1);h.click('[data-qc-action=legacy]');await flush();assert.equal(h.legacy.length,1);assert.deepEqual(writes(h),[]);
});
test('viewers and a different channel owner cannot invoke mutations',async t=>{
  const h=await makeChannels({data:listing([item({can_manage:false})])});t.after(()=>h.close());assert.equal(h.document.querySelector('[data-qc-action=start]'),null);assert.equal(h.document.querySelector('[data-qc-action=enable]'),null);assert.equal(h.document.querySelector('[data-qc-action=status]'),null);assert.match(h.element('#channels').textContent,/proprietário autorizado/);assert.deepEqual(writes(h),[]);
});
test('a read-only list has no create or mutation controls',async t=>{
  const h=await makeChannels({data:listing([item({can_manage:false})],{access:{can_manage:false}})});t.after(()=>h.close());assert.equal(h.document.querySelector('[data-qc-action=new]'),null);assert.equal(h.document.querySelector('[data-qc-action=enable]'),null);assert.deepEqual(writes(h),[]);
});
test('new channel needs an explicit submit and sends no enabled or assistant flag',async t=>{
  const h=await makeChannels({respond:(path,method,payload)=>method==='POST'?{...item(),credential:secret,credential_issued:true}:undefined});t.after(()=>h.close());h.click('[data-qc-action=new]');assert.equal(writes(h).length,0);h.fill('[name=label]','Canal revisado');h.fill('[name=allowed_senders]','+55 (67) 99999-9901');h.submit();h.submit();await flush();assert.equal(writes(h).length,1);const sent=writes(h)[0];assert.equal(sent.path,base);assert.deepEqual(sent.data.allowed_senders,['5567999999901']);assert.equal(sent.data.provider,'baileys');assert.equal('enabled' in sent.data,false);assert.equal('assistant_enabled' in sent.data,false);assert.equal('tenant_id' in sent.data,false);assert.equal('credential' in sent.data,false);assert.match(sent.data.idempotency_key,/^[a-f0-9-]{36}$/);assert.equal(h.controller.getState().items[0].enabled,false);assert.equal(h.controller.getState().items[0].assistant_enabled,false);
});
test('allowlist validation rejects duplicates, nonphones, foreign numbers and excess entries before POST',async t=>{
  const h=await makeChannels();t.after(()=>h.close());h.click('[data-qc-action=new]');h.fill('[name=label]','Novo canal');for(const value of ['+5567999999901\n5567999999901','user@lid','+12025550123',Array.from({length:11},(_,i)=>'55679999999'+String(i).padStart(2,'0')).join('\n')]){h.fill('[name=allowed_senders]',value);h.submit();await flush();assert.equal(writes(h).length,0);}assert.match(h.element('[role=alert]').textContent,/1 a 10/);
});
test('the one-time credential is absent from state, logs, URLs and persistent browser storage',async t=>{
  const h=await makeChannels({respond:(path,method)=>method==='POST'?{...item(),credential:secret,credential_issued:true}:undefined});t.after(()=>h.close());h.click('[data-qc-action=new]');h.fill('[name=label]','Canal privado');h.fill('[name=allowed_senders]','5567999999901');h.submit();await flush();assert.equal(h.document.querySelector('[data-qc-secret]'),null);assert.equal(h.element('#channels').textContent.includes(secret),false);assert.equal(JSON.stringify(h.controller.getState()).includes(secret),false);assert.equal(JSON.stringify(h.calls).includes(secret),false);assert.equal(JSON.stringify(h.logs).includes(secret),false);assert.equal(h.window.localStorage.length,0);assert.equal(h.window.sessionStorage.length,0);assert.equal(h.document.querySelector('[data-qc-action=copy-secret]'),null);assert.deepEqual(h.copied,[]);assert.equal(h.document.querySelector('[data-qc-secret]'),null);assert.equal(h.element('#channels').textContent.includes(secret),false);
});
test('create response loss is recovered only by explicit repetition of the identical intent',async t=>{
  let attempt=0;const h=await makeChannels({respond:(path,method)=>{if(method==='POST'){if(++attempt===1)throw Error('uncertain transport');return {...item(),credential:null,credential_issued:false};}}});t.after(()=>h.close());h.click('[data-qc-action=new]');h.fill('[name=label]','Canal privado');h.fill('[name=allowed_senders]','5567999999901');h.submit();await flush();assert.equal(h.controller.getState().uncertain,true);assert.equal(writes(h).length,1);h.submit();assert.equal(writes(h).length,1);h.click('[data-qc-action=retry-create]');await flush();assert.deepEqual(writes(h)[0],writes(h)[1]);assert.equal(h.document.querySelector('[data-qc-secret]'),null);assert.match(h.element('#channels').textContent,/Dispositivo recuperado/);
});
test('canceling creation performs no write and keeps the registered channels',async t=>{
  const h=await makeChannels();t.after(()=>h.close());h.click('[data-qc-action=new]');h.fill('[name=label]','Descartar');h.click('[data-qc-action=cancel-create]');assert.equal(writes(h).length,0);assert.match(h.element('#channels').textContent,/Canal sintético/);
});
test('enabling the channel requires a review and cannot enable the assistant or start a session',async t=>{
  const h=await makeChannels({respond:(path,method,payload)=>method==='PATCH'?item({enabled:payload.enabled,revision:2}):undefined});t.after(()=>h.close());h.click('[data-qc-action=enable]');assert.equal(writes(h).length,0);assert.match(h.element('.qc-confirm').textContent,/Não inicia uma sessão/);h.submit();await flush();assert.deepEqual(writes(h)[0],{path:base+'/'+channelA,method:'PATCH',data:{expected_revision:1,enabled:true}});assert.equal(h.controller.getState().items[0].assistant_enabled,false);assert.equal(writes(h).some(call=>call.path.endsWith('/start')),false);
});
test('start is an independent POST with current revision and no channel secret',async t=>{
  const row=item({enabled:true});const h=await makeChannels({data:listing([row]),respond:(path,method)=>method==='POST'?runtime(row,'connecting'):undefined});t.after(()=>h.close());h.click('[data-qc-action=start]');assert.equal(writes(h).length,0);h.submit();await flush();assert.deepEqual(writes(h)[0],{path:base+'/'+channelA+'/start',method:'POST',data:{expected_revision:1}});assert.equal(h.controller.getState().runtime.status,'connecting');assert.equal(h.controller.getState().items[0].assistant_enabled,false);
});
test('assistant activation has its own explicit acknowledgement and PATCH',async t=>{
  const row=item({enabled:true});const h=await makeChannels({data:listing([row]),respond:(path,method,payload)=>method==='PATCH'?item({enabled:true,assistant_enabled:payload.assistant_enabled,revision:2}):undefined});t.after(()=>h.close());h.click('[data-qc-action=assistant_on]');h.submit();await flush();assert.equal(writes(h).length,0);h.check('[name=authorize_assistant]');h.submit();await flush();assert.deepEqual(writes(h)[0].data,{expected_revision:1,assistant_enabled:true});assert.equal('enabled' in writes(h)[0].data,false);assert.equal(writes(h).some(call=>/start|send/.test(call.path)),false);
});
test('stop preserves channel/assistant configuration and never calls delete or logout',async t=>{
  const row=item({enabled:true,assistant_enabled:true});const h=await makeChannels({data:listing([row]),respond:(path,method)=>method==='POST'?runtime(row,'stopped'):undefined});t.after(()=>h.close());h.click('[data-qc-action=stop]');assert.match(h.element('.qc-confirm').textContent,/não desvincula o aparelho/);h.submit();await flush();assert.deepEqual(writes(h)[0],{path:base+'/'+channelA+'/stop',method:'POST',data:{expected_revision:1}});assert.equal(h.controller.getState().items[0].assistant_enabled,true);assert.equal(h.controller.getState().items[0].enabled,true);assert.equal(writes(h).some(call=>call.method==='DELETE'||/logout|disconnect/.test(call.path)),false);
});
test('a stale-revision rejection never retries the state change automatically',async t=>{
  const h=await makeChannels({respond:(path,method)=>{if(method==='PATCH')throw Object.assign(Error('stale secret text'),{status:409});}});t.after(()=>h.close());h.click('[data-qc-action=enable]');h.submit();await flush();assert.equal(writes(h).length,1);assert.equal(h.controller.getState().uncertain,false);assert.match(h.element('[role=alert]').textContent,/canal mudou/);assert.doesNotMatch(h.element('#channels').textContent,/stale secret/);await h.tick(20000);assert.equal(writes(h).length,1);
});
test('status polling is serialized and stops on unmount',async t=>{
  const h=await makeChannels({data:listing([item({enabled:true})])});t.after(()=>h.close());const initial=h.calls.length;await h.tick(10000);assert.equal(h.calls.length,initial+2);assert.equal(writes(h).length,0);h.controller.unmount();assert.equal(h.timers.size,0);await h.tick(30000);assert.equal(h.calls.length,initial+2);assert.equal(h.element('#channels').childElementCount,0);
});
test('hidden or inactive hosts do not poll and discard one-time private content',async t=>{
  const h=await makeChannels({hidden:true,data:listing([item({enabled:true})])});t.after(()=>h.close());assert.equal(h.calls.length,0);h.element('#channels').hidden=false;await flush();assert.ok(h.calls.length>=2);h.controller.setActive(false);const count=h.calls.length;await h.tick(20000);assert.equal(h.calls.length,count);assert.equal(h.timers.size,0);h.controller.setActive(true);await flush();assert.ok(h.calls.length>count);await h.visibility(true);const hiddenCount=h.calls.length;await h.tick(20000);assert.equal(h.calls.length,hiddenCount);
});
test('a delayed status or QR from channel A cannot replace the selected channel B',async t=>{
  const late=deferred(),a=item({enabled:true}),b=item({id:channelB,label:'Canal B',enabled:true});let waiting=true;
  const h=await makeChannels({data:listing([a,b]),respond:path=>path.endsWith('/'+channelA+'/qr')&&waiting?late.promise:undefined});t.after(()=>h.close());h.click('[data-qc-action=qr]');await flush();h.click('[data-qc-channel="'+channelB+'"]');await flush();waiting=false;late.resolve({channel_id:channelA,revision:1,image:png,expires_in_ms:45000});await flush();assert.equal(h.controller.getState().selected,channelB);assert.equal(h.controller.getState().hasQR,false);assert.equal(h.document.querySelector('[data-qc-qr]'),null);assert.match(h.element('#channels').textContent,/Canal B/);
});
test('tenant switch drops old credentials and refuses a delayed previous-company response',async t=>{
  const late=deferred();let pendingA=false;const h=await makeChannels({respond:path=>path.startsWith('/api/v1/tenants/'+tenantB)?listing([item({id:channelB,tenant_id:tenantB,label:'Empresa B'})]):pendingA?late.promise:undefined});t.after(()=>h.close());pendingA=true;const old=h.controller.load();await flush();await h.controller.setTenant(tenantB);late.resolve(listing());await old;assert.equal(h.controller.getState().tenantId,tenantB);assert.match(h.element('#channels').textContent,/Empresa B/);assert.doesNotMatch(h.element('#channels').textContent,/Canal sintético/);
});
test('QR display accepts only scoped PNG with matching revision and erases it at expiry',async t=>{
  const row=item({enabled:true});const h=await makeChannels({data:listing([row]),respond:path=>path.endsWith('/qr')?{channel_id:channelA,revision:1,image:png,expires_in_ms:1000}:path.endsWith('/status')?runtime(row,'qr_ready'):undefined});t.after(()=>h.close());h.click('[data-qc-action=qr]');await flush();assert.ok(h.element('[data-qc-qr] img').src.startsWith('data:image/png;base64,'));await h.tick(1000);assert.equal(h.document.querySelector('[data-qc-qr]'),null);assert.equal(h.controller.getState().hasQR,false);assert.match(h.element('[role=alert]').textContent,/QR expirou/);
});
test('foreign, stale or non-PNG QR payloads never become browser images',async t=>{
  let answer={channel_id:channelB,revision:1,image:png,expires_in_ms:45000};const h=await makeChannels({data:listing([item({enabled:true})]),respond:path=>path.endsWith('/qr')?answer:undefined});t.after(()=>h.close());for(const invalid of [answer,{channel_id:channelA,revision:2,image:png,expires_in_ms:45000},{channel_id:channelA,revision:1,image:'data:image/svg+xml,<svg onload=alert(1)>',expires_in_ms:45000}]){answer=invalid;h.click('[data-qc-action=qr]');await flush();assert.equal(h.document.querySelector('[data-qc-qr] img'),null);}assert.equal(writes(h).length,0);
});
test('status failures show unknown rather than inventing a successful disconnection or connection',async t=>{
  const h=await makeChannels({data:listing([item({enabled:true})]),respond:path=>{if(path.endsWith('/status'))throw Object.assign(Error('token-and-provider-detail'),{status:503});}});t.after(()=>h.close());assert.equal(h.controller.getState().runtime,null);assert.match(h.element('#channels').textContent,/Não verificada/);assert.doesNotMatch(h.element('#channels').textContent,/token-and-provider/);assert.equal(writes(h).length,0);
});
test('channel labels cannot inject HTML and no key appears after a replay',async t=>{
  const h=await makeChannels({data:listing([item({label:'<img src=x onerror=alert(1)>'})])});t.after(()=>h.close());assert.match(h.element('#channels').textContent,/<img/);assert.equal(h.document.querySelector('img[src=x]'),null);assert.equal(h.document.querySelector('[data-qc-secret]'),null);assert.deepEqual(h.errors,[]);
});
test('confirmation pauses polling so an acknowledgement cannot be erased by a background response',async t=>{
  const row=item({enabled:true});const h=await makeChannels({data:listing([row])});t.after(()=>h.close());h.click('[data-qc-action=assistant_on]');h.check('[name=authorize_assistant]');const count=h.calls.length;await h.tick(20000);assert.equal(h.calls.length,count);assert.equal(h.element('[name=authorize_assistant]').checked,true);h.click('[data-qc-action=cancel-confirm]');await h.tick(5000);assert.ok(h.calls.length>count);assert.equal(writes(h).length,0);
});
test('a changed server revision blocks old authority and does not start an immediate reload loop',async t=>{
  const row=item({enabled:true});const h=await makeChannels({data:listing([row]),respond:path=>path.endsWith('/status')?runtime(item({enabled:true,revision:2}),'connected'):undefined});t.after(()=>h.close());assert.equal(h.controller.getState().stale,true);assert.equal(h.controller.getState().runtime,null);const count=h.calls.length;await h.tick(30000);assert.equal(h.calls.length,count);h.click('[data-qc-action=start]');assert.equal(h.document.querySelector('[data-qc-form=confirm]'),null);assert.equal(writes(h).length,0);assert.match(h.element('[role=alert]').textContent,/Atualize a lista/);
});
test('an invalid status contract backs off without inventing a state or recursive GETs',async t=>{
  const h=await makeChannels({data:listing([item({enabled:true})]),respond:path=>path.endsWith('/status')?{channel_id:channelA,status:'connected'}:undefined});t.after(()=>h.close());assert.equal(h.controller.getState().runtime,null);assert.equal(h.calls.length,2);await h.tick(14999);assert.equal(h.calls.length,2);await h.tick(1);assert.equal(h.calls.length,3);assert.equal(writes(h).length,0);
});
