import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {JSDOM,VirtualConsole} from 'jsdom';
import {flush,tenantA} from './harness.mjs';
export {flush,deferred,tenantA,tenantB} from './harness.mjs';
export const contract=JSON.parse(await readFile(new URL('../tests/fixtures/subscription_templates_contract.json',import.meta.url),'utf8'));
export const source=await readFile(new URL('../app/static/orion-subscription-templates.js',import.meta.url),'utf8');
export const copy=value=>JSON.parse(JSON.stringify(value));
export const base='/api/v1/tenants/'+tenantA+'/subscription-whatsapp/templates';
export const templateId=contract.detail_response.template.id;
export const assetId=contract.upload_response.asset.id;
export const failure=(status,message='Synthetic failure')=>Object.assign(Error(message),{status});
export const detail=(extra={})=>({...copy(contract.detail_response),chatbot_revision:7,...extra});
export const listing=(templates=[],extra={})=>({...copy(contract.list_response),templates:copy(templates),counts:{total:templates.length,filtered:templates.length,limit:100},...extra});
export function asset(kind='image',data=Uint8Array.from([255,216,255,217]),extra={}){
 const value=copy(contract.upload_response.asset),mimes={image:'image/jpeg',sticker:'image/webp',video:'video/mp4',ptv:'video/mp4',audio:'audio/mpeg',document:'text/plain; charset=utf-8'};
 return {...value,kind,mime:mimes[kind],size:data.byteLength,file_name:kind==='image'?'imagem.jpg':kind==='audio'?'audio.mp3':'arquivo.txt',content_url:base+'/assets/'+assetId+'/content?expected_chatbot_revision=7&variant=original',...extra};
}
export const binary=(data=Uint8Array.from([255,216,255,217]),type='image/jpeg',extra={})=>new Response(data,{status:200,...extra,headers:{'Content-Type':type,'Content-Length':String(data.byteLength),...extra.headers}});
export function mediaTemplate(kind='image',metadata=asset(kind)){
 const value=detail();Object.assign(value.template,{kind,text:kind==='sticker'||kind==='ptv'?'':'Legenda sintética',followup_text:kind==='sticker'||kind==='ptv'?'':'Mensagem posterior',asset_id:metadata.id,asset:metadata,file_name:metadata.file_name});return value;
}
export async function makeTemplates({data=listing(),record=detail(),respond,respondBinary}={}){
 const errors=[],calls=[],binaryCalls=[],downloads=[],objectURLs=new Map(),revokedURLs=[],timers=new Map(),virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',error=>errors.push(error));
 const dom=new JSDOM('<!doctype html><html lang="pt-BR" data-theme="light"><body><main class="os-main"><section id="templates"></section></main></body></html>',{url:'https://orion.test/painel?aba=templates',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole});
 const {window}=dom,{document}=window;let now=Date.parse('2026-11-04T15:00:00Z'),timerId=0;
 Object.defineProperty(window.crypto,'randomUUID',{value:randomUUID});window.HTMLElement.prototype.scrollIntoView=()=>{};window.HTMLElement.prototype.scrollTo=()=>{};window.scrollTo=()=>{};
 window.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};window.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');this.dispatchEvent(new window.Event('close'));};
 window.Blob=Blob;window.TextDecoder=TextDecoder;window.AbortController=AbortController;
 window.URL.createObjectURL=blob=>{const url='blob:https://orion.test/'+randomUUID();objectURLs.set(url,blob);return url;};window.URL.revokeObjectURL=url=>{revokedURLs.push(url);objectURLs.delete(url);};
 window.HTMLAnchorElement.prototype.click=function(){downloads.push({href:this.href,name:this.download,blob:objectURLs.get(this.href)});};
 window.fetch=async(path,options)=>{binaryCalls.push({path,options});assert.ok(path.startsWith(base+'/assets/'),'Only scoped private media may be read');return await respondBinary?.(path,options)??binary();};
 window.setTimeout=(fn,delay=0)=>{const id=++timerId;timers.set(id,{fn,at:now+delay});return id;};window.clearTimeout=id=>timers.delete(id);
 const server={data:copy(data),record:copy(record)};
 const bridge={host:document.getElementById('templates'),tenantId:tenantA,async request(path,method='GET',payload){calls.push({path,method,data:copy(payload??null)});const result=await respond?.(path,method,payload,server);if(result!==undefined)return copy(result);if(method==='GET'&&(path===base||path.startsWith(base+'?')))return copy(server.data);if(method==='GET'&&path===base+'/'+templateId)return copy(server.record);throw Error('Explicit fixture response required for '+method+' '+path);}};
 window.eval(await readFile(new URL('../app/static/orion-fields.js',import.meta.url),'utf8'));window.eval(source);const controller=window.OrionSubscriptionTemplates.mount(bridge);await flush();
 function element(selector){const node=document.querySelector(selector);assert.ok(node,'Missing '+selector);return node;}
 return {dom,window,document,controller,bridge,server,calls,binaryCalls,downloads,objectURLs,revokedURLs,timers,errors,element,
  click(selector){const node=element(selector);node.click();return node;},
  fill(selector,value){const node=element(selector);node.value=value;node.dispatchEvent(new window.Event('input',{bubbles:true}));return node;},
  submit(selector='[data-st-form=editor]'){element(selector).dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));},
  async file(contents=new Uint8Array([255,216,255,217]),name='imagem.jpg',size){const node=element('[data-st-file]'),file=new window.File([contents],name,{type:'application/octet-stream'});if(size!==undefined)Object.defineProperty(file,'size',{value:size});Object.defineProperty(node,'files',{configurable:true,value:[file]});node.dispatchEvent(new window.Event('change',{bubbles:true}));await flush();return file;},
  async tick(milliseconds){const target=now+milliseconds;for(let i=0;i<200;i++){const due=[...timers].filter(([,task])=>task.at<=target).sort((a,b)=>a[1].at-b[1].at)[0];if(!due)break;now=due[1].at;timers.delete(due[0]);await due[1].fn();await flush();}now=target;await flush();},
  close(){window.OrionSubscriptionTemplates.unmount();timers.clear();window.close();}
 };
}
