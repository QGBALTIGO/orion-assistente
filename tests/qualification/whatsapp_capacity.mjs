/** Offline qualification: no WhatsApp socket/provider/production credentials.
 * Peers run in a separate OS process. Application and helper RSS/CPU/event-loop
 * metrics are independent; latency still includes both processes and IPC. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fork} from 'node:child_process';
import {createHash,randomBytes,randomUUID} from 'node:crypto';
import {monitorEventLoopDelay,performance} from 'node:perf_hooks';
import * as sdk from '../../qr-bridge/node_modules/@whiskeysockets/baileys/lib/index.js';
import {useDurableAuthState} from '../../qr-bridge/src/durable-auth.mjs';
import {protectSignalSessionLogs} from '../../qr-bridge/src/signal-log-safety.mjs';
import {makeEncryptedRetryStore} from '../../qr-bridge/src/encrypted-retry-store.mjs';
import {createIngressJournal} from '../../qr-bridge/src/ingress-journal.mjs';
import {makeOutgoingPresence} from '../../qr-bridge/src/outgoing-presence.mjs';
import {makeSendCapacity} from '../../qr-bridge/src/send-capacity.mjs';

protectSignalSessionLogs();
process.env.ORION_RETRY_TEST_MODE='1';
const companies=50,customersPerCompany=10,customers=companies*customersPerCompany;
const root=fs.mkdtempSync(path.join(os.tmpdir(),'orion-offline-wa-capacity-'));
const secret=randomBytes(48).toString('hex'),sdkAuth={BufferJSON:sdk.BufferJSON,initAuthCreds:sdk.initAuthCreds,proto:sdk.proto};
const tick=()=>new Promise(resolve=>setImmediate(resolve)),delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const started=performance.now(),operators=[];
const sourceFiles=['tests/qualification/whatsapp_capacity.mjs','tests/qualification/whatsapp_capacity_peer.mjs',
  'qr-bridge/src/durable-auth.mjs','qr-bridge/src/channel-storage.mjs','qr-bridge/src/encrypted-retry-store.mjs',
  'qr-bridge/src/ingress-journal.mjs','qr-bridge/src/outgoing-presence.mjs','qr-bridge/src/send-capacity.mjs'];
const sourceHashes=()=>Object.fromEntries(sourceFiles.map(name=>[name,createHash('sha256').update(fs.readFileSync(new URL('../../'+name,import.meta.url))).digest('hex')]));
const sourceAtStart=sourceHashes();
let stage='setup',peakAppRss=process.memoryUsage().rss,peerMetrics=null,peerSummary=null;
const report={schema:2,started_at:new Date().toISOString(),companies,customers,network_socket_count:0,external_messages_sent:0,
  production_credentials_read:false,qualification:'offline_synthetic_only',correctness_passed:false,offline_service_targets_passed:false,
  production_capacity_qualified:false,
  source_hash_capture:'After ESM import and before workload; compare again after the run. Coordinate a source freeze before release qualification.',
  source_hashes_at_start:sourceAtStart,
  criteria_fixed_before_run:{max_app_service_timer_stall_ms_exclusive:10000,reference_lease_ms:30000,
    unchanged_source_snapshot_required:true,
    scope_and_message_correctness:'100 percent; no loss/duplicate or scope leak allowed',
    service_phases:['durable_ingress_restart','signal_protocols_round_1','signal_protocols_round_2','retry_cache_isolation','presence_concurrency','admission_pressure'],
    setup_reported_separately:['peer_keys_and_prekeys','application_durable_accounts','fixture_session_establishment','application_auth_restart'],
    all_application_phases_strict_result_also_reported:true,
    note:'A green service result does not qualify simultaneous bootstrap/session-establishment storms or 500 immediate replies.'},
  application_components_real:['pinned Signal encryption/decryption','encrypted durable auth and restart','retry cache','ingress journal','outgoing presence','send admission'],
  stages:{},limits:['No real authenticated WhatsApp socket or provider delivery','Not 50 real connections or 500 immediate responses',
    'No HTTP/API/PostgreSQL/Redis/LLM capacity measurement','Media are typed encrypted protobuf envelopes; no media upload/download or physical rendering test',
    'No physical iPhone/Android or 24-hour stability qualification',
    'Both independent OS processes share this development host and may contend for CPU; end-to-end latency includes helper queue and IPC',
    'App component await times include in-process scheduling/durable state; they are not isolated per-operation CPU times']};
const percentile=(values,p)=>{const sorted=[...values].sort((a,b)=>a-b);return sorted[Math.min(sorted.length-1,Math.ceil(sorted.length*p)-1)]??0;};
const summary=values=>({count:values.length,p50_ms:percentile(values,.5),p95_ms:percentile(values,.95),p99_ms:percentile(values,.99),max_ms:Math.max(0,...values)});
const companyJid=i=>String(100000000000000+i)+'@s.whatsapp.net',customerJid=i=>String(110000000000000+i)+'@s.whatsapp.net';
const contents=[
  {conversation:'OFFLINE SYNTHETIC TEXT'},
  {listMessage:{title:'Synthetic barber',description:'No booking',buttonText:'Options',listType:1,sections:[{title:'Test',rows:[{title:'Synthetic cut',rowId:'offline:cut'}]}]}},
  {interactiveMessage:{body:{text:'Synthetic URL'},nativeFlowMessage:{buttons:[{name:'cta_url',buttonParamsJson:JSON.stringify({display_text:'Synthetic site',url:'https://example.com/'})}]}}},
  {audioMessage:{url:'https://example.com/not-fetched.enc',mimetype:'audio/mpeg',ptt:true,seconds:2,mediaKey:Buffer.alloc(32,1),fileSha256:Buffer.alloc(32,2)}},
  {imageMessage:{url:'https://example.com/not-fetched.enc',mimetype:'image/jpeg',caption:'Synthetic image',mediaKey:Buffer.alloc(32,3)}},
  {documentMessage:{url:'https://example.com/not-fetched.enc',mimetype:'application/pdf',fileName:'synthetic.pdf',mediaKey:Buffer.alloc(32,4)}},
  {stickerMessage:{url:'https://example.com/not-fetched.enc',mimetype:'image/webp',mediaKey:Buffer.alloc(32,5)}}
];
const open=i=>useDurableAuthState(path.join(root,'company-'+i,'auth'),{secret,sdk:sdkAuth});
const namespace=()=>({tenant_id:randomUUID(),channel_id:randomUUID(),device_id:randomUUID(),generation:randomUUID()});
function dirBytes(dir){let total=0;for(const e of fs.readdirSync(dir,{withFileTypes:true})){const f=path.join(dir,e.name);if(e.isDirectory())total+=dirBytes(f);else total+=fs.statSync(f).size;}return total;}
function startAppMetric(){
  const lag=monitorEventLoopDelay({resolution:10});lag.enable();
  const m={lag,cpu:process.cpuUsage(),started:performance.now(),last:performance.now(),stall:0,ticks:0,peak:process.memoryUsage().rss};
  m.timer=setInterval(()=>{const at=performance.now();m.stall=Math.max(m.stall,at-m.last-10);m.last=at;m.ticks++;m.peak=Math.max(m.peak,process.memoryUsage().rss);peakAppRss=Math.max(peakAppRss,m.peak);},10);
  return m;
}
async function finishAppMetric(m){
  await delay(20);clearInterval(m.timer);m.lag.disable();
  const cpu=process.cpuUsage(m.cpu),rss=process.memoryUsage().rss;m.peak=Math.max(m.peak,rss);peakAppRss=Math.max(peakAppRss,m.peak);
  return {process:'application_only',rss_bytes:rss,peak_rss_bytes:m.peak,cpu_user_ms:cpu.user/1000,cpu_system_ms:cpu.system/1000,
    metrics_window_ms:performance.now()-m.started,event_loop:{p99_ms:m.lag.percentile(99)/1e6,max_ms:m.lag.max/1e6,max_timer_stall_ms:Math.max(0,m.stall),timer_ticks:m.ticks}};
}
const minimalEnv={PATH:process.env.PATH??'',SystemRoot:process.env.SystemRoot??'',TEMP:os.tmpdir(),TMP:os.tmpdir(),ORION_RETRY_TEST_MODE:'1'};
const child=fork(new URL('./whatsapp_capacity_peer.mjs',import.meta.url),[],{serialization:'advanced',stdio:['ignore','ignore','pipe','ipc'],env:minimalEnv,execArgv:[]});
let serial=0,helperClosed=false;const pending=new Map();let helperStderrBytes=0;
child.stderr.on('data',chunk=>{helperStderrBytes+=chunk.length;}); // never forward Signal or exceptions
child.on('message',reply=>{const p=pending.get(reply?.id);if(!p)return;pending.delete(reply.id);clearTimeout(p.timer);
  if(reply.ok===true)p.resolve(reply.result);else p.reject(Error('SYNTHETIC_PEER_OPERATION_FAILED'));});
child.on('error',()=>{for(const p of pending.values()){clearTimeout(p.timer);p.reject(Error('SYNTHETIC_PEER_PROCESS_FAILED'));}pending.clear();});
const helperExited=new Promise(resolve=>child.on('exit',(code,signal)=>{helperClosed=true;for(const p of pending.values()){clearTimeout(p.timer);p.reject(Error('SYNTHETIC_PEER_EXITED'));}pending.clear();resolve({code,signal});}));
function rpc(name,data={}){
  if(helperClosed)return Promise.reject(Error('SYNTHETIC_PEER_EXITED'));const id=++serial;
  return new Promise((resolve,reject)=>{const timer=setTimeout(()=>{pending.delete(id);reject(Error('SYNTHETIC_PEER_TIMEOUT'));},180000);
    pending.set(id,{resolve,reject,timer});child.send({id,name,data},error=>{if(error&&pending.has(id)){pending.delete(id);clearTimeout(timer);reject(Error('SYNTHETIC_PEER_IPC_FAILED'));}});});
}
async function measure(name,category,fn){
  stage=name;process.stderr.write('offline-stage '+name+'\n');
  await rpc('metric_start');const m=startAppMetric();await delay(20);const at=performance.now();
  let value,failed;
  try{value=await fn();}catch(error){failed=error;}
  const workElapsed=performance.now()-at;const app=await finishAppMetric(m);const helper=await rpc('metric_finish');
  report.stages[name]={category,work_elapsed_ms:workElapsed,application:app,synthetic_peer_helper:helper,...(value??{})};
  if(failed)throw failed;await tick();return value;
}
async function drainAll(tasks){const results=await Promise.allSettled(tasks);const rejected=results.filter(row=>row.status==='rejected');
  if(rejected.length){report.stage_rejected_tasks=rejected.length;throw rejected[0].reason;}}
try{
  let bundles;
  await measure('peer_keys_and_prekeys','synthetic_setup',async()=>{const r=await rpc('initialize',{customers});bundles=r.bundles;
    assert.equal(r.customers,customers);return {new_synthetic_customers:r.customers};});
  await measure('application_durable_accounts','application_bootstrap',async()=>{
    const identities=new Set();
    for(let i=0;i<companies;i++){
      const auth=open(i),identity=Buffer.from(auth.state.creds.signedIdentityKey.public);identities.add(identity.toString('hex'));
      const scope={...namespace(),settings_revision:1},directory=path.join(root,'company-'+i);
      operators.push({auth,identity,scope,repository:sdk.DEFAULT_CONNECTION_CONFIG.makeSignalRepository(auth.state),
        cache:makeEncryptedRetryStore({directory:path.join(directory,'retry'),secret,max:100,maxTotalBytes:2*1024*1024,
          serialize:value=>JSON.stringify(value,sdk.BufferJSON.replacer),deserialize:value=>JSON.parse(value,sdk.BufferJSON.reviver)}),
        journal:createIngressJournal({directory:path.join(directory,'ingress'),secret,namespace:Object.fromEntries(Object.entries(scope).filter(([k])=>k!=='settings_revision'))})});
      await tick();
    }
    assert.equal(identities.size,companies);return {distinct_durable_account_identities:identities.size};
  });
  await measure('durable_ingress_restart','service',async()=>{
    for(let i=0;i<customers;i++){const op=operators[Math.floor(i/customersPerCompany)],item={id:'offline-in-'+i,at:Date.now(),order:i,
      scope:op.scope,message:{key:{remoteJid:customerJid(i),id:'offline-in-'+i},message:{conversation:'Synthetic request '+i}}};
      assert.equal(op.journal.append(item).fresh,true);assert.equal(op.journal.append(item).fresh,false);}
    let recovered=0;
    for(let i=0;i<companies;i++){const op=operators[i];op.journal=createIngressJournal({directory:path.join(root,'company-'+i,'ingress'),secret,
      namespace:Object.fromEntries(Object.entries(op.scope).filter(([k])=>k!=='settings_revision'))});
      const entries=op.journal.pending();assert.equal(entries.length,customersPerCompany);recovered+=entries.length;
      for(const row of entries)assert.equal(op.journal.ack(row.id),true);assert.equal(op.journal.diagnostics().entries,0);await tick();}
    assert.equal(recovered,customers);return {recovered,duplicate_admissions:0,lost:0};
  });
  await measure('fixture_session_establishment','fixture_session_setup',async()=>{
    for(let i=0;i<customers;i++){await operators[Math.floor(i/customersPerCompany)].repository.injectE2ESession({jid:customerJid(i),session:bundles[i]});if(i%10===9)await tick();}
    bundles=null;return {established:customers};
  });
  for(let round=0;round<2;round++){
    if(round===1)await measure('application_auth_restart','application_bootstrap',async()=>{
      for(let i=0;i<companies;i++){const op=operators[i];op.auth=open(i);assert.deepEqual(op.auth.state.creds.signedIdentityKey.public,op.identity);
        op.repository=sdk.DEFAULT_CONNECTION_CONFIG.makeSignalRepository(op.auth.state);await tick();}return {identities_preserved:companies};
    });
    await measure('signal_protocols_round_'+(round+1),'service',async()=>{
      const times=[],encryptTimes=[],decryptTimes=[],helperTimes=[],ids=new Set();let decrypted=0,retryable=0,acknowledged=0;
      await drainAll(Array.from({length:customers},async(_,i)=>{
        const at=performance.now(),op=operators[Math.floor(i/customersPerCompany)],content=contents[i%contents.length],id='offline-'+round+'-'+i;
        const bytes=Buffer.from(sdk.proto.Message.encode(sdk.proto.Message.fromObject(content)).finish()),key={id,remoteJid:customerJid(i),fromMe:true};
        assert.equal(op.cache.save(key,content),true);assert.deepEqual(op.cache.get(key),content);retryable++;
        const encryptAt=performance.now(),packet=await op.repository.encryptMessage({jid:customerJid(i),data:bytes});encryptTimes.push(performance.now()-encryptAt);
        assert.notDeepEqual(packet.ciphertext,bytes);
        const reply=await rpc('roundtrip',{index:i,round,packet,expected:bytes});assert.equal(reply.decrypted,true);decrypted++;helperTimes.push(reply.helper_component_ms);
        const decryptAt=performance.now(),ackBytes=Buffer.from('synthetic-ack-'+round+'-'+i);
        assert.deepEqual(Buffer.from(await op.repository.decryptMessage({jid:customerJid(i),...reply.ack})),ackBytes);decryptTimes.push(performance.now()-decryptAt);acknowledged++;
        assert.equal(ids.has(id),false);ids.add(id);times.push(performance.now()-at);
      }));
      assert.equal(decrypted,customers);assert.equal(acknowledged,customers);
      return {decrypted,acknowledged,retryable,unexpected_duplicates:0,latency_end_to_end_including_helper_ipc:summary(times),
        application_encrypt_await:summary(encryptTimes),application_ack_decrypt_await:summary(decryptTimes),helper_component_await:summary(helperTimes),content_types:contents.length};
    });
  }
  await measure('retry_cache_isolation','service',async()=>{
    let rejected=0;
    for(let i=0;i<companies;i++){const other=operators[(i+1)%companies],id='offline-1-'+i*customersPerCompany,index=i*customersPerCompany;
      assert.equal(other.cache.get({id,remoteJid:customerJid(index),fromMe:true}),undefined);rejected++;
      assert.equal(operators[i].cache.get({id,remoteJid:customerJid(index+1),fromMe:true}),undefined);rejected++;}
    assert.equal(rejected,companies*2);return {cross_company_and_cross_recipient_requests_rejected:rejected};
  });
  await measure('presence_concurrency','service',async()=>{
    let completed=0,active=0,peakActive=0,composing=0,recording=0;const times=[];
    const presence=Array.from({length:companies},()=>makeOutgoingPresence({socket:{async sendPresenceUpdate(kind){if(kind==='composing')composing++;if(kind==='recording')recording++;}},isConnected:()=>true}));
    try{await drainAll(Array.from({length:customers},async(_,i)=>{const at=performance.now(),op=presence[Math.floor(i/customersPerCompany)],audio=i%4===0;
      await op.run({phone:customerJid(i).split('@')[0],chatJid:customerJid(i),isCurrent:()=>true,action:audio?{type:'audio',asset:{duration_ms:800}}:{type:'text',text:'Synthetic response'}},
        async({beforeSend})=>{active++;peakActive=Math.max(active,peakActive);try{await beforeSend();completed++;}finally{active--;}});
      times.push(performance.now()-at);
    }));for(const p of presence)assert.deepEqual(p.diagnostics(),{active:0,conversations:0});}finally{for(const p of presence)p.close();}
    assert.equal(completed,customers);assert.equal(peakActive,customers);assert.ok(composing>0&&recording>0);
    return {completed,peak_independent_conversations:peakActive,composing_events:composing,recording_events:recording,latency:summary(times)};
  });
  await measure('admission_pressure','service',async()=>{
    const gates=Array.from({length:companies},()=>makeSendCapacity({now:()=>0}));let admitted=0,refused=0;
    await Promise.all(Array.from({length:customers},(_,i)=>gates[Math.floor(i/customersPerCompany)].acquire().then(()=>admitted++).catch(()=>refused++)));
    assert.equal(admitted,250);assert.equal(refused,250);
    return {policy_per_channel_per_minute:5,admitted_immediately:admitted,refused_without_network:refused,all_five_hundred_immediate_replies_supported:false};
  });
  report.synthetic_storage_bytes=dirBytes(root);report.correctness_passed=true;
}catch(error){report.failure={stage,name:['AssertionError','Error','TypeError'].includes(error?.name)?error.name:'Error',code:'OFFLINE_QUALIFICATION_FAILED'};process.exitCode=1;}
finally{
  try{if(!helperClosed)peerSummary=await rpc('shutdown');}catch{report.helper_shutdown_requested=false;}
  let helperExit=await Promise.race([helperExited,delay(5000).then(()=>null)]);
  if(!helperExit){child.kill();helperExit=await helperExited;report.helper_forced_stop=true;}
  report.helper_exit=helperExit;report.helper_summary=peerSummary;report.helper_stderr_bytes_suppressed=helperStderrBytes;
  const service=Object.entries(report.stages).filter(([,v])=>v.category==='service'),app=Object.entries(report.stages).filter(([,v])=>v.category!=='synthetic_setup');
  const failed=service.filter(([,v])=>v.application.event_loop.max_timer_stall_ms>=10000||v.application.event_loop.timer_ticks<1).map(([name])=>name);
  report.offline_service_criteria_violations=failed;
  report.source_hashes_at_finish=sourceHashes();
  report.source_files_changed_during_run=sourceFiles.filter(name=>report.source_hashes_at_finish[name]!==sourceAtStart[name]);
  report.source_snapshot_qualified=report.source_files_changed_during_run.length===0;
  report.offline_service_targets_passed=report.correctness_passed&&failed.length===0&&service.length===6&&report.source_snapshot_qualified;
  report.offline_all_application_phases_violations=app.filter(([,v])=>v.application.event_loop.max_timer_stall_ms>=10000||v.application.event_loop.timer_ticks<1).map(([name])=>name);
  report.offline_all_application_phases_targets_passed=report.correctness_passed&&report.offline_all_application_phases_violations.length===0&&report.source_snapshot_qualified;
  report.elapsed_ms=performance.now()-started;report.peak_application_process_rss_bytes=peakAppRss;report.finished_at=new Date().toISOString();
  const resolved=path.resolve(root),temp=path.resolve(os.tmpdir());if(path.dirname(resolved)!==temp||!path.basename(resolved).startsWith('orion-offline-wa-capacity-'))throw Error('unsafe_qualification_cleanup');
  fs.rmSync(resolved,{recursive:true,force:true});report.synthetic_private_state_removed=!fs.existsSync(resolved);
  const outputArg=process.argv.indexOf('--output');if(outputArg>=0){const output=process.argv[outputArg+1];if(!output)throw Error('missing_output');
    fs.mkdirSync(path.dirname(path.resolve(output)),{recursive:true});fs.writeFileSync(output,JSON.stringify(report,null,2)+'\n');}
  process.stdout.write(JSON.stringify(report)+'\n');
}
