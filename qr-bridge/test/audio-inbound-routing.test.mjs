import {shouldCacheLinkedSelfText} from '../src/retry-capture-policy.mjs';
import {makeMessageRetryStore} from '../src/message-retry-store.mjs';
import {normalizeInbound,updateEvent,callEvent,safetyControl,queueControl} from '../src/inbound-event.mjs';
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import os from 'node:os';
import path from 'node:path';
import {createHmac,randomUUID} from 'node:crypto';
import {makeContactNames} from '../src/contact-names.mjs';
import {extractText,resolveMessagePhone} from '../src/core.mjs';
import {shouldIngestUpsert,timestampMs} from '../src/linked-session-health.mjs';
import {forwardAssistant,registerAssistantIngress,acknowledgeAssistantPresentation} from '../src/ai-forward.mjs';
import {createAssistantPresentationStore,relayAssistantPresentation,ASSISTANT_SELECTION_TEXT,assistantNativeMode} from '../src/assistant-presentation.mjs';
import {isBookingProtocolMessage,resolveBookingSenderPhone} from '../src/appointment-confirmation.mjs';
import {makeBookingIntakeGuard} from '../src/booking-intake-guard.mjs';
import {allowAssistantRecipient,allowAssistantAudio,pilotNumber} from '../src/ai-pilot.mjs';
import {makeConversationIntake} from '../src/conversation-intake.mjs';
import {makeAssistantTyping} from '../src/assistant-typing.mjs';
import {makeOutgoingPresence} from '../src/outgoing-presence.mjs';
import {disconnectEvidence} from '../src/disconnect-evidence.mjs';
import {createNativeTestCanary} from '../src/native-test-canary.mjs';
import {transcribeAssistantAudio} from '../src/ai-audio.mjs';
import {createAssistantIngress} from '../src/assistant-ingress.mjs';
import * as assistantSdk from '@whiskeysockets/baileys';
const {BufferJSON}=assistantSdk;

// Real inbound handler, intake and private forwarder. Network and speech
// recognition are replaced, so this proves routing rather than ASR accuracy.
const source=fs.readFileSync(new URL('../src/server.mjs',import.meta.url),'utf8').replace(/\r\n/g,'\n');
const start=source.indexOf('  const inboundMetadata=new Map();');
const end=source.indexOf('  client.ev.on("connection.update"',start);
assert.ok(start>0&&end>start);
const handlerSource=source.slice(start,end);
const phone='5511999999999';
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
process.env.ORION_CONTACT_TEST_MODE='1';
const contactDirectories=[];
after(()=>{for(const directory of contactDirectories)fs.rmSync(directory,{recursive:true,force:true});});

function harness({transcribe=async()=> 'Synthetic speech',audioEnabled=true,answer=null,realAudio=false,legacyController,directory:reuseDirectory,
  revisionEnabled=false,forwardResponse,sendResponse,presence,typingConfig={maxPauseMs:0},multipleRecipients=false,withConnection=false,
  centralEnabled=false,centralAnswer='Confirmação de agendamento',nativeEnabled=false,nativeSend=async()=>{},interactions=false}={}){
  let listener,transcriptions=0,downloads=0,managedTranscriptions=0,legacyTranscriptions=0;
  const directory=reuseDirectory||fs.mkdtempSync(path.join(os.tmpdir(),'orion-inbound-names-'));
  if(!reuseDirectory)contactDirectories.push(directory);
  const namesStore=makeContactNames({directory,secret:'synthetic-local-contact-key-at-least-32-bytes'});
  namesStore.bindIdentity('5511999990000@s.whatsapp.net');
  let nameChanges=0;
  const forwarded=[],inbox=[],sent=[],sentOptions=[],events=[],queues=new Map(),seen=new Set(),notices=[],presenceCalls=[],centralCalls=[],nativeSends=[],presentationAcks=[],presentationsSent=[];let nativeSerial=0;
  const env={QR_AI_ENABLED:'1',QR_AI_AUDIO_ENABLED:audioEnabled?'1':'0',QR_AI_GROUP_WAIT_MS:'0',
    QR_AI_PILOT_NUMBER:phone,QR_AI_API_URL:'http://orion-assistente.railway.internal/internal/qr/ai/reply'};
  if(revisionEnabled)env.QR_AI_INTAKE_REVISION_ENABLED='1';
  if(multipleRecipients)delete env.QR_AI_PILOT_NUMBER;
  if(centralEnabled)env.ORION_CENTRAL_CONFIRMATION_REPLY_ENABLED='1';
  if(nativeEnabled)Object.assign(env,{QR_NATIVE_TEST_ENABLED:'1',QR_NATIVE_TEST_PHONE:'+'+phone});
  const handlers=new Map();const client={user:{id:'5511999990000@s.whatsapp.net'},ev:{on(event,callback){assert.ok(['messages.upsert','messages.update','messages.delete','call','connection.update'].includes(event));handlers.set(event,callback);if(event==='messages.upsert')listener=callback;}}};
  if(presence)client.sendPresenceUpdate=(type,jid)=>{
    presenceCalls.push({type,jid});events.push(['typing',type,jid]);return presence(type,jid);
  };
  const context=vm.createContext({client,socket:client,mine:1,generation:1,status:'connected',stopping:false,
    legacyCentral:{transportCurrent:()=>true,assertTransportCurrent:async()=>{},nativeTestScope:()=>null,
      ...(legacyController||{configured:()=>false})},socketOutgoingPresence:new WeakMap(),createNativeTestCanary,
    namesStore,namesSync:{changed(){nameChanges++;},start(){}},path,authDir:directory,createAssistantIngress,BufferJSON,createHmac,
    authRegistrationId:12345,createAssistantPresentationStore,ASSISTANT_SELECTION_TEXT,assistantNativeMode,
    acknowledgeAssistantPresentation:args=>acknowledgeAssistantPresentation({...args,fetchImpl:async(_url,request)=>{
      presentationAcks.push(JSON.parse(request.body));return {ok:true,json:async()=>({acknowledged:true})};}}),
    normalizeFromJid:value=>String(value).match(/^(\d+)(?::\d+)?@s\.whatsapp\.net$/)?.[1]||null,
    primaryIngressDiagnostics:null,primaryIngressCanAutomate:null,stopPrimaryIngress(){},
    primaryHealth:{connected(){events.push(['readiness',context.primaryIngressDiagnostics?.(),context.primaryIngressCanAutomate?.()]);}},
    everConnected:false,qr:'',qrAt:0,pairingCode:'',pairAt:0,pairedNumber:'',lastError:null,
    connectedSince:0,pendingNotificationsReceived:false,notifyNewLink:false,
    refreshConnectedProfile:async()=>{},SMOKE_ID:'synthetic-smoke',APPOINTMENT_QA_ID:'synthetic-appointment',
    setTimeout:(fn,ms)=>({fn,ms,unref(){}}),clearTimeout(){},
    normalizeInbound,updateEvent,callEvent,safetyControl,queueControl,extractText,resolveMessagePhone,shouldIngestUpsert,timestampMs,makeConversationIntake,disconnectEvidence,
    makeOutgoingPresence:options=>makeOutgoingPresence({...options,...typingConfig,
      typingFactory:value=>makeAssistantTyping({...value,...typingConfig})}),
    shouldCacheLinkedSelfText,retryStore:makeMessageRetryStore(),retryDiagnostics:{persistedSelfTexts:0,persistFailures:0},
    pilotNumber:()=>pilotNumber(env),transport:{record:(...args)=>events.push(args)},retryMessages:{save(){}},seen,
    connectedStore:{record(){},settings:()=>({chatbot:multipleRecipients,transcription:multipleRecipients})},
    assistantAudioEnabled:()=>audioEnabled,
    allowAssistantRecipient:(p,s)=>allowAssistantRecipient(p,s,env),
    allowAssistantAudio:(p,s)=>allowAssistantAudio(p,s,env),
    transcribeAssistantAudio:async(message,options)=>{
      transcriptions++;
      if(typeof options.transcribe==='function'){
        managedTranscriptions++;
        return transcribeAssistantAudio(message,{...options,
          // Legacy-controller fixtures without ASR replace only that boundary.
          // Explicit controllers exercise the real server callback unchanged.
          transcribe:typeof legacyController?.transcribe==='function'?options.transcribe:()=>transcribe(message,options),
          downloadMediaMessage:async()=>{downloads++;return Buffer.from('synthetic audio bytes');},
          fetchFn:async()=>{throw Error('Adopted audio must not use the global HTTP proxy');}});
      }
      legacyTranscriptions++;
      if(!realAudio)return transcribe(message,options);
      return transcribeAssistantAudio(message,{...options,
        downloadMediaMessage:async()=>{downloads++;return Buffer.from('synthetic audio bytes');},
        fetchFn:async(_url,request)=>{
          assert.equal(request.signal.aborted,false);
          return {ok:true,json:async()=>({text:await transcribe(message,options)})};
        }});
    },downloadMediaMessage:()=>{},logger:{},persistInbox(){},
    pushMessage:row=>{inbox.push(row);seen.add(row.id);},lastInboundAt:0,inboundSinceConnect:false,recentAppends:0,
    assistantQueues:queues,KEY:'x'.repeat(32),process:{env},Date,
    isBookingProtocolMessage,resolveBookingSenderPhone,makeBookingIntakeGuard,
    fetchBookingProtocolConfirmation:async(args)=>{centralCalls.push(args);return centralAnswer;},
    sendLegacyRaw:async(p,body,options)=>{
      assert.equal(options.isCurrent(),true);
      sent.push(body);sentOptions.push({phone:p,transactionalTyping:options.transactionalTyping===true});return {accepted:true};
    },
    sendLegacyProtocol:async(p,action,guards)=>{
      await guards.assertAuthorized();await nativeSend(action,guards);await guards.assertAuthorized();
      const id='native-provider-'+(++nativeSerial);guards.onPrepared({key:{id,remoteJid:p+'@s.whatsapp.net',fromMe:true}});
      nativeSends.push({phone:p,action,id});return {accepted:true,id};
    },
    send:async(p,text,options)=>{
      assert.equal(p,phone);assert.equal(options.isCurrent(),true);
      let result;
      if(sendResponse)result=await sendResponse(text,options);
      else if(interactions&&options.presentation){
        const msg=await relayAssistantPresentation({presentation:options.presentation,sdk:{...assistantSdk,userJid:client.user.id},
          jid:p+'@s.whatsapp.net',phone:p,retryStore:context.retryStore,presentationStore:context.socketOutgoingPresence.get(client).assistantPresentations(),
          isCurrent:options.isCurrent,assertAuthorized:async()=>{},beforeSend:async()=>{},
          acknowledge:payload=>context.acknowledgeAssistantPresentation({apiUrl:env.QR_AI_API_URL,token:context.KEY,payload}),
          relay:async(_,body,opts)=>{presentationsSent.push({body,id:opts.messageId});}});
        result={accepted:true,id:msg.key.id};
      }else result={accepted:true};
      if(result?.accepted)sent.push(text);return result;
    },
    registerAssistantIngress:args=>registerAssistantIngress({...args,fetchImpl:async(_url,options)=>{
      notices.push(JSON.parse(options.body));return {ok:true,json:async()=>({revision:notices.length,...(interactions?{assistant_interactions:1}:{})})};
    }}),
    forwardAssistant:args=>forwardAssistant({...args,fetchImpl:async(_url,options)=>{
      const payload=JSON.parse(options.body);forwarded.push(payload);
      return {ok:true,json:async()=>forwardResponse?forwardResponse(payload,forwarded.length):({answer})};
    }}),console:{info(){},warn(){}}
  });
  vm.runInContext(withConnection?source.slice(start,source.indexOf('\n}\nasync function connect(){',end)):handlerSource,context);
  const message=(id,{text,remote=phone+'@s.whatsapp.net',senderPn,seconds=1,fileLength=20,pushName,fromMe=false}={})=>({
    key:{id,remoteJid:remote,fromMe,...(senderPn?{senderPn}:{})},pushName,messageTimestamp:Math.floor(Date.now()/1000),
    message:text===undefined?{audioMessage:{fileLength,seconds}}:{conversation:text}
  });
  return {context,env,forwarded,inbox,sent,sentOptions,events,message,notices,presenceCalls,namesStore,centralCalls,directory,nativeSends,presentationAcks,presentationsSent,
    recover:()=>context.replayAssistantIngress(),diagnostics:()=>context.primaryIngressDiagnostics?.(),
    get nameChanges(){return nameChanges;},
    emit:(messages,type='notify')=>listener({type,messages}),emitEvent:(event,value)=>handlers.get(event)(value),
    get transcriptions(){return transcriptions;},get downloads(){return downloads;},
    get managedTranscriptions(){return managedTranscriptions;},get legacyTranscriptions(){return legacyTranscriptions;}};
}

