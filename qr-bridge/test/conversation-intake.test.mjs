import test from 'node:test';
import assert from 'node:assert/strict';
import {makeConversationIntake} from '../src/conversation-intake.mjs';
import {transcribeAssistantAudio} from '../src/ai-audio.mjs';

const key='synthetic-tenant:account-generation:private-conversation';
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function deferred(){let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};}
function plain(id,text,extra={}){return {conversationKey:key,messageId:id,prepare:async()=>({text}),...extra};}

test('a correction typed during a slow audio transcript joins it before any conversation execution',async()=>{
  const speech=deferred(),calls=[],intake=makeConversationIntake({settleMs:0,consume:async batch=>calls.push(batch)});
  const first=intake.submit({conversationKey:key,messageId:'AUDIO',prepare:()=>speech.promise});
  await tick();
  const correction=intake.submit(plain('CORRECTION','Pera, é sexta, não quinta.'));
  await tick();assert.equal(calls.length,0);
  speech.resolve({text:'Quero corte quinta-feira.'});
  assert.deepEqual(await Promise.all([first,correction]),[{state:'processed'},{state:'processed'}]);
  assert.equal(calls.length,1);
  assert.deepEqual(calls[0].messages.map(message=>message.id),['AUDIO','CORRECTION']);
  assert.equal(calls[0].text,'Quero corte quinta-feira.\nPera, é sexta, não quinta.');
  assert.equal(intake.pending(),0);
});

test('text, audio and text retain ingress order even when later work would finish faster',async()=>{
  const speech=deferred(),events=[],batches=[];
  const intake=makeConversationIntake({settleMs:0,consume:async batch=>batches.push(batch)});
  const first=intake.submit(plain('TEXT-1','Quero cabelo e barba.'));
  const second=intake.submit({conversationKey:key,messageId:'VOICE-2',prepare:async()=>{
    events.push('audio-start');return speech.promise;
  }});
  const third=intake.submit(plain('TEXT-3','Deixa só o corte.',{prepare:async()=>{
    events.push('correction-ready');return {text:'Deixa só o corte.'};
  }}));
  await tick();assert.deepEqual(events,['audio-start']);assert.equal(batches.length,0);
  speech.resolve({text:'Amanhã às duas, não, melhor às três.'});
  await Promise.all([first,second,third]);
  assert.deepEqual(events,['audio-start','correction-ready']);
  assert.deepEqual(batches[0].messages.map(message=>message.id),['TEXT-1','VOICE-2','TEXT-3']);
  assert.equal(batches.length,1);
});

test('duplicate delivery is claimed before download and remains deduplicated after an uncertain consumer',async()=>{
  const media=deferred();let downloads=0,consumes=0;
  const intake=makeConversationIntake({settleMs:0,consume:async()=>{consumes++;throw Error('possibly committed');}});
  const request={conversationKey:key,messageId:'DUP',prepare:async()=>{downloads++;return media.promise;}};
  const original=intake.submit(request);
  assert.deepEqual(await intake.submit(request),{state:'duplicate'});
  await tick();assert.equal(downloads,1);
  media.resolve({text:'Pode confirmar esse resumo.'});
  assert.deepEqual(await original,{state:'consume_failed'});
  assert.deepEqual(await intake.submit(request),{state:'duplicate'});
  assert.equal(downloads,1);assert.equal(consumes,1);
});

test('durable dedupe and separate authenticated tenant/account keys do not share contents',async()=>{
  const batches=[];let downloads=0;
  const intake=makeConversationIntake({settleMs:0,
    isDuplicate:({conversationKey,messageId})=>conversationKey===key&&messageId==='RESTORED',
    consume:async batch=>batches.push(batch)});
  assert.deepEqual(await intake.submit(plain('RESTORED','private',{prepare:async()=>{downloads++;}})),{state:'duplicate'});
  const otherKey='another-tenant:another-account:private-conversation';
  await Promise.all([intake.submit(plain('SAME-ID','Tenant A')),
    intake.submit(plain('SAME-ID','Tenant B',{conversationKey:otherKey}))]);
  assert.equal(downloads,0);assert.equal(batches.length,2);
  assert.equal(batches.find(batch=>batch.conversationKey===key).text,'Tenant A');
  assert.equal(batches.find(batch=>batch.conversationKey===otherKey).text,'Tenant B');
});

test('a slow conversation does not block another conversation',async()=>{
  const voice=deferred(),finished=[];
  const intake=makeConversationIntake({settleMs:0,consume:async batch=>finished.push(batch.conversationKey)});
  const slow=intake.submit({conversationKey:key,messageId:'SLOW',prepare:()=>voice.promise});
  await intake.submit(plain('FAST','Price question',{conversationKey:'other-authenticated-conversation'}));
  assert.deepEqual(finished,['other-authenticated-conversation']);
  voice.resolve({text:'Synthetic speech'});await slow;
  assert.deepEqual(finished,['other-authenticated-conversation',key]);
});

test('configured debounce groups a burst but cannot extend beyond its maximum window',async()=>{
  let at=0,firstSleep=true;const sleeps=[],batches=[],tasks=[];
  const intake=makeConversationIntake({settleMs:100,maxGroupMs:150,now:()=>at,
    sleep:async duration=>{
      sleeps.push(duration);at+=duration;
      if(firstSleep){firstSleep=false;tasks.push(intake.submit(plain('LATER','Correction')));}
    },consume:async batch=>batches.push(batch)});
  tasks.push(intake.submit(plain('EARLY','Original')));
  await Promise.all(tasks);await tick();
  assert.equal(batches.length,1);assert.deepEqual(batches[0].messages.map(item=>item.id),['EARLY','LATER']);
  assert.equal(at,150);
  assert.ok(sleeps.every(value=>value>0&&value<=1000));
});

