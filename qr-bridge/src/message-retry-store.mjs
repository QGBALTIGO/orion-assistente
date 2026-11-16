import {retryIdentity,retryIdentityMatches,sameRetryIdentity} from './retry-identity.mjs';
/**
 * Cache transitório de mensagens para o callback getMessage do Baileys.
 * WhatsApp pode solicitar reenvio de uma mensagem que não conseguiu
 * descriptografar. Sem o texto original, Baileys não consegue recriptografar.
 *
 * Dados só em RAM, limite e TTL. Nunca expor por HTTP nem registrar em logs.
 */
export function makeMessageRetryStore({max=1000,ttlMs=24*60*60*1000,clock=Date.now}={}){
  const entries=new Map();
  function prune(){
    const now=clock();
    for(const [id,item] of entries)if(item.expires<=now)entries.delete(id);
    while(entries.size>max)entries.delete(entries.keys().next().value);
  }
  return {
    save(key,message){
      if(!key?.id || !message || typeof message!=="object")return false;
      prune();
      const id=String(key.id);
      const identity=retryIdentity(key),previous=entries.get(id);
      if(previous?.identity?.peer&&!sameRetryIdentity(previous.identity,identity))return false;
      if(entries.has(id))entries.delete(id);
      entries.set(id,{message,identity,expires:clock()+ttlMs});
      prune();
      return true;
    },
    get(key){
      if(!key?.id)return undefined;
      prune();
      const row=entries.get(String(key.id));
      return row&&retryIdentityMatches(row.identity,key)?row.message:undefined;
    },
    entry(key){prune();return entries.get(String(key?.id||''));},
    size(){prune();return entries.size},
    clear(){entries.clear()}
  };
}

/** A Baileys retorna no sendMessage após aceitação, não entrega no aparelho. */
export function deliveryFromAck(status){
  if(status===null||status===undefined)return null;
  switch(Number(status)){
    case 0:return "failed";
    case 1:return "pending";
    case 2:return "server_accepted";
    case 3:return "delivered";
    case 4:return "read";
    case 5:return "played";
    default:return null;
  }
}
export function advanceDelivery(previous,next){
  const rank={pending:0,server_accepted:1,delivered:2,read:3,played:4};
  if(next==="failed")return ["pending","server_accepted"].includes(previous)?"failed":previous;
  if(!(next in rank))return previous;
  return (rank[next]>= (rank[previous]??0))?next:previous;
}
