import {createHmac} from 'node:crypto';

function privateAddress(jid){
  const match=typeof jid==='string'&&jid.match(/^(\d+)(?::(\d+))?@(s\.whatsapp\.net|lid)$/);
  return match?{user:match[1],kind:match[3]==='lid'?'lid':'pn',
    deviceClass:!match[2]||Number(match[2])===0?'primary':'companion'}:null;
}

// Only classifications leave this helper; opaque LIDs are never turned into PNs.
export function receiptDiagnosticDetails(node,ownIdentity={}){
  const attrs=node?.attrs||{},from=privateAddress(attrs.from),
    participant=privateAddress(attrs.participant),sender=participant||from;
  const own=[privateAddress(ownIdentity?.id),privateAddress(ownIdentity?.lid)].filter(Boolean);
  return {kind:from?.kind||'other',participantKind:participant?.kind||'other',
    hasParticipant:typeof attrs.participant==='string'&&attrs.participant.length>0,
    isSelf:!!sender&&own.some(x=>x.user===sender.user&&x.kind===sender.kind),
    deviceClass:sender?.deviceClass||'unknown'};
}

export function sessionDomainCounts(jids){
  const result={count:0,pnCount:0,lidCount:0,otherCount:0,primaryCount:0,companionCount:0};
  for(const jid of Array.isArray(jids)?jids:[]){
    const address=privateAddress(jid);result.count++;
    result[address?.kind==='pn'?'pnCount':address?.kind==='lid'?'lidCount':'otherCount']++;
    if(address)result[address.deviceClass==='primary'?'primaryCount':'companionCount']++;
  }
  return result;
}

export function encryptionTargetDetails(jid,ownIdentity={}){
  const address=privateAddress(jid);
  const own=[privateAddress(ownIdentity?.id),privateAddress(ownIdentity?.lid)].filter(Boolean);
  const ownPn=privateAddress(ownIdentity?.id),ownLid=privateAddress(ownIdentity?.lid);
  return {kind:address?.kind||'other',deviceClass:address?.deviceClass||'unknown',
    isSelf:!!address&&own.some(x=>x.kind===address.kind&&x.user===address.user),
    // Compare only explicit own identities. A known LID user placed in the PN
    // domain is suspicious unless it is also the explicit own PN user. This
    // reports a mismatch; it never resolves a LID into a phone number.
    ownDomainMismatch:address?.kind==='pn'&&ownLid?.kind==='lid'&&address.user===ownLid.user&&
      !(ownPn?.kind==='pn'&&address.user===ownPn.user)};
}

// Use Baileys' public repository-factory hook. Observe only the actual target
// address, never data/ciphertext, while preserving the original call/Promise.
// An observer failure must not turn an accepted send into an ambiguous error.
export function observeSignalRepository(factory,observe){
  return function(...factoryArgs){
    const repository=Reflect.apply(factory,this,factoryArgs);
    const original=repository.encryptMessage;
    repository.encryptMessage=function(...args){
      try{observe(args[0]?.jid);}catch{}
      return Reflect.apply(original,this,args);
    };
    return repository;
  };
}

// Observe the actual device-list result before Baileys drops the domain.
// Never retain a user node, device list, phone, LID, or signed key-index bytes.
export function usyncDeviceDomainCounts(node,ownIdentity={}){
  const child=(parent,tag)=>Array.isArray(parent?.content)?parent.content.find(x=>x?.tag===tag):null;
  const list=child(child(node,'usync'),'list');
  const users=(Array.isArray(list?.content)?list.content:[])
    .filter(x=>x?.tag==='user'&&child(x,'devices')).slice(0,1000);
  if(!users.length)return null;
  const {count,pnCount,lidCount,otherCount}=sessionDomainCounts(users.map(x=>x.attrs?.jid));
  const result={count,pnCount,lidCount,otherCount,
    pnPrimaryCount:0,lidPrimaryCount:0,otherPrimaryCount:0,
    pnCompanionCount:0,lidCompanionCount:0,otherCompanionCount:0,
    pnEligibleCompanionCount:0,lidEligibleCompanionCount:0,otherEligibleCompanionCount:0};
  const own=privateAddress(ownIdentity?.id),ownDevice=ownIdentity?.id?.match(/:(\d+)@/)?.[1];
  for(const user of users){
    const address=privateAddress(user.attrs?.jid),prefix=address?.kind||'other';
    const deviceList=child(child(user,'devices'),'device-list');
    const devices=(Array.isArray(deviceList?.content)?deviceList.content:[]).slice(0,1000);
    for(const device of devices){
      if(device?.tag!=='device')continue;
      const id=Number(device.attrs?.id);
      if(!Number.isSafeInteger(id)||id<0)continue;
      if(id===0){result[prefix+'PrimaryCount']++;continue;}
      result[prefix+'CompanionCount']++;
      // Exactly the installed 6.7.24 excludeZeroDevices=true extraction rule:
      // it compares me.id user/device, and requires a nonzero key-index.
      const isCurrent=!!own&&!!address&&own.user===address.user&&Number(ownDevice)===id;
      if(!isCurrent&&Number(device.attrs?.['key-index']))result[prefix+'EligibleCompanionCount']++;
    }
  }
  return result;
}

