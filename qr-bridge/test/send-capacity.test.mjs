import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {makeSendCapacity} from '../src/send-capacity.mjs';
import {sendStoredText} from '../src/reliable-send.mjs';
import {resolvePilotRecipient,makePilotDeviceCache} from '../src/pilot-recipient.mjs';

const tick=()=>new Promise(resolve=>setImmediate(resolve));
function clock(){
  let at=0;
  const pending=[],durations=[];
  return {
    now:()=>at,
    sleep:ms=>new Promise(resolve=>{durations.push(ms);pending.push(resolve)}),
    advance(ms){at+=ms;pending.splice(0).forEach(resolve=>resolve())},
    durations
  };
}
async function fill(capacity){for(let i=0;i<5;i++)await capacity.acquire()}

test('five shared admissions per minute; manual overflow fails without waiting',async()=>{
  const c=clock(),capacity=makeSendCapacity(c);
  await fill(capacity);
  await assert.rejects(capacity.acquire(),/Aguarde um minuto/);
  assert.deepEqual(c.durations,[]);
  c.advance(59999);
  await assert.rejects(capacity.acquire(),/Aguarde um minuto/);
  c.advance(1);
  await capacity.acquire();
});

test('concurrent manual callers cannot reserve the same available capacity',async()=>{
  const capacity=makeSendCapacity({now:()=>0});
  const outcomes=await Promise.allSettled(Array.from({length:20},()=>capacity.acquire()));
  assert.equal(outcomes.filter(x=>x.status==='fulfilled').length,5);
  assert.equal(outcomes.filter(x=>x.status==='rejected').length,15);
});

test('AI waits until capacity returns while manual traffic retains the same limit',async()=>{
  const c=clock(),capacity=makeSendCapacity(c);
  await fill(capacity);
  let admitted=false;
  const waiting=capacity.acquire({wait:true}).then(()=>{admitted=true});
  await tick();
  assert.equal(admitted,false);
  await assert.rejects(capacity.acquire(),/Aguarde um minuto/);
  c.advance(59999);await tick();
  assert.equal(admitted,false);
  assert.equal(c.durations.at(-1),1);
  c.advance(1);await waiting;
  assert.equal(admitted,true);
  assert.ok(c.durations.every(ms=>ms>0&&ms<=1000));
});

test('competing waiters share atomic rolling windows without bursts above five',async()=>{
  const c=clock(),capacity=makeSendCapacity(c);
  await fill(capacity);
  const admissions=[];
  const tasks=Array.from({length:7},()=>capacity.acquire({wait:true}).then(()=>admissions.push(c.now())));
  c.advance(60000);await tick();
  assert.equal(admissions.length,5);
  assert.deepEqual(admissions,[60000,60000,60000,60000,60000]);
  await assert.rejects(capacity.acquire(),/Aguarde um minuto/);
  c.advance(60000);await Promise.all(tasks);
  assert.deepEqual(admissions,[60000,60000,60000,60000,60000,120000,120000]);
  assert.ok(c.durations.every(ms=>ms<=1000));
});

test('stale socket, generation or allowlist cancels within one bounded sleep',async()=>{
  for(const change of ['socket','generation','allowlist']){
    const c=clock(),capacity=makeSendCapacity(c);
    await fill(capacity);
    const state={socket:true,generation:true,allowlist:true};
    const waiting=capacity.acquire({wait:true,isCurrent:()=>Object.values(state).every(Boolean)});
    // Attach the rejection handler before simulating wakeup.
    const cancelled=assert.rejects(waiting,/conexão ou autorização mudou/);
    assert.deepEqual(c.durations,[1000]);
    state[change]=false;c.advance(1000);await cancelled;
    assert.deepEqual(c.durations,[1000]);
    c.advance(59000);await fill(capacity);
  }
});

test('already stale work cannot consume capacity, even after session reset',async()=>{
  const c=clock(),capacity=makeSendCapacity(c);
  await assert.rejects(capacity.acquire({wait:true,isCurrent:()=>false}),/mudou antes do envio/);
  await fill(capacity);
  let current=true;
  const pending=capacity.acquire({wait:true,isCurrent:()=>current});
  const cancelled=assert.rejects(pending,/mudou antes do envio/);
  current=false;capacity.clear();c.advance(1000);await cancelled;
  await fill(capacity);
  await assert.rejects(capacity.acquire(),/Aguarde um minuto/);
});

