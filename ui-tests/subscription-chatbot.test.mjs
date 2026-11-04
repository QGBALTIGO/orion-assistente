import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {makeSuite,planState,defaultResponse} from './harness.mjs';
import {makeChatbot,base,ruleId,deviceId,backupId,jobId,previewId,listing,catalog,detail,preview,job,copy,contract,failure,flush,deferred,tenantA,tenantB,archiveDocument,archiveZip,archiveResponse} from './subscription-chatbot-harness.mjs';
const field=path=>'[data-sc-field="'+path+'"]',act=name=>'[data-sc-action="'+name+'"]',form=name=>'[data-sc-form="'+name+'"]';
const writes=h=>h.calls.filter(call=>call.method!=='GET');
const source=await readFile(new URL('../app/static/orion-subscription-chatbot.js',import.meta.url),'utf8');
const permissions=planState('none',{workspace_profile:'subscription_services',features:['account.manage','business.setup','devices.manage','chatbot.manage'],permissions:{'account.manage':true,'business.setup':true,'devices.manage':true,'chatbot.manage':true}});
async function edit(h){h.click(act('edit'));await flush();}
function createText(h,text='Olá, {name}!'){h.click(act('create'));h.fill(field('keyword'),'Mensagem sintética');h.fill(field('response.actions.0.text'),text);}
function updated(server,payload){const next={...copy(server.rule.rule),...copy(payload.rule),revision:server.rule.rule.revision+1};server.rule={rule:next,chatbot_revision:2};server.data.chatbot_revision=2;server.catalog.chatbot_revision=2;return server.rule;}
async function openRestore(h,mode='add_missing'){h.click(act('backups'));await flush();h.click(act('backup-restore'));h.check(field('transfer_mode')+'[value="'+mode+'"]');}
async function upload(h,contents='{}',name='orion-chatbot.json',size){await h.file(contents,name,size);h.element('[data-sc-import-file]').required=false;}

test('Chatbot is subscription-only, reads scoped endpoints and leaves other modules closed',async t=>{
 const h=await makeSuite({initialRoute:'chatbot',permissions,bridge:{tenants:[{id:tenantA,name:'Empresa sintética',role:'owner',workspace_profile:'subscription_services'}]},configureWindow:w=>w.eval(source),respond:path=>path.startsWith(base+'?')?listing():path===base+'/catalog'?catalog():defaultResponse(path)});t.after(()=>h.close());await flush();
 assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'chatbot');assert.equal(h.document.querySelectorAll('[data-sc-card]').length,1);
 assert.deepEqual([...h.document.querySelectorAll('#osNav [data-os-route]')].map(n=>n.dataset.osRoute),['dispositivos','chatbot','novidades','ajuda','member']);assert.equal(writes(h).length,0);assert.equal(h.calls.some(c=>/testar.chatbot|templates|plan-|management\/dashboard/.test(c.path)),false);
 await h.window.OrionSuite.open('inbox');await flush();assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'novidades');assert.equal(h.document.querySelector('#osSubscriptionChatbot'),null);assert.deepEqual(h.errors,[]);
});

test('standard businesses cannot open Chatbot with an injected capability',async t=>{
 const h=await makeSuite({initialRoute:'chatbot',permissions:planState('elite',{workspace_profile:'standard',permissions:{'chatbot.manage':true,'account.manage':true}})});t.after(()=>h.close());await flush();assert.equal(h.document.querySelector('#osNav [data-os-route=chatbot]'),null);assert.equal(h.calls.some(c=>c.path.includes('/subscription-whatsapp/')),false);assert.deepEqual(h.errors,[]);
});

test('opening and formatted/raw previews are escaped reads without URL calls or sends',async t=>{
 const rule=detail();rule.rule.keyword='<img src=x onerror=alert(1)>';rule.rule.response.actions[0].text='*Olá*, {name}! <script>alert(1)</script> _teste_';const data=listing();data.items[0].keyword=rule.rule.keyword;
 const h=await makeChatbot({data,rule});t.after(()=>h.close());assert.equal(h.element('[data-sc-card] h2').textContent,rule.rule.keyword);assert.equal(h.document.querySelector('[data-sc-card] h2 img'),null);await h.expand();assert.equal(h.element('.os-chatbot-preview strong:nth-of-type(2)').textContent,'Olá');assert.equal(h.document.querySelector('.os-chatbot-preview script'),null);
 h.click(act('preview-raw'));assert.match(h.element('.os-chatbot-raw').textContent,/\*Olá\*/);assert.equal(writes(h).length,0);assert.deepEqual(h.errors,[]);
});

