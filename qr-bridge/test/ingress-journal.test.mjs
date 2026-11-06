import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {randomUUID} from 'node:crypto';
import {BufferJSON} from '@whiskeysockets/baileys';
import {createIngressJournal} from '../src/ingress-journal.mjs';
import {digest,encryptedChannelStore} from '../src/channel-storage.mjs';

const secret='synthetic-ingress-journal-private-key-'.repeat(3);
const codec={serialize:value=>JSON.stringify(value,BufferJSON.replacer),deserialize:value=>JSON.parse(value,BufferJSON.reviver)};
const scope=()=>({tenant_id:randomUUID(),channel_id:randomUUID(),device_id:randomUUID(),generation:randomUUID(),settings_revision:1});
function fixture(t,options={}){
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'orion-ingress-journal-'));fs.chmodSync(directory,0o700);
  t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  const input={directory,secret,...codec,...options};return {directory,input,open:()=>createIngressJournal(input)};
}
const entry=(id='msg-1',extra={})=>({id,scope:scope(),at:1000000,order:1,message:{key:{id},message:{conversation:'Synthetic private message'}},...extra});
const greeting=id=>entry(id,{message:{key:{id,fromMe:false,remoteJid:'5511999999999@s.whatsapp.net'},message:{conversation:'Olá!'}}});

test('only resolved inert never-attempted terminal input stops blocking while encrypted evidence survives restart',t=>{
  for(const reason of ['expired','scope_changed','policy_denied','invalid_message']){
    const f=fixture(t),j=f.open(),value=greeting(reason);j.append(value);
    assert.equal(j.pending()[0].admission_attempted,false);assert.equal(j.pending()[0].terminal_inert,false);
    j.confirmIdentity(value.id,'5511999999999');j.block(value.id,reason);
    assert.equal(j.diagnostics().blocked,0);assert.equal(j.diagnostics().retained_terminal,1);assert.equal(j.diagnostics().entries,1);
    const restored=f.open(),retained=restored.append({...value,at:value.at+99999}).entry;
    assert.equal(retained.state,'blocked');assert.equal(retained.at,value.at);assert.deepEqual(retained.message,value.message);
    assert.equal(restored.diagnostics().retained_terminal,1);assert.equal(restored.diagnostics().blocked,0);
    assert.deepEqual(restored.pending(),[]);assert.throws(()=>restored.ack(value.id),/entry_blocked/);
    assert.throws(()=>restored.markAdmissionAttempted(value.id),/entry_blocked/);
    assert.throws(()=>restored.confirmIdentity(value.id,'5511999999999'),/entry_blocked/);
  }
});

test('attempted, unresolved, control and business-started inputs remain global review barriers',t=>{
  for(const mode of ['attempted','unknown-identity','control','audio','business_started','identity_unresolved','authorization_revoked','broker_refused']){
    const f=fixture(t),j=f.open(),value=greeting(mode);
    if(mode==='control')value.message.message={conversation:'PARAR'};
    if(mode==='audio')value.message.message={audioMessage:{mediaKey:Buffer.from('synthetic')}};
    j.append(value);if(mode!=='unknown-identity')j.confirmIdentity(value.id,'5511999999999');
    if(mode==='attempted')j.markAdmissionAttempted(value.id);
    if(mode==='business_started'){j.begin([value.id]);j.abandon();}
    else j.block(value.id,['identity_unresolved','authorization_revoked','broker_refused'].includes(mode)?mode:'expired');
    assert.equal(j.diagnostics().blocked,1,mode);assert.equal(f.open().diagnostics().blocked,1,mode);
    assert.equal(f.open().diagnostics().retained_terminal,0,mode);
  }
});

test('legacy missing attempt evidence remains unknown and append cannot inject a false terminal proof',t=>{
  const f=fixture(t),value=greeting('legacy'),store=encryptedChannelStore(f.input);
  store.set('incoming-admission-v1',{version:1,next_sequence:2,entries:[{...value,sequence:1,state:'pending'}]});
  const j=f.open();j.confirmIdentity(value.id,'5511999999999');j.block(value.id,'expired');
  assert.equal(j.diagnostics().blocked,1);assert.equal(f.open().diagnostics().blocked,1);
  const fresh=fixture(t).open(),injected={...greeting('injected'),admission_attempted:false,terminal_inert:true};
  fresh.append(injected);fresh.block(injected.id,'expired');assert.equal(fresh.diagnostics().blocked,1);
  assert.equal(fresh.append(injected).entry.terminal_inert,false);
});

