import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';import os from 'node:os';import path from 'node:path';
import {makeMessageRetryStore} from '../src/message-retry-store.mjs';
import {makeEncryptedRetryStore} from '../src/encrypted-retry-store.mjs';
import {readAuthorizedRetry} from '../src/retry-identity.mjs';
process.env.ORION_RETRY_TEST_MODE='1';
const pn='5567999999901@s.whatsapp.net',other='5567999999902@s.whatsapp.net',lid='100000000000001@lid',msg={conversation:'private synthetic text'};
const key={id:'SYNTHETIC-RETRY',remoteJid:pn,fromMe:true};
for(const type of ['memory','encrypted']){
 function setup(t){if(type==='memory')return makeMessageRetryStore();const directory=fs.mkdtempSync(path.join(os.tmpdir(),'orion-retry-scope-'));t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));return makeEncryptedRetryStore({directory,secret:'synthetic-key-'.repeat(4)});}
 test(type+': another recipient knowing the exact id cannot retrieve ciphertext plaintext',async t=>{
  const s=setup(t);s.save(key,msg);
  for(const query of [{...key,participant:other},{id:key.id},{...key,fromMe:false},{...key,remoteJid:other},{...key,remoteJid:'123@g.us'}])assert.equal(await readAuthorizedRetry(s,query),undefined);
  assert.deepEqual(await readAuthorizedRetry(s,{...key,remoteJid:pn.replace('@',':3@')}),msg);
 });
 test(type+': incoming message id cannot overwrite an outbound retry',async t=>{
  const s=setup(t);assert.equal(s.save(key,msg),true);assert.equal(s.save({...key,fromMe:false,remoteJid:other},{conversation:'attacker'}),false);
  assert.deepEqual(await readAuthorizedRetry(s,key),msg);
 });
 test(type+': LID alias needs exact provider proof, not supplied retry fields',async t=>{
  const s=setup(t);s.save(key,msg);let alive=true;
  const query={...key,remoteJid:lid,remoteJidAlt:pn};
  assert.equal(await readAuthorizedRetry(s,query),undefined);
  assert.equal(await readAuthorizedRetry(s,query,{socket:{onWhatsApp:async()=>[{exists:true,jid:other,lid}]}}),undefined);
  assert.deepEqual(await readAuthorizedRetry(s,query,{socket:{onWhatsApp:async()=>[{exists:true,jid:pn,lid}]}}),msg);
  assert.equal(await readAuthorizedRetry(s,query,{isCurrent:()=>alive,socket:{onWhatsApp:async()=>{alive=false;return [{exists:true,jid:pn,lid}];}}}),undefined);
 });
 test(type+': legacy unbound records remain inspectable internally but cannot authorize network replay',async t=>{
  const s=setup(t);s.save({id:key.id},msg);assert.deepEqual(s.get({id:key.id}),msg);assert.equal(await readAuthorizedRetry(s,key),undefined);
 });
}
test('encrypted records are bound to id and session directory, not just a shared encryption secret',t=>{
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'orion-retry-aad-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
 const a=path.join(root,'a'),b=path.join(root,'b'),secret='synthetic-key-'.repeat(4);
 const sa=makeEncryptedRetryStore({directory:a,secret}),sb=makeEncryptedRetryStore({directory:b,secret});sa.save(key,msg);
 const name=fs.readdirSync(a)[0];fs.copyFileSync(path.join(a,name),path.join(b,name));assert.equal(sb.get(key),undefined);
 sa.save({...key,id:'second'},{conversation:'second'});const second=fs.readdirSync(a).find(x=>x!==name);
 fs.copyFileSync(path.join(a,name),path.join(a,second));assert.equal(sa.get({...key,id:'second'}),undefined);
});
