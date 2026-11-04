/** Provider retries are not authorization by message id alone. The peer and
 * direction are persisted with the proto; aliases come only from the SDK's
 * authenticated identity mapping or a verified provider lookup, never payload.
 */
export function privateRetryPeer(value){
  if(typeof value!=='string'||value.length>128)return null;
  const pn=value.match(/^([1-9]\d{7,14})(?::\d{1,5})?@s\.whatsapp\.net$/);
  if(pn)return pn[1]+'@s.whatsapp.net';
  const lid=value.match(/^(\d{1,30})(?::\d{1,5})?@lid$/);
  return lid?lid[1]+'@lid':null;
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
  let timer;
  const timeout=new Promise(resolve=>{timer=setTimeout(()=>resolve(null),5000);timer.unref?.();});
  const bounded=work=>Promise.race([Promise.resolve().then(work),timeout]);
  try{
    const requested=privateRetryPeer(key?.remoteJid);
    if(!isCurrent()||!requested||key?.fromMe!==true)return undefined;
    const row=store.entry?.(key);
    if(!row?.identity?.peer||row.identity.fromMe!==true||row.identity.id!==key.id)return undefined;
    await assertCurrent();if(!isCurrent())return undefined;
    const proofs=new Map();
    async function samePeer(a,b){
      if(!a||!b)return false;
      if(a===b)return true;
      const pn=[a,b].find(x=>x.endsWith('@s.whatsapp.net')),lid=[a,b].find(x=>x.endsWith('@lid'));
      if(!pn||!lid)return false;
      const proofKey=pn+'\0'+lid;
      if(proofs.has(proofKey))return proofs.get(proofKey);
      const work=(async()=>{
        const mapping=socket?.signalRepository?.lidMapping;
        const mappedLid=typeof mapping?.getLIDForPN==='function'?await bounded(()=>mapping.getLIDForPN(pn)):null;
        const mappedPn=typeof mapping?.getPNForLID==='function'?await bounded(()=>mapping.getPNForLID(lid)):null;
        // A contradictory mapping is not permission to try an unrelated number.
        if(mappedLid!=null&&privateRetryPeer(mappedLid)!==lid||mappedPn!=null&&privateRetryPeer(mappedPn)!==pn)return false;
        if(mappedLid!=null||mappedPn!=null)return true;
        // Compatibility with pinned v6, whose onWhatsApp still returns LID.
        // v7 uses the persistent mapping above and needs no phone-online query.
        if(typeof socket?.onWhatsApp!=='function')return false;
        const found=await bounded(()=>socket.onWhatsApp(pn.split('@')[0]));
        return Array.isArray(found)&&found.length===1&&found[0]?.exists===true&&
          privateRetryPeer(found[0].jid)===pn&&privateRetryPeer(found[0].lid)===lid;
      })();
      proofs.set(proofKey,work);return work;
    }
    if(!await samePeer(row.identity.peer,requested))return undefined;
    if(key.participant!==undefined){
      const participant=privateRetryPeer(key.participant);
      if(!participant)return undefined;
      const own=[privateRetryPeer(socket?.user?.id),privateRetryPeer(socket?.user?.lid)].filter(Boolean);
      // The private recipient's verified PN/LID companion is valid. Merely
      // comparing strings rejects that recovery even when its identity matches.
      if(!own.includes(participant)&&!await samePeer(participant,requested))return undefined;
    }
    await assertCurrent();if(!isCurrent())return undefined;
    const fresh=store.entry?.(key);
    return fresh&&sameRetryIdentity(row.identity,fresh.identity)?fresh.message:undefined;
  }catch{return undefined;}
  finally{clearTimeout(timer);}
}