test('admission attempt is monotonic, durable and fenced before any broker call can proceed',t=>{
  let current=true;const f=fixture(t,{isCurrent:()=>current}),j=f.open(),value=greeting('attempt');j.append(value);
  j.confirmIdentity(value.id,'5511999999999');j.markAdmissionAttempted(value.id);
  assert.equal(f.open().pending()[0].admission_attempted,true);
  j.append({...value,admission_attempted:false});assert.equal(j.pending()[0].admission_attempted,true);
  const file=path.join(f.directory,digest('incoming-admission-v1')+'.enc'),before=fs.readFileSync(file);current=false;
  assert.throws(()=>j.markAdmissionAttempted(value.id),/owner_superseded/);assert.deepEqual(fs.readFileSync(file),before);
});

test('a failed durable attempt marker cannot enter the broker or erase the original never-attempted source',t=>{
  const f=fixture(t),j=f.open(),value=greeting('disk-full-attempt');j.append(value);j.confirmIdentity(value.id,'5511999999999');
  const original=fs.openSync,file=path.join(f.directory,digest('incoming-admission-v1')+'.enc'),before=fs.readFileSync(file);let calls=0;
  try{
    fs.openSync=(...args)=>{if(typeof args[0]==='string'&&args[0].endsWith('.tmp')){const error=Error('synthetic disk full');error.code='ENOSPC';throw error;}return original(...args);};
    const dispatch=()=>{j.markAdmissionAttempted(value.id);calls++;};
    assert.throws(dispatch,/journal_unavailable/);
  }finally{fs.openSync=original;}
  assert.equal(calls,0);assert.equal(j.diagnostics().state,'unavailable');assert.deepEqual(fs.readFileSync(file),before);
  assert.equal(f.open().pending()[0].admission_attempted,false);
});

test('ingress is encrypted on disk and survives restart including native BufferJSON media source',t=>{
  const f=fixture(t),j=f.open(),value=entry();
  value.message.message={audioMessage:{mediaKey:Buffer.from('private media key'),fileSha256:Buffer.from([1,2,3]),url:'https://invalid.example/source'}};
  j.append(value);
  const disk=fs.readFileSync(path.join(f.directory,digest('incoming-admission-v1')+'.enc'));
  assert.equal(disk.includes(Buffer.from('private media key')),false);assert.equal(disk.includes(Buffer.from('invalid.example')),false);
  const restored=f.open().pending()[0];assert.deepEqual(restored.message,value.message);assert.equal(restored.at,value.at);
  assert.equal(restored.scope.generation,value.scope.generation);assert.equal(j.diagnostics().entries,1);
});

test('503 before backend admission leaves work durable and FIFO after a process restart',t=>{
  const f=fixture(t),j=f.open(),a=entry('a',{order:500}),b=entry('b',{order:501});j.append(a);j.append(b);
  const failed=j.pending()[0];assert.equal(failed.id,'a'); // The caller's failed broker call never acknowledges this entry.
  const recovered=f.open();recovered.append(entry('new-after-restart',{order:1}));
  assert.deepEqual(recovered.pending().map(row=>row.id),['a','b','new-after-restart']);
  assert.equal(recovered.ack('a'),true);assert.deepEqual(f.open().pending().map(row=>row.id),['b','new-after-restart']);
});

test('lost backend response replays one stable identity and only confirmed admission acknowledges it',t=>{
  const f=fixture(t),j=f.open(),value=entry();j.append(value);
  const admitted=new Set();let calls=0;
  function broker(row){calls++;const duplicate=admitted.has(row.id);admitted.add(row.id);return {effects:[],duplicate};}
  const first=broker(j.pending()[0]);assert.equal(first.duplicate,false); // Simulate response loss: no journal ack.
  const recovered=f.open(),retry=recovered.pending()[0],second=broker(retry);
  assert.equal(second.duplicate,true);assert.equal(retry.id,value.id);assert.equal(retry.at,value.at);
  recovered.ack(retry.id);assert.equal(calls,2);assert.equal(admitted.size,1);assert.deepEqual(f.open().pending(),[]);
});

test('duplicate local upserts do not overwrite first message, original time or settings scope',t=>{
  const j=fixture(t).open(),value=entry();j.append(value);
  const result=j.append({...value,at:9999999,order:99,scope:{...value.scope,settings_revision:2},message:{conversation:'replacement'}});
  assert.equal(result.fresh,false);assert.equal(result.entry.at,value.at);assert.equal(result.entry.scope.settings_revision,1);
  assert.deepEqual(result.entry.message,value.message);assert.equal(j.diagnostics().entries,1);
});

