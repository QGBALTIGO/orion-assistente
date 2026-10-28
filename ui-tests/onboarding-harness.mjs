import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { JSDOM, VirtualConsole } from 'jsdom';
export { flush, deferred } from './harness.mjs';
export const tenantA='10000000-0000-4000-8000-000000000001',tenantB='20000000-0000-4000-8000-000000000002';
export const pro='30000000-0000-4000-8000-000000000003',service='40000000-0000-4000-8000-000000000004';
export const copy=value=>JSON.parse(JSON.stringify(value));
export function fullState(overrides={}) {
  return {tenant_id:tenantA,name:'Empresa sintética',timezone:'America/Cuiaba',version:3,snapshot:'e'.repeat(64),can_edit:true,status:'incomplete',current_step:'business',
    steps:['business','location','professionals','catalog','hours','channels','review'].map(id=>({id,label:{business:'Seu negócio',location:'Onde você atende',professionals:'Quem atende',catalog:'O que você oferece',hours:'Quando você atende',channels:'Recepção e canais',review:'Revisar e concluir'}[id],required:true,complete:false})),pending:[],
    configuration:{niche:'barbearia',activity:'services',contact_phone:null,modality:'onsite',private_address:{postal_code:null,street:'Rua sintética',number:'1',no_number:false,complement:null,neighborhood:'Centro',city:'Cuiabá',state:'MT'}},
    catalog:{professionals:[{id:pro,name:'Profissional sintético',active:false,requested_active:true,draft:true,accepts_appointments:true,fingerprint:'a'.repeat(64)}],services:[{id:service,name:'Serviço sintético',description:null,price_cents:3500,duration_min:40,prep_min:5,cleanup_min:5,active:false,requested_active:true,draft:true,professional_ids:[pro],fingerprint:'b'.repeat(64)}],products:[],hours:[{id:'60000000-0000-4000-8000-000000000006',professional_id:pro,weekday:0,start_time:'09:00:00',end_time:'18:00:00',breaks:[],fingerprint:'c'.repeat(64)}]},
    channels:{whatsapp:{status:'disconnected',message:'Conexão pendente',can_connect:false},assistant:{status:'draft',generation:2,profile:{assistant_name:'Recepção',style_rules:{style:'warm',length:'balanced',emoji:'none',formality:'formal',commercial_intensity:'none',avoid_phrases:['Expressão preservada']},capabilities:['catalog_answers'],policies:[{topic:'Fato existente',description:'Não substituir'}]}},site:{status:'not_published'},google:{status:'not_connected'}},last_operation_key:null,operations:[],...overrides};
}
export function entry(overrides={}) {return {enabled:true,kind:'onboarding',create_allowed:true,account:{name:'Conta sintética',email:'synthetic@example.invalid',verified:false},businesses:[],invitations:[],tenant_id:tenantA,current_step:'business',...overrides};}
export async function makeOnboarding({data=fullState(),decision=entry(),respond,postalRespond,user={name:'Conta sintética',email:'synthetic@example.invalid',role:'member',authenticated:true},auto=true}={}) {
  const errors=[],calls=[],postalCalls=[],completed=[],tenants=[],opened=[],timers=new Map();let clock=0,timerId=0;
  const console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error));
  const dom=new JSDOM('<!doctype html><html lang="pt-BR" data-theme="light"><body><aside id="original-nav">Menu existente</aside><main><section id="existing-panel">Painel existente</section><section id="setup"></section></main></body></html>',{url:'https://orion.test/panel',runScripts:'outside-only',virtualConsole:console});
  const {window}=dom;window.fetch=async(url,options={})=>{if(!postalRespond||!/^https:\/\/viacep\.com\.br\/ws\/\d{8}\/json\/$/.test(String(url)))throw Error('No network is allowed in this isolated DOM harness');postalCalls.push({url:String(url),options});const response=await postalRespond(String(url),options);return response instanceof Response?response:{ok:true,json:async()=>copy(response)};};
  window.setTimeout=(fn,delay=0)=>{const id=++timerId;timers.set(id,{fn,at:clock+delay});return id;};window.clearTimeout=id=>timers.delete(id);
  window.HTMLElement.prototype.scrollIntoView=()=>{};Object.defineProperty(window.crypto,'randomUUID',{value:randomUUID,configurable:true});
  const server={data:copy(data),decision:copy(decision)};
  const bridge={host:window.document.getElementById('setup'),user,
    async request(path,method='GET',payload){calls.push({path,method,data:copy(payload??null)});const supplied=await respond?.(path,method,payload,server);if(supplied!==undefined)return copy(supplied);if(method==='GET')return path.startsWith('/api/v1/onboarding/entry')?copy(server.decision):copy(server.data);throw Error('A mutation requires an explicit synthetic response: '+method+' '+path);},
    onComplete:async value=>completed.push(value),onTenantChange:async id=>tenants.push(id),openLegacy:async page=>opened.push(page)};
  window.eval(await readFile(new URL('../app/static/orion-fields.js',import.meta.url),'utf8'));
  window.eval(await readFile(new URL('../app/static/orion-onboarding.js',import.meta.url),'utf8'));
  const controller=window.OrionOnboarding.mount(bridge);
  function element(selector){const el=window.document.querySelector(selector);assert.ok(el,'missing '+selector);return el;}
  const h={dom,window,document:window.document,controller,bridge,server,calls,postalCalls,completed,tenants,opened,errors,timers,element,
    click(selector){const el=element(selector);el.click();return el;},
    fill(selector,value){const el=element(selector);el.value=value;el.dispatchEvent(new window.Event('input',{bubbles:true}));return el;},
    change(selector,value){const el=element(selector);if(el.type==='checkbox')el.checked=value;else el.value=value;el.dispatchEvent(new window.Event('change',{bubbles:true}));return el;},
    submit(){element('[data-ob-form]').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));},
    async tick(ms){const target=clock+ms;for(let tries=0;tries<100;tries++){const due=[...timers].filter(([,job])=>job.at<=target).sort((a,b)=>a[1].at-b[1].at)[0];if(!due)break;clock=due[1].at;timers.delete(due[0]);due[1].fn();for(let i=0;i<5;i++)await new Promise(resolve=>setImmediate(resolve));}clock=target;},
    close(){window.OrionOnboarding.unmount();timers.clear();window.close();}};
  if(auto)await controller.resolveEntry();return h;
}
