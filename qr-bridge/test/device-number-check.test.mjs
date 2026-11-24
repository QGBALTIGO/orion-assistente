import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {EventEmitter} from 'node:events';
import {Readable} from 'node:stream';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {DEFAULT_CONNECTION_CONFIG,initAuthCreds,USyncQuery,USyncUser} from '@whiskeysockets/baileys';
import {createDeviceNumberCheck,createNumberCheckLookup,NUMBER_CHECK_LIMITS,validNumberCheckRequest} from '../src/device-number-check.mjs';
import {createChannelManager} from '../src/channel-manager.mjs';
import {createChannelRoutes} from '../src/channel-routes.mjs';
import {createBaileysChannelSession} from '../src/channel-session.mjs';

process.env.ORION_RETRY_TEST_MODE='1';
const PHONE='+5567999999901',OTHER='+5567999999902';
const defaults=JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/subscription_devices_contract.json',import.meta.url),'utf8')).settings_defaults;
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
const scope=device=>({device_id:device.id,generation:device.generation,settings_revision:device.settings_revision});
const snapshot=()=>({id:randomUUID(),generation:randomUUID(),settings_revision:1,settings:structuredClone(defaults),capabilities:{}});
const registered=number=>({jid:number.replace(/^\+/,'')+'@s.whatsapp.net',exists:true});
function temp(t){const root=fs.mkdtempSync(path.join(os.tmpdir(),'orion-number-read-'));fs.chmodSync(root,0o700);t.after(()=>fs.rmSync(root,{recursive:true,force:true}));return root;}
function timers(){let serial=0;const tasks=new Map();return {tasks,schedule(fn,ms){const id=++serial;tasks.set(id,{fn,ms});return id;},cancel:id=>tasks.delete(id),
  fire(ms){const entries=[...tasks].filter(([,task])=>task.ms===ms);assert.ok(entries.length,'Missing timer '+ms);const [id,task]=entries[0];tasks.delete(id);task.fn();}};}
function fixture(options={}){
  let device=snapshot(),alive=true,connected=true,authorizations=0;
  const calls=[],effects=[],behavior={query:async(...numbers)=>numbers.map(registered),authorize:async()=>{}};
  const client={onWhatsApp:async(...numbers)=>{calls.push(numbers);return behavior.query(...numbers);}};
  for(const method of ['sendMessage','relayMessage','sendPresenceUpdate','groupParticipantsUpdate','groupFetchAllParticipating','groupCreate','groupLeave','requestPairingCode'])
    client[method]=()=>{effects.push(method);assert.fail('Read must not call '+method);};
  const check=createDeviceNumberCheck({client,isCurrent:()=>alive,isConnected:()=>connected,deviceSnapshot:()=>device,...options});
  const authorize=async()=>{authorizations++;await behavior.authorize(authorizations);};
  return {check,client,calls,effects,behavior,authorize,get input(){return {...scope(device),numbers:[PHONE,OTHER]};},
    get device(){return device;},get authorizations(){return authorizations;},revoke(){alive=false;},disconnect(){connected=false;},update(patch){device={...device,...patch};}};
}

test('registration query returns only requested numbers, preserves order and performs no outbound effect',async()=>{
  const f=fixture();f.behavior.query=async()=>[{...registered(PHONE),exists:false},{...registered(OTHER),lid:'private@lid',private:'secret metadata'}];
  const result=await f.check(f.input,{authorize:f.authorize});
  assert.deepEqual(result,{...scope(f.device),total:2,results:[
    {number:PHONE,status:'not_registered',reason:null},{number:OTHER,status:'registered',reason:null}]});
  assert.deepEqual(f.calls,[[PHONE.slice(1),OTHER.slice(1)]]);assert.equal(f.effects.length,0);
  assert.equal(JSON.stringify(result).includes('private'),false);
});

