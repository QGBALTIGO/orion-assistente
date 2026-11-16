import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {randomUUID,webcrypto,createHash} from 'node:crypto';
import {JSDOM,VirtualConsole} from 'jsdom';
import {flush,tenantA} from './harness.mjs';
export {flush,deferred,tenantA,tenantB} from './harness.mjs';
export const copy=value=>JSON.parse(JSON.stringify(value));
export const contract=JSON.parse(await readFile(new URL('../tests/fixtures/subscription_diagnostics_contract.json',import.meta.url),'utf8'));
export const source=await readFile(new URL('../app/static/orion-subscription-diagnostics.js',import.meta.url),'utf8');
export const base='/api/v1/tenants/'+tenantA+'/subscription-whatsapp/diagnostics';
export const device=contract.detail_response.device.id;
export const accountKey='synthetic@example.invalid|member';
export const accountScope=createHash('sha256').update(accountKey).digest('hex');
export const failure=(status,message='Synthetic private error')=>Object.assign(Error(message),{status});
export const report=(extra={})=>({...copy(contract.detail_response),...extra});
export const listing=(extra={})=>({...copy(contract.list_response),...extra});
export const operation=(name='prepare_response',extra={})=>({...copy(contract[name]),...extra});
export const recovery=(extra={})=>({tenant_id:tenantA,account_scope:accountScope,idempotency_key:contract.prepare_request.idempotency_key,device_id:device,kind:'message_test',expected_version:contract.prepare_request.expected_version,...extra});
export const selectedURL='https://orion.test/panel?aba=diagnosticos&device_id='+device;
export async function makeDiagnostics({data=report(),devices=listing(),respond,url=selectedURL,historyState,wait=true,identity=accountKey,canWrite=true,cryptoEnabled=true}={}) {
  const errors=[],calls=[],external=[],console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error));
  const dom=new JSDOM('<!doctype html><html lang="pt-BR"><body><main><section id="diagnostics"></section></main></body></html>',{url,runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:console});
  const {window}=dom,{document}=window;
  Object.defineProperty(window.crypto,'randomUUID',{value:randomUUID});if(cryptoEnabled)Object.defineProperty(window.crypto,'subtle',{value:webcrypto.subtle});window.TextEncoder=TextEncoder;
  window.HTMLElement.prototype.scrollIntoView=()=>{};window.scrollTo=()=>{};
  window.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};window.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');this.dispatchEvent(new window.Event('close'));};
  window.fetch=async(...args)=>{external.push(args);throw Error('Direct fetch is forbidden');};
  if(historyState)window.history.replaceState(copy(historyState),'',window.location.href);
  const server={data:copy(data),devices:copy(devices),requests:new Map()},access={allowed:true,writable:canWrite};
  const bridge={host:document.getElementById('diagnostics'),tenantId:tenantA,accountKey:identity,canAccess:()=>access.allowed,canWrite:()=>access.writable,async request(path,method='GET',payload){
    calls.push({path,method,data:payload?copy(payload):null});const custom=await respond?.(path,method,payload,server);if(custom!==undefined)return copy(custom);
    if(method==='GET'&&path===base)return copy(server.devices);
    if(method==='GET'&&path===base+'/'+device)return copy(server.data);
    if(method==='GET'&&path.startsWith(base+'/requests/')){const value=server.requests.get(path.split('/').at(-1));if(!value)throw failure(404);return copy(value);}
    if(method==='POST'&&path.endsWith('/test-messages/prepare')){const value=operation('prepare_response',{idempotency_key:payload.idempotency_key});if(payload.phone)value.preview.recipient={mode:'explicit',phone_e164:payload.phone};server.requests.set(payload.idempotency_key,value);return copy(value);}
    if(method==='POST'&&path.endsWith('/confirm')){const prepared=server.requests.get(payload.idempotency_key);assert.ok(prepared);const value=operation('confirm_response',{idempotency_key:payload.idempotency_key,preview:copy(prepared.preview)});server.requests.set(payload.idempotency_key,value);return copy(value);}
    if(method==='POST'&&path.endsWith('/proxy-tests')){const value=operation('proxy_response',{idempotency_key:payload.idempotency_key});server.requests.set(payload.idempotency_key,value);return copy(value);}
    throw failure(404);
  }};
  window.eval(await readFile(new URL('../app/static/orion-fields.js',import.meta.url),'utf8'));window.eval(source);const controller=window.OrionSubscriptionDiagnostics.mount(bridge);
  if(wait)await controller.ready;else await flush();
  function element(selector){const node=document.querySelector(selector);assert.ok(node,'Missing '+selector);return node;}
  return {dom,window,document,controller,bridge,server,access,calls,external,errors,element,
    click(selector){const node=element(selector);node.click();return node;},
    fill(selector,value){const node=element(selector);node.value=value;node.dispatchEvent(new window.Event('input',{bubbles:true}));return node;},
    change(selector,value){const node=element(selector);node.value=value;node.dispatchEvent(new window.Event('change',{bubbles:true}));return node;},
    submit(){element('[data-dg-form=test-message]').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));},
    close(){window.OrionSubscriptionDiagnostics.unmount();window.close();}
  };
}
