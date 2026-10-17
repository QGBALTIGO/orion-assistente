/** Single-recipient pilot gate. Never infer an allowed number from a LID. */
const BR_MOBILE = /^55[1-9]\d9\d{8}$/;
export function pilotNumber(env=process.env){
  const value=env.QR_AI_PILOT_NUMBER||"";
  return BR_MOBILE.test(value)?value:null;
}
export function allowAssistantRecipient(phone,settings,env=process.env){
  if(env.QR_AI_ENABLED!=="1"||!env.QR_AI_API_URL)return false;
  if(env.QR_AI_PILOT_NUMBER!==undefined && env.QR_AI_PILOT_NUMBER!==""){
    const permitted=pilotNumber(env);
    return permitted!==null && phone===permitted;
  }
  return settings?.chatbot===true;
}
export function allowAssistantAudio(phone,settings,env=process.env){
  if(env.QR_AI_AUDIO_ENABLED!=="1"||!allowAssistantRecipient(phone,settings,env))return false;
  return settings?.transcription===true || pilotNumber(env)===phone;
}
