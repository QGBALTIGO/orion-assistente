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

const PORT=Number(process.env.PORT || 3000);
const SESSION_DIR=process.env.QR_SESSION_DIR || "/data/auth";
const KEY=process.env.QR_BRIDGE_TOKEN || "";
const logger=pino({level:"silent"});
if(Buffer.byteLength(KEY)<32 || !SESSION_DIR.startsWith("/data/")){
  throw Error("Configuração segura do bridge não encontrada");
}
fs.mkdirSync(SESSION_DIR,{recursive:true,mode:0o700});
let socket=null,generation=0,timer=null,attempts=0,status="starting",
  qr="",qrAt=0,lastError=null,pairedNumber="",pairingCode="",pairAt=0,requesting=false;
const messages=[],seen=new Set(),sendTimes=[],MAX=500;

function validPhone(input){
  const phone=String(input||"").replace(/[()+\s-]/g,"");
  if(!/^[1-9]\d{7,14}$/.test(phone))throw Error("Número inválido. Informe DDI + DDD + telefone");
  return phone;
}
function pushMessage(item){
  if(seen.has(item.id))return;
  seen.add(item.id);messages.push(item);
  while(messages.length>MAX){const old=messages.shift();seen.delete(old.id)}
}
function stats(){
  return {status,hasQr:status==="qr_ready"&&!!qr&&Date.now()-qrAt<45000,
    lastError,phone:status==="connected"?pairedNumber:null,
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
function extractText(msg){
  if(!msg||typeof msg!=="object")return null;
  const source=msg.ephemeralMessage?.message||msg.viewOnceMessage?.message||msg;
  return source.conversation||source.extendedTextMessage?.text||
    source.imageMessage?.caption||source.videoMessage?.caption||null;
}
function normalizeFromJid(jid){
  const match=String(jid||"").match(/^(\d{8,15})@s\.whatsapp\.net$/);
  return match?.[1]||null;
}
function installListeners(client,mine,saveCreds){
  client.ev.on("creds.update",saveCreds);
  client.ev.on("messages.upsert",({messages:incoming,type})=>{
    if(mine!==generation||status!=="connected"||type!=="notify")return;
    for(const message of incoming||[]){
      if(message.key?.fromMe)continue;
      const phone=normalizeFromJid(message.key?.remoteJid);
      if(!phone||!message.key?.id||!message.message)continue;
      const body=extractText(message.message);
      if(!body || typeof body!=="string")continue;
      pushMessage({id:"wa:"+message.key.id,phone,body:body.slice(0,2000),
        direction:"in",at:Date.now()});
    }
  });
  client.ev.on("connection.update",update=>{
    if(mine!==generation)return;
    if(update.qr){
      qr=update.qr;qrAt=Date.now();status="qr_ready";lastError=null;
      console.info("Orion QR: pareamento disponível (conteúdo omitido)");
    }
    if(update.connection==="open"){
      status="connected";qr="";qrAt=0;pairingCode="";pairAt=0;attempts=0;
      pairedNumber=normalizeFromJid(client.user?.id?.split(":")[0]+"@s.whatsapp.net")||"";
      lastError=null;console.info("Orion QR: dispositivo conectado");
    }
    if(update.connection==="close"){
      qr="";qrAt=0;pairingCode="";pairAt=0;
      const code=update.lastDisconnect?.error?.output?.statusCode;
      if(code===DisconnectReason.loggedOut){
        status="logged_out";lastError="Dispositivo removido no WhatsApp.";
        console.info("Orion QR: aparelho desconectado");return;
      }
      status="reconnecting";
      const delay=code===DisconnectReason.restartRequired?1500:
        Math.min(30000,2000*2**Math.min(attempts++,4));
      timer=setTimeout(()=>{if(mine===generation)connect().catch(()=>{})},delay);
    }
  });
}
async function connect(){
  const mine=++generation;
  clearTimeout(timer);timer=null;qr="";qrAt=0;pairingCode="";pairAt=0;
  status="connecting";
  try{
    const {state,saveCreds}=await useMultiFileAuthState(SESSION_DIR);
    const client=makeWASocket({auth:state,browser:Browsers.ubuntu("Orion"),
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
  if(status==="logged_out")throw Error("Sessão revogada: reinicie o vínculo pelo WhatsApp");
  if(requesting)throw Error("Geração em andamento");
  const phone=validPhone(number);
  if(!socket||!socket.requestPairingCode || !qr || Date.now()-qrAt>45000)
    throw Error("Aguarde a disponibilidade do QR e tente novamente");
  requesting=true;
  try{
    const code=await socket.requestPairingCode(phone);
    if(!/^[A-Za-z0-9]{8}$/.test(code))throw Error("Formato inesperado de código");
    pairingCode=code.toUpperCase();pairAt=Date.now();pairedNumber=phone;
    return {status:"pairing",code:pairingCode.replace(/(.{4})/,"$1-"),
      expiresIn:120};
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
      if(status==="connected")return reply(res,200,{status,image:null,expiresIn:0});
      if(!qr||Date.now()-qrAt>=45000)
        return reply(res,200,{status,image:null,expiresIn:0});
      const image=await QRCode.toDataURL(qr,{errorCorrectionLevel:"M",margin:2,width:370});
      return reply(res,200,{status:"qr_ready",image,
        expiresIn:Math.ceil((45000-(Date.now()-qrAt))/1000)});
    }
    if(path==="/internal/pair"&&req.method==="POST")
      return reply(res,200,await pair((await collectBody(req)).phone));
    if(path==="/internal/reconnect"&&req.method==="POST"){
      if(status==="connected")return reply(res,409,{error:"already_connected"});
      if(socket){try{socket.end(undefined)}catch{}}
      connect().catch(()=>{});
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
