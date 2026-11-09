import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createHash,createHmac,randomUUID} from 'node:crypto';
import {createIngressJournal} from '../src/ingress-journal.mjs';
import {createAssistantIngressReview} from '../src/assistant-ingress-review.mjs';
import {encryptedChannelStore} from '../src/channel-storage.mjs';

const secret='synthetic-device-operator-private-key-123456789012345678901234567890';
const actor='30000000-0000-4000-8000-000000000003';
const scope={tenant_id:'10000000-0000-4000-8000-000000000001',
  channel_id:'20000000-0000-4000-8000-000000000001',
  device_id:'40000000-0000-4000-8000-000000000001',
  generation:'50000000-0000-4000-8000-000000000001',settings_revision:3};
const fullScope={...scope,auth_epoch:'a'.repeat(64),owner:'60000000-0000-4000-8000-000000000001',fence:8};
const hash=value=>createHash('sha256').update(value).digest('hex');
const trace=id=>createHmac('sha256',secret).update('orion-trace-v1:'+id).digest('hex').slice(0,20);
const raw=id=>({key:{id,fromMe:false,remoteJid:'opaque-synthetic@lid'},
  message:{conversation:'Synthetic sensitive message '+id},messageTimestamp:Math.floor(Date.now()/1000)});

function fixture(t,{now=1794187200000,availableBytes}={}){
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'orion-device-ingress-review-'));
  t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  let authorized=true,currentScope={...fullScope},assertion=async()=>{};
  const options={directory,secret,namespace:Object.fromEntries(Object.entries(scope).filter(([k])=>k!=='settings_revision')),
    now:()=>now,isCurrent:()=>authorized};
  const journal=createIngressJournal(options);
  const reviewer=createAssistantIngressReview({journal,directory,secret,device:true,
    ...(availableBytes?{availableBytes}:{})});
  const context={authority:{actor_id:actor,authority:'platform_owner_proxy'},
    getScope:()=>currentScope,isCurrent:()=>authorized,assertCurrent:async()=>assertion()};
  const append=(id,reason,settings_revision=scope.settings_revision)=>{
    const key='message:'+id;
    journal.append({id:key,scope:{...scope,settings_revision},at:now-60*60*1000,
      order:0,message:raw(id)});
    if(reason==='business_started')journal.begin([key]);
    journal.block(key,reason);
    return key;
  };
  const dry=()=>({version:1,mode:'dry_run',operation_id:randomUUID(),traces:[]});
  const review=(body,ctx=context)=>reviewer(body,ctx);
  const apply=(snapshot,chosen,operation_id=randomUUID())=>({
    version:1,mode:'apply',operation_id,
    expected:{scope:snapshot.scope,snapshot_sha256:snapshot.snapshot_sha256},
    entries:chosen.map(([id,kind])=>{const row=snapshot.entries.find(item=>item.trace===trace('message:'+id));
      return {trace:row.trace,fingerprint:row.fingerprint,disposition:'do_not_replay',
        evidence:{kind,receipt_sha256:'b'.repeat(64),build_sha:'c'.repeat(40),event_at:now-30000}};
    })
  });
  const store=()=>encryptedChannelStore({directory,secret}).get('incoming-admission-v1');
  return {directory,journal,options,review,apply,dry,append,store,context,
    stop:()=>{authorized=false;},changeScope:value=>{currentScope=value;},
    setAssert:value=>{assertion=value;}};
}

test('4 retained subscription messages: operator retires them in 2 audited no-replay batches',async t=>{
  const f=fixture(t);
  f.append('synthetic-a','business_started');
  f.append('synthetic-b','expired');
  f.append('synthetic-c','scope_changed',2);
  f.append('synthetic-d','identity_unresolved');
  assert.equal(f.journal.diagnostics().blocked,4);
  const first=await f.review(f.dry());
  assert.equal(first.mutation,false);
  assert.equal(first.total_entries,4);
  assert.equal(first.entries.find(row=>row.trace===trace('message:synthetic-a')).eligible_as[0],'operator_attested_uncertain');
  assert.equal(first.entries.find(row=>row.trace===trace('message:synthetic-b')).eligible_as[0],'operator_attested_no_replay');
  assert.doesNotMatch(JSON.stringify(first),/Synthetic sensitive|opaque-synthetic|@lid|conversation|remoteJid/);
  const one=await f.review(f.apply(first,[['synthetic-a','operator_attested_uncertain'],
      ['synthetic-b','operator_attested_no_replay']]));
  assert.equal(one.mutation,true);assert.equal(f.journal.diagnostics().blocked,2);
  const second=await f.review(f.dry());
  const two=await f.review(f.apply(second,[['synthetic-c','operator_attested_no_replay'],
      ['synthetic-d','operator_attested_no_replay']]));
  assert.equal(two.mutation,true);
  assert.equal(f.journal.diagnostics().blocked,0);
  assert.equal(f.journal.diagnostics().retained,4);
  assert.deepEqual(f.journal.pending(),[]);
  assert.equal(f.store().reviews.length,2);
  assert.ok(f.store().entries.every(row=>row.work.phase==='operator_retired'&&row.work.review_required));
  assert.equal(f.store().entries[0].message.message.conversation,'Synthetic sensitive message synthetic-a');
  assert.equal(fs.readdirSync(path.join(f.directory,'operator-review-backups')).length,2);
  assert.equal(createIngressJournal(f.options).diagnostics().blocked,0,'restarted bridge must not replay old input');
  assert.doesNotMatch(fs.readFileSync(path.join(f.directory,fs.readdirSync(f.directory).find(n=>n.endsWith('.enc')))),
    /Synthetic sensitive/,'source stays encrypted on disk');
});