const assistantMenu=()=>{const id='D'.repeat(32);return {version:1,id,action:{type:'reply_buttons',text:'Resumo fictício',footer:'',
  buttons:[{id:`oa1_${id}_0`,title:'Confirmar'},{id:`oa1_${id}_1`,title:'Corrigir'}]}};};
async function waitAssistant(predicate){const until=Date.now()+2500;while(Date.now()<until){if(predicate())return;await tick();}assert.ok(predicate(),'Expected assistant boundary');}
function presentationTestMode(t){const before=process.env.ORION_RETRY_TEST_MODE;process.env.ORION_RETRY_TEST_MODE='1';
  t.after(()=>{if(before===undefined)delete process.env.ORION_RETRY_TEST_MODE;else process.env.ORION_RETRY_TEST_MODE=before;});}
test('PRIMARY assistant negotiates one presentation and a quoted numeric response returns only its bound selection',async t=>{
  presentationTestMode(t);
  const h=harness({interactions:true,revisionEnabled:true,forwardResponse:(_payload,index)=>index===1?{answer:'Resumo fictício',presentation:assistantMenu()}:{answer:null}});
  h.emit([h.message('assistant-menu-start',{text:'Olá'})]);await waitAssistant(()=>h.sent.length===1);
  assert.equal(h.presentationsSent.length,1);assert.equal(h.presentationAcks.length,1);
  const provider=h.presentationsSent[0].id,reply=h.message('assistant-quoted-choice',{text:'1'});
  reply.message={extendedTextMessage:{text:'1',contextInfo:{stanzaId:provider}}};
  h.emit([reply]);await waitAssistant(()=>h.forwarded.length===2);
  assert.deepEqual(h.forwarded[0].capabilities,{assistant_interactions:1});
  assert.deepEqual(h.forwarded[1].selection,{id:assistantMenu().action.buttons[0].id,quoted_message_id:provider});
  assert.equal(h.forwarded[1].text,'[Escolha recebida]');assert.equal(h.forwarded[1].envelope,undefined);
  h.emit([reply]);for(let i=0;i<30;i++)await tick();
  assert.equal(h.forwarded.length,2);assert.equal(h.sent.length,1);assert.equal(h.presentationAcks.length,1);
});
test('PRIMARY correction during an ACK wait groups with the click and requests review without a selection',async t=>{
  presentationTestMode(t);
  const h=harness({interactions:true,revisionEnabled:true,forwardResponse:(_payload,index)=>index===1?{answer:'Resumo fictício',presentation:assistantMenu()}:{answer:null}});
  h.context.acknowledgeAssistantPresentation=async()=>{throw Error('synthetic ACK lost');};
  h.emit([h.message('assistant-menu-lost-ack',{text:'Olá'})]);await waitAssistant(()=>h.sent.length===1);
  let release;h.context.acknowledgeAssistantPresentation=async()=>new Promise(resolve=>{release=resolve;});
  const click=h.message('assistant-native-choice',{text:''});click.message={interactiveResponseMessage:{nativeFlowResponseMessage:{name:'quick_reply',
    paramsJson:JSON.stringify({id:assistantMenu().action.buttons[0].id,display_text:'NÃO AUTORIZA POR TEXTO'})},contextInfo:{stanzaId:h.presentationsSent[0].id}}};
  h.emit([click]);await waitAssistant(()=>typeof release==='function');
  h.emit([h.message('assistant-later-correction',{text:'Na verdade, quero corrigir o horário'})]);
  await waitAssistant(()=>h.notices.length===3);release({acknowledged:true});
  await waitAssistant(()=>h.forwarded.length===2);
  assert.equal(h.forwarded[1].message_ids.length,2);assert.equal(h.forwarded[1].selection,undefined);
  assert.equal(h.forwarded[1].require_summary_review,true);assert.match(h.forwarded[1].text,/corrigir o horário/);
  assert.doesNotMatch(h.forwarded[1].text,/NÃO AUTORIZA/);assert.equal(h.sent.length,1);
});

test('real adopted handler journals before processing and uses only the managed reply path',async()=>{
  const events=[],proofs=[];
  const scope={tenant_id:'10000000-0000-4000-8000-000000000001',channel_id:'20000000-0000-4000-8000-000000000001',session_digest:'a'.repeat(64)};
  const legacyController={configured:()=>true,resume:async()=>{},allowed:p=>p===phone,reviewAllowed:p=>p===phone,
    transportCurrent:()=>true,assertTransportCurrent:async()=>{},ingressScope:()=>scope,
    reviewScope:()=>({...scope,owner:'30000000-0000-4000-8000-000000000001',fence:3}),
    inbound:async payload=>{events.push(['journal',payload]);const proof={revision:proofs.length+1};proofs.push(proof);return proof;},
    forward:async(payload,options)=>{events.push(['managed',payload,options]);return {accepted:true};}};
  const h=harness({legacyController,answer:'This legacy answer must never be used'});
  await h.emit([h.message('SYNTHETIC-ADOPTED',{text:'Olá'})]);
  assert.deepEqual(events.map(e=>e[0]),['journal','managed']);
  assert.equal(events[1][1].intake_revision,1);assert.equal(events[1][2].noticeProofs[0],proofs[0]);
  assert.equal(h.forwarded.length,0);assert.equal(h.sent.length,0);assert.equal(h.notices.length,0);
});

