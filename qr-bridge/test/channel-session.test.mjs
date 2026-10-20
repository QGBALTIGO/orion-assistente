import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createBaileysChannelSession} from '../src/channel-session.mjs';

process.env.ORION_RETRY_TEST_MODE='1';

const phone='5567999999901',self='5567999999902',flush=()=>new Promise(r=>setImmediate(r));
async function session(t,{audio=false,assistant=true,typingFactory}={}){
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'orion-channel-socket-'));fs.chmodSync(directory,0o700);
  t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  let alive=true,now=100000;const messages=[],sent=[],notices=[];let config;
  const client={ev:new EventEmitter(),user:{id:self+'@s.whatsapp.net'},end(){this.ended=true;},
    onWhatsApp:async pn=>[{exists:true,jid:pn+'@s.whatsapp.net'}],sendMessage:async()=>assert.fail('direct sendMessage is forbidden'),
    relayMessage:async(jid,body,options)=>{sent.push({jid,body,options});}};
  const context={directory,secret:'synthetic-secret-'.repeat(4),isCurrent:()=>alive,
    assertCurrent:async()=>{if(!alive)throw Error('lease lost');},allowedSender:value=>value===phone,
    assistantEnabled:()=>assistant,audioEnabled:()=>audio,
    onInbound:async value=>{notices.push(value);return Object.freeze({revision:notices.length});},
    transcribe:async()=> 'Áudio transcrito sintético',
    onConnected(){},onDisconnected(){},onReceipt:async()=>{},onMessage:async value=>messages.push(value)};
  const mod={
    default:options=>{config=options;return client;},BufferJSON:{},initAuthCreds:()=>({synthetic:'private-auth'}),
    generateMessageIDV2:()=> 'provider-id',generateWAMessage:async(jid,{text})=>({key:{id:'provider-id',remoteJid:jid},message:{conversation:text}}),
    downloadMediaMessage:async()=>Buffer.from('OggS synthetic media'),
    Browsers:{windows:()=>['synthetic']},DisconnectReason:{loggedOut:401},proto:{Message:{AppStateSyncKeyData:{fromObject:v=>v}}}};
  const open=()=>createBaileysChannelSession(context,{now:()=>now,loadBaileys:async()=>mod,intakeOptions:{settleMs:0,maxGroupMs:0},...(typingFactory?{typingFactory}:{})});
  const result=await open();
  t.after(()=>result.close());
  return {result,client,messages,sent,notices,context,mod,open,get config(){return config;},setTime:value=>{now=value;},revoke:()=>{alive=false;},
    receive:(key,body={conversation:'hello'},time=100000)=>client.ev.emit('messages.upsert',{type:'notify',messages:[{key:{id:'id-'+Math.random(),remoteJid:phone+'@s.whatsapp.net',...key},message:body,messageTimestamp:time/1000}]})};
}
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