test('empty provider array is inconclusive; only explicit false is negative and a verified PN with LID remains positive',async()=>{
  const f=fixture();f.behavior.query=async()=>[];
  assert.ok((await f.check(f.input,{authorize:f.authorize})).results.every(row=>row.status==='inconclusive'));
  f.behavior.query=async()=>[{...registered(PHONE),exists:false},{...registered(OTHER),lid:'1234@lid'}];
  assert.deepEqual((await f.check(f.input,{authorize:f.authorize})).results.map(row=>row.status),['not_registered','registered']);
});

test('partial response preserves verified positives while absent contacts remain inconclusive',async()=>{
  const f=fixture();f.behavior.query=async()=>[registered(PHONE)];
  assert.deepEqual((await f.check(f.input,{authorize:f.authorize})).results,[
    {number:PHONE,status:'registered',reason:null},{number:OTHER,status:'inconclusive',reason:'provider_result_unverified'}]);
});

function usyncResult(rows){return {tag:'iq',attrs:{type:'result'},content:[{tag:'usync',attrs:{},content:[{tag:'list',attrs:{},content:rows.map(([number,type])=>({
  tag:'user',attrs:{jid:number.slice(1)+'@s.whatsapp.net'},content:type===undefined?[]:[{tag:'contact',attrs:type===null?{}:{type}}]}))}]}]};}

test('installed SDK contact parser preserves explicit in/out without using the lossy onWhatsApp projection',async()=>{
  const calls=[],client={onWhatsApp:()=>assert.fail('Do not use filtered results'),executeUSyncQuery:async query=>{
    calls.push(query);return query.parseUSyncQueryResult(usyncResult([[PHONE,'in'],[OTHER,'out']]));
  }};
  const lookupNumbers=createNumberCheckLookup({client,USyncQuery,USyncUser}),f=fixture({lookupNumbers});
  const result=await f.check(f.input,{authorize:f.authorize});
  assert.deepEqual(result.results.map(row=>row.status),['registered','not_registered']);
  assert.deepEqual(calls[0].users.map(user=>user.phone),[PHONE,OTHER]);
  assert.deepEqual(calls[0].protocols.map(protocol=>protocol.name),['contact']);assert.equal(f.calls.length,0);
});

for(const rows of [[],[[PHONE,'in']],[[PHONE,'in'],[OTHER,undefined]],[[PHONE,'in'],[OTHER,null]],[[PHONE,'in'],[OTHER,'retry']]])
test('SDK incomplete/unknown contact result never becomes a negative: '+JSON.stringify(rows),async()=>{
  const client={executeUSyncQuery:async query=>query.parseUSyncQueryResult(usyncResult(rows))};
  const f=fixture({lookupNumbers:createNumberCheckLookup({client,USyncQuery,USyncUser})});
  const result=await f.check(f.input,{authorize:f.authorize});
  assert.ok(result.results.every(row=>row.status!=='not_registered'));
  assert.equal(result.results[1].status,'inconclusive');
});

for(const value of [undefined,null,{},'timeout',[{jid:'12345@lid',exists:true}],
  [{jid:'5567999999903@s.whatsapp.net',exists:true}],[{...registered(PHONE),exists:'true'}],
  [registered(PHONE),registered(PHONE)],[{jid:PHONE,exists:true}]])
  test('unverified provider response cannot create a negative verdict: '+JSON.stringify(value),async()=>{
    const f=fixture();f.behavior.query=async()=>value;
    const result=await f.check(f.input,{authorize:f.authorize});
    assert.ok(result.results.every(row=>row.status==='inconclusive'&&row.reason==='provider_result_unverified'));
  });

test('provider rejection is an error, not a nonexistent number, and never exposes provider content',async()=>{
  const f=fixture();f.behavior.query=async()=>{throw Error('secret credential +123456789 private response');};
  const result=await f.check(f.input,{authorize:f.authorize});
  assert.ok(result.results.every(row=>row.status==='error'&&row.reason==='provider_failed'));
  assert.equal(JSON.stringify(result).includes('secret'),false);assert.equal(f.calls.length,1);
});

test('duplicate inputs keep their positions and result but are only queried once',async()=>{
  const f=fixture(),input={...f.input,numbers:[PHONE,OTHER,PHONE,PHONE,OTHER]};
  const result=await f.check(input,{authorize:f.authorize});
  assert.equal(result.total,5);assert.deepEqual(result.results.map(row=>row.number),input.numbers);
  assert.deepEqual(f.calls,[[PHONE.slice(1),OTHER.slice(1)]]);
});

