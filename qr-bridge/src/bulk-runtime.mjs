/** Executes only the bulk part admitted by the private SQL broker.
 * Immediate batches, audiences and authorization stay on the server. This adapter
 * cannot expand groups, choose recipients, restart a bulk or retry a send. */
import {channelId,digest} from './channel-storage.mjs';
import {messageFailureCode,safeMessageFailureCode} from './device-message-failure.mjs';

const kinds=new Set(['text','image','sticker','video','ptv','audio','document','location','url_button']);
const revision=value=>Number.isSafeInteger(value)&&value>=1;
const sha=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const phone=value=>typeof value==='string'&&/^[1-9]\d{7,14}$/.test(value);
const provider=value=>typeof value==='string'&&/^[a-zA-Z0-9._:-]{1,160}$/.test(value);
const scope=expected=>({device_id:expected.id,generation:expected.generation,settings_revision:expected.settings_revision});
const fail=code=>Object.assign(Error(code),{code});

export const isBulkEffect=item=>item?.type==='bulk_action'&&channelId(item.operation_id)&&kinds.has(item.action_type);

function validPermit(value,expected,item){
  return value?.authorized===true&&value.operation_id===item.operation_id&&value.type==='bulk_action'&&
    value.purpose==='bulk_action'&&value.device_id===expected.id&&value.generation===expected.generation&&
    value.settings_revision===expected.settings_revision&&channelId(value.batch_id)&&channelId(value.recipient_id)&&
    sha(value.run_version)&&Number.isSafeInteger(value.part_index)&&value.part_index>=0&&value.part_index<=2&&
    phone(value.payload?.phone)&&value.payload?.action?.type===item.action_type&&kinds.has(value.payload.action.type)&&
    value.chatbot_revision===undefined&&value.rule_id===undefined&&value.diagnostic_request_id===undefined&&
    value.recipient_mode===undefined&&value.self_phone===undefined&&value.campaign_id===undefined&&
    value.schedule_id===undefined&&value.run_id===undefined&&value.bulk_id===undefined;
}

function fingerprint(value){
  return digest(JSON.stringify([value.type,value.device_id,value.generation,value.settings_revision,
    value.batch_id,value.recipient_id,value.run_version,value.part_index,value.payload]));
}

