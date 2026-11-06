/** Typed assistant UI only. No model-provided proto, URL fetch or business action.
 * Selection authority remains in the API's current, contact-bound state. */
import path from 'node:path';
import {createHmac} from 'node:crypto';
import {channelId} from './channel-storage.mjs';
import {makeEncryptedRetryStore} from './encrypted-retry-store.mjs';
import {buildChatbotContent} from './chatbot-message.mjs';
import {normalizeChatbotInput} from './chatbot-input.mjs';
import {extractText} from './core.mjs';
import {sendStoredProtocol} from './reliable-send.mjs';
import {urlButtonContent} from './url-button.mjs';

const plain=x=>!!x&&typeof x==='object'&&!Array.isArray(x);
const opaque=x=>typeof x==='string'&&/^[A-Za-z0-9:_-]{1,160}$/.test(x);
const provider=x=>typeof x==='string'&&/^[A-Za-z0-9._:-]{1,160}$/.test(x);
const phone=x=>typeof x==='string'&&/^[1-9]\d{7,14}$/.test(x);
const invalid=()=>Object.assign(Error('assistant_presentation_invalid'),{code:'ASSISTANT_PRESENTATION_INVALID'});
const observe=(callback,category)=>{try{callback?.(category);}catch{/* Diagnostics never alter delivery or authorization. */}};
function ackFailure(error){
  if(error?.name==='AbortError'||error?.name==='TimeoutError'||error?.message==='assistant_ack_timeout')return 'ack_timeout';
  const status=error?.status;
  return [400,401,403,404,409,422,429,500,502,503,504].includes(status)?`ack_http_${status}`:
    Number.isInteger(status)&&status>=500&&status<=599?'ack_http_5xx':'ack_failed';
}
const fields=(value,names)=>plain(value)&&Object.keys(value).every(key=>names.includes(key));
const label=(value,max,optional=false)=>{
  if(optional&&value===undefined)return '';
  if(typeof value!=='string'||value.length>max||!optional&&!value.trim()||
      /\0|[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value))throw invalid();
  return value;
};
export const ASSISTANT_SELECTION_TEXT='[Escolha recebida]';
export const assistantInteractionSupported=proof=>proof?.assistant_interactions===1;
export const assistantSelectionId=value=>typeof value==='string'&&/^oa1_[A-Za-z0-9_-]{32}_[0-9]$/.test(value);
export function assistantSelection(value){
  if(!fields(value,['id','quoted_message_id'])||!assistantSelectionId(value.id)||!provider(value.quoted_message_id))throw invalid();
  return {id:value.id,quoted_message_id:value.quoted_message_id};
}
export function assistantNativeMode({tenantId,channelId:channel,phone:recipient,env=process.env}={}){
  if(!channelId(tenantId)||!channelId(channel)||!phone(recipient))return 'text_fallback';
  const raw=env.QR_ASSISTANT_NATIVE_SCOPES;
  if(typeof raw!=='string'||!raw||raw.length>20000)return 'text_fallback';
  const entries=raw.split(',').map(x=>x.trim());
  if(entries.length>128||entries.some(entry=>{
    const p=entry.split(':');return p.length!==3||!channelId(p[0])||!channelId(p[1])||!phone(p[2]);
  }))return 'text_fallback';
  return entries.includes(`${tenantId}:${channel}:${recipient}`)?'native':'text_fallback';
}
function appointmentDestination(value){
  if(typeof value!=='string'||value.length>2048||/[\u0000-\u0020\u007f]|%[01][0-9a-f]|%7f/i.test(value))throw invalid();
  let url;try{url=new URL(value);}catch{throw invalid();}
  if(url.origin!=='https://orionagenda.pro'||url.username||url.password||url.search||
      !(url.pathname==='/a'||/^\/AG-[A-Za-z0-9_-]{4,120}$/.test(url.pathname))||
      !/^#[A-Za-z0-9._~-]{12,512}$/.test(url.hash)||url.href!==value)throw invalid();
  return value;
}
export function validateAssistantPresentation(value){
  if(!fields(value,['version','id','action'])||value.version!==1||!opaque(value.id)||
      !/^[A-Za-z0-9_-]{32}$/.test(value.id)||!plain(value.action))throw invalid();
  const action=structuredClone(value.action),ids=[];
  const choice=id=>{if(!assistantSelectionId(id)||!id.startsWith(`oa1_${value.id}_`)||ids.includes(id))throw invalid();ids.push(id);return id;};
  if(action.type==='list'){
    if(!fields(action,['type','title','description','button_text','footer','sections'])||!Array.isArray(action.sections)||
        !action.sections.length||action.sections.length>10)throw invalid();
    label(action.title,60);label(action.description,1000);label(action.button_text,20);label(action.footer,60,true);
    for(const section of action.sections){
      if(!fields(section,['title','rows'])||!Array.isArray(section.rows)||!section.rows.length)throw invalid();
      label(section.title,24,true);
      for(const row of section.rows){if(!fields(row,['id','title','description']))throw invalid();choice(row.id);label(row.title,24);label(row.description,72,true);}
    }
    if(ids.length>10)throw invalid();
  }else if(action.type==='reply_buttons'){
    if(!fields(action,['type','text','footer','buttons'])||!Array.isArray(action.buttons)||action.buttons.length<2||action.buttons.length>3)throw invalid();
    label(action.text,1024);label(action.footer,60,true);
    for(const button of action.buttons){if(!fields(button,['id','title']))throw invalid();choice(button.id);label(button.title,20);}
  }else if(action.type==='url_button'){
    if(!fields(action,['type','text','footer','button_text','url','purpose']))throw invalid();
    label(action.text,1024);label(action.footer,60,true);label(action.button_text,20);
    if(action.purpose!==undefined){if(action.purpose!=='appointment')throw invalid();appointmentDestination(action.url);}
    else {delete action.purpose;urlButtonContent(action);}
  }else throw invalid();
  return Object.freeze({version:1,id:value.id,action,selectionIds:Object.freeze(ids)});
}
const nativeNodes=()=>({additionalNodes:[{tag:'biz',attrs:{},content:[{tag:'interactive',attrs:{type:'native_flow',v:'1'},
  content:[{tag:'native_flow',attrs:{v:'9',name:'mixed'}}]}]}]});
export async function buildAssistantPresentation(presentation,{sdk,interactiveMode='text_fallback',assertCurrent=async()=>{},isCurrent=()=>true}={}){
  const p=validateAssistantPresentation(presentation),a=p.action;
  await assertCurrent();if(!isCurrent()||!['native','text_fallback'].includes(interactiveMode))throw invalid();
  if(a.type==='list')return {...await buildChatbotContent(a,{sdk,interactiveMode,assertCurrent,isCurrent}),selectionIds:p.selectionIds};
  let content,wireMode=interactiveMode;
  if(a.type==='reply_buttons'){
    content=interactiveMode==='native'?{interactiveMessage:{body:{text:a.text},...(a.footer?{footer:{text:a.footer}}:{}),
      nativeFlowMessage:{buttons:a.buttons.map(b=>({name:'quick_reply',buttonParamsJson:JSON.stringify({display_text:b.title,id:b.id})})),messageVersion:1}}}:
      {extendedTextMessage:{text:[a.text,...a.buttons.map((b,i)=>`${i+1}. ${b.title}`),a.footer||'',
        'Responda citando esta mensagem e informe o número da opção.'].filter(Boolean).join('\n\n')}};
  }else if(a.type==='url_button'){
    if(a.purpose===undefined&&interactiveMode==='native')return {...await buildChatbotContent(a,{sdk,assertCurrent,isCurrent}),selectionIds:p.selectionIds};
    if(a.purpose===undefined){
      // Run the unchanged generic validator even for a text presentation.
      const checked=await buildChatbotContent(a,{sdk,assertCurrent,isCurrent});checked.dispose();
    }
    content=interactiveMode==='native'?{interactiveMessage:{body:{text:a.text},...(a.footer?{footer:{text:a.footer}}:{}),
      nativeFlowMessage:{buttons:[{name:'cta_url',buttonParamsJson:JSON.stringify({display_text:a.button_text,url:a.url})}],messageVersion:1}}}:
      {extendedTextMessage:{text:[a.text,`${a.button_text}: ${a.url}`,a.footer||''].filter(Boolean).join('\n\n')}};
  }
  await assertCurrent();if(!isCurrent()||typeof sdk?.proto?.Message?.fromObject!=='function')throw invalid();
  return {content:sdk.proto.Message.fromObject(content),selectionIds:p.selectionIds,wireMode,dispose(){},
    ...(interactiveMode==='native'?{relayOptions:nativeNodes()}: {})};
}
export async function relayAssistantPresentation({presentation,sdk,jid,recipientProof,phone:recipient,
  retryStore,presentationStore,relay,isCurrent,assertAuthorized,beforeSend,interactiveMode='text_fallback',acknowledge,
  deferPresentationAck=false,onPrepared=()=>{},onDiagnostic=()=>{}}){
  const diagnostic=(category,id)=>{try{onDiagnostic(category,id);}catch{}};
  let built;
  try{
    const sent=await sendStoredProtocol({jid,recipientProof,store:retryStore,isCurrent,assertAuthorized,
      makeMessage:async to=>{
        built=await buildAssistantPresentation(presentation,{sdk,interactiveMode,assertCurrent:assertAuthorized,isCurrent});
        await beforeSend();
        return sdk.generateWAMessageFromContent(to,built.content,{userJid:sdk.userJid,messageId:sdk.generateMessageIDV2(sdk.userJid)});
      },
      onPrepared:message=>{presentationStore.prepared(presentation,recipient,message);onPrepared(message);diagnostic('prepared',message.key.id);},
      relay:(to,body,options)=>relay(to,body,{...options,...built?.relayOptions})});
    presentationStore.accepted(recipient,sent.key.id);
    diagnostic('accepted',sent.key.id);
    const acknowledgePresentation=()=>presentationStore.acknowledge(recipient,sent.key.id,acknowledge,isCurrent,category=>diagnostic(category,sent.key.id));
    // A managed SQL send slot must be finished from the durable accepted
    // receipt before its own presentation can pass the independent ACK guard.
    // This private callback never enters the protocol, ledger or public DTO.
    if(deferPresentationAck===true)return {...sent,acknowledgePresentation};
    // A missing ACK can be reconciled by a later selection; it never causes
    // another WhatsApp send or a change to the already accepted protocol.
    await acknowledgePresentation();
    return sent;
  }finally{built?.dispose();}
}
function quotedText(message){
  let content=message?.message;
  for(let i=0;i<4;i++){const next=content?.ephemeralMessage?.message||content?.viewOnceMessage?.message||content?.viewOnceMessageV2?.message;if(!next)break;content=next;}
  return {text:extractText(content),quoted:content?.extendedTextMessage?.contextInfo?.stanzaId};
}
export function assistantSelectionCandidate(message){
  let content=message?.message;
  for(let i=0;i<4;i++){
    if(content?.viewOnceMessage||content?.viewOnceMessageV2||content?.viewOnceMessageV2Extension)return null;
    if(!content?.ephemeralMessage?.message)break;content=content.ephemeralMessage.message;
  }
  const normalized=normalizeChatbotInput(message);
  return normalized?.origin==='contact'&&assistantSelectionId(normalized.selection_id)?
    assistantSelection({id:normalized.selection_id,quoted_message_id:normalized.quoted_message_id}):null;
}
/** Same-session encrypted map, separate from subscription menus. A prepared
 * message is never promoted to accepted by an incoming quote or a restart. */
export function createAssistantPresentationStore({directory,secret,scope,now=Date.now,ackTimeoutMs=5000}={}){
  if(!Number.isSafeInteger(ackTimeoutMs)||ackTimeoutMs<1||ackTimeoutMs>5000)throw invalid();
  const namespace=createHmac('sha256',secret).update('orion/assistant-presentation/v1\0'+JSON.stringify(scope)).digest('hex');
  const store=makeEncryptedRetryStore({directory:path.join(directory,namespace),secret,now,ttlMs:15*60*1000,max:256,maxPayloadBytes:8192,maxTotalBytes:2*1024*1024});
  const key=(recipient,id)=>({id,remoteJid:recipient+'@s.whatsapp.net',fromMe:true});
  function record(recipient,id){return phone(recipient)&&provider(id)?store.get(key(recipient,id)):null;}
  function save(recipient,id,value){if(store.save(key(recipient,id),value)!==true)throw Error('assistant_presentation_not_persisted');}
  const acknowledgements=new Map();
  async function acknowledge(recipient,id,ack,isCurrent=()=>true,onDiagnostic){
    const old=record(recipient,id);
    if(!old?.accepted){observe(onDiagnostic,'ack_map_unaccepted');return false;}
    if(typeof ack!=='function'){observe(onDiagnostic,'ack_unavailable');return false;}
    if(!isCurrent()){observe(onDiagnostic,'ack_stale');return false;}
    if(old.acknowledged===true){observe(onDiagnostic,'ack_cached');return true;}
    const operation=recipient+':'+id;
    if(acknowledgements.has(operation)){observe(onDiagnostic,'ack_joined');return acknowledgements.get(operation);}
    const task=(async()=>{for(let i=0;i<2;i++){
      if(!isCurrent()){observe(onDiagnostic,'ack_stale');return false;}
      let timer;
      try{observe(onDiagnostic,'ack_attempt');const result=await Promise.race([Promise.resolve().then(()=>ack({phone:recipient,presentation_id:old.presentation_id,provider_id:id})),
          new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('assistant_ack_timeout')),ackTimeoutMs);})]);
        if(!isCurrent()){observe(onDiagnostic,'ack_stale');return false;}
        if(result?.acknowledged!==true){observe(onDiagnostic,'ack_unconfirmed');return false;}
        const fresh=record(recipient,id);
        if(!fresh?.accepted||fresh.presentation_id!==old.presentation_id){observe(onDiagnostic,'ack_map_changed');return false;}
        save(recipient,id,{...fresh,acknowledged:true});observe(onDiagnostic,'ack_confirmed');return true;
      }catch(error){observe(onDiagnostic,ackFailure(error));if(!isCurrent())return false;}finally{clearTimeout(timer);}
    }return false;})();
    acknowledgements.set(operation,task);try{return await task;}finally{acknowledgements.delete(operation);}
  }
  return {
    prepared(presentation,recipient,message){const p=validateAssistantPresentation(presentation);
      if(!phone(recipient)||!provider(message?.key?.id)||message.key.fromMe!==true)throw invalid();
      const old=record(recipient,message.key.id);
      if(old){if(old.presentation_id!==p.id||JSON.stringify(old.choices)!==JSON.stringify(p.selectionIds))throw invalid();return;}
      save(recipient,message.key.id,{presentation_id:p.id,choices:[...p.selectionIds],accepted:false,acknowledged:false});},
    accepted(recipient,id){const old=record(recipient,id);if(!old||!opaque(old.presentation_id))throw invalid();save(recipient,id,{...old,accepted:true});},
    acknowledge,
    resolve(message,recipient,onDiagnostic){
      const rejected=category=>{observe(onDiagnostic,category);return null;};
      if(message?.key?.fromMe!==false||!phone(recipient))return rejected('sender_unverified');
      let candidate=assistantSelectionCandidate(message);
      if(!candidate){const {text,quoted}=quotedText(message);if(typeof text!=='string'||!/^(?:[1-9]|10)$/.test(text.trim())||!provider(quoted))return rejected('selection_unrecognized');
        const old=record(recipient,quoted),id=old?.choices?.[Number(text.trim())-1];
        if(!assistantSelectionId(id))return rejected(old?'choice_mismatch':'map_missing');candidate={id,quoted_message_id:quoted};}
      const old=record(recipient,candidate.quoted_message_id);
      if(!old)return rejected('map_missing');
      if(old.accepted!==true)return rejected('map_unaccepted');
      if(!old.choices?.includes(candidate.id))return rejected('choice_mismatch');
      observe(onDiagnostic,'matched');return candidate;
    }
  };
}
