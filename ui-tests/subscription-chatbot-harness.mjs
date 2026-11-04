import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {JSDOM,VirtualConsole} from 'jsdom';
import {flush,tenantA} from './harness.mjs';
export {flush,deferred,tenantA,tenantB} from './harness.mjs';
export const contract=JSON.parse(await readFile(new URL('../tests/fixtures/subscription_chatbot_contract.json',import.meta.url),'utf8'));
export const copy=value=>JSON.parse(JSON.stringify(value));
export const ruleId=contract.detail_response.rule.id;
export const deviceId=contract.catalog_response.devices[0].id;
export const backupId=contract.backups_response.items[0].id;
export const jobId=contract.job_response.job.id;
export const previewId=contract.preview_response.preview.id;
export const base='/api/v1/tenants/'+tenantA+'/subscription-whatsapp/chatbot';
export const failure=(status,message='Synthetic failure')=>Object.assign(Error(message),{status});
export const detail=(extra={})=>({...copy(contract.detail_response),...extra});
export const listing=(extra={})=>({...copy(contract.list_response),pending_job:null,...extra});
export const catalog=(extra={})=>({...copy(contract.catalog_response),...extra});
export const preview=(extra={})=>({preview:{...copy(contract.preview_response.preview),expires_at:'2026-11-04T00:15:00Z',...extra},chatbot_revision:1});
export const job=(status='queued',kind='restore',extra={})=>({job:{...copy(contract.job_response.job),kind,status,source:kind==='restore'?'backup':null,...extra}});
export async function makeChatbot({data=listing(),configuration=catalog(),rule=detail(),respond}={}){
  const errors=[],calls=[],downloads=[],timers=new Map(),console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error));
  const dom=new JSDOM('<!doctype html><html lang="pt-BR" data-theme="light"><body><main class="os-main"><section id="chatbot"></section></main></body></html>',{url:'https://orion.test/painel?aba=chatbot',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:console});
  const {window}=dom,{document}=window;let now=Date.parse('2026-11-04T00:00:00Z'),timerId=0,hidden=false;
  window.Date.now=()=>now;Object.defineProperty(document,'hidden',{get:()=>hidden});Object.defineProperty(window.crypto,'randomUUID',{value:randomUUID});
  window.HTMLElement.prototype.scrollIntoView=()=>{};window.HTMLElement.prototype.scrollTo=()=>{};window.scrollTo=()=>{};
  window.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};window.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');this.dispatchEvent(new window.Event('close'));};
  window.HTMLAnchorElement.prototype.click=function(){downloads.push({href:this.href,download:this.download});};
  window.fetch=()=>{throw Error('The Chatbot harness refuses all network requests');};
  window.setTimeout=(fn,delay=0)=>{const id=++timerId;timers.set(id,{fn,at:now+delay});return id;};window.clearTimeout=id=>timers.delete(id);
  const server={data:copy(data),catalog:copy(configuration),rule:copy(rule),backups:copy(contract.backups_response)};
  server.backups.items.forEach(item=>item.download_url=base+'/backups/'+item.id+'/download');
  const bridge={host:document.getElementById('chatbot'),tenantId:tenantA,async request(path,method='GET',payload){calls.push({path,method,data:copy(payload??null)});const result=await respond?.(path,method,payload,server);if(result!==undefined)return copy(result);if(method!=='GET')throw Error('An explicit fixture response is required for '+method+' '+path);if(path.startsWith(base+'?'))return copy(server.data);if(path===base+'/catalog')return copy(server.catalog);if(path===base+'/rules/'+ruleId)return copy(server.rule);if(path.startsWith(base+'/backups?'))return copy(server.backups);throw Error('Unexpected synthetic read '+path);}};
  for(const file of ['orion-fields.js','orion-subscription-chatbot.js'])window.eval(await readFile(new URL('../app/static/'+file,import.meta.url),'utf8'));
  const controller=window.OrionSubscriptionChatbot.mount(bridge);await flush();
  function element(selector){const node=document.querySelector(selector);assert.ok(node,'Missing '+selector);return node;}
  return {dom,window,document,controller,server,calls,downloads,timers,errors,element,
    click(selector){const node=element(selector);node.click();return node;},
    fill(selector,value){const node=element(selector);node.value=value;node.dispatchEvent(new window.Event('input',{bubbles:true}));return node;},
    change(selector,value){const node=element(selector);node.value=value;node.dispatchEvent(new window.Event('change',{bubbles:true}));return node;},
    check(selector,value=true){const node=element(selector);node.checked=value;node.dispatchEvent(new window.Event('change',{bubbles:true}));return node;},
    submit(selector){element(selector).dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));},
    async expand(){const node=element('[data-sc-details="'+ruleId+'"]');node.open=true;node.dispatchEvent(new window.Event('toggle'));await flush();},
    async file(contents='{"format":"orion"}',name='orion-chatbot.json',size){const node=element('[data-sc-import-file]'),file=new window.File([contents],name,{type:'application/json'});if(size)Object.defineProperty(file,'size',{value:size});Object.defineProperty(node,'files',{configurable:true,value:[file]});node.dispatchEvent(new window.Event('change',{bubbles:true}));await flush();return file;},
    async tick(milliseconds){const target=now+milliseconds;for(let attempt=0;attempt<200;attempt++){const due=[...timers].filter(([,task])=>task.at<=target).sort((a,b)=>a[1].at-b[1].at)[0];if(!due)break;now=due[1].at;timers.delete(due[0]);await due[1].fn();await flush();}now=target;await flush();},
    async visibility(value){hidden=value;document.dispatchEvent(new window.Event('visibilitychange'));await flush();},
    close(){window.OrionSubscriptionChatbot.unmount();timers.clear();window.close();}
  };
}
