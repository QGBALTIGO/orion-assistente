import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import * as sdk from '@whiskeysockets/baileys';
import {makeEncryptedRetryStore} from '../src/encrypted-retry-store.mjs';
import {urlButtonContent} from '../src/url-button.mjs';
import {normalizeInbound} from '../src/inbound-event.mjs';
import {forwardAssistant,registerAssistantIngress,acknowledgeAssistantPresentation} from '../src/ai-forward.mjs';
import {makeTransportDiagnostics} from '../src/transport-diagnostics.mjs';
import {createAssistantIngress} from '../src/assistant-ingress.mjs';
import {buildAssistantPresentation,validateAssistantPresentation,assistantNativeMode,createAssistantPresentationStore,
  relayAssistantPresentation,assistantSelectionCandidate} from '../src/assistant-presentation.mjs';
process.env.ORION_RETRY_TEST_MODE='1';
const nonce='A'.repeat(32),token=index=>`oa1_${nonce}_${index}`,recipient='5567999999901',other='5567999999902';
const secret='synthetic-assistant-presentation-secret-'.repeat(2),jid=recipient+'@s.whatsapp.net';
const presentation=()=>({version:1,id:nonce,action:{type:'reply_buttons',text:'Pedido fictício: confirmar?',footer:'',
  buttons:[{id:token(0),title:'Confirmar'},{id:token(1),title:'Corrigir'}]}});
const quoted=(id,text='1',phone=recipient)=>({key:{id:'synthetic-reply',fromMe:false,remoteJid:phone+'@s.whatsapp.net'},
  message:{extendedTextMessage:{text,contextInfo:{stanzaId:id}}}});
const clicked=(id,selection=token(0))=>({key:{id:'synthetic-click',fromMe:false,remoteJid:jid},
  message:{interactiveResponseMessage:{nativeFlowResponseMessage:{name:'quick_reply',paramsJson:JSON.stringify({id:selection,display_text:'texto não confiável'})},contextInfo:{stanzaId:id}}}});
const wire=value=>sdk.proto.Message.decode(sdk.proto.Message.encode(value).finish());
function fixture(t){
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'orion-assistant-presentation-'));
  t.after(()=>{assert.ok(path.resolve(directory).startsWith(path.resolve(os.tmpdir())+path.sep+'orion-assistant-presentation-'));fs.rmSync(directory,{recursive:true,force:true});});
  const scope={tenant:'synthetic',channel:'synthetic',registration:123};
  const open=(patch={})=>createAssistantPresentationStore({directory,secret,scope,...patch});
  return {directory,open,scope,retry:makeEncryptedRetryStore({directory:path.join(directory,'retry'),secret}),store:open()};
}

test('accepted presentation provenance is private, immutable and scoped to the stored peer and namespace',t=>{
  const f=fixture(t),id='provenanced-provider',proof={peer_tag:'a'.repeat(64),review_stamp:'b'.repeat(64),kind:'navigation'};
  f.store.prepared(presentation(),recipient,{key:{id,fromMe:true}},proof);
  assert.equal(f.store.selectionProvenance(recipient,id),null);
  f.store.accepted(recipient,id);proof.review_stamp='c'.repeat(64);
  const captured=f.open().selectionProvenance(recipient,id);assert.equal(captured.review_stamp,'b'.repeat(64));
  assert.equal(f.store.selectionProvenance(other,id),null);
  assert.equal(f.open({scope:{...f.scope,registration:124}}).selectionProvenance(recipient,id),null);
  assert.throws(()=>f.store.prepared(presentation(),recipient,{key:{id,fromMe:true}},proof));
  const candidate=clicked(id);candidate.provenance=proof;
  assert.deepEqual(f.store.resolve(candidate,recipient),{id:token(0),quoted_message_id:id});
  assert.equal(f.store.selectionProvenance(recipient,id).review_stamp,'b'.repeat(64));
});

