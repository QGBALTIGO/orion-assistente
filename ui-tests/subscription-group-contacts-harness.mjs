import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {randomUUID,webcrypto,createHash} from 'node:crypto';
import {JSDOM,VirtualConsole} from 'jsdom';
import {flush,tenantA} from './harness.mjs';
export {flush,deferred,tenantA,tenantB} from './harness.mjs';

export const copy=value=>JSON.parse(JSON.stringify(value));
export const contract=JSON.parse(await readFile(new URL('../tests/fixtures/subscription_group_contacts_contract.json',import.meta.url),'utf8'));
export const source=await readFile(new URL('../app/static/orion-subscription-group-contacts.js',import.meta.url),'utf8');
export const base='/api/v1/tenants/'+tenantA+'/subscription-whatsapp/group-contacts';
export const deviceId=contract.options.devices[0].id,groupId=contract.options.groups[0].id,remoteId=contract.groups.items[0].id;
export const accountKey='synthetic-group-contacts@example.invalid|member';
export const accountScope=createHash('sha256').update(accountKey).digest('hex');
export const failure=(status,extra={})=>Object.assign(Error('Falha sintética'),{status,...extra});
export const recovery=(extra={})=>({tenant_id:tenantA,account_scope:accountScope,idempotency_key:contract.import_request.idempotency_key,...extra});
export const writes=h=>h.calls.filter(call=>call.method!=='GET');
export const uid=n=>'00000000-0000-4000-8000-'+String(n).padStart(12,'0');
const stamp=()=>new Date().toISOString();
const keys=(value,expected)=>assert.deepEqual(Object.keys(value).sort(),[...expected].sort());

// A transport-free additive store: explicit imports retain unrelated members,
// existing names and opt-outs. Reads only issue synthetic preview snapshots.
export function syntheticServer(){
  const options=copy(contract.options);options.generated_at=stamp();
  const groups=copy(contract.groups.items),members=new Map([[remoteId,copy(contract.members.members)],[groups[1].id,[{phone:'+5567999999905',name:'Outro contato sintético',role:'member'}]]]);
  const local=new Map([[groupId,new Map([
    ['+5567999999902',{name:'Nome local confirmado',active:true,blocked:false}],
    ['+5567999999904',{name:'Contato fora da origem',active:true,blocked:true}],
    ['+5567999999903',{name:'Nome local anterior',active:false,blocked:false}],
  ])]]);
  const server={options,groups,members,local,previews:new Map(),requests:new Map(),imports:[],unexpected:[]};
  function localDTO(id){const dto=options.groups.find(row=>row.id===id),rows=[...local.get(id).values()].filter(row=>row.active);return {...copy(dto),member_count:rows.length,blocked_count:rows.filter(row=>row.blocked).length};}
  server.execute=async(path,method='GET',payload)=>{
    if(payload!==undefined)payload=copy(payload);
    const url=new URL(path,'https://orion.test');if(!url.pathname.startsWith(base)){server.unexpected.push({path,method});throw failure(404);}
    const suffix=url.pathname.slice(base.length),q=url.searchParams;
    if(method==='GET'){
      if(suffix==='/options')return copy({...options,groups:options.groups.map(row=>local.has(row.id)?localDTO(row.id):row)});
      if(suffix.startsWith('/requests/')){const value=server.requests.get(suffix.split('/').at(-1));if(!value)throw failure(404,{code:'GROUP_CONTACTS_NOT_FOUND'});return {...copy(value.operation),replayed:true};}
      const device=options.devices.find(row=>row.id===q.get('device_id'));
      if(!device||!device.available)throw failure(409,{code:'GROUP_CONTACTS_DEVICE_UNAVAILABLE'});
      if(suffix==='/groups')return {device:copy(device),items:copy(groups),total:groups.length};
      if(suffix==='/members'){
        const group=groups.find(row=>row.id===q.get('group_id'));if(!group)throw failure(404,{code:'GROUP_CONTACTS_NOT_FOUND'});
        const rows=copy(members.get(group.id)||[]),token='synthetic-preview-'+randomUUID()+'A'.repeat(100);
        const missing=group.id===remoteId?1:0,duplicates=group.id===remoteId?1:0;
        const value={preview_token:token,expires_at:new Date(Date.now()+300000).toISOString(),device:copy(device),group:copy(group),members:rows,total:rows.length+missing+duplicates,unavailable_count:missing,duplicate_count:duplicates};
        server.previews.set(token,copy(value));return value;
      }
      throw failure(404);
    }
    assert.equal(method,'POST');assert.equal(suffix,'/import');
    keys(payload,['idempotency_key','preview_token','phones','target_group_id','expected_version','new_group_name']);
    const previous=server.requests.get(payload.idempotency_key);
    if(previous){assert.deepEqual(previous.payload,payload);return {...copy(previous.operation),replayed:true};}
    const preview=server.previews.get(payload.preview_token);if(!preview)throw failure(422,{code:'GROUP_CONTACTS_PREVIEW_INVALID'});
    if(Date.parse(preview.expires_at)<=Date.now())throw failure(410,{code:'GROUP_CONTACTS_PREVIEW_EXPIRED'});
    assert.ok(payload.phones.length>0&&payload.phones.length<=5000);assert.equal(new Set(payload.phones).size,payload.phones.length);
    assert.deepEqual(payload.phones,[...payload.phones].sort());
    for(const phone of payload.phones)assert.ok(preview.members.some(row=>row.phone===phone));
    let group,kind;
    if(payload.target_group_id===null){
      assert.equal(payload.expected_version,null);assert.ok(payload.new_group_name.trim()&&payload.new_group_name.length<=100);
      group={id:randomUUID(),name:payload.new_group_name,version:1,member_count:0,blocked_count:0,archived:false};kind='group_create';options.groups.push(group);local.set(group.id,new Map());
    }else{
      assert.equal(payload.new_group_name,null);group=options.groups.find(row=>row.id===payload.target_group_id);assert.ok(group);
      if(payload.expected_version!==group.version)throw failure(409,{code:'GROUP_CONTACTS_CONFLICT',groupContactsRejection:{reason:'stale_group_version',idempotency_key:payload.idempotency_key,group_id:group.id,expected_version:payload.expected_version}});
      group.version++;kind='group_update';
    }
    let added=0,existing=0,restored=0;const rows=local.get(group.id);
    for(const phone of payload.phones){const old=rows.get(phone),remote=preview.members.find(row=>row.phone===phone);if(old){if(old.active)existing++;else{restored++;old.active=true;}}else{added++;rows.set(phone,{name:remote.name||phone,active:true,blocked:false});}}
    const result={origin:'group_contacts',group:localDTO(group.id),selected_count:payload.phones.length,added_count:added,existing_count:existing,restored_count:restored};
    const operation={request_id:randomUUID(),idempotency_key:payload.idempotency_key,kind,status:'succeeded',campaign_id:null,group_id:group.id,replayed:false,created_at:stamp(),expires_at:null,preview:null,result};
    server.requests.set(payload.idempotency_key,{payload:copy(payload),operation:copy(operation)});server.imports.push(copy(payload));return operation;
  };return server;
}

