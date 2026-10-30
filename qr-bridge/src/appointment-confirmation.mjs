/** Literal protocol confirmation for any verified private WhatsApp PN.
 * This is NOT the chatbot. No AI model, tenant ID or customer-facing URL is
 * selected by this adapter. The backend returns only an authorized response.
 */
const VALID_PHONE=/^55[1-9][0-9]{9,10}$/;
const VALID_MESSAGE=/^[a-zA-Z0-9._:-]{1,157}$/;
const PROTOCOL=/(?:^|[^a-zA-Z0-9])(AG-[A-HJ-NP-Z2-9]{6})(?![a-zA-Z0-9])/gi;

export function isBookingProtocolMessage(value){
  if(typeof value!=='string'||!value.trim()||value.length>1500)return false;
  if(/\b(cancel\w*|remarc\w*|reagend\w*|desmarc\w*)\b/i.test(value))return false;
  const matches=[...value.matchAll(PROTOCOL)].map(item=>item[1].toUpperCase());
  return matches.length>0&&new Set(matches).size===1;
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
  apiUrl,token,phone,messageId,text,isCurrent=()=>true,fetchImpl=fetch
}){
  if(!VALID_PHONE.test(phone)||!VALID_MESSAGE.test(messageId)||!isBookingProtocolMessage(text))
    throw Error('invalid_confirmation_inbound');
  if(typeof token!=='string'||token.length<32)throw Error('invalid_confirmation_transport');
  const target=endpoint(apiUrl);
  if(!target)throw Error('missing_confirmation_endpoint');
  if(!isCurrent())return null;
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),12000);
  try{
    const response=await fetchImpl(target.toString(),{
      method:'POST',redirect:'error',
      headers:{'Content-Type':'application/json','X-Orion-Bridge-Key':token},
      body:JSON.stringify({phone,message_id:messageId,text}),
      signal:controller.signal
    });
    if(!response.ok||!isCurrent())throw Error('central_confirmation_unavailable');
    const data=await response.json();
    if(!['confirmed','unrecognized'].includes(data?.state))return null;
    if(typeof data.answer!=='string'||!data.answer.trim()||data.answer.length>1200)
      throw Error('central_confirmation_invalid_response');
    return data.answer;
  }finally{clearTimeout(timeout);}
}
