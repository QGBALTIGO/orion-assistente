import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {randomUUID,webcrypto,createHash} from 'node:crypto';
import {JSDOM,VirtualConsole} from 'jsdom';
import {flush,tenantA} from './harness.mjs';
export {flush,deferred,tenantA,tenantB} from './harness.mjs';

export const copy=value=>JSON.parse(JSON.stringify(value));
export const contract=JSON.parse(await readFile(new URL('../tests/fixtures/subscription_direct_messages_contract.json',import.meta.url),'utf8'));
export const source=await readFile(new URL('../app/static/orion-subscription-direct-messages.js',import.meta.url),'utf8');
export const base='/api/v1/tenants/'+tenantA+'/subscription-whatsapp/direct-messages';
export const templateBase=base.replace('/direct-messages','/templates');
export const sendId=contract.detail.send.send_id;
export const accountKey='synthetic-direct@example.invalid|member';
export const accountScope=createHash('sha256').update(accountKey).digest('hex');
export const failure=(status,extra={})=>Object.assign(Error('Falha sintética'),{status,...extra});
export const recovery=(extra={})=>({tenant_id:tenantA,account_scope:accountScope,idempotency_key:contract.review_request.idempotency_key,kind:'review',send_id:null,confirming:false,...extra});
export const writes=h=>h.calls.filter(call=>call.method!=='GET');
export const uid=n=>'00000000-0000-4000-8000-'+String(n).padStart(12,'0');
export const stamp=()=>new Date().toISOString();
export const kinds=['text','image','video','audio','document','sticker','ptv','location'];
const keys=(value,expected)=>assert.deepEqual(Object.keys(value).sort(),[...expected].sort());
const labels={pending:'Na fila',running:'Em processamento',sent:'Enviado',failed:'Falhou',uncertain:'Resultado incerto',cancelled:'Cancelado'};

