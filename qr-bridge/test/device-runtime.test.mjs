import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {randomUUID} from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createBaileysChannelSession} from '../src/channel-session.mjs';
import {createChannelManager} from '../src/channel-manager.mjs';
import {createChannelResumeStore} from '../src/channel-resume.mjs';
import {deviceStorage} from '../src/device-storage.mjs';
import {channelDirectory,encryptedChannelStore} from '../src/channel-storage.mjs';
import {createChannelBroker} from '../src/channel-broker.mjs';
import {DEFAULT_CONNECTION_CONFIG,BufferJSON,initAuthCreds} from '@whiskeysockets/baileys';
import {createIngressJournal} from '../src/ingress-journal.mjs';

process.env.ORION_RETRY_TEST_MODE='1';
const phone='5567999999901',self='5567999999902';
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function until(predicate){for(let i=0;i<200;i++){if(predicate())return;await tick();}assert.fail('Expected device pipeline boundary was not reached');}
function deferred(){let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};}
const defaults=JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/subscription_devices_contract.json',import.meta.url),'utf8')).settings_defaults;
function snapshot(){return {id:randomUUID(),generation:randomUUID(),settings_revision:1,settings:structuredClone(defaults),
  capabilities:{proxy:true,webhook:true,transcription_available:true,transcription_max_bytes:25_000_000,transcription_max_seconds:1800,transcription_max_text_chars:120000}};}
function temp(t,prefix){const dir=fs.mkdtempSync(path.join(os.tmpdir(),prefix));fs.chmodSync(dir,0o700);t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));return dir;}

async function fixture(t){
  const root=temp(t,'orion-device-runtime-'),directory=path.join(root,randomUUID()),deviceDirectory=path.join(root,'device');
  fs.mkdirSync(directory,{mode:0o700});fs.mkdirSync(deviceDirectory,{mode:0o700});
  let state=snapshot(),alive=true,serial=0,current,client,config,clock=1000000;
  const events=[],sends=[],finished=[],authorizations=[],audioCalls=[],downloads=[],downloadSources=[],rejections=[],webhooks=[],disconnections=[],tasks=new Map(),permits=new Map(),instances=[];
  const behavior={event:async()=>[],transcribe:async()=>({text:'voz',truncated:false,effects:[]}),send:async()=>{},finish:async()=>{},
    pending:async()=>({effects:[],reconcile:[]}),webhook:async()=>{},lookupCall:async()=>null,prepare:async()=>{},admission:async()=>null,capacity:async()=>{}};
  const context={channelId:randomUUID(),tenantId:randomUUID(),directory,deviceDirectory,secret:'synthetic-device-secret-'.repeat(3),
    deviceSnapshot:()=>state,isCurrent:()=>alive,assertCurrent:async()=>{if(!alive)throw Error('revoked');},
    allowedSender:()=>false,assistantEnabled:()=>false,audioEnabled:()=>false,notifySelf:false,
    onInbound:()=>assert.fail('Device must not reach interaction/journal'),onMessage:()=>assert.fail('Device must not reach AI'),
    onReceipt:()=>assert.fail('Device must not reach legacy receipts'),transcribe:()=>assert.fail('Device must not reach legacy audio'),
    onConnected(){},onDisconnected(policy){disconnections.push(policy);},
    deviceEvent:async payload=>{events.push(payload);return await behavior.admission(payload)||{effects:await behavior.event(payload),duplicate:false};},
    deviceEffectAuthorize:async payload=>{
      authorizations.push(payload);const data=permits.get(payload.operation_id);if(!data)throw Error('not authorized');
      return {authorized:true,...payload,...data};
    },
    deviceEffectFinish:async payload=>{await behavior.finish(payload);finished.push(payload);return {recorded:true};},
    deviceEffectsPending:async()=>behavior.pending(),
    deviceTranscribe:async payload=>{audioCalls.push(payload);return behavior.transcribe(payload);}};
  const schedule=(fn,ms)=>{const id=++serial;tasks.set(id,{fn,ms,at:clock+ms});return id;},cancel=id=>tasks.delete(id);
  async function open(){
    client={ev:new EventEmitter(),user:{id:self+'@s.whatsapp.net',name:'Synthetic business'},end(){this.ended=true;},
      onWhatsApp:async pn=>[{exists:true,jid:pn+'@s.whatsapp.net'}],sendMessage:()=>assert.fail('No direct send'),
      signalRepository:{lidMapping:{getPNForLID:lid=>behavior.lookupCall(lid)}},
      rejectCall:async(id,from)=>{rejections.push({id,from});},logout:async()=>{},
      relayMessage:async(jid,body)=>{sends.push({jid,text:body.conversation});await behavior.send();}};
    const mod={default:options=>{config=options;return client;},DEFAULT_CONNECTION_CONFIG:{version:DEFAULT_CONNECTION_CONFIG.version},BufferJSON,initAuthCreds,
      Browsers:{windows:()=>['synthetic']},DisconnectReason:{loggedOut:401},proto:{Message:{AppStateSyncKeyData:{fromObject:value=>value}}},
      generateMessageIDV2:()=> 'provider-'+(++serial),generateWAMessage:async(jid,{text},options)=>{await behavior.prepare();return {key:{id:options.messageId,remoteJid:jid},message:{conversation:text}};},
      downloadMediaMessage:async message=>{downloadSources.push(message);const buffer=Buffer.from('OggS synthetic audio');downloads.push(buffer);return buffer;}};
    current=await createBaileysChannelSession(context,{loadBaileys:async()=>mod,now:()=>clock,presenceNow:()=>clock,schedule,cancel,
      deviceOptions:{runtimeOptions:{capacity:{acquire:options=>behavior.capacity(options)},postWebhook:async payload=>{webhooks.push(payload);return behavior.webhook(payload);}}}});
    instances.push(current);client.ev.emit('connection.update',{connection:'open'});await until(()=>events.some(e=>e.kind==='status'));return current;
  }
  function effect(type,payload,extra={}){const operation_id=randomUUID();permits.set(operation_id,{type,payload,...extra});return {operation_id,type};}
  function receive(id,body='hello',key={},at=clock){client.ev.emit('messages.upsert',{type:'notify',messages:[{key:{id,fromMe:false,remoteJid:phone+'@s.whatsapp.net',...key},
    messageTimestamp:at/1000,message:typeof body==='string'?{conversation:body}:body,pushName:'Synthetic contact'}]});}
  await open();t.after(()=>instances.forEach(session=>session.close()));
  return {context,get directory(){return context.directory;},get deviceDirectory(){return context.deviceDirectory;},events,sends,finished,authorizations,audioCalls,downloads,downloadSources,rejections,webhooks,disconnections,behavior,effect,receive,open,permits,
    get session(){return current;},get client(){return client;},get config(){return config;},get snapshot(){return state;},
    update(patch={}){state={...structuredClone(state),...patch,settings_revision:state.settings_revision+1};current.deviceSettingsChanged();},
    setTime(value){clock=value;},
    advance(ms){clock+=ms;for(const [id,task]of [...tasks])if(task.at<=clock){tasks.delete(id);void Promise.resolve().then(task.fn).catch(()=>{});}},
    revoke(){alive=false;current.close();},async poll(){const task=[...tasks.values()].find(item=>item.ms===5000);assert.ok(task);await task.fn();}};
}

