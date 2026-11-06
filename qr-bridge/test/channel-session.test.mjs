import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {randomUUID,createHash} from 'node:crypto';
import {createBaileysChannelSession} from '../src/channel-session.mjs';
import * as sdk from '@whiskeysockets/baileys';
import {DEFAULT_CONNECTION_CONFIG} from '@whiskeysockets/baileys';
import {encryptedChannelStore} from '../src/channel-storage.mjs';
import {makePairedAuthFixture} from './helpers/paired-auth-fixture.mjs';
import {presenceClock,presenceFlush} from './helpers/presence-clock.mjs';
import {Boom} from '@hapi/boom';

process.env.ORION_RETRY_TEST_MODE='1';
test('provider phone is the confirmed PN only after open; LIDs are never relabeled as phone numbers',async t=>{
  const s=await session(t);assert.equal(s.result.phone(),null);assert.equal(s.result.supportsUrlButton(),true);
  s.client.ev.emit('connection.update',{connection:'open'});await flush();assert.equal(s.result.phone(),self);
  s.client.user.id='123456789:7@lid';assert.equal(s.result.phone(),null);
  s.client.user.id=self+':7@s.whatsapp.net';assert.equal(s.result.phone(),self);
});

const phone='5567999999901',self='5567999999902',flush=()=>new Promise(r=>setImmediate(r));
test('managed close records safe structured SDK evidence without changing the500 recovery policy or sending',async t=>{
  const s=await session(t,{registered:true}),policies=[];s.context.onDisconnected=policy=>policies.push(policy);
  s.client.ev.emit('connection.update',{connection:'open'});await flush();
  s.client.ev.emit('connection.update',{connection:'close',lastDisconnect:{error:new Boom('private exception phone/token',
    {statusCode:500,data:{reason:'500',location:'private peer'}})}});
  assert.equal(policies.length,1);assert.equal(policies[0].reason,'session_recovery');assert.equal(policies[0].retry,true);
  const health=s.result.connectionHealth();assert.equal(health.disconnect_code,500);
  assert.deepEqual(health.disconnect_evidence,{source:'provider_failure',condition:'unknown',provider_reason_numeric:500,websocket_code:null});
  assert.doesNotMatch(JSON.stringify(health),/private exception|private peer/);assert.equal(s.sent.length,0);
});
async function session(t,{audio=false,assistant=true,typingFactory,notifySelf=false,schedule,cancel,registered=false,resumeOnly=false,savedAuth,clock:virtualClock}={}){
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'orion-channel-socket-'));fs.chmodSync(directory,0o700);
  t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  let alive=true,now=100000;const messages=[],sent=[],notices=[];let config;
  const client={ev:new EventEmitter(),user:{id:self+'@s.whatsapp.net'},end(){this.ended=true;},
    onWhatsApp:async pn=>[{exists:true,jid:pn+'@s.whatsapp.net'}],sendMessage:async()=>assert.fail('direct sendMessage is forbidden'),
    relayMessage:async(jid,body,options)=>{sent.push({jid,body,options});}};
  const context={directory,tenantId:randomUUID(),channelId:randomUUID(),secret:'synthetic-secret-'.repeat(4),isCurrent:()=>alive,notifySelf,resumeOnly,newLinkNotice:{pending:false},
    assertCurrent:async()=>{if(!alive)throw Error('lease lost');},allowedSender:value=>value===phone,
    assistantEnabled:()=>assistant,audioEnabled:()=>audio,
    onInbound:async value=>{notices.push(value);return Object.freeze({revision:notices.length});},
    transcribe:async()=> 'Áudio transcrito sintético',
    onConnected(){},onDisconnected(){},onReceipt:async()=>{},onMessage:async value=>{messages.push(value);return {state:'committed_no_reply',not_committed:false};}};
  if(registered||savedAuth){const auth=path.join(directory,'auth');fs.mkdirSync(auth,{mode:0o700});
    const initial=savedAuth||makePairedAuthFixture({phone:self}).creds;if(registered)initial.registered=true;
    encryptedChannelStore({directory:auth,secret:context.secret,serialize:value=>JSON.stringify(value,sdk.BufferJSON.replacer)}).set('creds',initial);}
  const mod={
    ...sdk,
    fetchLatestWaWebVersion:async()=>({version:DEFAULT_CONNECTION_CONFIG.version,isLatest:false}),
    DEFAULT_CONNECTION_CONFIG:{version:DEFAULT_CONNECTION_CONFIG.version},
    default:options=>{config=options;return client;},BufferJSON:sdk.BufferJSON,initAuthCreds:sdk.initAuthCreds,
    generateMessageIDV2:()=> 'provider-id',generateWAMessage:async(jid,{text})=>({key:{id:'provider-id',remoteJid:jid,fromMe:true},message:{conversation:text}}),
    downloadMediaMessage:async()=>Buffer.from('OggS synthetic media'),
    Browsers:{windows:()=>['synthetic']},DisconnectReason:{loggedOut:401},proto:sdk.proto};
  const open=()=>createBaileysChannelSession(context,{now:()=>virtualClock?.now()??now,...(virtualClock?{presenceNow:virtualClock.now}:{}),loadBaileys:async()=>mod,intakeOptions:{settleMs:0,maxGroupMs:0},...(typingFactory?{typingFactory}:{}),...(schedule?{schedule,cancel}:{})});
  const result=await open();
  t.after(()=>result.close());
  return {result,client,messages,sent,notices,context,mod,open,get config(){return config;},setTime:value=>{now=value;},revoke:()=>{alive=false;},
    receive:(key,body={conversation:'hello'},time=100000)=>client.ev.emit('messages.upsert',{type:'notify',messages:[{key:{id:'id-'+Math.random(),remoteJid:phone+'@s.whatsapp.net',...key},message:body,messageTimestamp:time/1000}]})};
}

test('code pairing requires a ready, unregistered channel and never starts or sends a message',async t=>{
  const s=await session(t),requested=[];
  assert.equal(s.result.supportsPairing(),false);
  s.client.requestPairingCode=async number=>{requested.push(number);return 'abcdefgh';};
  assert.equal(s.result.supportsPairing(),true);
  await assert.rejects(s.result.pair(phone),/not_ready/);
  assert.equal(requested.length,0);
  s.client.ev.emit('connection.update',{qr:'synthetic-qr'});
  const value=await s.result.pair(phone);
  assert.deepEqual(value,{status:'pairing',code:'ABCD-EFGH',expires_in_ms:90000});
  assert.deepEqual(requested,[phone]);assert.equal(s.result.status(),'pairing');assert.equal(s.result.qr(),null);
  assert.equal(s.sent.length,0);assert.equal(s.messages.length,0);assert.equal(s.client.ended,undefined);
  assert.equal(JSON.stringify(s.result.status()).includes(value.code),false);
});