const admissionScope={tenant_id:'10000000-0000-4000-8000-000000000001',
  channel_id:'20000000-0000-4000-8000-000000000001',session_digest:'a'.repeat(64)};
function adopted({inbound,forward,allowed=p=>p===phone,scope=()=>admissionScope}={}){
  let revision=0;const notices=[],turns=[];
  const fencedScope=()=>({...scope(),owner:'30000000-0000-4000-8000-000000000001',fence:3});
  return {notices,turns,configured:()=>true,resume:async()=>{},allowed,reviewAllowed:allowed,
    ingressScope:scope,reviewScope:fencedScope,transportCurrent:()=>true,assertTransportCurrent:async()=>{},
    async inbound(payload){notices.push(payload);return inbound?inbound(payload):{revision:++revision};},
    async forward(payload,options){turns.push(payload);return forward?forward(payload,options):{accepted:true};}};
}

function nativeController(options={}){
  const central=adopted(options);return {...central,nativeTestScope:central.reviewScope};
}

function preparationController({grant=true,transcribe=async()=>null,forward}={}){
  let revision=0;const notifications=[];
  const central=adopted({allowed:p=>[phone,'5511999999998'].includes(p),forward,
    inbound:async()=>({revision:++revision,assistant_interactions:1,control_revision:grant?100+revision:null})});
  central.transcribe=transcribe;
  central.preparationFailed=async(payload,options)=>{
    assert.equal(options.isCurrent(),true);notifications.push(payload);return {accepted:true,state:'accepted'};
  };
  return {central,notifications};
}

test('adopted PRIMARY routes audio only through its managed ASR callback without a global request',async()=>{
  let asr=0;const f=preparationController({transcribe:async({buffer,phone:recipient,signal})=>{
    asr++;assert.equal(recipient,phone);assert.ok(Buffer.isBuffer(buffer));assert.equal(signal.aborted,false);return 'Consulta sintética sem reserva';}});
  const h=harness({legacyController:f.central});await h.emit([h.message('managed-audio')]);
  assert.equal(asr,1);assert.equal(h.managedTranscriptions,1);assert.equal(h.legacyTranscriptions,0);
  assert.equal(f.central.turns.length,1);assert.equal(f.central.turns[0].text,'Consulta sintética sem reserva');
  assert.equal(h.diagnostics().entries,0);assert.equal(f.notifications.length,0);
});

test('a known preparation failure explains once and no longer blocks another contact or a fresh text',async()=>{
  const f=preparationController(),h=harness({legacyController:f.central,multipleRecipients:true});
  const audio=h.message('bad-audio');await h.emit([audio]);
  assert.equal(h.transcriptions,1);assert.equal(f.central.turns.length,0);assert.equal(f.notifications.length,1);
  assert.equal(f.notifications[0].control_revision,101);assert.equal(f.notifications[0].message_ids[0],'bad-audio');
  assert.match(f.notifications[0].operation_id,/^[a-f0-9-]{36}$/);
  assert.equal(h.diagnostics().blocked,0);assert.equal(h.diagnostics().retained,1);
  await h.emit([audio]);await h.recover();assert.equal(h.transcriptions,1);assert.equal(f.notifications.length,1);
  await h.emit([h.message('other-text',{remote:'5511999999998@s.whatsapp.net',text:'Consulta sintética B'})]);
  assert.equal(f.central.turns.length,1);
  assert.equal(f.central.turns[0].require_summary_review,undefined);
  await h.emit([h.message('fresh-text',{text:'Consulta sintética A'})]);
  assert.equal(f.central.turns.length,2);assert.equal(f.central.turns[1].require_summary_review,true);
  assert.equal(h.diagnostics().blocked,0);
});

test('no control revision means no notification grant and is never replaced with intake revision',async()=>{
  const f=preparationController({grant:false}),h=harness({legacyController:f.central});
  await h.emit([h.message('no-control-proof')]);
  assert.equal(f.notifications.length,0);assert.equal(h.sent.length,0);assert.equal(f.central.turns.length,0);
  assert.equal(h.diagnostics().retained,1);assert.equal(h.diagnostics().blocked,0);
});

test('after restart a failed audio rejects an old native confirmation even without SQL control revision',async t=>{
  presentationTestMode(t);
  const f=preparationController({grant:false}),first=harness({legacyController:f.central});
  const menu=assistantMenu(),provider='old-native-summary';
  const store=first.context.socketOutgoingPresence.get(first.context.client).assistantPresentations();
  store.prepared(menu,phone,{key:{id:provider,fromMe:true}});store.accepted(phone,provider);
  await first.emit([first.message('failed-before-old-click')]);
  first.context.stopPrimaryIngress();first.context.generation++;
  let acks=0;const next=preparationController({grant:false});next.central.presentationAck=async()=>{acks++;return {acknowledged:true};};
  const restored=harness({legacyController:next.central,directory:first.directory});
  const click=restored.message('old-native-click',{text:''});
  click.message={listResponseMessage:{singleSelectReply:{selectedRowId:menu.action.buttons[0].id},contextInfo:{stanzaId:provider}}};
  await restored.emit([click]);
  assert.equal(acks,0);assert.equal(next.central.turns.length,0);assert.equal(restored.sent.length,0);
  assert.equal(restored.diagnostics().retained,1);assert.equal(restored.diagnostics().blocked,0);
  await restored.emit([restored.message('fresh-sim',{text:'Sim'})]);
  assert.equal(next.central.turns.length,1);assert.equal(next.central.turns[0].require_summary_review,true);
  assert.equal(next.central.turns[0].selection,undefined);
});

test('after a failed audio and restart new native service staff and time choices reach a new summary before one confirmation',async t=>{
  presentationTestMode(t);
  const original=preparationController({grant:false}),first=harness({legacyController:original.central});
  const oldMenu=assistantMenu(),oldProvider='pre-failure-summary';
  const oldStore=first.context.socketOutgoingPresence.get(first.context.client).assistantPresentations();
  oldStore.prepared(oldMenu,phone,{key:{id:oldProvider,fromMe:true}});oldStore.accepted(phone,oldProvider);
  await first.emit([first.message('failed-navigation-start')]);first.context.stopPrimaryIngress();first.context.generation++;
  const f=preparationController({grant:false});let h,stage=0,domainWrites=0,latest;
  f.central.presentationAck=async()=>({acknowledged:true});
  f.central.forward=async(payload,options)=>{
    f.central.turns.push(payload);assert.equal(options.isCurrent(),true);
    if(stage){assert.equal(payload.selection.id,latest.choice);assert.equal(payload.selection.quoted_message_id,latest.provider);}
    if(stage===4){domainWrites++;stage++;return {accepted:true};}
    assert.equal(domainWrites,0);assert.equal(payload.require_summary_review,true);
    const step=['catalog','professional','time','confirm'][stage],nonce='NAV'+String(stage).padStart(29,'0');
    const choice=`oa1_${nonce}_0`,provider='new-native-'+stage;
    const action=stage===3?{type:'reply_buttons',text:'Novo resumo sintético',footer:'',
      buttons:[{id:choice,title:'Confirmar'},{id:`oa1_${nonce}_1`,title:'Alterar'}]}:
      {type:'list',title:'Escolha atual',description:'Opção atual',button_text:'Ver opções',footer:'',
        sections:[{title:'',rows:[{id:choice,title:['Serviço','Profissional','Horário'][stage]}]}]};
    const store=h.context.socketOutgoingPresence.get(h.context.client).assistantPresentations();
    store.prepared({version:1,id:nonce,action},phone,{key:{id:provider,fromMe:true}},
      {...options.presentationProvenance,kind:step==='confirm'?'summary':'navigation'});
    store.accepted(phone,provider);latest={provider,choice,step};stage++;
    return {accepted:true,...(step==='confirm'?{summary_review_delivered:true}:{})};
  };
  h=harness({legacyController:f.central,directory:first.directory});
  const old=h.message('old-confirm-before-new-list',{text:''});old.message={listResponseMessage:{
    singleSelectReply:{selectedRowId:oldMenu.action.buttons[0].id},contextInfo:{stanzaId:oldProvider}}};
  await h.emit([old]);assert.equal(f.central.turns.length,0);assert.equal(domainWrites,0);
  await h.emit([h.message('fresh-list-after-failure',{text:'Lista'})]);assert.equal(stage,1);
  for(let index=0;index<4;index++){
    const click=h.message('new-choice-'+index,{text:''});
    click.message={interactiveResponseMessage:{nativeFlowResponseMessage:{name:'quick_reply',
      paramsJson:JSON.stringify({id:latest.choice,display_text:'Not business authority'})},contextInfo:{stanzaId:latest.provider}}};
    await h.emit([click]);assert.equal(domainWrites,index===3?1:0);
    if(index===3){await h.emit([click]);assert.equal(domainWrites,1);}
  }
  assert.equal(stage,5);assert.equal(h.diagnostics().blocked,0);assert.equal(h.diagnostics().retained,1);
  assert.equal(f.central.turns.at(-1).require_summary_review,undefined);
});