test('generic send_text negotiates new pacing/recheck without breaking an old API or changing effect IDs',async t=>{
  for(const capable of [false,true]){
    const f=await fixture(t),presence=[];f.context.supportsSendRecheck=()=>capable;
    f.client.sendPresenceUpdate=async type=>{presence.push(type);};
    const item=f.effect('send_text',{phone,text:'Aviso autorizado'});f.behavior.event=async row=>row.kind==='text'?[item]:[];
    f.receive('cap-'+capable);
    if(capable){await until(()=>presence.length===1);assert.equal(f.sends.length,0);f.advance(800);}
    await until(()=>f.finished.length===1);assert.equal(f.sends.length,1);assert.equal(f.finished[0].state,'accepted');
    assert.equal(f.authorizations.some(value=>value.recheck===true),capable);
    assert.ok(f.authorizations.every(value=>value.operation_id===item.operation_id));
    assert.deepEqual(presence,['composing','paused']);
  }
});

test('generic send_text final recheck rejects changed payload and current policy before any relay',async t=>{
  for(const mode of ['payload','policy']){
    const f=await fixture(t),presence=[];f.context.supportsSendRecheck=()=>true;
    f.client.sendPresenceUpdate=async type=>{presence.push(type);};
    const item=f.effect('send_text',{phone,text:'Mesmo aviso'}),authorize=f.context.deviceEffectAuthorize;let changed=false;
    f.context.deviceEffectAuthorize=async payload=>{
      const permit=await authorize(payload);if(changed&&payload.recheck)return mode==='policy'?{authorized:false}:{...permit,payload:{...permit.payload,text:'Alterado'}};
      return permit;
    };
    f.behavior.event=async row=>row.kind==='text'?[item]:[];f.receive('changed-'+mode);await until(()=>presence.length===1);
    changed=true;f.advance(800);await until(()=>f.finished.length===1);
    assert.equal(f.sends.length,0);assert.equal(f.finished[0].state,'not_sent');assert.equal(f.finished[0].operation_id,item.operation_id);
  }
});

test('device ingress has no pilot allowlist, but defaults never invoke AI, ASR, sends or unrelated recipients',async t=>{
  const f=await fixture(t);f.receive('private1');await until(()=>f.events.some(e=>e.message_id==='private1'));
  f.receive('group','hello',{remoteJid:'120363000000000@g.us'});f.receive('self','hello',{remoteJid:self+'@s.whatsapp.net'});
  f.receive('mine','hello',{fromMe:true});f.receive('unknown-lid','hello',{remoteJid:'9999999999999999@lid'});
  f.receive('private1');await tick();await tick();
  assert.equal(f.events.filter(e=>e.kind==='text').length,1);assert.equal(f.sends.length,0);assert.equal(f.downloads.length,0);
  await assert.rejects(f.session.send(phone,'manual bypass'),/effect_grant/);
  const prefix='a'.repeat(99);
  f.client.ev.emit('messages.upsert',{type:'notify',messages:[{key:{id:'unicode-name',fromMe:false,remoteJid:phone+'@s.whatsapp.net'},
    messageTimestamp:1000,message:{conversation:'hello'},pushName:prefix+'🙂 trailing'}]});
  await until(()=>f.events.some(e=>e.message_id==='unicode-name'));
  assert.equal(f.events.find(e=>e.message_id==='unicode-name').name,prefix+'🙂','The schema limit counts Unicode characters, without splitting an emoji');
});
test('subscription offline append recovers a thirty-second outage with a stable dedupe ID and five-minute bound',async t=>{
  const f=await fixture(t);
  const message={key:{id:'offline-during-outage',remoteJid:phone+'@s.whatsapp.net',fromMe:false},messageTimestamp:970,
    message:{conversation:'Solicitação durante a queda'}};
  f.client.ev.emit('messages.upsert',{type:'append',messages:[message]});await until(()=>f.events.some(row=>row.message_id===message.key.id));
  const first=f.events.find(row=>row.message_id===message.key.id);assert.equal(first.occurred_at,new Date(970000).toISOString());
  f.client.ev.emit('messages.upsert',{type:'append',messages:[message,{...message,key:{...message.key,id:'too-old'},messageTimestamp:699}]});
  await tick();await tick();assert.equal(f.events.filter(row=>row.kind==='text').length,1);
  f.session.close();await f.open();f.client.ev.emit('messages.upsert',{type:'append',messages:[message]});
  await until(()=>f.events.filter(row=>row.message_id===message.key.id).length===2);
  assert.equal(f.events.filter(row=>row.message_id===message.key.id)[1].event_id,first.event_id,
    'The existing SQL unique device/provider-event fence receives the same durable identity after restart');
  assert.equal(f.sends.length,0,'Recovery never bypasses backend effect admission');
});

const broker503=()=>Object.assign(Error('synthetic dependency outage'),{status:503});
function storedIngress(f){
  return encryptedChannelStore({directory:incomingDirectory(f),secret:f.context.secret,
    serialize:value=>JSON.stringify(value,BufferJSON.replacer),deserialize:value=>JSON.parse(value,BufferJSON.reviver)}).get('incoming-admission-v1');
}
const incomingDirectory=f=>path.join(f.deviceDirectory,'incoming',path.basename(f.directory));

test('incoming is encrypted before broker admission and retries a 503 with the same event/time/order',async t=>{
  const f=await fixture(t);let unavailable=true;
  f.behavior.event=async event=>{if(event.kind!=='text')return [];
    const retained=storedIngress(f);assert.equal(retained.entries.length,1);assert.equal(retained.entries[0].message.key.id,event.message_id);
    if(unavailable)throw broker503();return [];
  };
  f.receive('durable-503','Private synthetic incoming payload');
  await until(()=>f.events.some(event=>event.message_id==='durable-503'));await tick();await tick();
  assert.equal(f.session.deviceDiagnostics().ingress.pending,1);assert.equal(f.session.connectionHealth().state,'degraded');
  const before=f.events.find(event=>event.message_id==='durable-503');
  for(const file of fs.readdirSync(incomingDirectory(f))){
    assert.equal(fs.readFileSync(path.join(incomingDirectory(f),file)).includes(Buffer.from('Private synthetic incoming payload')),false);
  }
  unavailable=false;await f.poll();await until(()=>f.session.deviceDiagnostics().ingress.pending===0);
  const attempts=f.events.filter(event=>event.message_id==='durable-503');assert.equal(attempts.length,2);
  for(const key of ['event_id','occurred_at','ingress_order'])assert.equal(attempts[1][key],before[key]);
  f.receive('durable-503');await tick();await tick();assert.equal(f.events.filter(event=>event.message_id==='durable-503').length,2);
  assert.equal(f.sends.length,0);assert.equal(f.session.connectionHealth().state,'connected');
});

test('session restart replays retained incoming without another provider upsert',async t=>{
  const f=await fixture(t);f.behavior.event=async event=>{if(event.kind==='text')throw broker503();return [];};
  f.receive('restart-local-incoming');await until(()=>f.events.some(event=>event.message_id==='restart-local-incoming'));await tick();
  const first=f.events.find(event=>event.message_id==='restart-local-incoming');
  f.session.close();f.behavior.event=async()=>[];await f.open();
  await until(()=>f.events.filter(event=>event.message_id==='restart-local-incoming').length===2);
  await until(()=>f.session.deviceDiagnostics().ingress.pending===0);
  const replay=f.events.filter(event=>event.message_id==='restart-local-incoming')[1];
  assert.equal(replay.event_id,first.event_id);assert.equal(replay.occurred_at,first.occurred_at);assert.equal(replay.ingress_order,first.ingress_order);
  assert.equal(f.sends.length,0);
});