// An allowlist, not a serializer: never forward Baileys objects/errors to logs.
export function makeTransportDiagnostics({secret,now=Date.now,emit=()=>{}}){
  const counts={},events=[];
  const tag=id=>typeof id==='string'&&id ? createHmac('sha256',secret)
    .update('orion-trace-v1:'+id).digest('hex').slice(0,20):null;
  function record(stage,id=null,details={}){
    counts[stage]=(counts[stage]||0)+1;
    const safe={};
    for(const name of ['kind','status','type','error','category','count','fromMe','hasPn','hasLid','cached','tracked','generation',
      'participantKind','hasParticipant','isSelf','ownDomainMismatch','deviceClass','sendToAll','pnCount','lidCount','otherCount','primaryCount','companionCount',
      'pnPrimaryCount','lidPrimaryCount','otherPrimaryCount','pnCompanionCount','lidCompanionCount','otherCompanionCount',
      'pnEligibleCompanionCount','lidEligibleCompanionCount','otherEligibleCompanionCount']){
      const value=details[name];
      if(typeof value==='boolean'||(typeof value==='number'&&Number.isFinite(value)))safe[name]=value;
      else if(typeof value==='string'&&/^[a-zA-Z0-9_-]{1,40}$/.test(value))safe[name]=value;
    }
    const event={at:now(),stage,trace:tag(id),...safe};
    events.push(event);if(events.length>200)events.shift();
    emit(event);
  }
  function log(...args){
    const obj=args.find(x=>x&&typeof x==='object')||{};
    const msg=args.find(x=>typeof x==='string')||'';
    const id=obj.key?.id||obj.msgAttrs?.id||obj.attrs?.id||obj.id||obj.msgId;
    if(msg==='failed to decrypt message'){
      const error=String(obj.err?.message||'');
      record('decrypt_failed',id,{error:error.includes('Bad MAC')?'bad_mac':error.includes('Key used')?'counter':error.includes('No session')?'missing_session':'other'});
    }else if(msg==='recv retry request')record('retry_received',id);
    else if(msg==='sent retry receipt')record('retry_requested',id,{count:obj.retryCount});
    else if(msg==='error in sending message again'){
      const ids=Array.isArray(obj.ids)?[...new Set(obj.ids.filter(x=>typeof x==='string'&&x))].slice(0,30):[];
      for(const failedId of ids.length?ids:[id])record('retry_send_failed',failedId);
    }
    else if(msg==='recv retry request, but message not available')record('retry_content_missing',id);
    else if(msg==='will not send message again, as sent too many times')record('retry_limit',id);
    else if(msg==='received error in ack')record('server_rejected',id,{
      error:/^\d{3}$/.test(String(obj.attrs?.error||''))?String(obj.attrs.error):'unknown'});
    else if(msg==='fetching sessions')record('session_fetch_requested',null,sessionDomainCounts(obj.jidsRequiringFetch));
    else if(msg==='forced new session for retry recp'){
      const {kind,deviceClass}=receiptDiagnosticDetails({attrs:{from:obj.participant}});
      record('retry_sessions_refreshed',null,{kind,deviceClass,sendToAll:obj.sendToAll===true});
    }
    else if(/^sending message to \d+ devices$/.test(msg))record('send_fanout',id,{count:Number(msg.match(/\d+/)[0])});
    else if(msg==='offline preview received')record('offline_preview');
    else if(/^handled \d+ offline messages/.test(msg))record('offline_complete');
    else if(msg.startsWith('failed to commit '))record('auth_commit_failed');
  }
  const logger={level:'info',trace:log,debug:log,info:log,warn:log,error:log,fatal:log};
  logger.child=()=>logger;
  return {record,tag,logger,snapshot:()=>({counts:{...counts},events:events.map(x=>({...x}))})};
}
