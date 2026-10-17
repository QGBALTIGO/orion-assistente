import test from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import {syncBuiltinESMExports} from 'node:module';
import {databaseBackup} from '../src/database-backup-store.mjs';

const MAGIC=Buffer.from('ORIONDB1');
const SECRET='synthetic-only-database-backup-secret-0123456789012345';
const MAX=32*1024*1024,QUOTA=64*1024*1024;
const sha=body=>crypto.createHash('sha256').update(body).digest('hex');
function encrypted(id,{secret=SECRET,plain=Buffer.from('PGDMP\u0001synthetic database dump\n'),nonce=crypto.randomBytes(12)}={}){
  const key=Buffer.from(crypto.hkdfSync('sha256',Buffer.from(secret),Buffer.alloc(0),Buffer.from('orion:database-backup:v1'),32));
  try{
    const cipher=crypto.createCipheriv('aes-256-gcm',key,nonce);
    cipher.setAAD(Buffer.concat([MAGIC,Buffer.from(id,'ascii')]));
    return Buffer.concat([MAGIC,nonce,cipher.update(plain),cipher.final(),cipher.getAuthTag()]);
  }finally{key.fill(0)}
}

// Run the actual server's authorization and private-route dispatch, omitting
// unrelated WhatsApp startup and replacing only its fixed volume with a temp dir.
const serverSource=fs.readFileSync(new URL('../src/server.mjs',import.meta.url),'utf8');
function productionHandler({root,secret,enabled,onBackup}){
  const begin='const server=http.createServer(async(req,res)=>{';
  const start=serverSource.indexOf(begin),end=serverSource.indexOf('    if(path==="/internal/status"',start);
  assert.ok(start>=0&&end>start,'private route must remain in the production request handler');
  const helpers=serverSource.slice(serverSource.indexOf('function auth(req){'),serverSource.indexOf('function collectBody(req){'));
  const prefix=serverSource.slice(start+begin.length,end).replace("root:'/data/orion-db-backups'",'root');
  assert.ok(prefix.includes('root,id:'),'the test must replace the production volume, never write to /data');
  const context={Buffer,URL,KEY:secret,root,timingSafeEqual:crypto.timingSafeEqual,
    process:{env:{QR_DATABASE_BACKUP_ENABLED:enabled?'1':'0'}},
    databaseBackup:(req,res,options)=>{onBackup?.(options);return databaseBackup(req,res,options)}};
  vm.runInNewContext(helpers+'\nglobalThis.handler=async(req,res)=>{'+prefix+
    'return reply(res,404,{error:"not_found"});}catch(error){return reply(res,503,{error:"refused"});}};',context);
  return context.handler;
}

async function fixture(t,{enabled=true,secret=SECRET,rootLink=false}={}){
  const base=fs.mkdtempSync(path.join(os.tmpdir(),'orion-db-store-test-'));
  const root=path.join(base,'backups'),outside=path.join(base,'outside');
  fs.mkdirSync(outside);
  if(rootLink)fs.symlinkSync(outside,root,process.platform==='win32'?'junction':'dir');
  const admissions=[];
  const server=http.createServer(productionHandler({root,secret,enabled,onBackup:options=>admissions.push(options)}));
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(async()=>{
    server.closeAllConnections?.();
    await new Promise(resolve=>server.close(resolve));
    // Delete only this test's freshly created absolute directory.
    assert.equal(path.dirname(path.resolve(base)),path.resolve(os.tmpdir()));
    assert.ok(path.basename(base).startsWith('orion-db-store-test-'));
    fs.rmSync(base,{recursive:true,force:true});
  });
  async function request(id,{method='GET',body,headers={},auth=secret,chunks}={}){
    return await new Promise((resolve,reject)=>{
      const req=http.request({host:'127.0.0.1',port:server.address().port,agent:false,
        path:'/internal/ops/database-backups/'+id,method,
        headers:{...(auth===null?{}:{'x-orion-bridge-key':auth}),...headers}},res=>{
        const data=[];
        res.on('data',chunk=>data.push(chunk));
        res.on('end',()=>resolve({status:res.statusCode,headers:res.headers,body:Buffer.concat(data)}));
      });
      req.setTimeout(10000,()=>req.destroy(Error('local backup test timed out')));
      req.on('error',reject);
      if(chunks)for(const chunk of chunks)req.write(chunk);
      req.end(body);
    });
  }
  const post=(id,body,options={})=>request(id,{method:'POST',body,
    ...options,headers:{'content-type':'application/octet-stream','x-orion-sha256':sha(body),...options.headers}});
  return {base,root,outside,request,post,admissions};
}

