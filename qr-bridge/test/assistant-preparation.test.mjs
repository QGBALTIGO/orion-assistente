import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {createAssistantIngress} from '../src/assistant-ingress.mjs';
import {createIngressJournal} from '../src/ingress-journal.mjs';
import {encryptedChannelStore} from '../src/channel-storage.mjs';
import {makeConversationIntake} from '../src/conversation-intake.mjs';

const scope={tenant_id:'10000000-0000-4000-8000-000000000001',channel_id:'20000000-0000-4000-8000-000000000001',session_digest:'a'.repeat(64)};
const secret='synthetic-preparation-secret-at-least-32-bytes',phoneA='5511999999999',phoneB='5511888888888';
const raw=id=>({key:{id,fromMe:false,remoteJid:phoneA+'@s.whatsapp.net'},messageTimestamp:1700000000,
  message:{audioMessage:{fileLength:1000,seconds:4},synthetic:'private-original-'+id}});
function fixture(t,changes={}){
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'orion-assistant-preparation-'));
  t.after(()=>{const resolved=path.resolve(directory);assert.equal(path.dirname(resolved),path.resolve(os.tmpdir()));
    assert.ok(path.basename(resolved).startsWith('orion-assistant-preparation-'));fs.rmSync(resolved,{recursive:true,force:true});});
  const options={directory,secret,scope,now:()=>1700000010000,...changes};
  const digest=createHash('sha256').update(JSON.stringify(Object.fromEntries(Object.entries(scope).sort(([a],[b])=>a.localeCompare(b))))).digest('hex');
  const journalDirectory=path.join(directory,digest);
  const stored=()=>encryptedChannelStore({directory:journalDirectory,secret}).get('incoming-admission-v1');
  const prepare=(queue,id,phone=phoneA,control=31)=>{
    queue.append(raw(id),'notify',1700000000000);queue.admitted(id);
    queue.prepare([id],{phone,proofs:[{revision:7,control_revision:control}]});
  };
  return {directory,journalDirectory,options,open:()=>createAssistantIngress(options),stored,prepare};
}

test('preparation and consumption are separate durable boundaries before callbacks',async t=>{
  const f=fixture(t),queue=f.open();f.prepare(queue,'audio');
  assert.equal(f.stored().entries[0].work.phase,'preparing');
  let consumes=0;
  const intake=makeConversationIntake({settleMs:0,beforeConsume:batch=>queue.consume(batch.messageIds),
    consume:async()=>{consumes++;assert.equal(f.stored().entries[0].work.phase,'consuming');}});
  assert.equal((await intake.submit({conversationKey:'synthetic',messageId:'audio',prepare:async()=>({text:'safe inquiry'})})).state,'processed');
  assert.equal(consumes,1);assert.throws(()=>queue.failPreparation(['audio'],'prepare_failed'),/not_owned/);
  queue.abandon();assert.equal(f.open().diagnostics().blocked,1);
});

test('known pre-consume rejection preserves original encrypted input and releases unrelated admission after restart',t=>{
  const f=fixture(t),queue=f.open(),original=raw('failed');f.prepare(queue,'failed');
  const notice=queue.failPreparation(['failed'],'prepare_failed');
  assert.equal(notice.expires_at,1700000000000+15*60*1000);
  assert.equal(notice.control_revision,31);assert.equal(queue.diagnostics().blocked,0);
  assert.equal(queue.diagnostics().retained,1);assert.equal(queue.requiresReview(phoneA),true);
  assert.deepEqual(f.stored().entries[0].message.raw,original);
  assert.equal(f.stored().entries[0].at,1700000000000);
  assert.equal(queue.pending().length,0);queue.abandon();
  const restored=f.open();assert.equal(restored.requiresReview(phoneA),true);
  assert.equal(restored.canAutomate(),true);assert.equal(restored.claimPreparationNotice(notice.operation_id),false);
  f.prepare(restored,'other',phoneB);assert.equal(restored.canConsume(['other']),true);
});

