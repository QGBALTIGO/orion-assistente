/** Admission recovery only. A started preparation/turn is never replayed after
 * restart or an uncertain result; its encrypted input remains reviewable. */
import path from 'node:path';
import {createHash,createHmac} from 'node:crypto';
import {performance} from 'node:perf_hooks';
import {createIngressJournal} from './ingress-journal.mjs';
import {createAssistantIngressReview} from './assistant-ingress-review.mjs';

export const PREPARATION_FAILURE_REPLY='Não consegui processar essa mensagem com segurança. Nenhum agendamento foi alterado por ela. Pode escrever o pedido completo em uma mensagem? Vou conferir o resumo antes de qualquer confirmação.';

export function createAssistantIngress({directory,secret,scope,isCurrent=()=>true,serialize,deserialize,
  now=Date.now,ttlMs=15*60*1000,maxEntries=512,maxBytes=8*1024*1024}={}){
  if(!Number.isSafeInteger(ttlMs)||ttlMs<1||ttlMs>24*60*60*1000)throw Error('invalid_assistant_ingress_ttl');
  const namespace=Object.freeze(Object.fromEntries(Object.entries(scope||{}).sort(([a],[b])=>a.localeCompare(b)))),active=new Set(),admitted=new Set(),activeAt=new Map();
  const digest=createHash('sha256').update(JSON.stringify(namespace)).digest('hex');
  const journal=createIngressJournal({directory:path.join(directory,digest),secret,namespace,
    serialize,deserialize,isCurrent,now,maxEntries,maxBytes,maxEntryBytes:Math.min(512*1024,maxBytes)});
  const valid=entry=>entry.message&&['notify','append'].includes(entry.message.type)&&
    entry.message.raw?.key?.id===entry.id&&entry.message.raw?.key?.fromMe!==true;
  const peerTag=phone=>{
    if(typeof phone!=='string'||!/^\d{8,20}$/.test(phone))throw Error('invalid_assistant_ingress_peer');
    return createHmac('sha256',secret).update('orion:assistant-preparation-peer:v1\0'+digest+'\0'+phone).digest('hex');
  };
  const operatorReview=createAssistantIngressReview({journal,directory:path.join(directory,digest),secret,peerTag,serialize});
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
    operatorReview,
    append(message,type,at){
      if(!message?.key?.id||!['notify','append'].includes(type))throw Error('invalid_assistant_ingress');
      return journal.append({id:message.key.id,scope:namespace,at,order:0,message:{raw:message,type}});
    },
    pending,
    begin(ids){
      const fresh=ids.filter(id=>!active.has(id));
      const times=new Map(pending().map(entry=>[entry.id,entry.at]));
      if(fresh.length&&!journal.begin(fresh))throw Error('assistant_ingress_review_required');
      fresh.forEach(id=>{active.add(id);activeAt.set(id,times.get(id));});
    },
    prepare(ids,{phone,proofs}={}){
      if(!Array.isArray(ids)||!ids.length||!Array.isArray(proofs)||proofs.length!==ids.length)throw Error('invalid_assistant_preparation_proof');
      const times=new Map(pending().map(entry=>[entry.id,entry.at])),peer=peerTag(phone);
      const items=ids.map((id,index)=>({id,peer_tag:peer,intake_revision:proofs[index]?.revision,
        control_revision:Number.isSafeInteger(proofs[index]?.control_revision)&&proofs[index].control_revision>=1?proofs[index].control_revision:null}));
      if(!journal.prepare(items))throw Error('assistant_ingress_review_required');
      ids.forEach(id=>{active.add(id);activeAt.set(id,times.get(id));});
    },
    consume(ids){journal.consume(ids);},
    consumptionProof:ids=>journal.consumptionProof(ids),
    isProvenanceCurrent(phone,proof){
      try{return proof?.peer_tag===peerTag(phone)&&typeof proof.review_stamp==='string'&&
        /^[a-f0-9]{64}$/.test(proof.review_stamp)&&proof.review_stamp===journal.currentReviewStamp(proof.peer_tag);}catch{return false;}
    },
    allowsSelection(phone,proof){
      if(!this.requiresReview(phone))return proof===null||proof===undefined||this.isProvenanceCurrent(phone,proof);
      return this.isProvenanceCurrent(phone,proof)&&['navigation','summary'].includes(proof.kind);
    },
    zeroWrite(ids,result){journal.zeroWrite(ids,result);},
    requiresReview(phone){const peer=peerTag(phone);return journal.preparationFailures().some(entry=>
      entry.work.peer_tag===peer&&entry.work.review_required);},
    failPreparation(ids,reason){
      const started=ids.filter(id=>active.has(id));if(!started.length)return null;
      const operation=journal.failPreparation(started,reason);
      started.forEach(id=>{active.delete(id);activeAt.delete(id);admitted.delete(id);});
      const records=journal.preparationFailures().filter(entry=>entry.work.notice_operation_id===operation);
      const revisions=records.map(entry=>entry.work.control_revision);
      return {operation_id:operation,message_ids:started,expires_at:Math.min(...records.map(entry=>entry.at))+ttlMs,
        control_revision:revisions.every(value=>Number.isSafeInteger(value)&&value>=1)?Math.max(...revisions):null};
    },
    claimPreparationNotice:operation=>journal.claimPreparationNotice(operation),
    finishPreparationNotice:(operation,result)=>journal.finishPreparationNotice(operation,result),
    admitted(id){
      if(!journal.pending().some(entry=>entry.id===id))throw Error('assistant_ingress_admission_not_pending');
      admitted.add(id);
    },
    complete(id,{reviewDelivered=false}={}){if(!active.has(id))throw Error('assistant_ingress_completion_not_owned');
      const result=journal.complete(id,{reviewDelivered});active.delete(id);activeAt.delete(id);admitted.delete(id);return result;},
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
