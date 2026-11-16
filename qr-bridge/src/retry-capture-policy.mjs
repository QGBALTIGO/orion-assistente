/** Which messages from a linked phone may be durably cached for retry.
 * Never persist inbound customer messages or disappearing/media content.
 * The encrypted store still applies a 48h TTL, size limit and at-rest AEAD.
 */
export function shouldCacheLinkedSelfText(message){
  const key=message?.key,content=message?.message;
  if(key?.fromMe!==true || typeof key.id!=='string' ||
      !/^[A-Za-z0-9._:-]{1,160}$/.test(key.id) ||
      !content || typeof content!=='object')return false;
  const keys=Object.keys(content);
  if(keys.length!==1 || !['conversation','extendedTextMessage'].includes(keys[0]))return false;
  const text=keys[0]==='conversation'?content.conversation:content.extendedTextMessage?.text;
  return typeof text==='string' && text.trim().length>=1 && text.length<=1200;
}
