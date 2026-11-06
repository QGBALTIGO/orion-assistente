/** Admission recovery only. A started preparation/turn is never replayed after
 * restart or an uncertain result; its encrypted input remains reviewable. */
import path from 'node:path';
import {createHash} from 'node:crypto';
import {performance} from 'node:perf_hooks';
import {createIngressJournal} from './ingress-journal.mjs';

export function createAssistantIngress({directory,secret,scope,isCurrent=()=>true,serialize,deserialize,
  now=Date.now,ttlMs=15*60*1000,maxEntries=512,maxBytes=8*1024*1024}={}){
  if(!Number.isSafeInteger(ttlMs)||ttlMs<1||ttlMs>24*60*60*1000)throw Error('invalid_assistant_ingress_ttl');
  const namespace=Object.freeze(Object.fromEntries(Object.entries(scope||{}).sort(([a],[b])=>a.localeCompare(b)))),active=new Set(),admitted=new Set(),activeAt=new Map();
  const digest=createHash('sha256').update(JSON.stringify(namespace)).digest('hex');
  const journal=createIngressJournal({directory:path.join(directory,digest),secret,namespace,
    serialize,deserialize,isCurrent,now,maxEntries,maxBytes});
  const valid=entry=>entry.message&&['notify','append'].includes(entry.message.type)&&
    entry.message.raw?.key?.id===entry.id&&entry.message.raw?.key?.fromMe!==true;
  function canConsume(ids=[]){
    const health=journal.diagnostics();
    if(!isCurrent()||health.state!=='ok'||health.blocked||ids.some(id=>!active.has(id)||now()-activeAt.get(id)>ttlMs))return false;
    // A successful current backend notice already persisted safety controls.
    // Other conversations may still be preparing without blocking this one.
    return !health.pending||journal.pending().every(entry=>admitted.has(entry.id));
  }
  function canAutomate(){
    const health=journal.diagnostics();
    return isCurrent()&&health.state==='ok'&&!health.blocked&&
      (!health.pending||journal.pending().every(entry=>admitted.has(entry.id)));
  }
  function pending(){
    const ready=[];
    for(const entry of journal.pending()){
      if(!valid(entry)){journal.block(entry.id,'invalid_message');continue;}
      if(entry.at<now()-ttlMs||entry.at>now()+60000){journal.block(entry.id,'expired');continue;}
      ready.push(entry);
    }
    return ready;
  }
  return {
    scope:namespace,
    append(message,type,at){
      if(!message?.key?.id||!['notify','append'].includes(type))throw Error('invalid_assistant_ingress');
      return journal.append({id:message.key.id,scope:namespace,at,order:0,message:{raw:message,type}});
    },
    pending,
    confirmIdentity:(id,phone)=>journal.confirmIdentity(id,phone),
    markAdmissionAttempted(id){
      // Identity lookup may have crossed the source TTL. Expire locally before
      // the first request; a prior request is still retained as ambiguous.
      if(!pending().some(entry=>entry.id===id))throw Error('assistant_ingress_admission_not_pending');
      return journal.markAdmissionAttempted(id);
    },
    begin(ids){
      const fresh=ids.filter(id=>!active.has(id));
      const times=new Map(pending().map(entry=>[entry.id,entry.at]));
      if(fresh.length&&!journal.begin(fresh))throw Error('assistant_ingress_review_required');
      fresh.forEach(id=>{active.add(id);activeAt.set(id,times.get(id));});
    },
    admitted(id){
      if(!journal.pending().some(entry=>entry.id===id))throw Error('assistant_ingress_admission_not_pending');
      journal.markAdmissionAttempted(id);
      admitted.add(id);
    },
    complete(id){if(!active.has(id))throw Error('assistant_ingress_completion_not_owned');
      const result=journal.complete(id);active.delete(id);activeAt.delete(id);admitted.delete(id);return result;},
    exclude(id){const result=journal.ack(id);admitted.delete(id);return result;},
    review(id,reason='business_started'){
      active.delete(id);activeAt.delete(id);admitted.delete(id);return journal.block(id,reason);
    },
    active:id=>active.has(id),
    canConsume,
    canAutomate,
    async waitForAdmission(ids,{isCurrent:authorized=()=>true,sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms)),
      maxWaitMs=10000,monotonic=()=>performance.now()}={}){
      if(!Number.isSafeInteger(maxWaitMs)||maxWaitMs<1||maxWaitMs>15000)throw Error('invalid_admission_wait');
      const deadline=monotonic()+maxWaitMs;
      for(;;){
        if(!authorized()||!isCurrent()||monotonic()>=deadline)return false;
        const health=journal.diagnostics();
        if(health.state!=='ok'||health.blocked||ids.some(id=>!active.has(id)||now()-activeAt.get(id)>ttlMs))return false;
        // Expiry is checked using the original source time while waiting.
        pending();
        if(canConsume(ids))return true;
        await sleep(Math.min(200,Math.max(1,deadline-monotonic())));
      }
    },
    abandon(){active.clear();activeAt.clear();admitted.clear();journal.abandon();},
    diagnostics:()=>journal.diagnostics()
  };
}
