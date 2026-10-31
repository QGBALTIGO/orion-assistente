/**
 * Read-only diagnostics about one intentionally selected contact.
 * Never guess LID -> phone or mutate Signal sessions.
 * Never include WhatsApp JIDs, keys or recipient identifiers in logs.
 */
// Brazilian mobile accounts may still have a legacy 8-digit WA PN.
// A returned alias only proves ownership if the *same queried PN* yielded
// that one verified result. A separate lookup of the alias does NOT prove it
// belongs to the owner of the original phone.
export function brazilLegacyPN(phone) {
  if(typeof phone!=='string'||!/^55[1-9][0-9]9[0-9]{8}$/.test(phone))return null;
  return phone.slice(0,4)+phone.slice(5);
}

export function verifiedRecipientFromSingleLookup(phone,rows) {
  if(typeof phone!=='string'||!/^[1-9]\\d{7,14}$/.test(phone)||!Array.isArray(rows))
    return {verified:false,reason:'invalid_lookup'};
  const candidates=rows.filter(row=>row && typeof row.jid==='string');
  if(candidates.length!==1||rows.length!==1)
    return {verified:false,reason:rows.length===0?'no_result':'ambiguous_lookup'};
  const match=candidates[0], exact=phone+'@s.whatsapp.net';
  const alias=brazilLegacyPN(phone);
  if(match.jid!==exact && (!alias||match.jid!==alias+'@s.whatsapp.net'))
    return {verified:false,reason:'different_identifier'};
  if(match.exists!==true)
    return {verified:false,reason:'not_registered'};
  return {verified:true,kind:match.jid===exact?'exact':'provider_normalized',
          jid:match.jid,lid:typeof match.lid==='string'&&/^\\d{1,30}@lid$/.test(match.lid)?match.lid:null};
}

export function inspectLookup(requestedPhone,results,ownJid) {
  const phone=String(requestedPhone??'');
  if(!/^[1-9]\\d{7,14}$/.test(phone))throw Error('Invalid recipient');
  const self=typeof ownJid==='string'&&ownJid.split('@')[0].split(':')[0]===phone;
  if(!Array.isArray(results))return {registered:null,identity:'unknown',hasLid:null,self,lookupKind:'unavailable'};
  // Preserve earlier exact contact inspection behavior; the pinned 6.7.24
  // parser returns booleans, but older tests may inject a contact object.
  const exact=results.find(row=>row?.jid===phone+'@s.whatsapp.net');
  if(exact){
    return {registered:Boolean(exact.exists),identity:'pn_confirmed',
            hasLid:typeof exact.lid==='string'&&exact.lid.endsWith('@lid'),self,lookupKind:'exact'};
  }
  const verified=verifiedRecipientFromSingleLookup(phone,results);
  if(verified.verified && verified.kind==='provider_normalized')
    return {registered:true,identity:'phone_normalized',hasLid:Boolean(verified.lid),
            self,lookupKind:'provider_normalized'};
  return {registered:results.length===0?false:null,identity:'unverified',
          hasLid:null,self,lookupKind:verified.reason};
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