test('concurrent retries share the same code and another phone cannot replace an active attempt',async t=>{
  const s=await session(t);let requested=0,resolve;
  s.client.requestPairingCode=async()=>{requested++;return new Promise(done=>{resolve=done;});};
  s.client.ev.emit('connection.update',{qr:'synthetic-qr'});
  const a=s.result.pair(phone),b=s.result.pair(phone);await flush();assert.equal(requested,1);
  await assert.rejects(s.result.pair(self),/in_progress/);
  resolve('1234ABCD');assert.deepEqual(await a,await b);
  s.setTime(110000);assert.deepEqual(await s.result.pair(phone),{status:'pairing',code:'1234-ABCD',expires_in_ms:80000});
  s.client.ev.emit('connection.update',{qr:'rotated-qr'});
  assert.equal(s.result.status(),'pairing');assert.equal(s.result.qr(),null);assert.equal(requested,1);
});

test('expired code is discarded and only an explicit request with a fresh QR can create another',async t=>{
  const s=await session(t);let requested=0;
  s.client.requestPairingCode=async()=>{requested++;return 'ABCD1234';};
  s.client.ev.emit('connection.update',{qr:'synthetic-qr'});await s.result.pair(phone);
  s.setTime(190000);assert.equal(s.result.status(),'pair_expired');assert.equal(s.result.qr(),null);
  await assert.rejects(s.result.pair(phone),/not_ready/);assert.equal(requested,1);
  s.client.ev.emit('connection.update',{qr:'fresh-qr'});assert.equal(s.result.status(),'qr_ready');
  assert.equal((await s.result.pair(self)).code,'ABCD-1234');assert.equal(requested,2);assert.equal(s.sent.length,0);
});

test('uncertain or malformed provider result is not automatically retried before expiry',async t=>{
  for(const raw of [null,'123','ABCD-EFGH\n','ABCD-EFGH extra','<script>']){
    const s=await session(t);let requested=0;
    s.client.requestPairingCode=async()=>{requested++;if(raw===null)throw Error('secret provider failure');return raw;};
    s.client.ev.emit('connection.update',{qr:'synthetic-qr'});
    await assert.rejects(s.result.pair(phone),error=>error.message==='channel_pairing_unavailable');
    assert.equal(s.result.status(),'pair_failed');
    s.client.ev.emit('connection.update',{qr:'rotated-qr'});assert.equal(s.result.status(),'pair_failed');
    await assert.rejects(s.result.pair(phone));assert.equal(requested,1);assert.equal(s.sent.length,0);
    s.setTime(190000);assert.equal(s.result.status(),'pair_expired');
  }
});

test('a provider still pending after expiry blocks duplicate attempts and cannot return a late code',async t=>{
  const s=await session(t);let requested=0,resolve;
  s.client.requestPairingCode=async()=>{requested++;return new Promise(done=>{resolve=done;});};
  s.client.ev.emit('connection.update',{qr:'synthetic-qr'});const pending=s.result.pair(phone);await flush();
  s.setTime(191000);assert.equal(s.result.status(),'pair_expired');
  s.client.ev.emit('connection.update',{qr:'fresh-qr'});
  await assert.rejects(s.result.pair(phone),/expired/);assert.equal(requested,1);
  const settled=assert.rejects(pending);resolve('ABCD1234');await settled;
  assert.equal(s.result.status(),'qr_ready');assert.equal(s.sent.length,0);
});

test('expiry exposes readiness only when a newer internal QR is still fresh',async t=>{
  const s=await session(t);s.client.requestPairingCode=async()=> 'ABCD1234';
  s.client.ev.emit('connection.update',{qr:'initial-qr'});await s.result.pair(phone);
  s.setTime(180000);s.client.ev.emit('connection.update',{qr:'newer-qr'});assert.equal(s.result.status(),'pairing');
  s.setTime(190000);assert.equal(s.result.status(),'qr_ready');assert.equal(s.result.qr().value,'newer-qr');
  assert.equal((await s.result.pair(phone)).expires_in_ms,90000);assert.equal(s.sent.length,0);
});

test('expiry timer clears the code without polling and pairing preserves encrypted auth files',async t=>{
  const timers=new Map();let serial=0;
  const s=await session(t,{schedule:(fn,ms)=>{const id=++serial;timers.set(id,{fn,ms});return id;},cancel:id=>timers.delete(id)});
  s.client.ev.emit('creds.update');await flush();
  const authDir=path.join(s.context.directory,'auth'),before=fs.readdirSync(authDir).map(name=>[name,fs.readFileSync(path.join(authDir,name)).toString('hex')]);
  s.client.requestPairingCode=async()=> 'ABCD1234';s.client.ev.emit('connection.update',{qr:'synthetic-qr'});
  await s.result.pair(phone);assert.equal(timers.size,1);const expiry=[...timers.values()][0];assert.equal(expiry.ms,90000);
  const after=fs.readdirSync(authDir).map(name=>[name,fs.readFileSync(path.join(authDir,name)).toString('hex')]);
  assert.deepEqual(after,before);s.setTime(190000);expiry.fn();assert.equal(timers.size,0);
  assert.equal(s.result.status(),'pair_expired');await assert.rejects(s.result.pair(phone),/not_ready/);assert.equal(s.sent.length,0);
});

test('registered, resumed, closed and invalid-phone sessions cannot request a code or reset auth',async t=>{
  for(const mode of ['registered','connected','resume','closed','invalid']){
    const s=await session(t);let requested=0;
    s.client.requestPairingCode=async()=>{requested++;return 'ABCDEFGH';};
    s.client.ev.emit('connection.update',{qr:'synthetic-qr'});
    if(mode==='registered')s.config.auth.creds.registered=true;
    if(mode==='connected')s.client.ev.emit('connection.update',{connection:'open'});
    if(mode==='resume')s.context.resumeOnly=true;
    if(mode==='closed')s.result.close();
    for(const value of mode==='invalid'?[null,12345678,'+5567999999901','1234567','0123456789',phone+'\n','١٢٣٤٥٦٧٨']: [phone])
      await assert.rejects(s.result.pair(value));
    assert.equal(requested,0);assert.equal(s.sent.length,0);
    if(mode==='registered')assert.equal(s.config.auth.creds.registered,true);
    if(mode!=='closed')assert.equal(s.client.ended,undefined);
  }
});

test('revocation, closing or connection during provider request discards its late code',async t=>{
  for(const mode of ['revoked','closed','connected','registered']){
    const s=await session(t);let resolve;
    s.client.requestPairingCode=()=>new Promise(done=>{resolve=done;});
    s.client.ev.emit('connection.update',{qr:'synthetic-qr'});const pending=s.result.pair(phone);await flush();
    if(mode==='revoked')s.revoke();
    if(mode==='closed')s.result.close();
    if(mode==='connected')s.client.ev.emit('connection.update',{connection:'open'});
    if(mode==='registered')s.config.auth.creds.registered=true;
    resolve('ABCDEFGH');await assert.rejects(pending);assert.equal(s.sent.length,0);
  }
});