test('1000 valid numbers are all accepted and checked in serial bounded batches without truncation',async()=>{
  const f=fixture(),numbers=Array.from({length:1000},(_,i)=>'+'+(15550000000+i));
  let active=0,max=0;
  f.behavior.query=async(...values)=>{active++;max=Math.max(max,active);await tick();active--;return values.map(registered);};
  const result=await f.check({...f.input,numbers},{authorize:f.authorize});
  assert.equal(result.total,1000);assert.equal(result.results.length,1000);
  assert.ok(result.results.every(row=>row.status==='registered'));
  assert.deepEqual(result.results.map(row=>row.number),numbers);
  assert.equal(f.calls.length,20);assert.ok(f.calls.every(row=>row.length<=50));assert.equal(max,1);
});

test('strict input rejects extras, unknown device, empty/oversized lists and noncanonical or sparse numbers',async()=>{
  const f=fixture();
  for(const input of [null,[],{...f.input,extra:true},{...f.input,device_id:'bad'},
    {...f.input,generation:'bad'},{...f.input,settings_revision:0},{...f.input,settings_revision:1.5},
    {...f.input,numbers:[]},{...f.input,numbers:Array(1001).fill(PHONE)},{...f.input,numbers:Array(2)},
    ...['5567999999901','+0123456789','+1234567','+1234567890123456',1234567890,' +5567999999901',PHONE+'@s.whatsapp.net'].map(n=>({...f.input,numbers:[n]}))]){
    assert.equal(validNumberCheckRequest(input),false);
    await assert.rejects(f.check(input,{authorize:f.authorize}),e=>e.numberCheckReason==='invalid_request');
  }
  assert.equal(f.calls.length,0);
});

for(const mode of ['stale','disconnected','device','generation','settings'])
  test('refuses '+mode+' scope before a lookup',async()=>{
    const f=fixture(),input=f.input;
    if(mode==='stale')f.revoke();if(mode==='disconnected')f.disconnect();
    if(mode==='device')input.device_id=randomUUID();if(mode==='generation')input.generation=randomUUID();if(mode==='settings')input.settings_revision++;
    await assert.rejects(f.check(input,{authorize:f.authorize}),e=>e.numberCheckReason==='context_changed');assert.equal(f.calls.length,0);
  });

for(const mode of ['credential','stale','disconnected','generation','settings'])
  test('discards provider data when '+mode+' changes while query is outstanding',async()=>{
    const f=fixture(),pending=deferred();f.behavior.query=()=>pending.promise;
    const operation=f.check(f.input,{authorize:f.authorize});await tick();
    if(mode==='credential')f.behavior.authorize=async()=>{throw Error('revoked');};
    if(mode==='stale')f.revoke();if(mode==='disconnected')f.disconnect();
    if(mode==='generation')f.update({generation:randomUUID()});if(mode==='settings')f.update({settings_revision:2});
    pending.resolve([registered(PHONE)]);await assert.rejects(operation,e=>e.numberCheckReason==='context_changed');
  });

test('provider timeout keeps the slot until its query settles and never starts more batches',async()=>{
  const time=timers(),f=fixture({...time,batchSize:1}),pending=deferred();f.behavior.query=()=>pending.promise;
  const operation=f.check(f.input,{authorize:f.authorize});await tick();
  await assert.rejects(f.check(f.input,{authorize:f.authorize}),e=>e.numberCheckReason==='read_busy');
  time.fire(NUMBER_CHECK_LIMITS.batch_timeout_ms);
  const result=await operation;assert.ok(result.results.every(row=>row.status==='inconclusive'&&row.reason==='provider_timeout'));
  await assert.rejects(f.check(f.input,{authorize:f.authorize}),e=>e.numberCheckReason==='read_busy');
  pending.resolve([registered(PHONE)]);await tick();assert.equal(f.calls.length,1);
  f.behavior.query=async(...numbers)=>numbers.map(registered);
  assert.ok((await f.check(f.input,{authorize:f.authorize})).results.every(row=>row.status==='registered'));
});

