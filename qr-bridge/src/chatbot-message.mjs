/** Native Baileys 6.7.24 protocols. Only canonical grants and verified private
 * assets enter this builder; raw URLs, file paths and browser proto do not. */
import {createHash} from 'node:crypto';
import {channelId} from './channel-storage.mjs';
import {fetchChatbotImage} from './chatbot-network.mjs';
import {awaitChatbotEncryptedMedia} from './chatbot-media-upload.mjs';
import {urlButtonContent} from './url-button.mjs';

const invalid=()=>Object.assign(Error('chatbot_message_invalid'),{code:'CHATBOT_MESSAGE_INVALID'});
const plain=value=>!!value&&typeof value==='object'&&!Array.isArray(value);
const sha=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
export const chatbotSelectionId=value=>typeof value==='string'&&/^[a-zA-Z0-9:_-]{1,160}$/.test(value);
function text(value,max,{optional=false}={}){
  if(optional&&(value===undefined||value===null))return '';
  if(typeof value!=='string'||value.length>max||/[\u0000]/.test(value)||(!optional&&!value.trim())||
      /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value))throw invalid();
  return value;
}
const limits={image:{mime:'image/jpeg',max:5_000_000},sticker:{mime:'image/webp',max:500_000},
  video:{mime:'video/mp4',max:16_000_000,seconds:180},ptv:{mime:'video/mp4',max:16_000_000,seconds:60},
  audio:{mime:'audio/mpeg',max:16_000_000,seconds:1800},
  document:{mimes:['text/plain; charset=utf-8','application/pdf','application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','application/vnd.android.package-archive'],max:10_000_000}};

export function validateChatbotAsset(asset,kind){
  const limit=limits[kind];
  if(!limit||!plain(asset)||!channelId(asset.id)||asset.kind!==kind||!(limit.mimes||[limit.mime]).includes(asset.mime)||
      !Number.isSafeInteger(asset.size)||asset.size<1||asset.size>limit.max||!sha(asset.sha256))throw invalid();
  if(!['document','audio'].includes(kind)){
    if(!Number.isSafeInteger(asset.width)||!Number.isSafeInteger(asset.height)||asset.width<1||asset.height<1||asset.width>4096||asset.height>4096)throw invalid();
    if(['sticker','ptv'].includes(kind)&&(asset.width!==512||asset.height!==512))throw invalid();
  }
  if(['document','audio'].includes(kind)&&(asset.width!=null||asset.height!=null||asset.thumbnail!=null))throw invalid();
  if(kind==='document'&&asset.duration_ms!=null)throw invalid();
  if(limit.seconds&&(!Number.isSafeInteger(asset.duration_ms)||asset.duration_ms<1||asset.duration_ms>limit.seconds*1000))throw invalid();
  if(kind==='sticker'&&asset.duration_ms!=null&&(!Number.isSafeInteger(asset.duration_ms)||asset.duration_ms<1||asset.duration_ms>6000))throw invalid();
  if(kind==='sticker'&&asset.duration_ms==null&&asset.size>100_000)throw invalid();
  if(asset.thumbnail!=null&&(!plain(asset.thumbnail)||asset.thumbnail.mime!=='image/jpeg'||!sha(asset.thumbnail.sha256)||
      !Number.isSafeInteger(asset.thumbnail.size)||asset.thumbnail.size<1||asset.thumbnail.size>100_000))throw invalid();
  if(['image','video','ptv'].includes(kind)&&!asset.thumbnail)throw invalid();
  if(kind==='document'&&asset.file_name!=null)fileName(asset.file_name,asset.mime);
  return asset;
}

function fileName(value,mime){
  const clean=text(value,120);
  const extensions={'text/plain; charset=utf-8':['txt','csv'],'application/pdf':['pdf'],
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document':['docx'],
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':['xlsx'],
    'application/vnd.android.package-archive':['apk']};
  if(!(extensions[mime]||[]).includes(clean.split('.').pop().toLowerCase())||/[\/\\\u0000-\u001f\u007f]/.test(clean)||clean==='..')throw invalid();
  return clean;
}
function verifyBytes(value,meta){
  if(!Buffer.isBuffer(value)||value.length!==meta.size||createHash('sha256').update(value).digest('hex')!==meta.sha256)throw invalid();
  return value;
}

