/** One explicitly requested URL button. No link preview, remote fetch,
 * recipient selection, business callback or caller-supplied protocol nodes. */
import {publicDestination} from './device-network.mjs';

const invalid=()=>Object.assign(Error('chatbot_message_invalid'),{code:'CHATBOT_MESSAGE_INVALID'});
const fields=new Set(['type','text','footer','button_text','url']);
function label(value,max,{optional=false,singleLine=false}={}){
  if(optional&&value===undefined)return '';
  if(typeof value!=='string'||value.length>max||(!optional&&!value.trim())||(singleLine&&/[\u0000-\u001f\u007f]/u.test(value))||
      /[\u0000]|[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value))throw invalid();
  return value;
}
function destination(value){
  if(typeof value!=='string'||/[{}]|%7[bd]|%[01][0-9a-f]|%7f/i.test(value)||
      /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value))throw invalid();
  // This is a client-opened URL, never a server request. Validate its literal
  // syntax/public host without claiming to trust a future DNS response.
  try{publicDestination(value);}catch{throw invalid();}
  return value;
}
export function urlButtonContent(action){
  if(!action||typeof action!=='object'||Array.isArray(action)||action.type!=='url_button'||
      Object.keys(action).some(key=>!fields.has(key)))throw invalid();
  const text=label(action.text,1024),footer=label(action.footer,60,{optional:true}),
    display_text=label(action.button_text,20,{singleLine:true}),url=destination(action.url);
  return {interactiveMessage:{body:{text},...(footer?{footer:{text:footer}}:{}),
    nativeFlowMessage:{buttons:[{name:'cta_url',buttonParamsJson:JSON.stringify({display_text,url})}],messageVersion:1}}};
}

/** Recognize only the exact typed shape we create before adding fixed hints.
 * Arbitrary native flows/JSON/nodes cannot opt themselves into this path. */
export function isUrlButtonMessage(message){
  try{
    const value=message?.interactiveMessage,flow=value?.nativeFlowMessage,buttons=flow?.buttons;
    if(value?.header||value?.carouselMessage||value?.shopStorefrontMessage||value?.collectionMessage||
        (flow?.messageParamsJson!=null&&flow.messageParamsJson!=='')||
        !Array.isArray(buttons)||buttons.length!==1||buttons[0]?.name!=='cta_url'||flow.messageVersion!==1||
        typeof buttons[0].buttonParamsJson!=='string'||buttons[0].buttonParamsJson.length>8192)return false;
    const params=JSON.parse(buttons[0].buttonParamsJson);
    if(!params||typeof params!=='object'||Array.isArray(params)||Object.keys(params).length!==2||
        !Object.hasOwn(params,'display_text')||!Object.hasOwn(params,'url'))return false;
    const expected=urlButtonContent({type:'url_button',text:value.body?.text,footer:value.footer?.text??'',
      button_text:params.display_text,url:params.url});
    return expected.interactiveMessage.nativeFlowMessage.buttons[0].buttonParamsJson===buttons[0].buttonParamsJson;
  }catch{return false;}
}