test('lost admission response replays the SQL event identity then leaves effects to the existing ledger',async t=>{
  const f=await fixture(t),effect=f.effect('send_text',{phone,text:'synthetic current response'}),committed=new Set();
  f.behavior.admission=async event=>{
    if(event.kind!=='text')return null;
    if(!committed.has(event.event_id)){committed.add(event.event_id);throw Error('synthetic response lost after SQL commit');}
    return {effects:[],duplicate:true};
  };
  f.receive('commit-lost');await until(()=>committed.size===1);await tick();await tick();
  assert.equal(f.session.deviceDiagnostics().ingress.pending,1);assert.equal(f.sends.length,0);
  await f.poll();await until(()=>f.session.deviceDiagnostics().ingress.pending===0);
  assert.equal(committed.size,1);assert.equal(f.events.filter(event=>event.message_id==='commit-lost').length,2);
  f.behavior.pending=async()=>({effects:[effect],reconcile:[]});await f.poll();await until(()=>f.finished.length===1);
  await f.poll();assert.equal(f.sends.length,1);assert.equal(f.session.deviceDiagnostics().ingress.entries,0);
});

test('a failed incoming preserves contact order while another contact continues',async t=>{
  const f=await fixture(t),other='5567999999903',admitted=[];let unavailable=true;
  f.behavior.event=async event=>{if(event.kind!=='text')return [];
    if(event.message_id==='ordered-first'&&unavailable)throw broker503();admitted.push(event.message_id);return [];
  };
  f.receive('ordered-first');f.receive('ordered-second');f.receive('independent-contact','Oi',{remoteJid:other+'@s.whatsapp.net'});
  await until(()=>admitted.includes('independent-contact'));assert.equal(admitted.includes('ordered-second'),false);
  unavailable=false;await f.poll();await until(()=>f.session.deviceDiagnostics().ingress.pending===0);
  assert.ok(admitted.indexOf('ordered-first')<admitted.indexOf('ordered-second'));assert.equal(f.sends.length,0);
});

test('expired retained incoming blocks event-return and polled effects but fresh controls and another device continue',async t=>{
  const f=await fixture(t);f.behavior.event=async event=>{if(event.kind==='text')throw broker503();return [];};
  f.receive('expires-retained','PARAR');await until(()=>f.events.some(event=>event.message_id==='expires-retained'));await tick();
  const original=storedIngress(f).entries[0];f.setTime(1300001);await f.poll();
  await until(()=>f.session.deviceDiagnostics().ingress.blocked===1);
  const retained=storedIngress(f).entries[0];assert.equal(retained.at,original.at);assert.equal(retained.order,original.order);
  assert.equal(retained.blocked_reason,'expired');assert.equal(retained.admission_attempted,true);assert.equal(retained.terminal_inert,false);
  assert.equal(f.session.connectionHealth().state,'needs_attention');
  const effect=f.effect('send_text',{phone,text:'must await operator review'});
  f.behavior.event=async event=>event.kind==='text'?[effect]:[];f.behavior.pending=async()=>({effects:[effect],reconcile:[]});
  f.receive('fresh-control','PARAR');await until(()=>f.events.some(event=>event.message_id==='fresh-control'));
  await until(()=>f.session.deviceDiagnostics().ingress.pending===0);await f.poll();
  assert.equal(f.authorizations.length,0);assert.equal(f.sends.length,0);assert.equal(f.session.deviceDiagnostics().ingress.blocked,1);
  const g=await fixture(t),allowed=g.effect('send_text',{phone,text:'Independent device response'});
  g.behavior.event=async event=>event.kind==='text'?[allowed]:[];g.receive('unaffected-device');await until(()=>g.sends.length===1);
});

test('a resolved greeting that expires before its first broker attempt remains retained but does not stop an independent contact',async t=>{
  const f=await fixture(t),barrier=deferred(),entered=deferred(),original=f.context.assertCurrent,other='5567999999903';let paused=false;
  f.context.assertCurrent=async()=>{await original();if(!paused){paused=true;entered.resolve();await barrier.promise;}};
  f.receive('never-attempted-expired','Olá!');await entered.promise;
  const captured=storedIngress(f).entries[0];assert.equal(captured.admission_attempted,false);
  f.setTime(1300001);barrier.resolve();await until(()=>f.session.deviceDiagnostics().ingress.retained_terminal===1);
  const retained=storedIngress(f).entries[0];
  assert.equal(retained.blocked_reason,'expired');assert.equal(retained.admission_attempted,false);assert.equal(retained.terminal_inert,true);
  assert.equal(retained.at,captured.at);assert.equal(f.events.some(event=>event.message_id==='never-attempted-expired'),false);
  assert.equal(f.session.deviceDiagnostics().ingress.blocked,0);
  const effect=f.effect('send_text',{phone:other,text:'Independent authorized response'});
  f.behavior.event=async event=>event.message_id==='independent-after-expiry'?[effect]:[];
  f.receive('independent-after-expiry','Pedido atual',{remoteJid:other+'@s.whatsapp.net'});await until(()=>f.sends.length===1);
  assert.equal(f.sends[0].jid,other+'@s.whatsapp.net');assert.equal(f.authorizations.length,1);
  f.session.close();await f.open();assert.equal(f.session.deviceDiagnostics().ingress.retained_terminal,1);
  f.receive('never-attempted-expired','replacement');await tick();await tick();
  assert.equal(f.events.some(event=>event.message_id==='never-attempted-expired'),false);
  assert.equal(storedIngress(f).entries[0].at,captured.at);assert.deepEqual(storedIngress(f).entries[0].message,retained.message);
});

for(const pausedGuard of [3,4])test(`expiry while awaiting admission guard${pausedGuard} never marks or posts a first attempt`,async t=>{
  const f=await fixture(t);await tick();await tick();
  const barrier=deferred(),entered=deferred(),original=f.context.assertCurrent,other='5567999999903';let checks=0;
  f.context.assertCurrent=async()=>{await original();if(++checks===pausedGuard){entered.resolve();await barrier.promise;}};
  f.receive('expired-during-final-guard','Olá!');await entered.promise;
  const captured=storedIngress(f).entries[0];assert.equal(captured.terminal_inert,true);assert.equal(captured.admission_attempted,false);
  f.setTime(1300001);barrier.resolve();await until(()=>f.session.deviceDiagnostics().ingress.retained_terminal===1);
  const retained=storedIngress(f).entries[0];assert.equal(retained.admission_attempted,false);assert.equal(retained.blocked_reason,'expired');
  assert.equal(f.events.some(event=>event.message_id==='expired-during-final-guard'),false);
  assert.equal(f.session.deviceDiagnostics().ingress.blocked,0);
  const effect=f.effect('send_text',{phone:other,text:'Current independent response'});
  f.behavior.event=async event=>event.message_id==='after-final-guard'?[effect]:[];
  f.receive('after-final-guard','Pedido atual',{remoteJid:other+'@s.whatsapp.net'});await until(()=>f.sends.length===1);
  assert.equal(f.sends[0].jid,other+'@s.whatsapp.net');assert.equal(f.authorizations.length,1);
});

test('an exact greeting with an ambiguous503 admission still stops other-contact effects after expiry',async t=>{
  const f=await fixture(t),other='5567999999903';
  f.behavior.event=async event=>{if(event.kind==='text'){
    const retained=storedIngress(f).entries.find(entry=>entry.message.key.id===event.message_id);
    assert.equal(retained.admission_attempted,true,'Marker must be on disk before entering the broker adapter');
    throw broker503();
  }return [];};
  f.receive('attempted-greeting','hello');await until(()=>f.events.some(event=>event.message_id==='attempted-greeting'));await tick();
  f.setTime(1300001);await f.poll();await until(()=>f.session.deviceDiagnostics().ingress.blocked===1);
  const retained=storedIngress(f).entries[0];assert.equal(retained.terminal_inert,true);assert.equal(retained.admission_attempted,true);
  const effect=f.effect('send_text',{phone:other,text:'Must wait for review'});
  f.behavior.event=async event=>event.kind==='text'?[effect]:[];f.behavior.pending=async()=>({effects:[effect],reconcile:[]});
  f.receive('blocked-independent','Pedido novo',{remoteJid:other+'@s.whatsapp.net'});
  await until(()=>f.events.some(event=>event.message_id==='blocked-independent'));await tick();await f.poll();
  assert.equal(f.authorizations.length,0);assert.equal(f.sends.length,0);assert.equal(f.session.deviceDiagnostics().ingress.blocked,1);
});