test('all filters use the contracted query names, preserve scope, and reject reversed dates',async t=>{
 const h=await makeChatbot();t.after(()=>h.close());h.fill(field('filter.q'),'preço & valor');h.change(field('filter.search_in'),'response');h.change(field('filter.device_id'),'all');h.change(field('filter.status'),'inactive');h.change(field('filter.match_mode'),'array');h.change(field('filter.response_type'),'url');h.fill(field('filter.created_from'),'2026-11-01');h.fill(field('filter.created_to'),'2026-11-04');h.submit(form('filters'));await flush();const url=new URL(h.calls.at(-1).path,'https://orion.test');assert.equal(url.searchParams.get('q'),'preço & valor');assert.equal(url.searchParams.get('device_id'),'all');assert.equal(url.searchParams.get('search_in'),'response');assert.equal(url.searchParams.get('created_from'),'2026-11-01');
 const n=h.calls.length;h.fill(field('filter.created_from'),'2026-12-01');h.submit(form('filters'));await flush();assert.equal(h.calls.length,n);assert.match(h.element('[data-sc-feedback]').textContent,/data inicial/);h.click(act('clear-filters'));await flush();assert.equal(new URL(h.calls.at(-1).path,'https://orion.test').searchParams.has('device_id'),false);assert.equal(writes(h).length,0);
});

test('editor saves a versioned tenant-scoped definition with an idempotency key',async t=>{
 const h=await makeChatbot({respond(path,method,payload,server){if(path===base+'/rules'&&method==='POST')return updated(server,payload);}});t.after(()=>h.close());createText(h);h.change(field('match_mode'),'array');h.fill(field('keyword'),'Olá, bom dia');h.change(field('device_id'),deviceId);h.submit(form('editor'));await flush();const call=writes(h)[0];assert.equal(call.path,base+'/rules');assert.equal(call.data.expected_chatbot_revision,1);assert.match(call.data.idempotency_key,/^[a-f0-9-]{36}$/);assert.equal(call.data.rule.device_id,deviceId);assert.equal(call.data.rule.match_mode,'array');assert.equal(call.data.rule.response.actions[0].delay_seconds,1);assert.equal(h.document.querySelector(form('editor')),null);assert.match(h.element('[data-sc-feedback]').textContent,/criada/);assert.deepEqual(h.errors,[]);
});

test('dirty editor close needs confirmation and switching kinds preserves each draft',async t=>{
 const h=await makeChatbot();t.after(()=>h.close());createText(h,'Texto preservado');h.click(act('response-list'));h.fill(field('response.title'),'Lista preservada');h.click(act('response-sequence'));assert.equal(h.element(field('response.actions.0.text')).value,'Texto preservado');h.click(act('response-list'));assert.equal(h.element(field('response.title')).value,'Lista preservada');h.click('.os-chatbot-editor '+act('close-dialog'));assert.ok(h.element(form('discard')));h.click(form('discard')+' '+act('close-dialog'));assert.ok(h.element(form('editor')));h.click('.os-chatbot-editor '+act('close-dialog'));h.submit(form('discard'));assert.equal(h.document.querySelector(form('editor')),null);assert.equal(writes(h).length,0);
});

test('sequence reordering preserves action identity and bounds reject more than 30 seconds',async t=>{
 const h=await makeChatbot();t.after(()=>h.close());createText(h,'Primeira');h.click(act('step-add'));h.fill(field('response.actions.1.text'),'Segunda');h.click(act('step-up')+'[data-sc-index="1"]');assert.equal(h.element(field('response.actions.0.text')).value,'Segunda');h.fill(field('response.actions.0.delay_seconds'),'15');h.fill(field('response.actions.1.delay_seconds'),'15');assert.equal(h.element(act('step-add')).disabled,true);h.fill(field('response.actions.1.delay_seconds'),'14');h.click(act('step-add'));h.fill(field('response.actions.2.text'),'Terceira');h.fill(field('response.actions.2.delay_seconds'),'2');h.submit(form('editor'));await flush();assert.equal(writes(h).length,0);assert.match(h.element('[data-sc-dialog-error]').textContent,/30 segundos/);h.click(act('step-remove')+'[data-sc-index="2"]');assert.equal(h.document.querySelectorAll('[data-sc-step]').length,2);
});

