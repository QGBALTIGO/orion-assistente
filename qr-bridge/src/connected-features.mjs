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
  if(!Buffer.isBuffer(buffer)||buffer.length<1||buffer.length>2_000_000)return null;
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
export async function handleCallOffers(calls,{
  socket,settings,replySent,sendReply=async()=>{},timezone="America/Campo_Grande"
}){
  const actions=[];
  if(!settings.callBlocking||typeof socket?.rejectCall!=="function")return actions;
  for(const call of calls||[]){
    if(call?.status!=="offer"||call.isGroup||typeof call.id!=="string"||!call.id||
       !/^[0-9]{8,20}(?::\d+)?@s\.whatsapp\.net$/.test(call.from||""))continue;
    const phone=normalizeFromJid(call.from);
    if(!phone || !shouldRejectCall(settings,phone,new Date(),timezone))continue;
    try{
      await socket.rejectCall(call.id,call.from);
      actions.push("rejected");
      if(settings.callReply&&settings.callReplyText.trim()&&!replySent.has(phone)){
        replySent.add(phone);
        await sendReply(call.from,settings.callReplyText);
      }
    }catch{
      // Never claim a call was rejected if the WhatsApp server did not respond.
      actions.push("failed");
    }
  }
  return actions;
}
