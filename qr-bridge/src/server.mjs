import {makeBoundedCache} from './bounded-cache.mjs';
import {readAuthorizedRetry} from './retry-identity.mjs';
import {useDurableAuthState} from './durable-auth.mjs';
import {fenceSignalRepository} from './signal-fence.mjs';
import {makeConnectionHealth,disconnectPolicy,sustainedRecovery} from './connection-health.mjs';
import {disconnectEvidence} from './disconnect-evidence.mjs';
import {selectWaWebVersion} from './wa-version-policy.mjs';
import {classifyPairedAuth} from './paired-auth-state.mjs';
import {normalizeInbound,updateEvent,callEvent,safetyControl,queueControl} from './inbound-event.mjs';
/**
 * Orion Linked Device — aparelho de teste único, transporte não oficial.
 * Apenas mensagens privadas recebidas após conexão; nada de histórico antigo,
 * mídia, grupos, auto-respostas ou envios em massa.
 * Mensagens recentes ficam apenas na RAM; sessão Baileys cifrada no volume.
 */
import http from "node:http";
import {databaseBackup} from './database-backup-store.mjs';
import path, {join as joinPath} from "node:path";
import fs from "node:fs";
import {timingSafeEqual,randomUUID,createHmac} from "node:crypto";
import makeWASocket,{DisconnectReason,Browsers,DEFAULT_CONNECTION_CONFIG,Curve,proto,initAuthCreds,fetchLatestWaWebVersion,makeCacheableSignalKeyStore,generateWAMessage,generateWAMessageFromContent,prepareWAMessageMedia,generateMessageIDV2,BufferJSON,downloadMediaMessage} from "@whiskeysockets/baileys";
import QRCode from "qrcode";
import {validPhone,normalizeFromJid,extractText,resolveMessagePhone,freshQR,formatPairingCode,connectionPhase,reconnectPlan} from "./core.mjs";
import {sendConnectionNoticeOnce,noticeState} from "./connection-alert.mjs";
import {makeMessageRetryStore,deliveryFromAck,advanceDelivery} from "./message-retry-store.mjs";
import {makeEncryptedRetryStore} from "./encrypted-retry-store.mjs";
import {sendStoredText,sendStoredProtocol} from "./reliable-send.mjs";
import {buildChatbotContent} from './chatbot-message.mjs';
import {createNativeTestCanary,loadNativeTestAsset} from './native-test-canary.mjs';
import {makeSendCapacity} from "./send-capacity.mjs";
import {makeConversationIntake} from "./conversation-intake.mjs";
import {createAssistantIngress} from './assistant-ingress.mjs';
import {collectIngressReviewBody} from './assistant-ingress-review.mjs';
import {verifyIngressReviewAssertion} from './ingress-review-auth.mjs';
import {withIngressHealth} from './ingress-health.mjs';
import {makeOutgoingPresence} from './outgoing-presence.mjs';
import {inspectLookup,inspectDelivery} from "./recipient-diagnostics.mjs";
import {resolveTransactionalRecipient} from "./transactional-recipient.mjs";
import {makeDurableInbox} from "./durable-inbox.mjs";
import {shouldIngestUpsert,timestampMs,ingestionHealth} from "./linked-session-health.mjs";
import {makeConnectedStore} from "./connected-store.mjs";
import {makeContactNames,installContactNameEvents,requestedContactPhones,requestedContactQuery} from './contact-names.mjs';
import {makeContactNameSync} from './contact-name-forward.mjs';
import {transcriberConfig,cleanPictureUrl,transcribeAudio,handleCallOffers} from "./connected-features.mjs";
import {protectSignalSessionLogs} from "./signal-log-safety.mjs";
import {forwardAssistant,registerAssistantIngress,acknowledgeAssistantPresentation} from "./ai-forward.mjs";
import {createAssistantPresentationStore,relayAssistantPresentation,assistantNativeMode,ASSISTANT_SELECTION_TEXT} from './assistant-presentation.mjs';
import {isBookingProtocolMessage,fetchBookingProtocolConfirmation,resolveBookingSenderPhone} from "./appointment-confirmation.mjs";
import {makeBookingIntakeGuard} from './booking-intake-guard.mjs';
import {allowAssistantRecipient,allowAssistantAudio,pilotNumber} from "./ai-pilot.mjs";
import {deliveryPolicy,requiresPreRelayCache} from "./pilot-send-policy.mjs";
import {shouldCacheLinkedSelfText} from "./retry-capture-policy.mjs";
import {resolvePilotRecipient,makePilotDeviceCache} from "./pilot-recipient.mjs";
import {transcribeAssistantAudio} from "./ai-audio.mjs";
import {makeTransportDiagnostics,receiptDiagnosticDetails,usyncDeviceDomainCounts,encryptionTargetDetails,observeSignalRepository} from "./transport-diagnostics.mjs";
import {backupAuth} from "./auth-backup.mjs";
import {createChannelRoutes} from './channel-routes.mjs';
import {createBaileysChannelSession} from './channel-session.mjs';
import {createLegacyCentral} from './legacy-central.mjs';
import {testReplyFor} from './trigger-test.mjs';
import {makeTestFallbackText,sendTestCloudCta,selectTestCtaMode} from './test-url-button.mjs';
import {runConsentedPairSmoke,SMOKE_ID,APPOINTMENT_QA_ID} from './consented-live-qa.mjs';

const legacyCentral=createLegacyCentral({
  start:(id,key)=>channelRoutes.adopt(id,key),send:(id,key,payload)=>channelRoutes.send(id,key,payload),
  sessionDigest:()=>status==='connected'?createHmac('sha256',KEY).update('orion:preserved-primary-session:v1\0'+pairedNumber).digest('hex'):null,
  status:()=>status,rawSend:(phone,text,options)=>sendLegacyRaw(phone,text,options),
  onTransportLeaseLost:()=>stopPrimaryTransport('lease_lost'),publicHealth:()=>primaryHealth.snapshot(),
  outgoingReady:source=>source==='manual'||primaryIngressCanAutomate?.()===true});
const channelRoutes=createChannelRoutes({sessionFactory:context=>context.legacyTransport?
  legacyCentral.attach(context):createBaileysChannelSession(context)});

const PORT=Number(process.env.PORT || 3000);
const SESSION_DIR=process.env.QR_SESSION_DIR || "/data/auth";
const KEY=process.env.QR_BRIDGE_TOKEN || "";
const transport=makeTransportDiagnostics({secret:KEY,
  emit:event=>console.info('Orion transport '+JSON.stringify(event))});
const logger=transport.logger;
let primaryIngressDiagnostics=null,primaryIngressCanAutomate=null,primaryIngressReview=null,stopPrimaryIngress=()=>{};
const primaryHealth=makeConnectionHealth({emit:event=>console.info(JSON.stringify(event)),
  readiness:value=>withIngressHealth(value,primaryIngressDiagnostics?.()),
  isSyncPending:()=>socket?.ev?.isBuffering?.()===true,onStale:reason=>{
  if(stopping||status!=='connected'||!socket)return;
  try{socket.end(Object.assign(Error(reason),{output:{statusCode:408}}));}
  catch{stopPrimaryTransport(reason);}
}});
// libsignal-node 6.x logs full SessionEntry objects including ratchet keys.
// Redact those exact calls before any socket/session operation; keep normal logs.
protectSignalSessionLogs();
// Baileys 6.7.24 accepts CacheStore here. A bounded TTL protects against
// stale Signal retry state; it does NOT change long-lived auth credentials.
const msgRetryCounterCache=makeBoundedCache({max:10000,ttlMs:600000});
if(Buffer.byteLength(KEY)<32 || !SESSION_DIR.startsWith("/data/")){
  throw Error("Configuração segura do bridge não encontrada");
}
fs.mkdirSync(SESSION_DIR,{recursive:true,mode:0o700});
const ACTIVE_MARKER=SESSION_DIR+".active";
let authDir=SESSION_DIR;
try {
  const candidate=fs.readFileSync(ACTIVE_MARKER,"utf8").trim();
  if(candidate.startsWith(SESSION_DIR+"-renew-")&&candidate.startsWith("/data/"))authDir=candidate;
} catch {}
let authBackup={state:'pending_owner'},authBackupDirectory=null;
const socketDeviceCaches=new WeakMap();
const socketOutgoingPresence=new WeakMap();
let newLinkNoticeAuthDir=null;
let stopping=false,resumeTimer=null;
let socket=null,generation=0,timer=null,attempts=0,sessionRecoveryAttempts=0,status="starting",
  connectStartedAt=Date.now(),lastQrAt=0,lastDisconnectCode=null,everConnected=false,
  pairingRequestedForGeneration=-1, pairPhone="",saveQueue=Promise.resolve(),
  notifiedGeneration=-1,
  qr="",qrAt=0,lastError=null,pairedNumber="",pairingCode="",pairAt=0,requesting=false;
function stopPrimaryTransport(reason){
  stopPrimaryIngress();
  ++generation;clearTimeout(timer);timer=null;
  pauseContactNames();
  const previous=socket;socket=null;
  socketOutgoingPresence.get(previous||{})?.close();
  qr='';qrAt=0;pairingCode='';pairAt=0;connectedSince=0;
  status='stopped';
  lastError=reason==='lease_lost'?'Aguardando a responsabilidade exclusiva pela sessão do WhatsApp.':null;
  try{previous?.ev?.removeAllListeners();}catch{}
  try{previous?.end(undefined);}catch{}
  primaryHealth.stop(reason);
}
let durableInbox=makeDurableInbox({directory:authDir,secret:KEY});
let connectedStore=makeConnectedStore({directory:authDir,secret:KEY});
let contactNames=makeContactNames({directory:authDir,secret:KEY});
let contactNameEvents=null,contactNameSync=null;
function pauseContactNames({purge=false}={}){
  contactNameEvents?.stop();contactNameEvents=null;
  contactNameSync?.stop();contactNameSync=null;
  if(purge)contactNames.purge();else contactNames.suspend();
}
let connectedProfile={name:null,photoUrl:null,updatedAt:null},profileFetchedAt=0,lastRestartAt=0;
const repliedToCallers=new Map();
const recentCallOffers=new Map();
const assistantQueues=new Map();
const assistantEnabled=()=>process.env.QR_AI_ENABLED==="1"&&!!process.env.QR_AI_API_URL;
const assistantAudioEnabled=()=>assistantEnabled()&&process.env.QR_AI_AUDIO_ENABLED==="1";
const callDiagnostics={
  eventsObserved:0,offersSeen:0,rejected:0,rejectFailed:0,replied:0,replyFailed:0,
  replyNoPhone:0,unresolvedWithAllowlist:0,duplicatesSkipped:0,
  allowedSkipped:0,replyCooldown:0
};
async function refreshConnectedProfile(client){
  if(!client?.user?.id || Date.now()-profileFetchedAt<60000)return;
  profileFetchedAt=Date.now();
  const display=typeof client.user.name==="string"?client.user.name.trim().slice(0,90):"";
  let photoUrl=null;
  try{photoUrl=cleanPictureUrl(await client.profilePictureUrl(client.user.id,"image"))}catch{}
  if(socket===client && status==="connected")
    connectedProfile={name:display||null,photoUrl,updatedAt:Date.now()};
}
const messages=durableInbox.load();
const seen=new Set(messages.map(x=>x.id)),sendCapacity=makeSendCapacity(),MAX=500;
let connectedSince=0,lastInboundAt=0,pendingNotificationsReceived=false,inboundSinceConnect=false,
  recentAppends=0;
