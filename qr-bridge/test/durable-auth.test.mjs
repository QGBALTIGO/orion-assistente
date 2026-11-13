import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {BufferJSON,initAuthCreds,proto} from '@whiskeysockets/baileys';
import {useDurableAuthState,decodeAuthRecord} from '../src/durable-auth.mjs';
import {backupAuth,readAuthBackup} from '../src/auth-backup.mjs';
const sdk={BufferJSON,initAuthCreds,proto},secret='synthetic-private-auth-key-'.repeat(3);
function fixture(t){const root=fs.mkdtempSync(path.join(os.tmpdir(),'orion-durable-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));return {root,dir:path.join(root,'auth')};}

test('fresh identity is durable before the first socket and every native key type survives reopen',async t=>{
  const {dir}=fixture(t),auth=useDurableAuthState(dir,{secret,sdk});
  auth.state.creds.registered=true;auth.state.creds.nextPreKeyId=87;auth.saveCreds();
  const values={'pre-key':{public:Buffer.alloc(32,1),private:Buffer.alloc(32,2)},session:Buffer.from('ratchet-state'),
    'sender-key':Buffer.from('sender-state'),'sender-key-memory':{'123@g.us':true},
    'app-state-sync-key':{keyData:Buffer.alloc(32,3)},'app-state-sync-version':{version:2,hash:Buffer.alloc(128),indexValueMap:{}}};
  for(const [type,value] of Object.entries(values))await auth.state.keys.set({[type]:{'fixture:1':value}});
  const reopened=useDurableAuthState(dir,{secret,sdk});
  assert.deepEqual(reopened.state.creds.signedIdentityKey,auth.state.creds.signedIdentityKey);
  assert.equal(reopened.state.creds.nextPreKeyId,87);assert.equal(reopened.state.creds.registered,true);
  for(const [type,value] of Object.entries(values)){
    const stored=(await reopened.state.keys.get(type,['fixture:1']))['fixture:1'];
    if(type==='app-state-sync-key')assert.deepEqual(Buffer.from(stored.keyData),value.keyData);
    else assert.deepEqual(JSON.parse(JSON.stringify(stored,BufferJSON.replacer)),JSON.parse(JSON.stringify(value,BufferJSON.replacer)));
  }
  for(const name of fs.readdirSync(dir)){
    const bytes=fs.readFileSync(path.join(dir,name),'utf8');
    assert.equal(JSON.parse(bytes).orion_auth_v,2);assert.doesNotMatch(bytes,/ratchet-state|sender-state|advSecretKey|signedIdentityKey/);
    if(process.platform!=='win32')assert.equal(fs.statSync(path.join(dir,name)).mode&0o777,0o600);
  }
});

test('legacy migration requires a verified backup and preserves the exact identity and ratchet',async t=>{
  const {root,dir}=fixture(t);fs.mkdirSync(dir);
  const creds=initAuthCreds();creds.registered=true;
  fs.writeFileSync(path.join(dir,'creds.json'),JSON.stringify(creds,BufferJSON.replacer));
  fs.writeFileSync(path.join(dir,'session-5567999999901.0.json'),JSON.stringify(Buffer.from('existing-ratchet'),BufferJSON.replacer));
  const before=fs.readFileSync(path.join(dir,'creds.json'));
  assert.throws(()=>useDurableAuthState(dir,{secret,sdk}),/auth_storage/);
  assert.deepEqual(fs.readFileSync(path.join(dir,'creds.json')),before);
  const backups=path.join(root,'backup');const proof=backupAuth({directory:dir,backupDirectory:backups,secret,label:'fixture-migration'});
  assert.equal(proof.state,'created_verified');
  const migrated=useDurableAuthState(dir,{secret,sdk,backupVerified:true});
  assert.deepEqual(migrated.state.creds,JSON.parse(JSON.stringify(creds,BufferJSON.replacer),BufferJSON.reviver));assert.deepEqual((await migrated.state.keys.get('session',['5567999999901.0']))['5567999999901.0'],Buffer.from('existing-ratchet'));
  const copy=readAuthBackup(path.join(backups,fs.readdirSync(backups)[0]),secret);
  assert.deepEqual(Buffer.from(copy.files.find(x=>x.name==='creds.json').data,'base64'),before);
  assert.equal(backupAuth({directory:dir,backupDirectory:backups,secret,label:'fixture-migration'}).state,'verified_existing');
  assert.equal(backupAuth({directory:dir,backupDirectory:backups,secret,label:'fixture-after'}).state,'created_verified');
});

test('wrong key, tampering or missing credentials never generate a replacement identity',t=>{
  for(const kind of ['wrong-key','tampered','missing']){
    const {dir}=fixture(t);const auth=useDurableAuthState(dir,{secret,sdk});auth.saveCreds();
    fs.writeFileSync(path.join(dir,'session-keep.json'),'{}');
    if(kind==='tampered'){const file=path.join(dir,'creds.json'),obj=JSON.parse(fs.readFileSync(file));obj.tag=Buffer.alloc(16).toString('base64');fs.writeFileSync(file,JSON.stringify(obj));}
    if(kind==='missing')fs.unlinkSync(path.join(dir,'creds.json'));
    let newIdentities=0;
    assert.throws(()=>useDurableAuthState(dir,{secret:kind==='wrong-key'?secret+'wrong':secret,sdk:{...sdk,initAuthCreds(){newIdentities++;return initAuthCreds();}}}),/auth_storage/);
    assert.equal(newIdentities,0);
  }
});

test('expired owner cannot read or update session keys and never changes the persisted identity',async t=>{
  const {dir}=fixture(t);let current=true,failures=0;
  const auth=useDurableAuthState(dir,{secret,sdk,isCurrent:()=>current,onFailure:()=>failures++});
  const before=fs.readFileSync(path.join(dir,'creds.json'));current=false;
  auth.state.creds.nextPreKeyId=900;
  assert.throws(auth.saveCreds,/auth_storage/);
  await assert.rejects(auth.state.keys.set({session:{stale:Buffer.from('stale')}}));
  await assert.rejects(auth.state.keys.get('session',['stale']));
  assert.deepEqual(fs.readFileSync(path.join(dir,'creds.json')),before);assert.ok(failures>0);
});

test('transaction journal replays an interrupted key batch instead of starting new auth',async t=>{
  const {dir}=fixture(t);const auth=useDurableAuthState(dir,{secret,sdk});
  // Fail the second target rename after the encrypted journal was committed.
  const rename=fs.renameSync;let inject=true;
  fs.renameSync=(from,to)=>{if(inject&&to.endsWith('session-second.json'))throw Error('simulated disk interruption');return rename(from,to);};
  try{await assert.rejects(auth.state.keys.set({session:{first:Buffer.from('one'),second:Buffer.from('two')}}));}
  finally{inject=false;fs.renameSync=rename;}
  const restored=useDurableAuthState(dir,{secret,sdk});
  assert.deepEqual(await restored.state.keys.get('session',['first','second']),{first:Buffer.from('one'),second:Buffer.from('two')});
  const journal=decodeAuthRecord(fs.readFileSync(path.join(dir,'orion-auth-transaction.json')),{directory:dir,secret,name:'orion-auth-transaction.json'});
  assert.equal(journal.value,null);assert.equal(fs.readdirSync(dir).some(x=>x.endsWith('.tmp')),false);
});

test('key deletion remains a tombstone and cannot revive an old prekey',async t=>{
  const {dir}=fixture(t);const auth=useDurableAuthState(dir,{secret,sdk});
  await auth.state.keys.set({'pre-key':{'12':{public:Buffer.alloc(32),private:Buffer.alloc(32)}}});
  await auth.state.keys.set({'pre-key':{'12':null}});
  assert.equal((await useDurableAuthState(dir,{secret,sdk}).state.keys.get('pre-key',['12']))['12'],null);
});

test('symlink credentials and unexpected auth paths fail closed',{skip:process.platform==='win32'?'Windows file symlink permission is unavailable in this test environment':false},async t=>{
  const {root,dir}=fixture(t);fs.mkdirSync(dir);const elsewhere=path.join(root,'original');fs.writeFileSync(elsewhere,'{}');
  fs.symlinkSync(elsewhere,path.join(dir,'creds.json'));
  assert.throws(()=>useDurableAuthState(dir,{secret,sdk}));assert.equal(fs.readFileSync(elsewhere,'utf8'),'{}');
});
