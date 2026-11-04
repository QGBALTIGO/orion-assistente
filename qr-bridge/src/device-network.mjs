/** Explicit per-device egress. Never installs a global agent or follows redirects. */
import http from 'node:http';
import https from 'node:https';
import {lookup as dnsLookup} from 'node:dns/promises';
import {isIP} from 'node:net';

const unsafe=()=>Error('device_destination_refused');
const bare=value=>String(value).replace(/^\[|\]$/g,'');
function ipv4Number(value){return value.split('.').reduce((n,part)=>(n<<8n)+BigInt(part),0n);}
function ipv6Number(value){
  const sides=value.split('::'),left=sides[0]?sides[0].split(':'):[],right=sides[1]?sides[1].split(':'):[];
  const groups=sides.length===2?[...left,...Array(8-left.length-right.length).fill('0'),...right]:left;
  return groups.reduce((n,part)=>(n<<16n)+BigInt('0x'+part),0n);
}
const inside=(number,base,prefix,bits)=>(number>>BigInt(bits-prefix))===(base>>BigInt(bits-prefix));
const denied4=[['0.0.0.0',8],['10.0.0.0',8],['100.64.0.0',10],['127.0.0.0',8],
  ['169.254.0.0',16],['172.16.0.0',12],['192.0.0.0',24],['192.0.2.0',24],
  ['192.88.99.0',24],['192.168.0.0',16],['198.18.0.0',15],['198.51.100.0',24],
  ['203.0.113.0',24],['224.0.0.0',4],['240.0.0.0',4]].map(([base,prefix])=>[ipv4Number(base),prefix]);
const denied6=[['2001::',23],['2001:db8::',32],['2002::',16],['3fff::',20]]
  .map(([base,prefix])=>[ipv6Number(base),prefix]);

export function isPublicAddress(value){
  const address=bare(value),family=isIP(address);
  if(family===4){const number=ipv4Number(address);return !denied4.some(([base,prefix])=>inside(number,base,prefix,32));}
  // Only global unicast, excluding transition/documentation/reserved ranges.
  // Embedded IPv4 (including mapped loopback) is never reinterpreted as global.
  if(family===6&&!address.includes('.')){
    const number=ipv6Number(address);
    return inside(number,ipv6Number('2000::'),3,128)&&!denied6.some(([base,prefix])=>inside(number,base,prefix,128));
  }
  return false;
}

export function publicDestination(value,{proxy=false}={}){
  if(typeof value!=='string'||value.length>2048||value!==value.trim()||/[\u0000-\u0020\u007f\\]/.test(value))throw unsafe();
  let url;try{url=new URL(value);}catch{throw unsafe();}
  if(!(proxy?url.protocol==='http:':['http:','https:'].includes(url.protocol))||url.username||url.password||url.hash||
      (proxy&&(url.search||url.pathname!=='/'))||url.port==='0')throw unsafe();
  const host=bare(url.hostname).toLowerCase();
  if(isIP(host)){if(!isPublicAddress(host))throw unsafe();}
  else if(host.length>253||!host.includes('.')||host.endsWith('.')||
      !/^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])$/.test(host)||host.split('.').some(part=>!part||part.length>63||part.startsWith('-')||part.endsWith('-'))||
      /(?:^|\.)(?:localhost|local|internal|test|invalid|onion)$/.test(host))throw unsafe();
  return url;
}

export async function resolvePublicDestination(value,{proxy=false,lookup=dnsLookup,signal,timeoutMs=3000}={}){
  const url=publicDestination(value,{proxy}),host=bare(url.hostname);
  if(signal?.aborted)throw Error('device_request_aborted');
  let timer,abort;
  try{
    const records=isIP(host)?[{address:host,family:isIP(host)}]:await Promise.race([
      Promise.resolve().then(()=>lookup(host,{all:true,verbatim:true})),
      new Promise((_,reject)=>{timer=setTimeout(()=>reject(unsafe()),timeoutMs);}),
      new Promise((_,reject)=>{abort=()=>reject(Error('device_request_aborted'));signal?.addEventListener('abort',abort,{once:true});})
    ]);
    if(signal?.aborted||!Array.isArray(records)||!records.length||records.length>32||
        records.some(row=>!row||!isPublicAddress(row.address)||isIP(row.address)!==row.family))throw unsafe();
    return {url,address:records[0].address,family:records[0].family};
  }finally{clearTimeout(timer);if(abort)signal?.removeEventListener('abort',abort);}
}

