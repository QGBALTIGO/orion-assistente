import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {BufferJSON} from '@whiskeysockets/baileys';
import {createAssistantIngress} from '../src/assistant-ingress.mjs';

const scope={tenant_id:'10000000-0000-4000-8000-000000000001',
  channel_id:'20000000-0000-4000-8000-000000000001',session_digest:'a'.repeat(64)};
function fixture(t,overrides={}){
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'orion-assistant-admission-'));
  t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  const options={directory,secret:'synthetic-admission-key-at-least-32-bytes',scope,isCurrent:()=>true,
    serialize:value=>JSON.stringify(value,BufferJSON.replacer),deserialize:value=>JSON.parse(value,BufferJSON.reviver),...overrides};
  return {options,open:changes=>createAssistantIngress({...options,...changes})};
}
const raw=(id,text='synthetic private message')=>({key:{id,fromMe:false,remoteJid:'5511999999999@s.whatsapp.net'},
  messageTimestamp:1700000000,message:{conversation:text,syntheticBytes:Buffer.from([2,4,8])}});

test('restart with reordered trusted scope keys retains original ID, body, bytes, timestamp and FIFO',t=>{
  const f=fixture(t,{now:()=>1700000010000}),first=f.open();
  first.append(raw('FIRST'),'append',1700000000000);first.append(raw('SECOND'),'notify',1700000001000);
  first.abandon();
  const restored=f.open({scope:{session_digest:scope.session_digest,channel_id:scope.channel_id,tenant_id:scope.tenant_id}});
  const duplicate=restored.append(raw('FIRST','forged replacement'),'notify',1700000009000);
  assert.equal(duplicate.fresh,false);assert.equal(duplicate.entry.at,1700000000000);
  const pending=restored.pending();assert.deepEqual(pending.map(entry=>entry.id),['FIRST','SECOND']);
  assert.equal(pending[0].message.raw.message.conversation,'synthetic private message');
  assert.equal(pending[0].message.raw.messageTimestamp,1700000000);assert.equal(pending[0].message.type,'append');
  assert.deepEqual(pending[0].message.raw.message.syntheticBytes,Buffer.from([2,4,8]));
  const files=fs.readdirSync(f.options.directory,{recursive:true}).filter(name=>name.endsWith('.enc'));
  assert.equal(files.length,1);
  assert.equal(fs.readFileSync(path.join(f.options.directory,files[0])).includes(Buffer.from('synthetic private message')),false);
});

test('pending control and review work block other business, while own live batch can complete',t=>{
  const f=fixture(t,{now:()=>1700000010000}),queue=f.open();
  queue.append(raw('TURN'),'notify',1700000000000);queue.begin(['TURN']);
  assert.equal(queue.canConsume(['TURN']),true);assert.equal(queue.diagnostics().blocked,0);
  queue.append(raw('CONTROL','não responda'),'notify',1700000001000);
  assert.equal(queue.canConsume(['TURN']),false);
  queue.begin(['CONTROL']);assert.equal(queue.canConsume(['TURN','CONTROL']),true);
  queue.review('CONTROL');assert.equal(queue.canConsume(['TURN']),false);
  assert.equal(queue.diagnostics().blocked,1);assert.equal(queue.diagnostics().active,1);
});

test('started preparation survives crash as review and cannot complete or replay in a new instance',t=>{
  const f=fixture(t,{now:()=>1700000010000}),first=f.open();
  first.append(raw('AUDIO'),'notify',1700000000000);first.begin(['AUDIO']);
  first.abandon();const restored=f.open();
  assert.deepEqual(restored.pending(),[]);assert.equal(restored.diagnostics().blocked,1);
  assert.throws(()=>restored.complete('AUDIO'),/not_owned/);assert.equal(restored.canConsume([]),false);
});

test('expired/future source time is quarantined and a provider duplicate cannot refresh it',t=>{
  let at=1700000010000;const f=fixture(t,{now:()=>at,ttlMs:1000}),queue=f.open();
  queue.append(raw('OLD'),'notify',at);at+=1001;
  assert.deepEqual(queue.pending(),[]);assert.equal(queue.diagnostics().blocked,1);
  assert.equal(queue.append(raw('OLD','new text'),'notify',at).entry.at,1700000010000);
  assert.deepEqual(queue.pending(),[]);assert.equal(queue.canConsume([]),false);
});

test('identity lookup crossing TTL can retain an inert greeting without sending a stale first admission',t=>{
  let at=1700000010000;const f=fixture(t,{now:()=>at,ttlMs:1000}),queue=f.open();
  const message={key:{id:'INERT',fromMe:false,remoteJid:'5511999999999@s.whatsapp.net'},message:{conversation:'oi'}};
  queue.append(message,'notify',at);queue.confirmIdentity('INERT','5511999999999');at+=1001;
  assert.throws(()=>queue.markAdmissionAttempted('INERT'),/admission_not_pending/);
  assert.equal(queue.diagnostics().retained_terminal,1);assert.equal(queue.diagnostics().blocked,0);
  assert.equal(queue.canAutomate(),true);assert.equal(f.open().diagnostics().retained_terminal,1);
  const retained=f.open().append(message,'notify',at).entry;
  assert.equal(retained.admission_attempted,false);assert.equal(retained.state,'blocked');
});

