import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {randomUUID,webcrypto,createHash} from 'node:crypto';
import {JSDOM,VirtualConsole} from 'jsdom';
import {flush,tenantA} from './harness.mjs';
export {flush,deferred,tenantA,tenantB} from './harness.mjs';

export const copy=value=>JSON.parse(JSON.stringify(value));
export const contract=JSON.parse(await readFile(new URL('../tests/fixtures/subscription_scheduled_messages_contract.json',import.meta.url),'utf8'));
export const source=await readFile(new URL('../app/static/orion-subscription-scheduled-messages.js',import.meta.url),'utf8');
export const base='/api/v1/tenants/'+tenantA+'/subscription-whatsapp/scheduled-messages';
export const scheduleId=contract.detail_response.schedule.schedule_id,runId=contract.detail_response.schedule.run_id;
export const accountKey='synthetic-schedules@example.invalid|member';
export const accountScope=createHash('sha256').update(accountKey).digest('hex');
export const failure=(status,extra={})=>Object.assign(Error('Synthetic schedule failure'),{status,...extra});
export const recovery=(extra={})=>({tenant_id:tenantA,account_scope:accountScope,idempotency_key:contract.review_request.idempotency_key,kind:'review_create',schedule_id:null,run_id:null,confirming:false,...extra});
export const writes=h=>h.calls.filter(call=>call.method!=='GET'&&!new URL(call.path,'https://orion.test').pathname.endsWith('/audience'));
const stamp=()=>new Date().toISOString();
export const dateAt=s=>new Date(s.start_date+'T'+s.start_time+':00-03:00').toISOString();
const keys=(value,expected)=>assert.deepEqual(Object.keys(value).sort(),[...expected].sort());

