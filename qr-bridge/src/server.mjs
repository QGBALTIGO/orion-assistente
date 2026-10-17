/**
 * Orion Linked Device — aparelho de teste único, transporte não oficial.
 * Apenas mensagens privadas recebidas após conexão; nada de histórico antigo,
 * mídia, grupos, auto-respostas ou envios em massa.
 * Mensagens recentes ficam apenas na RAM; sessão Baileys cifrada no volume.
 */
import http from "node:http";
import path from "node:path";
import fs from "node:fs";
import {timingSafeEqual,randomUUID} from "node:crypto";
import makeWASocket,{DisconnectReason,Browsers,DEFAULT_CONNECTION_CONFIG,useMultiFileAuthState,fetchLatestWaWebVersion,makeCacheableSignalKeyStore,generateWAMessage,generateMessageIDV2,BufferJSON,downloadMediaMessage} from "@whiskeysockets/baileys";
import QRCode from "qrcode";
import NodeCache from "@cacheable/node-cache";
import {validPhone,normalizeFromJid,extractText,resolveMessagePhone,freshQR,formatPairingCode,connectionPhase,reconnectPlan} from "./core.mjs";
import {sendConnectionNoticeOnce,noticeState} from "./connection-alert.mjs";
import {makeMessageRetryStore,deliveryFromAck,advanceDelivery} from "./message-retry-store.mjs";
import {makeEncryptedRetryStore} from "./encrypted-retry-store.mjs";
import {sendStoredText} from "./reliable-send.mjs";
import {makeSendCapacity} from "./send-capacity.mjs";
import {inspectLookup,inspectDelivery} from "./recipient-diagnostics.mjs";
import {makeDurableInbox} from "./durable-inbox.mjs";
import {shouldIngestUpsert,timestampMs,ingestionHealth} from "./linked-session-health.mjs";
import {makeConnectedStore} from "./connected-store.mjs";
import {transcriberConfig,cleanPictureUrl,transcribeAudio,handleCallOffers} from "./connected-features.mjs";
import {protectSignalSessionLogs} from "./signal-log-safety.mjs";
import {forwardAssistant} from "./ai-forward.mjs";
import {allowAssistantRecipient,allowAssistantAudio,pilotNumber} from "./ai-pilot.mjs";
import {deliveryPolicy} from "./pilot-send-policy.mjs";
import {resolvePilotRecipient,makePilotDeviceCache} from "./pilot-recipient.mjs";
import {transcribeAssistantAudio} from "./ai-audio.mjs";
import {makeTransportDiagnostics,receiptDiagnosticDetails,usyncDeviceDomainCounts,encryptionTargetDetails,observeSignalRepository} from "./transport-diagnostics.mjs";
import {backupAuth} from "./auth-backup.mjs";

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
let socket=null,generation=0,timer=null,attempts=0,status="starting",
  connectStartedAt=Date.now(),lastQrAt=0,lastDisconnectCode=null,everConnected=false,
  pairingRequestedForGeneration=-1, pairPhone="",saveQueue=Promise.resolve(),
  notifiedGeneration=-1,
  qr="",qrAt=0,lastError=null,pairedNumber="",pairingCode="",pairAt=0,requesting=false;
