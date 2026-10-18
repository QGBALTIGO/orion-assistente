import assert from "node:assert/strict";
import {test} from "node:test";
import {forwardAssistant,registerAssistantIngress} from "../src/ai-forward.mjs";
const secret="synthetic-bridge-key-for-node-test-12345678";
const base="http://orion-assistente.railway.internal:8080/internal/qr/ai/reply";
const profileError=()=>({type:'extra_forbidden',loc:['body','profile'],msg:'Extra inputs are not permitted'});
const oldProfileResponse=()=>({ok:false,status:422,json:async()=>({detail:[profileError()]})});

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
test('only the exact old profile schema rejection permits one retry with the same IDs, revision and total timeout',async t=>{
  const timers=[],cleared=[],requests=[];let currentChecks=0;
  t.mock.method(globalThis,'setTimeout',(callback,ms)=>{const timer={callback,ms};timers.push(timer);return timer;});
  t.mock.method(globalThis,'clearTimeout',timer=>cleared.push(timer));
  const answer=await forwardAssistant({apiUrl:base,token:secret,phone:'5567999999999',messageId:'PROFILE-1',
    messageIds:['PROFILE-1','PROFILE-2'],text:'Resumo completo',intakeRevision:17,requireSummaryReview:true,
    profile:{name:'Joana',source:'push_name'},isCurrent:()=>{currentChecks++;return true;},
    fetchImpl:async(url,options)=>{
      requests.push({url,...options});
      return requests.length===1?oldProfileResponse():{ok:true,status:200,json:async()=>({answer:'Resumo pronto'})};
    }});
  assert.equal(answer,'Resumo pronto');assert.equal(requests.length,2);assert.equal(currentChecks,1);
  const first=JSON.parse(requests[0].body),second=JSON.parse(requests[1].body);
  assert.deepEqual(first.profile,{name:'Joana',source:'push_name'});delete first.profile;
  assert.deepEqual(second,first);
  assert.deepEqual(second,{phone:'5567999999999',message_id:'PROFILE-1',text:'Resumo completo',
    message_ids:['PROFILE-1','PROFILE-2'],intake_revision:17,require_summary_review:true});
  assert.equal(requests[0].url,requests[1].url);assert.equal(requests[1].url,base);
  assert.equal(requests[0].signal,requests[1].signal);assert.equal(requests[1].signal.aborted,false);
  assert.deepEqual(requests[0].headers,requests[1].headers);assert.equal(requests[1].method,'POST');
  assert.equal(timers.length,1);assert.equal(timers[0].ms,19000);assert.deepEqual(cleared,[timers[0]]);
});

test('a generic status, malformed rejection, different path or multiple validation errors never retries',async()=>{
  const responses=[
    ...[400,401,404,409,429,500,503].map(status=>({ok:false,status,json:async()=>({detail:[profileError()]})})),
    {ok:false,status:422,json:async()=>{throw Error('invalid JSON');}},
    ...[null,{},'invalid',{detail:'invalid'},{detail:[]},
      {detail:[{type:'value_error',loc:['body','profile']}]},
      {detail:[{type:'extra_forbidden',loc:['profile']}]},
      {detail:[{type:'extra_forbidden',loc:['query','profile']}]},
      {detail:[{type:'extra_forbidden',loc:['body','profile','name']}]},
      {detail:[{type:'extra_forbidden',loc:['body','text']}]},
      {detail:[profileError(),{type:'missing',loc:['body','text']}]},
      {detail:[profileError(),profileError()]}].map(body=>({ok:false,status:422,json:async()=>body}))
  ];
  for(const response of responses){
    let calls=0;
    await assert.rejects(()=>forwardAssistant({apiUrl:base,token:secret,phone:'5567999999999',messageId:'PROFILE',
      text:'Olá',profile:{name:'Joana',source:'push_name'},fetchImpl:async()=>{calls++;return response;}}),
      /assistant_unavailable/);
    assert.equal(calls,1);
  }
});

test('network/cancelled/uncertain responses and a rejection without an originally sent profile are not replayed',async()=>{
  for(const mode of ['network','cancelled','invalid_success_json','no_profile','unusable_profile']){
    let calls=0;
    await assert.rejects(()=>forwardAssistant({apiUrl:base,token:secret,phone:'5567999999999',messageId:'PROFILE',text:'Olá',
      profile:mode==='no_profile'?undefined:{name:mode==='unusable_profile'?'Cliente WhatsApp':'Joana',source:'push_name'},
      fetchImpl:async()=>{
        calls++;
        if(mode==='network')throw Error('synthetic connection reset');
        if(mode==='cancelled')throw Object.assign(Error('synthetic cancelled request'),{name:'AbortError'});
        if(mode==='invalid_success_json')return {ok:true,status:200,json:async()=>{throw Error('uncertain success body');}};
        return oldProfileResponse();
      }}));
    assert.equal(calls,1);
  }
});

test('a failure of the one compatibility retry never permits a third request',async()=>{
  for(const mode of ['profile_rejected_again','server_error','network','invalid_success_json']){
    let calls=0;
    await assert.rejects(()=>forwardAssistant({apiUrl:base,token:secret,phone:'5567999999999',messageId:'PROFILE',text:'Olá',
      profile:{name:'Joana',source:'push_name'},fetchImpl:async()=>{
        if(++calls===1)return oldProfileResponse();
        if(mode==='network')throw Error('synthetic connection reset');
        if(mode==='invalid_success_json')return {ok:true,status:200,json:async()=>{throw Error('uncertain success body');}};
        return mode==='profile_rejected_again'?oldProfileResponse():{ok:false,status:500};
      }}));
    assert.equal(calls,2);
  }
});

test('a stale generation/authorization or expired shared timeout prevents the compatibility retry',async t=>{
  const timers=[],cleared=[];
  t.mock.method(globalThis,'setTimeout',(callback,ms)=>{const timer={callback,ms};timers.push(timer);return timer;});
  t.mock.method(globalThis,'clearTimeout',timer=>cleared.push(timer));
  for(const mode of ['stale','expired']){
    let calls=0;
    await assert.rejects(()=>forwardAssistant({apiUrl:base,token:secret,phone:'5567999999999',messageId:'PROFILE',text:'Olá',
      profile:{name:'Joana',source:'push_name'},isCurrent:()=>mode!=='stale',fetchImpl:async()=>{
        calls++;if(mode==='expired')timers.at(-1).callback();return oldProfileResponse();
      }}),/assistant_unavailable/);
    assert.equal(calls,1);
  }
  assert.equal(timers.length,2);assert.deepEqual(cleared,timers);
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
