import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import {EventEmitter} from 'node:events';
import {createHash,createHmac,timingSafeEqual,randomUUID} from 'node:crypto';
import {createIngressJournal} from '../src/ingress-journal.mjs';
import {createAssistantIngressReview,collectIngressReviewBody} from '../src/assistant-ingress-review.mjs';
import {verifyIngressReviewAssertion} from '../src/ingress-review-auth.mjs';
import {encryptedChannelStore,digest} from '../src/channel-storage.mjs';
import {resolveMessagePhone} from '../src/core.mjs';

const secret='synthetic-operator-master-not-a-real-key-0000',now=1700000010000,phone='5511999999999';
const scope={tenant_id:'10000000-0000-4000-8000-000000000001',channel_id:'20000000-0000-4000-8000-000000000001',session_digest:'a'.repeat(64)};
const actor='30000000-0000-4000-8000-000000000001';
const authority={actor_id:actor,authority:'platform_owner_proxy'};
const fullScope={...scope,auth_epoch:'b'.repeat(64),owner:'40000000-0000-4000-8000-000000000001',fence:4};
const canonical=value=>JSON.stringify(value,(_,item)=>item&&typeof item==='object'&&!Array.isArray(item)?
  Object.fromEntries(Object.entries(item).sort(([a],[b])=>a.localeCompare(b))):item);
const hash=value=>createHash('sha256').update(value).digest('hex');
const raw=id=>({key:{id,fromMe:false,remoteJid:phone+'@s.whatsapp.net'},message:{audioMessage:{seconds:4},synthetic:'private-'+id}});
const trace=id=>createHmac('sha256',secret).update('orion-trace-v1:'+id).digest('hex').slice(0,20);
function fixture(t,options={}){
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'orion-ingress-operator-'));
  t.after(()=>{assert.equal(path.dirname(path.resolve(directory)),path.resolve(os.tmpdir()));
    assert.ok(path.basename(directory).startsWith('orion-ingress-operator-'));fs.rmSync(directory,{recursive:true,force:true});});
  let current=true,scopeValue={...fullScope},assertHook=()=>{},reads=0;
  const journalOptions={directory,secret,namespace:scope,isCurrent:()=>current,now:()=>now,...options};
  const journal=createIngressJournal(journalOptions),stored=()=>encryptedChannelStore({directory,secret}).get('incoming-admission-v1');
  const makeReview=queue=>createAssistantIngressReview({journal:queue,directory,secret,peerTag:()=>hash('scoped-peer'),
    ...(options.availableBytes?{availableBytes:options.availableBytes}:{})});
  const context={authority,getScope:()=>scopeValue,isCurrent:()=>current,assertCurrent:async()=>{reads++;await assertHook();},
    resolvePhone:key=>resolveMessagePhone(key,null),allowedSender:value=>value===phone};
  const append=id=>journal.append({id,scope,at:now-10000,order:0,message:{raw:raw(id),type:'notify'}});
  const seed=()=>{append('failed');journal.begin(['failed']);journal.block('failed','business_started');append('unstarted');append('untouched');};
  const dry=(operation=randomUUID(),traces=[])=>({version:1,mode:'dry_run',operation_id:operation,traces});
  const apply=(snapshot,operation=randomUUID(),ids=['failed','unstarted'])=>({version:1,mode:'apply',operation_id:operation,
    expected:{scope:snapshot.scope,snapshot_sha256:snapshot.snapshot_sha256},entries:ids.map(id=>{
      const entry=snapshot.entries.find(item=>item.trace===trace(id));return {trace:entry.trace,fingerprint:entry.fingerprint,disposition:'do_not_replay',
        evidence:{kind:id==='failed'?'operator_attested_prepare_failed':'not_started',receipt_sha256:'c'.repeat(64),build_sha:'d'.repeat(40),event_at:now-5000}};})});
  return {directory,journal,stored,context,append,seed,dry,apply,review:makeReview(journal),makeReview,
    restart:()=>createIngressJournal(journalOptions),setCurrent:value=>{current=value;},setScope:value=>{scopeValue=value;},
    setAssert:callback=>{assertHook=callback;},reads:()=>reads};
}