test('uncertain relay is attempted once; admission never retries or refunds it',async()=>{
  const c=clock(),capacity=makeSendCapacity(c);
  let relays=0;
  async function sendOnce(){
    await capacity.acquire({wait:true});
    relays++;
    throw Error('uncertain relay');
  }
  await assert.rejects(sendOnce(),/uncertain relay/);
  assert.equal(relays,1);
  for(let i=0;i<4;i++)await capacity.acquire();
  await assert.rejects(capacity.acquire(),/Aguarde um minuto/);
  assert.equal(relays,1);
});

test('cancellation while waiting never reaches transport',async()=>{
  const c=clock(),capacity=makeSendCapacity(c);
  await fill(capacity);
  let current=true,relays=0;
  const job=(async()=>{
    await capacity.acquire({wait:true,isCurrent:()=>current});
    relays++;
  })();
  const cancelled=assert.rejects(job,/mudou antes do envio/);
  current=false;c.advance(1000);await cancelled;
  assert.equal(relays,0);
});

// Execute server.mjs's real send wiring against the real reliable-send and LID
// resolver. Only its clock, message generator and socket/network are replaced.
function serverSendHarness(c){
  const source=fs.readFileSync(new URL('../src/server.mjs',import.meta.url),'utf8');
  const send=source.slice(source.indexOf('async function send('),source.indexOf('\nconst server=http.createServer'));
  assert.ok(send.startsWith('async function send('));
  const phone='5511999990001',calls={lookup:0,devices:0,generate:0,cache:0,relay:0};
  const capacity=makeSendCapacity(c);
  const socket={
    user:{id:'5511999990002:2@s.whatsapp.net',lid:'88002211:2@lid'},
    async onWhatsApp(){calls.lookup++;return [{jid:phone+'@s.whatsapp.net',lid:'99112233@lid',exists:true}]},
    async getUSyncDevices(){calls.devices++;return []},
    async relayMessage(){calls.relay++}
  };
  const deviceCaches=new WeakMap([[socket,makePilotDeviceCache()]]);
  const context={
    legacyCentral:{configured:()=>false},
    socket,generation:1,status:'connected',authorized:true,sendCapacity:capacity,
    validPhone:input=>input,deliveryPolicy:()=>({reliable:true,freshDevices:true}),
    pilotNumber:()=>phone,process:{env:{QR_PILOT_USE_VERIFIED_LID:'1'}},
    resolvePilotRecipient,socketDeviceCaches:deviceCaches,sendStoredText,
    async generateWAMessage(jid,message){calls.generate++;return {key:{id:'synthetic-id',remoteJid:jid},message}},
    generateMessageIDV2:()=> 'synthetic-id',logger:{},
    retryStore:{save(){calls.cache++;return true}},
    retryDiagnostics:{prepared:0,relayAccepted:0},deliveryReceipts:new Map(),deliveryStart:new Map(),
    transport:{record(){},tag:()=> 'synthetic-trace'},pushMessage(){}
  };
  vm.runInNewContext(send+'\nglobalThis.sendUnderTest=send;',context);
  return {context,capacity,calls,phone,send:(options)=>context.sendUnderTest(phone,'synthetic answer',options)};
}

test('real server send holds sixth AI answer before lookup, generation, cache and one relay',async()=>{
  const c=clock(),h=serverSendHarness(c);
  await fill(h.capacity);
  await assert.rejects(h.send(),/Aguarde um minuto/);
  const waiting=h.send({waitForCapacity:true,isCurrent:()=>h.context.authorized});
  await tick();
  assert.deepEqual(h.calls,{lookup:0,devices:0,generate:0,cache:0,relay:0});
  c.advance(60000);
  const result=await waiting;
  assert.equal(result.accepted,true);
  assert.deepEqual(h.calls,{lookup:1,devices:1,generate:1,cache:1,relay:1});
});

