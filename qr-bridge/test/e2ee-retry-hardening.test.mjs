import test from 'node:test';
import assert from 'node:assert/strict';
import {shouldCacheLinkedSelfText} from '../src/retry-capture-policy.mjs';
import {requiresPreRelayCache} from '../src/pilot-send-policy.mjs';
import {sendStoredText} from '../src/reliable-send.mjs';
import {makeEncryptedRetryStore} from '../src/encrypted-retry-store.mjs';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
const number='5567999999999',jid=number+'@s.whatsapp.net';
const secret='synthetic-test-only-retry-key-123456789012345678901234';
process.env.ORION_RETRY_TEST_MODE='1';

test('only our own bounded non-ephemeral text can enter durable retry cache',()=>{
 const text={key:{fromMe:true,id:'TX-1'},message:{conversation:'Teste de entrega'}};
 assert.equal(shouldCacheLinkedSelfText(text),true);
 assert.equal(shouldCacheLinkedSelfText({...text,key:{fromMe:false,id:'TX-1'}}),false);
 assert.equal(shouldCacheLinkedSelfText({...text,key:{fromMe:true,id:'../../etc/passwd'}}),false);
 assert.equal(shouldCacheLinkedSelfText({...text,message:{ephemeralMessage:{message:{conversation:'privado'}}}}),false);
 assert.equal(shouldCacheLinkedSelfText({...text,message:{viewOnceMessage:{}}}),false);
 assert.equal(shouldCacheLinkedSelfText({...text,message:{conversation:'text',protocolMessage:{}}}),false);
 assert.equal(shouldCacheLinkedSelfText({...text,message:{conversation:'a'.repeat(1201)}}),false);
 assert.equal(shouldCacheLinkedSelfText({...text,message:{extendedTextMessage:{text:'texto'}}}),true);
 assert.equal(shouldCacheLinkedSelfText({...text,message:{imageMessage:{caption:'texto'}}}),false);
 assert.equal(shouldCacheLinkedSelfText(null),false);
});

test('transactional notification keeps the encrypted original before relay and after a restart',async t=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'orion-e2ee-idempotent-'));
 t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
 const store=makeEncryptedRetryStore({directory:dir,secret,max:5});
 const id='WA-RETRY-SYNTHETIC';
 const msg={conversation:'Aviso sintético, não enviar a ninguém'};
 let networkCalls=0;
 assert.equal(requiresPreRelayCache({reliable:false,source:'appointment'}),true);
 await sendStoredText({jid,text:msg.conversation,store,
   makeMessage:async()=>({key:{id,remoteJid:jid,fromMe:true},message:msg}),
   relay:async(_recipient,encrypted,opts)=>{
     networkCalls++;
     assert.equal(opts.messageId,id);
     assert.deepEqual(store.get({id,remoteJid:jid}),msg);
     const bytes=fs.readdirSync(dir).map(name=>fs.readFileSync(path.join(dir,name),'utf8')).join('');
     assert.equal(bytes.includes(msg.conversation),false,'Not stored in cleartext');
   }});
 assert.equal(networkCalls,1);
 const restarted=makeEncryptedRetryStore({directory:dir,secret,max:5});
 assert.deepEqual(restarted.get({id}),msg);
 // No application-level send again just because the provider requests retry.
 assert.equal(networkCalls,1);
});

test('storage error cannot cause a WhatsApp relay or a second attempted send',async()=>{
 let count=0;
 await assert.rejects(
   sendStoredText({
     jid,text:'Teste sem envio',
     makeMessage:async()=>({key:{id:'F001'},message:{conversation:'teste'}}),
     store:{save(){return false}},
     relay:async()=>{count++;}
   }), /Unable to cache/);
 assert.equal(count,0);
});
