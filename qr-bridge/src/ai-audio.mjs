/** Private Groq audio proxy. Buffered audio is erased after the request. */
export async function transcribeAssistantAudio(message,{
  client,downloadMediaMessage,phone,token,apiUrl,logger,fetchFn=fetch
}){
  const media=message?.message?.audioMessage||
    message?.message?.ephemeralMessage?.message?.audioMessage;
  if(!media)return null;
  const size=Number(media.fileLength||0);
  if(!Number.isFinite(size)||size<1||size>2_000_000)return null;
  if(!/^55[1-9]\d(?:9\d{8}|[2-5]\d{7})$/.test(phone))return null;
  if(typeof token!=="string"||token.length<32)return null;
  let target;
  try{
    target=new URL(apiUrl);
    if(target.protocol!=="http:"||target.hostname!=="orion-assistente.railway.internal"||
      target.pathname!=="/internal/qr/ai/reply"||target.search||target.hash)return null;
    target.pathname="/internal/qr/ai/transcribe";
  }catch{return null}
  const buffer=await downloadMediaMessage(message,"buffer",{},{
    logger,reuploadRequest:client.updateMediaMessage
  });
  if(!Buffer.isBuffer(buffer))return null;
  if(buffer.length<1||buffer.length>2_000_000){buffer.fill(0);return null}
  const controller=new AbortController();
  const timeout=setTimeout(()=>controller.abort(),27000);
  try{
    const response=await fetchFn(target.toString(),{
      method:"POST",
      headers:{"Content-Type":"audio/ogg","X-Orion-Bridge-Key":token,
               "X-Orion-Phone":phone},
      body:buffer,signal:controller.signal
    });
    if(!response.ok)return null;
    const result=await response.json();
    return typeof result?.text==="string" ? result.text.trim().slice(0,1500)||null:null;
  }finally{clearTimeout(timeout);buffer.fill(0)}
}