test('eight action editors use real catalog references, preserve variants, and do no URL preview work',async t=>{
 const config=catalog();const templates=[['template','image'],['sticker','sticker'],['shortvideo','ptv']].map(([name,kind],i)=>({id:'80000000-0000-4000-8000-00000000000'+(i+1),name,kind,revision:1,available:true}));config.templates=templates;
 config.rules.push({id:'30000000-0000-4000-8000-000000000002',keyword:'Menu sintético',device_id:null,enabled:false,response_kind:'list',revision:1});
 const h=await makeChatbot({configuration:config});t.after(()=>h.close());createText(h,'Texto preservado');
 for(const type of ['template','sticker','shortvideo']){h.change(field('response.actions.0.type'),type);const options=[...h.element(field('response.actions.0.template_id')).options].map(o=>o.value);assert.ok(options.includes(templates.find(x=>x.name===type).id));}
 h.change(field('response.actions.0.type'),'carousel');assert.equal(h.document.querySelectorAll('[data-sc-field$=".image_url"]').length,3);assert.equal(h.document.querySelector('[data-sc-field$=".action_url"]'),null);
 h.change(field('response.actions.0.type'),'url');h.fill(field('response.actions.0.url'),'https://example.invalid/resposta');h.fill(field('response.actions.0.custom_template'),'{{status}}');h.check(field('response.actions.0.as_file'));assert.equal(writes(h).length,0);
 h.change(field('response.actions.0.type'),'nested_reply');assert.ok([...h.element(field('response.actions.0.rule_id')).options].some(o=>o.value===ruleId));h.change(field('response.actions.0.type'),'nested_list');h.change(field('response.actions.0.rule_id'),config.rules.at(-1).id);assert.match(h.element(form('editor')).textContent,/selecionada está inativa/);
 h.change(field('response.actions.0.type'),'text');assert.equal(h.element(field('response.actions.0.text')).value,'Texto preservado');h.change(field('response.actions.0.type'),'url');assert.equal(h.element(field('response.actions.0.url')).value,'https://example.invalid/resposta');assert.equal(h.element(field('response.actions.0.as_file')).checked,true);assert.equal(writes(h).length,0);assert.deepEqual(h.errors,[]);
});

test('empty template catalogs do not fabricate selectable templates or saving success',async t=>{
 const h=await makeChatbot();t.after(()=>h.close());createText(h);h.change(field('response.actions.0.type'),'template');assert.equal(h.element(field('response.actions.0.template_id')).disabled,true);assert.equal(h.element(field('response.actions.0.template_id')).options.length,1);h.submit(form('editor'));await flush();assert.equal(writes(h).length,0);assert.match(h.element('[data-sc-dialog-error]').textContent,/template disponível/);
});

test('menu allows ten total rows, adds two-row sections, and row delays are capped at five',async t=>{
 const h=await makeChatbot();t.after(()=>h.close());h.click(act('create'));h.click(act('response-list'));assert.equal(h.document.querySelectorAll('[data-sc-menu-row]').length,2);for(let i=0;i<4;i++)h.click(act('section-add'));assert.equal(h.document.querySelectorAll('[data-sc-menu-row]').length,10);assert.equal(h.element(act('section-add')).disabled,true);assert.equal(h.element(act('row-add')).disabled,true);assert.equal(h.element(field('response.sections.0.rows.0.action.delay_seconds')).max,'5');
 h.click(act('row-remove')+'[data-sc-section="0"][data-sc-index="1"]');h.click(act('section-add'));assert.equal(h.document.querySelectorAll('[data-sc-menu-row]').length,10);assert.equal(h.document.querySelectorAll('[data-sc-menu-section]').length,6);assert.equal(writes(h).length,0);
});

test('five contact tags are documented and unrecognized expressions cannot save',async t=>{
 const h=await makeChatbot();t.after(()=>h.close());createText(h,'Olá, {password}');h.click(act('tags'));assert.equal(h.document.querySelectorAll(form('tags')+' code').length,5);h.click(form('tags')+' '+act('close-dialog'));h.submit(form('editor'));await flush();assert.equal(writes(h).length,0);assert.match(h.element('[data-sc-dialog-error]').textContent,/cinco tags/);
});

