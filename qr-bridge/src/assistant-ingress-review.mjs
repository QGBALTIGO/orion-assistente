/** Private operator-only operational retirement (PRIMARY or device-scoped).
 * No queued message or transport/domain effect is replayed or resent.
 * This is a manual attestation,
 * not inference from missing logs, a replay lane, or a new sending authority. */
import fs from 'node:fs';
import path from 'node:path';
import {createHash,createHmac} from 'node:crypto';
import {channelId,digest,privateDirectory} from './channel-storage.mjs';

const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const exact=(value,keys)=>object(value)&&Object.keys(value).length===keys.length&&keys.every(key=>Object.hasOwn(value,key));
const hex=(value,length)=>typeof value==='string'&&new RegExp('^[a-f0-9]{'+length+'}$').test(value);
const integer=value=>Number.isSafeInteger(value)&&value>=0;
const legacyKinds=['operator_attested_prepare_failed','not_started'];
const deviceKinds=['operator_attested_uncertain','operator_attested_no_replay'];
const scopeKeys=['tenant_id','channel_id','session_digest','auth_epoch','owner','fence'];
const deviceScopeKeys=['tenant_id','channel_id','device_id','generation','settings_revision','auth_epoch','owner','fence'];
const validScope=(value,device=false)=>device?
  exact(value,deviceScopeKeys)&&[value.tenant_id,value.channel_id,value.device_id,value.generation,value.owner].every(channelId)&&
    Number.isSafeInteger(value.settings_revision)&&value.settings_revision>=1&&
    hex(value.auth_epoch,64)&&Number.isSafeInteger(value.fence)&&value.fence>=1:
  exact(value,scopeKeys)&&[value.tenant_id,value.channel_id,value.owner].every(channelId)&&
    hex(value.session_digest,64)&&hex(value.auth_epoch,64)&&Number.isSafeInteger(value.fence)&&value.fence>=1;
const canonical=value=>JSON.stringify(value,(_,item)=>object(item)?Object.fromEntries(Object.entries(item).sort(([a],[b])=>a.localeCompare(b))):item);
const hash=value=>createHash('sha256').update(value).digest('hex');
const error=code=>Object.assign(Error(code),{code,status:{invalid_ingress_review_request:400,
  ingress_review_backup_failed:503,ingress_review_capacity:503,ingress_review_storage_unavailable:503}[code]||409});
const trace=(secret,id)=>createHmac('sha256',secret).update('orion-trace-v1:'+id).digest('hex').slice(0,20);
const eligible=(entry,device=false)=>{
  if(entry.work!==undefined)return [];
  if(device){
    if(entry.state!=='blocked')return [];
    return entry.blocked_reason==='business_started'?['operator_attested_uncertain']:
      ['preparation_failed','operator_do_not_replay'].includes(entry.blocked_reason)?[]:['operator_attested_no_replay'];
  }
  return entry.state==='blocked'&&entry.blocked_reason==='business_started'?['operator_attested_prepare_failed']:
    entry.state==='pending'||entry.state==='blocked'&&entry.blocked_reason==='expired'?['not_started']:[];
};

function validateRequest(body,{device=false}={}){
  if(!channelId(body?.operation_id)||body.version!==1)throw error('invalid_ingress_review_request');
  if(body.mode==='dry_run'){
    if(!exact(body,['version','mode','operation_id','traces'])||!Array.isArray(body.traces)||body.traces.length>2||
        body.traces.some(value=>!hex(value,20))||new Set(body.traces).size!==body.traces.length)throw error('invalid_ingress_review_request');
  }else if(body.mode==='apply'){
    if(!exact(body,['version','mode','operation_id','expected','entries'])||!exact(body.expected,['scope','snapshot_sha256'])||
        !validScope(body.expected.scope,device)||!hex(body.expected.snapshot_sha256,64)||!Array.isArray(body.entries)||
        !body.entries.length||body.entries.length>2||new Set(body.entries.map(item=>item?.trace)).size!==body.entries.length)throw error('invalid_ingress_review_request');
    for(const item of body.entries){
      if(!exact(item,['trace','fingerprint','disposition','evidence'])||!hex(item.trace,20)||!hex(item.fingerprint,64)||
          item.disposition!=='do_not_replay'||!exact(item.evidence,['kind','receipt_sha256','build_sha','event_at'])||
          !(device?deviceKinds:legacyKinds).includes(item.evidence.kind)||!hex(item.evidence.receipt_sha256,64)||!hex(item.evidence.build_sha,40)||
          !integer(item.evidence.event_at))throw error('invalid_ingress_review_request');
    }
  }else throw error('invalid_ingress_review_request');
}

