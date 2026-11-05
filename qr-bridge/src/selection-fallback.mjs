/** Quoted numeric replies to our numbered menus. No unquoted guessing or
 * authority from caller-supplied option IDs. Backend revalidates every token. */
import {extractText} from './core.mjs';
import {chatbotSelectionId} from './chatbot-message.mjs';
const providerId=value=>typeof value==='string'&&/^[a-zA-Z0-9._:-]{1,160}$/.test(value);
const phone=value=>typeof value==='string'&&/^[1-9]\d{7,14}$/.test(value);
export function saveFallbackSelection(store,message,recipient,ids){
  if(!phone(recipient)||!providerId(message?.key?.id)||message.key.fromMe!==true||
      !Array.isArray(ids)||ids.length<1||ids.length>10||new Set(ids).size!==ids.length||ids.some(id=>!chatbotSelectionId(id)))
    throw Error('invalid_fallback_selection');
  if(store.save({id:message.key.id,remoteJid:recipient+'@s.whatsapp.net',fromMe:true},{choices:[...ids]})!==true)
    throw Error('fallback_selection_not_persisted');
}
export function resolveFallbackSelection(store,message,recipient){
  if(message?.key?.fromMe!==false||!phone(recipient))return null;
  let content=message.message;
  for(let depth=0;depth<4;depth++){
    const wrapped=content?.ephemeralMessage?.message||content?.viewOnceMessage?.message||content?.viewOnceMessageV2?.message;
    if(!wrapped)break;content=wrapped;
  }
  const body=extractText(content),quoted=content?.extendedTextMessage?.contextInfo?.stanzaId;
  if(typeof body!=='string'||! /^(?:[1-9]|10)$/.test(body.trim())||!providerId(quoted))return null;
  const stored=store.get({id:quoted,remoteJid:recipient+'@s.whatsapp.net',fromMe:true});
  const selected=stored?.choices?.[Number(body.trim())-1];
  if(!Array.isArray(stored?.choices)||!chatbotSelectionId(selected))return null;
  return {origin:'contact',selection_id:selected,quoted_message_id:quoted};
}
