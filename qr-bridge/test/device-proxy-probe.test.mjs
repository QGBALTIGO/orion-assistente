import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import http from 'node:http';
import tls from 'node:tls';
import net from 'node:net';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {probeDeviceProxy,PROXY_PROBE_TARGET} from '../src/device-proxy-probe.mjs';

const config={enabled:true,url:'http://proxy.example.com:8080',username:'synthetic-user'};
const secret='synthetic-password';
const lookup=async host=>[{address:host==='proxy.example.com'?'8.8.8.8':'1.1.1.1',family:4}];
function fake({status=200,head=Buffer.alloc(0),authorized=true,hang=false,tlsError=false}={}){
  const requests=[],connections=[],sockets=[];
  const socket=()=>{const value=new EventEmitter();value.destroy=()=>{value.destroyed=true;};sockets.push(value);return value;};
  return {requests,connections,sockets,
    requestHttp(options){const request=socket();requests.push(options);request.end=()=>queueMicrotask(()=>{
      if(!hang)request.emit('connect',{statusCode:status},socket(),head);
    });return request;},
    connectTls(options){connections.push(options);const secure=socket();secure.authorized=authorized;
      queueMicrotask(()=>secure.emit(tlsError?'error':'secureConnect',Error('private provider detail')));return secure;}};
}

test('disabled proxy and a revoked admission perform no DNS, sockets, TLS, or direct fallback',async()=>{
  const forbidden=()=>assert.fail('No network is authorized');
  assert.deepEqual(await probeDeviceProxy({enabled:false},{lookup:forbidden,requestHttp:forbidden,connectTls:forbidden}),
    {state:'not_configured',latency_ms:null,target:'tls_reference',error_code:'PROXY_NOT_CONFIGURED'});
  await assert.rejects(probeDeviceProxy(config,{assertCurrent:async()=>{throw Error('revoked');},lookup:forbidden,requestHttp:forbidden}));
});

test('probe pins the proxy and CONNECT destination, preserves certificate hostname, and keeps credentials inside CONNECT',async()=>{
  const f=fake(),resolved=[],checks=[];
  const result=await probeDeviceProxy(config,{secret,...f,lookup:async(host,...args)=>{resolved.push(host);return lookup(host,...args);},assertCurrent:async()=>checks.push('checked')});
  assert.equal(result.state,'reachable');assert.ok(result.latency_ms>=0);assert.equal(result.target,'tls_reference');
  assert.deepEqual(resolved,['proxy.example.com',PROXY_PROBE_TARGET.host]);
  assert.equal(checks.length,2);assert.equal(f.requests.length,1);assert.equal(f.connections.length,1);
  const request=f.requests[0],connection=f.connections[0];
  assert.equal(request.hostname,'8.8.8.8');assert.equal(request.port,8080);assert.equal(request.path,'1.1.1.1:443');
  assert.equal(request.method,'CONNECT');assert.equal(request.agent,false);assert.equal(request.maxHeaderSize,8192);assert.equal(request.insecureHTTPParser,false);
  assert.equal(request.headers['Proxy-Authorization'],'Basic '+Buffer.from('synthetic-user:'+secret).toString('base64'));
  assert.equal(connection.servername,PROXY_PROBE_TARGET.host);assert.equal(connection.rejectUnauthorized,true);assert.equal(connection.minVersion,'TLSv1.2');
  assert.equal('host' in connection,false);assert.equal('port' in connection,false);assert.equal('checkServerIdentity' in connection,false);
  assert.ok(f.sockets.every(socket=>socket.destroyed));
  assert.equal(JSON.stringify(result).includes(secret),false);assert.equal(JSON.stringify(result).includes('8.8.8.8'),false);
});

test('IPv6 public destinations are pinned and bracketed only in CONNECT authority',async()=>{
  const f=fake();await probeDeviceProxy(config,{...f,lookup:async host=>[{address:host==='proxy.example.com'?'2001:4860:4860::8888':'2606:4700:4700::1111',family:6}]});
  assert.equal(f.requests[0].hostname,'2001:4860:4860::8888');assert.equal(f.requests[0].family,6);
  assert.equal(f.requests[0].path,'[2606:4700:4700::1111]:443');
});