test('copy creates an inactive rule only after confirmation; destructive dialogs default to cancel',async t=>{
 const h=await makeChatbot({respond(path,method,payload){if(path.endsWith('/duplicate')&&method==='POST'){const response=detail();response.rule.id='30000000-0000-4000-8000-000000000009';response.rule.enabled=false;response.rule.creation_order=2;return response;}}});t.after(()=>h.close());h.click(act('copy'));assert.equal(writes(h).length,0);h.submit(form('copy'));await flush();assert.equal(writes(h)[0].data.expected_revision,1);assert.match(h.element('[data-sc-feedback]').textContent,/inativa/);h.click(act('delete'));h.click(form('delete')+' '+act('close-dialog'));h.click(act('delete-all'));assert.equal(h.element(field('delete_all_confirm')).value,'no');h.submit(form('delete-all'));await flush();assert.equal(writes(h).length,1);
});

test('test response requires an explicit modal submit and never sends on opening',async t=>{
 const h=await makeChatbot();t.after(()=>h.close());await h.expand();h.click(act('test'));assert.match(h.element(form('test')).textContent,/telefone real/);h.fill(field('test_phone'),'+55 11 99999-9999');h.click(form('test')+' '+act('close-dialog'));assert.equal(writes(h).length,0);
});

test('URL tag inspection is explicit and pauses on an uncertain command with the identical retry key',async t=>{
 let attempts=0;const h=await makeChatbot({respond(path,method){if(path.endsWith('/url-tags')&&method==='POST'){if(++attempts===1)throw failure(503);return job('succeeded','url_tags',{result:{tags:['status','message']}});}}});t.after(()=>h.close());createText(h);h.change(field('response.actions.0.type'),'url');h.fill(field('response.actions.0.url'),'https://example.invalid/test');h.click(act('read-url-tags'));assert.equal(writes(h).length,0);h.submit(form('url-tags'));await flush();assert.equal(writes(h).length,1);await h.tick(10000);assert.equal(writes(h).length,1);h.click(form('url-tags')+' '+act('retry-pending'));await flush();assert.equal(writes(h).length,2);assert.deepEqual(writes(h)[0].data,writes(h)[1].data);assert.match(h.element(form('editor')).textContent,/\{\{status\}\}/);assert.equal(h.document.querySelector(form('url-tags')),null);assert.deepEqual(h.errors,[]);
});

test('read-only accounts keep backup download and selected export while every mutation is unavailable',async t=>{
 const config=catalog();config.capabilities={...config.capabilities,manage:false,test_response:false,import:false,restore:false,read_url_tags:false};const h=await makeChatbot({configuration:config,respond(path,method){if(path===base+'/exports'&&method==='POST')return {export:{...copy(contract.export_response.export),url:base+'/exports/'+jobId}};}});t.after(()=>h.close());assert.equal(h.element(act('create')).disabled,true);assert.equal(h.element(act('import')).disabled,true);assert.equal(h.element(act('edit')).disabled,true);
 h.click(act('export'));await flush();assert.equal(h.element(form('export')+' button[type=submit]').disabled,true);h.check('[data-sc-select-id="'+ruleId+'"]');h.submit(form('export'));await flush();assert.deepEqual(writes(h)[0].data,{ids:[ruleId],format:'zip'});assert.equal(h.downloads.length,1);
 h.click(act('backups'));await flush();assert.equal(h.element(act('backup-restore')).disabled,true);h.click(act('backup-download'));await flush();assert.equal(h.downloads.length,2);assert.equal(writes(h).length,1);assert.deepEqual(h.errors,[]);
 for(const saved of h.downloads){assert.match(saved.href,/^blob:/);assert.equal(saved.blob.type,'application/zip');assert.deepEqual(Buffer.from(await saved.blob.arrayBuffer()),archiveZip);}
 for(const {options} of h.downloadCalls){assert.equal(options.method,'GET');assert.equal(options.credentials,'same-origin');assert.equal(options.mode,'same-origin');assert.equal(options.redirect,'error');assert.equal(options.cache,'no-store');}
 assert.equal(h.downloads[1].download,'orion-chatbot-backup.zip');await h.tick(1000);assert.equal(h.objectURLs.size,0);assert.equal(h.revokedURLs.length,2);
});

