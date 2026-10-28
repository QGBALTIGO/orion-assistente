import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createBaileysChannelSession} from '../src/channel-session.mjs';

process.env.ORION_RETRY_TEST_MODE='1';

const phone='5567999999901',self='5567999999902',flush=()=>new Promise(r=>setImmediate(r));
async function session(t,{audio=false,assistant=true,typingFactory,notifySelf=false,schedule,cancel,registered=false,resumeOnly=false}={}){
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'orion-channel-socket-'));fs.chmodSync(directory,0o700);
  t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  let alive=true,now=100000;const messages=[],sent=[],notices=[];let config;
  const client={ev:new EventEmitter(),user:{id:self+'@s.whatsapp.net'},end(){this.ended=true;},
    onWhatsApp:async pn=>[{exists:true,jid:pn+'@s.whatsapp.net'}],sendMessage:async()=>assert.fail('direct sendMessage is forbidden'),
    relayMessage:async(jid,body,options)=>{sent.push({jid,body,options});}};
  const context={directory,secret:'synthetic-secret-'.repeat(4),isCurrent:()=>alive,notifySelf,resumeOnly,newLinkNotice:{pending:false},
    assertCurrent:async()=>{if(!alive)throw Error('lease lost');},allowedSender:value=>value===phone,
    assistantEnabled:()=>assistant,audioEnabled:()=>audio,
    onInbound:async value=>{notices.push(value);return Object.freeze({revision:notices.length});},
    transcribe:async()=> 'Áudio transcrito sintético',
    onConnected(){},onDisconnected(){},onReceipt:async()=>{},onMessage:async value=>messages.push(value)};
  const mod={
    default:options=>{config=options;return client;},BufferJSON:{},initAuthCreds:()=>({synthetic:'private-auth',registered}),
    generateMessageIDV2:()=> 'provider-id',generateWAMessage:async(jid,{text})=>({key:{id:'provider-id',remoteJid:jid},message:{conversation:text}}),
    downloadMediaMessage:async()=>Buffer.from('OggS synthetic media'),
    Browsers:{windows:()=>['synthetic']},DisconnectReason:{loggedOut:401},proto:{Message:{AppStateSyncKeyData:{fromObject:v=>v}}}};
  const open=()=>createBaileysChannelSession(context,{now:()=>now,loadBaileys:async()=>mod,intakeOptions:{settleMs:0,maxGroupMs:0},...(typingFactory?{typingFactory}:{}),...(schedule?{schedule,cancel}:{})});
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

test('a fresh managed link retains its notice intent through the provider restart, without persisting the intent',async t=>{
  const timers=[],s=await session(t,{notifySelf:true,assistant:false,schedule:fn=>{timers.push(fn);return timers.length;},cancel(){}});
  assert.equal(s.context.newLinkNotice.pending,true);
  s.config.auth.creds.registered=true;s.client.ev.emit('creds.update');await flush();s.result.close();
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
  assert.deepEqual(await s.config.getMessage({id:'provider-id'}),s.sent[0].body);
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

test('exact outgoing proto is encrypted before relay and survives a new channel socket',async t=>{
  const s=await session(t);s.client.ev.emit('connection.update',{connection:'open'});
  s.client.relayMessage=async(jid,body,options)=>{
    assert.equal(jid,phone+'@s.whatsapp.net');assert.deepEqual(await s.config.getMessage({id:options.messageId}),body);
    s.sent.push({jid,body,options});
  };
  assert.deepEqual(await s.result.send(phone,'private synthetic payload'),{id:'provider-id'});
  assert.equal(s.sent.length,1);assert.equal(s.sent[0].options.useUserDevicesCache,false);
  const retryDir=path.join(s.context.directory,'retry-proto');
  assert.equal(fs.readdirSync(retryDir).length,1);
  assert.equal(fs.readFileSync(path.join(retryDir,fs.readdirSync(retryDir)[0])).includes(Buffer.from('private synthetic payload')),false);
  s.result.close();assert.equal(await s.config.getMessage({id:'provider-id'}),undefined);
  const reopened=await s.open();t.after(()=>reopened.close());
  assert.deepEqual(await s.config.getMessage({id:'provider-id'}),{conversation:'private synthetic payload'});
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
  assert.deepEqual(await s.config.getMessage({id:'provider-id'}),{conversation:'one attempt'});
});

test('audio records interaction before download/ASR and passes only the bounded transcript',async t=>{
  const s=await session(t,{audio:true}),buffer=Buffer.from('OggS synthetic media'),order=[];
  s.context.onInbound=async value=>{s.notices.push(value);order.push('notice');return {revision:s.notices.length};};
  s.mod.downloadMediaMessage=async()=>{order.push('download');assert.equal(s.notices.length,1);return buffer;};
  s.context.transcribe=async value=>{order.push('asr');assert.equal(value.phone,phone);assert.equal(value.mediaType,'audio/ogg');assert.equal(value.buffer,buffer);return 'Quero corte';};
  s.context.onMessage=async value=>{order.push('forward');s.messages.push(value);};
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

test('closing during audio aborts ASR, erases downloaded bytes and never forwards stale text',async t=>{
  const s=await session(t,{audio:true}),buffer=Buffer.from('OggS bounded'),started=[];
  s.mod.downloadMediaMessage=async()=>buffer;
  s.context.transcribe=async({signal})=>{started.push(signal);return new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(Error('aborted')),{once:true}));};
  s.receive({}, {audioMessage:{fileLength:buffer.length,seconds:1}});await flush();assert.equal(started.length,1);
  s.result.close();await flush();assert.equal(started[0].aborted,true);assert.ok(buffer.every(byte=>byte===0));assert.equal(s.messages.length,0);
});

test('typing is scoped to a verified private sender and always stopped after the reply callback',async t=>{
  const events=[];const s=await session(t,{typingFactory:options=>({begin(meta){assert.equal(options.pilotPhone(),phone);assert.equal(meta.phone,phone);events.push('begin');return {beforeReply:async()=>events.push('reply'),stop:()=>events.push('stop')};}})});
  s.context.onMessage=async(_value,{beforeReply})=>{await beforeReply('answer');events.push('forward-done');};
  s.receive({});await flush();await flush();assert.deepEqual(events,['begin','reply','forward-done','stop']);
});

test('verified LID/device proof belongs to this socket and uses the same reliable proto cache',async t=>{
  const s=await session(t);s.client.ev.emit('connection.update',{connection:'open'});
  s.context.verifiedLid=()=>true;s.client.user={id:self+':4@s.whatsapp.net',lid:'100000000000001:4@lid'};
  s.client.onWhatsApp=async pn=>[{exists:true,jid:pn+'@s.whatsapp.net',lid:'100000000000002@lid'}];s.client.getUSyncDevices=async()=>[];
  await s.result.send(phone,'verified channel');assert.equal(s.sent[0].jid,'100000000000002@lid');assert.equal(s.sent[0].options.useUserDevicesCache,true);
  assert.deepEqual(await s.config.getMessage({id:'provider-id'}),{conversation:'verified channel'});
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