test('a control that expires before any admission attempt keeps automatic effects stopped',async t=>{
  const f=await fixture(t),barrier=deferred(),entered=deferred(),original=f.context.assertCurrent;let paused=false;
  f.context.assertCurrent=async()=>{await original();if(!paused){paused=true;entered.resolve();await barrier.promise;}};
  f.receive('unadmitted-stop','PARAR');await entered.promise;f.setTime(1300001);barrier.resolve();
  await until(()=>f.session.deviceDiagnostics().ingress.blocked===1);
  const retained=storedIngress(f).entries[0];assert.equal(retained.admission_attempted,false);assert.equal(retained.terminal_inert,false);
  assert.equal(f.events.some(event=>event.message_id==='unadmitted-stop'),false);
  const effect=f.effect('send_text',{phone,text:'Must not bypass unrecorded opt-out'});
  f.behavior.pending=async()=>({effects:[effect],reconcile:[]});await f.poll();
  assert.equal(f.authorizations.length,0);assert.equal(f.sends.length,0);
});

test('settings change retains old incoming as blocked and never rebinds it to the new revision',async t=>{
  const f=await fixture(t);f.behavior.event=async event=>{if(event.kind==='text')throw broker503();return [];};
  f.receive('prior-settings');await until(()=>f.events.some(event=>event.message_id==='prior-settings'));await tick();
  const previous=storedIngress(f).entries[0];f.update();
  await until(()=>f.session.deviceDiagnostics().ingress.blocked===1);f.behavior.event=async()=>[];await f.poll();
  assert.equal(f.events.filter(event=>event.message_id==='prior-settings').length,1);
  const retained=storedIngress(f).entries[0];assert.equal(retained.scope.settings_revision,previous.scope.settings_revision);
  assert.equal(retained.blocked_reason,'scope_changed');assert.equal(retained.at,previous.at);
  f.receive('current-settings','PARAR');await until(()=>f.events.some(event=>event.message_id==='current-settings'));
  assert.equal(f.events.find(event=>event.message_id==='current-settings').settings_revision,f.snapshot.settings_revision);
});

test('a retained foreign tenant namespace can never reach the device broker',async t=>{
  const f=await fixture(t);f.session.close();
  const journal=createIngressJournal({directory:incomingDirectory(f),secret:f.context.secret,
    serialize:value=>JSON.stringify(value,BufferJSON.replacer),deserialize:value=>JSON.parse(value,BufferJSON.reviver),now:()=>1000000});
  journal.append({id:'message:foreign-namespace',scope:{tenant_id:randomUUID(),channel_id:f.context.channelId,
    device_id:f.snapshot.id,generation:f.snapshot.generation,settings_revision:f.snapshot.settings_revision},at:1000000,order:1,
    message:{key:{id:'foreign-namespace',fromMe:false,remoteJid:phone+'@s.whatsapp.net'},messageTimestamp:1000,message:{conversation:'foreign synthetic'}}});
  await f.open();await until(()=>f.session.deviceDiagnostics().ingress.blocked===1);
  assert.equal(f.events.some(event=>event.message_id==='foreign-namespace'),false);
  assert.equal(storedIngress(f).entries[0].blocked_reason,'scope_changed');assert.equal(f.authorizations.length,0);
});

test('unresolved identity remains durable and retries after mapping without blocking another contact',async t=>{
  const f=await fixture(t),lid='123456789012345@lid';let mapped=false;
  f.behavior.lookupCall=async()=>mapped?phone+'@s.whatsapp.net':null;
  f.receive('identity-later','Oi',{remoteJid:lid});f.receive('identity-independent','Oi',{remoteJid:'5567999999903@s.whatsapp.net'});
  await until(()=>f.events.some(event=>event.message_id==='identity-independent'));await tick();await tick();
  assert.equal(f.events.some(event=>event.message_id==='identity-later'),false);assert.equal(f.session.deviceDiagnostics().ingress.pending,1);
  mapped=true;await f.poll();await until(()=>f.session.deviceDiagnostics().ingress.pending===0);
  assert.equal(f.events.find(event=>event.message_id==='identity-later').phone,phone);
});

test('invalid broker admission does not acknowledge retained source and a stale-event refusal is retained for review',async t=>{
  const f=await fixture(t);let stale=false;
  f.behavior.admission=async event=>event.kind==='text'?(stale?{effects:[],duplicate:false,ignored:'stale_event'}:{effects:[]}):null;
  f.receive('invalid-admission');await until(()=>f.events.some(event=>event.message_id==='invalid-admission'));await tick();await tick();
  assert.equal(f.session.deviceDiagnostics().ingress.pending,1);stale=true;await f.poll();
  await until(()=>f.session.deviceDiagnostics().ingress.blocked===1);assert.equal(storedIngress(f).entries[0].blocked_reason,'expired');
  assert.equal(f.sends.length,0);
});

test('audio source proto survives encrypted incoming replay before normal ASR authorization',async t=>{
  const f=await fixture(t);f.snapshot.settings.transcription={...f.snapshot.settings.transcription,enabled:true,max_minutes:30,reply_enabled:true};
  const effect=f.effect('transcribe',{phone,message_id:'audio-local-replay',max_bytes:25_000_000,max_seconds:1800});let unavailable=true;
  f.behavior.event=async event=>{if(event.kind!=='audio')return [];if(unavailable)throw broker503();return [effect];};
  f.receive('audio-local-replay',{audioMessage:{fileLength:100,seconds:10,mimetype:'audio/ogg',mediaKey:Buffer.from('synthetic-media-secret')}});
  await until(()=>f.events.some(event=>event.message_id==='audio-local-replay'));await tick();assert.equal(f.downloads.length,0);
  f.session.close();unavailable=false;await f.open();await until(()=>f.audioCalls.length===1);
  assert.equal(Buffer.isBuffer(f.downloadSources[0].message.audioMessage.mediaKey),true);
  assert.equal(f.downloadSources[0].message.audioMessage.mediaKey.toString(),'synthetic-media-secret');
  assert.equal(f.session.deviceDiagnostics().ingress.pending,0);
});

test('audio admission committed with a lost response retains source on the real duplicate-empty-effects contract',async t=>{
  const f=await fixture(t);f.snapshot.settings.transcription={...f.snapshot.settings.transcription,enabled:true,max_minutes:30,reply_enabled:true};
  const committed=new Set();
  f.behavior.admission=async event=>{
    if(event.kind!=='audio')return null;
    if(!committed.has(event.event_id)){committed.add(event.event_id);throw Error('SQL committed; synthetic response lost');}
    // device_event ON CONFLICT returns no source-dependent ASR effect. Its
    // normal effects/pending endpoint deliberately excludes transcribe.
    return {effects:[],duplicate:true};
  };
  f.receive('audio-commit-lost',{audioMessage:{fileLength:100,seconds:10,mimetype:'audio/ogg',mediaKey:Buffer.from('retained-audio-source')}});
  await until(()=>committed.size===1);await tick();await tick();await f.poll();
  await until(()=>f.session.deviceDiagnostics().ingress.blocked===1);
  assert.equal(f.audioCalls.length,0);assert.equal(f.downloads.length,0);assert.equal(committed.size,1);
  let retained=storedIngress(f).entries[0];assert.equal(retained.blocked_reason,'business_started');
  assert.equal(retained.message.message.audioMessage.mediaKey.toString(),'retained-audio-source');
  f.session.close();await f.open();await f.poll();retained=storedIngress(f).entries[0];
  assert.equal(retained.blocked_reason,'business_started');assert.equal(f.events.filter(event=>event.message_id==='audio-commit-lost').length,2);
  assert.equal(f.audioCalls.length,0);assert.equal(f.sends.length,0);
});

