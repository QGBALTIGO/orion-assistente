from pathlib import Path
root=Path('.')
p=root/'qr-bridge/src/device-network.mjs';s=p.read_text();start=s.index('<<<<<<< HEAD');end=s.index('\n',s.index('>>>>>>> ',start))
s=s[:start]+'''  return {address:target.address,family:target.family,port:Number(target.url.port||443),url:pinned,headers,
    servername:isIP(bare(target.url.hostname))?undefined:target.url.hostname};
}

export async function createDeviceProxy(config,{secret,lookup=dnsLookup,signal,assertCurrent=async()=>{},
  loadAgent=()=>import('https-proxy-agent')}={}){
  const target=await prepareDeviceProxy(config,{secret,lookup,signal});
  if(!target)return {socket:{},download:{},close(){}};
  const {HttpsProxyAgent}=await loadAgent();await assertCurrent();
  if(signal?.aborted)throw Error('device_request_aborted');
  const agent=new HttpsProxyAgent(target.url,{headers:target.headers,keepAlive:false,timeout:15000,
    servername:target.servername,minVersion:'TLSv1.2',rejectUnauthorized:true});'''+s[end:];p.write_text(s)
p=root/'qr-bridge/src/device-proxy-probe.mjs';s=p.read_text().replace("import http from 'node:http';","import https from 'node:https';").replace('requestHttp=http.request','requestHttps=https.request').replace("request=requestHttp({protocol:'http:',hostname:proxy.address,port:proxy.port,family:proxy.family,", "request=requestHttps({protocol:'https:',hostname:proxy.address,port:proxy.port,family:proxy.family,\n          servername:proxy.servername,minVersion:'TLSv1.2',rejectUnauthorized:true,")
s=s.replace('Native bounded HTTP parsing', 'Native bounded HTTPS parsing');p.write_text(s)
p=root/'qr-bridge/test/device-proxy-probe.test.mjs';s=p.read_text().replace("url:'http://proxy.example.com:8080'", "url:'https://proxy.example.com:8080'").replace('requestHttp','requestHttps');s=s.replace("assert.equal(request.method,'CONNECT');", "assert.equal(request.protocol,'https:');assert.equal(request.servername,'proxy.example.com');assert.equal(request.rejectUnauthorized,true);assert.equal(request.minVersion,'TLSv1.2');\n  assert.equal(request.method,'CONNECT');")
s=s.replace("http.request({...options,hostname:'127.0.0.1'", "http.request({...options,protocol:'http:',hostname:'127.0.0.1'")
p.write_text(s)
for rel in ['qr-bridge/test/device-diagnostics.test.mjs','tests/integration/test_subscription_diagnostics_postgres.py']:
 p=root/rel;s=p.read_text().replace('http://proxy.example.com','https://proxy.example.com').replace('http://proxy2.example.com','https://proxy2.example.com');p.write_text(s)
