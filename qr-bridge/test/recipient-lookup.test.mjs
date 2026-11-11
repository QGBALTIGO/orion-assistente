import test from 'node:test';
import assert from 'node:assert/strict';
import {lookupRecipient} from '../src/recipient-lookup.mjs';

const phone='5567999999901';
const found=[{exists:true,jid:phone+'@s.whatsapp.net'}];

test('one transient lookup error is retried within the same operation',async()=>{
  let calls=0,checks=0;
  const result=await lookupRecipient({onWhatsApp:async()=>{if(++calls===1)throw Error('temporary');return found;}},phone,
    {assertCurrent:async()=>{checks++;},retryMs:1,timeoutMs:50});
  assert.deepEqual(result,found);assert.equal(calls,2);assert.ok(checks>=4);
});

test('negative identity verdict is returned without retry',async()=>{
  let calls=0;
  const result=await lookupRecipient({onWhatsApp:async()=>{calls++;return [{exists:false,jid:phone+'@s.whatsapp.net'}];}},phone,
    {retryMs:1,timeoutMs:50});
  assert.equal(result[0].exists,false);assert.equal(calls,1);
});

test('expired lookup cannot block the operation indefinitely',async()=>{
  let calls=0;
  await assert.rejects(lookupRecipient({onWhatsApp:()=>{calls++;return new Promise(()=>{});}},phone,
    {retryMs:1,timeoutMs:5}),/recipient_lookup_timeout/);
  assert.equal(calls,2);
});

test('revocation after the first error prevents the second lookup',async()=>{
  let calls=0,current=true;
  await assert.rejects(lookupRecipient({onWhatsApp:async()=>{calls++;current=false;throw Error('temporary');}},phone,
    {isCurrent:()=>current,retryMs:1,timeoutMs:50}),/recipient_lookup_superseded/);
  assert.equal(calls,1);
});
