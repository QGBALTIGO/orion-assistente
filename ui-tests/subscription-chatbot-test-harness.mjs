import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {JSDOM,VirtualConsole} from 'jsdom';
import {flush,tenantA} from './harness.mjs';
export {flush,deferred,tenantA,tenantB} from './harness.mjs';
export const contract=JSON.parse(await readFile(new URL('../tests/fixtures/subscription_chatbot_test_contract.json',import.meta.url),'utf8'));
export const source=await readFile(new URL('../app/static/orion-subscription-chatbot-test.js',import.meta.url),'utf8');
export const copy=value=>JSON.parse(JSON.stringify(value));
export const base='/api/v1/tenants/'+tenantA+'/subscription-whatsapp/chatbot-test';
export const deviceId=contract.bootstrap_response.devices[0].id;
export const ruleId=contract.simulate_response.matched_rule.id;
export const rowId=contract.message_examples.list.sections[0].rows[0].id;
export const failure=(status,message='Synthetic failure')=>Object.assign(Error(message),{status});
export const bootstrap=(extra={})=>({...copy(contract.bootstrap_response),...extra});
export const message=(type,extra={})=>({...copy(contract.message_examples[type]),id:randomUUID(),...extra});
export function response(payload,extra={}){
  const value=copy(contract.simulate_response);
  return {...value,request_id:payload.request_id,context_id:payload.context_id,chatbot_revision:payload.expected_chatbot_revision,
    device:{...value.device,id:payload.device_id},input:{type:payload.input.type,text:payload.input.type==='text'?payload.input.text.trim():payload.input.type==='rule'?'/start':'Informações',rule_id:payload.input.rule_id??null,row_id:payload.input.row_id??null},messages:[message('text')],...extra};
}
export function media(kind='image',bytes=Uint8Array.from([0xff,0xd8,0xff,0xd9]),extra={}){
  const value=message('media',{kind,caption:'Arquivo sintético'}),asset=value.asset;
  Object.assign(asset,{kind,mime:contract.media_types[kind].mime,size:bytes.byteLength,thumbnail_url:null,thumbnail_mime:null,thumbnail_size:null,...extra});
  return {value,bytes};
}
export function binary(bytes,contentType='image/jpeg',options={}){
  return new Response(bytes,{status:200,...options,headers:{'Content-Type':contentType,'Content-Length':String(bytes.byteLength),...options.headers}});
}
export async function makeChatbotTest({data=bootstrap(),respond,respondBinary,url='https://orion.test/painel?aba=testar-chatbot',initialDeviceId,initialRuleId}={}){
  const errors=[],calls=[],binaryCalls=[],objectURLs=new Map(),revokedURLs=[],timers=new Map(),console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error));
  const dom=new JSDOM('<!doctype html><html lang="pt-BR" data-theme="light"><body><aside id="outside"><button>Menu</button></aside><main class="os-main"><section id="tester"></section></main></body></html>',{url,runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:console});
  const {window}=dom,{document}=window;let now=Date.parse('2026-11-04T15:00:00Z'),timerId=0;
  window.Date.now=()=>now;Object.defineProperty(window.crypto,'randomUUID',{value:randomUUID});
  window.HTMLElement.prototype.scrollTo=()=>{};window.HTMLElement.prototype.scrollIntoView=()=>{};window.scrollTo=()=>{};
  window.Blob=Blob;window.TextDecoder=TextDecoder;window.AbortController=AbortController;
  window.URL.createObjectURL=blob=>{const url='blob:https://orion.test/'+randomUUID();objectURLs.set(url,blob);return url;};
  window.URL.revokeObjectURL=url=>{revokedURLs.push(url);objectURLs.delete(url);};
  window.setTimeout=(fn,delay=0)=>{const id=++timerId;timers.set(id,{fn,at:now+delay});return id;};window.clearTimeout=id=>timers.delete(id);
  window.fetch=async(path,options)=>{binaryCalls.push({path,options});assert.ok(path.startsWith(base+'/assets/'),'Only private fixture media reads are permitted');const response=await respondBinary?.(path,options);if(response===undefined)throw Error('An explicit fixture binary response is required');return response;};
  const server={data:copy(data)},params=new URL(url).searchParams;
  const bridge={host:document.getElementById('tester'),tenantId:tenantA,initialDeviceId:initialDeviceId===undefined?params.get('device_id'):initialDeviceId,initialRuleId:initialRuleId===undefined?params.get('reply_id'):initialRuleId,async request(path,method='GET',payload){calls.push({path,method,data:copy(payload??null)});const result=await respond?.(path,method,payload,server);if(result!==undefined)return copy(result);if(path===base&&method==='GET')return copy(server.data);if(path===base+'/simulate'&&method==='POST')return response(payload);throw Error('Unexpected synthetic request '+method+' '+path);}};
  window.eval(source);const controller=window.OrionSubscriptionChatbotTest.mount(bridge);await flush();
  function element(selector){const value=document.querySelector(selector);assert.ok(value,'Missing '+selector);return value;}
  return {dom,window,document,controller,bridge,server,calls,binaryCalls,objectURLs,revokedURLs,timers,errors,element,
    click(selector){const node=element(selector);node.click();return node;},
    fill(value){const node=element('[data-sct-input]');node.value=value;node.dispatchEvent(new window.Event('input',{bubbles:true}));return node;},
    select(value){const node=element('[data-sct-device]');node.value=value;node.dispatchEvent(new window.Event('change',{bubbles:true}));return node;},
    submit(){element('[data-sct-form]').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));},
    key(key,options={}){const node=element('[data-sct-input]');node.dispatchEvent(new window.KeyboardEvent('keydown',{key,bubbles:true,cancelable:true,...options}));},
    async send(text){this.fill(text);this.submit();await flush();},
    async tick(milliseconds){const target=now+milliseconds;for(let attempt=0;attempt<200;attempt++){const due=[...timers].filter(([,task])=>task.at<=target).sort((a,b)=>a[1].at-b[1].at)[0];if(!due)break;now=due[1].at;timers.delete(due[0]);await due[1].fn();await flush();}now=target;await flush();},
    close(){window.OrionSubscriptionChatbotTest.unmount();timers.clear();window.close();}
  };
}