// Ciphertext is copied without decoding or moving the original. The backup is
// deliberately bound to its original directory/AAD; its UUID is a reference,
// never a caller-controlled filesystem path. Quotas here are local, not global.
function encryptedBackup(directory,operation,availableBytes){
  privateDirectory(directory,{create:false});
  const file=path.join(directory,digest('incoming-admission-v1')+'.enc');
  const read=file=>{
    const stat=fs.lstatSync(file);
    if(!stat.isFile()||stat.isSymbolicLink()||stat.size<28||stat.size>16*1024*1024||
        process.platform!=='win32'&&(stat.mode&0o077)!==0)throw error('ingress_review_backup_failed');
    const fd=fs.openSync(file,fs.constants.O_RDONLY|(fs.constants.O_NOFOLLOW||0));
    try{return fs.readFileSync(fd);}finally{fs.closeSync(fd);}
  };
  const bytes=read(file),sha=hash(bytes),backupDirectory=privateDirectory(path.join(directory,'operator-review-backups'));
  const target=path.join(backupDirectory,operation+'.enc');
  if(fs.existsSync(target)){
    const previous=read(target);
    if(previous.length!==bytes.length||hash(previous)!==sha)throw error('ingress_review_backup_failed');
    return {reference:operation,sha256:sha,bytes:bytes.length};
  }
  const files=fs.readdirSync(backupDirectory);
  if(files.length>=16||files.some(name=>!/^[-a-f0-9]{36}\.enc$/.test(name)))throw error('ingress_review_capacity');
  const free=availableBytes(directory);
  if(!Number.isSafeInteger(free)||free<64*1024*1024+bytes.length)throw error('ingress_review_capacity');
  const fd=fs.openSync(target,'wx',0o600);
  try{fs.writeFileSync(fd,bytes);fs.fsyncSync(fd);}finally{fs.closeSync(fd);}
  if(process.platform!=='win32'){
    const fd=fs.openSync(backupDirectory,'r');try{fs.fsyncSync(fd);}finally{fs.closeSync(fd);}
  }
  return {reference:operation,sha256:sha,bytes:bytes.length};
}

