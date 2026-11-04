import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {Readable} from 'node:stream';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createDeviceDiagnostics} from '../src/device-diagnostics.mjs';
import {createChannelManager} from '../src/channel-manager.mjs';
import {createChannelBroker} from '../src/channel-broker.mjs';
import {createChannelRoutes} from '../src/channel-routes.mjs';
import {channelDirectory,privateDirectory,sendLedger,digest} from '../src/channel-storage.mjs';

const deferred=()=>{let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};};
function fixture(t){
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'orion-device-diagnostics-'));fs.chmodSync(root,0o700);
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const id=randomUUID(),tenant=randomUUID(),owner=randomUUID(),key='synthetic-diagnostic-key-'.repeat(3),
    request={request_id:randomUUID(),device_id:randomUUID(),generation:randomUUID(),settings_revision:4},
    configuration_version='a'.repeat(64),authorizations=[],receipts=[],finishes=[],probes=[],controllers=[];
  let claimed=null,time=0;
  const behavior={grant:value=>value,receipt:value=>value,finish:async()=>{},
    probe:async(config,options)=>{await options.assertCurrent();return {state:'reachable',latency_ms:12,target:'tls_reference',error_code:null};}};
  const broker={
    lease:()=>assert.fail('Proxy diagnostics must never claim a WhatsApp lease'),
    async deviceProxyReceipt(channel,secret,input){assert.equal(channel,id);assert.equal(secret,key);assert.equal(input.request_id,request.request_id);receipts.push(input);
      return behavior.receipt({read_only:true,kind:'proxy_test',channel_id:id,tenant_id:tenant,...request});},
    async deviceProxyAuthorize(channel,secret,input){
      assert.equal(channel,id);assert.equal(secret,key);assert.equal(input.request_id,request.request_id);authorizations.push(input);
      if(!input.recheck){if(claimed)throw Error('Already claimed');claimed={owner:input.owner,claim_token:input.claim_token,claim_fence:1};}
      else assert.deepEqual({owner:input.owner,claim_token:input.claim_token,claim_fence:input.claim_fence},claimed);
      return behavior.grant({authorized:true,kind:'proxy_test',channel_id:id,tenant_id:tenant,...request,...claimed,
        configuration_version,expires_in_ms:30000-time,expires_at:'2099-01-01T00:00:00.000Z',
        proxy:{enabled:true,url:'http://proxy.example.com:8080',username:'synthetic-user'},proxy_secret:{password:'synthetic-password'}},input);
    },
    async deviceProxyFinish(channel,secret,input){assert.equal(channel,id);assert.equal(secret,key);finishes.push(input);await behavior.finish();return {recorded:true};}
  };
  const probe=async(...args)=>{probes.push(args);return behavior.probe(...args);};
  function create(options={}){const value=createDeviceDiagnostics({root,broker,owner,clock:()=>time,now:()=>1700000000000,probe,...options});controllers.push(value);return value;}
  t.after(()=>controllers.forEach(value=>value.close()));
  return {id,tenant,owner,key,root,request,configuration_version,broker,behavior,authorizations,receipts,finishes,probes,probe,create,setTime:value=>{time=value;}};
}

test('receipt reads have no filesystem mutations, authorization claims, probes, messages or WhatsApp sessions',async t=>{
  const f=fixture(t),controller=f.create();
  for(let i=0;i<3;i++)assert.equal((await controller.read(f.id,f.key,f.request.request_id)).status,'unrecorded');
  assert.deepEqual(fs.readdirSync(f.root),[]);assert.equal(f.authorizations.length,0);assert.equal(f.probes.length,0);assert.equal(f.finishes.length,0);
});

