import test from "node:test";
import assert from "node:assert/strict";
import {transcriberConfig,cleanPictureUrl,transcribeAudio,handleCallOffers,resolveCallPhone} from "../src/connected-features.mjs";
import {validateSettings} from "../src/connected-store.mjs";
test("private transcription backend requires validated Railway endpoint and secret",()=>{
  const e={QR_ASR_URL:"http://speech.railway.internal:8000/transcribe",QR_ASR_TOKEN:"long-token-for-a-private-service-000000"};
  assert.ok(transcriberConfig(e));
  for(const u of ["https://evil.test/transcribe","http://127.0.0.1:8000/transcribe",
    "http://speech.railway.internal.evil.test:8000/transcribe"]){
    assert.equal(transcriberConfig({...e,QR_ASR_URL:u}),null);
  }
});
test("profile URLs cannot be supplied by arbitrary hosts",()=>{
  assert.equal(cleanPictureUrl("https://cdn.fbcdn.net/example.jpg"),"https://cdn.fbcdn.net/example.jpg");
  assert.equal(cleanPictureUrl("https://fbcdn.net.evil.test/a"),null);
  assert.equal(cleanPictureUrl("javascript:alert(1)"),null);
});
test("audio transcription is opt in, bounded and never sent back automatically",async()=>{
  const config={url:"http://speech.railway.internal:8000/transcribe",key:"test"};
  let uploads=0;
  const message={message:{audioMessage:{fileLength:5}}};
  const result=await transcribeAudio(message,{
    client:{updateMediaMessage:async()=>{}},
    settings:{transcription:true,language:"pt"},config,
    downloadMediaMessage:async()=>Buffer.from("fake"),
    logger:{},fetchFn:async()=>{uploads++;return {ok:true,json:async()=>({text:"Olá"})}}
  });
  assert.equal(result,"Olá");assert.equal(uploads,1);
  assert.equal(await transcribeAudio(message,{settings:{transcription:false},config,
    downloadMediaMessage:async()=>{throw Error("must not download")}}),null);
});

test("incoming PN calls reject once, send to the number, and obey reply cooldown",async()=>{
  const settings=validateSettings({callBlocking:true,callReply:true,callReplyText:"Agora não podemos atender."});
  const replies=[],rejections=[],diagnostics={};
  const recentCalls=new Map(),replySent=new Map();
  let now=Date.parse("2026-10-17T18:00:00Z");
  const opts={
    socket:{rejectCall:async(id,from)=>{rejections.push([id,from])}},
    settings,diagnostics,recentCalls,replySent,now:()=>now,
    sendReply:async(phone,body)=>{
      replies.push([phone,body]);return {accepted:true,delivery:"pending"};
    }
  };
  const call=(id)=>({id,from:"5567999912345@s.whatsapp.net",status:"offer"});
  assert.deepEqual(await handleCallOffers([call("call-1")],opts),["rejected","replied"]);
  assert.deepEqual(await handleCallOffers([call("call-1")],opts),[]);
  assert.deepEqual(await handleCallOffers([call("call-2")],opts),["rejected"]);
  assert.deepEqual(replies,[["5567999912345","Agora não podemos atender."]]);
  assert.equal(rejections.length,2);
  assert.equal(diagnostics.duplicatesSkipped,1);
  assert.equal(diagnostics.replyCooldown,1);
  now+=16*60_000;
  assert.deepEqual(await handleCallOffers([call("call-3")],opts),["rejected","replied"]);
  assert.equal(replies.length,2);
});

test("incoming LID calls use the original LID for reject and mapped PN only for reply",async()=>{
  const jid="246991120506883@lid",pn="5567999912345@s.whatsapp.net";
  const rejects=[],replies=[],diagnostics={};
  const socket={rejectCall:async(id,from)=>rejects.push([id,from]),
    signalRepository:{lidMapping:{getPNForLID:async lid=>{
      assert.equal(lid,jid);return pn;
    }}}};
  const settings=validateSettings({callBlocking:true,callReply:true});
  const result=await handleCallOffers([{id:"lid1",from:jid,chatId:jid,status:"offer"}],{
    socket,settings,replySent:new Map(),diagnostics,
    sendReply:async(phone,msg)=>{replies.push([phone,msg]);return {accepted:true}}
  });
  assert.deepEqual(result,["rejected","replied"]);
  assert.deepEqual(rejects,[["lid1",jid]]);
  assert.equal(replies[0][0],"5567999912345");
  assert.equal(diagnostics.rejected,1);
  assert.equal(diagnostics.replied,1);
  assert.equal(await resolveCallPhone({from:jid,callerPn:pn},socket),"5567999912345");
});

