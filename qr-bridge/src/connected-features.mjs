/** Optional linked-device features. No network call unless explicitly enabled. */
import {normalizeFromJid} from "./core.mjs";
import {shouldRejectCall} from "./connected-store.mjs";
export function transcriberConfig(env=process.env){
  const address=env.QR_ASR_URL||"",key=env.QR_ASR_TOKEN||"";
  try{
    const u=new URL(address);
    if(u.protocol!=="http:" || !u.hostname.endsWith(".railway.internal") ||
        u.port!=="8000"||u.pathname!=="/transcribe"||
        u.username||u.password||u.search||u.hash||key.length<32)return null;
    return {url:u.toString(),key};
  }catch{return null}
}
export function cleanPictureUrl(value){
  try{
    const u=new URL(value);
    if(u.protocol!=="https:"||u.username||u.password ||
        u.port||u.hash||u.hostname.length>150)return null;
    if(!/(^|\.)(whatsapp\.net|whatsapp\.com|fbcdn\.net)$/.test(u.hostname))return null;
    return u.toString();
  }catch{return null}
}
export async function transcribeAudio(message,{
  client,downloadMediaMessage,settings,config,fetchFn=fetch,logger
}){
  if(!settings.transcription||!config)return null;
  const media=message?.message?.audioMessage||
    message?.message?.ephemeralMessage?.message?.audioMessage;
  if(!media)return null;
  const size=Number(media.fileLength||0);
  if(!Number.isFinite(size)||size<1||size>2_000_000)return null;
  const buffer=await downloadMediaMessage(message,"buffer",{},{
    logger,reuploadRequest:client.updateMediaMessage
  });
  if(!Buffer.isBuffer(buffer))return null;
  if(buffer.length<1||buffer.length>2_000_000){
    buffer.fill(0);return null;
  }
  const controller=new AbortController();
  const timeout=setTimeout(()=>controller.abort(),35000);
  try{
    const r=await fetchFn(config.url,{
      method:"POST",headers:{"X-Orion-ASR-Key":config.key,
        "Content-Type":"application/octet-stream",
        "X-Orion-Audio-Language":settings.language},
      body:buffer,signal:controller.signal
    });
    if(!r.ok)return null;
    const result=await r.json();
    return typeof result?.text==="string"?
      result.text.trim().slice(0,2000)||null:null;
  }finally{clearTimeout(timeout);buffer.fill(0)}
}
// A call's 'from' can be a PN JID or an opaque LID JID. Never interpret
// digits from @lid as a phone number: LID identifiers are not telephone numbers.
const PN_JID=/^[1-9]\d{7,19}(?::\d+)?@s\.whatsapp\.net$/;
const LID_JID=/^[1-9]\d{7,19}(?::\d+)?@lid$/;
const CALL_RETRY_MS=60_000;
const CALL_REPLY_COOLDOWN_MS=15*60_000;
const MAX_CALL_CACHE=500;
function phoneFromPn(value){
  return typeof value==="string" && PN_JID.test(value)?normalizeFromJid(value):null;
}
function boundMap(map,now,ttl){
  for(const [key,time] of map){
    if(!Number.isFinite(time)||time+ttl<=now)map.delete(key);
  }
  while(map.size>=MAX_CALL_CACHE)map.delete(map.keys().next().value);
}
export async function resolveCallPhone(call,socket){
  for(const candidate of [call?.callerPn,call?.senderPn,call?.chatId,call?.from]){
    const phone=phoneFromPn(candidate);
    if(phone)return phone;
  }
  const lid=call?.from;
  if(typeof lid!=="string"||!LID_JID.test(lid))return null;
  const lookup=socket?.signalRepository?.lidMapping?.getPNForLID;
  if(typeof lookup!=="function")return null;
  try{
    const pn=await lookup.call(socket.signalRepository.lidMapping,lid);
    return phoneFromPn(pn);
  }catch{return null}
}
export async function handleCallOffers(calls,{
  socket,settings,replySent=new Map(),recentCalls=new Map(),
  diagnostics=null,sendReply=async()=>({accepted:false}),
  timezone="America/Campo_Grande",now=()=>Date.now()
}){
  const actions=[];
  if(!settings?.callBlocking||typeof socket?.rejectCall!=="function")return actions;
  const count=key=>{if(diagnostics)diagnostics[key]=(diagnostics[key]||0)+1};
  for(const call of Array.isArray(calls)?calls:[]){
    if(call?.status!=="offer"||call.isGroup||typeof call.id!=="string"||
       !call.id||call.id.length>200||typeof call.from!=="string"||
       !(PN_JID.test(call.from)||LID_JID.test(call.from)))continue;
    count("offersSeen");
    const time=now();
    boundMap(recentCalls,time,CALL_RETRY_MS);
    boundMap(replySent,time,CALL_REPLY_COOLDOWN_MS);
    const dedup=call.id+"|"+call.from;
    if(recentCalls.has(dedup)){count("duplicatesSkipped");continue}
    // Mark before awaiting network activity: Baileys can emit duplicate offers.
    recentCalls.set(dedup,time);
    const phone=await resolveCallPhone(call,socket);
    const allowlist=Array.isArray(settings.allowedCallers)?settings.allowedCallers:[];
    if(!phone&&allowlist.length){
      // Cannot decide whether an opaque LID belongs to an allowed caller.
      // Preserve the allowlist rather than potentially blocking that contact.
      count("unresolvedWithAllowlist");
      actions.push("unresolved");
      continue;
    }
    if(phone&&!shouldRejectCall(settings,phone,new Date(time),timezone)){
      count("allowedSkipped");
      continue;
    }
    if(!phone&&settings.callSchedule){
      // The schedule decision does not depend on identity if no allowlist.
      if(!shouldRejectCall({...settings,allowedCallers:[]}, "",new Date(time),timezone)){
        count("allowedSkipped");
        continue;
      }
    }
    try{
      await socket.rejectCall(call.id,call.from);
      count("rejected");
      actions.push("rejected");
    }catch{
      count("rejectFailed");
      actions.push("reject_failed");
      continue;
    }
    if(!settings.callReply||!settings.callReplyText?.trim())continue;
    if(!phone){
      count("replyNoPhone");
      actions.push("reply_unresolved");
      continue;
    }
    if(replySent.has(phone)){count("replyCooldown");continue}
    // Reserve the reply slot before an await; allow retry on a later call if
    // sending fails. Never claim success from a nonaccepted send result.
    replySent.set(phone,time);
    try{
      const sent=await sendReply(phone,settings.callReplyText.trim());
      if(sent?.accepted===false)throw Error("not accepted");
      count("replied");
      actions.push("replied");
    }catch{
      replySent.delete(phone);
      count("replyFailed");
      actions.push("reply_failed");
    }
  }
  return actions;
}
