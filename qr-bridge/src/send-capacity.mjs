/** Bounded rolling-window admission for genuine, consented transactional sends.
 * This is backpressure, never a request to bypass WhatsApp limits.
 * No network relay or automated retry happens inside this queue.
 */
export function makeSendCapacity({limit=5,windowMs=60000,maxWaiters=40,maxWaitMs=180000,
  now=Date.now,sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms))}={}){
  if(!Number.isSafeInteger(limit)||limit<1||!Number.isSafeInteger(windowMs)||windowMs<1||
     !Number.isSafeInteger(maxWaiters)||maxWaiters<1||maxWaiters>200||
     !Number.isSafeInteger(maxWaitMs)||maxWaitMs<1||maxWaitMs>600000)
    throw Error('Invalid send capacity');
  const times=[];let waiting=0;
  const unavailable=()=>Error('A fila de mensagens está ocupada. Nenhuma mensagem foi enviada.');
  return {
    async acquire({wait=false,isCurrent=()=>true}={}){
      if(!isCurrent())throw Error('A conexão ou autorização mudou antes do envio. Nenhuma mensagem foi enviada.');
      const start=now();
      let held=false;
      try{
        for(;;){
          if(!isCurrent())throw Error('A conexão ou autorização mudou antes do envio. Nenhuma mensagem foi enviada.');
          const at=now();
          while(times.length&&at-times[0]>=windowMs)times.shift();
          // Atomic in JS: no await occurs between checking the window and
          // reserving an admission, even with dozens of concurrent callers.
          if(times.length<limit){times.push(at);return;}
          if(!wait)throw Error('Aguarde um minuto antes de enviar mais mensagens');
          if(!held){
            if(waiting>=maxWaiters)throw unavailable();
            waiting++;held=true;
          }
          if(at-start>=maxWaitMs)throw unavailable();
          await sleep(Math.min(1000,Math.max(1,Math.min(times[0]+windowMs-at,maxWaitMs-(at-start)))));
        }
      }finally{if(held)waiting--;}
    },
    pending(){return waiting;},
    clear(){times.length=0;}
  };
}
