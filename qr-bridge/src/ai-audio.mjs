/** Bounded download lifecycle shared by legacy and authenticated channel audio.
 * consume owns routing/auth; this helper never fetches an API or persists media.
 */
export async function withAssistantAudio(message,{client,downloadMediaMessage,logger,signal},consume){
  const media=message?.message?.audioMessage||
    message?.message?.ephemeralMessage?.message?.audioMessage;
  if(!media)return null;
  const size=Number(media.fileLength||0);
  const seconds=Number(media.seconds);
  if(!Number.isFinite(size)||size<1||size>2_000_000||
      !Number.isFinite(seconds)||seconds<=0||seconds>120||signal?.aborted)return null;
  const mediaType=String(media.mimetype||'audio/ogg').split(';')[0].trim().toLowerCase();
  if(!['audio/ogg','audio/webm'].includes(mediaType)||typeof consume!=='function'||typeof downloadMediaMessage!=='function')return null;
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
    const result=await consume({buffer,mediaType,signal:controller.signal});
    return controller.signal.aborted?null:result;
  }finally{
    clearTimeout(timeout);signal?.removeEventListener('abort',abort);
    if(Buffer.isBuffer(buffer))buffer.fill(0);
    for(const chunk of chunks)chunk.fill(0);
    if(typeof stream?.destroy==='function')stream.destroy();
  }
}

/** Private legacy proxy. Its fixed URL and bridge credential stay unchanged. */
export async function transcribeAssistantAudio(message,{
  client,downloadMediaMessage,phone,token,apiUrl,logger,fetchFn=fetch,signal
}){
  if(!/^55[1-9]\d(?:9\d{8}|[2-5]\d{7})$/.test(phone)||typeof token!=="string"||token.length<32)return null;
  let target;
  try{
    target=new URL(apiUrl);
    if(target.protocol!=="http:"||target.hostname!=="orion-assistente.railway.internal"||
      target.pathname!=="/internal/qr/ai/reply"||target.search||target.hash||target.username||target.password)return null;
    target.pathname="/internal/qr/ai/transcribe";
  }catch{return null;}
  return withAssistantAudio(message,{client,downloadMediaMessage,logger,signal},async({buffer,mediaType,signal:audioSignal})=>{
    const response=await fetchFn(target.toString(),{method:'POST',headers:{'Content-Type':mediaType,
      'X-Orion-Bridge-Key':token,'X-Orion-Phone':phone},body:buffer,signal:audioSignal});
    if(!response.ok||audioSignal.aborted)return null;
    const result=await response.json();
    if(audioSignal.aborted||result?.truncated===true||typeof result?.text!=='string')return null;
    const text=result.text.trim();return text.length<=1500?text||null:null;
  });
}