for(const resumeOnly of [false,true])test(`registered managed ${resumeOnly?'resume':'explicit start'} never arms a new-link notice, even without any receipt`,async t=>{
  const timers=[],s=await session(t,{notifySelf:true,registered:true,resumeOnly,
    schedule:fn=>{timers.push(fn);return timers.length;},cancel(){}});
  const marker=path.join(s.context.directory,'auth','orion_link_notification.v1.json');
  assert.equal(fs.existsSync(marker),false);s.client.ev.emit('connection.update',{connection:'open'});await flush();await flush();
  assert.equal(timers.length,0,'a resumed account must not even schedule sendConnectionNoticeOnce');
  assert.equal(s.sent.length,0);assert.equal(fs.existsSync(marker),false);assert.equal(s.config.auth.creds.registered,true);
  assert.equal(s.context.newLinkNotice.pending,false);
});

const authHashes=directory=>fs.readdirSync(path.join(directory,'auth')).sort().map(name=>[name,
  createHash('sha256').update(fs.readFileSync(path.join(directory,'auth',name))).digest('hex')]);

for(const resumeOnly of [false,true])test(`actual SDK QR account with registered=false and no LID survives ${resumeOnly?'resume':'start'} without changing encrypted auth or arming a notice`,async t=>{
  const timers=[],creds=makePairedAuthFixture({phone:self}).creds;
  assert.equal(creds.registered,false);assert.equal(creds.me.lid,undefined);
  const s=await session(t,{savedAuth:creds,resumeOnly,notifySelf:true,schedule:fn=>{timers.push(fn);return timers.length;},cancel(){}});
  const before=authHashes(s.context.directory);s.result.close();s.context.resumeOnly=true;
  const reopened=await s.open();t.after(()=>reopened.close());
  assert.deepEqual(authHashes(s.context.directory),before);assert.equal(s.config.auth.creds.registered,false);
  assert.equal(reopened.supportsPairing(),false);s.client.requestPairingCode=()=>assert.fail('Paired auth cannot request a replacement code');
  await assert.rejects(reopened.pair(phone),/pairing_unavailable/);
  s.client.ev.emit('connection.update',{connection:'open'});await flush();await flush();
  assert.equal(timers.length,0);assert.equal(s.sent.length,0);assert.equal(s.context.newLinkNotice.pending,false);
});

test('a provider QR during paired resume stops before exposing a new QR and preserves signed auth',async t=>{
  const s=await session(t,{savedAuth:makePairedAuthFixture({phone:self}).creds,resumeOnly:true}),disconnected=[];
  s.context.onDisconnected=value=>disconnected.push(value);const before=authHashes(s.context.directory);
  s.client.ev.emit('connection.update',{qr:'replacement-qr'});
  assert.equal(s.result.qr(),null);assert.equal(s.result.status(),'stopped');assert.equal(disconnected[0].retry,false);
  assert.equal(disconnected[0].action,'inspect_session_state');assert.deepEqual(authHashes(s.context.directory),before);
  assert.equal(s.sent.length,0);
});

for(const mutation of ['registered_only','missing_identity','tampered_account'])test(`resume rejects ${mutation} without writing auth or constructing another socket`,async t=>{
  const s=await session(t,{savedAuth:makePairedAuthFixture({phone:self}).creds});s.result.close();s.context.resumeOnly=true;
  const creds=s.config.auth.creds;
  if(mutation==='registered_only'){delete creds.me;delete creds.account;creds.registered=true;}
  if(mutation==='missing_identity')delete creds.signedIdentityKey;
  if(mutation==='tampered_account'){
    const signature=typeof creds.account.accountSignature==='string'?Buffer.from(creds.account.accountSignature,'base64'):Buffer.from(creds.account.accountSignature);
    signature[0]^=1;creds.account.accountSignature=signature;
  }
  encryptedChannelStore({directory:path.join(s.context.directory,'auth'),secret:s.context.secret,
    serialize:value=>JSON.stringify(value,sdk.BufferJSON.replacer)}).set('creds',creds);
  const before=authHashes(s.context.directory);s.mod.default=()=>assert.fail('Incomplete auth cannot create a native socket');
  await assert.rejects(s.open(),/channel_auth_unavailable/);assert.deepEqual(authHashes(s.context.directory),before);
});

test('a missing credential record on resume cannot create a new identity',async t=>{
  const s=await session(t);s.result.close();s.context.resumeOnly=true;
  for(const name of fs.readdirSync(path.join(s.context.directory,'auth')))fs.unlinkSync(path.join(s.context.directory,'auth',name));
  s.mod.initAuthCreds=()=>assert.fail('No initAuthCreds on resume');s.mod.default=()=>assert.fail('No new socket');
  await assert.rejects(s.open(),/channel_auth_unavailable/);assert.deepEqual(fs.readdirSync(path.join(s.context.directory,'auth')),[]);
});

test('a provisional native pairing request shares its active code but cannot expose a QR or start a new attempt after expiry',async t=>{
  const s=await session(t);let calls=0;
  s.client.requestPairingCode=async()=>{calls++;Object.assign(s.config.auth.creds,{me:{id:phone+'@s.whatsapp.net',name:'~'},pairingCode:'ABCDEFGH'});return 'ABCDEFGH';};
  s.client.ev.emit('connection.update',{qr:'initial'});const first=await s.result.pair(phone);
  assert.equal(s.result.supportsPairing(),false);assert.deepEqual(await s.result.pair(phone),first);
  s.setTime(191000);assert.equal(s.result.qr(),null);await assert.rejects(s.result.pair(phone));assert.equal(calls,1);
});

test('a fresh managed link retains its notice intent through the provider restart, without persisting the intent',async t=>{
  const timers=[],s=await session(t,{notifySelf:true,assistant:false,schedule:fn=>{timers.push(fn);return timers.length;},cancel(){}});
  assert.equal(s.context.newLinkNotice.pending,true);
  Object.assign(s.config.auth.creds,makePairedAuthFixture({phone:self}).creds,{registered:true});s.client.ev.emit('creds.update');await flush();s.result.close();
  const reopened=await s.open();t.after(()=>reopened.close());
  assert.equal(s.config.auth.creds.registered,true);s.client.ev.emit('connection.update',{connection:'open'});await flush();
  assert.equal(timers.length,1);await timers.shift()();assert.equal(s.sent.length,1);assert.equal(s.context.newLinkNotice.pending,false);
  s.result.close();reopened.close();s.context.newLinkNotice={pending:false};
  const boot=await s.open();t.after(()=>boot.close());
  s.client.ev.emit('connection.update',{connection:'open'});await flush();assert.equal(timers.length,0);assert.equal(s.sent.length,1);
});

