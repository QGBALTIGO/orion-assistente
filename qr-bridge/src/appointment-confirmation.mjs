import {resolveMessagePhone} from './core.mjs';

/** Literal protocol confirmation for any verified private WhatsApp PN.
 * This is NOT the chatbot. No AI model, tenant ID or customer-facing URL is
 * selected by this adapter. The backend returns only an authorized response.
 */
const VALID_PHONE=/^(?:55[1-9][0-9]{9,10}|5959[0-9]{8})$/;
const VALID_MESSAGE=/^[a-zA-Z0-9._:-]{1,157}$/;
const PROTOCOL=/(?:^|[^a-zA-Z0-9])(AG-[A-HJ-NP-Z2-9]{6})(?![a-zA-Z0-9])/gi;

export function isBookingProtocolMessage(value){
  if(typeof value!=='string'||!value.trim()||value.length>1500)return false;
  if(/\b(cancel\w*|remarc\w*|reagend\w*|desmarc\w*)\b/i.test(value))return false;
  const matches=[...value.matchAll(PROTOCOL)].map(item=>item[1].toUpperCase());
  return matches.length>0&&new Set(matches).size===1;
}

/** Bounded second look at the authenticated Baileys LID->PN mapping.
 * A new device may publish its PN mapping shortly after the first upsert.
 * Never derive a phone from the LID number or from unverified message text.
 */
export async function resolveBookingSenderPhone(key,client,{
  isCurrent=()=>true,resolver=resolveMessagePhone,
  sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms))
}={}){
  const remote=String(key?.remoteJid||'');
  if(!isCurrent())return null;
  let phone=await resolver(key,client);
  if(phone||!/^\d{8,20}(?::\d+)?@lid$/.test(remote))return phone;
  for(const ms of [150,350,600]){
    if(!isCurrent())return null;
    await sleep(ms);
    if(!isCurrent())return null;
    phone=await resolver(key,client);
    if(phone)return phone;
  }
  return null;
}

function endpoint(apiUrl){
  if(typeof apiUrl!=='string'||!apiUrl)return null;
  const url=new URL(apiUrl);
  if(url.protocol!=='http:'||!url.hostname.endsWith('.railway.internal')||
     url.username||url.password||url.search||url.hash||
     !['/internal/qr/ai/reply','/'].includes(url.pathname))throw Error('unsafe_confirmation_endpoint');
  url.pathname='/internal/qr/appointment-confirmation';
  return url;
}

export async function fetchBookingProtocolConfirmation({
  apiUrl,token,phone,messageId,text,isCurrent=()=>true,fetchImpl=fetch,
  onStatus=()=>{},sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms))
}){
  if(!VALID_PHONE.test(phone)||!VALID_MESSAGE.test(messageId)||!isBookingProtocolMessage(text))
    throw Error('invalid_confirmation_inbound');
  if(typeof token!=='string'||token.length<32)throw Error('invalid_confirmation_transport');
  const target=endpoint(apiUrl);
  if(!target)throw Error('missing_confirmation_endpoint');

  // The backend is idempotent on authenticated inbound message ID. Retrying a
  // definite lookup failure does NOT transmit anything on WhatsApp.
  for(let attempt=0;attempt<2;attempt++){
    if(!isCurrent())return null;
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),10000);
    try{
      const response=await fetchImpl(target.toString(),{
        method:'POST',redirect:'error',
        headers:{'Content-Type':'application/json','X-Orion-Bridge-Key':token},
        body:JSON.stringify({phone,message_id:messageId,text}),
        signal:controller.signal
      });
      if(!response.ok){
        if(attempt===0&&[502,503,504].includes(response.status)){
          onStatus('retrying');await sleep(250);continue;
        }
        throw Error(response.status===401||response.status===403?
          'central_confirmation_auth_denied':'central_confirmation_http_error');
      }
      if(!isCurrent())return null;
      const data=await response.json();
      const state=typeof data?.state==='string'?data.state:'invalid';
      if(!['confirmed','unrecognized','unavailable','disabled','not_protocol'].includes(state))
        throw Error('central_confirmation_invalid_response');
      onStatus(state);
      if(state==='unavailable'&&attempt===0){
        onStatus('retrying');await sleep(250);continue;
      }
      if(!['confirmed','unrecognized'].includes(state))return null;
      if(typeof data.answer!=='string'||!data.answer.trim()||data.answer.length>1200)
        throw Error('central_confirmation_invalid_response');
      return data.answer;
    }catch(error){
      if(attempt===0&&isCurrent()&&(
         error?.name==='AbortError'||error instanceof TypeError)){
        onStatus('retrying');await sleep(250);continue;
      }
      throw error;
    }finally{clearTimeout(timeout);}
  }
  return null;
}