test('uncertain ASR notification is not retried and never becomes another assistant turn',async()=>{
  const f=preparationController();let notices=0;
  f.central.preparationFailed=async()=>{notices++;throw Error('synthetic notification result lost');};
  const h=harness({legacyController:f.central}),audio=h.message('unknown-notice');
  await h.emit([audio]);await h.emit([audio]);await h.recover();
  assert.equal(notices,1);assert.equal(h.transcriptions,1);assert.equal(f.central.turns.length,0);
  assert.equal(h.diagnostics().retained,1);assert.equal(h.diagnostics().blocked,0);
});

test('PRIMARY operator retires attested originals after provider logout without ASR sends or business and fences a stale actor',async t=>{
  presentationTestMode(t);
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'orion-inbound-names-'));contactDirectories.push(directory);
  const central=nativeController(),key='x'.repeat(32),at=Date.now()-10000;
  const original=id=>({key:{id,remoteJid:phone+'@s.whatsapp.net',fromMe:false},messageTimestamp:Math.floor(at/1000),message:{audioMessage:{seconds:4,fileLength:1000}}});
  const queue=createAssistantIngress({directory:path.join(directory,'assistant-ingress-encrypted'),secret:key,scope:admissionScope,
    serialize:value=>JSON.stringify(value,BufferJSON.replacer),deserialize:value=>JSON.parse(value,BufferJSON.reviver)});
  queue.append(original('operator-failed'),'notify',at);queue.begin(['operator-failed']);queue.review('operator-failed');
  queue.append(original('operator-pending'),'notify',at);queue.abandon();
  const h=harness({legacyController:central,directory});await h.emit([original('operator-failed')]);
  const oldMenu=assistantMenu(),oldProvider='operator-old-summary';
  const oldStore=h.context.socketOutgoingPresence.get(h.context.client).assistantPresentations();
  oldStore.prepared(oldMenu,phone,{key:{id:oldProvider,fromMe:true}});oldStore.accepted(phone,oldProvider);
  const authority={actor_id:'40000000-0000-4000-8000-000000000001',authority:'platform_owner_proxy'};
  const dry=await h.context.primaryIngressReview({version:1,mode:'dry_run',operation_id:randomUUID(),traces:[]},authority);
  assert.equal(dry.entries.length,2);assert.equal(dry.scope.owner,'30000000-0000-4000-8000-000000000001');assert.equal(dry.scope.fence,3);
  assert.match(dry.scope.auth_epoch,/^[a-f0-9]{64}$/);assert.notEqual(dry.scope.auth_epoch,'12345');
  h.context.status='logged_out';
  const body={version:1,mode:'apply',operation_id:randomUUID(),expected:{scope:dry.scope,snapshot_sha256:dry.snapshot_sha256},
    entries:dry.entries.map(entry=>({trace:entry.trace,fingerprint:entry.fingerprint,disposition:'do_not_replay',evidence:{
      kind:entry.eligible_as[0],receipt_sha256:'d'.repeat(64),build_sha:'e'.repeat(40),event_at:at+1000}}))};
  const result=await h.context.primaryIngressReview(body,authority);assert.equal(result.mutation,true);
  assert.equal(h.transcriptions,0);assert.equal(h.sent.length,0);assert.equal(central.turns.length,0);assert.equal(central.notices.length,0);
  assert.equal(h.diagnostics().retained,2);assert.equal(h.diagnostics().blocked,0);assert.equal(h.diagnostics().pending,0);
  const replay=await h.context.primaryIngressReview(body,authority);assert.equal(replay.replayed,true);
  h.context.generation++;
  await assert.rejects(h.context.primaryIngressReview(body,authority),{code:'ingress_review_not_ready'});
  assert.equal(h.transcriptions,0);assert.equal(central.turns.length,0);assert.equal(h.sent.length,0);
  let revision=0;const restoredCentral=nativeController({inbound:async()=>({revision:++revision,assistant_interactions:1})}),
    restored=harness({legacyController:restoredCentral,directory});
  const click=restored.message('operator-old-confirm',{text:''});click.message={listResponseMessage:{
    singleSelectReply:{selectedRowId:oldMenu.action.buttons[0].id},contextInfo:{stanzaId:oldProvider}}};
  await restored.emit([click]);assert.equal(restoredCentral.turns.length,0);assert.equal(restored.transcriptions,0);
  const absent=restoredCentral.inbound;restoredCentral.inbound=async()=>({revision:++revision});
  await restored.emit([{...click,key:{...click.key,id:'operator-old-confirm-no-cap'}}]);
  assert.equal(restoredCentral.turns.length,0);assert.equal(restored.transcriptions,0);restoredCentral.inbound=absent;
  await restored.emit([restored.message('operator-fresh-text',{text:'Pode conferir um novo resumo?'})]);
  assert.equal(restoredCentral.turns.length,1);assert.equal(restoredCentral.turns[0].require_summary_review,true);
  assert.equal(restoredCentral.turns[0].selection,undefined);assert.equal(restored.diagnostics().retained,2);
});
test('PRIMARY selection diagnostics require exact native scope and ACK failure never reaches business or sends',async t=>{
  presentationTestMode(t);
  for(const optedIn of [false,true]){
    let acks=0;
    const central=adopted({inbound:async()=>({revision:1,assistant_interactions:1})});
    central.presentationAck=async()=>{acks++;throw Object.assign(Error('private provider detail'),{status:403});};
    const h=harness({legacyController:central}),menu=assistantMenu(),id='synthetic-primary-native-summary';
    h.env.QR_ASSISTANT_NATIVE_SCOPES=`${admissionScope.tenant_id}:${admissionScope.channel_id}:${optedIn?phone:'5511888888888'}`;
    const store=h.context.socketOutgoingPresence.get(h.context.client).assistantPresentations();
    store.prepared(menu,phone,{key:{id,fromMe:true}});store.accepted(phone,id);
    const choice=h.message('synthetic-primary-native-click',{text:'not forwarded'});
    choice.message={listResponseMessage:{singleSelectReply:{selectedRowId:menu.action.buttons[0].id},contextInfo:{stanzaId:id}}};
    await h.emit([choice]);assert.equal(acks,2);assert.equal(central.turns.length,0);assert.equal(h.sent.length,0);
    const categories=h.events.filter(event=>event[0]==='assistant_selection').map(event=>event[2].category);
    if(optedIn){assert.ok(categories.includes('matched'));assert.equal(categories.filter(value=>value==='ack_http_403').length,2);
      assert.equal(categories.at(-1),'ack_unconfirmed');}
    else assert.deepEqual(categories,[]);
  }
});
test('PRIMARY canary commands and malformed tokens are intercepted before AI and never change ordinaryORIONTESTE',async()=>{
  const central=nativeController(),h=harness({legacyController:central,nativeEnabled:true});
  await h.emit([h.message('native-list',{text:'ORIONTESTE LISTA'})]);assert.equal(h.nativeSends.length,1);assert.equal(h.nativeSends[0].action.type,'list');
  const menu=h.nativeSends[0],choice=h.message('native-choice',{text:'placeholder'});
  choice.message={listResponseMessage:{singleSelectReply:{selectedRowId:menu.action.sections[0].rows[0].id},contextInfo:{stanzaId:menu.id}}};
  await h.emit([choice]);assert.equal(h.nativeSends.length,2);assert.match(h.nativeSends[1].action.text,/Nenhum agendamento foi criado/);
  h.env.QR_NATIVE_TEST_ENABLED='0';await h.emit([h.message('disabled-namespace',{text:'orion:test:untrusted'}),h.message('invalid-namespace',{text:'ORIONTESTE LISTA extra'})]);
  assert.equal(h.nativeSends.length,2);assert.equal(central.turns.length,0);assert.equal(h.forwarded.length,0);assert.equal(h.notices.length,0);assert.equal(h.transcriptions,0);
});

