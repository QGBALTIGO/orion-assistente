import { createHash } from "node:crypto";
import { closeSync, existsSync, fsyncSync, openSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { generateMessageIDV2, prepareWAMessageMedia, proto } from "@whiskeysockets/baileys";

const sha256 = bytes => createHash("sha256").update(bytes).digest("hex");

function validGrant(doc) {
  return /^[a-f0-9]{32}$/.test(doc.id) && /^[A-Za-z0-9]{20,32}$/.test(doc.invite) &&
    /^[1-9]\d{7,23}@newsletter$/.test(doc.address) && /^[a-f0-9]{64}$/.test(doc.version) &&
    Number.isInteger(doc.expires) && typeof doc.caption === "string" && doc.caption.length <= 1024 &&
    typeof doc.link === "string" && /^https:\/\/(?:s\.shopee\.com\.br|shope\.ee)\//.test(doc.link) &&
    doc.caption.split("\n").some(line => line === doc.link || line === "🔗 " + doc.link) &&
    typeof doc.jpeg === "string" && doc.jpeg.length <= 600_000 &&
    /^[a-f0-9]{64}$/.test(doc.sha256);
}

function matchChannelMessage(node, expected, depth = 0) {
  if (depth > 7 || !Array.isArray(node?.content)) return null;
  if (node.tag === "message") {
    const raw = node.content.find(row => row.tag === "plaintext")?.content;
    if (!Buffer.isBuffer(raw) && !(raw instanceof Uint8Array)) return null;
    try {
      const media = proto.Message.decode(raw).imageMessage;
      if (media?.caption !== expected.caption || Buffer.from(media.fileSha256 || []).toString("hex") !== expected.sha256)
        return null;
      const serverId = node.attrs?.server_id;
      return /^\d{1,16}$/.test(serverId || "") ? { media, serverId } : null;
    } catch { return null; }
  }
  for (const child of node.content) {
    const result = matchChannelMessage(child, expected, depth + 1);
    if (result) return result;
  }
  return null;
}

async function imageMatches(media, grant) {
  let url;
  try {
    url = new URL(media.url || new URL(media.directPath, "https://mmg.whatsapp.net/"));
  } catch { return false; }
  if (url.protocol !== "https:" || url.port || url.username || url.password ||
      !/(?:^|\.)whatsapp\.net$/.test(url.hostname)) return false;
  const res = await fetch(url, { redirect: "error", signal: AbortSignal.timeout(8_000) });
  if (!res.ok || !res.body) return false;
  const hash = createHash("sha256");
  let size = 0;
  for await (const chunk of res.body) {
    size += chunk.length;
    if (size > 500_000) return false;
    hash.update(chunk);
  }
  return hash.digest("hex") === grant.sha256;
}

export function channelTest({ credentialDirectory, stateDirectory, channel, connection, logger }) {
  let running = false;
  return async (send, slot) => {
    if (!credentialDirectory) return [404, { code: "test_unavailable" }];
    if (slot !== null && !["0", "1"].includes(slot)) return [404, { code: "test_unavailable" }];
    const credential = join(credentialDirectory, "orion-channel-test" + (slot === null ? "" : "-" + slot));
    if (!existsSync(credential)) return [404, { code: "test_unavailable" }];
    const grant = JSON.parse(readFileSync(credential, "utf8"));
    if (!validGrant(grant)) return [422, { status: "invalid_grant" }];
    const journal = join(stateDirectory, "channel-test-" + grant.id + ".json");
    const update = (receipt) => {
      writeFileSync(journal + ".next", JSON.stringify(receipt), { mode: 0o600, flush: true });
      renameSync(journal + ".next", journal);
      return receipt;
    };
    if (running) return [409, { status: "in_progress" }];
    running = true;
    try {
      const receipt = existsSync(journal) ? JSON.parse(readFileSync(journal, "utf8")) : null;
      if (receipt?.status === "confirmed") return [200, receipt];
      const { client, version } = connection();
      if (!client || version !== grant.version) return [409, { status: "account_unavailable" }];
      const current = () => {
        const active = connection();
        return active.client === client && active.version === version;
      };
      let attempt = receipt;
      if (!attempt) {
        if (!send || Date.now() >= grant.expires) return [404, { status: "not_attempted" }];
        if (slot === "1") {
          if (!/^[a-f0-9]{32}$/.test(grant.after)) return [422, { status: "invalid_grant" }];
          const previous = join(stateDirectory, "channel-test-" + grant.after + ".json");
          const first = existsSync(previous) ? JSON.parse(readFileSync(previous, "utf8")) : null;
          if (!first || first.status === "provider_rejected" || Date.now() - first.started < 600_000)
            return [409, { status: "outside_test_interval" }];
        }
        const [status, destination] = await channel(grant.invite);
        if (status !== 200 || destination.address !== grant.address || destination.version !== grant.version || !current())
          return [422, { status: "channel_unavailable" }];
        const jpeg = Buffer.from(grant.jpeg, "base64");
        const thumbnail = Buffer.from(grant.thumbnail, "base64");
        if (jpeg[0] !== 0xff || jpeg[1] !== 0xd8 || sha256(jpeg) !== grant.sha256 || !thumbnail.length ||
            thumbnail.length > 20_000) return [422, { status: "invalid_card" }];
        const message = await prepareWAMessageMedia({
          image: jpeg, caption: grant.caption, mimetype: "image/jpeg", width: 900, height: 970, jpegThumbnail: thumbnail,
        }, { jid: grant.address, upload: client.waUploadToServer, logger, mediaUploadTimeoutMs: 12_000 });
        if (!current() || Date.now() >= grant.expires || !message?.imageMessage?.directPath ||
            Buffer.from(message.imageMessage.fileSha256 || []).toString("hex") !== grant.sha256)
          return [409, { status: "image_upload_unverified" }];
        attempt = { status: "result_unknown", client_id: generateMessageIDV2(client.user.id), started: Date.now() };
        // Reserve the only allowed attempt before the external publication boundary.
        writeFileSync(journal, JSON.stringify(attempt), { flag: "wx", mode: 0o600, flush: true });
        const parent = openSync(stateDirectory, "r");
        try { fsyncSync(parent); } finally { closeSync(parent); }
        const ack = client.waitForMessage(attempt.client_id, 7000).catch(() => null);
        try {
          await client.relayMessage(grant.address, message, { messageId: attempt.client_id });
        } catch {
          // A socket error can happen after the provider received the update.
        }
        const response = await ack;
        if (response?.tag === "ack" && response.attrs?.id === attempt.client_id && response.attrs?.from === grant.address) {
          const reportedId = response.attrs.server_id;
          attempt = update({ ...attempt, status: response.attrs.error ? "provider_rejected" : "provider_ack",
            server_id: /^\d{1,16}$/.test(reportedId || "") ? reportedId : undefined });
          if (attempt.status === "provider_rejected") return [200, attempt];
        }
      }
      if (current()) {
        try {
          const feed = await client.newsletterFetchMessages(grant.address, 12);
          const found = matchChannelMessage(feed, grant);
          if (found && current()) {
            const downloaded = await imageMatches(found.media, grant).catch(() => false);
            return [200, update({ ...attempt, status: downloaded ? "confirmed" : "media_pending",
              server_id: found.serverId, caption_exact: true, affiliate_link_exact: true,
              card_download_exact: downloaded })];
          }
        } catch {
          // Reconciliation reads are safe. The reserved attempt is never replayed.
        }
      }
      return [200, attempt];
    } finally {
      running = false;
    }
  };
}
