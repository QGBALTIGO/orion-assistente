/** Delivery metadata is intentionally separate from ordinary subscription inputs.
 * No remote media URL, unverified name, arbitrary command or location guess. */
export const IMAGE_MAX_BYTES=8_000_000;
export const IMAGE_TYPES=['image/jpeg','image/png','image/webp'];
const id=v=>typeof v==='string'&&/^[a-zA-Z0-9._:-]{1,160}$/.test(v);
const number=(v,min,max)=>typeof v==='number'&&Number.isFinite(v)&&v>=min&&v<=max;
const integer=v=>v!==null&&v!==undefined&&typeof v!=='boolean'&&Number.isSafeInteger(Number(v))&&Number(v)>=0?Number(v):null;
function contentOf(message){
  let content=message?.message;
  for(let i=0;i<4;i++){
    if(content?.viewOnceMessage||content?.viewOnceMessageV2||content?.viewOnceMessageV2Extension)return {viewOnce:true};
    if(!content?.ephemeralMessage?.message)break;
    content=content.ephemeralMessage.message;
  }
  return content||{};
}
export function deliveryMedia(message,{audio=null}={}){
  if(message?.key?.fromMe!==false)return null;
  const c=contentOf(message);
  if(c.viewOnce)return {kind:'view_once'};
  if(c.audioMessage&&!audio)return {kind:'unsupported_audio'};
  if(c.imageMessage){
    const i=c.imageMessage,size=integer(i.fileLength),media_type=typeof i.mimetype==='string'?i.mimetype.split(';')[0].trim().toLowerCase():'';
    return size>0&&size<=IMAGE_MAX_BYTES&&IMAGE_TYPES.includes(media_type)?{kind:'image',size,media_type}:{kind:'image_unavailable'};
  }
  const live=c.liveLocationMessage,point=live||c.locationMessage;
  if(point){
    const latitude=point.degreesLatitude,longitude=point.degreesLongitude;
    if(!number(latitude,-90,90)||!number(longitude,-180,180))return {kind:'invalid_location'};
    if(!live)return {kind:'location',latitude,longitude};
    const source_id=message.orionDeliveryLiveSource||message.key.id,
      sequence=integer(live.sequenceNumber)??(message.orionDeliveryLiveUpdate===true?null:0);
    if(!id(source_id)||sequence===null)return {kind:'invalid_location'};
    return {kind:'live_location',latitude,longitude,source_id,sequence,update:message.orionDeliveryLiveUpdate===true};
  }
  if(c.documentMessage||c.documentWithCaptionMessage)return {kind:'document'};
  if(c.videoMessage)return {kind:'video'};
  return null;
}
export function deliveryLiveUpdate(item,now){
  const key=item?.key,content=item?.update?.message;
  if(!key||key.fromMe!==false||!id(key.id)||!content?.liveLocationMessage)return null;
  const candidate={key:{...key},message:content,messageTimestamp:Math.floor(now/1000),
    orionDeliveryLiveSource:key.id,orionDeliveryLiveUpdate:true};
  const media=deliveryMedia(candidate);
  return media?.kind==='live_location'?candidate:null;
}
export async function withDeliveryImage(message,{client,downloadMediaMessage,logger,signal,downloadOptions={},
  maxBytes=IMAGE_MAX_BYTES,assertCurrent=async()=>{}},consume){
  const meta=deliveryMedia(message);
  if(meta?.kind!=='image'||!Number.isSafeInteger(maxBytes)||maxBytes<1||maxBytes>IMAGE_MAX_BYTES||
      meta.size>maxBytes||signal?.aborted||typeof consume!=='function'||typeof downloadMediaMessage!=='function')return null;
  const controller=new AbortController(),abort=()=>controller.abort(),chunks=[];
  let buffer,stream;signal?.addEventListener('abort',abort,{once:true});
  const timer=setTimeout(abort,90000);timer.unref?.();
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
    const result=await consume({buffer,mediaType:meta.media_type,signal:controller.signal});
    await assertCurrent();return controller.signal.aborted?null:result;
  }finally{clearTimeout(timer);signal?.removeEventListener('abort',abort);
    if(Buffer.isBuffer(buffer))buffer.fill(0);for(const chunk of chunks)chunk.fill(0);stream?.destroy?.();}
}
