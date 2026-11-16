import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {randomUUID,webcrypto,createHash} from 'node:crypto';
import {JSDOM,VirtualConsole} from 'jsdom';
import {flush,tenantA} from './harness.mjs';
export {flush,deferred,tenantA,tenantB} from './harness.mjs';
export const copy=value=>JSON.parse(JSON.stringify(value));
export const contract=JSON.parse(await readFile(new URL('../tests/fixtures/subscription_campaigns_contract.json',import.meta.url),'utf8'));
export const source=await readFile(new URL('../app/static/orion-subscription-campaigns.js',import.meta.url),'utf8');
export const base='/api/v1/tenants/'+tenantA+'/subscription-whatsapp/campaigns';
export const campaignId=contract.detail_response.campaign.id,groupId=contract.group_response.group.id;
export const accountKey='synthetic@example.invalid|member';
export const accountScope=createHash('sha256').update(accountKey).digest('hex');
export const failure=(status,extra={})=>Object.assign(Error('Synthetic campaign failure'),{status,...extra});
export const recovery=(extra={})=>({tenant_id:tenantA,account_scope:accountScope,idempotency_key:contract.review_request.idempotency_key,kind:'review',campaign_id:campaignId,group_id:null,action:null,...extra});
export const writes=h=>h.calls.filter(call=>call.method!=='GET');
export function install(window) {Object.defineProperty(window.crypto,'randomUUID',{value:randomUUID,configurable:true});Object.defineProperty(window.crypto,'subtle',{value:webcrypto.subtle,configurable:true});window.TextEncoder=TextEncoder;window.eval(source);}
export function syntheticServer() {
  const server={options:copy(contract.options_response),campaigns:new Map([[campaignId,copy(contract.detail_response.campaign)]]),groups:new Map([[groupId,copy(contract.group_response)]]),requests:new Map(),confirmations:[],effects:[],recipients:[],unexpected:[]};
  const stamp=()=>new Date().toISOString();
  const paged=(items,q)=>{const page=Math.max(1,Number(q.get('page')||1)),page_size=30;return {items:items.slice((page-1)*page_size,page*page_size),total:items.length,page,page_size,pages:Math.max(1,Math.ceil(items.length/page_size)),generated_at:stamp(),timezone:'America/Sao_Paulo'};};
  const summaries=()=>[...server.groups.values()].filter(g=>!g.group.archived).map(g=>g.group);
  function project(c) {return {...copy(c),devices:server.options.devices.filter(d=>c.device_ids.includes(d.id)).map(({id,name})=>({id,name})),template_name:server.options.templates.find(t=>t.id===c.template_id)?.name||null};}
  function saved(op,path,payload) {server.requests.set(op.idempotency_key,{operation:copy(op),path,payload:copy(payload)});return copy(op);}
  function envelope(kind,payload,{campaign=null,group=null,result=null,preview=null,status='succeeded'}={}) {return {request_id:randomUUID(),idempotency_key:payload.idempotency_key,kind,status,campaign_id:campaign?.id||null,group_id:group?.id||null,result,preview,replayed:false,created_at:stamp(),expires_at:preview?.expires_at||null};}
  function checkVersion(resource,kind,payload) {if(resource.version===payload.expected_version)return;const proof=payload.expected_version<resource.version?{reason:'stale_version',idempotency_key:payload.idempotency_key,kind,campaign_id:kind.startsWith('group_')?null:resource.id,group_id:kind.startsWith('group_')?resource.id:null,expected_version:payload.expected_version}:undefined;throw failure(409,{code:'CAMPAIGN_CONFLICT',campaignRejection:proof});}
  server.execute=async(path,method='GET',payload)=>{
    const url=new URL(path,'https://orion.test'),suffix=url.pathname.slice(base.length),q=url.searchParams;
    if(!path.startsWith(base)){server.unexpected.push({path,method});throw failure(404);}
    if(method==='GET'){
      if(suffix==='/options')return {...copy(server.options),groups:copy(summaries())};
      if(suffix===''){const rows=[...server.campaigns.values()].filter(c=>!c.archived&&(!q.get('status')||c.status===q.get('status'))&&(!q.get('q')||c.title.toLocaleLowerCase().includes(q.get('q').toLocaleLowerCase())));return paged(rows.map(project),q);}
      if(suffix==='/groups')return {items:copy(summaries()),total:summaries().length};
      if(suffix.startsWith('/requests/')){const result=server.requests.get(suffix.split('/').at(-1));if(!result)throw failure(404);return {...copy(result.operation),preview:result.operation.status==='prepared'?copy(result.operation.preview):null,replayed:true};}
      if(suffix.startsWith('/groups/')){const group=server.groups.get(suffix.split('/')[2]);if(!group)throw failure(404);return copy(group);}
      const id=suffix.split('/')[1],campaign=server.campaigns.get(id);if(!campaign)throw failure(404);
      if(suffix.endsWith('/recipients'))return paged(server.recipients.filter(r=>(!q.get('status')||r.status===q.get('status'))&&(!q.get('q')||(r.name+' '+r.phone+' '+(r.message||'')).includes(q.get('q')))),q);
      return {campaign:project(campaign),progress:copy(campaign.progress),groups:copy(summaries().filter(g=>campaign.group_ids.includes(g.id))),recipients:paged(copy(server.recipients),new URLSearchParams())};
    }
    assert.ok(payload?.idempotency_key,'Every mutation has a stable key');
    const prior=server.requests.get(payload.idempotency_key);
    if(suffix.endsWith('/confirm')){
      assert.equal(method,'POST');assert.deepEqual(Object.keys(payload).sort(),['confirmation','consent_confirmed','idempotency_key','review_version']);
      assert.ok(prior&&prior.operation.kind==='review');assert.equal(payload.review_version,prior.operation.preview.review_version);assert.equal(payload.confirmation,'AGENDAR');assert.equal(payload.consent_confirmed,true);
      if(prior.operation.status==='succeeded')return {...copy(prior.operation),preview:null,replayed:true};
      const c=server.campaigns.get(prior.operation.campaign_id);c.version++;c.status='scheduled';c.status_label='Agendada';c.actions=['pause','edit','archive'];c.progress=copy(contract.scheduled_detail_response.progress);
      server.confirmations.push({key:payload.idempotency_key,campaign_id:c.id});server.recipients=copy(contract.scheduled_detail_response.recipients.items);
      prior.operation={...prior.operation,status:'succeeded',result:{campaign:project(c),progress:c.progress}};return {...copy(prior.operation),preview:null};
    }
    if(prior){assert.equal(prior.path,path);assert.deepEqual(prior.payload,payload);return {...copy(prior.operation),replayed:true};}
    if(suffix===''&&method==='POST'){
      const c={...copy(contract.detail_response.campaign),...copy(payload.campaign),id:randomUUID(),created_at:stamp(),updated_at:stamp()};server.campaigns.set(c.id,c);return saved(envelope('create',payload,{campaign:c,result:{campaign:project(c)}}),path,payload);
    }
    if(suffix==='/groups'&&method==='POST'){
      const group={id:randomUUID(),name:payload.name,version:1,member_count:payload.members.length,blocked_count:0,archived:false},members=payload.members.map(m=>({...m,blocked:false,permission_version:0,blocked_reason:null}));server.groups.set(group.id,{group,members});return saved(envelope('group_create',payload,{group,result:{group,members}}),path,payload);
    }
    if(suffix.startsWith('/groups/')){
      const value=server.groups.get(suffix.split('/')[2]);if(!value)throw failure(404);const kind=suffix.endsWith('/permissions')?'group_permissions':method==='DELETE'?'group_archive':'group_update';checkVersion(value.group,kind,payload);
      if(kind==='group_permissions'){assert.equal(payload.confirmation,'AUTORIZAR');for(const contact of payload.contacts){const member=value.members.find(m=>m.phone===contact.phone);assert.equal(member.permission_version,contact.version);member.blocked=false;member.permission_version++;member.blocked_reason=null;}}
      else if(kind==='group_archive')value.group.archived=true;
      else{value.group.name=payload.name;value.members=payload.members.map(m=>({...m,...value.members.find(old=>old.phone===m.phone),name:m.name,phone:m.phone,blocked:value.members.find(old=>old.phone===m.phone)?.blocked||false,permission_version:value.members.find(old=>old.phone===m.phone)?.permission_version||0,blocked_reason:value.members.find(old=>old.phone===m.phone)?.blocked_reason||null}));}
      value.group.version++;value.group.member_count=value.members.length;value.group.blocked_count=value.members.filter(m=>m.blocked).length;return saved(envelope(kind,payload,{group:value.group,result:copy(value)}),path,payload);
    }
    const id=suffix.split('/')[1],c=server.campaigns.get(id);if(!c)throw failure(404);
    if(method==='PATCH'){checkVersion(c,'update',payload);Object.assign(c,copy(payload.campaign));c.version++;return saved(envelope('update',payload,{campaign:c,result:{campaign:project(c)}}),path,payload);}
    if(suffix.endsWith('/review')){
      checkVersion(c,'review',payload);const preview=copy(contract.review_response.preview);preview.campaign=project(c);preview.devices=copy(server.options.devices.filter(d=>c.device_ids.includes(d.id)));preview.groups=copy(summaries().filter(g=>c.group_ids.includes(g.id)));preview.review_version=createHash('sha256').update(randomUUID()).digest('hex');preview.expires_at=new Date(Date.now()+120000).toISOString();preview.schedule={...preview.schedule,...Object.fromEntries(['timezone','window_start','window_end','weekdays','interval_seconds','start_date','start_time','start_at'].map(key=>[key,c[key]]))};preview.message_preview={...preview.message_preview,type:c.message_type==='text'?'text':server.options.templates.find(t=>t.id===c.template_id).kind,template_name:server.options.templates.find(t=>t.id===c.template_id)?.name||null,text:c.text,parts:[{type:c.message_type==='text'?'text':server.options.templates.find(t=>t.id===c.template_id).kind,text:c.text}]};return saved(envelope('review',payload,{campaign:c,preview,status:'prepared'}),path,payload);
    }
    if(suffix.endsWith('/actions')){
      checkVersion(c,'action',payload);c.version++;c.status=({pause:'paused',resume:'scheduled',edit:'draft',archive:'cancelled'})[payload.action];if(payload.action==='archive')c.archived=true;const example=contract.status_examples[c.status];c.status_label=example.status_label;c.actions=copy(example.actions);return saved(envelope('action',payload,{campaign:c,result:{campaign:project(c)}}),path,payload);
    }
    server.unexpected.push({path,method});throw failure(404);
  };
  return server;
}
export async function makeCampaigns({respond,historyState,wait=true,identity=accountKey,canWrite=true,url='https://orion.test/panel?aba=campanhas',server=syntheticServer(),cryptoEnabled=true,configureWindow}={}) {
  const errors=[],external=[],calls=[],console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error));
  const dom=new JSDOM('<!doctype html><html lang="pt-BR"><body><main><section id="campaigns"></section></main></body></html>',{url,runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:console});const {window}=dom,{document}=window;
  window.HTMLElement.prototype.scrollIntoView=()=>{};window.scrollTo=()=>{};
  window.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};window.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');this.dispatchEvent(new window.Event('close'));};
  window.fetch=async(...args)=>{external.push(args);throw Error('Direct external fetch forbidden');};
  if(historyState)window.history.replaceState(copy(historyState),'',window.location.href);
  const access={allowed:true,writable:canWrite};
  const bridge={host:document.getElementById('campaigns'),tenantId:tenantA,accountKey:identity,canAccess:()=>access.allowed,canWrite:()=>access.writable,async request(path,method='GET',payload){calls.push({path,method,data:payload?copy(payload):null});const custom=await respond?.(path,method,payload,server);if(custom!==undefined)return copy(custom);return copy(await server.execute(path,method,payload));}};
  window.eval(await readFile(new URL('../app/static/orion-fields.js',import.meta.url),'utf8'));install(window);if(!cryptoEnabled)Object.defineProperty(window.crypto,'subtle',{value:undefined,configurable:true});configureWindow?.(window);const controller=window.OrionSubscriptionCampaigns.mount(bridge);if(wait)await controller.ready;
  function element(selector){const node=document.querySelector(selector);assert.ok(node,'Missing '+selector);return node;}
  return {dom,window,document,controller,bridge,server,access,calls,external,errors,element,
    click(selector){const node=element(selector);node.click();return node;},
    fill(selector,value){const node=element(selector);node.value=value;node.dispatchEvent(new window.Event('input',{bubbles:true}));return node;},
    change(selector,value){const node=element(selector);if(node.type==='checkbox')node.checked=Boolean(value);else node.value=value;node.dispatchEvent(new window.Event('change',{bubbles:true}));return node;},
    submit(kind='campaign'){element('[data-cp-form="'+kind+'"]').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));},
    close(){window.OrionSubscriptionCampaigns.unmount();window.close();}
  };
}
