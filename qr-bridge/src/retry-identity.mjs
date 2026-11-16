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
export async function readAuthorizedRetry(store,key,{socket,isCurrent=()=>true,assertCurrent=async()=>{}}={}){
  try{
    const requested=privateRetryPeer(key?.remoteJid);
    if(!isCurrent()||!requested||key?.fromMe!==true)return undefined;
    // The SDK encrypts to participant when supplied. A private retry cannot
    // redirect an otherwise correctly bound message to an unrelated account.
    if(key.participant!==undefined){
      const participant=privateRetryPeer(key.participant);
      const own=[privateRetryPeer(socket?.user?.id),privateRetryPeer(socket?.user?.lid)].filter(Boolean);
      if(!participant||(participant!==requested&&!own.includes(participant)))return undefined;
    }
    const row=store.entry?.(key);
    if(!row?.identity?.peer||row.identity.fromMe!==true||row.identity.id!==key.id)return undefined;
    await assertCurrent();if(!isCurrent())return undefined;
    if(row.identity.peer===requested)return row.message;
    const peers=[row.identity.peer,requested],pn=peers.find(x=>x.endsWith('@s.whatsapp.net')),lid=peers.find(x=>x.endsWith('@lid'));
    if(!pn||!lid||typeof socket?.onWhatsApp!=='function')return undefined;
    const phone=pn.split('@')[0];let timer;
    let found;
    try{found=await Promise.race([socket.onWhatsApp(phone),new Promise(resolve=>{timer=setTimeout(()=>resolve(null),5000);timer.unref?.();})]);}
    finally{clearTimeout(timer);}
    await assertCurrent();if(!isCurrent())return undefined;
    // Never relabel a numeric LID as a telephone or trust fields in the retry.
    if(!Array.isArray(found)||found.length!==1||found[0]?.exists!==true||
        privateRetryPeer(found[0].jid)!==pn||privateRetryPeer(found[0].lid)!==lid)return undefined;
    return row.message;
  }catch{return undefined;}
}
