/** Explicit one-participant PRIMARY canary. No business/AI operation, generic
 * admin send endpoint, new socket, auth migration, or uncertain-send replay. */
import fs from 'node:fs';
import path from 'node:path';
import {randomUUID,createHmac} from 'node:crypto';
import {encryptedChannelStore,privateDirectory,channelId,digest} from './channel-storage.mjs';
import {normalizeFromJid} from './core.mjs';
import {timestampMs} from './linked-session-health.mjs';
import {normalizeChatbotInput} from './chatbot-input.mjs';

const commands=new Map([['ORIONTESTE LISTA','list'],['ORIONTESTE BOTAO','url_button'],['ORIONTESTE AUDIO','audio']]);
const prefix='orion:test:',providerId=value=>typeof value==='string'&&/^[a-zA-Z0-9._:-]{1,160}$/.test(value);
const unknown=()=>Error('native_test_unavailable');
export const NATIVE_TEST_AUDIO=Object.freeze({id:'19475505-2fce-4bd1-a017-0057649acb92',kind:'audio',mime:'audio/mpeg',
  size:16704,sha256:'04a38cec1b5110832117985ae2f61309ff9818db4f3350407fa206afedf23f17',duration_ms:2088,
  width:null,height:null,thumbnail:null,file_name:'teste-orion-2s.mp3'});
export async function loadNativeTestAsset(asset,{variant}={}){
  if(variant!=='original'||JSON.stringify(asset)!==JSON.stringify(NATIVE_TEST_AUDIO))throw unknown();
  return fs.readFileSync(new URL('./fixtures/native-test-tone-2s.mp3',import.meta.url));
}
function contentOf(message){
  let content=message?.message,invalid=false;for(let i=0;i<4;i++){
    if(content?.ephemeralMessage?.message){content=content.ephemeralMessage.message;continue;}
    const nested=content?.viewOnceMessage?.message||content?.viewOnceMessageV2?.message||content?.viewOnceMessageV2Extension?.message||content?.documentWithCaptionMessage?.message||
      content?.editedMessage?.message||content?.protocolMessage?.editedMessage;
    if(!nested)break;invalid=true;content=nested;
  }return {content,invalid};
}
function textKind(text){
  if(typeof text!=='string')return null;const value=text.trim().toUpperCase();
  return commands.get(value)||(/^ORIONTESTE(?:\s|:)/.test(value)?'invalid':text.includes(prefix)?'selection':null);
}
function classification(message){
  const {content,invalid}=contentOf(message),text=typeof content?.conversation==='string'?content.conversation:content?.extendedTextMessage?.text;
  const kind=textKind(text);
  if(kind)return invalid||['imageMessage','videoMessage','audioMessage','documentMessage','protocolMessage','editedMessage'].some(key=>content?.[key])?'invalid':kind;
  const caption=content?.imageMessage?.caption??content?.videoMessage?.caption??content?.documentMessage?.caption;
  if(textKind(caption))return 'invalid';
  const value=content?.listResponseMessage||content?.buttonsResponseMessage||content?.templateButtonReplyMessage||content?.interactiveResponseMessage;
  const id=value?.singleSelectReply?.selectedRowId??value?.selectedButtonId??value?.selectedId;
  if(typeof id==='string'&&id.startsWith(prefix)||typeof value?.nativeFlowResponseMessage?.paramsJson==='string'&&
      value.nativeFlowResponseMessage.paramsJson.includes(prefix))return invalid?'invalid':'selection';
  return null;
}
function actionFor(kind,choices){
  if(kind==='list')return {type:'list',title:'TESTE ORION',description:'Serviços fictícios. Nenhum horário será reservado.',
    footer:'Somente teste autorizado',button_text:'Ver serviços',sections:[{title:'Barbeiro fictício',rows:choices.map(({id,slot})=>
      ({id,title:slot==='cut'?'Corte de teste':'Barba de teste',description:'Simulação sem agendamento'}))}]};
  if(kind==='url_button')return {type:'url_button',text:'TESTE ORION — botão de acesso. Nenhum cadastro ou agendamento será criado.',
    footer:'Somente teste autorizado',button_text:'Acessar meu sistema',url:'https://orionagenda.pro/'};
  if(kind==='audio')return {type:'audio',asset:{...NATIVE_TEST_AUDIO}};
  throw unknown();
}