test('external, cross-tenant and query-bearing archive URLs are refused before fetch',async t=>{
 for(const url of ['https://example.invalid/download',base.replace(tenantA,tenantB)+'/exports/'+jobId,base+'/exports/'+jobId+'?redirect=external']){
  const h=await makeChatbot({respond(path,method){if(path===base+'/exports'&&method==='POST')return {export:{id:jobId,url}};}});t.after(()=>h.close());h.click(act('export'));await flush();h.check('[data-sc-export-all]');h.submit(form('export'));await flush();assert.equal(h.downloads.length,0);assert.equal(h.downloadCalls.length,0);assert.equal(h.controller.getState().pending,null);assert.match(h.element('[data-sc-dialog-error]').textContent,/endereço do arquivo/);assert.equal(writes(h).length,1);
 }
});

test('JSON export waits for the validated response before announcing or saving the archive',async t=>{
 const ready=deferred();const h=await makeChatbot({respond(path,method){if(path===base+'/exports'&&method==='POST')return {export:{id:jobId,url:base+'/exports/'+jobId}};},respondDownload:()=>ready.promise});t.after(()=>h.close());h.click(act('export'));await flush();h.check('[data-sc-export-all]');h.change(field('export_format'),'json');h.submit(form('export'));await flush();assert.equal(h.downloads.length,0);assert.equal(h.controller.getState().busy,true);assert.equal(h.element('[data-sc-feedback]').textContent,'');ready.resolve(archiveResponse('json'));await flush();assert.equal(h.downloads.length,1);assert.deepEqual(JSON.parse(await h.downloads[0].blob.text()),archiveDocument);assert.equal(h.downloads[0].download,'orion-chatbot.json');assert.equal(h.document.querySelector(form('export')),null);assert.match(h.element('[data-sc-feedback]').textContent,/Download.*iniciado/);
});

test('HTTP failures from export and backup remain visible and never become downloaded files or pending commands',async t=>{
 for(const [kind,status] of [['export',413],['backups',409]]){
  const message='Falha sintética: confira o arquivo antes de tentar novamente.';const h=await makeChatbot({respond(path,method){if(path===base+'/exports'&&method==='POST')return {export:{id:jobId,url:base+'/exports/'+jobId}};},respondDownload:()=>new Response(JSON.stringify({detail:{code:'CHATBOT_INVALID_GRAPH',message}}),{status,headers:{'Content-Type':'application/json'}})});t.after(()=>h.close());h.click(act(kind));await flush();if(kind==='export'){h.check('[data-sc-export-all]');h.submit(form('export'));}else h.click(act('backup-download'));await flush();assert.equal(h.downloads.length,0);assert.equal(h.objectURLs.size,0);assert.equal(h.controller.getState().pending,null);assert.equal(h.controller.getState().busy,false);assert.equal(h.element(form(kind)+' [data-sc-dialog-error]').textContent,message);assert.equal(h.element('[data-sc-feedback]').textContent,'');await h.tick(10000);assert.equal(h.downloadCalls.length,1);
 }
});

test('wrong archive MIME, partial bodies and successful JSON error payloads are not saved',async t=>{
 for(const response of [archiveResponse('zip','{"detail":"not a zip"}',{headers:{'Content-Type':'application/json'}}),archiveResponse('zip',archiveZip,{headers:{'Content-Length':String(archiveZip.length+1)}}),archiveResponse('json','{"detail":"not an export"}')]){
  const h=await makeChatbot({respond(path,method){if(path===base+'/exports'&&method==='POST')return {export:{id:jobId,url:base+'/exports/'+jobId}};},respondDownload:()=>response});t.after(()=>h.close());h.click(act('export'));await flush();h.check('[data-sc-export-all]');if(response.headers.get('Content-Type')==='application/json; charset=utf-8')h.change(field('export_format'),'json');h.submit(form('export'));await flush();assert.equal(h.downloads.length,0);assert.equal(h.objectURLs.size,0);assert.match(h.element('[data-sc-dialog-error]').textContent,/formato esperado|incompleto|não é um arquivo/);assert.equal(h.controller.getState().pending,null);
 }
});

