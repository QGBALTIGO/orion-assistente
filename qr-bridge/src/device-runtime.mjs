/** Tenant/device-only behaviours. The broker owns policy, quotas and durable
 * admission; this adapter can only execute the explicit effect it authorizes.
 * No conversation intake, legacy assistant or appointment router is imported. */
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {digest,privateDirectory,sendLedger,channelId} from './channel-storage.mjs';
import {resolveMessagePhone,normalizeFromJid} from './core.mjs';
import {resolveCallPhone,cleanPictureUrl} from './connected-features.mjs';
import {timestampMs} from './linked-session-health.mjs';
import {makeSendCapacity} from './send-capacity.mjs';
import {deviceAudioMetadata,withDeviceAudio,DEVICE_TEXT_MAX_CHARS} from './device-audio.mjs';
import {postDeviceWebhook} from './device-network.mjs';
import {createChatbotRuntime,isChatbotEffect} from './chatbot-runtime.mjs';
import {normalizeChatbotInput,createOrderedIngress} from './chatbot-input.mjs';
import {knownMessagePreflight,messageFailureCode,safeMessageFailureCode} from './device-message-failure.mjs';
import {diagnosticMessagePermit} from './device-diagnostic-message.mjs';
import {createCampaignRuntime,isCampaignEffect} from './campaign-runtime.mjs';
import {createScheduledRuntime,isScheduledEffect} from './scheduled-runtime.mjs';
import {createBulkRuntime,isBulkEffect} from './bulk-runtime.mjs';

const privateJid=/^[1-9]\d{7,19}(?::\d+)?@(s\.whatsapp\.net|lid)$/;
const phonePattern=/^[1-9]\d{7,14}$/;
const messageId=value=>typeof value==='string'&&/^[a-zA-Z0-9._:-]{1,160}$/.test(value);
const name=value=>typeof value==='string'?Array.from(value.replace(/[\u0000-\u001f\u007f]/g,'').trim()).slice(0,100).join(''):null;
const scope=snapshot=>({device_id:snapshot.id,generation:snapshot.generation,settings_revision:snapshot.settings_revision});
const same=(a,b)=>a&&b&&a.id===b.id&&a.generation===b.generation&&a.settings_revision===b.settings_revision;
const knownEffect=value=>isChatbotEffect(value)||isCampaignEffect(value)||isScheduledEffect(value)||isBulkEffect(value)||(value&&channelId(value.operation_id)&&['send_text','reject_call','transcribe','webhook'].includes(value.type));

export function validDeviceSnapshot(value){
  return !!(value&&typeof value==='object'&&!Array.isArray(value)&&channelId(value.id)&&channelId(value.generation)&&
    Number.isSafeInteger(value.settings_revision)&&value.settings_revision>=1&&value.settings&&typeof value.settings==='object'&&
    !Array.isArray(value.settings)&&value.capabilities&&typeof value.capabilities==='object'&&!Array.isArray(value.capabilities)&&
    (value.chatbot===undefined||value.chatbot===null||Number.isSafeInteger(value.chatbot.revision)&&value.chatbot.revision>=1));
}

