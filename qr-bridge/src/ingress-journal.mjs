/** Encrypted admission queue, separate from display history and outbound effects.
 * A provider message stays here until the backend confirms durable admission.
 * Blocked entries remain reviewable; capacity never evicts unacknowledged work. */
import {channelId,encryptedChannelStore} from './channel-storage.mjs';

const journalKey='incoming-admission-v1';
const reasons=new Set(['expired','scope_changed','policy_denied','invalid_message','authorization_revoked','broker_refused','identity_unresolved','business_started']);
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
  const active=new Set();
  function guard(){if(fault)throw Error('ingress_journal_unavailable');if(!isCurrent())throw Error('channel_owner_superseded');}
  function validEntry(entry){
    return entry&&typeof entry==='object'&&!Array.isArray(entry)&&validId(entry.id)&&validScope(entry.scope)&&
      (!namespace||sameNamespace(entry.scope,namespace))&&Number.isSafeInteger(entry.at)&&entry.at>0&&
      integer(entry.order)&&Number.isSafeInteger(entry.sequence)&&entry.sequence>=1&&
      entry.message&&typeof entry.message==='object'&&!Array.isArray(entry.message)&&
      ['pending','blocked'].includes(entry.state)&&
      (entry.state==='pending'?entry.blocked_reason===undefined&&entry.blocked_at===undefined:
        reasons.has(entry.blocked_reason)&&Number.isSafeInteger(entry.blocked_at)&&entry.blocked_at>=0)&&
      // Quarantine metadata must fit even when a source filled its entry limit.
      size({...entry,state:'pending',blocked_reason:undefined,blocked_at:undefined})<=maxEntryBytes;
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
    const length=size(value);if(length>maxBytes)throw Error('invalid_ingress_journal');return length;
  }
  try{data=store.get(journalKey)||{version:1,next_sequence:1,entries:[]};bytes=validate(data);}
  catch(error){if(error.message==='channel_owner_superseded')throw error;throw new Error('ingress_journal_corrupt',{cause:error});}
  function persist(next){
    guard();const length=validate(next);
    try{store.set(journalKey,next);}
    catch(error){fault='ingress_journal_write_failed';throw new Error('ingress_journal_unavailable',{cause:error});}
    data=next;bytes=length;full=false;
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
      const next={version:1,next_sequence:data.next_sequence+1,entries:[...data.entries,entry]};
      // Reserve bounded space to mark every pending row blocked later. Filling
      // the queue must not prevent retaining an expiry or authorization result.
      if(size(next)+next.entries.filter(row=>row.state==='pending').length*128>maxBytes)return capacity();
      persist(next);return {fresh:true,entry:clone(entry)};
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
    complete(id){
      guard();if(!validId(id))throw Error('invalid_ingress_id');
      const entry=data.entries.find(value=>value.id===id);if(!entry)return false;
      if(entry.state!=='blocked'||entry.blocked_reason!=='business_started'||!active.has(id)){
        throw Error('ingress_journal_completion_not_owned');
      }
      persist({...data,entries:data.entries.filter(value=>value.id!==id)});active.delete(id);return true;
    },
    // Session teardown may already have lost its write lease. Revoking these
    // volatile capabilities needs no I/O; all started payloads remain durable
    // and immediately become review work. This cannot clear or retry an entry.
    abandon(){active.clear();},
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
        blocked=data.entries.length-pending.length-working;
      return {state:fault?'unavailable':full?'full':'ok',error_code:fault||(full?'ingress_journal_full':null),
        pending:pending.length,blocked,active:working,entries:data.entries.length,bytes,
        oldest_at:data.entries.length?Math.min(...data.entries.map(entry=>entry.at)):null};
    }
  };
}