test('audio interruption after admission retains source and never restarts ambiguous paid ASR',async t=>{
  const f=await fixture(t);f.snapshot.settings.transcription={...f.snapshot.settings.transcription,enabled:true,max_minutes:30,reply_enabled:true};
  const effect=f.effect('transcribe',{phone,message_id:'audio-business-started',max_bytes:25_000_000,max_seconds:1800}),barrier=deferred();
  f.behavior.event=async event=>event.kind==='audio'?[effect]:[];
  f.behavior.transcribe=async()=>{await barrier.promise;return {text:'synthetic completed speech',truncated:false,effects:[]};};
  f.receive('audio-business-started',{audioMessage:{fileLength:100,seconds:10,mimetype:'audio/ogg',mediaKey:Buffer.from('retain-across-crash')}});
  await until(()=>f.audioCalls.length===1);assert.equal(f.session.deviceDiagnostics().ingress.active,1);
  f.session.close();assert.equal(f.session.deviceDiagnostics().ingress.blocked,1);barrier.resolve();await tick();await tick();
  await f.open();await f.poll();assert.equal(f.audioCalls.length,1);assert.equal(f.session.deviceDiagnostics().ingress.blocked,1);
  assert.equal(storedIngress(f).entries[0].message.message.audioMessage.mediaKey.toString(),'retain-across-crash');
});

test('healthy audio plus text bursts admit following controls before source ASR without quarantining valid audio',async t=>{
  const f=await fixture(t);f.snapshot.settings.transcription={...f.snapshot.settings.transcription,enabled:true,max_minutes:30,reply_enabled:true};
  const effect=f.effect('transcribe',{phone,message_id:'burst-audio',max_bytes:25_000_000,max_seconds:1800});
  f.behavior.event=async event=>event.kind==='audio'?[effect]:[];
  f.receive('burst-audio',{audioMessage:{fileLength:100,seconds:10,mimetype:'audio/ogg'}});f.receive('burst-text','correction');
  f.receive('burst-other','other',{remoteJid:'5567999999903@s.whatsapp.net'});
  await until(()=>f.audioCalls.length===1);await until(()=>f.session.deviceDiagnostics().ingress.entries===0);
  assert.equal(f.events.filter(event=>event.kind==='text').length,2);assert.equal(f.session.deviceDiagnostics().ingress.blocked,0);
  assert.equal(f.session.connectionHealth().state,'connected');
});

test('explicit auth-epoch reset preserves old incoming journal without replaying it under the new login',async t=>{
  const f=await fixture(t);f.session.close();
  const storage=deviceStorage(temp(t,'orion-device-relink-'),f.snapshot,f.context.secret);
  f.context.deviceDirectory=storage.directory;f.context.directory=storage.authDirectory();await f.open();
  f.behavior.event=async event=>{if(event.kind==='text')throw broker503();return [];};
  f.receive('old-login-input');await until(()=>f.events.some(event=>event.message_id==='old-login-input'));await tick();
  const oldDirectory=incomingDirectory(f),oldFiles=fs.readdirSync(oldDirectory),oldAt=storedIngress(f).entries[0].at;
  f.session.close();f.context.directory=storage.reset();assert.notEqual(incomingDirectory(f),oldDirectory);
  assert.equal(fs.existsSync(path.join(oldDirectory,oldFiles[0])),true,'Reset auth must retain the previous admission evidence');
  f.behavior.event=async()=>[];await f.open();await f.poll();await tick();
  assert.equal(f.events.filter(event=>event.message_id==='old-login-input').length,1);
  assert.equal(f.session.deviceDiagnostics().ingress.entries,0);
  const retained=encryptedChannelStore({directory:oldDirectory,secret:f.context.secret,
    serialize:value=>JSON.stringify(value,BufferJSON.replacer),deserialize:value=>JSON.parse(value,BufferJSON.reviver)}).get('incoming-admission-v1');
  assert.equal(retained.entries[0].at,oldAt);assert.equal(retained.entries[0].message.key.id,'old-login-input');
  f.receive('new-login-input');await until(()=>f.events.some(event=>event.message_id==='new-login-input'));
});

test('pending opt-out prevents a previously waiting send from authorizing before replay',async t=>{
  const f=await fixture(t),effect=f.effect('send_text',{phone,text:'must not overtake pending optout'}),barrier=deferred();let unavailable=true,capacityEntered=false;
  f.behavior.capacity=async()=>{capacityEntered=true;await barrier.promise;};
  f.behavior.pending=async()=>({effects:[effect],reconcile:[]});const polling=f.poll();await until(()=>capacityEntered);
  f.behavior.event=async event=>{if(event.kind==='text'&&unavailable)throw broker503();return [];};
  f.receive('pending-optout','PARAR');await until(()=>f.events.some(event=>event.message_id==='pending-optout'));await tick();await tick();
  assert.equal(f.session.deviceDiagnostics().ingress.pending,1);unavailable=false;barrier.resolve();await polling;
  assert.equal(f.authorizations.length,0);assert.equal(f.sends.length,0,'No authorization/send may overtake an unadmitted control');
  f.behavior.pending=async()=>({effects:[],reconcile:[]});await f.poll();await until(()=>f.session.deviceDiagnostics().ingress.pending===0);
  assert.equal(f.sends.length,0);
});

test('a newly blocked incoming fences an already prepared native send before relay',async t=>{
  const f=await fixture(t),effect=f.effect('send_text',{phone:'5567999999903',text:'prepared before review required'}),barrier=deferred();let preparing=false;
  f.behavior.event=async event=>{if(event.message_id==='older-optout')throw broker503();return event.kind==='text'?[effect]:[];};
  f.behavior.prepare=async()=>{preparing=true;await barrier.promise;};
  f.receive('prepared-send','trigger',{remoteJid:'5567999999903@s.whatsapp.net'});await until(()=>preparing);
  f.receive('older-optout','PARAR');await until(()=>f.events.some(event=>event.message_id==='older-optout'));await tick();f.setTime(1300001);await f.poll();
  await until(()=>f.session.deviceDiagnostics().ingress.blocked===1);barrier.resolve();await tick();await tick();
  assert.equal(f.sends.length,0,'Final native relay guard also sees blocked incoming');
});

test('incoming storage failure closes only the native channel with terminal policy before any broker event',async t=>{
  const f=await fixture(t),rename=fs.renameSync;
  fs.renameSync=(from,to)=>{if(to.startsWith(path.join(f.deviceDirectory,'incoming')+path.sep))throw Error('synthetic storage unavailable');return rename(from,to);};
  try{f.receive('cannot-persist');}finally{fs.renameSync=rename;}
  assert.equal(f.client.ended,true);assert.equal(f.events.some(event=>event.message_id==='cannot-persist'),false);
  assert.equal(f.disconnections.at(-1).retry,false);assert.equal(f.disconnections.at(-1).reason,'ingress_storage_unavailable');
  assert.equal(f.session.deviceDiagnostics().ingress_error,'ingress_journal_unavailable');
  assert.equal(f.session.connectionHealth().state,'needs_attention');
});

