import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {makeContactNames,CONTACT_NAME_LIMIT} from '../src/contact-names.mjs';
import {forwardContactNames,makeContactNameSync} from '../src/contact-name-forward.mjs';

process.env.ORION_CONTACT_TEST_MODE='1';
const secret='synthetic-private-metadata-key-for-local-tests-only';
const base='http://orion-assistente.railway.internal:8080/internal/qr/ai/reply';
const own='5511999990000',a='5511999990001',b='5511999990002';
const pn=phone=>phone+'@s.whatsapp.net';
const row=(phone=a,name='Joana Exemplo')=>({phone,name,source:'contact_name'});
const ack=()=>({ok:true,json:async()=>({ok:true,updated:0})});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};
function fixture(t,{fetchImpl=async()=>ack(),storeOptions={}}={}){
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'orion-contact-forward-'));
  const params={directory,secret,...storeOptions};
  const store=makeContactNames(params);store.bindIdentity(pn(own));
  let live=true,next=0;
  const timers=new Map(),calls=[];
  const sync=makeContactNameSync({store,apiUrl:base,token:secret,isCurrent:()=>live,
    fetchImpl:async(url,options)=>{calls.push({url,...options});return fetchImpl(url,options);},
    setTimer:(fn,delay)=>{const id=++next;timers.set(id,{fn,delay});return id;},
    clearTimer:id=>timers.delete(id)});
  t.after(()=>{sync.stop();fs.rmSync(directory,{recursive:true,force:true});});
  return {store,params,sync,timers,calls,setLive:value=>{live=value;},
    async advance(){
      assert.ok(timers.size>0,'expected a scheduled metadata batch');
      const [id,{fn,delay}]=timers.entries().next().value;timers.delete(id);fn();await tick();return delay;
    }};
}

test('private metadata forwarder sends only PN/name/source/account and requires an explicit acknowledgment',async()=>{
  let requests=0;
  assert.equal(await forwardContactNames({apiUrl:base,token:secret,accountPhone:own,items:[row()],
    fetchImpl:async(url,options)=>{
      requests++;assert.equal(url,'http://orion-assistente.railway.internal:8080/internal/qr/contact-names');
      assert.equal(options.headers['X-Orion-Bridge-Key'],secret);
      assert.deepEqual(JSON.parse(options.body),{account_phone:own,items:[row()]});
      return {ok:true,json:async()=>({ok:true,updated:1})};
    }}),true);
  assert.equal(requests,1);
  for(const reply of [{ok:false},{ok:true,json:async()=>({ok:false,updated:0})},
    {ok:true,json:async()=>({ok:true,updated:2})},{ok:true,json:async()=>({ok:true})}])
    await assert.rejects(()=>forwardContactNames({apiUrl:base,token:secret,accountPhone:own,items:[row()],
      fetchImpl:async()=>reply}),/contact_batch_/);
});

test('arbitrary URLs, credentials in URL, invalid account/recipient/LID and untrusted name sources fail before HTTP',async()=>{
  const invalid=[{apiUrl:'https://example.invalid/internal/qr/ai/reply'},
    {apiUrl:'http://orion-assistente.railway.internal/internal/other'},
    {apiUrl:base+'?key=synthetic'},{apiUrl:base+'#fragment'},
    {apiUrl:'http://user:password@orion-assistente.railway.internal/internal/qr/ai/reply'},
    {token:'short'},{accountPhone:'123456789012345678@lid'},
    {items:[]},{items:[row(own)]},{items:[row(),row()]},
    {items:[row('123456789012345678@lid')]},{items:[{...row(),source:'guessed'}]},
    {items:[row(a,'Cliente WhatsApp')]},{items:Array.from({length:101},(_,i)=>row(String(5511900000000+i)))}];
  for(const patch of invalid)await assert.rejects(()=>forwardContactNames({apiUrl:base,token:secret,
    accountPhone:own,items:[row()],fetchImpl:async()=>{throw Error('HTTP must not run');},...patch}),
    /invalid_contact_batch|unsafe_contact_endpoint/);
});

test('connection replays persisted active-account names in bounded batches; reads and unchanged events send nothing',async t=>{
  const f=fixture(t);
  const contacts=Array.from({length:205},(_,i)=>({id:pn(String(5511900000000+i)),name:'Cliente Exemplo '+i}));
  f.store.observeContacts(contacts);
  const replay=makeContactNames(f.params);assert.deepEqual(replay.entries(),[]);
  replay.bindIdentity(pn(own));assert.equal(replay.entries().length,205);
  f.sync.start();assert.equal(f.calls.length,0);
  await f.advance();await f.advance();await f.advance();
  assert.deepEqual(f.calls.map(call=>JSON.parse(call.body).items.length),[100,100,5]);
  assert.equal(f.sync.health().pending,0);assert.equal(f.timers.size,0);
  f.store.requested({phones:[String(5511900000000)]});f.sync.changed();
  assert.equal(f.calls.length,3);assert.equal(f.timers.size,0);
  f.sync.stop();f.store.bindIdentity(pn(own));f.sync.start();await f.advance();
  assert.equal(f.calls.length,4); // API outage/process restart recovery is idempotent server-side.
});