test('a notification has one persisted operation and acceptance never repairs summary review',t=>{
  const f=fixture(t),queue=f.open();f.prepare(queue,'failed');const notice=queue.failPreparation(['failed'],'prepare_failed');
  assert.equal(queue.claimPreparationNotice(notice.operation_id),true);
  assert.equal(queue.claimPreparationNotice(notice.operation_id),false);
  queue.finishPreparationNotice(notice.operation_id,{accepted:true});
  assert.equal(f.stored().entries[0].work.notice_state,'accepted');assert.equal(queue.requiresReview(phoneA),true);
  queue.abandon();assert.equal(f.open().claimPreparationNotice(notice.operation_id),false);
});

test('an unknown notification result is never automatically sent again',t=>{
  const f=fixture(t),queue=f.open();f.prepare(queue,'failed');const notice=queue.failPreparation(['failed'],'prepare_failed');
  queue.claimPreparationNotice(notice.operation_id);queue.finishPreparationNotice(notice.operation_id,undefined);
  assert.equal(f.stored().entries[0].work.notice_state,'unknown');
  assert.equal(queue.claimPreparationNotice(notice.operation_id),false);
  queue.abandon();assert.equal(f.open().claimPreparationNotice(notice.operation_id),false);
});

test('restart and another contacts accepted summary cannot clear the failed contacts review',t=>{
  const f=fixture(t),first=f.open();f.prepare(first,'failed-A');first.failPreparation(['failed-A'],'prepare_failed');first.abandon();
  const queue=f.open();f.prepare(queue,'new-B',phoneB);queue.consume(['new-B']);queue.complete('new-B',{reviewDelivered:true});
  assert.equal(queue.requiresReview(phoneA),true);assert.equal(queue.requiresReview(phoneB),false);
  f.prepare(queue,'disabled-A');queue.consume(['disabled-A']);queue.complete('disabled-A');
  assert.equal(queue.requiresReview(phoneA),true);
  f.prepare(queue,'new-summary-A');queue.consume(['new-summary-A']);queue.complete('new-summary-A',{reviewDelivered:true});
  assert.equal(queue.requiresReview(phoneA),false);assert.equal(queue.diagnostics().retained,1);
});

test('a newer failure cannot be cleared by an older accepted summary',t=>{
  const f=fixture(t),queue=f.open();f.prepare(queue,'failure-1');queue.failPreparation(['failure-1'],'prepare_failed');
  f.prepare(queue,'old-summary');queue.consume(['old-summary']);
  f.prepare(queue,'failure-2');queue.failPreparation(['failure-2'],'prepare_failed');
  queue.complete('old-summary',{reviewDelivered:true});assert.equal(queue.requiresReview(phoneA),true);
  assert.equal(f.stored().entries.filter(entry=>entry.work?.review_required).length,2);
  f.prepare(queue,'fresh-summary');queue.consume(['fresh-summary']);queue.complete('fresh-summary',{reviewDelivered:true});
  assert.equal(queue.requiresReview(phoneA),false);
});

test('presentation lineage rejects an old summary but permits new navigation while review is pending',t=>{
  const f=fixture(t),queue=f.open();f.prepare(queue,'old-summary');queue.consume(['old-summary']);
  const oldProof=queue.consumptionProof(['old-summary']);queue.complete('old-summary');
  f.prepare(queue,'failed');queue.failPreparation(['failed'],'prepare_failed');queue.abandon();
  const restored=f.open();assert.equal(restored.allowsSelection(phoneA,{...oldProof,kind:'summary'}),false);
  assert.equal(restored.allowsSelection(phoneA,null),false);
  f.prepare(restored,'new-navigation');restored.consume(['new-navigation']);
  const navigation=restored.consumptionProof(['new-navigation']);
  assert.equal(restored.allowsSelection(phoneA,{...navigation,kind:'navigation'}),true);
  assert.equal(restored.allowsSelection(phoneB,{...navigation,kind:'navigation'}),false);
  restored.complete('new-navigation');assert.equal(restored.requiresReview(phoneA),true);
  f.prepare(restored,'fresh-summary');restored.consume(['fresh-summary']);
  const fresh=restored.consumptionProof(['fresh-summary']);restored.complete('fresh-summary',{reviewDelivered:true});
  assert.equal(restored.requiresReview(phoneA),false);
  assert.equal(restored.isProvenanceCurrent(phoneA,fresh),true,'Clearing review must not return to the pre-failure generation');
  assert.equal(restored.isProvenanceCurrent(phoneA,oldProof),false);
  assert.equal(restored.allowsSelection(phoneA,{...fresh,kind:'summary'}),true);
  assert.equal(restored.allowsSelection(phoneA,null),true,'Old records without provenance keep their existing backend guards outside review');
});