test('a known relay keeps its original provenance if a new failure arrives between prepared and accepted',async t=>{
  const f=fixture(t),queue=createAssistantIngress({directory:path.join(f.directory,'ingress'),secret,
    scope:{tenant_id:'10000000-0000-4000-8000-000000000001',channel_id:'20000000-0000-4000-8000-000000000001',session_digest:'a'.repeat(64)}});
  const start=id=>{queue.append({key:{id,fromMe:false},message:{conversation:'synthetic'}},'notify',Date.now());
    queue.admitted(id);queue.prepare([id],{phone:recipient,proofs:[{revision:7,control_revision:31}]});};
  start('reply');queue.consume(['reply']);const provenance={...queue.consumptionProof(['reply']),kind:'summary'};
  const sent=await relayAssistantPresentation({presentation:presentation(),provenance,sdk:{...sdk,userJid:other+'@s.whatsapp.net'},jid,phone:recipient,
    retryStore:f.retry,presentationStore:f.store,isCurrent:()=>true,assertAuthorized:async()=>{},beforeSend:async()=>{},
    acknowledge:async()=>({acknowledged:true}),relay:async()=>{start('new-failure');queue.failPreparation(['new-failure'],'prepare_failed');}});
  const saved=f.open().selectionProvenance(recipient,sent.key.id);
  assert.deepEqual(saved,provenance);assert.ok(f.store.resolve(clicked(sent.key.id),recipient));
  assert.equal(queue.allowsSelection(recipient,saved),false);
  queue.complete('reply',{reviewDelivered:true});assert.equal(queue.requiresReview(recipient),true);
});
test('real pinned protobuf retains two opaque quick replies without accepting provider nodes',async()=>{
  const built=await buildAssistantPresentation(presentation(),{sdk,interactiveMode:'native'});
  const native=wire(built.content).interactiveMessage.nativeFlowMessage;
  assert.deepEqual(native.buttons.map(x=>JSON.parse(x.buttonParamsJson)),[
    {display_text:'Confirmar',id:token(0)},{display_text:'Corrigir',id:token(1)}]);
  assert.equal(native.messageVersion,1);assert.ok(built.relayOptions.additionalNodes);
  assert.throws(()=>validateAssistantPresentation({...presentation(),action:{...presentation().action,nodes:[{}]}}));
  assert.throws(()=>validateAssistantPresentation({...presentation(),action:{...presentation().action,buttons:[{id:token(0),title:'Confirmar'}]}}));
  assert.throws(()=>validateAssistantPresentation({...presentation(),action:{...presentation().action,buttons:[{id:token(0),title:'A'},{id:token(0),title:'B'}]}}));
});
test('conservative numbered fallback keeps labels and hides tokens, with an explicit quoted reply instruction',async()=>{
  const built=await buildAssistantPresentation(presentation(),{sdk});
  const message=wire(built.content);assert.equal(message.interactiveMessage,null);
  assert.match(message.extendedTextMessage.text,/1\. Confirmar/);assert.match(message.extendedTextMessage.text,/2\. Corrigir/);
  assert.match(message.extendedTextMessage.text,/citando esta mensagem/);assert.doesNotMatch(message.extendedTextMessage.text,/oa1_/);
  assert.deepEqual(built.selectionIds,[token(0),token(1)]);
});
test('assistant list uses the existing single-select proto and conservative fallback with the same opaque choices',async()=>{
  const p={version:1,id:nonce,action:{type:'list',title:'Serviços fictícios',description:'Escolha uma opção',button_text:'Ver opções',footer:'',
    sections:[{title:'',rows:[{id:token(0),title:'Serviço A',description:'Valor cadastrado'},{id:token(1),title:'Serviço B',description:''}]}]}};
  const native=await buildAssistantPresentation(p,{sdk,interactiveMode:'native'});
  assert.equal(wire(native.content).listMessage.listType,1);
  assert.deepEqual(wire(native.content).listMessage.sections[0].rows.map(x=>x.rowId),[token(0),token(1)]);
  const text=await buildAssistantPresentation(p,{sdk});assert.match(wire(text.content).extendedTextMessage.text,/1\. Serviço A/);
  assert.deepEqual(text.selectionIds,native.selectionIds);
  assert.throws(()=>validateAssistantPresentation({...p,action:{...p.action,sections:[{rows:[{id:'oa1_'+'Z'.repeat(32)+'_0',title:'Outra empresa'}]}]}}));
});
test('native assistant opt-in binds exact tenant, channel and recipient and never inherits subscription flags',()=>{
  const tenantId='11111111-1111-4111-8111-111111111111',channel='22222222-2222-4222-8222-222222222222';
  const opts={tenantId,channelId:channel,phone:recipient};
  assert.equal(assistantNativeMode({...opts,env:{QR_NATIVE_INTERACTIVE_ENABLED:'1'}}),'text_fallback');
  assert.equal(assistantNativeMode({...opts,env:{QR_ASSISTANT_NATIVE_SCOPES:`${tenantId}:${channel}:${recipient}`}}),'native');
  assert.equal(assistantNativeMode({...opts,phone:other,env:{QR_ASSISTANT_NATIVE_SCOPES:`${tenantId}:${channel}:${recipient}`}}),'text_fallback');
  for(const value of ['*',`${tenantId}:${channel}:*`,`${tenantId}:${channel}:${recipient},invalid`])
    assert.equal(assistantNativeMode({...opts,env:{QR_ASSISTANT_NATIVE_SCOPES:value}}),'text_fallback');
});
test('appointment proof URLs have a separate exact origin/path/fragment validator; generic URL buttons stay strict',async()=>{
  const action={type:'url_button',text:'Reserva fictícia verificada.',button_text:'Ver agendamento',footer:'',
    purpose:'appointment',url:'https://orionagenda.pro/AG-SYNTHETIC#proof_synthetic_123456'};
  const built=await buildAssistantPresentation({version:1,id:nonce,action},{sdk,interactiveMode:'native'});
  assert.equal(JSON.parse(wire(built.content).interactiveMessage.nativeFlowMessage.buttons[0].buttonParamsJson).url,action.url);
  const generic={...action};delete generic.purpose;assert.throws(()=>urlButtonContent(generic));
  for(const url of ['https://other.example/a#proof_synthetic_123456','http://orionagenda.pro/a#proof_synthetic_123456',
    'https://orionagenda.pro/a?next=https://other.example/#proof_synthetic_123456','https://orionagenda.pro/private#proof_synthetic_123456',
    'https://orionagenda.pro/a#short','https://orionagenda.pro/a#token%0a1234567890'])
    assert.throws(()=>validateAssistantPresentation({version:1,id:nonce,action:{...action,url}}));
});
test('exact protocol and encrypted selection map exist before relay; only accepted messages activate the ACK',async t=>{
  const f=fixture(t),acks=[],p=presentation();let calls=0,sentId;
  const sent=await relayAssistantPresentation({presentation:p,sdk:{...sdk,userJid:other+'@s.whatsapp.net'},jid,phone:recipient,
    retryStore:f.retry,presentationStore:f.store,isCurrent:()=>true,assertAuthorized:async()=>{},beforeSend:async()=>{},
    acknowledge:async body=>{acks.push(body);return {acknowledged:true};},
    relay:async(to,body,opts)=>{calls++;sentId=opts.messageId;assert.equal(to,jid);
      assert.deepEqual(wire(f.retry.get({id:sentId,remoteJid:jid})),wire(body));
      assert.equal(f.store.resolve(quoted(sentId),recipient),null);assert.equal(acks.length,0);}});
  assert.equal(calls,1);assert.equal(sent.key.id,sentId);assert.deepEqual(acks,[{phone:recipient,presentation_id:nonce,provider_id:sentId}]);
  assert.deepEqual(f.open().resolve(quoted(sentId),recipient),{id:token(0),quoted_message_id:sentId});
  assert.deepEqual(f.open().resolve(clicked(sentId),recipient),{id:token(0),quoted_message_id:sentId});
  assert.equal(f.open().resolve(quoted(sentId,'1',other),other),null);
  assert.equal(f.open({scope:{...f.scope,registration:124}}).resolve(quoted(sentId),recipient),null);
  assert.equal(f.store.resolve({key:{fromMe:false},message:{conversation:'1'}},recipient),null);
  const encrypted=fs.readdirSync(f.directory,{recursive:true}).filter(x=>x.endsWith('.enc')).map(x=>fs.readFileSync(path.join(f.directory,x),'utf8')).join('');
  assert.doesNotMatch(encrypted,/oa1_|5567999999901|Pedido fictício/);
});
test('a lost ACK is retried only as an idempotent ACK, including after restart, with no second relay',async t=>{
  const f=fixture(t);let sends=0,ackCalls=0;
  const sent=await relayAssistantPresentation({presentation:presentation(),sdk:{...sdk,userJid:other+'@s.whatsapp.net'},jid,phone:recipient,
    retryStore:f.retry,presentationStore:f.store,isCurrent:()=>true,assertAuthorized:async()=>{},beforeSend:async()=>{},
    acknowledge:async()=>{ackCalls++;throw Error('synthetic lost response');},relay:async()=>{sends++;}});
  assert.equal(sends,1);assert.equal(ackCalls,2);
  const reopened=f.open(),selection=reopened.resolve(clicked(sent.key.id),recipient);assert.ok(selection);
  assert.equal(await reopened.acknowledge(recipient,selection.quoted_message_id,async()=>{ackCalls++;return {acknowledged:true};}),true);
  assert.equal(sends,1);assert.equal(ackCalls,3);
  assert.equal(await reopened.acknowledge(recipient,selection.quoted_message_id,()=>assert.fail('Already acknowledged')),true);
});
test('ambiguous relay retains an unaccepted map and never emits an ACK or a fallback send',async t=>{
  const f=fixture(t);let sends=0,acks=0,id;
  await assert.rejects(relayAssistantPresentation({presentation:presentation(),sdk:{...sdk,userJid:other+'@s.whatsapp.net'},jid,phone:recipient,
    retryStore:f.retry,presentationStore:f.store,isCurrent:()=>true,assertAuthorized:async()=>{},beforeSend:async()=>{},
    acknowledge:async()=>{acks++;},relay:async(_,__,opts)=>{id=opts.messageId;sends++;throw Error('synthetic relay unknown');}}),/unknown/);
  assert.equal(sends,1);assert.equal(acks,0);assert.ok(f.retry.get({id,remoteJid:jid}));
  assert.equal(f.open().resolve(clicked(id),recipient),null);
});
test('revocation after preparation prevents cache and relay; a quote cannot grant acceptance',async t=>{
  const f=fixture(t);let current=true,sends=0;
  await assert.rejects(relayAssistantPresentation({presentation:presentation(),sdk:{...sdk,userJid:other+'@s.whatsapp.net'},jid,phone:recipient,
    retryStore:f.retry,presentationStore:f.store,isCurrent:()=>current,assertAuthorized:async()=>{},beforeSend:async()=>{current=false;},
    acknowledge:async()=>assert.fail('No ACK'),relay:async()=>{sends++;}}),/superseded/);
  assert.equal(sends,0);
});
test('ACK wait is bounded and concurrent fast clicks share one accepted acknowledgement',async t=>{
  const f=fixture(t),store=f.open({ackTimeoutMs:10}),id='synthetic-accepted';
  store.prepared(presentation(),recipient,{key:{id,fromMe:true}});store.accepted(recipient,id);
  let calls=0;const hung=()=>{calls++;return new Promise(()=>{});};
  assert.deepEqual(await Promise.all([store.acknowledge(recipient,id,hung),store.acknowledge(recipient,id,hung)]),[false,false]);
  assert.equal(calls,2);
  assert.equal(await store.acknowledge(recipient,id,async()=>({acknowledged:true}),()=>false),false);
});
test('native assistant selections are recognized as typed metadata and view-once/foreign IDs stay outside the contract',()=>{
  const result=normalizeInbound(clicked('synthetic-summary'));
  assert.equal(result.event.kind,'text');assert.equal(result.text,'[Escolha recebida]');assert.deepEqual(result.selection,{id:token(0),quoted_message_id:'synthetic-summary'});
  assert.equal(assistantSelectionCandidate(clicked('synthetic-summary','ocb:subscription-scope')),null);
  const once=clicked('synthetic-summary');once.message={viewOnceMessage:{message:once.message}};
  assert.equal(normalizeInbound(once).event.kind,'view_once');
  const bad=clicked('synthetic-summary');delete bad.message.interactiveResponseMessage.contextInfo;
  assert.equal(assistantSelectionCandidate(bad),null);
});
test('rolling compatibility omits new request fields until the notice explicitly advertises support',async()=>{
  const bodies=[],options={apiUrl:'http://api.railway.internal/internal/qr/ai/reply',token:secret,phone:recipient,messageId:'synthetic',text:'[Escolha recebida]',
    selection:{id:token(0),quoted_message_id:'synthetic-summary'},fetchImpl:async(_,request)=>{bodies.push(JSON.parse(request.body));return {ok:true,json:async()=>({answer:'Resumo',presentation:presentation()})};}};
  assert.equal(await forwardAssistant(options),'Resumo');assert.equal(bodies[0].capabilities,undefined);assert.equal(bodies[0].selection,undefined);
  const result=await forwardAssistant({...options,interactionSupported:true});assert.equal(result.presentation.id,nonce);
  assert.deepEqual(bodies[1].capabilities,{assistant_interactions:1});assert.equal(bodies[1].selection.id,token(0));
  const proof=await registerAssistantIngress({...options,fetchImpl:async()=>({ok:true,json:async()=>({revision:2,assistant_interactions:1})})});
  assert.deepEqual(proof,{revision:2,assistant_interactions:1});
});
test('legacy presentation ACK uses only its fixed authenticated endpoint and never a payment or send route',async()=>{
  const calls=[];const payload={phone:recipient,presentation_id:nonce,provider_id:'synthetic-summary'};
  await acknowledgeAssistantPresentation({apiUrl:'http://api.railway.internal/internal/qr/ai/reply',token:secret,payload,
    fetchImpl:async(url,request)=>{calls.push({url:String(url),body:JSON.parse(request.body)});return {ok:true,json:async()=>({acknowledged:true})};}});
  assert.deepEqual(calls,[{url:'http://api.railway.internal/internal/qr/ai/presentation-ack',body:payload}]);
});
test('selection diagnostics report bounded reasons without changing the fail-closed map contract',t=>{
  const f=fixture(t),observed=[],emit=value=>observed.push(value),id='synthetic-diagnostic-summary';
  assert.equal(f.store.resolve(clicked(id),recipient,emit),null);assert.deepEqual(observed,['map_missing']);
  f.store.prepared(presentation(),recipient,{key:{id,fromMe:true}});observed.length=0;
  assert.equal(f.store.resolve(clicked(id),recipient,emit),null);assert.deepEqual(observed,['map_unaccepted']);
  f.store.accepted(recipient,id);observed.length=0;
  assert.equal(f.store.resolve(clicked(id,token(9)),recipient,emit),null);assert.deepEqual(observed,['choice_mismatch']);
  assert.ok(f.store.resolve(clicked(id),recipient,()=>{throw Error('synthetic broken log sink');}));
  assert.equal(f.store.resolve({key:{fromMe:true},message:clicked(id).message},recipient,emit),null);
  assert.ok(observed.every(value=>/^[a-z_]+$/.test(value)));
});
test('ACK diagnostic codes use the existing HMAC trace and never expose payload, provider body or exception text',async t=>{
  const f=fixture(t),events=[],id='synthetic-private-provider-id',d=makeTransportDiagnostics({secret,emit:event=>events.push(event)});
  f.store.prepared(presentation(),recipient,{key:{id,fromMe:true}});f.store.accepted(recipient,id);
  const diagnostic=category=>d.record('assistant_selection',id,{category});
  const refused=async()=>{throw Object.assign(Error('PRIVATE PROVIDER TEXT '+recipient+' '+nonce),{status:403});};
  assert.equal(await f.store.acknowledge(recipient,id,refused,()=>true,diagnostic),false);
  assert.equal(events.filter(x=>x.category==='ack_http_403').length,2);
  const serialized=JSON.stringify(events);
  for(const value of [secret,recipient,nonce,id,'PRIVATE PROVIDER TEXT',token(0)])assert.equal(serialized.includes(value),false);
  assert.ok(events.every(x=>/^[a-f0-9]{20}$/.test(x.trace)&&/^[a-z0-9_]+$/.test(x.category)));
  assert.equal(await f.store.acknowledge(recipient,id,async()=>({acknowledged:true}),()=>true,()=>{throw Error('log sink');}),true);
});
test('legacy ACK preserves only the response status, never its private response body',async()=>{
  let bodyReads=0;
  await assert.rejects(acknowledgeAssistantPresentation({apiUrl:'http://api.railway.internal/internal/qr/ai/reply',token:secret,
    payload:{phone:recipient,presentation_id:nonce,provider_id:'synthetic-private-provider'},
    fetchImpl:async()=>({ok:false,status:409,json:async()=>{bodyReads++;return {private:'must not read'};}})}),
    error=>error.message==='assistant_ack_unavailable'&&error.status===409);
  assert.equal(bodyReads,0);
});