export function createNativeTestCanary({directory,secret,env=process.env,now=Date.now,getScope,isCurrent,
  assertCurrent,allowedSender,resolvePhone,isBusy=()=>false,isReady=()=>true,send,instance=randomUUID(),schedule=setTimeout,cancel=clearTimeout}={}){
  if(typeof directory!=='string'||typeof secret!=='string'||secret.length<32||!channelId(instance)||
      [getScope,isCurrent,assertCurrent,allowedSender,resolvePhone,isReady,send].some(fn=>typeof fn!=='function'))throw unknown();
  let revision=0,store=null,state=null,storeIdentity=null,fault=false,observerFull=false;
  const openedAt=now(),observed=new Map(),aliases=new Set();
  const participant=()=>typeof env.QR_NATIVE_TEST_PHONE==='string'&&/^\+[1-9][0-9]{7,14}$/.test(env.QR_NATIVE_TEST_PHONE)?env.QR_NATIVE_TEST_PHONE.slice(1):null;
  const enabled=()=>env.QR_NATIVE_TEST_ENABLED==='1'&&!!participant();
  function scope(){
    const value=getScope();
    if(!value||![value.tenant_id,value.channel_id,value.owner].every(channelId)||!Number.isSafeInteger(value.fence)||value.fence<1||
      !Number.isSafeInteger(value.native_generation)||value.native_generation<1||typeof value.session_digest!=='string'||!/^[a-f0-9]{64}$/.test(value.session_digest))throw unknown();
    return {tenant_id:value.tenant_id,channel_id:value.channel_id,session_digest:value.session_digest,owner:value.owner,fence:value.fence,
      native_generation:value.native_generation,process_instance:instance,test_participant:participant(),
      auth_epoch:createHmac('sha256',secret).update('orion-native-test-auth-v1\0'+directory+'\0'+value.session_digest).digest('hex')};
  }
  function authority(expected,ticket,deadline){
    try{return now()<deadline&&!fault&&!observerFull&&enabled()&&isCurrent()===true&&isReady()===true&&ticket===revision&&allowedSender(participant())===true&&JSON.stringify(scope())===JSON.stringify(expected);}catch{return false;}
  }
  async function guard(expected,ticket,deadline){if(!authority(expected,ticket,deadline))throw unknown();await assertCurrent();if(!authority(expected,ticket,deadline))throw unknown();}
  function ensure(expected){
    const identity=JSON.stringify([expected.tenant_id,expected.channel_id,expected.session_digest,expected.auth_epoch]);
    if(store){if(storeIdentity!==identity)throw unknown();return;}
    storeIdentity=identity;
    try{
      store=encryptedChannelStore({directory:privateDirectory(path.join(directory,'native-test',digest(identity))),secret,isCurrent:()=>isCurrent()===true});
      state=store.get('ledger')||{v:1,operations:{},menus:{}};
      if(state.v!==1||!state.operations||Array.isArray(state.operations)||typeof state.operations!=='object'||
        !state.menus||Array.isArray(state.menus)||typeof state.menus!=='object'||Object.keys(state.operations).length>64||Object.keys(state.menus).length>64)throw unknown();
    }catch{fault=true;throw unknown();}
  }
  function commit(change){
    if(fault||!state)throw unknown();const next=structuredClone(state);change(next);
    try{store.set('ledger',next);state=next;}catch{fault=true;throw unknown();}
  }
  function observe(message,{type,replay=false}={}){
    if(!enabled()||replay||type!=='notify'||message?.key?.fromMe!==false||!providerId(message.key.id))return revision;
    const remote=message.key.remoteJid;
    if(typeof remote!=='string'||!/^\d{8,20}(?::\d+)?@(s\.whatsapp\.net|lid)$/.test(remote))return revision;
    const original=observed.get(message.key.id);if(original)return original.revision;
    const at=timestampMs(message.messageTimestamp);if(!at||at<openedAt-1000||at<now()-300000||at>now()+60000)return revision;
    const direct=normalizeFromJid(remote)||normalizeFromJid(message.key.remoteJidAlt)||normalizeFromJid(message.key.senderPn)||normalizeFromJid(message.key.participantPn);
    if(direct&&direct!==participant())return revision;
    // An as-yet-unmapped LID conservatively cancels ONLY this test's pending
    // effect. Other business/AI conversations are never changed or cancelled.
    if(direct===participant()||aliases.has(remote)||!direct&&remote.endsWith('@lid')){
      // Retain original tickets/timestamps throughout this native generation.
      // Evicting an unclaimed/busy command could revive it on a late duplicate.
      // Capacity therefore disables ONLY this diagnostic, never business work.
      if(observed.size>=256){observerFull=true;revision++;return revision;}
      revision++;observed.set(message.key.id,{revision,at});
    }return revision;
  }
  async function handle({message,type,replay=false,ticket=revision}={}){
    const kind=classification(message);if(!kind)return {handled:false};
    const result=value=>({handled:true,kind,state:value});
    if(kind==='invalid')return result('ignored');
    if(!enabled())return result('disabled');
    const original=observed.get(message?.key?.id),at=original?.at??timestampMs(message?.messageTimestamp),remote=message?.key?.remoteJid;
    ticket=original?.revision??ticket;
    if(replay||type!=='notify'||message?.key?.fromMe!==false||!providerId(message.key.id)||typeof remote!=='string'||
      !/^\d{8,20}(?::\d+)?@(s\.whatsapp\.net|lid)$/.test(remote)||!at||at<openedAt-1000||at<now()-300000||at>now()+60000)return result('ignored');
    if(observerFull)return result('capacity_full');
    let expected,key,deadline=at+300000;
    try{
      expected=scope();await guard(expected,ticket,deadline);
      let timer,phone;
      try{phone=await Promise.race([Promise.resolve().then(()=>resolvePhone(message.key)),
        new Promise((_,reject)=>{timer=schedule(()=>reject(unknown()),3000);timer?.unref?.();})]);}finally{cancel(timer);}
      await guard(expected,ticket,deadline);
      if(phone!==participant()||allowedSender(phone)!==true)return result('denied');
      aliases.add(remote);if(aliases.size>8)aliases.delete(aliases.values().next().value);
      if(isBusy()!==false)return result('assistant_busy');
      ensure(expected);
      key=digest(JSON.stringify([phone,message.key.id]));
      if(state.operations[key])return result('duplicate');
      if(Object.keys(state.operations).length>=64)return result('capacity_full');
      let action,menuKey=null;
      if(kind==='selection'){
        const selection=normalizeChatbotInput(message),quoted=selection?.quoted_message_id;
        if(!selection?.selection_id?.startsWith(prefix)||!providerId(quoted))return result('selection_refused');
        menuKey=digest(quoted);const menu=state.menus[menuKey];
        const choice=menu?.choices?.find(value=>value.id===selection.selection_id);
        if(!menu||menu.provider_id!==quoted||menu.phone!==phone||menu.scope!==JSON.stringify(expected)||menu.consumed!==false||menu.accepted!==true||
          !Number.isFinite(menu.expires_at)||menu.expires_at<=now()||!choice||!['cut','beard'].includes(choice.slot))return result('selection_refused');
        deadline=Math.min(deadline,menu.expires_at);
        action={type:'text',text:'TESTE ORION — você selecionou '+(choice.slot==='cut'?'Corte de teste':'Barba de teste')+'. Nenhum agendamento foi criado.'};
      }else action=actionFor(kind,kind==='list'?['cut','beard'].map(slot=>({slot,id:prefix+randomUUID()+':'+slot})):[]);
      const choices=kind==='list'?action.sections[0].rows.map((row,index)=>({id:row.id,slot:index===0?'cut':'beard'})):null;
      commit(next=>{next.operations[key]={kind,at,scope:JSON.stringify(expected),state:'uncertain'};
        if(menuKey)next.menus[menuKey].consumed=true;});
      const sent=await send(phone,action,{isCurrent:()=>authority(expected,ticket,deadline),assertAuthorized:()=>guard(expected,ticket,deadline),
        onPrepared:msg=>{
          if(!authority(expected,ticket,deadline)||!providerId(msg?.key?.id))throw unknown();
          if(kind==='list')commit(next=>{next.menus[digest(msg.key.id)]={provider_id:msg.key.id,phone,scope:JSON.stringify(expected),
            created_at:at,expires_at:at+300000,consumed:false,accepted:false,choices,operation:key};});
        }});
      if(sent?.accepted!==true||!providerId(sent.id))return result('uncertain');
      // This is observation of a completed relay, not another send grant.
      // Persist known acceptance synchronously before a fast selection can
      // supersede the command revision; never overwrite a new owner's state.
      if(isCurrent()!==true||JSON.stringify(scope())!==JSON.stringify(expected))throw unknown();
      commit(next=>{next.operations[key].state='accepted';next.operations[key].provider_id=sent.id;
        if(kind==='list'){const menu=next.menus[digest(sent.id)];if(!menu||menu.operation!==key)throw unknown();menu.accepted=true;}});
      return result('accepted');
    }catch{return result(fault?'storage_unavailable':key?'uncertain':'unavailable');}
  }
  return {observe,recognizes:message=>classification(message)!==null,handle};
}
