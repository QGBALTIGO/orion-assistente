import test from 'node:test';
import assert from 'node:assert/strict';
import {knownMessagePreflight,messagePreflightError,messageFailureCode,safeMessageFailureCode} from '../src/device-message-failure.mjs';

test('only errors issued at the local preflight boundary prove a device send was not attempted',()=>{
  const real=messagePreflightError('TRANSPORT_UNAVAILABLE_BEFORE_SEND');
  const spoof=Object.assign(Error('TRANSPORT_UNAVAILABLE_BEFORE_SEND'),{code:real.code});
  assert.equal(knownMessagePreflight(real),'TRANSPORT_UNAVAILABLE_BEFORE_SEND');
  assert.equal(knownMessagePreflight(spoof),null);
  assert.equal(messageFailureCode(real,{beforeSend:true,current:true}),'TRANSPORT_UNAVAILABLE_BEFORE_SEND');
  assert.equal(messageFailureCode(real,{beforeSend:false,current:true}),'MESSAGE_OUTCOME_UNCERTAIN');
  assert.equal(messageFailureCode(real,{beforeSend:true,current:false}),'DEVICE_REVOKED');
  assert.equal(safeMessageFailureCode('private-provider-error: token=secret'),undefined);
});

test('a changed connection after relay never claims the message was unsent',()=>{
  for(const [scope,revoked] of [[{},'DEVICE_REVOKED'],[{chatbot:true},'CHATBOT_REVOKED'],
    [{campaign:true},'CAMPAIGN_REVOKED'],[{scheduled:true},'SCHEDULE_REVOKED'],[{bulk:true},'BULK_REVOKED']]){
    assert.equal(messageFailureCode({code:'CHATBOT_REVOKED'},{beforeSend:true,current:false,...scope}),revoked);
    assert.equal(messageFailureCode({code:'CHATBOT_REVOKED'},{beforeSend:false,current:false,...scope}),
      'MESSAGE_OUTCOME_UNCERTAIN');
  }
});
