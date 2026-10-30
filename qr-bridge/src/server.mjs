import {normalizeInbound,updateEvent,callEvent,safetyControl,queueControl} from './inbound-event.mjs';
/**
 * Orion Linked Device — aparelho de teste único, transporte não oficial.
 * Apenas mensagens privadas recebidas após conexão; nada de histórico antigo,
 * mídia, grupos, auto-respostas ou envios em massa.
 * Mensagens recentes ficam apenas na RAM; sessão Baileys cifrada no volume.
 */
import http from "node:http";
import {databaseBackup} from './database-backup-store.mjs';
import path from "node:path";
import fs from "node:fs";
import {timingSafeEqual,randomUUID,createHmac} from "node:crypto";
import makeWASocket,{DisconnectReason,Browsers,DEFAULT_CONNECTION_CONFIG,useMultiFileAuthState,fetchLatestWaWebVersion,makeCacheableSignalKeyStore,generateWAMessage,generateMessageIDV2,BufferJSON,downloadMediaMessage} from "@whiskeysockets/baileys";
import QRCode from "qrcode";
import NodeCache from "@cacheable/node-cache";
import {validPhone,normalizeFromJid,extractText,resolveMessagePhone,freshQR,formatPairingCode,connectionPhase,reconnectPlan} from "./core.mjs";
import {sendConnectionNoticeOnce,noticeState} from "./connection-alert.mjs";
import {makeMessageRetryStore,deliveryFromAck,advanceDelivery} from "./message-retry-store.mjs";
import {makeEncryptedRetryStore} from "./encrypted-retry-store.mjs";
import {sendStoredText} from "./reliable-send.mjs";
import {makeSendCapacity} from "./send-capacity.mjs";
import {makeConversationIntake} from "./conversation-intake.mjs";
import {makeAssistantTyping} from "./assistant-typing.mjs";
import {inspectLookup,inspectDelivery} from "./recipient-diagnostics.mjs";
import {makeDurableInbox} from "./durable-inbox.mjs";
import {shouldIngestUpsert,timestampMs,ingestionHealth} from "./linked-session-health.mjs";
import {makeConnectedStore} from "./connected-store.mjs";
import {makeContactNames,installContactNameEvents,requestedContactPhones,requestedContactQuery} from './contact-names.mjs';
import {makeContactNameSync} from './contact-name-forward.mjs';
import {transcriberConfig,cleanPictureUrl,transcribeAudio,handleCallOffers} from "./connected-features.mjs";
import {protectSignalSessionLogs} from "./signal-log-safety.mjs";
import {forwardAssistant,registerAssistantIngress} from "./ai-forward.mjs";
import {isBookingProtocolMessage,fetchBookingProtocolConfirmation,resolveBookingSenderPhone} from "./appointment-confirmation.mjs";
import {allowAssistantRecipient,allowAssistantAudio,pilotNumber} from "./ai-pilot.mjs";
import {deliveryPolicy} from "./pilot-send-policy.mjs";
import {resolvePilotRecipient,makePilotDeviceCache} from "./pilot-recipient.mjs";
import {transcribeAssistantAudio} from "./ai-audio.mjs";
import {makeTransportDiagnostics,receiptDiagnosticDetails,usyncDeviceDomainCounts,encryptionTargetDetails,observeSignalRepository} from "./transport-diagnostics.mjs";
import {backupAuth} from "./auth-backup.mjs";
import {createChannelRoutes} from './channel-routes.mjs';
import {createBaileysChannelSession} from './channel-session.mjs';
import {createLegacyCentral} from './legacy-central.mjs';
import {testReplyFor} from './trigger-test.mjs';
import {makeTestFallbackText,sendTestCloudCta,selectTestCtaMode} from './test-url-button.mjs';

const legacyCentral=createLegacyCentral({
  start:(id,key)=>channelRoutes.adopt(id,key),send:(id,key,payload)=>channelRoutes.send(id,key,payload),
  sessionDigest:()=>status==='connected'?createHmac('sha256',KEY).update('orion:preserved-primary-session:v1\0'+pairedNumber).digest('hex'):null,
  status:()=>status,rawSend:(phone,text,options)=>sendLegacyRaw(phone,text,options)});