test('admitted or active input can never be retired while an effect may still run',async t=>{
  const f=fixture(t);
  f.journal.append({id:'message:active',scope,at:1794187199000,order:0,message:raw('active')});
  f.journal.begin(['message:active']);
  const snapshot=await f.review(f.dry());
  assert.deepEqual(snapshot.entries[0].eligible_as,[]);
  assert.equal(snapshot.entries[0].state,'blocked');
});

test('cross-device, cross-generation, old scope and unsigned operator requests fail closed',async t=>{
  const f=fixture(t);f.append('synthetic-x','business_started');
  const snap=await f.review(f.dry()),body=f.apply(snap,[['synthetic-x','operator_attested_uncertain']]);
  await assert.rejects(f.review(body,{...f.context,authority:{actor_id:actor,authority:'tenant_owner'}}),
    {code:'invalid_operator_assertion'});
  f.changeScope({...fullScope,device_id:randomUUID()});
  await assert.rejects(f.review(body),{code:'ingress_review_scope_conflict'});
  f.changeScope({...fullScope,generation:randomUUID()});
  await assert.rejects(f.review(f.dry()),{code:'ingress_review_scope_conflict'});
  f.changeScope(fullScope);
  const wrong={...body,expected:{...body.expected,scope:{...fullScope,auth_epoch:'f'.repeat(64)}}};
  await assert.rejects(f.review(wrong),{code:'ingress_review_scope_conflict'});
  assert.equal(f.journal.diagnostics().blocked,1);
});

test('evidence kind must reflect uncertain work; 1-hour old entries accept current evidence',async t=>{
  const f=fixture(t);f.append('synthetic-y','business_started');f.append('synthetic-z','expired');
  const snap=await f.review(f.dry());
  await assert.rejects(f.review(f.apply(snap,[['synthetic-y','operator_attested_no_replay']])),
    {code:'ingress_review_entry_refused'});
  await assert.rejects(f.review(f.apply(snap,[['synthetic-z','operator_attested_uncertain']])),
    {code:'ingress_review_entry_refused'});
  const result=await f.review(f.apply(snap,[['synthetic-y','operator_attested_uncertain']]));
  assert.equal(result.mutation,true);
});

test('snapshot conflict, lost owner lease and backup failure preserve ciphertext and blocked state',async t=>{
  const f=fixture(t);f.append('synthetic-z','expired');
  const snap=await f.review(f.dry()),body=f.apply(snap,[['synthetic-z','operator_attested_no_replay']]);
  f.append('synthetic-joined','scope_changed');
  await assert.rejects(f.review(body),{code:'ingress_review_snapshot_conflict'});
  f.stop();
  await assert.rejects(f.review(f.dry()),{code:'ingress_review_not_ready'});
  const other=fixture(t,{availableBytes:()=>0});
  other.append('synthetic-fail','expired');
  const current=await other.review(other.dry());
  await assert.rejects(other.review(other.apply(current,[['synthetic-fail','operator_attested_no_replay']])),
    {code:'ingress_review_capacity'});
  assert.equal(other.journal.diagnostics().blocked,1);
  assert.equal(other.store().reviews,undefined);
});

test('review type cannot be switched between PRIMARY and subscription device',async t=>{
  const f=fixture(t);f.append('synthetic-1','expired');const snap=await f.review(f.dry());
  const body=f.apply(snap,[['synthetic-1','operator_attested_no_replay']]);
  const altered={...body,entries:body.entries.map(item=>({...item,
    evidence:{...item.evidence,kind:'not_started'}}))};
  await assert.rejects(f.review(altered),{code:'invalid_ingress_review_request'});
});