test('real server send cancels authorization revoked during LID lookup before cache or relay',async()=>{
  const c=clock(),h=serverSendHarness(c);
  let lookup;
  h.context.socket.onWhatsApp=()=>new Promise(resolve=>{lookup=resolve;h.calls.lookup++});
  const sending=h.send({waitForCapacity:true,isCurrent:()=>h.context.authorized});
  const cancelled=assert.rejects(sending,/Nenhuma mensagem foi enviada/);
  await tick();
  assert.equal(h.calls.lookup,1);
  h.context.authorized=false;
  lookup([{jid:h.phone+'@s.whatsapp.net',lid:'99112233@lid',exists:true}]);
  await cancelled;
  assert.deepEqual(h.calls,{lookup:1,devices:0,generate:0,cache:0,relay:0});
});

test('real server send cancels during generation and never retries an uncertain relay',async()=>{
  const c=clock(),h=serverSendHarness(c);
  h.context.generateWAMessage=async(jid,message)=>{
    h.calls.generate++;h.context.authorized=false;
    return {key:{id:'synthetic-id',remoteJid:jid},message};
  };
  await assert.rejects(h.send({waitForCapacity:true,isCurrent:()=>h.context.authorized}),/Nenhuma mensagem foi enviada/);
  assert.equal(h.calls.cache,0);assert.equal(h.calls.relay,0);
  const uncertain=serverSendHarness(clock());
  uncertain.context.socket.relayMessage=async()=>{uncertain.calls.relay++;throw Error('uncertain relay')};
  await assert.rejects(uncertain.send({waitForCapacity:true,isCurrent:()=>true}),/uncertain relay/);
  assert.equal(uncertain.calls.generate,1);assert.equal(uncertain.calls.cache,1);assert.equal(uncertain.calls.relay,1);
});


test('pico de 100 atendimentos não cria 100 esperas, admite só fila limitada e não bursta',async()=>{
  const c=clock(),capacity=makeSendCapacity({...c,limit:1,maxWaiters:3,maxWaitMs:2000});
  await capacity.acquire();
  const pending=Array.from({length:3},()=>capacity.acquire({wait:true}));
  const results=pending.map(p=>p.then(()=>({ok:true}),error=>({ok:false,error:error.message})));
  assert.equal(capacity.pending(),3);
  const over=await Promise.allSettled(Array.from({length:97},()=>capacity.acquire({wait:true})));
  assert.equal(over.filter(x=>x.status==='rejected').length,97);
  assert.equal(capacity.pending(),3);
  c.advance(2000);await tick();
  const settled=await Promise.all(results);
  assert.ok(settled.every(x=>x.ok===false&&/fila de mensagens/.test(x.error)));
  assert.equal(capacity.pending(),0);
  assert.ok(c.durations.every(x=>x>0&&x<=1000));
  c.advance(58000);await capacity.acquire();
  await assert.rejects(capacity.acquire(),/Aguarde um minuto/);
});

test('envio transacional só simula digitando após admissão e cancela presença ao concluir',async()=>{
  const c=clock(),h=serverSendHarness(c);
  const stages=[];
  h.context.makeAssistantTyping=()=>({begin:()=>({
    beforeReply:async()=>{stages.push('composing');},
    stop:()=>stages.push('paused')
  })});
  const result=await h.send({waitForCapacity:true,transactionalTyping:true,isCurrent:()=>true});
  assert.equal(result.accepted,true);
  assert.deepEqual(stages,['composing','paused']);
  assert.equal(h.calls.relay,1);
});

test('fila lotada não envia presença transacional nem pacote criptografado',async()=>{
  const c=clock(),h=serverSendHarness(c);
  let typed=false;
  h.context.makeAssistantTyping=()=>({begin:()=>{typed=true;return {beforeReply:async()=>{},stop(){}}}});
  await fill(h.capacity);
  await assert.rejects(h.send({transactionalTyping:true}),/Aguarde um minuto/);
  assert.equal(typed,false);
  assert.equal(h.calls.cache,0);
  assert.equal(h.calls.relay,0);
});
