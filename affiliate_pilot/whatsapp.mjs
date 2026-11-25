import http from "node:http";
import { createHmac, randomUUID, timingSafeEqual } from "node:crypto";
import { existsSync, renameSync } from "node:fs";
import { join } from "node:path";
import makeWASocket, {
  Browsers, BufferJSON, DEFAULT_CONNECTION_CONFIG, DisconnectReason,
  fetchLatestWaWebVersion, initAuthCreds, proto,
} from "@whiskeysockets/baileys";
import QRCode from "qrcode";
import pino from "pino";
import { useDurableAuthState } from "../qr-bridge/src/durable-auth.mjs";
import { groupAddAdmin } from "../qr-bridge/src/device-group-add.mjs";
import { classifyPairedAuth } from "../qr-bridge/src/paired-auth-state.mjs";
import { protectSignalSessionLogs } from "../qr-bridge/src/signal-log-safety.mjs";
import { selectWaWebVersion } from "../qr-bridge/src/wa-version-policy.mjs";
import { channelTest } from "./channel-test.mjs";
import { promoTechPublisher } from "./promotech-publish.mjs";

const key = process.env.AFFILIATE_WA_TOKEN;
const directory = join(process.env.STATE_DIRECTORY, "whatsapp-auth");
const port = Number(process.env.AFFILIATE_WA_PORT);
if (key?.length < 40 || !key || !port) throw Error("Configuração do conector incompleta.");
protectSignalSessionLogs();
const logger = pino({ level: "silent" });
const groupId = /^[1-9]\d{5,21}(?:-\d{5,18})?@g\.us$/;
let socket, qr, qrUntil = 0, windowUntil = 0, timer, reconnect, generation = 0;
let status = "unlinked", nextStart = 0, retries = 0, busy = false, closing = false;

function retire(next) {
  ++generation;
  clearTimeout(timer);
  clearTimeout(reconnect);
  qr = undefined;
  const previous = socket;
  socket = undefined;
  status = next;
  try { previous?.end(undefined); } catch {}
}

function account() {
  const id = socket?.user?.id;
  if (!id) return null;
  const peer = id.replace(/:\d+@/, "@");
  return { account: "••••" + peer.split("@")[0].slice(-4),
    version: createHmac("sha256", key).update(peer).digest("hex") };
}

async function connect() {
  const current = ++generation;
  status = "connecting";
  try {
    const negotiated = await selectWaWebVersion({
      bundledVersion: DEFAULT_CONNECTION_CONFIG.version, fetchVersion: fetchLatestWaWebVersion,
    });
    if (generation !== current) return;
    const auth = useDurableAuthState(directory, {
      secret: key, sdk: { BufferJSON, initAuthCreds, proto },
      isCurrent: () => generation === current,
    });
    const client = makeWASocket({
      auth: auth.state, logger, browser: Browsers.windows("Chrome"), version: negotiated.version,
      markOnlineOnConnect: false, syncFullHistory: false, shouldSyncHistoryMessage: () => false,
      connectTimeoutMs: 18_000, defaultQueryTimeoutMs: 12_000, getMessage: async () => undefined,
    });
    socket = client;
    client.ev.on("creds.update", () => {
      if (current === generation) {
        try { auth.saveCreds(); } catch { retire("error"); }
      }
    });
    client.ev.on("connection.update", update => {
      if (current !== generation || closing) return;
      if (update.qr && Date.now() < windowUntil) {
        qr = update.qr;
        qrUntil = Math.min(Date.now() + 40_000, windowUntil);
        status = "qr_ready";
      }
      if (update.connection === "open") {
        clearTimeout(timer);
        qr = undefined;
        windowUntil = 0;
        retries = 0;
        status = "linked";
      }
      if (update.connection === "close") {
        const code = update.lastDisconnect?.error?.output?.statusCode;
        retire("error");
        if (code === DisconnectReason.loggedOut) {
          status = "logged_out";
          return;
        }
        const requested = Date.now() < windowUntil;
        if ((requested || classifyPairedAuth(auth.state.creds, { proto }) === "paired") && ++retries <= 4 &&
            code !== DisconnectReason.connectionReplaced) {
          status = "connecting";
          if (requested) limitPairing();
          reconnect = setTimeout(connect, code === DisconnectReason.restartRequired ? 500 : 2_500 * retries);
        }
      }
    });
  } catch {
    if (generation === current) retire("error");
  }
}

function start() {
  if (closing || status === "linked") return snapshot();
  const now = Date.now();
  if (now < windowUntil && (status === "qr_ready" || status === "connecting")) return snapshot();
  if (now < nextStart) return { status };
  if (status === "logged_out" && existsSync(directory)) renameSync(directory, directory + "-revoked-" + randomUUID());
  retire("connecting");
  nextStart = now + 15_000;
  windowUntil = now + 100_000;
  retries = 0;
  limitPairing();
  void connect();
  return { status };
}

function limitPairing() {
  clearTimeout(timer);
  timer = setTimeout(() => {
    if (status !== "linked") retire("unlinked");
    windowUntil = 0;
  }, Math.max(0, windowUntil - Date.now()));
}

async function snapshot() {
  const currentQr = qr;
  if (status === "qr_ready" && currentQr && Date.now() < qrUntil) {
    const image = await QRCode.toDataURL(currentQr, { width: 290, margin: 3, errorCorrectionLevel: "M" });
    if (qr === currentQr && Date.now() < qrUntil) return { status, image, expires: Math.ceil((qrUntil - Date.now()) / 1000) };
  }
  return status === "linked" ? { status, ...account() } : { status: status === "qr_ready" ? "connecting" : status };
}