test('backup HTTP stays disabled without opt-in; production authorization runs before storage',async t=>{
  const h=await fixture(t,{enabled:false}),id=crypto.randomUUID(),body=encrypted(id);
  assert.equal((await h.post(id,body,{auth:null})).status,401);
  assert.equal((await h.post(id,body,{auth:'wrong-but-synthetic-secret'})).status,401);
  assert.equal(h.admissions.length,0);
  assert.equal((await h.post(id,body)).status,423);
  assert.equal(h.admissions.length,1);assert.equal(fs.existsSync(h.root),false);
});

test('authenticated encrypted PGDMP backup round-trips exact bytes with digest and no-store',async t=>{
  const h=await fixture(t),id=crypto.randomUUID(),body=encrypted(id);
  const uploaded=await h.post(id,body);
  assert.equal(uploaded.status,201,uploaded.body.toString());
  assert.deepEqual(JSON.parse(uploaded.body),{id,sha256:sha(body),size:body.length});
  const downloaded=await h.request(id);
  assert.equal(downloaded.status,200);assert.deepEqual(downloaded.body,body);
  assert.equal(downloaded.headers['x-orion-sha256'],sha(body));
  assert.equal(downloaded.headers['content-type'],'application/octet-stream');
  assert.equal(downloaded.headers['cache-control'],'no-store');
  assert.equal(Number(downloaded.headers['content-length']),body.length);
  assert.deepEqual(fs.readdirSync(h.root),[id+'.bin']);
  assert.ok(!body.includes(Buffer.from('PGDMP')));
});

test('only canonical UUID4 IDs are admitted and traversal cannot create a file',async t=>{
  const h=await fixture(t),valid=crypto.randomUUID(),body=encrypted(valid);
  for(const id of ['..%2foutside%2fstolen','%2e%2e%2foutside','../../outside',valid+'/',valid+'.bin',
    valid.toUpperCase(),'11111111-1111-1111-8111-111111111111','',valid+'%00']){
    const result=await h.post(id,body);
    assert.ok([400,404].includes(result.status),id+': '+result.status);
  }
  assert.deepEqual(fs.readdirSync(h.outside),[]);
  assert.equal(fs.existsSync(h.root),false);
});

test('method, MIME, checksum and missing files have bounded non-sensitive errors',async t=>{
  const h=await fixture(t),id=crypto.randomUUID(),body=encrypted(id);
  assert.equal((await h.request(id,{method:'DELETE'})).status,405);
  assert.equal((await h.request(id)).status,404);
  assert.equal((await h.post(id,body,{headers:{'content-type':'application/json'}})).status,415);
  for(const digest of ['',sha(body).toUpperCase(),'x'.repeat(64)])
    assert.equal((await h.post(id,body,{headers:{'x-orion-sha256':digest}})).status,400);
  const mismatch=await h.post(id,body,{headers:{'x-orion-sha256':'0'.repeat(64)}});
  assert.equal(mismatch.status,422);assert.equal(mismatch.headers['cache-control'],'no-store');
  assert.deepEqual(JSON.parse(mismatch.body),{error:'database_backup_refused'});
  assert.deepEqual(fs.readdirSync(h.root),[]);
});