test('duplicate cross-tenant, cross-channel, cross-device and cross-generation identities fail without overwrite',t=>{
  const j=fixture(t).open(),value=entry();j.append(value);
  for(const key of ['tenant_id','channel_id','device_id','generation']){
    assert.throws(()=>j.append({...value,scope:{...value.scope,[key]:randomUUID()}}),/scope_conflict/);
  }
  assert.deepEqual(j.pending()[0].scope,value.scope);
});

test('expired and revoked work is retained encrypted and cannot be acknowledged or revived by duplicate delivery',t=>{
  const f=fixture(t,{now:()=>2000000}),j=f.open(),value=entry();j.append(value);j.block(value.id,'expired');
  assert.deepEqual(j.pending(),[]);assert.equal(j.diagnostics().blocked,1);assert.throws(()=>j.ack(value.id),/entry_blocked/);
  const duplicate=f.open().append({...value,at:2000000});
  assert.equal(duplicate.fresh,false);assert.equal(duplicate.entry.state,'blocked');assert.equal(duplicate.entry.at,value.at);
  assert.equal(duplicate.entry.blocked_reason,'expired');assert.equal(duplicate.entry.blocked_at,2000000);
  assert.equal(fs.readdirSync(f.directory).filter(name=>name.endsWith('.enc')).length,1);
});

test('settings changes remain loadable for adapter scope_changed quarantine without rewriting original scope',t=>{
  const f=fixture(t),j=f.open(),value=entry();j.append(value);
  const current=f.open();current.block(value.id,'scope_changed');
  assert.equal(current.diagnostics().blocked,1);assert.equal(current.append(value).entry.scope.settings_revision,1);
});

test('entry count saturation never evicts existing pending or blocked work',t=>{
  const f=fixture(t,{maxEntries:2}),j=f.open();j.append(entry('a'));j.append(entry('b'));j.block('a','policy_denied');
  assert.throws(()=>j.append(entry('c')),/journal_full/);assert.equal(j.diagnostics().state,'full');
  assert.equal(f.open().diagnostics().entries,2);assert.deepEqual(j.pending().map(row=>row.id),['b']);
  j.ack('b');assert.equal(j.diagnostics().state,'ok');j.append(entry('c'));assert.equal(j.diagnostics().blocked,1);
});

test('serialized total capacity includes BufferJSON overhead and does not truncate source messages',t=>{
  const f=fixture(t,{maxBytes:1800,maxEntryBytes:1300}),j=f.open();
  const first=entry('a',{message:{bytes:Buffer.alloc(400,123)}});j.append(first);
  const original=fs.readFileSync(path.join(f.directory,digest('incoming-admission-v1')+'.enc'));
  assert.throws(()=>j.append(entry('b',{message:{bytes:Buffer.alloc(400,123)}})),/journal_full/);
  assert.deepEqual(fs.readFileSync(path.join(f.directory,digest('incoming-admission-v1')+'.enc')),original);
  assert.deepEqual(f.open().pending()[0].message,first.message);
});

test('oversized source is refused without silently retaining a truncated payload',t=>{
  const j=fixture(t,{maxEntryBytes:700}).open();assert.throws(()=>j.append(entry('large',{message:{text:'x'.repeat(1000)}})),/journal_full/);
  assert.equal(j.diagnostics().entries,0);assert.equal(j.diagnostics().state,'full');
});

test('a near-capacity pending record can still be quarantined without extending its original timestamp',t=>{
  const f=fixture(t,{maxBytes:1200,maxEntryBytes:1000,now:()=>9999999999999}),j=f.open();
  let last;
  for(let length=100;length<800;length+=10){
    const row=entry('limit',{message:{text:'x'.repeat(length)}});
    try{const probe=fixture(t,{maxBytes:1200,maxEntryBytes:1000}).open();probe.append(row);last=row;}catch(error){assert.match(error.message,/journal_full/);break;}
  }
  assert.ok(last);j.append(last);assert.equal(j.block(last.id,'authorization_revoked'),true);
  const recovered=f.open(),record=recovered.append(last).entry;
  assert.equal(record.at,last.at);assert.equal(record.blocked_reason,'authorization_revoked');assert.equal(recovered.diagnostics().blocked,1);
});

