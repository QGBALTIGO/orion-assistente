import {shouldCacheLinkedSelfText} from './retry-capture-policy.mjs';
import {timestampMs} from './linked-session-health.mjs';
import {privateRetryPeer,retryIdentityMatches} from './retry-identity.mjs';

const validId=value=>typeof value==='string'&&/^[A-Za-z0-9._:-]{1,160}$/.test(value);

/** Capture only current, authenticated self echoes admitted by the existing
 * text policy. This never sends, resolves aliases, imports history, or grants
 * retry authority: readAuthorizedRetry still checks the requesting recipient.
 * `record` receives only an internal id and fixed classifications; transport
 * diagnostics HMAC the id before it leaves the process.
 */
export async function captureManagedRetryMessages(event,{
  store,isCurrent,assertCurrent,now=Date.now,record=()=>{}
}={}){
  const result={captured:0,skipped:0,failed:0};
  const report=(id,category)=>{
    try{record('retry_capture',validId(id)?id:null,{category,cached:['captured','existing'].includes(category)});}catch{}
  };
  if(!['notify','append'].includes(event?.type)||!Array.isArray(event?.messages))return result;
  // Avoid loading a historical dump or retaining an unbounded pending batch.
  const own=event.messages.slice(0,1000).filter(message=>message?.key?.fromMe===true);
  if(!own.length)return result;
  if(typeof isCurrent!=='function'||typeof assertCurrent!=='function'||typeof store?.entry!=='function'||typeof store?.save!=='function'){
    result.failed=own.length;report(null,'configuration_error');return result;
  }
  const current=()=>{try{return isCurrent()===true;}catch{return false;}};
  if(!current()){result.skipped=own.length;report(null,'inactive_socket');return result;}
  try{await assertCurrent();}catch{result.skipped=own.length;report(null,'fence_rejected');return result;}
  for(const message of own){
    const key=message.key,id=key.id;
    if(!current()){result.skipped++;report(id,'inactive_socket');continue;}
    if(!shouldCacheLinkedSelfText(message)){result.skipped++;report(id,'ineligible_content');continue;}
    if(!privateRetryPeer(key.remoteJid)){result.skipped++;report(id,'invalid_peer');continue;}
    let at,clock;
    try{at=timestampMs(message.messageTimestamp);clock=now();}catch{}
    if(!at||!Number.isFinite(clock)){result.skipped++;report(id,'invalid_timestamp');continue;}
    if(at<clock-15*60*1000){result.skipped++;report(id,'stale_message');continue;}
    if(at>clock+60000){result.skipped++;report(id,'future_message');continue;}
    try{
      // An echo may be the exact proto already written before relay. Preserve
      // its original bytes, expiry and origin, including across reconnects.
      if(store.entry(key)){result.skipped++;report(id,'existing');continue;}
      if(!current()){result.skipped++;report(id,'inactive_socket');continue;}
      if(store.save(key,message.message,{origin:'linked_self'})!==true){result.failed++;report(id,'storage_failed');continue;}
      result.captured++;report(id,'captured');
    }catch{result.failed++;report(id,'storage_failed');}
  }
  return result;
}

/** Manual owner commands must remain manual after their retry copy is saved.
 * Old records without origin keep the pre-existing bridge-generated meaning.
 */
export function isBridgeRetryMessage(store,key){
  if(!validId(key?.id)||key.fromMe!==true||!privateRetryPeer(key.remoteJid))return false;
  const row=store.entry(key);
  return !!(row?.message&&row.identity?.id===key.id&&row.identity?.peer&&row.identity.fromMe===true&&
    retryIdentityMatches(row.identity,key)&&row.origin!=='linked_self');
}
