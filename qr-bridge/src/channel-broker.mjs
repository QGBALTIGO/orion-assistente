/** Only fixed private API paths; no destination is taken from a request/model. */
import {channelId} from './channel-storage.mjs';
import {DEVICE_AUDIO_MEDIA_TYPES} from './device-audio.mjs';

export function createChannelBroker({apiUrl,fetchImpl=fetch,env=process.env}){
  const base=new URL(apiUrl);
  if(base.protocol!=='http:'||!base.hostname.endsWith('.railway.internal')||
      base.username||base.password||base.search||base.hash||base.pathname!=='/')throw Error('unsafe_channel_broker');
  async function request(id,key,operation,body,{owner,fence,signal,phone,mediaType,device,method='POST'}={}){
    if(!channelId(id)||typeof key!=='string'||key.length<32||key.length>256)throw Error('invalid_channel');
    const deviceAudio=operation==='device/audio',audio=operation==='audio'||deviceAudio;
    if(audio&&((!deviceAudio&&env.QR_AI_AUDIO_ENABLED!=='1')||!channelId(owner)||!Number.isSafeInteger(fence)||fence<1||
        !(deviceAudio?/^[1-9]\d{7,14}$/:/^55[1-9]\d(?:9\d{8}|[2-5]\d{7})$/).test(phone)||!Buffer.isBuffer(body)||body.length<1||body.length>(deviceAudio?25_000_000:2_000_000)||
        !(deviceAudio?DEVICE_AUDIO_MEDIA_TYPES:['audio/ogg','audio/webm']).includes(mediaType)))throw Error('channel_audio_disabled_or_invalid');
    if(device&&(!channelId(device.device_id)||!channelId(device.generation)||!Number.isSafeInteger(device.settings_revision)||device.settings_revision<1||
      (deviceAudio&&!channelId(device.operation_id))))throw Error('invalid_device_request');
    if(deviceAudio&&!device)throw Error('invalid_device_request');
    if(signal?.aborted)throw Error('channel_request_aborted');
    const target=new URL(`/internal/qr/channels/${id}/${operation}`,base);
    const controller=new AbortController(),abort=()=>controller.abort(),timer=setTimeout(abort,deviceAudio?125000:audio?35000:operation==='ai/reply'?19000:5000);
    signal?.addEventListener('abort',abort,{once:true});
    try{
      const response=await fetchImpl(target,{method,redirect:'error',signal:controller.signal,
        headers:{'Content-Type':audio?mediaType:'application/json','X-Orion-Channel-Key':key,
          ...(owner?{'X-Orion-Channel-Owner':owner,'X-Orion-Channel-Fence':String(fence)}:{}),
          ...(audio?{'X-Orion-Phone':phone}:{}),
          ...(device?{'X-Orion-Device-Id':device.device_id,'X-Orion-Device-Generation':device.generation,
            'X-Orion-Device-Revision':String(device.settings_revision),...(device.operation_id?{'X-Orion-Device-Effect':device.operation_id}:{})}:{})},
        ...(method==='GET'?{}:{body:audio?body:JSON.stringify(body)})});
      if(!response.ok||controller.signal.aborted){const error=Error('channel_broker_refused');error.status=response.status;throw error;}
      return await response.json();
    }finally{clearTimeout(timer);signal?.removeEventListener('abort',abort);}
  }
  return {
    lease:(id,key,operation,owner,fence)=>request(id,key,'lease/'+operation,{owner,...(fence?{fence}:{})}),
    interaction:(id,key,lease,payload)=>request(id,key,'interaction',payload,lease),
    journal:(id,key,lease,payload)=>request(id,key,'messages',payload,lease),
    receipt:(id,key,lease,payload)=>request(id,key,'message-receipt',payload,lease),
    transcribe:(id,key,lease,{phone,buffer,mediaType,signal})=>request(id,key,'audio',buffer,{...lease,phone,mediaType,signal}),
    forward:(id,key,lease,payload)=>request(id,key,'ai/reply',payload,lease),
    authorizeSend:(id,key,lease,payload)=>request(id,key,'send-authorize',payload,lease),
    finishSend:(id,key,lease,payload)=>request(id,key,'send-finish',payload,lease),
    deviceEvent:(id,key,lease,payload)=>request(id,key,'device/event',payload,lease),
    deviceEffectsPending:(id,key,lease,device)=>request(id,key,'device/effects/pending',undefined,{...lease,device,method:'GET'}),
    deviceEffectAuthorize:(id,key,lease,payload)=>request(id,key,'device/effects/authorize',payload,lease),
    deviceEffectFinish:(id,key,lease,payload)=>request(id,key,'device/effects/finish',payload,lease),
    deviceTranscribe:(id,key,lease,{phone,buffer,mediaType,signal,...device})=>request(id,key,'device/audio',buffer,{...lease,phone,mediaType,signal,device}),
    deviceControlAuthorize:(id,key,payload)=>request(id,key,'device/control/authorize',payload),
    deviceControlFinish:(id,key,payload)=>request(id,key,'device/control/finish',payload)
  };
}