test('managed connection notice goes only to socket self, caches its exact proto and is not repeated after restart',async t=>{
  const timers=[],s=await session(t,{notifySelf:true,assistant:false,schedule:fn=>{timers.push(fn);return timers.length;},cancel(){}});
  assert.equal(s.sent.length,0);s.client.ev.emit('connection.update',{connection:'open'});await flush();
  s.client.ev.emit('connection.update',{connection:'open'});await flush();assert.equal(timers.length,1);
  await timers.shift()();assert.equal(s.sent.length,1);assert.equal(s.sent[0].jid,self+'@s.whatsapp.net');
  assert.match(s.sent[0].body.conversation,/Dispositivo Conectado/);
  assert.deepEqual(await s.config.getMessage({id:'provider-id',remoteJid:self+'@s.whatsapp.net',fromMe:true}),s.sent[0].body);
  await assert.rejects(s.result.send(self,'cannot send arbitrary self message'));
  s.result.close();const reopened=await s.open();t.after(()=>reopened.close());
  s.client.ev.emit('connection.update',{connection:'open'});await flush();await timers.shift()();
  assert.equal(s.sent.length,1);assert.equal(s.messages.length,0);
});

test('revoked connection cannot send a delayed notice; restart with no registered credentials never creates a socket',async t=>{
  const timers=[],s=await session(t,{notifySelf:true,schedule:fn=>{timers.push(fn);return 1;},cancel(){}});
  s.client.ev.emit('connection.update',{connection:'open'});await flush();s.revoke();await timers[0]();assert.equal(s.sent.length,0);
  const empty=await session(t);empty.result.close();empty.context.resumeOnly=true;
  empty.mod.default=()=>assert.fail('unlinked device must not open a socket at restart');
  await assert.rejects(empty.open(),/channel_auth_unavailable/);
});

test('failed connection bookkeeping reports disconnection instead of leaving a stopped socket in a live manager',async t=>{
  const s=await session(t),events=[];
  s.context.onConnected=()=>{throw Error('synthetic persistence failure');};
  s.context.onDisconnected=reason=>events.push(reason);
  s.client.ev.emit('connection.update',{connection:'open'});await flush();await flush();
  assert.equal(s.result.status(),'stopped');assert.deepEqual(events,['disconnected']);assert.equal(s.sent.length,0);
});
test('actual socket factory uses per-channel auth and refuses group, self, history and unapproved sender',async t=>{
  const s=await session(t);
  s.receive({remoteJid:'120000@g.us',participantPn:phone+'@s.whatsapp.net'});
  s.receive({remoteJid:self+'@s.whatsapp.net'});s.receive({remoteJid:'5567999999903@s.whatsapp.net'});
  s.receive({}, {conversation:'old'},1000);s.receive({}, {audioMessage:{url:'never-download'}});
  await flush();await flush();assert.equal(s.messages.length,0);
  s.receive({});await flush();await flush();assert.equal(s.messages.length,1);
  assert.equal(s.config.syncFullHistory,false);assert.equal(s.config.shouldSyncHistoryMessage(),false);
});
test('recent managed offline append from before reconnect is consumed once while old history stays excluded',async t=>{
  const s=await session(t);s.setTime(1000000);
  const message={key:{id:'recovered-managed',remoteJid:phone+'@s.whatsapp.net',fromMe:false},messageTimestamp:970,
    message:{conversation:'Pedido recebido durante a interrupção'}};
  s.client.ev.emit('messages.upsert',{type:'append',messages:[message]});await flush();await flush();
  assert.equal(s.messages.length,1);assert.equal(s.messages[0].message_id,'recovered-managed');
  s.client.ev.emit('messages.upsert',{type:'append',messages:[message,{...message,key:{...message.key,id:'too-old'},messageTimestamp:99}]});
  await flush();await flush();assert.equal(s.messages.length,1);
});

test('exact outgoing proto is encrypted before relay and survives a new channel socket',async t=>{
  const s=await session(t);s.client.ev.emit('connection.update',{connection:'open'});
  s.client.relayMessage=async(jid,body,options)=>{
    assert.equal(jid,phone+'@s.whatsapp.net');assert.deepEqual(await s.config.getMessage({id:options.messageId,remoteJid:jid,fromMe:true}),body);
    s.sent.push({jid,body,options});
  };
  assert.deepEqual(await s.result.send(phone,'private synthetic payload'),{id:'provider-id'});
  assert.equal(s.sent.length,1);assert.equal(s.sent[0].options.useUserDevicesCache,false);
  const retryDir=path.join(s.context.directory,'retry-proto');
  assert.equal(fs.readdirSync(retryDir).length,1);
  assert.equal(fs.readFileSync(path.join(retryDir,fs.readdirSync(retryDir)[0])).includes(Buffer.from('private synthetic payload')),false);
  s.result.close();assert.equal(await s.config.getMessage({id:'provider-id',remoteJid:phone+'@s.whatsapp.net',fromMe:true}),undefined);
  const reopened=await s.open();t.after(()=>reopened.close());
  assert.deepEqual(await s.config.getMessage({id:'provider-id',remoteJid:phone+'@s.whatsapp.net',fromMe:true}),{conversation:'private synthetic payload'});
});

test('a different channel cannot recover another channels proto even with the same message id',async t=>{
  const a=await session(t),b=await session(t);a.client.ev.emit('connection.update',{connection:'open'});
  await a.result.send(phone,'private channel A');assert.equal(await b.config.getMessage({id:'provider-id'}),undefined);
  a.setTime(100000+48*60*60*1000+1);assert.equal(await a.config.getMessage({id:'provider-id'}),undefined);
});

test('cache failure or revocation during preparation prevents the first network relay',async t=>{
  const s=await session(t);s.client.ev.emit('connection.update',{connection:'open'});
  s.mod.generateWAMessage=async()=>({key:{id:'oversize'},message:{conversation:'x'.repeat(100000)}});
  await assert.rejects(s.result.send(phone,'hello'));assert.equal(s.sent.length,0);
  s.mod.generateWAMessage=async()=>{s.revoke();return {key:{id:'revoked'},message:{conversation:'hello'}};};
  await assert.rejects(s.result.send(phone,'hello'));assert.equal(s.sent.length,0);
});

test('an ambiguous relay is attempted once with the prepared proto retained for Signal retry',async t=>{
  const s=await session(t);s.client.ev.emit('connection.update',{connection:'open'});let attempts=0;
  s.client.relayMessage=async()=>{attempts++;throw Error('uncertain relay');};
  await assert.rejects(s.result.send(phone,'one attempt'));assert.equal(attempts,1);
  assert.deepEqual(await s.config.getMessage({id:'provider-id',remoteJid:phone+'@s.whatsapp.net',fromMe:true}),{conversation:'one attempt'});
});