test('archive streams cannot exceed the byte budget when Content-Length is missing or false',async t=>{
 for(const length of [null,'1','10485761']){
  let cancelled=false;const stream=new ReadableStream({start(controller){controller.enqueue(new Uint8Array(10*1024*1024));controller.enqueue(new Uint8Array([0]));},cancel(){cancelled=true;}});
  const h=await makeChatbot({respondDownload:()=>new Response(stream,{headers:{'Content-Type':'application/zip',...(length===null?{}:{'Content-Length':length})}})});t.after(()=>h.close());h.click(act('backups'));await flush();h.click(act('backup-download'));await flush();assert.equal(h.downloads.length,0);assert.equal(h.objectURLs.size,0);assert.match(h.element('[data-sc-dialog-error]').textContent,/tamanho permitido/);if(length!=='10485761')assert.equal(cancelled,true);assert.equal(h.controller.getState().busy,false);
 }
});

test('unmount aborts an archive request and ignores its late response',async t=>{
 const ready=deferred();const h=await makeChatbot({respondDownload:()=>ready.promise});t.after(()=>h.close());h.click(act('backups'));await flush();h.click(act('backup-download'));await flush();const signal=h.downloadCalls[0].options.signal;assert.equal(signal.aborted,false);h.controller.unmount();assert.equal(signal.aborted,true);ready.resolve(archiveResponse());await flush();assert.equal(h.downloads.length,0);assert.equal(h.objectURLs.size,0);assert.equal(h.document.querySelector('dialog'),null);assert.deepEqual(h.errors,[]);
});

test('import uploads only a bounded file for preview, changing assignment invalidates that preview',async t=>{
 const h=await makeChatbot({respond(path,method){if(path===base+'/imports/preview'&&method==='POST')return preview();}});t.after(()=>h.close());h.click(act('import'));await upload(h,'{"schema_version":1}');h.submit(form('import'));await flush();await flush();const request=writes(h)[0];assert.equal(request.path,base+'/imports/preview');assert.equal(Buffer.from(request.data.content_base64,'base64').toString(),' {"schema_version":1}'.trim());assert.equal(request.data.device_id,null);assert.equal(h.document.querySelectorAll('.os-chatbot-preview-counts>div').length,6);assert.equal(writes(h).length,1);h.change(field('transfer_device'),deviceId);assert.equal(h.document.querySelector('.os-chatbot-preview-counts'),null);assert.match(h.element('[data-sc-transfer-submit]').textContent,/Visualizar/);
});

test('import rejects oversized files before any API request',async t=>{
 const h=await makeChatbot();t.after(()=>h.close());h.click(act('import'));await upload(h,'{}','orion-chatbot.zip',10*1024*1024+1);h.submit(form('import'));await flush();assert.equal(writes(h).length,0);assert.match(h.element('[data-sc-dialog-error]').textContent,/até 10 MB/);
});

test('exact restore needs a server preview and the case-sensitive RESTORE phrase',async t=>{
 const h=await makeChatbot({respond(path,method){if(path===base+'/backups/'+backupId+'/preview'&&method==='POST')return preview({source:'backup',mode:'exact',counts:{create:0,update:1,delete:3,skip:0,template_clones:0,media_copies:0}});if(path===base+'/previews/'+previewId+'/apply'&&method==='POST')return job('queued');}});t.after(()=>h.close());await openRestore(h,'exact');assert.equal(writes(h).length,0);h.submit(form('restore'));await flush();assert.equal(writes(h).length,1);assert.equal(h.element('[data-sc-transfer-submit]').disabled,true);h.fill(field('restore_confirmation'),'restore');h.submit(form('restore'));await flush();assert.equal(writes(h).length,1);h.fill(field('restore_confirmation'),'RESTORE');assert.equal(h.element('[data-sc-transfer-submit]').disabled,false);h.submit(form('restore'));await flush();assert.equal(writes(h).length,2);assert.equal(writes(h)[1].data.confirmation,'RESTORE');assert.equal(h.controller.getState().pending,'job');assert.match(h.element(form('restore')).textContent,/na fila/);
});

