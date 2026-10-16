/**
 * Logical health of a WhatsApp linked device is different from API health.
 * No cellular or browser connectivity is required after initial pairing.
 */
export function timestampMs(value){
  if(value instanceof Date)return value.getTime();
  if(value && typeof value.toNumber==="function")value=value.toNumber();
  const n=Number(value);
  if(!Number.isFinite(n)||n<=0)return null;
  return n<1e12?n*1000:n;
}
export function shouldIngestUpsert(type,timestamp,now=Date.now(),graceMs=15*60*1000){
  if(type==="notify")return true;
  if(type!=="append")return false;
  const time=timestampMs(timestamp);
  return time!==null && time>=now-graceMs && time<=now+60000;
}
export function ingestionHealth({connected,openAt,pendingNotifications,
  hasCurrentMessages,now=Date.now()}){
  if(!connected)return "disconnected";
  if(pendingNotifications || hasCurrentMessages)return "receiving";
  if(!openAt)return "unknown";
  if(now-openAt>45000)return "awaiting_initial_sync";
  return "initializing";
}
