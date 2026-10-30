
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {sendStoredText} from '../src/reliable-send.mjs';
import {makeEncryptedRetryStore} from '../src/encrypted-retry-store.mjs';
import {deliveryPolicy} from '../src/pilot-send-policy.mjs';

process.env.ORION_RETRY_TEST_MODE='1';
test('transporte para cliente BR/PY preserva conteúdo criptografado antes do relay',async()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'orion-reliable-safe-'));
  try{
    const store=makeEncryptedRetryStore({
      directory:dir,
      secret:'only-synthetic-bridge-token-very-long-for-crypto-123456789'
    });
    for(const [n,phone] of ['5511999998888','595992820210'].entries()){
      const id='TEST-RETRY-'+n,message={conversation:'Mensagem privada '+n};
      const jid=phone+'@s.whatsapp.net';
      await sendStoredText({
        jid,store,text:message.conversation,
        makeMessage:async()=>({key:{id,remoteJid:jid},message}),
        relay:async(_recipient,prepared)=>{
          assert.deepEqual(prepared,message);
          assert.deepEqual(store.get({id}),message);
          const contents=fs.readdirSync(dir).map(name=>fs.readFileSync(path.join(dir,name),'utf8')).join('');
          assert.ok(!contents.includes(message.conversation),'not encrypted on disk');
        }
      });
    }
    assert.equal(fs.readdirSync(dir).length,2);
    assert.deepEqual(store.get({id:'TEST-RETRY-0'}),{conversation:'Mensagem privada 0'});
    assert.deepEqual(store.get({id:'TEST-RETRY-1'}),{conversation:'Mensagem privada 1'});
  }finally{fs.rmSync(dir,{force:true,recursive:true})}
});

test('global reliable delivery is opt-in without relaxing approved pilot nor allowing bulk traffic',()=>{
  const base={QR_AI_ENABLED:'0',QR_AI_PILOT_NUMBER:'',
    QR_RELIABLE_SEND_V2:'1',QR_FORCE_FRESH_DEVICES:'0'};
  for(const p of ['5511999998888','595992820210']){
    assert.deepEqual(deliveryPolicy(p,base),{reliable:true,freshDevices:false,pilot:false});
  }
});
