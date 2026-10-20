import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {createChannelResumeStore} from '../src/channel-resume.mjs';

test('restart intent is encrypted, survives reopen, and never treats a wrong key as a stop',t=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'orion-resume-'));fs.chmodSync(root,0o700);
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const secret='private-master-'.repeat(4),row={id:randomUUID(),tenant:randomUUID(),key:'private-channel-'.repeat(4)};
  const open=()=>createChannelResumeStore({root,secret});assert.deepEqual(open().list(),[]);
  open().remember(row);assert.deepEqual(open().list(),[row]);
  const file=path.join(root,'resume',fs.readdirSync(path.join(root,'resume'))[0]),bytes=fs.readFileSync(file);
  for(const value of Object.values(row))assert.equal(bytes.includes(Buffer.from(value)),false);
  open().forget(row.id,'unrelated-key');assert.deepEqual(open().list(),[row]);
  open().forget(row.id,row.key);assert.deepEqual(open().list(),[]);
});

test('corrupt or differently encrypted resume data fails closed without overwriting it',t=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'orion-resume-corrupt-'));fs.chmodSync(root,0o700);
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const row={id:randomUUID(),tenant:randomUUID(),key:'private-channel-'.repeat(4)};
  const store=createChannelResumeStore({root,secret:'a'.repeat(48)});store.remember(row);
  assert.throws(()=>createChannelResumeStore({root,secret:'b'.repeat(48)}).list());
  assert.deepEqual(store.list(),[row]);
  const file=path.join(root,'resume',fs.readdirSync(path.join(root,'resume'))[0]);fs.writeFileSync(file,'corrupt');
  assert.throws(()=>store.list());assert.throws(()=>store.remember(row));assert.equal(fs.readFileSync(file,'utf8'),'corrupt');
});
