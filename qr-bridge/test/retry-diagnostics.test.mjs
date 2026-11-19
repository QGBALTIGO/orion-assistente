import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createCipheriv,createHmac,hkdfSync,randomBytes} from 'node:crypto';
import {makeEncryptedRetryStore} from '../src/encrypted-retry-store.mjs';
import {readAuthorizedRetry} from '../src/retry-identity.mjs';

process.env.ORION_RETRY_TEST_MODE='1';
const secret='synthetic-private-retry-diagnostics-key-0123456789';
const pn='5567999999901@s.whatsapp.net',other='5567999999902@s.whatsapp.net',lid='100000000000001@lid';
const owner='5567999999903:4@s.whatsapp.net',ownerLid='100000000000003:4@lid';
const key={id:'SYNTHETIC-PRIVATE-RETRY',remoteJid:pn,fromMe:true};
const message={conversation:'Confidential synthetic conversation'};
function setup(t,options={}){
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'orion-retry-diagnostics-'));
  t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  return {directory,store:makeEncryptedRetryStore({directory,secret,...options})};
}
async function lookup(store,query=key,options={}){
  const diagnostics=[];
  const content=await readAuthorizedRetry(store,query,{...options,onResult:value=>diagnostics.push(value)});
  assert.equal(diagnostics.length,1);
  assert.deepEqual(Object.keys(diagnostics[0]).sort(),['cached','reason']);
  assert.doesNotMatch(JSON.stringify(diagnostics),/SYNTHETIC-PRIVATE-RETRY|Confidential|556799|100000000000001|synthetic-private|@|\.enc/);
  return {content,...diagnostics[0]};
}

test('retry miss diagnostics distinguish absent, expiry and a bounded-capacity eviction',async t=>{
  let now=Date.now();
  const {directory,store}=setup(t,{now:()=>now,ttlMs:60000,max:1});
  assert.deepEqual(await lookup(store),{content:undefined,cached:false,reason:'absent'});
  assert.equal(store.save(key,message),true);
  const filename=fs.readdirSync(directory)[0];fs.utimesSync(path.join(directory,filename),new Date(now-1000),new Date(now-1000));
  assert.equal(store.save({...key,id:'SECOND'},message),true);
  assert.deepEqual(await lookup(store),{content:undefined,cached:false,reason:'capacity_evicted'});
  now+=60000;
  assert.deepEqual(await lookup(store,{...key,id:'SECOND'}),{content:undefined,cached:false,reason:'expired'});
  assert.equal((await lookup(store,{...key,id:'SECOND'})).reason,'expired');
  // Diagnostics never restore removed content or invent historical reasons.
  const restarted=makeEncryptedRetryStore({directory,secret,now:()=>now,ttlMs:60000,max:1});
  assert.equal((await lookup(restarted,{...key,id:'SECOND'})).reason,'absent');
});

test('expiry caused by prune remains distinguishable from an unobserved missing record',async t=>{
  let now=Date.now();const {directory,store}=setup(t,{now:()=>now,ttlMs:1000});
  store.save(key,message);const filename=fs.readdirSync(directory)[0];
  fs.utimesSync(path.join(directory,filename),new Date(now-2000),new Date(now-2000));store.prune();
  assert.equal((await lookup(store)).reason,'expired');
  now+=1000;assert.equal((await lookup(store)).reason,'absent');
});

test('corruption, unsupported format and AEAD failure have separate reasons and no payload',async t=>{
  for(const [kind,change] of [
    ['corrupt_record',()=>'{not json'],
    ['unsupported_format',record=>JSON.stringify({...record,v:42})],
    ['decryption_failed',record=>JSON.stringify({...record,ciphertext:'AAAA'})],
    ['corrupt_record',record=>JSON.stringify({...record,tag:[]})]
  ]){
    await t.test(kind,async child=>{
      const {directory,store}=setup(child);store.save(key,message);
      const filename=path.join(directory,fs.readdirSync(directory)[0]);
      fs.writeFileSync(filename,change(JSON.parse(fs.readFileSync(filename,'utf8'))));
      assert.deepEqual(await lookup(store),{content:undefined,cached:false,reason:kind});
    });
  }
});

