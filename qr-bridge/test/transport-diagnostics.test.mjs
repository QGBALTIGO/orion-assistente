import test from 'node:test';
import assert from 'node:assert/strict';
import {makeTransportDiagnostics} from '../src/transport-diagnostics.mjs';
import {resolveMessagePhone} from '../src/core.mjs';

test('Baileys 6 explicit sender PN resolves private LID without nonexistent mapping API',async()=>{
  assert.equal(await resolveMessagePhone({remoteJid:'123456789012345:2@lid',senderPn:'5511999999999@s.whatsapp.net'},{}),'5511999999999');
  assert.equal(await resolveMessagePhone({remoteJid:'123456789012345@lid',senderPn:'5511999999999@lid'},{}),null);
  assert.equal(await resolveMessagePhone({remoteJid:'123456789012345@g.us',remoteJidAlt:'5511999999999@s.whatsapp.net',participantPn:'5511999999999@s.whatsapp.net'},{}),null);
});
test('diagnostics correlate original ID without exposing identifiers, content, or error data',()=>{
  const output=[],d=makeTransportDiagnostics({secret:'test-only-key',now:()=>100,emit:x=>output.push(x)});
  const privateId='PRIVATE_MESSAGE_ID';
  d.record('send_prepared',privateId,{phone:'5511999999999',body:'private body'});
  d.logger.error({key:{id:privateId,remoteJid:'5511999999999@s.whatsapp.net'},err:{message:'Bad MAC PRIVATE_KEY',stack:'PRIVATE_STACK'}},'failed to decrypt message');
  d.logger.debug({attrs:{id:privateId},key:{id:privateId}},'recv retry request');
  d.logger.trace({xml:'PRIVATE_CRYPTO'},'xml recv');
  assert.equal(output.length,3);
  assert.equal(new Set(output.map(x=>x.trace)).size,1);
  assert.equal(output[1].error,'bad_mac');
  assert.equal(d.snapshot().counts.retry_received,1);
  const text=JSON.stringify(output);
  for(const secret of [privateId,'5511999999999','private body','PRIVATE_KEY','PRIVATE_STACK','PRIVATE_CRYPTO'])assert.ok(!text.includes(secret));
});
test('getMessage lookups are not falsely counted as recipient retries and trace retention is bounded',()=>{
  const d=makeTransportDiagnostics({secret:'test-only-key'});
  for(let i=0;i<210;i++)d.record('get_message','id-'+i,{cached:true});
  assert.equal(d.snapshot().events.length,200);
  assert.equal(d.snapshot().counts.get_message,210);
  assert.equal(d.snapshot().counts.retry_received,undefined);
});
