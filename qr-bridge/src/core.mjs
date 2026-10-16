/** Validação local: nenhuma chamada ao WhatsApp ou dependência externa. */

export function validPhone(input) {
  const phone=String(input??"").replace(/[()+\s-]/g,"");
  if(!/^[1-9]\d{7,14}$/.test(phone)) {
    throw Error("Número inválido. Informe DDI + DDD + telefone");
  }
  return phone;
}

export function normalizeFromJid(jid) {
  const match=String(jid??"").match(/^(\d{8,15})(?::\d+)?@s\.whatsapp\.net$/);
  return match?.[1]||null;
}

export function extractText(message) {
  if(!message||typeof message!=="object") return null;
  const source=message.ephemeralMessage?.message||
    message.viewOnceMessage?.message||message;
  return source.conversation||source.extendedTextMessage?.text||
    source.imageMessage?.caption||source.videoMessage?.caption||null;
}

export async function resolveMessagePhone(key, client) {
  // PN do servidor é confiável; LID é um identificador opaco e jamais
  // pode ser confundido com número de telefone de um destinatário.
  const direct=normalizeFromJid(key?.remoteJid)||
    normalizeFromJid(key?.remoteJidAlt);
  if(direct) return direct;
  const lid=String(key?.remoteJid||"");
  if(!/^\d{8,20}@lid$/.test(lid)) return null;
  try {
    const mapping=client?.signalRepository?.lidMapping;
    if(typeof mapping?.getPNForLID!=="function")return null;
    const mapped=await mapping.getPNForLID(lid);
    return normalizeFromJid(mapped);
  }catch{
    return null;
  }
}

export function freshQR(timestamp,now=Date.now()){
  return Number.isFinite(timestamp)&&timestamp>0&&now>=timestamp&&now-timestamp<45000;
}


export function connectionPhase(status,qrTimestamp,startedAt,now=Date.now()){
  if(status==="qr_ready"&&!freshQR(qrTimestamp,now))return "qr_expired";
  if(status==="connecting"&&now-startedAt>25000)return "qr_delayed";
  return status;
}


export function formatPairingCode(code) {
  const raw=String(code||"").replace(/-/g,"").toUpperCase();
  if(!/^[A-Z0-9]{8}$/.test(raw))throw Error("Formato inesperado de código");
  return raw.slice(0,4)+"-"+raw.slice(4);
}
