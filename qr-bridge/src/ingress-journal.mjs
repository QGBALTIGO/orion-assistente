/** Encrypted admission queue, separate from display history and outbound effects.
 * A provider message stays here until the backend confirms durable admission.
 * Blocked entries remain reviewable; capacity never evicts unacknowledged work. */
import {channelId,encryptedChannelStore} from './channel-storage.mjs';
import {createHash,randomUUID} from 'node:crypto';

const journalKey='incoming-admission-v1';
const reasons=new Set(['expired','scope_changed','policy_denied','invalid_message','authorization_revoked','broker_refused','identity_unresolved','business_started','preparation_failed','operator_do_not_replay']);
const preparationFailures=new Set(['prepare_failed','needs_clarification']);
const deviceNamespaceKeys=['tenant_id','channel_id','device_id','generation'];
const legacyNamespaceKeys=['tenant_id','channel_id','session_digest'];
const object=value=>value&&typeof value==='object'&&!Array.isArray(value);
const exactKeys=(value,keys)=>object(value)&&Object.keys(value).length===keys.length&&keys.every(key=>Object.hasOwn(value,key));
const legacyScope=value=>exactKeys(value,legacyNamespaceKeys)&&channelId(value.tenant_id)&&channelId(value.channel_id)&&
  typeof value.session_digest==='string'&&/^[0-9a-f]{64}$/.test(value.session_digest);
const deviceNamespace=value=>exactKeys(value,deviceNamespaceKeys)&&deviceNamespaceKeys.every(key=>channelId(value[key]));
const validId=value=>typeof value==='string'&&value.length>=1&&value.length<=256&&!/[\u0000-\u001f\u007f]/.test(value);
const integer=value=>Number.isSafeInteger(value)&&value>=0;
const validScope=value=>legacyScope(value)||(exactKeys(value,[...deviceNamespaceKeys,'settings_revision'])&&
  deviceNamespaceKeys.every(key=>channelId(value[key]))&&Number.isSafeInteger(value.settings_revision)&&value.settings_revision>=1);
const sameNamespace=(a,b)=>{
  const legacy=Object.hasOwn(a,'session_digest');if(legacy!==Object.hasOwn(b,'session_digest'))return false;
  return (legacy?legacyNamespaceKeys:deviceNamespaceKeys).every(key=>a[key]===b[key]);
};

