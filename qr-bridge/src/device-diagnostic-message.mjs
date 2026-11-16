/** A private, branded capability made only from the broker's confirmed grant.
 * No public route or generic send option can opt into sending to oneself. */
import {channelId,digest} from './channel-storage.mjs';
const permits=new WeakSet(),phone=value=>typeof value==='string'&&/^[1-9]\d{7,14}$/.test(value);

export function diagnosticMessagePermit(grant,expected,operationId){
  if(grant?.authorized!==true||grant.purpose!=='diagnostic_test'||grant.type!=='send_text'||grant.operation_id!==operationId||
    !channelId(grant.diagnostic_request_id)||grant.confirmed!==true||!['self','explicit'].includes(grant.recipient_mode)||
    grant.device_id!==expected.id||grant.generation!==expected.generation||grant.settings_revision!==expected.settings_revision||
    !phone(grant.self_phone)||!phone(grant.payload?.phone)||typeof grant.payload.text!=='string'||
    (grant.recipient_mode==='self'?grant.payload.phone!==grant.self_phone:grant.payload.phone===grant.self_phone))throw Error('diagnostic_message_refused');
  const value=Object.freeze({operation_id:operationId,diagnostic_request_id:grant.diagnostic_request_id,
    purpose:grant.purpose,confirmed:true,recipient_mode:grant.recipient_mode,self_phone:grant.self_phone,
    device_id:grant.device_id,generation:grant.generation,settings_revision:grant.settings_revision,
    phone:grant.payload.phone,text:grant.payload.text,
    fingerprint:digest(JSON.stringify([grant.operation_id,grant.diagnostic_request_id,grant.purpose,grant.confirmed,
      grant.recipient_mode,grant.self_phone,grant.device_id,grant.generation,grant.settings_revision,grant.payload]))});
  permits.add(value);return value;
}

export function currentDiagnosticMessage(permit,{phone:recipient,text,self,snapshot}){
  return !!(permits.has(permit)&&permit.phone===recipient&&permit.text===text&&phone(self)&&permit.self_phone===self&&
    snapshot?.id===permit.device_id&&snapshot.generation===permit.generation&&snapshot.settings_revision===permit.settings_revision&&
    (permit.recipient_mode==='self'?recipient===self:recipient!==self));
}