function persistInbox(){
  if(!durableInbox.save(messages))console.warn("Orion QR: caixa de mensagens não persistida (sem dados pessoais)");
}
const retryMessages=makeMessageRetryStore();
let encryptedRetry=makeEncryptedRetryStore({
  directory:path.join(authDir,"retry-encrypted"),
  secret:KEY,
  serialize:obj=>JSON.stringify(obj,BufferJSON.replacer),
  deserialize:str=>JSON.parse(str,BufferJSON.reviver)
});
const retryStore={
  entry:key=>retryMessages.entry(key)||encryptedRetry.entry(key),
  save(key,message){
    if(stopping||!legacyCentral.transportCurrent())return false;
    const memory=retryMessages.save(key,message);
    const encrypted=encryptedRetry.save(key,message);
    return memory && encrypted;
  },
  get(key){
    const cached=retryMessages.get(key);
    if(cached)return cached;
    const persisted=encryptedRetry.get(key);
    // Never bind a legacy cache entry to untrusted caller-supplied addressing.
    const entry=encryptedRetry.entry(key);
    if(persisted&&entry?.identity?.peer)retryMessages.save({id:entry.identity.id,remoteJid:entry.identity.peer,fromMe:entry.identity.fromMe},persisted);
    return persisted;
  }
};
const retryDiagnostics={
  cacheHits:0,cacheMisses:0,cacheMissTracked:0,cacheMissUntracked:0,
  persistedSelfTexts:0,persistFailures:0,prepared:0,relayAccepted:0,
  receiptUpdates:0,delivered:0,read:0,lastDeliveryDelayMs:null
};
const deliveryReceipts=new Map();
const deliveryStart=new Map();
for(const row of messages){
  if(row.direction==="out" && row.id.startsWith("out:")){
    deliveryReceipts.set(row.id.slice(4),row.delivery||"pending");
  }
}

