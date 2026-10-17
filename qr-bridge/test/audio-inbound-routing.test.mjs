import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {extractText,resolveMessagePhone} from '../src/core.mjs';
import {shouldIngestUpsert,timestampMs} from '../src/linked-session-health.mjs';
import {forwardAssistant} from '../src/ai-forward.mjs';
import {allowAssistantRecipient,allowAssistantAudio} from '../src/ai-pilot.mjs';

// Exercise the real inbound event handler and private forwarder without a socket.
const source=fs.readFileSync(new URL('../src/server.mjs',import.meta.url),'utf8');
const start=source.indexOf('  client.ev.on("messages.upsert"');
const end=source.indexOf('  client.ev.on("connection.update"',start);
assert.ok(start>0&&end>start);
const handlerSource=source.slice(start,end);
const phone='5511999999999';

async function route({text,transcript,audioEnabled=true}){
  let listener,transcriptions=0;
  const forwarded=[],inbox=[],queues=new Map(),seen=new Set();
  const env={QR_AI_ENABLED:'1',QR_AI_AUDIO_ENABLED:audioEnabled?'1':'0',
    QR_AI_PILOT_NUMBER:phone,QR_AI_API_URL:'http://orion-assistente.railway.internal/internal/qr/ai/reply'};
  const client={ev:{on(event,callback){assert.equal(event,'messages.upsert');listener=callback;}}};
  const context=vm.createContext({client,socket:client,mine:1,generation:1,status:'connected',
    extractText,resolveMessagePhone,shouldIngestUpsert,timestampMs,
    transport:{record(){}},retryMessages:{save(){}},seen,
    connectedStore:{record(){},settings:()=>({chatbot:false,transcription:false})},
    assistantAudioEnabled:()=>audioEnabled,
    allowAssistantRecipient:(p,s)=>allowAssistantRecipient(p,s,env),
    allowAssistantAudio:(p,s)=>allowAssistantAudio(p,s,env),
    transcribeAssistantAudio:async()=>{transcriptions++;return transcript;},
    transcribeAudio:async()=>null,transcriberConfig:()=>({}),downloadMediaMessage:()=>{},logger:{},
    pushMessage:row=>{inbox.push(row);seen.add(row.id);},lastInboundAt:0,inboundSinceConnect:false,recentAppends:0,
    assistantQueues:queues,KEY:'x'.repeat(32),process:{env},Date,
    forwardAssistant:args=>forwardAssistant({...args,fetchImpl:async(_url,options)=>{
      forwarded.push(JSON.parse(options.body));return {ok:true,json:async()=>({answer:null})};
    }}),console:{info(){},warn(){}}
  });
  vm.runInContext(handlerSource,context);
  await listener({type:'notify',messages:[{key:{id:'SYNTHETIC_AUDIO_ROUTING',remoteJid:phone+'@s.whatsapp.net'},
    messageTimestamp:Math.floor(Date.now()/1000),message:text===undefined?{audioMessage:{fileLength:20}}:{conversation:text}}]});
  await Promise.all([...queues.values()]);
  return {forwarded,inbox,transcriptions};
}

test('voice transcript marker is display metadata; AI receives only the actual words',async()=>{
  const transcript='Cara, quanto custa o cabelo e a barba?';
  const result=await route({transcript});
  assert.equal(result.transcriptions,1);
  assert.equal(result.inbox[0].body,'[Áudio transcrito] '+transcript);
  assert.equal(result.forwarded.length,1);
  assert.equal(result.forwarded[0].text,transcript);
  assert.equal(result.forwarded[0].phone,phone);
});

test('typed text is preserved verbatim and disabled audio never reaches the AI',async()=>{
  const text='[Áudio transcrito] Este marcador foi digitado pelo cliente';
  const typed=await route({text});
  assert.equal(typed.transcriptions,0);assert.equal(typed.forwarded[0].text,text);
  const disabled=await route({transcript:'Synthetic speech',audioEnabled:false});
  assert.equal(disabled.transcriptions,0);
  assert.deepEqual(disabled.forwarded,[]);assert.deepEqual(disabled.inbox,[]);
});
