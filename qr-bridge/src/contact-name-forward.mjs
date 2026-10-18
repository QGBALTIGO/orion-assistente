/** Bounded metadata-only replay to Orion. Never sends a WhatsApp message. */
import {cleanContactProfile,contactPhone,CONTACT_NAME_LIMIT} from './contact-names.mjs';

export async function forwardContactNames({apiUrl,token,accountPhone,items,fetchImpl=fetch,signal}){
  if(typeof token!=='string'||token.length<32||!contactPhone(accountPhone)||!Array.isArray(items)||
      items.length<1||items.length>100)throw Error('invalid_contact_batch');
  const target=new URL(apiUrl);
  if(target.protocol!=='http:'||!target.hostname.endsWith('.railway.internal')||
      target.pathname!=='/internal/qr/ai/reply'||target.search||target.hash||target.username||target.password)
    throw Error('unsafe_contact_endpoint');
  target.pathname='/internal/qr/contact-names';
  const phones=new Set();
  const clean=items.map(row=>{
    const profile=cleanContactProfile(row);
    if(!profile||!contactPhone(row.phone)||row.phone===accountPhone||phones.has(row.phone))
      throw Error('invalid_contact_batch');
    phones.add(row.phone);return {phone:row.phone,...profile};
  });
  const controller=new AbortController(),cancel=()=>controller.abort();
  if(signal?.aborted)throw Error('contact_batch_cancelled');
  signal?.addEventListener('abort',cancel,{once:true});
  const timeout=setTimeout(cancel,5000);
  try{
    const response=await fetchImpl(target.toString(),{method:'POST',headers:{
      'Content-Type':'application/json','X-Orion-Bridge-Key':token},
      body:JSON.stringify({account_phone:accountPhone,items:clean}),signal:controller.signal});
    if(!response.ok)throw Error('contact_batch_unavailable');
    const result=await response.json();
    if(result?.ok!==true||!Number.isSafeInteger(result.updated)||result.updated<0||result.updated>clean.length)
      throw Error('contact_batch_not_acknowledged');
    return true;
  }finally{clearTimeout(timeout);signal?.removeEventListener('abort',cancel);}
}

/** One instance per socket generation. A cancelled in-flight response cannot
 * acknowledge/requeue data for a replacement account. Failed metadata batches
 * never delay ingestion, typing, AI actions or outbound delivery.
 */
export function makeContactNameSync({store,apiUrl,token,isCurrent,fetchImpl=fetch,
  setTimer=setTimeout,clearTimer=clearTimeout,debounceMs=750,
  retryDelays=[1000,5000,15000,30000,60000,120000,300000,300000]}){
  let active=false,account=null,epoch=0,timer=null,inflight=null,failures=0;
  const pending=new Map(),acknowledged=new Map();
  const current=()=>active&&isCurrent()&&account===store.accountPhone();
  const fingerprint=row=>row.source+'\0'+row.name;
  function stop(){
    active=false;account=null;epoch++;failures=0;
    if(timer!==null)clearTimer(timer);timer=null;
    inflight?.abort();inflight=null;pending.clear();acknowledged.clear();
  }
  function stage(){
    let changes=false;
    const known=new Map(store.entries().map(row=>[row.phone,row]));
    for(const row of known.values()){
      const item={phone:row.phone,name:row.display_name,source:row.name_source};
      const value=fingerprint(item);
      if(acknowledged.get(item.phone)===value){pending.delete(item.phone);continue;}
      if(pending.has(item.phone)&&fingerprint(pending.get(item.phone))===value)continue;
      if(!pending.has(item.phone)&&pending.size>=CONTACT_NAME_LIMIT)continue;
      pending.set(item.phone,item);changes=true;
    }
    // An expired/quarantined name must not survive in the retry queue.
    for(const [phone,item] of pending){
      const row=known.get(phone);
      if(!row||row.display_name!==item.name||row.name_source!==item.source)pending.delete(phone);
    }
    return changes;
  }
  function schedule(delay){
    if(timer!==null||inflight||!pending.size||!current())return;
    timer=setTimer(()=>{timer=null;void flush();},delay);
    timer?.unref?.();
  }
  async function flush(){
    if(!current()){stop();return;}
    stage();
    const batch=[...pending.values()].slice(0,100);
    if(!batch.length)return;
    const mine=epoch,own=account,controller=new AbortController();
    inflight=controller;
    try{
      await forwardContactNames({apiUrl,token,accountPhone:own,items:batch,fetchImpl,signal:controller.signal});
      if(mine!==epoch||!current())return;
      for(const item of batch){
        const mark=fingerprint(item);
        acknowledged.set(item.phone,mark);
        if(pending.has(item.phone)&&fingerprint(pending.get(item.phone))===mark)pending.delete(item.phone);
      }
      // The registry and successful replay memory have the same upper bound.
      while(acknowledged.size>CONTACT_NAME_LIMIT)acknowledged.delete(acknowledged.keys().next().value);
      failures=0;
    }catch{
      if(mine===epoch&&current())failures++;
    }finally{
      if(mine===epoch){
        inflight=null;
        if(!current()){stop();return;}
        if(!failures)schedule(debounceMs);
        else if(failures<=retryDelays.length)schedule(retryDelays[failures-1]);
        // After bounded retries the bounded queue stays paused. A real new
        // metadata change or a later connection replays the registry again.
      }
    }
  }
  return {
    start(){
      stop();
      if(!apiUrl||!isCurrent()||!store.accountPhone())return;
      active=true;account=store.accountPhone();stage();schedule(debounceMs);
    },
    changed(){
      if(!active||account!==store.accountPhone()){
        this.start();return;
      }
      if(!current()){stop();return;}
      const changed=stage();
      if(changed)failures=0;
      if(!changed&&failures>retryDelays.length)return;
      schedule(debounceMs);
    },
    stop,
    // Counts only; no contact names, PNs or tokens belong in operational logs.
    health:()=>({active:current(),pending:pending.size,inflight:!!inflight,retryAttempt:failures})
  };
}
