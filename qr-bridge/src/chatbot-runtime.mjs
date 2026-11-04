/** Runs only server-derived Chatbot effects. It has no matching rules, legacy
 * assistant, arbitrary send endpoint or automatic retry of external effects. */
import {channelId,digest} from './channel-storage.mjs';
import {requestChatbotJSON} from './chatbot-network.mjs';

const phone=value=>typeof value==='string'&&/^[1-9]\d{7,14}$/.test(value);
const providerId=value=>typeof value==='string'&&/^[a-zA-Z0-9._:-]{1,160}$/.test(value);
const revision=value=>Number.isSafeInteger(value)&&value>=1;
const scope=(expected,chatbot_revision)=>({device_id:expected.id,generation:expected.generation,
  settings_revision:expected.settings_revision,chatbot_revision});
export const isChatbotEffect=item=>item?.type==='chatbot_action'&&channelId(item.operation_id)&&revision(item.chatbot_revision);
const types=new Set(['text','list','image','sticker','video','ptv','document','carousel','http']);
const error=code=>Object.assign(Error(code),{code});

export function createChatbotRuntime(context,{ledger,capacity,sendChatbot,
  currentDevice,guardDevice,onAccepted=()=>{},onEffects=async()=>{},now=Date.now,
  schedule=setTimeout,cancel=clearTimeout,httpRequest=requestChatbotJSON,fetchImage}={}){
  const inflight=new Set(),controllers=new Set();let closed=false;
  const current=(expected,rev)=>!closed&&currentDevice(expected)&&context.deviceSnapshot()?.chatbot?.revision===rev;
  const guard=async(expected,rev)=>{await guardDevice(expected);if(!current(expected,rev))throw error('CHATBOT_REVOKED');};
  function sync(){for(const item of controllers)if(!current(item.expected,item.rev))item.abort.abort();}
  async function pause(ms,abort){
    if(ms<=0)return;
    await new Promise((resolve,reject)=>{
      let timer;
      const finish=failure=>{cancel(timer);abort.signal.removeEventListener('abort',stop);failure?reject(failure):resolve();};
      const stop=()=>finish(error('CHATBOT_REVOKED'));
      timer=schedule(()=>finish(),ms);abort.signal.addEventListener('abort',stop,{once:true});
      if(abort.signal.aborted)stop();
    });
  }
  async function finish(expected,item,state,extra={}){
    try{
      const result=await context.deviceEffectFinish({...scope(expected,item.chatbot_revision),operation_id:item.operation_id,state,...extra});
      if(current(expected,item.chatbot_revision)&&Array.isArray(result?.effects)&&result.effects.length<=32)await onEffects(result.effects,{expected});
    }catch{/* Ledger evidence survives a lost finish response. No resend. */}
  }
  const recordScope=(record,expected,item)=>record?.chatbot_revision===item.chatbot_revision&&record.device_id===expected.id&&record.generation===expected.generation;
  async function recover(expected,item,record,{reconcile=false}={}){
    if(!recordScope(record,expected,item))return;
    if(record.state==='http_result'&&record.response_json){
      // This is persistence of bytes already received, never another external
      // request. A fresh generation can never use an old operation's result.
      await guardDevice(expected);
      const result=await context.deviceChatbotHttpResult({...scope(expected,item.chatbot_revision),
        settings_revision:record.settings_revision,operation_id:item.operation_id,response_json:record.response_json,...(reconcile?{reconcile:true}:{})});
      if(result?.recorded!==true||result.operation_id!==item.operation_id)throw error('CHATBOT_RESULT_UNCONFIRMED');
      ledger.save(item.operation_id,{...record,state:'accepted',accepted:true,response_json:undefined});
      if(current(expected,item.chatbot_revision)&&Array.isArray(result?.effects)&&result.effects.length<=32)await onEffects(result.effects,{expected});
      return;
    }
    if(record.accepted||record.state==='refused')await finish({...expected,settings_revision:record.settings_revision},item,
      record.accepted?'accepted':'not_sent',{...(reconcile?{reconcile:true}:{}),...(record.provider_id?{provider_id:record.provider_id}:{})});
  }
  async function execute(item,{expected}={}){
    if(!isChatbotEffect(item)||!types.has(item.action_type)||!current(expected,item.chatbot_revision)||inflight.has(item.operation_id))return;
    const id=item.operation_id,rev=item.chatbot_revision,abort=new AbortController(),entry={expected,rev,abort};
    inflight.add(id);controllers.add(entry);
    let permit,reserved,attempted=false;
    try{
      await guard(expected,rev);
      const old=ledger.get(id);
      if(old){await recover(expected,item,old,{reconcile:true});return;}
      const due=item.due_at==null?null:Date.parse(item.due_at);
      if(due!==null&&!Number.isFinite(due))throw error('CHATBOT_EFFECT_INVALID');
      const wait=due!==null?Math.max(0,due-now()):Number(item.delay_seconds||0)*1000;
      if(!Number.isFinite(wait)||wait<0||wait>30000)return;
      await pause(wait,abort);await guard(expected,rev);
      // The descriptor names only the resolved transport type. Admission is
      // before SQL claim, so queueing alone cannot create an uncertain send.
      if(item.action_type!=='http')await capacity.acquire({wait:true,isCurrent:()=>current(expected,rev)&&!abort.signal.aborted});
      await guard(expected,rev);
      permit=await context.deviceEffectAuthorize({...scope(expected,rev),operation_id:id});
      await guard(expected,rev);
      if(permit?.authorized!==true)return;
      const valid=value=>value?.authorized===true&&value.operation_id===id&&value.type==='chatbot_action'&&
        value.device_id===expected.id&&value.generation===expected.generation&&value.settings_revision===expected.settings_revision&&
        value.chatbot_revision===rev&&channelId(value.rule_id)&&revision(value.rule_revision)&&
        phone(value.payload?.phone)&&value.payload?.action?.type===item.action_type;
      if(!valid(permit))throw error('CHATBOT_EFFECT_INVALID');
      const fingerprint=digest(JSON.stringify([scope(expected,rev),permit.rule_id,permit.rule_revision,permit.payload]));
      const recheck=async()=>{
        await guard(expected,rev);
        const fresh=await context.deviceEffectAuthorize({...scope(expected,rev),operation_id:id,recheck:true});
        await guard(expected,rev);
        if(!valid(fresh)||digest(JSON.stringify([scope(expected,rev),fresh.rule_id,fresh.rule_revision,fresh.payload]))!==fingerprint)throw error('CHATBOT_REVOKED');
      };
      const data=permit.payload;
      reserved=ledger.reserve(id,fingerprint);
      if(!reserved.fresh){await recover(expected,item,reserved.record,{reconcile:true});return;}
      reserved.record={...reserved.record,...scope(expected,rev),type:'chatbot_action'};
      ledger.save(id,reserved.record);
      const active=()=>current(expected,rev)&&!abort.signal.aborted;
      if(data.action.type==='http'){
        await recheck();attempted=true;
        const result=await httpRequest({url:data.action.url,method:data.action.method,data:data.action.data,
          signal:abort.signal,isCurrent:active,assertCurrent:recheck,requestId:id});
        // Record a complete response before asking the API to derive text/TXT.
        // A crash after this point can repeat only that idempotent persistence.
        const record={...reserved.record,state:'http_result',response_json:result.json};ledger.save(id,record);
        await recover(expected,item,record);
      }else{
        if(typeof sendChatbot!=='function')throw error('CHATBOT_TRANSPORT_UNAVAILABLE');
        await recheck();
        const result=await sendChatbot(data.phone,data.action,{signal:abort.signal,isCurrent:active,assertAuthorized:recheck,
          fetchImage,onRelayAttempt:()=>{attempted=true;},
          loadAsset:(asset,{variant,signal})=>context.deviceChatbotAsset({...scope(expected,rev),operation_id:id,asset,variant,signal}),
          prepareCarouselImage:async(card_index,buffer,{signal})=>{
            const prepared=await context.deviceChatbotCarouselImage({...scope(expected,rev),operation_id:id,card_index,buffer,signal});
            return prepared?.asset;
          }});
        if(!providerId(result?.id))throw error('CHATBOT_SEND_UNKNOWN');
        ledger.save(id,{...reserved.record,state:'accepted',accepted:true,provider_id:result.id});
        onAccepted(result.id,{operation_id:id,phone:data.phone});
        await finish(expected,item,'accepted',{provider_id:result.id});
      }
    }catch(failure){
      if(permit?.authorized===true){
        // No provider call is repeated, including a timed-out GET or POST.
        // A durable HTTP response remains available for result reconciliation.
        const old=ledger.get(id);
        if(old?.state!=='http_result'&&!old?.accepted){
          if(reserved?.fresh&&!attempted)ledger.save(id,{...reserved.record,state:'refused',accepted:false,delivery:'not_sent'});
          await finish(expected,item,attempted?'uncertain':'not_sent',{error_code:current(expected,rev)?'CHATBOT_EFFECT_FAILED':'CHATBOT_REVOKED'});
        }
      }
    }finally{abort.abort();controllers.delete(entry);inflight.delete(id);}
  }
  async function reconcile(items,expected){
    for(const item of items||[]){
      if(!isChatbotEffect(item)||item.device_id!==expected.id||item.generation!==expected.generation||inflight.has(item.operation_id))continue;
      const record=ledger.get(item.operation_id);if(!record)continue;
      inflight.add(item.operation_id);
      try{await recover(expected,item,record,{reconcile:true});}catch{/* Only a later receipt reconciliation may run again. */}
      finally{inflight.delete(item.operation_id);}
    }
  }
  return {execute,reconcile,sync,pending:()=>inflight.size,close(){closed=true;for(const item of controllers)item.abort.abort();controllers.clear();}};
}
