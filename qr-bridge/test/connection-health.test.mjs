import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {disconnectPolicy,makeConnectionHealth,runtimeIdentity,sustainedRecovery} from '../src/connection-health.mjs';
import {makeEventBuffer} from '@whiskeysockets/baileys';
import {fenceSignalRepository} from '../src/signal-fence.mjs';

for(const [code,reason,action] of [[401,'logged_out','reauthenticate'],[403,'forbidden','inspect_provider_restriction'],
  [411,'multidevice_mismatch','inspect_client_compatibility'],[440,'connection_replaced','inspect_session_ownership']])test(`disconnect ${code} preserves auth and requires its specific intervention`,()=>{
  assert.deepEqual(disconnectPolicy(code),{code,reason,action,retry:false,delayMs:null});
});
test('a generic stream 500 recovers the existing auth twice and never triggers an automatic new identity',()=>{
  for(const attempts of [0,1]){const policy=disconnectPolicy(500,attempts);assert.equal(policy.retry,true);assert.equal(policy.reason,'session_recovery');}
  assert.deepEqual(disconnectPolicy(500,2),{code:500,reason:'session_recovery_exhausted',action:'inspect_session_state',retry:false,delayMs:null});
});
test('network and service recovery uses bounded jitter and exhausts its finite budget',()=>{
  for(const code of [408,428,503,undefined])for(const attempt of [0,1,7]){
    const policy=disconnectPolicy(code,attempt,{random:()=>0.5});
    assert.equal(policy.retry,true);assert.ok(policy.delayMs>=1000&&policy.delayMs<=30000);assert.equal(policy.action,null);
  }
  assert.equal(disconnectPolicy(515).delayMs,500);
  assert.equal(disconnectPolicy(515,8).retry,false,'Repeated provider restart requests cannot loop forever');
  assert.equal(disconnectPolicy(408,8).reason,'reconnect_exhausted');assert.equal(disconnectPolicy(null).code,null);
});
function fixture(options={}){
  let now=1000,serial=0;const tasks=new Map(),events=[],stale=[];
  const socket=Object.assign(new EventEmitter(),{isOpen:true});
  const health=makeConnectionHealth({now:()=>now,emit:value=>events.push(value),onStale:reason=>stale.push(reason),
    sampleMs:5000,staleMs:90000,schedule:(fn,ms)=>{const id=++serial;tasks.set(id,{fn,at:now+ms});return id;},cancel:id=>tasks.delete(id),...options});
  health.connecting();health.watch(socket);health.connected();
  function step(ms=5000){now+=ms;for(const [id,item] of [...tasks])if(item.at<=now){tasks.delete(id);item.fn();}}
  return {health,socket,events,stale,tasks,step,time:()=>now};
}
test('quiet customer chats stay connected when public transport keepalive frames arrive',()=>{
  const f=fixture();
  for(let index=0;index<120;index++){if(index%5===0)f.socket.emit('message',Buffer.from('synthetic keepalive'));f.step();}
  assert.deepEqual(f.stale,[]);assert.equal(f.health.snapshot().state,'connected');
  assert.equal(f.health.snapshot().transport_observed,true);f.health.stop();
});
test('missing transport observer support is unknown and never grounds a disconnect',()=>{
  const f=fixture();
  // No frame has ever been seen. App-message silence is not a verdict.
  for(let index=0;index<120;index++)f.step();
  assert.deepEqual(f.stale,[]);assert.equal(f.health.snapshot().transport_observed,false);
  assert.equal(f.health.snapshot().state,'connected');f.health.stop();
});
test('a socket no longer open degrades only that session without a customer probe',()=>{
  const a=fixture(),b=fixture();a.socket.isOpen=false;a.step();b.socket.emit('message');b.step();
  assert.deepEqual(a.stale,['socket_not_open']);assert.deepEqual(b.stale,[]);
  assert.equal(a.health.snapshot().state,'degraded');assert.equal(b.health.snapshot().state,'connected');a.health.stop();b.health.stop();
});
test('observed transport silence needs the deadline and two samples before local recovery',()=>{
  const f=fixture();f.socket.emit('message');
  for(let index=0;index<18;index++)f.step();assert.deepEqual(f.stale,[]);
  f.step();assert.deepEqual(f.stale,[]);f.step();assert.deepEqual(f.stale,['transport_unresponsive']);
  assert.equal(f.health.snapshot().reason,'transport_unresponsive');f.health.stop();
});
test('event loop delay gives queued keepalive input a turn and does not reconnect immediately',()=>{
  const f=fixture();f.socket.emit('message');f.step(120000);
  assert.equal(f.health.snapshot().state,'degraded');assert.equal(f.health.snapshot().reason,'event_loop_delayed');assert.deepEqual(f.stale,[]);
  f.socket.emit('message');f.step();assert.equal(f.health.snapshot().state,'connected');assert.deepEqual(f.stale,[]);f.health.stop();
});
test('closing removes passive listeners and timers and late frames cannot revive a retired session',()=>{
  const f=fixture();f.socket.emit('message');const last=f.health.snapshot().last_activity_at;
  f.health.stop('shutdown');assert.equal(f.socket.listenerCount('message'),0);assert.equal(f.socket.listenerCount('pong'),0);assert.equal(f.tasks.size,0);
  f.step(999999);f.socket.emit('message');assert.equal(f.health.snapshot().last_activity_at,last);assert.equal(f.health.snapshot().state,'stopped');
});
test('outage and recovery timestamps survive the next socket while retaining prior cause',()=>{
  const f=fixture();f.health.disconnected(disconnectPolicy(408),1);const outage=f.health.snapshot().interrupted_at;
  f.health.stop();f.step(4000);f.health.connecting();f.health.watch(f.socket);f.health.connected();
  assert.equal(f.health.snapshot().recovered_at,f.time());assert.equal(f.health.snapshot().interrupted_at,null);
  assert.equal(f.events.findLast(event=>event.event==='connected').outage_ms,4000);
  assert.equal(typeof outage,'number');f.health.stop();
});
test('runtime correlation never serializes arbitrary env values, frame bytes or exception contents',()=>{
  const metadata=runtimeIdentity({RAILWAY_GIT_COMMIT_SHA:'a'.repeat(40),RAILWAY_DEPLOYMENT_ID:'10000000-0000-4000-8000-000000000001',
    RAILWAY_REPLICA_ID:'SECRET_TOKEN=do-not-log',QR_BRIDGE_TOKEN:'private secret',PHONE:'123456789'});
  assert.equal(metadata.build_commit,'a'.repeat(40));assert.equal(metadata.deployment_id,'10000000-0000-4000-8000-000000000001');
  assert.doesNotMatch(JSON.stringify(metadata),/SECRET_TOKEN|private secret|123456789/);
  const f=fixture();f.socket.emit('message',Buffer.from('private customer text'));f.health.disconnected(disconnectPolicy(408),1);
  assert.doesNotMatch(JSON.stringify(f.events),/private customer text/);f.health.stop();
});
test('bounded passive samples prove monitoring continues without content, probes or per-frame logs',()=>{
  const f=fixture();
  for(let index=0;index<36;index++){f.socket.emit('message',Buffer.from('private probe-free frame'));f.step();}
  const samples=f.events.filter(event=>event.event==='health_sample');assert.equal(samples.length,3);
  assert.ok(samples.every(event=>event.ws_open===true&&event.ev_buffering===false&&event.state==='connected'));
  assert.doesNotMatch(JSON.stringify(samples),/private probe-free frame/);assert.ok(samples.every(event=>event.rss_bytes>0));f.health.stop();
});
test('fence blocks SDK native encryption and decryption before touching an obsolete owner',async()=>{
  let calls=0;const factory=fenceSignalRepository(()=>({async encryptMessage(){calls++;},async decryptMessage(){calls++;},jidToSignalProtocolAddress:()=> 'pure'}),()=>false);
  const repository=factory();await assert.rejects(repository.encryptMessage({}),/owner_superseded/);await assert.rejects(repository.decryptMessage({}),/owner_superseded/);
  assert.equal(calls,0);assert.equal(repository.jidToSignalProtocolAddress(),'pure');
});
test('a lease lost during asynchronous Signal work suppresses and erases its late bytes',async()=>{
  let alive=true,resolve;const bytes=Buffer.from('synthetic decrypted protocol');
  const repository=fenceSignalRepository(()=>({decryptMessage:()=>new Promise(done=>{resolve=done;})}),()=>alive)();
  const pending=repository.decryptMessage({});alive=false;resolve(bytes);
  await assert.rejects(pending,/owner_superseded/);assert.ok(bytes.every(value=>value===0));
});
test('rapid open/close cycles do not reset retry budgets; sustained raw activity does',()=>{
  assert.equal(sustainedRecovery({connected_at:1000,last_activity_at:2000,transport_observed:true},2000),false);
  assert.equal(sustainedRecovery({connected_at:1000,last_activity_at:62000,transport_observed:true},62000),true);
  assert.equal(sustainedRecovery({connected_at:1000,last_activity_at:62000,transport_observed:false},62000),false);
  assert.equal(sustainedRecovery({connected_at:1000,last_activity_at:2000,transport_observed:true},62000),false);
  assert.equal(sustainedRecovery({connected_at:1000,last_activity_at:62000,transport_observed:true,sync_pending:true},62000),false);
  assert.equal(disconnectPolicy(500,8,{sessionAttempts:0}).retry,false,'Total network budget remains bounded');
});
test('real pinned event buffer can hide inbound messages while WS stays alive; detect without forcing a flush',()=>{
  const quiet={debug(){},trace(){},info(){},warn(){},error(){}};
  const ev=makeEventBuffer(quiet),messages=[],states=[];
  ev.on('messages.upsert',value=>messages.push(value));ev.on('connection.update',value=>states.push(value));
  ev.buffer();ev.emit('connection.update',{connection:'open'});
  ev.emit('messages.upsert',{type:'notify',messages:[{key:{id:'buffered-fixture',remoteJid:'5567999999901@s.whatsapp.net',fromMe:false},message:{conversation:'synthetic input'},messageTimestamp:1}]});
  assert.equal(states.length,1);assert.equal(messages.length,0);assert.equal(ev.isBuffering(),true);
  const f=fixture({isSyncPending:()=>ev.isBuffering()});
  for(let index=0;index<26;index++){f.socket.emit('message');f.step();}
  assert.deepEqual(f.stale,['sync_stalled']);assert.equal(messages.length,0);
  assert.equal(ev.isBuffering(),true,'The application must not force incomplete SDK sync to flush');
  f.health.stop();
  // Only simulate the SDK receiving its missing completion, after diagnosis.
  ev.flush();assert.equal(messages.length,1);assert.equal(messages[0].messages[0].key.id,'buffered-fixture');
});