test('audio records interaction before download/ASR and passes only the bounded transcript',async t=>{
  const s=await session(t,{audio:true}),buffer=Buffer.from('OggS synthetic media'),order=[];
  s.context.onInbound=async value=>{s.notices.push(value);order.push('notice');return {revision:s.notices.length};};
  s.mod.downloadMediaMessage=async()=>{order.push('download');assert.equal(s.notices.length,1);return buffer;};
  s.context.transcribe=async value=>{order.push('asr');assert.equal(value.phone,phone);assert.equal(value.mediaType,'audio/ogg');assert.equal(value.buffer,buffer);return 'Quero corte';};
  s.context.onMessage=async value=>{order.push('forward');s.messages.push(value);return {state:'committed_no_reply',not_committed:false};};
  s.receive({}, {audioMessage:{fileLength:buffer.length,seconds:1,mimetype:'audio/ogg'}});
  await flush();await flush();assert.deepEqual(order,['notice','download','asr','forward']);
  assert.equal(s.messages[0].text,'Quero corte');assert.ok(buffer.every(byte=>byte===0));
});

test('assistant off, audio off and invalid audio still notice but never download or infer',async t=>{
  for(const options of [{audio:true,assistant:false},{audio:false},{audio:true}]){
    const s=await session(t,options);s.mod.downloadMediaMessage=async()=>assert.fail('must not download');
    s.context.transcribe=async()=>assert.fail('must not transcribe');
    s.receive({}, {audioMessage:{fileLength:50,seconds:options.assistant===false||options.audio===false?1:121,mimetype:'audio/ogg'}});
    await flush();await flush();assert.equal(s.notices.length,1);assert.equal(s.messages.length,0);
  }
});

test('unapproved audio and failed interaction notices never reach media processing',async t=>{
  const s=await session(t,{audio:true});s.mod.downloadMediaMessage=async()=>assert.fail('must not download');
  s.receive({remoteJid:'5567999999903@s.whatsapp.net'},{audioMessage:{fileLength:20,seconds:1}});
  await flush();assert.equal(s.notices.length,0);
  s.context.onInbound=async()=>{throw Error('notice refused');};
  s.receive({}, {audioMessage:{fileLength:20,seconds:1}});await flush();await flush();assert.equal(s.messages.length,0);
});

function replayClock(){let serial=0;const tasks=new Map();return {tasks,
  schedule:(fn,ms)=>{const id=++serial;tasks.set(id,{fn,ms});return id;},cancel:id=>tasks.delete(id),
  async replay(){const found=[...tasks.values()].find(task=>task.ms===5000);assert.ok(found,'Pending admission arms a bounded recovery timer');await found.fn();for(let i=0;i<12;i++)await flush();}};}
async function reached(predicate){for(let i=0;i<200;i++){if(predicate())return;await flush();}assert.fail('Expected managed admission boundary');}
function storedAssistantIngress(s){
  const base=path.join(s.context.directory,'incoming-assistant'),namespace=fs.readdirSync(base)[0];
  return encryptedChannelStore({directory:path.join(base,namespace),secret:s.context.secret,
    serialize:value=>JSON.stringify(value,sdk.BufferJSON.replacer),deserialize:value=>JSON.parse(value,sdk.BufferJSON.reviver)}).get('incoming-admission-v1');
}

test('managed notice 503 persists encrypted input and retries original payload only before preparation',async t=>{
  const clock=replayClock(),s=await session(t,clock);let unavailable=true,attempts=0;
  s.context.onInbound=async value=>{attempts++;s.notices.push(value);
    assert.equal(storedAssistantIngress(s).entries.find(entry=>entry.id===value.message_id).admission_attempted,true,
      'Attempt evidence must be durable before the authenticated backend adapter runs');
    if(unavailable)throw Object.assign(Error('synthetic API503'),{status:503});return {revision:1};};
  s.receive({id:'notice-503'},{conversation:'Private original synthetic body'});await reached(()=>attempts===1);await flush();await flush();
  assert.equal(s.messages.length,0);assert.equal(s.result.ingressDiagnostics().pending,1);
  const base=path.join(s.context.directory,'incoming-assistant'),namespace=fs.readdirSync(base)[0],file=path.join(base,namespace,fs.readdirSync(path.join(base,namespace))[0]);
  assert.equal(fs.readFileSync(file).includes(Buffer.from('Private original synthetic body')),false);
  s.receive({id:'notice-503'},{conversation:'changed duplicate must not replace'});unavailable=false;await clock.replay();
  await reached(()=>s.result.ingressDiagnostics().entries===0);assert.equal(attempts,2);assert.equal(s.messages.length,1);
  assert.equal(s.messages[0].text,'Private original synthetic body');assert.equal(s.notices[1].text,'Private original synthetic body');
});

test('managed identity lookup expiring before a first notice retains an inert greeting without blocking fresh work',async t=>{
  const s=await session(t);let release,entered;const waiting=new Promise(resolve=>{entered=resolve;}),barrier=new Promise(resolve=>{release=resolve;});
  const original=s.context.assertCurrent;let paused=false;
  s.context.assertCurrent=async()=>{await original();if(!paused){paused=true;entered();await barrier;}};
  s.receive({id:'never-noticed',fromMe:false},{conversation:'oi'});await waiting;s.setTime(100000+900001);release();
  await reached(()=>s.result.ingressDiagnostics().retained_terminal===1);
  assert.equal(s.notices.length,0);assert.equal(s.result.ingressDiagnostics().blocked,0);
  const retained=storedAssistantIngress(s).entries[0];assert.equal(retained.admission_attempted,false);assert.equal(retained.terminal_inert,true);
  s.receive({id:'fresh-managed',fromMe:false},{conversation:'Pedido atual'},100000+900001);
  await reached(()=>s.messages.length===1);assert.equal(s.messages[0].message_id,'fresh-managed');
  assert.equal(s.result.ingressDiagnostics().retained_terminal,1);assert.equal(storedAssistantIngress(s).entries[0].id,'never-noticed');
});

test('managed malformed notice proof is never admitted or prepared and can recover with a typed revision',async t=>{
  const clock=replayClock(),s=await session(t,clock);let valid=false,asr=0;
  s.context.onInbound=async()=>valid?{revision:1}:{recorded:true};s.context.transcribe=async()=>{asr++;return 'synthetic';};
  s.receive({id:'malformed-proof'},{conversation:'No inference until revision proof'});await flush();await flush();
  assert.equal(s.result.ingressDiagnostics().pending,1);assert.equal(s.messages.length,0);assert.equal(asr,0);
  valid=true;await clock.replay();await reached(()=>s.messages.length===1);assert.equal(s.result.ingressDiagnostics().entries,0);
});

