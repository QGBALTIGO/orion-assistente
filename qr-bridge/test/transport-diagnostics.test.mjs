import test from 'node:test';
import assert from 'node:assert/strict';
import {makeTransportDiagnostics,receiptDiagnosticDetails,sessionDomainCounts,usyncDeviceDomainCounts} from '../src/transport-diagnostics.mjs';
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

test('receipt classification separates own LID companion from recipient PN primary',()=>{
  const own={id:'5511999999999:4@s.whatsapp.net',lid:'123456789012345:4@lid'};
  assert.deepEqual(receiptDiagnosticDetails({attrs:{from:'123456789012345:3@lid'}},own),{
    kind:'lid',participantKind:'other',hasParticipant:false,isSelf:true,deviceClass:'companion'});
  assert.deepEqual(receiptDiagnosticDetails({attrs:{from:'5511888888888:0@s.whatsapp.net'}},own),{
    kind:'pn',participantKind:'other',hasParticipant:false,isSelf:false,deviceClass:'primary'});
  assert.equal(receiptDiagnosticDetails({attrs:{from:'5511999999999@lid'}},own).isSelf,false);
  assert.deepEqual(receiptDiagnosticDetails({attrs:{from:'123@g.us',participant:'123456789012345:3@lid'}},own),{
    kind:'other',participantKind:'lid',hasParticipant:true,isSelf:true,deviceClass:'companion'});
  assert.equal(receiptDiagnosticDetails({attrs:{from:'invalid'}},null).deviceClass,'unknown');
});

test('session fetch diagnostics count domains without retaining addresses',()=>{
  assert.deepEqual(sessionDomainCounts(['5511999999999@s.whatsapp.net','5511999999999:2@s.whatsapp.net',
    '123456789012345:3@lid','unrecognized@hosted']),{
    count:4,pnCount:2,lidCount:1,otherCount:1,primaryCount:1,companionCount:2});
  assert.equal(sessionDomainCounts(null).count,0);
});

test('device USync results classify domains before library extraction and omit contact-only results',()=>{
  const node={tag:'iq',attrs:{id:'PRIVATE_IQ_ID'},content:[{tag:'usync',content:[{tag:'list',content:[
    {tag:'user',attrs:{jid:'5511999999999@s.whatsapp.net'},content:[{tag:'devices',content:[{tag:'key-index-list',content:Buffer.from('PRIVATE_SIGNED_BYTES')}]}]},
    {tag:'user',attrs:{jid:'123456789012345@lid'},content:[{tag:'devices'}]},
    {tag:'user',attrs:{jid:'unrelated@hosted'},content:[{tag:'contact'}]}
  ]}]}]};
  const counts=usyncDeviceDomainCounts(node);
  assert.deepEqual(counts,{count:2,pnCount:1,lidCount:1,otherCount:0});
  assert.equal(usyncDeviceDomainCounts({content:[]}),null);
  for(const value of ['5511999999999','123456789012345','PRIVATE_SIGNED_BYTES','PRIVATE_IQ_ID'])
    assert.ok(!JSON.stringify(counts).includes(value));
});

test('actual Baileys retry failure shape retains each trace and hides stack',()=>{
  const output=[],d=makeTransportDiagnostics({secret:'test-only-key',emit:x=>output.push(x)});
  d.logger.error({key:{id:''},ids:['PRIVATE_A','PRIVATE_B'],trace:'PRIVATE_STACK'},'error in sending message again');
  d.logger.warn({attrs:{id:'PRIVATE_A',error:'479',from:'5511999999999@s.whatsapp.net'}},'received error in ack');
  d.logger.debug({msgId:'PRIVATE_B'},'sending message to 3 devices');
  d.logger.debug({jidsRequiringFetch:['5511999999999@s.whatsapp.net','123456789012345:3@lid']},'fetching sessions');
  d.logger.debug({participant:'123456789012345:3@lid',sendToAll:false},'forced new session for retry recp');
  assert.equal(output[0].trace,d.tag('PRIVATE_A'));
  assert.equal(output[1].trace,d.tag('PRIVATE_B'));
  assert.equal(output[2].error,'479');
  assert.equal(output[3].count,3);
  assert.equal(output[4].pnCount,1);assert.equal(output[4].lidCount,1);
  assert.equal(output[5].kind,'lid');assert.equal(output[5].deviceClass,'companion');
  for(const value of ['PRIVATE_A','PRIVATE_B','PRIVATE_STACK','5511999999999','123456789012345'])
    assert.ok(!JSON.stringify(output).includes(value));
});
