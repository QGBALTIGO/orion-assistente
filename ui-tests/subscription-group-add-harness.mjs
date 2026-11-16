import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {randomUUID,webcrypto,createHash} from 'node:crypto';
import {JSDOM,VirtualConsole} from 'jsdom';
import {flush,tenantA} from './harness.mjs';
export {flush,deferred,tenantA,tenantB} from './harness.mjs';

export const copy=value=>JSON.parse(JSON.stringify(value));
export const contract=JSON.parse(await readFile(new URL('../tests/fixtures/subscription_group_add_contract.json',import.meta.url),'utf8'));
export const source=await readFile(new URL('../app/static/orion-subscription-group-add.js',import.meta.url),'utf8');
export const base='/api/v1/tenants/'+tenantA+'/subscription-whatsapp/group-add';
export const deviceId=contract.options.devices[0].id,localGroupId=contract.options.groups[0].id,destinationId=contract.groups.items[0].id,noAdminId=contract.groups.items[1].id,sourceGroupId=contract.groups.items[2].id;
export const accountKey='synthetic-group-add@example.invalid|member';
export const accountScope=createHash('sha256').update(accountKey).digest('hex');
export const failure=(status,extra={})=>Object.assign(Error('Falha sintética'),{status,...extra});
export const recovery=(extra={})=>({tenant_id:tenantA,account_scope:accountScope,idempotency_key:contract.ids.idempotency_key,...extra});
export const mutations=h=>h.calls.filter(call=>call.method==='POST'&&call.path===base+'/batches');
export const previews=h=>h.calls.filter(call=>call.method==='POST'&&call.path===base+'/preview');
export const uid=n=>'00000000-0000-4000-8000-'+String(n).padStart(12,'0');
export const field=name=>'[data-ga-field="'+name+'"]';
export const action=name=>'[data-ga-action="'+name+'"]';
const stamp=()=>new Date().toISOString();
const keys=(value,expected)=>assert.deepEqual(Object.keys(value).sort(),[...expected].sort());
const filterKeys=['contact_group_id','without_group','search','created_from','created_to','source_group_id'];
const statusKeys=['pending','running','added','already_member','not_added','unknown','cancelled'];

// This in-memory adapter never contacts WhatsApp. It serves the shared public
// fixtures, records explicit intents and exposes outcomes supplied by each test.
export function syntheticServer(){
  const server={options:copy(contract.options),groups:copy(contract.groups.items),tokens:new Map(),requests:new Map(),batches:new Map(),creates:[],unexpected:[]};
  server.execute=async(path,method='GET',payload)=>{
    if(payload!==undefined)payload=copy(payload);const url=new URL(path,'https://orion.test');
    if(!url.pathname.startsWith(base)){server.unexpected.push({path,method});throw failure(404);}
    const suffix=url.pathname.slice(base.length);
    if(method==='GET'){
      if(suffix==='/options')return copy(server.options);
      if(suffix==='/groups'){
        const device=server.options.devices.find(row=>row.id===url.searchParams.get('device_id'));
        if(!device||device.status!=='connected'||!device.available)throw failure(409,{code:'GROUP_ADD_DEVICE_UNAVAILABLE'});
        return {device:copy(device),items:copy(server.groups),total:server.groups.length};
      }
      if(suffix.startsWith('/requests/')){const record=server.requests.get(suffix.split('/').at(-1));if(!record)throw failure(404,{code:'GROUP_ADD_NOT_FOUND'});return {...copy(record),replayed:true};}
      if(suffix.startsWith('/batches/')){const record=server.batches.get(suffix.split('/').at(-1));if(!record)throw failure(404,{code:'GROUP_ADD_NOT_FOUND'});return copy(record);}
      throw failure(404);
    }
    assert.equal(method,'POST');
    if(suffix==='/preview'){
      keys(payload,['device_id','destination_group_id','source',...filterKeys]);
      const device=server.options.devices.find(row=>row.id===payload.device_id);assert.ok(device);assert.equal(device.status,'connected');
      const destination=server.groups.find(row=>row.id===payload.destination_group_id),origin=server.groups.find(row=>row.id===payload.source_group_id);
      const fixture=payload.destination_group_id===null?'preview_no_destination':destination?.can_add===false?'preview_no_admin':payload.source==='group'?'preview_group':'preview_contacts';
      const value=copy(contract[fixture]);value.device=copy(device);value.source=payload.source;value.filters=Object.fromEntries(filterKeys.map(key=>[key,payload[key]]));
      value.destination=destination?{id:destination.id,name:destination.name,member_count:destination.member_count,is_admin:destination.can_add,blocked_reason:destination.blocked_reason}:null;
      if(payload.source==='group'){
        assert.ok(origin);assert.notEqual(origin.id,destination?.id);assert.equal(payload.contact_group_id,null);assert.equal(payload.without_group,false);assert.equal(payload.search,'');assert.equal(payload.created_from,null);assert.equal(payload.created_to,null);
        value.source_group={id:origin.id,name:origin.name,member_count:origin.member_count};
      }else{assert.equal(payload.source,'contacts');assert.equal(payload.source_group_id,null);value.source_group=null;}
      if(payload.search){const query=payload.search.toLocaleLowerCase();value.members=value.members.filter(row=>row.name.toLocaleLowerCase().includes(query)||row.phone.includes(query));}
      value.counts={...value.counts,total:value.members.length,ready:value.members.filter(row=>row.status==='ready').length,already_member:value.members.filter(row=>row.status==='already_member').length,blocked:value.members.filter(row=>row.status==='blocked').length};
      value.generated_at=stamp();value.preview_token=value.destination?.is_admin&&value.counts.ready?'synthetic-preview-'+randomUUID()+'A'.repeat(100):null;value.expires_at=value.preview_token?new Date(Date.now()+300000).toISOString():null;
      if(value.preview_token)server.tokens.set(value.preview_token,copy(value));return value;
    }
    assert.equal(suffix,'/batches');keys(payload,['idempotency_key','preview_token','phones']);
    assert.equal(server.requests.has(payload.idempotency_key),false,'The UI must not repeat a mutation to recover its response');
    const preview=server.tokens.get(payload.preview_token);assert.ok(preview);assert.ok(Date.parse(preview.expires_at)>Date.now());
    assert.ok(payload.phones.length>0&&payload.phones.length<=5000);assert.deepEqual(payload.phones,[...new Set(payload.phones)].sort());
    assert.ok(payload.phones.every(phone=>preview.members.some(row=>row.phone===phone&&row.status==='ready')));
    const value=copy(contract.batch_created),batch=value.batch;batch.id=randomUUID();batch.idempotency_key=payload.idempotency_key;batch.device={id:preview.device.id,name:preview.device.name};batch.destination={id:preview.destination.id,name:preview.destination.name};batch.source=preview.source;batch.total=payload.phones.length;batch.created_at=stamp();batch.progress=Object.fromEntries(statusKeys.map(key=>[key,key==='pending'?payload.phones.length:0]));
    value.members=payload.phones.map((phone,index)=>({id:randomUUID(),ordinal:index+1,phone,name:preview.members.find(row=>row.phone===phone).name,status:'pending',error_code:null,started_at:null,finished_at:null}));
    server.requests.set(payload.idempotency_key,value);server.batches.set(batch.id,value);server.creates.push(copy(payload));return copy(value);
  };
  server.outcome=(key,statuses,batchStatus)=>{
    const value=server.requests.get(key);assert.ok(value);assert.equal(statuses.length,value.members.length);
    value.batch.version++;value.batch.status=batchStatus;value.batch.started_at=stamp();value.batch.finished_at=['pending','running'].includes(batchStatus)?null:stamp();
    value.members.forEach((row,index)=>{row.status=statuses[index];row.started_at=row.status==='pending'?null:stamp();row.finished_at=['pending','running'].includes(row.status)?null:stamp();row.error_code=row.status==='not_added'?'GROUP_ADD_PROVIDER_REFUSED':row.status==='unknown'?'GROUP_ADD_OUTCOME_UNKNOWN':null;});
    value.batch.progress=Object.fromEntries(statusKeys.map(key=>[key,value.members.filter(row=>row.status===key).length]));return copy(value);
  };
  return server;
}

