import test from 'node:test';
import assert from 'node:assert/strict';
import {createLegacyCentral} from '../src/legacy-central.mjs';
import {createChannelManager} from '../src/channel-manager.mjs';
import {createBaileysChannelSession} from '../src/channel-session.mjs';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createHmac} from 'node:crypto';

const id='10000000-0000-4000-8000-000000000001',tenant='20000000-0000-4000-8000-000000000001';
const phone='5567999999901',key='synthetic-primary-key-0000000000000000000000000000';

function fixture(changes={},options={}){
  const calls=[],env={QR_LEGACY_CENTRAL_ENABLED:'1',QR_CHANNEL_BROKER_URL:'http://api.railway.internal',
    QR_BRIDGE_TOKEN:key,QR_AI_PILOT_NUMBER:phone,...changes};
  let active=true,connected=true,currentDigest='a'.repeat(64),context,closeAdapter;
  const central=createLegacyCentral({env,sessionDigest:()=> 'a'.repeat(64),sessionDigest:()=>currentDigest,status:()=>connected?'connected':'reconnecting',
    fetchImpl:async(url,options)=>{
      calls.push(['bootstrap',String(url),options]);
      return {ok:true,json:async()=>({legacy_transport:true,channel_id:id,tenant_id:tenant,session_digest:'a'.repeat(64),allowed_senders:[phone]})};
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
    rawSend:async(...args)=>{calls.push(['raw',...args]);return {accepted:true,id:'out:SYNTHETIC'};},...options});
  return {central,calls,env,context:()=>context,adapter:()=>closeAdapter,revoke:()=>{active=false;},disconnect:()=>{connected=false;},replaceAccount:()=>{currentDigest='b'.repeat(64);}};
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

test('adopted audio delegates only to the current managed transcription context without a business turn',async()=>{
  const f=fixture();await f.central.resume();let asr=0;
  f.context().transcribe=async payload=>{asr++;assert.equal(payload.phone,phone);return 'synthetic managed transcript';};
  const result=await f.central.transcribe({phone,buffer:Buffer.from([1,2,3]),mediaType:'audio/ogg'});
  assert.equal(result,'synthetic managed transcript');assert.equal(asr,1);
  assert.equal(f.calls.some(row=>['message','raw','send'].includes(row[0])),false);
  await assert.rejects(f.central.transcribe({phone:'5567999999902',buffer:Buffer.from([1])}),/not_ready/);
  assert.equal(asr,1);
});

test('lease loss during managed audio never falls back to the legacy credential or a business reply',async()=>{
  const f=fixture();await f.central.resume();let release;
  f.context().transcribe=()=>new Promise(resolve=>{release=resolve;});
  const pending=f.central.transcribe({phone,buffer:Buffer.from([1,2,3]),mediaType:'audio/ogg'});
  while(!release)await new Promise(resolve=>setImmediate(resolve));
  f.revoke();release('late private transcript');await assert.rejects(pending,/revoked/);
  assert.equal(f.calls.some(row=>['message','raw','send'].includes(row[0])),false);
});

test('a preparation failure delegates its existing operation to the managed normal sender only',async()=>{
  const f=fixture();await f.central.resume();let called=0;
  const payload={phone,message_ids:['synthetic-audio'],operation_id:'30000000-0000-4000-8000-000000000001',control_revision:31};
  f.context().onPreparationFailed=async(actual,options)=>{called++;assert.equal(actual,payload);assert.equal(options.isCurrent(),true);return {accepted:true};};
  assert.equal((await f.central.preparationFailed(payload,{noticeProofs:[],isCurrent:()=>true})).accepted,true);
  assert.equal(called,1);assert.equal(f.calls.some(row=>['message','raw','send'].includes(row[0])),false);
  f.revoke();await assert.rejects(f.central.preparationFailed(payload),/not_ready/);assert.equal(called,1);
});

test('manager retirement detaches only the adapter and refuses stale sends',async()=>{
  const f=fixture();await f.central.resume();const adapter=f.adapter();adapter.close();
  assert.equal(f.central.health().connected,true);assert.equal(f.central.health().ready,false);
  await assert.rejects(adapter.send(phone,'Sintético'),/revoked/);
  assert.equal(f.calls.some(c=>c[0]==='raw'),false);
});

test('a different physical account cannot use the adopted company or send on its behalf',async()=>{
  const f=fixture();await f.central.resume();f.replaceAccount();
  assert.equal(f.central.health().session_preserved,false);assert.equal(f.central.health().ready,false);
  assert.equal(f.central.allowed(phone),false);assert.equal(f.adapter().status(),'connecting');
  await assert.rejects(f.adapter().send(phone,'Sintético'),/revoked/);
  assert.equal(f.calls.some(c=>c[0]==='raw'),false);
});

test('trusted ingress scope exists only for the current matching physical session',async()=>{
  const f=fixture();assert.equal(f.central.ingressScope(),null);await f.central.resume();
  const scope=f.central.ingressScope();assert.ok(Object.isFrozen(scope));
  assert.deepEqual(scope,{tenant_id:tenant,channel_id:id,session_digest:'a'.repeat(64)});
  f.replaceAccount();assert.equal(f.central.ingressScope(),null);
  const revoked=fixture();await revoked.central.resume();revoked.revoke();assert.equal(revoked.central.ingressScope(),null);
});

test('provider disconnect keeps only the fenced no-replay review identity available',async()=>{
  const f=fixture();await f.central.resume();
  const actualOwner='30000000-0000-4000-8000-000000000001';Object.assign(f.context(),{owner:actualOwner,fence:9});
  f.disconnect();
  assert.equal(f.central.ingressScope(),null);assert.equal(f.central.nativeTestScope(),null);
  assert.deepEqual(f.central.reviewScope(),{tenant_id:tenant,channel_id:id,session_digest:'a'.repeat(64),owner:actualOwner,fence:9});
  assert.equal(f.central.reviewAllowed(phone),true);assert.equal(f.central.allowed(phone),false);
  assert.equal(f.adapter().status(),'connecting');
  f.revoke();assert.equal(f.central.reviewScope(),null);assert.equal(f.central.reviewAllowed(phone),false);
});

test('native canary scope is a read-only snapshot of the same actual owner/fence and matching preserved account',async()=>{
  const f=fixture();assert.equal(f.central.nativeTestScope(),null);await f.central.resume();assert.equal(f.central.nativeTestScope(),null);
  const actualOwner='30000000-0000-4000-8000-000000000001';Object.assign(f.context(),{owner:actualOwner,fence:7});
  const before=f.calls.length,proof=f.central.nativeTestScope();assert.ok(Object.isFrozen(proof));
  assert.deepEqual(proof,{tenant_id:tenant,channel_id:id,session_digest:'a'.repeat(64),owner:actualOwner,fence:7});assert.equal(f.calls.length,before);
  f.replaceAccount();assert.equal(f.central.nativeTestScope(),null);
  const revoked=fixture();await revoked.central.resume();Object.assign(revoked.context(),{owner:actualOwner,fence:7});revoked.revoke();assert.equal(revoked.central.nativeTestScope(),null);
});

test('public pending/review readiness stays separate from raw transport authorization',async()=>{
  let state='degraded';const f=fixture({}, {publicHealth:()=>({state,ingress_pending:1,ingress_blocked:0})});
  await f.central.resume();assert.equal(f.central.health().connected,true);assert.equal(f.central.health().ready,false);
  assert.equal(f.adapter().status(),'degraded');assert.equal(f.adapter().connectionHealth().ingress_pending,1);
  assert.ok(f.central.ingressScope());await f.central.inbound({phone,message_id:'CONTROL',text:'pare'});
  await f.central.forward({phone,message_id:'TURN',text:'teste'});
  state='needs_attention';assert.equal(f.adapter().status(),'needs_attention');assert.equal(f.central.health().ready,false);
  state='connected';assert.equal(f.central.health().ready,true);
});

test('legacy automated/appointment relay rechecks pending admission after its capacity wait',async()=>{
  for(const source of ['automation','appointment','assistant']){
    let ready=true,relay=0,release;const capacity=new Promise(resolve=>{release=resolve;});
    const f=fixture({}, {outgoingReady:()=>ready,rawSend:async(_phone,_text,options)=>{
      await capacity;if(!options.isCurrent())return {accepted:false};relay++;return {accepted:true,id:'out:SYNTHETIC'};}});
    await f.central.resume();const running=f.adapter().send(phone,'Aviso',{source});
    await Promise.resolve();ready=false;release();await assert.rejects(running,/unknown|revoked/);
    assert.equal(relay,0);await assert.rejects(f.central.automatic(phone,'Outro aviso'),/not_ready/);
  }
});

test('fenced manual transport delegates to the original reliable sender exactly once',async()=>{
  const f=fixture();await f.central.resume();
  assert.deepEqual(await f.adapter().send(phone,'Sintético'),{id:'SYNTHETIC'});
  assert.equal(f.calls.filter(c=>c[0]==='raw').length,1);
  f.revoke();await assert.rejects(f.adapter().send(phone,'Sintético'),/revoked/);
  assert.equal(f.calls.filter(c=>c[0]==='raw').length,1);
});

test('adopted PRIMARY preserves the private deferred ACK callback for the same accepted presentation',async()=>{
  let sentOptions,acks=0;
  const acknowledgePresentation=async()=>{acks++;return true;};
  const f=fixture({}, {rawSend:async(_phone,_text,options)=>{sentOptions=options;return {accepted:true,id:'out:SYNTHETIC',acknowledgePresentation};}});
  await f.central.resume();
  f.context().presentationAck=async()=>({acknowledged:true});
  const presentation={version:1,id:'A'.repeat(32),action:{type:'reply_buttons'}};
  const result=await f.adapter().send(phone,'Resumo fictício',{source:'assistant',presentation,deferPresentationAck:true});
  assert.equal(sentOptions.deferPresentationAck,true);assert.equal(typeof sentOptions.presentationAck,'function');
  assert.equal(result.id,'SYNTHETIC');assert.equal(result.acknowledgePresentation,acknowledgePresentation);assert.equal(acks,0);
  assert.equal(await result.acknowledgePresentation(),true);assert.equal(acks,1);
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
  const central=createLegacyCentral({env,sessionDigest:()=> 'a'.repeat(64),status:()=> 'connected',
    fetchImpl:async()=>({ok:true,json:async()=>({legacy_transport:true,channel_id:id,tenant_id:tenant,session_digest:'a'.repeat(64),allowed_senders:[phone]})}),
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

test('old self PN requires HMAC correspondence with the preserved physical session',async()=>{
  const old='556781234567',proof=createHmac('sha256',key).update('orion:preserved-primary-session:v1\0'+old).digest('hex');
  for(const valid of [false,true]){
    let starts=0;
    const central=createLegacyCentral({env:{QR_LEGACY_CENTRAL_ENABLED:'1',QR_CHANNEL_BROKER_URL:'http://api.railway.internal',QR_BRIDGE_TOKEN:key},
      sessionDigest:()=>proof,status:()=> 'connected',
      fetchImpl:async()=>({ok:true,json:async()=>({legacy_transport:true,channel_id:id,tenant_id:tenant,
        session_digest:valid?proof:'a'.repeat(64),allowed_senders:[old],verified_self_phone:old})}),
      start:async()=>{starts++;},send:async()=>assert.fail('No bootstrap message'),rawSend:async()=>assert.fail('No raw send')});
    if(valid){await central.resume();assert.equal(starts,1);}
    else {await assert.rejects(central.resume(),/invalid_legacy_adoption/);assert.equal(starts,0);}
  }
});

test('central passes appointment source only to verified transactional path',async()=>{
  const f=fixture();await f.central.resume();
  const adapter=f.adapter();
  await adapter.send(phone,'Teste autorizado, sem dados pessoais',{
    source:'appointment',transactionalTyping:true});
  const call=f.calls.find(c=>c[0]==='raw');
  assert.equal(call[3].source,'appointment');
  assert.equal(call[3].transactionalTyping,true);
  assert.equal(call[1],phone);
  const next=await adapter.send(phone,'Mensagem manual');
  assert.equal(f.calls.filter(c=>c[0]==='raw')[1][3].source,'automation');
});

test('aviso automático autorizado propaga digitando apenas para o envio transacional',async()=>{
  const f=fixture();await f.central.resume();
  const adapter=f.adapter();
  await adapter.send(phone,'Aviso transacional sintético',{transactionalTyping:true,isCurrent:()=>true});
  const actual=f.calls.find(c=>c[0]==='raw');
  assert.equal(actual[1],phone);
  assert.equal(actual[3].waitForCapacity,true);
  assert.equal(actual[3].transactionalTyping,true);
  // A manual transport request has no implicit typing behavior.
  await adapter.send(phone,'Mensagem manual sintética');
  const second=f.calls.filter(c=>c[0]==='raw')[1];
  assert.equal(second[3].transactionalTyping,false);
});
