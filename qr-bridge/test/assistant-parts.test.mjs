import test from 'node:test';import assert from 'node:assert/strict';
import {assistantParts} from '../src/assistant-parts.mjs';
import {forwardAssistant} from '../src/ai-forward.mjs';
test('per-message bodies retain their exact ordered IDs and complete joined request',()=>{
  const parts=[{message_id:'A',text:'primeira'},{message_id:'B',text:'segunda'}];
  assert.deepEqual(assistantParts(parts,['A','B'],'primeira\nsegunda'),parts);
  for(const value of [[parts[1],parts[0]],[parts[0]],null,[{...parts[0],tenant_id:'forged'},parts[1]],[parts[0],{...parts[1],text:'changed'}]])
    assert.throws(()=>assistantParts(value,['A','B'],'primeira\nsegunda'),/invalid_message_parts/);
});
test('legacy forward sends the exact parts once; invalid parts never make an API call',async()=>{
  const requests=[],parts=[{message_id:'A',text:'primeira'},{message_id:'B',text:'segunda'}];
  const options={apiUrl:'http://api.railway.internal/internal/qr/ai/reply',token:'synthetic-secret-'.repeat(4),phone:'5567999999901',
    messageId:'A',messageIds:['A','B'],text:'primeira\nsegunda',parts,
    fetchImpl:async(url,request)=>{requests.push(JSON.parse(request.body));return {ok:true,json:async()=>({answer:null})};}};
  await forwardAssistant(options);assert.equal(requests.length,1);assert.deepEqual(requests[0].parts,parts);
  await assert.rejects(forwardAssistant({...options,parts:[parts[0]]}),/invalid_message_parts/);assert.equal(requests.length,1);
});
