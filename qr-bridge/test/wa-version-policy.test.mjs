import test from 'node:test';import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import fs from 'node:fs';import os from 'node:os';import path from 'node:path';
import * as sdk from '@whiskeysockets/baileys';
import {createWaVersionPolicy} from '../src/wa-version-policy.mjs';
import {createBaileysChannelSession} from '../src/channel-session.mjs';
import {encryptedChannelStore} from '../src/channel-storage.mjs';
process.env.ORION_RETRY_TEST_MODE='1';
const bundled=sdk.DEFAULT_CONNECTION_CONFIG.version,latest=[2,3000,bundled[2]+1];
const deferred=()=>{let resolve;const promise=new Promise(done=>{resolve=done;});return {resolve,promise};};
const flush=()=>new Promise(resolve=>setImmediate(resolve));

test('primary and managed concurrent selections share one bounded official helper call and pin its valid result',async()=>{
  const policy=createWaVersionPolicy(),gate=deferred();let calls=0;
  const request={bundledVersion:bundled,fetchVersion:async options=>{calls++;assert.deepEqual(options,{timeout:3500});return gate.promise;}};
  const primary=policy.select(request),managed=policy.select(request);await flush();assert.equal(calls,1);
  gate.resolve({version:latest,isLatest:true});assert.deepEqual(await primary,{version:latest,source:'latest_web'});
  const second=await managed;assert.deepEqual(second.version,latest);second.version[2]=1;
  assert.deepEqual((await policy.select({...request,fetchVersion:()=>assert.fail('Pinned version cannot drift on reconnect')})).version,latest);
});
test('explicit auto=0 uses the actual installed bundled tuple without any network or clearing a previous success',async()=>{
  const policy=createWaVersionPolicy(),fetchVersion=async()=>({version:latest,isLatest:true});
  await policy.select({bundledVersion:bundled,fetchVersion});
  assert.deepEqual(await policy.select({bundledVersion:bundled,auto:false,fetchVersion:()=>assert.fail('No network')}),{version:bundled,source:'bundled_disabled'});
  assert.deepEqual((await policy.select({bundledVersion:bundled,fetchVersion})).version,latest);
});
for(const result of [null,{isLatest:false,version:latest},{isLatest:true,version:[2,3000,0]},
  {isLatest:true,version:[2,3000,NaN]},{isLatest:true,version:[2,3000,1,4]},{isLatest:true,version:'2.3000.1'}])
test('unverified or malformed Web lookup remains a labeled bounded fallback: '+JSON.stringify(result),async()=>{
  let now=0,calls=0;const policy=createWaVersionPolicy({now:()=>now});
  const request={bundledVersion:bundled,fetchVersion:async()=>{calls++;return result;}};
  assert.deepEqual(await policy.select(request),{version:bundled,source:'bundled_fallback'});
  now=59999;await policy.select(request);assert.equal(calls,1);
  now=60001;await policy.select(request);assert.equal(calls,2);
});
test('a helper which never settles cannot exceed 3.5s and gets a sixty-second retry cooldown',async()=>{
  let now=0,calls=0,serial=0;const tasks=new Map(),policy=createWaVersionPolicy({now:()=>now,
    schedule:(fn,ms)=>{const id=++serial;tasks.set(id,{fn,ms});return id;},cancel:id=>tasks.delete(id)});
  const request={bundledVersion:bundled,fetchVersion:async()=>{calls++;return new Promise(()=>{});}};
  const pending=policy.select(request);await flush();assert.equal(calls,1);assert.equal(tasks.size,1);
  const timeout=[...tasks.values()][0];assert.equal(timeout.ms,3500);now=3500;timeout.fn();
  assert.deepEqual(await pending,{version:bundled,source:'bundled_fallback'});assert.equal(tasks.size,0);
  now=63499;await policy.select(request);assert.equal(calls,1);
  now=63501;assert.deepEqual(await policy.select({bundledVersion:bundled,fetchVersion:async()=>({version:latest,isLatest:true})}),{version:latest,source:'latest_web'});
});
test('a thrown helper is contained without logging its response or changing the pinned SDK',async()=>{
  const policy=createWaVersionPolicy();assert.deepEqual(await policy.select({bundledVersion:bundled,
    fetchVersion:async()=>{throw Error('secret upstream details');}}),{version:bundled,source:'bundled_fallback'});
  await assert.rejects(policy.select({bundledVersion:[2,3000,-1]}),/invalid_bundled_web_version/);
});