// This ledger is deliberately transport-free. It rejects hidden union fields,
// records idempotent admissions and exposes progress independently of the view.
export function syntheticServer(){
  const options=copy(contract.options);options.generated_at=stamp();
  options.devices.push({...copy(options.devices[0]),id:uid(30),name:'Dispositivo secundário'});
  options.templates=kinds.map((kind,index)=>({id:uid(100+index),name:'Template '+kind,kind,available:true,blocked_reason:null,revision:1}));
  const contacts=Array.from({length:35},(_,index)=>({...copy(contract.contacts.items[0]),key:'phone:'+String(5567999999901+index),phone_e164:'+'+String(5567999999901+index),name:index?'Contato sintético '+String(index+1).padStart(2,'0'):'Contato sintético'}));
  contacts[34].available=false;contacts[34].blocked_reason='O contato retirou a autorização.';
  const server={options,contacts,sends:new Map([[sendId,copy(contract.detail)]]),requests:new Map(),uploads:new Map(),assets:new Map(),savedTemplates:[],confirmations:[],effects:[],unexpected:[]};
  const paged=(items,q=new URLSearchParams())=>{const page=Math.max(1,Number(q.get('page')||1));return {items:copy(items.slice((page-1)*30,page*30)),total:items.length,page,page_size:30,pages:Math.max(1,Math.ceil(items.length/30)),timezone:options.timezone,generated_at:stamp()};};
  const publicOperation=entry=>{const op=copy(entry.operation);if(op.status==='prepared'&&Date.parse(op.expires_at)<=Date.now())op.status='expired';if(op.status!=='prepared')op.preview=null;return op;};
  const operation=(kind,payload,{preview=null,result=null,id=null,status='prepared'}={})=>({request_id:randomUUID(),idempotency_key:payload.idempotency_key,kind,status,send_id:id,replayed:false,created_at:stamp(),expires_at:preview?.expires_at||null,preview,result});
  const record=(path,payload,op)=>{const entry={path,payload:copy(payload),operation:copy(op)};server.requests.set(op.idempotency_key,entry);return publicOperation(entry);};
  server.updateProgress=detail=>{const rows=detail.recipients.items,total=rows.length,count=kind=>rows.filter(row=>row.status===kind).length,processed=rows.filter(row=>!['pending','running'].includes(row.status)).length;detail.send.progress={total,processed,pending:count('pending')+count('running'),sent:count('sent'),failed:count('failed'),uncertain:count('uncertain'),cancelled:count('cancelled'),percent:total?Math.round(processed/total*1000)/10:0};};
  function content(message){
    const template=options.templates.find(t=>t.id===message.template_id),asset=server.assets.get(message.asset_id);
    const kind=message.kind==='template'?template?.kind:message.kind==='media'?asset?.kind:'text';assert.ok(kind);
    let body=message.kind==='text'?message.text:message.kind==='media'?message.caption:'Conteúdo sintético '+kind;
    const warnings=[];if(message.kind==='media'){if(kind==='audio'){if(body)warnings.push('AUDIO_CAPTION_OMITTED');body='';}else if(/[\r\n]/.test(body)){body=body.replace(/[\r\n]+/g,' ');warnings.push('CAPTION_LINES_NORMALIZED');}}
    const followup=template&&kind!=='text'?'Complemento sintético':null;
    const part={type:kind,...(kind==='text'?{text:body}:{}),...(['image','video','document'].includes(kind)?{caption:body}:{}),...(kind==='document'?{file_name:message.file_name||'Exemplo.pdf'}:{}),...(kind==='location'?{latitude:-15.6,longitude:-56.1}:{})};
    return {kind,warnings,template,message_preview:{type:kind,template_name:template?.name||null,text:kind==='text'?body:null,caption:['image','video','document'].includes(kind)?body:null,followup,parts:[part,...(followup?[{type:'text',text:followup}]:[])]}};
  }
  server.execute=async(path,method='GET',payload)=>{
    const url=new URL(path,'https://orion.test');
    if(url.pathname===templateBase+'/assets'&&method==='POST'){
      keys(payload,['request_id','expected_chatbot_revision','kind','filename','content_base64']);assert.equal(payload.expected_chatbot_revision,options.chatbot_revision);assert.ok(['image','video','audio','document'].includes(payload.kind));
      const old=server.uploads.get(payload.request_id);if(old){assert.deepEqual(old.payload,copy(payload));return copy(old.result);}
      const extension=payload.kind==='image'?'jpg':payload.kind==='audio'?'mp3':payload.filename.split('.').pop().toLowerCase(),filename=payload.filename.slice(0,payload.filename.lastIndexOf('.')).trim().replace(/^\.+|\.+$/g,'')+'.'+extension;
      const mime={jpg:'image/jpeg',mp3:'audio/mpeg',mp4:'video/mp4',txt:'text/plain; charset=utf-8',csv:'text/plain; charset=utf-8',pdf:'application/pdf',docx:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',xlsx:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',apk:'application/vnd.android.package-archive'}[extension];
      const result={...copy(contract.upload_response),request_id:payload.request_id,chatbot_revision:options.chatbot_revision,file_name:filename};result.asset={...result.asset,id:randomUUID(),kind:payload.kind,mime,file_name:filename,size:Buffer.from(payload.content_base64,'base64').length};
      server.uploads.set(payload.request_id,{payload:copy(payload),result:copy(result)});server.assets.set(result.asset.id,result.asset);return result;
    }
    if(!url.pathname.startsWith(base)){server.unexpected.push({path,method});throw failure(404);}
    const suffix=url.pathname.slice(base.length),q=url.searchParams;
    if(method==='GET'){
      if(suffix==='/options')return copy(options);
      if(suffix==='/contacts'){const term=(q.get('q')||'').toLocaleLowerCase();return paged(contacts.filter(c=>(c.name+' '+c.phone_e164).toLocaleLowerCase().includes(term)),q);}
      if(suffix.startsWith('/requests/')){const entry=server.requests.get(suffix.split('/').at(-1));if(!entry)throw failure(404);return {...publicOperation(entry),replayed:true};}
      const detail=server.sends.get(suffix.slice(1));if(detail)return {send:copy(detail.send),recipients:paged(detail.recipients.items)};
      throw failure(404);
    }
    assert.equal(method,'POST');assert.ok(payload?.idempotency_key);const prior=server.requests.get(payload.idempotency_key);
    if(suffix==='/confirm'){
      keys(payload,['idempotency_key','review_version','confirmation','consent_confirmed']);assert.equal(payload.confirmation,'ENVIAR');assert.equal(payload.consent_confirmed,true);assert.ok(prior);
      if(prior.operation.status==='succeeded')return {...publicOperation(prior),replayed:true};if(Date.parse(prior.operation.expires_at)<=Date.now())throw failure(410);
      const preview=prior.operation.preview;assert.equal(payload.review_version,preview.review_version);assert.ok(preview.eligible);
      const id=randomUUID(),rows=preview.items.map(row=>{const value=copy(row);delete value.available;delete value.blocked_reason;return {...value,id:randomUUID(),send_id:id,status:row.available?'pending':'cancelled',status_label:row.available?'Na fila':'Cancelado',error_message:row.blocked_reason,parts:[],sent_at:null,created_at:stamp()};});
      const send={...copy(contract.detail.send),send_id:id,created_at:stamp(),confirmed_at:stamp()},detail={send,recipients:paged(rows)};detail.recipients.items=rows;server.updateProgress(detail);server.sends.set(id,detail);
      const saved=preview.save_template?{id:randomUUID(),...preview.save_template,revision:options.chatbot_revision+1}:null;if(saved)server.savedTemplates.push(copy(saved));
      prior.operation={...prior.operation,status:'succeeded',send_id:id,result:{send:copy(send),saved_template:saved}};server.confirmations.push({idempotency_key:payload.idempotency_key,send_id:id});return publicOperation(prior);
    }
    if(prior){if(prior.path!==path||JSON.stringify(prior.payload)!==JSON.stringify(payload))throw failure(409,{code:'DIRECT_IDEMPOTENCY_CONFLICT'});return {...publicOperation(prior),replayed:true};}
    if(suffix==='/review'){
      keys(payload,['idempotency_key','message']);const m=payload.message,optional=m.save_template?['save_template']:[],union=m.kind==='text'?['text']:m.kind==='media'?['asset_id','file_name','caption']:['template_id'];keys(m,['kind','device_id','recipients',...union,...optional]);
      const device=options.devices.find(d=>d.id===m.device_id&&d.available);assert.ok(device);
      if(m.kind==='text')assert.ok(m.text.trim()&&m.text.length<=1000);if(m.kind==='media'){assert.equal(m.recipients.mode,'phone');assert.ok(server.assets.has(m.asset_id));assert.ok(m.caption.length<=1000);}
      if(m.save_template)assert.ok(m.save_template.name.trim()&&m.save_template.name.length<=100);
      const selected=m.recipients.mode==='phone'?[m.recipients.phone]:m.recipients.contact_keys.map(key=>key.slice(6));
      keys(m.recipients,m.recipients.mode==='phone'?['mode','phone','contact_keys']:['mode','contact_keys']);assert.ok(selected.length>=1&&selected.length<=10);assert.equal(new Set(selected).size,selected.length);for(const digits of selected)assert.match(digits,/^[1-9]\d{7,14}$/);
      const prepared=content(m),rows=selected.map((digits,index)=>{const contact=m.recipients.mode==='contacts'?contacts.find(c=>c.key==='phone:'+digits):null;const available=contact?.available!==false;return {row_key:randomUUID(),ordinal:index+1,source:contact?'catalog':'manual',phone_e164:'+'+digits,name:contact?.name||'',device:{id:device.id,name:device.name,phone_e164:device.phone_e164??null},message_type:m.kind,template:prepared.template?{id:prepared.template.id,name:prepared.template.name,kind:prepared.template.kind}:null,message_preview:copy(prepared.message_preview),available,blocked_reason:available?null:contact.blocked_reason,error_code:available?null:'DIRECT_OPTED_OUT'};});
      const blocked=rows.filter(row=>!row.available).length,preview={review_version:createHash('sha256').update(randomUUID()).digest('hex'),expires_at:new Date(Date.now()+120000).toISOString(),kind:m.kind,timezone:options.timezone,total:rows.length,eligible:rows.length-blocked,blocked,items:rows,save_template:m.save_template?{name:m.save_template.name,kind:prepared.kind}:null,warnings:prepared.warnings};
      return record(path,payload,operation('review',payload,{preview}));
    }
    if(suffix.endsWith('/actions')){
      keys(payload,['idempotency_key','action','expected_version','confirmation']);assert.equal(payload.action,'cancel');assert.equal(payload.confirmation,'CANCELAR');const id=suffix.split('/')[1],detail=server.sends.get(id);if(!detail)throw failure(404);
      if(payload.expected_version!==detail.send.version)throw failure(409,{code:'DIRECT_CONFLICT',directRejection:{reason:'stale_version',idempotency_key:payload.idempotency_key,kind:'cancel',send_id:id,expected_version:payload.expected_version}});
      for(const row of detail.recipients.items)if(row.status==='pending'){row.status='cancelled';row.status_label='Cancelado';}
      detail.send.version++;detail.send.status=detail.recipients.items.some(row=>row.status==='uncertain')?'uncertain':'cancelled';detail.send.status_label=labels[detail.send.status];detail.send.actions=[];detail.send.finished_at=stamp();server.updateProgress(detail);
      return record(path,payload,operation('cancel',payload,{id,status:'succeeded',result:{send:copy(detail.send),saved_template:null}}));
    }
    server.unexpected.push({path,method});throw failure(404);
  };return server;
}

