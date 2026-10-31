/** Strict, read-only PN resolution for Orion transactional notices.
 *
 * A Brazilian mobile number may have a legacy 8-digit WhatsApp ID. Resolve
 * it only if the provider says the requested PN is not registered AND the
 * canonical 8-digit candidate is uniquely registered. Never guess a LID,
 * never select an unrelated ID, never expose a JID in logs or API results.
 *
 * This function neither sends nor changes device keys or consent state.
 */
import {brazilLegacyPN,verifiedRecipientFromSingleLookup} from './recipient-diagnostics.mjs';

export function recipientPreflightError(){
  const error=new Error('O WhatsApp não confirmou o destinatário. Nenhuma mensagem foi enviada.');
  error.code='recipient_identity_unverified';
  return error;
}

export async function resolveTransactionalRecipient(phone,lookup,{isCurrent=()=>true,ownPhone=null}={}){
  const validPhone=typeof phone==='string'&&/^[1-9]\d{7,14}$/.test(phone);
  if(!validPhone||typeof lookup!=='function'||!isCurrent())throw recipientPreflightError();
  let original;
  try{original=await lookup(phone);}catch{throw recipientPreflightError();}
  if(!isCurrent()||!Array.isArray(original))throw recipientPreflightError();
  let resolved=verifiedRecipientFromSingleLookup(phone,original);
  let kind='verified_exact';
  if(!resolved.verified){
    // Never fall back when the first query contains an unrelated/ambiguous
    // contact: that might be a different person, not an old Brazilian ID.
    if(original.length!==0)throw recipientPreflightError();
    const legacy=brazilLegacyPN(phone);
    if(!legacy)throw recipientPreflightError();
    let alternative;
    try{alternative=await lookup(legacy);}catch{throw recipientPreflightError();}
    if(!isCurrent()||!Array.isArray(alternative))throw recipientPreflightError();
    resolved=verifiedRecipientFromSingleLookup(legacy,alternative);
    if(!resolved.verified||resolved.kind!=='exact')throw recipientPreflightError();
    kind='verified_brazil_legacy';
  }else if(resolved.kind==='provider_normalized'){
    kind='verified_provider_normalized';
  }
  if(typeof resolved.jid!=='string'||!/^[1-9]\d{7,14}@s\.whatsapp\.net$/.test(resolved.jid))
    throw recipientPreflightError();
  if(typeof ownPhone==='string'&&resolved.jid===ownPhone+'@s.whatsapp.net')
    throw recipientPreflightError();
  if(!isCurrent())throw recipientPreflightError();
  return Object.freeze({jid:resolved.jid,kind,requested:phone});
}