test('expired restore previews cannot be applied and mode changes discard prior confirmation',async t=>{
 const h=await makeChatbot({respond(path,method){if(path.endsWith('/preview')&&method==='POST')return preview({source:'backup',mode:'exact',expires_at:'2026-11-04T00:00:02Z'});}});t.after(()=>h.close());await openRestore(h,'exact');h.submit(form('restore'));await flush();h.fill(field('restore_confirmation'),'RESTORE');await h.tick(2100);assert.equal(h.element('[data-sc-transfer-submit]').disabled,true);h.submit(form('restore'));await flush();assert.equal(writes(h).length,1);h.check(field('transfer_mode')+'[value="merge"]');assert.equal(h.document.querySelector(field('restore_confirmation')),null);assert.match(h.element('[data-sc-transfer-submit]').textContent,/Visualizar/);
});

test('pending restore from reload uses read-only job reconciliation, pauses hidden and never repeats apply',async t=>{
 let polls=0;const pending=job().job;const h=await makeChatbot({data:listing({pending_job:pending}),respond(path,method,_payload,server){if(path===base+'/jobs/'+jobId){polls++;if(polls===1)return job('running');server.data.pending_job=null;return job('succeeded','restore',{safety_backup_id:backupId});}}});t.after(()=>h.close());assert.equal(h.controller.getState().pending,'job');assert.equal(h.element(act('create')).disabled,true);await h.visibility(true);await h.tick(10000);assert.equal(polls,0);await h.visibility(false);assert.equal(polls,1);await h.tick(2100);assert.equal(polls,2);assert.equal(h.controller.getState().pending,null);assert.equal(writes(h).length,0);assert.match(h.element('[data-sc-feedback]').textContent,/Restauração concluída/);assert.deepEqual(h.errors,[]);
});

test('malformed terminal job never clears the unknown outcome or retries the original action',async t=>{
 let polls=0;const h=await makeChatbot({respond(path,method){if(path.endsWith('/url-tags')&&method==='POST')return job('queued','url_tags');if(path===base+'/jobs/'+jobId){polls++;return job('succeeded','url_tags',{result:{tags:['not a valid tag']}});}}});t.after(()=>h.close());createText(h);h.change(field('response.actions.0.type'),'url');h.fill(field('response.actions.0.url'),'https://example.invalid/test');h.click(act('read-url-tags'));h.submit(form('url-tags'));await flush();await h.tick(2100);assert.equal(h.controller.getState().pending,'job');assert.equal(polls,1);assert.equal(writes(h).length,1);assert.equal(h.element('.os-chatbot-editor button[type=submit]').disabled,true);
});

test('409 preserves editor draft and requires explicit reload before save',async t=>{
 const h=await makeChatbot({respond(path,method){if(path===base+'/rules/'+ruleId&&method==='PUT')throw failure(409);}});t.after(()=>h.close());await edit(h);h.fill(field('response.actions.0.text'),'Rascunho preservado');h.submit(form('editor'));await flush();assert.equal(h.element(field('response.actions.0.text')).value,'Rascunho preservado');assert.equal(h.element(form('editor')+' button[type=submit]').disabled,true);h.click(act('reload-editor'));h.click(form('reload-editor')+' '+act('close-dialog'));assert.equal(h.element(field('response.actions.0.text')).value,'Rascunho preservado');h.click(act('reload-editor'));h.submit(form('reload-editor'));await flush();assert.equal(h.element(field('response.actions.0.text')).value,'Olá, {name}!');assert.equal(h.element(form('editor')+' button[type=submit]').disabled,false);assert.equal(writes(h).length,1);
});

test('unmount drops a delayed rule read and closes drafts without leaking state into the next tenant',async t=>{
 const late=deferred();const h=await makeChatbot({respond(path){if(path===base+'/rules/'+ruleId)return late.promise;}});t.after(()=>h.close());h.click(act('edit'));h.controller.unmount();late.resolve(detail());await flush();assert.equal(h.document.querySelector('dialog'),null);assert.equal(h.document.querySelector('#chatbot').children.length,0);assert.equal(writes(h).length,0);assert.deepEqual(h.errors,[]);
});

test('a failed initial read offers a read-only retry and never enables writes prematurely',async t=>{
 let fail=true;const h=await makeChatbot({respond(path){if(path===base+'/catalog'&&fail)throw failure(503);}});t.after(()=>h.close());assert.equal(h.element(act('create')).disabled,true);assert.match(h.element(act('refresh')).textContent,/Tentar novamente/);fail=false;h.click(act('refresh'));await flush();assert.equal(h.element(act('create')).disabled,false);assert.equal(writes(h).length,0);assert.deepEqual(h.errors,[]);
});