test('explicit dispatch reserves before one probe, rechecks its own claim and recovers a lost acknowledgement without repeating',async t=>{
  const f=fixture(t),controller=f.create({appliedToSession:()=>false});f.behavior.finish=async()=>{throw Error('lost ACK private detail');};
  const started=await controller.dispatch(f.id,f.key,f.request);assert.equal(started.status,'running');
  await controller.settled();const result=await controller.read(f.id,f.key,f.request.request_id);
  assert.equal(result.status,'succeeded');assert.equal(result.result.state,'reachable');assert.equal(result.result.applied_to_session,false);
  assert.equal(result.result.configuration_version,f.configuration_version);assert.equal(result.result.scope,'configured_proxy');
  assert.equal(result.result.observed_at,'2023-11-14T22:13:20.000Z');assert.equal(f.probes.length,1);
  assert.equal(f.authorizations.filter(value=>!value.recheck).length,1);assert.ok(f.authorizations.filter(value=>value.recheck).length>=3);
  assert.deepEqual(f.finishes[0].result,result.result);
  const recovery=f.create();assert.deepEqual(await recovery.read(f.id,f.key,f.request.request_id),result);
  assert.deepEqual(await recovery.dispatch(f.id,f.key,f.request),result);assert.equal(f.probes.length,1);
  assert.equal(f.authorizations.filter(value=>!value.recheck).length,1);
  const publicText=JSON.stringify(result);for(const value of ['synthetic-password','synthetic-user','proxy.example.com',f.owner,f.key,f.authorizations[0].claim_token])assert.equal(publicText.includes(value),false,value);
  const ledgerDirectory=path.join(f.root,f.tenant,f.id,'device-diagnostics','send-ledger');
  for(const file of fs.readdirSync(ledgerDirectory)){const bytes=fs.readFileSync(path.join(ledgerDirectory,file));assert.equal(bytes.includes(Buffer.from('synthetic-password')),false);assert.equal(bytes.includes(Buffer.from(f.request.request_id)),false);}
});

test('a concurrent duplicate recovers running state and cannot acquire another claim',async t=>{
  const f=fixture(t),gate=deferred(),entered=deferred();f.behavior.probe=async()=>{entered.resolve();await gate.promise;return {state:'reachable',latency_ms:1,target:'tls_reference'};};
  const controller=f.create();await controller.dispatch(f.id,f.key,f.request);await entered.promise;
  assert.equal((await controller.dispatch(f.id,f.key,f.request)).status,'running');assert.equal(f.probes.length,1);
  assert.equal(f.authorizations.filter(value=>!value.recheck).length,1);gate.resolve();await controller.settled();
});

test('a reservation interrupted before its metadata/result is written remains uncertain after restart',async t=>{
  const f=fixture(t),base=channelDirectory(f.root,f.tenant,f.id),directory=privateDirectory(path.join(base,'device-diagnostics'));
  sendLedger(directory,f.key).reserve(f.request.request_id,digest(JSON.stringify([f.request,f.configuration_version])));
  const controller=f.create();assert.equal((await controller.read(f.id,f.key,f.request.request_id)).status,'uncertain');
  assert.equal((await controller.dispatch(f.id,f.key,f.request)).status,'uncertain');assert.equal(f.authorizations.length,0);assert.equal(f.probes.length,0);
});

test('receipt authorization and device/generation scope cannot cross tenant, request or key',async t=>{
  const f=fixture(t),controller=f.create();
  await assert.rejects(controller.dispatch(f.id,f.key,{...f.request,generation:randomUUID()}));
  await assert.rejects(controller.read(f.id,'wrong-synthetic-key-'.repeat(3),f.request.request_id));
  f.behavior.receipt=value=>({...value,request_id:randomUUID()});await assert.rejects(controller.read(f.id,f.key,f.request.request_id));
  f.behavior.receipt=value=>({...value,read_only:false});await assert.rejects(controller.read(f.id,f.key,f.request.request_id));
  assert.equal(f.authorizations.length,0);assert.equal(f.probes.length,0);assert.deepEqual(fs.readdirSync(f.root),[]);
});

for(const [label,mutate] of [
  ['owner',value=>({...value,owner:randomUUID()})],['token',value=>({...value,claim_token:randomUUID()})],
  ['fence',value=>({...value,claim_fence:0})],['generation',value=>({...value,generation:randomUUID()})],
  ['revision',value=>({...value,settings_revision:5})],['version',value=>({...value,configuration_version:'bad'})],
  ['authorization',value=>({...value,authorized:false})],['lifetime',value=>({...value,expires_in_ms:30001})]]){
  test('invalid proxy claim '+label+' never starts a probe',async t=>{
    const f=fixture(t);f.behavior.grant=mutate;
    await assert.rejects(f.create().dispatch(f.id,f.key,f.request));assert.equal(f.probes.length,0);assert.deepEqual(fs.readdirSync(f.root),[]);
  });
}