export function createDeviceRuntime(context,{client,downloadMediaMessage,logger,sendText,sendChatbot,isGeneratedMessage=()=>false,
  resolveSelection=()=>null,
  now=Date.now,openedAt=now(),schedule=setTimeout,cancel=clearTimeout,
  postWebhook=postDeviceWebhook,downloadOptions={},capacity=makeSendCapacity({now}),pollMs=5000,chatbotOptions={}}={}){
  const initial=context.deviceSnapshot?.();if(!validDeviceSnapshot(initial))throw Error('invalid_device_snapshot');
  const directory=privateDirectory(path.join(context.deviceDirectory,'effects'));
  const ledger=sendLedger(directory,context.secret);
  const seen=new Set(),inflight=new Set(),controllers=new Set(),sent=new Map();
  let closed=false,connected=false,pending=0,audioBusy=false,webhookBusy=false,poll=null,polling=false,audioTail=Promise.resolve(),ingressOrder=0;
  const current=expected=>!closed&&context.isCurrent()&&same(expected,context.deviceSnapshot());
  const guard=async expected=>{if(!current(expected))throw Error('device_operation_superseded');await context.assertCurrent();if(!current(expected))throw Error('device_operation_superseded');};
  const remember=id=>{if(seen.has(id))return false;seen.add(id);if(seen.size>2000)seen.delete(seen.values().next().value);return true;};
  function controller(expected){
    const abort=new AbortController();controllers.add({abort,expected});return abort;
  }
  function releaseController(abort){for(const entry of controllers)if(entry.abort===abort)controllers.delete(entry);}
  function sync(){for(const entry of controllers)if(!current(entry.expected))entry.abort.abort();chatbot.sync();campaigns.sync();schedules.sync();bulk.sync();}
  async function finish(expected,operation_id,state,extra={}){
    try{await context.deviceEffectFinish({...scope(expected),operation_id,state,...extra});}catch{/* The durable grant stays conservative if acknowledgement is lost. */}
  }
  async function event(expected,data,sourceMessage){
    await guard(expected);
    const result=await context.deviceEvent({...scope(expected),...data});
    await guard(expected);
    if(!result||!Array.isArray(result.effects)||result.effects.length>32)throw Error('invalid_device_effects');
    await effects(result.effects,{expected,message:sourceMessage});return result;
  }
  async function effects(items,{expected=context.deviceSnapshot(),message}={}){
    for(const item of items||[]){
      if(!knownEffect(item)||!current(expected))continue;
      await execute(item,{expected,message});
    }
  }
  async function reconcile(items,expected){
    await chatbot.reconcile(items,expected);
    await campaigns.reconcile(items,expected);
    await schedules.reconcile(items,expected);
    await bulk.reconcile(items,expected);
    for(const item of items||[]){
      if(isChatbotEffect(item)||['campaign_action','scheduled_action','bulk_action'].includes(item?.type))continue;
      if(!channelId(item?.operation_id)||item.device_id!==expected.id||item.generation!==expected.generation||
          !Number.isSafeInteger(item.settings_revision)||item.settings_revision<1||!current(expected))continue;
      // A receipt is evidence only. No authorize, capacity grant, download,
      // provider request or socket effect can be reached from this path.
      const record=ledger.get(item.operation_id);if(!record||!record.accepted&&record.state!=='refused')continue;
      await guard(expected);
      await finish({id:item.device_id,generation:item.generation,settings_revision:item.settings_revision},item.operation_id,
        record.accepted?'accepted':'not_sent',{reconcile:true,...(record.provider_id?{provider_id:record.provider_id}:{}),
          ...(!record.accepted&&safeMessageFailureCode(record.error_code)?{error_code:record.error_code}:{})});
    }
  }
  async function execute(item,{expected,message}={}){
    if(isChatbotEffect(item))return chatbot.execute(item,{expected});
    if(isCampaignEffect(item))return campaigns.execute(item,{expected});
    if(isScheduledEffect(item))return schedules.execute(item,{expected});
    if(isBulkEffect(item))return bulk.execute(item,{expected});
    const id=item.operation_id;if(inflight.has(id))return;
    // Pending reconciliation has no original encrypted media message. It may
    // never claim an ASR effect before the live event's own callback consumes it.
    if(item.type==='transcribe'&&!message)return;
    if(item.type==='webhook'&&webhookBusy)return;
    inflight.add(id);if(item.type==='webhook')webhookBusy=true;
    let permit,abort,attempted=false,reserved,releaseAudio;
    if(item.type==='transcribe'){
      const previous=audioTail;audioTail=new Promise(resolve=>{releaseAudio=resolve;});
      await previous;audioBusy=true;
    }
    try{
      await guard(expected);
      // Backpressure happens before claiming the SQL effect, so a waiting
      // process crash does not turn a definitely-unsent part into uncertainty.
      if(item.type==='send_text')await capacity.acquire({wait:true,isCurrent:()=>current(expected)});
      await guard(expected);
      permit=await context.deviceEffectAuthorize({...scope(expected),operation_id:id});
      await guard(expected);
      if(permit?.authorized!==true||permit.operation_id!==id||permit.type!==item.type||
          permit.device_id!==expected.id||permit.generation!==expected.generation||permit.settings_revision!==expected.settings_revision||
          !permit.payload||typeof permit.payload!=='object'||Array.isArray(permit.payload))throw Error('device_effect_refused');
      const data=permit.payload;
      const diagnostic=item.type==='send_text'&&permit.purpose==='diagnostic_test'?diagnosticMessagePermit(permit,expected,id):null;
      if(!diagnostic&&(item.purpose==='diagnostic_test'||permit.diagnostic_request_id!==undefined||permit.confirmed!==undefined||
        permit.recipient_mode!==undefined||permit.self_phone!==undefined))throw Error('diagnostic_message_refused');
      if(item.type!=='webhook'){
        const fingerprint=diagnostic?diagnostic.fingerprint:digest(JSON.stringify([expected.id,expected.generation,item.type,data]));
        reserved=ledger.reserve(id,fingerprint);
        if(!reserved.fresh){
          const old=reserved.record;
          await finish(expected,id,old.accepted?'accepted':old.state==='refused'?'not_sent':'uncertain',
            {...(old.provider_id?{provider_id:old.provider_id}:{}),
              ...(!old.accepted&&safeMessageFailureCode(old.error_code)?{error_code:old.error_code}:{})});return;
        }
      }
      abort=controller(expected);
      if(item.type==='send_text'){
        if(!phonePattern.test(data.phone)||typeof data.text!=='string'||!data.text.trim()||data.text.length>DEVICE_TEXT_MAX_CHARS)throw Error('device_effect_invalid');
        const recheck=async()=>{
          await guard(expected);
          const fresh=await context.deviceEffectAuthorize({...scope(expected),operation_id:id,recheck:true});
          await guard(expected);
          if(diagnosticMessagePermit(fresh,expected,id).fingerprint!==diagnostic.fingerprint)throw Error('diagnostic_message_refused');
        };
        await guard(expected);if(!diagnostic)attempted=true;
        const result=await sendText(data.phone,data.text,{isCurrent:()=>current(expected)&&!abort.signal.aborted,maxTextChars:DEVICE_TEXT_MAX_CHARS,
          ...(diagnostic?{diagnostic,assertAuthorized:recheck,onRelayAttempt:()=>{attempted=true;}}:{})});
        if(!messageId(result?.id))throw Error('device_send_unknown');
        ledger.save(id,{...reserved.record,state:'accepted',accepted:true,provider_id:result.id});
        sent.set(result.id,{operation_id:id,phone:data.phone});if(sent.size>2000)sent.delete(sent.keys().next().value);
        await finish(expected,id,'accepted',{provider_id:result.id});
      }else if(item.type==='reject_call'){
        if(!messageId(data.call_id)||typeof data.call_from!=='string'||!privateJid.test(data.call_from)||typeof client.rejectCall!=='function')throw Error('device_effect_invalid');
        await guard(expected);attempted=true;await client.rejectCall(data.call_id,data.call_from);
        ledger.save(id,{...reserved.record,state:'accepted',accepted:true});await finish(expected,id,'accepted');
      }else if(item.type==='transcribe'){
        if(!message||data.message_id!==message.key?.id||!phonePattern.test(data.phone)||expected.settings.transcription?.enabled!==true||
            expected.capabilities.transcription_available!==true||data.max_seconds>expected.settings.transcription.max_minutes*60||
            data.max_seconds>expected.capabilities.transcription_max_seconds||data.max_bytes>expected.capabilities.transcription_max_bytes)throw Error('device_audio_unavailable');
        const result=await withDeviceAudio(message,{client,downloadMediaMessage,logger,signal:abort.signal,downloadOptions,
          maxBytes:data.max_bytes,maxSeconds:data.max_seconds,assertCurrent:()=>guard(expected)},async payload=>{
          await guard(expected);attempted=true;
          return context.deviceTranscribe({...scope(expected),operation_id:id,phone:data.phone,...payload});
        });
        if(!result||result.truncated!==false||typeof result.text!=='string'||!result.text.trim()||result.text.length>240000||[...result.text].length>120000||
            !Array.isArray(result.effects)||result.effects.length>32)throw Error('device_audio_unavailable');
        await guard(expected);ledger.save(id,{...reserved.record,state:'accepted',accepted:true});
        await finish(expected,id,'accepted');await effects(result.effects,{expected});
        // Only explicit server-derived Chatbot effects can follow ASR.
        // The transcript never enters legacy AI or appointment intake.
      }else if(item.type==='webhook'){
        if(expected.settings.webhook?.enabled!==true||expected.capabilities.webhook!==true||
            typeof data.url!=='string'||typeof data.body!=='object'||!messageId(data.event_id))throw Error('device_webhook_unavailable');
        await guard(expected);attempted=true;
        await postWebhook({url:data.url,payload:data.body,eventId:data.event_id,signal:abort.signal,
          isCurrent:()=>current(expected),assertCurrent:()=>guard(expected)});
        await finish(expected,id,'accepted');
      }
    }catch(error){
      const definitelyUnsent=!attempted||item.type==='send_text'&&knownMessagePreflight(error)!==null;
      const state=definitelyUnsent?'not_sent':'uncertain';
      const error_code=item.type==='send_text'?messageFailureCode(error,{beforeSend:definitelyUnsent,current:current(expected)}):
        !current(expected)?'DEVICE_REVOKED':item.type==='webhook'?'DEVICE_WEBHOOK_FAILED':item.type==='transcribe'?'DEVICE_AUDIO_FAILED':'DEVICE_EFFECT_FAILED';
      if(reserved?.fresh&&definitelyUnsent)ledger.save(id,{...reserved.record,state:'refused',accepted:false,delivery:'not_sent',error_code});
      if(permit?.authorized===true){
        // HTTP delivery is at-least-once and may be retried by its SQL outbox.
        // WhatsApp sends/rejections and paid ASR never retry an ambiguous attempt.
        await finish(expected,id,item.type==='webhook'?'failed':state,{error_code});
      }
    }finally{
      if(abort){abort.abort();releaseController(abort);}inflight.delete(id);
      if(item.type==='transcribe'){audioBusy=false;releaseAudio?.();}if(item.type==='webhook')webhookBusy=false;
    }
  }
  function nextPoll(){
    cancel(poll);if(closed||!connected)return;
    poll=schedule(async()=>{
      if(polling||closed||!connected)return nextPoll();polling=true;
      try{const expected=context.deviceSnapshot();await guard(expected);
        const result=await context.deviceEffectsPending(scope(expected));await guard(expected);
        if(Array.isArray(result?.reconcile)&&result.reconcile.length<=32)await reconcile(result.reconcile,expected);
        if(Array.isArray(result?.effects)&&result.effects.length<=32)await effects(result.effects,{expected});
      }catch{/* Read-only polling does not claim success or restart transport. */}
      finally{polling=false;nextPoll();}
    },pollMs);poll?.unref?.();
  }
  function queue(task){
    if(closed||pending+ingress.pending()>=32)return;pending++;
    void task().catch(()=>{}).finally(()=>{pending--;});
  }
  function receive({messages=[],type}={}){
    if(!['notify','append'].includes(type))return;
    for(const message of messages){
      const key=message?.key,at=timestampMs(message?.messageTimestamp),expected=context.deviceSnapshot();
      if(!current(expected)||![false,true].includes(key?.fromMe)||!messageId(key?.id)||!privateJid.test(key?.remoteJid||'')||
          !at||(type!=='append'&&at<openedAt-1000)||at<now()-300000||at>now()+60000||pending+ingress.pending()>=32)continue;
      const input=normalizeChatbotInput(message,{isGeneratedMessage});
      if(!input||(input.origin==='owner_command'&&!Number.isSafeInteger(expected.chatbot?.revision))||!remember('message:'+key.id))continue;
      // Assigned before the first async PN/LID resolution or ASR request.
      ingressOrder=Math.max(ingressOrder+1,Math.floor(now())*1000);
      ingress.enqueue({message,expected,at,input,order:ingressOrder});
    }
  }
  const chatbot=createChatbotRuntime(context,{ledger,capacity,sendChatbot,currentDevice:current,guardDevice:guard,
    onEffects:effects,now,schedule,cancel,onAccepted:(id,data)=>{sent.set(id,data);if(sent.size>2000)sent.delete(sent.keys().next().value);},...chatbotOptions});
  const campaigns=createCampaignRuntime(context,{ledger,capacity,sendChatbot,currentDevice:current,guardDevice:guard,
    onEffects:effects,now,onAccepted:(id,data)=>{sent.set(id,data);if(sent.size>2000)sent.delete(sent.keys().next().value);}});
  const schedules=createScheduledRuntime(context,{ledger,capacity,sendChatbot,currentDevice:current,guardDevice:guard,
    onEffects:effects,now,onAccepted:(id,data)=>{sent.set(id,data);if(sent.size>2000)sent.delete(sent.keys().next().value);}});
  const bulk=createBulkRuntime(context,{ledger,capacity,sendChatbot,currentDevice:current,guardDevice:guard,
    onEffects:effects,now,onAccepted:(id,data)=>{sent.set(id,data);if(sent.size>2000)sent.delete(sent.keys().next().value);}});
  const ingress=createOrderedIngress({schedule,cancel,isCurrent:item=>current(item.expected),
    resolveIdentity:async({expected,message})=>{await guard(expected);const phone=await resolveMessagePhone(message.key,client);await guard(expected);
      const self=normalizeFromJid(client.user?.id);return phonePattern.test(phone||'')&&self&&phone!==self?phone:null;},
    run:async({expected,message,at,input,order},phone)=>{
      await guard(expected);const audio=input.origin==='contact'?deviceAudioMetadata(message):null;
      const resolved=input.origin==='contact'&&!input.selection_id?resolveSelection(message,phone)||input:input;
      await event(expected,{event_id:'msg:'+digest(message.key.id).slice(0,40),kind:audio?'audio':'text',occurred_at:new Date(at).toISOString(),
        phone,message_id:message.key.id,name:name(message.pushName),...resolved,ingress_order:order,
        ...(Number.isSafeInteger(expected.chatbot?.revision)?{chatbot_revision:expected.chatbot.revision}:{}),...(audio?{audio}:{})},message);
    }});
  function calls(values){
    for(const call of (Array.isArray(values)?values:[]).slice(0,20)){
      const expected=context.deviceSnapshot();
      if(!current(expected)||call?.status!=='offer'||call.isGroup||!messageId(call.id)||!privateJid.test(call.from||'')||
          pending+ingress.pending()>=32||!remember('call:'+call.id+':'+call.from))continue;
      queue(async()=>{
        await guard(expected);const phone=await resolveCallPhone(call,client);await guard(expected);
        const self=normalizeFromJid(client.user?.id),selfLid=client.user?.lid?.replace(/:\d+(?=@)/,'');
        if(!self||phone===self||call.from.replace(/:\d+(?=@)/,'')===selfLid)return;
        await event(expected,{event_id:'call:'+digest(call.id+':'+call.from).slice(0,40),kind:'call',occurred_at:new Date(now()).toISOString(),
          ...(phonePattern.test(phone||'')?{phone}:{}),call_id:call.id,call_from:call.from});
      });
    }
  }
  async function onConnected(){
    connected=true;nextPoll();const expected=context.deviceSnapshot();
    await guard(expected);const phone=normalizeFromJid(client.user?.id);if(!phone)return;
    let avatar_url=null;
    if(typeof client.profilePictureUrl==='function'){
      try{avatar_url=cleanPictureUrl(await client.profilePictureUrl(phone+'@s.whatsapp.net','preview'));}catch{}
    }
    await guard(expected);
    await event(expected,{event_id:'status:'+randomUUID(),kind:'status',occurred_at:new Date(now()).toISOString(),phone,
      name:name(client.user?.name),state:'connected',...(avatar_url?{avatar_url}:{})});
  }
  function receipts(updates){
    for(const item of (Array.isArray(updates)?updates:[]).slice(0,100)){
      const known=sent.get(item.key?.id),ack=Number(item.update?.status);if(!known||ack<3||ack>5)continue;
      const expected=context.deviceSnapshot();queue(()=>event(expected,{event_id:'receipt:'+digest(item.key.id+':'+ack).slice(0,40),kind:'outbound',
        occurred_at:new Date(now()).toISOString(),phone:known.phone,message_id:item.key.id,state:ack>=4?'read':'delivered'}));
    }
  }
  return {receive,calls,receipts,onConnected,sync,effects,
    diagnostics:()=>({pending:pending+ingress.pending(),inflight:inflight.size+chatbot.pending()+campaigns.pending()+schedules.pending()+bulk.pending(),audio_busy:audioBusy}),
    close(){if(closed)return;closed=true;connected=false;cancel(poll);ingress.close();chatbot.close();campaigns.close();schedules.close();bulk.close();for(const entry of controllers)entry.abort.abort();controllers.clear();sent.clear();seen.clear();}};
}
