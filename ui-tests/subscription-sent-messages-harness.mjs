import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {randomUUID,webcrypto,createHash} from 'node:crypto';
import {JSDOM,VirtualConsole} from 'jsdom';
import {flush,tenantA} from './harness.mjs';
export {flush,deferred,tenantA,tenantB} from './harness.mjs';
export const contract=JSON.parse(await readFile(new URL('../tests/fixtures/subscription_sent_messages_contract.json',import.meta.url),'utf8'));
export const source=await readFile(new URL('../app/static/orion-subscription-sent-messages.js',import.meta.url),'utf8');
export const copy=value=>JSON.parse(JSON.stringify(value));
export const base='/api/v1/tenants/'+tenantA+'/subscription-whatsapp/sent-messages';
export const accountKey='synthetic@example.invalid|member';
export const accountScope=createHash('sha256').update(accountKey).digest('hex');
export const failure=(status,message='Synthetic failure')=>Object.assign(Error(message),{status});
export const item=(status='failed',extra={})=>({...copy(contract.item_examples[status]),...extra});
export const listing=(items=contract.list_response.items,extra={})=>({...copy(contract.list_response),items:copy(items),filtered_total:items.length,total_pages:Math.max(1,Math.ceil(items.length/30)),...extra});
export const detail=(record=item(),extra={})=>({...copy(contract.detail_response),item:copy(record),...extra});
export const operation=(payload,extra={})=>({...copy(contract.resend_response),request_id:'83000000-0000-4000-8000-000000000001',...extra});
export function recovery(extra={}) {const record=item();return {tenant_id:tenantA,account_scope:accountScope,idempotency_key:contract.resend_request.idempotency_key,original_effect_id:record.id,root_effect_id:record.root_effect_id,expected_version:record.version,...extra};}
export async function makeMessages({data=listing(),record,respond,url='https://orion.test/panel?aba=mensagens-enviadas',historyState,wait=true,identity=accountKey,canWrite=true}={}) {
  const errors=[],calls=[],external=[],timers=new Map(),console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error));
  const dom=new JSDOM('<!doctype html><html lang="pt-BR" data-theme="light"><body><main class="os-main"><section id="messages"></section></main></body></html>',{url,runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:console});
  const {window}=dom,{document}=window;let now=0,timerId=0;
  Object.defineProperty(window.crypto,'randomUUID',{value:randomUUID});Object.defineProperty(window.crypto,'subtle',{value:webcrypto.subtle});window.TextEncoder=TextEncoder;
  window.HTMLElement.prototype.scrollIntoView=()=>{};window.HTMLElement.prototype.scrollTo=()=>{};window.scrollTo=()=>{};
  window.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};window.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');this.dispatchEvent(new window.Event('close'));};
  window.fetch=async(...args)=>{external.push(args);throw Error('No direct fetch is allowed in message history');};
  window.setTimeout=(fn,delay=0)=>{const id=++timerId;timers.set(id,{fn,at:now+delay});return id;};window.clearTimeout=id=>timers.delete(id);
  if(historyState)window.history.replaceState(copy(historyState),'',window.location.href);
  const server={data:copy(data),record:record?copy(record):null,records:new Map(data.items.map(value=>[value.id,copy(value)]))},access={allowed:true,writable:canWrite};
  const bridge={host:document.getElementById('messages'),tenantId:tenantA,accountKey:identity,canAccess:()=>access.allowed,canWrite:()=>access.writable,async request(path,method='GET',payload){calls.push({path,method,data:payload?copy(payload):null});const result=await respond?.(path,method,payload,server);if(result!==undefined)return copy(result);if(method==='GET'&&path.startsWith(base+'?'))return copy(server.data);const id=path.slice(base.length+1);if(method==='GET'&&server.records.has(id))return detail(server.record||server.records.get(id));throw Error('Explicit fixture response required for '+method+' '+path);}};
  window.eval(await readFile(new URL('../app/static/orion-fields.js',import.meta.url),'utf8'));window.eval(source);const controller=window.OrionSubscriptionSentMessages.mount(bridge);
  if(wait)await controller.ready;else await flush();
  function element(selector){const node=document.querySelector(selector);assert.ok(node,'Missing '+selector);return node;}
  return {dom,window,document,controller,bridge,server,access,calls,external,timers,errors,element,
    click(selector){const node=element(selector);node.click();return node;},
    fill(selector,value){const node=element(selector);node.value=value;node.dispatchEvent(new window.Event('input',{bubbles:true}));return node;},
    change(selector,value){const node=element(selector);node.value=value;node.dispatchEvent(new window.Event('change',{bubbles:true}));return node;},
    submit(){element('[data-sm-form=filters]').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));},
    async tick(milliseconds){const target=now+milliseconds;for(let i=0;i<100;i++){const next=[...timers].filter(([,value])=>value.at<=target).sort((a,b)=>a[1].at-b[1].at)[0];if(!next)break;now=next[1].at;timers.delete(next[0]);await next[1].fn();await flush();}now=target;await flush();},
    close(){window.OrionSubscriptionSentMessages.unmount();timers.clear();window.close();}
  };
}