test('disk exhaustion cannot acknowledge or overwrite pending work, and a fresh owner can recover original bytes',t=>{
  const f=fixture(t),j=f.open(),value=entry();j.append(value);
  const file=path.join(f.directory,digest('incoming-admission-v1')+'.enc'),before=fs.readFileSync(file),original=fs.openSync;
  try{
    fs.openSync=(...args)=>{if(typeof args[0]==='string'&&args[0].endsWith('.tmp')){const error=Error('synthetic disk full');error.code='ENOSPC';throw error;}return original(...args);};
    assert.throws(()=>j.ack(value.id),/journal_unavailable/);
  }finally{fs.openSync=original;}
  assert.equal(j.diagnostics().state,'unavailable');assert.equal(j.diagnostics().error_code,'ingress_journal_write_failed');
  assert.throws(()=>j.pending(),/journal_unavailable/);assert.deepEqual(fs.readFileSync(file),before);
  assert.equal(f.open().pending()[0].id,value.id);
});

test('returned snapshots cannot mutate encrypted or in-memory admission state',t=>{
  const f=fixture(t),j=f.open(),value=entry(),added=j.append(value);added.entry.message.message.conversation='mutated';
  value.message.message.conversation='also mutated';const pending=j.pending();pending[0].scope.settings_revision=10;pending.splice(0,1);
  assert.equal(j.pending()[0].scope.settings_revision,1);assert.equal(f.open().pending()[0].message.message.conversation,'Synthetic private message');
});

test('lease expiry fences reads, appends, acknowledgement and quarantine without changing original bytes',t=>{
  let current=true;const f=fixture(t,{isCurrent:()=>current}),j=f.open(),value=entry();j.append(value);
  const file=path.join(f.directory,digest('incoming-admission-v1')+'.enc'),before=fs.readFileSync(file);current=false;
  for(const action of [()=>j.pending(),()=>j.append(entry('b')),()=>j.ack(value.id),()=>j.block(value.id,'authorization_revoked')]){
    assert.throws(action,/owner_superseded/);
  }
  assert.deepEqual(fs.readFileSync(file),before);
});

test('fence loss immediately before atomic replacement cannot erase existing unacknowledged entry',t=>{
  let active=true,calls=0,fenceAt=Infinity;
  const f=fixture(t,{isCurrent:()=>active&&++calls<fenceAt}),j=f.open(),value=entry();j.append(value);
  const file=path.join(f.directory,digest('incoming-admission-v1')+'.enc'),before=fs.readFileSync(file);fenceAt=calls+3;
  assert.throws(()=>j.ack(value.id),/journal_unavailable/);assert.equal(j.diagnostics().state,'unavailable');active=true;
  assert.deepEqual(fs.readFileSync(file),before);assert.equal(createIngressJournal({...f.input,isCurrent:()=>true}).pending()[0].id,value.id);
  assert.throws(()=>j.append(entry('new')),/journal_unavailable/);
  assert.deepEqual(fs.readdirSync(f.directory),[path.basename(file)]);
});

test('corrupt ciphertext and wrong encryption secret fail closed without overwriting retained bytes',t=>{
  const f=fixture(t),j=f.open();j.append(entry());
  assert.throws(()=>createIngressJournal({...f.input,secret:'different-synthetic-secret'}),/journal_corrupt/);
  const file=path.join(f.directory,digest('incoming-admission-v1')+'.enc'),damaged=fs.readFileSync(file);damaged[damaged.length-1]^=0xff;fs.writeFileSync(file,damaged);
  assert.throws(()=>f.open(),/journal_corrupt/);assert.deepEqual(fs.readFileSync(file),damaged);
});

test('validly encrypted invalid schema, repeated identity and foreign namespace all fail closed',t=>{
  const f=fixture(t),store=encryptedChannelStore(f.input),value=entry();
  const row={...value,sequence:1,state:'pending'};
  for(const data of [{version:99,next_sequence:2,entries:[row]},
    {version:1,next_sequence:3,entries:[row,{...row,sequence:2}]},
    {version:1,next_sequence:2,entries:[{...row,scope:{...row.scope,settings_revision:0}}]}]){
    store.set('incoming-admission-v1',data);assert.throws(()=>f.open(),/journal_corrupt/);
  }
  store.set('incoming-admission-v1',{version:1,next_sequence:2,entries:[row]});
  const namespace={...value.scope};delete namespace.settings_revision;namespace.tenant_id=randomUUID();
  assert.throws(()=>createIngressJournal({...f.input,namespace}),/journal_corrupt/);
});

test('invalid IDs, scopes, timestamps, payloads and block reasons are rejected before persistence',t=>{
  const f=fixture(t),j=f.open(),value=entry();
  for(const patch of [{id:''},{id:'bad\nvalue'},{scope:{...value.scope,settings_revision:0}},
    {scope:{...value.scope,unexpected:true}},{at:0},{order:-1},{message:null},{message:[]}]){
    assert.throws(()=>j.append({...value,...patch}),/invalid_ingress_entry/);
  }
  assert.throws(()=>j.block('missing','arbitrary sensitive error text'),/invalid_ingress_block/);
  assert.equal(j.ack('missing'),false);assert.equal(j.block('missing','expired'),false);assert.deepEqual(fs.readdirSync(f.directory),[]);
});

