import test from 'node:test';
import assert from 'node:assert/strict';
import {makeAssistantTyping} from '../src/assistant-typing.mjs';

const phone='5511999999999',jid=phone+'@s.whatsapp.net';
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const flush=async()=>{await Promise.resolve();await Promise.resolve();await Promise.resolve();};
function fakeClock(){
  let time=0,sequence=0;const pending=new Map();
  return {now:()=>time,schedule(fn,ms){const key={id:++sequence,unref(){}};pending.set(key,{at:time+ms,fn});return key;},
    cancel:key=>pending.delete(key),count:()=>pending.size,async advance(ms){
      const target=time+ms;
      for(;;){const next=[...pending].filter(([,v])=>v.at<=target).sort((a,b)=>a[1].at-b[1].at)[0];
        if(!next)break;pending.delete(next[0]);time=next[1].at;next[1].fn();await flush();}
      time=target;await flush();
    }};
}
function harness(extra={}){
  const clock=fakeClock(),calls=[];let connected=true,allowed=true,current=true;
  const socket={sendPresenceUpdate:async(type,target)=>calls.push({type,target,at:clock.now()})};
  const typing=makeAssistantTyping({socket,pilotPhone:()=>phone,isConnected:()=>connected,isAllowed:()=>allowed,
    now:clock.now,schedule:clock.schedule,cancel:clock.cancel,...extra});
  return {typing,clock,calls,socket,options:{phone,chatJid:jid,isCurrent:()=>current},
    disconnect(){connected=false;},revoke(){allowed=false;},supersede(){current=false;}};
}

test('targeted composing surrounds one reply; short pause is proportional and never exceeds 800ms',async()=>{
  for(const [length,delay] of [[1,150],[40,320],[1200,800]]){
    const h=harness();let sends=0;
    const result=h.typing.run(h.options,async lease=>{await lease.beforeReply('x'.repeat(length));sends++;return 'one result';});
    assert.equal(h.calls[0].type,'composing');await h.clock.advance(delay-1);assert.equal(sends,0);
    await h.clock.advance(1);assert.equal(await result,'one result');assert.equal(sends,1);
    assert.deepEqual(h.calls.map(x=>x.type),['composing','paused']);
    assert.ok(h.calls.every(x=>x.target===jid));assert.equal(h.clock.count(),0);
  }
});

test('inference time is deducted; a long operation gets no additional delay',async()=>{
  const h=harness();let sends=0;
  const work=deferred();
  const result=h.typing.run(h.options,async lease=>{await work.promise;await lease.beforeReply('x'.repeat(1200));sends++;});
  await h.clock.advance(2000);work.resolve();await result;
  assert.equal(sends,1);assert.equal(h.calls.at(-1).at,2000);assert.equal(h.clock.count(),0);
});

test('presence throw, rejection and a forever pending presence never block a reply',async()=>{
  for(const failure of ['throw','reject','hang']){
    const h=harness({maxPauseMs:0});let sends=0;
    h.socket.sendPresenceUpdate=(type,target)=>{
      h.calls.push({type,target});
      if(failure==='throw')throw Error('synthetic presence failure');
      if(failure==='reject')return Promise.reject(Error('synthetic presence failure'));
      return new Promise(()=>{});
    };
    await h.typing.run(h.options,async lease=>{await lease.beforeReply('response');sends++;});
    await flush();assert.equal(sends,1);assert.equal(h.clock.count(),0);
    assert.ok(h.calls.every(x=>x.target===jid&&['composing','paused'].includes(x.type)));
  }
});

test('silent result and thrown preparation both clear presence without producing any message',async()=>{
  for(const fails of [false,true]){
    const h=harness();
    const result=h.typing.run(h.options,async()=>{if(fails)throw Error('synthetic inference error');return null;});
    if(fails)await assert.rejects(result,/synthetic inference/);else assert.equal(await result,null);
    assert.deepEqual(h.calls.map(x=>x.type),['composing','paused']);assert.equal(h.clock.count(),0);
  }
});

test('groups, broadcasts, other contacts, mismatched PN, absent pilot and stale batches emit nothing',async()=>{
  for(const options of [
    {chatJid:'123456789@g.us'},{chatJid:'status@broadcast'},{phone:'5511888888888',chatJid:'5511888888888@s.whatsapp.net'},
    {chatJid:'5511888888888@s.whatsapp.net'},{isCurrent:()=>false}
  ]){
    const h=harness();await h.typing.run({...h.options,...options},async lease=>lease.beforeReply('response'));
    assert.deepEqual(h.calls,[]);assert.equal(h.clock.count(),0);
  }
  const h=harness({pilotPhone:()=>null});await h.typing.run(h.options,async()=>{});assert.deepEqual(h.calls,[]);
});

test('opaque private LID uses only the authenticated PN target; no LID-to-phone derivation',async()=>{
  const h=harness({maxPauseMs:0});await h.typing.run({...h.options,chatJid:'12345678901234567890@lid'},async()=>{});
  assert.deepEqual(h.calls.map(x=>x.target),[jid,jid]);
});

test('revision cancellation interrupts the visual pause and clears the same connected chat',async()=>{
  const h=harness();let sends=0;
  const result=h.typing.run(h.options,async lease=>{await lease.beforeReply('x'.repeat(1200));if(h.options.isCurrent())sends++;});
  h.supersede();await h.clock.advance(100);await result;
  assert.equal(sends,0);assert.deepEqual(h.calls.map(x=>x.type),['composing','paused']);assert.equal(h.clock.count(),0);
});

