/**
 * Read-only diagnostics about one intentionally selected contact.
 * Never guess LID -> phone or mutate Signal sessions.
 * Never include WhatsApp JIDs, keys or recipient identifiers in logs.
 */
export function inspectLookup(requestedPhone,results,ownJid){
  const phone=String(requestedPhone??"");
  if(!/^[1-9]\d{7,14}$/.test(phone))throw Error("Invalid recipient");
  if(!Array.isArray(results))return {registered:null,identity:"unknown",hasLid:null,self:false};
  const match=results.find(x=>typeof x?.jid==="string"&&
    x.jid.split("@")[0].split(":")[0]===phone);
  const self=typeof ownJid==="string"&&ownJid.split("@")[0].split(":")[0]===phone;
  if(!match)return {registered:false,identity:"unverified",hasLid:null,self};
  return {
    registered:Boolean(match.exists),
    identity:match.jid===phone+"@s.whatsapp.net"?"pn_confirmed":"different_identifier",
    hasLid:typeof match.lid==="string"&&match.lid.endsWith("@lid"),
    self
  };
}

export function inspectDelivery(rows,phone,retryCounts=new Map()){
  const outgoing=(Array.isArray(rows)?rows:[])
    .filter(x=>x?.phone===phone&&x.direction==="out").slice(-30);
  const count=s=>outgoing.filter(x=>x.delivery===s).length;
  return {count:outgoing.length,pending:count("pending"),
    serverAccepted:count("server_accepted"),
    delivered:count("delivered"),read:count("read")+count("played"),
    failed:count("failed"),
    retryRequests:outgoing.reduce((n,x)=>n+Number(retryCounts.get(String(x.id||"").replace(/^out:/,""))||0),0),
    messagesWithRetry:outgoing.filter(x=>retryCounts.has(String(x.id||"").replace(/^out:/,""))).length,
    // An absent receipt is not proof the text is unreadable.
    lastReceipt:outgoing.length?String(outgoing[outgoing.length-1].delivery||"unknown"):"none"};
}

/**
 * Compute live delivery/retry diagnostics for every lookup outcome.
 * Never cache the counters: retry receipts can change between inspections.
 */
export function attachDeliveryDiagnostics(summary,rows,phone,retryCounts){
  return {...summary,delivery:inspectDelivery(rows,phone,retryCounts)};
}