test('strict legacy session scope is retained and rejected across session digests or device scope types',t=>{
  const legacy={tenant_id:randomUUID(),channel_id:randomUUID(),session_digest:'a'.repeat(64)},f=fixture(t,{namespace:legacy}),j=f.open();
  const value=entry('legacy-message',{scope:legacy});j.append(value);
  assert.deepEqual(f.open().pending()[0].scope,legacy);
  for(const invalid of [{...legacy,session_digest:'A'.repeat(64)},{...legacy,settings_revision:1},{...legacy,extra:1}]){
    assert.throws(()=>j.append(entry('other',{scope:invalid})),/invalid_ingress_entry/);
  }
  const unbound=createIngressJournal({...f.input,namespace:undefined});
  assert.throws(()=>unbound.append({...value,scope:{...legacy,session_digest:'b'.repeat(64)}}),/scope_conflict/);
  assert.throws(()=>unbound.append({...value,scope:scope()}),/scope_conflict/);
});

test('business begin persists one atomic non-replay boundary for the whole batch and completion is current-instance only',t=>{
  const f=fixture(t),j=f.open();j.append(entry('a'));j.append(entry('b'));
  assert.equal(j.begin(['a','b']),true);assert.deepEqual(j.pending(),[]);assert.equal(j.diagnostics().active,2);assert.equal(j.diagnostics().blocked,0);
  const restarted=f.open();assert.deepEqual(restarted.pending(),[]);assert.equal(restarted.diagnostics().active,0);assert.equal(restarted.diagnostics().blocked,2);
  assert.throws(()=>restarted.complete('a'),/completion_not_owned/);
  assert.equal(restarted.begin(['a','b']),false);assert.equal(j.complete('a'),true);assert.equal(j.complete('b'),true);
  assert.equal(f.open().diagnostics().entries,0);assert.equal(j.complete('b'),false);
});

test('business failure relinquishes its completion claim while keeping uncertain payload for review',t=>{
  const f=fixture(t),j=f.open();j.append(entry('failed'));j.begin(['failed']);
  assert.equal(j.block('failed','business_started'),true);assert.equal(j.diagnostics().active,0);assert.equal(j.diagnostics().blocked,1);
  assert.throws(()=>j.complete('failed'),/completion_not_owned/);assert.equal(f.open().diagnostics().blocked,1);
  assert.equal(j.diagnostics().oldest_at,1000000,'Review-only backlog still exposes original age');
});

test('invalid or partially missing batches cannot start only a subset of business work',t=>{
  const f=fixture(t),j=f.open();j.append(entry('a'));j.append(entry('b'));
  for(const ids of [[],['a','a'],['a','missing'],['a','bad\nidentity']]){
    assert.throws(()=>j.begin(ids),/invalid_ingress_begin|entry_missing/);assert.deepEqual(j.pending().map(row=>row.id),['a','b']);
  }
  j.block('b','expired');assert.equal(j.begin(['a','b']),false);assert.deepEqual(j.pending().map(row=>row.id),['a']);
});

test('fence loss during batch begin cannot admit business or leave a partial started subset',t=>{
  let calls=0,fenceAt=Infinity;const f=fixture(t,{isCurrent:()=>++calls<fenceAt}),j=f.open();j.append(entry('a'));j.append(entry('b'));
  fenceAt=calls+3;assert.throws(()=>j.begin(['a','b']),/journal_unavailable/);
  const recovered=createIngressJournal({...f.input,isCurrent:()=>true});
  assert.deepEqual(recovered.pending().map(row=>row.id),['a','b']);assert.equal(recovered.diagnostics().blocked,0);
});

test('teardown can revoke only volatile completion capability after lease loss without changing encrypted review evidence',t=>{
  let current=true;const f=fixture(t,{isCurrent:()=>current}),j=f.open();j.append(entry('started'));j.begin(['started']);
  const file=path.join(f.directory,digest('incoming-admission-v1')+'.enc'),before=fs.readFileSync(file);current=false;
  j.abandon();assert.equal(j.diagnostics().active,0);assert.equal(j.diagnostics().blocked,1);assert.deepEqual(fs.readFileSync(file),before);
  current=true;assert.throws(()=>j.complete('started'),/completion_not_owned/);
  assert.equal(f.open().diagnostics().blocked,1);assert.deepEqual(j.pending(),[]);
});
