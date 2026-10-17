import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {runOperatorTest,createOperatorLedger} from '../src/operator-test.mjs';

const phone='5511999999999',own='5511888888888',marker='ORION-SYNTHETIC-002';
const key='synthetic-private-bridge-key-not-real-0000000000';
function setup(changes={}){
  const env={QR_OPERATOR_TEST_ENABLED:'1',QR_OPERATOR_TEST_PHONE:phone,QR_AI_PILOT_NUMBER:phone,
    QR_OPERATOR_TEST_MARKER:marker,QR_BRIDGE_TOKEN:key,...changes};
  const saved=new Map(),events=[],calls=[];
  let time=1000;
  const harness={env,events,calls,saved,rows:[],own,connected:true,registered:true,
    delivery:'delivered',ambiguous:false,sendCount:0};
  function ledgerFactory(directory,name){
    const created=!saved.has(name);
    if(created)saved.set(name,{}); // O_EXCL reservation occurs before the first await.
    return {created,data:structuredClone(saved.get(name)),save(){saved.set(name,structuredClone(this.data))},close(){}};
  }
  async function fetchImpl(url,options){
    const parsed=new URL(url);
    assert.equal(parsed.origin,'http://127.0.0.1:3000');
    assert.equal(options.headers['X-Orion-Bridge-Key'],key);
    assert.equal(options.redirect,'error');
    calls.push([parsed.pathname,options.method]);
    let result;
    if(parsed.pathname==='/internal/status')result={status:harness.connected?'connected':'connecting',phone:harness.own};
    else if(parsed.pathname==='/internal/recipient'){
      assert.equal(parsed.searchParams.get('phone'),phone);
      result={registered:harness.registered,identity:'pn_confirmed',self:harness.own===phone};
    }else if(parsed.pathname==='/internal/messages'){
      assert.equal(parsed.searchParams.get('phone'),phone);
      result={items:structuredClone(harness.rows)};
    }else{
      assert.equal(parsed.pathname,'/internal/send');assert.equal(options.method,'POST');
      assert.equal(saved.get(marker).stage,'attempting');
      assert.equal(saved.get(marker).attemptedAt,time);
      assert.deepEqual(JSON.parse(options.body),{phone,text:marker});
      harness.sendCount++;
      harness.rows.push({id:'out:SYNTHETIC-RAW-ID',phone,direction:'out',body:marker,delivery:harness.delivery});
      if(harness.ambiguous)throw Error('SECRET EXCEPTION CONTENT');
      result={accepted:true,id:'out:SYNTHETIC-RAW-ID',trace:'a'.repeat(20)};
    }
    return {ok:true,json:async()=>result};
  }
  harness.options={env,fetchImpl,ledgerFactory,now:()=>time,sleep:async ms=>{time+=ms},emit:event=>events.push(event)};
  return harness;
}

test('disabled runner performs no requests and no ledger operations',async()=>{
  const h=setup({QR_OPERATOR_TEST_ENABLED:'0'});
  assert.equal((await runOperatorTest(h.options)).stage,'disabled');
  assert.equal(h.calls.length,0);assert.equal(h.saved.size,0);
});

test('missing explicit phone or nonpilot configuration cannot send',async()=>{
  for(const changes of [{QR_OPERATOR_TEST_PHONE:''},{QR_OPERATOR_TEST_PHONE:own},
    {QR_AI_PILOT_NUMBER:''},{QR_OPERATOR_TEST_MARKER:'../unsafe'},{QR_BRIDGE_TOKEN:'short'}]){
    const h=setup(changes);
    assert.equal((await runOperatorTest(h.options)).stage,'configuration_refused');
    assert.equal(h.calls.length,0);assert.equal(h.saved.size,0);
  }
});

test('one verified recipient send records receipt separately from readability',async()=>{
  const h=setup();
  const result=await runOperatorTest(h.options);
  assert.equal(h.sendCount,1);assert.equal(result.receipt,'delivered');
  assert.equal(result.handsetReadability,'unverified');
  assert.equal(h.saved.get(marker).id,'out:SYNTHETIC-RAW-ID');
  assert.equal(h.saved.get(marker).receipt,'delivered');
  const logs=JSON.stringify(h.events);
  for(const secret of [phone,own,key,'SYNTHETIC-RAW-ID'])assert.ok(!logs.includes(secret));
  assert.ok(h.events.some(event=>event.stage==='accepted_unverified'&&event.trace==='a'.repeat(20)));
});

