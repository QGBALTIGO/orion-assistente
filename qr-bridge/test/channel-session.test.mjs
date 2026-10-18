import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createBaileysChannelSession} from '../src/channel-session.mjs';

const phone='5567999999901',self='5567999999902',flush=()=>new Promise(r=>setImmediate(r));
async function session(t){
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'orion-channel-socket-'));fs.chmodSync(directory,0o700);
  t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  let alive=true,now=100000;const messages=[],sent=[];let config;
  const client={ev:new EventEmitter(),user:{id:self+'@s.whatsapp.net'},end(){this.ended=true;},
    onWhatsApp:async pn=>[{exists:true,jid:pn+'@s.whatsapp.net'}],sendMessage:async(jid,body)=>{sent.push({jid,body});return {key:{id:'provider-id'}};}};
  const context={directory,secret:'synthetic-secret-'.repeat(4),isCurrent:()=>alive,
    assertCurrent:async()=>{if(!alive)throw Error('lease lost');},allowedSender:value=>value===phone,
    onConnected(){},onDisconnected(){},onReceipt:async()=>{},onMessage:async value=>messages.push(value)};
  const result=await createBaileysChannelSession(context,{now:()=>now,loadBaileys:async()=>({
    default:options=>{config=options;return client;},BufferJSON:{},initAuthCreds:()=>({synthetic:'private-auth'}),
    Browsers:{windows:()=>['synthetic']},DisconnectReason:{loggedOut:401},proto:{Message:{AppStateSyncKeyData:{fromObject:v=>v}}}})});
  t.after(()=>result.close());
  return {result,client,messages,sent,config,revoke:()=>{alive=false;},
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
