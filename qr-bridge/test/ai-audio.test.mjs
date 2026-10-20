import assert from "node:assert/strict";
import {test} from "node:test";
import {transcribeAssistantAudio,withAssistantAudio} from "../src/ai-audio.mjs";

test("mocked voice upload uses private backend, erases audio buffer",async()=>{
 const data=Buffer.from("audio-synthetic-test");
 let called=false;
 const text=await transcribeAssistantAudio({
   message:{audioMessage:{fileLength:data.length,seconds:1}}
 },{
   client:{updateMediaMessage:async()=>{}},logger:{},
   downloadMediaMessage:async()=>data,
   phone:"5567999999999",token:"private-token-123456789123456789123456",
   apiUrl:"http://orion-assistente.railway.internal:8080/internal/qr/ai/reply",
   fetchFn:async(url,req)=>{
     called=true;
     assert.match(url,/\/internal\/qr\/ai\/transcribe$/);
     assert.equal(req.headers["X-Orion-Phone"],"5567999999999");
     return {ok:true,json:async()=>({text:"Quero um corte"})}
   }
 });
 assert.equal(text,"Quero um corte");
 assert.equal(called,true);
 assert.deepEqual([...data],[...Buffer.alloc(data.length)]);
});

test("reject malformed phone and oversize audio without uploading",async()=>{
 let called=false;
 for(const invalid of [{phone:"552199755364",size:50},{phone:"5567999999999",size:2_000_001}]){
   const value=await transcribeAssistantAudio({message:{audioMessage:{fileLength:invalid.size,seconds:1}}},{
      client:{},logger:{},phone:invalid.phone,token:"test-123456789123456789123456789123456",
      apiUrl:"http://orion-assistente.railway.internal:8080/internal/qr/ai/reply",
      downloadMediaMessage:async()=>{throw Error("should not download")},
      fetchFn:async()=>{called=true;throw Error("should not send")}
   });
   assert.equal(value,null);
 }
 assert.equal(called,false);
});

const settings={client:{},logger:{},phone:'5567999999999',
  token:'test-123456789123456789123456789123456',
  apiUrl:'http://orion-assistente.railway.internal:8080/internal/qr/ai/reply'};
const voice=(patch={})=>({message:{audioMessage:{fileLength:20,seconds:1,...patch}}});

test('invalid duration and an already aborted intake never start a media download',async()=>{
  const controller=new AbortController();controller.abort();
  for(const patch of [{seconds:undefined},{seconds:0},{seconds:-1},{seconds:121},{seconds:NaN}]){
    assert.equal(await transcribeAssistantAudio(voice(patch),{...settings,
      downloadMediaMessage:async()=>assert.fail('must not download')}),null);
  }
  assert.equal(await transcribeAssistantAudio(voice(),{...settings,signal:controller.signal,
    downloadMediaMessage:async()=>assert.fail('must not download')}),null);
});

test('streaming byte cap aborts and erases every downloaded chunk before any upload',async()=>{
  const a=Buffer.alloc(1_000_001,1),b=Buffer.alloc(1_000_001,2);
  let destroyed=false,signal;
  const stream={async *[Symbol.asyncIterator](){yield a;yield b;},destroy(){destroyed=true;}};
  assert.equal(await transcribeAssistantAudio(voice(),{...settings,
    downloadMediaMessage:async(_message,kind,opts)=>{
      assert.equal(kind,'stream');signal=opts.options.signal;return stream;
    },fetchFn:async()=>assert.fail('must not upload oversized audio')}),null);
  assert.equal(signal.aborted,true);assert.equal(destroyed,true);
  assert.ok(a.every(value=>value===0));assert.ok(b.every(value=>value===0));
});

test('intake cancellation reaches download and upload and erases buffers on abort',async()=>{
  const controller=new AbortController(),data=Buffer.from('synthetic audio'),gate={};
  let downloadSignal;
  const running=transcribeAssistantAudio(voice(),{...settings,signal:controller.signal,
    downloadMediaMessage:async(_message,_kind,opts)=>{downloadSignal=opts.options.signal;return data;},
    fetchFn:async(_url,opts)=>{
      assert.equal(opts.signal,downloadSignal);
      gate.started=true;
      return new Promise((_resolve,reject)=>opts.signal.addEventListener('abort',()=>reject(Error('aborted')),{once:true}));
    }});
  await new Promise(resolve=>setImmediate(resolve));assert.equal(gate.started,true);
  controller.abort();await assert.rejects(running,/aborted/);
  assert.equal(downloadSignal.aborted,true);assert.ok(data.every(value=>value===0));
});

test('provider failure and excessive or truncated transcript erase audio without returning partial text',async()=>{
  for(const result of [{ok:false},{ok:true,text:'x'.repeat(1501)},{ok:true,text:'partial',truncated:true}]){
    const data=Buffer.from('synthetic audio');
    const value=await transcribeAssistantAudio(voice(),{...settings,downloadMediaMessage:async()=>data,
      fetchFn:async()=>({ok:result.ok,json:async()=>result})});
    assert.equal(value,null);assert.ok(data.every(byte=>byte===0));
  }
});

test('shared bounded audio lifecycle has no API routing and keeps actual supported MIME',async()=>{
  const data=Buffer.from('synthetic WebM'),calls=[];
  const result=await withAssistantAudio(voice({mimetype:'audio/webm; codecs=opus'}),{client:{},logger:{},downloadMediaMessage:async()=>data},async({buffer,mediaType})=>{assert.equal(buffer,data);calls.push(mediaType);return 'complete text';});
  assert.equal(result,'complete text');assert.deepEqual(calls,['audio/webm']);assert.ok(data.every(byte=>byte===0));
  assert.equal(await withAssistantAudio(voice({mimetype:'video/mp4'}),{downloadMediaMessage:async()=>assert.fail('must not download')},async()=>assert.fail('must not consume')),null);
});
