import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {runConsentedPairSmoke,SMOKE_ID} from '../src/consented-live-qa.mjs';

const env={ORION_ONE_TIME_LIVE_SEND:SMOKE_ID,QR_RELIABLE_SEND_V2:'1',
  ORION_SMOKE_TEST_MODE:'1'};
const source='556781030648';
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
 assert.deepEqual(seen.map(x=>x.phone),['5521997557364','595992820210']);
 assert.ok(seen.every(x=>x.opts.waitForCapacity&&x.opts.transactionalTyping&&
    x.text.includes('não cria nem altera agendamentos')&&x.text.length<500));
 assert.equal(events.filter(x=>x.state==='accepted').length,2);
 assert.ok(events.every(x=>!JSON.stringify(x).includes('5521997557364')&&
   !JSON.stringify(x).includes('595992820210')));
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
   {env,pairedPhone:'5511999999999'},
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