test('managed pending notice recovers on restart without another provider upsert and keeps original timestamp',async t=>{
  const clock=replayClock(),s=await session(t,clock);let unavailable=true;
  s.context.onInbound=async value=>{s.notices.push(value);if(unavailable)throw Error('synthetic unavailable');return {revision:1};};
  s.receive({id:'notice-restart'},{conversation:'kept across restart'});await reached(()=>s.notices.length===1);await flush();await flush();
  assert.equal(s.result.ingressDiagnostics().pending,1);s.result.close();s.setTime(110000);unavailable=false;
  const reopened=await s.open();t.after(()=>reopened.close());s.client.ev.emit('connection.update',{connection:'open'});
  await reached(()=>s.messages.length===1);assert.equal(s.messages[0].message_id,'notice-restart');
  assert.equal(reopened.ingressDiagnostics().entries,0);
});

test('managed assistant begun before ASR retains uncertain source and never replays paid preparation after restart',async t=>{
  const clock=replayClock(),s=await session(t,{...clock,audio:true});let asr=0;
  s.context.transcribe=async()=>{asr++;throw Error('synthetic unknown ASR response');};
  s.receive({id:'started-audio'},{audioMessage:{fileLength:20,seconds:1,mimetype:'audio/ogg'}});
  await reached(()=>s.result.ingressDiagnostics()?.blocked===1);assert.equal(asr,1);assert.equal(s.messages.length,0);
  s.result.close();const reopened=await s.open();t.after(()=>reopened.close());s.client.ev.emit('connection.update',{connection:'open'});
  for(let i=0;i<10;i++)await flush();assert.equal(asr,1);assert.equal(reopened.ingressDiagnostics().blocked,1);assert.equal(s.sent.length,0);
});

for(const [label,outcome] of [['undefined',undefined],['null',null],['empty',{}],['unaccepted',{accepted:false}]])
test('managed assistant '+label+' business outcome remains reviewable instead of fabricated completion',async t=>{
  const s=await session(t);s.context.onMessage=async()=>outcome;s.receive({id:'uncertain-'+label});
  await reached(()=>s.result.ingressDiagnostics()?.blocked===1);assert.equal(s.result.ingressDiagnostics().entries,1);
  assert.equal(s.sent.length,0);
});

test('managed known policy skip after admitted safety notice completes without business replay',async t=>{
  const s=await session(t);s.context.onMessage=async()=>({state:'policy_skipped',not_committed:true});s.receive({id:'policy-known'});
  await reached(()=>s.result.ingressDiagnostics()?.entries===0);assert.equal(s.notices.length,1);assert.equal(s.sent.length,0);
});

test('managed pending identity resolves on recovery while healthy other contacts do not require review',async t=>{
  const clock=replayClock(),s=await session(t,clock),other='5567999999903';let mapped=false;
  s.context.allowedSender=value=>[phone,other].includes(value);
  s.client.signalRepository={lidMapping:{getPNForLID:async()=>mapped?phone+'@s.whatsapp.net':null}};
  s.receive({id:'lid-pending',remoteJid:'100000000000001@lid'});await flush();await flush();
  assert.equal(s.result.ingressDiagnostics().pending,1);assert.equal(s.notices.length,0);
  mapped=true;await clock.replay();await reached(()=>s.messages.length===1);
  s.receive({id:'contact-a'});s.receive({id:'contact-b',remoteJid:other+'@s.whatsapp.net'});
  await reached(()=>s.messages.length===3);assert.equal(s.result.ingressDiagnostics().blocked,0);assert.equal(s.result.ingressDiagnostics().entries,0);
});

test('managed old physical account journal stays isolated when authenticated own PN changes',async t=>{
  const clock=replayClock(),s=await session(t,clock);let unavailable=true;
  s.context.onInbound=async value=>{s.notices.push(value);if(unavailable)throw Error('synthetic notice outage');return {revision:s.notices.length};};
  s.receive({id:'prior-self'});await reached(()=>s.notices.length===1);await flush();await flush();s.result.close();
  const base=path.join(s.context.directory,'incoming-assistant'),oldNamespace=fs.readdirSync(base)[0];
  s.client.user.id='5567999999904@s.whatsapp.net';unavailable=false;const reopened=await s.open();t.after(()=>reopened.close());
  s.client.ev.emit('connection.update',{connection:'open'});for(let i=0;i<8;i++)await flush();
  assert.equal(s.notices.length,1);assert.equal(s.messages.length,0);assert.equal(fs.existsSync(path.join(base,oldNamespace)),true);
  assert.equal(reopened.ingressDiagnostics().entries,0);s.receive({id:'current-self'});await reached(()=>s.messages.length===1);
});

test('managed expired pending notice stays blocked for review with no backend retry or timestamp rewrite',async t=>{
  const clock=replayClock(),s=await session(t,clock);s.context.onInbound=async()=>{throw Error('synthetic503');};
  s.receive({id:'expires-original'});await reached(()=>s.result.ingressDiagnostics()?.pending===1);await flush();await flush();
  s.setTime(100000+900001);await clock.replay();assert.equal(s.result.ingressDiagnostics().pending,0);
  assert.equal(s.result.ingressDiagnostics().blocked,1);assert.equal(s.result.ingressDiagnostics().oldest_at,100000);assert.equal(s.messages.length,0);
});

test('managed storage failure stops native channel before notice and does not arm reconnection',async t=>{
  const s=await session(t),rename=fs.renameSync,policies=[];s.context.onDisconnected=policy=>policies.push(policy);
  fs.renameSync=(from,to)=>{if(to.includes(path.sep+'incoming-assistant'+path.sep))throw Error('synthetic full storage');return rename(from,to);};
  try{s.receive({id:'cannot-journal'});}finally{fs.renameSync=rename;}
  assert.equal(s.client.ended,true);assert.equal(s.notices.length,0);assert.equal(policies[0].retry,false);
  assert.equal(s.result.connectionHealth().state,'needs_attention');assert.equal(s.result.connectionHealth().ingress_storage_unavailable,true);
});

test('managed pending notice prevents external automatic relay after asynchronous recipient lookup',async t=>{
  const clock=replayClock(),s=await session(t,clock);s.client.ev.emit('connection.update',{connection:'open'});await flush();
  let release,lookup=false;const gate=new Promise(resolve=>{release=resolve;});
  s.client.onWhatsApp=async pn=>{lookup=true;await gate;return [{exists:true,jid:pn+'@s.whatsapp.net'}];};
  const sending=s.result.send(phone,'synthetic appointment notice',{source:'appointment'});await reached(()=>lookup);
  s.context.onInbound=async()=>{throw Error('synthetic notice503');};s.receive({id:'stop-before-external-relay'},{conversation:'PARAR'});
  await reached(()=>s.result.ingressDiagnostics()?.pending===1);await flush();await flush();release();
  await assert.rejects(sending,/operation_superseded/);assert.equal(s.sent.length,0);assert.equal(s.result.ingressDiagnostics().pending,1);
});