test('moving a retry record to another channel cannot authorize replay with the same secret',async t=>{
  const a=setup(t),b=setup(t);a.store.save(key,message);
  const name=fs.readdirSync(a.directory)[0];fs.copyFileSync(path.join(a.directory,name),path.join(b.directory,name));
  assert.deepEqual(await lookup(b.store),{content:undefined,cached:false,reason:'decryption_failed'});
});

test('filesystem access failure is reported without paths or error strings',async t=>{
  const {directory,store}=setup(t);store.save(key,message);
  fs.rmSync(directory,{recursive:true});fs.writeFileSync(directory,'synthetic inaccessible directory');
  assert.deepEqual(await lookup(store),{content:undefined,cached:false,reason:'storage_error'});
});

test('unsafe cache files and oversized ciphertext are refused before decryption',async t=>{
  const {directory,store}=setup(t,{maxPayloadBytes:1024});store.save(key,message);
  const filename=path.join(directory,fs.readdirSync(directory)[0]);
  fs.writeFileSync(filename,'x'.repeat(4097));
  assert.equal((await lookup(store)).reason,'oversized_record');
  fs.rmSync(filename);fs.mkdirSync(filename);
  assert.equal((await lookup(store)).reason,'unsafe_file');
});

test('legacy v1 records remain readable internally but cannot authorize an unbound retry',async t=>{
  const {directory,store}=setup(t);
  const encryptionKey=Buffer.from(hkdfSync('sha256',Buffer.from(secret),Buffer.from('orion/baileys/message-retry/v1'),Buffer.from('aes-256-gcm'),32));
  const iv=randomBytes(12),cipher=createCipheriv('aes-256-gcm',encryptionKey,iv);cipher.setAAD(Buffer.from('orion-retry-v1'));
  const ciphertext=Buffer.concat([cipher.update(JSON.stringify({expires:Date.now()+60000,message})),cipher.final()]);
  const filename=createHmac('sha256',encryptionKey).update(key.id).digest('hex')+'.enc';
  fs.writeFileSync(path.join(directory,filename),JSON.stringify({v:1,iv:iv.toString('base64'),tag:cipher.getAuthTag().toString('base64'),ciphertext:ciphertext.toString('base64')}));
  assert.deepEqual(store.get({id:key.id}),message);
  assert.deepEqual(await lookup(store),{content:undefined,cached:false,reason:'unbound_identity'});
});

test('invalid direction, peer and participant are distinguished before any storage lookup',async()=>{
  const store={entry(){throw Error('The private cache must not be read for an unauthorized key');}};
  for(const [query,reason] of [
    [{...key,id:''},'invalid_id'],[{...key,remoteJid:'status@broadcast'},'invalid_peer'],
    [{...key,fromMe:false},'invalid_direction'],[{...key,fromMe:undefined},'invalid_direction'],
    [{...key,participant:'not-a-private-jid'},'invalid_participant'],
    [{...key,participant:other},'participant_mismatch']
  ])assert.deepEqual(await lookup(store,query),{content:undefined,cached:false,reason});
});

test('verified outbound identity permits recipient and own companion-device retries',async t=>{
  const {store}=setup(t);store.save(key,message);
  for(const participant of [undefined,pn.replace('@',':9@'),owner,ownerLid]){
    const query={...key,remoteJid:pn.replace('@',':3@'),...(participant?{participant}:{})};
    assert.deepEqual(await lookup(store,query,{socket:{user:{id:owner,lid:ownerLid}}}),{content:message,cached:true,reason:'hit'});
  }
});