test('a canary task never blocks another contact in its upsert batch; busy veto affects only the canary',async()=>{
  const other='5511888888888',central=nativeController({allowed:value=>[phone,other].includes(value)}),gate=deferred();
  const h=harness({legacyController:central,nativeEnabled:true,multipleRecipients:true,nativeSend:async()=>gate.promise});
  const task=h.emit([h.message('canary-with-other',{text:'ORIONTESTE BOTAO'}),h.message('other-customer',{text:'Olá',remote:other+'@s.whatsapp.net'})]);
  await tick();await tick();assert.equal(central.turns.length,1);assert.equal(central.turns[0].phone,other);
  assert.equal(h.nativeSends.length,0);gate.resolve();await task;
});

test('two pilot commands in one upsert batch observe the latest input before the first can relay',async()=>{
  const central=nativeController(),h=harness({legacyController:central,nativeEnabled:true});
  await h.emit([h.message('superseded-list',{text:'ORIONTESTE LISTA'}),h.message('current-button',{text:'ORIONTESTE BOTAO'})]);
  assert.equal(h.nativeSends.length,1);assert.equal(h.nativeSends[0].action.type,'url_button');assert.equal(central.turns.length,0);
});

test('an in-flight real AI turn vetoes only the canary without changing AI/backend queues or producing a parallel answer',async()=>{
  const gate=deferred(),central=nativeController({forward:async()=>{await gate.promise;return {accepted:true};}}),h=harness({legacyController:central,nativeEnabled:true});
  const ordinary=h.emit([h.message('ordinary-first',{text:'Qual o horário?'})]);await tick();assert.equal(central.turns.length,1);
  await h.emit([h.message('busy-canary',{text:'ORIONTESTE LISTA'})]);assert.equal(h.nativeSends.length,0);assert.equal(central.turns.length,1);
  gate.resolve();await ordinary;await h.emit([h.message('fresh-canary-after-idle',{text:'ORIONTESTE LISTA'})]);assert.equal(h.nativeSends.length,1);
});

test('a retained503 control/admission vetoes only the canary instead of bypassing the primary durable inbox',async()=>{
  const central=nativeController({inbound:async()=>{throw Error('synthetic503');}}),h=harness({legacyController:central,nativeEnabled:true});
  await h.emit([h.message('retained-before-test',{text:'parar mensagens'})]);assert.equal(h.diagnostics().pending,1);
  await h.emit([h.message('canary-cannot-pass-control',{text:'ORIONTESTE BOTAO'})]);
  assert.equal(h.nativeSends.length,0);assert.equal(central.turns.length,0);assert.equal(h.diagnostics().pending,1);
});

test('legacy notice503 is encrypted before async admission and recovers the original ID/body/time once',async()=>{
  let unavailable=true;const central=adopted({inbound:async()=>{assert.equal(h.diagnostics().pending,1);
    if(unavailable)throw Error('503');return {revision:1};}}),h=harness({legacyController:central});
  const raw=h.message('NOTICE-503',{text:'SYNTHETIC ORIGINAL PRIVATE TEXT'}),at=Date.now()-30000;
  raw.messageTimestamp=Math.floor(at/1000);
  await h.emit([raw]);assert.equal(h.diagnostics().pending,1);assert.equal(h.diagnostics().blocked,0);
  assert.equal(central.turns.length,0);assert.equal(h.inbox.length,0);assert.equal(h.transcriptions,0);
  const encrypted=fs.readdirSync(h.directory,{recursive:true}).filter(name=>name.endsWith('.enc'));
  assert.ok(encrypted.length);for(const file of encrypted)
    assert.equal(fs.readFileSync(path.join(h.directory,file)).includes(Buffer.from('SYNTHETIC ORIGINAL PRIVATE TEXT')),false);
  await h.emit([{...raw,message:{conversation:'FORGED DUPLICATE'},messageTimestamp:Math.floor(Date.now()/1000)}]);
  assert.equal(central.notices.length,1);
  unavailable=false;await h.recover();assert.equal(central.turns.length,1);
  assert.equal(central.turns[0].message_id,'NOTICE-503');assert.equal(central.turns[0].text,'SYNTHETIC ORIGINAL PRIVATE TEXT');
  assert.equal(h.inbox[0].at,Math.floor(at/1000)*1000);assert.equal(h.diagnostics().entries,0);
  await h.emit([raw]);assert.equal(central.turns.length,1);
});

test('legacy process restart replays only fresh pre-prepare audio with original BufferJSON payload',async()=>{
  const unavailable=adopted({inbound:async()=>{throw Error('503');}}),first=harness({legacyController:unavailable});
  const raw=first.message('RESTART-AUDIO');raw.message.audioMessage.fileSha256=Buffer.from([1,5,9]);
  const at=Number(raw.messageTimestamp)*1000;await first.emit([raw]);assert.equal(first.transcriptions,0);
  first.context.stopPrimaryIngress();first.context.generation++;
  const central=adopted(),restored=harness({legacyController:central,directory:first.directory,
    transcribe:async message=>{assert.deepEqual(message.message.audioMessage.fileSha256,Buffer.from([1,5,9]));return 'fala original';}});
  await restored.recover();assert.equal(restored.transcriptions,1);assert.equal(central.turns.length,1);
  assert.equal(restored.inbox[0].at,at);assert.equal(restored.diagnostics().entries,0);
});

for(const outcome of [undefined,null,{}, {accepted:false},{state:'superseded_after_write',not_committed:false}])
  test(`legacy unknown business outcome ${JSON.stringify(outcome)} retains review and never replays`,async()=>{
    const central=adopted({forward:async()=>outcome}),h=harness({legacyController:central});
    const raw=h.message('UNCERTAIN',{text:'Pode confirmar'});await h.emit([raw]);
    assert.equal(h.diagnostics().blocked,1);assert.equal(h.diagnostics().active,0);assert.equal(central.turns.length,1);
    await h.recover();await h.emit([raw]);assert.equal(central.turns.length,1);assert.equal(h.diagnostics().blocked,1);
    await h.emit([h.message('SAFETY-CONTROL',{text:'Não responda mais'})]);
    assert.equal(central.notices.length,2);assert.equal(central.turns.length,1);assert.equal(h.sent.length,0);
  });

for(const outcome of [{state:'committed_no_reply',not_committed:false},{state:'policy_skipped',not_committed:true}])
  test(`legacy explicit terminal ${outcome.state} completes the current durable input`,async()=>{
    const central=adopted({forward:async()=>outcome}),h=harness({legacyController:central});
    await h.emit([h.message('KNOWN-NO-REPLY',{text:'Olá'})]);assert.equal(h.diagnostics().entries,0);
    assert.equal(h.sent.length,0);assert.equal(central.turns.length,1);
  });

test('legacy crash after preparation keeps encrypted input as review in the replacement process',async()=>{
  const inference=deferred(),oldCentral=adopted({forward:async()=>{await inference.promise;return {accepted:true};}});
  const old=harness({legacyController:oldCentral}),running=old.emit([old.message('STARTED',{text:'Pode confirmar'})]);
  await tick();assert.equal(old.diagnostics().active,1);
  old.context.stopPrimaryIngress();old.context.generation++;
  const central=adopted(),replacement=harness({legacyController:central,directory:old.directory});
  await replacement.recover();assert.equal(replacement.diagnostics().blocked,1);assert.equal(central.turns.length,0);
  await replacement.emit([replacement.message('NEW-AFTER-CRASH',{text:'Outra mensagem'})]);
  assert.equal(central.notices.length,1);assert.equal(central.turns.length,0);
  inference.resolve();await running;assert.equal(replacement.diagnostics().blocked,1);
});

test('legacy open synchronously loads existing review before health publication or automated readiness',async()=>{
  const old=harness({legacyController:adopted({forward:async()=>undefined})});
  await old.emit([old.message('REVIEW-ON-OPEN',{text:'Pode confirmar'})]);old.context.stopPrimaryIngress();old.context.generation++;
  const central=adopted(),replacement=harness({legacyController:central,directory:old.directory,withConnection:true});
  replacement.emitEvent('connection.update',{connection:'open'});
  const publication=replacement.events.find(event=>event[0]==='readiness');
  assert.equal(publication[1].blocked,1);assert.equal(publication[2],false);
  assert.equal(replacement.context.primaryIngressCanAutomate(),false);assert.equal(central.turns.length,0);
});

test('legacy healthy messages from two allowed conversations both execute without false review',async()=>{
  const other='5511999999998',central=adopted({allowed:p=>[phone,other].includes(p)});
  const h=harness({legacyController:central,multipleRecipients:true});
  await h.emit([h.message('CONTACT-A',{text:'Primeiro'}),h.message('CONTACT-B',{text:'Segundo',remote:other+'@s.whatsapp.net'})]);
  assert.equal(central.turns.length,2);assert.equal(h.diagnostics().entries,0);assert.equal(h.diagnostics().blocked,0);
});

