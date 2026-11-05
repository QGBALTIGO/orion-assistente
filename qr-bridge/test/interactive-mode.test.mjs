import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {chatbotInteractiveMode} from '../src/interactive-mode.mjs';
const tenantId=randomUUID(),deviceId=randomUUID(),scope=tenantId+':'+deviceId;
const mode=(type,raw,extra={})=>chatbotInteractiveMode({type},{tenantId,deviceId,env:{QR_NATIVE_LIST_SCOPES:raw},...extra});

test('an exact tenant/device scope opts in only lists, preserving fallback for every other protocol',()=>{
  assert.equal(mode('list',scope),'native');
  for(const type of ['carousel','text','image','sticker','audio','document','location',undefined])assert.equal(mode(type,scope),'text_fallback');
  assert.equal(mode('list',' '+randomUUID()+':'+randomUUID()+', '+scope+' '),'native');
});
test('another tenant, another device and missing trusted context never inherit the native-list pilot',()=>{
  for(const extra of [{tenantId:randomUUID()},{deviceId:randomUUID()},{tenantId:null},{deviceId:null}])assert.equal(mode('list',scope,extra),'text_fallback');
});
test('malformed, wildcard, partial, excessive or empty configuration fails closed as a whole',()=>{
  for(const raw of [undefined,'','*',deviceId,tenantId+':*',scope+',',scope+',bad',scope.toUpperCase(),scope+':extra',
    Array(129).fill(scope).join(','),'x'.repeat(10001)])assert.equal(mode('list',raw),'text_fallback');
});
test('the existing explicit global opt-in retains its list and carousel behavior',()=>{
  for(const type of ['list','carousel'])assert.equal(chatbotInteractiveMode({type},{env:{QR_NATIVE_INTERACTIVE_ENABLED:'1',QR_NATIVE_LIST_SCOPES:'malformed'}}),'native');
  assert.equal(chatbotInteractiveMode({type:'list'},{tenantId,deviceId,env:{QR_NATIVE_INTERACTIVE_ENABLED:'true'}}),'text_fallback');
});
