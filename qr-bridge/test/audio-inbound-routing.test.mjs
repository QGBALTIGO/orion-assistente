import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {extractText,resolveMessagePhone} from '../src/core.mjs';
import {shouldIngestUpsert,timestampMs} from '../src/linked-session-health.mjs';
import {forwardAssistant} from '../src/ai-forward.mjs';
import {allowAssistantRecipient,allowAssistantAudio,pilotNumber} from '../src/ai-pilot.mjs';
import {makeConversationIntake} from '../src/conversation-intake.mjs';
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

function harness({transcribe=async()=> 'Synthetic speech',audioEnabled=true,answer=null,realAudio=false}={}){
  let listener,transcriptions=0,downloads=0;
  const forwarded=[],inbox=[],sent=[],events=[],queues=new Map(),seen=new Set();
  const env={QR_AI_ENABLED:'1',QR_AI_AUDIO_ENABLED:audioEnabled?'1':'0',QR_AI_GROUP_WAIT_MS:'0',
    QR_AI_PILOT_NUMBER:phone,QR_AI_API_URL:'http://orion-assistente.railway.internal/internal/qr/ai/reply'};
  const client={ev:{on(event,callback){assert.equal(event,'messages.upsert');listener=callback;}}};
  const context=vm.createContext({client,socket:client,mine:1,generation:1,status:'connected',
    extractText,resolveMessagePhone,shouldIngestUpsert,timestampMs,makeConversationIntake,
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
    send:async(p,text,options)=>{
      assert.equal(p,phone);assert.equal(options.isCurrent(),true);sent.push(text);return {accepted:true};
    },
    forwardAssistant:args=>forwardAssistant({...args,fetchImpl:async(_url,options)=>{
      forwarded.push(JSON.parse(options.body));return {ok:true,json:async()=>({answer})};
    }}),console:{info(){},warn(){}}
  });
  vm.runInContext(handlerSource,context);
  const message=(id,{text,remote=phone+'@s.whatsapp.net',senderPn,seconds=1,fileLength=20}={})=>({
    key:{id,remoteJid:remote,...(senderPn?{senderPn}:{})},messageTimestamp:Math.floor(Date.now()/1000),
    message:text===undefined?{audioMessage:{fileLength,seconds}}:{conversation:text}
  });
  return {context,env,forwarded,inbox,sent,events,message,
    emit:messages=>listener({type:'notify',messages}),
    get transcriptions(){return transcriptions;},get downloads(){return downloads;}};
}

test('voice transcript marker stays in inbox; AI receives only actual words',async()=>{
  const transcript='Cara, quanto custa o cabelo e a barba?';
  const h=harness({transcribe:async()=>transcript});
  await h.emit([h.message('AUDIO')]);
  assert.equal(h.transcriptions,1);
  assert.equal(h.inbox[0].body,'[Áudio transcrito] '+transcript);
  assert.equal(h.forwarded.length,1);assert.equal(h.forwarded[0].text,transcript);
  assert.equal(h.forwarded[0].phone,phone);assert.equal(h.forwarded[0].message_ids,undefined);
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
