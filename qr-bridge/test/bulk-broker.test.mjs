import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {createChannelBroker} from '../src/channel-broker.mjs';

const ids=()=>({id:randomUUID(),key:'synthetic-private-key-'.repeat(3),lease:{owner:randomUUID(),fence:7},
  device:{device_id:randomUUID(),generation:randomUUID(),settings_revision:3,operation_id:randomUUID()},
  asset:{id:randomUUID(),size:4,mime:'text/plain; charset=utf-8'}});

test('bulk asset request binds the exact private path, effect, device and lease',async()=>{
  const value=ids(),calls=[],broker=createChannelBroker({apiUrl:'http://orion-api.railway.internal',fetchImpl:async(url,options)=>{
    calls.push({url,options});return new Response('test',{headers:{'content-type':value.asset.mime,'content-length':'4'}});}});
  const bytes=await broker.deviceBulkAsset(value.id,value.key,value.lease,{...value.device,asset:value.asset});
  assert.equal(bytes.toString(),'test');assert.equal(calls.length,1);const {url,options}=calls[0];
  assert.equal(url.pathname,`/internal/qr/channels/${value.id}/device/bulk/assets/${value.asset.id}`);
  assert.equal(url.searchParams.get('operation_id'),value.device.operation_id);assert.equal(url.searchParams.get('variant'),'original');
  assert.equal(url.searchParams.get('device_id'),value.device.device_id);assert.equal(url.searchParams.get('generation'),value.device.generation);
  assert.equal(url.searchParams.get('settings_revision'),'3');assert.equal(options.method,'GET');assert.equal(options.body,undefined);
  assert.equal(options.redirect,'error');assert.equal(options.headers['X-Orion-Device-Effect'],value.device.operation_id);
  assert.equal(options.headers['X-Orion-Channel-Owner'],value.lease.owner);assert.equal(options.headers['X-Orion-Chatbot-Revision'],undefined);
  bytes.fill(0);
});

for(const patch of [{asset:{id:'../../secret',size:4,mime:'text/plain'}},{operation_id:'bad'},
  {settings_revision:0},{chatbot_revision:1},{variant:'remote'}]){
  test('invalid bulk asset scope is rejected before private network: '+JSON.stringify(patch),async()=>{
    const value=ids();let calls=0;const broker=createChannelBroker({apiUrl:'http://orion-api.railway.internal',fetchImpl:async()=>{calls++;throw Error('unexpected');}});
    await assert.rejects(async()=>broker.deviceBulkAsset(value.id,value.key,value.lease,{...value.device,asset:value.asset,...patch}));
    assert.equal(calls,0);
  });
}

test('bulk asset body length and content type are enforced',async()=>{
  for(const [body,mime]of [['oversized','text/plain; charset=utf-8'],['test','application/json'],['x','text/plain; charset=utf-8']]){
    const value=ids(),broker=createChannelBroker({apiUrl:'http://orion-api.railway.internal',fetchImpl:async()=>new Response(body,{headers:{'content-type':mime}})});
    await assert.rejects(()=>broker.deviceBulkAsset(value.id,value.key,value.lease,{...value.device,asset:value.asset}));
  }
});

test('revoked bulk asset signal makes no network request',async()=>{
  const value=ids(),controller=new AbortController();controller.abort();let calls=0;
  const broker=createChannelBroker({apiUrl:'http://orion-api.railway.internal',fetchImpl:async()=>{calls++;throw Error('unexpected');}});
  await assert.rejects(()=>broker.deviceBulkAsset(value.id,value.key,value.lease,{...value.device,asset:value.asset,signal:controller.signal}));
  assert.equal(calls,0);
});
