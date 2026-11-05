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
  resolveSelection=()=>null,ingressJournal,onIngressError=()=>{},
  now=Date.now,openedAt=now(),schedule=setTimeout,cancel=clearTimeout,
  postWebhook=postDeviceWebhook,downloadOptions={},capacity=makeSendCapacity({now}),pollMs=5000,chatbotOptions={}}={}){
  const initial=context.deviceSnapshot?.();if(!validDeviceSnapshot(initial))throw Error('invalid_device_snapshot');
  if(!ingressJournal||!['append','pending','ack','begin','complete','block','diagnostics'].every(key=>typeof ingressJournal[key]==='function'))throw Error('device_ingress_journal_required');
  const directory=privateDirectory(path.join(context.deviceDirectory,'effects'));
  const ledger=sendLedger(directory,context.secret);
  const seen=new Set(),inflight=new Set(),controllers=new Set(),sent=new Map();
  const incoming=new Set(),replayWaiters=new Set(),admissionWaiters=new Set();let ingressError=null;
  let closed=false,connected=false,pending=0,audioBusy=false,webhookBusy=false,poll=null,polling=false,audioTail=Promise.resolve(),ingressOrder=0;
  const current=expected=>!closed&&context.isCurrent()&&same(expected,context.deviceSnapshot());
  const guard=async expected=>{if(!current(expected))throw Error('device_operation_superseded');await context.assertCurrent();if(!current(expected))throw Error('device_operation_superseded');};
  const ingressBlocked=()=>{const backlog=ingressJournal.diagnostics();return ingressError!==null||backlog.pending>0||backlog.blocked>0;};
  const effectCurrent=expected=>current(expected)&&!ingressBlocked();
  const guardEffect=async expected=>{await guard(expected);if(ingressBlocked())throw Error('device_ingress_review_required');};
  function journalFailure(error){
    ingressError=error?.message==='ingress_journal_full'?'ingress_journal_full':'ingress_journal_unavailable';
    try{onIngressError(ingressError,ingressJournal.diagnostics());}catch{}
  }
  function journalBlock(id,reason){try{ingressJournal.block(id,reason);}catch(error){journalFailure(error);}}
  const ingressScope=expected=>({tenant_id:context.tenantId,channel_id:context.channelId,...scope(expected)});
  const sameIngressScope=(entry,expected)=>Object.entries(ingressScope(expected)).every(([key,value])=>entry.scope[key]===value);
  function wakeReplay(){for(const resolve of replayWaiters)resolve();replayWaiters.clear();}
  const waitForReplay=()=>new Promise(resolve=>replayWaiters.add(resolve));
  function wakeAdmission(){for(const resolve of admissionWaiters)resolve();admissionWaiters.clear();}
  const waitForAdmission=()=>new Promise(resolve=>admissionWaiters.add(resolve));
  const remember=id=>{if(seen.has(id))return false;seen.add(id);if(seen.size>2000)seen.delete(seen.values().next().value);return true;};
  function controller(expected){
    const abort=new AbortController();controllers.add({abort,expected});return abort;
  }
  function releaseController(abort){for(const entry of controllers)if(entry.abort===abort)controllers.delete(entry);}
  function sync(){for(const entry of controllers)if(!effectCurrent(entry.expected))entry.abort.abort();chatbot.sync();campaigns.sync();schedules.sync();bulk.sync();wakeReplay();pumpIngress();}
  async function finish(expected,operation_id,state,extra={}){
    try{await context.deviceEffectFinish({...scope(expected),operation_id,state,...extra});}catch{/* The durable grant stays conservative if acknowledgement is lost. */}
  }
  async function event(expected,data,sourceMessage,onAdmitted,deferEffects=false){
    await guard(expected);
    const result=await context.deviceEvent({...scope(expected),...data});
    await guard(expected);
    if(!result||!Array.isArray(result.effects)||result.effects.length>32)throw Error('invalid_device_effects');
    if(onAdmitted){
      if(result.ignored==='stale_event'){const error=Error('incoming_expired');error.ingressReason='expired';throw error;}
      if(result.ignored!==undefined||typeof result.duplicate!=='boolean')throw Error('invalid_device_admission');
      // Admission and effect execution are separate. A lost/ambiguous effect
      // acknowledgement must never cause the incoming event to be replayed.
      onAdmitted(result);
    }
    if(deferEffects)return result;
    return {...result,...await effects(result.effects,{expected,message:sourceMessage})};
  }
  async function effects(items,{expected=context.deviceSnapshot(),message}={}){
    if(ingressBlocked())return {source_consumed:false,source_deferred:ingressJournal.diagnostics().blocked===0&&!ingressError};
    let consumed=false,deferred=false;
    for(const item of items||[]){
      if(!knownEffect(item))continue;
      if(!effectCurrent(expected)){if(item.type==='transcribe'&&current(expected)&&ingressJournal.diagnostics().pending>0)deferred=true;continue;}
      const result=await execute(item,{expected,message});if(result?.source_consumed===true)consumed=true;if(result?.source_deferred===true)deferred=true;
    }
    return {source_consumed:consumed,source_deferred:deferred};
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
    if(!effectCurrent(expected))return;
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
      await guardEffect(expected);
      // Backpressure happens before claiming the SQL effect, so a waiting
      // process crash does not turn a definitely-unsent part into uncertainty.
      if(item.type==='send_text')await capacity.acquire({wait:true,isCurrent:()=>effectCurrent(expected)});
      await guardEffect(expected);
      permit=await context.deviceEffectAuthorize({...scope(expected),operation_id:id});
      await guardEffect(expected);
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
              ...(!old.accepted&&safeMessageFailureCode(old.error_code)?{error_code:old.error_code}:{})});
          return {source_consumed:item.type==='transcribe'&&old.accepted===true};
        }
      }
      abort=controller(expected);
      if(item.type==='send_text'){
        if(!phonePattern.test(data.phone)||typeof data.text!=='string'||!data.text.trim()||data.text.length>DEVICE_TEXT_MAX_CHARS)throw Error('device_effect_invalid');
        const recheck=async()=>{
          await guardEffect(expected);
          const fresh=await context.deviceEffectAuthorize({...scope(expected),operation_id:id,recheck:true});
          await guardEffect(expected);
          if(diagnosticMessagePermit(fresh,expected,id).fingerprint!==diagnostic.fingerprint)throw Error('diagnostic_message_refused');
        };
        await guardEffect(expected);if(!diagnostic)attempted=true;
        const result=await sendText(data.phone,data.text,{isCurrent:()=>effectCurrent(expected)&&!abort.signal.aborted,maxTextChars:DEVICE_TEXT_MAX_CHARS,
          ...(diagnostic?{diagnostic,assertAuthorized:recheck,onRelayAttempt:()=>{attempted=true;}}:{})});
        if(!messageId(result?.id))throw Error('device_send_unknown');
        ledger.save(id,{...reserved.record,state:'accepted',accepted:true,provider_id:result.id});
        sent.set(result.id,{operation_id:id,phone:data.phone});if(sent.size>2000)sent.delete(sent.keys().next().value);
        await finish(expected,id,'accepted',{provider_id:result.id});
      }else if(item.type==='reject_call'){
        if(!messageId(data.call_id)||typeof data.call_from!=='string'||!privateJid.test(data.call_from)||typeof client.rejectCall!=='function')throw Error('device_effect_invalid');
        await guardEffect(expected);attempted=true;await client.rejectCall(data.call_id,data.call_from);
        ledger.save(id,{...reserved.record,state:'accepted',accepted:true});await finish(expected,id,'accepted');
      }else if(item.type==='transcribe'){
        if(!message||data.message_id!==message.key?.id||!phonePattern.test(data.phone)||expected.settings.transcription?.enabled!==true||
            expected.capabilities.transcription_available!==true||data.max_seconds>expected.settings.transcription.max_minutes*60||
            data.max_seconds>expected.capabilities.transcription_max_seconds||data.max_bytes>expected.capabilities.transcription_max_bytes)throw Error('device_audio_unavailable');
        const result=await withDeviceAudio(message,{client,downloadMediaMessage,logger,signal:abort.signal,downloadOptions,
          maxBytes:data.max_bytes,maxSeconds:data.max_seconds,assertCurrent:()=>guardEffect(expected)},async payload=>{
          await guardEffect(expected);attempted=true;
          return context.deviceTranscribe({...scope(expected),operation_id:id,phone:data.phone,...payload});
        });
        if(!result||result.truncated!==false||typeof result.text!=='string'||!result.text.trim()||result.text.length>240000||[...result.text].length>120000||
            !Array.isArray(result.effects)||result.effects.length>32)throw Error('device_audio_unavailable');
        await guardEffect(expected);ledger.save(id,{...reserved.record,state:'accepted',accepted:true});
        await finish(expected,id,'accepted');await effects(result.effects,{expected});
        // Only explicit server-derived Chatbot effects can follow ASR.
        // The transcript never enters legacy AI or appointment intake.
        return {source_consumed:true};
      }else if(item.type==='webhook'){
        if(expected.settings.webhook?.enabled!==true||expected.capabilities.webhook!==true||
            typeof data.url!=='string'||typeof data.body!=='object'||!messageId(data.event_id))throw Error('device_webhook_unavailable');
        await guardEffect(expected);attempted=true;
        await postWebhook({url:data.url,payload:data.body,eventId:data.event_id,signal:abort.signal,
          isCurrent:()=>effectCurrent(expected),assertCurrent:()=>guardEffect(expected)});
        await finish(expected,id,'accepted');
      }
    }catch(error){
      if(item.type==='transcribe'&&!permit&&current(expected)&&!ingressError&&ingressJournal.diagnostics().pending>0&&ingressJournal.diagnostics().blocked===0)return {source_deferred:true};
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
      try{wakeReplay();wakeAdmission();pumpIngress();const expected=context.deviceSnapshot();await guard(expected);
        // Give retained controls/opt-outs precedence over automatic outbox
        // admission. Blocked input requires explicit operator review.
        const backlog=ingressJournal.diagnostics();if(backlog.pending>0||backlog.blocked>0||ingressError)return;
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
          !at||(type!=='append'&&at<openedAt-1000)||at<now()-300000||at>now()+60000)continue;
      const input=normalizeChatbotInput(message,{isGeneratedMessage});
      if(!input||(input.origin==='owner_command'&&!Number.isSafeInteger(expected.chatbot?.revision))||seen.has('message:'+key.id))continue;
      const self=normalizeFromJid(client.user?.id);
      if(key.fromMe===false&&self&&normalizeFromJid(key.remoteJid)===self)continue;
      // Persistence precedes the seen marker, PN/LID resolution, memory-slot
      // admission and every broker request. Source proto retains media keys.
      ingressOrder=Math.max(ingressOrder+1,Math.floor(now())*1000);
      try{ingressJournal.append({id:'message:'+key.id,scope:ingressScope(expected),at,order:ingressOrder,message});}
      catch(error){journalFailure(error);return;}
      remember('message:'+key.id);pumpIngress();
    }
  }
  const chatbot=createChatbotRuntime(context,{ledger,capacity,sendChatbot,currentDevice:effectCurrent,guardDevice:guardEffect,
    onEffects:effects,now,schedule,cancel,onAccepted:(id,data)=>{sent.set(id,data);if(sent.size>2000)sent.delete(sent.keys().next().value);},...chatbotOptions});
  const campaigns=createCampaignRuntime(context,{ledger,capacity,sendChatbot,currentDevice:effectCurrent,guardDevice:guardEffect,
    onEffects:effects,now,onAccepted:(id,data)=>{sent.set(id,data);if(sent.size>2000)sent.delete(sent.keys().next().value);}});
  const schedules=createScheduledRuntime(context,{ledger,capacity,sendChatbot,currentDevice:effectCurrent,guardDevice:guardEffect,
    onEffects:effects,now,onAccepted:(id,data)=>{sent.set(id,data);if(sent.size>2000)sent.delete(sent.keys().next().value);}});
  const bulk=createBulkRuntime(context,{ledger,capacity,sendChatbot,currentDevice:effectCurrent,guardDevice:guardEffect,
    onEffects:effects,now,onAccepted:(id,data)=>{sent.set(id,data);if(sent.size>2000)sent.delete(sent.keys().next().value);}});
  const ingress=createOrderedIngress({schedule,cancel,isCurrent:item=>current(item.expected),onSettled:item=>incoming.delete(item.entry.id),
    resolveIdentity:async({entry,expected,message})=>{
      await guard(expected);const phone=await resolveMessagePhone(message.key,client);await guard(expected);
      const self=normalizeFromJid(client.user?.id);
      if(phonePattern.test(phone||'')&&self&&phone!==self)return phone;
      if(phone&&phone===self)journalBlock(entry.id,'policy_denied');
      // Unresolved identity stays durable. onSettled frees its active marker
      // after the bounded lookup, so the next poll can safely retry it.
      return null;
    },
    run:async({entry,expected,message,at,input,order},phone)=>{
      let admitted=false,audioActive=false,completed=false;
      try{while(current(expected)&&connected&&!ingressError){
        if(at<now()-300000){journalBlock(entry.id,'expired');return;}
        try{
          await guard(expected);const audio=input.origin==='contact'?deviceAudioMetadata(message):null;
          const resolved=input.origin==='contact'&&!input.selection_id?resolveSelection(message,phone)||input:input;
          const result=await event(expected,{event_id:'msg:'+digest(message.key.id).slice(0,40),kind:audio?'audio':'text',occurred_at:new Date(at).toISOString(),
            phone,message_id:message.key.id,name:name(message.pushName),...resolved,ingress_order:order,
            ...(Number.isSafeInteger(expected.chatbot?.revision)?{chatbot_revision:expected.chatbot.revision}:{}),...(audio?{audio}:{})},message,response=>{
              try{
                if(audio){
                  // A duplicate confirms SQL admission, but its empty effects
                  // cannot recover the source-dependent ASR grant. Preserve
                  // source for review rather than inventing an ASR retry.
                  if(response.duplicate===true){journalBlock(entry.id,'business_started');admitted=true;return;}
                  if(ingressJournal.begin([entry.id])!==true)throw Error('incoming_audio_claim_missing');
                  audioActive=true;
                }else if(ingressJournal.ack(entry.id)!==true)throw Error('incoming_ack_missing');
                admitted=true;
              }
              catch(error){journalFailure(error);throw error;}
            },!!audio);
          if(audioActive){
            // Keep admission ordering independent from source-consuming ASR.
            // A following control must reach SQL before any automatic effect.
            audioActive=false;void consumeAudio(entry,expected,message,result).catch(()=>{});
          }
          return;
        }catch(error){
          if(admitted||!current(expected)||ingressError)return;
          if(error?.ingressReason){journalBlock(entry.id,error.ingressReason);return;}
          if(Number.isInteger(error?.status)&&error.status>=400&&error.status<500&&![408,429].includes(error.status)){
            journalBlock(entry.id,[401,403,404,423].includes(error.status)?'authorization_revoked':'broker_refused');return;
          }
          // The event ID is stable and SQL admission idempotent. Wait for the
          // normal poll rather than retrying a possibly executed send/effect.
          await waitForReplay();
        }
      }}finally{
        if(audioActive&&!completed&&current(expected))journalBlock(entry.id,'business_started');
        incoming.delete(entry.id);wakeAdmission();pumpIngress();
      }
    }});
  async function consumeAudio(entry,expected,message,result){
    let completed=false,items=result.effects;
    try{while(current(expected)&&connected&&!ingressError){
      const backlog=ingressJournal.diagnostics();
      if(backlog.blocked>0||entry.at<now()-300000)return;
      if(backlog.pending>0){await waitForAdmission();continue;}
      const outcome=await effects(items,{expected,message});
      const asrExpected=expected.settings.transcription?.enabled===true&&expected.capabilities.transcription_available===true;
      if(outcome.source_consumed===true||!asrExpected){
        try{if(ingressJournal.complete(entry.id)!==true)throw Error('incoming_audio_completion_missing');completed=true;}
        catch(error){journalFailure(error);}return;
      }
      if(outcome.source_deferred===true){items=items.filter(item=>item.type==='transcribe');continue;}
      return;
    }}finally{if(!completed&&current(expected))journalBlock(entry.id,'business_started');wakeAdmission();}
  }
  function pumpIngress(){
    if(closed||!connected||!context.isCurrent()||ingressError)return;
    let entries;try{entries=ingressJournal.pending();}catch(error){journalFailure(error);return;}
    for(const entry of entries){
      const expected=context.deviceSnapshot();ingressOrder=Math.max(ingressOrder,entry.order);
      if(!sameIngressScope(entry,expected)){incoming.delete(entry.id);journalBlock(entry.id,'scope_changed');continue;}
      if(incoming.has(entry.id))continue;
      if(entry.at<now()-300000){journalBlock(entry.id,'expired');continue;}
      if(pending+ingress.pending()>=32)return;
      const input=normalizeChatbotInput(entry.message,{isGeneratedMessage});
      if(!input||(input.origin==='owner_command'&&!Number.isSafeInteger(expected.chatbot?.revision))){journalBlock(entry.id,'policy_denied');continue;}
      incoming.add(entry.id);
      if(!ingress.enqueue({entry,expected,message:entry.message,at:entry.at,input,order:entry.order}))incoming.delete(entry.id);
    }
  }
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
    connected=true;pumpIngress();nextPoll();const expected=context.deviceSnapshot();
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
    diagnostics:()=>({pending:pending+ingress.pending(),inflight:inflight.size+chatbot.pending()+campaigns.pending()+schedules.pending()+bulk.pending(),audio_busy:audioBusy,
      ingress:ingressJournal.diagnostics(),ingress_error:ingressError}),
    close(){if(closed)return;closed=true;connected=false;cancel(poll);wakeReplay();wakeAdmission();ingressJournal.abandon?.();ingress.close();chatbot.close();campaigns.close();schedules.close();bulk.close();for(const entry of controllers)entry.abort.abort();controllers.clear();sent.clear();seen.clear();incoming.clear();}};
}
