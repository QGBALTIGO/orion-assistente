import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {createDeviceNumberCheck,validNumberCheckRequest,NUMBER_CHECK_LIMITS}
  from '../src/device-number-check.mjs';

const d=randomUUID(),g=randomUUID(),one='+5567999999901',two='+595991234567';
const input={device_id:d,generation:g,settings_revision:7,phones:[one,two]};
function setup({reply=async phone=>[{jid:phone+'@s.whatsapp.net',exists:true}],now=()=>1000}={}){
  const calls=[],effect=[],state={alive:true,connected:true,generation:g,revision:7,auth:true};
  const client={
    async onWhatsApp(phone){calls.push(phone);return reply(phone);},
    sendMessage(){effect.push('sendMessage');throw Error('unexpected_send');},
    relayMessage(){effect.push('relayMessage');throw Error('unexpected_relay');},
    requestPairingCode(){effect.push('pair');throw Error('unexpected_pair');},
    logout(){effect.push('logout');throw Error('unexpected_logout');}
  };
  const check=createDeviceNumberCheck({
    client,now,gapMs:0,isCurrent:()=>state.alive,isConnected:()=>state.connected,
    deviceSnapshot:()=>({id:d,generation:state.generation,settings_revision:state.revision})});
  const authorize=async()=>{if(!state.auth)throw Error('revoked');};
  return {check,authorize,calls,effect,state};
}
test('strict request contracts reject duplicates, invalid/ambiguous numbers, context and extra fields',()=>{
  assert.equal(validNumberCheckRequest(input),true);
  for(const candidate of [
    {...input,phones:[]},{...input,phones:[one,one]},
    {...input,phones:['5567999999901']},{...input,phones:[one,'+0123']},
    {...input,phones:[one,two,one,one,one,one]},
    {...input,generation:'not-uuid'},{...input,device_id:'no-device'},
    {...input,other:'forbidden'},{...input,settings_revision:0}
  ]) assert.equal(validNumberCheckRequest(candidate),false);
});
test('registered and unregistered require exact PN evidence; read-only socket',async()=>{
  const f=setup({reply:async phone=>[{jid:phone+'@s.whatsapp.net',exists:phone===one.slice(1)}]});
  const actual=await f.check(input,{authorize:f.authorize});
  assert.deepEqual(actual,{device_id:d,generation:g,settings_revision:7,items:[
    {phone:one,status:'registered'},{phone:two,status:'not_registered'}]});
  assert.deepEqual(f.calls,[one.slice(1),two.slice(1)]);
  assert.deepEqual(f.effect,[]);
});
test('provider errors, LID-only IDs, malformed proof and ambiguous proof remain unknown',async()=>{
  const f=setup({reply:async phone=>{
    if(phone===one.slice(1))return [{jid:'12345@lid',exists:true}];
    throw Error('transient');
  }});
  const result=await f.check(input,{authorize:f.authorize});
  assert.deepEqual(result.items.map(x=>x.status),['unknown','unknown']);
  assert.deepEqual(f.effect,[]);
  const g2=setup({reply:async phone=>[
    {jid:phone+'@s.whatsapp.net',exists:true},
    {jid:phone+'@s.whatsapp.net',exists:false}]});
  const both=await g2.check({...input,phones:[one]},{authorize:g2.authorize});
  assert.equal(both.items[0].status,'unknown');
});
test('requires live authorized connection before and after every provider read',async()=>{
  const f=setup();
  f.state.auth=false;
  await assert.rejects(f.check(input,{authorize:f.authorize}),/number_check_unavailable/);
  assert.deepEqual(f.calls,[]);
  f.state.auth=true;f.state.connected=false;
  await assert.rejects(f.check(input,{authorize:f.authorize}),/number_check_unavailable/);
  assert.deepEqual(f.calls,[]);
  f.state.connected=true;
  const g2=setup({reply:async phone=>{g2.state.generation=randomUUID();return [{jid:phone+'@s.whatsapp.net',exists:true}];}});
  await assert.rejects(g2.check(input,{authorize:g2.authorize}),/number_check_unavailable/);
});
test('no overlap, no automatic retry on provider hang, quota reservations counted before query',async()=>{
  let release;
  const pending=new Promise(resolve=>{release=resolve;});
  const f=setup({reply:()=>pending});
  const first=f.check({...input,phones:[one]},{authorize:f.authorize});
  await assert.rejects(f.check({...input,phones:[one]},{authorize:f.authorize}),/number_check_unavailable/);
  release([{jid:one.slice(1)+'@s.whatsapp.net',exists:true}]);
  assert.equal((await first).items[0].status,'registered');
  assert.deepEqual(f.calls,[one.slice(1)]);
  const quota=setup();
  for(let i=0;i<NUMBER_CHECK_LIMITS.per_hour;i++){
    await quota.check({...input,phones:[one]},{authorize:quota.authorize});
  }
  await assert.rejects(quota.check({...input,phones:[one]},{authorize:quota.authorize}),/number_check_unavailable/);
  assert.equal(quota.calls.length,30);
});
