/** Fence the SDK's own retry/receive work as well as application sends.
 * Signal and its persisted key formats are unchanged. */
export function fenceSignalRepository(factory,isCurrent){
  if(typeof factory!=='function'||typeof isCurrent!=='function')throw Error('invalid_signal_fence');
  return function(...args){
    const repository=Reflect.apply(factory,this,args);
    for(const name of ['encryptMessage','decryptMessage','encryptGroupMessage','decryptGroupMessage','injectE2ESession','processSenderKeyDistributionMessage']){
      const original=repository[name];
      if(typeof original!=='function')continue;
      repository[name]=async function(...values){
        if(!isCurrent())throw Error('signal_owner_superseded');
        const result=await Reflect.apply(original,this,values);
        if(!isCurrent()){
          if(Buffer.isBuffer(result))result.fill(0);
          if(Buffer.isBuffer(result?.ciphertext))result.ciphertext.fill(0);
          throw Error('signal_owner_superseded');
        }
        return result;
      };
    }
    return repository;
  };
}
