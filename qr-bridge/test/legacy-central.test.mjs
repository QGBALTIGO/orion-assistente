import test from 'node:test';
import assert from 'node:assert/strict';
import {createLegacyCentral} from '../src/legacy-central.mjs';
import {createChannelManager} from '../src/channel-manager.mjs';
import {createBaileysChannelSession} from '../src/channel-session.mjs';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const id='10000000-0000-4000-8000-000000000001',tenant='20000000-0000-4000-8000-000000000001';
const phone='5567999999901',key='synthetic-primary-key-0000000000000000000000000000';

function fixture(changes={}){
  const calls=[],env={QR_LEGACY_CENTRAL_ENABLED:'1',QR_CHANNEL_BROKER_URL:'http://api.railway.internal',
    QR_BRIDGE_TOKEN:key,QR_AI_PILOT_NUMBER:phone,...changes};
  let active=true,connected=true,context,closeAdapter;
  const central=createLegacyCentral({env,status:()=>connected?'connected':'reconnecting',
    fetchImpl:async(url,options)=>{
      calls.push(['bootstrap',String(url),options]);
      return {ok:true,json:async()=>({legacy_transport:true,channel_id:id,tenant_id:tenant,allowed_senders:[phone]})};
    },
    start:async()=>{
      context={channelId:id,tenantId:tenant,secret:key,legacyTransport:true,
        isCurrent:()=>active,assertCurrent:async()=>{if(!active)throw Error('revoked');},allowedSender:value=>value===phone,
        onInbound:async p=>{calls.push(['inbound',p]);return {revision:7};},
        onMessage:async(p,o)=>{calls.push(['message',p,o]);return {accepted:true};},
        onReceipt:async(i,d)=>calls.push(['receipt',i,d])};
      closeAdapter=await central.attach(context);
    },
    send:async(...args)=>{calls.push(['send',...args]);return {accepted:true};},
    rawSend:async(...args)=>{calls.push(['raw',...args]);return {accepted:true,id:'out:SYNTHETIC'};}});
  return {central,calls,env,context:()=>context,adapter:()=>closeAdapter,revoke:()=>{active=false;},disconnect:()=>{connected=false;}};
}

test('adopts one registry identity without opening or sending on the existing socket',async()=>{
  const f=fixture();assert.equal((await f.central.resume()).ready,true);
  await f.central.resume();assert.equal(f.calls.filter(c=>c[0]==='bootstrap').length,1);
  assert.equal(f.calls.filter(c=>['raw','send'].includes(c[0])).length,0);
  assert.equal(f.adapter().status(),'connected');
  f.disconnect();assert.equal(f.adapter().status(),'connecting');
  assert.equal(f.central.health().session_preserved,true);
});

test('preserves the broker notice proof and batches, with no parallel legacy answer',async()=>{
  const f=fixture();const proof=await f.central.inbound({phone,message_id:'SYNTHETIC',text:'Olá',kind:'text'});
  const options={noticeProofs:[proof],isCurrent:()=>true};
  await f.central.forward({phone,message_id:'SYNTHETIC',text:'Olá'},options);
  assert.deepEqual(f.calls.map(c=>c[0]),['bootstrap','inbound','message']);
  assert.equal(f.calls.at(-1)[2],options);
  await assert.rejects(f.central.inbound({phone:'5567999999902',message_id:'OTHER'}),/denied/);
});

test('manager retirement detaches only the adapter and refuses stale sends',async()=>{
  const f=fixture();await f.central.resume();const adapter=f.adapter();adapter.close();
  assert.equal(f.central.health().connected,true);assert.equal(f.central.health().ready,false);
  await assert.rejects(adapter.send(phone,'Sintético'),/revoked/);
  assert.equal(f.calls.some(c=>c[0]==='raw'),false);
});

test('fenced manual transport delegates to the original reliable sender exactly once',async()=>{
  const f=fixture();await f.central.resume();
  assert.deepEqual(await f.adapter().send(phone,'Sintético'),{id:'SYNTHETIC'});
  assert.equal(f.calls.filter(c=>c[0]==='raw').length,1);
  f.revoke();await assert.rejects(f.adapter().send(phone,'Sintético'),/revoked/);
  assert.equal(f.calls.filter(c=>c[0]==='raw').length,1);
});

