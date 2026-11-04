/** A configured-proxy TLS probe. It never creates or borrows a WhatsApp socket,
 * follows a redirect, or takes a destination from an API request. */
import http from 'node:http';
import tls from 'node:tls';
import {lookup as dnsLookup} from 'node:dns/promises';
import {performance} from 'node:perf_hooks';
import {prepareDeviceProxy,resolvePublicDestination} from './device-network.mjs';

export const PROXY_PROBE_TARGET=Object.freeze({host:'www.cloudflare.com',port:443,id:'tls_reference'});
const categories=new Set(['PROXY_NOT_CONFIGURED','PROXY_CONFIGURATION_INVALID','PROXY_DESTINATION_REFUSED',
  'PROXY_TARGET_UNAVAILABLE','PROXY_AUTH_FAILED','PROXY_CONNECT_REFUSED','PROXY_TLS_FAILED','PROXY_TIMEOUT',
  'PROXY_ABORTED','PROXY_CONFIGURATION_CHANGED','PROXY_AUTHORIZATION_REVOKED','PROXY_PROBE_FAILED','PROXY_OUTCOME_UNCERTAIN']);
export const safeProxyError=value=>categories.has(value)?value:undefined;
export const proxyProbeError=code=>Object.assign(Error(safeProxyError(code)||'PROXY_PROBE_FAILED'),{code:safeProxyError(code)||'PROXY_PROBE_FAILED'});

export async function probeDeviceProxy(config,{secret,signal,assertCurrent=async()=>{},lookup=dnsLookup,
  requestHttp=http.request,connectTls=tls.connect,now=()=>performance.now(),timeoutMs=10000,onNetworkAttempt=()=>{}}={}){
  if(!Number.isInteger(timeoutMs)||timeoutMs<1||timeoutMs>15000)throw proxyProbeError('PROXY_CONFIGURATION_INVALID');
  await assertCurrent();
  if(signal?.aborted)throw proxyProbeError('PROXY_ABORTED');
  if(config?.enabled!==true)return {state:'not_configured',latency_ms:null,target:PROXY_PROBE_TARGET.id,error_code:'PROXY_NOT_CONFIGURED'};
  const controller=new AbortController();let timedOut=false;
  const abort=()=>controller.abort(),timer=setTimeout(()=>{timedOut=true;abort();},timeoutMs);
  signal?.addEventListener('abort',abort,{once:true});
  try{
    if(signal?.aborted)abort();
    let proxy,target;
    try{proxy=await prepareDeviceProxy(config,{secret,lookup,signal:controller.signal});}
    catch(error){throw proxyProbeError(error?.message==='device_proxy_invalid'?'PROXY_CONFIGURATION_INVALID':'PROXY_DESTINATION_REFUSED');}
    // Resolve all answers locally, require every answer to be public, and put
    // the selected IP in CONNECT itself. Proxy-side DNS cannot replace it.
    try{target=await resolvePublicDestination('https://'+PROXY_PROBE_TARGET.host,{lookup,signal:controller.signal});}
    catch{throw proxyProbeError('PROXY_TARGET_UNAVAILABLE');}
    await assertCurrent();
    if(controller.signal.aborted)throw proxyProbeError('PROXY_ABORTED');
    return await new Promise((resolve,reject)=>{
      let request,tunnel,secure,done=false;const started=now();
      const finish=(error,value)=>{
        if(done)return;done=true;controller.signal.removeEventListener('abort',onAbort);
        // No application request/body is written after the verified handshake.
        secure?.destroy();tunnel?.destroy();request?.destroy();
        error?reject(proxyProbeError(error)):resolve(value);
      };
      const onAbort=()=>finish('PROXY_ABORTED');
      controller.signal.addEventListener('abort',onAbort,{once:true});
      const authority=(target.family===6?'['+target.address+']':target.address)+':'+PROXY_PROBE_TARGET.port;
      try{
        // Native bounded HTTP parsing avoids accumulating an unbounded CONNECT
        // response. The only host used for TCP is the validated proxy IP.
        onNetworkAttempt();
        request=requestHttp({protocol:'http:',hostname:proxy.address,port:proxy.port,family:proxy.family,
          method:'CONNECT',path:authority,agent:false,maxHeaderSize:8192,insecureHTTPParser:false,
          headers:{Host:authority,...proxy.headers()}});
        request.once('error',()=>finish('PROXY_CONNECT_REFUSED'));
        request.once('response',response=>{response.destroy();finish(response.statusCode===407?'PROXY_AUTH_FAILED':'PROXY_CONNECT_REFUSED');});
        request.once('connect',(response,socket,head)=>{
          tunnel=socket;tunnel.once('error',()=>finish('PROXY_CONNECT_REFUSED'));
          if(done){tunnel.destroy();return;}
          if(response.statusCode!==200||head?.length){finish(response.statusCode===407?'PROXY_AUTH_FAILED':'PROXY_CONNECT_REFUSED');return;}
          if(controller.signal.aborted){onAbort();return;}
          try{
            secure=connectTls({socket:tunnel,servername:PROXY_PROBE_TARGET.host,rejectUnauthorized:true,minVersion:'TLSv1.2'});
            secure.once('error',()=>finish('PROXY_TLS_FAILED'));
            secure.once('close',()=>{if(!done)finish('PROXY_TLS_FAILED');});
            secure.once('secureConnect',()=>{
              if(controller.signal.aborted)return onAbort();
              if(secure.authorized!==true)return finish('PROXY_TLS_FAILED');
              finish(null,{state:'reachable',latency_ms:Math.max(0,Math.round(now()-started)),target:PROXY_PROBE_TARGET.id,error_code:null});
            });
          }catch{finish('PROXY_TLS_FAILED');}
        });
        if(controller.signal.aborted)return onAbort();
        request.end();
      }catch{finish('PROXY_CONNECT_REFUSED');}
    });
  }catch(error){
    if(timedOut)throw proxyProbeError('PROXY_TIMEOUT');
    if(signal?.aborted)throw proxyProbeError('PROXY_ABORTED');
    throw proxyProbeError(error?.code);
  }finally{clearTimeout(timer);signal?.removeEventListener('abort',abort);controller.abort();}
}
