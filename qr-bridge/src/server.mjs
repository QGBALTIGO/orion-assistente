/**
 * Orion QR bridge: conexão de UM dispositivo autorizado, SOMENTE PARA HOMOLOGAÇÃO.
 * Não assina mensagens, não recebe webhooks, não envia respostas e não
 * disponibiliza credenciais da sessão pelo endpoint.
 *
 * Baileys usa cliente web NÃO OFICIAL. Pode causar restrições de conta.
 */
import http from "node:http";
import fs from "node:fs";
import { timingSafeEqual } from "node:crypto";
import makeWASocket, {
  DisconnectReason,
  Browsers,
  useMultiFileAuthState,
} from "@whiskeysockets/baileys";
import QRCode from "qrcode";
import pino from "pino";

const PORT = Number(process.env.PORT || 3000);
const SESSION_DIR = process.env.QR_SESSION_DIR || "/data/auth";
const KEY = process.env.QR_BRIDGE_TOKEN || "";
const logger = pino({level:"silent"});
if (Buffer.byteLength(KEY) < 32) {
  throw new Error("QR_BRIDGE_TOKEN requer ao menos 32 caracteres");
}
if (!SESSION_DIR.startsWith("/data/")) {
  throw new Error("O estado precisa usar volume persistente /data");
}
fs.mkdirSync(SESSION_DIR, { recursive:true, mode:0o700 });

let currentSocket = null;
let generation = 0;
let reconnectTimer = null;
let attempts = 0;
let qrValue = "";
let qrAt = 0;
let status = "starting";
let lastError = null;

function auth(req) {
  const sent = String(req.headers["x-orion-bridge-key"] || "");
  const actual = Buffer.from(KEY, "utf8");
  const supplied = Buffer.from(sent, "utf8");
  return actual.length === supplied.length && timingSafeEqual(actual,supplied);
}
function reply(res, code, data) {
  res.writeHead(code, {
    "Content-Type":"application/json; charset=utf-8",
    "Cache-Control":"no-store, max-age=0",
    "Pragma":"no-cache",
    "X-Content-Type-Options":"nosniff",
    "Referrer-Policy":"no-referrer",
  });
  res.end(JSON.stringify(data));
}

async function connect() {
  const mine = ++generation;
  clearTimeout(reconnectTimer);
  reconnectTimer = null;
  qrValue = "";
  qrAt = 0;
  status = "connecting";
  try {
    const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);
    const socket = makeWASocket({
      auth:state,
      browser:Browsers.ubuntu("Orion QR Teste"),
      printQRInTerminal:false,
      markOnlineOnConnect:false,
      syncFullHistory:false,
      logger,
    });
    currentSocket = socket;
    socket.ev.on("creds.update", saveCreds);
    // Nenhum manipulador messages.upsert: nenhuma mensagem é armazenada,
    // processada ou respondida nesta fase.
    socket.ev.on("connection.update", update => {
      if (mine !== generation) return;
      if (update.qr) {
        qrValue = update.qr; qrAt = Date.now();
        status = "qr_ready"; lastError = null;
        console.info("Orion QR: código de pareamento disponível (conteúdo omitido)");
      }
      if (update.connection === "open") {
        status = "connected"; qrValue = ""; qrAt = 0;
        attempts = 0; lastError = null;
        console.info("Orion QR: dispositivo vinculado");
      }
      if (update.connection === "close") {
        qrValue = ""; qrAt = 0;
        const code = update.lastDisconnect?.error?.output?.statusCode;
        if (code === DisconnectReason.loggedOut) {
          status = "logged_out";
          console.info("Orion QR: sessão desvinculada");
          lastError = "Sessão removida pelo WhatsApp. Novo vínculo requer revisão manual.";
          return;
        }
        status = "reconnecting";
        const ms = code === DisconnectReason.restartRequired
          ? 1000 : Math.min(30000, 2000 * 2 ** Math.min(attempts++, 4));
        reconnectTimer = setTimeout(() => {
          if (mine === generation) connect().catch(() => {});
        }, ms);
      }
    });
  } catch {
    status = "error";
    console.info("Orion QR: falha genérica ao iniciar socket");
    lastError = "Falha ao iniciar conexão. Verifique integridade do volume.";
    reconnectTimer = setTimeout(() => {
      if (mine === generation) connect().catch(() => {});
    }, 30000);
  }
}

const server = http.createServer(async (req,res) => {
  const path = (req.url || "").split("?")[0];
  if (path === "/health" && req.method === "GET") {
    return reply(res, 200, { status:"ok", bridge:"qr-only" });
  }
  if (!auth(req)) return reply(res,401,{error:"unauthorized"});
  if (path === "/internal/status" && req.method === "GET") {
    return reply(res,200,{status,hasQr:status==="qr_ready" &&
      Boolean(qrValue) && Date.now()-qrAt<45000,
      connectionType:"unofficial_linked_device",messagesEnabled:false,
      lastError});
  }
  if (path === "/internal/qr" && req.method === "GET") {
    if (status === "connected") return reply(res,200,{
      status:"connected",image:null,expiresIn:0});
    if (!qrValue || Date.now()-qrAt>45000) return reply(res,200,{
      status, image:null, expiresIn:0});
    try {
      const image = await QRCode.toDataURL(qrValue,{
        errorCorrectionLevel:"M",margin:2,width:360,
      });
      return reply(res,200,{
        status:"qr_ready", image,
        expiresIn:Math.max(0,Math.ceil((45000-(Date.now()-qrAt))/1000)),
      });
    } catch {
      return reply(res,503,{error:"qr_unavailable"});
    }
  }
  if (path === "/internal/reconnect" && req.method === "POST") {
    if (status==="connected") return reply(res,409,{error:"already_connected"});
    if (status==="logged_out") return reply(res,409,{error:"logged_out"});
    if (currentSocket) {
      try { currentSocket.end(undefined); } catch {}
    }
    connect().catch(() => {});
    return reply(res,202,{status:"connecting"});
  }
  return reply(res,404,{error:"not_found"});
});
server.requestTimeout = 10000;
server.headersTimeout = 10000;
server.listen(PORT,"0.0.0.0",()=>{
  connect().catch(()=>{});
});
