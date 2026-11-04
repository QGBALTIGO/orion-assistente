import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {isPublicAddress,publicDestination,resolvePublicDestination,postDeviceWebhook,createDeviceProxy} from '../src/device-network.mjs';

const publicLookup=async()=>[{address:'8.8.8.8',family:4}];
test('device egress accepts global addresses and rejects private, metadata, mapped and reserved destinations',()=>{
  for(const ip of ['8.8.8.8','1.1.1.1','2606:4700:4700::1111','2001:4860:4860::8888'])assert.equal(isPublicAddress(ip),true,ip);
  for(const ip of ['0.0.0.0','10.1.2.3','100.64.1.2','127.1.2.3','169.254.169.254','172.31.1.2','192.168.1.1','192.0.2.2',
    '198.18.1.1','198.51.100.3','203.0.113.1','224.0.0.1','255.255.255.255','::1','::','::ffff:8.8.8.8','::ffff:127.0.0.1',
    '64:ff9b::a00:1','fc00::1','fe80::1','2001:db8::1','2002:a00:1::1','3fff::1','bad'])assert.equal(isPublicAddress(ip),false,ip);
  for(const url of ['http://localhost','http://service.railway.internal','http://example.local','http://127.1/',
    'http://0x7f000001/','http://[::ffff:127.0.0.1]/','file:///etc/passwd','https://user:secret@example.com/',
    'https://example.com/#part','https://example.com:0/','https://example.com./',' https://example.com/',
    'https://example.com\\@127.0.0.1/'])assert.throws(()=>publicDestination(url),/destination_refused/,url);
  assert.equal(publicDestination('https://hooks.example.com/path?token=fixture').hostname,'hooks.example.com');
  assert.throws(()=>publicDestination('https://proxy.example.com/',{proxy:true}),/destination_refused/);
  assert.throws(()=>publicDestination('http://proxy.example.com/hidden',{proxy:true}),/destination_refused/);
});

test('all DNS answers must be public; cancellation and mixed public/private DNS never create a request',async()=>{
  for(const records of [[],[{address:'127.0.0.1',family:4}],[{address:'8.8.8.8',family:4},{address:'10.0.0.1',family:4}],
    [{address:'8.8.8.8',family:6}]])await assert.rejects(resolvePublicDestination('https://hooks.example.com',{lookup:async()=>records}));
  const aborted=new AbortController();aborted.abort();
  await assert.rejects(resolvePublicDestination('https://hooks.example.com',{signal:aborted.signal,lookup:()=>assert.fail('DNS after cancel')}));
  let request=0;
  await assert.rejects(postDeviceWebhook({url:'https://hooks.example.com',payload:{},eventId:'fixture',
    lookup:async()=>[{address:'169.254.169.254',family:4}],requestHttps:()=>request++}));assert.equal(request,0);
});

function httpFixture(status=204){
  const calls=[];
  return {calls,request(url,options,callback){
    const req=new EventEmitter();req.destroy=()=>{req.destroyed=true;};
    req.end=bytes=>{calls.push({url,options,bytes});const response={statusCode:status,destroy(){this.destroyed=true;}};queueMicrotask(()=>callback(response));};
    return req;
  }};
}
test('webhooks pin the validated address, retain TLS hostname, and never follow any redirect',async()=>{
  const f=httpFixture();let dns=0;
  const result=await postDeviceWebhook({url:'https://hooks.example.com/receive',payload:{event_id:'e1'},eventId:'e1',
    lookup:async()=>{dns++;return publicLookup();},requestHttps:f.request});
  assert.equal(result.status,204);assert.equal(dns,1);assert.equal(f.calls.length,1);
  assert.equal(f.calls[0].url.hostname,'hooks.example.com');assert.equal(f.calls[0].options.agent,false);
  await new Promise((resolve,reject)=>f.calls[0].options.lookup('hooks.example.com',{},(error,address,family)=>{
    if(error)return reject(error);assert.equal(address,'8.8.8.8');assert.equal(family,4);resolve();}));
  for(const status of [301,302,303,307,308]){
    const redirect=httpFixture(status);
    await assert.rejects(postDeviceWebhook({url:'https://hooks.example.com',payload:{},eventId:'redirect',lookup:publicLookup,requestHttps:redirect.request}),/redirect_refused/);
    assert.equal(redirect.calls.length,1);
  }
});

test('revocation while DNS resolves suppresses HTTP and explicit cancellation tears down an ongoing request',async()=>{
  let alive=true,attempts=0;
  await assert.rejects(postDeviceWebhook({url:'https://hooks.example.com',payload:{},eventId:'revoked',
    lookup:async()=>{alive=false;return publicLookup();},isCurrent:()=>alive,requestHttps:()=>attempts++}),/aborted/);
  assert.equal(attempts,0);
  const controller=new AbortController();let req;
  const running=postDeviceWebhook({url:'https://hooks.example.com',payload:{},eventId:'abort',lookup:publicLookup,signal:controller.signal,
    requestHttps:()=>{req=new EventEmitter();req.destroy=()=>{req.destroyed=true;};req.end=()=>controller.abort();return req;}});
  await assert.rejects(running,/aborted/);assert.equal(req.destroyed,true);
});

test('HTTP proxy is per-device, pins its public IP and covers websocket, upload and download without exposing credentials in URL',async()=>{
  const built=[];
  class Agent{constructor(url,options){this.url=url;this.options=options;built.push(this);}destroy(){this.destroyed=true;}}
  const disabled=await createDeviceProxy({enabled:false},{loadAgent:()=>assert.fail('inactive dependency'),lookup:()=>assert.fail('inactive DNS')});
  assert.deepEqual(disabled.socket,{});
  const proxy=await createDeviceProxy({enabled:true,url:'http://proxy.example.com:8080',username:'synthetic-user'},
    {secret:'synthetic-password',lookup:publicLookup,loadAgent:async()=>({HttpsProxyAgent:Agent})});
  assert.equal(built.length,1);assert.equal(built[0].url.hostname,'8.8.8.8');assert.equal(built[0].url.username,'');assert.equal(built[0].url.password,'');
  assert.equal(built[0].options.headers()['Proxy-Authorization'],'Basic '+Buffer.from('synthetic-user:synthetic-password').toString('base64'));
  assert.equal(proxy.socket.agent,proxy.socket.fetchAgent);assert.equal(proxy.download.httpsAgent,proxy.socket.agent);
  assert.equal(proxy.download.proxy,false);assert.equal(proxy.download.maxRedirects,0);
  proxy.close();assert.equal(built[0].destroyed,true);
  await assert.rejects(createDeviceProxy({enabled:true,url:'http://proxy.example.com'},
    {lookup:async()=>[{address:'10.0.0.1',family:4}],loadAgent:()=>assert.fail('unsafe proxy')}));
});