test('request deadline preserves confirmed results and labels every remaining number inconclusive',async()=>{
  const time=timers(),f=fixture({...time,batchSize:1}),pending=deferred();
  f.behavior.query=async number=>number===PHONE.slice(1)?[registered(PHONE)]:pending.promise;
  const operation=f.check(f.input,{authorize:f.authorize});await tick();assert.equal(f.calls.length,2);
  time.fire(NUMBER_CHECK_LIMITS.timeout_ms);const result=await operation;
  assert.deepEqual(result.results,[{number:PHONE,status:'registered',reason:null},{number:OTHER,status:'inconclusive',reason:'deadline_exceeded'}]);
  await assert.rejects(f.check(f.input,{authorize:f.authorize}),e=>e.numberCheckReason==='read_busy');
  pending.resolve([]);await tick();assert.equal(f.calls.length,2);
});

test('authorization timeout prevents any provider request after a late authorization completes',async()=>{
  const time=timers(),f=fixture(time),pending=deferred();f.behavior.authorize=()=>pending.promise;
  const operation=f.check(f.input,{authorize:f.authorize});await tick();time.fire(NUMBER_CHECK_LIMITS.timeout_ms);
  const result=await operation;assert.ok(result.results.every(row=>row.status==='inconclusive'&&row.reason==='deadline_exceeded'));
  pending.resolve();await tick();assert.equal(f.calls.length,0);
});

test('failure stops further batches while preserving prior confirmed results',async()=>{
  const f=fixture({batchSize:1}),third='+5567999999903';
  f.behavior.query=async number=>{if(number===OTHER.slice(1))throw Error('provider unavailable');return [registered(number)];};
  const result=await f.check({...f.input,numbers:[PHONE,OTHER,third]},{authorize:f.authorize});
  assert.deepEqual(result.results,[{number:PHONE,status:'registered',reason:null},
    {number:OTHER,status:'error',reason:'provider_failed'},{number:third,status:'inconclusive',reason:'provider_failed'}]);
  assert.equal(f.calls.length,2);
});

function managerFixture(t){
  const root=temp(t),id=randomUUID(),tenant=randomUUID(),key='synthetic-channel-key-'.repeat(3);
  let device=snapshot(),revoked=false,fence=1,state='connected';const calls=[],made=[],time=timers(),reads=[];
  const behavior={query:async(...numbers)=>numbers.map(registered)};
  const broker={async lease(channel,credential,operation,owner,expectedFence){
    calls.push({channel,credential,operation,owner,expectedFence});
    if(revoked||channel!==id||credential!==key)throw Object.assign(Error('private rejection'),{status:401});
    return {channel_id:id,tenant_id:tenant,provider:'baileys',owner,fence,expires_in_ms:30000,
      allowed_senders:[],assistant_enabled:false,device:structuredClone(device)};
  }};
  const manager=createChannelManager({broker,root,clock:()=>0,...time,createSession:async context=>{
    const client={onWhatsApp:(...numbers)=>{reads.push(numbers);return behavior.query(...numbers);}};
    const session={context,closed:false,close(){this.closed=true;},status:()=>state,deviceSettingsChanged(){},
      send:()=>assert.fail('No send'),pair:()=>assert.fail('No pair')};
    session.numberCheck=createDeviceNumberCheck({client,isCurrent:()=>!session.closed&&context.isCurrent(),isConnected:()=>state==='connected',deviceSnapshot:context.deviceSnapshot});
    made.push(session);return session;
  }});
  t.after(()=>manager.close());
  return {manager,id,key,tenant,calls,made,behavior,reads,get input(){return {...scope(device),numbers:[PHONE,OTHER]};},
    revoke(){revoked=true;},replaceFence(){fence++;},update(patch){device={...device,...patch};},disconnect(){state='connecting';}};
}