/** Buffers stay alive until the exact protocol has been encrypted and relayed.
 * dispose() erases all original media and thumbnail buffers on every outcome. */
export async function buildChatbotContent(action,{sdk,loadAsset,prepareCarouselImage,
  fetchImage=fetchChatbotImage,upload,assertCurrent=async()=>{},isCurrent=()=>true,signal,logger,jid,
  mediaOptions={},interactiveMode='native'}={}){
  const owned=[];
  const dispose=()=>{for(const buffer of owned)buffer.fill(0);owned.length=0;};
  const guard=async()=>{await assertCurrent();if(signal?.aborted||!isCurrent())throw Object.assign(Error('chatbot_revoked'),{code:'CHATBOT_REVOKED'});};
  async function assetBytes(asset,variant){
    if(typeof loadAsset!=='function')throw invalid();
    await guard();const buffer=await loadAsset(asset,{variant,signal});
    if(Buffer.isBuffer(buffer))owned.push(buffer);
    await guard();return verifyBytes(buffer,variant==='thumbnail'?asset.thumbnail:asset);
  }
  async function media(asset,kind,caption,documentName){
    validateChatbotAsset(asset,kind);
    if(['sticker','ptv','audio'].includes(kind)&&caption)throw invalid();
    const buffer=await assetBytes(asset,'original');
    const thumbnail=asset.thumbnail?await assetBytes(asset,'thumbnail'):undefined;
    const mediaKind=kind==='ptv'?'video':kind;
    const input={[mediaKind]:buffer,mimetype:asset.mime,
      ...(kind==='ptv'?{ptv:true}:{}),
      ...(kind==='document'?{fileName:fileName(documentName??asset.file_name??'resposta.txt',asset.mime)}:{}),
      ...(kind==='audio'?{ptt:false}:{}),
      ...(asset.width?{width:asset.width,height:asset.height}:{}),
      ...(asset.duration_ms?{seconds:Math.max(1,Math.ceil(asset.duration_ms/1000))}:{}),
      ...(kind==='sticker'?{isAnimated:asset.duration_ms!=null}:{}),
      ...(thumbnail?{jpegThumbnail:thumbnail}:{}),
      ...(caption?{caption:text(caption,1024)}:{})};
    if(typeof upload!=='function'||typeof sdk?.prepareWAMessageMedia!=='function')throw invalid();
    await guard();
    const prepared=await sdk.prepareWAMessageMedia(input,{logger,jid,mediaUploadTimeoutMs:20000,
      options:{...mediaOptions,signal},upload:async(file,metadata)=>{
        await guard();await awaitChatbotEncryptedMedia(file,metadata,{signal,isCurrent});await guard();
        const result=await upload(file,metadata);await guard();return result;}});
    await guard();
    const key=kind==='ptv'?'ptvMessage':mediaKind+'Message';
    if(!prepared?.[key]||(kind==='ptv'&&prepared.videoMessage))throw invalid();
    return prepared;
  }
  try{
    if(!plain(action)||typeof sdk?.proto?.Message?.fromObject!=='function')throw invalid();
    await guard();let content,selectionIds,textFallback=false;
    if(!['native','text_fallback'].includes(interactiveMode))throw invalid();
    if(action.type==='text')content={extendedTextMessage:{text:text(action.text,32000)}};
    else if(action.type==='url_button')content=urlButtonContent(action);
    else if(action.type==='location'){
      if(!Number.isFinite(action.latitude)||!Number.isFinite(action.longitude)||Math.abs(action.latitude)>90||Math.abs(action.longitude)>180)throw invalid();
      content={locationMessage:{degreesLatitude:action.latitude,degreesLongitude:action.longitude}};
    }
    else if(action.type==='list'){
      if(!Array.isArray(action.sections)||action.sections.length<1||action.sections.length>10)throw invalid();
      const ids=new Set();let count=0;
      const sections=action.sections.map(section=>{
        if(!plain(section)||!Array.isArray(section.rows)||!section.rows.length||section.rows.length>10)throw invalid();
        return {title:text(section.title,24,{optional:true}),rows:section.rows.map(row=>{
          if(!plain(row)||!chatbotSelectionId(row.id)||ids.has(row.id)||++count>10)throw invalid();ids.add(row.id);
          return {rowId:row.id,title:text(row.title,24),description:text(row.description,72,{optional:true})};
        })};
      });
      content={listMessage:{title:text(action.title,60),description:text(action.description,1000),
        footerText:text(action.footer,60,{optional:true}),buttonText:text(action.button_text,20),listType:1,sections}};
      if(interactiveMode==='text_fallback'){
        let index=0;selectionIds=[];textFallback=true;
        const rows=sections.flatMap(section=>[...(section.title?[section.title]:[]),...section.rows.map(row=>{
          selectionIds.push(row.rowId);return `${++index}. ${row.title}${row.description?' — '+row.description:''}`;
        })]);
        content={extendedTextMessage:{text:[action.title,action.description,...rows,action.footer||'',
          'Responda citando esta mensagem e informe o número da opção.'].filter(Boolean).join('\n\n')}};
      }
    }else if(limits[action.type])content=await media(action.asset,action.type,action.caption,action.file_name);
    else if(action.type==='carousel'){
      if(!Array.isArray(action.cards)||action.cards.length!==3||interactiveMode==='native'&&typeof prepareCarouselImage!=='function')throw invalid();
      if(interactiveMode==='text_fallback'){
        const ids=new Set();textFallback=true;
        const cards=action.cards.map((card,index)=>{
          if(!plain(card)||!chatbotSelectionId(card.selection_id)||ids.has(card.selection_id))throw invalid();ids.add(card.selection_id);
          const title=text(card.title,60),body=text(card.body,1024,{optional:index!==0});text(card.button_text,20);
          return `${index+1}. ${title}${body?'\n'+body:''}`;
        });
        // Carousel selections are evidence-only in the current domain model.
        // Present its information without inviting a nonexistent action.
        content={extendedTextMessage:{text:[text(action.body,1024),...cards].join('\n\n')}};
      }else{
      const cards=[],ids=new Set();
      for(const [index,card] of action.cards.entries()){
        if(!plain(card)||!chatbotSelectionId(card.selection_id)||ids.has(card.selection_id))throw invalid();ids.add(card.selection_id);
        const title=text(card.title,60),body=text(card.body,1024,{optional:index!==0}),button=text(card.button_text,20);
        await guard();let original;
        try{
          // Manual resends use the immutable private asset from the original
          // attempt. A changed remote URL must never change its visible image.
          let asset=card.asset;
          if(asset==null){
            original=await fetchImage({url:card.image_url,signal,isCurrent,assertCurrent:guard});
            if(!Buffer.isBuffer(original?.buffer)||original.buffer.length<1||original.buffer.length>8_000_000)throw invalid();
            await guard();asset=await prepareCarouselImage(index,original.buffer,{signal});
          }else if(card.image_url!=null)throw invalid();
          await guard();
          const prepared=await media(asset,'image');
          cards.push({header:{title,hasMediaAttachment:true,imageMessage:prepared.imageMessage},body:{text:body},
            nativeFlowMessage:{buttons:[{name:'quick_reply',buttonParamsJson:JSON.stringify({display_text:button,id:card.selection_id})}],messageVersion:1}});
        }finally{original?.buffer?.fill(0);}
      }
      // This is the actual interactive carousel proto. Rendering depends on
      // WhatsApp client capabilities; there is intentionally no text fallback.
      content={interactiveMessage:{body:{text:text(action.body,1024)},carouselMessage:{cards,messageVersion:1}}};
      }
    }else throw invalid();
    await guard();return {content:sdk.proto.Message.fromObject(content),dispose,
      ...(selectionIds?{selectionIds:Object.freeze(selectionIds)}:{}),wireMode:textFallback?'text_fallback':'native'};
  }catch(error){dispose();throw error;}
}
