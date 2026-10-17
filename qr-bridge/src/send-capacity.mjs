/** Shared rolling-window admission. Waiting never transmits or retries messages. */
export function makeSendCapacity({limit=5,windowMs=60000,now=Date.now,
  sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms))}={}){
  if(!Number.isSafeInteger(limit)||limit<1||!Number.isSafeInteger(windowMs)||windowMs<1)
    throw Error('Invalid send capacity');
  const times=[];
  return {
    async acquire({wait=false,isCurrent=()=>true}={}){
      for(;;){
        if(!isCurrent())throw Error('A conexão ou autorização mudou antes do envio. Nenhuma mensagem foi enviada.');
        const at=now();
        while(times.length&&at-times[0]>=windowMs)times.shift();
        // No await between checking and reserving, including concurrent callers.
        if(times.length<limit){times.push(at);return;}
        if(!wait)throw Error('Aguarde um minuto antes de enviar mais mensagens');
        await sleep(Math.min(1000,Math.max(1,times[0]+windowMs-at)));
      }
    },
    clear(){times.length=0;}
  };
}
