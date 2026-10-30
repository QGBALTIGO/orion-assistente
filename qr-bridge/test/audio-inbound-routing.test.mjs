import {normalizeInbound,updateEvent,callEvent,safetyControl,queueControl} from '../src/inbound-event.mjs';
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import os from 'node:os';
import path from 'node:path';
import {makeContactNames} from '../src/contact-names.mjs';
import {extractText,resolveMessagePhone} from '../src/core.mjs';
import {shouldIngestUpsert,timestampMs} from '../src/linked-session-health.mjs';
import {forwardAssistant,registerAssistantIngress} from '../src/ai-forward.mjs';
import {isBookingProtocolMessage,resolveBookingSenderPhone} from '../src/appointment-confirmation.mjs';
import {allowAssistantRecipient,allowAssistantAudio,pilotNumber} from '../src/ai-pilot.mjs';
import {makeConversationIntake} from '../src/conversation-intake.mjs';
import {makeAssistantTyping} from '../src/assistant-typing.mjs';
import {transcribeAssistantAudio} from '../src/ai-audio.mjs';

// Real inbound handler, intake and private forwarder. Network and speech
// recognition are replaced, so this proves routing rather than ASR accuracy.
const source=fs.readFileSync(new URL('../src/server.mjs',import.meta.url),'utf8');
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

function harness({transcribe=async()=> 'Synthetic speech',audioEnabled=true,answer=null,realAudio=false,legacyController,
  revisionEnabled=false,forwardResponse,sendResponse,presence,typingConfig={maxPauseMs:0},
  centralEnabled=false,centralAnswer='Confirmação de agendamento'}={}){
  let listener,transcriptions=0,downloads=0;
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'orion-inbound-names-'));
  contactDirectories.push(directory);
  const namesStore=makeContactNames({directory,secret:'synthetic-local-contact-key-at-least-32-bytes'});
  namesStore.bindIdentity('5511999990000@s.whatsapp.net');
  let nameChanges=0;
  const forwarded=[],inbox=[],sent=[],events=[],queues=new Map(),seen=new Set(),notices=[],presenceCalls=[],centralCalls=[];
  const env={QR_AI_ENABLED:'1',QR_AI_AUDIO_ENABLED:audioEnabled?'1':'0',QR_AI_GROUP_WAIT_MS:'0',
    QR_AI_PILOT_NUMBER:phone,QR_AI_API_URL:'http://orion-assistente.railway.internal/internal/qr/ai/reply'};
  if(revisionEnabled)env.QR_AI_INTAKE_REVISION_ENABLED='1';
  if(centralEnabled)env.ORION_CENTRAL_CONFIRMATION_REPLY_ENABLED='1';
  const handlers=new Map();const client={ev:{on(event,callback){assert.ok(['messages.upsert','messages.update','messages.delete','call'].includes(event));handlers.set(event,callback);if(event==='messages.upsert')listener=callback;}}};
  if(presence)client.sendPresenceUpdate=(type,jid)=>{
    presenceCalls.push({type,jid});events.push(['typing',type,jid]);return presence(type,jid);
  };
  const context=vm.createContext({client,socket:client,mine:1,generation:1,status:'connected',
    legacyCentral:legacyController||{configured:()=>false},
    namesStore,namesSync:{changed(){nameChanges++;}},
    normalizeInbound,updateEvent,callEvent,safetyControl,queueControl,extractText,resolveMessagePhone,shouldIngestUpsert,timestampMs,makeConversationIntake,
    makeAssistantTyping:options=>makeAssistantTyping({...options,...typingConfig}),
    pilotNumber:()=>pilotNumber(env),transport:{record:(...args)=>events.push(args)},retryMessages:{save(){}},seen,
    connectedStore:{record(){},settings:()=>({chatbot:false,transcription:false})},
    assistantAudioEnabled:()=>audioEnabled,
    allowAssistantRecipient:(p,s)=>allowAssistantRecipient(p,s,env),
    allowAssistantAudio:(p,s)=>allowAssistantAudio(p,s,env),
    transcribeAssistantAudio:async(message,options)=>{
      transcriptions++;
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
    isBookingProtocolMessage,resolveBookingSenderPhone,
    fetchBookingProtocolConfirmation:async(args)=>{centralCalls.push(args);return centralAnswer;},
    sendLegacyRaw:async(p,body,options)=>{
      assert.equal(options.isCurrent(),true);
      sent.push(body);return {accepted:true};
    },
    send:async(p,text,options)=>{
      assert.equal(p,phone);assert.equal(options.isCurrent(),true);
      const result=sendResponse?await sendResponse(text,options):{accepted:true};
      if(result?.accepted)sent.push(text);return result;
    },
    registerAssistantIngress:args=>registerAssistantIngress({...args,fetchImpl:async(_url,options)=>{
      notices.push(JSON.parse(options.body));return {ok:true,json:async()=>({revision:notices.length})};
    }}),
    forwardAssistant:args=>forwardAssistant({...args,fetchImpl:async(_url,options)=>{
      const payload=JSON.parse(options.body);forwarded.push(payload);
      return {ok:true,json:async()=>forwardResponse?forwardResponse(payload,forwarded.length):({answer})};
    }}),console:{info(){},warn(){}}
  });
  vm.runInContext(handlerSource,context);
  const message=(id,{text,remote=phone+'@s.whatsapp.net',senderPn,seconds=1,fileLength=20,pushName,fromMe=false}={})=>({
    key:{id,remoteJid:remote,fromMe,...(senderPn?{senderPn}:{})},pushName,messageTimestamp:Math.floor(Date.now()/1000),
    message:text===undefined?{audioMessage:{fileLength,seconds}}:{conversation:text}
  });
  return {context,env,forwarded,inbox,sent,events,message,notices,presenceCalls,namesStore,centralCalls,
    get nameChanges(){return nameChanges;},
    emit:(messages,type='notify')=>listener({type,messages}),emitEvent:(event,value)=>handlers.get(event)(value),
    get transcriptions(){return transcriptions;},get downloads(){return downloads;}};
}

test('real adopted handler journals before processing and uses only the managed reply path',async()=>{
  const events=[],proofs=[];
  const legacyController={configured:()=>true,resume:async()=>{},allowed:p=>p===phone,
    inbound:async payload=>{events.push(['journal',payload]);const proof={revision:proofs.length+1};proofs.push(proof);return proof;},
    forward:async(payload,options)=>{events.push(['managed',payload,options]);return {accepted:true};}};
  const h=harness({legacyController,answer:'This legacy answer must never be used'});
  await h.emit([h.message('SYNTHETIC-ADOPTED',{text:'Olá'})]);
  assert.deepEqual(events.map(e=>e[0]),['journal','managed']);
  assert.equal(events[1][1].intake_revision,1);assert.equal(events[1][2].noticeProofs[0],proofs[0]);
  assert.equal(h.forwarded.length,0);assert.equal(h.sent.length,0);assert.equal(h.notices.length,0);
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
  assert.deepEqual(h.presenceCalls.map(x=>x.type),['composing','paused']);
  gate.resolve();await Promise.all([first,correction]);
  assert.deepEqual(h.presenceCalls.map(x=>x.type),['composing','paused','composing','paused']);
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

test('protocolo de cliente +595 mostra digitando sem liberar bot IA a contatos novos',async()=>{
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
  assert.deepEqual(statuses.map(s=>s.state),['composing','paused']);
  assert.ok(statuses.every(s=>s.jid===recipient+'@s.whatsapp.net'));
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
