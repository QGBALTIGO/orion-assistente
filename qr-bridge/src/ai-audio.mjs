/** Private Groq audio proxy. Buffered audio is erased after the request. */
export async function transcribeAssistantAudio(message,{
  client,downloadMediaMessage,phone,token,apiUrl,logger,fetchFn=fetch,signal
}){
  const media=message?.message?.audioMessage||
    message?.message?.ephemeralMessage?.message?.audioMessage;
  if(!media)return null;
  const size=Number(media.fileLength||0);
  const seconds=Number(media.seconds);
  if(!Number.isFinite(size)||size<1||size>2_000_000||
      !Number.isFinite(seconds)||seconds<=0||seconds>120||signal?.aborted)return null;
  if(!/^55[1-9]\d(?:9\d{8}|[2-5]\d{7})$/.test(phone))return null;
  if(typeof token!=="string"||token.length<32)return null;
  let target;
  try{
    target=new URL(apiUrl);
    if(target.protocol!=="http:"||target.hostname!=="orion-assistente.railway.internal"||
      target.pathname!=="/internal/qr/ai/reply"||target.search||target.hash||target.username||target.password)return null;
    target.pathname="/internal/qr/ai/transcribe";
  }catch{return null}
  const controller=new AbortController();
  const abort=()=>controller.abort();
  signal?.addEventListener('abort',abort,{once:true});
  const timeout=setTimeout(abort,35000);
  let buffer,stream;const chunks=[];
  try{
    stream=await downloadMediaMessage(message,"stream",{options:{signal:controller.signal,timeout:15000}},{
      logger,reuploadRequest:client.updateMediaMessage
    });
    if(Buffer.isBuffer(stream))buffer=stream;
    else if(stream?.[Symbol.asyncIterator]){
      let bytes=0;
      for await(const chunk of stream){
        const data=Buffer.isBuffer(chunk)?chunk:Buffer.from(chunk);
        chunks.push(data);bytes+=data.length;
        if(bytes>2_000_000||controller.signal.aborted){controller.abort();return null;}
      }
      buffer=Buffer.concat(chunks);
    }
    if(!Buffer.isBuffer(buffer)||buffer.length<1||buffer.length>2_000_000||controller.signal.aborted)return null;
    const response=await fetchFn(target.toString(),{
      method:"POST",
      headers:{"Content-Type":"audio/ogg","X-Orion-Bridge-Key":token,
               "X-Orion-Phone":phone},
      body:buffer,signal:controller.signal
    });
    if(!response.ok||controller.signal.aborted)return null;
    const result=await response.json();
    if(controller.signal.aborted||result?.truncated===true||typeof result?.text!=="string")return null;
    const text=result.text.trim();
    return text.length<=1500?text||null:null;
  }finally{
    clearTimeout(timeout);signal?.removeEventListener('abort',abort);
    if(Buffer.isBuffer(buffer))buffer.fill(0);
    for(const chunk of chunks)chunk.fill(0);
    if(typeof stream?.destroy==='function')stream.destroy();
  }
}