test('dry run is read-only and exposes only exact safe source identity, not raw message or peer',async t=>{
  const f=fixture(t);f.seed();const file=path.join(f.directory,digest('incoming-admission-v1')+'.enc'),before=fs.readFileSync(file);
  const result=await f.review(f.dry(),f.context);
  assert.equal(result.mutation,false);assert.equal(result.total_entries,3);assert.equal(result.truncated,false);
  assert.deepEqual(result.entries[0].eligible_as,['operator_attested_prepare_failed']);
  assert.equal(result.entries[0].at,now-10000);assert.equal(result.entries[0].sequence,1);
  assert.deepEqual(Object.keys(result.entries[0]).sort(),['trace','fingerprint','at','sequence','state','blocked_reason','phase','eligible_as'].sort());
  assert.ok(!JSON.stringify(result).includes(phone));assert.ok(!JSON.stringify(result).includes('private-'));
  assert.deepEqual(fs.readFileSync(file),before);assert.equal(fs.existsSync(path.join(f.directory,'operator-review-backups')),false);
  const filtered=await f.review(f.dry(randomUUID(),[trace('unstarted')]),f.context);
  assert.equal(filtered.entries.length,1);assert.equal(filtered.entries[0].trace,trace('unstarted'));
});

test('manual attestation retires only selected sources, backs up ciphertext and keeps durable per-peer review',async t=>{
  const f=fixture(t);f.seed();const original=f.stored(),beforeBytes=fs.readFileSync(path.join(f.directory,digest('incoming-admission-v1')+'.enc'));
  const snapshot=await f.review(f.dry(),f.context),body=f.apply(snapshot),result=await f.review(body,f.context),after=f.stored();
  assert.equal(result.mutation,true);assert.equal(result.replayed,false);assert.equal(result.review_required,true);
  assert.equal(result.backup.reference,body.operation_id);assert.equal(result.backup.sha256,hash(beforeBytes));
  assert.deepEqual(fs.readFileSync(path.join(f.directory,'operator-review-backups',body.operation_id+'.enc')),beforeBytes);
  assert.equal(after.next_sequence,original.next_sequence);assert.deepEqual(after.entries[2],original.entries[2]);
  for(let i=0;i<2;i++){
    assert.deepEqual(after.entries[i].message,original.entries[i].message);assert.equal(after.entries[i].at,original.entries[i].at);
    assert.equal(after.entries[i].sequence,original.entries[i].sequence);assert.deepEqual(after.entries[i].scope,original.entries[i].scope);
    assert.equal(after.entries[i].work.phase,'operator_retired');assert.equal(after.entries[i].work.review_required,true);
  }
  assert.equal(after.reviews[0].authority,'platform_owner_proxy');assert.equal(after.reviews[0].actor_id,actor);
  assert.equal(after.reviews[0].entries[0].prior_reason,'business_started');
  assert.equal(f.journal.diagnostics().blocked,0);assert.equal(f.journal.diagnostics().retained,2);
  const restarted=f.restart();assert.equal(restarted.preparationFailures().length,2);assert.equal(restarted.pending().length,1);
  assert.equal(restarted.claimPreparationNotice(body.operation_id),false);assert.equal(restarted.pending()[0].id,'untouched');
  restarted.append({id:'fresh',scope,at:now,order:0,message:{raw:raw('fresh'),type:'notify'}});
  assert.equal(f.stored().reviews.length,1,'later append must retain the audit');
  assert.ok(!beforeBytes.includes(Buffer.from('private-failed')));
});

test('same operation is idempotent after restart and altered payload or actor conflicts',async t=>{
  const f=fixture(t);f.seed();const body=f.apply(await f.review(f.dry(),f.context)),first=await f.review(body,f.context),before=f.stored();
  const replay=await f.makeReview(f.restart())(body,f.context);
  assert.equal(replay.replayed,true);assert.equal(replay.mutation,false);assert.deepEqual(replay.backup,first.backup);assert.deepEqual(f.stored(),before);
  const changed=structuredClone(body);changed.entries[0].evidence.receipt_sha256='e'.repeat(64);
  await assert.rejects(f.makeReview(f.restart())(changed,f.context),{code:'ingress_review_operation_conflict'});
  await assert.rejects(f.makeReview(f.restart())(body,{...f.context,authority:{...authority,actor_id:randomUUID()}}),{code:'ingress_review_operation_conflict'});
});

for(const field of ['owner','fence','auth_epoch','session_digest','tenant_id','channel_id'])test('apply requires current exact scope '+field,async t=>{
  const f=fixture(t);f.seed();const snapshot=await f.review(f.dry(),f.context),body=f.apply(snapshot),before=f.stored();
  body.expected.scope={...body.expected.scope,[field]:field==='fence'?5:field.endsWith('id')||field==='owner'?randomUUID():'e'.repeat(64)};
  await assert.rejects(f.review(body,f.context),{code:'ingress_review_scope_conflict'});assert.deepEqual(f.stored(),before);
});

