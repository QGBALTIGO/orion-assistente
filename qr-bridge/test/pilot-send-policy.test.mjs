import test from "node:test";
import assert from "node:assert/strict";
import {deliveryPolicy} from "../src/pilot-send-policy.mjs";

const env={QR_AI_ENABLED:"1", QR_AI_PILOT_NUMBER:"5567999999999",
  QR_RELIABLE_SEND_V2:"0", QR_FORCE_FRESH_DEVICES:"0"};
test("approved single pilot uses pre-cached retry and fresh recipient devices",()=>{
 const policy=deliveryPolicy("5567999999999",env);
 assert.deepEqual(policy,{reliable:true,freshDevices:true,pilot:true});
});
test("other clients retain the existing sending strategy",()=>{
 assert.deepEqual(deliveryPolicy("5511999999999",env),
   {reliable:false,freshDevices:false,pilot:false});
});
test("disabled or invalid pilot never enables pilot delivery mode",()=>{
 for(const change of [
   {QR_AI_ENABLED:"0"},{QR_AI_PILOT_NUMBER:"552199755364"},
   {QR_AI_PILOT_NUMBER:""},{QR_AI_PILOT_NUMBER:"55UNKNOWN"}
 ])assert.equal(deliveryPolicy("5567999999999",{...env,...change}).pilot,false);
});
test("existing global flags still work without pilot",()=>{
 assert.deepEqual(deliveryPolicy("5511999999999",{
   QR_AI_ENABLED:"0",QR_AI_PILOT_NUMBER:"",
   QR_RELIABLE_SEND_V2:"1",QR_FORCE_FRESH_DEVICES:"1"
 }),{reliable:true,freshDevices:true,pilot:false});
});
