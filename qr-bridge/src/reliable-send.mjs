/**
 * Generate + cache the EXACT proto.IMessage BEFORE relay.
 * The recipient may issue a Signal retry while relayMessage is still pending.
 *
 * No automatic retry when relay throws: server acceptance can be ambiguous.
 */
export async function sendStoredText({jid,text,makeMessage,relay,store,onPrepared=()=>{},onAccepted=()=>{},freshDevices=false}){
  if(typeof jid!=="string" || !/^[1-9]\d{7,14}@s\.whatsapp\.net$/.test(jid)){
    throw Error("Invalid recipient JID");
  }
  if(typeof text!=="string" || !text.trim() || text.length>1200)throw Error("Invalid text");
  if(typeof makeMessage!=="function" || typeof relay!=="function" || !store?.save){
    throw Error("Reliable send not configured");
  }
  const msg=await makeMessage(jid,text.trim());
  if(!msg?.key?.id || !msg?.message)throw Error("Failed to build WhatsApp message");
  // Cache MUST be available before first network packet leaves this socket.
  if(store.save(msg.key,msg.message)!==true)throw Error("Unable to cache outgoing message");
  onPrepared(msg);
  await relay(jid,msg.message,{
    messageId:msg.key.id,
    // Disable potentially stale recipient-device cache only when requested.
    useUserDevicesCache:!freshDevices
  });
  onAccepted(msg);
  return msg;
}