test('new source during authority wait conflicts and never overwrites unrelated entries',async t=>{
  const f=fixture(t);f.seed();const body=f.apply(await f.review(f.dry(),f.context));let changed=false;
  f.setAssert(()=>{if(!changed){changed=true;f.append('racing');}});
  await assert.rejects(f.review(body,f.context),{code:'ingress_review_snapshot_conflict'});
  assert.equal(f.stored().entries.length,4);assert.equal(f.stored().reviews,undefined);
});

test('owner loss after identity resolution prevents backup and mutation',async t=>{
  const f=fixture(t);f.seed();const body=f.apply(await f.review(f.dry(),f.context)),before=f.stored();
  await assert.rejects(f.review(body,{...f.context,resolvePhone:async()=>{f.setCurrent(false);return phone;}}),{code:'ingress_review_not_ready'});
  f.setCurrent(true);assert.deepEqual(f.stored(),before);assert.equal(fs.existsSync(path.join(f.directory,'operator-review-backups')),false);
});

for(const alteration of ['wrong_trace','wrong_fingerprint','wrong_kind','wrong_time','wrong_disposition','body_actor','extra_field'])
  test('strict review rejects '+alteration,async t=>{
    const f=fixture(t);f.seed();const body=f.apply(await f.review(f.dry(),f.context));
    if(alteration==='wrong_trace')body.entries[0].trace='e'.repeat(20);
    if(alteration==='wrong_fingerprint')body.entries[0].fingerprint='e'.repeat(64);
    if(alteration==='wrong_kind')body.entries[0].evidence.kind='not_started';
    if(alteration==='wrong_time')body.entries[0].evidence.event_at=now-10001;
    if(alteration==='wrong_disposition')body.entries[0].disposition='replay';
    if(alteration==='body_actor')body.actor_id=actor;
    if(alteration==='extra_field')body.entries[0].evidence.url='https://example.invalid/';
    const before=f.stored();await assert.rejects(f.review(body,f.context));assert.deepEqual(f.stored(),before);
  });

for(const stage of ['live_begin','preparing','consuming','authorization_revoked','policy_denied','identity_unresolved'])
  test('no automatic release for '+stage,async t=>{
    const f=fixture(t);f.append('failed');
    if(stage==='live_begin')f.journal.begin(['failed']);
    else if(['preparing','consuming'].includes(stage)){
      f.journal.prepare([{id:'failed',peer_tag:hash('peer'),intake_revision:1,control_revision:null}]);
      if(stage==='consuming')f.journal.consume(['failed']);
    }else f.journal.block('failed',stage);
    const snapshot=await f.review(f.dry(),f.context),body=f.apply(snapshot,randomUUID(),['failed']),before=f.stored();
    assert.deepEqual(snapshot.entries[0].eligible_as,[]);
    await assert.rejects(f.review(body,f.context));assert.deepEqual(f.stored(),before);
  });

test('unproved LID and other recipients cannot be assigned the operators allowlisted identity',async t=>{
  const f=fixture(t);f.seed();const body=f.apply(await f.review(f.dry(),f.context)),before=f.stored();
  await assert.rejects(f.review(body,{...f.context,resolvePhone:async()=>null}),{code:'ingress_review_identity_unresolved'});
  await assert.rejects(f.review(body,{...f.context,allowedSender:()=>false}),{code:'ingress_review_identity_unresolved'});
  assert.deepEqual(f.stored(),before);
});

test('disk reserve and backup failure retain the original blocked state without effects',async t=>{
  const f=fixture(t,{availableBytes:()=>0});f.seed();const body=f.apply(await f.review(f.dry(),f.context)),before=f.stored();
  await assert.rejects(f.review(body,f.context),{code:'ingress_review_capacity'});assert.deepEqual(f.stored(),before);
  assert.equal(f.journal.diagnostics().blocked,1);
  const second=fixture(t);second.seed();const other=second.apply(await second.review(second.dry(),second.context));
  fs.writeFileSync(path.join(second.directory,'operator-review-backups'),'not a directory');
  await assert.rejects(second.review(other,second.context),{code:'ingress_review_backup_failed'});
  assert.equal(second.stored().reviews,undefined);assert.equal(second.stored().entries[0].blocked_reason,'business_started');
});

test('entry and audit retention cannot overflow the journal or evict another original',async t=>{
  const f=fixture(t,{maxBytes:1600,maxEntryBytes:1000});f.append('failed');f.journal.begin(['failed']);f.journal.block('failed','business_started');
  const snapshot=await f.review(f.dry(),f.context),body=f.apply(snapshot,randomUUID(),['failed']),before=f.stored();
  // The source fits. A large attestation/audit does not gain a second quota or
  // erase the source to make the operational retirement appear successful.
  await assert.rejects(f.review(body,f.context),{code:'ingress_review_capacity'});
  assert.deepEqual(f.stored(),before);assert.equal(fs.existsSync(path.join(f.directory,'operator-review-backups')),false);
});

