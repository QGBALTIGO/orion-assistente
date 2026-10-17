import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {backupAuth,readAuthBackup} from '../src/auth-backup.mjs';
const secret='synthetic-backup-secret-012345678901234567890';
test('backup encrypts and verifies exact auth bytes without changing originals',()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'orion-backup-'));
  try{
    const directory=path.join(root,'auth'),backupDirectory=path.join(root,'backups');fs.mkdirSync(directory);
    const creds=JSON.stringify({noiseKey:{private:'SYNTHETIC_PRIVATE'},signedIdentityKey:{private:'SYNTHETIC_IDENTITY'}});
    fs.writeFileSync(path.join(directory,'creds.json'),creds);
    fs.writeFileSync(path.join(directory,'session-example.json'),'{"session":"SYNTHETIC_SESSION"}');
    const options={directory,backupDirectory,secret,label:'before-test'};
    assert.equal(backupAuth(options).state,'created_verified');
    const file=path.join(backupDirectory,fs.readdirSync(backupDirectory)[0]);
    assert.ok(!fs.readFileSync(file).includes(Buffer.from('SYNTHETIC_PRIVATE')));
    const saved=readAuthBackup(file,secret);
    assert.equal(saved.files.length,2);
    assert.equal(Buffer.from(saved.files.find(x=>x.name==='creds.json').data,'base64').toString(),creds);
    assert.equal(fs.readFileSync(path.join(directory,'creds.json'),'utf8'),creds);
    assert.equal(backupAuth(options).state,'verified_existing');
    assert.throws(()=>readAuthBackup(file,secret+'wrong'));
    fs.writeFileSync(path.join(directory,'creds.json'),'broken');
    assert.throws(()=>backupAuth(options));
  }finally{fs.rmSync(root,{recursive:true,force:true});}
});