export function createAssistantIngressReview({journal,directory,secret,peerTag,serialize=JSON.stringify,device=false,
  availableBytes=directory=>{const stat=fs.statfsSync(directory);return Number(stat.bavail)*Number(stat.bsize);}}){
  const fingerprint=entry=>hash(serialize(entry));
  return async function review(body,{authority,getScope,isCurrent,assertCurrent,resolvePhone,allowedSender}={}){
    validateRequest(body,{device});
    if(authority?.authority!=='platform_owner_proxy'||!channelId(authority.actor_id))throw Object.assign(error('invalid_operator_assertion'),{status:401});
    const current=()=>isCurrent?.()===true;
    const guard=async()=>{if(!current())throw error('ingress_review_not_ready');await assertCurrent();
      if(!current())throw error('ingress_review_not_ready');};
    await guard();
    const scope=getScope();if(!validScope(scope,device))throw error('ingress_review_not_ready');
    const scopeCurrent=()=>current()&&canonical(getScope())===canonical(scope);
    if(body.mode==='apply'&&canonical(body.expected.scope)!==canonical(scope))throw error('ingress_review_scope_conflict');
    const snapshot=journal.operatorSnapshot();
    const immutable=device?['tenant_id','channel_id','device_id','generation']:['tenant_id','channel_id','session_digest'];
    if(snapshot.data.entries.some(entry=>immutable.some(key=>entry.scope[key]!==scope[key])||
        device&&(entry.scope.settings_revision===undefined||!Number.isSafeInteger(entry.scope.settings_revision))))
      throw error('ingress_review_scope_conflict');
    const publicEntry=entry=>({trace:trace(secret,entry.id),fingerprint:fingerprint(entry),at:entry.at,sequence:entry.sequence,
      state:entry.state,blocked_reason:entry.blocked_reason??null,phase:entry.work?.phase??null,
      eligible_as:snapshot.active_ids.includes(entry.id)?[]:eligible(entry,device)});
    if(body.mode==='dry_run'){
      const selected=body.traces.length?snapshot.data.entries.filter(entry=>body.traces.includes(trace(secret,entry.id))):snapshot.data.entries;
      await guard();if(!scopeCurrent()||journal.operatorSnapshot().snapshot_sha256!==snapshot.snapshot_sha256)throw error('ingress_review_snapshot_conflict');
      return {version:1,mode:'dry_run',scope,snapshot_sha256:snapshot.snapshot_sha256,
        entries:selected.slice(0,32).map(publicEntry),total_entries:snapshot.data.entries.length,truncated:selected.length>32,mutation:false};
    }
    const requestDigest=hash(canonical(body));
    const replay=snapshot.data.reviews?.find(item=>item.operation_id===body.operation_id);
    let entries=[];
    if(!replay){
      if(body.expected.snapshot_sha256!==snapshot.snapshot_sha256)throw error('ingress_review_snapshot_conflict');
      for(const item of body.entries){
        const found=snapshot.data.entries.filter(entry=>trace(secret,entry.id)===item.trace);
        if(found.length!==1||fingerprint(found[0])!==item.fingerprint||!eligible(found[0],device).includes(item.evidence.kind))throw error('ingress_review_entry_refused');
        const source=found[0],key=device?source.message?.key:source.message.raw?.key;
        if(device){
          if(typeof key?.id!=='string'||source.id!=='message:'+key.id||key.fromMe!==false||
              typeof source.message?.message!=='object')throw error('ingress_review_entry_refused');
          // Device addresses can be opaque LIDs. Never invent a PN or use an
          // operator-provided identity; this digest is not a sender identity.
          entries.push({id:source.id,fingerprint:item.fingerprint,peer_tag:createHmac('sha256',secret)
            .update('orion:device-ingress-no-replay:v1\0'+scope.tenant_id+'\0'+scope.device_id+'\0'+source.id).digest('hex'),evidence:item.evidence});
        }else{
          if(key?.id!==source.id||key.fromMe!==false||!['notify','append'].includes(source.message.type))throw error('ingress_review_entry_refused');
          const phone=await resolvePhone(key);
          if(typeof phone!=='string'||!/^\d{8,20}$/.test(phone)||allowedSender(phone)!==true)throw error('ingress_review_identity_unresolved');
          entries.push({id:source.id,fingerprint:item.fingerprint,peer_tag:peerTag(phone),evidence:item.evidence});
        }
      }
    }
    await guard();if(!scopeCurrent())throw error('ingress_review_scope_conflict');
    // Replay still goes through current authority/scope checks, but never needs
    // the old raw PN to remain resolvable and never writes or backs up again.
    if(replay)entries=body.entries.map(item=>({id:item.trace}));
    let result;
    try{result=journal.operatorApply({expectedSnapshot:body.expected.snapshot_sha256,operationId:body.operation_id,
      requestDigest,actorId:authority.actor_id,entries,backup:operation=>{
        try{return encryptedBackup(directory,operation,availableBytes);}catch(cause){
          if(cause.code==='ingress_review_capacity')throw cause;throw error('ingress_review_backup_failed');}
      }});
    }catch(cause){
      if(cause.code?.startsWith('ingress_review_'))throw cause;
      if(['ingress_review_snapshot_conflict','ingress_review_operation_conflict','ingress_review_entry_refused'].includes(cause.message))throw error(cause.message);
      if(cause.message==='ingress_journal_full')throw error('ingress_review_capacity');
      throw error('ingress_review_storage_unavailable');
    }
    return {version:1,mode:'apply',operation_id:body.operation_id,replayed:result.replayed,mutation:!result.replayed,
      before_snapshot_sha256:result.review.before_snapshot_sha256,after_snapshot_sha256:result.snapshot_sha256,
      after_entries_sha256:result.review.after_entries_sha256,backup:result.review.backup,review_required:true};
  };
}

export function collectIngressReviewBody(req,{device=false}={}){
  return new Promise((resolve,reject)=>{
    let size=0,settled=false;const chunks=[];
    const fail=()=>{if(!settled){settled=true;reject(error('invalid_ingress_review_request'));}};
    req.on('data',part=>{if(settled)return;size+=part.length;if(size>8192){fail();return;}chunks.push(Buffer.from(part));});
    req.on('end',()=>{if(settled)return;try{const body=JSON.parse(Buffer.concat(chunks).toString('utf8'));
      validateRequest(body,{device});settled=true;resolve(body);}catch{fail();}});
    req.on('aborted',fail);req.on('error',fail);
  });
}