export async function makeGroupContacts({respond,historyState,wait=true,identity=accountKey,canWrite=true,canOpenDevices=false,server=syntheticServer(),configureWindow}={}){
  const errors=[],external=[],calls=[],signals=[],virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',error=>errors.push(error));
  const dom=new JSDOM('<!doctype html><html lang="pt-BR"><body><main><section id="osSubscriptionGroupContacts"></section></main></body></html>',{url:'https://orion.test/panel?aba=extrator-contatos',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole});
  const {window}=dom,{document}=window;window.HTMLElement.prototype.scrollIntoView=()=>{};window.scrollTo=()=>{};
  window.fetch=async(...args)=>{external.push(args);throw Error('External request forbidden');};
  Object.defineProperty(window.crypto,'randomUUID',{value:randomUUID,configurable:true});Object.defineProperty(window.crypto,'subtle',{value:webcrypto.subtle,configurable:true});window.TextEncoder=TextEncoder;
  if(historyState)window.history.replaceState(copy(historyState),'',window.location.href);const access={allowed:true,writable:canWrite,devices:canOpenDevices};
  const bridge={host:document.getElementById('osSubscriptionGroupContacts'),tenantId:tenantA,accountKey:identity,canAccess:()=>access.allowed,canWrite:()=>access.writable,canOpenDevices:()=>access.devices,async request(path,method='GET',payload,options){calls.push({path,method,data:payload?copy(payload):null});signals.push({path,method,signal:options?.signal});const custom=await respond?.(path,method,payload,server,options);if(custom!==undefined)return copy(custom);return copy(await server.execute(path,method,payload));}};
  window.eval(await readFile(new URL('../app/static/orion-fields.js',import.meta.url),'utf8'));window.eval(source);configureWindow?.(window);
  const controller=window.OrionSubscriptionGroupContacts.mount(bridge);if(wait)await controller.ready;
  function element(selector){const node=document.querySelector(selector);assert.ok(node,'Missing '+selector);return node;}
  return {dom,window,document,controller,bridge,server,access,calls,signals,external,errors,element,
    click(selector){const node=element(selector);node.click();return node;},
    fill(selector,value){const node=element(selector);node.value=value;node.dispatchEvent(new window.Event('input',{bubbles:true}));return node;},
    change(selector,value){const node=element(selector);if(node.type==='checkbox'||node.type==='radio')node.checked=Boolean(value);else node.value=value;node.dispatchEvent(new window.Event('change',{bubbles:true}));return node;},
    close(){window.OrionSubscriptionGroupContacts.unmount();window.close();}};
}
export const field=name=>'[data-gc-field="'+name+'"]';
export const action=name=>'[data-gc-action="'+name+'"]';
export async function loadMembers(h,id=remoteId){h.change(field('group_id'),id);h.click(action('load-members'));await flush();}