test('a prior assistant admission attempt keeps the entire channel blocked after source expiry',t=>{
  let at=1700000010000;const f=fixture(t,{now:()=>at,ttlMs:1000}),queue=f.open();
  const message={key:{id:'ATTEMPT',fromMe:false,remoteJid:'5511999999999@s.whatsapp.net'},message:{conversation:'oi'}};
  queue.append(message,'notify',at);queue.confirmIdentity('ATTEMPT','5511999999999');queue.markAdmissionAttempted('ATTEMPT');at+=1001;
  assert.deepEqual(queue.pending(),[]);assert.equal(queue.diagnostics().blocked,1);assert.equal(queue.canAutomate(),false);
  assert.equal(f.open().diagnostics().blocked,1);assert.equal(f.open().append(message,'notify',at).entry.admission_attempted,true);
});

test('different tenant or physical account never opens another namespace pending input',t=>{
  const f=fixture(t,{now:()=>1700000010000}),queue=f.open();queue.append(raw('ORIGINAL'),'notify',1700000000000);
  for(const change of [{session_digest:'b'.repeat(64)},{tenant_id:'30000000-0000-4000-8000-000000000001'}]){
    const other=f.open({scope:{...scope,...change}});assert.deepEqual(other.pending(),[]);assert.equal(other.diagnostics().entries,0);
  }
  assert.equal(f.open().pending()[0].id,'ORIGINAL');
});

test('capacity and corrupted ciphertext fail closed without evicting pending input',t=>{
  const f=fixture(t,{now:()=>1700000010000,maxEntries:1}),queue=f.open();queue.append(raw('ONE'),'notify',1700000000000);
  assert.throws(()=>queue.append(raw('TWO'),'notify',1700000000000),/full/);assert.equal(queue.diagnostics().state,'full');
  assert.equal(queue.canConsume([]),false);assert.equal(queue.pending()[0].id,'ONE');
  const encrypted=fs.readdirSync(f.options.directory,{recursive:true}).find(name=>name.endsWith('.enc'));
  fs.writeFileSync(path.join(f.options.directory,encrypted),Buffer.from('corrupt'));
  assert.throws(()=>f.open(),/corrupt/);
});

test('lost owner cannot capture or start input, and abandon requires no current lease',t=>{
  let current=true;const f=fixture(t,{now:()=>1700000010000,isCurrent:()=>current}),queue=f.open();
  queue.append(raw('ONE'),'notify',1700000000000);queue.begin(['ONE']);current=false;
  assert.throws(()=>queue.append(raw('TWO'),'notify',1700000000000),/superseded/);
  queue.abandon();assert.equal(queue.diagnostics().active,0);assert.equal(queue.diagnostics().blocked,1);
});

test('fresh authoritative admission releases a healthy sibling barrier only for the current process',async t=>{
  const f=fixture(t,{now:()=>1700000010000}),queue=f.open();
  queue.append(raw('A'),'notify',1700000000000);queue.begin(['A']);
  queue.append(raw('B'),'notify',1700000001000);assert.equal(queue.canConsume(['A']),false);assert.equal(queue.canAutomate(),false);
  queue.admitted('B');assert.equal(queue.canConsume(['A']),true);assert.equal(queue.canAutomate(),true);
  const restored=f.open();assert.equal(restored.diagnostics().blocked,1);assert.equal(restored.canConsume([]),false);
  assert.throws(()=>queue.admitted('UNKNOWN'),/not_pending/);
});

test('waiting before inference survives slow admission, but original TTL and authority bound the wait',async t=>{
  let at=1700000010000;const f=fixture(t,{now:()=>at,ttlMs:1000}),queue=f.open();
  queue.append(raw('A'),'notify',at);queue.begin(['A']);queue.append(raw('B'),'notify',at);
  let waits=0;
  assert.equal(await queue.waitForAdmission(['A'],{sleep:async()=>{waits++;queue.admitted('B');}}),true);
  assert.equal(waits,1);queue.append(raw('C'),'notify',at);
  assert.equal(await queue.waitForAdmission(['A'],{sleep:async()=>{at+=1001;}}),false);
  assert.equal(queue.canConsume(['A']),false);assert.equal(queue.diagnostics().active,1);
  assert.equal(await queue.waitForAdmission(['A'],{isCurrent:()=>false,sleep:async()=>assert.fail('must not wait')}),false);
});

test('admission wait has a monotonic finite deadline even under continuous unrelated input',async t=>{
  const f=fixture(t,{now:()=>1700000010000}),queue=f.open();let elapsed=0;
  queue.append(raw('A'),'notify',1700000010000);queue.begin(['A']);queue.append(raw('B'),'notify',1700000010000);
  assert.equal(await queue.waitForAdmission(['A'],{maxWaitMs:5,monotonic:()=>elapsed,sleep:async ms=>{elapsed+=ms;}}),false);
  assert.equal(elapsed,5);assert.equal(queue.diagnostics().active,1);
  await assert.rejects(queue.waitForAdmission(['A'],{maxWaitMs:15001}),/invalid_admission_wait/);
});