export function syntheticServer() {
  const server={options:copy(contract.options_response),contacts:copy(contract.contacts_response.items),runs:new Map([[runId,copy(contract.detail_response)]]),requests:new Map(),confirmations:[],releases:[],effects:[],unexpected:[],groupMembers:new Map([[contract.options_response.groups[0].id,contract.contacts_response.items.slice(0,2).map(c=>c.key)],[contract.options_response.groups[1].id,[contract.contacts_response.items[1].key]]])};
  const paged=(items,q=new URLSearchParams())=>{const page=Math.max(1,Number(q.get('page')||1));return {items:copy(items.slice((page-1)*30,page*30)),total:items.length,page,page_size:30,pages:Math.max(1,Math.ceil(items.length/30)),timezone:server.options.timezone,generated_at:stamp()};};
  const findRun=(id,run)=>{const found=run?server.runs.get(run):[...server.runs.values()].find(d=>d.schedule.schedule_id===id);if(!found||found.schedule.schedule_id!==id)throw failure(404);return found;};
  const publicOperation=entry=>{const value=copy(entry.operation);if(value.status==='prepared'&&Date.parse(value.expires_at)<=Date.now()){value.status='expired';value.preview=null;}else if(value.status!=='prepared')value.preview=null;return value;};
  function audience(s) {
    const direct=s.all_contacts?server.contacts.map(c=>c.key):s.contact_keys;
    const grouped=s.group_ids.flatMap(id=>server.groupMembers.get(id)||[]),combined=[...direct,...grouped],distinct=[...new Set(combined)],blocked=distinct.filter(key=>!server.contacts.find(c=>c.key===key)?.available).length;
    const start=s.start_date&&s.start_time?dateAt(s):null;
    return {total:distinct.length,direct:direct.length,from_groups:grouped.length,duplicates:combined.length-distinct.length,blocked,eligible:distinct.length-blocked,generated_at:stamp(),timezone:server.options.timezone,start_at:start,estimated_end_at:start&&distinct.length?new Date(Date.parse(start)+distinct.length*s.interval_seconds*1000).toISOString():null};
  }
  const message=s=>{const template=server.options.templates.find(t=>t.id===s.template_id),type=s.message_type==='text'?'text':template.kind;return {type,text:s.text,caption:null,followup:null,template_name:template?.name||null,parts:[{type,text:s.text}]};};
  const counts=items=>({active:items.filter(i=>i.bucket==='active').length,past_due:items.filter(i=>i.bucket==='past_due').length,history:items.filter(i=>i.bucket==='history').length,all:items.length,...Object.fromEntries(['attention','queued','processing','upcoming'].map(k=>[k,items.filter(i=>i.active_filter===k).length]))});
  function operation(kind,payload,{target=null,run=null,preview=null,result=null,status='prepared'}={}) {return {request_id:randomUUID(),idempotency_key:payload.idempotency_key,kind,status,schedule_id:target,run_id:run,replayed:false,created_at:stamp(),expires_at:preview?.expires_at||null,preview,result};}
  function record(path,payload,op) {const entry={path,payload:copy(payload),operation:copy(op)};server.requests.set(op.idempotency_key,entry);return publicOperation(entry);}
  function versionCheck(item,kind,payload) {
    if(item.version===payload.expected_version&&(payload.expected_run_version===null||item.run_version===payload.expected_run_version))return;
    const old=payload.expected_version<=item.version&&(payload.expected_run_version===null||payload.expected_run_version<=item.run_version);
    throw failure(409,{code:'SCHEDULE_CONFLICT',...(old?{scheduleRejection:{reason:'stale_version',idempotency_key:payload.idempotency_key,kind,schedule_id:item.schedule_id,run_id:kind==='cancel_series'?null:item.run_id,expected_version:payload.expected_version,expected_run_version:payload.expected_run_version??null}}:{})});
  }
  server.execute=async(path,method='GET',payload)=>{
    const url=new URL(path,'https://orion.test'),suffix=url.pathname.slice(base.length),q=url.searchParams;
    if(!path.startsWith(base)){server.unexpected.push({path,method});throw failure(404);}
    if(method==='POST'&&suffix==='/audience')return audience(payload);
    if(method==='GET'){
      if(suffix==='/options')return copy(server.options);
      if(suffix==='/contacts'){const text=(q.get('q')||'').toLocaleLowerCase();return paged(server.contacts.filter(c=>(c.name+' '+c.phone_e164).toLocaleLowerCase().includes(text)),q);}
      if(suffix.startsWith('/requests/')){const entry=server.requests.get(suffix.split('/').at(-1));if(!entry)throw failure(404);return {...publicOperation(entry),replayed:true};}
      if(suffix===''||suffix==='/'){const all=[...server.runs.values()].map(d=>d.schedule),text=(q.get('q')||'').toLocaleLowerCase(),tab=q.get('tab')||'active',status=q.get('status'),matching=all.filter(i=>!text||i.title.toLocaleLowerCase().includes(text));const filtered=matching.filter(i=>(tab==='all'||i.bucket===tab)&&(!status||i.active_filter===status));return {...paged(filtered,q),counts:counts(matching)};}
      const id=suffix.split('/')[1];
      if(suffix.endsWith('/runs'))return paged([...server.runs.values()].map(d=>d.schedule).filter(i=>i.schedule_id===id),q);
      const detail=findRun(id,q.get('run_id'));
      if(suffix.endsWith('/recipients')){const text=(q.get('q')||'').toLocaleLowerCase();return paged(detail.recipients.items.filter(r=>(!q.get('status')||r.status===q.get('status'))&&(!text||(r.name+' '+r.phone).toLocaleLowerCase().includes(text))),q);}
      return copy(detail);
    }
    assert.equal(method,'POST');assert.ok(payload?.idempotency_key,'Every mutation preserves its own identity');
    const prior=server.requests.get(payload.idempotency_key);
    if(suffix==='/confirm'||suffix==='/pending/confirm'){
      keys(payload,['idempotency_key','review_version','confirmation','consent_confirmed']);assert.ok(prior);assert.equal(payload.consent_confirmed,true);
      const pending=suffix==='/pending/confirm';assert.equal(prior.operation.kind==='pending_review',pending);assert.equal(payload.confirmation,pending?'ENVIAR PENDENTES':'AGENDAR');
      if(prior.operation.status==='succeeded')return {...publicOperation(prior),replayed:true};
      if(Date.parse(prior.operation.expires_at)<=Date.now())throw failure(410);
      assert.equal(payload.review_version,prior.operation.preview.review_version);
      if(pending){const released=prior.operation.preview.runs.map(row=>({schedule_id:row.schedule_id,run_id:row.run_id}));if(!released.length)throw failure(422,{code:'SCHEDULE_NO_ELIGIBLE'});for(const {run_id} of released){const d=server.runs.get(run_id);d.schedule.run_version++;d.schedule.bucket='active';d.schedule.active_filter='attention';d.schedule.status_label='Necessita de atenção';d.released=true;}prior.operation.status='succeeded';prior.operation.result={released,total:released.length};server.releases.push(copy(released));return publicOperation(prior);}
      const definition=prior.payload.schedule,editing=prior.operation.kind==='review_edit',old=editing?findRun(prior.operation.schedule_id,prior.operation.run_id):null;
      if(old)versionCheck(old.schedule,'review_edit',prior.payload);
      const id=old?.schedule.schedule_id||randomUUID(),run=old?.schedule.run_id||randomUUID(),a=audience(definition),item={...copy(contract.detail_response.schedule),schedule_id:id,run_id:run,title:definition.title,version:(old?.schedule.version||0)+1,run_version:(old?.schedule.run_version||0)+1,scheduled_at:a.start_at,local_date:definition.start_date,local_time:definition.start_time,interval_seconds:definition.interval_seconds,repeat_every_30_days:definition.repeat_every_30_days,estimated_end_at:a.estimated_end_at,device:{id:definition.device_id,name:server.options.devices.find(d=>d.id===definition.device_id).name,status:'connected'},progress:{total:a.total,processed:0,sent:0,pending:a.total,failed:0,uncertain:0,cancelled:0,percent:0}};
      server.runs.set(run,{schedule:item,definition:{...copy(definition),timezone:server.options.timezone},message_preview:message(definition),groups:server.options.groups.filter(g=>definition.group_ids.includes(g.id)),recipients:paged(copy(contract.detail_response.recipients.items).slice(0,a.total))});
      prior.operation={...prior.operation,status:'succeeded',schedule_id:id,run_id:run,result:{schedule:copy(item)}};server.confirmations.push({idempotency_key:payload.idempotency_key,schedule_id:id,run_id:run});return publicOperation(prior);
    }
    if(prior){if(prior.path!==path||JSON.stringify(prior.payload)!==JSON.stringify(payload))throw failure(409,{code:'SCHEDULE_IDEMPOTENCY_CONFLICT'});return {...publicOperation(prior),replayed:true};}
    if(suffix==='/pending/review'){
      keys(payload,['idempotency_key']);const runs=[...server.runs.values()].filter(d=>!d.released&&d.schedule.status==='pending'&&d.schedule.series_status==='active'&&d.schedule.scheduled_at&&Date.parse(d.schedule.scheduled_at)<=Date.now()).map(d=>copy(d.schedule));const preview={review_version:createHash('sha256').update(randomUUID()).digest('hex'),expires_at:new Date(Date.now()+120000).toISOString(),total:runs.length,runs};return record(path,payload,operation('pending_review',payload,{preview}));
    }
    if(suffix==='/review'||suffix.endsWith('/review')){
      const editing=suffix!=='/review';keys(payload,editing?['idempotency_key','run_id','expected_version','expected_run_version','schedule']:['idempotency_key','schedule']);
      keys(payload.schedule,Object.keys(contract.schedule_input));const definition=payload.schedule;
      if(definition.text?.length>1000||definition.start_date<server.options.today)throw failure(422);
      const item=editing?findRun(suffix.split('/')[1],payload.run_id).schedule:null;
      if(item)versionCheck(item,'review_edit',payload);
      const a=audience(definition),preview={review_version:createHash('sha256').update(randomUUID()).digest('hex'),expires_at:new Date(Date.now()+120000).toISOString(),title:definition.title,definition:{...copy(definition),timezone:server.options.timezone},...Object.fromEntries(['total','eligible','blocked','duplicates','direct','from_groups'].map(k=>[k,a[k]])),device:copy(server.options.devices.find(d=>d.id===definition.device_id)),groups:copy(server.options.groups.filter(g=>definition.group_ids.includes(g.id))),message_preview:message(definition),schedule:{start_at:a.start_at,timezone:a.timezone,estimated_end_at:a.estimated_end_at,repeat_every_30_days:definition.repeat_every_30_days}};
      return record(path,payload,operation(editing?'review_edit':'review_create',payload,{target:item?.schedule_id||null,run:item?.run_id||null,preview}));
    }
    if(suffix.endsWith('/actions')){
      keys(payload,['idempotency_key','action','run_id','expected_version','expected_run_version','confirmation']);assert.equal(payload.confirmation,'CANCELAR');const item=findRun(suffix.split('/')[1],payload.run_id).schedule;versionCheck(item,payload.action,payload);
      const targets=payload.action==='cancel_series'?[...server.runs.values()].map(d=>d.schedule).filter(i=>i.schedule_id===item.schedule_id):[item];
      for(const i of targets){if(payload.action==='cancel_series')i.series_status='cancelled';i.version++;if(i.status==='pending'){i.run_version++;i.status='cancelled';i.bucket='history';i.active_filter=null;i.status_label='Cancelada';i.actions=[];i.progress={...i.progress,pending:0,cancelled:i.progress.total,processed:i.progress.total,percent:100};}}
      const result=payload.action==='cancel_series'?{schedule_id:item.schedule_id,cancelled:true}:{schedule:copy(item)};return record(path,payload,operation(payload.action,payload,{target:item.schedule_id,run:payload.action==='cancel_series'?null:item.run_id,result,status:'succeeded'}));
    }
    server.unexpected.push({path,method});throw failure(404);
  };
  return server;
}

