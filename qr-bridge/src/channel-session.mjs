import {makeBoundedCache} from './bounded-cache.mjs';
import {makeTransportDiagnostics} from './transport-diagnostics.mjs';
import {readAuthorizedRetry} from './retry-identity.mjs';
/** A Baileys instance per context; shared helpers, no copy of the legacy server.
 * Private allowlisted text and opt-in bounded audio; no legacy AI routing.
 * Calls/history importing remain disabled. ASR never grants booking authority.
 */
import path from 'node:path';
import fs from 'node:fs';
import {encryptedChannelStore,privateDirectory} from './channel-storage.mjs';
import {resolveMessagePhone,normalizeFromJid,freshQR} from './core.mjs';
import {shouldIngestUpsert,timestampMs} from './linked-session-health.mjs';
import {deliveryFromAck} from './message-retry-store.mjs';
import {makeEncryptedRetryStore} from './encrypted-retry-store.mjs';
import {sendStoredText,sendStoredProtocol} from './reliable-send.mjs';
import {buildChatbotContent} from './chatbot-message.mjs';
import {makePilotDeviceCache,resolvePilotRecipient} from './pilot-recipient.mjs';
import {makeAssistantTyping} from './assistant-typing.mjs';
import {withAssistantAudio} from './ai-audio.mjs';
import {makeConversationIntake} from './conversation-intake.mjs';
import {sendConnectionNoticeOnce} from './connection-alert.mjs';
import {normalizeInbound,priorityText,updateEvent,callEvent,queueControl} from './inbound-event.mjs';
import {createDeviceRuntime} from './device-runtime.mjs';
import {messagePreflightError} from './device-message-failure.mjs';
import {createDeviceProxy} from './device-network.mjs';
import {currentDiagnosticMessage} from './device-diagnostic-message.mjs';