test('corrupted retained incoming fails boot closed and preserves the encrypted evidence',async t=>{
  const f=await fixture(t);f.behavior.event=async event=>{if(event.kind==='text')throw broker503();return [];};
  f.receive('before-corruption');await until(()=>f.events.some(event=>event.message_id==='before-corruption'));await tick();f.session.close();
  const directory=incomingDirectory(f),file=path.join(directory,fs.readdirSync(directory).find(value=>value.endsWith('.enc')));
  fs.writeFileSync(file,'synthetic-corrupt-retained-record');await assert.rejects(f.open(),/ingress_journal_corrupt/);
  assert.equal(fs.readFileSync(file,'utf8'),'synthetic-corrupt-retained-record');assert.equal(f.client.ended,true);
  assert.equal(f.disconnections.at(-1).retry,false);assert.equal(f.sends.length,0);
});

test('only the server-derived current effect may send outside the pilot policy, once across restart',async t=>{
  const f=await fixture(t),effect=f.effect('send_text',{phone,text:'Mensagem de ausência'});
  f.behavior.event=async e=>e.kind==='text'?[effect]:[];
  f.receive('inbound1');await until(()=>f.finished.some(e=>e.operation_id===effect.operation_id));
  assert.deepEqual(f.sends,[{jid:phone+'@s.whatsapp.net',text:'Mensagem de ausência'}]);
  f.session.close();await f.open();f.receive('inbound1');await until(()=>f.authorizations.length===2);await tick();
  assert.equal(f.sends.length,1);assert.ok(f.finished.every(row=>row.state==='accepted'));
  const forged=f.effect('send_text',{phone,text:'foreign'},{generation:randomUUID()});
  f.behavior.event=async e=>e.kind==='text'?[forged]:[];f.receive('inbound2');await until(()=>f.authorizations.length===3);await tick();
  assert.equal(f.sends.length,1);
});

test('ambiguous WhatsApp sends retain uncertainty and never repeat after process/session replacement',async t=>{
  const f=await fixture(t),effect=f.effect('send_text',{phone,text:'Mensagem sintética'});
  f.behavior.event=async e=>e.kind==='text'?[effect]:[];f.behavior.send=async()=>{throw Error('synthetic timeout after relay');};
  f.receive('uncertain1');await until(()=>f.finished.length===1);assert.equal(f.finished[0].state,'uncertain');
  f.session.close();await f.open();f.receive('uncertain1');await until(()=>f.finished.length===2);
  assert.equal(f.sends.length,1);assert.equal(f.finished[1].state,'uncertain');
});

test('device transcription supports complete long output in independently authorized ordered parts without activating chatbot',async t=>{
  const f=await fixture(t);f.snapshot.settings.transcription={...f.snapshot.settings.transcription,enabled:true,max_minutes:30,reply_enabled:true};
  const text='palavra '.repeat(14000),parts=[];
  for(let i=0;i<text.length;i+=32000)parts.push(f.effect('send_text',{phone,text:text.slice(i,i+32000)}));
  const audio=f.effect('transcribe',{phone,message_id:'voice1',max_bytes:25_000_000,max_seconds:1800});
  f.behavior.event=async e=>e.kind==='audio'?[audio]:[];
  f.behavior.transcribe=async()=>({text,truncated:false,duration_seconds:1800,effects:parts});
  f.receive('voice1',{audioMessage:{fileLength:100,seconds:1800,mimetype:'audio/ogg'}});
  await until(()=>f.sends.length===parts.length&&f.finished.some(e=>e.operation_id===parts.at(-1).operation_id));
  assert.equal(f.audioCalls.length,1);assert.ok(f.sends.map(e=>e.text).join('')===text,'Every transcript character and separator must survive the split');
  assert.ok(f.sends.every(e=>e.text.length<=32000));assert.ok(f.downloads[0].every(n=>n===0));
  assert.equal(f.audioCalls[0].operation_id,audio.operation_id);
});

test('revision change during ASR aborts the old signal, wipes buffers and discards every late reply',async t=>{
  const f=await fixture(t),gate=deferred();f.snapshot.settings.transcription.enabled=true;
  const audio=f.effect('transcribe',{phone,message_id:'voice2',max_bytes:25_000_000,max_seconds:120});
  const reply=f.effect('send_text',{phone,text:'late transcript'});
  f.behavior.event=async e=>e.kind==='audio'?[audio]:[];f.behavior.transcribe=()=>gate.promise;
  f.receive('voice2',{audioMessage:{fileLength:100,seconds:20,mimetype:'audio/ogg'}});await until(()=>f.audioCalls.length===1);
  f.update();assert.equal(f.audioCalls[0].signal.aborted,true);
  gate.resolve({text:'late',truncated:false,effects:[reply]});await until(()=>f.downloads[0].every(n=>n===0));
  assert.equal(f.sends.length,0);
});

test('polling cannot claim an audio effect without its source message or consume the live callback opportunity',async t=>{
  const f=await fixture(t);f.snapshot.settings.transcription.enabled=true;
  const audio=f.effect('transcribe',{phone,message_id:'live-audio',max_bytes:25_000_000,max_seconds:120});
  f.behavior.pending=async()=>({effects:[audio]});await f.poll();assert.equal(f.authorizations.length,0);assert.equal(f.downloads.length,0);
  f.behavior.event=async e=>e.kind==='audio'?[audio]:[];
  f.receive('live-audio',{audioMessage:{fileLength:100,seconds:10,mimetype:'audio/mpeg'}});await until(()=>f.audioCalls.length===1);
  assert.equal(f.audioCalls[0].mediaType,'audio/mpeg');
});

test('call rejection retains the original LID and never guesses a destination for a hidden phone',async t=>{
  const f=await fixture(t);const lid='9999999999999999@lid';
  f.behavior.event=async e=>e.kind==='call'?[f.effect('reject_call',{call_id:e.call_id,call_from:e.call_from})]:[];
  f.client.ev.emit('call',[{status:'offer',id:'hidden-call',from:lid,isGroup:false}]);
  await until(()=>f.rejections.length===1);const event=f.events.find(e=>e.kind==='call');
  assert.equal('phone' in event,false);assert.deepEqual(f.rejections,[{id:'hidden-call',from:lid}]);assert.equal(f.sends.length,0);
  f.client.ev.emit('call',[{status:'offer',id:'hidden-call',from:lid,isGroup:false},{status:'accept',id:'accept-call',from:lid},
    {status:'offer',id:'group-call',from:lid,isGroup:true}]);await tick();assert.equal(f.rejections.length,1);
});

test('webhooks retry only after a fresh server grant; configuration revocation prevents a due attempt',async t=>{
  const f=await fixture(t);f.snapshot.settings.webhook.enabled=true;
  const hook=f.effect('webhook',{url:'https://hooks.example.com',event_id:'fixture-hook',body:{version:1}});
  f.behavior.event=async e=>e.kind==='text'?[hook]:[];f.behavior.webhook=async()=>{throw Error('synthetic external failure');};
  f.receive('webhook1');await until(()=>f.finished.length===1);assert.equal(f.finished[0].state,'failed');assert.equal(f.webhooks.length,1);
  f.behavior.pending=async()=>({effects:[hook]});await f.poll();assert.equal(f.webhooks.length,2);
  f.update({settings:{...f.snapshot.settings,webhook:{...f.snapshot.settings.webhook,enabled:false}}});
  await f.poll();assert.equal(f.webhooks.length,2);
});

test('lost finish acknowledgements reconcile durable accepted receipts without a second send or fresh send authorization',async t=>{
  const f=await fixture(t),effect=f.effect('send_text',{phone,text:'Texto já aceito'});
  f.behavior.event=async e=>e.kind==='text'?[effect]:[];let lose=true;
  f.behavior.finish=async()=>{if(lose)throw Error('lost finish response');};
  f.receive('lost-finish');await until(()=>f.sends.length===1);await tick();await tick();
  assert.equal(f.finished.length,0);lose=false;
  f.behavior.pending=async()=>({effects:[],reconcile:[{...effect,device_id:f.snapshot.id,generation:f.snapshot.generation,settings_revision:1}]});
  await f.poll();assert.equal(f.finished.length,1);assert.equal(f.finished[0].reconcile,true);assert.equal(f.finished[0].state,'accepted');
  assert.equal(f.sends.length,1);assert.equal(f.authorizations.length,1);
});

