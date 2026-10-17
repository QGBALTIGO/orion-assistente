/** Ordered private-message intake. prepare may download/transcribe, never mutate
 * business state. Only consume receives a complete ordered batch. The caller
 * supplies an authenticated account/tenant/conversation key and durable dedupe.
 * This module does not connect, persist audio, send, or retry an operation.
 */
export function makeConversationIntake({consume,onRejected=async()=>{},settleMs=350,maxGroupMs=1200,
  maxMessages=6,maxTextChars=1500,maxPending=20,maxConversations=100,
  maxSeen=2000,seenTtlMs=86400000,prepareTimeoutMs=40000,
  now=Date.now,sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms)),
  isDuplicate=()=>false}={}){
  if(typeof consume!=='function'||typeof onRejected!=='function'||typeof isDuplicate!=='function'||
      !Number.isSafeInteger(settleMs)||settleMs<0||settleMs>2000||
      !Number.isSafeInteger(maxGroupMs)||maxGroupMs<settleMs||maxGroupMs>5000||
      !Number.isSafeInteger(maxMessages)||maxMessages<1||maxMessages>20||
      !Number.isSafeInteger(maxTextChars)||maxTextChars<1||maxTextChars>6000||
      !Number.isSafeInteger(maxPending)||maxPending<maxMessages||maxPending>100||
      !Number.isSafeInteger(maxConversations)||maxConversations<1||maxConversations>1000||
      !Number.isSafeInteger(maxSeen)||maxSeen<maxPending||maxSeen>100000||
      !Number.isSafeInteger(seenTtlMs)||seenTtlMs<1||
      !Number.isSafeInteger(prepareTimeoutMs)||prepareTimeoutMs<1||prepareTimeoutMs>120000)
    throw Error('Invalid conversation intake configuration');
  const conversations=new Map(),seen=new Map(),active=new Set();
  function prune(){
    const time=now();
    for(const [id,expires] of seen)if(expires<=time)seen.delete(id);
    while(seen.size>maxSeen)seen.delete(seen.keys().next().value);
  }
  function current(entry){try{return entry.isCurrent()===true;}catch{return false;}}
  async function prepare(entry){
    if(!current(entry))return {state:'stale'};
    if(entry.preparedResult)return entry.preparedResult;
    if(entry.notice){
      const notice=await entry.notice;
      if(notice?.failed)return {state:'revision_failed'};
      if(notice?.skip)return {state:'skipped'};
      if(!current(entry))return {state:'stale'};
    }
    const controller=new AbortController();
    let timer;
    try{
      const result=await Promise.race([
        Promise.resolve().then(()=>entry.prepare({signal:controller.signal})),
        new Promise(resolve=>{timer=setTimeout(()=>{
          controller.abort();resolve({timeout:true});
        },prepareTimeoutMs);})
      ]);
      if(!current(entry))return {state:'stale'};
      if(result?.skip===true)return {state:'skipped'};
      if(result?.timeout)return {state:'prepare_failed'};
      if(typeof result?.text!=='string'||!result.text.trim())return {state:'prepare_failed'};
      if(result.text.length>maxTextChars)return {state:'needs_clarification'};
      return entry.preparedResult={state:'ready',text:result.text.trim()};
    }catch{return {state:current(entry)?'prepare_failed':'stale'};}
    finally{clearTimeout(timer);}
  }
  function finish(entries,state){for(const entry of entries){active.delete(entry.dedupe);delete entry.preparedResult;entry.resolve({state});}}
  async function settle(state,started){
    // Debounce short bursts, but a continuous stream cannot extend this window.
    for(;;){
      const remaining=Math.min(state.lastAt+settleMs,started+maxGroupMs)-now();
      if(remaining<=0)return;
      await sleep(Math.min(remaining,1000));
    }
  }
  async function drain(key,state){
    try{
      while(state.queue.length){
        const batch=[],prepared=[];
        let failed=null;
        const started=state.queue[0].at;
        await settle(state,started);
        // Messages queued while audio is being transcribed belong to this batch
        // too. They cannot overtake it or execute before the later correction.
        while(state.queue.length&&batch.length<maxMessages){
          const entry=state.queue.shift();batch.push(entry);
          const result=await prepare(entry);
          if(result.state==='ready')prepared.push({id:entry.messageId,text:result.text});
          else if(result.state!=='skipped')failed=failed||result.state;
          if(!state.queue.length)await settle(state,started);
        }
        if(state.queue.length){
          // A bounded batch must never execute a known incomplete request.
          batch.push(...state.queue.splice(0));failed=failed||'needs_clarification';
        }
        const text=prepared.map(item=>item.text).join('\n');
        if(state.incomplete)failed=failed||'needs_clarification';
        if(text.length>maxTextChars)failed=failed||'needs_clarification';
        if(batch.some(entry=>!current(entry)))failed='stale';
        if(failed){
          try{await onRejected(Object.freeze({conversationKey:key,reason:failed,
            messageIds:Object.freeze(batch.map(entry=>entry.messageId)),isCurrent:()=>batch.every(current)}));}catch{}
          finish(batch,failed);
        }
        else if(!prepared.length)finish(batch,'skipped');
        else{
          try{
            const revision=Math.max(0,...batch.map(entry=>entry.revision||0));
            const outcome=await consume(Object.freeze({conversationKey:key,text,
              messages:Object.freeze(prepared.map(item=>Object.freeze(item))),
              ...(revision?{intakeRevision:revision}:{}),
              isCurrent:()=>!state.incomplete&&batch.every(current)&&(!revision||revision===state.revision)}));
            if(outcome?.state==='superseded'&&outcome?.not_committed===true&&state.queue.length&&batch.every(current)){
              // Only the API's explicit zero-write acknowledgment authorizes
              // carrying prepared text. No ASR or uncertain operation is replayed.
              for(const entry of batch)if(!entry.preparedResult)entry.preparedResult={state:'skipped'};
              state.queue.unshift(...batch);
              continue;
            }
            finish(batch,'processed');
          }catch{
            // A consumer may have committed before throwing: never replay it.
            finish(batch,'consume_failed');
          }
        }
        state.pending-=batch.length;
      }
    }finally{
      if(conversations.get(key)===state)conversations.delete(key);
    }
  }
  return {
    submit({conversationKey,messageId,prepare:prepareMessage,isCurrent=()=>true,notice}={}){
      if(typeof conversationKey!=='string'||!conversationKey||conversationKey.length>240||
          typeof messageId!=='string'||!messageId||messageId.length>160||
          typeof prepareMessage!=='function'||typeof isCurrent!=='function'||(notice!==undefined&&typeof notice!=='function'))
        return Promise.resolve({state:'invalid'});
      prune();
      const dedupe=JSON.stringify([conversationKey,messageId]);
      try{
        const duplicate=isDuplicate({conversationKey,messageId});
        if(typeof duplicate!=='boolean')return Promise.resolve({state:'invalid'});
        if(active.has(dedupe)||seen.has(dedupe)||duplicate)return Promise.resolve({state:'duplicate'});
      }catch{return Promise.resolve({state:'invalid'});}
      let state=conversations.get(conversationKey);
      if(state?.pending>=maxPending){
        state.incomplete=true;
        return Promise.resolve({state:'overloaded'});
      }
      if(!state&&conversations.size>=maxConversations)
        return Promise.resolve({state:'overloaded'});
      // Claim synchronously before starting any asynchronous media work.
      active.add(dedupe);seen.set(dedupe,now()+seenTtlMs);prune();
      if(!state){state={queue:[],lastAt:now(),pending:0,revision:0};conversations.set(conversationKey,state);}
      state.lastAt=now();state.pending++;
      const entry={messageId,dedupe,prepare:prepareMessage,isCurrent,at:state.lastAt};
      if(notice)entry.notice=Promise.resolve().then(notice).then(result=>{
        if(result?.skip===true)return {skip:true};
        if(!Number.isSafeInteger(result?.revision)||result.revision<1)return {failed:true};
        entry.revision=result.revision;state.revision=Math.max(state.revision,result.revision);
        return result;
      },()=>({failed:true}));
      const promise=new Promise(resolve=>{entry.resolve=resolve;state.queue.push(entry);});
      if(!state.running){state.running=true;void drain(conversationKey,state);}
      return promise;
    },
    pending(){return [...conversations.values()].reduce((sum,state)=>sum+state.pending,0);}
  };
}