test('legacy healthy sibling waits before inference until another contact503 notice recovers',async()=>{
  const other='5511999999998';let unavailable=true,revision=0;
  const central=adopted({allowed:p=>[phone,other].includes(p),inbound:async payload=>{
    if(payload.phone===other&&unavailable)throw Error('503');return {revision:++revision};}});
  const h=harness({legacyController:central,multipleRecipients:true});
  const running=h.emit([h.message('HEALTHY-WAIT',{text:'Primeiro'}),h.message('SIBLING-503',{text:'Segundo',remote:other+'@s.whatsapp.net'})]);
  await tick();assert.equal(central.turns.length,0);assert.equal(h.diagnostics().pending,1);assert.equal(h.diagnostics().blocked,0);
  unavailable=false;await h.recover();await running;
  assert.equal(central.turns.length,2);assert.equal(h.diagnostics().entries,0);assert.equal(h.diagnostics().blocked,0);
});

test('legacy a failed pending control prevents a delayed old reply and is not silently cleared',async()=>{
  const inference=deferred();let attempted=0;
  const central=adopted({inbound:async payload=>{if(payload.message_id==='STOP')throw Error('503');return {revision:1};},
    forward:async(_payload,options)=>{await inference.promise;if(options.isCurrent())attempted++;return {accepted:attempted===1};}});
  const h=harness({legacyController:central}),running=h.emit([h.message('OLD-TURN',{text:'SIM'})]);
  await tick();const stopping=h.emit([h.message('STOP',{text:'Não responda mais'})]);
  await tick();inference.resolve();await Promise.all([running,stopping]);
  assert.equal(attempted,0);assert.equal(h.diagnostics().pending,1);assert.equal(h.diagnostics().blocked,1);
  assert.equal(h.sent.length,0);
});

test('legacy unauthorized PN is excluded before journaling, unresolved LID remains retryable before prepare',async()=>{
  const central=adopted(),h=harness({legacyController:central});
  await h.emit([h.message('DENIED-PN',{text:'private',remote:'5511999999998@s.whatsapp.net'})]);
  assert.equal(h.diagnostics().entries,0);assert.equal(central.notices.length,0);
  const raw=h.message('OPAQUE-LID',{text:'Olá',remote:'123456789012345678@lid'});
  await h.emit([raw]);assert.equal(h.diagnostics().pending,1);assert.equal(h.transcriptions,0);
  h.context.client.signalRepository={lidMapping:{getPNForLID:async()=>phone+'@s.whatsapp.net'}};
  await h.recover();assert.equal(central.turns.length,1);assert.equal(h.diagnostics().entries,0);
});

test('legacy expired input is retained for review and a new timestamp on the same ID cannot revive it',async()=>{
  const central=adopted(),h=harness({legacyController:central}),raw=h.message('EXPIRED',{text:'Antigo'});
  raw.messageTimestamp=Math.floor((Date.now()-16*60000)/1000);await h.emit([raw]);
  assert.equal(h.diagnostics().blocked,1);assert.equal(central.notices.length,0);
  raw.messageTimestamp=Math.floor(Date.now()/1000);await h.emit([raw]);await h.recover();
  assert.equal(h.diagnostics().blocked,1);assert.equal(central.turns.length,0);
});

test('real inbound handler forwards a known WhatsApp profile without deriving a name from message text',async()=>{
  const h=harness();
  await h.emit([h.message('CONTACT-NAME',{text:'O texto não é meu nome',pushName:'Joana Exemplo'})]);
  assert.equal(h.namesStore.lookup(phone).display_name,'Joana Exemplo');
  assert.deepEqual(h.forwarded[0].profile,{name:'Joana Exemplo',source:'push_name'});
  assert.equal(h.nameChanges,1);
  await h.emit([h.message('NO-PROFILE',{text:'Agora sou um texto diferente'})]);
  assert.deepEqual(h.forwarded[1].profile,{name:'Joana Exemplo',source:'push_name'});
  assert.equal(h.nameChanges,1);
});

test('real local signal-repository PN resolution associates an otherwise opaque LID with its received profile',async()=>{
  const h=harness();let resolutions=0;
  const lid='123456789012345678@lid';
  h.context.client.signalRepository={lidMapping:{getPNForLID:async value=>{
    resolutions++;assert.equal(value,lid);return phone+'@s.whatsapp.net';
  }}};
  const message=h.message('MAPPED-NAME',{remote:lid,text:'Olá',pushName:'Cliente Exemplo'});
  h.namesStore.observeMessages([message]);assert.equal(h.namesStore.lookup(phone),null);
  await h.emit([message]);
  assert.equal(resolutions,1);assert.equal(h.namesStore.lookup(phone).display_name,'Cliente Exemplo');
  assert.deepEqual(h.forwarded[0].profile,{name:'Cliente Exemplo',source:'push_name'});
  assert.equal(h.inbox[0].phone,phone);
});

test('a PN mapping awaited by an old generation cannot add a name to the replacement account registry',async()=>{
  const h=harness(),gate=deferred();
  h.context.client.signalRepository={lidMapping:{getPNForLID:()=>gate.promise}};
  const running=h.emit([h.message('LATE-NAME',{remote:'123456789012345678@lid',text:'Olá',pushName:'Nome Antigo'})]);
  await tick();h.context.generation++;
  h.namesStore.bindIdentity('5511999990008@s.whatsapp.net');
  gate.resolve(phone+'@s.whatsapp.net');await running;
  assert.deepEqual(h.namesStore.entries(),[]);assert.equal(h.nameChanges,0);
  assert.deepEqual(h.forwarded,[]);assert.deepEqual(h.inbox,[]);
});

test('outgoing pushName never names the recipient, and an old append cannot replace a current pushName',async()=>{
  const h=harness();
  await h.emit([h.message('FROM-ME-NAME',{text:'Enviada',pushName:'Nosso Nome',fromMe:true})]);
  assert.equal(h.namesStore.lookup(phone),null);assert.deepEqual(h.forwarded,[]);
  await h.emit([h.message('CURRENT-NAME',{text:'Olá',pushName:'Nome Atual'})]);
  await h.emit([h.message('OLD-NAME',{text:'Mensagem Pendente',pushName:'Nome Antigo'})],'append');
  assert.equal(h.namesStore.lookup(phone).display_name,'Nome Atual');
  assert.equal(h.forwarded.at(-1).profile.name,'Nome Atual');
});

test('voice transcript marker stays in inbox; AI receives only actual words',async()=>{
  const transcript='Cara, quanto custa o cabelo e a barba?';
  const h=harness({transcribe:async()=>transcript});
  await h.emit([h.message('AUDIO')]);
  assert.equal(h.transcriptions,1);
  assert.equal(h.inbox[0].body,'[Áudio transcrito] '+transcript);
  assert.equal(h.forwarded.length,1);assert.equal(h.forwarded[0].text,transcript);
  assert.equal(h.forwarded[0].phone,phone);assert.equal(h.forwarded[0].message_ids,undefined);
});

test('real handler registers correction during inference and carries it once after explicit non-commit',async()=>{
  const gate=deferred();
  const h=harness({revisionEnabled:true,transcribe:async()=> 'Quero corte quinta às 15h',
    forwardResponse:async(_payload,number)=>{
      if(number===1){await gate.promise;return {answer:null,step:'superseded',not_committed:true};}
      return {answer:'Resumo corrigido para sexta, ainda não confirmado.'};
    }});
  const first=h.emit([h.message('VOICE')]);await tick();
  assert.equal(h.forwarded.length,1);assert.equal(h.notices.length,1);
  const second=h.emit([h.message('LATE',{text:'Não quinta, melhor sexta.'})]);await tick();
  assert.equal(h.notices.length,2);assert.equal(h.forwarded.length,1);
  gate.resolve();await Promise.all([first,second]);
  assert.equal(h.transcriptions,1);assert.equal(h.forwarded.length,2);
  assert.equal(h.forwarded[1].text,'Quero corte quinta às 15h\nNão quinta, melhor sexta.');
  assert.deepEqual(h.forwarded[1].message_ids,['VOICE','LATE']);assert.equal(h.forwarded[1].intake_revision,2);
  assert.equal(h.forwarded[1].require_summary_review,true);
  assert.deepEqual(h.sent,['Resumo corrigido para sexta, ainda não confirmado.']);
  await h.emit([h.message('AFTER',{text:'Pode confirmar'})]);
  assert.equal(h.forwarded[2].require_summary_review,false);
});

test('suppressed summary requires review on next turn, with no replay after a known write',async()=>{
  const gate=deferred();
  const h=harness({revisionEnabled:true,forwardResponse:async(_payload,number)=>{
    if(number===1){await gate.promise;return {answer:'Resumo que não foi enviado'};}
    return {answer:'Preciso reapresentar o resumo antes de confirmar.'};
  }});
  const first=h.emit([h.message('OLD-SUMMARY',{text:'Quero corte'})]);await tick();
  const second=h.emit([h.message('EARLY-SIM',{text:'SIM'})]);await tick();
  gate.resolve();await Promise.all([first,second]);
  assert.deepEqual(h.forwarded.map(x=>x.text),['Quero corte','SIM']);
  assert.equal(h.forwarded[1].require_summary_review,true);
  assert.deepEqual(h.sent,['Preciso reapresentar o resumo antes de confirmar.']);
});

