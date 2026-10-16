import test from "node:test";
import assert from "node:assert/strict";
import {chooseFreshDeviceLookup} from "../src/device-refresh.mjs";

test("manual diagnostic refresh is one-shot and defaults off",()=>{
  const config={reliableSendEnabled:true,globalDefault:false};
  assert.equal(chooseFreshDeviceLookup(false,config),false);
  assert.equal(chooseFreshDeviceLookup(true,config),true);
  assert.equal(chooseFreshDeviceLookup(false,config),false);
});
test("manual refresh rejects a transport that cannot honor the request",()=>{
  assert.throws(()=>chooseFreshDeviceLookup(true,{reliableSendEnabled:false}),
                /não habilitado/);
  assert.equal(chooseFreshDeviceLookup(false,{reliableSendEnabled:false}),false);
});
test("a global default remains supported but cannot override transport safety",()=>{
  assert.equal(chooseFreshDeviceLookup(false,{reliableSendEnabled:true,globalDefault:true}),true);
  assert.equal(chooseFreshDeviceLookup(false,{reliableSendEnabled:false,globalDefault:true}),false);
});
test("diagnostic request must be an actual boolean, not a string",()=>{
  for(const value of [null,undefined,"true","false",1,0,{}]){
    assert.throws(()=>chooseFreshDeviceLookup(value,{reliableSendEnabled:true}),/inválida/);
  }
});