export async function makeDirect({respond,historyState,wait=true,identity=accountKey,canWrite=true,url='https://orion.test/panel?aba=enviar-mensagem',server=syntheticServer(),cryptoEnabled=true,configureWindow}={}){
  const errors=[],external=[],calls=[],signals=[],console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error));
  const dom=new JSDOM('<!doctype html><html lang="pt-BR"><body><main><section id="direct"></section></main></body></html>',{url,runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:console});const {window}=dom,{document}=window;
  window.HTMLElement.prototype.scrollIntoView=()=>{};window.scrollTo=()=>{};window.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};window.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};
  window.fetch=async(...args)=>{external.push(args);throw Error('Direct external fetch forbidden');};Object.defineProperty(window.crypto,'randomUUID',{value:randomUUID,configurable:true});Object.defineProperty(window.crypto,'subtle',{value:webcrypto.subtle,configurable:true});window.TextEncoder=TextEncoder;
  if(historyState)window.history.replaceState(copy(historyState),'',window.location.href);const access={allowed:true,writable:canWrite};
  const bridge={host:document.getElementById('direct'),tenantId:tenantA,accountKey:identity,canAccess:()=>access.allowed,canWrite:()=>access.writable,async request(path,method='GET',payload,options){calls.push({path,method,data:payload?copy(payload):null});signals.push({path,method,signal:options?.signal});const custom=await respond?.(path,method,payload,server,options);if(custom!==undefined)return copy(custom);return copy(await server.execute(path,method,payload));}};
  window.eval(await readFile(new URL('../app/static/orion-fields.js',import.meta.url),'utf8'));window.eval(source);if(!cryptoEnabled)Object.defineProperty(window.crypto,'subtle',{value:undefined,configurable:true});configureWindow?.(window);
  const controller=window.OrionSubscriptionDirectMessages.mount(bridge);if(wait)await controller.ready;
  function element(selector){const node=document.querySelector(selector);assert.ok(node,'Missing '+selector);return node;}
  return {dom,window,document,controller,bridge,server,access,calls,signals,external,errors,element,
    click(selector){const node=element(selector);node.click();return node;},
    fill(selector,value){const node=element(selector);node.value=value;node.dispatchEvent(new window.Event('input',{bubbles:true}));return node;},
    change(selector,value){const node=element(selector);if(node.type==='checkbox'||node.type==='radio')node.checked=Boolean(value);else node.value=value;node.dispatchEvent(new window.Event('change',{bubbles:true}));return node;},
    async upload(name='orientacoes.txt',content='Arquivo de teste sintético',options={}){const node=element('[data-dm-file]'),file=new window.File([content],name,{type:options.type||'text/plain'});if(options.size!==undefined)Object.defineProperty(file,'size',{value:options.size});Object.defineProperty(node,'files',{value:options.files||[file],configurable:true});node.dispatchEvent(new window.Event('change',{bubbles:true}));for(let i=0;i<30;i++){await flush();if(!controller.getState().busy)break;}return file;},
    close(){window.OrionSubscriptionDirectMessages.unmount();window.close();}};
}
export function fillText(h,{text='Olá, {name}. Texto literal.',phone='5567999999901',device=h.server.options.devices[0].id}={}){h.change('[data-dm-field="device_id"]',device);h.fill('[data-dm-field="phone"]',phone);h.fill('[data-dm-field="text"]',text);}
export async function review(h,values){if(values!==false)fillText(h,values);h.click('[data-dm-action="review"]');await flush();}
export async function confirm(h){h.change('[data-dm-consent]',true);h.fill('[data-dm-field="confirmation"]','ENVIAR');h.click('[data-dm-action="confirm"]');await flush();}
