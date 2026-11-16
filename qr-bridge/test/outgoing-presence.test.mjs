import test from 'node:test';
import assert from 'node:assert/strict';
import {makeOutgoingPresence,outgoingPresenceTarget} from '../src/outgoing-presence.mjs';

const phone='5511999999901',other='595991234567',jid=phone+'@s.whatsapp.net';
const defer=()=>{let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};};
const flush=async()=>{for(let i=0;i<30;i++)await Promise.resolve();};
function fixture(){
  let time=0,sequence=0,connected=true;const timers=new Map(),presence=[];
  const schedule=(fn,ms)=>{const token={id:++sequence,unref(){}};timers.set(token,{fn,at:time+ms});return token;};
  const cancel=token=>timers.delete(token);
  const socket={sendPresenceUpdate:async(type,to)=>presence.push({type,to,at:time})};
  const coordinator=makeOutgoingPresence({socket,isConnected:()=>connected,now:()=>time,schedule,cancel});
  return {coordinator,socket,presence,now:()=>time,timers,disconnect(){connected=false;},async advance(ms){
    await flush();const target=time+ms;
    for(;;){const next=[...timers].filter(([,value])=>value.at<=target).sort((a,b)=>a[1].at-b[1].at)[0];
      if(!next)break;timers.delete(next[0]);time=next[1].at;next[1].fn();await flush();}
    time=target;await flush();
  }};
}
const options={phone,chatJid:jid,isCurrent:()=>true,action:{type:'text',text:'Olá'}};
const send=(h,extra={},work=()=>{})=>h.coordinator.run({...options,...extra},async({beforeSend})=>{await beforeSend();return work();});

test('deterministic visible-content pacing covers all formats and is bounded; only audio duration changes recording target',()=>{
  assert.equal(outgoingPresenceTarget({type:'text',text:'x'}),800);
  assert.equal(outgoingPresenceTarget({type:'text',text:'x'.repeat(10000)}),3000);
  assert.equal(outgoingPresenceTarget({type:'list',title:'Menu',description:'x'.repeat(100),sections:[]}),1890);
  for(const type of ['url_button','image','document','video','ptv','sticker','location','carousel'])
    assert.ok(outgoingPresenceTarget({type,text:'x'.repeat(500)})<=3000);
  assert.equal(outgoingPresenceTarget({type:'audio',asset:{duration_ms:1000}}),1000);
  assert.equal(outgoingPresenceTarget({type:'audio',asset:{duration_ms:2500}}),2500);
  assert.equal(outgoingPresenceTarget({type:'audio',asset:{duration_ms:15000}}),5000);
  assert.equal(outgoingPresenceTarget({type:'audio',asset:{duration_ms:1800000}}),5000);
});

test('composing precedes a new short send by800ms and always pauses after known completion',async()=>{
  const h=fixture(),relays=[];let checks=0;
  const result=send(h,{assertAuthorized:async()=>{checks++;}},()=>relays.push(h.now()));
  await h.advance(799);assert.equal(relays.length,0);assert.deepEqual(h.presence.map(item=>item.type),['composing']);
  await h.advance(1);await result;assert.deepEqual(relays,[800]);assert.equal(checks,2);
  assert.deepEqual(h.presence.map(item=>item.type),['composing','paused']);assert.equal(h.timers.size,0);
});

test('AI and media preparation count toward the same target without a second delay',async()=>{
  const h=fixture(),lease=h.coordinator.begin(options);await h.advance(2500);await lease.beforeReply('x'.repeat(500));
  let relayed=false;const result=send(h,{action:{type:'text',text:'x'.repeat(500)}},()=>{relayed=true;});
  await h.advance(499);assert.equal(relayed,false);await h.advance(1);await result;assert.equal(h.now(),3000);
  assert.equal(h.presence.filter(item=>item.type==='composing').length,1);lease.stop();
  const media=fixture(),prepared=defer();let sent=false;
  const upload=media.coordinator.run({...options,action:{type:'image'}},async({beforeSend})=>{
    await prepared.promise;await beforeSend();sent=true;
  });
  await media.advance(1500);prepared.resolve();await upload;assert.equal(sent,true);assert.equal(media.now(),1500);
});

test('audio uses SDK recording and all other message kinds use composing, targeted only',async()=>{
  for(const type of ['audio','image','video','ptv','sticker','document','list','url_button']){
    const h=fixture(),result=send(h,{action:{type,asset:{duration_ms:2500}}});await h.advance(type==='audio'?2500:800);await result;
    assert.deepEqual(h.presence.map(item=>item.type),[type==='audio'?'recording':'composing','paused']);
    assert.ok(h.presence.every(item=>item.to===jid));
  }
});

