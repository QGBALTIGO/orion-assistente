import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {captureManagedRetryMessages,isBridgeRetryMessage} from '../src/managed-retry-capture.mjs';
import {makeEncryptedRetryStore} from '../src/encrypted-retry-store.mjs';
import {readAuthorizedRetry} from '../src/retry-identity.mjs';
import {normalizeChatbotInput} from '../src/chatbot-input.mjs';
import {makeTransportDiagnostics} from '../src/transport-diagnostics.mjs';

process.env.ORION_RETRY_TEST_MODE='1';
const pn='5567999999901@s.whatsapp.net',other='5567999999902@s.whatsapp.net',lid='100000000000001@lid';
const instant=Date.UTC(2026,10,19,14,12),secret='synthetic-managed-retry-only-'.repeat(3);
const own=(id='MANUAL-SELF-TEXT',remoteJid=pn)=>({key:{id,remoteJid,fromMe:true},message:{conversation:'!responder mensagem sintética'},messageTimestamp:instant/1000});
const event=(messages,type='notify')=>({type,messages});
function setup(t){
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'orion-managed-retry-capture-'));
  t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  const options={directory,secret,now:()=>instant};
  const store=makeEncryptedRetryStore(options),diagnostics=[];
  return {store,reopen:()=>makeEncryptedRetryStore(options),diagnostics,options:{store,isCurrent:()=>true,assertCurrent:async()=>{},now:()=>instant,
    record:(stage,id,details)=>diagnostics.push({stage,id,...details})}};
}

test('fresh manual PN text obtains an encrypted retry copy and remains an owner command after restart',async t=>{
  const s=setup(t),message=own();
  assert.deepEqual(await captureManagedRetryMessages(event([message]),s.options),{captured:1,skipped:0,failed:0});
  const reopened=s.reopen();
  assert.deepEqual(await readAuthorizedRetry(reopened,message.key),message.message);
  assert.equal(reopened.entry(message.key).origin,'linked_self');
  assert.equal(isBridgeRetryMessage(reopened,message.key),false);
  assert.equal(normalizeChatbotInput(message,{isGeneratedMessage:key=>isBridgeRetryMessage(reopened,key)}).origin,'owner_command');
  assert.equal(await readAuthorizedRetry(reopened,{...message.key,remoteJid:other}),undefined);
});

test('fresh LID companion echo stays bound to LID and only exact live alias proof authorizes a PN retry',async t=>{
  const s=setup(t),message=own('LID-COMPANION',lid.replace('@',':7@'));
  assert.equal((await captureManagedRetryMessages(event([message],'append'),s.options)).captured,1);
  const reopened=s.reopen(),query={...message.key,remoteJid:pn};
  assert.equal(reopened.entry(message.key).identity.peer,lid);
  assert.equal(await readAuthorizedRetry(reopened,query),undefined);
  assert.equal(await readAuthorizedRetry(reopened,query,{socket:{onWhatsApp:async()=>[{exists:true,jid:other,lid}]}}),undefined);
  assert.deepEqual(await readAuthorizedRetry(reopened,query,{socket:{onWhatsApp:async()=>[{exists:true,jid:pn,lid}]}}),message.message);
});

test('existing generated and manual protos cannot be replaced or have their expiry extended by an echo',async t=>{
  const s=setup(t),message=own();
  assert.equal(s.store.save(message.key,message.message),true);
  const before=s.store.entry(message.key);
  const echo={...message,message:{conversation:'a different echo must not replace the proto'}};
  assert.equal((await captureManagedRetryMessages(event([echo]),s.options)).skipped,1);
  assert.deepEqual(s.store.entry(message.key),before);
  assert.equal(isBridgeRetryMessage(s.store,message.key),true);
  assert.equal(normalizeChatbotInput(message,{isGeneratedMessage:key=>isBridgeRetryMessage(s.store,key)}),null);
  const manual=own('MANUAL-SECOND');
  await captureManagedRetryMessages(event([manual]),s.options);
  const original=s.store.entry(manual.key);
  await captureManagedRetryMessages(event([{...manual,message:{conversation:'replacement'}}]),s.options);
  assert.deepEqual(s.store.entry(manual.key),original);
});

test('a colliding id cannot overwrite a retry copy bound to another recipient',async t=>{
  const s=setup(t),original=own(),collision=own(original.key.id,other);
  s.store.save(original.key,original.message);
  assert.equal((await captureManagedRetryMessages(event([collision]),s.options)).captured,0);
  assert.deepEqual(await readAuthorizedRetry(s.store,original.key),original.message);
  assert.equal(await readAuthorizedRetry(s.store,collision.key),undefined);
  assert.equal(isBridgeRetryMessage(s.store,collision.key),false);
});

test('incoming, ephemeral, view-once, media, protocol and oversized content never gain retry copies',async t=>{
  const s=setup(t),base=own();
  const contents=[{ephemeralMessage:{message:{conversation:'private'}}},{viewOnceMessage:{message:{conversation:'private'}}},
    {imageMessage:{caption:'private'}},{audioMessage:{}},{protocolMessage:{type:0}},
    {conversation:'one',protocolMessage:{}},{conversation:'a'.repeat(1201)},{conversation:'   '},
    {conversation:'one',messageContextInfo:{}},null];
  const messages=contents.map((message,i)=>({...base,key:{...base.key,id:'INELIGIBLE-'+i},message}));
  messages.push({...base,key:{...base.key,id:'INCOMING',fromMe:false}});
  const result=await captureManagedRetryMessages(event(messages),s.options);
  assert.equal(result.captured,0);assert.equal(result.failed,0);
  for(const message of messages)assert.equal(s.store.entry(message.key),undefined);
});

