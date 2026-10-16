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
      if(entries.has(id))entries.delete(id);
      entries.set(id,{message,expires:clock()+ttlMs});
      prune();
      return true;
    },
    get(key){
      if(!key?.id)return undefined;
      prune();
      return entries.get(String(key.id))?.message;
    },
    size(){prune();return entries.size},
    clear(){entries.clear()}
  };
}

/** A Baileys retorna no sendMessage após aceitação, não entrega no aparelho. */
export function deliveryFromAck(status){
  switch(Number(status)){
    case 0:return "failed";
    case 1:return "pending";
    case 2:return "server_accepted";
    case 3:return "delivered";
    case 4:return "read";
    case 5:return "played";
    default:return "pending";
  }
}
export function advanceDelivery(previous,next){
  const rank={pending:0,server_accepted:1,delivered:2,read:3,played:4};
  if(next==="failed")return previous==="pending"?"failed":previous;
  if(!(next in rank))return previous;
  return (rank[next]>= (rank[previous]??0))?next:previous;
}