test('managed existing review journal is loaded before the first connected snapshot or external automatic send',async t=>{
  const s=await session(t);s.context.onMessage=async()=>undefined;s.receive({id:'review-before-open'});
  await reached(()=>s.result.ingressDiagnostics()?.blocked===1);s.result.close();
  const reopened=await s.open();t.after(()=>reopened.close());s.client.ev.emit('connection.update',{connection:'open'});
  const immediate=reopened.connectionHealth();assert.equal(immediate.state,'needs_attention');assert.equal(immediate.ingress_blocked,1);
  await assert.rejects(reopened.send(phone,'must remain paused',{source:'automation'}),/operation_superseded/);assert.equal(s.sent.length,0);
});

test('managed corrupt retained admission cannot publish a healthy open or reconnect without review',async t=>{
  const clock=replayClock(),s=await session(t,clock),policies=[];s.context.onDisconnected=policy=>policies.push(policy);
  s.context.onInbound=async()=>{throw Error('synthetic notice503');};s.receive({id:'corrupt-before-open'});
  await reached(()=>s.result.ingressDiagnostics()?.pending===1);await flush();await flush();s.result.close();
  const base=path.join(s.context.directory,'incoming-assistant'),namespace=fs.readdirSync(base)[0],file=path.join(base,namespace,fs.readdirSync(path.join(base,namespace))[0]);
  fs.writeFileSync(file,'synthetic invalid encrypted evidence');const reopened=await s.open();t.after(()=>reopened.close());
  s.client.ev.emit('connection.update',{connection:'open'});assert.equal(s.client.ended,true);
  assert.equal(reopened.connectionHealth().state,'needs_attention');assert.equal(policies.at(-1).retry,false);
  assert.equal(fs.readFileSync(file,'utf8'),'synthetic invalid encrypted evidence');assert.equal(s.sent.length,0);
});

test('closing during audio aborts ASR, erases downloaded bytes and never forwards stale text',async t=>{
  const s=await session(t,{audio:true}),buffer=Buffer.from('OggS bounded'),started=[];
  s.mod.downloadMediaMessage=async()=>buffer;
  s.context.transcribe=async({signal})=>{started.push(signal);return new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(Error('aborted')),{once:true}));};
  s.receive({}, {audioMessage:{fileLength:buffer.length,seconds:1}});await flush();assert.equal(started.length,1);
  s.result.close();await flush();assert.equal(started[0].aborted,true);assert.ok(buffer.every(byte=>byte===0));assert.equal(s.messages.length,0);
});

test('typing is scoped to a verified private sender and always stopped after the reply callback',async t=>{
  const events=[];const s=await session(t,{typingFactory:options=>({begin(meta){assert.equal(options.pilotPhone(),phone);assert.equal(meta.phone,phone);events.push('begin');return {beforeReply:async()=>events.push('reply'),stop:()=>events.push('stop')};}})});
  s.client.ev.emit('connection.update',{connection:'open'});await flush();
  s.context.onMessage=async(_value,{beforeReply})=>{await beforeReply('answer');events.push('forward-done');return {accepted:true};};
  s.receive({});await flush();await flush();assert.deepEqual(events,['begin','reply','forward-done','stop']);
});

test('managed AI preparation and final reliable relay share one presence lease and credit elapsed inference',async t=>{
  const clock=presenceClock(100000),s=await session(t,{clock,schedule:clock.schedule,cancel:clock.cancel,registered:true}),presence=[];
  s.client.sendPresenceUpdate=async(type,jid)=>{presence.push({type,jid,at:clock.now()});};
  s.client.ev.emit('connection.update',{connection:'open'});await flush();
  let release;const inference=new Promise(resolve=>{release=resolve;});
  s.context.onMessage=async(_value,{beforeReply})=>{await inference;await beforeReply('answer');const result=await s.result.send(phone,'answer',{source:'assistant'});return {...result,accepted:true};};
  s.receive({id:'credited-AI'});await reached(()=>presence.length===1);await clock.advance(1200);release();
  await reached(()=>s.sent.length===1);await flush();assert.equal(clock.now(),101200);
  assert.deepEqual(presence.map(value=>value.type),['composing','paused']);assert.equal(s.result.ingressDiagnostics().blocked,0);
});

test('managed connection notice uses composing and bounded pacing solely for the authenticated self account',async t=>{
  const clock=presenceClock(100000),s=await session(t,{clock,schedule:clock.schedule,cancel:clock.cancel,notifySelf:true}),presence=[];
  s.client.sendPresenceUpdate=async(type,jid)=>presence.push({type,jid,at:clock.now()});
  s.client.ev.emit('connection.update',{connection:'open'});await flush();await clock.advance(2500);await flush();
  assert.equal(presence[0]?.type,'composing');assert.equal(s.sent.length,0);
  await clock.advance(3000);await reached(()=>s.sent.length===1);
  assert.ok(presence.every(value=>value.jid===self+'@s.whatsapp.net'));assert.equal(presence.at(-1).type,'paused');
});

test('verified LID/device proof belongs to this socket and uses the same reliable proto cache',async t=>{
  const s=await session(t);s.client.ev.emit('connection.update',{connection:'open'});
  s.context.verifiedLid=()=>true;s.client.user={id:self+':4@s.whatsapp.net',lid:'100000000000001:4@lid'};
  s.client.onWhatsApp=async pn=>[{exists:true,jid:pn+'@s.whatsapp.net',lid:'100000000000002@lid'}];s.client.getUSyncDevices=async()=>[];
  await s.result.send(phone,'verified channel');assert.equal(s.sent[0].jid,'100000000000002@lid');assert.equal(s.sent[0].options.useUserDevicesCache,true);
  assert.deepEqual(await s.config.getMessage({id:'provider-id',remoteJid:'100000000000002@lid',fromMe:true}),{conversation:'verified channel'});
});
test('verified recipient PN required and lease is rechecked after provider lookup',async t=>{
  const s=await session(t);s.client.ev.emit('connection.update',{connection:'open'});
  s.client.onWhatsApp=async()=>[{exists:true,jid:'1234567890@lid'}];
  await assert.rejects(s.result.send(phone,'no'));assert.equal(s.sent.length,0);
  s.client.onWhatsApp=async()=>{s.revoke();return [{exists:true,jid:phone+'@s.whatsapp.net'}];};
  await assert.rejects(s.result.send(phone,'no'));assert.equal(s.sent.length,0);
});
test('expired lease cannot save auth, forward a message or send a reply',async t=>{
  const s=await session(t);s.client.ev.emit('connection.update',{connection:'open'});s.revoke();
  await assert.rejects(s.config.auth.keys.set({session:{synthetic:{secret:'never-write'}}}));
  s.receive({});await flush();assert.equal(s.messages.length,0);
  await assert.rejects(s.result.send(phone,'denied'));assert.equal(s.sent.length,0);
});
test('send acceptance never manufactures a receipt and unknown self identity blocks sending',async t=>{
  const s=await session(t);s.client.ev.emit('connection.update',{connection:'open'});
  assert.deepEqual(await s.result.send(phone,'synthetic text'),{id:'provider-id'});assert.equal(s.sent.length,1);
  s.client.user.id='1234567890@lid';await assert.rejects(s.result.send(phone,'denied'));assert.equal(s.sent.length,1);
});
test('a newer intake during proto generation suppresses relay without weakening the channel lease',async t=>{
  const s=await session(t);s.client.ev.emit('connection.update',{connection:'open'});let latest=true;
  s.mod.generateWAMessage=async()=>{latest=false;return {key:{id:'obsolete'},message:{conversation:'old summary'}};};
  await assert.rejects(s.result.send(phone,'old summary',{isCurrent:()=>latest}));assert.equal(s.sent.length,0);
  assert.equal(s.context.isCurrent(),true);assert.equal(s.result.status(),'connected');
});


