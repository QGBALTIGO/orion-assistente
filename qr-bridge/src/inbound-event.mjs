/** Adapter metadata, never model instructions. No media download or side effect. */
import {createHash} from 'node:crypto';
const id=value=>typeof value==='string'&&/^[a-zA-Z0-9._:-]{1,160}$/.test(value)?value:undefined;
export function normalizeInbound(message){
  let source=message?.message||{};
  source=source.ephemeralMessage?.message||source;
  if(source.viewOnceMessage||source.viewOnceMessageV2||source.viewOnceMessageV2Extension)
    return {text:'',event:{version:1,kind:'view_once'},special:true};
  let kind='unknown',text='',context;
  if(source.editedMessage)return {text:'',event:{version:1,kind:'edited'},special:true};
  if(source.videoMessage?.gifPlayback===true&&!source.videoMessage.caption?.trim())return {text:'',event:{version:1,kind:'sticker'},silent:true,special:false};
  if(source.orionAdapterCall===true)return {text:'',event:{version:1,kind:'call'},special:true};
  if(typeof source.conversation==='string'){kind='text';text=source.conversation;}
  else if(source.extendedTextMessage){kind='text';text=source.extendedTextMessage.text||'';context=source.extendedTextMessage.contextInfo;}
  else for(const [field,value] of [['audioMessage','audio'],['imageMessage','image'],['videoMessage','video'],['documentMessage','document'],['stickerMessage','sticker'],['reactionMessage','reaction'],['locationMessage','location'],['contactMessage','contacts'],['contactsArrayMessage','contacts'],['pollCreationMessage','poll']]){
    if(source[field]){kind=value;text=source[field].caption||'';context=source[field].contextInfo;break;}
  }
  const protocol=source.protocolMessage;
  if(protocol?.editedMessage){kind='edited';text='';}
  else if(protocol?.type===0&&protocol.key){kind='deleted';text='';}
  const target=id(protocol?.key?.id),reply=id(context?.stanzaId);
  const invalid=typeof text!=='string'||text.includes('\0')||/[\uD800-\uDFFF]/u.test(text.replace(/[\u{10000}-\u{10FFFF}]/gu,''));
  const oversized=typeof text==='string'&&text.length>1500;
  const control=oversized&&!invalid?safetyControl(text):null;
  const event={version:1,kind,...(target?{target}:{}),...(reply?{reply_to:reply}:{}),...(oversized?{oversized:true}:{}),...(invalid?{invalid:true}:{}),...(control?{control}:{})};
  return {event,text:invalid||oversized?'':text.trim(),silent:['sticker','reaction'].includes(kind),
    special:Boolean(invalid||oversized||reply||['edited','deleted','image','video','document'].includes(kind)||!text&&kind!=='audio')};
}
export function safetyControl(text){
  if(typeof text!=='string')return null;
  const value=text.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase();let choice=null;
  const clauses=value.split(/[.;!?\n]|\b(?:mas|na verdade|quer dizer)\b/);
  for(const part of clauses){const clause=part.trim().replace(/^[ ,]+|[ ,]+$/g,'');
    if(/\bnao\s+(?:quero\s+)?(?:parar|interromper|sair|cancelar|cancele|cancela|tirar|retirar)\b/.test(clause)||/\bquero receber\b/.test(clause)){choice=null;continue;}
    if(/\b(?:nao quero|nao (?:me )?(?:mande|manda|envie|envia)|parar|pare|para de (?:mandar|enviar)|cancela|cancelar|retira|tirar|tira)\b/.test(clause)){
      if(clause.includes('aniversario'))choice='birthday';
      else if(/\b(?:promocao|promocoes|promocional|promocionais|marketing|publicidade|lista)\b/.test(clause))choice='marketing';
      else if(/\b(?:tudo|nada|mensagens?|contato)\b/.test(clause))choice='all';
    }
    if(['parar','sair'].includes(clause))choice='all';
    if(/\bso (?:os? )?lembretes?\b/.test(clause))choice='marketing';
  }
  if(choice)return choice;
  const last=clauses.filter(c=>c.trim()).at(-1)?.trim()||'';
  if(/^(?:quero|preciso|prefiro) (?:falar com (?:um |uma |o |a )?)?(?:humano|atendente|pessoa)(?: de verdade)?$/.test(last))return 'human';
  return null;
}
export function priorityText(text){return safetyControl(text)!==null||typeof text==='string'&&/\b(?:humano|atendente|pessoa|urg[eê]ncia)\b/i.test(text);}
export function queueControl(queue,item){
  const choice=entry=>entry[1].event.control||safetyControl(entry[1].text);
  const incoming=choice(item),same=queue.findIndex(entry=>incoming==='human'?choice(entry)==='human':incoming&&choice(entry)&&choice(entry)!=='human');
  if(same>=0){const rank={birthday:1,marketing:2,all:3,human:4};if(rank[incoming]>=rank[choice(queue[same])])queue[same]=item;return;}
  if(queue.length<2){queue.push(item);return;}
  const disposable=queue.findIndex(entry=>!choice(entry));if(disposable>=0)queue[disposable]=item;
}
export const controlText=event=>({birthday:'não quero mensagens de aniversário',marketing:'não quero promoções',all:'parar mensagens',human:'quero falar com atendente'})[event?.control]||'';
export function updateEvent(key,update,now=Date.now){
  const target=id(key?.id);if(!target||key?.fromMe||!update)return null;
  // Baileys v6.7.24 process-message.ts emits editedMessage.message or a null
  // message with WAMessageStubType.REVOKE=1. Neither is ordinary new text.
  if(update.message===null&&update.messageStubType===1)update={...update,message:{protocolMessage:{type:0,key}}};
  else if(update.message?.editedMessage)update={...update,message:{protocolMessage:{editedMessage:update.message.editedMessage.message,key}}};
  if(!update.message)return null;
  const normalized=normalizeInbound({message:update.message});
  if(!['edited','deleted'].includes(normalized.event.kind))return null;
  const digest=createHash('sha256').update(JSON.stringify([target,update.message])).digest('hex').slice(0,24);
  return {key:{...key,id:`${normalized.event.kind}:${digest}`},messageTimestamp:Math.floor(now()/1000),
    message:{protocolMessage:{...update.message.protocolMessage,key:{...key,id:target}}}};
}
export function callEvent(call,now=Date.now){
  if(call?.status!=='offer'||call.isGroup===true||typeof call.from!=='string'||!/^\d{8,20}(?::\d+)?@s\.whatsapp\.net$/.test(call.from)||typeof call.id!=='string'||!call.id)return null;
  const digest=createHash('sha256').update(call.from+':'+call.id).digest('hex').slice(0,32);
  return {key:{id:'call:'+digest,fromMe:false,remoteJid:call.from},messageTimestamp:Math.floor(now()/1000),message:{orionAdapterCall:true}};
}