test('a renamed contact during an in-flight request survives the old acknowledgment and is sent once afterward',async t=>{
  const gate=deferred();let requests=0;
  const f=fixture(t,{fetchImpl:async()=>{if(++requests===1)await gate.promise;return ack();}});
  f.store.observeContacts([{id:pn(a),name:'Nome Anterior'}]);f.sync.start();await f.advance();
  f.store.observeContacts([{id:pn(a),name:'Nome Atual'}]);f.sync.changed();
  gate.resolve();await tick();await f.advance();
  assert.deepEqual(f.calls.map(call=>JSON.parse(call.body).items[0].name),['Nome Anterior','Nome Atual']);
  assert.equal(f.sync.health().pending,0);assert.equal(f.timers.size,0);
});

test('eight retries are bounded; an unchanged credentials/contact event cannot reopen exhausted attempts',async t=>{
  let fail=true;
  const f=fixture(t,{fetchImpl:async()=>{if(fail)throw Error('synthetic rollout outage');return ack();}});
  f.store.observeContacts([{id:pn(a),name:'Nome Original'}]);f.sync.start();
  const delays=[];for(let i=0;i<9;i++)delays.push(await f.advance());
  assert.deepEqual(delays,[750,1000,5000,15000,30000,60000,120000,300000,300000]);
  assert.equal(f.calls.length,9);assert.equal(f.timers.size,0);
  f.store.bindIdentity(pn(own));f.sync.changed();
  assert.equal(f.timers.size,0);assert.equal(f.calls.length,9);
  f.store.observeContacts([{id:pn(a),name:'Mudança Real'}]);fail=false;f.sync.changed();await f.advance();
  assert.equal(f.calls.length,10);assert.equal(f.sync.health().pending,0);
});

test('logout/account replacement aborts pending requests and a late old response cannot acknowledge the new account',async t=>{
  const gate=deferred();let requests=0;
  const f=fixture(t,{fetchImpl:async()=>{if(++requests===1)await gate.promise;return ack();}});
  f.store.observeContacts([{id:pn(a),name:'Cliente da Conta Anterior'}]);f.sync.start();await f.advance();
  const oldSignal=f.calls[0].signal;f.sync.stop();f.store.purge();
  assert.equal(oldSignal.aborted,true);assert.equal(f.sync.health().pending,0);
  const nextOwn='5511999990999';f.store.bindIdentity(pn(nextOwn));
  f.store.observeContacts([{id:pn(b),name:'Cliente da Conta Atual'}]);f.sync.start();
  gate.resolve();await tick();await f.advance();
  assert.deepEqual(f.calls.map(call=>JSON.parse(call.body)),[
    {account_phone:own,items:[row(a,'Cliente da Conta Anterior')]},
    {account_phone:nextOwn,items:[row(b,'Cliente da Conta Atual')]}]);
  assert.equal(f.sync.health().pending,0);
});

test('a stopped or stale socket cannot issue the queued metadata request',async t=>{
  const f=fixture(t);f.store.observeContacts([{id:pn(a),name:'Joana'}]);f.sync.start();
  f.setLive(false);await f.advance();
  assert.equal(f.calls.length,0);assert.equal(f.sync.health().pending,0);
  f.setLive(true);f.sync.start();f.sync.stop();
  assert.equal(f.timers.size,0);assert.equal(f.calls.length,0);
});

test('expired or quarantined contacts are removed before retry instead of forwarding a stale name',async t=>{
  let now=1_800_000_000_000;
  const f=fixture(t,{storeOptions:{now:()=>now,ttlMs:1000},fetchImpl:async()=>({ok:false})});
  f.store.observeContacts([{id:pn(a),name:'Joana'}]);f.sync.start();await f.advance();
  now+=1001;await f.advance();
  assert.equal(f.calls.length,1);assert.equal(f.sync.health().pending,0);
  assert.equal(f.timers.size,0);
});

test('registry and replay queue stay bounded even when a large contact event arrives',t=>{
  const f=fixture(t);
  f.store.observeContacts(Array.from({length:CONTACT_NAME_LIMIT+30},(_,i)=>({
    id:pn(String(5511900000000+i)),name:'Contato Exemplo '+i})));
  f.sync.start();assert.equal(f.store.entries().length,CONTACT_NAME_LIMIT);
  assert.equal(f.sync.health().pending,CONTACT_NAME_LIMIT);assert.equal(f.calls.length,0);
});
