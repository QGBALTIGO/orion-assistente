import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash,createHmac} from 'node:crypto';
import {verifyIngressReviewAssertion} from '../src/ingress-review-auth.mjs';

const secret='synthetic-ingress-review-master-secret-123456',actor='a0f20f4f-fb32-4999-874d-d2916cf1af63',at=1794000000000;
const body={version:1,mode:'dry_run',operation_id:'3570002d-8506-42cf-b1d2-8f0371ad77dd',traces:[]};
const canonical='{"mode":"dry_run","operation_id":"3570002d-8506-42cf-b1d2-8f0371ad77dd","traces":[],"version":1}';
function headers(payload=canonical,time=at,id=actor){
  const digest=createHash('sha256').update(payload).digest('hex');
  return {'x-orion-ops-actor':id,'x-orion-ops-at':String(time),'x-orion-ops-signature':
    createHmac('sha256',secret).update(['orion:assistant-ingress-operator:v1',id,String(time),digest].join('\n')).digest('hex')};
}
const verify=(head=headers(),data=body,current=at,key=secret)=>verifyIngressReviewAssertion({headers:head,body:data,secret:key,now:()=>current});
const denied=fn=>assert.throws(fn,error=>error.status===401&&error.code==='invalid_operator_assertion'&&error.message==='invalid_operator_assertion');

test('operator assertion accepts recursive canonical JSON, independent of insertion order',()=>{
  assert.equal(createHash('sha256').update(canonical).digest('hex'),'5b4b5950c80106d067a6c500c99a4ec5300c6881d48b7cb30d0a9380db420ec5');
  assert.equal(headers()['x-orion-ops-signature'],'b370d34771c4d0849ad7cb2ee234afa443ae2b95308a30cd1f2c9514a9176a63');
  assert.deepEqual(verify(),{actor_id:actor,authority:'platform_owner_proxy'});
  assert.deepEqual(verify(headers(),{traces:[],operation_id:body.operation_id,mode:'dry_run',version:1}),verify());
  const nested={z:[{b:2,a:1}],a:{t:null,k:true}};
  assert.deepEqual(verify(headers('{"a":{"k":true,"t":null},"z":[{"a":1,"b":2}]}'),nested),verify());
});
test('operator assertion binds every body field, timestamp, actor and key',()=>{
  denied(()=>verify(headers(),{...body,mode:'apply'}));
  denied(()=>verify({...headers(),'x-orion-ops-actor':'b0f20f4f-fb32-4999-874d-d2916cf1af63'}));
  denied(()=>verify({...headers(),'x-orion-ops-at':String(at+1)}));
  denied(()=>verify(headers(),body,at,secret+'changed'));
  denied(()=>verify(headers(),body,at,'short'));
});
test('operator assertion has a bounded clock window and rejects noncanonical time encodings',()=>{
  assert.deepEqual(verify(headers(),body,at+60000),verify());
  assert.deepEqual(verify(headers(),body,at-5000),verify());
  denied(()=>verify(headers(),body,at+60001));
  denied(()=>verify(headers(),body,at-5001));
  for(const value of ['0'+at,' '+at,at+' ','1e12',String(at)+'.0',String(Number.MAX_SAFE_INTEGER+1)])
    denied(()=>verify({...headers(),'x-orion-ops-at':value}));
});
test('operator assertion rejects missing, repeated and malformed headers without echoing input',()=>{
  denied(()=>verify({}));
  for(const field of Object.keys(headers())){
    denied(()=>verify({...headers(),[field]:[headers()[field],headers()[field]]}));
    denied(()=>verify({...headers(),[field]:headers()[field]+',injected'}));
  }
  for(const signature of ['f'.repeat(63),'F'.repeat(64),'z'.repeat(64),'f'.repeat(65)])
    denied(()=>verify({...headers(),'x-orion-ops-signature':signature}));
});
test('operator assertion rejects non JSON or excessively nested values and never invokes getters',()=>{
  for(const value of [undefined,NaN,Infinity,1.25,1n,()=>{},new Date(),{x:undefined}])
    denied(()=>verifyIngressReviewAssertion({headers:headers(),body:value,secret,now:()=>at}));
  let value={};for(let i=0;i<14;i++)value={x:value};denied(()=>verify(headers(),value));
  const cycle={};cycle.x=cycle;denied(()=>verify(headers(),cycle));
  let called=false;const getter={get x(){called=true;return 1;}};
  denied(()=>verify(headers(),getter));assert.equal(called,false);
  denied(()=>verify(headers(),Array(1)));
});