test('legacy and consumer-unknown records cannot acquire preparation-failure release',t=>{
  const f=fixture(t),queue=f.open();queue.append(raw('legacy'),'notify',1700000000000);queue.begin(['legacy']);
  assert.throws(()=>queue.failPreparation(['legacy'],'prepare_failed'),/not_owned/);
  queue.review('legacy');assert.equal(queue.diagnostics().blocked,1);queue.abandon();
  const restored=f.open();assert.equal(restored.canAutomate(),false);
  assert.equal(restored.failPreparation(['legacy'],'prepare_failed'),null);
  assert.equal(restored.diagnostics().blocked,1);
  assert.equal(f.stored().entries[0].work,undefined);
});

test('loss of ownership cannot classify, clear review or notify a preparing attempt',t=>{
  let current=true;const f=fixture(t,{isCurrent:()=>current}),queue=f.open();f.prepare(queue,'audio');
  const before=fs.readdirSync(f.journalDirectory).filter(name=>name.endsWith('.enc')).map(name=>fs.readFileSync(path.join(f.journalDirectory,name)));
  current=false;
  assert.throws(()=>queue.failPreparation(['audio'],'prepare_failed'),/superseded/);
  assert.throws(()=>queue.consume(['audio']),/superseded/);queue.abandon();
  const after=fs.readdirSync(f.journalDirectory).filter(name=>name.endsWith('.enc')).map(name=>fs.readFileSync(path.join(f.journalDirectory,name)));
  assert.deepEqual(after,before);
});

test('failure to persist consuming never calls the business callback or releases the source',async t=>{
  const f=fixture(t),queue=f.open();f.prepare(queue,'audio');let consumes=0;
  const rename=fs.renameSync;
  const intake=makeConversationIntake({settleMs:0,beforeConsume:batch=>queue.consume(batch.messageIds),consume:async()=>{consumes++;}});
  fs.renameSync=(from,to)=>{if(path.resolve(to).startsWith(path.resolve(f.journalDirectory)+path.sep))throw Error('synthetic_disk_failure');return rename(from,to);};
  try{
    const result=await intake.submit({conversationKey:'synthetic',messageId:'audio',prepare:async()=>({text:'safe inquiry'})});
    assert.equal(result.state,'consume_failed');assert.equal(consumes,0);
    assert.equal(queue.diagnostics().state,'unavailable');assert.equal(queue.canAutomate(),false);
  }finally{fs.renameSync=rename;}
  assert.equal(f.stored().entries[0].work.phase,'preparing');
});

test('a preparation timeout can finish only pre-consume and late ASR cannot enter business',async t=>{
  const f=fixture(t),queue=f.open();f.prepare(queue,'timeout');let release,consumes=0;
  const intake=makeConversationIntake({settleMs:0,prepareTimeoutMs:10,
    onRejected:batch=>{assert.equal(batch.reason,'prepare_failed');queue.failPreparation(batch.messageIds,batch.reason);},
    beforeConsume:batch=>queue.consume(batch.messageIds),consume:async()=>{consumes++;}});
  const result=await intake.submit({conversationKey:'synthetic',messageId:'timeout',prepare:()=>new Promise(resolve=>{release=resolve;})});
  assert.equal(result.state,'prepare_failed');assert.equal(queue.diagnostics().blocked,0);assert.equal(queue.diagnostics().retained,1);
  release({text:'late transcript'});await new Promise(resolve=>setImmediate(resolve));assert.equal(consumes,0);
});