function adminProof(meta, client) {
  return meta && groupId.test(meta.id) && groupAddAdmin(meta, client.user)
    ? { address: meta.id, title: String(meta.subject || "Grupo do WhatsApp").slice(0, 100) } : null;
}

async function groups(address) {
  if (busy || closing || status !== "linked" || !socket || !account()) return [409, { code: "unavailable" }];
  busy = true;
  const client = socket, version = account().version;
  try {
    if (address && !groupId.test(address)) return [422, { code: "address" }];
    const records = address ? [await client.groupMetadata(address)] : Object.values(await client.groupFetchAllParticipating());
    if (client !== socket || status !== "linked" || account()?.version !== version) return [409, { code: "unavailable" }];
    const matches = records.map(meta => adminProof(meta, client)).filter(Boolean);
    if (address && matches[0]?.address !== address) return [403, { code: "admin" }];
    return [200, { version, groups: matches.slice(0, 100), truncated: matches.length > 100 }];
  } finally {
    busy = false;
  }
}

async function channel(invite) {
  if (!/^[A-Za-z0-9]{20,32}$/.test(invite || "")) return [422, { code: "address" }];
  if (busy || closing || status !== "linked" || !account()) return [409, { code: "unavailable" }];
  const client = socket, version = account().version;
  const current = () => client === socket && !closing && status === "linked" && account()?.version === version;
  const canManage = details => ["OWNER", "ADMIN"].includes(details?.viewer_metadata?.role);
  busy = true;
  try {
    let details = await client.newsletterMetadata("invite", invite);
    if (!current()) return [409, { code: "unavailable" }];
    const id = details?.id;
    if (typeof id !== "string" || !/^[1-9]\d{7,23}@newsletter$/.test(id)) {
      return [422, { code: "channel_not_found" }];
    }
    if (!canManage(details)) details = await client.newsletterMetadata("jid", id);
    if (!current()) return [409, { code: "unavailable" }];
    if (details?.id !== id || (details.state?.type && details.state.type !== "ACTIVE")) {
      return [422, { code: "channel_not_found" }];
    }
    if (!canManage(details)) return [403, { code: "channel_admin" }];
    const title = details.thread_metadata?.name?.text || details.name;
    return [200, { version, address: id, role: details.viewer_metadata.role,
      title: typeof title === "string" && title.trim() ? title.trim().slice(0, 100) : "Canal do WhatsApp" }];
  } finally {
    busy = false;
  }
}

async function disconnect() {
  if (closing) return [409, { code: "unavailable" }];
  closing = true;
  try {
    if (status === "linked") {
      await socket.logout();
      if (existsSync(directory)) renameSync(directory, directory + "-revoked-" + randomUUID());
    }
    retire("unlinked");
    windowUntil = 0;
    return [200, { status }];
  } finally {
    closing = false;
  }
}

function authorized(req) {
  const supplied = Buffer.from(req.headers.authorization || ""), expected = Buffer.from("Bearer " + key);
  return supplied.length === expected.length && timingSafeEqual(supplied, expected);
}

const oneChannelTest = channelTest({
  credentialDirectory: process.env.CREDENTIALS_DIRECTORY, stateDirectory: process.env.STATE_DIRECTORY,
  channel, logger,
  connection: () => status === "linked" && !closing ? { client: socket, version: account()?.version } : {},
});
const recurringPromoTech = promoTechPublisher({
  binding: JSON.parse(process.env.AFFILIATE_PROMOTECH_BINDING || "null"),
  stateDirectory: process.env.STATE_DIRECTORY, channel, logger,
  connection: () => status === "linked" && !closing ? { client: socket, version: account()?.version } : {},
});

const server = http.createServer(async (req, res) => {
  function reply(code, data) {
    res.writeHead(code, { "Content-Type": "application/json", "Cache-Control": "no-store" });
    res.end(JSON.stringify(data));
  }
  if (!authorized(req)) return reply(401, { code: "unauthorized" });
  try {
    const url = new URL(req.url, "http://localhost");
    if (req.method === "GET" && url.pathname === "/status") return reply(200, await snapshot());
    if (req.method === "POST" && url.pathname === "/link") return reply(200, await start());
    if (req.method === "DELETE" && url.pathname === "/link") return reply(...await disconnect());
    if (req.method === "GET" && url.pathname === "/groups") return reply(...await groups(url.searchParams.get("address")));
    if (req.method === "GET" && url.pathname === "/channel") return reply(...await channel(url.searchParams.get("invite")));
    if (["POST", "GET"].includes(req.method) && url.pathname === "/channel-test")
      return reply(...await oneChannelTest(req.method === "POST", url.searchParams.get("slot")));
    if (req.method === "POST" && url.pathname === "/promotech") return reply(...await recurringPromoTech(req));
    return reply(404, { code: "unknown_route" });
  } catch {
    return reply(503, { code: "unavailable" });
  }
});
server.listen(port, "127.0.0.1", () => {
  if (existsSync(join(directory, "creds.json"))) {
    try {
      const auth = useDurableAuthState(directory, { secret: key, sdk: { BufferJSON, initAuthCreds, proto } });
      const kind = classifyPairedAuth(auth.state.creds, { proto });
      if (kind === "paired") void connect();
      if (kind === "invalid") status = "error";
    } catch { status = "error"; }
  }
});
