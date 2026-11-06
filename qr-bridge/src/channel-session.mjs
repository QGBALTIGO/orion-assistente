import {makeBoundedCache} from './bounded-cache.mjs';
import {fenceSignalRepository} from './signal-fence.mjs';
import {makeConnectionHealth,disconnectPolicy} from './connection-health.mjs';
import {selectWaWebVersion} from './wa-version-policy.mjs';
import {makeTransportDiagnostics} from './transport-diagnostics.mjs';
import {readAuthorizedRetry} from './retry-identity.mjs';
/** A Baileys instance per context; shared helpers, no copy of the legacy server.
 * Private allowlisted text and opt-in bounded audio; no legacy AI routing.
 * Calls/history importing remain disabled. ASR never grants booking authority.
 */
import path from 'node:path';
import fs from 'node:fs';
import {createHmac} from 'node:crypto';
import {encryptedChannelStore,privateDirectory} from './channel-storage.mjs';
import {resolveMessagePhone,normalizeFromJid,freshQR} from './core.mjs';
import {shouldIngestUpsert,timestampMs} from './linked-session-health.mjs';
import {deliveryFromAck} from './message-retry-store.mjs';
import {makeEncryptedRetryStore} from './encrypted-retry-store.mjs';
import {sendStoredText,sendStoredProtocol} from './reliable-send.mjs';
import {buildChatbotContent,validateChatbotAsset} from './chatbot-message.mjs';
import {saveFallbackSelection,resolveFallbackSelection} from './selection-fallback.mjs';
import {makePilotDeviceCache,resolvePilotRecipient} from './pilot-recipient.mjs';
import {makeAssistantTyping} from './assistant-typing.mjs';
import {makeOutgoingPresence} from './outgoing-presence.mjs';
import {withAssistantAudio} from './ai-audio.mjs';
import {makeConversationIntake} from './conversation-intake.mjs';
import {sendConnectionNoticeOnce} from './connection-alert.mjs';
import {normalizeInbound,priorityText,updateEvent,callEvent,queueControl} from './inbound-event.mjs';
import {createDeviceRuntime} from './device-runtime.mjs';
import {createIngressJournal} from './ingress-journal.mjs';
import {createAssistantIngress} from './assistant-ingress.mjs';
import {withIngressHealth} from './ingress-health.mjs';
import {messagePreflightError} from './device-message-failure.mjs';
import {createDeviceProxy} from './device-network.mjs';
import {currentDiagnosticMessage} from './device-diagnostic-message.mjs';
import {createDeviceGroupContacts} from './device-group-contacts.mjs';
import {createDeviceNumberCheck} from './device-number-check.mjs';
import {createGroupAddSocket} from './device-group-add.mjs';
import {classifyPairedAuth} from './paired-auth-state.mjs';
import {chatbotInteractiveMode} from './interactive-mode.mjs';
import {disconnectEvidence} from './disconnect-evidence.mjs';