export function createBulkRuntime(context,{ledger,capacity,sendChatbot,currentDevice,guardDevice,
  onAccepted=()=>{},onEffects=async()=>{},now=Date.now}={}){
  const inflight=new Set(),controllers=new Set();let closed=false;
  const current=expected=>!closed&&currentDevice(expected);
  const guard=async expected=>{await guardDevice(expected);if(!current(expected))throw fail('BULK_REVOKED');};
  const recordScope=(record,expected)=>record?.type==='bulk_action'&&record.device_id===expected.id&&
    record.generation===expected.generation&&revision(record.settings_revision)&&channelId(record.batch_id)&&
    channelId(record.recipient_id)&&sha(record.run_version);

  async function finish(expected,item,state,extra={}){
    try{
      const result=await context.deviceEffectFinish({...scope(expected),operation_id:item.operation_id,state,...extra});
      if(current(expected)&&Array.isArray(result?.effects)&&result.effects.length<=32)await onEffects(result.effects,{expected});
    }catch{/* The encrypted receipt survives a lost finish response. No resend. */}
  }

  async function recover(expected,item,record){
    if(!recordScope(record,expected)||!record.accepted&&record.state!=='refused')return;
    await guard(expected);
    await finish({...expected,settings_revision:record.settings_revision},item,record.accepted?'accepted':'not_sent',{
      reconcile:true,...(record.provider_id?{provider_id:record.provider_id}:{}),
      ...(!record.accepted&&safeMessageFailureCode(record.error_code)?{error_code:record.error_code}:{})});
  }

  async function execute(item,{expected}={}){
    if(!isBulkEffect(item)||!current(expected)||inflight.has(item.operation_id))return;
    const id=item.operation_id,abort=new AbortController(),entry={expected,abort};
    inflight.add(id);controllers.add(entry);
    let permit,reserved,attempted=false;
    try{
      await guard(expected);
      const old=ledger.get(id);
      if(old){await recover(expected,item,old);return;}
      const due=item.due_at==null?now():Date.parse(item.due_at);
      if(!Number.isFinite(due)||due>now())return;
      // Backpressure precedes claim so waiting alone cannot become an attempt.
      await capacity.acquire({wait:true,isCurrent:()=>current(expected)&&!abort.signal.aborted});
      await guard(expected);
      permit=await context.deviceEffectAuthorize({...scope(expected),operation_id:id});
      await guard(expected);
      if(permit?.authorized!==true)return;
      if(!validPermit(permit,expected,item))throw fail('BULK_EFFECT_INVALID');
      const bound=fingerprint(permit);
      const recheck=async()=>{
        await guard(expected);
        const fresh=await context.deviceEffectAuthorize({...scope(expected),operation_id:id,recheck:true});
        await guard(expected);
        if(!validPermit(fresh,expected,item)||fingerprint(fresh)!==bound)throw fail('BULK_REVOKED');
      };
      reserved=ledger.reserve(id,bound);
      if(!reserved.fresh){await recover(expected,item,reserved.record);return;}
      reserved.record={...reserved.record,...scope(expected),type:'bulk_action',batch_id:permit.batch_id,
        recipient_id:permit.recipient_id,run_version:permit.run_version,part_index:permit.part_index};
      ledger.save(id,reserved.record);
      if(typeof sendChatbot!=='function')throw fail('BULK_TRANSPORT_UNAVAILABLE');
      const active=()=>current(expected)&&!abort.signal.aborted;
      await recheck();
      const result=await sendChatbot(permit.payload.phone,permit.payload.action,{signal:abort.signal,isCurrent:active,
        assertAuthorized:recheck,onRelayAttempt:()=>{attempted=true;},
        loadAsset:(asset,{variant,signal})=>context.deviceBulkAsset({...scope(expected),operation_id:id,asset,variant,signal})});
      if(!provider(result?.id))throw fail('BULK_SEND_UNKNOWN');
      ledger.save(id,{...reserved.record,state:'accepted',accepted:true,provider_id:result.id});
      onAccepted(result.id,{operation_id:id,phone:permit.payload.phone});
      await finish(expected,item,'accepted',{provider_id:result.id});
    }catch(error){
      if(permit?.authorized===true){
        const old=ledger.get(id);
        if(!old?.accepted){
          const error_code=messageFailureCode(error,{beforeSend:!attempted,current:current(expected),bulk:true});
          if(reserved?.fresh&&!attempted)ledger.save(id,{...reserved.record,state:'refused',accepted:false,delivery:'not_sent',error_code});
          await finish(expected,item,attempted?'uncertain':'not_sent',{error_code});
        }
      }
    }finally{abort.abort();controllers.delete(entry);inflight.delete(id);}
  }

  async function reconcile(items,expected){
    for(const item of items||[]){
      // Receipt descriptors intentionally need no action or message content.
      if(item?.type!=='bulk_action'||!channelId(item.operation_id)||item.device_id!==expected.id||
          item.generation!==expected.generation||inflight.has(item.operation_id)||!current(expected))continue;
      const record=ledger.get(item.operation_id);if(!record)continue;
      inflight.add(item.operation_id);
      try{await recover(expected,item,record);}catch{/* Only receipt persistence may run again. */}
      finally{inflight.delete(item.operation_id);}
    }
  }

  return {execute,reconcile,pending:()=>inflight.size,
    sync(){for(const item of controllers)if(!current(item.expected))item.abort.abort();},
    close(){closed=true;for(const item of controllers)item.abort.abort();controllers.clear();}};
}