test('stale authorization after transcription cancels the complete batch before business logic',async()=>{
  const speech=deferred();let authorized=true,consumes=0;
  const intake=makeConversationIntake({settleMs:0,consume:async()=>{consumes++;}});
  const pending=intake.submit({conversationKey:key,messageId:'STALE',isCurrent:()=>authorized,prepare:()=>speech.promise});
  await tick();authorized=false;speech.resolve({text:'Synthetic old account speech'});
  assert.deepEqual(await pending,{state:'stale'});assert.equal(consumes,0);
});

test('failed or oversized preparation never executes a partial request without its correction',async()=>{
  for(const failed of [true,false]){
    let consumes=0;
    const intake=makeConversationIntake({settleMs:0,maxTextChars:20,consume:async()=>{consumes++;}});
    const one=intake.submit({conversationKey:key,messageId:'FIRST',prepare:async()=>failed?null:{text:'A'.repeat(20)}});
    const two=intake.submit(plain('SECOND','Correction'));
    const result=await Promise.all([one,two]);
    assert.ok(result.every(item=>item.state===(failed?'prepare_failed':'needs_clarification')));
    assert.equal(consumes,0);
  }
});

test('too many adjacent messages require clarification instead of executing an incomplete group',async()=>{
  const speech=deferred();let consumes=0;
  const intake=makeConversationIntake({settleMs:0,maxMessages:2,consume:async()=>{consumes++;}});
  const tasks=[intake.submit({conversationKey:key,messageId:'FIRST',prepare:()=>speech.promise}),
    intake.submit(plain('SECOND','Correction 1')),intake.submit(plain('THIRD','Correction 2'))];
  speech.resolve({text:'Original'});
  assert.ok((await Promise.all(tasks)).every(item=>item.state==='needs_clarification'));
  assert.equal(consumes,0);
});

test('a rejected queued correction invalidates the unexecuted batch rather than silently ignoring it',async()=>{
  const speech=deferred();let consumes=0;
  const intake=makeConversationIntake({settleMs:0,maxMessages:2,maxPending:2,consume:async()=>{consumes++;}});
  const first=intake.submit({conversationKey:key,messageId:'FIRST',prepare:()=>speech.promise});
  const second=intake.submit(plain('SECOND','Correction 1'));
  assert.deepEqual(await intake.submit(plain('THIRD','Correction 2')),{state:'overloaded'});
  speech.resolve({text:'Original'});
  assert.ok((await Promise.all([first,second])).every(item=>item.state==='needs_clarification'));
  assert.equal(consumes,0);assert.equal(intake.pending(),0);
});

test('timed-out transcription is aborted and a late result cannot execute later',async()=>{
  const speech=deferred();let signal,consumes=0;
  const intake=makeConversationIntake({settleMs:0,prepareTimeoutMs:10,consume:async()=>{consumes++;}});
  const pending=intake.submit({conversationKey:key,messageId:'TIMEOUT',prepare:options=>{signal=options.signal;return speech.promise;}});
  assert.deepEqual(await pending,{state:'prepare_failed'});assert.equal(signal.aborted,true);
  speech.resolve({text:'Late speech'});await tick();assert.equal(consumes,0);
});

test('active duplicate claims survive bounded completed-history eviction',async()=>{
  const pending=deferred();let downloads=0;
  const intake=makeConversationIntake({settleMs:0,maxMessages:1,maxPending:1,maxSeen:1,
    consume:async()=>{}});
  const first=intake.submit({conversationKey:key,messageId:'ACTIVE',prepare:()=>{downloads++;return pending.promise;}});
  await tick();
  await intake.submit(plain('OTHER','Other',{conversationKey:'other-conversation'}));
  assert.deepEqual(await intake.submit(plain('ACTIVE','Duplicate')),{state:'duplicate'});
  pending.resolve({text:'Original'});await first;assert.equal(downloads,1);
});

test('generated audio bytes traverse the real transcription proxy in order; ASR network is explicitly mocked',async()=>{
  // Generated RIFF/PCM tone, not a human recording or evidence of speech recognition.
  const wav=Buffer.alloc(44+1600);wav.write('RIFF');wav.writeUInt32LE(wav.length-8,4);
  wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);
  wav.writeUInt32LE(8000,24);wav.writeUInt32LE(16000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);
  wav.write('data',36);wav.writeUInt32LE(1600,40);
  const response=deferred(),batches=[];let downloads=0;
  const intake=makeConversationIntake({settleMs:0,consume:async batch=>batches.push(batch)});
  const audio=intake.submit({conversationKey:key,messageId:'GENERATED_MEDIA',prepare:async()=>({text:
    await transcribeAssistantAudio({message:{audioMessage:{fileLength:wav.length,seconds:1}}},{
      client:{},phone:'5511999999999',token:'x'.repeat(32),logger:{},
      apiUrl:'http://orion-assistente.railway.internal/internal/qr/ai/reply',
      downloadMediaMessage:async()=>{downloads++;return wav;},
      fetchFn:async(_url,request)=>{assert.equal(request.body,wav);assert.equal(wav.subarray(0,4).toString(),'RIFF');
        await response.promise;return {ok:true,json:async()=>({text:'Quinta-feira'})};}
    })})});
  await tick();const correction=intake.submit(plain('MEDIA_CORRECTION','Sexta-feira, não quinta.'));
  response.resolve();await Promise.all([audio,correction]);
  assert.equal(downloads,1);assert.equal(batches.length,1);
  assert.equal(batches[0].text,'Quinta-feira\nSexta-feira, não quinta.');
  assert.ok(wav.every(byte=>byte===0));
});