function pushMessage(item){
  if(seen.has(item.id))return;
  seen.add(item.id);messages.push(item);
  try{connectedStore.record(item.id,item.at)}catch{
    console.warn("Orion QR: estatísticas indisponíveis; nenhuma contagem estimada");
  }
  while(messages.length>MAX){const old=messages.shift();seen.delete(old.id)}
  persistInbox();
}
function visibleStatus(){
  if(status==='connected'&&socket?.ws&&socket.ws.isOpen===false)return 'degraded';
  if(status==='connected'&&['degraded','needs_attention'].includes(primaryHealth.snapshot().state))return primaryHealth.snapshot().state;
  if(status==="pairing"&&pairAt>0&&Date.now()-pairAt>=90000)
    return "pair_expired";
  return connectionPhase(status,qrAt,connectStartedAt);
}
function stats(){
  const phase=visibleStatus();
  return {status:phase,hasQr:phase==="qr_ready"&&!!qr&&freshQR(qrAt),
    connection_health:primaryHealth.snapshot(),
    lastError,disconnectCode:lastDisconnectCode,
    waitingSeconds:Math.floor((Date.now()-connectStartedAt)/1000),
    phone:status==="connected"?(pairedNumber||null):null,
    phone_verified:status==="connected"&&Boolean(pairedNumber),
    capabilities:{pair:typeof socket?.requestPairingCode==='function'},
    connectionType:"unofficial_linked_device",messagesEnabled:status==="connected",
    retainedMessages:messages.length,paired:!!pairingCode,
    connectionNotice:process.env.ORION_NOTIFY_SELF_ON_LINK==="1" ? noticeState(authDir) : "disabled",
    deliveryDiagnostics:{...retryDiagnostics},
    transportDiagnostics:transport.snapshot(),
    transportVersion:"verified-lid-pilot-v1",
    buildCommit:/^[a-f0-9]{40}$/.test(process.env.RAILWAY_GIT_COMMIT_SHA||'')?
      process.env.RAILWAY_GIT_COMMIT_SHA:null,
    authBackup,
    transportConfig:{reliable:process.env.QR_RELIABLE_SEND_V2==="1",
      pilotAddressing:process.env.QR_PILOT_USE_VERIFIED_LID==='1'?'verified_lid':'pn',
      freshDevices:process.env.QR_FORCE_FRESH_DEVICES==="1",
      autoVersion:process.env.QR_WA_VERSION_AUTO!=="0",pilotConfigured:!!pilotNumber(),
      audio:assistantAudioEnabled(),generation},
    reliableSendEnabled:process.env.QR_RELIABLE_SEND_V2==="1",
    primaryPhoneOnlineRequired:false,automaticRepliesEnabled:assistantEnabled()&&
      (connectedStore.settings().chatbot||Boolean(pilotNumber())),
    inboxPersistence:durableInbox.health(),
    retainedMessages:messages.length,
    ingestion:ingestionHealth({connected:status==="connected",openAt:connectedSince,
      pendingNotifications:pendingNotificationsReceived,hasCurrentMessages:inboundSinceConnect}),
    connectedSince:status==="connected"?connectedSince:null,
    lastInboundAt:lastInboundAt||null,
    recentBufferedMessages:recentAppends,
    profile:status==="connected"?connectedProfile:null,
    messageStats:connectedStore.stats(),
    featureSettings:status==="connected"?connectedStore.settings():null,
    featureCapabilities:{chatbot:assistantEnabled(),transcription:!!transcriberConfig()||assistantAudioEnabled(),
      callBlocking:typeof socket?.rejectCall==="function"},
    callDiagnostics:{...callDiagnostics},
    controlStorage:connectedStore.health(),contactNameStorage:contactNames.health()};
}
function auth(req){
  // A blank or short secret must NEVER authorize a blank header.
  if(typeof KEY!=="string"||Buffer.byteLength(KEY,"utf8")<32||Buffer.byteLength(KEY,"utf8")>256)return false;
  const header=req.headers["x-orion-bridge-key"];
  if(typeof header!=="string")return false;
  const input=Buffer.from(header,"utf8"),expected=Buffer.from(KEY,"utf8");
  return input.length===expected.length&&timingSafeEqual(input,expected);
}
function reply(res,code,data){
  res.writeHead(code,{"Content-Type":"application/json; charset=utf-8",
    "Cache-Control":"no-store","Pragma":"no-cache","X-Content-Type-Options":"nosniff",
    "Referrer-Policy":"no-referrer"});
  res.end(JSON.stringify(data));
}
function collectBody(req){
  return new Promise((resolve,reject)=>{
    let size=0;let content="";
    req.on("data",part=>{
      size+=part.length;
      if(size>3000){reject(Error("Tamanho excedido"));req.destroy();return}
      content+=part.toString("utf8");
    });
    req.on("end",()=>{try{resolve(JSON.parse(content))}catch{reject(Error("JSON inválido"))}});
    req.on("error",reject);
  });
}
function installListeners(client,mine,saveCreds,notifyNewLink=false,authRegistrationId=null){
  // Capture the store for this socket. Old callbacks may never write into a
  // replacement account's registry, including after async PN resolution.
  const namesStore=contactNames;
  const namesCurrent=()=>mine===generation&&socket===client&&
    !['logged_out','pair_failed','disconnecting'].includes(status);
  const namesSync=makeContactNameSync({store:namesStore,apiUrl:process.env.QR_AI_API_URL,token:KEY,
    isCurrent:()=>namesCurrent()&&status==='connected'});
  const namesEvents=installContactNameEvents({client,store:namesStore,isCurrent:namesCurrent,
    onChange:()=>namesSync.changed()});
  contactNameSync=namesSync;contactNameEvents=namesEvents;
  client.ws.on('CB:message',node=>{
    if(mine!==generation)return;
    transport.record('socket_message',node.attrs?.id,{
      kind:String(node.attrs?.from||'').endsWith('@lid')?'lid':'other',
      hasPn:!!node.attrs?.sender_pn,hasLid:!!node.attrs?.sender_lid});
  });
  client.ws.on('CB:receipt',node=>{
    if(mine!==generation)return;
    transport.record('socket_receipt',node.attrs?.id,{
      type:node.attrs?.type||'delivery',...receiptDiagnosticDetails(node,client.user)});
    if(node.attrs?.type==='retry'&&node.attrs?.id)countRetryRequest(node.attrs.id);
  });
  client.ws.on('CB:iq,,usync',node=>{
    if(mine!==generation)return;
    const domains=usyncDeviceDomainCounts(node,client.user);
    if(domains)transport.record('usync_device_domains',node.attrs?.id,domains);
  });
  // WhatsApp often requests reconnect right after a successful pair (515).
  // Persist auth state before starting the replacement socket.
  client.ev.on("creds.update",()=>{
    transport.record('creds_update');
    if(mine!==generation)return;
    try{saveCreds();saveQueue=Promise.resolve();}
    catch{
      lastError="Falha ao salvar sessão WhatsApp. Envios interrompidos; credenciais não foram reiniciadas.";
      console.warn("Orion QR: falha ao salvar credenciais (dados omitidos)");
    }
  });
  client.ev.on("messages.update",updates=>{
    if(mine!==generation)return;
    for(const item of updates||[]){
      const id=item?.key?.id;
      if(!id || !item?.key?.fromMe)continue;
      const next=deliveryFromAck(item.update?.status);
      if(!next)continue;
      transport.record('receipt_update',id,{status:next});
      if(!deliveryReceipts.has(id))continue;
      const prev=deliveryReceipts.get(id);
      const after=advanceDelivery(prev,next);
      deliveryReceipts.set(id,after);
      retryDiagnostics.receiptUpdates++;
      if(["delivered","read","played"].includes(after)){
        if(!["delivered","read","played"].includes(prev))retryDiagnostics.delivered++;
        if(["read","played"].includes(after) && !["read","played"].includes(prev))retryDiagnostics.read++;
        const started=deliveryStart.get(id);
        if(started && Number.isFinite(started))retryDiagnostics.lastDeliveryDelayMs=Date.now()-started;
        deliveryStart.delete(id);
      }
      const found=messages.find(x=>x.id==="out:"+id);
      if(found){found.delivery=after;persistInbox();}
      if(legacyCentral.configured()&&['delivered','read','played'].includes(after))
        void legacyCentral.receipt(id,after==='played'?'read':after).catch(()=>{});
    }
  });
  client.ev.on("call",async calls=>{
    if(mine!==generation||status!=="connected")return;
    // Distinguishes missing Baileys call events from disabled/restricted blocking.
    callDiagnostics.eventsObserved+=(Array.isArray(calls)?calls.length:0);
    try{
      const actions=await handleCallOffers(calls,{
        socket:client,settings:connectedStore.settings(),replySent:repliedToCallers,
        recentCalls:recentCallOffers,diagnostics:callDiagnostics,
        sendReply:(phone,body)=>send(phone,body)
      });
      if(actions.length)console.info("Orion QR: tratamento de chamadas: "+
        actions.join(",")+" (números e identificadores omitidos)");
    }catch{console.warn("Orion QR: falha ao processar controle de chamada (dados omitidos)")}
  });
  const inboundMetadata=new Map();
  const handledBookingProtocols=new Set();
  const bookingAdmission=makeBookingIntakeGuard();
  const intakeCurrent=()=>mine===generation&&socket===client&&status==='connected';
  let assistantPresentationStore;
  const typingPresence={...makeOutgoingPresence({socket:client,isConnected:()=>intakeCurrent()&&legacyCentral.transportCurrent()}),
    assistantPresentations(){
      if(!Number.isSafeInteger(authRegistrationId)||authRegistrationId<1)throw Error('assistant_presentation_identity_unavailable');
      const scope=legacyCentral.ingressScope?.();
      return assistantPresentationStore||(assistantPresentationStore=createAssistantPresentationStore({
        directory:path.join(authDir,'assistant-presentations'),secret:KEY,
        scope:{tenant:scope?.tenant_id??null,channel:scope?.channel_id??null,self:normalizeFromJid(client.user?.id),registration:authRegistrationId}}));
    }};
  socketOutgoingPresence.set(client,typingPresence);
  const assistantSelectionDiagnostic=(phone,id)=>(category,reference=id)=>{
    try{const scope=legacyCentral.ingressScope?.();
      if(assistantNativeMode({tenantId:scope?.tenant_id,channelId:scope?.channel_id,phone,env:process.env})==='native')
        transport.record('assistant_selection',reference,{category});
    }catch{/* Observability cannot alter inbound processing. */}
  };
  const nativeCanary=createNativeTestCanary({directory:authDir,secret:KEY,env:process.env,
    isCurrent:()=>intakeCurrent()&&legacyCentral.transportCurrent(),
    isReady:()=>!ingressFault&&(!assistantIngress||assistantIngress.canAutomate()),
    assertCurrent:()=>legacyCentral.assertTransportCurrent(),allowedSender:phone=>legacyCentral.allowed(phone),
    getScope:()=>{const value=legacyCentral.nativeTestScope();return value?{...value,native_generation:mine}:null;},
    resolvePhone:key=>resolveMessagePhone(key,client),isBusy:()=>inboundIntake.pending()>0||assistantQueues.size>0,
    send:(phone,action,options)=>sendLegacyProtocol(phone,action,options)});
  let assistantIngress=null,ingressIdentity=null,ingressFault=null,ingressTimer=null,replayRunning=false;
  let opaqueAdmissionAt=Date.now(),opaqueAdmissionCount=0;
  const ingressDiagnostics=()=>ingressFault?{state:'unavailable',error_code:'assistant_ingress_unavailable',
    pending:assistantIngress?.diagnostics().pending||0,blocked:assistantIngress?.diagnostics().blocked||0}:
    assistantIngress?.diagnostics();
  if(legacyCentral.configured()){
    primaryIngressDiagnostics=ingressDiagnostics;
    primaryIngressCanAutomate=()=>!ingressFault&&assistantIngress?.canAutomate()===true;
  }
  const reviewPrimaryIngress=async(body,authority)=>{
    // MASTER is also the adopted PRIMARY credential. The signed assertion
    // binds the platform-owner proxy audit; it is not a second secret boundary.
    if(!assistantIngress||ingressFault||!intakeCurrent()||!legacyCentral.configured())
      throw Object.assign(Error('ingress_review_not_ready'),{status:409,code:'ingress_review_not_ready'});
    return assistantIngress.operatorReview(body,{authority,isCurrent:()=>intakeCurrent()&&legacyCentral.transportCurrent(),
      assertCurrent:()=>legacyCentral.assertTransportCurrent(),getScope:()=>{
        const scope=legacyCentral.nativeTestScope();
        return scope&&Number.isSafeInteger(authRegistrationId)&&authRegistrationId>=1?{...scope,
          auth_epoch:createHmac('sha256',KEY).update('orion:assistant-ingress-auth-epoch:v1\0'+scope.session_digest+'\0'+authRegistrationId).digest('hex')}:null;
      },
      // Only stored authenticated PN hints are used. No network lookup, guessed
      // LID, display name, or arbitrary target supplied by the operator body.
      resolvePhone:key=>resolveMessagePhone(key,null),allowedSender:phone=>legacyCentral.allowed(phone)});
  };
  if(legacyCentral.configured())primaryIngressReview=reviewPrimaryIngress;
  stopPrimaryIngress=()=>{clearTimeout(ingressTimer);ingressTimer=null;assistantIngress?.abandon();typingPresence.close();
    if(primaryIngressReview===reviewPrimaryIngress)primaryIngressReview=null;};
  function ensureAssistantIngress(){
    if(!legacyCentral.configured())return null;
    const scope=legacyCentral.ingressScope();
    if(!scope)throw Error('assistant_ingress_scope_unavailable');
    const identity=JSON.stringify([scope.tenant_id,scope.channel_id,scope.session_digest]);
    if(assistantIngress&&identity===ingressIdentity)return assistantIngress;
    assistantIngress?.abandon();ingressIdentity=identity;
    const current=()=>intakeCurrent()&&JSON.stringify((()=>{const value=legacyCentral.ingressScope();
      return value?[value.tenant_id,value.channel_id,value.session_digest]:null;})())===identity;
    assistantIngress=createAssistantIngress({directory:path.join(authDir,'assistant-ingress-encrypted'),secret:KEY,
      scope,isCurrent:current,serialize:value=>JSON.stringify(value,BufferJSON.replacer),
      deserialize:value=>JSON.parse(value,BufferJSON.reviver)});
    ingressFault=null;return assistantIngress;
  }
  function scheduleIngressRecovery(){
    if(!legacyCentral.configured()||!intakeCurrent()||ingressTimer||!assistantIngress?.diagnostics().pending)return;
    ingressTimer=setTimeout(()=>{ingressTimer=null;void replayAssistantIngress();},5000);ingressTimer.unref?.();
  }
  async function replayAssistantIngress(){
    if(replayRunning||!intakeCurrent())return;
    replayRunning=true;
    try{
      const journal=ensureAssistantIngress();
      // Capture is bounded independently of the expensive identity/API lane.
      // Controls may still be admitted while a previous started turn needs review.
      const entries=journal.pending().slice(0,8).filter(entry=>!assistantQueues.has(String(mine)+':'+entry.id));
      if(entries.length)await receive({messages:entries.map(entry=>entry.message.raw),type:'notify'},
        new Map(entries.map(entry=>[entry.id,entry])));
    }catch{ingressFault=true;transport.record('assistant_ingress_unavailable');}
    finally{replayRunning=false;scheduleIngressRecovery();}
  }
  const groupSetting=(name,fallback,max)=>{
    const raw=process.env[name],value=Number(raw);
    return raw!==undefined&&Number.isSafeInteger(value)&&value>=0&&value<=max?value:fallback;
  };
  const settleMs=groupSetting('QR_AI_GROUP_WAIT_MS',350,2000);
  const revisionEnabled=process.env.QR_AI_INTAKE_REVISION_ENABLED==='1'||legacyCentral.configured();
  const summaryReviewRequired=new Map();
  const inboundIntake=makeConversationIntake({
    retryUnconsumedNoticeFailures:legacyCentral.configured(),
    settleMs,maxGroupMs:Math.max(settleMs,groupSetting('QR_AI_GROUP_MAX_WAIT_MS',1200,5000)),
    isDuplicate:({messageId})=>seen.has('wa:'+messageId),
    beforeConsume:batch=>{if(assistantIngress)assistantIngress.consume(batch.messageIds);},
    onRejected:async batch=>{
      for(const id of batch.messageIds)transport.record('inbound_batch_rejected',id,{category:batch.reason});
      if(batch.reason==='stale'||!batch.isCurrent()||!intakeCurrent())return;
      const entries=batch.messageIds.map(id=>inboundMetadata.get(id)).filter(item=>item?.allowed);
      if(assistantIngress){
        // Only the live, durably preparing attempt can prove that no business
        // consumer ran. Unknown/legacy/consuming entries stay blocked.
        try{
          const failed=assistantIngress.failPreparation(batch.messageIds,batch.reason);
          if(!failed)return;
          const phone=entries[0]?.phone;
          if(phone)summaryReviewRequired.set(phone,true);
          const current=()=>intakeCurrent()&&batch.isCurrent()&&!!phone&&entries.every(item=>item.phone===phone)&&
            Date.now()<failed.expires_at&&legacyCentral.allowed(phone)&&assistantIngress.canAutomate();
          if(!current()||!Number.isSafeInteger(failed.control_revision)||typeof legacyCentral.preparationFailed!=='function')return;
          const proofs=failed.message_ids.map(id=>inboundMetadata.get(id)?.noticeProof);
          if(!assistantIngress.claimPreparationNotice(failed.operation_id))return;
          let outcome;
          try{outcome=await legacyCentral.preparationFailed({phone,...failed},{noticeProofs:proofs,isCurrent:current});}
          finally{assistantIngress.finishPreparationNotice(failed.operation_id,outcome);}
        }catch{for(const id of batch.messageIds)if(assistantIngress.active(id))assistantIngress.review(id);}
        return;
      }
      const phone=entries[0]?.phone;
      if(legacyCentral.configured()&&!allowAssistantRecipient(phone,connectedStore.settings()))return;
      const current=()=>intakeCurrent()&&batch.isCurrent()&&!!phone&&
        entries.every(item=>item.phone===phone)&&(legacyCentral.configured()?legacyCentral.allowed(phone):allowAssistantRecipient(phone,connectedStore.settings()));
      if(!current())return;
      const answer=batch.reason==='revision_failed'?
        'Não consegui validar a sequência das mensagens agora. Pode reenviar o pedido completo com a correção em uma mensagem?':batch.reason==='needs_clarification'?
        'Recebi informações demais de uma vez para interpretar com segurança. Pode resumir o pedido e a correção em uma mensagem mais curta?':
        'Não consegui entender bem esse áudio. Pode mandar novamente, com até dois minutos, ou escrever só o serviço e o horário que procura?';
      // One explanatory attempt for the complete rejected batch; never replay an operation.
      await send(phone,answer,{waitForCapacity:true,isCurrent:current});
    },
    consume:async batch=>{
      const entries=batch.messages.map(item=>inboundMetadata.get(item.id));
      const phone=entries[0]?.phone,traceId=batch.messages[0]?.id;
      const ids=batch.messages.map(item=>item.id);
      const provenance=assistantIngress?.consumptionProof(ids);
      if(assistantIngress&&entries.some(item=>item?.selection&&!assistantIngress.allowsSelection(phone,item.selectionProvenance))){
        for(const id of ids)if(assistantIngress.active(id))assistantIngress.complete(id);
        return {state:'policy_skipped',not_committed:true};
      }
      const authorized=()=>intakeCurrent()&&batch.isCurrent()&&!!phone&&
        entries.every(item=>item?.allowed&&item.phone===phone)&&
        (legacyCentral.configured()?legacyCentral.allowed(phone):allowAssistantRecipient(phone,connectedStore.settings()));
      const current=()=>authorized()&&(!assistantIngress||(!ingressFault&&assistantIngress.canConsume(ids)&&
        assistantIngress.isProvenanceCurrent(phone,provenance)&&
        entries.every(item=>!item?.selection||assistantIngress.allowsSelection(phone,item.selectionProvenance))));
      if(assistantIngress)await assistantIngress.waitForAdmission(ids,{isCurrent:()=>!ingressFault&&authorized()});
      if(!current())return;
      const typing=typingPresence.begin({phone,chatJid:entries[0]?.jid,isCurrent:current});let outcome;
      try{outcome=await (async()=>{
      if(legacyCentral.configured()){
        if(!allowAssistantRecipient(phone,connectedStore.settings()))return;
        return legacyCentral.forward({phone,message_id:traceId,message_ids:batch.messages.map(item=>item.id),
          text:batch.text,parts:batch.messages.map(item=>({message_id:item.id,text:item.text})),intake_revision:batch.intakeRevision,
          ...(entries.length===1&&entries[0].selection?{selection:entries[0].selection}:{}),
          ...(assistantIngress?.requiresReview(phone)||entries.length>1&&entries.some(item=>item.selection)?{require_summary_review:true}:{}),
          ...(entries.some(item=>item.event)?{envelope:entries.findLast(item=>item.event)?.event}:{})},
          {noticeProofs:entries.map(item=>item.noticeProof),isCurrent:current,presentationProvenance:provenance,
            beforeReply:value=>typing.beforeReply(value)});
      }
      transport.record('ai_forward',traceId,{count:batch.messages.length});
      let answer,response;
      const interactionSupported=entries.every(item=>item.noticeProof?.assistant_interactions===1);
      try{response=await forwardAssistant({apiUrl:process.env.QR_AI_API_URL,token:KEY,phone,
        messageId:traceId,messageIds:batch.messages.map(item=>item.id),text:batch.text,intakeRevision:batch.intakeRevision,
        ...(process.env.QR_AI_PARTS_ENABLED==='1'?{parts:batch.messages.map(item=>({message_id:item.id,text:item.text}))}:{}),
        profile:namesStore.profile(phone),isCurrent:current,...(entries.some(item=>item.event)?{envelope:entries.findLast(item=>item.event)?.event}:{}),
        interactionSupported,...(entries.length===1&&entries[0].selection?{selection:entries[0].selection}:{}),
        requireSummaryReview:entries.length>1&&entries.some(item=>item.selection)?true:revisionEnabled?summaryReviewRequired.get(phone)!==false:undefined});
        answer=typeof response==='string'?response:response?.answer;}
      catch(error){if(revisionEnabled)summaryReviewRequired.set(phone,true);transport.record('ai_forward_failed',traceId);throw error;}
      if(response?.state==='superseded'&&response?.not_committed===true){
        if(revisionEnabled)summaryReviewRequired.set(phone,true);return response;
      }
      if(!current()){
        if(revisionEnabled)summaryReviewRequired.set(phone,true);return;
      }
      if(!answer){transport.record('ai_silent',traceId);return;}
      await typing.beforeReply(answer);
      if(!current()){
        if(revisionEnabled)summaryReviewRequired.set(phone,true);return;
      }
      transport.record('ai_answer',traceId);
      try{
        const sent=await send(phone,answer,{waitForCapacity:true,isCurrent:current,
          ...(response?.presentation?{presentation:response.presentation,source:'assistant'}:{})});
        if(revisionEnabled)summaryReviewRequired.set(phone,!sent?.accepted||!current());
        transport.record(sent?.accepted?'ai_relay_returned':'ai_send_unconfirmed',traceId);
        return sent;
      }catch(error){if(revisionEnabled)summaryReviewRequired.set(phone,true);transport.record('ai_send_failed',traceId);throw error;}
      })();}finally{typing.stop();}
      if(assistantIngress&&outcome?.state==='superseded'&&outcome?.not_committed===true)assistantIngress.zeroWrite(ids,outcome);
      if(assistantIngress&&!(outcome?.state==='superseded'&&outcome?.not_committed===true)){
        const confirmed=current()&&(outcome?.accepted===true||
          (outcome?.state==='committed_no_reply'&&outcome.not_committed===false)||
          (outcome?.state==='policy_skipped'&&outcome.not_committed===true));
        for(const id of ids)if(assistantIngress.active(id)){
          if(confirmed)assistantIngress.complete(id,{reviewDelivered:outcome?.accepted===true&&outcome.summary_review_delivered===true});else assistantIngress.review(id);
        }
      }
      return outcome;
    }
  });
  let noticePending=0;const rejectedNotices=new Map(),rejectedEpoch=new Map();
  function enqueueOverflow(message,normalized,conversationKey){
    rejectedEpoch.set(conversationKey,(rejectedEpoch.get(conversationKey)||0)+1);
    if(rejectedEpoch.size>100)rejectedEpoch.delete(rejectedEpoch.keys().next().value);
    const old=rejectedNotices.get(conversationKey),control=normalized.event.control||safetyControl(normalized.text);
    if(old){if(control)queueControl(old.controls,[message,normalized]);else old.latest=[message,normalized];return;}
    if(rejectedNotices.size>=100)return;
    const state={controls:[],latest:null};rejectedNotices.set(conversationKey,state);
    void (async()=>{let item=[message,normalized];while(item&&intakeCurrent()){
      const [msg,event]=item,phone=await resolveMessagePhone(msg.key,client);
      if(!phone||!intakeCurrent())return;
      if(legacyCentral.configured()){await legacyCentral.resume();if(!legacyCentral.allowed(phone))return;
        await legacyCentral.inbound({phone,message_id:msg.key.id,text:event.text||'',kind:'unsupported',...(event.special?{envelope:event.event}:{})});
      }else{if(!allowAssistantRecipient(phone,connectedStore.settings()))return;
        await registerAssistantIngress({apiUrl:process.env.QR_AI_API_URL,token:KEY,phone,messageId:msg.key.id,control:event.event.control||safetyControl(event.text)});
      }
      if(state.controls.length)item=state.controls.shift();else{item=state.latest;state.latest=null;}
      if(!item&&intakeCurrent())try{await send(phone,'Recebi muitas mensagens de uma vez. Pode reenviar o pedido completo com a última correção? Nenhuma nova operação foi confirmada por essa entrada.',{waitForCapacity:true,isCurrent:()=>intakeCurrent()});}catch{}
    }})().catch(()=>{}).finally(()=>rejectedNotices.delete(conversationKey));
  }
  async function receive({messages:incoming,type},replayed=null){
    transport.record('upsert_batch',null,{count:incoming?.length||0,type});
    if(!intakeCurrent())return;
    const jobs=[];
    for(const message of incoming||[]){
      const traceId=message.key?.id;
      const replayEntry=replayed?.get(traceId),sourceType=replayEntry?.message.type||type;
      if(!replayEntry&&!shouldIngestUpsert(sourceType,message.messageTimestamp)){
        transport.record('inbound_filtered_age',traceId,{type});continue;
      }
      if(message.key?.id&&message.message){
        retryMessages.save(message.key,message.message);
        // When a message was authored in the owner's phone rather than
        // through this bridge, Baileys may still be asked to retry its
        // ciphertext for another linked device. Persist only our own simple
        // text messages; never copy received customer messages or media into
        // the durable retry cache.
        if(shouldCacheLinkedSelfText(message)){
          if(retryStore.save(message.key,message.message))retryDiagnostics.persistedSelfTexts++;
          else retryDiagnostics.persistFailures++;
        }
      }
      if(message.key?.fromMe){transport.record('inbound_own',traceId);continue;}
      if(typeof traceId!=='string'||!/^[a-zA-Z0-9._:-]{1,157}$/.test(traceId)||!message.message){
        transport.record('inbound_no_content',traceId);continue;
      }
      const remote=String(message.key.remoteJid||'').match(/^(\d{8,20})(?::\d+)?@(s\.whatsapp\.net|lid)$/);
      if(!remote){transport.record('inbound_unresolved',traceId);continue;}
      // Register synchronously before PN resolution or media work. In the
      // single-recipient pilot, PN/LID variants share this configured key; a LID
      // never supplies a telephone number. Authorization still occurs below.
      const conversationKey=String(mine)+':'+(pilotNumber()||remote[1]+'@'+remote[2]);
      const nativeTicket=nativeCanary.observe(message,{type:sourceType,replay:!!replayEntry});
      if(nativeCanary.recognizes(message)){
        const task=nativeCanary.handle({message,type:sourceType,replay:!!replayEntry,ticket:nativeTicket})
          .then(result=>transport.record('native_test_'+result.state,traceId,{type:result.kind}))
          .catch(()=>transport.record('native_test_unavailable',traceId));
        jobs.push(task);continue;
      }
      const normalized=normalizeInbound(message);if(normalized.silent)continue;
      // Central booking confirmation is a separate deterministic lane.
      // Only a new private text containing a protocol enters it, independent
      // of the AI allowlist. Any other message follows the unchanged pilot.
      const centralEnabled=process.env.ORION_CENTRAL_CONFIRMATION_REPLY_ENABLED==='1';
      const freshForCentral=sourceType==='notify'||(sourceType==='append'&&
        (timestampMs(message.messageTimestamp)||0)>=Date.now()-120000);
      if(!replayEntry&&centralEnabled&&freshForCentral&&normalized.event.kind==='text'&&
          isBookingProtocolMessage(normalized.text)){
        const key=String(mine)+':'+traceId;
        if(handledBookingProtocols.has(key)||seen.has('wa:'+traceId)){
          transport.record('booking_protocol_duplicate',traceId);continue;
        }
        // Bounded before PN resolution, Redis lookup, or a send-capacity wait.
        // A burst of protocol-looking text must never create unbounded tasks.
        const releaseAdmission=bookingAdmission.reserve();
        if(!releaseAdmission){
          transport.record('booking_protocol_capacity_full',traceId);continue;
        }
        handledBookingProtocols.add(key);
        if(handledBookingProtocols.size>2000)
          handledBookingProtocols.delete(handledBookingProtocols.values().next().value);
        const task=(async()=>{
          let sendAttempted=false,phase='identity';
          try{
            const phone=await resolveBookingSenderPhone(message.key,client,{isCurrent:intakeCurrent});
            if(!intakeCurrent())return;
            if(!phone){
              // A verified LID->PN mapping may arrive later for the same WA ID.
              // This callback never sent anything, so a fresh provider retry
              // can safely re-evaluate its sender identity.
              handledBookingProtocols.delete(key);
              transport.record('booking_protocol_identity_unresolved',traceId,{
                kind:String(message.key.remoteJid||'').endsWith('@lid')?'lid':'pn'});
              return;
            }
            if(!bookingAdmission.allowSender(phone)){
              transport.record('booking_protocol_rate_limited',traceId);
              return;
            }
            transport.record('booking_protocol_identity_verified',traceId);
            phase='journal';
            const now=timestampMs(message.messageTimestamp)||Date.now();
            pushMessage({id:'wa:'+traceId,phone,body:normalized.text.slice(0,2000),
              direction:'in',at:now});
            lastInboundAt=Date.now();inboundSinceConnect=true;
            phase='backend';
            const response=await fetchBookingProtocolConfirmation({
              apiUrl:process.env.QR_AI_API_URL||process.env.QR_CHANNEL_BROKER_URL,
              token:KEY,phone,messageId:traceId,text:normalized.text,isCurrent:intakeCurrent,
              onStatus:state=>transport.record('booking_protocol_backend_state',traceId,{status:state})});
            if(!intakeCurrent())return;
            if(!response){
              transport.record('booking_protocol_no_reply',traceId);
              return;
            }
            phase='send';
            sendAttempted=true; // Never retry after an ambiguous WhatsApp operation.
            // Capacity is admitted before composing starts. Presence reflects
            // a pending authenticated reply, never a rate-limited backlog.
            const result=await sendLegacyRaw(phone,response,{
              waitForCapacity:true,isCurrent:intakeCurrent,transactionalTyping:true});
            transport.record(result?.accepted?'booking_protocol_sent':'booking_protocol_unconfirmed',traceId);
          }catch(error){
            if(!sendAttempted)handledBookingProtocols.delete(key);
            const known=new Set(['invalid_confirmation_inbound','invalid_confirmation_transport',
              'missing_confirmation_endpoint','unsafe_confirmation_endpoint',
              'central_confirmation_auth_denied','central_confirmation_http_error',
              'central_confirmation_unavailable','central_confirmation_invalid_response']);
            const category=known.has(error?.message)?error.message:
              error?.name==='AbortError'?'timeout':'other';
            // No phone, LID, untrusted text, token or provider exception in logs.
            transport.record('booking_protocol_failed',traceId,{category:phase,error:category});
          }finally{releaseAdmission();}
        })();
        jobs.push(task);
        continue;
      }
      // Explicit opt-in pilot: exact keyword, one verified phone, never fromMe.
      // Do not forward the test command to the customer-facing assistant.
      if(normalized.text?.trim().toUpperCase()==='ORIONTESTE' &&
          process.env.QR_TRIGGER_TEST_PHONE && sourceType==='notify'&&!replayEntry){
        const phone=await resolveMessagePhone(message.key,client);
        const replyText=testReplyFor({text:normalized.text,phone,
          allowedPhone:process.env.QR_TRIGGER_TEST_PHONE,fromMe:message.key.fromMe});
        if(replyText){
          const key='test:'+traceId;
          if(!seen.has(key)){
            seen.add(key);
            if(seen.size>2000)seen.delete(seen.values().next().value);
            try{
              const {useMeta,metaNotConfigured}=selectTestCtaMode(process.env);
              if(useMeta){
                const sent=await sendTestCloudCta({phone,text:replyText,
                  accessToken:process.env.META_CLOUD_ACCESS_TOKEN,
                  phoneNumberId:process.env.META_CLOUD_PHONE_NUMBER_ID,
                  apiVersion:process.env.META_CLOUD_API_VERSION||'v25.0'});
                transport.record(sent?.accepted?'trigger_test_cta_api_accepted':'trigger_test_cta_api_rejected',traceId);
              }else{
                if(metaNotConfigured)transport.record('trigger_test_cta_not_configured',traceId);
                // Authorized one-number pilot: direct established QR text transport.
                // The managed-channel adoption may still be pending after a restart.
                const sent=await sendLegacyRaw(phone,makeTestFallbackText(replyText),{
                  waitForCapacity:true,isCurrent:()=>intakeCurrent()});
                transport.record(sent?.accepted?'trigger_test_qr_link_accepted':'trigger_test_qr_link_unconfirmed',traceId);
              }
            }catch(error){transport.record('trigger_test_send_failed',traceId);}
          }
          continue;
        }
      }
      const jobKey=String(mine)+':'+traceId;
      let admission=null;
      if(legacyCentral.configured()){
        // Synchronous encrypted capture precedes PN lookup, backend notice and
        // intake/display dedupe. The old display inbox is never the admission log.
        try{
          const journal=ensureAssistantIngress();
          if(assistantQueues.has(jobKey)||seen.has('wa:'+traceId))continue;
          const knownPhone=remote[2]==='s.whatsapp.net'?normalizeFromJid(message.key.remoteJid):null;
          if(knownPhone&&!legacyCentral.allowed(knownPhone))continue;
          if(!knownPhone&&!replayEntry){
            if(Date.now()-opaqueAdmissionAt>=60000){opaqueAdmissionAt=Date.now();opaqueAdmissionCount=0;}
            if(++opaqueAdmissionCount>64){ingressFault=true;transport.record('assistant_ingress_rate_limited');continue;}
          }
          if(replayEntry)admission=replayEntry;
          else{
            const captured=journal.append(message,sourceType,timestampMs(message.messageTimestamp)||Date.now());
            admission=captured.entry;
            // The original encrypted payload wins. A repeated source ID cannot
            // replace its timestamp/body/sender while admission is pending.
            if(!captured.fresh){scheduleIngressRecovery();continue;}
          }
          if(admission.state!=='pending')continue;
          if(!journal.pending().some(entry=>entry.id===traceId))continue;
          if(noticePending>=40){
            scheduleIngressRecovery();continue;
          }
        }catch{ingressFault=true;transport.record('assistant_ingress_unavailable');continue;}
      }else if(revisionEnabled&&noticePending>=40){enqueueOverflow(message,normalized,conversationKey);continue;}
      const epoch=rejectedEpoch.get(conversationKey)||0;
      const metadata={allowed:false,phone:null,jid:message.key.remoteJid,...(normalized.special?{event:normalized.event}:{})};
      const current=()=>intakeCurrent()&&(rejectedEpoch.get(conversationKey)||0)===epoch&&(!metadata.allowed||
        (legacyCentral.configured()?legacyCentral.allowed(metadata.phone):allowAssistantRecipient(metadata.phone,connectedStore.settings())));
      let identityTask;
      const resolveIdentity=()=>identityTask||=(async()=>{
        const phone=await resolveMessagePhone(message.key,client);
        if(!current())return null;
        if(!phone){transport.record('inbound_unresolved',traceId);return null;}
        if(namesStore.observeResolvedMessage(message,phone,{historical:sourceType==='append'}))namesSync.changed();
        if(legacyCentral.configured())await legacyCentral.resume();
        metadata.phone=phone;metadata.allowed=legacyCentral.configured()?legacyCentral.allowed(phone):allowAssistantRecipient(phone,connectedStore.settings());
        inboundMetadata.set(traceId,metadata);
        transport.record('inbound_resolved',traceId);
        return phone;
      })();
      // Start the authenticated notice before queue admission; rejected work
      // still revokes an obsolete turn and records customer safety controls.
      const noticeTask=revisionEnabled?(()=>{noticePending++;return (async()=>{
          const phone=await resolveIdentity();
          if(!phone&&assistantIngress)throw Error('assistant_identity_unresolved');
          if(!phone||!current()||!metadata.allowed){
            if(phone&&(normalized.selection||normalized.event.reply_to))
              assistantSelectionDiagnostic(phone,traceId)(!metadata.allowed?'not_allowed':'stale');
            return {skip:true};
          }
          const body=normalized.text,media=message.message.audioMessage||message.message.ephemeralMessage?.message?.audioMessage;
          if(legacyCentral.configured()){
            const proof=await legacyCentral.inbound({phone,message_id:traceId,text:body||'',kind:media?'audio':body?'text':'unsupported',
              ...(normalized.special?{envelope:normalized.event}:{})});
            if(!current())throw Error('assistant_ingress_admission_stale');
            if(assistantIngress&&Number.isSafeInteger(proof?.revision)&&proof.revision>=1)assistantIngress.admitted(traceId);
            metadata.noticeProof=proof;return proof;
          }
          if(!normalized.special&&!body&&(!media||!assistantAudioEnabled()||!allowAssistantAudio(phone,connectedStore.settings())))return {skip:true};
          const proof=await registerAssistantIngress({apiUrl:process.env.QR_AI_API_URL,token:KEY,phone,messageId:traceId,control:normalized.event.control||safetyControl(body)});
          metadata.noticeProof=proof;return proof;
        })().finally(()=>{noticePending--;});})():null;
      void noticeTask?.catch(()=>{});
      const task=inboundIntake.submit({conversationKey,messageId:traceId,isCurrent:current,
        ...(noticeTask?{notice:()=>noticeTask}:{}),
        prepare:async({signal})=>{
          const phone=await resolveIdentity();
          if(!current()||signal.aborted)return {skip:true};
          if(!phone)return {skip:true};
          let body=normalized.text;
          const media=message.message.audioMessage||message.message.ephemeralMessage?.message?.audioMessage;
          if(!normalized.special&&!body&&!media){transport.record('inbound_no_text',traceId);return {skip:true};}
          if(assistantIngress?.requiresReview(phone)&&normalized.selection&&metadata.noticeProof?.assistant_interactions!==1){
            assistantIngress.exclude(traceId);return {skip:true};
          }
          if(metadata.noticeProof?.assistant_interactions===1&&(normalized.selection||normalized.event.reply_to)){
            const diagnose=assistantSelectionDiagnostic(phone,traceId);diagnose('detected');
            diagnose('quote_provided',normalized.selection?.quoted_message_id||normalized.event.reply_to);
            const store=typingPresence.assistantPresentations(),selection=store.resolve(message,phone,diagnose);
            if(selection){
              const provenance=store.selectionProvenance(phone,selection.quoted_message_id);
              if(assistantIngress&&!assistantIngress.allowsSelection(phone,provenance)){diagnose('stale');assistantIngress.exclude(traceId);return {skip:true};}
              const acknowledge=legacyCentral.configured()?payload=>legacyCentral.presentationAck(payload):
                payload=>acknowledgeAssistantPresentation({apiUrl:process.env.QR_AI_API_URL,token:KEY,payload});
              if(!await store.acknowledge(phone,selection.quoted_message_id,acknowledge,current,diagnose)){
                diagnose('ack_unconfirmed');
                assistantIngress?.exclude(traceId);return {skip:true};
              }
              metadata.selection=selection;metadata.selectionProvenance=provenance;delete metadata.event;body=ASSISTANT_SELECTION_TEXT;
            }else if(normalized.selection){assistantIngress?.exclude(traceId);return {skip:true};}
          }
          if(assistantIngress){
            if(!metadata.allowed||!allowAssistantRecipient(phone,connectedStore.settings()))return {skip:true};
            if(ingressFault||assistantIngress.diagnostics().blocked)return {skip:true};
            // Persist a no-replay boundary before ASR, display dedupe, business
            // or any outgoing effect. Only this live attempt can complete it.
            assistantIngress.prepare([traceId],{phone,proofs:[metadata.noticeProof]});
          }
          const at=admission?.at||timestampMs(message.messageTimestamp)||Date.now();
          const row={id:'wa:'+traceId,phone,body:body?String(body).slice(0,2000):'[Áudio recebido]',direction:'in',at};
          pushMessage(row);
          lastInboundAt=Date.now();inboundSinceConnect=true;if(sourceType==='append')recentAppends++;
          if(!metadata.allowed){transport.record('pilot_denied',traceId);return {skip:true};}
          transport.record('pilot_allowed',traceId);
          if(metadata.selection)return {text:ASSISTANT_SELECTION_TEXT};
          if(normalized.special)return {text:body||'[Evento de mensagem recebido]'};
          if(!body){
            if(!assistantAudioEnabled()||!allowAssistantAudio(phone,connectedStore.settings()))return {skip:true};
            const txt=await transcribeAssistantAudio(message,{client,downloadMediaMessage,phone,token:KEY,
              apiUrl:process.env.QR_AI_API_URL,logger,signal,
              ...(legacyCentral.configured()?{transcribe:payload=>legacyCentral.transcribe(payload)}:{})});
            if(!current()||signal.aborted)return {skip:true};
            if(!txt)throw Error('audio_not_understood');
            row.body='[Áudio transcrito] '+txt;persistInbox();body=txt;
          }
          return {text:body};
        }});
      const job=task.then(async result=>{
        if(result.state==='duplicate')transport.record('inbound_duplicate',traceId);
        else if(result.state==='overloaded'){transport.record('inbound_queue_full',traceId);await noticeTask?.catch(()=>{});if(current()&&!assistantIngress)enqueueOverflow(message,normalized,conversationKey);}
        else if(result.state==='consume_failed')console.warn('Orion QR: atendimento IA não concluído (dados omitidos)');
        if(assistantIngress&&admission){
          try{
            if(assistantIngress.active(traceId))assistantIngress.review(traceId);
            else if(result.state==='skipped'&&current()&&metadata.phone&&
              (!metadata.allowed||!allowAssistantRecipient(metadata.phone,connectedStore.settings())||
                (!normalized.special&&!normalized.text&&!message.message.audioMessage&&!message.message.ephemeralMessage?.message?.audioMessage)))assistantIngress.exclude(traceId);
          }catch{assistantIngress.abandon();ingressFault=true;transport.record('assistant_ingress_unavailable');}
          // revision_failed is the only released intake claim. It has never
          // reached preparation; encrypted pending input is retried unchanged.
          scheduleIngressRecovery();
        }
      }).finally(()=>{
        if(inboundMetadata.get(traceId)===metadata)inboundMetadata.delete(traceId);
        if(assistantQueues.get(jobKey)===job)assistantQueues.delete(jobKey);
      });
      if(!assistantQueues.has(jobKey))assistantQueues.set(jobKey,job);
      jobs.push(job);
    }
    await Promise.all(jobs);
  }
  client.ev.on('messages.upsert',receive);
  client.ev.on('messages.update',updates=>{for(const item of (updates||[]).slice(0,100)){
    const event=updateEvent(item.key,item.update);if(event)void receive({messages:[event],type:'notify'});
  }});
  client.ev.on('messages.delete',({keys=[]}={})=>{for(const key of keys.slice(0,100)){
    const event=updateEvent(key,{message:null,messageStubType:1});if(event)void receive({messages:[event],type:'notify'});
  }});
  client.ev.on('call',calls=>{for(const call of (calls||[]).slice(0,20)){const event=callEvent(call);if(event)void receive({messages:[event],type:'notify'});}});
  client.ev.on("connection.update",update=>{
    if(mine!==generation)return;
    if(update.receivedPendingNotifications===true)
      pendingNotificationsReceived=true;
    if(update.qr && pairingRequestedForGeneration!==mine){
      qr=update.qr;qrAt=Date.now();lastQrAt=qrAt;status="qr_ready";lastError=null;
      console.info("Orion QR: pareamento disponível (conteúdo omitido)");
    }
    if(update.connection==="open"){
      status="connected";everConnected=true;qr="";qrAt=0;pairingCode="";pairAt=0;
       namesStore.bindIdentity(client.user);namesSync.start();
       try{connectedStore.setIdentity(client.user?.id)}catch{
         console.warn("Orion QR: configuração criptografada indisponível")
       }
       void refreshConnectedProfile(client);
      connectedSince=Date.now();inboundSinceConnect=false;
      if(update.receivedPendingNotifications===true)pendingNotificationsReceived=true;
      pairedNumber=normalizeFromJid(client.user?.id)||"";
      if(legacyCentral.configured()){
        // Load existing review/pending work under the now-proven physical
        // identity before publishing readiness or permitting automated sends.
        try{ensureAssistantIngress().pending();}
        catch{ingressFault=true;transport.record('assistant_ingress_unavailable');}
      }
      primaryHealth.connected();
      if(legacyCentral.configured())void replayAssistantIngress();
      lastError=null;console.info("Orion QR: dispositivo conectado");
      // One-time, source-bound live delivery test explicitly authorized by
      // the owner. Persistent per-recipient claims prevent any duplicate.
      if([SMOKE_ID,APPOINTMENT_QA_ID].includes(process.env.ORION_ONE_TIME_LIVE_SEND)){
        setTimeout(()=>{
          const stillCurrent=()=>mine===generation&&socket===client&&status==='connected';
          if(!stillCurrent())return;
          void runConsentedPairSmoke({
            directory:authDir,pairedPhone:pairedNumber,isCurrent:stillCurrent,
            send:(to,body,options)=>sendLegacyRaw(to,body,options),
            report:event=>console.info('Orion Live QA '+JSON.stringify(event))
          }).catch(()=>console.warn('Orion Live QA: gate_or_storage_unavailable'));
        },1800).unref();
      }
      if(notifyNewLink && process.env.ORION_NOTIFY_SELF_ON_LINK==="1" && notifiedGeneration!==mine){
        notifiedGeneration=mine;
        // Only the authenticated account receives a one-time confirmation.
        // Never send on QR creation, intermediate pairing or failed login.
        setTimeout(async()=>{
          if(mine!==generation || status!=="connected")return;
          try{
            await saveQueue;
            if(mine!==generation || status!=="connected")return;
            if(newLinkNoticeAuthDir===authDir)newLinkNoticeAuthDir=null;
            const result=await sendConnectionNoticeOnce({
              authDir,
              client,
              sendText:async (jid,text)=>{
                const phone=normalizeFromJid(jid),noticeCurrent=()=>mine===generation&&status==='connected'&&socket===client&&
                  legacyCentral.transportCurrent()&&normalizeFromJid(client.user?.id)===phone;
                return typingPresence.run({phone,chatJid:jid,isCurrent:noticeCurrent,assertAuthorized:()=>legacyCentral.assertTransportCurrent(),
                  action:{type:'text',text}},async({beforeSend})=>{
                await beforeSend();return sendStoredText({
                  jid,text,store:retryStore,freshDevices:true,
                  isCurrent:noticeCurrent,assertAuthorized:()=>legacyCentral.assertTransportCurrent(),
                  makeMessage:async (recipient,messageText)=>generateWAMessage(
                    recipient,{text:messageText},{
                      userJid:client.user?.id,logger,
                      messageId:generateMessageIDV2(client.user?.id)
                    }),
                  relay:async(recipient,message,opts)=>{await legacyCentral.assertTransportCurrent();if(!noticeCurrent())throw Error('connection_notice_revoked');return client.relayMessage(recipient,message,opts);}
                });
                });
              },
              isCurrent:()=>mine===generation&&status==="connected"&&socket===client,
              onSent:item=>{
                const id=item.id||randomUUID();
                if(item.message)retryMessages.save({id},item.message);
                deliveryReceipts.set(id,"pending");
                pushMessage({id:"out:"+id,phone:item.phone,body:item.body,
                  direction:"out",at:Date.now(),delivery:"pending"});
              },
            });
            console.info("Orion QR: confirmação para a própria conta: "+result.state);
          }catch{
            console.warn("Orion QR: confirmação não concluída; estado preservado para análise");
          }
        },2500);
      }
    }
    if(update.connection==="close"){
      stopPrimaryIngress();
      pauseContactNames({purge:Number(update.lastDisconnect?.error?.output?.statusCode)===DisconnectReason.loggedOut});
      qr="";qrAt=0;pairingCode="";pairAt=0;
      connectedSince=0;pendingNotificationsReceived=false;inboundSinceConnect=false;
      const code=Number(update.lastDisconnect?.error?.output?.statusCode);
      lastDisconnectCode=Number.isFinite(code)?code:null;
      console.info("Orion QR: conexão encerrada (código="+(lastDisconnectCode??"desconhecido")+")");
      if(sustainedRecovery(primaryHealth.snapshot())){attempts=0;sessionRecoveryAttempts=0;}
      const decision=disconnectPolicy(code,attempts,{sessionAttempts:sessionRecoveryAttempts});
      if(code===500)sessionRecoveryAttempts++;
      primaryHealth.disconnected(decision,attempts+1,disconnectEvidence(update.lastDisconnect?.error));
      if(code===DisconnectReason.loggedOut){
        if(newLinkNoticeAuthDir===authDir)newLinkNoticeAuthDir=null;
        status=everConnected?"logged_out":"pair_failed";
        lastError=everConnected?
          "WhatsApp revogou a conexão. Use Refazer vínculo.":
          "O WhatsApp rejeitou o vínculo. Um novo código exige outra sessão.";
        return;
      }
      if(!decision.retry){
        status='error';socket=null;clearTimeout(timer);timer=null;
        lastError=decision.reason==='connection_replaced'?'A sessão foi assumida por outra instância. Confira a responsabilidade da conexão.':
          decision.reason==='reconnect_exhausted'?'As tentativas automáticas foram interrompidas. Confira a conexão antes de tentar novamente.':
          'O WhatsApp exige uma verificação da sessão. As credenciais foram preservadas.';
        return;
      }
      status="reconnecting";
      lastError=code===DisconnectReason.restartRequired?
        "WhatsApp solicitou reinicialização após pareamento; restaurando sessão.":
        "Conexão interrompida; tentando restabelecer.";
      const delay=decision.delayMs;
      attempts++;
      timer=setTimeout(()=>{
        if(mine===generation){
          void saveQueue.finally(()=>connect().catch(()=>{}));
        }
      },delay);
    }
  });
}
async function connect(){
  if(stopping)return;
  stopPrimaryIngress();
  const mine=++generation;
  pauseContactNames();
  const namesForConnection=contactNames,selectedAuthDir=authDir;
  clearTimeout(timer);timer=null;qr="";qrAt=0;pairingCode="";pairAt=0;
  status="connecting";connectStartedAt=Date.now();pairingRequestedForGeneration=-1;
  primaryHealth.connecting();
  pendingNotificationsReceived=false;inboundSinceConnect=false;
  try{
    // The primary socket shares the manager's SQL owner/fence. Reserve that
    // owner before reading or mutating Signal state, including on startup.
    if(legacyCentral.configured())await legacyCentral.resume();
    await legacyCentral.assertTransportCurrent();
    if(stopping||mine!==generation)return;
    if(authBackupDirectory!==selectedAuthDir){
      authBackup=backupAuth({directory:selectedAuthDir,backupDirectory:'/data/orion-auth-backups',
        secret:KEY,label:'before-durable-auth-v2'});
      authBackupDirectory=selectedAuthDir;
      console.info('Orion auth backup '+JSON.stringify(authBackup));
    }
    const {state,saveCreds}=useDurableAuthState(selectedAuthDir,{
      secret:KEY,sdk:{BufferJSON,proto,initAuthCreds},
      backupVerified:['created_verified','verified_existing'].includes(authBackup.state),
      isCurrent:()=>!stopping&&mine===generation&&selectedAuthDir===authDir&&legacyCentral.transportCurrent(),
      onFailure:()=>{
        transport.record('auth_write_failed');
        if(mine!==generation)return;
        status='error';lastError='Falha de persistência da sessão. Os envios foram interrompidos para proteger as chaves.';
        ++generation;clearTimeout(timer);timer=null;pauseContactNames();try{socket?.end(undefined);}catch{}
      }});
    if(mine!==generation||namesForConnection!==contactNames||selectedAuthDir!==authDir)return;
    namesForConnection.bindIdentity(state.creds.me);
    const originalSet=state.keys.set;
    state.keys.set=async data=>{
      try{
        await originalSet(data);
        for(const category of Object.keys(data))transport.record('auth_keys_saved',null,{category,count:Object.keys(data[category]||{}).length});
      }catch(error){transport.record('auth_write_failed');throw error;}
    };
    const webVersion=await selectWaWebVersion({auto:process.env.QR_WA_VERSION_AUTO!=='0',
      bundledVersion:DEFAULT_CONNECTION_CONFIG.version,fetchVersion:fetchLatestWaWebVersion});
    if(mine!==generation||namesForConnection!==contactNames||selectedAuthDir!==authDir)return;
    await legacyCentral.assertTransportCurrent();
    if(stopping||mine!==generation||selectedAuthDir!==authDir||!legacyCentral.transportCurrent())return;
    primaryHealth.protocolVersion(webVersion.version,webVersion.source);
    const deviceCache=makePilotDeviceCache(makeBoundedCache({max:5000,ttlMs:300000}));
    const client=makeWASocket({
      userDevicesCache:deviceCache,
      auth:{creds:state.creds,keys:makeCacheableSignalKeyStore(state.keys,logger)},
      makeSignalRepository:fenceSignalRepository(observeSignalRepository(DEFAULT_CONNECTION_CONFIG.makeSignalRepository,jid=>{
        if(mine===generation)transport.record('signal_encrypt_target',null,encryptionTargetDetails(jid,state.creds.me));
      }),()=>!stopping&&mine===generation&&selectedAuthDir===authDir&&legacyCentral.transportCurrent()),
      msgRetryCounterCache,
      maxMsgRetryCount:5,
      getMessage:async key=>{
        const content=await readAuthorizedRetry(retryStore,key,{socket:client,
          isCurrent:()=>!stopping&&mine===generation&&socket===client&&status==='connected'&&legacyCentral.transportCurrent(),
          assertCurrent:()=>legacyCentral.assertTransportCurrent()});
        // The library also sends its OWN retry-control messages. A cache
        // miss for one of those IDs is not proof that a customer-facing
        // outgoing message is missing. Report these separately, without IDs.
        const tracked=Boolean(key?.id && (
          deliveryReceipts.has(key.id) || messages.some(m=>m.id==='out:'+key.id)
        ));
        transport.record('get_message',key?.id,{cached:!!content,tracked});
        if(content)retryDiagnostics.cacheHits++;
        else {
          retryDiagnostics.cacheMisses++;
          if(tracked)retryDiagnostics.cacheMissTracked++;
          else retryDiagnostics.cacheMissUntracked++;
        }
        return content;
      },
      browser:Browsers.windows("Chrome"),
      version:webVersion.version,
      printQRInTerminal:false,markOnlineOnConnect:false,syncFullHistory:false,logger});
    socketDeviceCaches.set(client,deviceCache);
    // QR pairing leaves registered=false in the pinned SDK. Only genuinely
    // unlinked auth arms a new notice; restoring a signed account stays silent.
    // The memory-only intent survives Baileys' post-pairing restartRequired.
    if(classifyPairedAuth(state.creds,{Curve,proto})==='unlinked'&&process.env.ORION_NOTIFY_SELF_ON_LINK==="1")newLinkNoticeAuthDir=selectedAuthDir;
    const notifyNewLink=newLinkNoticeAuthDir===selectedAuthDir;
    socket=client;installListeners(client,mine,saveCreds,notifyNewLink,state.creds.registrationId);
    primaryHealth.watch(client.ws);
  }catch{
    if(stopping||mine!==generation)return;
    status="error";lastError="Não foi possível iniciar o serviço do WhatsApp.";
    const decision=disconnectPolicy(null,attempts++);primaryHealth.disconnected(decision,attempts);
    console.warn("Orion QR: erro genérico ao iniciar");
    if(decision.retry)timer=setTimeout(()=>{if(mine===generation)connect().catch(()=>{})},decision.delayMs);
  }
}
async function pair(number){
  if(status==="connected")return {status:"connected"};
  if(status==="logged_out"||status==="pair_failed")
    throw Error("Sessão revogada: reinicie o vínculo pelo WhatsApp");
  if(requesting)throw Error("Geração em andamento");
  const phone=validPhone(number);
  if(!socket||!socket.requestPairingCode || !qr || !freshQR(qrAt))
    throw Error("Aguarde a disponibilidade do QR e tente novamente");
  if(pairingRequestedForGeneration===generation){
    if(pairingCode&&pairPhone===phone&&Date.now()-pairAt<90000)
      return {status:"pairing",code:formatPairingCode(pairingCode),
        expiresIn:Math.max(1,Math.ceil((90000-Date.now()+pairAt)/1000))};
    throw Error("Código anterior expirou. Reinicie a conexão para gerar outro.");
  }
  requesting=true;pairingRequestedForGeneration=generation;
  qr="";qrAt=0;status="pairing";pairPhone=phone;
  try{
    const code=await socket.requestPairingCode(phone);
    const formatted=formatPairingCode(code);
    pairingCode=formatted.replace("-","");pairAt=Date.now();
    console.info("Orion QR: código solicitado, aguardando autorização no telefone");
    return {status:"pairing",code:formatted,expiresIn:90};
  }catch(error){
    // Never issue two pairing codes on the same broken socket.
    lastError="O WhatsApp não confirmou o código. Reinicie a conexão.";
    status="pair_failed";
    throw error;
  }finally{requesting=false}
}
// Read-only USync contact lookup, only on deliberate operator action.
// Short cooldown prevents repeated requests to WhatsApp for the same person.
const recipientInspections=new Map();
const resendCounterByMessage=new Map();
function countRetryRequest(id){
  if(!id)return;
  if(!resendCounterByMessage.has(id)&&resendCounterByMessage.size>=1000){
    resendCounterByMessage.delete(resendCounterByMessage.keys().next().value);
  }
  resendCounterByMessage.set(id,Math.min(20,(resendCounterByMessage.get(id)||0)+1));
}
async function recipientInspection(value){
  const phone=validPhone(value);
  if(status!=="connected" || !socket)throw Error("O WhatsApp não está conectado");
  const now=Date.now();
  const cached=recipientInspections.get(phone);
  if(cached&&cached.expires>now)return {...cached.data,delivery:inspectDelivery(messages,phone,resendCounterByMessage)};
  // onWhatsApp returns PN plus optional LID in this installed Baileys version.
  // No assertSessions(force), no sent message and no auth-state mutation.
  let lookup;
  const started=Date.now();
  try {
    lookup=await socket.onWhatsApp(phone);
  }catch{
    return {registered:null,identity:"lookup_failed",hasLid:null,
      self:phone===String(socket.user?.id||"").split(":")[0].split("@")[0],
      lookupMs:null,delivery:inspectDelivery(messages,phone),
      note:"WhatsApp não respondeu à consulta. Nenhuma mensagem foi enviada."};
  }
  const summary=inspectLookup(phone,lookup,socket.user?.id);
  const data={...summary,lookupMs:Date.now()-started,
    delivery:inspectDelivery(messages,phone)};
  if(recipientInspections.size>=40){
    const oldest=recipientInspections.keys().next().value;
    recipientInspections.delete(oldest);
  }
  recipientInspections.set(phone,{data,expires:now+90000});
  return data;
}
async function send(phone,text,options={}){
  if(legacyCentral.configured())return legacyCentral.automatic(phone,text,options);
  return sendLegacyRaw(phone,text,options);
}
async function sendLegacyProtocol(phone,action,{isCurrent=()=>false,assertAuthorized,onPrepared=()=>{}}={}){
  if(!['text','list','url_button','audio'].includes(action?.type)||typeof assertAuthorized!=='function')throw Error('native_test_unavailable');
  const target=validPhone(phone),client=socket,mine=generation;
  const current=()=>socket===client&&generation===mine&&status==='connected'&&isCurrent()===true&&legacyCentral.transportCurrent();
  const authorize=async()=>{if(!current())throw Error('native_test_unavailable');await assertAuthorized();if(!current())throw Error('native_test_unavailable');};
  await authorize();await sendCapacity.acquire({wait:true,isCurrent:current});await authorize();
  const matches=await client.onWhatsApp(target);await authorize();
  if(!Array.isArray(matches)||!matches.some(row=>row.exists===true&&normalizeFromJid(row.jid)===target))throw Error('native_test_unavailable');
  const proof=await resolvePilotRecipient({phone:target,pilotPhone:target,enabled:process.env.QR_PILOT_USE_VERIFIED_LID==='1',
    socket:client,deviceCache:socketDeviceCaches.get(client),isCurrent:current});await authorize();
  let built;
  try{return await socketOutgoingPresence.get(client).run({phone:target,chatJid:proof.jid,isCurrent:current,assertAuthorized:authorize,action},async({beforeSend})=>{
    const sent=await sendStoredProtocol({jid:proof.jid,recipientProof:proof,store:retryStore,isCurrent:current,assertAuthorized:authorize,
      onPrepared:msg=>{onPrepared(msg);retryDiagnostics.prepared++;deliveryReceipts.set(msg.key.id,'pending');deliveryStart.set(msg.key.id,Date.now());
        transport.record('native_test_prepared',msg.key.id,{type:action.type,kind:proof.kind});},
      makeMessage:async jid=>{
        built=await buildChatbotContent(action,{sdk:{proto,prepareWAMessageMedia},
          jid,loadAsset:loadNativeTestAsset,upload:(...args)=>client.waUploadToServer(...args),assertCurrent:authorize,isCurrent:current,logger,interactiveMode:'native'});
        await beforeSend();return generateWAMessageFromContent(jid,built.content,{userJid:client.user.id,messageId:generateMessageIDV2(client.user.id)});
      },relay:(jid,body,options)=>client.relayMessage(jid,body,options)});
    retryDiagnostics.relayAccepted++;transport.record('native_test_relay_returned',sent.key.id,{type:action.type});
    return {accepted:true,id:sent.key.id};
  });}finally{built?.dispose();}
}
async function sendLegacyRaw(phone,text,{waitForCapacity=false,isCurrent=()=>true,transactionalTyping=false,source='automation',assertAuthorized,pacing=true,presentation,presentationAck,deferPresentationAck=false,presentationProvenance}={}){
  if(presentation&&source!=='assistant')throw Error('assistant_presentation_denied');
  const target=validPhone(phone);
  if(status!=="connected"||!socket)throw Error("O WhatsApp não está conectado");
  if(typeof text!=="string"||!text.trim()||text.length>1200)
    throw Error("Mensagem deve ter entre 1 e 1200 caracteres");
  const selectedSocket=socket;
  const selectedGeneration=generation;
  let authorized=true;
  const current=()=>authorized&&socket===selectedSocket&&generation===selectedGeneration&&status==='connected'&&isCurrent();
  const authorize=async()=>{
    if(!current()||!legacyCentral.transportCurrent())throw Error('A conexão ou autorização mudou antes do envio. Nenhuma mensagem foi enviada.');
    if(typeof assertAuthorized==='function')try{await assertAuthorized();}catch(error){authorized=false;throw error;}
    if(!current()||!legacyCentral.transportCurrent())throw Error('A conexão ou autorização mudou antes do envio. Nenhuma mensagem foi enviada.');
  };
  await sendCapacity.acquire({wait:waitForCapacity,isCurrent:current});
  await authorize();
  if(!current())throw Error('A conexão ou autorização mudou antes do envio. Nenhuma mensagem foi enviada.');
  let result;
  // Transactional notices must address the WhatsApp-verified PN, not a
  // guessed JID reconstructed from a registered Brazilian mobile phone.
  // The legacy 8-digit candidate is allowed only after an empty exact lookup
  // and exactly one positive provider lookup of its regulated counterpart.
  let verifiedRecipient=null;
  if(source==='appointment'){
    verifiedRecipient=await resolveTransactionalRecipient(
      target,candidate=>selectedSocket.onWhatsApp(candidate),
      {isCurrent:current,ownPhone:pairedNumber});
    if(!current())throw Error('A conexão mudou antes do envio. Nenhuma mensagem foi enviada.');
    transport.record('appointment_recipient_verified',null,{kind:verifiedRecipient.kind});
  }
  const {reliable,freshDevices}=deliveryPolicy(target,process.env,{source});
  const recipientProof=await resolvePilotRecipient({phone:target,pilotPhone:pilotNumber(process.env),
    enabled:reliable&&process.env.QR_PILOT_USE_VERIFIED_LID==='1',socket:selectedSocket,deviceCache:socketDeviceCaches.get(selectedSocket),
    isCurrent:current});
  if(!current())
    throw Error('A conexão mudou antes do envio. Nenhuma mensagem foi enviada.');
  const {jid,kind}=recipientProof;
  // Preserve independently verified pilot LID routing. Other appointment
  // notices use the PN that the provider itself confirmed.
  const destination=verifiedRecipient&&kind==='pn'?verifiedRecipient.jid:jid;
  // An appointment may use the PN normalized by the provider's answer to the
  // original authorized lookup. Presence/FIFO follow that proved recipient;
  // the business authorization remains bound to the original frozen grant.
  const presencePhone=normalizeFromJid(destination)||target;
  await authorize();
  return socketOutgoingPresence.get(selectedSocket).run({phone:presencePhone,chatJid:destination,isCurrent:current,
    assertAuthorized:authorize,action:presentation?.action||{type:'text',text},pacing},async({beforeSend})=>{
  await beforeSend();
  // An appointment is a transactional message: its exact encrypted retry
  // source MUST exist before the first network send, regardless of the
  // older optional global reliability flag.
  if(presentation){
    const scope=legacyCentral.ingressScope?.();
    result=await relayAssistantPresentation({presentation,provenance:presentationProvenance,sdk:{proto,generateWAMessageFromContent,generateMessageIDV2,userJid:selectedSocket.user?.id},
      jid:destination,recipientProof,phone:target,retryStore,presentationStore:socketOutgoingPresence.get(selectedSocket).assistantPresentations(),
      isCurrent:current,assertAuthorized:authorize,beforeSend,
      interactiveMode:assistantNativeMode({tenantId:scope?.tenant_id,channelId:scope?.channel_id,phone:target}),
      acknowledge:presentationAck||(payload=>acknowledgeAssistantPresentation({apiUrl:process.env.QR_AI_API_URL,token:KEY,payload})),deferPresentationAck,
      onDiagnostic:(category,id)=>{
        if(assistantNativeMode({tenantId:scope?.tenant_id,channelId:scope?.channel_id,phone:target})==='native')
          transport.record('assistant_presentation',id,{category});
      },
      relay:async(recipient,message,options)=>{await authorize();return selectedSocket.relayMessage(recipient,message,options);},
      onPrepared:msg=>{retryDiagnostics.prepared++;deliveryReceipts.set(msg.key.id,'pending');deliveryStart.set(msg.key.id,Date.now());
        pushMessage({id:'out:'+msg.key.id,phone:target,body:text.trim(),direction:'out',at:Date.now(),delivery:'pending'});}});
  }else if(requiresPreRelayCache({reliable,source})){
    result=await sendStoredText({
      jid:destination,recipientProof,text:text.trim(),store:retryStore,freshDevices,
      isCurrent:current,assertAuthorized:authorize,
      makeMessage:async (recipient,messageText)=>generateWAMessage(
        recipient,{text:messageText},{
          userJid:selectedSocket.user?.id,logger,
          messageId:generateMessageIDV2(selectedSocket.user?.id)
        }),
      relay:async(recipient,message,opts)=>{await authorize();return selectedSocket.relayMessage(recipient,message,opts);},
      onPrepared:msg=>{
        retryDiagnostics.prepared++;
        deliveryReceipts.set(msg.key.id,"pending");
        deliveryStart.set(msg.key.id,Date.now());
        transport.record('send_prepared',msg.key.id,{kind});
        pushMessage({id:'out:'+msg.key.id,phone:target,body:text.trim(),direction:'out',at:Date.now(),delivery:'pending'});
      }
    });
  }else{
    await authorize();
    result=await selectedSocket.sendMessage(destination,{text:text.trim()});
    if(result?.key?.id&&result?.message)retryStore.save(result.key,result.message);
  }
  if(!result?.key?.id || !result?.message){
    return {accepted:false,delivery:"unconfirmed",error:"Sem comprovante do servidor WhatsApp"};
  }
  retryDiagnostics.relayAccepted++;
  transport.record('relay_returned',result.key.id);
  if(!deliveryReceipts.has(result.key.id))deliveryReceipts.set(result.key.id,"pending");
  if(!deliveryStart.has(result.key.id))deliveryStart.set(result.key.id,Date.now());
  const id="out:"+result.key.id;
  pushMessage({id,phone:target,body:text.trim(),direction:"out",at:Date.now(),
    delivery:deliveryReceipts.get(result.key.id)||"pending"});
  // sendMessage resolve quando a biblioteca entrega ao transporte, não
  // quando o iPhone consegue descriptografar. Apenas receipts validam isso.
  return {accepted:true,delivery:deliveryReceipts.get(result.key.id)||"pending",id,trace:transport.tag(result.key.id),
    ...(typeof result.acknowledgePresentation==='function'?{acknowledgePresentation:result.acknowledgePresentation}:{})};
  });
}

