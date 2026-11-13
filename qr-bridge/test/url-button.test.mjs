import test from 'node:test';
import assert from 'node:assert/strict';
import * as sdk from '@whiskeysockets/baileys';
import {buildChatbotContent} from '../src/chatbot-message.mjs';
import {urlButtonContent,isUrlButtonMessage} from '../src/url-button.mjs';
import {nativeRelayOptions} from '../src/native-relay-options.mjs';
import {normalizeChatbotInput} from '../src/chatbot-input.mjs';

const action={type:'url_button',text:'Acesse seu sistema com segurança.',footer:'Orion',button_text:'Acessar meu sistema',url:'https://orionagenda.pro/'};
const options={sdk,interactiveMode:'text_fallback',loadAsset:()=>assert.fail('No asset'),upload:()=>assert.fail('No upload'),fetchImage:()=>assert.fail('No link preview/fetch')};
const wire=content=>sdk.proto.Message.decode(sdk.proto.Message.encode(content).finish());

test('actual pinned SDK serializes one native URL CTA with body/footer and no reply token or text fallback',async()=>{
  const built=await buildChatbotContent(action,options);
  try{
    const decoded=wire(built.content),value=decoded.interactiveMessage,flow=value.nativeFlowMessage;
    assert.equal(value.body.text,action.text);assert.equal(value.footer.text,action.footer);assert.equal(flow.messageVersion,1);
    assert.equal(flow.buttons.length,1);assert.equal(flow.buttons[0].name,'cta_url');
    assert.deepEqual(JSON.parse(flow.buttons[0].buttonParamsJson),{display_text:action.button_text,url:action.url});
    assert.equal(decoded.extendedTextMessage,null);assert.equal(decoded.listMessage,null);assert.equal(decoded.viewOnceMessage,null);
    assert.equal(value.carouselMessage,null);assert.equal(value.header,null);assert.equal(built.selectionIds,undefined);
    assert.equal(built.wireMode,'native');assert.equal(isUrlButtonMessage(decoded),true);
  }finally{built.dispose();}
});

test('optional footer is absent and an ordinary text action still builds an ordinary message',async()=>{
  const noFooter=urlButtonContent({...action,footer:undefined});assert.equal(noFooter.interactiveMessage.footer,undefined);
  const built=await buildChatbotContent({type:'text',text:'Mensagem existente'},options);
  try{assert.equal(wire(built.content).interactiveMessage,null);assert.equal(wire(built.content).extendedTextMessage.text,'Mensagem existente');}
  finally{built.dispose();}
});

test('URL syntax/private targets/credentials/fragments and contact placeholders are rejected without any request',async()=>{
  const urls=['http://orionagenda.pro/','javascript:alert(1)','file:///etc/passwd','https://user:pass@orionagenda.pro/',
    'https://localhost/','https://service.internal/','https://127.0.0.1/','https://0x7f000001/',
    'https://169.254.169.254/','https://10.0.0.1/','https://[::1]/','https://[::ffff:127.0.0.1]/',
    'https://orionagenda.pro/#token','https://orionagenda.pro/{phone_number}','https://orionagenda.pro/?q=%7Bname%7d',
    'https://orionagenda.pro/ bad','https://orionagenda.pro/\\bad','https://orionagenda.pro/\n','https://orionagenda.pro/%0a',
    'https://orionagenda.pro/%1F','https://orionagenda.pro/%7f','https://orionagenda.pro/\ud800','https://orionagenda.pro/'+'a'.repeat(2048)];
  for(const url of urls)await assert.rejects(buildChatbotContent({...action,url},options),/chatbot_message_invalid/);
});

test('native text/button/footer limits count UTF16 and reject broken Unicode and extra protocol fields',()=>{
  assert.ok(urlButtonContent({...action,text:'🙂'.repeat(512),button_text:'🙂'.repeat(10),footer:'🙂'.repeat(30)}));
  for(const patch of [{text:'🙂'.repeat(513)},{button_text:'🙂'.repeat(11)},{footer:'🙂'.repeat(31)},
    {text:''},{button_text:' '},{button_text:'Open\nsite'},{button_text:'Open\tsite'},{button_text:'Open\u007f'},
    {text:'broken\ud800'},{footer:'bad\u0000'},
    {title:'Unrequested header'},{buttons:[]},{additionalNodes:[{tag:'evil'}]},{selection_id:'ocb:fake'},
    {nativeFlowMessage:{buttons:[]}}, {url:null}])assert.throws(()=>urlButtonContent({...action,...patch}),/chatbot_message_invalid/);
});

test('only the typed one-URL-button shape receives fixed native-flow hints; arbitrary flows cannot inject nodes',()=>{
  const content=wire(urlButtonContent(action));
  assert.deepEqual(nativeRelayOptions(content),{additionalNodes:[{tag:'biz',attrs:{},content:[{tag:'interactive',attrs:{type:'native_flow',v:'1'},
    content:[{tag:'native_flow',attrs:{v:'9',name:'mixed'}}]}]}]});
  const clone=()=>JSON.parse(JSON.stringify(urlButtonContent(action)));
  for(const mutate of [value=>value.interactiveMessage.nativeFlowMessage.buttons.push({name:'cta_call'}),
    value=>value.interactiveMessage.nativeFlowMessage.buttons[0].name='quick_reply',
    value=>value.interactiveMessage.nativeFlowMessage.buttons[0].buttonParamsJson='{"display_text":"Open","url":"https://orionagenda.pro/","id":"forged"}',
    value=>value.interactiveMessage.nativeFlowMessage.buttons[0].buttonParamsJson='{"display_text":"Open","url":"https://127.0.0.1/"}',
    value=>value.interactiveMessage.nativeFlowMessage.messageParamsJson='{"arbitrary":"flow"}']){
    const value=clone();mutate(value);assert.equal(isUrlButtonMessage(value),false);assert.deepEqual(nativeRelayOptions(value),{});
  }
});

test('revocation stops URL-button preparation and a CTA click can never become a business selection',async()=>{
  await assert.rejects(buildChatbotContent(action,{...options,isCurrent:()=>false}),/revoked/);
  assert.equal(normalizeChatbotInput({key:{id:'click',fromMe:false},message:{interactiveResponseMessage:{contextInfo:{stanzaId:'URL-BUTTON'},
    nativeFlowResponseMessage:{name:'cta_url',paramsJson:JSON.stringify({url:action.url})}}}}),null);
});