test('new AI preparation cannot replace an active authorized audio recording or receive a late old pause',async()=>{
  const h=fixture(),upload=defer(),events=[];
  const audio=h.coordinator.run({...options,action:{type:'audio',asset:{duration_ms:2500}}},async({beforeSend})=>{
    await upload.promise;await beforeSend();events.push('audio');
  });await flush();assert.deepEqual(h.presence.map(item=>item.type),['recording']);
  await h.advance(1000);const preparation=h.coordinator.begin(options);
  assert.deepEqual(h.presence.map(item=>item.type),['recording']);
  await h.advance(1500);upload.resolve();await audio;
  assert.deepEqual(events,['audio']);assert.deepEqual(h.presence.map(item=>item.type),['recording','paused','composing']);
  const reply=send(h,{},()=>events.push('reply'));await reply;
  assert.deepEqual(events,['audio','reply']);assert.deepEqual(h.presence.map(item=>item.type),['recording','paused','composing','paused']);
  preparation.stop();assert.equal(h.presence.length,4);
});

test('revocation/new inbound, owner loss, shutdown and disconnected socket cancel wait with no relay or stale paused',async()=>{
  for(const reason of ['revoke','disconnect','shutdown']){
    const h=fixture();let current=true,relays=0;
    const result=send(h,{isCurrent:()=>current},()=>relays++);const rejected=assert.rejects(result,/superseded/);
    await h.advance(200);if(reason==='revoke')current=false;else if(reason==='disconnect')h.disconnect();else h.coordinator.close();
    await h.advance(100);await rejected;assert.equal(relays,0);
    assert.deepEqual(h.presence.map(item=>item.type),['composing']);assert.equal(h.timers.size,0);
  }
});

test('authorization failure before presence and after wait never reaches message generation/cache/relay',async()=>{
  for(const failAt of [1,2]){
    const h=fixture();let checks=0,effects=0;
    const result=send(h,{assertAuthorized:async()=>{if(++checks===failAt)throw Error('policy_refused');}},()=>effects++);
    const rejected=assert.rejects(result,/policy_refused/);await h.advance(800);await rejected;
    assert.equal(effects,0);assert.equal(h.presence.length,failAt===1?0:1);assert.equal(h.timers.size,0);
  }
});

test('conversation FIFO is local and cancellation cannot let a third sender overtake the active first sender',async()=>{
  const h=fixture(),release=defer(),events=[];let bCurrent=true;
  const a=send(h,{pacing:false},async()=>{events.push('A-start');await release.promise;events.push('A-finish');});await flush();
  const b=send(h,{pacing:false,isCurrent:()=>bCurrent},()=>events.push('B'));const rejected=assert.rejects(b,/superseded/);
  const c=send(h,{pacing:false},()=>events.push('C'));
  bCurrent=false;await h.advance(100);await rejected;
  await send(h,{phone:other,chatJid:other+'@s.whatsapp.net',pacing:false},()=>events.push('other'));
  assert.deepEqual(events,['A-start','other']);release.resolve();await a;await h.advance(100);await c;
  assert.deepEqual(events,['A-start','other','A-finish','C']);h.coordinator.close();
});

test('a new sender arriving after a cancelled tail still waits for the original active send',async()=>{
  const h=fixture(),release=defer(),events=[];let current=true;
  const a=send(h,{pacing:false},async()=>{events.push('A-start');await release.promise;events.push('A-finish');});await flush();
  const b=send(h,{pacing:false,isCurrent:()=>current});const rejected=assert.rejects(b,/superseded/);
  current=false;await h.advance(100);await rejected;
  const c=send(h,{pacing:false},()=>events.push('C'));await h.advance(100);assert.deepEqual(events,['A-start']);
  release.resolve();await a;await h.advance(100);await c;assert.deepEqual(events,['A-start','A-finish','C']);
});

test('presence heartbeat refreshes at4s, stops at20s, and never delays preparation beyond its target',async()=>{
  const h=fixture(),lease=h.coordinator.begin(options);await h.advance(20000);
  assert.deepEqual(h.presence.map(item=>item.at),[0,4000,8000,12000,16000,20000]);
  assert.equal(h.presence.at(-1).type,'paused');assert.equal(h.timers.size,0);lease.stop();
});

test('missing SDK presence or rejected presence never prevents a message; oldAPI pacing opt-out has no new delay',async()=>{
  for(const mode of ['absent','reject','old-api']){
    const h=fixture();if(mode==='absent')delete h.socket.sendPresenceUpdate;
    if(mode==='reject')h.socket.sendPresenceUpdate=async()=>{throw Error('synthetic presence failure');};
    let relays=0;const result=send(h,{pacing:mode!=='old-api'},()=>relays++);
    await h.advance(mode==='reject'?800:0);await result;assert.equal(relays,1);h.coordinator.close();
  }
});

test('valid queued work is preserved, queue wait counts toward its target, and no second cosmetic delay is added',async()=>{
  const h=fixture(),release=defer();let sentAt=null;
  const a=send(h,{pacing:false},()=>release.promise);await flush();
  const b=send(h,{action:{type:'text',text:'x'.repeat(500)}},()=>{sentAt=h.now();});
  await h.advance(5500);assert.equal(sentAt,null);release.resolve();await a;await h.advance(100);await b;
  assert.equal(sentAt,5600);await flush();
  assert.equal(h.coordinator.diagnostics().conversations,0);
});