function managerFixture(t){
  const root=temp(t,'orion-device-control-'),id=randomUUID(),tenant=randomUUID(),key='synthetic-control-key-'.repeat(3);
  const device=snapshot(),made=[],operations=new Map(),finishes=[],leases=[],stored=createChannelResumeStore({root,secret:'synthetic-resume-key-'.repeat(3)});
  let fence=0,leaseOwner=null,removed=false,removing=false,block=false;
  const broker={
    async lease(_id,_key,action,owner,expectedFence){
      leases.push(action);if(_id!==id||_key!==key||block)throw Error('denied');
      if(action==='receipt')return {channel_id:id,tenant_id:tenant,provider:'baileys',read_only:true};
      if(removing||removed)throw Error('normal lease stopped for remove');
      if(action==='claim'){leaseOwner=owner;fence++;}
      if(!['claim','inspect'].includes(action)&&(owner!==leaseOwner||expectedFence!==fence))throw Error('stale');
      return {channel_id:id,tenant_id:tenant,provider:'baileys',owner,fence,expires_in_ms:30000,allowed_senders:[],assistant_enabled:false,device};
    },
    async deviceControlAuthorize(_id,_key,input){
      assert.equal(_id,id);assert.equal(_key,key);const action=operations.get(input.operation_id);if(!action||block)throw Error('denied');
      if(action==='remove')removing=true;
      return {authorized:true,...input,channel_id:id,tenant_id:tenant,action,payload:action==='pair'?{phone}:undefined};
    },
    async deviceControlFinish(_id,_key,payload){finishes.push(payload);if(operations.get(payload.operation_id)==='remove'&&payload.status==='succeeded')removed=true;return {};},
    deviceEvent:async()=>({effects:[]})};
  function manager(){const m=createChannelManager({root,broker,resumeStore:stored,clock:()=>0,schedule:()=>1,cancel:()=>{},
    createSession:async context=>{const item={context,closed:false,logoutCalls:0};made.push(item);
      const session={status:()=>item.closed?'stopped':'connected',phone:()=>self,close(){item.closed=true;},
        async logout({authorize}){await authorize();item.logoutCalls++;},send:()=>assert.fail('No arbitrary send'),
        deviceSettingsChanged(){},deviceDiagnostics:()=>({pending:0})};
      await context.onConnected();return session;}});t.after(()=>m.close());return m;}
  function action(name){const operation_id=randomUUID();operations.set(operation_id,name);return {operation_id,device_id:device.id,generation:device.generation,settings_revision:device.settings_revision,...(name==='pair'?{phone}:{})};}
  return {root,id,tenant,key,device,made,finishes,leases,stored,manager,action,block:()=>{block=true;},get removed(){return removed;}};
}

test('device restart preserves auth epoch, relink rotates it, and remove uses exclusive control even after normal lease revocation',async t=>{
  const f=managerFixture(t),m=f.manager();
  const connect=f.action('connect');assert.equal((await m.deviceControl(f.id,f.key,'connect',connect)).status,'succeeded');
  const original=f.made[0].context.directory;
  await m.deviceControl(f.id,f.key,'connect',connect);assert.equal(f.made.length,1);
  assert.equal((await m.deviceControl(f.id,f.key,'restart',f.action('restart'))).status,'succeeded');assert.equal(f.made[1].context.directory,original);
  assert.equal((await m.deviceControl(f.id,f.key,'relink',f.action('relink'))).status,'succeeded');
  assert.notEqual(f.made[2].context.directory,original);assert.equal(f.made[1].logoutCalls,1);assert.equal(fs.existsSync(original),false);
  const remove=f.action('remove');assert.equal((await m.deviceControl(f.id,f.key,'remove',remove)).status,'succeeded');
  assert.equal(f.made[2].logoutCalls,1);assert.equal(f.removed,true);assert.equal(m.size,0);assert.equal(f.stored.list().length,0);
  const before=f.made.length,receipt=await m.deviceOperation(f.id,f.key,remove.operation_id);
  assert.equal(receipt.status,'succeeded');assert.equal(receipt.device_id,f.device.id);assert.equal(f.made.length,before);
});

test('device resume intent binds logical device and proxy config, never a later device on the reused channel',async t=>{
  const f=managerFixture(t),m=f.manager();await m.deviceControl(f.id,f.key,'connect',f.action('connect'));
  const row=f.stored.list()[0];assert.equal(row.device_id,f.device.id);assert.equal(row.generation,f.device.generation);
  m.close();f.device.id=randomUUID();f.device.generation=randomUUID();
  assert.deepEqual(await f.manager().resume(),{resumed:0});assert.equal(f.made.length,1);assert.equal(f.stored.list().length,0);
});

test('proxy edits close the running socket and cancel restart intent until an explicit connection action',async t=>{
  const f=managerFixture(t),m=f.manager();await m.deviceControl(f.id,f.key,'connect',f.action('connect'));
  f.device.settings.proxy={enabled:true,url:'https://proxy.example.com:8080'};f.device.settings_revision++;
  await assert.rejects(m.deviceStatus(f.id,f.key),/proxy_restart_required/);assert.equal(f.made[0].closed,true);assert.equal(f.stored.list().length,0);
  assert.deepEqual(await f.manager().resume(),{resumed:0});assert.equal(f.made.length,1);
});

test('auth namespaces and explicit deletion cannot touch another device or legacy keys',t=>{
  const root=temp(t,'orion-device-storage-'),tenant=randomUUID(),channel=randomUUID(),base=channelDirectory(root,tenant,channel),key='synthetic-key-'.repeat(4);
  const a=deviceStorage(base,snapshot(),key),b=deviceStorage(base,snapshot(),key),old=a.authDirectory(),other=b.authDirectory();
  fs.writeFileSync(path.join(other,'marker'),'preserve',{mode:0o600});const fresh=a.reset();assert.notEqual(fresh,old);assert.equal(fs.existsSync(old),false);
  a.remove();assert.equal(fs.readFileSync(path.join(other,'marker'),'utf8'),'preserve');
});

test('device audio uses only its fenced private route and own identity headers without QR_AI flags',async()=>{
  const calls=[],id=randomUUID(),key='synthetic-channel-key-'.repeat(3),owner=randomUUID(),device_id=randomUUID(),generation=randomUUID(),operation_id=randomUUID();
  const broker=createChannelBroker({apiUrl:'http://api.railway.internal/',env:{},fetchImpl:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({text:'complete',truncated:false,effects:[]})};}});
  await broker.deviceTranscribe(id,key,{owner,fence:3},{device_id,generation,settings_revision:2,operation_id,phone,buffer:Buffer.from('OggS synthetic'),mediaType:'audio/ogg'});
  assert.equal(calls[0].url.pathname,`/internal/qr/channels/${id}/device/audio`);assert.equal(calls[0].options.redirect,'error');
  assert.equal(calls[0].options.headers['X-Orion-Device-Effect'],operation_id);assert.equal(calls[0].options.headers['X-Orion-Channel-Fence'],'3');
  await broker.deviceEffectsPending(id,key,{owner,fence:3},{device_id,generation,settings_revision:2});
  assert.equal(calls[1].options.method,'GET');assert.equal('body' in calls[1].options,false);
});

function diagnosticEffect(f,{recipient=self,mode='self',...extra}={}){
  return f.effect('send_text',{phone:recipient,text:'Teste confirmado sintético'},
    {purpose:'diagnostic_test',diagnostic_request_id:randomUUID(),confirmed:true,recipient_mode:mode,self_phone:self,...extra});
}
async function pollEffect(f,effect){f.behavior.pending=async()=>({effects:[effect],reconcile:[]});await f.poll();}