test('bound identity mismatch never falls back to id-only lookup',async t=>{
  const {store}=setup(t);store.save(key,message);
  assert.equal((await lookup(store,{...key,remoteJid:other})).reason,'peer_mismatch');
  for(const [identity,reason] of [
    [{id:key.id,peer:pn,fromMe:false},'stored_direction'],
    [{id:'another-message',peer:pn,fromMe:true},'identity_id_mismatch']
  ])assert.deepEqual(await lookup({entry:()=>({identity,message})}),{content:undefined,cached:false,reason});
});

test('alias proof availability, denial, provider errors and exact success are distinct',async t=>{
  const {store}=setup(t);store.save(key,message);const query={...key,remoteJid:lid};
  assert.equal((await lookup(store,query)).reason,'alias_proof_unavailable');
  assert.equal((await lookup(store,query,{socket:{onWhatsApp:async()=>[{exists:true,jid:other,lid}]}})).reason,'alias_proof_denied');
  assert.equal((await lookup(store,query,{socket:{onWhatsApp:async()=>{throw Error('secret provider error: '+pn);}}})).reason,'alias_proof_error');
  assert.deepEqual(await lookup(store,query,{socket:{onWhatsApp:async()=>[{exists:true,jid:pn,lid}]}}),{content:message,cached:true,reason:'hit_verified_alias'});
});

test('alias proof timeout does not permit replay and is distinguishable from denial',async t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  const {store}=setup(t);store.save(key,message);
  const promise=lookup(store,{...key,remoteJid:lid},{socket:{onWhatsApp:()=>new Promise(()=>{})}});
  await Promise.resolve();t.mock.timers.tick(5001);
  assert.deepEqual(await promise,{content:undefined,cached:false,reason:'alias_proof_timeout'});
});

test('session change during authorization or alias resolution cannot serve a cached proto',async t=>{
  const {store}=setup(t);store.save(key,message);let current=true;
  assert.equal((await lookup(store,key,{isCurrent:()=>false})).reason,'stale_session');
  assert.equal((await lookup(store,key,{assertCurrent:async()=>{throw Error('lease revoked '+pn);}})).reason,'authorization_failed');
  assert.equal((await lookup(store,{...key,remoteJid:lid},{isCurrent:()=>current,socket:{onWhatsApp:async()=>{current=false;return [{exists:true,jid:pn,lid}];}}})).reason,'stale_session');
});

test('diagnostic callback failures and untrusted lookup reasons cannot expose or alter a valid retry',async t=>{
  const {store}=setup(t);store.save(key,message);
  assert.deepEqual(await readAuthorizedRetry(store,key,{onResult:()=>{throw Error('observer failed');}}),message);
  assert.deepEqual(await readAuthorizedRetry(store,key,{onResult:()=>Promise.reject(Error('observer failed'))}),message);
  assert.deepEqual(await lookup({entryResult:()=>({reason:pn+message.conversation})}),{content:undefined,cached:false,reason:'absent'});
});

test('encrypted retry origin persists across restarts and bridge sends retain their origin',t=>{
  const {directory,store}=setup(t);
  assert.equal(store.save(key,message,{origin:'linked_self'}),true);
  assert.equal(makeEncryptedRetryStore({directory,secret}).entry(key).origin,'linked_self');
  const generated={...key,id:'SYNTHETIC-GENERATED'};
  assert.equal(store.save(generated,message),true);
  assert.equal(store.entry(generated).origin,'bridge_generated');
  assert.equal(store.save(generated,message,{origin:'linked_self'}),false);
  assert.equal(store.entry(generated).origin,'bridge_generated');
  assert.equal(store.save({...key,id:'INVALID-ORIGIN'},message,{origin:'user-unverified'}),false);
});

test('old v2 records without origin preserve the existing bridge-generated interpretation',t=>{
  const {directory,store}=setup(t,{serialize:value=>JSON.stringify({...value,origin:undefined})});
  assert.equal(store.save(key,message),true);
  assert.equal(makeEncryptedRetryStore({directory,secret}).entry(key).origin,'bridge_generated');
});
