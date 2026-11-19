/** Provider retries are not authorization by message id alone. The peer and
 * direction are persisted with the proto; PN/LID aliases require live proof.
 */
export function privateRetryPeer(value){
  const match=typeof value==='string'&&value.match(/^(\d{8,20})(?::\d+)?@(s\.whatsapp\.net|lid)$/);
  return match?match[1]+'@'+match[2]:null;
}
export function retryIdentity(key){
  return {id:String(key?.id||''),peer:privateRetryPeer(key?.remoteJid),fromMe:key?.fromMe===true};
}
export function retryIdentityMatches(stored,key){
  if(key?.remoteJid!==undefined&&(!stored?.peer||stored.peer!==privateRetryPeer(key.remoteJid)))return false;
  if(typeof key?.fromMe==='boolean'&&stored?.fromMe!==key.fromMe)return false;
  return true;
}
export function sameRetryIdentity(a,b){return a?.id===b?.id&&a?.peer===b?.peer&&a?.fromMe===b?.fromMe;}
const storeReasons=new Set(['absent','expired','capacity_evicted','invalid_id','unsafe_file','oversized_record',
  'corrupt_record','unsupported_format','decryption_failed','storage_error']);

export async function readAuthorizedRetry(store,key,{socket,isCurrent=()=>true,assertCurrent=async()=>{},onResult=()=>{}}={}){
  // Only classifications leave this callback, never keys, peers, errors or
  // plaintext. Observability failure must not change retry authorization.
  const finish=(reason,message)=>{
    try{const result=onResult({cached:message!==undefined,reason});result?.catch?.(()=>{});}catch{}
    return message;
  };
  let failure='authorization_failed';
  try{
    if(!isCurrent())return finish('stale_session');
    if(typeof key?.id!=='string'||!key.id)return finish('invalid_id');
    const requested=privateRetryPeer(key?.remoteJid);
    if(!requested)return finish('invalid_peer');
    if(key?.fromMe!==true)return finish('invalid_direction');
    // The SDK encrypts to participant when supplied. A private retry cannot
    // redirect an otherwise correctly bound message to an unrelated account.
    if(key.participant!==undefined){
      const participant=privateRetryPeer(key.participant);
      const own=[privateRetryPeer(socket?.user?.id),privateRetryPeer(socket?.user?.lid)].filter(Boolean);
      if(!participant)return finish('invalid_participant');
      if(participant!==requested&&!own.includes(participant))return finish('participant_mismatch');
    }
    failure='storage_error';
    const lookup=typeof store?.entryResult==='function'?store.entryResult(key):{entry:store?.entry?.(key),reason:'absent'};
    const row=lookup?.entry;
    if(!row)return finish(storeReasons.has(lookup?.reason)?lookup.reason:'absent');
    if(!row.identity?.peer)return finish('unbound_identity');
    if(row.identity.fromMe!==true)return finish('stored_direction');
    if(row.identity.id!==key.id)return finish('identity_id_mismatch');
    if(!row.message||typeof row.message!=='object')return finish('invalid_content');
    failure='authorization_failed';
    await assertCurrent();if(!isCurrent())return finish('stale_session');
    if(row.identity.peer===requested)return finish('hit',row.message);
    const peers=[row.identity.peer,requested],pn=peers.find(x=>x.endsWith('@s.whatsapp.net')),lid=peers.find(x=>x.endsWith('@lid'));
    if(!pn||!lid)return finish('peer_mismatch');
    if(typeof socket?.onWhatsApp!=='function')return finish('alias_proof_unavailable');
    const phone=pn.split('@')[0];let timer,timedOut=false;
    let found;
    failure='alias_proof_error';
    try{found=await Promise.race([socket.onWhatsApp(phone),new Promise(resolve=>{timer=setTimeout(()=>{timedOut=true;resolve(null);},5000);timer.unref?.();})]);}
    finally{clearTimeout(timer);}
    failure='authorization_failed';
    await assertCurrent();if(!isCurrent())return finish('stale_session');
    if(timedOut)return finish('alias_proof_timeout');
    // Never relabel a numeric LID as a telephone or trust fields in the retry.
    if(!Array.isArray(found)||found.length!==1||found[0]?.exists!==true||
        privateRetryPeer(found[0].jid)!==pn||privateRetryPeer(found[0].lid)!==lid)return finish('alias_proof_denied');
    return finish('hit_verified_alias',row.message);
  }catch{return finish(failure);}
}