test('a confirmed diagnostic effect alone can address the verified current self PN, once across restart',async t=>{
  const f=await fixture(t),effect=diagnosticEffect(f);await pollEffect(f,effect);
  assert.equal(f.sends.length,1);assert.equal(f.sends[0].jid,self+'@s.whatsapp.net');
  assert.equal(f.finished.at(-1).state,'accepted');assert.ok(f.authorizations.filter(value=>value.recheck).length>=2);
  assert.equal(f.authorizations.at(-1).recheck,true);
  const claims=f.authorizations.filter(value=>!value.recheck).length;
  f.session.close();await f.open();await f.poll();assert.equal(f.sends.length,1);assert.equal(f.finished.at(-1).state,'accepted');
  assert.equal(f.authorizations.filter(value=>!value.recheck).length,claims+1,'A new session reconciles the durable reserved effect, without relaying it');
});

test('verified external LID policy preserves only the separately confirmed self diagnostic route',async t=>{
  const f=await fixture(t);f.context.verifiedLid=()=>true;
  f.client.user={id:self+':7@s.whatsapp.net',lid:'100000000000001:7@lid'};
  f.client.getUSyncDevices=()=>assert.fail('Self diagnostic is authorized by the current self grant, never an external recipient proof');
  await pollEffect(f,diagnosticEffect(f));assert.equal(f.sends.length,1);assert.equal(f.sends[0].jid,self+'@s.whatsapp.net');
  assert.equal(f.finished.at(-1).state,'accepted');
  await pollEffect(f,diagnosticEffect(f,{recipient:phone,mode:'explicit'}));
  assert.equal(f.sends.length,1,'An external diagnostic with no LID proof must not fall back to PN');
  assert.notEqual(f.finished.at(-1).state,'accepted');
});

test('ordinary send_text and generic/public send options retain their self-send prohibition',async t=>{
  const f=await fixture(t);await pollEffect(f,f.effect('send_text',{phone:self,text:'ordinary self bypass'}));
  assert.equal(f.sends.length,0);assert.notEqual(f.finished.at(-1).state,'accepted');
  await assert.rejects(f.session.send(self,'forged',{allowSelf:true,diagnostic:{purpose:'diagnostic_test',confirmed:true}}),/effect_grant/);
  assert.equal(f.sends.length,0);
});

test('ordinary text cannot become a self-send if the active PN changes while preparing the message',async t=>{
  const f=await fixture(t);f.behavior.prepare=async()=>{f.client.user.id=phone+'@s.whatsapp.net';};
  await pollEffect(f,f.effect('send_text',{phone,text:'ordinary after identity change'}));assert.equal(f.sends.length,0);
});

for(const [label,extra] of [
  ['unconfirmed',{confirmed:false}],['missing request',{diagnostic_request_id:null}],['missing purpose',{purpose:undefined}],
  ['unknown mode',{recipient_mode:'phone'}],['missing verified self',{self_phone:null}],['wrong self',{self_phone:phone}],
  ['generic explicit self',{recipient_mode:'explicit'}],['another device',{device_id:randomUUID()}],
  ['another generation',{generation:randomUUID()}],['another revision',{settings_revision:2}]
]){
  test('diagnostic message refuses '+label+' before any relay',async t=>{
    const f=await fixture(t);await pollEffect(f,diagnosticEffect(f,extra));
    assert.equal(f.sends.length,0);assert.notEqual(f.finished.at(-1)?.state,'accepted');
  });
}

test('self identity changing during recipient lookup cannot fall through into a regular external send',async t=>{
  for(const mode of ['self','explicit']){
    const f=await fixture(t),recipient=mode==='self'?self:phone;
    f.client.onWhatsApp=async pn=>{f.client.user.id='5567999999903@s.whatsapp.net';return [{exists:true,jid:pn+'@s.whatsapp.net'}];};
    await pollEffect(f,diagnosticEffect(f,{recipient,mode}));assert.equal(f.sends.length,0);assert.equal(f.finished.at(-1).state,'not_sent');
  }
});

test('a LID, missing PN or changed own PN at message construction never grants the self exception',async t=>{
  for(const next of ['9999999999999999@lid',null,'5567999999903@s.whatsapp.net']){
    const f=await fixture(t);f.behavior.prepare=async()=>{f.client.user.id=next;};
    await pollEffect(f,diagnosticEffect(f));assert.equal(f.sends.length,0);assert.equal(f.finished.at(-1).state,'not_sent');
  }
});

test('final SQL recheck after message construction blocks revoked intent immediately before relay',async t=>{
  const f=await fixture(t),original=f.context.deviceEffectAuthorize;let prepared=false,checkedAfterPreparation=false;
  f.behavior.prepare=async()=>{prepared=true;};
  f.context.deviceEffectAuthorize=async input=>{
    if(prepared&&input.recheck){checkedAfterPreparation=true;throw Error('confirmed intent revoked');}
    return original(input);
  };
  await pollEffect(f,diagnosticEffect(f));assert.equal(prepared,true);assert.equal(checkedAfterPreparation,true);
  assert.equal(f.sends.length,0);assert.equal(f.finished.at(-1).state,'not_sent');
});

test('lease/revision changes and altered recheck payloads cannot relay a previously confirmed diagnostic',async t=>{
  for(const phase of ['revision','lease','payload','request','mode']){
    const f=await fixture(t),original=f.context.deviceEffectAuthorize;let prepared=false;
    f.behavior.prepare=async()=>{prepared=true;if(phase==='revision')f.update();if(phase==='lease')f.revoke();};
    f.context.deviceEffectAuthorize=async input=>{
      const result=await original(input);
      if(prepared&&input.recheck){
        if(phase==='payload')result.payload={...result.payload,text:'Changed after confirmation'};
        if(phase==='request')result.diagnostic_request_id=randomUUID();
        if(phase==='mode')result.recipient_mode='explicit';
      }
      return result;
    };
    await pollEffect(f,diagnosticEffect(f));assert.equal(f.sends.length,0,phase);assert.equal(f.finished.at(-1).state,'not_sent',phase);
  }
});

test('confirmed explicit diagnostic preserves target and self identity; timeout after relay stays uncertain without retry',async t=>{
  const f=await fixture(t),effect=diagnosticEffect(f,{recipient:phone,mode:'explicit'});f.behavior.send=async()=>{throw Error('Lost provider acknowledgement');};
  await pollEffect(f,effect);assert.equal(f.sends.length,1);assert.equal(f.sends[0].jid,phone+'@s.whatsapp.net');
  assert.equal(f.finished.at(-1).state,'uncertain');
  f.session.close();await f.open();await f.poll();assert.equal(f.sends.length,1);assert.equal(f.finished.at(-1).state,'uncertain');
});

test('lost diagnostic finish ACK reconciles an accepted receipt without another grant or message',async t=>{
  const f=await fixture(t),effect=diagnosticEffect(f);let lost=true;
  f.behavior.finish=async()=>{if(lost)throw Error('ACK lost');};await pollEffect(f,effect);assert.equal(f.sends.length,1);
  const count=f.authorizations.length;lost=false;
  f.behavior.pending=async()=>({effects:[],reconcile:[{operation_id:effect.operation_id,device_id:f.snapshot.id,
    generation:f.snapshot.generation,settings_revision:f.snapshot.settings_revision}]});
  await f.poll();assert.equal(f.authorizations.length,count);assert.equal(f.sends.length,1);
  assert.equal(f.finished.at(-1).state,'accepted');assert.equal(f.finished.at(-1).reconcile,true);
});