export async function createBaileysChannelSession(context,{loadBaileys=()=>import('@whiskeysockets/baileys'),selectVersion=selectWaWebVersion,now=Date.now,presenceNow=()=>performance.now(),typingFactory=makeAssistantTyping,intakeOptions={},schedule=setTimeout,cancel=clearTimeout,deviceOptions={}}={}){
  if(context.legacyTransport===true)throw Error('legacy_requires_existing_socket');
  await context.assertCurrent();
  const mod=await loadBaileys();await context.assertCurrent();
  const makeSocket=mod.default,openedAt=now();
  const diagnostics=makeTransportDiagnostics({secret:context.secret,now,emit:event=>{
    if(['decrypt_failed','retry_received','retry_send_failed','retry_content_missing','get_message','server_rejected','auth_write_failed'].includes(event.stage))
      console.info(JSON.stringify({component:'orion-managed-transport',...event}));
  }});
  const silent=diagnostics.logger;
  let deviceRuntime=null,assistantIngress=null,assistantIdentity=null,assistantFault=null,assistantTimer=null,assistantReplaying=false;
  const assistantJobs=new Set();
  const ingressDiagnostics=()=>deviceRuntime?.diagnostics()?.ingress||(assistantFault?
    {...(assistantIngress?.diagnostics()||{}),state:'unavailable',error_code:'assistant_ingress_unavailable'}:assistantIngress?.diagnostics());
  const health=makeConnectionHealth({now,schedule,cancel,previous:context.previousConnectionHealth,
    readiness:value=>withIngressHealth(value,ingressDiagnostics()),scope:{channel_id:context.channelId,tenant_id:context.tenantId,
    owner:context.owner,fence:context.fence,generation:context.deviceSnapshot?.()?.generation},
    isSyncPending:()=>client.ev.isBuffering?.()===true,
    emit:event=>{console.info(JSON.stringify(event));context.onConnectionHealth?.(health.snapshot());},onStale:reason=>{
      if(!current()||state!=='connected')return;
      const policy={...disconnectPolicy(408),reason};health.disconnected(policy,1);close();context.onDisconnected(policy);
    }});
  health.connecting();
  const store=encryptedChannelStore({directory:privateDirectory(path.join(context.directory,'auth')),secret:context.secret,
    serialize:value=>JSON.stringify(value,mod.BufferJSON.replacer),deserialize:value=>JSON.parse(value,mod.BufferJSON.reviver),
    isCurrent:()=>context.isCurrent()===true});
  const savedCreds=store.get('creds');
  if(!savedCreds&&fs.readdirSync(path.join(context.directory,'auth')).some(name=>name.endsWith('.enc')))throw Error('channel_auth_unavailable');
  if(context.resumeOnly===true&&!savedCreds)throw Error('channel_auth_unavailable');
  const creds=savedCreds||mod.initAuthCreds();
  const authState=()=>classifyPairedAuth(creds,mod);
  const restoredState=authState();
  // The SDK leaves registered=false after a successful QR pair. Its signed
  // companion account is the restore criterion; a flag is never authority.
  if(!['paired','unlinked'].includes(restoredState)||(context.resumeOnly===true&&restoredState!=='paired'))throw Error('channel_auth_unavailable');
  // Never pair a replacement identity after a partial/corrupted restore.
  if(!savedCreds)store.set('creds',creds);
  const pendingKeys=store.get('auth-key-transaction');
  if(pendingKeys!==null){
    if(!Array.isArray(pendingKeys)||pendingKeys.length>10000||pendingKeys.some(row=>!row||typeof row.id!=='string'||!row.id.startsWith('key:')||row.id.length>2048))throw Error('channel_auth_unavailable');
    for(const row of pendingKeys)store.set(row.id,row.value);
    store.set('auth-key-transaction',null);
  }
  // A new link may require a provider restart before its first open. Carry
  // only this process's new-link intent; restored paired auth never arms it.
  const newLinkNotice=context.newLinkNotice||{pending:false};
  if(context.notifySelf===true&&context.resumeOnly!==true&&restoredState==='unlinked')newLinkNotice.pending=true;
  const notifyNewLink=context.notifySelf===true&&context.resumeOnly!==true&&newLinkNotice.pending===true;
  let state='connecting',qrValue=null,qrAt=0,closed=false,queue=Promise.resolve(),identityQueue=Promise.resolve(),pending=0,noticeTimer=null,opened=false,pairAttempt=null,pairTimer=null;
  const seen=new Set(),metadata=new Map(),typingLeases=new Set(),abort=new AbortController();
  const retryStore=makeEncryptedRetryStore({directory:privateDirectory(path.join(context.directory,'retry-proto')),secret:context.secret,
    serialize:value=>JSON.stringify(value,mod.BufferJSON.replacer),deserialize:value=>JSON.parse(value,mod.BufferJSON.reviver),now,
    ...(context.deviceSnapshot?.()?{maxPayloadBytes:512*1024,maxTotalBytes:96*1024*2000}:{})});
  const selectionStore=context.deviceSnapshot?.()?makeEncryptedRetryStore({
    directory:privateDirectory(path.join(context.directory,'selection-fallback')),secret:context.secret,
    serialize:JSON.stringify,deserialize:JSON.parse,now,max:2000,maxPayloadBytes:8192,maxTotalBytes:8*1024*1024}):null;
  const deviceCache=makePilotDeviceCache();
  const overflowNotices=new Map(),overflowEpoch=new Map();
  const current=()=>!closed&&context.isCurrent();
  const guarded=async()=>{if(!current())throw Error('channel_socket_replaced');await context.assertCurrent();if(!current())throw Error('channel_socket_replaced');};
  const selfIdentity=()=>{
    const self=normalizeFromJid(client.user?.id);
    return self?createHmac('sha256',context.secret).update('orion:managed-assistant-session:v1\0'+self).digest('hex'):null;
  };
  function assistantStorageFailure(){
    assistantFault='assistant_ingress_unavailable';
    const policy={code:null,reason:'ingress_storage_unavailable',action:'inspect_ingress_storage',retry:false,delayMs:null};
    health.disconnected(policy,1);close();context.onDisconnected(policy);
  }
  function ensureAssistantIngress(){
    const identity=selfIdentity();if(!identity)throw Error('assistant_ingress_identity_unavailable');
    if(assistantIngress&&assistantIdentity===identity)return assistantIngress;
    assistantIngress?.abandon();assistantIdentity=identity;
    assistantIngress=createAssistantIngress({directory:path.join(context.directory,'incoming-assistant'),secret:context.secret,
      scope:{tenant_id:context.tenantId,channel_id:context.channelId,session_digest:identity},now,
      isCurrent:()=>current()&&selfIdentity()===identity,
      serialize:value=>JSON.stringify(value,mod.BufferJSON.replacer),deserialize:value=>JSON.parse(value,mod.BufferJSON.reviver)});
    return assistantIngress;
  }
  function scheduleAssistantReplay(){
    if(!current()||assistantFault||assistantTimer||!assistantIngress?.diagnostics().pending)return;
    assistantTimer=schedule(()=>{assistantTimer=null;void replayAssistantIngress();},5000);assistantTimer?.unref?.();
  }
  async function replayAssistantIngress(){
    if(!current()||assistantFault||assistantReplaying)return;
    assistantReplaying=true;
    try{
      const journal=ensureAssistantIngress(),entries=journal.pending().filter(entry=>!assistantJobs.has(entry.id)).slice(0,8);
      if(entries.length)receive({messages:entries.map(entry=>entry.message.raw),type:'notify'},new Map(entries.map(entry=>[entry.id,entry])));
    }catch{assistantStorageFailure();}
    finally{assistantReplaying=false;scheduleAssistantReplay();}
  }
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
    if(pairAttempt!==attempt||!attempt.code||expires<=0||['paired','invalid'].includes(authState())||creds.registered===true||state==='connected')throw Error('channel_pairing_unavailable');
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
  const webVersion=await selectVersion({auto:process.env.QR_WA_VERSION_AUTO!=='0',
    bundledVersion:mod.DEFAULT_CONNECTION_CONFIG?.version,fetchVersion:mod.fetchLatestWaWebVersion});
  await guarded();health.protocolVersion(webVersion.version,webVersion.source);
  const client=makeSocket({auth,logger:silent,browser:mod.Browsers.windows('Chrome'),
    version:webVersion.version,
    ...(typeof mod.DEFAULT_CONNECTION_CONFIG?.makeSignalRepository==='function'?{
      makeSignalRepository:fenceSignalRepository(mod.DEFAULT_CONNECTION_CONFIG.makeSignalRepository,current)}:{}),
    printQRInTerminal:false,markOnlineOnConnect:false,syncFullHistory:false,userDevicesCache:deviceCache,maxMsgRetryCount:5,
    msgRetryCounterCache:context.retryCounterCache||makeBoundedCache({max:10000,ttlMs:600000,now}),
    placeholderResendCache:makeBoundedCache({max:5000,ttlMs:600000,now}),
    callOfferCache:makeBoundedCache({max:1000,ttlMs:300000,now}),
    ...proxyTransport?.socket,
    shouldSyncHistoryMessage:()=>false,getMessage:async key=>{
      const message=await readAuthorizedRetry(retryStore,key,{socket:client,isCurrent:current,assertCurrent:guarded});
      diagnostics.record('get_message',key?.id,{cached:!!message});return message;
    }});
  health.watch(client.ws);
  const outgoingPresence=makeOutgoingPresence({socket:client,isConnected:()=>state==='connected'&&current(),typingFactory,now:presenceNow,schedule,cancel});
  const close=()=>{if(closed)return;closed=true;state='stopped';qrValue=null;pairAttempt=null;cancel(pairTimer);cancel(noticeTimer);cancel(assistantTimer);assistantIngress?.abandon();abort.abort();health.stop();deviceRuntime?.close();proxyTransport?.close();outgoingPresence.close();for(const lease of typingLeases)lease.stop();typingLeases.clear();client.ev.removeAllListeners();try{client.end(undefined);}catch{}};
  const groupContacts=createDeviceGroupContacts({...deviceOptions.groupContactsOptions,client,isCurrent:current,
    isConnected:()=>state==='connected',deviceSnapshot:()=>context.deviceSnapshot?.(),schedule,cancel});
  const numberCheck=createDeviceNumberCheck({...deviceOptions.numberCheckOptions,client,isCurrent:current,
    isConnected:()=>state==='connected',deviceSnapshot:()=>context.deviceSnapshot?.(),schedule,cancel});
  const groupMutationReady=()=>{
      const runtime=deviceRuntime?.diagnostics(),readiness=health.snapshot();
      if(!runtime||!runtime.ingress||runtime.ingress_error||runtime.ingress.pending>0||runtime.ingress.blocked>0||
          ['full','unavailable'].includes(runtime.ingress.state)||readiness.ingress_storage_unavailable===true)
        throw Object.assign(Error('group_add_unavailable'),{groupAddReason:'ingress_blocked'});
      if(state!=='connected'||readiness.state!=='connected'||readiness.sync_pending===true||client.ws?.isOpen===false)
        throw Object.assign(Error('group_add_unavailable'),{groupAddReason:'device_unavailable'});
      return current();
  };
  const groupAdd=createGroupAddSocket({...deviceOptions.groupAddOptions,client,isCurrent:current,
    isConnected:()=>state==='connected',deviceSnapshot:()=>context.deviceSnapshot?.(),schedule,cancel,
    assertMutationReady:async()=>{await guarded();if(!groupMutationReady())throw Error('group_add_unavailable');},
    mutationCurrent:groupMutationReady});
  async function relayText(phone,text,{isCurrent:operationCurrent=()=>true,maxTextChars=1200,device=false,source='automation',
    diagnostic=null,assertAuthorized,onRelayAttempt,pacing=true}={}){
    await guarded();const self=normalizeFromJid(client.user?.id);
    let authorized=true;
    const sendCurrent=()=>{try{return authorized&&current()&&operationCurrent()===true&&
      (device||source==='manual'||!assistantIngress||assistantIngress.canAutomate());}catch{return false;}};
    const checkDiagnostic=()=>{
      const own=normalizeFromJid(client.user?.id);
      if(!own||!diagnostic&&phone===own)throw Error('channel_send_denied');
      if(diagnostic&&(!device||typeof assertAuthorized!=='function'||typeof onRelayAttempt!=='function'||
        !currentDiagnosticMessage(diagnostic,{phone,text,self:own,snapshot:context.deviceSnapshot?.()})))throw Error('diagnostic_message_refused');
    };
    const authorize=async()=>{
      await guarded();if(!sendCurrent())throw Error('channel_operation_superseded');checkDiagnostic();
      if(typeof assertAuthorized==='function'){try{await assertAuthorized();}catch(error){authorized=false;throw error;}if(!sendCurrent())throw Error('channel_operation_superseded');checkDiagnostic();}
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
    // Confirmed self diagnostics have their own exact current-self grant;
    // the external-recipient resolver deliberately refuses a self address.
    const proof=await resolvePilotRecipient({phone,pilotPhone:phone,enabled:!(diagnostic&&phone===self)&&context.verifiedLid?.()===true,
      socket:client,deviceCache,isCurrent:sendCurrent});await authorize();
    return outgoingPresence.run({phone,chatJid:proof.jid,isCurrent:sendCurrent,assertAuthorized:authorize,action:{type:'text',text},pacing},async({beforeSend})=>{
    await beforeSend();
    const sent=await sendStoredText({jid:proof.jid,recipientProof:proof,text,store:retryStore,freshDevices:true,isCurrent:sendCurrent,assertAuthorized:authorize,maxTextChars,preserveWhitespace:device,
      makeMessage:(jid,body)=>mod.generateWAMessage(jid,{text:body},{userJid:client.user.id,logger:silent,messageId:mod.generateMessageIDV2(client.user.id)}),
      relay:async(jid,body,options)=>{
        await authorize();
        if(state!=='connected')throw messagePreflightError('TRANSPORT_UNAVAILABLE_BEFORE_SEND');
        // Both the intended self PN and the active device/lease are checked
        // after the last async preparation, immediately before the relay.
        checkDiagnostic();onRelayAttempt?.();
        return client.relayMessage(jid,body,options);
      }});
    return {id:sent?.key?.id};
    });
  }
  async function relayChatbot(phone,action,{isCurrent:operationCurrent=()=>true,assertAuthorized,signal,
    loadAsset,prepareCarouselImage,onRelayAttempt,fetchImage}={}){
    let authorized=true;
    const sendCurrent=()=>{try{return authorized&&current()&&operationCurrent()===true&&!signal?.aborted;}catch{return false;}};
    const authorize=async()=>{await guarded();if(!sendCurrent()||typeof assertAuthorized!=='function')throw Error('chatbot_operation_superseded');try{await assertAuthorized();}catch(error){authorized=false;throw error;}if(!sendCurrent())throw Error('chatbot_operation_superseded');};
    await authorize();const self=normalizeFromJid(client.user?.id);
    if(state!=='connected')throw messagePreflightError('TRANSPORT_UNAVAILABLE_BEFORE_SEND');
    if(!context.deviceSnapshot?.()||!self||phone===self||!/^\d{8,15}$/.test(phone))throw Error('channel_send_denied');
    const exists=await client.onWhatsApp(phone);await authorize();
    if(!exists?.some(row=>row.exists&&normalizeFromJid(row.jid)===phone))throw messagePreflightError(Array.isArray(exists)&&exists.some(row=>row?.exists===false&&normalizeFromJid(row.jid)===phone)
      ?'RECIPIENT_NOT_REGISTERED':'RECIPIENT_IDENTITY_UNVERIFIED');
    const proof=await resolvePilotRecipient({phone,pilotPhone:phone,enabled:context.verifiedLid?.()===true,socket:client,deviceCache,isCurrent:sendCurrent});await authorize();
    if(action?.type==='audio')validateChatbotAsset(action.asset,'audio');
    let built;
    try{return await outgoingPresence.run({phone,chatJid:proof.jid,isCurrent:sendCurrent,assertAuthorized:authorize,action,signal},async({beforeSend})=>{
      const sent=await sendStoredProtocol({jid:proof.jid,recipientProof:proof,store:retryStore,isCurrent:sendCurrent,
        assertAuthorized:authorize,onRelayAttempt,
        onPrepared:message=>{if(built?.selectionIds)saveFallbackSelection(selectionStore,message,phone,built.selectionIds);},
        makeMessage:async jid=>{
          built=await buildChatbotContent(action,{sdk:mod,jid,loadAsset,prepareCarouselImage,fetchImage,signal,
            upload:(...args)=>client.waUploadToServer(...args),logger:silent,assertCurrent:authorize,isCurrent:sendCurrent,mediaOptions:proxyTransport?.download,
            interactiveMode:deviceOptions.interactiveMode??chatbotInteractiveMode(action,{tenantId:context.tenantId,deviceId:context.deviceSnapshot?.()?.id})});
          await beforeSend();
          return mod.generateWAMessageFromContent(jid,built.content,{userJid:client.user.id,messageId:mod.generateMessageIDV2(client.user.id),timestamp:new Date(now())});
        },
        relay:(jid,body,options)=>client.relayMessage(jid,body,options)});
      return {id:sent.key.id};
    });}finally{built?.dispose();}
  }
  if(deviceSnapshot){
    const ingressFailure=(code,backlog)=>{
      const policy={code:null,reason:'ingress_storage_unavailable',action:'inspect_ingress_storage',retry:false,delayMs:null};
      health.disconnected(policy,1);
      context.onConnectionHealth?.({...health.snapshot(),ingress:backlog||null,ingress_error:code});
      close();context.onDisconnected(policy);
    };
    let ingressJournal;
    try{
      // Explicit relink rotates auth epoch, not logical device generation.
      // Preserve old journals separately; never replay them into a new login.
      const authEpoch=path.basename(context.directory);
      if(!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(authEpoch))throw Error('invalid_device_auth_epoch');
      ingressJournal=createIngressJournal({directory:privateDirectory(path.join(context.deviceDirectory,'incoming',authEpoch)),
      secret:context.secret,now,isCurrent:current,
      serialize:value=>JSON.stringify(value,mod.BufferJSON.replacer),deserialize:value=>JSON.parse(value,mod.BufferJSON.reviver)});}
    catch(error){ingressFailure('ingress_journal_unavailable',null);throw error;}
    deviceRuntime=createDeviceRuntime(context,{client,downloadMediaMessage:mod.downloadMediaMessage,logger:silent,
      now,openedAt,schedule,cancel,downloadOptions:proxyTransport.download,
      capacity:context.deviceSendCapacity,
      sendText:(phone,text,options)=>relayText(phone,text,{...options,device:true}),sendChatbot:relayChatbot,
      isGeneratedMessage:key=>!!retryStore.get(key),
      resolveSelection:(message,phone)=>resolveFallbackSelection(selectionStore,message,phone),...deviceOptions.runtimeOptions,
      ingressJournal,onIngressError:ingressFailure});
  }
  const groupSetting=(name,fallback,max)=>{const raw=process.env[name],value=Number(raw);
    return raw!==undefined&&Number.isSafeInteger(value)&&value>=0&&value<=max?value:fallback;};
  const settleMs=groupSetting('QR_AI_GROUP_WAIT_MS',350,2000);
  const intake=makeConversationIntake({settleMs,maxGroupMs:Math.max(settleMs,groupSetting('QR_AI_GROUP_MAX_WAIT_MS',1200,5000)),
    ...intakeOptions,
    retryUnconsumedNoticeFailures:true,
    onRejected:async batch=>{const first=metadata.get(batch.messageIds[0]);if(first)context.onRejected?.({phone:first.phone});
      try{for(const id of batch.messageIds)if(assistantIngress?.active(id))assistantIngress.review(id);}catch{assistantStorageFailure();}},
    consume:async batch=>{
      const entries=batch.messages.map(item=>metadata.get(item.id)),first=entries[0];
      const ids=batch.messages.map(item=>item.id),journal=assistantIngress,identity=assistantIdentity;
      const rawCurrent=()=>current()&&batch.isCurrent()&&!!first&&selfIdentity()===identity&&entries.every(item=>item?.identity===identity&&item.phone===first.phone&&context.allowedSender(item.phone));
      const batchCurrent=()=>rawCurrent()&&!assistantFault&&journal?.canConsume(ids)===true;
      if(journal&&!await journal.waitForAdmission(ids,{isCurrent:rawCurrent}))return {state:'superseded',not_committed:true};
      if(!batchCurrent())return {state:'superseded',not_committed:true};
      await guarded();
      const outcome=await context.onMessage({phone:first.phone,message_id:batch.messages[0].id,message_ids:ids,
        text:batch.text,parts:batch.messages.map(item=>({message_id:item.id,text:item.text})),intake_revision:batch.intakeRevision,
        ...(entries.some(item=>item.event)?{envelope:entries.findLast(item=>item.event)?.event}:{})},{noticeProofs:entries.map(item=>item.proof),isCurrent:batchCurrent,
        beforeReply:value=>entries.at(-1)?.lease?.beforeReply(value)});
      if(!(outcome?.state==='superseded'&&outcome?.not_committed===true)){
        const confirmed=outcome?.accepted===true||outcome?.state==='committed_no_reply'&&outcome.not_committed===false||
          outcome?.state==='policy_skipped'&&outcome.not_committed===true;
        for(const id of ids)if(journal.active(id)){if(confirmed)journal.complete(id);else journal.review(id);}
      }
      return outcome;
    }});
  client.ev.on('creds.update',()=>{write(()=>store.set('creds',creds)).catch(()=>{diagnostics.record('auth_write_failed');close();});});
  client.ev.on('connection.update',event=>{
    if(!current())return close();
    if(event.qr){
      // A resumed identity must never drift into a new QR login. Keep the
      // encrypted account intact for inspection and require explicit action.
      if(context.resumeOnly===true||['paired','invalid'].includes(authState())||(!pairAttempt&&authState()==='pending')){
        const policy={code:null,reason:'auth_state_unavailable',action:'inspect_session_state',retry:false,delayMs:null};
        health.disconnected(policy,1);close();context.onDisconnected(policy);return;
      }
      expirePairing();qrValue=event.qr;qrAt=now();if(!pairAttempt)state='qr_ready';
    }
    if(event.connection==='open'){
      // Restore readiness synchronously before publishing a connected state
      // or permitting an unrelated automatic send. New links create no queue.
      if(!deviceRuntime&&fs.existsSync(path.join(context.directory,'incoming-assistant'))){
        try{ensureAssistantIngress();}catch{assistantStorageFailure();return;}
      }
      health.connected();
      state='connected';qrValue=null;pairAttempt=null;cancel(pairTimer);pairTimer=null;
      if(!opened){opened=true;
        void Promise.resolve().then(()=>context.onConnected()).then(()=>{
          if(deviceRuntime){void deviceRuntime.onConnected().catch(()=>{});return;}
          if(assistantIngress)void replayAssistantIngress();
          if(!notifyNewLink||!current())return;
          noticeTimer=schedule(async()=>{
            try{
              await queue;await guarded();newLinkNotice.pending=false;
              await sendConnectionNoticeOnce({authDir:path.join(context.directory,'auth'),client,isCurrent:current,
                sendText:async(jid,text)=>{
                  await guarded();
                  const phone=normalizeFromJid(jid),ownCurrent=()=>current()&&normalizeFromJid(client.user?.id)===phone;
                  return outgoingPresence.run({phone,chatJid:jid,isCurrent:ownCurrent,assertAuthorized:guarded,action:{type:'text',text}},async({beforeSend})=>{
                  await beforeSend();return sendStoredText({jid,text,store:retryStore,freshDevices:true,isCurrent:ownCurrent,assertAuthorized:guarded,
                    makeMessage:(to,body)=>mod.generateWAMessage(to,{text:body},{userJid:client.user.id,logger:silent,messageId:mod.generateMessageIDV2(client.user.id)}),
                    relay:async(to,body,options)=>{await guarded();return client.relayMessage(to,body,options);}});
                  });
                }});
            }catch{/* Preserve the receipt; never retry an ambiguous notice. */}
          },2500);noticeTimer?.unref?.();
        }).catch(()=>{close();context.onDisconnected('disconnected');});
      }
    }
    if(event.connection==='close'){
      const code=event.lastDisconnect?.error?.output?.statusCode;
      const policy=disconnectPolicy(code);health.disconnected(policy,1,disconnectEvidence(event.lastDisconnect?.error));
      close();context.onDisconnected(policy);
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
  function receive({messages=[],type},replayEntries=new Map()){
    if(deviceRuntime){deviceRuntime.receive({messages,type});return;}
    for(const message of messages){
      const id=message.key?.id,at=timestampMs(message.messageTimestamp),replayEntry=replayEntries.get(id),sourceType=replayEntry?.message.type||type;
      if(!current()||message.key?.fromMe||typeof id!=='string'||!/^[a-zA-Z0-9._:-]{1,160}$/.test(id)||seen.has(id)||
          !shouldIngestUpsert(sourceType,message.messageTimestamp,now())||!at||at<now()-900000||at>now()+60000||
          (!replayEntry&&sourceType!=='append'&&at<openedAt-1000))continue;
       const normalized=normalizeInbound(message),text=normalized.text,media=message.message?.audioMessage||message.message?.ephemeralMessage?.message?.audioMessage;
       if(normalized.silent)continue;
      const peer=String(message.key.remoteJid||'');
      if(!/^\d{8,20}(?::\d+)?@(s\.whatsapp\.net|lid)$/.test(peer))continue;
      const knownPhone=normalizeFromJid(peer),self=normalizeFromJid(client.user?.id);
      if(knownPhone&&(!context.allowedSender(knownPhone)||knownPhone===self))continue;
      let journal,admission,identity;
      try{
        journal=ensureAssistantIngress();identity=assistantIdentity;
        if(assistantJobs.has(id))continue;
        if(replayEntry)admission=replayEntry;
        else{const captured=journal.append(message,sourceType,at);admission=captured.entry;
          if(!captured.fresh){scheduleAssistantReplay();continue;}}
        if(admission.state!=='pending'||!journal.pending().some(entry=>entry.id===id))continue;
        // A bounded safety lane still registers handoff/opt-out/corrections
        // during a normal-work burst. It grants no domain or send authority.
        const priority=priorityText(text)||normalized.event.control||['edited','deleted'].includes(normalized.event.kind)||
          typeof text==='string'&&/\b(?:espera|aguarde|pera|corrigir|corrigindo|n[aã]o\s+confirme)\b/i.test(text);
        if(pending>=32&&(!priority||pending>=40)){scheduleAssistantReplay();continue;}
      }catch{assistantStorageFailure();return;}
      // Only durable pending admission may acquire a volatile seen/job marker.
      seen.add(id);if(seen.size>2000)seen.delete(seen.values().next().value);assistantJobs.add(id);
      const ingressEpoch=overflowEpoch.get(peer)||0;
      // Only identity resolution is ordered here. Never wait for ASR or an AI
      // reply before registering a newer authenticated message's revision.
      pending++;
      const identityTask=identityQueue.catch(()=>{}).then(async()=>{
        await guarded();const phone=await resolveMessagePhone(message.key,client);await guarded();
        const self=normalizeFromJid(client.user?.id);
        if(!current()||selfIdentity()!==identity)return null;
        if(!phone)throw Error('assistant_identity_unresolved');
        if(!self||!context.allowedSender(phone)||phone===self){journal.exclude(id);return null;}
         const entry={phone,identity,...(normalized.special?{event:normalized.event}:{})};metadata.set(id,entry);
        const entryCurrent=()=>current()&&selfIdentity()===identity&&context.allowedSender(phone)&&(overflowEpoch.get(peer)||0)===ingressEpoch;
         // Even rejected input registers its revision before consuming more
         // work. It revokes stale inference and records handoff/opt-out in the
         // journal. The rejected prefix never reaches the domain consumer.
         const proofTask=Promise.resolve(context.onInbound({phone,message_id:id,text:text||'',kind:media?'audio':text?'text':'unsupported',
           ...(normalized.special?{envelope:normalized.event}:{})})).then(async proof=>{
             await guarded();if(!entryCurrent()||!Number.isSafeInteger(proof?.revision)||proof.revision<1)throw Error('assistant_notice_unconfirmed');
             journal.admitted(id);entry.proof=proof;return proof;
           });
         void proofTask.catch(()=>{});
        const task=intake.submit({conversationKey:context.channelId+':'+phone,messageId:id,isCurrent:entryCurrent,
           notice:async()=>{const proof=await proofTask;await guarded();return proof;},
          prepare:async({signal})=>{
            await guarded();
            if(!entryCurrent())return {skip:true};
            if(context.assistantEnabled?.()===false||(!normalized.special&&!text?.trim()&&context.audioEnabled?.()!==true)){
              journal.exclude(id);return {skip:true};
            }
            if(journal.diagnostics().blocked){journal.review(id,'policy_denied');return {skip:true};}
            // This persisted boundary forbids any ASR/domain replay following
            // a crash or an uncertain result. Only the live attempt can finish.
            journal.begin([id]);
            entry.lease=outgoingPresence.begin({phone,chatJid:peer,isCurrent:()=>entryCurrent()&&context.assistantEnabled?.()!==false});typingLeases.add(entry.lease);
             if(normalized.special)return {text:text||'[Evento de mensagem recebido]'};
             let body=text;
            if(!body?.trim())body=await withAssistantAudio(message,{client,downloadMediaMessage:mod.downloadMediaMessage,logger:silent,
              signal:AbortSignal.any([abort.signal,signal])},async({buffer,mediaType,signal})=>{
                await guarded();return context.transcribe({phone,buffer,mediaType,signal});});
            await guarded();if(signal.aborted)return {skip:true};return {text:body};
          }});
        // The identity queue returns immediately; only this message's cleanup
        // waits for ordered consumption (including an explicit zero-write carry).
        void task.then(result=>{
          if(!current()||selfIdentity()!==identity){journal.abandon();return;}
          try{
            if(journal.active(id))journal.review(id);
            else if(['revision_failed','overloaded','duplicate'].includes(result?.state))seen.delete(id);
          }catch{assistantStorageFailure();}
        },()=>{if(current()&&selfIdentity()===identity)try{if(journal.active(id))journal.review(id);}catch{assistantStorageFailure();}})
          .finally(()=>{entry.lease?.stop();typingLeases.delete(entry.lease);metadata.delete(id);pending--;assistantJobs.delete(id);scheduleAssistantReplay();});
        return true;
      });
      identityQueue=identityTask;identityTask.then(accepted=>{if(!accepted){pending--;assistantJobs.delete(id);seen.delete(id);scheduleAssistantReplay();}},()=>{
        pending--;assistantJobs.delete(id);seen.delete(id);scheduleAssistantReplay();
      });
    }
  }
  client.ev.on('messages.upsert',receive);
  return {close,groupContacts,groupAdd,numberCheck,status:()=>{expirePairing();return state;},
    connectionHealth:()=>health.snapshot(),
    ingressDiagnostics,
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
    // Format support is separate from connection readiness or send authority.
    supportsUrlButton:()=>true,
    supportsPairing:()=>current()&&context.resumeOnly!==true&&authState()==='unlinked'&&state!=='connected'&&typeof client.requestPairingCode==='function',
    qr:()=>{expirePairing();return !pairAttempt&&authState()==='unlinked'&&context.resumeOnly!==true&&['qr_ready','pair_expired'].includes(state)&&freshQR(qrAt,now())?
      {value:qrValue,expires_in_ms:Math.max(0,45000-(now()-qrAt))}:null;},
    async pair(phone){
      await guarded();expirePairing();
      if(typeof phone!=='string'||phone!==phone.trim()||!/^[1-9]\d{7,14}$/.test(phone)||['paired','invalid'].includes(authState())||creds.registered===true||state==='connected'||
          context.resumeOnly===true||typeof client.requestPairingCode!=='function')throw Error('channel_pairing_unavailable');
      if(pairAttempt){
        if(pairAttempt.phone!==phone)throw Error('channel_pairing_in_progress');
        if(now()>=pairAttempt.expiresAt)throw Error('channel_pairing_expired');
        if(pairAttempt.pending)return pairAttempt.promise;
        return pairingResult(pairAttempt);
      }
      if(authState()!=='unlinked')throw Error('channel_pairing_unavailable');
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