for(const unsafe of ['127.0.0.1','10.0.0.2','169.254.169.254','::1','::ffff:127.0.0.1']){
  test('proxy probe rejects mixed public/private DNS before opening a socket: '+unsafe,async()=>{
    for(const which of ['proxy','target']){
      let calls=0;
      await assert.rejects(probeDeviceProxy(config,{lookup:async host=>which===(host==='proxy.example.com'?'proxy':'target')
        ?[{address:'8.8.8.8',family:4},{address:unsafe,family:net.isIP(unsafe)}]:lookup(host),
        requestHttp:()=>{calls++;assert.fail('Private DNS must not connect');}}));assert.equal(calls,0);
    }
  });
}

test('changes while DNS resolves are reauthorized before the first network attempt',async()=>{
  let calls=0,valid=true;
  await assert.rejects(probeDeviceProxy(config,{lookup:async host=>{valid=false;return lookup(host);},
    assertCurrent:async()=>{if(!valid)throw Object.assign(Error('private config detail'),{code:'PROXY_CONFIGURATION_CHANGED'});},
    requestHttp:()=>calls++}),{code:'PROXY_CONFIGURATION_CHANGED'});assert.equal(calls,0);
});

test('malformed proxy URLs or credentials cannot redirect the probe or inject headers',async()=>{
  for(const value of ['http://127.1','http://0x7f000001','http://user:password@proxy.example.com',
    'http://proxy.example.com/path','http://proxy.example.com/?target=internal','http://service.railway.internal',
    'file:///etc/passwd','http://proxy.example.com#fragment']){
    await assert.rejects(probeDeviceProxy({...config,url:value},{lookup,requestHttp:()=>assert.fail('Unsafe configuration must not connect')}));
  }
  await assert.rejects(probeDeviceProxy(config,{lookup,secret:'password\r\nInjected: yes',requestHttp:()=>assert.fail('Invalid credentials')}),{code:'PROXY_CONFIGURATION_INVALID'});
});

for(const status of [301,302,307,308,403,407,500]){
  test('CONNECT '+status+' never redirects, retries, or starts TLS',async()=>{
    const f=fake({status});
    await assert.rejects(probeDeviceProxy(config,{...f,lookup,secret}),{code:status===407?'PROXY_AUTH_FAILED':'PROXY_CONNECT_REFUSED'});
    assert.equal(f.requests.length,1);assert.equal(f.connections.length,0);assert.ok(f.sockets.every(socket=>socket.destroyed));
  });
}

test('unexpected tunnel bytes and unauthenticated/failed TLS cannot become success',async()=>{
  for(const variant of [{head:Buffer.from('unrequested bytes')},{authorized:false},{tlsError:true}]){
    const f=fake(variant);
    await assert.rejects(probeDeviceProxy(config,{...f,lookup}));assert.ok(f.sockets.every(socket=>socket.destroyed));
  }
});

test('total timeout and external cancellation destroy an in-flight request with sanitized errors',async()=>{
  const f=fake({hang:true});await assert.rejects(probeDeviceProxy(config,{...f,lookup,timeoutMs:15}),{code:'PROXY_TIMEOUT'});
  assert.ok(f.sockets.every(socket=>socket.destroyed));
  const g=fake({hang:true}),abort=new AbortController();
  await assert.rejects(probeDeviceProxy(config,{...g,lookup,signal:abort.signal,
    requestHttp(options){const req=g.requestHttp(options);queueMicrotask(()=>abort.abort());return req;}}),{code:'PROXY_ABORTED'});
  assert.ok(g.sockets.every(socket=>socket.destroyed));
  const slow=probeDeviceProxy(config,{lookup:()=>new Promise(()=>{}),timeoutMs:15,requestHttp:()=>assert.fail('DNS timed out')});
  await assert.rejects(slow,{code:'PROXY_TIMEOUT'});
});

async function listen(server){await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));return server.address().port;}
async function stop(server){await new Promise(resolve=>server.close(resolve));}

