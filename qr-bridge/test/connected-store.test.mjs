import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {makeConnectedStore,validateSettings,shouldRejectCall} from "../src/connected-store.mjs";
test("connected counts are real, deduplicated, encrypted and scoped to account",()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),"orion-control-"));
  try{
    const params={directory:dir,secret:"some-long-test-secret-key-with-32-plus-bytes"};
    const a=makeConnectedStore(params);
    assert.equal(a.stats().available,false);
    a.setIdentity("5511999998888:7@s.whatsapp.net");
    const ts=Date.now();
    assert.equal(a.record("wa:A",ts),true);
    assert.equal(a.record("wa:A",ts),false);
    assert.equal(a.record("out:B",ts),true);
    assert.equal(a.stats().today,2);
    assert.equal(a.stats().last7Days,2);
    assert.equal(a.stats().total,2);
    assert.equal(a.stats().scope,"since_tracking_enabled");
    const content=fs.readFileSync(path.join(dir,"connected-control.enc"),"utf8");
    assert.ok(!content.includes("5511999998888")&&!content.includes("wa:A"));
    const reloaded=makeConnectedStore(params);
    reloaded.setIdentity("5511999998888:7@s.whatsapp.net");
    assert.equal(reloaded.stats().total,2);
    reloaded.setIdentity("5521999997777@s.whatsapp.net");
    assert.equal(reloaded.stats().total,0);
  }finally{fs.rmSync(dir,{recursive:true,force:true})}
});
test("settings are default-off and reject capability spoofing",()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),"orion-settings-"));
  try{
    const p={directory:dir,secret:"another-long-test-secret-key-with-32-bytes"};
    const store=makeConnectedStore(p);store.setIdentity("5511888888888@s.whatsapp.net");
    assert.equal(store.settings().callBlocking,false);
    assert.throws(()=>store.update({...store.settings(),transcription:true},{transcription:false}),/não configurado/);
    assert.throws(()=>store.update({...store.settings(),chatbot:true},{chatbot:false}),/não integrado/);
    assert.throws(()=>validateSettings({other:true}),/não autorizado/);
    assert.throws(()=>validateSettings({allowedCallers:["invalid"]}),/inválida/);
    const settings=store.update({...store.settings(),callBlocking:true,allowedCallers:["5511999998888"]},{});
    assert.equal(settings.callBlocking,true);
    assert.equal(makeConnectedStore(p).settings().callBlocking,true);
  }finally{fs.rmSync(dir,{recursive:true,force:true})}
});
test("call blocking honors allowlist and local receiving windows",()=>{
  const s=validateSettings({callBlocking:true,allowedCallers:["5511999998888"]});
  assert.equal(shouldRejectCall(s,"5511999998888"),false);
  assert.equal(shouldRejectCall(s,"5511999997777"),true);
  s.callSchedule=true;
  s.callHours={start:"09:00",end:"18:00",weekdays:[1,2,3,4,5]};
  // 2026-10-16 15:00 UTC is 11:00 in Mato Grosso do Sul
  assert.equal(shouldRejectCall(s,"5511999997777",new Date("2026-10-16T15:00:00Z")),false);
  assert.equal(shouldRejectCall(s,"5511999997777",new Date("2026-10-17T02:00:00Z")),true);
});
