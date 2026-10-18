import assert from "node:assert/strict";
import {test} from "node:test";
import {forwardAssistant,registerAssistantIngress} from "../src/ai-forward.mjs";
const secret="synthetic-bridge-key-for-node-test-12345678";
const base="http://orion-assistente.railway.internal:8080/internal/qr/ai/reply";

test('private notice is scoped to the exact validated PN and has no text or business action',async()=>{
  let calls=0;
  const result=await registerAssistantIngress({apiUrl:base,token:secret,phone:'5567999999999',messageId:'NOTICE',
    fetchImpl:async(url,options)=>{calls++;assert.equal(new URL(url).pathname,'/internal/qr/ai/notice');
      assert.deepEqual(JSON.parse(options.body),{phone:'5567999999999',message_id:'NOTICE'});
      assert.equal(options.headers['X-Orion-Bridge-Key'],secret);
      return {ok:true,json:async()=>({revision:12})};}});
  assert.deepEqual(result,{revision:12});assert.equal(calls,1);
});

test('only an explicit zero-write API acknowledgment permits internal superseded carry',async()=>{
  for(const [body,expected] of [[{answer:null,step:'superseded',not_committed:true},{state:'superseded',not_committed:true}],
    [{answer:null,step:'superseded_after_write',not_committed:false},null],[{answer:null,step:'unknown'},null]]){
    const result=await forwardAssistant({apiUrl:base,token:secret,phone:'5567999999999',messageId:'ONE',
      text:'SIM',intakeRevision:4,fetchImpl:async(_url,options)=>{
        assert.equal(JSON.parse(options.body).intake_revision,4);return {ok:true,json:async()=>body};}});
    assert.deepEqual(result,expected);
  }
});
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
test('optional observed profile keeps its known source, while unusable metadata is omitted without blocking the message',async()=>{
  for(const [profile,expected] of [[{name:'  Joana  ',source:'contact_name'},{name:'Joana',source:'contact_name'}],
    [{name:'👩‍🔧',source:'push_name'},{name:'👩‍🔧',source:'push_name'}],
    [{name:'Cliente WhatsApp',source:'push_name'},undefined],[{name:'Joana',source:'guessed'},undefined],
    [{name:'5511999999999',source:'push_name'},undefined]]){
    await forwardAssistant({apiUrl:base,token:secret,phone:'5567999999999',messageId:'PROFILE',text:'Olá',profile,
      fetchImpl:async(_url,options)=>{
        const body=JSON.parse(options.body);assert.deepEqual(body.profile,expected);
        assert.equal(body.text,'Olá');return {ok:true,json:async()=>({answer:null})};
      }});
  }
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