test('unknown forward and unaccepted relay keep review sticky and never repeat the request',async()=>{
  for(const failForward of [true,false]){
    let sends=0;
    const h=harness({revisionEnabled:true,forwardResponse:async(_body,number)=>{
      if(number===1&&failForward)throw Error('unknown response');return {answer:'Resumo'};
    },sendResponse:async()=>({accepted:++sends>1})});
    await h.emit([h.message('UNCERTAIN',{text:'Quero corte'})]);
    await h.emit([h.message('NEW-TURN',{text:'SIM'})]);
    assert.deepEqual(h.forwarded.map(x=>x.message_id),['UNCERTAIN','NEW-TURN']);
    assert.equal(h.forwarded[1].require_summary_review,true);
  }
});

test('typed marker is preserved and disabled audio never reaches ASR or AI',async()=>{
  const text='[Áudio transcrito] Este marcador foi digitado pelo cliente';
  const typed=harness();await typed.emit([typed.message('TEXT',{text})]);
  assert.equal(typed.transcriptions,0);assert.equal(typed.forwarded[0].text,text);
  const disabled=harness({audioEnabled:false});await disabled.emit([disabled.message('DISABLED')]);
  assert.equal(disabled.transcriptions,0);assert.deepEqual(disabled.forwarded,[]);assert.deepEqual(disabled.sent,[]);
});

test('text correction received during ASR joins one ordered interpretation and one response',async()=>{
  const gate=deferred(),h=harness({transcribe:()=>gate.promise,answer:'Entendido: só barba.'});
  const first=h.emit([h.message('AUDIO-FIRST')]);await tick();
  const second=h.emit([h.message('TEXT-CORRECTION',{text:'Corrigindo: só a barba, não quero cabelo.'})]);
  assert.equal(h.forwarded.length,0);
  gate.resolve('Quero cabelo e barba amanhã.');await Promise.all([first,second]);
  assert.equal(h.forwarded.length,1);
  assert.equal(h.forwarded[0].message_id,'AUDIO-FIRST');
  assert.deepEqual(h.forwarded[0].message_ids,['AUDIO-FIRST','TEXT-CORRECTION']);
  assert.equal(h.forwarded[0].text,'Quero cabelo e barba amanhã.\nCorrigindo: só a barba, não quero cabelo.');
  assert.deepEqual(h.sent,['Entendido: só barba.']);assert.equal(h.inbox.length,2);
});

test('verified PN alias and opaque LID ingress preserve order before async resolution',async()=>{
  const gate=deferred(),h=harness({transcribe:()=>gate.promise});
  const first=h.emit([h.message('LID-AUDIO',{remote:'123456789012345@lid',senderPn:phone+'@s.whatsapp.net'})]);
  await tick();const second=h.emit([h.message('PN-CORRECTION',{text:'Não, apenas barba.'})]);
  gate.resolve('Quero cortar o cabelo.');await Promise.all([first,second]);
  assert.equal(h.forwarded.length,1);
  assert.deepEqual(h.forwarded[0].message_ids,['LID-AUDIO','PN-CORRECTION']);
  assert.equal(h.forwarded[0].text,'Quero cortar o cabelo.\nNão, apenas barba.');
});

test('duplicate upserts are claimed before real audio proxy downloads',async()=>{
  const gate=deferred(),h=harness({transcribe:()=>gate.promise,realAudio:true});
  const message=h.message('SAME-AUDIO');const first=h.emit([message]);await tick();
  await h.emit([message]);assert.equal(h.downloads,1);assert.equal(h.transcriptions,1);
  gate.resolve('Quero barba.');await first;await h.emit([message]);
  assert.equal(h.downloads,1);assert.equal(h.forwarded.length,1);
});

test('unauthorized PN and unresolved LID do not download, transcribe, forward or reply',async()=>{
  const h=harness({realAudio:true});
  await h.emit([h.message('OTHER',{remote:'5511888888888@s.whatsapp.net'}),
    h.message('UNKNOWN-LID',{remote:'123456789012345@lid'})]);
  assert.equal(h.downloads,0);assert.equal(h.transcriptions,0);
  assert.deepEqual(h.forwarded,[]);assert.deepEqual(h.sent,[]);
});

test('session replacement or authorization revocation during ASR prevents AI and sends',async()=>{
  for(const revoke of [h=>{h.context.generation=2;},h=>{h.env.QR_AI_ENABLED='0';}]){
    const gate=deferred(),h=harness({transcribe:()=>gate.promise,answer:'Do not send'});
    const task=h.emit([h.message('STALE-AUDIO')]);await tick();revoke(h);
    gate.resolve('Quero barba.');await task;
    assert.deepEqual(h.forwarded,[]);assert.deepEqual(h.sent,[]);
  }
});

test('ASR failure and excessive combined text each produce one clarification with no partial operation',async()=>{
  const failed=harness({transcribe:async()=>null});
  await failed.emit([failed.message('FAIL-A'),failed.message('FAIL-B')]);
  assert.equal(failed.sent.length,1);assert.deepEqual(failed.forwarded,[]);
  const long=harness();await long.emit([long.message('LONG-A',{text:'a'.repeat(800)}),
    long.message('LONG-B',{text:'b'.repeat(800)})]);
  assert.equal(long.sent.length,1);assert.deepEqual(long.forwarded,[]);
});

test('audio above duration or byte limit fails before download and explains once',async()=>{
  for(const options of [{seconds:121},{fileLength:2_000_001}]){
    const h=harness({realAudio:true});await h.emit([h.message('TOO-LARGE',options)]);
    assert.equal(h.downloads,0);assert.deepEqual(h.forwarded,[]);assert.equal(h.sent.length,1);
  }
});

test('real authorized handler starts targeted typing before one API call and pauses after one reply',async()=>{
  const h=harness({answer:'Resumo sintético para revisão.',presence:async()=>{}});
  await h.emit([h.message('TYPING-REPLY',{text:'Quero um horário'})]);
  assert.deepEqual(h.presenceCalls.map(x=>x.type),['composing','paused']);
  assert.ok(h.presenceCalls.every(x=>x.jid===phone+'@s.whatsapp.net'));
  assert.equal(h.forwarded.length,1);assert.deepEqual(h.sent,['Resumo sintético para revisão.']);
  assert.ok(h.events.findIndex(x=>x[0]==='typing'&&x[1]==='composing')<h.events.findIndex(x=>x[0]==='ai_forward'));
  assert.ok(h.events.findIndex(x=>x[0]==='typing'&&x[1]==='paused')>h.events.findIndex(x=>x[0]==='ai_relay_returned'));
});

test('real handler ignores presence failures or a pending presence and sends exactly once',async()=>{
  for(const presence of [()=>{throw Error('presence failed');},()=>Promise.reject(Error('presence failed')),
    ()=>new Promise(()=>{})]){
    const h=harness({answer:'Uma resposta.',presence});
    await h.emit([h.message('PRESENCE-FAIL',{text:'Mensagem sintética'})]);
    assert.equal(h.forwarded.length,1);assert.deepEqual(h.sent,['Uma resposta.']);
    assert.ok(h.presenceCalls.every(x=>x.jid===phone+'@s.whatsapp.net'));
  }
});

test('real API silence or forward failure pauses typing with zero message attempts or retries',async()=>{
  for(const fails of [false,true]){
    const h=harness({presence:async()=>{},forwardResponse:()=>{if(fails)throw Error('synthetic timeout');return {answer:null,step:'human'};}});
    await h.emit([h.message('SILENT-OR-ERROR',{text:'Mensagem sintética'})]);
    assert.equal(h.forwarded.length,1);assert.deepEqual(h.sent,[]);
    assert.deepEqual(h.presenceCalls.map(x=>x.type),['composing','paused']);
  }
});

test('real stale socket or revoked pilot after inference never sends a response or presence on the replacement',async()=>{
  for(const revoke of [h=>{h.context.generation=2;},h=>{h.context.status='disconnected';},h=>{h.env.QR_AI_ENABLED='0';}]){
    const gate=deferred(),h=harness({presence:async()=>{},forwardResponse:async()=>{await gate.promise;return {answer:'Não enviar'};}});
    const task=h.emit([h.message('BECOMES-STALE',{text:'Mensagem sintética'})]);await tick();
    assert.deepEqual(h.presenceCalls.map(x=>x.type),['composing']);revoke(h);gate.resolve();await task;
    assert.deepEqual(h.sent,[]);assert.equal(h.forwarded.length,1);
    assert.deepEqual(h.presenceCalls.map(x=>x.type),['composing']);
  }
});

