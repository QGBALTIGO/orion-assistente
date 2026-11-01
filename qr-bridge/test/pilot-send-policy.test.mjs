import test from "node:test";
import assert from "node:assert/strict";
import {deliveryPolicy,requiresPreRelayCache} from "../src/pilot-send-policy.mjs";

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

test("all booking professional notices require cache before first relay even if global switch is off",()=>{
 for(const source of ['appointment']){
  assert.equal(requiresPreRelayCache({reliable:false,source}),true);
  assert.equal(requiresPreRelayCache({reliable:true,source}),true);
 }
 for(const source of ['manual','automation','assistant']){
  assert.equal(requiresPreRelayCache({reliable:false,source}),false);
  assert.equal(requiresPreRelayCache({reliable:true,source}),true);
 }
 assert.equal(requiresPreRelayCache(),false);
 assert.equal(requiresPreRelayCache({reliable:"true",source:"unknown"}),false);
});

test("all appointment cancellations and notices bypass stale device cache without broadening identity",()=>{
 for(const phone of ["5511999999999","595992000001"]){
   assert.deepEqual(deliveryPolicy(phone,{...env,QR_AI_ENABLED:"0"},
      {source:"appointment"}),{reliable:true,freshDevices:true,pilot:false});
 }
 assert.deepEqual(deliveryPolicy("5511999999999",{...env,QR_AI_ENABLED:"0"},
    {source:"manual"}),{reliable:false,freshDevices:false,pilot:false});
 assert.deepEqual(deliveryPolicy("5511999999999",{...env,QR_AI_ENABLED:"0"},
    {source:"automation"}),{reliable:false,freshDevices:false,pilot:false});
});
