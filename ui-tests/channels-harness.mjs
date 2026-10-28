import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {JSDOM,VirtualConsole} from 'jsdom';
export {flush,deferred} from './harness.mjs';
export const tenantA='10000000-0000-4000-8000-000000000001',tenantB='20000000-0000-4000-8000-000000000002',channelA='30000000-0000-4000-8000-000000000003',channelB='40000000-0000-4000-8000-000000000004',owner='50000000-0000-4000-8000-000000000005';
export const copy=value=>JSON.parse(JSON.stringify(value));
export const png='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/cL8AAAAASUVORK5CYII=';
export function item(overrides={}){return {id:channelA,tenant_id:tenantA,owner_account_id:owner,provider:'baileys',label:'Canal sintético',enabled:false,assistant_enabled:false,revision:1,can_manage:true,assistant_activation_allowed:true,allowed_senders:['5567999999901'],...overrides};}
export function listing(items=[item()],overrides={}){const connection=items.length===1?items[0]:null;const canonical_state=items.length>1?'ambiguous':connection?'ready':'missing';const legacy=connection?.legacy_transport===true;return {items:connection?[connection]:[],connection,canonical_id:connection?.id||null,canonical_state,access:{can_manage:true},capabilities:{provision:!connection&&canonical_state==='missing',start:!!connection&&!legacy,stop:!!connection&&!legacy,qr:!!connection&&!legacy,pair:!!connection&&!legacy,prepare:false,recover:false,change_state:!!connection&&!legacy,change_assistant:!!connection,edit_label:false,edit_allowlist:false},...overrides};}
export function runtime(row=item(),status='stopped'){return {channel_id:row.id,revision:row.revision,enabled:row.enabled,assistant_enabled:row.assistant_enabled,status};}
export async function makeChannels({data=listing(),respond,hidden=false}={}){
  const errors=[],logs=[],calls=[],copied=[],legacy=[],statuses=[],timers=new Map();let clock=0,timerId=0,pageHidden=false;
  const console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error));
  const dom=new JSDOM('<!doctype html><html lang="pt-BR" data-theme="light"><body><aside id="legacy">Conector existente</aside><main id="parent"><section id="channels"></section></main></body></html>',{url:'https://orion.test/panel',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:console});
  const {window}=dom,{document}=window;document.getElementById('channels').hidden=hidden;
  Object.defineProperty(document,'hidden',{get:()=>pageHidden});Object.defineProperty(window.crypto,'randomUUID',{value:randomUUID});
  Object.defineProperty(window.navigator,'clipboard',{value:{writeText:async value=>copied.push(value)}});
  window.fetch=()=>{throw Error('The isolated harness refuses all network requests');};
  window.setTimeout=(fn,delay=0)=>{const id=++timerId;timers.set(id,{fn,at:clock+delay});return id;};window.clearTimeout=id=>timers.delete(id);
  window.Date.now=()=>clock;
  window.console.log=(...args)=>logs.push(args);window.console.warn=(...args)=>logs.push(args);window.console.error=(...args)=>logs.push(args);
  for(const storage of [window.localStorage,window.sessionStorage]){storage.clear();}
  const server={data:copy(data)};
  const bridge={host:document.getElementById('channels'),tenantId:tenantA,
    async request(path,method='GET',payload){calls.push({path,method,data:copy(payload??null)});const result=await respond?.(path,method,payload,server);if(result!==undefined)return copy(result);if(method!=='GET')throw Error('An explicit response is required for '+method+' '+path);if(path.endsWith('/qr'))return {channel_id:channelA,revision:1,image:png,expires_in_ms:45000};if(path.endsWith('/status')){const id=path.split('/').at(-2),row=server.data.items.find(row=>row.id===id);return runtime(row,row.enabled?'stopped':'disabled');}return copy(server.data);},onLegacy:async()=>legacy.push(true),onStatus:async status=>statuses.push(status)};
  window.eval(await readFile(new URL('../app/static/orion-fields.js',import.meta.url),'utf8'));
  window.eval(await readFile(new URL('../app/static/orion-connect.js',import.meta.url),'utf8'));
  window.eval(await readFile(new URL('../app/static/orion-channels.js',import.meta.url),'utf8'));const controller=window.OrionChannels.mount(bridge);
  const flush=async()=>{for(let i=0;i<5;i++)await new Promise(resolve=>setImmediate(resolve));};
  function element(selector){const el=document.querySelector(selector);assert.ok(el,'missing '+selector);return el;}
  const h={dom,window,document,controller,bridge,server,calls,copied,legacy,statuses,errors,logs,timers,element,
    click(selector){const el=element(selector);el.click();return el;},fill(selector,value){const el=element(selector);el.value=value;el.dispatchEvent(new window.Event('input',{bubbles:true}));return el;},
    check(selector,value=true){const el=element(selector);el.checked=value;el.dispatchEvent(new window.Event('change',{bubbles:true}));return el;},
    submit(){element('[data-qc-form]').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));},
    async tick(ms){const target=clock+ms;for(let tries=0;tries<100;tries++){const due=[...timers].filter(([,job])=>job.at<=target).sort((a,b)=>a[1].at-b[1].at)[0];if(!due)break;clock=due[1].at;timers.delete(due[0]);await due[1].fn();await flush();}clock=target;await flush();},
    async visibility(hidden){pageHidden=hidden;document.dispatchEvent(new window.Event('visibilitychange'));await flush();},
    close(){window.OrionChannels.unmount();timers.clear();window.close();}};
  await flush();return h;
}
