import {createHmac} from 'node:crypto';

// An allowlist, not a serializer: never forward Baileys objects/errors to logs.
export function makeTransportDiagnostics({secret,now=Date.now,emit=()=>{}}){
  const counts={},events=[];
  const tag=id=>typeof id==='string'&&id ? createHmac('sha256',secret)
    .update('orion-trace-v1:'+id).digest('hex').slice(0,20):null;
  function record(stage,id=null,details={}){
    counts[stage]=(counts[stage]||0)+1;
    const safe={};
    for(const name of ['kind','status','type','error','category','count','fromMe','hasPn','hasLid','cached','generation']){
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
    else if(msg==='error in sending message again')record('retry_send_failed',id);
    else if(msg==='recv retry request, but message not available')record('retry_content_missing',id);
    else if(msg==='will not send message again, as sent too many times')record('retry_limit',id);
    else if(msg==='offline preview received')record('offline_preview');
    else if(/^handled \d+ offline messages/.test(msg))record('offline_complete');
    else if(msg.startsWith('failed to commit '))record('auth_commit_failed');
  }
  const logger={level:'info',trace:log,debug:log,info:log,warn:log,error:log,fatal:log};
  logger.child=()=>logger;
  return {record,tag,logger,snapshot:()=>({counts:{...counts},events:events.map(x=>({...x}))})};
}
