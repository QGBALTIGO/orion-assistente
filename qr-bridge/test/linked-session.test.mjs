import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {makeDurableInbox} from "../src/durable-inbox.mjs";
import {shouldIngestUpsert,timestampMs,ingestionHealth} from "../src/linked-session-health.mjs";

process.env.ORION_INBOX_TEST_MODE="1";
const secret="test-only-not-production-secret-key-000000";
const fresh=()=>{
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),"orion-linked-device-"));
  return {directory,cleanup:()=>fs.rmSync(directory,{recursive:true,force:true})};
};
const row=(id,at=Date.now())=>({id,phone:"5521997557364",
  direction:"in",body:"Olá, mesmo com meu celular offline",at});

test("inbox keeps encrypted messages through process restart, no phone involved",()=>{
  const t=fresh();try{
    const first=makeDurableInbox({directory:t.directory,secret});
    assert.deepEqual(first.load(),[]);
    assert.equal(first.save([row("wa:1")]),true);
    const bytes=fs.readFileSync(first.file,"utf8");
    assert.doesNotMatch(bytes,/5521997557364|celular offline|wa:1/);
    if(process.platform!=='win32')assert.equal(fs.statSync(first.file).mode&0o777,0o600);
    const afterRestart=makeDurableInbox({directory:t.directory,secret});
    assert.deepEqual(afterRestart.load(),[row("wa:1",afterRestart.load()[0].at)]);
    assert.equal(afterRestart.health(),"ready");
  }finally{t.cleanup()}
});
test("inbox accepts messages and delivery receipts without duplicates",()=>{
 const t=fresh();try{
   const box=makeDurableInbox({directory:t.directory,secret});
   const now=Date.now();
   assert.equal(box.save([row("wa:1",now),row("wa:1",now),
     {...row("out:2",now),direction:"out",delivery:"read"}]),true);
   assert.equal(box.load().length,2);
   assert.equal(box.load().find(x=>x.id==="out:2").delivery,"read");
 }finally{t.cleanup()}
});
test("stale messages expire and max retention is bounded",()=>{
 const t=fresh();try{
  const now=Date.now();
  const box=makeDurableInbox({directory:t.directory,secret,now:()=>now,limit:2});
  assert.equal(box.save([row("old",now-8*86400000),row("1",now),row("2",now),row("3",now)]),true);
  assert.deepEqual(box.load().map(x=>x.id),["2","3"]);
 }finally{t.cleanup()}
});
test("tampering prevents overwrite and never returns guessed plaintext",()=>{
 const t=fresh();try{
  const box=makeDurableInbox({directory:t.directory,secret});
  box.save([row("1")]);
  const raw=fs.readFileSync(box.file,"utf8");
  fs.writeFileSync(box.file,raw.replace(/"tag":"[^"]+"/,'"tag":"AAAA"'));
  const reopened=makeDurableInbox({directory:t.directory,secret});
  assert.deepEqual(reopened.load(),[]);
  assert.equal(reopened.health(),"unreadable");
  assert.equal(reopened.save([row("2")]),false);
 }finally{t.cleanup()}
});
test("revoked session can purge inbox independently",()=>{
 const t=fresh();try{
  const box=makeDurableInbox({directory:t.directory,secret});
  box.save([row("1")]);box.purge();
  assert.deepEqual(box.load(),[]);
 }finally{t.cleanup()}
});
test("no importing old append history, but recent backlog may be handled",()=>{
 const now=1750000000000;
 assert.equal(shouldIngestUpsert("notify",null,now),true);
 assert.equal(shouldIngestUpsert("append",now/1000,now),true);
 assert.equal(shouldIngestUpsert("append",now/1000-600,now),true);
 assert.equal(shouldIngestUpsert("append",now/1000-3600,now),false);
 assert.equal(shouldIngestUpsert("replace",now/1000,now),false);
 assert.equal(shouldIngestUpsert("append",null,now),false);
});
test("Baileys Protobuf timestamps convert without phone inspection",()=>{
 const now=1750000000;
 assert.equal(timestampMs({toNumber:()=>now}),now*1000);
 assert.equal(timestampMs(now*1000),now*1000);
 assert.equal(timestampMs(undefined),null);
});
test("connected socket is not enough to prove message ingestion",()=>{
 assert.equal(ingestionHealth({connected:false}),"disconnected");
 assert.equal(ingestionHealth({connected:true,openAt:1000,now:2000}),"initializing");
 assert.equal(ingestionHealth({connected:true,openAt:1000,now:50001}),"awaiting_initial_sync");
 assert.equal(ingestionHealth({connected:true,openAt:1000,now:50001,pendingNotifications:true}),"receiving");
 assert.equal(ingestionHealth({connected:true,openAt:1000,now:50001,hasCurrentMessages:true}),"receiving");
});

test("web panel closed and primary phone offline are not needed for socket storage",()=>{
  const t=fresh();
  try{
    const inbox=makeDurableInbox({directory:t.directory,secret});
    const record={...row("wa:offline"),body:"Oi"};
    assert.equal(inbox.save([record]),true);
    // Fully independent process: only mounted volume + bridge secret
    const freshProcess=makeDurableInbox({directory:t.directory,secret});
    assert.deepEqual(freshProcess.load(),[record]);
  }finally{t.cleanup()}
});
test("do not fetch/import chat history or auto reply on inbound callbacks",()=>{
  const src=fs.readFileSync(new URL("../src/server.mjs",import.meta.url),"utf8");
  const beginning=src.indexOf('async function receive({messages:incoming,type})');
  const ending=src.indexOf('client.ev.on("connection.update"',beginning);
  const handler=src.slice(beginning,ending);
  assert.ok(handler.includes('shouldIngestUpsert(type,message.messageTimestamp)'));
  assert.match(handler,/if\(message\.key\?\.fromMe\)\{[^}]*continue;/);
  assert.ok(!handler.includes('sendMessage('));
  assert.ok(!handler.includes('sendStoredText('));
  assert.ok(!handler.includes('relayMessage('));
  assert.match(src,/markOnlineOnConnect:false/);
  assert.match(src,/syncFullHistory:false/);
});

test("Baileys retry cache has bounded TTL without changing persistent auth",()=>{
  const server=fs.readFileSync(new URL("../src/server.mjs",import.meta.url),"utf8");
  assert.match(server,/msgRetryCounterCache=new NodeCache/);
  assert.match(server,/stdTTL:600,checkperiod:120,useClones:false/);
  assert.match(server,/maxMsgRetryCount:5/);
  assert.match(server,/keys:makeCacheableSignalKeyStore\(state.keys,logger\)/);
  assert.match(server,/getMessage:async key=>/);
  assert.doesNotMatch(server,/assertSessions\(\s*\[/);
});
