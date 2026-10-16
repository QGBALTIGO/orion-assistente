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
  assert.equal(fs.statSync(path.join(d,files[0])).mode&0o777,0o600);
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
  for(let i=0;i<8;i++)st.save({id:"id-"+i},{conversation:"message-"+i});
  assert.ok(fs.readdirSync(d).length<=3);
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