test('only exact private source identity and the journal namespace may be reviewed',async t=>{
  const f=fixture(t);f.seed();const snapshot=await f.review(f.dry(),f.context),body=f.apply(snapshot),before=f.stored();
  f.setScope({...fullScope,tenant_id:randomUUID()});
  await assert.rejects(f.review(f.dry(),f.context),{code:'ingress_review_scope_conflict'});
  f.setScope(fullScope);
  const modified=structuredClone(before);modified.entries[0].message.raw.key.fromMe=true;
  encryptedChannelStore({directory:f.directory,secret}).set('incoming-admission-v1',modified);
  await assert.rejects(f.review(body,f.context));assert.equal(f.stored().entries[0].work,undefined);
});

test('new journal change from a synchronous backup callback defeats the final CAS',t=>{
  const f=fixture(t);f.seed();const snapshot=f.journal.operatorSnapshot(),entry=snapshot.data.entries[0];
  assert.throws(()=>f.journal.operatorApply({expectedSnapshot:snapshot.snapshot_sha256,operationId:randomUUID(),
    requestDigest:'e'.repeat(64),actorId:actor,entries:[{id:entry.id,fingerprint:hash(JSON.stringify(entry)),peer_tag:hash('peer'),
      evidence:{kind:'operator_attested_prepare_failed',receipt_sha256:'c'.repeat(64),build_sha:'d'.repeat(40),event_at:now-5000}}],
    backup:operation=>{f.append('raced');return {reference:operation,sha256:'f'.repeat(64),bytes:100};}}),/snapshot_conflict/);
  assert.equal(f.stored().reviews,undefined);assert.equal(f.stored().entries.at(-1).id,'raced');
});

test('HTTP body bound rejects extra identity, excessive input and malformed JSON',async()=>{
  for(const text of ['{',JSON.stringify({version:1,mode:'dry_run',operation_id:randomUUID(),traces:[],actor_id:actor}),' '.repeat(8193)]){
    const request=new EventEmitter(),promise=collectIngressReviewBody(request);request.emit('data',Buffer.from(text));request.emit('end');
    await assert.rejects(promise,{code:'invalid_ingress_review_request'});
  }
});

test('real server route requires MASTER and signed owner assertion; adopted PRIMARY has honest same-key exception',async()=>{
  const source=fs.readFileSync(new URL('../src/server.mjs',import.meta.url),'utf8');
  const authSource=source.slice(source.indexOf('function auth(req)'),source.indexOf('function reply(res'));
  const routeSource=source.slice(source.indexOf('const server=http.createServer'),source.indexOf('server.headersTimeout='));
  let handler,calls=0;
  const context=vm.createContext({Buffer,URL,KEY:secret,timingSafeEqual,collectIngressReviewBody,verifyIngressReviewAssertion,
    channelRoutes:async()=>false,http:{createServer:fn=>{handler=fn;return{};}},
    primaryIngressReview:async()=>{calls++;return {mutation:false};},reply:(res,code,data)=>{res.code=code;res.body=data;}});
  vm.runInContext(authSource+'\n'+routeSource,context);
  const body={version:1,mode:'dry_run',operation_id:randomUUID(),traces:[]},at=String(Date.now());
  const signature=createHmac('sha256',secret).update(['orion:assistant-ingress-operator:v1',actor,at,hash(canonical(body))].join('\n')).digest('hex');
  const headers={'x-orion-bridge-key':secret,'x-orion-ops-actor':actor,'x-orion-ops-at':at,'x-orion-ops-signature':signature};
  const invoke=async(custom,method='POST')=>{const request=new EventEmitter();Object.assign(request,{url:'/internal/ops/assistant-ingress-review',method,headers:custom});
    const response={},pending=handler(request,response);request.emit('data',Buffer.from(JSON.stringify(body)));request.emit('end');await pending;return response;};
  assert.equal((await invoke({...headers,'x-orion-bridge-key':'a-different-normal-channel-key-000000000'})).code,401);
  assert.equal((await invoke({'x-orion-bridge-key':secret})).code,401);
  assert.equal((await invoke(headers,'GET')).code,405);
  assert.equal((await invoke(headers)).code,200);assert.equal(calls,1);
  // PRIMARY's normal credential equals MASTER: possession of those same bytes
  // can forge the assertion. This test must never claim an independent secret.
  assert.equal((await invoke(headers)).code,200);assert.equal(calls,2);
});
