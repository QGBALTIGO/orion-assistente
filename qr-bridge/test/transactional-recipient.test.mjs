import test from 'node:test';
import assert from 'node:assert/strict';
import {resolveTransactionalRecipient,recipientPreflightError} from '../src/transactional-recipient.mjs';

const full='5567999999922',legacy='556799999922';
const row=number=>({jid:number+'@s.whatsapp.net',exists:true,lid:'100022233@lid'});
const attempt=async (answers,extra={})=>{
  const calls=[];
  const resolved=await resolveTransactionalRecipient(full,async number=>{
    calls.push(number);
    const reply=answers[number];
    if(reply instanceof Error)throw reply;
    return reply;
  },extra);
  return {calls,resolved};
};

test('modern Brazilian PN needs exactly one lookup and keeps the 9 digit',async()=>{
 const {calls,resolved}=await attempt({[full]:[row(full)]});
 assert.deepEqual(calls,[full]);
 assert.equal(resolved.jid,full+'@s.whatsapp.net');
 assert.equal(resolved.kind,'verified_exact');
 assert.equal(Object.isFrozen(resolved),true);
});

test('a separately registered 8-digit PN cannot prove the 9-digit owner and is never queried',async()=>{
 const calls=[];
 await assert.rejects(resolveTransactionalRecipient(full,async number=>{
   calls.push(number);return number===full?[]:[row(legacy)];
 }),{code:'recipient_identity_unverified'});
 assert.deepEqual(calls,[full],'no fallback to another subscriber or network query');
});

test('USync normalization of original query also resolves old PN without a second query',async()=>{
 const {calls,resolved}=await attempt({[full]:[row(legacy)]});
 assert.deepEqual(calls,[full]);
 assert.equal(resolved.jid,legacy+'@s.whatsapp.net');
 assert.equal(resolved.kind,'verified_provider_normalized');
});

test('ambiguous, foreign, unregistered and stale responses never authorize a send',async()=>{
 const cases=[
  {[full]:[],[legacy]:[]},
  {[full]:[{jid:'5521888888888@s.whatsapp.net',exists:true}]},
  {[full]:[row(full),row(legacy)]},
  {[full]:[],[legacy]:[row(legacy),row(legacy)]},
  {[full]:[],[legacy]:[{jid:legacy+'@s.whatsapp.net',exists:false}]},
  {[full]:[{jid:'100022233@lid',exists:true}]},
  {[full]:undefined},
  {[full]:new Error('provider private details MUST NOT LEAK')},
  {[full]:[],[legacy]:new Error('provider private details MUST NOT LEAK')}
 ];
 for(const answers of cases){
   await assert.rejects(attempt(answers),error=>
     error.code==='recipient_identity_unverified' &&
     !error.message.includes('private details') &&
     error.message.includes('Nenhuma mensagem'));
 }
});

test('a non-Brazilian number never gets a guessed legacy candidate',async()=>{
 let calls=0;
 await assert.rejects(resolveTransactionalRecipient('595981234567',async()=>{calls++;return [];}),{
   code:'recipient_identity_unverified'});
 assert.equal(calls,1);
});

test('a changed socket cannot use a lookup proof and must fail before encryption',async()=>{
 let current=true,calls=0;
 await assert.rejects(resolveTransactionalRecipient(full,async()=>{
   calls++;current=false;return [row(full)];
 },{isCurrent:()=>current}),{code:'recipient_identity_unverified'});
 assert.equal(calls,1);
});

test('self-account and provider-normalized alias equal to self are refused',async()=>{
 await assert.rejects(attempt({[full]:[row(full)]},{ownPhone:full}),{code:'recipient_identity_unverified'});
 await assert.rejects(attempt({[full]:[row(legacy)]},{ownPhone:legacy}),{code:'recipient_identity_unverified'});
});

test('preflight error identifies only a no-send condition',()=>{
 const result=recipientPreflightError();
 assert.equal(result.code,'recipient_identity_unverified');
 assert.doesNotMatch(result.message,/\+\d|@lid|@s\.whatsapp/);
});