test('a consumer error remains consuming unknown and cannot be called a preparation rejection',async t=>{
  const f=fixture(t),queue=f.open();f.prepare(queue,'consumer');let rejects=0;
  const intake=makeConversationIntake({settleMs:0,beforeConsume:batch=>queue.consume(batch.messageIds),
    onRejected:async()=>{rejects++;},consume:async()=>{throw Error('synthetic unknown business result');}});
  const result=await intake.submit({conversationKey:'synthetic',messageId:'consumer',prepare:async()=>({text:'safe inquiry'})});
  assert.equal(result.state,'consume_failed');assert.equal(rejects,0);assert.equal(f.stored().entries[0].work.phase,'consuming');
  assert.throws(()=>queue.failPreparation(['consumer'],'prepare_failed'),/not_owned/);
  queue.review('consumer');assert.equal(queue.diagnostics().blocked,1);
});

test('only an explicit zero-write outcome can return a live consumer to preparation',t=>{
  const f=fixture(t),queue=f.open();f.prepare(queue,'audio');queue.consume(['audio']);
  assert.throws(()=>queue.zeroWrite(['audio'],{state:'superseded',not_committed:false}),/invalid/);
  queue.zeroWrite(['audio'],{state:'superseded',not_committed:true});
  assert.equal(f.stored().entries[0].work.phase,'preparing');
  assert.equal(typeof queue.failPreparation(['audio'],'needs_clarification').operation_id,'string');
});

test('a near-limit source retains its full bytes through every failure-notice transition',t=>{
  const f=fixture(t),queue=f.open(),message=raw('large');message.message.synthetic='X'.repeat(510*1024);
  queue.append(message,'notify',1700000000000);queue.admitted('large');
  queue.prepare(['large'],{phone:phoneA,proofs:[{revision:7,control_revision:31}]});
  const notice=queue.failPreparation(['large'],'prepare_failed');queue.claimPreparationNotice(notice.operation_id);
  queue.finishPreparationNotice(notice.operation_id,{accepted:true});
  assert.deepEqual(f.stored().entries[0].message.raw,message);assert.equal(queue.diagnostics().retained,1);
});

test('bounded retained entries are never evicted to admit another source or hidden by metadata updates',t=>{
  const f=fixture(t,{maxEntries:1}),queue=f.open();f.prepare(queue,'full');const notice=queue.failPreparation(['full'],'prepare_failed');
  assert.equal(queue.diagnostics().state,'full');
  assert.throws(()=>queue.append(raw('later'),'notify',1700000001000),/full/);
  queue.claimPreparationNotice(notice.operation_id);queue.finishPreparationNotice(notice.operation_id,{state:'refused'});
  assert.equal(queue.diagnostics().state,'full');assert.equal(f.stored().entries.length,1);
  assert.equal(f.stored().entries[0].id,'full');assert.equal(f.open().diagnostics().state,'full');
});

test('insufficient transition space refuses preparation before ASR and preserves pending input',t=>{
  const f=fixture(t,{maxBytes:1024}),queue=f.open(),message=raw('bounded');message.message.synthetic='X'.repeat(160);
  queue.append(message,'notify',1700000000000);queue.admitted('bounded');
  assert.throws(()=>queue.prepare(['bounded'],{phone:phoneA,proofs:[{revision:7,control_revision:31}]}),/full/);
  assert.equal(queue.active('bounded'),false);assert.equal(f.stored().entries[0].state,'pending');
  assert.deepEqual(f.stored().entries[0].message.raw,message);
});

test('the device journal default business boundary remains conservative and unchanged',t=>{
  const f=fixture(t),journal=createIngressJournal({directory:f.directory+'/device',secret});
  const deviceScope={tenant_id:scope.tenant_id,channel_id:scope.channel_id,device_id:'30000000-0000-4000-8000-000000000001',
    generation:'40000000-0000-4000-8000-000000000001',settings_revision:1};
  journal.append({id:'device',scope:deviceScope,at:1700000000000,order:0,message:{text:'synthetic'}});
  journal.begin(['device']);journal.block('device','business_started');assert.equal(journal.diagnostics().blocked,1);
  assert.throws(()=>journal.failPreparation(['device'],'prepare_failed'),/not_owned/);
});
