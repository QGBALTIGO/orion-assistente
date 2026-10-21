/** Encaminha apenas mensagem privada recém-recebida para a API Orion autorizada. */
import {cleanContactProfile} from './contact-names.mjs';
export async function forwardAssistant({apiUrl,token,phone,messageId,messageIds,text,intakeRevision,requireSummaryReview,profile,envelope,isCurrent=()=>true,fetchImpl=fetch}){
  if(typeof token!=="string"||token.length<32)throw Error("bridge_key_missing");
  if(typeof phone!=="string"||!/^55[1-9]\d(?:9\d{8}|[2-5]\d{7})$/.test(phone))throw Error("invalid_recipient");
  if(typeof messageId!=="string"||! /^[a-zA-Z0-9._:-]{1,160}$/.test(messageId))throw Error("invalid_message_id");
  if(typeof text!=="string"||!text.trim())return null;
  if(text.length>1500)throw Error("message_too_long");
  if(messageIds!==undefined&&(!Array.isArray(messageIds)||messageIds.length<1||messageIds.length>6||
    messageIds[0]!==messageId||new Set(messageIds).size!==messageIds.length||
    messageIds.some(id=>typeof id!=="string"||!/^[a-zA-Z0-9._:-]{1,160}$/.test(id))))
    throw Error("invalid_message_ids");
  if(intakeRevision!==undefined&&(!Number.isSafeInteger(intakeRevision)||intakeRevision<1))
    throw Error("invalid_intake_revision");
  if(requireSummaryReview!==undefined&&typeof requireSummaryReview!=='boolean')
    throw Error('invalid_summary_review');
  const contactProfile=cleanContactProfile(profile);
  const target=new URL(apiUrl);
  if(target.protocol!=="http:"||!target.hostname.endsWith(".railway.internal")||
     target.pathname!=="/internal/qr/ai/reply" || target.search || target.hash || target.username || target.password)
    throw Error("unsafe_ai_endpoint");
  const controller=new AbortController();
  const timeout=setTimeout(()=>controller.abort(),19000);
  try{
    const payload={phone,message_id:messageId,text,...(envelope?{envelope}:{}),
      ...(intakeRevision===undefined?{}:{intake_revision:intakeRevision}),
      ...(requireSummaryReview===undefined?{}:{require_summary_review:requireSummaryReview}),
      ...(messageIds?.length>1?{message_ids:messageIds}:{})};
    const post=body=>fetchImpl(target.toString(),{
      method:"POST",headers:{
        "Content-Type":"application/json",
        "X-Orion-Bridge-Key":token,
      },
      body:JSON.stringify(body),
      signal:controller.signal,
    });
    let response=await post({...payload,...(contactProfile?{profile:contactProfile}:{})});
    if(contactProfile&&!response.ok&&response.status===422){
      let rejection;
      try{rejection=await response.json();}catch{}
      const errors=rejection?.detail,only=Array.isArray(errors)&&errors.length===1?errors[0]:null;
      // An older StrictModel rejects this optional field before invoking the
      // handler. Only that exact zero-execution rejection permits one retry.
      if(only?.type!=='extra_forbidden'||!Array.isArray(only.loc)||only.loc.length!==2||
          only.loc[0]!=='body'||only.loc[1]!=='profile')throw Error('assistant_unavailable');
      if(controller.signal.aborted||isCurrent()!==true)throw Error('assistant_unavailable');
      response=await post(payload);
    }
    if(!response.ok)throw Error("assistant_unavailable");
    const result=await response.json();
    if(result?.step==='superseded'&&result?.answer===null&&result?.not_committed===true)
      return Object.freeze({state:'superseded',not_committed:true});
    if(result?.answer===null)return null;
    if(typeof result?.answer!=="string"||!result.answer.trim()||
       result.answer.length>1200)throw Error("invalid_ai_reply");
    return result.answer;
  }finally{clearTimeout(timeout)}
}

/** One authenticated notice after PN resolution; no media and no business action. */
export async function registerAssistantIngress({apiUrl,token,phone,messageId,control,fetchImpl=fetch}){
  if(typeof token!=='string'||token.length<32||typeof phone!=='string'||
      !/^55[1-9]\d(?:9\d{8}|[2-5]\d{7})$/.test(phone)||
      typeof messageId!=='string'||!/^[a-zA-Z0-9._:-]{1,160}$/.test(messageId))
    throw Error('invalid_ingress_notice');
  const target=new URL(apiUrl);
  if(target.protocol!=='http:'||!target.hostname.endsWith('.railway.internal')||
      target.pathname!=='/internal/qr/ai/reply'||target.search||target.hash||target.username||target.password)
    throw Error('unsafe_ai_endpoint');
  target.pathname='/internal/qr/ai/notice';
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),5000);
  try{
    const response=await fetchImpl(target.toString(),{method:'POST',headers:{'Content-Type':'application/json',
      'X-Orion-Bridge-Key':token},body:JSON.stringify({phone,message_id:messageId,...(control?{control}:{})}),signal:controller.signal});
    if(!response.ok)throw Error('ingress_notice_unavailable');
    const result=await response.json();
    if(!Number.isSafeInteger(result?.revision)||result.revision<1)throw Error('invalid_ingress_revision');
    return {revision:result.revision};
  }finally{clearTimeout(timeout);}
}