export async function makeSchedules({respond,historyState,wait=true,identity=accountKey,canWrite=true,url='https://orion.test/panel?aba=programadas',server=syntheticServer(),cryptoEnabled=true,configureWindow}={}) {
  const errors=[],external=[],calls=[],signals=[],console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error));
  const dom=new JSDOM('<!doctype html><html lang="pt-BR"><body><main><section id="schedules"></section></main></body></html>',{url,runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:console});const {window}=dom,{document}=window;
  window.HTMLElement.prototype.scrollIntoView=()=>{};window.scrollTo=()=>{};
  window.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};window.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');this.dispatchEvent(new window.Event('close'));};
  window.fetch=async(...args)=>{external.push(args);throw Error('Direct external fetch forbidden');};
  Object.defineProperty(window.crypto,'randomUUID',{value:randomUUID,configurable:true});Object.defineProperty(window.crypto,'subtle',{value:webcrypto.subtle,configurable:true});window.TextEncoder=TextEncoder;
  if(historyState)window.history.replaceState(copy(historyState),'',window.location.href);
  const access={allowed:true,writable:canWrite};
  const bridge={host:document.getElementById('schedules'),tenantId:tenantA,accountKey:identity,canAccess:()=>access.allowed,canWrite:()=>access.writable,async request(path,method='GET',payload,options){calls.push({path,method,data:payload?copy(payload):null});signals.push({path,method,signal:options?.signal});const custom=await respond?.(path,method,payload,server,options);if(custom!==undefined)return copy(custom);return copy(await server.execute(path,method,payload));}};
  window.eval(await readFile(new URL('../app/static/orion-fields.js',import.meta.url),'utf8'));window.eval(source);if(!cryptoEnabled)Object.defineProperty(window.crypto,'subtle',{value:undefined,configurable:true});configureWindow?.(window);const controller=window.OrionSubscriptionScheduledMessages.mount(bridge);if(wait)await controller.ready;
  function element(selector){const node=document.querySelector(selector);assert.ok(node,'Missing '+selector);return node;}
  return {dom,window,document,controller,bridge,server,access,calls,signals,external,errors,element,
    click(selector){const node=element(selector);node.click();return node;},
    fill(selector,value){const node=element(selector);node.value=value;node.dispatchEvent(new window.Event('input',{bubbles:true}));return node;},
    change(selector,value){const node=element(selector);if(node.type==='checkbox')node.checked=Boolean(value);else node.value=value;node.dispatchEvent(new window.Event('change',{bubbles:true}));return node;},
    submit(kind='schedule'){element('[data-sc-form="'+kind+'"]').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));},
    close(){window.OrionSubscriptionScheduledMessages.unmount();window.close();}
  };
}

export async function fillSchedule(h,{definition=contract.schedule_input,template=false}={}) {
  h.click('[data-sc-action="new"]');await flush();
  for(const key of ['title','start_date','start_time'])h.fill('[data-sc-field="'+key+'"]',definition[key]);
  h.change('[data-sc-field="device_id"]',definition.device_id);h.change('[data-sc-field="interval_seconds"]',String(definition.interval_seconds));
  for(const id of definition.group_ids){h.change('[data-sc-group="'+id+'"]',true);await flush();}
  for(const key of definition.contact_keys){h.change('[data-sc-contact="'+key+'"]',true);await flush();}
  if(template){h.change('[data-sc-field="message_type"]','template');h.change('[data-sc-field="template_id"]',contract.options_response.templates[1].id);}
  else h.fill('[data-sc-field="text"]',definition.text);
  await flush();
}