test('disconnect or recipient revocation stops timers without sending on a stale socket or target',async()=>{
  for(const action of ['disconnect','revoke']){
    const h=harness();const lease=h.typing.begin(h.options);h[action]();await h.clock.advance(100);lease.stop();
    assert.deepEqual(h.calls.map(x=>x.type),['composing']);assert.equal(h.clock.count(),0);
  }
});

test('an old lease or late old presence failure cannot pause a newer lease for the same PN',async()=>{
  const h=harness({maxPauseMs:0}),oldPresence=deferred();let first=true;
  h.socket.sendPresenceUpdate=(type,target)=>{h.calls.push({type,target});
    if(type==='composing'&&first){first=false;return oldPresence.promise;}return Promise.resolve();};
  const old=h.typing.begin(h.options),fresh=h.typing.begin(h.options);
  const count=h.calls.length;old.stop();oldPresence.reject(Error('late old failure'));await flush();
  assert.equal(h.calls.length,count);assert.equal(h.calls.at(-1).type,'composing');
  fresh.stop();assert.equal(h.calls.at(-1).type,'paused');assert.equal(h.clock.count(),0);
});

test('refresh is bounded and the 20-second watchdog stops even if preparation remains pending',async()=>{
  const h=harness(),work=deferred();let sends=0;
  const result=h.typing.run(h.options,async lease=>{await work.promise;await lease.beforeReply('response');sends++;});
  await h.clock.advance(20000);
  assert.equal(h.calls.at(-1).type,'paused');assert.equal(h.clock.count(),0);
  assert.ok(h.calls.filter(x=>x.type==='composing').length<=5);
  const count=h.calls.length;await h.clock.advance(20000);assert.equal(h.calls.length,count);
  work.resolve();await result;assert.equal(sends,1);assert.equal(h.calls.length,count);
});


test('transacional exibe digitando ao remetente paraguaio verificado, sem liberar o piloto de IA',async()=>{
  const h=harness();
  const verified='595992820210',isCurrent=()=>true;
  const transactional=makeAssistantTyping({
    socket:h.socket,pilotPhone:()=>verified,
    isConnected:()=>true,isAllowed:phone=>phone===verified,
    now:h.clock.now,schedule:h.clock.schedule,cancel:h.clock.cancel,
    transactional:true,maxPauseMs:1500
  });
  const pending=transactional.run({
    phone:verified,chatJid:'123456789012345@lid',isCurrent
  },async lease=>{
    await lease.beforeReply('Confirmação autorizada de agendamento '+'.'.repeat(250));
    return 'one confirmed message';
  });
  assert.equal(h.calls[0].type,'composing');
  assert.equal(h.calls[0].target,verified+'@s.whatsapp.net');
  await h.clock.advance(1499);
  assert.equal(h.calls.length,1);
  await h.clock.advance(1);
  assert.equal(await pending,'one confirmed message');
  assert.deepEqual(h.calls.map(c=>c.type),['composing','paused']);
  assert.equal(h.clock.count(),0);
  // The legacy AI default is still restricted to its existing Brazilian pilot.
  await h.typing.run({...h.options,phone:verified,chatJid:verified+'@s.whatsapp.net'},async()=>{});
  assert.equal(h.calls.length,2);
});

test('contatos diferentes podem receber presença transacional sem se interromper',async()=>{
  const h=harness(),other='5511999990022';
  const maker=phone=>makeAssistantTyping({
    socket:h.socket,pilotPhone:()=>phone,
    isConnected:()=>true,isAllowed:value=>value===phone,
    transactional:true,maxPauseMs:1300,
    now:h.clock.now,schedule:h.clock.schedule,cancel:h.clock.cancel
  });
  const a=maker(phone),b=maker(other);
  const resultA=a.run({phone,chatJid:jid,isCurrent:()=>true},
    async lease=>{await lease.beforeReply('x'.repeat(200));return 'A';});
  const resultB=b.run({phone:other,chatJid:other+'@s.whatsapp.net',isCurrent:()=>true},
    async lease=>{await lease.beforeReply('y'.repeat(200));return 'B';});
  assert.deepEqual(h.calls.filter(x=>x.type==='composing').map(x=>x.target).sort(),
    [jid,other+'@s.whatsapp.net'].sort());
  await h.clock.advance(1300);
  assert.deepEqual(await Promise.all([resultA,resultB]),['A','B']);
  assert.deepEqual(h.calls.filter(x=>x.type==='paused').map(x=>x.target).sort(),
    [jid,other+'@s.whatsapp.net'].sort());
  assert.equal(h.clock.count(),0);
});

test('presença transacional não é enviada a grupo, contato não autorizado ou sessão desconectada',async()=>{
  for(const {phone:recipient,chatJid,connected,allow} of [
    {phone:'595992820210',chatJid:'123@g.us',connected:true,allow:true},
    {phone:'595992820210',chatJid:'595992820210@s.whatsapp.net',connected:true,allow:false},
    {phone:'595992820210',chatJid:'595992820210@s.whatsapp.net',connected:false,allow:true},
    {phone:'12025550123',chatJid:'12025550123@s.whatsapp.net',connected:true,allow:true}
  ]){
    const h=harness(),typing=makeAssistantTyping({
      socket:h.socket,pilotPhone:()=>recipient,isConnected:()=>connected,isAllowed:()=>allow,
      transactional:true,now:h.clock.now,schedule:h.clock.schedule,cancel:h.clock.cancel
    });
    await typing.run({phone:recipient,chatJid,isCurrent:()=>true},async()=>{});
    assert.deepEqual(h.calls,[]);assert.equal(h.clock.count(),0);
  }
});
