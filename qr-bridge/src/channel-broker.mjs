/** Only fixed private API paths; no destination is taken from a request/model. */
import {channelId} from './channel-storage.mjs';
import {DEVICE_AUDIO_MEDIA_TYPES} from './device-audio.mjs';
import {validGroupAddRequest} from './device-group-add.mjs';

async function readPrivateAsset(response,{size,mime},signal){
  const chunks=[];let total=0,reader;
  try{
    if(response.headers?.get('content-type')?.toLowerCase()!==mime.toLowerCase()||
        (response.headers?.get('content-length')!=null&&Number(response.headers.get('content-length'))!==size))throw Error('chatbot_asset_invalid');
    if(!response.body?.getReader)throw Error('chatbot_asset_unavailable');
    reader=response.body.getReader();
    for(;;){
      if(signal.aborted)throw Error('chatbot_asset_aborted');
      const {done,value}=await reader.read();if(done)break;
      const chunk=Buffer.from(value);total+=chunk.length;
      if(total>size){chunk.fill(0);throw Error('chatbot_asset_too_large');}chunks.push(chunk);
    }
    if(signal.aborted||total!==size)throw Error('chatbot_asset_invalid');
    return Buffer.concat(chunks,total);
  }finally{for(const chunk of chunks)chunk.fill(0);try{await reader?.cancel();}catch{}reader?.releaseLock();}
}