test('secret/configuration changes after admission fail before DNS and are not mislabeled as proxy reachability',async t=>{
  const f=fixture(t);f.behavior.grant=(value,input)=>input.recheck?{...value,proxy_secret:{password:'changed-secret'}}:value;
  const controller=f.create();await controller.dispatch(f.id,f.key,f.request);await controller.settled();
  const result=await controller.read(f.id,f.key,f.request.request_id);
  assert.equal(f.probes.length,0);assert.equal(result.status,'failed');assert.equal(result.result.error_code,'PROXY_CONFIGURATION_CHANGED');
});

test('revocation during network work is rechecked afterward, so a successful handshake cannot publish stale success',async t=>{
  const f=fixture(t);let revoked=false;
  f.behavior.probe=async()=>{revoked=true;return {state:'reachable',latency_ms:4,target:'tls_reference'};};
  f.behavior.grant=value=>{if(revoked)throw Error('Actor removed');return value;};
  const controller=f.create();await controller.dispatch(f.id,f.key,f.request);await controller.settled();
  const result=await controller.read(f.id,f.key,f.request.request_id);
  assert.equal(result.status,'failed');assert.equal(result.result.error_code,'PROXY_AUTHORIZATION_REVOKED');assert.equal(result.result.latency_ms,null);
  assert.equal(f.probes.length,1);assert.equal(f.finishes.length,1);
});

test('failed network probes are also reauthorized after completion and raw provider exceptions are discarded',async t=>{
  const f=fixture(t);let after=false;
  f.behavior.probe=async()=>{after=true;throw Error('private proxy host and password');};
  let checked=false;f.behavior.grant=value=>{if(after)checked=true;return value;};
  const controller=f.create();await controller.dispatch(f.id,f.key,f.request);await controller.settled();
  const result=await controller.read(f.id,f.key,f.request.request_id);
  assert.equal(checked,true);assert.equal(result.result.error_code,'PROXY_PROBE_FAILED');assert.equal(JSON.stringify(result).includes('private proxy'),false);
});

test('an absent proxy is a failed diagnostic and can never be reported as successful reachability',async t=>{
  const f=fixture(t);f.behavior.grant=value=>({...value,proxy:{enabled:false}});
  f.behavior.probe=async()=>({state:'not_configured',latency_ms:null,target:'tls_reference',error_code:'PROXY_NOT_CONFIGURED'});
  const controller=f.create();await controller.dispatch(f.id,f.key,f.request);await controller.settled();
  const result=await controller.read(f.id,f.key,f.request.request_id);
  assert.equal(result.status,'failed');assert.equal(result.result.state,'not_configured');
  assert.equal(result.result.error_code,'PROXY_NOT_CONFIGURED');assert.equal(result.result.latency_ms,null);
  assert.equal(f.finishes[0].status,'failed');
});

test('claim rechecks cannot extend the original deadline and shutdown cancels an active probe',async t=>{
  const f=fixture(t);f.behavior.probe=async()=>{f.setTime(30001);return {state:'reachable',latency_ms:5,target:'tls_reference'};};
  const controller=f.create();await controller.dispatch(f.id,f.key,f.request);await controller.settled();
  assert.equal((await controller.read(f.id,f.key,f.request.request_id)).result.error_code,'PROXY_AUTHORIZATION_REVOKED');
  const g=fixture(t),entered=deferred();let aborted=false;
  g.behavior.probe=async(_config,{signal})=>new Promise((_,reject)=>{entered.resolve();signal.addEventListener('abort',()=>{aborted=true;reject(Error('aborted'));},{once:true});});
  const active=g.create();await active.dispatch(g.id,g.key,g.request);await entered.promise;active.close();await active.settled();assert.equal(aborted,true);
});