test('real groups, unrelated contacts, disabled AI and stale audio never start typing',async()=>{
  const h=harness({answer:'Não enviar',presence:async()=>{}});
  await h.emit([h.message('GROUP',{text:'Oi',remote:'5511999999999@g.us'}),
    h.message('OTHER-CONTACT',{text:'Oi',remote:'5511888888888@s.whatsapp.net'}),
    h.message('UNRESOLVED',{text:'Oi',remote:'123456789012345@lid'})]);
  h.env.QR_AI_ENABLED='0';await h.emit([h.message('AI-DISABLED',{text:'Oi'})]);
  assert.deepEqual(h.presenceCalls,[]);assert.deepEqual(h.forwarded,[]);assert.deepEqual(h.sent,[]);
  const gate=deferred(),audio=harness({presence:async()=>{},transcribe:()=>gate.promise});
  const pending=audio.emit([audio.message('STALE-ASR')]);await tick();audio.context.generation=2;gate.resolve('Oi');await pending;
  assert.deepEqual(audio.presenceCalls,[]);assert.deepEqual(audio.forwarded,[]);
});

test('real revised batch stops the old typing lease then shows only the single corrected reply',async()=>{
  let time=0,id=0;const timers=new Map(),gate=deferred();
  const schedule=(fn,ms)=>{const key=++id;timers.set(key,{fn,at:time+ms});return key;};
  const h=harness({revisionEnabled:true,presence:async()=>{},
    typingConfig:{maxPauseMs:0,now:()=>time,schedule,cancel:key=>timers.delete(key)},
    forwardResponse:async(_body,number)=>{
      if(number===1){await gate.promise;return {answer:null,step:'superseded',not_committed:true};}
      return {answer:'Resumo final corrigido.'};
    }});
  const first=h.emit([h.message('OLD-TYPING',{text:'Quinta às três'})]);await tick();
  const correction=h.emit([h.message('NEW-TYPING',{text:'Não quinta, sexta'})]);await tick();
  time=100;for(const [key,timer] of [...timers])if(timer.at<=time){timers.delete(key);timer.fn();}
  assert.deepEqual(h.presenceCalls.map(x=>x.type),['composing']);
  gate.resolve();await Promise.all([first,correction]);
  assert.deepEqual(h.presenceCalls.map(x=>x.type),['composing','composing','paused']);
  assert.deepEqual(h.sent,['Resumo final corrigido.']);assert.equal(h.forwarded.length,2);
  assert.deepEqual(h.forwarded[1].message_ids,['OLD-TYPING','NEW-TYPING']);assert.equal(timers.size,0);
});


test('central QR confirms a newly arriving unknown contact without enabling chatbot',async()=>{
  const h=harness({centralEnabled:true,centralAnswer:'Seu horário está confirmado. 📅'});
  const newPhone='5511888888888';
  const payload=h.message('NEW-BOOKING-1',{
    text:'*Olá, Orion!* 👋\n\n🧾 Protocolo: *AG-3SVM6N*',
    remote:newPhone+'@s.whatsapp.net'
  });
  await h.emit([payload]);
  assert.equal(h.centralCalls.length,1);
  assert.equal(h.centralCalls[0].phone,newPhone);
  assert.equal(h.centralCalls[0].messageId,'NEW-BOOKING-1');
  assert.deepEqual(h.sent,['Seu horário está confirmado. 📅']);
  assert.equal(h.inbox.length,1);
  assert.equal(h.forwarded.length,0,'AI remains restricted');
  assert.equal(h.notices.length,0,'no AI ingress for public confirmation');
  await h.emit([payload]);
  assert.equal(h.centralCalls.length,1,'same WA id is deduplicated');
  await h.emit([h.message('NEW-QUESTION-1',{
    text:'Bom dia, quais serviços vocês fazem?',
    remote:newPhone+'@s.whatsapp.net'
  })]);
  assert.equal(h.centralCalls.length,1,'not an appointment protocol');
  assert.equal(h.forwarded.length,0,'unknown contact cannot activate AI');
});

test('new WhatsApp LID gets a bounded PN mapping retry, then confirmation without AI',async()=>{
  const h=harness({centralEnabled:true,centralAnswer:'Confirmado com segurança'});
  const lid='123456789012345678@lid';
  let calls=0;
  h.context.client.signalRepository={lidMapping:{getPNForLID:async value=>{
    assert.equal(value,lid);calls++;
    return calls===1?null:'5511888888888@s.whatsapp.net';
  }}};
  await h.emit([h.message('LID-NEW-CLIENT',{text:'Protocolo: AG-3SVM6N',remote:lid})]);
  assert.equal(calls,2);
  assert.equal(h.centralCalls.length,1);
  assert.equal(h.centralCalls[0].phone,'5511888888888');
  assert.deepEqual(h.sent,['Confirmado com segurança']);
  assert.equal(h.forwarded.length,0,'chatbot still requires allowlist');
  assert.ok(h.events.some(event=>event[0]==='booking_protocol_identity_verified'));
});

test('unresolvable WhatsApp LID cannot be guessed or get customer information',async()=>{
  const h=harness({centralEnabled:true});
  const lid='123456789012345679@lid';
  h.context.client.signalRepository={lidMapping:{getPNForLID:async()=>null}};
  await h.emit([h.message('LID-MISSING-PN',{text:'Protocolo: AG-3SVM6N',remote:lid})]);
  assert.deepEqual(h.centralCalls,[]);
  assert.deepEqual(h.sent,[]);
  assert.ok(h.events.some(event=>event[0]==='booking_protocol_identity_unresolved'));
});

test('protocolo de cliente +595 solicita digitando ao transporte depois da fila sem liberar bot IA',async()=>{
  const recipient='595992820210',statuses=[];
  const h=harness({
    centralEnabled:true,
    presence:(state,jid)=>{statuses.push({state,jid});return Promise.resolve();},
    typingConfig:{maxPauseMs:0}
  });
  await h.emit([h.message('PY-TRANSACTIONAL-1',{
    remote:recipient+'@s.whatsapp.net',text:'Olá, Orion! 🧾 Protocolo: AG-GUHRPR'
  })]);
  assert.equal(h.centralCalls.length,1);
  assert.equal(h.centralCalls[0].phone,recipient);
  assert.equal(h.sent.length,1);
  assert.deepEqual(statuses,[],'a presença ocorre após a admissão no transporte, não no handler');
  assert.deepEqual(h.sentOptions,[{phone:recipient,transactionalTyping:true}]);
  assert.equal(h.forwarded.length,0,'chatbot IA continua restrito');
});

test('código inválido não gera presença nem resposta automática ao contato novo',async()=>{
  const recipient='595992820210',statuses=[];
  const h=harness({centralEnabled:true,presence:(type,jid)=>{statuses.push({type,jid});}});
  await h.emit([h.message('PY-INVALID-1',{
    remote:recipient+'@s.whatsapp.net',text:'Oi, quanto custa?'
  })]);
  assert.deepEqual(statuses,[]);
  assert.equal(h.centralCalls.length,0);
  assert.equal(h.sent.length,0);
  assert.equal(h.forwarded.length,0);
});

test('um número não pode consultar o banco sem limite mesmo com vários protocolos',async()=>{
 const h=harness({centralEnabled:true,centralAnswer:'Confirmação sintética'});
 const entries=Array.from({length:20},(_,i)=>h.message('BOOKING-SPAM-'+i,{
   text:'Olá Orion! Protocolo: AG-3SVM6N'
 }));
 await h.emit(entries);
 assert.equal(h.centralCalls.length,8,'limite local por PN');
 assert.equal(h.sent.length,8);
 assert.equal(h.events.filter(e=>e[0]==='booking_protocol_rate_limited').length,12);
 assert.equal(h.events.some(e=>e[0]==='ai_forward'),false);
 assert.equal(h.forwarded.length,0,'não habilitar IA ao público');
});

test('contatos diferentes recebem consultas independentes e nenhuma mensagem de grupo',async()=>{
 const h=harness({centralEnabled:true});
 const all=[
   h.message('SAFE-BR-1',{remote:'5511999998888@s.whatsapp.net',
     text:'Protocolo: AG-3SVM6N'}),
   h.message('SAFE-PY-1',{remote:'595992820210@s.whatsapp.net',
     text:'Protocolo: AG-3SVM6N'}),
   h.message('GROUP-INVALID',{remote:'1234567890@g.us',
     text:'Protocolo: AG-3SVM6N'})
 ];
 await h.emit(all);
 assert.deepEqual(h.centralCalls.map(c=>c.phone).sort(),['5511999998888','595992820210']);
 assert.equal(h.sent.length,2);
 assert.equal(h.forwarded.length,0);
});