export async function createBaileysChannelSession(context,{loadBaileys=()=>import('@whiskeysockets/baileys'),now=Date.now,typingFactory=makeAssistantTyping,intakeOptions={},schedule=setTimeout,cancel=clearTimeout,deviceOptions={}}={}){
  if(context.legacyTransport===true)throw Error('legacy_requires_existing_socket');
  await context.assertCurrent();
  const mod=await loadBaileys();await context.assertCurrent();
  const makeSocket=mod.default,openedAt=now();
  const diagnostics=makeTransportDiagnostics({secret:context.secret,now,emit:event=>{
    if(['decrypt_failed','retry_received','retry_send_failed','retry_content_missing','get_message','server_rejected','auth_write_failed'].includes(event.stage))
      console.info(JSON.stringify({component:'orion-managed-transport',...event}));
  }});
  const silent=diagnostics.logger;
  const store=encryptedChannelStore({directory:privateDirectory(path.join(context.directory,'auth')),secret:context.secret,
    serialize:value=>JSON.stringify(value,mod.BufferJSON.replacer),deserialize:value=>JSON.parse(value,mod.BufferJSON.reviver)});
  const savedCreds=store.get('creds');
  if(!savedCreds&&fs.readdirSync(path.join(context.directory,'auth')).some(name=>name.endsWith('.enc')))throw Error('channel_auth_unavailable');
  const creds=savedCreds||mod.initAuthCreds();
  // Never pair a replacement identity after a partial/corrupted restore.
  if(!savedCreds)store.set('creds',creds);
  const pendingKeys=store.get('auth-key-transaction');
  if(pendingKeys!==null){
    if(!Array.isArray(pendingKeys)||pendingKeys.length>10000||pendingKeys.some(row=>!row||typeof row.id!=='string'||!row.id.startsWith('key:')||row.id.length>2048))throw Error('channel_auth_unavailable');
    for(const row of pendingKeys)store.set(row.id,row.value);
    store.set('auth-key-transaction',null);
  }
  if(context.resumeOnly===true&&creds.registered!==true)throw Error('channel_auth_unavailable');
  // A new link may require a provider restart before its first open. Carry
  // only this process's new-link intent; restored registered auth never arms it.
  const newLinkNotice=context.newLinkNotice||{pending:false};
  if(context.notifySelf===true&&context.resumeOnly!==true&&creds.registered!==true)newLinkNotice.pending=true;
  const notifyNewLink=context.notifySelf===true&&context.resumeOnly!==true&&newLinkNotice.pending===true;
  let state='connecting',qrValue=null,qrAt=0,closed=false,queue=Promise.resolve(),identityQueue=Promise.resolve(),pending=0,noticeTimer=null,opened=false,pairAttempt=null,pairTimer=null;
  const seen=new Set(),metadata=new Map(),typingByPhone=new Map(),typingLeases=new Set(),abort=new AbortController();
  const retryStore=makeEncryptedRetryStore({directory:privateDirectory(path.join(context.directory,'retry-proto')),secret:context.secret,
    serialize:value=>JSON.stringify(value,mod.BufferJSON.replacer),deserialize:value=>JSON.parse(value,mod.BufferJSON.reviver),now,
    ...(context.deviceSnapshot?.()?{maxPayloadBytes:512*1024,maxTotalBytes:96*1024*2000}:{})});
  const deviceCache=makePilotDeviceCache();
  let deviceRuntime=null;
  const overflowNotices=new Map(),overflowEpoch=new Map();
  const current=()=>!closed&&context.isCurrent();
  const guarded=async()=>{if(!current())throw Error('channel_socket_replaced');await context.assertCurrent();if(!current())throw Error('channel_socket_replaced');};
  function expirePairing(){
    if(pairAttempt&&now()>=pairAttempt.expiresAt){
      cancel(pairTimer);pairTimer=null;
      pairAttempt.code=null;
      if(['pairing','pair_failed'].includes(state))state='pair_expired';
      // A timed-out provider request may still be running. Do not issue a
      // second request until it has settled, even when its code has expired.
      if(!pairAttempt.pending){
        pairAttempt=null;
        if(state==='pair_expired'&&qrValue&&freshQR(qrAt,now()))state='qr_ready';
      }
    }
  }
  function pairingResult(attempt){
    const expires=Math.floor(attempt.expiresAt-now());
    if(pairAttempt!==attempt||!attempt.code||expires<=0||creds.registered===true||state==='connected')throw Error('channel_pairing_unavailable');
    return {status:'pairing',code:attempt.code,expires_in_ms:Math.min(90000,expires)};
  }
  function overflowNotice(message,normalized){
    const peer=String(message.key?.remoteJid||'');
    if(!/^\d{8,20}(?::\d+)?@(s\.whatsapp\.net|lid)$/.test(peer))return;
    if(!overflowEpoch.has(peer)&&overflowEpoch.size>=100)return;
    overflowEpoch.set(peer,(overflowEpoch.get(peer)||0)+1);
    const existing=overflowNotices.get(peer),priority=priorityText(normalized.text)||!!normalized.event.control;
    if(existing){
      if(priority)queueControl(existing.controls,[message,normalized]);
      else existing.latest=[message,normalized];
      return;
    }
    if(overflowNotices.size>=100)return;
    const state={controls:[],latest:null};overflowNotices.set(peer,state);
    void (async()=>{
      let item=[message,normalized];
      while(item&&current()){
        const [msg,event]=item;await guarded();const pn=await resolveMessagePhone(msg.key,client);await guarded();
        if(!pn||!context.allowedSender(pn))return;
        context.onRejected?.({phone:pn});
        await context.onInbound({phone:pn,message_id:msg.key.id,text:event.text||'',kind:event.text?'text':'unsupported',
          ...(event.special?{envelope:event.event}:{})});
        if(state.controls.length)item=state.controls.shift();
        else {item=state.latest;state.latest=null;}
      }
    })().catch(()=>{}).finally(()=>overflowNotices.delete(peer));
  }
  // Signal persistence must complete in the event turn. Awaiting broker I/O
  // here loses a creds.update followed immediately by restartRequired/close.
  // isCurrent is the manager's monotonic, fenced lease, not an optimistic flag.
  const write=job=>{
    try{if(!current())throw Error('channel_socket_replaced');job();return Promise.resolve();}
    catch(error){if(current()){diagnostics.record('auth_write_failed');close();context.onDisconnected('disconnected');}return Promise.reject(error);}
  };
  const auth={creds,keys:{
    async get(type,ids){if(!current())throw Error('channel_socket_replaced');return Object.fromEntries(ids.map(id=>{
      let value=store.get('key:'+type+':'+id);
      if(type==='app-state-sync-key'&&value)value=mod.proto.Message.AppStateSyncKeyData.fromObject(value);
      return [id,value];}));},
    set:data=>write(()=>{
      const batch=[];
      for(const [type,entries] of Object.entries(data))for(const [id,value] of Object.entries(entries))batch.push({id:'key:'+type+':'+id,value:value??null});
      if(batch.length>10000||batch.some(row=>row.id.length>2048))throw Error('channel_key_batch_invalid');
      if(!batch.length)return;
      store.set('auth-key-transaction',batch);
      for(const row of batch)store.set(row.id,row.value);
      store.set('auth-key-transaction',null);
    })}};
  const deviceSnapshot=context.deviceSnapshot?.();
  if(deviceSnapshot?.settings.proxy?.enabled===true&&deviceSnapshot.capabilities.proxy!==true)throw Error('device_proxy_unavailable');
  const proxyTransport=deviceSnapshot?await (deviceOptions.createProxy||createDeviceProxy)(deviceSnapshot.settings.proxy,
    {secret:deviceSnapshot.proxy_secret?.password,signal:abort.signal,assertCurrent:guarded,...deviceOptions.proxyOptions}):null;
  await guarded();
  const client=makeSocket({auth,logger:silent,browser:mod.Browsers.windows('Chrome'),
    printQRInTerminal:false,markOnlineOnConnect:false,syncFullHistory:false,userDevicesCache:deviceCache,maxMsgRetryCount:5,
    msgRetryCounterCache:context.retryCounterCache||makeBoundedCache({max:10000,ttlMs:600000,now}),
    placeholderResendCache:makeBoundedCache({max:5000,ttlMs:600000,now}),
    callOfferCache:makeBoundedCache({max:1000,ttlMs:300000,now}),
    ...proxyTransport?.socket,
    shouldSyncHistoryMessage:()=>false,getMessage:async key=>{
      const message=await readAuthorizedRetry(retryStore,key,{socket:client,isCurrent:current,assertCurrent:guarded});
      diagnostics.record('get_message',key?.id,{cached:!!message});return message;
    }});
  const close=()=>{if(closed)return;closed=true;state='stopped';qrValue=null;pairAttempt=null;cancel(pairTimer);cancel(noticeTimer);abort.abort();deviceRuntime?.close();proxyTransport?.close();for(const lease of typingLeases)lease.stop();typingLeases.clear();client.ev.removeAllListeners();try{client.end(undefined);}catch{}};
  async function relayText(phone,text,{isCurrent:operationCurrent=()=>true,maxTextChars=1200,device=false,
    diagnostic=null,assertAuthorized,onRelayAttempt}={}){
    await guarded();const self=normalizeFromJid(client.user?.id);
    const sendCurrent=()=>{try{return current()&&operationCurrent()===true;}catch{return false;}};
    const checkDiagnostic=()=>{
      const own=normalizeFromJid(client.user?.id);
      if(!own||!diagnostic&&phone===own)throw Error('channel_send_denied');
      if(diagnostic&&(!device||typeof assertAuthorized!=='function'||typeof onRelayAttempt!=='function'||
        !currentDiagnosticMessage(diagnostic,{phone,text,self:own,snapshot:context.deviceSnapshot?.()})))throw Error('diagnostic_message_refused');
    };
    const authorize=async()=>{
      await guarded();if(!sendCurrent())throw Error('channel_operation_superseded');checkDiagnostic();
      if(diagnostic){await assertAuthorized();if(!sendCurrent())throw Error('channel_operation_superseded');checkDiagnostic();}
    };
    if(!sendCurrent())throw Error('channel_operation_superseded');
    if(state!=='connected')throw messagePreflightError('TRANSPORT_UNAVAILABLE_BEFORE_SEND');
    checkDiagnostic();
    if(!self||phone===self&&!diagnostic||(!device&&!context.allowedSender(phone))||device&&!context.deviceSnapshot?.())throw Error('channel_send_denied');
    await authorize();
    const exists=await client.onWhatsApp(phone);await authorize();
    const recipient=exists?.find(row=>row.exists&&normalizeFromJid(row.jid)===phone);
    if(!recipient)throw messagePreflightError(Array.isArray(exists)&&exists.some(row=>row?.exists===false&&normalizeFromJid(row.jid)===phone)
      ?'RECIPIENT_NOT_REGISTERED':'RECIPIENT_IDENTITY_UNVERIFIED');
    const proof=await resolvePilotRecipient({phone,pilotPhone:phone,enabled:!device&&context.verifiedLid?.()===true,
      socket:client,deviceCache,isCurrent:current});await authorize();
    const sent=await sendStoredText({jid:proof.jid,recipientProof:proof,text,store:retryStore,freshDevices:true,isCurrent:sendCurrent,maxTextChars,preserveWhitespace:device,
      makeMessage:(jid,body)=>mod.generateWAMessage(jid,{text:body},{userJid:client.user.id,logger:silent,messageId:mod.generateMessageIDV2(client.user.id)}),
      relay:async(jid,body,options)=>{
        await authorize();
        if(state!=='connected')throw messagePreflightError('TRANSPORT_UNAVAILABLE_BEFORE_SEND');
        // Both the intended self PN and the active device/lease are checked
        // after the last async preparation, immediately before the relay.
        checkDiagnostic();if(diagnostic)onRelayAttempt();
        return client.relayMessage(jid,body,options);
      }});
    return {id:sent?.key?.id};
  }
  async function relayChatbot(phone,action,{isCurrent:operationCurrent=()=>true,assertAuthorized,signal,
    loadAsset,prepareCarouselImage,onRelayAttempt,fetchImage}={}){
    const sendCurrent=()=>{try{return current()&&operationCurrent()===true&&!signal?.aborted;}catch{return false;}};
    const authorize=async()=>{await guarded();if(!sendCurrent()||typeof assertAuthorized!=='function')throw Error('chatbot_operation_superseded');await assertAuthorized();if(!sendCurrent())throw Error('chatbot_operation_superseded');};
    await authorize();const self=normalizeFromJid(client.user?.id);
    if(state!=='connected')throw messagePreflightError('TRANSPORT_UNAVAILABLE_BEFORE_SEND');
    if(!context.deviceSnapshot?.()||!self||phone===self||!/^\d{8,15}$/.test(phone))throw Error('channel_send_denied');
    const exists=await client.onWhatsApp(phone);await authorize();
    if(!exists?.some(row=>row.exists&&normalizeFromJid(row.jid)===phone))throw messagePreflightError(Array.isArray(exists)&&exists.some(row=>row?.exists===false&&normalizeFromJid(row.jid)===phone)
      ?'RECIPIENT_NOT_REGISTERED':'RECIPIENT_IDENTITY_UNVERIFIED');
    const proof=await resolvePilotRecipient({phone,pilotPhone:phone,enabled:false,socket:client,deviceCache,isCurrent:sendCurrent});await authorize();
    let built;
    try{
      const sent=await sendStoredProtocol({jid:proof.jid,recipientProof:proof,store:retryStore,isCurrent:sendCurrent,
        assertAuthorized:authorize,onRelayAttempt,
        makeMessage:async jid=>{
          built=await buildChatbotContent(action,{sdk:mod,jid,loadAsset,prepareCarouselImage,fetchImage,signal,
            upload:(...args)=>client.waUploadToServer(...args),logger:silent,assertCurrent:authorize,isCurrent:sendCurrent,mediaOptions:proxyTransport?.download});
          return mod.generateWAMessageFromContent(jid,built.content,{userJid:client.user.id,messageId:mod.generateMessageIDV2(client.user.id),timestamp:new Date(now())});
        },
        relay:(jid,body,options)=>client.relayMessage(jid,body,options)});
      return {id:sent.key.id};
    }finally{built?.dispose();}
  }
  if(deviceSnapshot)deviceRuntime=createDeviceRuntime(context,{client,downloadMediaMessage:mod.downloadMediaMessage,logger:silent,
    now,openedAt,schedule,cancel,downloadOptions:proxyTransport.download,
    capacity:context.deviceSendCapacity,
    sendText:(phone,text,options)=>relayText(phone,text,{...options,device:true}),sendChatbot:relayChatbot,
    isGeneratedMessage:key=>!!retryStore.get(key),...deviceOptions.runtimeOptions});
  const groupSetting=(name,fallback,max)=>{const raw=process.env[name],value=Number(raw);
    return raw!==undefined&&Number.isSafeInteger(value)&&value>=0&&value<=max?value:fallback;};
  const settleMs=groupSetting('QR_AI_GROUP_WAIT_MS',350,2000);
  const intake=makeConversationIntake({settleMs,maxGroupMs:Math.max(settleMs,groupSetting('QR_AI_GROUP_MAX_WAIT_MS',1200,5000)),
    ...intakeOptions,
    onRejected:async batch=>{const first=metadata.get(batch.messageIds[0]);if(first)context.onRejected?.({phone:first.phone});},
    consume:async batch=>{
      const entries=batch.messages.map(item=>metadata.get(item.id)),first=entries[0];
      const batchCurrent=()=>current()&&batch.isCurrent()&&!!first&&entries.every(item=>item?.phone===first.phone&&context.allowedSender(item.phone));
      if(!batchCurrent())return {state:'superseded',not_committed:true};
      await guarded();
      return context.onMessage({phone:first.phone,message_id:batch.messages[0].id,message_ids:batch.messages.map(item=>item.id),
        text:batch.text,intake_revision:batch.intakeRevision,
        ...(entries.some(item=>item.event)?{envelope:entries.findLast(item=>item.event)?.event}:{})},{noticeProofs:entries.map(item=>item.proof),isCurrent:batchCurrent,
        beforeReply:value=>entries.at(-1)?.lease?.beforeReply(value)});
    }});
  client.ev.on('creds.update',()=>{write(()=>store.set('creds',creds)).catch(()=>{diagnostics.record('auth_write_failed');close();});});
  client.ev.on('connection.update',event=>{
    if(!current())return close();
    if(event.qr){expirePairing();qrValue=event.qr;qrAt=now();if(!pairAttempt)state='qr_ready';}
    if(event.connection==='open'){
      state='connected';qrValue=null;pairAttempt=null;cancel(pairTimer);pairTimer=null;
      if(!opened){opened=true;
        void Promise.resolve().then(()=>context.onConnected()).then(()=>{
          if(deviceRuntime){void deviceRuntime.onConnected().catch(()=>{});return;}
          if(!notifyNewLink||!current())return;
          noticeTimer=schedule(async()=>{
            try{
              await queue;await guarded();newLinkNotice.pending=false;
              await sendConnectionNoticeOnce({authDir:path.join(context.directory,'auth'),client,isCurrent:current,
                sendText:async(jid,text)=>{
                  await guarded();
                  return sendStoredText({jid,text,store:retryStore,freshDevices:true,isCurrent:current,
                    makeMessage:(to,body)=>mod.generateWAMessage(to,{text:body},{userJid:client.user.id,logger:silent,messageId:mod.generateMessageIDV2(client.user.id)}),
                    relay:async(to,body,options)=>{await guarded();return client.relayMessage(to,body,options);}});
                }});
            }catch{/* Preserve the receipt; never retry an ambiguous notice. */}
          },2500);noticeTimer?.unref?.();
        }).catch(()=>{close();context.onDisconnected('disconnected');});
      }
    }
    if(event.connection==='close'){
      const code=event.lastDisconnect?.error?.output?.statusCode;
      close();context.onDisconnected(code===mod.DisconnectReason.loggedOut?'logged_out':'disconnected');
    }
  });
  client.ev.on('messages.update',updates=>{
    if(deviceRuntime){deviceRuntime.receipts(updates);return;}
    for(const item of updates){const delivery=deliveryFromAck(item.update?.status);
      if(current()&&['delivered','read'].includes(delivery))context.onReceipt(item.key?.id,delivery).catch(()=>{});
      const edited=updateEvent(item.key,item.update,now);if(current()&&edited)receive({messages:[edited],type:'notify'});}
  });
  client.ev.on('messages.delete',({keys=[]}={})=>{
    if(deviceRuntime)return;
    for(const key of keys.slice(0,100))if(key?.fromMe===false&&key.id)
      receive({type:'notify',messages:[{key:{...key,id:'deleted:'+key.id.slice(0,145)},messageTimestamp:now()/1000,
        message:{protocolMessage:{type:0,key}}}]});
  });
  client.ev.on('call',calls=>{if(deviceRuntime){deviceRuntime.calls(calls);return;}for(const call of (calls||[]).slice(0,20)){const event=callEvent(call,now);if(event)receive({messages:[event],type:'notify'});}});
  function receive({messages=[],type}){
    if(deviceRuntime){deviceRuntime.receive({messages,type});return;}
    for(const message of messages){
      const id=message.key?.id,at=timestampMs(message.messageTimestamp);
      if(!current()||message.key?.fromMe||typeof id!=='string'||!/^[a-zA-Z0-9._:-]{1,160}$/.test(id)||seen.has(id)||
          !shouldIngestUpsert(type,message.messageTimestamp,now())||!at||at<openedAt-1000)continue;
       const normalized=normalizeInbound(message),text=normalized.text,media=message.message?.audioMessage||message.message?.ephemeralMessage?.message?.audioMessage;
       if(normalized.silent)continue;
       const overflow=pending>=32;
       if(pending>=40){overflowNotice(message,normalized);continue;}
      seen.add(id);if(seen.size>2000)seen.delete(seen.values().next().value);
      const peer=String(message.key.remoteJid||'');
      if(!/^\d{8,20}(?::\d+)?@(s\.whatsapp\.net|lid)$/.test(peer))continue;
      if(overflow)overflowEpoch.set(peer,(overflowEpoch.get(peer)||0)+1);
      const ingressEpoch=overflowEpoch.get(peer)||0;
      // Only identity resolution is ordered here. Never wait for ASR or an AI
      // reply before registering a newer authenticated message's revision.
      pending++;
      const identityTask=identityQueue.catch(()=>{}).then(async()=>{
        await guarded();const phone=await resolveMessagePhone(message.key,client);await guarded();
        const self=normalizeFromJid(client.user?.id);
        if(!phone||!self||!context.allowedSender(phone)||phone===self)return null;
         const entry={phone,...(normalized.special?{event:normalized.event}:{})};metadata.set(id,entry);
        const entryCurrent=()=>current()&&context.allowedSender(phone)&&(overflowEpoch.get(peer)||0)===ingressEpoch;
         // Even rejected input registers its revision before consuming more
         // work. It revokes stale inference and records handoff/opt-out in the
         // journal. The rejected prefix never reaches the domain consumer.
         const proofTask=Promise.resolve(context.onInbound({phone,message_id:id,text:text||'',kind:media?'audio':text?'text':'unsupported',
           ...(normalized.special?{envelope:normalized.event}:{})})).then(proof=>{entry.proof=proof;return proof;});
         void proofTask.catch(()=>{});
         if(overflow&&!priorityText(text)&&!normalized.event.control){
           context.onRejected?.({phone});void proofTask.catch(()=>{}).finally(()=>{metadata.delete(id);pending--;});return true;
         }
        const task=intake.submit({conversationKey:context.channelId+':'+phone,messageId:id,isCurrent:entryCurrent,
           notice:async()=>{const proof=await proofTask;await guarded();return proof;},
          prepare:async({signal})=>{
            await guarded();
            if(context.assistantEnabled?.()===false||(!normalized.special&&!text?.trim()&&context.audioEnabled?.()!==true))return {skip:true};
            let typing=typingByPhone.get(phone);
            if(!typing){typing=typingFactory({socket:client,pilotPhone:()=>phone,isConnected:()=>state==='connected'&&current(),
              isAllowed:value=>context.allowedSender(value)&&context.assistantEnabled?.()!==false});typingByPhone.set(phone,typing);}
            entry.lease=typing.begin({phone,chatJid:peer,isCurrent:entryCurrent});typingLeases.add(entry.lease);
             if(normalized.special)return {text:text||'[Evento de mensagem recebido]'};
             let body=text;
            if(!body?.trim())body=await withAssistantAudio(message,{client,downloadMediaMessage:mod.downloadMediaMessage,logger:silent,
              signal:AbortSignal.any([abort.signal,signal])},async({buffer,mediaType,signal})=>{
                await guarded();return context.transcribe({phone,buffer,mediaType,signal});});
            await guarded();if(signal.aborted)return {skip:true};return {text:body};
          }});
        // The identity queue returns immediately; only this message's cleanup
        // waits for ordered consumption (including an explicit zero-write carry).
        void task.then(result=>{if(result?.state==='overloaded'){overflowEpoch.set(peer,(overflowEpoch.get(peer)||0)+1);context.onRejected?.({phone});}},()=>{}).finally(()=>{entry.lease?.stop();typingLeases.delete(entry.lease);metadata.delete(id);pending--;});
        return true;
      });
      identityQueue=identityTask;identityTask.then(accepted=>{if(!accepted)pending--;},()=>{pending--;});
    }
  }
  client.ev.on('messages.upsert',receive);
  return {close,status:()=>{expirePairing();return state;},
    deviceSettingsChanged:()=>deviceRuntime?.sync(),
    deviceDiagnostics:()=>deviceRuntime?.diagnostics()||null,
    async logout({authorize}={}){
      if(!deviceRuntime||typeof authorize!=='function')throw Error('device_logout_unavailable');
      // Removing revokes normal device effects/leases before the provider call.
      // Only the already-recorded control operation may finish this logout.
      deviceRuntime.close();await authorize();
      if(closed||state!=='connected')return {remote_logout:false};
      if(typeof client.logout!=='function')throw Error('device_logout_unavailable');
      await client.logout();return {remote_logout:true};
    },
    phone:()=>state==='connected'?(normalizeFromJid(client.user?.id)||null):null,
    supportsPairing:()=>current()&&context.resumeOnly!==true&&creds.registered!==true&&state!=='connected'&&typeof client.requestPairingCode==='function',
    qr:()=>{expirePairing();return !pairAttempt&&['qr_ready','pair_expired'].includes(state)&&freshQR(qrAt,now())?
      {value:qrValue,expires_in_ms:Math.max(0,45000-(now()-qrAt))}:null;},
    async pair(phone){
      await guarded();expirePairing();
      if(typeof phone!=='string'||phone!==phone.trim()||!/^[1-9]\d{7,14}$/.test(phone)||creds.registered===true||state==='connected'||
          context.resumeOnly===true||typeof client.requestPairingCode!=='function')throw Error('channel_pairing_unavailable');
      if(pairAttempt){
        if(pairAttempt.phone!==phone)throw Error('channel_pairing_in_progress');
        if(now()>=pairAttempt.expiresAt)throw Error('channel_pairing_expired');
        if(pairAttempt.pending)return pairAttempt.promise;
        return pairingResult(pairAttempt);
      }
      if(!qrValue||!freshQR(qrAt,now()))throw Error('channel_pairing_not_ready');
      const attempt={phone,expiresAt:now()+90000,pending:true,code:null,promise:null};
      pairAttempt=attempt;state='pairing';
      pairTimer=schedule(()=>{if(pairAttempt===attempt){attempt.expiresAt=Math.min(attempt.expiresAt,now());expirePairing();}},90000);pairTimer?.unref?.();
      attempt.promise=(async()=>{
        try{
          const raw=await client.requestPairingCode(phone);await guarded();expirePairing();
          if(typeof raw!=='string'||!((raw.length===8&&/^[A-Za-z0-9]{8}$/.test(raw))||
              (raw.length===9&&/^[A-Za-z0-9]{4}-[A-Za-z0-9]{4}$/.test(raw))))throw Error('invalid_pairing_code');
          const code=raw.replace('-','').toUpperCase();attempt.code=code.slice(0,4)+'-'+code.slice(4);
          return pairingResult(attempt);
        }catch{
          attempt.code=null;
          if(pairAttempt===attempt&&current()&&state!=='connected')state=now()>=attempt.expiresAt?'pair_expired':'pair_failed';
          // Provider failures can be ambiguous. Retain the attempt until its
          // bounded expiry; neither polling nor an immediate retry resets auth.
          throw Error('channel_pairing_unavailable');
        }finally{attempt.pending=false;expirePairing();}
      })();
      return attempt.promise;
    },
    async send(phone,text,options){if(deviceRuntime)throw Error('device_requires_effect_grant');return relayText(phone,text,options);}
  };
}
