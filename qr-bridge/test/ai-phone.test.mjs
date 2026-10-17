import assert from "node:assert/strict";
import {test} from "node:test";
import {forwardAssistant} from "../src/ai-forward.mjs";

const base="http://orion-assistente.railway.internal:8080/internal/qr/ai/reply";
const token="test-token-123456789123456789123456789123456";
test("invalid 8-digit number starting with 9 is refused before request",async()=>{
  await assert.rejects(()=>forwardAssistant({
    apiUrl:base,token,phone:"552199755364",messageId:"idA",text:"oi",
    fetchImpl:async()=>{throw Error("must not call")}
  }),/invalid_recipient/);
});
test("valid Brazilian nine-digit mobile can use private endpoint",async()=>{
  const result=await forwardAssistant({
    apiUrl:base,token,phone:"5521997553640",messageId:"idA",text:"oi",
    fetchImpl:async()=>({ok:true,json:async()=>({answer:"Olá"})})
  });
  assert.equal(result,"Olá");
});
