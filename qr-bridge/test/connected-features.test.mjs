import test from "node:test";
import assert from "node:assert/strict";
import {transcriberConfig,cleanPictureUrl,transcribeAudio,handleCallOffers} from "../src/connected-features.mjs";
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
test("incoming calls ignore allowlist, groups, repeat offers and unconfigured block",async()=>{
  let rejected=0, replied=0;
  const settings=validateSettings({callBlocking:true,callReply:true,allowedCallers:["5511999998888"]});
  const call=(from,id)=>({from,id,status:"offer"});
  const repeated=new Set();
  const opts={socket:{rejectCall:async()=>{rejected++}},
    settings,replySent:repeated,sendReply:async()=>{replied++}};
  await handleCallOffers([call("5511999998888@s.whatsapp.net","a"),call("5511999997777@s.whatsapp.net","b")],opts);
  await handleCallOffers([call("5511999997777@s.whatsapp.net","c"),
    {...call("5511999997777@s.whatsapp.net","d"),isGroup:true}],opts);
  assert.equal(rejected,2);
  assert.equal(replied,1);
  assert.deepEqual(await handleCallOffers([call("5511999997777@s.whatsapp.net","z")],
    {...opts,settings:{...settings,callBlocking:false}}),[]);
});