export async function makeGroupAdd({respond,historyState,wait=true,identity=accountKey,canWrite=true,canOpenDevices=false,server=syntheticServer(),configureWindow}={}){
  const errors=[],external=[],calls=[],signals=[],virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',error=>errors.push(error));
  const dom=new JSDOM('<!doctype html><html lang="pt-BR"><body><main><section id="osSubscriptionGroupAdd"></section></main></body></html>',{url:'https://orion.test/painel?aba=adicionar-ao-grupo',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole});
  const {window}=dom,{document}=window;window.HTMLElement.prototype.scrollIntoView=()=>{};window.scrollTo=()=>{};
  window.fetch=async(...args)=>{external.push(args);throw Error('External request forbidden');};
  Object.defineProperty(window.crypto,'randomUUID',{value:randomUUID,configurable:true});Object.defineProperty(window.crypto,'subtle',{value:webcrypto.subtle,configurable:true});window.TextEncoder=TextEncoder;
  if(historyState)window.history.replaceState(copy(historyState),'',window.location.href);const access={allowed:true,writable:canWrite,devices:canOpenDevices};
  const bridge={host:document.getElementById('osSubscriptionGroupAdd'),tenantId:tenantA,accountKey:identity,canAccess:()=>access.allowed,canWrite:()=>access.writable,canOpenDevices:()=>access.devices,async request(path,method='GET',payload,options){calls.push({path,method,data:payload===undefined?null:copy(payload)});signals.push({path,method,signal:options?.signal});const custom=await respond?.(path,method,payload,server,options);if(custom!==undefined)return copy(custom);return copy(await server.execute(path,method,payload));}};
  window.eval(await readFile(new URL('../app/static/orion-fields.js',import.meta.url),'utf8'));window.eval(source);configureWindow?.(window);
  const controller=window.OrionSubscriptionGroupAdd.mount(bridge);if(wait)await controller.ready;
  function element(selector){const node=document.querySelector(selector);assert.ok(node,'Missing '+selector);return node;}
  return {dom,window,document,controller,bridge,server,access,calls,signals,external,errors,element,
    click(selector){const node=element(selector);node.click();return node;},
    fill(selector,value){const node=element(selector);node.value=value;node.dispatchEvent(new window.Event('input',{bubbles:true}));return node;},
    change(selector,value){const node=element(selector);if(node.type==='checkbox'||node.type==='radio')node.checked=Boolean(value);else node.value=value;node.dispatchEvent(new window.Event('change',{bubbles:true}));return node;},
    close(){window.OrionSubscriptionGroupAdd.unmount();window.close();}};
}
export async function loadContacts(h,destination=destinationId){h.change(field('destination_group_id'),destination);h.click(action('load'));await flush();}