const server=http.createServer(async(req,res)=>{
  const uri=new URL(req.url||"/","http://internal");
  const path=uri.pathname;
  if(path==="/health"&&req.method==="GET")
    return reply(res,200,{status:"ok",bridge:"linked-device"});
  // Additional channels authenticate their own credential at the SQL broker;
  // the shared legacy pilot token never grants access to these routes.
  if(path.startsWith('/internal/channels/')&&await channelRoutes(req,res))return;
  // A disabled local backup key cannot be mistaken for a bad operator key.
  // The service still fails closed; never reveal actual key length.
  if(path.startsWith('/internal/ops/database-backups/')&&Buffer.byteLength(KEY,"utf8")<32)
    return reply(res,503,{error:"bridge_not_configured"});
  if(!auth(req))return reply(res,401,{error:"unauthorized"});
  try{
    if(path==='/internal/ops/assistant-ingress-review'){
      if(req.method!=='POST')return reply(res,405,{error:'method_not_allowed'});
      try{
        const body=await collectIngressReviewBody(req);
        const authority=verifyIngressReviewAssertion({headers:req.headers,body,secret:KEY});
        if(!primaryIngressReview)return reply(res,409,{error:'ingress_review_not_ready'});
        return reply(res,200,await primaryIngressReview(body,authority));
      }catch(error){
        const codes={invalid_ingress_review_request:400,invalid_operator_assertion:401,
          ingress_review_not_ready:409,ingress_review_scope_conflict:409,ingress_review_snapshot_conflict:409,
          ingress_review_operation_conflict:409,ingress_review_entry_refused:409,ingress_review_identity_unresolved:409,
          ingress_review_backup_failed:503,ingress_review_capacity:503,ingress_review_storage_unavailable:503};
        const code=Object.hasOwn(codes,error.code)?error.code:'ingress_review_storage_unavailable';
        return reply(res,codes[code],{error:code});
      }
    }
    const deviceReview=path.match(/^\/internal\/ops\/device-ingress-review\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/);
    if(deviceReview&&!uri.search){
      if(req.method!=='POST')return reply(res,405,{error:'method_not_allowed'});
      try{
        const key=req.headers['x-orion-channel-key'];
        if(typeof key!=='string'||key.length<32||key.length>256)
          return reply(res,401,{error:'channel_unauthorized'});
        const body=await collectIngressReviewBody(req,{device:true});
        const authority=verifyIngressReviewAssertion({headers:req.headers,body,secret:KEY});
        // A platform owner assertion and the actual current channel key are
        // both required. A legacy MASTER alone never opens another tenant.
        return reply(res,200,await channelRoutes.reviewDevice(deviceReview[1],key,body,authority));
      }catch(error){
        const codes={invalid_ingress_review_request:400,invalid_operator_assertion:401,
          channel_unauthorized:401,ingress_review_not_ready:409,
          ingress_review_scope_conflict:409,ingress_review_snapshot_conflict:409,
          ingress_review_operation_conflict:409,ingress_review_entry_refused:409,
          ingress_review_identity_unresolved:409,ingress_review_backup_failed:503,
          ingress_review_capacity:503,ingress_review_storage_unavailable:503};
        const code=error?.code||error?.message;
        const safe=Object.hasOwn(codes,code)?code:'ingress_review_storage_unavailable';
        return reply(res,codes[safe],{error:safe});
      }
    }
    if(path.startsWith('/internal/ops/database-backups/'))
      return await databaseBackup(req,res,{root:'/data/orion-db-backups',id:path.slice('/internal/ops/database-backups/'.length),
        enabled:process.env.QR_DATABASE_BACKUP_ENABLED==='1',secret:KEY});
    if(path==="/internal/status"&&req.method==="GET")
      return reply(res,200,stats());
    if(path==='/internal/legacy-central/status'&&req.method==='GET')
      return reply(res,200,legacyCentral.health());
    if(path==="/internal/profile/refresh"&&req.method==="POST"){
      if(status!=="connected"||!socket)return reply(res,409,{error:"not_connected"});
      await refreshConnectedProfile(socket);
      return reply(res,200,{profile:connectedProfile});
    }
    if(path==="/internal/settings"&&req.method==="POST"){
      if(status!=="connected"||!socket)return reply(res,409,{error:"not_connected"});
      const settings=connectedStore.update(await collectBody(req),{
        chatbot:assistantEnabled(),transcription:!!transcriberConfig()||assistantAudioEnabled(),
        callBlocking:typeof socket.rejectCall==="function"
      });
      return reply(res,200,{settings});
    }
    if(path==="/internal/restart"&&req.method==="POST"){
      const input=await collectBody(req);
      if(input.confirm!=="REINICIAR"||status!=="connected"||Date.now()-lastRestartAt<60000)
        return reply(res,409,{error:"restart_not_allowed"});
      lastRestartAt=Date.now();
      ++generation;clearTimeout(timer);timer=null;
      pauseContactNames();
      try{socket?.end(undefined)}catch{}
      await saveQueue;
      attempts=0;await connect();
      return reply(res,202,{status:"connecting"});
    }
    if(path==="/internal/disconnect"&&req.method==="POST"){
      const input=await collectBody(req);
      if(input.confirm!=="DESCONECTAR"||status!=="connected"||!socket)
        return reply(res,409,{error:"disconnect_not_allowed"});
      const old=socket;
      ++generation;clearTimeout(timer);timer=null;
      pauseContactNames();
      status="disconnecting";
      try{await old.logout()}catch{
        status="error";lastError="Não foi possível confirmar a desconexão.";
        return reply(res,503,{error:"disconnect_failed"});
      }
      socket=null;status="logged_out";pairedNumber="";
      contactNames.purge();
      connectedProfile={name:null,photoUrl:null,updatedAt:null};
      return reply(res,200,{status:"logged_out"});
    }
    if(path==="/internal/qr"&&req.method==="GET"){
      if(status==="connected")return reply(res,200,{status:"connected",image:null,expiresIn:0});
      if(!qr||!freshQR(qrAt))
        return reply(res,200,{status:visibleStatus(),image:null,expiresIn:0,
          waitingSeconds:Math.floor((Date.now()-connectStartedAt)/1000),
          lastError,disconnectCode:lastDisconnectCode});
      const image=await QRCode.toDataURL(qr,{errorCorrectionLevel:"M",margin:2,width:370});
      return reply(res,200,{status:"qr_ready",image,
        expiresIn:Math.ceil((45000-(Date.now()-qrAt))/1000)});
    }
    if(path==="/internal/pair"&&req.method==="POST")
      return reply(res,200,await pair((await collectBody(req)).phone));
    if(path==="/internal/recover"&&req.method==="POST"){
      if(status!=="logged_out"&&status!=="pair_failed")
        return reply(res,409,{error:"not_logged_out"});
      ++generation;
      pauseContactNames({purge:true});
      clearTimeout(timer);timer=null;
      try{socket?.end(undefined)}catch{}
      socket=null;
      // Keep revoked credentials isolated for later secure cleanup: never
      // overwrite them with fresh auth or print their contents.
      authDir=SESSION_DIR+"-renew-"+randomUUID();
      fs.mkdirSync(authDir,{recursive:true,mode:0o700});
      fs.writeFileSync(ACTIVE_MARKER,authDir,{mode:0o600});
      qr="";qrAt=0;pairingCode="";pairAt=0;pairedNumber="";
      messages.splice(0);seen.clear();sendCapacity.clear();
      retryMessages.clear();deliveryReceipts.clear();deliveryStart.clear();
      durableInbox=makeDurableInbox({directory:authDir,secret:KEY});
      connectedStore=makeConnectedStore({directory:authDir,secret:KEY});
      contactNames=makeContactNames({directory:authDir,secret:KEY});
      connectedProfile={name:null,photoUrl:null,updatedAt:null};
      profileFetchedAt=0;repliedToCallers.clear();
      const restored=durableInbox.load();
      for(const row of restored){messages.push(row);seen.add(row.id)}
      lastInboundAt=0;pendingNotificationsReceived=false;inboundSinceConnect=false;
      recentAppends=0;
      encryptedRetry=makeEncryptedRetryStore({
        directory:joinPath(authDir,"retry-encrypted"),secret:KEY,
        serialize:obj=>JSON.stringify(obj,BufferJSON.replacer),
        deserialize:str=>JSON.parse(str,BufferJSON.reviver)
      });
      recipientInspections.clear();resendCounterByMessage.clear();
      msgRetryCounterCache.flushAll();
      lastError=null;attempts=0;everConnected=false;lastDisconnectCode=null;
      await connect();
      return reply(res,202,{status:"connecting"});
    }
    if(path==="/internal/reconnect"&&req.method==="POST"){
      if(req.headers['x-orion-canonical-prepare']==='1'&&
          !['qr_ready','qr_expired','qr_delayed','pair_expired','stopped'].includes(visibleStatus()))
        return reply(res,409,{error:"restart_not_allowed"});
      if(status==="connected")return reply(res,409,{error:"already_connected"});
      if(status==="logged_out"||status==="pair_failed")
        return reply(res,409,{error:"not_logged_out"});
      if(status==="pairing"&&Date.now()-pairAt<90000)
        return reply(res,409,{error:"pairing_in_progress"});
      // Invalidate old listeners first; stale connection.update cannot
      // race us and overwrite fresh QR data.
      ++generation;clearTimeout(timer);timer=null;
      pauseContactNames();
      try{socket?.end(undefined)}catch{}
      await saveQueue;
      attempts=0;await connect();
      return reply(res,202,{status:"connecting"});
    }
    if(path==="/internal/chats"&&req.method==="GET"){
      const grouped=new Map();
      for(const item of messages) grouped.set(item.phone,{phone:item.phone,
        preview:item.body.slice(0,90),at:item.at});
      return reply(res,200,{items:[...grouped.values()].sort((a,b)=>b.at-a.at).slice(0,80).map(row=>{
        const name=status==='connected'?contactNames.lookup(row.phone):null;
        return {...row,display_name:name?.display_name||null,name_source:name?.name_source||null};
      })});
    }
    if(path==="/internal/contact-names"&&req.method==="POST"){
      const input=await collectBody(req);
      requestedContactPhones(input);
      return reply(res,200,status==='connected'?contactNames.requested(input):{account_phone:null,items:[]});
    }
    if(path==="/internal/contact-name-matches"&&req.method==="POST"){
      const input=await collectBody(req);
      requestedContactQuery(input);
      return reply(res,200,status==='connected'?contactNames.matches(input):{account_phone:null,phones:[]});
    }
    if(path==="/internal/messages"&&req.method==="GET"){
      const phone=validPhone(uri.searchParams.get("phone"));
      return reply(res,200,{items:messages.filter(x=>x.phone===phone).slice(-100)});
    }
    if(path==="/internal/recipient"&&req.method==="GET"){
      const phone=validPhone(uri.searchParams.get("phone"));
      return reply(res,200,await recipientInspection(phone));
    }
    if(path==="/internal/send"&&req.method==="POST"){
      if(legacyCentral.configured())return reply(res,423,{error:'central_required'});
      const body=await collectBody(req);
      return reply(res,200,await send(body.phone,body.text));
    }
    return reply(res,404,{error:"not_found"});
  }catch(error){
    const message=String(error?.message||"Falha na operação");
    const safeMessages=["Formato inesperado de código","Geração em andamento",
      "Aguarde a disponibilidade do QR e tente novamente",
      "Sessão revogada: reinicie o vínculo pelo WhatsApp",
      "O WhatsApp não está conectado",
      "Mensagem deve ter entre 1 e 1200 caracteres",
      "Aguarde um minuto antes de enviar mais mensagens",
      "Número inválido. Informe DDI + DDD + telefone",
      "already_connected"];
    const known=safeMessages.find(x=>message.includes(x));
    console.info("Orion QR: operação recusada (sem dados pessoais)");
    return reply(res,known==="already_connected"?409:422,
      {error:known||"Falha de conexão ou WhatsApp temporariamente indisponível"});
  }
});
server.headersTimeout=10000;
server.requestTimeout=15000;
server.listen({port:PORT, host:"::", ipv6Only:false},()=>{
  console.info("Orion QR: serviço privado em IPv6/dual-stack, porta "+PORT);
  const resumeChannels=async()=>{
    if(stopping)return;
    if(legacyCentral.configured())await legacyCentral.resume().catch(error=>console.warn('Orion QR: adoção da sessão principal pendente '+JSON.stringify({category:['legacy_adoption_not_ready','invalid_legacy_adoption','unsafe_legacy_broker'].includes(error?.message)?error.message:'unavailable',httpStatus:Number.isInteger(error?.status)&&error.status>=400&&error.status<=599?error.status:null})));
    if(!stopping&&legacyCentral.transportCurrent()&&status==='stopped')void connect();
    await channelRoutes.resume().catch(()=>console.warn('Orion QR: retomada dos dispositivos pendente'));
  };
  void connect();
  void resumeChannels();
  resumeTimer=setInterval(resumeChannels,legacyCentral.configured()?5000:30000);resumeTimer.unref();
});

// Stop admitting effects and fence sockets before draining HTTP requests.
// A forced end never deletes credentials or changes an uncertain send to sent.
let shutdownPromise;
function shutdown(){
  if(shutdownPromise)return shutdownPromise;
  stopping=true;clearInterval(resumeTimer);stopPrimaryTransport('shutdown');channelRoutes.close();
  shutdownPromise=(async()=>{
    const closed=new Promise(resolve=>server.close(resolve));server.closeIdleConnections?.();
    let deadline;
    try{
      await Promise.race([Promise.allSettled([closed,saveQueue,...assistantQueues.values()]),
        new Promise(resolve=>{deadline=setTimeout(resolve,8000);})]);
    }finally{clearTimeout(deadline);server.closeAllConnections?.();}
  })();
  return shutdownPromise;
}
for(const signal of ['SIGTERM','SIGINT'])process.once(signal,()=>{void shutdown().finally(()=>process.exit(0));});