test('decorative floods never take an inference slot; a useful caption is preserved',async t=>{
 const s=await session(t);for(let i=0;i<400;i++){s.receive({id:'sticker-'+i},{stickerMessage:{}});s.receive({id:'gif-'+i},{videoMessage:{gifPlayback:true}});}
 s.receive({id:'caption'},{imageMessage:{caption:'Qual o preço do corte?'}});await flush();await flush();
 assert.equal(s.notices.length,1);assert.equal(s.messages.length,1);assert.equal(s.messages[0].text,'Qual o preço do corte?');
});
test('native Baileys edits, revocations and calls forward typed events without ASR',async t=>{
 const s=await session(t);s.context.transcribe=()=>assert.fail('event cannot download or transcribe media');
 const key={id:'original',fromMe:false,remoteJid:phone+'@s.whatsapp.net'};
 s.client.ev.emit('messages.update',[{key,update:{message:{editedMessage:{message:{conversation:'sim'}}}}}]);
 await flush();await flush();assert.equal(s.messages[0].envelope.kind,'edited');assert.equal(s.messages[0].envelope.target,'original');
 s.client.ev.emit('messages.update',[{key,update:{message:null,messageStubType:1}}]);
 await flush();await flush();assert.equal(s.messages[1].envelope.kind,'deleted');
 s.client.ev.emit('call',[{id:'native-call',from:phone+'@s.whatsapp.net',status:'offer',isGroup:false}]);
 await flush();await flush();assert.equal(s.messages[2].envelope.kind,'call');
});
test('bounded ingress overflow retains human control and the final correction without executing a prefix',async t=>{
 const s=await session(t);let release;const barrier=new Promise(r=>release=r),notices=[];
 s.context.onInbound=async value=>{notices.push(value);await barrier;return {revision:notices.indexOf(value)+1};};
 for(let i=0;i<50;i++)s.receive({id:'burst-'+i},{conversation:'Quero marcar '+i});
 s.receive({id:'human-final'},{conversation:'quero falar com atendente'});
 s.receive({id:'correction-final'},{conversation:'não, espera, não confirme nada'});
 await flush();await flush();release();for(let i=0;i<12;i++)await flush();
 assert.ok(notices.some(n=>n.message_id==='human-final'));
 assert.ok(notices.some(n=>n.message_id==='correction-final'));
 assert.equal(s.messages.length,0);assert.equal(s.sent.length,0);
});

test('a single large decorative batch does not discard its final useful message',async t=>{
 const s=await session(t);const messages=Array.from({length:400},(_,i)=>({key:{id:'bulk-sticker-'+i,fromMe:false,remoteJid:phone+'@s.whatsapp.net'},message:{stickerMessage:{}},messageTimestamp:100}));
 messages.push({key:{id:'bulk-final',fromMe:false,remoteJid:phone+'@s.whatsapp.net'},message:{conversation:'Qual o preço do corte?'},messageTimestamp:100});
 s.client.ev.emit('messages.upsert',{type:'notify',messages});await flush();await flush();
 assert.equal(s.messages.length,1);assert.equal(s.messages[0].text,'Qual o preço do corte?');assert.equal(s.notices.length,1);
});

test('creds.update immediately followed by close persists updated keys before reconnect',async t=>{
  const s=await session(t);
  Object.assign(s.config.auth.creds,makePairedAuthFixture({phone:self}).creds);s.config.auth.creds.nextPreKeyId=733;
  s.client.ev.emit('creds.update');s.result.close();
  const reopened=await s.open();t.after(()=>reopened.close());
  assert.equal(s.config.auth.creds.registered,false);
  assert.equal(s.config.auth.creds.nextPreKeyId,733);
});

test('crypto storage does not await a broker roundtrip or lose an accepted key write during close',async t=>{
  const s=await session(t);
  s.context.assertCurrent=()=>new Promise(()=>{});
  const saved=s.config.auth.keys.set({session:{fixture:{ratchet:'accepted'}}});
  s.result.close();await saved;
  s.context.assertCurrent=async()=>{};
  const reopened=await s.open();t.after(()=>reopened.close());
  assert.deepEqual(await s.config.auth.keys.get('session',['fixture']),{fixture:{ratchet:'accepted'}});
});

test('managed encrypted key journal restores the whole interrupted batch',async t=>{
  const s=await session(t),rename=fs.renameSync;
  const {digest}=await import('../src/channel-storage.mjs');
  fs.renameSync=(from,to)=>{if(to.endsWith(digest('key:session:second')+'.enc'))throw Error('synthetic disk fault');return rename(from,to);};
  try{await assert.rejects(s.config.auth.keys.set({session:{first:{ratchet:1},second:{ratchet:2}}}));}
  finally{fs.renameSync=rename;}
  assert.equal(s.result.status(),'stopped');
  const reopened=await s.open();t.after(()=>reopened.close());
  assert.deepEqual(await s.config.auth.keys.get('session',['first','second']),{first:{ratchet:1},second:{ratchet:2}});
});

test('managed keys without their identity cannot create a fresh account',async t=>{
  const s=await session(t);await s.config.auth.keys.set({session:{old:{ratchet:1}}});s.result.close();
  const {digest}=await import('../src/channel-storage.mjs');
  fs.unlinkSync(path.join(s.context.directory,'auth',digest('creds')+'.enc'));
  s.mod.initAuthCreds=()=>assert.fail('must not replace the lost identity');
  await assert.rejects(s.open(),/channel_auth_unavailable/);
});
