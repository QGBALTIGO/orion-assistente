import { createHash } from "node:crypto";
import { closeSync, existsSync, fsyncSync, openSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { generateMessageIDV2, prepareWAMessageMedia } from "@whiskeysockets/baileys";

export function promoTechPublisher({ binding, stateDirectory, channel, connection, logger }) {
  let running = false;
  const file = join(stateDirectory, "promotech-autopost.json");
  function save(value) {
    const draft = file + ".next";
    writeFileSync(draft, JSON.stringify(value), { mode: 0o600, flush: true });
    renameSync(draft, file);
    const parent = openSync(stateDirectory, "r");
    try { fsyncSync(parent); } finally { closeSync(parent); }
    return value;
  }

  return async req => {
    if (!binding) return [404, { status: "invalid" }];
    if (running) return [409, { status: "busy" }];
    const { client, version } = connection();
    if (!client) return [409, { status: "offline" }];
    if (version !== binding.version) return [403, { status: "permission" }];

    running = true;
    try {
      const chunks = [];
      let bytes = 0;
      for await (const chunk of req) {
        bytes += chunk.length;
        if (bytes > 640_000) return [413, { status: "invalid" }];
        chunks.push(chunk);
      }
      let offer;
      try { offer = JSON.parse(Buffer.concat(chunks).toString("utf8")); } catch { return [422, { status: "invalid" }]; }
      if (!offer || !/^[a-f0-9]{32}$/.test(offer.id) || !Number.isInteger(offer.expires) ||
          offer.expires * 1000 <= Date.now() || offer.expires * 1000 > Date.now() + 60_000 ||
          typeof offer.caption !== "string" || offer.caption.length < 1 || offer.caption.length > 1024 ||
          typeof offer.link !== "string" || !/^https:\/\/(?:s\.shopee\.com\.br|shope\.ee)\/[^\s<>]+$/.test(offer.link) ||
          !offer.caption.includes(offer.link) || typeof offer.jpeg !== "string" ||
          typeof offer.thumbnail !== "string") return [422, { status: "invalid" }];

      const last = existsSync(file) ? JSON.parse(readFileSync(file, "utf8")) : null;
      if (last?.id === offer.id) return [200, last];
      const delay = last ? last.started + 1_800_000 - Date.now() : 0;
      if (delay > 0) return [409, { status: "cooldown", seconds: Math.ceil(delay / 1000) }];

      const current = () => {
        const now = connection();
        return now.client === client && now.version === version;
      };
      let proof;
      try { proof = await channel(binding.invite); } catch { return [409, { status: "offline" }]; }
      const [code, destination] = proof;
      if (!current() || (code !== 200 && code !== 403)) return [409, { status: "offline" }];
      if (code === 403 || destination.address !== binding.jid || destination.version !== version)
        return [403, { status: "permission" }];

      const jpeg = Buffer.from(offer.jpeg, "base64"), thumbnail = Buffer.from(offer.thumbnail, "base64");
      if (jpeg[0] !== 0xff || jpeg[1] !== 0xd8 || jpeg.length > 420_000 ||
          thumbnail.length > 20_000 || !thumbnail.length) return [422, { status: "invalid" }];
      let message;
      try {
        message = await prepareWAMessageMedia({ image: jpeg, caption: offer.caption, mimetype: "image/jpeg",
          width: 900, height: 970, jpegThumbnail: thumbnail },
          { jid: binding.jid, upload: client.waUploadToServer, logger, mediaUploadTimeoutMs: 12_000 });
      } catch { return [422, { status: "upload_failed" }]; }
      if (!current() || offer.expires * 1000 <= Date.now()) return [409, { status: "offline" }];
      const hash = createHash("sha256").update(jpeg).digest();
      if (!message?.imageMessage?.directPath || !hash.equals(Buffer.from(message.imageMessage.fileSha256 || [])))
        return [422, { status: "upload_failed" }];

      const receipt = save({ id: offer.id, status: "unknown", started: Date.now() });
      const id = generateMessageIDV2(client.user.id);
      const waiting = client.waitForMessage(id, 7000).catch(() => null);
      try { await client.relayMessage(binding.jid, message, { messageId: id }); } catch {}
      const ack = await waiting;
      if (ack?.tag !== "ack" || ack.attrs?.from !== binding.jid || ack.attrs?.id !== id) return [200, receipt];
      if (ack.attrs.error) return [200, save({ ...receipt, status: "rejected" })];
      if (!/^\d{1,16}$/.test(ack.attrs.server_id || "")) return [200, receipt];
      return [200, save({ ...receipt, status: "published", server_id: ack.attrs.server_id })];
    } finally {
      running = false;
    }
  };
}