test('native HTTP parser enforces the header cap against a real local synthetic proxy',async t=>{
  const sockets=new Set(),proxy=net.createServer(socket=>{sockets.add(socket);socket.on('error',()=>{});socket.on('close',()=>sockets.delete(socket));
    socket.once('data',()=>socket.write('HTTP/1.1 200 Connection established\r\nX-Oversize: '+'a'.repeat(9000)+'\r\n\r\n'));});
  const port=await listen(proxy);t.after(async()=>{for(const socket of sockets)socket.destroy();await stop(proxy);});
  await assert.rejects(probeDeviceProxy(config,{lookup,
    requestHttp(options){assert.equal(options.hostname,'8.8.8.8');return http.request({...options,hostname:'127.0.0.1',port});},
    connectTls:()=>assert.fail('Oversized headers must be refused before TLS')}),{code:'PROXY_CONNECT_REFUSED'});
});

test('real CONNECT + TLS verifies the fixed hostname and refuses an untrusted certificate, with no HTTP application data',async t=>{
  // Local disposable CA/certificate; neither DNS nor sockets can leave loopback.
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'orion-proxy-tls-'));fs.chmodSync(directory,0o700);
  t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  const cert=path.join(directory,'certificate.pem'),key=path.join(directory,'private-key.pem');
  execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-keyout',key,'-out',cert,'-subj','/CN='+PROXY_PROBE_TARGET.host,
    '-addext','subjectAltName=DNS:'+PROXY_PROBE_TARGET.host,'-days','1'],{stdio:'ignore'});
  const certificate=fs.readFileSync(cert),sockets=new Set(),authorities=[];let applicationBytes=0,servername;
  const target=tls.createServer({key:fs.readFileSync(key),cert:certificate},socket=>{
    sockets.add(socket);servername=socket.servername;socket.on('data',bytes=>{applicationBytes+=bytes.length;});socket.on('error',()=>{});socket.on('close',()=>sockets.delete(socket));});
  target.on('tlsClientError',()=>{});const targetPort=await listen(target);
  const proxy=http.createServer();proxy.on('connect',(request,socket)=>{
    authorities.push(request.url);sockets.add(socket);socket.on('error',()=>{});socket.on('close',()=>sockets.delete(socket));
    const upstream=net.connect({host:'127.0.0.1',port:targetPort},()=>{socket.write('HTTP/1.1 200 Connection established\r\n\r\n');socket.pipe(upstream);upstream.pipe(socket);});
    sockets.add(upstream);upstream.on('error',()=>socket.destroy());upstream.on('close',()=>sockets.delete(upstream));socket.on('close',()=>upstream.destroy());
  });const proxyPort=await listen(proxy);
  t.after(async()=>{for(const socket of sockets)socket.destroy();await Promise.all([stop(proxy),stop(target)]);});
  const requestHttp=options=>{assert.equal(options.hostname,'8.8.8.8');return http.request({...options,hostname:'127.0.0.1',port:proxyPort});};
  const result=await probeDeviceProxy(config,{lookup,requestHttp,connectTls:options=>tls.connect({...options,ca:certificate})});
  assert.equal(result.state,'reachable');
  await assert.rejects(probeDeviceProxy(config,{lookup,requestHttp}),{code:'PROXY_TLS_FAILED'});
  execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-keyout',key,'-out',cert,'-subj','/CN=wrong-host.example.com',
    '-addext','subjectAltName=DNS:wrong-host.example.com','-days','1'],{stdio:'ignore'});
  const wrongCertificate=fs.readFileSync(cert);target.setSecureContext({key:fs.readFileSync(key),cert:wrongCertificate});
  await assert.rejects(probeDeviceProxy(config,{lookup,requestHttp,connectTls:options=>tls.connect({...options,ca:wrongCertificate})}),{code:'PROXY_TLS_FAILED'});
  assert.ok(authorities.every(value=>value==='1.1.1.1:443'));assert.equal(applicationBytes,0);
  // A real verified handshake supplies SNI. Server handling may finish after
  // the client closes, so the separate strict client-options test also guards it.
  if(servername!==undefined)assert.equal(servername,PROXY_PROBE_TARGET.host);
});
