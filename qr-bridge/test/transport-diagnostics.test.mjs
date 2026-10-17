import test from 'node:test';
import assert from 'node:assert/strict';
import {makeTransportDiagnostics,receiptDiagnosticDetails,sessionDomainCounts,usyncDeviceDomainCounts,
  encryptionTargetDetails,observeSignalRepository} from '../src/transport-diagnostics.mjs';
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
  assert.deepEqual(counts,{count:2,pnCount:1,lidCount:1,otherCount:0,
    pnPrimaryCount:0,lidPrimaryCount:0,otherPrimaryCount:0,
    pnCompanionCount:0,lidCompanionCount:0,otherCompanionCount:0,
    pnEligibleCompanionCount:0,lidEligibleCompanionCount:0,otherEligibleCompanionCount:0});
  assert.equal(usyncDeviceDomainCounts({content:[]}),null);
  for(const value of ['5511999999999','123456789012345','PRIVATE_SIGNED_BYTES','PRIVATE_IQ_ID'])
    assert.ok(!JSON.stringify(counts).includes(value));
});

test('USync distinguishes primary-only LID rows from eligible companions using the pinned extractor rules',()=>{
  const user=(jid,devices)=>({tag:'user',attrs:{jid},content:[{tag:'devices',content:[
    {tag:'device-list',content:devices.map(attrs=>({tag:'device',attrs}))},
    {tag:'key-index-list',content:Buffer.from('PRIVATE_INDEX_BYTES')}
  ]}]});
  const node={content:[{tag:'usync',content:[{tag:'list',content:[
    user('5511999999999@s.whatsapp.net',[{id:'0'},{id:'4','key-index':'1'},{id:'2','key-index':'1'},{id:'3'},{id:'bad'}]),
    user('123456789012345@lid',[{id:'0'},{id:'4','key-index':'1'},{id:'5','key-index':'0'}])
  ]}]}]};
  const own={id:'5511999999999:4@s.whatsapp.net',lid:'123456789012345:4@lid'};
  const counts=usyncDeviceDomainCounts(node,own);
  assert.equal(counts.pnPrimaryCount,1);assert.equal(counts.lidPrimaryCount,1);
  assert.equal(counts.pnCompanionCount,3);assert.equal(counts.lidCompanionCount,2);
  assert.equal(counts.pnEligibleCompanionCount,1);
  // The installed extractor only compares me.id (PN), so this LID remains
  // eligible even though the matching LID device is the current connection.
  assert.equal(counts.lidEligibleCompanionCount,1);
  const output=[],diagnostics=makeTransportDiagnostics({secret:'test',emit:x=>output.push(x)});
  diagnostics.record('usync_device_domains',null,counts);
  assert.equal(output[0].pnEligibleCompanionCount,1);
  assert.equal(output[0].lidPrimaryCount,1);
  for(const value of ['5511999999999','123456789012345','PRIVATE_INDEX_BYTES'])
    assert.ok(!JSON.stringify(output).includes(value));
});

test('actual encryption target classification distinguishes own PN/LID from recipient without inferring aliases',()=>{
  const own={id:'5511999999999:4@s.whatsapp.net',lid:'123456789012345:4@lid'};
  assert.deepEqual(encryptionTargetDetails('123456789012345@lid',own),{kind:'lid',deviceClass:'primary',isSelf:true,ownDomainMismatch:false});
  assert.deepEqual(encryptionTargetDetails('123456789012345:2@s.whatsapp.net',own),{kind:'pn',deviceClass:'companion',isSelf:false,ownDomainMismatch:true});
  assert.deepEqual(encryptionTargetDetails('5511888888888@s.whatsapp.net',own),{kind:'pn',deviceClass:'primary',isSelf:false,ownDomainMismatch:false});
  assert.deepEqual(encryptionTargetDetails('123@g.us',own),{kind:'other',deviceClass:'unknown',isSelf:false,ownDomainMismatch:false});
  assert.equal(encryptionTargetDetails('5511999999999@s.whatsapp.net',own).ownDomainMismatch,false);
  assert.equal(encryptionTargetDetails('123456789012345@s.whatsapp.net',{id:'123456789012345@s.whatsapp.net',lid:own.lid}).ownDomainMismatch,false);
  assert.equal(encryptionTargetDetails('123456789012345@s.whatsapp.net',{lid:'123456789012345@s.whatsapp.net'}).ownDomainMismatch,false);
  const output=[],diagnostics=makeTransportDiagnostics({secret:'test',emit:x=>output.push(x)});
  diagnostics.record('signal_encrypt_target',null,encryptionTargetDetails('123456789012345@s.whatsapp.net',own));
  assert.equal(output[0].ownDomainMismatch,true);
  for(const value of ['5511999999999','123456789012345'])assert.ok(!JSON.stringify(output).includes(value));
});

test('repository observer preserves this, arguments, repository identity and exact Promise without reading payload',async()=>{
  const pending=Promise.resolve({ciphertext:Buffer.from('PRIVATE_CIPHERTEXT')});
  let observed,calledThis,calledArgs,factoryThis,factoryArgs;
  const repository={encryptMessage(...args){calledThis=this;calledArgs=args;return pending;},other:'untouched'};
  const factory=function(...args){factoryThis=this;factoryArgs=args;return repository;};
  const context={},auth={creds:{private:'PRIVATE_AUTH'}};
  const make=observeSignalRepository(factory,jid=>{observed=jid;});
  const result=make.call(context,auth);
  assert.equal(result,repository);assert.equal(factoryThis,context);assert.deepEqual(factoryArgs,[auth]);
  const request={jid:'123456789012345@lid',get data(){throw Error('Payload must never be read by observer');}};
  const returned=result.encryptMessage(request,'extra');
  assert.equal(returned,pending);assert.equal(calledThis,repository);
  assert.equal(calledArgs[0],request);assert.equal(calledArgs[1],'extra');
  assert.equal(observed,request.jid);assert.equal(result.other,'untouched');
  await returned;
});

test('repository observer cannot mask original throws or rejections and never interrupts encryption',async()=>{
  const originalError=Error('PRIVATE_ORIGINAL_ERROR');
  const repository={encryptMessage(){throw originalError;}};
  observeSignalRepository(()=>repository,()=>{throw Error('PRIVATE_OBSERVER_ERROR');})();
  assert.throws(()=>repository.encryptMessage({jid:'123@lid'}),error=>error===originalError);
  const rejected=Promise.reject(originalError),asyncRepository={encryptMessage(){return rejected;}};
  observeSignalRepository(()=>asyncRepository,()=>{throw Error('PRIVATE_OBSERVER_ERROR');})();
  const returned=asyncRepository.encryptMessage({jid:'123@lid'});
  assert.equal(returned,rejected);await assert.rejects(returned,error=>error===originalError);
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
