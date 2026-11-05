import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {BufferJSON,initAuthCreds,Curve,proto} from '@whiskeysockets/baileys';
import {classifyPairedAuth} from '../src/paired-auth-state.mjs';
import {encryptedChannelStore} from '../src/channel-storage.mjs';
import {pairedAuthFixture,pairSuccessFixture} from './helpers/paired-auth-fixture.mjs';
const sdk={Curve,proto},classify=creds=>classifyPairedAuth(creds,sdk);
const clone=value=>JSON.parse(JSON.stringify(value,BufferJSON.replacer),BufferJSON.reviver);

test('actual successful QR SDK output is paired before registered or LID exist',()=>{
  const {creds,update,initialCreds}=pairSuccessFixture();
  assert.equal(initialCreds.registered,false);assert.equal(initialCreds.me,undefined);
  assert.deepEqual(Object.keys(update).sort(),['account','me','platform','signalIdentities']);
  assert.equal(creds.registered,false);assert.equal(creds.me.lid,undefined);
  assert.equal(creds.account.accountSignatureKey.length,32);assert.equal(creds.account.accountSignature.length,64);
  assert.equal(creds.account.deviceSignature.length,64);assert.equal(classify(creds),'paired');
});

test('complete phone-code and hosted pair-success signatures also classify as paired',()=>{
  assert.equal(classify(pairedAuthFixture({registered:true})),'paired');
  assert.equal(classify(pairedAuthFixture({hosted:true})),'paired');
});

test('fresh SDK credentials are unlinked; a registered flag alone is not pairing proof',()=>{
  const fresh=initAuthCreds();assert.equal(classify(fresh),'unlinked');
  fresh.registered=true;assert.equal(classify(fresh),'invalid');
});

for(const registered of [false,true])test(`phone-code provisional primary PN is pending, registered=${registered}`,()=>{
  const creds=initAuthCreds();Object.assign(creds,{registered,me:{id:'5567999999902@s.whatsapp.net',name:'~'},pairingCode:'ABCD1234'});
  assert.equal(classify(creds),'pending');assert.equal(creds.me.id,'5567999999902@s.whatsapp.net');
});

test('an entered phone or profile cannot replace the signed companion identity',()=>{
  const creds=initAuthCreds();creds.me={id:'5567999999902@s.whatsapp.net',name:'synthetic'};
  assert.equal(classify(creds),'invalid');creds.me.id='5567999999902:7@s.whatsapp.net';assert.equal(classify(creds),'invalid');
});

for(const jid of ['5567999999902@s.whatsapp.net','5567999999902:0@s.whatsapp.net','5567999999902:65536@s.whatsapp.net',
  '5567999999902:-1@s.whatsapp.net','900000000000002:7@lid','5567999999902:7@g.us'])
  test(`a signed account cannot authorize an invalid own companion JID: ${jid}`,()=>{
    const creds=pairedAuthFixture();creds.me.id=jid;assert.equal(classify(creds),'invalid');
  });

for(const field of ['accountSignature','deviceSignature','accountSignatureKey','details'])
  test(`tampered ${field} is rejected without changing stored credentials`,()=>{
    const creds=pairedAuthFixture();creds.account[field][0]^=1;
    const before=JSON.stringify(creds,BufferJSON.replacer);assert.equal(classify(creds),'invalid');
    assert.equal(JSON.stringify(creds,BufferJSON.replacer),before);
  });

test('a valid account from another companion key is not pairing proof for these credentials',()=>{
  const first=pairedAuthFixture(),other=pairedAuthFixture();first.account=other.account;
  assert.equal(classify(first),'invalid');
});

for(const mutate of [creds=>{delete creds.noiseKey;},creds=>{creds.signedIdentityKey.public=Buffer.alloc(0);},
  creds=>{delete creds.advSecretKey;},creds=>{creds.advSecretKey='malformed-base64';},
  creds=>{creds.signedPreKey.signature=Buffer.alloc(0);},creds=>{creds.registrationId=-1;},
  creds=>{creds.registered='false';},creds=>{creds.account.deviceSignature=Array(64).fill(0);}])
  test('partial or incorrectly typed credential material fails closed',()=>{
    const creds=pairedAuthFixture();mutate(creds);assert.equal(classify(creds),'invalid');
  });

test('existing QR auth survives encrypted restart with identical key material and no new registration',t=>{
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'orion-paired-auth-proof-'));
  t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  const open=()=>encryptedChannelStore({directory,secret:'synthetic-encrypted-auth-proof-secret-000000000000',
    serialize:value=>JSON.stringify(value,BufferJSON.replacer),deserialize:value=>JSON.parse(value,BufferJSON.reviver)});
  const original=pairedAuthFixture(),before=JSON.stringify(original,BufferJSON.replacer);open().set('creds',original);
  const restored=open().get('creds');assert.equal(classify(restored),'paired');assert.equal(restored.registered,false);
  assert.equal(JSON.stringify(restored,BufferJSON.replacer),before);assert.equal(classify(clone(restored)),'paired');
});
