/** Operator audit binding, in addition to the route's existing MASTER auth.
 * PRIMARY shares MASTER: this is not an independent privilege boundary. The
 * browser never receives the key and cannot supply its own operator identity. */
import {createHash,createHmac,timingSafeEqual} from 'node:crypto';
import {channelId} from './channel-storage.mjs';

const domain='orion:assistant-ingress-operator:v1';
const invalid=()=>Object.assign(Error('invalid_operator_assertion'),{status:401,code:'invalid_operator_assertion'});

function canonical(value,depth=0){
  if(depth>12)throw invalid();
  if(value===null||typeof value==='string'||typeof value==='boolean')return JSON.stringify(value);
  if(typeof value==='number'&&Number.isSafeInteger(value))return JSON.stringify(value);
  const ownValue=key=>{
    const descriptor=Object.getOwnPropertyDescriptor(value,key);
    if(!descriptor||!Object.hasOwn(descriptor,'value'))throw invalid();
    return descriptor.value;
  };
  if(Array.isArray(value))return '['+Array.from({length:value.length},(_,i)=>canonical(ownValue(String(i)),depth+1)).join(',')+']';
  if(value&&typeof value==='object'&&[Object.prototype,null].includes(Object.getPrototypeOf(value))){
    return '{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+canonical(ownValue(key),depth+1)).join(',')+'}';
  }
  throw invalid();
}

export function verifyIngressReviewAssertion({headers,body,secret,now=Date.now}={}){
  try{
    if(typeof secret!=='string'||secret.length<32||!headers||typeof now!=='function')throw invalid();
    const actor=headers['x-orion-ops-actor'],at=headers['x-orion-ops-at'],signature=headers['x-orion-ops-signature'];
    if(!channelId(actor)||typeof at!=='string'||!/^[1-9][0-9]{11,15}$/.test(at)||
        typeof signature!=='string'||!/^[a-f0-9]{64}$/.test(signature))throw invalid();
    const timestamp=Number(at),current=now();
    if(!Number.isSafeInteger(timestamp)||!Number.isSafeInteger(current)||timestamp>current+5000||current-timestamp>60000)throw invalid();
    const digest=createHash('sha256').update(canonical(body),'utf8').digest('hex');
    const expected=createHmac('sha256',secret).update([domain,actor,at,digest].join('\n'),'utf8').digest();
    if(!timingSafeEqual(expected,Buffer.from(signature,'hex')))throw invalid();
    return Object.freeze({actor_id:actor,authority:'platform_owner_proxy'});
  }catch{throw invalid();}
}