/** One HTTP attempt. The validated IP is the actual TCP destination; Host/SNI
 * retain the original hostname. A redirect never starts a second request. */
export async function postDeviceWebhook({url,payload,eventId,signal,isCurrent=()=>true,assertCurrent=async()=>{},
  lookup=dnsLookup,requestHttp=http.request,requestHttps=https.request,timeoutMs=5000}){
  const bytes=Buffer.from(JSON.stringify(payload));
  if(bytes.length>65536||typeof eventId!=='string'||!/^[a-zA-Z0-9._:-]{1,160}$/.test(eventId)||
      !Number.isInteger(timeoutMs)||timeoutMs<1||timeoutMs>15000)throw Error('device_webhook_invalid');
  const target=await resolvePublicDestination(url,{lookup,signal});
  await assertCurrent();if(signal?.aborted||!isCurrent())throw Error('device_request_aborted');
  return await new Promise((resolve,reject)=>{
    let request,timer,done=false;
    const finish=(error,value)=>{if(done)return;done=true;clearTimeout(timer);signal?.removeEventListener('abort',abort);error?reject(error):resolve(value);};
    const abort=()=>{request?.destroy();finish(Error('device_request_aborted'));};
    const pinnedLookup=(_host,options,callback)=>{
      if(typeof options==='function'){callback=options;options={};}
      if(!isCurrent()||signal?.aborted)return callback(Error('device_request_aborted'));
      callback(null,...(options?.all?[[{address:target.address,family:target.family}]]:[target.address,target.family]));
    };
    try{
      const requestFn=target.url.protocol==='https:'?requestHttps:requestHttp;
      request=requestFn(target.url,{method:'POST',agent:false,lookup:pinnedLookup,
        headers:{'Content-Type':'application/json','Content-Length':String(bytes.length),
          'User-Agent':'Orion-Dispositivos/1','X-Orion-Event-Id':eventId}},response=>{
        const status=Number(response.statusCode);
        // No response payload is needed, persisted or exposed. Stop consuming it.
        response.destroy();
        if(status>=200&&status<300)finish(null,{status});
        else finish(Error(status>=300&&status<400?'device_webhook_redirect_refused':'device_webhook_http_error'));
      });
      request.once('error',()=>finish(Error('device_webhook_unavailable')));
      timer=setTimeout(()=>{request.destroy();finish(Error('device_webhook_timeout'));},timeoutMs);
      signal?.addEventListener('abort',abort,{once:true});
      if(signal?.aborted||!isCurrent())return abort();
      request.end(bytes);
    }catch{request?.destroy();finish(Error('device_webhook_unavailable'));}
  });
}

/** HTTP CONNECT proxy, confined to this socket and its media requests. Password
 * never appears in a URL, error, debug constructor output or global environment. */
export async function createDeviceProxy(config,{secret,lookup=dnsLookup,signal,assertCurrent=async()=>{},
  loadAgent=()=>import('https-proxy-agent')}={}){
  if(config?.enabled!==true)return {socket:{},download:{},close(){}};
  const target=await resolvePublicDestination(config.url,{proxy:true,lookup,signal});
  const username=config.username??'',password=secret??'';
  if(typeof username!=='string'||typeof password!=='string'||username.length>256||password.length>1024||/[\r\n\u0000]/.test(username+password))throw Error('device_proxy_invalid');
  const {HttpsProxyAgent}=await loadAgent();await assertCurrent();
  if(signal?.aborted)throw Error('device_request_aborted');
  const pinned=new URL(target.url);pinned.hostname=target.family===6?'['+target.address+']':target.address;
  const headers=username||password?()=>({'Proxy-Authorization':'Basic '+Buffer.from(username+':'+password).toString('base64')}):()=>({});
  const agent=new HttpsProxyAgent(pinned,{headers,keepAlive:false,timeout:15000});
  return {socket:{agent,fetchAgent:agent,options:{proxy:false,httpsAgent:agent,httpAgent:agent,maxRedirects:0}},
    download:{proxy:false,httpsAgent:agent,httpAgent:agent,maxRedirects:0},close(){agent.destroy();}};
}