let durableInbox=makeDurableInbox({directory:authDir,secret:KEY});
let connectedStore=makeConnectedStore({directory:authDir,secret:KEY});
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
    phone:status==="connected"?pairedNumber:null,
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
    controlStorage:connectedStore.health()};
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
function installListeners(client,mine,saveCreds){
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
  client.ev.on("messages.upsert",async ({messages:incoming,type})=>{
    transport.record('upsert_batch',null,{count:incoming?.length||0,type});
    if(mine!==generation||status!=="connected")return;
    for(const message of incoming||[]){
      const traceId=message.key?.id;
      if(!shouldIngestUpsert(type,message.messageTimestamp)){
        transport.record('inbound_filtered_age',traceId,{type});continue;
      }
      if(message.key?.id && message.message)retryMessages.save(message.key,message.message);
      if(message.key?.fromMe){transport.record('inbound_own',traceId);continue;}
      if(!message.key?.id||!message.message){transport.record('inbound_no_content',traceId);continue;}
      const phone=await resolveMessagePhone(message.key,client);
      if(!phone){transport.record('inbound_unresolved',traceId,{
        hasPn:!!message.key.senderPn,hasLid:String(message.key.remoteJid||'').endsWith('@lid')});continue;}
      transport.record('inbound_resolved',traceId);
      // Count each valid inbound message, including media without a transcript.
      const at=timestampMs(message.messageTimestamp)||Date.now();
      try{connectedStore.record("wa:"+message.key.id,at)}catch{}
      let body=extractText(message.message);
       if(!body){
         try{
           let txt=null;
           if(assistantAudioEnabled()&&allowAssistantAudio(phone,connectedStore.settings())){
             txt=await transcribeAssistantAudio(message,{
               client,downloadMediaMessage,phone,token:KEY,
               apiUrl:process.env.QR_AI_API_URL,logger
             });
           }
           if(!txt)txt=await transcribeAudio(message,{
             client,downloadMediaMessage,settings:connectedStore.settings(),
             config:transcriberConfig(),logger
           });
           if(txt)body="[Áudio transcrito] "+txt;
         }catch{console.warn("Orion QR: transcrição não concluída (dados omitidos)")}
       }
       if(!body || typeof body!=="string"){transport.record('inbound_no_text',traceId);continue;}
      const id="wa:"+message.key.id;
      if(seen.has(id)){transport.record('inbound_duplicate',traceId);continue;}
      pushMessage({id,phone,body:body.slice(0,2000),
        direction:"in",at});
      lastInboundAt=Date.now();inboundSinceConnect=true;
      if(type==="append")recentAppends++;
      if(allowAssistantRecipient(phone,connectedStore.settings())){
        transport.record('pilot_allowed',traceId);
        // Ordena por destinatário sem bloquear outros clientes; nunca recupera histórico.
        const previous=assistantQueues.get(phone)||Promise.resolve();
        const job=previous.catch(()=>{}).then(async()=>{
          if(mine!==generation||socket!==client||status!=="connected"||
              !allowAssistantRecipient(phone,connectedStore.settings()))return;
          transport.record('ai_forward',traceId);
          let answer;
          try{answer=await forwardAssistant({
            apiUrl:process.env.QR_AI_API_URL,token:KEY,phone,
            messageId:String(message.key.id),text:body,
          });}catch(error){transport.record('ai_forward_failed',traceId);throw error;}
          if(!answer){transport.record('ai_silent',traceId);return;}
          transport.record('ai_answer',traceId);
          if(mine!==generation||socket!==client||status!=="connected"||
              !allowAssistantRecipient(phone,connectedStore.settings()))return;
          try{
            const sent=await send(phone,answer,{waitForCapacity:true,
              isCurrent:()=>mine===generation&&socket===client&&status==="connected"&&
                allowAssistantRecipient(phone,connectedStore.settings())});
            transport.record(sent?.accepted?'ai_relay_returned':'ai_send_unconfirmed',traceId);
          }catch(error){transport.record('ai_send_failed',traceId);throw error;}
          console.info("Orion QR: resposta automática processada (dados omitidos)");
        }).catch(()=>{
          console.warn("Orion QR: atendimento IA indisponível (dados omitidos)");
        }).finally(()=>{
          if(assistantQueues.get(phone)===job)assistantQueues.delete(phone);
        });
        assistantQueues.set(phone,job);
      }else transport.record('pilot_denied',traceId);
    }
  });
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
       try{connectedStore.setIdentity(client.user?.id)}catch{
         console.warn("Orion QR: configuração criptografada indisponível")
       }
       void refreshConnectedProfile(client);
      connectedSince=Date.now();inboundSinceConnect=false;
      if(update.receivedPendingNotifications===true)pendingNotificationsReceived=true;
      pairedNumber=normalizeFromJid(client.user?.id?.split(":")[0]+"@s.whatsapp.net")||"";
      lastError=null;console.info("Orion QR: dispositivo conectado");
      if(process.env.ORION_NOTIFY_SELF_ON_LINK==="1" && notifiedGeneration!==mine){
        notifiedGeneration=mine;
        // Only the authenticated account receives a one-time confirmation.
        // Never send on QR creation, intermediate pairing or failed login.
        setTimeout(async()=>{
          if(mine!==generation || status!=="connected")return;
          try{
            await saveQueue;
            if(mine!==generation || status!=="connected")return;
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
      qr="";qrAt=0;pairingCode="";pairAt=0;
      connectedSince=0;pendingNotificationsReceived=false;inboundSinceConnect=false;
      const code=Number(update.lastDisconnect?.error?.output?.statusCode);
      lastDisconnectCode=Number.isFinite(code)?code:null;
      console.info("Orion QR: conexão encerrada (código="+(lastDisconnectCode??"desconhecido")+")");
      if(code===DisconnectReason.loggedOut){
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
  clearTimeout(timer);timer=null;qr="";qrAt=0;pairingCode="";pairAt=0;
  status="connecting";connectStartedAt=Date.now();pairingRequestedForGeneration=-1;
  pendingNotificationsReceived=false;inboundSinceConnect=false;
  try{
    const {state,saveCreds}=await useMultiFileAuthState(authDir);
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
    socket=client;installListeners(client,mine,saveCreds);
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
async function send(phone,text,{waitForCapacity=false,isCurrent=()=>true}={}){
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
}

const server=http.createServer(async(req,res)=>{
  const uri=new URL(req.url||"/","http://internal");
  const path=uri.pathname;
  if(path==="/health"&&req.method==="GET")
    return reply(res,200,{status:"ok",bridge:"linked-device"});
  if(!auth(req))return reply(res,401,{error:"unauthorized"});
  try{
    if(path==="/internal/status"&&req.method==="GET")
      return reply(res,200,stats());
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
      status="disconnecting";
      try{await old.logout()}catch{
        status="error";lastError="Não foi possível confirmar a desconexão.";
        return reply(res,503,{error:"disconnect_failed"});
      }
      socket=null;status="logged_out";pairedNumber="";
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
      if(status==="connected")return reply(res,409,{error:"already_connected"});
      if(status==="logged_out"||status==="pair_failed")
        return reply(res,409,{error:"not_logged_out"});
      if(status==="pairing"&&Date.now()-pairAt<90000)
        return reply(res,409,{error:"pairing_in_progress"});
      // Invalidate old listeners first; stale connection.update cannot
      // race us and overwrite fresh QR data.
      ++generation;clearTimeout(timer);timer=null;
      try{socket?.end(undefined)}catch{}
      await saveQueue;
      await connect();
      return reply(res,202,{status:"connecting"});
    }
    if(path==="/internal/chats"&&req.method==="GET"){
      const grouped=new Map();
      for(const item of messages) grouped.set(item.phone,{phone:item.phone,
        preview:item.body.slice(0,90),at:item.at});
      return reply(res,200,{items:[...grouped.values()].sort((a,b)=>b.at-a.at).slice(0,80)});
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
});
