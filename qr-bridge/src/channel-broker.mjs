/** Only fixed private API paths; no destination is taken from a request/model. */
import {channelId} from './channel-storage.mjs';

export function createChannelBroker({apiUrl,fetchImpl=fetch,env=process.env}){
  const base=new URL(apiUrl);
  if(base.protocol!=='http:'||!base.hostname.endsWith('.railway.internal')||
      base.username||base.password||base.search||base.hash||base.pathname!=='/')throw Error('unsafe_channel_broker');
  async function request(id,key,operation,body,{owner,fence,signal,phone,mediaType}={}){
    if(!channelId(id)||typeof key!=='string'||key.length<32||key.length>256)throw Error('invalid_channel');
    const audio=operation==='audio';
    if(audio&&(env.QR_AI_AUDIO_ENABLED!=='1'||!channelId(owner)||!Number.isSafeInteger(fence)||fence<1||
        !/^55[1-9]\d(?:9\d{8}|[2-5]\d{7})$/.test(phone)||!Buffer.isBuffer(body)||body.length<1||body.length>2_000_000||
        !['audio/ogg','audio/webm'].includes(mediaType)))throw Error('channel_audio_disabled_or_invalid');
    if(signal?.aborted)throw Error('channel_request_aborted');
    const target=new URL(`/internal/qr/channels/${id}/${operation}`,base);
    const controller=new AbortController(),abort=()=>controller.abort(),timer=setTimeout(abort,audio?35000:operation==='ai/reply'?19000:5000);
    signal?.addEventListener('abort',abort,{once:true});
    try{
      const response=await fetchImpl(target,{method:'POST',redirect:'error',signal:controller.signal,
        headers:{'Content-Type':audio?mediaType:'application/json','X-Orion-Channel-Key':key,
          ...(owner?{'X-Orion-Channel-Owner':owner,'X-Orion-Channel-Fence':String(fence)}:{}),
          ...(audio?{'X-Orion-Phone':phone}:{})},body:audio?body:JSON.stringify(body)});
      if(!response.ok||controller.signal.aborted)throw Error('channel_broker_refused');
      return await response.json();
    }finally{clearTimeout(timer);signal?.removeEventListener('abort',abort);}
  }
  return {
    lease:(id,key,operation,owner,fence)=>request(id,key,'lease/'+operation,{owner,...(fence?{fence}:{})}),
    interaction:(id,key,lease,payload)=>request(id,key,'interaction',payload,lease),
    transcribe:(id,key,lease,{phone,buffer,mediaType,signal})=>request(id,key,'audio',buffer,{...lease,phone,mediaType,signal}),
    forward:(id,key,lease,payload)=>request(id,key,'ai/reply',payload,lease)
  };
}