test("no guessing LID numbers; allowlist protects unresolved identities",async()=>{
  const lid="246991120506883@lid",calls=[];
  const socket={rejectCall:async(id,from)=>calls.push([id,from])};
  const settings=validateSettings({callBlocking:true,callReply:true,
    allowedCallers:["5567999912345"]});
  const opts={socket,settings,diagnostics:{},replySent:new Map(),recentCalls:new Map(),
    sendReply:async()=>{throw Error("Should never reply")}};
  assert.deepEqual(await handleCallOffers([{status:"offer",id:"one",from:lid}],opts),["unresolved"]);
  assert.equal(calls.length,0);
  assert.equal(opts.diagnostics.unresolvedWithAllowlist,1);
  assert.deepEqual(await handleCallOffers([{status:"offer",id:"two",from:lid}],{
    ...opts,settings:{...settings,allowedCallers:[]}
  }),["rejected","reply_unresolved"]);
  assert.deepEqual(calls,[["two",lid]]);
});

test("allowlisted PN, groups and non-offer events are ignored",async()=>{
  const settings=validateSettings({callBlocking:true,callReply:true,
    allowedCallers:["5511999998888"]});
  const rejections=[],replies=[];
  const opts={socket:{rejectCall:async(id)=>rejections.push(id)},
    settings,replySent:new Map(),recentCalls:new Map(),
    sendReply:async(...args)=>{replies.push(args);return {accepted:true}}};
  await handleCallOffers([
    {from:"5511999998888@s.whatsapp.net",id:"allowed",status:"offer"},
    {from:"5511999997777@s.whatsapp.net",id:"group",status:"offer",isGroup:true},
    {from:"5511999997777@s.whatsapp.net",id:"ring",status:"ringing"},
    {from:"not-a-jid",id:"invalid",status:"offer"},
    {from:"5511999997777@s.whatsapp.net",id:"reject",status:"offer"}],opts);
  assert.deepEqual(rejections,["reject"]);
  assert.equal(replies.length,1);
  assert.deepEqual(await handleCallOffers([{from:"5511999997777@s.whatsapp.net",id:"other",status:"offer"}],
    {...opts,settings:{...settings,callBlocking:false}}),[]);
});

test("schedule respects Mato Grosso do Sul local hours",async()=>{
  const settings=validateSettings({callBlocking:true,callSchedule:true,
    callHours:{start:"09:00",end:"18:00",weekdays:[1,2,3,4,5]}});
  const offers=[];
  const opts={socket:{rejectCall:async(id)=>offers.push(id)},settings,
    replySent:new Map(),recentCalls:new Map(),
    now:()=>Date.parse("2026-10-19T16:00:00Z")};
  const from="5567999912345@s.whatsapp.net";
  assert.deepEqual(await handleCallOffers([{from,id:"within",status:"offer"}],opts),[]);
  opts.now=()=>Date.parse("2026-10-19T23:10:00Z");
  assert.deepEqual(await handleCallOffers([{from,id:"outside",status:"offer"}],opts),["rejected"]);
  assert.deepEqual(offers,["outside"]);
});

test("reply failure does not fake success or block a new call from retrying",async()=>{
  const settings=validateSettings({callBlocking:true,callReply:true});
  const diagnostics={},replySent=new Map(),recentCalls=new Map(),rejections=[];
  let sends=0;
  const opts={settings,replySent,recentCalls,diagnostics,
    socket:{rejectCall:async id=>rejections.push(id)},
    sendReply:async()=>{sends++;if(sends===1)return {accepted:false};
      return {accepted:true}}};
  const from="5567999912345@s.whatsapp.net";
  assert.deepEqual(await handleCallOffers([{from,id:"failed",status:"offer"}],opts),["rejected","reply_failed"]);
  assert.equal(diagnostics.replyFailed,1);
  assert.equal(replySent.size,0);
  assert.deepEqual(await handleCallOffers([{from,id:"retry",status:"offer"}],opts),["rejected","replied"]);
  assert.equal(diagnostics.replied,1);
  assert.equal(sends,2);
  assert.deepEqual(rejections,["failed","retry"]);
});

test("server rejection errors cannot be counted as successful replies",async()=>{
  const settings=validateSettings({callBlocking:true,callReply:true}),diagnostics={};
  let sent=false;
  const actions=await handleCallOffers([{
    from:"5567999912345@s.whatsapp.net",id:"fail",status:"offer"}],{
    settings,replySent:new Map(),recentCalls:new Map(),diagnostics,
    socket:{rejectCall:async()=>{throw Error("network failure")}},
    sendReply:async()=>{sent=true}
  });
  assert.deepEqual(actions,["reject_failed"]);
  assert.equal(sent,false);
  assert.equal(diagnostics.rejectFailed,1);
});

test("concurrent duplicate offers only reject and reply once",async()=>{
  const settings=validateSettings({callBlocking:true,callReply:true});
  let rejected=0,replied=0;
  const opts={settings,replySent:new Map(),recentCalls:new Map(),
    socket:{rejectCall:async()=>{rejected++;await new Promise(r=>setTimeout(r,5))}},
    sendReply:async()=>{replied++;return {accepted:true}}};
  const c={from:"5567999912345@s.whatsapp.net",id:"parallel",status:"offer"};
  await Promise.all([handleCallOffers([c],opts),handleCallOffers([c],opts)]);
  assert.equal(rejected,1);
  assert.equal(replied,1);
});
