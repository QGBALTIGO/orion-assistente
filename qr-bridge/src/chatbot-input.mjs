/** Only structured provider selections and verified human owner commands.
 * Labels and arbitrary JSON text never become a menu action identifier. */
import {extractText} from './core.mjs';
import {chatbotSelectionId} from './chatbot-message.mjs';

const providerId=value=>typeof value==='string'&&/^[a-zA-Z0-9._:-]{1,160}$/.test(value);
function contentOf(message){
  let content=message;
  for(let depth=0;depth<4;depth++){
    const wrapped=content?.ephemeralMessage?.message||content?.viewOnceMessage?.message||content?.viewOnceMessageV2?.message||content?.documentWithCaptionMessage?.message;
    if(!wrapped)return content;content=wrapped;
  }
  return content;
}

export function normalizeChatbotInput(message,{isGeneratedMessage=()=>false}={}){
  const content=contentOf(message?.message),fromMe=message?.key?.fromMe;
  if(!content||typeof content!=='object'||![true,false].includes(fromMe))return null;
  const raw=extractText(content),body=typeof raw==='string'&&raw.length<=32000?raw:null;
  if(fromMe){
    // The encrypted exact-proto cache exists before relay, including sends
    // whose provider acknowledgement has not arrived or was lost on restart.
    try{if(isGeneratedMessage(message.key)||!body?.startsWith('!')||!body.slice(1).trim())return null;}catch{return null;}
    return {origin:'owner_command',text:body};
  }
  const selection=content.listResponseMessage||content.buttonsResponseMessage||content.templateButtonReplyMessage||content.interactiveResponseMessage;
  if(selection){
    let id=selection.singleSelectReply?.selectedRowId??selection.selectedButtonId??selection.selectedId;
    if(selection.nativeFlowResponseMessage){
      const native=selection.nativeFlowResponseMessage;
      if(typeof native.paramsJson!=='string'||native.paramsJson.length>4096||!['quick_reply','single_select'].includes(native.name))return null;
      let params;try{params=JSON.parse(native.paramsJson);}catch{return null;}
      if(!params||Array.isArray(params)||typeof params!=='object')return null;
      id=params.id;
    }
    const quoted=selection.contextInfo?.stanzaId;
    if(!chatbotSelectionId(id)||!providerId(quoted))return null;
    return {origin:'contact',selection_id:id,quoted_message_id:quoted};
  }
  return {origin:'contact',...(body!==null?{text:body}:{})};
}

/** Arrival is admitted synchronously. Identity resolution keeps that arrival
 * order, then each resolved contact has its own queue through ASR and effects.
 * Other contacts do not wait for a slow transcription or configured delay. */
export function createOrderedIngress({resolveIdentity,run,isCurrent=()=>true,limit=32,
  lookupTimeoutMs=3000,schedule=setTimeout,cancel=clearTimeout}={}){
  const contacts=new Map();let identityTail=Promise.resolve(),pending=0,closed=false;
  async function lookup(input){
    if(closed||!isCurrent(input))return null;
    let timer;
    try{return await Promise.race([Promise.resolve().then(()=>resolveIdentity(input)),
      new Promise(resolve=>{timer=schedule(()=>resolve(null),lookupTimeoutMs);timer?.unref?.();})]);}
    finally{cancel(timer);}
  }
  function enqueue(input){
    if(closed||pending>=limit||!isCurrent(input))return false;pending++;
    const identity=identityTail.then(()=>lookup(input)).catch(()=>null);
    identityTail=identity.then(()=>{});
    void identity.then(contact=>{
      if(!contact||closed||!isCurrent(input))return;
      const previous=contacts.get(contact)||Promise.resolve();
      const task=previous.catch(()=>{}).then(()=>{if(!closed&&isCurrent(input))return run(input,contact);});
      contacts.set(contact,task);
      void task.catch(()=>{}).finally(()=>{if(contacts.get(contact)===task)contacts.delete(contact);});
      return task;
    }).catch(()=>{}).finally(()=>{pending--;});
    return true;
  }
  return {enqueue,pending:()=>pending,close(){closed=true;contacts.clear();}};
}
