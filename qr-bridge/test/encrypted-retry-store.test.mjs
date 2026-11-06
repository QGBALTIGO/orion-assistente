import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {makeEncryptedRetryStore} from "../src/encrypted-retry-store.mjs";

process.env.ORION_RETRY_TEST_MODE="1";
function temp(){
 const d=fs.mkdtempSync(path.join(os.tmpdir(),"orion-retry-"));
 return {d,clean:()=>fs.rmSync(d,{recursive:true,force:true})};
}
const secret="test-only-secret-key-do-not-use-in-production-12345678";
test("stored messages remain encrypted at rest and recover after restart",()=>{
 const {d,clean}=temp();
 try{
  const store=makeEncryptedRetryStore({directory:d,secret});
  const message={conversation:"Confidential text",contextInfo:{stanzaId:"abc"}};
  assert.equal(store.save({id:"ABC-1"},message),true);
  const files=fs.readdirSync(d);
  assert.equal(files.length,1);
  const bytes=fs.readFileSync(path.join(d,files[0]),"utf8");
  assert.doesNotMatch(bytes,/Confidential text|ABC-1/);
  if(process.platform!=='win32')assert.equal(fs.statSync(path.join(d,files[0])).mode&0o777,0o600);
  const restarted=makeEncryptedRetryStore({directory:d,secret});
  assert.deepEqual(restarted.get({id:"ABC-1"}),message);
 }finally{clean()}
});
test("tampered retry cannot decrypt",()=>{
 const {d,clean}=temp();
 try{
  const store=makeEncryptedRetryStore({directory:d,secret});
  store.save({id:"one"},{conversation:"saved"});
  const file=path.join(d,fs.readdirSync(d)[0]);
  const v=JSON.parse(fs.readFileSync(file,"utf8"));
  v.ciphertext="AAAA";
  fs.writeFileSync(file,JSON.stringify(v));
  assert.equal(store.get({id:"one"}),undefined);
 }finally{clean()}
});
test("wrong encryption key cannot read other sessions",()=>{
 const {d,clean}=temp();
 try{
  const st=makeEncryptedRetryStore({directory:d,secret});
  st.save({id:"id"},{conversation:"msg"});
  const other=makeEncryptedRetryStore({directory:d,secret:secret+"rotated"});
  assert.equal(other.get({id:"id"}),undefined);
 }finally{clean()}
});
test("expired sent message is unavailable after restart",()=>{
 const {d,clean}=temp();
 try{
  let clock=1000;
  const opts={directory:d,secret,now:()=>clock,ttlMs:400};
  const st=makeEncryptedRetryStore(opts);st.save({id:"id"},{conversation:"msg"});
  clock=1400;assert.equal(makeEncryptedRetryStore(opts).get({id:"id"}),undefined);
 }finally{clean()}
});
test("encryption key and filename do not allow path traversal",()=>{
 const {d,clean}=temp();
 try{
  const st=makeEncryptedRetryStore({directory:d,secret});
  assert.equal(st.save({id:"../../../../passwd"},{conversation:"hi"}),true);
  assert.match(fs.readdirSync(d)[0],/^[a-f0-9]{64}\.enc$/);
  assert.deepEqual(st.get({id:"../../../../passwd"}),{conversation:"hi"});
 }finally{clean()}
});
test("bounded maximum and clear remove cached blobs",()=>{
 const {d,clean}=temp();
 try{
  const st=makeEncryptedRetryStore({directory:d,secret,max:3});
  for(let i=0;i<8;i++)assert.equal(st.save({id:"id-"+i},{conversation:"message-"+i}),i<3);
  assert.equal(fs.readdirSync(d).length,3);
  for(let i=0;i<3;i++)assert.deepEqual(st.get({id:'id-'+i}),{conversation:'message-'+i});
  assert.equal(st.diagnostics().denied_saves,5);
  st.clear();assert.deepEqual(fs.readdirSync(d),[]);
 }finally{clean()}
});
test("corrupt input and oversized payload are refused safely",()=>{
 const {d,clean}=temp();
 try{
  const st=makeEncryptedRetryStore({directory:d,secret});
  assert.equal(st.save({},{}),false);
  assert.equal(st.save({id:"good"},{conversation:"x".repeat(200000)}),false);
  assert.deepEqual(fs.readdirSync(d),[]);
 }finally{clean()}
});
test("larger native records refuse overflow without evicting unexpired accepted originals",()=>{
 const {d,clean}=temp();
 try{
  const st=makeEncryptedRetryStore({directory:d,secret,maxPayloadBytes:512*1024,maxTotalBytes:280000});
  for(let i=0;i<5;i++)assert.equal(st.save({id:'native-'+i},{conversation:'x'.repeat(100000)}),i<2);
  const files=fs.readdirSync(d);assert.ok(files.reduce((sum,file)=>sum+fs.statSync(path.join(d,file)).size,0)<=280000);
  assert.equal(files.length,2);assert.equal(st.get({id:'native-0'}).conversation.length,100000);
  assert.equal(st.get({id:'native-1'}).conversation.length,100000);
  assert.equal(st.get({id:'native-4'}),undefined);
  const tooSmall=makeEncryptedRetryStore({directory:d,secret,maxPayloadBytes:512*1024,maxTotalBytes:1024});
  assert.equal(tooSmall.save({id:'cannot-cache'},{conversation:'x'.repeat(100000)}),false);
  assert.equal(fs.readdirSync(d).length,2,'a smaller new budget cannot erase retained originals');
 }finally{clean();}
});

test('disk reserve refuses the new send while retained retries survive restart',()=>{
 const {d,clean}=temp();
 try{
  let free=1000000;const opts={directory:d,secret,minimumFreeBytes:500000,availableBytes:()=>free};
  const st=makeEncryptedRetryStore(opts);assert.equal(st.save({id:'accepted'},{conversation:'original'}),true);
  free=500000;assert.equal(st.save({id:'new'},{conversation:'new payload'}),false);
  assert.equal(st.diagnostics().error_code,'disk_reserve');
  assert.deepEqual(makeEncryptedRetryStore(opts).get({id:'accepted'}),{conversation:'original'});
  assert.equal(st.get({id:'new'}),undefined);assert.equal(fs.readdirSync(d).length,1);
 }finally{clean();}
});

test('only expiry releases a full retry slot; prune does not erase a valid original',()=>{
 const {d,clean}=temp();
 try{
  let clock=Date.now();const opts={directory:d,secret,max:1,ttlMs:1000,now:()=>clock};
  const st=makeEncryptedRetryStore(opts);assert.equal(st.save({id:'first'},{conversation:'retained'}),true);
  st.prune();assert.equal(st.save({id:'second'},{conversation:'later'}),false);
  assert.deepEqual(st.get({id:'first'}),{conversation:'retained'});
  clock+=5000;assert.equal(st.save({id:'second'},{conversation:'later'}),true);
  assert.equal(st.get({id:'first'}),undefined);assert.deepEqual(st.get({id:'second'}),{conversation:'later'});
 }finally{clean();}
});
