/** Device transcription is separate from assistant input and has its own limits.
 * Download only after a server-issued effect grant; no disk or provider call here. */
export const DEVICE_AUDIO_MAX_BYTES=25_000_000;
export const DEVICE_AUDIO_MAX_SECONDS=1800;
export const DEVICE_TEXT_MAX_CHARS=32000;
export const DEVICE_AUDIO_MEDIA_TYPES=Object.freeze(['audio/ogg','audio/opus','audio/webm','audio/wav','audio/x-wav','audio/mpeg','audio/mp4']);

export function deviceAudioMetadata(message){
  const media=message?.message?.audioMessage||message?.message?.ephemeralMessage?.message?.audioMessage;
  if(!media)return null;
  const size=Number(media.fileLength),seconds=Number(media.seconds),media_type=String(media.mimetype||'audio/ogg').split(';')[0].trim().toLowerCase();
  if(typeof media.fileLength==='boolean'||typeof media.seconds==='boolean'||!Number.isFinite(size)||size<1||size>DEVICE_AUDIO_MAX_BYTES||!Number.isFinite(seconds)||seconds<=0||seconds>DEVICE_AUDIO_MAX_SECONDS||
      !DEVICE_AUDIO_MEDIA_TYPES.includes(media_type))return null;
  return {size,seconds,media_type};
}

export async function withDeviceAudio(message,{client,downloadMediaMessage,logger,signal,downloadOptions={},
  maxBytes,maxSeconds,timeoutMs=160000,assertCurrent=async()=>{}},consume){
  const metadata=deviceAudioMetadata(message);
  if(!metadata||!Number.isSafeInteger(maxBytes)||maxBytes<1||maxBytes>DEVICE_AUDIO_MAX_BYTES||
      !Number.isSafeInteger(maxSeconds)||maxSeconds<1||maxSeconds>DEVICE_AUDIO_MAX_SECONDS||
      metadata.size>maxBytes||metadata.seconds>maxSeconds||!Number.isSafeInteger(timeoutMs)||timeoutMs<1||timeoutMs>160000||
      signal?.aborted||typeof consume!=='function'||typeof downloadMediaMessage!=='function')return null;
  const controller=new AbortController(),abort=()=>controller.abort(),chunks=[];
  let buffer,stream;signal?.addEventListener('abort',abort,{once:true});
  const timer=setTimeout(abort,timeoutMs);
  try{
    await assertCurrent();if(signal?.aborted||controller.signal.aborted)return null;
    stream=await downloadMediaMessage(message,'stream',{options:{...downloadOptions,signal:controller.signal,timeout:30000,maxRedirects:0}},
      {logger,reuploadRequest:client.updateMediaMessage});
    if(Buffer.isBuffer(stream))buffer=stream;
    else if(stream?.[Symbol.asyncIterator]){
      let size=0;
      for await(const chunk of stream){
        const bytes=Buffer.isBuffer(chunk)?chunk:Buffer.from(chunk);chunks.push(bytes);size+=bytes.length;
        if(size>maxBytes||controller.signal.aborted){controller.abort();return null;}
      }
      buffer=Buffer.concat(chunks);
    }
    if(!Buffer.isBuffer(buffer)||!buffer.length||buffer.length>maxBytes||controller.signal.aborted)return null;
    await assertCurrent();if(controller.signal.aborted)return null;
    const result=await consume({buffer,mediaType:metadata.media_type,seconds:metadata.seconds,signal:controller.signal});
    await assertCurrent();return controller.signal.aborted?null:result;
  }finally{
    clearTimeout(timer);signal?.removeEventListener('abort',abort);
    if(Buffer.isBuffer(buffer))buffer.fill(0);for(const chunk of chunks)chunk.fill(0);
    if(typeof stream?.destroy==='function')stream.destroy();
  }
}