test('an invalid detail DTO fails safely without inserting it into the preview cache',async t=>{
 const value=detail();value.rule.response.actions=null;const h=await makeChatbot({rule:value});t.after(()=>h.close());await h.expand();assert.equal(h.document.querySelector('.os-chatbot-preview'),null);assert.match(h.element('[data-sc-feedback]').textContent,/preservados/);assert.deepEqual(h.errors,[]);assert.equal(writes(h).length,0);
});

test('an import uses the restore queue with source import and reports the correct completion',async t=>{
 let applied=false;const h=await makeChatbot({respond(path,method){if(path===base+'/imports/preview'&&method==='POST')return preview();if(path===base+'/previews/'+previewId+'/apply'&&method==='POST'){applied=true;return job('queued','restore',{source:'import'});}if(path===base+'/jobs/'+jobId)return job('succeeded','restore',{source:'import'});}});t.after(()=>h.close());h.click(act('import'));await upload(h);h.submit(form('import'));await flush();await flush();h.submit(form('import'));await flush();assert.equal(applied,true);await h.tick(2100);assert.equal(h.document.querySelector(form('import')),null);assert.match(h.element('[data-sc-feedback]').textContent,/Importação concluída/);assert.equal(writes(h).length,2);assert.equal(h.controller.getState().pending,null);
});

test('a failed restore invalidates its preview without retrying apply automatically',async t=>{
 const h=await makeChatbot({respond(path,method){if(path===base+'/backups/'+backupId+'/preview'&&method==='POST')return preview({source:'backup'});if(path===base+'/previews/'+previewId+'/apply'&&method==='POST')return job();if(path===base+'/jobs/'+jobId)return job('failed','restore',{error:'Falha sintética confirmada.'});}});t.after(()=>h.close());await openRestore(h);h.submit(form('restore'));await flush();h.submit(form('restore'));await flush();await h.tick(2100);assert.equal(h.document.querySelector('.os-chatbot-preview-counts'),null);assert.match(h.element('[data-sc-transfer-submit]').textContent,/Visualizar/);assert.match(h.element(form('restore')+' [data-sc-dialog-error]').textContent,/Falha sintética/);assert.equal(writes(h).length,2);assert.equal(h.controller.getState().pending,null);
});

test('URL tags work with synthetic data and no device, while opening still makes no request',async t=>{
 const h=await makeChatbot({configuration:catalog({devices:[]}),respond(path,method){if(path===base+'/url-tags'&&method==='POST')return job('succeeded','url_tags',{result:{tags:['status']}});}});t.after(()=>h.close());createText(h);h.change(field('response.actions.0.type'),'url');h.fill(field('response.actions.0.url'),'https://example.invalid/reply');h.click(act('read-url-tags'));assert.match(h.element(form('url-tags')).textContent,/sem dispositivo conectado/);assert.equal(h.element(form('url-tags')+' button[type=submit]').disabled,false);assert.equal(h.document.querySelector(field('tags_device')),null);assert.equal(writes(h).length,0);h.submit(form('url-tags'));await flush();assert.equal(writes(h).length,1);assert.equal(writes(h)[0].data.device_id,null);assert.match(h.element(form('editor')).textContent,/\{\{status\}\}/);assert.deepEqual(h.errors,[]);
});

test('a partly accepted synthetic test reports the accepted count without claiming delivery or retrying',async t=>{
 const h=await makeChatbot({respond(path,method){if(path===base+'/rules/'+ruleId+'/test'&&method==='POST')return job('failed','test_response',{result:{accepted_messages:2},error:{code:'CHATBOT_DELIVERY_UNCONFIRMED',message:'A solicitação não foi concluída.'}});}});t.after(()=>h.close());await h.expand();h.click(act('test'));h.fill(field('test_phone'),'+55 11 99999-9999');h.submit(form('test'));await flush();const message=h.element(form('test')+' [data-sc-dialog-error]').textContent;assert.match(message,/2 mensagem\(ns\) já foram aceitas pelo servidor/);assert.match(message,/entrega não está confirmada/);assert.match(message,/Confira o WhatsApp antes de repetir/);assert.equal(writes(h).length,1);await h.tick(10000);assert.equal(writes(h).length,1);assert.equal(h.controller.getState().pending,null);assert.deepEqual(h.errors,[]);
});
