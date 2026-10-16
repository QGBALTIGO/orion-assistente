/**
 * Orion Linked Device — aparelho de teste único, transporte não oficial.
 * Apenas mensagens privadas recebidas após conexão; nada de histórico antigo,
 * mídia, grupos, auto-respostas ou envios em massa.
 * Mensagens recentes ficam apenas na RAM; sessão Baileys cifrada no volume.
 */
import http from "node:http";
import fs from "node:fs";
import {timingSafeEqual,randomUUID} from "node:crypto";
import makeWASocket,{DisconnectReason,Browsers,useMultiFileAuthState} from "@whiskeysockets/baileys";
import QRCode from "qrcode";
import pino from "pino";
import {validPhone,normalizeFromJid,extractText,resolveMessagePhone,freshQR,formatPairingCode} from "./core.mjs";

const PORT=Number(process.env.PORT || 3000);
const SESSION_DIR=process.env.QR_SESSION_DIR || "/data/auth";
const KEY=process.env.QR_BRIDGE_TOKEN || "";
const logger=pino({level:"silent"});
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
let socket=null,generation=0,timer=null,attempts=0,status="starting",
  connectStartedAt=Date.now(),lastQrAt=0,lastDisconnectCode=null,everConnected=false,
  pairingRequestedForGeneration=-1, pairPhone="",saveQueue=Promise.resolve(),
  qr="",qrAt=0,lastError=null,pairedNumber="",pairingCode="",pairAt=0,requesting=false;
const messages=[],seen=new Set(),sendTimes=[],MAX=500;

function pushMessage(item){
  if(seen.has(item.id))return;
  seen.add(item.id);messages.push(item);
  while(messages.length>MAX){const old=messages.shift();seen.delete(old.id)}
}
function visibleStatus(){
  if(status==="qr_ready"&&!freshQR(qrAt))return "qr_expired";
  if(status==="connecting"&&Date.now()-connectStartedAt>25000)return "qr_delayed";
  return status;
}
function stats(){
  const phase=visibleStatus();
  return {status:phase,hasQr:phase==="qr_ready"&&!!qr&&freshQR(qrAt),
    lastError,disconnectCode:lastDisconnectCode,
    waitingSeconds:Math.floor((Date.now()-connectStartedAt)/1000),
    phone:status==="connected"?pairedNumber:null,
    connectionType:"unofficial_linked_device",messagesEnabled:status==="connected",
    retainedMessages:messages.length,paired:!!pairingCode};
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
  // WhatsApp often requests reconnect right after a successful pair (515).
  // Persist auth state before starting the replacement socket.
  client.ev.on("creds.update",()=>{
    saveQueue=saveQueue.then(()=>saveCreds()).catch(()=>{
      lastError="Falha ao salvar sessão WhatsApp. Não tente novo vínculo sem verificar o volume.";
      console.warn("Orion QR: falha ao salvar credenciais (dados omitidos)");
    });
  });
  client.ev.on("messages.upsert",async ({messages:incoming,type})=>{
    if(mine!==generation||status!=="connected"||type!=="notify")return;
    for(const message of incoming||[]){
      if(message.key?.fromMe)continue;
      const phone=await resolveMessagePhone(message.key,client);
      if(!phone||!message.key?.id||!message.message)continue;
      const body=extractText(message.message);
      if(!body || typeof body!=="string")continue;
      pushMessage({id:"wa:"+message.key.id,phone,body:body.slice(0,2000),
        direction:"in",at:Date.now()});
    }
  });
  client.ev.on("connection.update",update=>{
    if(mine!==generation)return;
    if(update.qr && pairingRequestedForGeneration!==mine){
      qr=update.qr;qrAt=Date.now();lastQrAt=qrAt;status="qr_ready";lastError=null;
      console.info("Orion QR: pareamento disponível (conteúdo omitido)");
    }
    if(update.connection==="open"){
      status="connected";everConnected=true;qr="";qrAt=0;pairingCode="";pairAt=0;attempts=0;
      pairedNumber=normalizeFromJid(client.user?.id?.split(":")[0]+"@s.whatsapp.net")||"";
      lastError=null;console.info("Orion QR: dispositivo conectado");
    }
    if(update.connection==="close"){
      qr="";qrAt=0;pairingCode="";pairAt=0;
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
      const delay=code===DisconnectReason.restartRequired?500:
        Math.min(30000,2000*2**Math.min(attempts++,4));
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
  try{
    const {state,saveCreds}=await useMultiFileAuthState(authDir);
    const client=makeWASocket({auth:state,browser:Browsers.windows("Chrome"),
      printQRInTerminal:false,markOnlineOnConnect:false,syncFullHistory:false,logger});
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
async function send(phone,text){
  const target=validPhone(phone);
  if(status!=="connected"||!socket)throw Error("O WhatsApp não está conectado");
  if(typeof text!=="string"||!text.trim()||text.length>1200)
    throw Error("Mensagem deve ter entre 1 e 1200 caracteres");
  const now=Date.now();
  while(sendTimes.length&&now-sendTimes[0]>60000)sendTimes.shift();
  if(sendTimes.length>=5)throw Error("Aguarde um minuto antes de enviar mais mensagens");
  sendTimes.push(now);
  const jid=target+"@s.whatsapp.net";
  const result=await socket.sendMessage(jid,{text:text.trim()});
  const id="out:"+(result?.key?.id||randomUUID());
  pushMessage({id,phone:target,body:text.trim(),direction:"out",at:Date.now()});
  return {sent:true,id};
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
      messages.splice(0);seen.clear();sendTimes.splice(0);
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
