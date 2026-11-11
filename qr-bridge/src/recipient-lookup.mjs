/** A recipient lookup is read-only. Retry only a thrown/expired lookup within
 * the same authorized operation; never retry a relay or an identity verdict. */
export async function lookupRecipient(socket,phone,{assertCurrent=async()=>{},isCurrent=()=>true,
  schedule=setTimeout,cancel=clearTimeout,timeoutMs=5000,retryMs=300}={}){
  if(typeof socket?.onWhatsApp!=='function'||!Number.isSafeInteger(timeoutMs)||timeoutMs<1||
      !Number.isSafeInteger(retryMs)||retryMs<0)throw Error('recipient_lookup_unavailable');
  const guard=async()=>{await assertCurrent();if(!isCurrent())throw Error('recipient_lookup_superseded');};
  const once=()=>new Promise((resolve,reject)=>{
    let done=false;
    const finish=(failure,value)=>{if(done)return;done=true;cancel(timer);failure?reject(failure):resolve(value);};
    const timer=schedule(()=>finish(Error('recipient_lookup_timeout')),timeoutMs);timer?.unref?.();
    Promise.resolve().then(()=>socket.onWhatsApp(phone)).then(value=>finish(null,value),failure=>finish(failure));
  });
  for(let attempt=0;attempt<2;attempt++){
    await guard();
    try{const result=await once();await guard();return result;}
    catch(failure){
      await guard();
      if(attempt===1)throw failure;
      if(retryMs>0)await new Promise(resolve=>{const timer=schedule(resolve,retryMs);timer?.unref?.();});
    }
  }
}