export function createIngressJournal({directory,secret,serialize=JSON.stringify,deserialize=JSON.parse,isCurrent=()=>true,
  now=Date.now,maxEntries=512,maxBytes=8*1024*1024,maxEntryBytes=512*1024,namespace}={}){
  if(!Number.isSafeInteger(maxEntries)||maxEntries<1||maxEntries>10000||
      !Number.isSafeInteger(maxBytes)||maxBytes<256||maxBytes>12*1024*1024||
      !Number.isSafeInteger(maxEntryBytes)||maxEntryBytes<128||maxEntryBytes>maxBytes||
      namespace&&!legacyScope(namespace)&&!deviceNamespace(namespace)){
    throw Error('invalid_ingress_journal_options');
  }
  const store=encryptedChannelStore({directory,secret,serialize,deserialize,isCurrent});
  const encode=value=>{const encoded=serialize(value);if(typeof encoded!=='string')throw Error('invalid_ingress_serialization');return encoded;};
  const clone=value=>deserialize(encode(value));
  const size=value=>Buffer.byteLength(encode(value),'utf8');
  let data,bytes=0,fault=null,full=false;
  // Business work cannot be replayed after an ambiguous result. A persisted
  // start is only completable by the live instance that wrote it; a new owner
  // sees review work, never an invitation to repeat domain mutations or sends.
  const active=new Set(),failureNotices=new Set(),noticeAttempts=new Set();
  function guard(){if(fault)throw Error('ingress_journal_unavailable');if(!isCurrent())throw Error('channel_owner_superseded');}
  const validRevision=value=>Number.isSafeInteger(value)&&value>=1;
  const hash=value=>createHash('sha256').update(encode(value)).digest('hex');
  const hex64=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
  const evidenceKeys=['kind','receipt_sha256','build_sha','event_at'];
  const validEvidence=value=>exactKeys(value,evidenceKeys)&&['operator_attested_prepare_failed','not_started','operator_attested_uncertain','operator_attested_no_replay'].includes(value.kind)&&
    hex64(value.receipt_sha256)&&typeof value.build_sha==='string'&&/^[a-f0-9]{40}$/.test(value.build_sha)&&integer(value.event_at);
  const safeReviewBatch=entries=>entries.length<=2||entries.every(item=>item?.evidence?.kind==='not_started');
  const operatorEligible=(entry,kind)=>{
    if(!entry||active.has(entry.id))return false;
    if(legacyScope(entry.scope)){
      if(kind==='operator_attested_uncertain')return entry.state==='blocked'&&entry.blocked_reason==='business_started'&&
        ['preparing','consuming'].includes(entry.work?.phase);
      if(entry.work!==undefined)return false;
      return kind==='operator_attested_prepare_failed'?entry.state==='blocked'&&entry.blocked_reason==='business_started':
        kind==='not_started'&&(entry.state==='pending'||entry.state==='blocked'&&entry.blocked_reason==='expired');
    }
    if(entry.work!==undefined)return false;
    if(!validScope(entry.scope))return false;
    // For a subscription device the operator can retire only blocked work.
    // The encrypted source remains reviewable; no backend event, send, ASR or
    // other side effect is replayed. Unfinished work is never silently skipped.
    if(entry.state!=='blocked')return false;
    return kind==='operator_attested_uncertain'?entry.blocked_reason==='business_started':
      kind==='operator_attested_no_replay'&&!['business_started','preparation_failed','operator_do_not_replay'].includes(entry.blocked_reason);
  };
  function validWork(entry){
    const work=entry.work;
    if(work===undefined)return !['preparation_failed','operator_do_not_replay'].includes(entry.blocked_reason);
    if(work?.phase==='operator_retired')return (legacyScope(entry.scope)||validScope(entry.scope))&&entry.state==='blocked'&&
      entry.blocked_reason==='operator_do_not_replay'&&exactKeys(work,['phase','operation_id','peer_tag','review_required'])&&
      channelId(work.operation_id)&&hex64(work.peer_tag)&&typeof work.review_required==='boolean';
    const base=['phase','attempt_id','peer_tag','intake_revision','control_revision'];
    if(!legacyScope(entry.scope)||entry.state!=='blocked'||!object(work)||!channelId(work.attempt_id)||
        !/^[a-f0-9]{64}$/.test(work.peer_tag)||!validRevision(work.intake_revision)||
        !(work.control_revision===null||validRevision(work.control_revision)))return false;
    if(work.phase==='preparing')return entry.blocked_reason==='business_started'&&exactKeys(work,base);
    if(work.phase==='consuming')return entry.blocked_reason==='business_started'&&
      exactKeys(work,[...base,'review_stamp'])&&typeof work.review_stamp==='string'&&/^[a-f0-9]{64}$/.test(work.review_stamp);
    return work.phase==='preparation_failed'&&entry.blocked_reason==='preparation_failed'&&
      exactKeys(work,[...base,'failure_code','failed_at','review_required','notice_operation_id','notice_state'])&&
      preparationFailures.has(work.failure_code)&&Number.isSafeInteger(work.failed_at)&&work.failed_at>=0&&
      typeof work.review_required==='boolean'&&channelId(work.notice_operation_id)&&
      ['pending','attempted','accepted','not_sent','unknown'].includes(work.notice_state);
  }
  const inertFailure=entry=>entry.work?.phase==='preparation_failed'&&entry.blocked_reason==='preparation_failed'||
    entry.work?.phase==='operator_retired'&&entry.blocked_reason==='operator_do_not_replay';
  const reviewStamp=peer=>createHash('sha256').update(JSON.stringify(data.entries.filter(entry=>
    inertFailure(entry)&&entry.work.peer_tag===peer).map(entry=>
    [entry.sequence,entry.work.notice_operation_id??entry.work.operation_id]).sort(([a],[b])=>a-b))).digest('hex');
  const reserve=entry=>entry.work?.phase==='preparing'||inertFailure(entry)&&['pending','attempted'].includes(entry.work.notice_state)?512:
    entry.state==='pending'?128:0;
  function validEntry(entry){
    return entry&&typeof entry==='object'&&!Array.isArray(entry)&&validId(entry.id)&&validScope(entry.scope)&&
      (!namespace||sameNamespace(entry.scope,namespace))&&Number.isSafeInteger(entry.at)&&entry.at>0&&
      integer(entry.order)&&Number.isSafeInteger(entry.sequence)&&entry.sequence>=1&&
      entry.message&&typeof entry.message==='object'&&!Array.isArray(entry.message)&&
      ['pending','blocked'].includes(entry.state)&&
      (entry.state==='pending'?entry.blocked_reason===undefined&&entry.blocked_at===undefined:
        reasons.has(entry.blocked_reason)&&Number.isSafeInteger(entry.blocked_at)&&entry.blocked_at>=0)&&
      // Quarantine metadata must fit even when a source filled its entry limit.
      validWork(entry)&&size({...entry,state:'pending',blocked_reason:undefined,blocked_at:undefined,work:undefined})<=maxEntryBytes;
  }
  function validate(value){
    if(!value||value.version!==1||!Number.isSafeInteger(value.next_sequence)||value.next_sequence<1||
        !Array.isArray(value.entries)||value.entries.length>maxEntries)throw Error('invalid_ingress_journal');
    const ids=new Set(),sequences=new Set();
    for(const entry of value.entries){
      if(!validEntry(entry)||ids.has(entry.id)||sequences.has(entry.sequence)||entry.sequence>=value.next_sequence){
        throw Error('invalid_ingress_journal');
      }
      ids.add(entry.id);sequences.add(entry.sequence);
    }
    if(value.reviews!==undefined){
      if(!Array.isArray(value.reviews)||value.reviews.length>16)throw Error('invalid_ingress_reviews');
      const operations=new Set();
      for(const review of value.reviews){
        if(!exactKeys(review,['operation_id','request_sha256','actor_id','authority','applied_at','before_snapshot_sha256',
          'after_entries_sha256','backup','entries'])||!channelId(review.operation_id)||operations.has(review.operation_id)||
          !channelId(review.actor_id)||review.authority!=='platform_owner_proxy'||!integer(review.applied_at)||
          ![review.request_sha256,review.before_snapshot_sha256,review.after_entries_sha256].every(hex64)||
          !exactKeys(review.backup,['reference','sha256','bytes'])||review.backup.reference!==review.operation_id||
          !hex64(review.backup.sha256)||!integer(review.backup.bytes)||review.backup.bytes<28||
          !Array.isArray(review.entries)||!review.entries.length||review.entries.length>32||
          !safeReviewBatch(review.entries))throw Error('invalid_ingress_reviews');
        operations.add(review.operation_id);
        for(const item of review.entries){
          if(!exactKeys(item,['source_id','fingerprint','sequence','prior_state','prior_reason','evidence'])||
              !validId(item.source_id)||!hex64(item.fingerprint)||!validRevision(item.sequence)||
              !['pending','blocked'].includes(item.prior_state)||!(item.prior_reason===null||reasons.has(item.prior_reason))||
              !validEvidence(item.evidence))throw Error('invalid_ingress_reviews');
        }
      }
    }
    for(const entry of value.entries.filter(entry=>entry.work?.phase==='operator_retired')){
      if(!value.reviews?.some(review=>review.operation_id===entry.work.operation_id&&review.entries.some(item=>
        item.source_id===entry.id&&item.sequence===entry.sequence)))throw Error('invalid_ingress_reviews');
    }
    const length=size(value);if(length>maxBytes)throw Error('invalid_ingress_journal');return length;
  }
  try{data=store.get(journalKey)||{version:1,next_sequence:1,entries:[]};bytes=validate(data);
    full=data.entries.length>=maxEntries&&data.entries.every(inertFailure);}
  catch(error){if(error.message==='channel_owner_superseded')throw error;throw new Error('ingress_journal_corrupt',{cause:error});}
  function persist(next,{admission=false}={}){
    guard();const length=validate(next);
    try{store.set(journalKey,next);}
    catch(error){fault='ingress_journal_write_failed';throw new Error('ingress_journal_unavailable',{cause:error});}
    const remainsFull=next.entries.length>=maxEntries&&next.entries.every(inertFailure)||
      full&&!admission&&next.entries.length>=data.entries.length;
    data=next;bytes=length;full=remainsFull;
  }
  function capacity(){full=true;throw Error('ingress_journal_full');}
  return {
    append(value){
      guard();
      if(!value||!validId(value.id)||!validScope(value.scope)||!Number.isSafeInteger(value.at)||value.at<=0||
          !integer(value.order)||!value.message||typeof value.message!=='object'||Array.isArray(value.message)||
          namespace&&!sameNamespace(value.scope,namespace))throw Error('invalid_ingress_entry');
      const old=data.entries.find(entry=>entry.id===value.id);
      if(old){
        if(!sameNamespace(old.scope,value.scope))throw Error('ingress_journal_scope_conflict');
        return {fresh:false,entry:clone(old)};
      }
      if(data.entries.length>=maxEntries||data.next_sequence>=Number.MAX_SAFE_INTEGER)return capacity();
      const entry=clone({id:value.id,scope:value.scope,at:value.at,order:value.order,message:value.message,
        sequence:data.next_sequence,state:'pending'});
      if(size(entry)>maxEntryBytes)return capacity();
      const next={...data,next_sequence:data.next_sequence+1,entries:[...data.entries,entry]};
      // Reserve bounded space to mark every pending row blocked later. Filling
      // the queue must not prevent retaining an expiry or authorization result.
      if(size(next)+next.entries.reduce((sum,row)=>sum+reserve(row),0)>maxBytes)return capacity();
      persist(next,{admission:true});return {fresh:true,entry:clone(entry)};
    },
    pending(){guard();return clone(data.entries.filter(entry=>entry.state==='pending').sort((a,b)=>a.sequence-b.sequence));},
    ack(id){
      guard();if(!validId(id))throw Error('invalid_ingress_id');
      const entry=data.entries.find(value=>value.id===id);if(!entry)return false;
      if(entry.state!=='pending')throw Error('ingress_journal_entry_blocked');
      persist({...data,entries:data.entries.filter(value=>value.id!==id)});return true;
    },
    begin(ids){
      guard();if(!Array.isArray(ids)||!ids.length||ids.length>maxEntries||ids.some(id=>!validId(id))||new Set(ids).size!==ids.length){
        throw Error('invalid_ingress_begin');
      }
      const selected=ids.map(id=>data.entries.find(entry=>entry.id===id));
      if(selected.some(entry=>!entry))throw Error('ingress_journal_entry_missing');
      if(selected.some(entry=>entry.state!=='pending'))return false;
      const at=now();if(!Number.isSafeInteger(at)||at<0)throw Error('invalid_ingress_clock');
      const selectedIds=new Set(ids);
      persist({...data,entries:data.entries.map(entry=>selectedIds.has(entry.id)?
        {...entry,state:'blocked',blocked_reason:'business_started',blocked_at:at}:entry)});
      ids.forEach(id=>active.add(id));return true;
    },
    prepare(items){
      guard();if(!Array.isArray(items)||!items.length||items.length>maxEntries||
          items.some(item=>!exactKeys(item,['id','peer_tag','intake_revision','control_revision'])||!validId(item.id)||
            typeof item.peer_tag!=='string'||!/^[a-f0-9]{64}$/.test(item.peer_tag)||!validRevision(item.intake_revision)||
            !(item.control_revision===null||validRevision(item.control_revision)))||
          new Set(items.map(item=>item.id)).size!==items.length)throw Error('invalid_ingress_preparation');
      const selected=items.map(item=>data.entries.find(entry=>entry.id===item.id));
      if(selected.some(entry=>!entry||!legacyScope(entry.scope)))throw Error('ingress_journal_entry_missing');
      if(selected.some(entry=>entry.state!=='pending'))return false;
      const at=now();if(!Number.isSafeInteger(at)||at<0)throw Error('invalid_ingress_clock');
      const proofs=new Map(items.map(({id,...proof})=>[id,proof]));
      const next={...data,entries:data.entries.map(entry=>proofs.has(entry.id)?{...entry,state:'blocked',
        blocked_reason:'business_started',blocked_at:at,work:{phase:'preparing',attempt_id:randomUUID(),...proofs.get(entry.id)}}:entry)};
      if(size(next)+next.entries.reduce((sum,row)=>sum+reserve(row),0)>maxBytes)return capacity();
      persist(next);items.forEach(item=>active.add(item.id));return true;
    },
    consume(ids){
      guard();if(!Array.isArray(ids)||!ids.length||new Set(ids).size!==ids.length)throw Error('invalid_ingress_consume');
      const selected=ids.map(id=>data.entries.find(entry=>entry.id===id));
      if(selected.some(entry=>!entry||!active.has(entry.id)||!['preparing','consuming'].includes(entry.work?.phase)))throw Error('ingress_consume_not_owned');
      const chosen=new Set(ids);
      persist({...data,entries:data.entries.map(entry=>chosen.has(entry.id)&&entry.work.phase==='preparing'?
        {...entry,work:{...entry.work,phase:'consuming',review_stamp:reviewStamp(entry.work.peer_tag)}}:entry)});
    },
    consumptionProof(ids){
      guard();if(!Array.isArray(ids)||!ids.length)throw Error('invalid_ingress_consumption_proof');
      const selected=ids.map(id=>data.entries.find(entry=>entry.id===id));
      if(selected.some(entry=>!entry||!active.has(entry.id)||entry.work?.phase!=='consuming')||
          new Set(selected.map(entry=>entry.work.peer_tag)).size!==1||
          new Set(selected.map(entry=>entry.work.review_stamp)).size!==1)throw Error('ingress_consumption_proof_not_owned');
      return {peer_tag:selected[0].work.peer_tag,review_stamp:selected[0].work.review_stamp};
    },
    currentReviewStamp(peer){guard();return reviewStamp(peer);},
    zeroWrite(ids,result){
      guard();if(result?.state!=='superseded'||result.not_committed!==true||!Array.isArray(ids)||!ids.length)throw Error('invalid_ingress_zero_write');
      const chosen=new Set(ids);
      if(ids.some(id=>!active.has(id)||data.entries.find(entry=>entry.id===id)?.work?.phase!=='consuming'))throw Error('ingress_zero_write_not_owned');
      persist({...data,entries:data.entries.map(entry=>{if(!chosen.has(entry.id))return entry;
        const {review_stamp,...work}=entry.work;return {...entry,work:{...work,phase:'preparing'}};})});
    },
    failPreparation(ids,reason){
      guard();if(!Array.isArray(ids)||!ids.length||new Set(ids).size!==ids.length||!preparationFailures.has(reason))throw Error('invalid_ingress_preparation_failure');
      const selected=ids.map(id=>data.entries.find(entry=>entry.id===id));
      if(selected.some(entry=>!entry||!active.has(entry.id)||entry.work?.phase!=='preparing')||
          new Set(selected.map(entry=>entry.work.peer_tag)).size!==1)throw Error('ingress_preparation_failure_not_owned');
      const at=now();if(!Number.isSafeInteger(at)||at<0)throw Error('invalid_ingress_clock');
      const operation=randomUUID(),chosen=new Set(ids);
      persist({...data,entries:data.entries.map(entry=>chosen.has(entry.id)?{...entry,blocked_reason:'preparation_failed',
        work:{...entry.work,phase:'preparation_failed',failure_code:reason,failed_at:at,review_required:true,
          notice_operation_id:operation,notice_state:'pending'}}:entry)});
      ids.forEach(id=>active.delete(id));failureNotices.add(operation);
      return operation;
    },
    claimPreparationNotice(operation){
      guard();if(!failureNotices.has(operation))return false;
      const selected=data.entries.filter(entry=>inertFailure(entry)&&entry.work.notice_operation_id===operation);
      if(!selected.length||selected.some(entry=>entry.work.notice_state!=='pending'))return false;
      persist({...data,entries:data.entries.map(entry=>selected.includes(entry)?{...entry,work:{...entry.work,notice_state:'attempted'}}:entry)});
      failureNotices.delete(operation);noticeAttempts.add(operation);return true;
    },
    finishPreparationNotice(operation,result){
      guard();if(!noticeAttempts.has(operation))throw Error('ingress_preparation_notice_not_owned');
      const state=result?.accepted===true?'accepted':result?.state==='refused'?'not_sent':'unknown';
      persist({...data,entries:data.entries.map(entry=>inertFailure(entry)&&entry.work.notice_operation_id===operation?
        {...entry,work:{...entry.work,notice_state:state}}:entry)});
      noticeAttempts.delete(operation);
    },
    preparationFailures(){guard();return clone(data.entries.filter(inertFailure).map(entry=>({id:entry.id,at:entry.at,work:entry.work})));},
    // Private operator boundary. Callers must already authenticate MASTER plus
    // the platform-owner assertion. Neither method grants replay or effects.
    operatorSnapshot(){guard();const stored=store.get(journalKey);
      if(stored&&hash(stored)!==hash(data))throw Error('ingress_review_snapshot_conflict');
      return {snapshot_sha256:hash(data),data:clone(data),active_ids:[...active]};},
    operatorApply({expectedSnapshot,operationId,requestDigest,actorId,entries,backup}={}){
      guard();
      if(!channelId(operationId)||!channelId(actorId)||!hex64(requestDigest)||!hex64(expectedSnapshot)||
          !Array.isArray(entries)||!entries.length||entries.length>32||!safeReviewBatch(entries)||
          typeof backup!=='function')throw Error('invalid_ingress_review');
      const previous=data.reviews?.find(review=>review.operation_id===operationId);
      if(previous){if(previous.request_sha256!==requestDigest||previous.actor_id!==actorId)throw Error('ingress_review_operation_conflict');
        return {replayed:true,review:clone(previous),snapshot_sha256:hash(data)};}
      if(hash(data)!==expectedSnapshot)throw Error('ingress_review_snapshot_conflict');
      if((data.reviews?.length||0)>=16)return capacity();
      if(new Set(entries.map(item=>item?.id)).size!==entries.length)throw Error('invalid_ingress_review');
      const selected=entries.map(item=>{
        const entry=data.entries.find(row=>row.id===item?.id);
        if(!exactKeys(item,['id','fingerprint','peer_tag','evidence'])||!hex64(item.peer_tag)||!validEvidence(item.evidence)||
            !operatorEligible(entry,item.evidence.kind)||hash(entry)!==item.fingerprint||
            item.evidence.event_at<entry.at||
            legacyScope(entry.scope)&&item.evidence.event_at>entry.at+15*60*1000||
            item.evidence.event_at>now()+60000)throw Error('ingress_review_entry_refused');
        return {entry,item};
      });
      const at=now();if(!integer(at))throw Error('invalid_ingress_clock');
      const chosen=new Map(selected.map(({entry,item})=>[entry.id,item]));
      const nextEntries=data.entries.map(entry=>!chosen.has(entry.id)?entry:{...entry,state:'blocked',
        blocked_reason:'operator_do_not_replay',blocked_at:entry.blocked_at??at,work:{phase:'operator_retired',operation_id:operationId,
          peer_tag:chosen.get(entry.id).peer_tag,review_required:true}});
      const review={operation_id:operationId,request_sha256:requestDigest,actor_id:actorId,authority:'platform_owner_proxy',
        applied_at:at,before_snapshot_sha256:expectedSnapshot,after_entries_sha256:hash(nextEntries),
        backup:{reference:operationId,sha256:'0'.repeat(64),bytes:16*1024*1024},entries:selected.map(({entry,item})=>
          ({source_id:entry.id,fingerprint:item.fingerprint,sequence:entry.sequence,prior_state:entry.state,
            prior_reason:entry.blocked_reason??null,evidence:item.evidence}))};
      const next={...data,entries:nextEntries,reviews:[...(data.reviews||[]),review]};
      if(size(next)+next.entries.reduce((sum,row)=>sum+reserve(row),0)>maxBytes)return capacity();
      guard();review.backup=backup(operationId);
      // Backup is synchronous and private; a thrown failure never mutates the
      // journal. Recheck the complete CAS immediately before encrypted commit.
      guard();if(hash(data)!==expectedSnapshot)throw Error('ingress_review_snapshot_conflict');
      persist(next);return {replayed:false,review:clone(review),snapshot_sha256:hash(data)};
    },
    complete(id,{reviewDelivered=false}={}){
      guard();if(!validId(id))throw Error('invalid_ingress_id');
      const entry=data.entries.find(value=>value.id===id);if(!entry)return false;
      if(entry.state!=='blocked'||entry.blocked_reason!=='business_started'||!active.has(id)){
        throw Error('ingress_journal_completion_not_owned');
      }
      if(reviewDelivered&&entry.work?.phase!=='consuming')throw Error('ingress_review_delivery_not_owned');
      const delivered=reviewDelivered&&entry.work.review_stamp===reviewStamp(entry.work.peer_tag);
      persist({...data,entries:data.entries.filter(value=>value.id!==id).map(value=>delivered&&inertFailure(value)&&
        value.work.peer_tag===entry.work.peer_tag?{...value,work:{...value.work,review_required:false}}:value)});
      active.delete(id);return true;
    },
    // Session teardown may already have lost its write lease. Revoking these
    // volatile capabilities needs no I/O; all started payloads remain durable
    // and immediately become review work. This cannot clear or retry an entry.
    abandon(){active.clear();failureNotices.clear();noticeAttempts.clear();},
    block(id,reason){
      guard();if(!validId(id)||!reasons.has(reason))throw Error('invalid_ingress_block');
      const entry=data.entries.find(value=>value.id===id);if(!entry)return false;
      if(entry.state==='blocked'){
        // The catch/finally path relinquishes only its in-memory completion
        // capability. The already durable uncertain boundary remains intact.
        if(reason==='business_started'&&entry.blocked_reason==='business_started')active.delete(id);
        return true;
      }
      const at=now();if(!Number.isSafeInteger(at)||at<0)throw Error('invalid_ingress_clock');
      persist({...data,entries:data.entries.map(value=>value.id===id?{...value,state:'blocked',blocked_reason:reason,blocked_at:at}:value)});
      return true;
    },
    diagnostics(){
      const pending=data.entries.filter(entry=>entry.state==='pending'),working=fault?0:active.size,
        retained=data.entries.filter(inertFailure).length,blocked=data.entries.length-pending.length-working-retained;
      return {state:fault?'unavailable':full?'full':'ok',error_code:fault||(full?'ingress_journal_full':null),
        pending:pending.length,blocked,active:working,...(retained?{retained}:{}),entries:data.entries.length,bytes,
        oldest_at:data.entries.length?Math.min(...data.entries.map(entry=>entry.at)):null};
    }
  };
}