test('existing marker only observes and never sends again',async()=>{
  const h=setup();await runOperatorTest(h.options);h.calls.length=0;
  await runOperatorTest(h.options);
  assert.equal(h.sendCount,1);
  assert.deepEqual(h.calls,[['/internal/messages','GET']]);
});

test('timeout after acceptance recovers exact outgoing body without second send',async()=>{
  const h=setup();h.ambiguous=true;
  const result=await runOperatorTest(h.options);
  assert.equal(result.receipt,'delivered');
  assert.equal(h.saved.get(marker).stage,'ambiguous_never_resend');
  assert.equal(h.sendCount,1);
  await runOperatorTest(h.options);assert.equal(h.sendCount,1);
  assert.ok(!JSON.stringify(h.events).includes('SECRET EXCEPTION CONTENT'));
});

test('pending receipt stays unconfirmed and old marker does not resend after timeout',async()=>{
  const h=setup();h.delivery='pending';
  const result=await runOperatorTest(h.options);
  assert.equal(result.stage,'receipt_unconfirmed_never_resend');assert.equal(result.receipt,'pending');
  assert.equal(result.elapsedMs,60000);assert.equal(h.sendCount,1);
  h.rows=[];await runOperatorTest(h.options);assert.equal(h.sendCount,1);
});

test('self, unregistered, and unavailable connection all prevent send',async()=>{
  for(const mutate of [h=>{h.own=phone},h=>{h.registered=false},h=>{h.connected=false}]){
    const h=setup();mutate(h);
    const result=await runOperatorTest(h.options);
    assert.equal(result.stage,'stopped_never_resend');assert.equal(h.sendCount,0);
    if(!h.connected)assert.equal(result.elapsedMs,45000);
  }
});

test('previous exact body is recovered without mutation',async()=>{
  const h=setup();
  h.rows=[{id:'out:OLD',phone,direction:'out',body:marker,delivery:'read'}];
  const result=await runOperatorTest(h.options);
  assert.equal(h.sendCount,0);assert.equal(result.receipt,'read');
  assert.equal(h.saved.get(marker).stage,'existing_observation_only');
});

test('duplicate matching bodies or changed message ids fail closed',async()=>{
  const h=setup();
  h.rows=['ONE','TWO'].map(id=>({id:'out:'+id,phone,direction:'out',body:marker,delivery:'delivered'}));
  assert.equal((await runOperatorTest(h.options)).stage,'stopped_never_resend');assert.equal(h.sendCount,0);
  const second=setup();await runOperatorTest(second.options);second.rows[0].id='out:DIFFERENT';
  assert.equal((await runOperatorTest(second.options)).stage,'stopped_never_resend');assert.equal(second.sendCount,1);
});

test('a failure to persist attempting state prevents network mutation',async()=>{
  const h=setup(),factory=h.options.ledgerFactory;
  h.options.ledgerFactory=(...args)=>{
    const ledger=factory(...args),save=ledger.save.bind(ledger);
    ledger.save=()=>{if(ledger.data.stage==='attempting')throw Error('disk failure');save()};
    return ledger;
  };
  assert.equal((await runOperatorTest(h.options)).stage,'stopped_never_resend');assert.equal(h.sendCount,0);
  h.options.ledgerFactory=factory;
  await runOperatorTest(h.options);assert.equal(h.sendCount,0);
});

test('POSIX ledger reserves permanently with private modes and refuses corruption',
  {skip:process.platform==='win32'},()=>{
    const root=fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(),'orion-operator-')));
    const directory=path.join(root,'ledger');
    try{
      const first=createOperatorLedger(directory,marker);assert.equal(first.created,true);
      first.data={stage:'attempting'};first.save();first.close();
      assert.equal(fs.statSync(directory).mode&0o777,0o700);
      const filename=path.join(directory,marker+'.json');
      assert.equal(fs.statSync(filename).mode&0o777,0o600);
      const second=createOperatorLedger(directory,marker);assert.equal(second.created,false);
      assert.deepEqual(second.data,{stage:'attempting'});second.close();
      fs.writeFileSync(filename,'{truncated');assert.throws(()=>createOperatorLedger(directory,marker));
      fs.chmodSync(directory,0o755);assert.throws(()=>createOperatorLedger(directory,marker));
    }finally{fs.rmSync(root,{recursive:true,force:true})}
  });