test('manager requires an existing connected exact device, private key and channel without starting a socket',async t=>{
  const f=managerFixture(t);
  await assert.rejects(f.manager.deviceNumberCheck(f.id,f.key,f.input),e=>e.numberCheckReason==='context_changed');
  assert.equal(f.made.length,0);assert.equal(f.calls.length,0);
  await f.manager.start(f.id,f.key);f.calls.length=0;
  for(const [id,key,input] of [[randomUUID(),f.key,f.input],[f.id,'wrong-key-'.repeat(5),f.input],
    [f.id,f.key,{...f.input,device_id:randomUUID()}],[f.id,f.key,{...f.input,generation:randomUUID()}],[f.id,f.key,{...f.input,settings_revision:2}]])
    await assert.rejects(f.manager.deviceNumberCheck(id,key,input),e=>e.numberCheckReason==='context_changed');
  assert.equal(f.reads.length,0);assert.equal(f.calls.length,0);
  const result=await f.manager.deviceNumberCheck(f.id,f.key,f.input);
  assert.equal(result.total,2);assert.equal(f.reads.length,1);assert.equal(f.made.length,1);
  assert.ok(f.calls.every(call=>call.operation==='check'&&call.expectedFence===1&&call.credential===f.key));
});

for(const mode of ['credential','fence','generation','settings','disconnected'])
  test('manager discards a lookup after '+mode+' changes and revalidates SQL on completion',async t=>{
    const f=managerFixture(t);await f.manager.start(f.id,f.key);f.calls.length=0;
    const pending=deferred();f.behavior.query=()=>pending.promise;
    const operation=f.manager.deviceNumberCheck(f.id,f.key,f.input);await tick();
    if(mode==='credential')f.revoke();if(mode==='fence')f.replaceFence();
    if(mode==='generation')f.update({generation:randomUUID()});if(mode==='settings')f.update({settings_revision:2});
    if(mode==='disconnected')f.disconnect();
    pending.resolve([registered(PHONE)]);await assert.rejects(operation,e=>e.numberCheckReason==='context_changed');assert.equal(f.made.length,1);
  });

test('real session exposes the checker only on an open socket and performs no message or presence calls',async t=>{
  const root=temp(t),directory=path.join(root,randomUUID()),deviceDirectory=path.join(root,'device');
  fs.mkdirSync(directory,{mode:0o700});fs.mkdirSync(deviceDirectory,{mode:0o700});
  const device=snapshot(),events=[],time=timers();let alive=true,reads=0;
  const client={ev:new EventEmitter(),user:{id:PHONE.slice(1)+'@s.whatsapp.net'},end(){},
    executeUSyncQuery:async query=>{reads++;return query.parseUSyncQueryResult(usyncResult(query.users.map(user=>[user.phone,'in'])));},
    onWhatsApp:()=>assert.fail('No lossy registration projection'),
    sendMessage:()=>assert.fail('No message'),relayMessage:()=>assert.fail('No relay'),sendPresenceUpdate:()=>assert.fail('No presence')};
  const context={directory,deviceDirectory,secret:'synthetic-device-secret-'.repeat(3),deviceSnapshot:()=>device,
    isCurrent:()=>alive,assertCurrent:async()=>{if(!alive)throw Error('revoked');},notifySelf:false,
    allowedSender:()=>false,assistantEnabled:()=>false,audioEnabled:()=>false,onConnected(){},onDisconnected(){},
    deviceEvent:async row=>{events.push(row);return {effects:[]};},deviceEffectsPending:async()=>({effects:[],reconcile:[]}),
    deviceEffectAuthorize:()=>assert.fail('No outbound grant'),deviceEffectFinish:()=>assert.fail('No effect finish')};
  const mod={DEFAULT_CONNECTION_CONFIG:{version:DEFAULT_CONNECTION_CONFIG.version},default:()=>client,BufferJSON:{},initAuthCreds,USyncQuery,USyncUser,
    Browsers:{windows:()=>['synthetic']},DisconnectReason:{loggedOut:401},proto:{Message:{AppStateSyncKeyData:{fromObject:value=>value}}}};
  const session=await createBaileysChannelSession(context,{loadBaileys:async()=>mod,...time});t.after(()=>session.close());
  const input={...scope(device),numbers:[PHONE,OTHER]};
  await assert.rejects(session.numberCheck(input,{authorize:context.assertCurrent}),e=>e.numberCheckReason==='context_changed');assert.equal(reads,0);
  client.ev.emit('connection.update',{connection:'open'});await tick();
  assert.ok((await session.numberCheck(input,{authorize:context.assertCurrent})).results.every(row=>row.status==='registered'));
  const pending=deferred();client.executeUSyncQuery=()=>pending.promise;
  const result=session.numberCheck(input,{authorize:async()=>{}});await tick();alive=false;
  pending.resolve([]);await assert.rejects(result,e=>e.numberCheckReason==='context_changed');
});