test('unbound, group, broadcast, malformed and invalid-id echoes remain ineligible',async t=>{
  const s=setup(t),base=own();
  const peers=[undefined,'123456789@g.us','status@broadcast','bad@lid','1234567@s.whatsapp.net'];
  const messages=peers.map((remoteJid,i)=>({...base,key:{...base.key,id:'BAD-PEER-'+i,remoteJid}}));
  messages.push({...base,key:{...base.key,id:'../../private'}});
  messages.push({...base,key:{...base.key,id:'BAD-DIRECTION',fromMe:'true'}});
  assert.equal((await captureManagedRetryMessages(event(messages),s.options)).captured,0);
  assert.ok(s.diagnostics.every(row=>row.id===null||/^[A-Za-z0-9._:-]{1,160}$/.test(row.id)));
});

test('stale, missing and far-future timestamps are rejected for notify as well as append',async t=>{
  const s=setup(t),base=own();
  const stamps=[undefined,null,0,'invalid',Infinity,(instant-900001)/1000,(instant+60001)/1000];
  for(const type of ['notify','append']){
    const messages=stamps.map((messageTimestamp,i)=>({...base,key:{...base.key,id:type+'-OLD-'+i},messageTimestamp}));
    assert.equal((await captureManagedRetryMessages(event(messages,type),s.options)).captured,0);
  }
  for(const type of ['history','set',undefined]){
    assert.equal((await captureManagedRetryMessages({type,messages:[base]},s.options)).captured,0);
  }
  assert.equal(s.store.entry(base.key),undefined);
});

test('the existing text policy accepts extended text and fresh append without changing payload',async t=>{
  const s=setup(t),message={...own(),message:{extendedTextMessage:{text:'Texto próprio sintético',contextInfo:{stanzaId:'SYNTHETIC-QUOTE'}}},messageTimestamp:(instant-1000)/1000};
  assert.equal((await captureManagedRetryMessages(event([message],'append'),s.options)).captured,1);
  assert.deepEqual(s.store.get(message.key),message.message);
});

test('missing or rejected authority and a socket revoked during the fence cannot write',async t=>{
  const s=setup(t),message=own();let checks=0;
  const fence=async()=>{checks++;};
  await captureManagedRetryMessages(event([message]),{...s.options,isCurrent:()=>false,assertCurrent:fence});
  assert.equal(checks,0);
  await captureManagedRetryMessages(event([message]),{...s.options,assertCurrent:async()=>{throw Error('private credential error');}});
  let alive=true;
  await captureManagedRetryMessages(event([message]),{...s.options,isCurrent:()=>alive,assertCurrent:async()=>{alive=false;}});
  await captureManagedRetryMessages(event([message]),{...s.options,assertCurrent:undefined});
  assert.equal(s.store.entry(message.key),undefined);
  assert.doesNotMatch(JSON.stringify(s.diagnostics),/private credential error/);
});

test('current ownership is rechecked immediately before each durable write',async t=>{
  const s=setup(t),messages=[own('FIRST'),own('SECOND')];let alive=true,writes=0;
  const store={entry:key=>s.store.entry(key),save(...args){writes++;const result=s.store.save(...args);alive=false;return result;}};
  const result=await captureManagedRetryMessages(event(messages),{...s.options,store,isCurrent:()=>alive});
  assert.deepEqual(result,{captured:1,skipped:1,failed:0});assert.equal(writes,1);
  assert.equal(s.store.entry(messages[1].key),undefined);
});

test('storage rejection is reported without throwing into inbound handling or issuing a relay',async t=>{
  const s=setup(t),message=own();
  for(const save of [()=>false,()=>{throw Error('private storage pathname');}]){
    assert.deepEqual(await captureManagedRetryMessages(event([message]),{...s.options,store:{entry:()=>undefined,save}}),{captured:0,skipped:0,failed:1});
  }
  assert.doesNotMatch(JSON.stringify(s.diagnostics),/private storage pathname/);
});

test('capture diagnostics disclose neither contents nor recipients and logging failures cannot block capture',async t=>{
  const s=setup(t),message=own('SYNTHETIC-PRIVATE-IDENTIFIER'),events=[];
  const diagnostics=makeTransportDiagnostics({secret,emit:value=>events.push(value)});
  await captureManagedRetryMessages(event([message]),{...s.options,record:diagnostics.record});
  const serialized=JSON.stringify(events);
  for(const value of [message.key.id,pn,message.message.conversation,secret])assert.equal(serialized.includes(value),false);
  assert.equal(events[0].category,'captured');assert.match(events[0].trace,/^[a-f0-9]{20}$/);
  const second=own('LOGGING-FAILURE');
  assert.equal((await captureManagedRetryMessages(event([second]),{...s.options,record:()=>{throw Error('logger');}})).captured,1);
});

test('legacy generated records retain their meaning while wrong direction and peer never match',()=>{
  const message=own(),row={message:message.message,identity:{id:message.key.id,peer:pn,fromMe:true}};
  const store={entry:()=>row};
  assert.equal(isBridgeRetryMessage(store,message.key),true);
  assert.equal(isBridgeRetryMessage(store,{...message.key,fromMe:false}),false);
  assert.equal(isBridgeRetryMessage(store,{...message.key,remoteJid:other}),false);
  assert.equal(isBridgeRetryMessage(store,{id:message.key.id}),false);
  assert.equal(isBridgeRetryMessage({entry:()=>({...row,origin:'linked_self'})},message.key),false);
});
