/** Bounded public HTTP for explicitly authorized Chatbot actions. Every actual
 * connection uses a validated DNS answer; redirects never start another hop. */
import http from 'node:http';
import https from 'node:https';
import {lookup as dnsLookup} from 'node:dns/promises';
import {resolvePublicDestination,publicDestination} from './device-network.mjs';

export const CHATBOT_HTTP_MAX_BYTES=512*1024;
export const CHATBOT_IMAGE_MAX_BYTES=8_000_000;
const error=code=>Object.assign(Error(code),{code});
const plain=value=>value!==null&&typeof value==='object'&&!Array.isArray(value)&&Object.getPrototypeOf(value)===Object.prototype;

function boundedJSON(value){
  if(!plain(value))throw error('CHATBOT_HTTP_JSON_INVALID');
  const stack=[[value,0]];let count=0;
  while(stack.length){
    const [item,depth]=stack.pop();
    if(++count>8192||depth>16)throw error('CHATBOT_HTTP_JSON_LIMIT');
    // Never round an external identifier or turn an overflowing JSON number
    // into null while persisting the response for tags. Such values need strings.
    if(typeof item==='number'&&(!Number.isFinite(item)||(Number.isInteger(item)&&!Number.isSafeInteger(item))))throw error('CHATBOT_HTTP_JSON_NUMBER');
    // PostgreSQL jsonb cannot store NUL or isolated UTF-16 surrogates. Reject
    // them before a response becomes durable reconciliation evidence. The u
    // regex treats a valid surrogate pair as its complete Unicode code point.
    if(typeof item==='string'&&/[\u0000\uD800-\uDFFF]/u.test(item))throw error('CHATBOT_HTTP_JSON_STRING');
    if(item&&typeof item==='object'){
      for(const [key,child] of Object.entries(item)){
        if(!Array.isArray(item)&&(/[\u0000\uD800-\uDFFF]/u.test(key)||key.length>4096||[...key].length>2048))throw error('CHATBOT_HTTP_JSON_KEY');
        stack.push([child,depth+1]);
      }
    }
  }
  return value;
}

/** The response body stays owned by this call and is erased on every failure. */
async function publicRequest({url,method='GET',body,signal,isCurrent=()=>true,assertCurrent=async()=>{},
  lookup=dnsLookup,requestHttp=http.request,requestHttps=https.request,timeoutMs=10000,maxBytes=CHATBOT_HTTP_MAX_BYTES,
  accept='application/json',requestId}){
  if(!['GET','POST'].includes(method)||!Number.isSafeInteger(maxBytes)||maxBytes<1||maxBytes>CHATBOT_IMAGE_MAX_BYTES||
      !Number.isSafeInteger(timeoutMs)||timeoutMs<1||timeoutMs>15000||
      (body!==undefined&&(!Buffer.isBuffer(body)||body.length>CHATBOT_HTTP_MAX_BYTES))||
      (requestId!==undefined&&!/^[0-9a-f-]{36}$/.test(requestId)))throw error('CHATBOT_HTTP_INVALID');
  const target=await resolvePublicDestination(url,{lookup,signal});
  await assertCurrent();if(signal?.aborted||!isCurrent())throw error('CHATBOT_REVOKED');
  return await new Promise((resolve,reject)=>{
    let request,response,timer,done=false,total=0;const chunks=[];
    const finish=(failure,result)=>{
      if(done){result?.buffer?.fill(0);return;}done=true;
      clearTimeout(timer);signal?.removeEventListener('abort',abort);
      for(const chunk of chunks)chunk.fill(0);chunks.length=0;
      failure?reject(failure):resolve(result);
    };
    const stop=code=>{if(done)return;finish(error(code));response?.destroy();request?.destroy();};
    const abort=()=>stop('CHATBOT_REVOKED');
    const pinnedLookup=(_host,options,callback)=>{
      if(typeof options==='function'){callback=options;options={};}
      if(signal?.aborted||!isCurrent())return callback(error('CHATBOT_REVOKED'));
      callback(null,...(options?.all?[[{address:target.address,family:target.family}]]:[target.address,target.family]));
    };
    try{
      const requestFn=target.url.protocol==='https:'?requestHttps:requestHttp;
      request=requestFn(target.url,{method,agent:false,minVersion:'TLSv1.2',rejectUnauthorized:true,lookup:pinnedLookup,
        headers:{'Accept':accept,'Accept-Encoding':'identity','User-Agent':'Orion-Chatbot/1',
          ...(requestId?{'X-Orion-Operation-Id':requestId}:{}),
          ...(body?{'Content-Type':'application/json','Content-Length':String(body.length)}:{})}},res=>{
        response=res;
        if(done){res.destroy();return;}
        const status=Number(res.statusCode);
        if(status<200||status>=300)return stop(status>=300&&status<400?'CHATBOT_HTTP_REDIRECT':'CHATBOT_HTTP_STATUS');
        const encoding=String(res.headers?.['content-encoding']||'identity').trim().toLowerCase();
        if(encoding&&encoding!=='identity')return stop('CHATBOT_HTTP_ENCODING');
        const length=res.headers?.['content-length'];
        if(length!==undefined&&(!/^\d+$/.test(String(length))||Number(length)>maxBytes))return stop('CHATBOT_HTTP_TOO_LARGE');
        res.on('data',chunk=>{
          if(done)return;
          if(signal?.aborted||!isCurrent())return abort();
          const owned=Buffer.from(chunk);total+=owned.length;
          if(total>maxBytes){owned.fill(0);return stop('CHATBOT_HTTP_TOO_LARGE');}
          chunks.push(owned);
        });
        res.once('aborted',()=>stop('CHATBOT_HTTP_INCOMPLETE'));
        res.once('error',()=>stop('CHATBOT_HTTP_UNAVAILABLE'));
        res.once('end',()=>{
          if(done)return;
          if(!total)return stop('CHATBOT_HTTP_EMPTY');
          if(length!==undefined&&Number(length)!==total)return stop('CHATBOT_HTTP_INCOMPLETE');
          const buffer=Buffer.concat(chunks,total);
          finish(null,{buffer,status,contentType:String(res.headers?.['content-type']||'').split(';')[0].trim().toLowerCase()});
        });
      });
      request.once('error',()=>stop('CHATBOT_HTTP_UNAVAILABLE'));
      timer=setTimeout(()=>stop('CHATBOT_HTTP_TIMEOUT'),timeoutMs);
      signal?.addEventListener('abort',abort,{once:true});
      if(signal?.aborted||!isCurrent())return abort();
      request.end(body);
    }catch{stop('CHATBOT_HTTP_UNAVAILABLE');}
  });
}