const channelRoutes=createChannelRoutes({sessionFactory:context=>context.legacyTransport?
  legacyCentral.attach(context):createBaileysChannelSession(context)});

const PORT=Number(process.env.PORT || 3000);
const SESSION_DIR=process.env.QR_SESSION_DIR || "/data/auth";
const KEY=process.env.QR_BRIDGE_TOKEN || "";
const transport=makeTransportDiagnostics({secret:KEY,
  emit:event=>console.info('Orion transport '+JSON.stringify(event))});
const logger=transport.logger;
// libsignal-node 6.x logs full SessionEntry objects including ratchet keys.
// Redact those exact calls before any socket/session operation; keep normal logs.
protectSignalSessionLogs();
// Baileys 6.7.24 accepts CacheStore here. A bounded TTL protects against
// stale Signal retry state; it does NOT change long-lived auth credentials.
const msgRetryCounterCache=new NodeCache({
  stdTTL:600,checkperiod:120,useClones:false
});
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
const authBackup=backupAuth({directory:authDir,backupDirectory:'/data/orion-auth-backups',
  secret:KEY,label:'before-transport-diagnostics-v1'});
console.info('Orion auth backup '+JSON.stringify(authBackup));
const socketDeviceCaches=new WeakMap();
let newLinkNoticeAuthDir=null;
let socket=null,generation=0,timer=null,attempts=0,status="starting",
  connectStartedAt=Date.now(),lastQrAt=0,lastDisconnectCode=null,everConnected=false,
  pairingRequestedForGeneration=-1, pairPhone="",saveQueue=Promise.resolve(),
  notifiedGeneration=-1,
  qr="",qrAt=0,lastError=null,pairedNumber="",pairingCode="",pairAt=0,requesting=false;
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
  save(key,message){
    const memory=retryMessages.save(key,message);
    const encrypted=encryptedRetry.save(key,message);
    return memory && encrypted;
  },
  get(key){
    const cached=retryMessages.get(key);
    if(cached)return cached;
    const persisted=encryptedRetry.get(key);
    if(persisted)retryMessages.save(key,persisted);
    return persisted;
  }
};
const retryDiagnostics={
  cacheHits:0,cacheMisses:0,prepared:0,relayAccepted:0,
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
  if(status==="pairing"&&pairAt>0&&Date.now()-pairAt>=90000)
    return "pair_expired";
  return connectionPhase(status,qrAt,connectStartedAt);
}
function stats(){
  const phase=visibleStatus();
  return {status:phase,hasQr:phase==="qr_ready"&&!!qr&&freshQR(qrAt),
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
  const input=Buffer.from(String(req.headers["x-orion-bridge-key"]||""));
  const expected=Buffer.from(KEY);
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
function installListeners(client,mine,saveCreds,notifyNewLink=false){
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
    saveQueue=saveQueue.then(()=>saveCreds()).catch(()=>{
      lastError="Falha ao salvar sessão WhatsApp. Não tente novo vínculo sem verificar o volume.";
      console.warn("Orion QR: falha ao salvar credenciais (dados omitidos)");
    });
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
  const intakeCurrent=()=>mine===generation&&socket===client&&status==='connected';
  const typingPresence=makeAssistantTyping({socket:client,pilotPhone:pilotNumber,isConnected:intakeCurrent,
    isAllowed:phone=>allowAssistantRecipient(phone,connectedStore.settings())});
  const groupSetting=(name,fallback,max)=>{
    const raw=process.env[name],value=Number(raw);
    return raw!==undefined&&Number.isSafeInteger(value)&&value>=0&&value<=max?value:fallback;
  };
  const settleMs=groupSetting('QR_AI_GROUP_WAIT_MS',350,2000);
  const revisionEnabled=process.env.QR_AI_INTAKE_REVISION_ENABLED==='1'||legacyCentral.configured();
  const summaryReviewRequired=new Map();
  const inboundIntake=makeConversationIntake({
    settleMs,maxGroupMs:Math.max(settleMs,groupSetting('QR_AI_GROUP_MAX_WAIT_MS',1200,5000)),
    isDuplicate:({messageId})=>seen.has('wa:'+messageId),
    onRejected:async batch=>{
      for(const id of batch.messageIds)transport.record('inbound_batch_rejected',id,{category:batch.reason});
      if(batch.reason==='stale'||!batch.isCurrent()||!intakeCurrent())return;
      const entries=batch.messageIds.map(id=>inboundMetadata.get(id)).filter(item=>item?.allowed);
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
      const current=()=>intakeCurrent()&&batch.isCurrent()&&!!phone&&
        entries.every(item=>item?.allowed&&item.phone===phone)&&
        (legacyCentral.configured()?legacyCentral.allowed(phone):allowAssistantRecipient(phone,connectedStore.settings()));
      if(!current())return;
      return typingPresence.run({phone,chatJid:entries[0]?.jid,isCurrent:current},async typing=>{
      if(legacyCentral.configured()){
        if(!allowAssistantRecipient(phone,connectedStore.settings()))return;
        return legacyCentral.forward({phone,message_id:traceId,message_ids:batch.messages.map(item=>item.id),
          text:batch.text,intake_revision:batch.intakeRevision,...(entries.some(item=>item.event)?{envelope:entries.findLast(item=>item.event)?.event}:{})},
          {noticeProofs:entries.map(item=>item.noticeProof),isCurrent:current,beforeReply:value=>typing.beforeReply(value)});
      }
      transport.record('ai_forward',traceId,{count:batch.messages.length});
      let answer;
      try{answer=await forwardAssistant({apiUrl:process.env.QR_AI_API_URL,token:KEY,phone,
        messageId:traceId,messageIds:batch.messages.map(item=>item.id),text:batch.text,intakeRevision:batch.intakeRevision,
        profile:namesStore.profile(phone),isCurrent:current,...(entries.some(item=>item.event)?{envelope:entries.findLast(item=>item.event)?.event}:{}),
        requireSummaryReview:revisionEnabled?summaryReviewRequired.get(phone)!==false:undefined});}
      catch(error){if(revisionEnabled)summaryReviewRequired.set(phone,true);transport.record('ai_forward_failed',traceId);throw error;}
      if(answer?.state==='superseded'&&answer?.not_committed===true){
        if(revisionEnabled)summaryReviewRequired.set(phone,true);return answer;
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
        const sent=await send(phone,answer,{waitForCapacity:true,isCurrent:current});
        if(revisionEnabled)summaryReviewRequired.set(phone,!sent?.accepted||!current());
        transport.record(sent?.accepted?'ai_relay_returned':'ai_send_unconfirmed',traceId);
      }catch(error){if(revisionEnabled)summaryReviewRequired.set(phone,true);transport.record('ai_send_failed',traceId);throw error;}
      });
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
  async function receive({messages:incoming,type}){
    transport.record('upsert_batch',null,{count:incoming?.length||0,type});
    if(!intakeCurrent())return;
    const jobs=[];
    for(const message of incoming||[]){
      const traceId=message.key?.id;
      if(!shouldIngestUpsert(type,message.messageTimestamp)){
        transport.record('inbound_filtered_age',traceId,{type});continue;
      }
      if(message.key?.id&&message.message)retryMessages.save(message.key,message.message);
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
      const normalized=normalizeInbound(message);if(normalized.silent)continue;
      // Central booking confirmation is a separate deterministic lane.
      // Only a new private text containing a protocol enters it, independent
      // of the AI allowlist. Any other message follows the unchanged pilot.
      const centralEnabled=process.env.ORION_CENTRAL_CONFIRMATION_REPLY_ENABLED==='1';
      const freshForCentral=type==='notify'||(type==='append'&&
        (timestampMs(message.messageTimestamp)||0)>=Date.now()-120000);
      if(centralEnabled&&freshForCentral&&normalized.event.kind==='text'&&
          isBookingProtocolMessage(normalized.text)){
        const key=String(mine)+':'+traceId;
        if(handledBookingProtocols.has(key)||seen.has('wa:'+traceId)){
          transport.record('booking_protocol_duplicate',traceId);continue;
        }
        handledBookingProtocols.add(key);
        if(handledBookingProtocols.size>2000)
          handledBookingProtocols.delete(handledBookingProtocols.values().next().value);
        const task=(async()=>{
          let sendAttempted=false,phase='identity',presenceLease=null;
          try{
            const phone=await resolveBookingSenderPhone(message.key,client,{isCurrent:intakeCurrent});
            if(!intakeCurrent())return;
            if(!phone){
              transport.record('booking_protocol_identity_unresolved',traceId,{
                kind:String(message.key.remoteJid||'').endsWith('@lid')?'lid':'pn'});
              return;
            }
            transport.record('booking_protocol_identity_verified',traceId);
            phase='journal';
            const now=timestampMs(message.messageTimestamp)||Date.now();
            pushMessage({id:'wa:'+traceId,phone,body:normalized.text.slice(0,2000),
              direction:'in',at:now});
            lastInboundAt=Date.now();inboundSinceConnect=true;
            phase='backend';
            // A verified protocol sender gets genuine composing presence while
            // its own booking is checked. This is independent of chatbot AI.
            presenceLease=makeAssistantTyping({
              socket:client,pilotPhone:()=>phone,isConnected:intakeCurrent,
              isAllowed:recipient=>recipient===phone,
              transactional:true,maxPauseMs:1500
            }).begin({phone,chatJid:message.key.remoteJid,isCurrent:intakeCurrent});
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
            await presenceLease.beforeReply(response);
            if(!intakeCurrent())return;
            sendAttempted=true; // Never retry after an ambiguous WhatsApp operation.
            const result=await sendLegacyRaw(phone,response,{
              waitForCapacity:true,isCurrent:intakeCurrent});
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
          }finally{presenceLease?.stop();}
        })();
        jobs.push(task);
        continue;
      }
      // Explicit opt-in pilot: exact keyword, one verified phone, never fromMe.
      // Do not forward the test command to the customer-facing assistant.
      if(normalized.text?.trim().toUpperCase()==='ORIONTESTE' &&
          process.env.QR_TRIGGER_TEST_PHONE && type==='notify'){
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
      if(revisionEnabled&&noticePending>=40){enqueueOverflow(message,normalized,conversationKey);continue;}
      const epoch=rejectedEpoch.get(conversationKey)||0;
      const metadata={allowed:false,phone:null,jid:message.key.remoteJid,...(normalized.special?{event:normalized.event}:{})};
      const current=()=>intakeCurrent()&&(rejectedEpoch.get(conversationKey)||0)===epoch&&(!metadata.allowed||
        (legacyCentral.configured()?legacyCentral.allowed(metadata.phone):allowAssistantRecipient(metadata.phone,connectedStore.settings())));
      let identityTask;
      const resolveIdentity=()=>identityTask||=(async()=>{
        const phone=await resolveMessagePhone(message.key,client);
        if(!current())return null;
        if(!phone){transport.record('inbound_unresolved',traceId);return null;}
        if(namesStore.observeResolvedMessage(message,phone,{historical:type==='append'}))namesSync.changed();
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
          if(!phone||!current()||!metadata.allowed)return {skip:true};
          const body=normalized.text,media=message.message.audioMessage||message.message.ephemeralMessage?.message?.audioMessage;
          if(legacyCentral.configured()){
            const proof=await legacyCentral.inbound({phone,message_id:traceId,text:body||'',kind:media?'audio':body?'text':'unsupported',
              ...(normalized.special?{envelope:normalized.event}:{})});
            metadata.noticeProof=proof;return proof;
          }
          if(!normalized.special&&!body&&(!media||!assistantAudioEnabled()||!allowAssistantAudio(phone,connectedStore.settings())))return {skip:true};
          return registerAssistantIngress({apiUrl:process.env.QR_AI_API_URL,token:KEY,phone,messageId:traceId,control:normalized.event.control||safetyControl(body)});
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
          const at=timestampMs(message.messageTimestamp)||Date.now();
          const row={id:'wa:'+traceId,phone,body:body?String(body).slice(0,2000):'[Áudio recebido]',direction:'in',at};
          pushMessage(row);
          lastInboundAt=Date.now();inboundSinceConnect=true;if(type==='append')recentAppends++;
          if(!metadata.allowed){transport.record('pilot_denied',traceId);return {skip:true};}
          transport.record('pilot_allowed',traceId);
          if(normalized.special)return {text:body||'[Evento de mensagem recebido]'};
          if(!body){
            if(!assistantAudioEnabled()||!allowAssistantAudio(phone,connectedStore.settings()))return {skip:true};
            const txt=await transcribeAssistantAudio(message,{client,downloadMediaMessage,phone,token:KEY,
              apiUrl:process.env.QR_AI_API_URL,logger,signal});
            if(!current()||signal.aborted)return {skip:true};
            if(!txt)throw Error('audio_not_understood');
            row.body='[Áudio transcrito] '+txt;persistInbox();body=txt;
          }
          return {text:body};
        }});
      const jobKey=String(mine)+':'+traceId;
      const job=task.then(async result=>{
        if(result.state==='duplicate')transport.record('inbound_duplicate',traceId);
        else if(result.state==='overloaded'){transport.record('inbound_queue_full',traceId);await noticeTask?.catch(()=>{});if(current())enqueueOverflow(message,normalized,conversationKey);}
        else if(result.state==='consume_failed')console.warn('Orion QR: atendimento IA não concluído (dados omitidos)');
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
      status="connected";everConnected=true;qr="";qrAt=0;pairingCode="";pairAt=0;attempts=0;
       namesStore.bindIdentity(client.user);namesSync.start();
       try{connectedStore.setIdentity(client.user?.id)}catch{
         console.warn("Orion QR: configuração criptografada indisponível")
       }
       void refreshConnectedProfile(client);
      connectedSince=Date.now();inboundSinceConnect=false;
      if(update.receivedPendingNotifications===true)pendingNotificationsReceived=true;
      pairedNumber=normalizeFromJid(client.user?.id)||"";
      lastError=null;console.info("Orion QR: dispositivo conectado");
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
                if(process.env.QR_RELIABLE_SEND_V2!=="1")return client.sendMessage(jid,{text});
                return sendStoredText({
                  jid,text,store:retryStore,freshDevices:false,
                  makeMessage:async (recipient,messageText)=>generateWAMessage(
                    recipient,{text:messageText},{
                      userJid:client.user?.id,logger,
                      messageId:generateMessageIDV2(client.user?.id)
                    }),
                  relay:(recipient,message,opts)=>client.relayMessage(recipient,message,opts)
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
      pauseContactNames({purge:Number(update.lastDisconnect?.error?.output?.statusCode)===DisconnectReason.loggedOut});
      qr="";qrAt=0;pairingCode="";pairAt=0;
      connectedSince=0;pendingNotificationsReceived=false;inboundSinceConnect=false;
      const code=Number(update.lastDisconnect?.error?.output?.statusCode);
      lastDisconnectCode=Number.isFinite(code)?code:null;
      console.info("Orion QR: conexão encerrada (código="+(lastDisconnectCode??"desconhecido")+")");
      if(code===DisconnectReason.loggedOut){
        if(newLinkNoticeAuthDir===authDir)newLinkNoticeAuthDir=null;
        status=everConnected?"logged_out":"pair_failed";
        lastError=everConnected?
          "WhatsApp revogou a conexão. Use Refazer vínculo.":
          "O WhatsApp rejeitou o vínculo. Um novo código exige outra sessão.";
        return;
      }
      status="reconnecting";
      lastError=code===DisconnectReason.restartRequired?
        "WhatsApp solicitou reinicialização após pareamento; restaurando sessão.":
        "Conexão interrompida; tentando restabelecer.";
      const decision=reconnectPlan(code,attempts,DisconnectReason.loggedOut,
        DisconnectReason.restartRequired);
      const delay=decision.delayMs;
      if(code!==DisconnectReason.restartRequired)attempts++;
      timer=setTimeout(()=>{
        if(mine===generation){
          void saveQueue.finally(()=>connect().catch(()=>{}));
        }
      },delay);
    }
  });
}
async function connect(){
  const mine=++generation;
  pauseContactNames();
  const namesForConnection=contactNames,selectedAuthDir=authDir;
  clearTimeout(timer);timer=null;qr="";qrAt=0;pairingCode="";pairAt=0;
  status="connecting";connectStartedAt=Date.now();pairingRequestedForGeneration=-1;
  pendingNotificationsReceived=false;inboundSinceConnect=false;
  try{
    const {state,saveCreds}=await useMultiFileAuthState(selectedAuthDir);
    if(mine!==generation||namesForConnection!==contactNames||selectedAuthDir!==authDir)return;
    namesForConnection.bindIdentity(state.creds.me);
    const originalSet=state.keys.set;
    state.keys.set=async data=>{
      try{
        await originalSet(data);
        for(const category of Object.keys(data))transport.record('auth_keys_saved',null,{category,count:Object.keys(data[category]||{}).length});
      }catch(error){transport.record('auth_write_failed');throw error;}
    };
    // Prefer the WhatsApp Web client revision instead of a months-old
    // bundled default. Official fetch may fail; in that case use bundled
    // Baileys version rather than blocking the QR forever.
    let currentVersion;
    if(process.env.QR_WA_VERSION_AUTO!=="0"){
      try{
        const result=await fetchLatestWaWebVersion({timeout:3500});
        const v=result.version;
        if(result.isLatest&&Array.isArray(v)&&v.length===3&&
          v.every(x=>Number.isSafeInteger(x)&&x>0))currentVersion=v;
      }catch{}
    }
    if(mine!==generation||namesForConnection!==contactNames||selectedAuthDir!==authDir)return;
    const deviceCache=makePilotDeviceCache(new NodeCache({stdTTL:300,useClones:false}));
    const client=makeWASocket({
      userDevicesCache:deviceCache,
      auth:{creds:state.creds,keys:makeCacheableSignalKeyStore(state.keys,logger)},
      makeSignalRepository:observeSignalRepository(DEFAULT_CONNECTION_CONFIG.makeSignalRepository,jid=>{
        if(mine===generation)transport.record('signal_encrypt_target',null,encryptionTargetDetails(jid,state.creds.me));
      }),
      msgRetryCounterCache,
      maxMsgRetryCount:5,
      getMessage:async key=>{
        const content=retryStore.get(key);
        transport.record('get_message',key?.id,{cached:!!content});
        if(content)retryDiagnostics.cacheHits++;
        else retryDiagnostics.cacheMisses++;
        return content;
      },
      browser:Browsers.windows("Chrome"),
      ...(currentVersion?{version:currentVersion}:{}),
      printQRInTerminal:false,markOnlineOnConnect:false,syncFullHistory:false,logger});
    socketDeviceCaches.set(client,deviceCache);
    // Do not announce a new link while merely restoring registered auth.
    // The memory-only intent survives Baileys' post-pairing restartRequired.
    if(state.creds.registered!==true&&process.env.ORION_NOTIFY_SELF_ON_LINK==="1")newLinkNoticeAuthDir=selectedAuthDir;
    const notifyNewLink=newLinkNoticeAuthDir===selectedAuthDir;
    socket=client;installListeners(client,mine,saveCreds,notifyNewLink);
  }catch{
    status="error";lastError="Não foi possível iniciar o serviço do WhatsApp.";
    console.warn("Orion QR: erro genérico ao iniciar");
    timer=setTimeout(()=>{if(mine===generation)connect().catch(()=>{})},20000);
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
async function sendLegacyRaw(phone,text,{waitForCapacity=false,isCurrent=()=>true,transactionalTyping=false}={}){
  const target=validPhone(phone);
  if(status!=="connected"||!socket)throw Error("O WhatsApp não está conectado");
  if(typeof text!=="string"||!text.trim()||text.length>1200)
    throw Error("Mensagem deve ter entre 1 e 1200 caracteres");
  const selectedSocket=socket;
  const selectedGeneration=generation;
  const current=()=>socket===selectedSocket&&generation===selectedGeneration&&status==='connected'&&isCurrent();
  await sendCapacity.acquire({wait:waitForCapacity,isCurrent:current});
  if(!current())throw Error('A conexão ou autorização mudou antes do envio. Nenhuma mensagem foi enviada.');
  let result;
  const {reliable,freshDevices}=deliveryPolicy(target);
  const recipientProof=await resolvePilotRecipient({phone:target,pilotPhone:pilotNumber(process.env),
    enabled:reliable&&process.env.QR_PILOT_USE_VERIFIED_LID==='1',socket:selectedSocket,deviceCache:socketDeviceCaches.get(selectedSocket),
    isCurrent:current});
  if(!current())
    throw Error('A conexão mudou antes do envio. Nenhuma mensagem foi enviada.');
  const {jid,kind}=recipientProof;
  let presenceLease=null;
  try{
    if(transactionalTyping){
      // Only an admitted, destination-authorized transactional notice sends
      // composing. Never emit it while a queue is waiting or a chat is revoked.
      presenceLease=makeAssistantTyping({
        socket:selectedSocket,pilotPhone:()=>target,isConnected:current,
        isAllowed:recipient=>recipient===target,
        transactional:true,maxPauseMs:1400
      }).begin({phone:target,chatJid:jid,isCurrent:current});
      await presenceLease.beforeReply(text);
      if(!current())throw Error('A conexão mudou antes do envio. Nenhuma mensagem foi enviada.');
    }
  if(reliable){
    result=await sendStoredText({
      jid,recipientProof,text:text.trim(),store:retryStore,freshDevices,
      isCurrent:current,
      makeMessage:async (recipient,messageText)=>generateWAMessage(
        recipient,{text:messageText},{
          userJid:selectedSocket.user?.id,logger,
          messageId:generateMessageIDV2(selectedSocket.user?.id)
        }),
      relay:(recipient,message,opts)=>selectedSocket.relayMessage(recipient,message,opts),
      onPrepared:msg=>{
        retryDiagnostics.prepared++;
        deliveryReceipts.set(msg.key.id,"pending");
        deliveryStart.set(msg.key.id,Date.now());
        transport.record('send_prepared',msg.key.id,{kind});
        pushMessage({id:'out:'+msg.key.id,phone:target,body:text.trim(),direction:'out',at:Date.now(),delivery:'pending'});
      }
    });
  }else{
    result=await selectedSocket.sendMessage(jid,{text:text.trim()});
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
  return {accepted:true,delivery:deliveryReceipts.get(result.key.id)||"pending",id,trace:transport.tag(result.key.id)};
  }finally{presenceLease?.stop();}
}

const server=http.createServer(async(req,res)=>{
  const uri=new URL(req.url||"/","http://internal");
  const path=uri.pathname;
  if(path==="/health"&&req.method==="GET")
    return reply(res,200,{status:"ok",bridge:"linked-device"});
  // Additional channels authenticate their own credential at the SQL broker;
  // the shared legacy pilot token never grants access to these routes.
  if(path.startsWith('/internal/channels/')&&await channelRoutes(req,res))return;
  if(!auth(req))return reply(res,401,{error:"unauthorized"});
  try{
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
      await connect();
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
        directory:path.join(authDir,"retry-encrypted"),secret:KEY,
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
      await connect();
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
  connect().catch(()=>{});
  const resumeChannels=async()=>{
    if(legacyCentral.configured())await legacyCentral.resume().catch(()=>console.warn('Orion QR: adoção da sessão principal pendente'));
    await channelRoutes.resume().catch(()=>console.warn('Orion QR: retomada dos dispositivos pendente'));
  };
  void resumeChannels();
  setInterval(resumeChannels,legacyCentral.configured()?5000:30000).unref();
});
