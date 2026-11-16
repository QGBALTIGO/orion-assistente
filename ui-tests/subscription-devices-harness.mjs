import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {JSDOM,VirtualConsole} from 'jsdom';
export {flush,deferred,tenantA,tenantB} from './harness.mjs';
import {flush,tenantA} from './harness.mjs';

export const contract=JSON.parse(await readFile(new URL('../tests/fixtures/subscription_devices_contract.json',import.meta.url),'utf8'));
export const copy=value=>JSON.parse(JSON.stringify(value));
export const deviceId=contract.list_response.items[0].id;
export const operationId='78787878-7878-4787-8787-787878787878';
export const base='/api/v1/tenants/'+tenantA+'/subscription-whatsapp/devices';
export const png='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/cL8AAAAASUVORK5CYII=';
export const failure=(status,message='Synthetic failure')=>Object.assign(Error(message),{status});
export function device(extra={}){
  return {...copy(contract.list_response.items[0]),features:{transcription:false,call_blocking:false},...extra};
}
export function listing(items=[device()],extra={}){
  return {...copy(contract.list_response),items,usage:{used:items.length,limit:1},can_create:items.length===0,...extra};
}
export function settings(extra={}){
  return {...copy(contract.settings_response),...extra};
}
export function ready(extra={}){
  const item=device();
  return {...item,status:'qr_ready',capabilities:{...item.capabilities,connect:false,qr:true,pair:true,restart:true,relink:true},...extra};
}
export function operation(action,item=device(),status='pending',extra={}){
  return {operation:{id:operationId,device_id:item?.id||deviceId,action,status,error:null,...extra},device:item};
}

export async function makeDevices({data=listing(),configuration=settings(),respond}={}){
  const errors=[],calls=[],copied=[],timers=new Map(),console=new VirtualConsole();
  console.on('jsdomError',error=>errors.push(error));
  const dom=new JSDOM('<!doctype html><html lang="pt-BR" data-theme="light"><body><main class="os-main"><section id="devices"></section></main></body></html>',{url:'https://orion.test/painel?aba=dispositivos',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:console});
  const {window}=dom,{document}=window;
  let now=0,timerId=0,hidden=false;
  window.Date.now=()=>now;
  Object.defineProperty(document,'hidden',{get:()=>hidden});
  Object.defineProperty(window.crypto,'randomUUID',{value:randomUUID});
  Object.defineProperty(window.navigator,'clipboard',{value:{writeText:async value=>copied.push(value)}});
  window.HTMLElement.prototype.scrollIntoView=()=>{};
  window.HTMLElement.prototype.scrollTo=()=>{};
  window.scrollTo=()=>{};
  window.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};
  window.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');this.dispatchEvent(new window.Event('close'));};
  window.fetch=()=>{throw Error('The device harness refuses all network requests');};
  window.setTimeout=(fn,delay=0)=>{const id=++timerId;timers.set(id,{fn,at:now+delay});return id;};
  window.clearTimeout=id=>timers.delete(id);
  const server={data:copy(data),settings:copy(configuration)};
  const bridge={host:document.getElementById('devices'),tenantId:tenantA,
    async request(path,method='GET',payload){
      calls.push({path,method,data:copy(payload??null)});
      const result=await respond?.(path,method,payload,server);
      if(result!==undefined)return copy(result);
      if(method!=='GET')throw Error('An explicit fixture response is required for '+method+' '+path);
      if(path===base)return copy(server.data);
      if(path===base+'/'+deviceId)return {device:copy(server.data.items.find(item=>item.id===deviceId))};
      if(path===base+'/'+deviceId+'/settings')return copy(server.settings);
      if(path===base+'/'+deviceId+'/qr')return {device_id:deviceId,revision:server.data.items[0].revision,image:png,expires_in_ms:45000};
      throw Error('Unexpected synthetic read '+path);
    }};
  for(const file of ['orion-fields.js','orion-connect.js','orion-subscription-devices.js'])window.eval(await readFile(new URL('../app/static/'+file,import.meta.url),'utf8'));
  const controller=window.OrionSubscriptionDevices.mount(bridge);
  await flush();
  function element(selector){const node=document.querySelector(selector);assert.ok(node,'Missing '+selector);return node;}
  return {dom,window,document,controller,server,calls,copied,timers,errors,element,
    click(selector){const node=element(selector);node.click();return node;},
    fill(selector,value){const node=element(selector);node.value=value;node.dispatchEvent(new window.Event('input',{bubbles:true}));return node;},
    change(selector,value){const node=element(selector);node.value=value;node.dispatchEvent(new window.Event('change',{bubbles:true}));return node;},
    check(selector,value=true){const node=element(selector);node.checked=value;node.dispatchEvent(new window.Event('change',{bubbles:true}));return node;},
    submit(selector){element(selector).dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));},
    async tick(milliseconds){
      const target=now+milliseconds;
      for(let attempt=0;attempt<200;attempt++){
        const due=[...timers].filter(([,job])=>job.at<=target).sort((a,b)=>a[1].at-b[1].at)[0];if(!due)break;
        now=due[1].at;timers.delete(due[0]);await due[1].fn();await flush();
      }
      now=target;await flush();
    },
    async visibility(value){hidden=value;document.dispatchEvent(new window.Event('visibilitychange'));await flush();},
    close(){window.OrionSubscriptionDevices.unmount();timers.clear();window.close();}
  };
}
