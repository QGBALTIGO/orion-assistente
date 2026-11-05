/** Public categories only. A provider's error text never becomes a log DTO.
 * Branded preflight failures are created at verified local boundaries; a
 * provider cannot turn a failure after relay into proof of non-delivery by
 * supplying a similarly named `code` property. */
const preflight=new WeakMap();
const known=new Set(['TRANSPORT_UNAVAILABLE_BEFORE_SEND','RECIPIENT_NOT_REGISTERED',
  'RECIPIENT_IDENTITY_UNVERIFIED','MESSAGE_CONTENT_INVALID','MESSAGE_PREPARATION_FAILED',
  'MESSAGE_OUTCOME_UNCERTAIN','DEVICE_REVOKED','CHATBOT_REVOKED','CAMPAIGN_REVOKED','SCHEDULE_REVOKED','BULK_REVOKED']);

export function messagePreflightError(category){
  if(!['TRANSPORT_UNAVAILABLE_BEFORE_SEND','RECIPIENT_NOT_REGISTERED','RECIPIENT_IDENTITY_UNVERIFIED'].includes(category))throw Error('invalid_preflight_category');
  const error=Object.assign(Error(category),{code:category==='TRANSPORT_UNAVAILABLE_BEFORE_SEND'
    ?'transport_unavailable_before_send':'recipient_identity_unverified'});
  preflight.set(error,category);return error;
}

export const knownMessagePreflight=error=>error&&typeof error==='object'?preflight.get(error)||null:null;
export const safeMessageFailureCode=value=>known.has(value)?value:undefined;

export function messageFailureCode(error,{beforeSend,current,chatbot=false,campaign=false,scheduled=false,bulk=false}){
  const revoked=bulk?'BULK_REVOKED':scheduled?'SCHEDULE_REVOKED':campaign?'CAMPAIGN_REVOKED':chatbot?'CHATBOT_REVOKED':'DEVICE_REVOKED';
  if(!current)return revoked;
  if(!beforeSend)return 'MESSAGE_OUTCOME_UNCERTAIN';
  const verified=knownMessagePreflight(error);if(verified)return verified;
  if(['CHATBOT_REVOKED','CHATBOT_EFFECT_REVOKED','CAMPAIGN_REVOKED','SCHEDULE_REVOKED','BULK_REVOKED'].includes(error?.code)||
    ['channel_operation_superseded','chatbot_operation_superseded','device_operation_superseded'].includes(error?.message))return revoked;
  if(['CHATBOT_MESSAGE_INVALID','CHATBOT_EFFECT_INVALID','CAMPAIGN_EFFECT_INVALID','SCHEDULE_EFFECT_INVALID','BULK_EFFECT_INVALID'].includes(error?.code)||
    ['device_effect_invalid','invalid_native_transport','invalid_native_message','channel_send_denied'].includes(error?.message))return 'MESSAGE_CONTENT_INVALID';
  return 'MESSAGE_PREPARATION_FAILED';
}
