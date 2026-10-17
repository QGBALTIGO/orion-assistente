/** Encaminha apenas mensagem privada recém-recebida para a API Orion autorizada. */
export async function forwardAssistant({apiUrl,token,phone,messageId,text,fetchImpl=fetch}){
  if(typeof token!=="string"||token.length<32)throw Error("bridge_key_missing");
  if(typeof phone!=="string"||!/^55[1-9]\d(?:9\d{8}|[2-5]\d{7})$/.test(phone))throw Error("invalid_recipient");
  if(typeof messageId!=="string"||! /^[a-zA-Z0-9._:-]{1,160}$/.test(messageId))throw Error("invalid_message_id");
  if(typeof text!=="string"||!text.trim())return null;
  const target=new URL(apiUrl);
  if(target.protocol!=="http:"||!target.hostname.endsWith(".railway.internal")||
     target.pathname!=="/internal/qr/ai/reply" || target.search || target.hash)
    throw Error("unsafe_ai_endpoint");
  const controller=new AbortController();
  const timeout=setTimeout(()=>controller.abort(),19000);
  try{
    const response=await fetchImpl(target.toString(),{
      method:"POST",headers:{
        "Content-Type":"application/json",
        "X-Orion-Bridge-Key":token,
      },
      body:JSON.stringify({phone,message_id:messageId,text:text.slice(0,1500)}),
      signal:controller.signal,
    });
    if(!response.ok)throw Error("assistant_unavailable");
    const result=await response.json();
    if(result?.answer===null)return null;
    if(typeof result?.answer!=="string"||!result.answer.trim()||
       result.answer.length>1200)throw Error("invalid_ai_reply");
    return result.answer;
  }finally{clearTimeout(timeout)}
}