test('receipts are forwarded without sending and automatic notices use the central manager',async()=>{
  const f=fixture();await f.central.resume();await f.central.receipt('SYNTHETIC','read');
  await f.central.automatic(phone,'Aviso sintético');
  const send=f.calls.find(c=>c[0]==='send');assert.equal(send[1],id);assert.equal(send[3].source,'automation');
  assert.equal(f.calls.some(c=>c[0]==='raw'),false);
});

test('no opt-in means no bootstrap, session or outbound attempt',async()=>{
  const f=fixture({QR_LEGACY_CENTRAL_ENABLED:'0'});
  assert.deepEqual(await f.central.resume(),{ready:false,configured:false});assert.deepEqual(f.calls,[]);
});

test('an external bootstrap destination is rejected before a request',async()=>{
  const f=fixture({QR_CHANNEL_BROKER_URL:'https://external.example'});
  await assert.rejects(f.central.resume(),/unsafe_legacy_broker/);assert.deepEqual(f.calls,[]);
});

test('a different pilot cannot silently broaden the registered allowlist',async()=>{
  const f=fixture({QR_AI_PILOT_NUMBER:'5567999999902'});
  await assert.rejects(f.central.resume(),/invalid_legacy_adoption/);
  assert.equal(f.central.health().ready,false);assert.equal(f.calls.length,1);
});

test('the ordinary session factory refuses a legacy alias before loading Baileys',async()=>{
  await assert.rejects(createBaileysChannelSession({legacyTransport:true},{loadBaileys:()=>assert.fail('new socket')}),/legacy_requires_existing_socket/);
});

for(const pause of [false,true])test(`full managed pipeline preserves the legacy socket; pause=${pause}`,async t=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'orion-legacy-central-test-'));
  let manager,mode='ai',revision=0,raw=0,finished=0;const calls=[];
  const env={QR_LEGACY_CENTRAL_ENABLED:'1',QR_CHANNEL_BROKER_URL:'http://api.railway.internal',QR_BRIDGE_TOKEN:key};
  const central=createLegacyCentral({env,status:()=> 'connected',
    fetchImpl:async()=>({ok:true,json:async()=>({legacy_transport:true,channel_id:id,tenant_id:tenant,allowed_senders:[phone]})}),
    start:(...a)=>manager.start(...a),send:(...a)=>manager.send(...a),
    rawSend:async()=>{raw++;return {id:'out:SYNTHETIC',accepted:true};}});
  const broker={
    async lease(_id,_key,operation,owner){return {channel_id:id,tenant_id:tenant,owner,provider:'baileys',fence:1,
      expires_in_ms:30000,allowed_senders:[phone],assistant_enabled:true,inbox_control:true,legacy_transport:true};},
    async journal(){calls.push('journal');return {recorded:true};},
    async interaction(){calls.push('interaction');return {recorded:true,revision:++revision};},
    async forward(){calls.push('forward');if(pause)mode='human';return {answer:'Resposta sintética',control_revision:revision};},
    async authorizeSend(_id,_key,_lease,payload){calls.push('authorize');if(mode!=='ai')throw Error('human');return {authorized:true,operation_id:payload.operation_id};},
    async finishSend(){finished++;return {released:true};}};
  manager=createChannelManager({broker,root,createSession:c=>central.attach(c)});
  t.after(()=>{manager.close();const resolved=path.resolve(root);assert.ok(resolved.startsWith(path.resolve(os.tmpdir())+path.sep+'orion-legacy-central-test-'));fs.rmSync(resolved,{recursive:true,force:true});});
  await central.resume();
  const proof=await central.inbound({phone,message_id:'SYNTHETIC-INBOUND',text:'Olá',kind:'text'});
  const result=await central.forward({phone,message_id:'SYNTHETIC-INBOUND',text:'Olá',intake_revision:revision},{noticeProofs:[proof]});
  assert.deepEqual(calls,['journal','interaction','forward','authorize']);
  assert.equal(raw,pause?0:1);assert.equal(finished,pause?0:1);assert.equal(result.accepted,!pause);
  assert.equal(central.health().connected,true);
});
