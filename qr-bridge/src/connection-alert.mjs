/**
 * Aviso no próprio WhatsApp após um vínculo real.
 *
 * - Dispara apenas quando connection.update é "open".
 * - Deriva o destino SOMENTE do socket autenticado, nunca do QR/formulário.
 * - Mantém um recibo em /data para evitar notificação repetida no restart.
 * - Marca antes de enviar: falhas ambíguas NÃO são reenviadas automaticamente.
 */
import fs from "node:fs";
import path from "node:path";

export const CONNECTION_CONFIRMATION_TEXT =
  "🎉 Dispositivo Conectado com Sucesso\n\n" +
  "O Orion foi conectado ao seu WhatsApp! ✅\n\n" +
  "Você já pode acompanhar o estado da conexão e testar as conversas no painel. " +
  "Se o WhatsApp desconectar temporariamente, o Orion tentará se reconectar automaticamente.\n\n" +
  "Esta é uma mensagem de confirmação enviada pelo dispositivo vinculado.";

export function ownAccountJid(userId) {
  const matched=String(userId||"").match(/^([1-9]\d{7,14})(?::\d+)?@s\.whatsapp\.net$/);
  return matched ? matched[1]+"@s.whatsapp.net" : null;
}

export function noticePath(authDir) {
  return path.join(authDir,"orion_link_notification.v1.json");
}

export function noticeState(authDir) {
  try {
    const record=JSON.parse(fs.readFileSync(noticePath(authDir),"utf8"));
    return ["attempted","sent","delivery_uncertain"].includes(record?.state)
      ? record.state : "unknown";
  } catch(error) {
    return error?.code==="ENOENT"?"pending":"unknown";
  }
}

function writeReceipt(file,state) {
  // Contains no chat content, JID, phone, token, credentials or QR.
  fs.writeFileSync(file,JSON.stringify({schema:1,state})+"\n",{mode:0o600});
}

export async function sendConnectionNoticeOnce({
  authDir, client, isCurrent, onSent=()=>{},
}) {
  if(!isCurrent() || !client || typeof client.sendMessage!=="function")
    return {state:"not_ready"};
  const ownJid=ownAccountJid(client.user?.id);
  if(!ownJid) return {state:"unknown_own_number"};
  const marker=noticePath(authDir);
  let fd;
  try{
    fd=fs.openSync(marker,"wx",0o600);
    fs.writeSync(fd,JSON.stringify({schema:1,state:"attempted"})+"\n");
    fs.fsyncSync(fd);
  }catch(error){
    if(error?.code==="EEXIST")return {state:"already_attempted"};
    throw error;
  }finally{
    if(fd!==undefined)fs.closeSync(fd);
  }
  if(!isCurrent()) {
    // No network I/O has occurred. Remove only this call's reservation.
    fs.unlinkSync(marker);
    return {state:"not_ready"};
  }
  try {
    const message=await client.sendMessage(ownJid,{text:CONNECTION_CONFIRMATION_TEXT});
    writeReceipt(marker,"sent");
    onSent({id:message?.key?.id||null,phone:ownJid.split("@")[0],
      body:CONNECTION_CONFIRMATION_TEXT});
    return {state:"sent"};
  }catch {
    // A send rejection does not prove the WhatsApp server didn't receive it.
    // Never automatically retry an ambiguous delivery (would spam user).
    writeReceipt(marker,"delivery_uncertain");
    return {state:"delivery_uncertain"};
  }
}
