import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {runConsentedPairSmoke,SMOKE_ID,APPOINTMENT_QA_ID} from '../src/consented-live-qa.mjs';

const env={ORION_ONE_TIME_LIVE_SEND:SMOKE_ID,QR_RELIABLE_SEND_V2:'1',
  ORION_SMOKE_TEST_MODE:'1',ORION_SMOKE_SOURCE_E164:'556781234567',
  ORION_SMOKE_BR_E164:'5521999999999',ORION_SMOKE_PY_E164:'595992000001'};
const source='556781234567';
const isolated=fn=>async()=>{
  const folder=fs.mkdtempSync(path.join(os.tmpdir(),'orion-live-pair-'));
  try{await fn(folder);}finally{fs.rmSync(folder,{force:true,recursive:true});}
};

test('explicit owner and exactly two recipients, never a group or customer export',
isolated(async directory=>{
 const seen=[],events=[];
 const send=async(phone,text,opts)=>{
   seen.push({phone,text,opts});
   return {accepted:true,delivery:'pending',trace:'synthetic-trace'};
 };
 const first=await runConsentedPairSmoke({
   env,directory,pairedPhone:source,isCurrent:()=>true,send,
   report:event=>events.push(event)
 });
 assert.deepEqual(first,{state:'finished',attempts:2});
 assert.deepEqual(seen.map(x=>x.phone),['5521999999999','595992000001']);
 assert.ok(seen.every(x=>x.opts.waitForCapacity&&x.opts.transactionalTyping&&
    x.text.includes('Não é um agendamento e não altera dados')&&x.text.length<500));
 assert.equal(events.filter(x=>x.state==='accepted').length,2);
 assert.ok(events.every(x=>!JSON.stringify(x).includes('5521999999999')&&
   !JSON.stringify(x).includes('595992000001')));
 const rerun=await runConsentedPairSmoke({
   env,directory,pairedPhone:source,isCurrent:()=>true,send,
   report:event=>events.push(event)
 });
 assert.deepEqual(rerun,{state:'finished',attempts:0});
 assert.equal(seen.length,2);
 assert.equal(events.filter(x=>x.state==='already_attempted').length,2);
}));

test('missing flag, changed source or insecure send storage cannot send',
isolated(async directory=>{
 let calls=0;
 const send=async()=>{calls++;return {accepted:true}};
 for(const params of [
   {env:{...env,ORION_ONE_TIME_LIVE_SEND:'0'},pairedPhone:source},
   {env,pairedPhone:'5567999999999'},
   {env:{...env,QR_RELIABLE_SEND_V2:'0'},pairedPhone:source},
   {env,pairedPhone:source,isCurrent:()=>false}
 ]){
   await runConsentedPairSmoke({directory,isCurrent:()=>true,send,...params});
 }
 assert.equal(calls,0);
}));

test('provider ambiguity is terminal: marker survives process restarts',
isolated(async directory=>{
 let calls=0;const events=[];
 const failing=async()=>{
   calls++;
   if(calls===1)throw Error('Provider may have accepted first message');
   return {accepted:true,delivery:'pending'};
 };
 const first=await runConsentedPairSmoke({
   env,directory,pairedPhone:source,isCurrent:()=>true,send:failing,
   report:event=>events.push(event)
 });
 assert.deepEqual(first,{state:'finished',attempts:2});
 assert.equal(calls,2);
 assert.equal(events.filter(x=>x.state==='unknown_after_attempt').length,1);
 await runConsentedPairSmoke({
   env,directory,pairedPhone:source,isCurrent:()=>true,send:failing,
   report:event=>events.push(event)
 });
 assert.equal(calls,2,'neither number must be retried after an uncertain result');
 assert.equal(events.filter(x=>x.state==='already_attempted').length,2);
}));

test('fenced off previous socket before claiming first recipient',
isolated(async directory=>{
 let called=0;
 const r=await runConsentedPairSmoke({
   env,directory,pairedPhone:source,
   isCurrent:()=>false,send:async()=>{called++;}
 });
 assert.equal(r.state,'source_unavailable');
 assert.equal(called,0);
 assert.equal(fs.existsSync(path.join(directory,'consented-live-qa')),false);
}));


test('six labelled transactional templates go only to owner-consented BR and PY and never create bookings',
isolated(async directory=>{
 const sent=[],events=[];
 const second={...env,ORION_ONE_TIME_LIVE_SEND:APPOINTMENT_QA_ID};
 const send=async(phone,text,opts)=>{
   sent.push({phone,text,opts});return {accepted:true,delivery:'pending',trace:'synthetic'};
 };
 const first=await runConsentedPairSmoke({directory,env:second,pairedPhone:source,
   isCurrent:()=>true,send,report:event=>events.push(event)});
 assert.deepEqual(first,{state:'finished',attempts:6});
 assert.equal(sent.length,6);
 assert.deepEqual(sent.map(x=>x.phone),[
   env.ORION_SMOKE_BR_E164,env.ORION_SMOKE_BR_E164,env.ORION_SMOKE_BR_E164,
   env.ORION_SMOKE_PY_E164,env.ORION_SMOKE_PY_E164,env.ORION_SMOKE_PY_E164]);
 assert.ok(sent.every(x=>x.text.includes('TESTE ORION')&&
   x.text.includes('Não é atendimento real')&&x.text.length<500));
 assert.ok(sent.every(x=>x.opts.waitForCapacity===true&&
   x.opts.transactionalTyping===true&&x.opts.isCurrent()===true));
 assert.equal(events.filter(x=>x.state==='accepted').length,6);
 assert.ok(events.every(x=>!JSON.stringify(x).includes(env.ORION_SMOKE_BR_E164)&&
   !JSON.stringify(x).includes(env.ORION_SMOKE_PY_E164)));
 const duplicate=await runConsentedPairSmoke({directory,env:second,
   pairedPhone:source,isCurrent:()=>true,send,
   report:event=>events.push(event)});
 assert.deepEqual(duplicate,{state:'finished',attempts:0});
 assert.equal(sent.length,6);
 assert.equal(events.filter(x=>x.state==='already_attempted').length,6);
}));

test('transactional trial refuses tampered source, no opt-in, or wrong target',
isolated(async directory=>{
 let sends=0;
 const send=async()=>{sends++;};
 for(const [e,sourcePhone] of [
  [{...env,ORION_ONE_TIME_LIVE_SEND:'bogus'},source],
  [{...env,ORION_ONE_TIME_LIVE_SEND:APPOINTMENT_QA_ID},'5567999999911'],
  [{...env,ORION_ONE_TIME_LIVE_SEND:APPOINTMENT_QA_ID,QR_RELIABLE_SEND_V2:'0'},source],
  [{...env,ORION_ONE_TIME_LIVE_SEND:APPOINTMENT_QA_ID,ORION_SMOKE_PY_E164:'5522999999901'},source],
  [{...env,ORION_ONE_TIME_LIVE_SEND:APPOINTMENT_QA_ID,ORION_SMOKE_BR_E164:source},source]
 ]){
  const response=await runConsentedPairSmoke({
    directory,env:e,pairedPhone:sourcePhone,isCurrent:()=>true,send});
  assert.equal(response.attempts,0);
 }
 assert.equal(sends,0);
}));
