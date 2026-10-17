import assert from "node:assert/strict";
import {test} from "node:test";
import {transcribeAssistantAudio} from "../src/ai-audio.mjs";

test("mocked voice upload uses private backend, erases audio buffer",async()=>{
 const data=Buffer.from("audio-synthetic-test");
 let called=false;
 const text=await transcribeAssistantAudio({
   message:{audioMessage:{fileLength:data.length}}
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
   const value=await transcribeAssistantAudio({message:{audioMessage:{fileLength:invalid.size}}},{
      client:{},logger:{},phone:invalid.phone,token:"test-123456789123456789123456789123456",
      apiUrl:"http://orion-assistente.railway.internal:8080/internal/qr/ai/reply",
      downloadMediaMessage:async()=>{throw Error("should not download")},
      fetchFn:async()=>{called=true;throw Error("should not send")}
   });
   assert.equal(value,null);
 }
 assert.equal(called,false);
});
