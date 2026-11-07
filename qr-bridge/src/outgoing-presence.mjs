import {makeAssistantTyping} from './assistant-typing.mjs';

const safe=fn=>{try{return fn()===true;}catch{return false;}};
const revoked=()=>Object.assign(Error('outgoing_operation_superseded'),{code:'OUTGOING_OPERATION_SUPERSEDED'});
const quiet=Object.freeze({beforeReply:async()=>{},stop(){}});

/** A bounded visual preparation target, not an estimate of human reading or
 * recording time. Never delays a retry, extends an AI budget, or changes IDs. */
export function outgoingPresenceTarget(action){
  if(action?.type==='audio'){
    const duration=action.asset?.duration_ms;
    return Math.min(5000,Math.max(800,Number.isSafeInteger(duration)&&duration>0?duration:800));
  }
  const values=[];
  const add=value=>{if(typeof value==='string')values.push(value);};
  for(const key of ['text','title','description','body','caption','footer','button_text'])add(action?.[key]);
  for(const section of (Array.isArray(action?.sections)?action.sections:[]).slice(0,10)){
    add(section.title);for(const row of (Array.isArray(section.rows)?section.rows:[]).slice(0,10)){add(row.title);add(row.description);}
  }
  for(const card of (Array.isArray(action?.cards)?action.cards:[]).slice(0,3))for(const key of ['title','body','button_text'])add(card[key]);
  return Math.min(3000,Math.max(800,[...values.join(' ')].length*18));
}

/** One coordinator per native socket. Only a proved private recipient reaches
 * begin/run. AI preparation and its eventual transport share the same clock;
 * distinct contacts never share a wait or presence lease. */
export function makeOutgoingPresence({socket,isConnected,typingFactory=makeAssistantTyping,
  now=()=>performance.now(),schedule=setTimeout,cancel=clearTimeout,maxDurationMs=20000}={}){
  if(typeof isConnected!=='function'||typeof typingFactory!=='function')throw Error('invalid_outgoing_presence');
  const preparing=new Map(),sending=new Map(),tails=new Map(),leases=new Set();let closed=false;
  const current=fn=>!closed&&safe(isConnected)&&safe(fn);
  function begin({phone,chatJid,isCurrent=()=>false,mode='composing'}={}){
    if(!current(isCurrent)||!/^[1-9][0-9]{7,14}$/.test(phone)||!['composing','recording'].includes(mode))return quiet;
    const activeSend=sending.get(phone),previous=preparing.get(phone);
    const preserveSend=activeSend&&!activeSend.stopped&&current(activeSend.guard);
    if(previous&&(!preserveSend||previous!==activeSend))previous.stop();
    const entry={phone,mode,started:now(),guard:isCurrent,stopped:false};
    entry.native=quiet;entry.activated=false;
    entry.activate=()=>{
      if(entry.activated||entry.stopped||!current(entry.guard))return;
      entry.activated=true;
      const factory=typingFactory({socket,pilotPhone:()=>phone,isConnected:()=>!closed&&safe(isConnected),
        isAllowed:()=>current(entry.guard),now,schedule,cancel,maxPauseMs:0,maxDurationMs,
        broadRecipients:true,presence:mode,pauseOnSuperseded:false});
      entry.native=factory.begin({phone,chatJid,isCurrent:()=>current(entry.guard)});
    };
    entry.beforeReply=value=>entry.native.beforeReply(value);
    entry.stop=()=>{
      if(entry.stopped)return;entry.stopped=true;entry.native.stop();leases.delete(entry);
      if(preparing.get(phone)===entry)preparing.delete(phone);
      const next=preparing.get(phone);
      if(!sending.has(phone)&&next&&!next.activated)next.activate();
    };
    preparing.set(phone,entry);leases.add(entry);if(!preserveSend)entry.activate();return entry;
  }
  async function wait(ms,isCurrent){
    if(ms<=0)return;
    await new Promise((resolve,reject)=>{
      let timer,monitor,done=false;const end=error=>{if(done)return;done=true;cancel(timer);cancel(monitor);error?reject(error):resolve();};
      const check=()=>{if(!current(isCurrent))return end(revoked());monitor=schedule(check,100);monitor?.unref?.();};
      timer=schedule(()=>end(),ms);check();
    });
  }
  async function run({phone,chatJid,isCurrent=()=>false,assertAuthorized=async()=>{},action,
    pacing=true,signal}={},work){
    if(typeof work!=='function'||typeof assertAuthorized!=='function')throw Error('invalid_outgoing_presence_work');
    let authorized=true;
    const valid=()=>authorized&&current(isCurrent)&&!signal?.aborted;
    const authorize=async()=>{try{await assertAuthorized();}catch(error){authorized=false;throw error;}};
    const previous=tails.get(phone);let release;
    const finished=new Promise(resolve=>{release=resolve;});
    // A cancelled waiter relinquishes its own work, but never opens a hole in
    // the still-running predecessor's conversation barrier.
    const tail=previous?previous.then(()=>finished):finished;tails.set(phone,tail);
    const queuedAt=now();let lease;
    try{
      // Conversation-local ordering only. A cancelled queued operation leaves
      // its place immediately without sending or delaying other contacts.
      let ready=!previous;previous?.finally(()=>{ready=true;}).catch(()=>{});
      while(!ready){if(!valid())throw revoked();await wait(100,valid);}
      if(!valid())throw revoked();await authorize();if(!valid())throw revoked();
      const mode=action?.type==='audio'?'recording':'composing';
      lease=preparing.get(phone);
      if(!lease||lease.stopped||lease.mode!==mode||!current(lease.guard))lease=begin({phone,chatJid,isCurrent:valid,mode});
      else{const preparationGuard=lease.guard;lease.guard=()=>safe(preparationGuard)&&valid();}
      if(lease!==quiet)lease.started=Math.min(lease.started,queuedAt);
      if(lease!==quiet){sending.set(phone,lease);lease.activate();}
      let paced=false;
      const beforeSend=async()=>{
        // Preparation/media upload also count. The caller invokes this at the
        // last preparation boundary before generating/caching the protocol.
        if(!paced){paced=true;
          const target=outgoingPresenceTarget(action);
          const residual=pacing&&typeof socket?.sendPresenceUpdate==='function'?
            Math.min(target,Math.max(0,target-(now()-(lease.started??now())))):0;
          await wait(residual,valid);
        }
        await authorize();if(!valid())throw revoked();
      };
      return await work({beforeSend});
    }finally{if(sending.get(phone)===lease)sending.delete(phone);lease?.stop();release();void tail.then(()=>{if(tails.get(phone)===tail)tails.delete(phone);});}
  }
  return Object.freeze({begin,run,close(){if(closed)return;closed=true;for(const lease of leases)lease.stop();leases.clear();preparing.clear();},
    diagnostics:()=>({active:leases.size,conversations:tails.size})});
}
