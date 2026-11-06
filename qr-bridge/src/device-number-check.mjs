/** Read-only, bounded WhatsApp membership checks using the current device socket.
 * No message, contact mutation, new session, or automatic retry is permitted.
 * This is a conservative first implementation, not a claim of BotBot parity. */
import {channelId} from './channel-storage.mjs';

export const NUMBER_CHECK_LIMITS=Object.freeze({
  per_request:5, per_hour:30, window_ms:3600000, gap_ms:800, timeout_ms:15000
});
const unavailable=()=>Error('number_check_unavailable');
const record=value=>!!value&&typeof value==='object'&&!Array.isArray(value);
export const numberCheckPhone=value=>typeof value==='string'&&/^\+[1-9]\d{7,14}$/.test(value);
export function validNumberCheckRequest(value){
  return record(value)&&Object.keys(value).length===4&&
    ['device_id','generation','settings_revision','phones'].every(key=>Object.hasOwn(value,key))&&
    channelId(value.device_id)&&channelId(value.generation)&&
    Number.isSafeInteger(value.settings_revision)&&value.settings_revision>=1&&
    Array.isArray(value.phones)&&value.phones.length>0&&value.phones.length<=NUMBER_CHECK_LIMITS.per_request&&
    value.phones.every(numberCheckPhone)&&new Set(value.phones).size===value.phones.length;
}
function verdict(phone,raw){
  // An LID, an alias or a missing JID is NOT proof of registration.
  if(!Array.isArray(raw)||raw.length>8)return 'unknown';
  const digits=phone.slice(1);
  const exact=raw.filter(row=>record(row)&&typeof row.jid==='string'&&
    new RegExp('^'+digits+'(?::[0-9]{1,5})?@s\\.whatsapp\\.net$').test(row.jid)&&
    typeof row.exists==='boolean');
  if(exact.length!==1)return 'unknown';
  return exact[0].exists===true?'registered':'not_registered';
}

export function createDeviceNumberCheck({client,isCurrent,isConnected,deviceSnapshot,
  now=Date.now,schedule=setTimeout,cancel=clearTimeout,
  timeoutMs=NUMBER_CHECK_LIMITS.timeout_ms,gapMs=NUMBER_CHECK_LIMITS.gap_ms}={}){
  if(typeof now!=='function'||typeof schedule!=='function'||typeof cancel!=='function'||
      !Number.isSafeInteger(timeoutMs)||timeoutMs<1||timeoutMs>NUMBER_CHECK_LIMITS.timeout_ms||
      !Number.isSafeInteger(gapMs)||gapMs<0||gapMs>NUMBER_CHECK_LIMITS.gap_ms)throw unavailable();
  let busy=false,windowAt=now(),checked=0;
  return async function check(input,{authorize}={}){
    if(!validNumberCheckRequest(input)||typeof authorize!=='function'||busy||
        typeof client?.onWhatsApp!=='function')throw unavailable();
    if(now()-windowAt>=NUMBER_CHECK_LIMITS.window_ms||now()<windowAt){
      windowAt=now();checked=0;
    }
    if(checked+input.phones.length>NUMBER_CHECK_LIMITS.per_hour)throw unavailable();
    // Reserve quota before the provider call; failures and timeouts consume it.
    checked+=input.phones.length;
    busy=true;
    const scope={device_id:input.device_id,generation:input.generation,
      settings_revision:input.settings_revision};
    let expired=false,timer;
    const current=()=>{
      const device=deviceSnapshot?.();
      return !expired&&isCurrent?.()===true&&isConnected?.()===true&&
        device?.id===scope.device_id&&device.generation===scope.generation&&
        device.settings_revision===scope.settings_revision;
    };
    const guard=async()=>{
      if(!current())throw unavailable();
      await authorize();
      if(!current())throw unavailable();
    };
    // A provider read is not cancellable. The busy slot stays held even if
    // the HTTP caller timed out. Never start an overlapping check on this socket.
    const work=(async()=>{
      try{
        const items=[];
        for(let i=0;i<input.phones.length;i++){
          await guard();
          if(i>0&&gapMs)await new Promise(resolve=>schedule(resolve,gapMs));
          await guard();
          let result;
          try{result=await client.onWhatsApp(input.phones[i].slice(1));}
          catch{result=null;}
          await guard();
          items.push({phone:input.phones[i],status:verdict(input.phones[i],result)});
        }
        return {...scope,items};
      }catch{throw unavailable();}
      finally{busy=false;}
    })();
    const deadline=new Promise((_,reject)=>{
      timer=schedule(()=>{expired=true;reject(unavailable());},timeoutMs);
    });
    try{return await Promise.race([work,deadline]);}
    finally{cancel(timer);}
  };
}