test('manager tests an unapplied configuration with no session, lease, reconnect, pair, or send',async t=>{
  const f=fixture(t),manager=createChannelManager({root:f.root,broker:f.broker,owner:f.owner,
    createSession:()=>assert.fail('Proxy test must not start WhatsApp'),diagnosticOptions:{probe:f.probe}});t.after(()=>manager.close());
  assert.equal(manager.size,0);await manager.deviceProxyTest(f.id,f.key,f.request);
  for(let i=0;i<100&&!f.finishes.length;i++)await new Promise(resolve=>setImmediate(resolve));
  assert.equal(f.finishes.length,1);assert.equal(manager.size,0);
  assert.equal((await manager.deviceDiagnosticOperation(f.id,f.key,f.request.request_id)).result.applied_to_session,false);
});

async function call(route,{url,method='GET',headers={},value={}}){
  const req=Readable.from([Buffer.from(JSON.stringify(value))]);Object.assign(req,{url,method,headers});
  const res={writeHead(code,headers){this.code=code;this.headers=headers;},end(value){this.body=JSON.parse(value);}};
  await route(req,res);return res;
}

test('private diagnostic routes accept only strict explicit dispatch, and GETs never reach mutation methods',async()=>{
  const id=randomUUID(),request_id=randomUUID(),key='synthetic-route-key-'.repeat(3),headers={'x-orion-channel-key':key},calls=[];
  const input={request_id,device_id:randomUUID(),generation:randomUUID(),settings_revision:1};
  const route=createChannelRoutes({env:{QR_CHANNELS_ENABLED:'1'},brokerFactory:()=>({}),managerFactory:()=>({
    deviceProxyTest:async(...args)=>{calls.push(['probe',...args]);return {request_id,status:'running'};},
    deviceDiagnosticOperation:async(...args)=>{calls.push(['receipt',...args]);return {request_id,status:'uncertain'};},
    deviceDiagnostics:async()=>({status:'stopped'}),deviceStatus:async()=>({status:'stopped'}),
    start:()=>assert.fail('No implicit start'),send:()=>assert.fail('No message from diagnostic route'),deviceControl:()=>assert.fail('No control action')} )});
  const url=`/internal/channels/${id}/device/diagnostics/proxy`,receipt=`/internal/channels/${id}/device/diagnostics/operations/${request_id}`;
  assert.equal((await call(route,{url,headers})).code,405);assert.equal((await call(route,{url:receipt,headers,method:'POST'})).code,405);
  assert.equal((await call(route,{url,method:'POST',value:input})).code,401);
  for(const forbidden of [{url:'http://169.254.169.254'}, {proxy:{enabled:true}}, {target:'arbitrary'}, {claim_token:randomUUID()}, {phone:'5567999999901'}, {allowSelf:true}, {owner:randomUUID()}])
    assert.equal((await call(route,{url,headers,method:'POST',value:{...input,...forbidden}})).code,409);
  assert.equal(calls.length,0);
  assert.equal((await call(route,{url:receipt,headers})).code,200);assert.equal(calls[0][0],'receipt');
  assert.equal((await call(route,{url,headers,method:'POST',value:input})).code,202);assert.deepEqual(calls[1],['probe',id,key,input]);
  assert.equal((await call(route,{url:`/internal/channels/${id}/device/diagnostics`,headers})).code,200);
});

test('broker diagnostic callbacks have fixed private paths and no WhatsApp lease headers',async()=>{
  const calls=[],id=randomUUID(),key='synthetic-private-key-'.repeat(3),payload={request_id:randomUUID(),owner:randomUUID(),claim_token:randomUUID()};
  const broker=createChannelBroker({apiUrl:'http://api.railway.internal',fetchImpl:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({})};}});
  await broker.deviceProxyAuthorize(id,key,payload);await broker.deviceProxyFinish(id,key,payload);await broker.deviceProxyReceipt(id,key,{request_id:payload.request_id});
  assert.deepEqual(calls.map(value=>value.url.pathname),['authorize','finish','receipt'].map(action=>`/internal/qr/channels/${id}/device/diagnostics/proxy/${action}`));
  for(const {options} of calls){assert.equal(options.method,'POST');assert.equal(options.redirect,'error');assert.equal(options.headers['X-Orion-Channel-Key'],key);assert.equal('X-Orion-Channel-Owner' in options.headers,false);}
});