export function createChannelBroker({apiUrl,fetchImpl=fetch,env=process.env}){
  const base=new URL(apiUrl);
  if(base.protocol!=='http:'||!base.hostname.endsWith('.railway.internal')||
      base.username||base.password||base.search||base.hash||base.pathname!=='/')throw Error('unsafe_channel_broker');
  async function request(id,key,operation,body,{owner,fence,signal,phone,mediaType,device,method='POST',binary,cardIndex}={}){
    if(!channelId(id)||typeof key!=='string'||key.length<32||key.length>256)throw Error('invalid_channel');
    const deviceAudio=operation==='device/audio',audio=operation==='audio'||deviceAudio;
    const carouselImage=operation==='device/chatbot/carousel-image',chatbot=operation.startsWith('device/chatbot/'),campaign=operation.startsWith('device/campaigns/'),scheduled=operation.startsWith('device/scheduled/'),bulk=operation.startsWith('device/bulk/'),raw=audio||carouselImage;
    if(audio&&((!deviceAudio&&env.QR_AI_AUDIO_ENABLED!=='1')||!channelId(owner)||!Number.isSafeInteger(fence)||fence<1||
        !(deviceAudio?/^[1-9]\d{7,14}$/:/^55[1-9]\d(?:9\d{8}|[2-5]\d{7})$/).test(phone)||!Buffer.isBuffer(body)||body.length<1||body.length>(deviceAudio?25_000_000:2_000_000)||
        !(deviceAudio?DEVICE_AUDIO_MEDIA_TYPES:['audio/ogg','audio/webm']).includes(mediaType)))throw Error('channel_audio_disabled_or_invalid');
    if(device&&(!channelId(device.device_id)||!channelId(device.generation)||!Number.isSafeInteger(device.settings_revision)||device.settings_revision<1||
      (deviceAudio&&!channelId(device.operation_id))))throw Error('invalid_device_request');
    if(deviceAudio&&!device)throw Error('invalid_device_request');
    if(chatbot&&operation!=='device/chatbot/http-result'&&(!device||!channelId(device.operation_id)||
        !Number.isSafeInteger(device.chatbot_revision)||device.chatbot_revision<1||!channelId(owner)||!Number.isSafeInteger(fence)||fence<1))throw Error('invalid_chatbot_request');
    if(campaign&&(!device||!channelId(device.operation_id)||device.chatbot_revision!==undefined||
        !channelId(owner)||!Number.isSafeInteger(fence)||fence<1))throw Error('invalid_campaign_request');
    if(scheduled&&(!device||!channelId(device.operation_id)||device.chatbot_revision!==undefined||
        !channelId(owner)||!Number.isSafeInteger(fence)||fence<1))throw Error('invalid_scheduled_request');
    if(bulk&&(!device||!channelId(device.operation_id)||device.chatbot_revision!==undefined||
        !channelId(owner)||!Number.isSafeInteger(fence)||fence<1))throw Error('invalid_bulk_request');
    if(carouselImage&&(!Buffer.isBuffer(body)||body.length<1||body.length>8_000_000||!Number.isSafeInteger(cardIndex)||cardIndex<0||cardIndex>2))throw Error('invalid_carousel_image');
    if(binary&&(!Number.isSafeInteger(binary.size)||binary.size<1||binary.size>16_000_000||typeof binary.mime!=='string'||binary.mime.length>80))throw Error('invalid_chatbot_asset');
    if(signal?.aborted)throw Error('channel_request_aborted');
    const target=new URL(`/internal/qr/channels/${id}/${operation}`,base);
    const controller=new AbortController(),abort=()=>controller.abort(),timer=setTimeout(abort,deviceAudio?125000:audio?35000:chatbot||campaign||scheduled||bulk?30000:operation==='ai/reply'?19000:5000);
    signal?.addEventListener('abort',abort,{once:true});
    try{
      const response=await fetchImpl(target,{method,redirect:'error',signal:controller.signal,
        headers:{'Content-Type':audio?mediaType:carouselImage?'application/octet-stream':'application/json','X-Orion-Channel-Key':key,
          ...(owner?{'X-Orion-Channel-Owner':owner,'X-Orion-Channel-Fence':String(fence)}:{}),
          ...(operation==='device/effects/pending'?{'X-Orion-Bulk-Protocol':'1'}:{}),
          ...(audio?{'X-Orion-Phone':phone}:{}),
          ...(device?{'X-Orion-Device-Id':device.device_id,'X-Orion-Device-Generation':device.generation,
            'X-Orion-Device-Revision':String(device.settings_revision),...(device.operation_id?{'X-Orion-Device-Effect':device.operation_id}:{}),
            ...(device.chatbot_revision?{'X-Orion-Chatbot-Revision':String(device.chatbot_revision)}:{})}:{}),
          ...(carouselImage?{'X-Orion-Chatbot-Card':String(cardIndex)}:{})},
        ...(method==='GET'?{}:{body:raw?body:JSON.stringify(body)})});
      if(!response.ok||controller.signal.aborted){const error=Error('channel_broker_refused');error.status=response.status;throw error;}
      return binary?await readPrivateAsset(response,binary,controller.signal):await response.json();
    }finally{clearTimeout(timer);signal?.removeEventListener('abort',abort);}
  }
  return {
    lease:(id,key,operation,owner,fence)=>request(id,key,'lease/'+operation,{owner,...(fence?{fence}:{})}),
    interaction:(id,key,lease,payload)=>request(id,key,'interaction',payload,lease),
    journal:(id,key,lease,payload)=>request(id,key,'messages',payload,lease),
    receipt:(id,key,lease,payload)=>request(id,key,'message-receipt',payload,lease),
    transcribe:(id,key,lease,{phone,buffer,mediaType,signal})=>request(id,key,'audio',buffer,{...lease,phone,mediaType,signal}),
    forward:(id,key,lease,payload)=>request(id,key,'ai/reply',payload,lease),
    presentationAck:(id,key,lease,payload)=>request(id,key,'ai/presentation-ack',payload,lease),
    authorizeSend:(id,key,lease,payload)=>request(id,key,'send-authorize',payload,lease),
    finishSend:(id,key,lease,payload)=>request(id,key,'send-finish',payload,lease),
    deviceEvent:(id,key,lease,payload)=>request(id,key,'device/event',payload,lease),
    deviceEffectsPending:(id,key,lease,device)=>request(id,key,'device/effects/pending',undefined,{...lease,device,method:'GET'}),
    deviceEffectAuthorize:(id,key,lease,payload)=>request(id,key,'device/effects/authorize',payload,lease),
    deviceEffectFinish:(id,key,lease,payload)=>request(id,key,'device/effects/finish',payload,lease),
    deviceTranscribe:(id,key,lease,{phone,buffer,mediaType,signal,...device})=>request(id,key,'device/audio',buffer,{...lease,phone,mediaType,signal,device}),
    deviceChatbotAsset:(id,key,lease,{asset,variant='original',signal,...device})=>{
      if(!channelId(asset?.id)||!['original','thumbnail'].includes(variant))throw Error('invalid_chatbot_asset');
      const meta=variant==='thumbnail'?asset.thumbnail:asset;if(!meta)throw Error('invalid_chatbot_asset');
      return request(id,key,`device/chatbot/assets/${asset.id}/content?variant=${variant}`,undefined,{...lease,signal,device,method:'GET',binary:meta});
    },
    deviceCampaignAsset:(id,key,lease,{asset,variant='original',signal,...device})=>{
      if(!channelId(asset?.id)||!['original','thumbnail'].includes(variant))throw Error('invalid_campaign_asset');
      const meta=variant==='thumbnail'?asset.thumbnail:asset;if(!meta)throw Error('invalid_campaign_asset');
      if(!channelId(device.device_id)||!channelId(device.generation)||!channelId(device.operation_id)||
          !Number.isSafeInteger(device.settings_revision)||device.settings_revision<1)throw Error('invalid_campaign_request');
      const query=new URLSearchParams({device_id:device.device_id,generation:device.generation,
        settings_revision:String(device.settings_revision),operation_id:device.operation_id,variant});
      return request(id,key,`device/campaigns/assets/${asset.id}?${query}`,undefined,{...lease,signal,device,method:'GET',binary:meta});
    },
    deviceScheduledAsset:(id,key,lease,{asset,variant='original',signal,...device})=>{
      if(!channelId(asset?.id)||!['original','thumbnail'].includes(variant))throw Error('invalid_scheduled_asset');
      const meta=variant==='thumbnail'?asset.thumbnail:asset;if(!meta)throw Error('invalid_scheduled_asset');
      if(!channelId(device.device_id)||!channelId(device.generation)||!channelId(device.operation_id)||
          !Number.isSafeInteger(device.settings_revision)||device.settings_revision<1)throw Error('invalid_scheduled_request');
      const query=new URLSearchParams({device_id:device.device_id,generation:device.generation,
        settings_revision:String(device.settings_revision),operation_id:device.operation_id,variant});
      return request(id,key,`device/scheduled/assets/${asset.id}?${query}`,undefined,{...lease,signal,device,method:'GET',binary:meta});
    },
    deviceBulkAsset:(id,key,lease,{asset,variant='original',signal,...device})=>{
      if(!channelId(asset?.id)||!['original','thumbnail'].includes(variant))throw Error('invalid_bulk_asset');
      const meta=variant==='thumbnail'?asset.thumbnail:asset;if(!meta)throw Error('invalid_bulk_asset');
      if(!channelId(device.device_id)||!channelId(device.generation)||!channelId(device.operation_id)||
          !Number.isSafeInteger(device.settings_revision)||device.settings_revision<1)throw Error('invalid_bulk_request');
      const query=new URLSearchParams({device_id:device.device_id,generation:device.generation,
        settings_revision:String(device.settings_revision),operation_id:device.operation_id,variant});
      return request(id,key,`device/bulk/assets/${asset.id}?${query}`,undefined,{...lease,signal,device,method:'GET',binary:meta});
    },
    deviceChatbotCarouselImage:(id,key,lease,{buffer,card_index,signal,...device})=>request(id,key,'device/chatbot/carousel-image',buffer,{...lease,signal,device,cardIndex:card_index}),
    deviceChatbotHttpResult:(id,key,lease,payload)=>request(id,key,'device/chatbot/http-result',payload,lease),
    deviceControlAuthorize:(id,key,payload)=>request(id,key,'device/control/authorize',payload),
    deviceControlFinish:(id,key,payload)=>request(id,key,'device/control/finish',payload),
    deviceProxyAuthorize:(id,key,payload)=>request(id,key,'device/diagnostics/proxy/authorize',payload),
    deviceProxyFinish:(id,key,payload)=>request(id,key,'device/diagnostics/proxy/finish',payload),
    deviceProxyReceipt:(id,key,payload)=>request(id,key,'device/diagnostics/proxy/receipt',payload),
    deviceGroupAddAuthorize:(id,key,lease,{recheck,...payload})=>{
      if(!validGroupAddRequest('add',payload)||typeof recheck!=='boolean'||!channelId(lease?.owner)||
          !Number.isSafeInteger(lease?.fence)||lease.fence<1)throw Error('invalid_group_add_request');
      return request(id,key,'device/group-add/authorize',{...payload,recheck},lease);
    },
    deviceGroupAddReceipt:(id,key,payload)=>{
      if(!validGroupAddRequest('lookup',payload))throw Error('invalid_group_add_request');
      return request(id,key,'device/group-add/receipt',payload);
    }
  };
}