test('bad secret, magic, authentication tag, swapped UUID and non-PGDMP plaintext fail before creation',async t=>{
  const h=await fixture(t),id=crypto.randomUUID(),body=encrypted(id);
  const badMagic=Buffer.from(body);badMagic[0]^=1;
  const badTag=Buffer.from(body);badTag[badTag.length-1]^=1;
  const variants=[badMagic,badTag,body.subarray(0,30),encrypted(id,{secret:SECRET+'wrong'}),
    encrypted(crypto.randomUUID()),encrypted(id,{plain:Buffer.from('plain SQL instead of a custom PG dump')})];
  for(const invalid of variants)assert.equal((await h.post(id,invalid)).status,422);
  assert.deepEqual(fs.readdirSync(h.root),[]);
  const other=await fixture(t,{secret:'short'});
  assert.equal((await other.post(id,body)).status,503,'a misconfigured local key must fail closed');
  assert.deepEqual(fs.readdirSync(other.root),[]);
});

test('POST cannot overwrite an existing backup; concurrent same-ID uploads have one winner',async t=>{
  const h=await fixture(t),id=crypto.randomUUID(),a=encrypted(id),b=encrypted(id);
  const results=await Promise.all([h.post(id,a),h.post(id,b)]);
  assert.deepEqual(results.map(x=>x.status).sort(),[201,409]);
  const winner=results[0].status===201?a:b;
  assert.deepEqual((await h.request(id)).body,winner);
  assert.equal((await h.post(id,encrypted(id))).status,409);
  assert.deepEqual(fs.readFileSync(path.join(h.root,id+'.bin')),winner);
});

test('declared and streamed byte limits reject before writing',async t=>{
  const h=await fixture(t),id=crypto.randomUUID();
  const headers={'content-type':'application/octet-stream','x-orion-sha256':'0'.repeat(64),'content-length':String(MAX+1)};
  assert.equal((await h.request(id,{method:'POST',headers})).status,413);
  const chunks=Array(32).fill(Buffer.alloc(1024*1024));chunks.push(Buffer.from('x'));
  const streamed=await h.request(id,{method:'POST',headers:{'content-type':'application/octet-stream','x-orion-sha256':'0'.repeat(64)},chunks});
  assert.equal(streamed.status,413,JSON.stringify({headers:streamed.headers,body:streamed.body.toString()}));
  assert.deepEqual(fs.readdirSync(h.root),[]);
});

test('quota admission is serialized across concurrent different-ID uploads',async t=>{
  const h=await fixture(t),first=crypto.randomUUID(),second=crypto.randomUUID();
  const a=encrypted(first,{plain:Buffer.concat([Buffer.from('PGDMP'),Buffer.alloc(2000)])}),b=encrypted(second,{plain:Buffer.concat([Buffer.from('PGDMP'),Buffer.alloc(2000)])});
  fs.mkdirSync(h.root);const filler=path.join(h.root,'existing-synthetic-file');fs.writeFileSync(filler,'');fs.truncateSync(filler,QUOTA-3000);
  const results=await Promise.all([h.post(first,a),h.post(second,b)]);
  assert.deepEqual(results.map(x=>x.status).sort(),[201,507]);
  const used=fs.readdirSync(h.root).reduce((sum,name)=>sum+fs.statSync(path.join(h.root,name)).size,0);
  assert.ok(used<=QUOTA);assert.equal(fs.readdirSync(h.root).length,2);
});

test('disk-space refusal and corrupted stored envelopes never return a successful backup',async t=>{
  const h=await fixture(t),id=crypto.randomUUID(),body=encrypted(id),statfs=fs.statfsSync;
  fs.statfsSync=()=>({bavail:0,bsize:4096});
  try{assert.equal((await h.post(id,body)).status,507)}finally{fs.statfsSync=statfs}
  assert.deepEqual(fs.readdirSync(h.root),[]);
  assert.equal((await h.post(id,body)).status,201);
  const corrupted=Buffer.from(body);corrupted[corrupted.length-1]^=1;fs.writeFileSync(path.join(h.root,id+'.bin'),corrupted);
  const download=await h.request(id);assert.equal(download.status,422);
  assert.deepEqual(JSON.parse(download.body),{error:'database_backup_refused'});
});