function local(t,{registered=false}={}){
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'orion-web-version-')),secret='synthetic-version-fixture-secret-'.repeat(3),configs=[];
  let current=true;const context={directory,secret,resumeOnly:registered,isCurrent:()=>current,
    assertCurrent:async()=>{if(!current)throw Error('lease expired');},allowedSender:()=>false,assistantEnabled:()=>false,
    onConnected(){},onDisconnected(){},onMessage(){},onReceipt:async()=>{}};
  if(registered){const auth=path.join(directory,'auth');fs.mkdirSync(auth,{mode:0o700});
    encryptedChannelStore({directory:auth,secret,serialize:value=>JSON.stringify(value,sdk.BufferJSON.replacer)})
      .set('creds',{...sdk.initAuthCreds(),registered:true});}
  const fake={...sdk,default:options=>{configs.push(options);return {ev:new EventEmitter(),end(){},user:{id:'5567999999901@s.whatsapp.net'}};}};
  t.after(()=>{const target=path.resolve(directory);assert.ok(target.startsWith(path.resolve(os.tmpdir())+path.sep+'orion-web-version-'));fs.rmSync(target,{recursive:true,force:true});});
  return {context,fake,configs,revoke:()=>{current=false;}};
}
function autoFlag(t,value){const old=process.env.QR_WA_VERSION_AUTO;t.after(()=>{if(old===undefined)delete process.env.QR_WA_VERSION_AUTO;else process.env.QR_WA_VERSION_AUTO=old;});
  process.env.QR_WA_VERSION_AUTO=value;}
for(const registered of [false,true])test(`actual managed constructor follows the shared policy for ${registered?'restored':'new'} auth without changing its identity`,async t=>{
  autoFlag(t,'1');const f=local(t,{registered}),policy=createWaVersionPolicy();let calls=0;
  const fetchVersion=async()=>{calls++;return {isLatest:true,version:latest};};
  const primary=await policy.select({bundledVersion:bundled,fetchVersion});f.fake.fetchLatestWaWebVersion=()=>assert.fail('Shared success must not fetch a second revision');
  const before=registered?fs.readFileSync(path.join(f.context.directory,'auth',fs.readdirSync(path.join(f.context.directory,'auth'))[0])):null;
  const session=await createBaileysChannelSession(f.context,{loadBaileys:async()=>f.fake,selectVersion:policy.select});t.after(()=>session.close());
  assert.deepEqual(f.configs[0].version,primary.version);assert.equal(calls,1);assert.equal(session.connectionHealth().web_version_source,'latest_web');
  if(before)assert.deepEqual(fs.readFileSync(path.join(f.context.directory,'auth',fs.readdirSync(path.join(f.context.directory,'auth'))[0])),before);
});
test('managed auto=0 does not fetch and reports the real bundled tuple',async t=>{
  autoFlag(t,'0');const f=local(t),policy=createWaVersionPolicy();f.fake.fetchLatestWaWebVersion=()=>assert.fail('No network');
  const session=await createBaileysChannelSession(f.context,{loadBaileys:async()=>f.fake,selectVersion:policy.select});t.after(()=>session.close());
  assert.deepEqual(f.configs[0].version,bundled);assert.equal(session.connectionHealth().web_version_source,'bundled_disabled');
});
test('managed lease expiry during version lookup cannot create a late socket or discard its valid auth',async t=>{
  autoFlag(t,'1');const f=local(t,{registered:true}),policy=createWaVersionPolicy(),gate=deferred();let fetched=false;
  f.fake.fetchLatestWaWebVersion=async()=>{fetched=true;return gate.promise;};
  const opening=createBaileysChannelSession(f.context,{loadBaileys:async()=>f.fake,selectVersion:policy.select});await flush();assert.equal(fetched,true);
  f.revoke();gate.resolve({isLatest:true,version:latest});await assert.rejects(opening,/lease expired|socket_replaced/);assert.equal(f.configs.length,0);
  assert.ok(fs.readdirSync(path.join(f.context.directory,'auth')).length>0,'Credentials remain available for the rightful owner');
});
