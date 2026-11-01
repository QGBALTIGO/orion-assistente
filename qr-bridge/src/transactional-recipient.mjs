/** Strict, read-only PN resolution for Orion transactional notices.
 *
 * Some Brazilian mobile numbers have a legacy 8-digit WhatsApp PN.
 * However, a separate successful lookup of that number does not prove it
 * belongs to the 9-digit number's owner. Only one provider-authenticated
 * answer to the ORIGINAL number may authorize a JID. Never guess LIDs,
 * never send to an independently matched alias, never log a JID or number.
 *
 * This function neither sends nor changes device keys or consent state.
 */
import {verifiedRecipientFromSingleLookup} from './recipient-diagnostics.mjs';

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
  const resolved=verifiedRecipientFromSingleLookup(phone,original);
  // Do not query a guessed number: a separately registered 8-digit account
  // might belong to someone else and must not receive customer booking data.
  if(!resolved.verified)throw recipientPreflightError();
  const kind=resolved.kind==='provider_normalized'
    ?'verified_provider_normalized':'verified_exact';
  if(typeof resolved.jid!=='string'||!/^[1-9]\d{7,14}@s\.whatsapp\.net$/.test(resolved.jid))
    throw recipientPreflightError();
  if(typeof ownPhone==='string'&&resolved.jid===ownPhone+'@s.whatsapp.net')
    throw recipientPreflightError();
  if(!isCurrent())throw recipientPreflightError();
  return Object.freeze({jid:resolved.jid,kind,requested:phone});
}