test('failed new-file write removes only the incomplete creation and never deletes an existing backup',async t=>{
  const h=await fixture(t),savedId=crypto.randomUUID(),saved=encrypted(savedId),newId=crypto.randomUUID(),body=encrypted(newId);
  assert.equal((await h.post(savedId,saved)).status,201);
  const write=fs.writeFileSync;
  let injected=false;
  fs.writeFileSync=(target,data,...args)=>{
    if(typeof target==='number'&&!injected){
      injected=true;write(target,data.subarray(0,15));
      const error=Error('synthetic local disk failure');error.code='EIO';throw error;
    }
    return write(target,data,...args);
  };
  try{assert.equal((await h.post(newId,body)).status,503)}finally{fs.writeFileSync=write}
  assert.equal(injected,true);assert.equal(fs.existsSync(path.join(h.root,newId+'.bin')),false,'partial new file must not block recovery');
  assert.deepEqual(fs.readFileSync(path.join(h.root,savedId+'.bin')),saved);
  assert.equal((await h.post(savedId,body)).status,422,'a rejected envelope cannot delete or modify the existing file');
  assert.deepEqual(fs.readFileSync(path.join(h.root,savedId+'.bin')),saved);
});

test('POSIX directory and new files have private modes',{skip:process.platform==='win32'?'Windows ACLs do not expose POSIX permission modes':false},async t=>{
  const h=await fixture(t),id=crypto.randomUUID();assert.equal((await h.post(id,encrypted(id))).status,201);
  assert.equal(fs.statSync(h.root).mode&0o777,0o700);assert.equal(fs.statSync(path.join(h.root,id+'.bin')).mode&0o777,0o600);
});

test('a symbolic root is refused without touching its target',async t=>{
  const h=await fixture(t,{rootLink:true}),id=crypto.randomUUID();
  assert.equal((await h.post(id,encrypted(id))).status,409);
  assert.deepEqual(fs.readdirSync(h.outside),[]);
});

test('symbolic backup entries cannot be read or replaced',async t=>{
  const h=await fixture(t),id=crypto.randomUUID(),body=encrypted(id),outside=path.join(h.outside,'private.bin');
  fs.mkdirSync(h.root);fs.writeFileSync(outside,body);
  try{fs.symlinkSync(outside,path.join(h.root,id+'.bin'),'file')}
  catch(error){if(['EPERM','EACCES'].includes(error.code)){t.skip('file symlink creation requires privileges on this Windows host');return}throw error}
  assert.notEqual((await h.request(id)).status,200,'GET must not follow a file outside the private root');
  assert.equal((await h.post(id,encrypted(id))).status,409);
  assert.deepEqual(fs.readFileSync(outside),body);
});

test('every decrypted chunk and derived key is wiped on success and on failed authentication',async t=>{
  const h=await fixture(t),create=crypto.createDecipheriv,concat=Buffer.concat;
  const keys=[],chunks=[],plaintext=[];
  crypto.createDecipheriv=(algorithm,key,...args)=>{
    keys.push(key);
    const cipher=create(algorithm,key,...args),update=cipher.update.bind(cipher),final=cipher.final.bind(cipher);
    cipher.update=(...input)=>{const result=update(...input);chunks.push(result);return result};
    cipher.final=(...input)=>{const result=final(...input);chunks.push(result);return result};
    return cipher;
  };
  Buffer.concat=function(list,...args){const result=concat.call(Buffer,list,...args);if(list.some(x=>chunks.includes(x)))plaintext.push(result);return result};
  syncBuiltinESMExports();
  try{
    const id=crypto.randomUUID(),good=encrypted(id);
    assert.equal((await h.post(id,good)).status,201);
    const badId=crypto.randomUUID(),bad=encrypted(badId);bad[bad.length-1]^=1;
    assert.equal((await h.post(badId,bad)).status,422);
    assert.ok(chunks.length>=3&&plaintext.length>=1&&keys.length===2);
    for(const value of [...keys,...chunks,...plaintext])assert.ok(value.every(byte=>byte===0),'decrypted bytes or derived key survived the request');
  }finally{crypto.createDecipheriv=create;Buffer.concat=concat;syncBuiltinESMExports()}
});
