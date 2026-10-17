import assert from "node:assert/strict";
import {test} from "node:test";
import {forwardAssistant} from "../src/ai-forward.mjs";
const secret="synthetic-bridge-key-for-node-test-12345678";
const base="http://orion-assistente.railway.internal:8080/internal/qr/ai/reply";
test("forward private-only, sanitized message to Orion",async()=>{
  let calls=0;
  const answer=await forwardAssistant({
    apiUrl:base,token:secret,phone:"5567999999999",messageId:"M123",text:"Agendar corte",
    fetchImpl:async (url,opts)=>{
      calls++;
      assert.equal(url,base);
      assert.equal(opts.headers["X-Orion-Bridge-Key"],secret);
      assert.deepEqual(JSON.parse(opts.body),{
        phone:"5567999999999",message_id:"M123",text:"Agendar corte",
      });
      return {ok:true,json:async()=>({answer:"Horários reais disponíveis"})};
    },
  });
  assert.equal(answer,"Horários reais disponíveis");
  assert.equal(calls,1);
});
test("reject arbitrary endpoint, invalid phone, invalid message id",async()=>{
  for(const patch of [
    {apiUrl:"https://evil.example/internal/qr/ai/reply"},
    {apiUrl:"http://evil.example/internal/qr/ai/reply"},
    {apiUrl:base+"?token=bad"},
    {phone:"1234"},
    {messageId:"\nunsafe"},
  ])await assert.rejects(()=>forwardAssistant({
    apiUrl:base,token:secret,phone:"5567999999999",messageId:"M123",
    text:"oi",fetchImpl:async()=>{throw Error("should not be called")},...patch
  }));
});
test("disabled backend gives no outgoing messages",async()=>{
  const result=await forwardAssistant({
    apiUrl:base,token:secret,phone:"5567999999999",messageId:"M123",text:"oi",
    fetchImpl:async()=>({ok:true,json:async()=>({answer:null,step:"disabled"})}),
  });
  assert.equal(result,null);
});

test("ordered grouped WA IDs are forwarded once without inbox prefixes",async()=>{
  let calls=0;
  await forwardAssistant({apiUrl:base,token:secret,phone:"5567999999999",messageId:"AUDIO-1",
    messageIds:["AUDIO-1","TEXT-2"],text:"Quero cabelo\nNão, só barba",fetchImpl:async(_url,opts)=>{
      calls++;
      assert.deepEqual(JSON.parse(opts.body),{phone:"5567999999999",message_id:"AUDIO-1",
        message_ids:["AUDIO-1","TEXT-2"],text:"Quero cabelo\nNão, só barba"});
      return {ok:true,json:async()=>({answer:null})};
    }});
  assert.equal(calls,1);
});

test("incomplete grouped identities and excessive text fail before API; no partial truncation",async()=>{
  for(const patch of [{messageIds:[]},{messageIds:["OTHER","M123"]},
    {messageIds:["M123","M123"]},{messageIds:["M123",null]},
    {messageIds:["M123",...Array.from({length:6},(_,i)=>'M'+i)]},
    {text:'x'.repeat(1501)},
    {apiUrl:'http://user:password@orion-assistente.railway.internal/internal/qr/ai/reply'}]){
    await assert.rejects(()=>forwardAssistant({apiUrl:base,token:secret,phone:"5567999999999",messageId:"M123",
      text:"oi",fetchImpl:async()=>{throw Error("should not be called")},...patch}));
  }
});