async function request(route,url,input,{method='POST',key='synthetic-key-'.repeat(4),raw}={}){
  const req=Readable.from([Buffer.from(raw??JSON.stringify(input))]);req.url=url;req.method=method;req.headers=key?{'x-orion-channel-key':key}:{};
  const output={};const res={writeHead:(status,headers)=>Object.assign(output,{status,headers}),end:body=>{output.body=JSON.parse(body);}};
  output.handled=await route(req,res);return output;
}

test('private route accepts a complete 1000-number payload with no silent 8KB cutoff',async()=>{
  const device=snapshot(),id=randomUUID(),calls=[],input={...scope(device),numbers:Array.from({length:1000},(_,i)=>'+'+(15550000000+i))};
  assert.ok(Buffer.byteLength(JSON.stringify(input))>8192);
  const route=createChannelRoutes({env:{QR_CHANNELS_ENABLED:'1'},brokerFactory:()=>({}),managerFactory:()=>({
    deviceNumberCheck:async(...args)=>{calls.push(args);return {...scope(device),total:input.numbers.length,results:[]};},
    start:()=>assert.fail('No start'),send:()=>assert.fail('No send')})});
  const result=await request(route,`/internal/channels/${id}/device/number-check/check`,input);
  assert.equal(result.status,200);assert.equal(result.headers['Cache-Control'],'no-store');assert.equal(calls.length,1);
  assert.deepEqual(calls[0][2],input);
});

test('private route enforces auth, method, channel path, fields and bounded request bytes before dispatch',async()=>{
  const device=snapshot(),id=randomUUID(),input={...scope(device),numbers:[PHONE]},url=`/internal/channels/${id}/device/number-check/check`;
  const route=createChannelRoutes({env:{QR_CHANNELS_ENABLED:'1'},managerFactory:()=>assert.fail('Invalid request must not instantiate manager')});
  assert.equal((await request(route,url,input,{method:'GET'})).status,405);
  assert.equal((await request(route,url,input,{key:null})).status,401);
  assert.equal((await request(route,url+'?extra=1',input)).status,404);
  assert.equal((await request(route,url,{...input,extra:1})).body.reason,'invalid_request');
  assert.equal((await request(route,url,{...input,numbers:Array(1001).fill(PHONE)})).body.reason,'invalid_request');
  assert.equal((await request(route,url,{}, {raw:'x'.repeat(NUMBER_CHECK_LIMITS.request_bytes+1)})).status,409);
  const disabled=createChannelRoutes({env:{},managerFactory:()=>assert.fail('No disabled manager')});
  assert.equal((await request(disabled,url,input)).status,423);
});

test('private route returns safe reasons only and never exposes provider messages or phone data',async()=>{
  const device=snapshot(),id=randomUUID(),input={...scope(device),numbers:[PHONE]},url=`/internal/channels/${id}/device/number-check/check`;
  let failure=Error('private token '+PHONE);
  const route=createChannelRoutes({env:{QR_CHANNELS_ENABLED:'1'},brokerFactory:()=>({}),managerFactory:()=>({deviceNumberCheck:async()=>{throw failure;}})});
  assert.deepEqual((await request(route,url,input)).body,{error:'number_check_unavailable'});
  failure=Object.assign(failure,{numberCheckReason:'read_busy'});
  assert.deepEqual((await request(route,url,input)).body,{error:'number_check_unavailable',reason:'read_busy'});
});
