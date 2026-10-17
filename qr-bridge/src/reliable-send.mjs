/**
 * Generate + cache the EXACT proto.IMessage BEFORE relay.
 * The recipient may issue a Signal retry while relayMessage is still pending.
 *
 * No automatic retry when relay throws: server acceptance can be ambiguous.
 */
import {isVerifiedPilotLid,withVerifiedPilotDevices} from './pilot-recipient.mjs';

export async function sendStoredText({jid,recipientProof,text,makeMessage,relay,store,onPrepared=()=>{},onAccepted=()=>{},freshDevices=false,isCurrent=()=>true}){
  if(typeof jid!=="string" || (!/^[1-9]\d{7,14}@s\.whatsapp\.net$/.test(jid)&&!isVerifiedPilotLid(recipientProof,jid))){
    throw Error("Invalid recipient JID");
  }
  if(typeof text!=="string" || !text.trim() || text.length>1200)throw Error("Invalid text");
  if(typeof makeMessage!=="function" || typeof relay!=="function" || !store?.save){
    throw Error("Reliable send not configured");
  }
  const msg=await makeMessage(jid,text.trim());
  if(!isCurrent())throw Error('A conexão mudou antes do envio. Nenhuma mensagem foi enviada.');
  if(!msg?.key?.id || !msg?.message)throw Error("Failed to build WhatsApp message");
  // Cache MUST be available before first network packet leaves this socket.
  if(store.save(msg.key,msg.message)!==true)throw Error("Unable to cache outgoing message");
  onPrepared(msg);
  if(!isCurrent())throw Error('A conexão mudou antes do envio. Nenhuma mensagem foi enviada.');
  await withVerifiedPilotDevices(recipientProof,()=>relay(jid,msg.message,{
    messageId:msg.key.id,
    // Disable potentially stale recipient-device cache only when requested.
    // A verified LID proof already refreshed and checked this socket's device
    // cache; consume that exact list rather than interpreting a second query.
    useUserDevicesCache:isVerifiedPilotLid(recipientProof,jid)||!freshDevices
  }));
  onAccepted(msg);
  return msg;
}