/** The fixed POST context is represented as equivalent query fields for GET.
 * Oversized GET URLs fail before networking instead of truncating contact text. */
export async function requestChatbotJSON({url,method='POST',data={},...options}){
  if(!plain(data)||!['GET','POST'].includes(method))throw error('CHATBOT_HTTP_INVALID');
  let body,result;
  try{
    if(method==='GET'){
      const target=publicDestination(url);
      for(const [key,value] of Object.entries(data)){
        if(!['string','number','boolean'].includes(typeof value)&&value!==null)throw error('CHATBOT_HTTP_INVALID');
        target.searchParams.set(key,value===null?'':String(value));
      }
      url=target.href;
    }else{
      body=Buffer.from(JSON.stringify(data));
      if(body.length>CHATBOT_HTTP_MAX_BYTES)throw error('CHATBOT_HTTP_TOO_LARGE');
    }
    result=await publicRequest({url,method,body,...options,maxBytes:CHATBOT_HTTP_MAX_BYTES,accept:'application/json'});
    if(result.contentType!=='application/json'&&!/^application\/[a-z0-9.+-]+\+json$/.test(result.contentType))throw error('CHATBOT_HTTP_CONTENT_TYPE');
    let value;try{value=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(result.buffer).replace(/^\uFEFF/,''));}
    catch{throw error('CHATBOT_HTTP_JSON_INVALID');}
    await options.assertCurrent?.();
    if(options.signal?.aborted||options.isCurrent?.()===false)throw error('CHATBOT_REVOKED');
    return {status:result.status,json:boundedJSON(value)};
  }finally{body?.fill(0);result?.buffer?.fill(0);}
}

/** Image URLs are never passed to Baileys. The caller must send these bytes to
 * Orion's authenticated image decoder and then load the normalized asset. */
export async function fetchChatbotImage({url,...options}){
  let result;
  try{
    result=await publicRequest({url,...options,method:'GET',maxBytes:CHATBOT_IMAGE_MAX_BYTES,accept:'image/*',timeoutMs:15000});
    await options.assertCurrent?.();
    if(options.signal?.aborted||options.isCurrent?.()===false)throw error('CHATBOT_REVOKED');
    return result;
  }catch(e){result?.buffer?.fill(0);throw e;}
}
