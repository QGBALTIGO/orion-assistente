import test from 'node:test';
import assert from 'node:assert/strict';
import {makeSuite,planState,defaultResponse} from './harness.mjs';
import {makeTemplates,source,contract,base,templateId,assetId,listing,detail,mediaTemplate,asset,binary,copy,failure,flush,deferred,tenantA,tenantB} from './subscription-templates-harness.mjs';
const act=name=>'[data-st-action="'+name+'"]',field=name=>'[data-st-field="'+name+'"]',form=name=>'[data-st-form="'+name+'"]',tab=name=>'[data-st-tab="'+name+'"]';
const writes=h=>h.calls.filter(call=>call.method!=='GET');
const permissions=planState('none',{workspace_profile:'subscription_services',features:['account.manage','business.setup','devices.manage','chatbot.manage','templates.manage'],permissions:{'account.manage':true,'business.setup':true,'devices.manage':true,'chatbot.manage':true,'templates.manage':true}});
function saved(server,payload){const value={...detail(),request_id:payload.request_id,chatbot_revision:payload.expected_chatbot_revision+1,template:{...server.record.template,...copy(payload.template),name:payload.template.name||'Texto sem nome',revision:(payload.expected_revision||0)+1,asset:payload.template.asset_id?server.record.template.asset:null}};server.record=value;server.data=listing([value.template],{chatbot_revision:value.chatbot_revision});return value;}
function createText(h,text='Olá, {name}!'){h.click(act('create'));h.fill(field('text'),text);}
async function edit(h){h.click(act('edit'));await flush();}
async function preview(h){h.click(act('preview'));await flush();}

test('Templates is a subscription-only route with its own permission and scoped reads',async t=>{
 const h=await makeSuite({initialRoute:'templates',permissions,bridge:{tenants:[{id:tenantA,name:'Empresa sintética',role:'owner',workspace_profile:'subscription_services'}]},configureWindow:window=>window.eval(source),respond:path=>path===base?listing():defaultResponse(path)});t.after(()=>h.close());await flush();
 assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'templates');assert.ok(h.document.querySelector('[data-st-empty]'));
 assert.deepEqual([...h.document.querySelectorAll('#osNav [data-os-route]')].map(node=>node.dataset.osRoute),['dashboard','dispositivos','chatbot','testar-chatbot','templates','novidades','ajuda','member']);assert.equal(writes(h).length,0);
 assert.equal(h.calls.some(call=>/\/chatbot-test|\/devices|\/management\/dashboard|plan-catalog/.test(call.path)),false);assert.deepEqual(h.errors,[]);
});

test('standard profiles and subscriptions without templates.manage cannot enter the library',async t=>{
 for(const [profile,permission] of [['standard',true],['subscription_services',false]]){
  const h=await makeSuite({initialRoute:'templates',permissions:planState('elite',{workspace_profile:profile,permissions:{'account.manage':true,'templates.manage':permission}}),bridge:{tenants:[{id:tenantA,name:'Empresa sintética',role:'owner',workspace_profile:profile}]},configureWindow:window=>window.eval(source)});t.after(()=>h.close());await flush();
  assert.equal(h.document.querySelector('#osNav [data-os-route="templates"]'),null);assert.equal(h.calls.some(call=>call.path.includes('/subscription-whatsapp/templates')),false);assert.deepEqual(h.errors,[]);
 }
});

test('empty states, search and capacity reflect server counts without operational requests',async t=>{
 const h=await makeTemplates();t.after(()=>h.close());assert.equal(h.element('[data-st-count]').textContent,'0 / 100 templates');assert.match(h.element('[data-st-empty]').textContent,/Nenhum template ainda/);
 h.fill('[data-st-search]','Sem resultado & teste');await h.tick(300);assert.equal(new URL(h.calls.at(-1).path,'https://orion.test').searchParams.get('q'),'Sem resultado & teste');assert.match(h.element('[data-st-empty]').textContent,/corresponde à sua pesquisa/);assert.equal(h.element('[data-st-search]').value,'Sem resultado & teste');assert.equal(writes(h).length,0);
 h.server.data.counts.total=100;await h.controller.load();assert.equal(h.element(act('create')).disabled,true);assert.equal(h.element('[data-st-count]').textContent,'100 / 100 templates');
 const legacy=Array.from({length:101},(_,index)=>({...detail().template,id:'60000000-0000-4000-8000-'+String(index+1).padStart(12,'0')}));h.server.data=listing(legacy);await h.controller.load();assert.equal(h.document.querySelectorAll('[data-st-template-id]').length,101);assert.equal(h.element(act('create')).disabled,true);assert.equal(h.element('[data-st-count]').textContent,'101 / 100 templates');
});

test('search results from an older request do not replace the current query',async t=>{
 const old=deferred();const h=await makeTemplates({respond(path){if(path===base+'?q=primeiro')return old.promise;}});t.after(()=>h.close());h.fill('[data-st-search]','primeiro');const reading=h.tick(300);await flush();h.fill('[data-st-search]','segundo');await h.tick(300);old.resolve(listing([detail().template]));await reading;await flush();assert.equal(h.element('[data-st-search]').value,'segundo');assert.equal(h.document.querySelector('[data-st-template-id]'),null);assert.equal(writes(h).length,0);
});

test('five forms preserve individual drafts and require a deliberate dirty close',async t=>{
 const h=await makeTemplates();t.after(()=>h.close());createText(h,'Texto preservado');assert.equal(h.document.querySelectorAll('[data-st-tab]').length,5);h.click(tab('media'));h.fill(field('text'),'Legenda preservada');h.fill(field('followup_text'),'Depois do arquivo');h.click(tab('location'));h.fill(field('latitude'),'-23.55');h.click(tab('text'));assert.equal(h.element(field('text')).value,'Texto preservado');h.click(tab('media'));assert.equal(h.element(field('text')).value,'Legenda preservada');assert.equal(h.element(field('followup_text')).value,'Depois do arquivo');
 h.click('[data-st-editor] '+act('close-dialog'));assert.ok(h.element(form('discard')));h.click(form('discard')+' '+act('close-dialog'));assert.ok(h.element('[data-st-editor]'));h.click('[data-st-editor] '+act('close-dialog'));h.submit(form('discard'));assert.equal(h.document.querySelector('[data-st-editor]'),null);assert.equal(writes(h).length,0);assert.deepEqual(h.errors,[]);
});

test('text uses a default name, trims content and sends the exact revisioned definition',async t=>{
 const h=await makeTemplates({respond(path,method,payload,server){if(path===base&&method==='POST')return saved(server,payload);}});t.after(()=>h.close());createText(h,'  Olá, {name}!  ');h.submit();await flush();assert.equal(writes(h).length,1);const payload=writes(h)[0].data;
 assert.equal(payload.expected_chatbot_revision,7);assert.match(payload.request_id,/^[0-9a-f-]{36}$/);assert.deepEqual(payload.template,{name:'',kind:'text',text:'Olá, {name}!',followup_text:'',asset_id:null,location:null,file_name:null});assert.equal(h.document.querySelector('[data-st-editor]'),null);assert.equal(h.element('[data-st-template-id] h3').textContent,'Texto sem nome');assert.match(h.element('[data-st-feedback]').textContent,/criado/);assert.deepEqual(h.errors,[]);
});

test('1000 UTF-16 units are accepted and 1001, blank and malformed text are refused without truncation',async t=>{
 const h=await makeTemplates({respond(path,method,payload,server){if(path===base&&method==='POST')return saved(server,payload);}});t.after(()=>h.close());createText(h);
 for(const invalid of [' ','x'.repeat(1001),'😀'.repeat(501),'a\ud800','a\0']){h.fill(field('text'),invalid);h.submit();await flush();assert.equal(writes(h).length,0);assert.equal(h.element(field('text')).value,invalid);}
 h.fill(field('text'),'😀'.repeat(500));assert.equal(h.element('[data-st-counter="text"]').textContent,'1000 / 1000');h.submit();await flush();assert.equal(writes(h).length,1);assert.equal(writes(h)[0].data.template.text.length,1000);
});

test('tags are an informational modal with five known values and no automatic insertion',async t=>{
 const h=await makeTemplates();t.after(()=>h.close());createText(h,'Texto original');h.click(act('tags'));assert.deepEqual([...h.document.querySelectorAll(form('tags')+' code')].map(node=>node.textContent),['{name}','{phone_number}','{my_name}','{my_email}','{my_contact_number}']);h.click(form('tags')+' '+act('close-dialog'));assert.equal(h.element(field('text')).value,'Texto original');assert.equal(writes(h).length,0);
});

test('geographic coordinates are required and validated before a location can be saved',async t=>{
 const h=await makeTemplates({respond(path,method,payload,server){if(path===base&&method==='POST')return saved(server,payload);}});t.after(()=>h.close());h.click(act('create'));h.click(tab('location'));h.fill(field('name'),'Local sintético');h.submit();await flush();assert.equal(writes(h).length,0);h.fill(field('latitude'),'91');h.fill(field('longitude'),'0');h.submit();await flush();assert.match(h.element('[data-st-dialog-error]').textContent,/latitude/);assert.equal(writes(h).length,0);
 h.fill(field('latitude'),'-23.55');h.fill(field('longitude'),'-46.63');h.submit();await flush();assert.deepEqual(writes(h)[0].data.template.location,{latitude:-23.55,longitude:-46.63});assert.equal(writes(h)[0].data.template.asset_id,null);
});

test('file extension, upload size and runtime capability reject invalid choices before any upload',async t=>{
 const h=await makeTemplates();t.after(()=>h.close());h.click(act('create'));h.click(tab('media'));await h.file('x','programa.exe');await h.file('x','foto.jpg',10000001);assert.equal(writes(h).length,0);h.click(tab('sticker'));await h.file('x','foto.webp');await h.file('x','foto.jpg',200001);assert.equal(writes(h).length,0);h.click(tab('ptv'));await h.file('x','video.mov');assert.equal(writes(h).length,0);
 h.server.data.capabilities.types.ptv=false;h.click('[data-st-editor] '+act('close-dialog'));await h.controller.load();h.click(act('create'));assert.equal(h.element(tab('ptv')).disabled,true);assert.deepEqual(h.errors,[]);
});

test('uploaded media keeps a separate caption and follow-up and saves the normalized asset identity',async t=>{
 const h=await makeTemplates({respond(path,method,payload,server){if(path===base+'/assets'&&method==='POST'){const value=asset(payload.kind);server.record.template.asset=value;return {request_id:payload.request_id,chatbot_revision:7,file_name:'foto.jpg',asset:value};}if(path===base&&method==='POST')return saved(server,payload);}});t.after(()=>h.close());h.click(act('create'));h.click(tab('media'));h.fill(field('name'),'Mídia sintética');h.fill(field('text'),'Legenda própria');h.fill(field('followup_text'),'Mensagem depois');await h.file();await flush();assert.match(h.element('[data-st-file-status]').textContent,/foto.jpg/);h.submit();await flush();assert.equal(writes(h).length,2);assert.equal(writes(h)[0].data.kind,'image');assert.equal(writes(h)[0].data.filename,'imagem.jpg');assert.ok(writes(h)[0].data.content_base64);const value=writes(h)[1].data.template;assert.equal(value.asset_id,assetId);assert.equal(value.text,'Legenda própria');assert.equal(value.followup_text,'Mensagem depois');assert.equal(value.file_name,'foto.jpg');assert.equal(value.kind,'image');assert.deepEqual(h.errors,[]);
});

test('legacy text remains visible without truncation and is limited only when a new edit is saved',async t=>{
 const record=detail();record.template.text='x'.repeat(1500);const h=await makeTemplates({data:listing([record.template]),record});t.after(()=>h.close());await edit(h);assert.equal(h.element(field('text')).value.length,1500);assert.equal(h.element('[data-st-counter="text"]').textContent,'1500 / 1000');h.submit();await flush();assert.equal(writes(h).length,0);assert.equal(h.element(field('text')).value.length,1500);
 const media=mediaTemplate();media.template.followup_text='y'.repeat(2000);const followup=await makeTemplates({data:listing([media.template]),record:media});t.after(()=>followup.close());await edit(followup);assert.equal(followup.element(field('followup_text')).value.length,2000);followup.submit();await flush();assert.equal(writes(followup).length,0);assert.equal(followup.element(field('followup_text')).value.length,2000);
});

test('editing preserves type and a conflict requires confirmation before replacing the local draft',async t=>{
 let attempts=0;const record=detail();const h=await makeTemplates({data:listing([record.template]),record,respond(path,method,payload,server){if(method==='PUT'){if(++attempts===1){server.record.template.revision=2;server.record.template.text='Alteração de outra sessão';server.record.chatbot_revision=8;server.data.chatbot_revision=8;throw failure(409,'A regra dependente exige uma revisão atual.');}return saved(server,payload);}}});t.after(()=>h.close());await edit(h);assert.equal(h.element(tab('media')).disabled,true);h.fill(field('text'),'Meu rascunho');h.submit();await flush();assert.equal(h.element(field('text')).value,'Meu rascunho');assert.equal(h.element(field('text')).disabled,true);assert.equal(h.element('[data-st-save]').disabled,true);assert.equal(writes(h).length,1);h.click(act('reconcile'));await flush();assert.equal(h.element(field('text')).value,'Meu rascunho');assert.match(h.element(form('reload-editor')).textContent,/Alteração de outra sessão/);h.click(form('reload-editor')+' '+act('close-dialog'));assert.equal(h.element(field('text')).value,'Meu rascunho');assert.equal(writes(h).length,1);h.click(act('reconcile'));await flush();h.submit(form('reload-editor'));assert.equal(h.element(field('text')).value,'Alteração de outra sessão');assert.equal(h.element(field('text')).disabled,false);assert.equal(h.element('[data-st-save]').disabled,false);h.fill(field('text'),'Meu rascunho revisado');h.submit();await flush();assert.equal(writes(h)[1].data.expected_revision,2);assert.equal(writes(h)[1].data.expected_chatbot_revision,8);assert.equal(writes(h)[1].data.template.text,'Meu rascunho revisado');assert.deepEqual(h.errors,[]);
});

test('reconciling a new media draft preserves its file and permits a preview at the current revision',async t=>{
 const h=await makeTemplates({respond(path,method,payload,server){if(path===base+'/assets'&&method==='POST')return {request_id:payload.request_id,chatbot_revision:7,file_name:'foto.jpg',asset:asset()};if(path===base&&method==='POST'){server.data.chatbot_revision=8;throw failure(409,'O Chatbot mudou em outra sessão.');}}});t.after(()=>h.close());
 h.click(act('create'));h.click(tab('media'));h.fill(field('name'),'Arquivo preservado');h.fill(field('text'),'Legenda preservada');await h.file();await flush();h.submit();await flush();assert.equal(h.element('[data-st-save]').disabled,true);
 h.click(act('reconcile'));await flush();assert.equal(h.element(field('name')).value,'Arquivo preservado');assert.equal(h.element(field('text')).value,'Legenda preservada');assert.match(h.element('[data-st-file-status]').textContent,/foto.jpg/);assert.equal(h.element('[data-st-save]').disabled,false);
 h.click(act('preview-draft'));await flush();assert.match(h.element('[data-st-media-content] img').src,/^blob:https:\/\/orion.test\//);assert.equal(h.binaryCalls.length,1);const url=new URL(h.binaryCalls[0].path,'https://orion.test');assert.equal(url.pathname,base+'/assets/'+assetId+'/content');assert.equal(url.searchParams.get('expected_chatbot_revision'),'8');assert.equal(writes(h).filter(call=>call.path===base+'/assets').length,1);assert.deepEqual(h.errors,[]);
});

test('referenced templates show their actual dependent rules and cannot be deleted',async t=>{
 const record=detail();record.template.dependent_count=1;record.dependents=[copy(contract.dependent_example)];const h=await makeTemplates({data:listing([record.template]),record});t.after(()=>h.close());h.click(act('delete'));await flush();assert.match(h.element(form('delete')).textContent,/vinculado/);assert.match(h.element('.os-template-dependents').textContent,/Olá/);assert.equal(h.document.querySelector(form('delete')+' button[type=submit]'),null);h.submit(form('delete'));assert.equal(writes(h).length,0);
});

test('delete is an explicit versioned command and cancel sends nothing',async t=>{
 const record=detail();const h=await makeTemplates({data:listing([record.template]),record,respond(path,method,payload,server){if(method==='DELETE'){server.data=listing([],{chatbot_revision:8});return {request_id:payload.request_id,deleted:1,template_id:templateId,chatbot_revision:8};}}});t.after(()=>h.close());h.click(act('delete'));await flush();h.click(form('delete')+' '+act('close-dialog'));assert.equal(writes(h).length,0);h.click(act('delete'));await flush();h.submit(form('delete'));await flush();assert.equal(writes(h).length,1);assert.equal(writes(h)[0].data.expected_revision,1);assert.equal(writes(h)[0].data.expected_chatbot_revision,7);assert.equal(h.document.querySelector('[data-st-template-id]'),null);
});

test('uncertain writes are never auto-repeated and an explicit retry preserves request identity',async t=>{
 let attempts=0;const h=await makeTemplates({respond(path,method,payload,server){if(path===base&&method==='POST'){if(++attempts===1)throw failure(503);return saved(server,payload);}}});t.after(()=>h.close());createText(h);h.submit();await flush();assert.equal(writes(h).length,1);await h.tick(60000);assert.equal(writes(h).length,1);assert.equal(h.element(field('text')).disabled,true);h.click('[data-st-editor] '+act('close-dialog'));assert.ok(h.element('[data-st-editor]'));h.click(act('retry'));await flush();assert.deepEqual(writes(h)[0].data,writes(h)[1].data);assert.equal(h.document.querySelector('[data-st-editor]'),null);assert.deepEqual(h.errors,[]);
});

test('read-only capabilities block every mutation while escaped textual previews remain safe',async t=>{
 const record=detail();record.template.name='<img src=x onerror=alert(1)>';record.template.text='*Olá* <script>alert(1)</script>';const data=listing([record.template]);data.capabilities.manage=false;const h=await makeTemplates({data,record});t.after(()=>h.close());assert.equal(h.element(act('create')).disabled,true);assert.equal(h.element(act('edit')).disabled,true);assert.equal(h.element(act('delete')).disabled,true);assert.equal(h.document.querySelector('h3 img'),null);await preview(h);assert.equal(h.element('.os-template-preview-text strong').textContent,'Olá');assert.equal(h.document.querySelector('.os-template-preview-text script'),null);assert.equal(writes(h).length,0);assert.equal(h.binaryCalls.length,0);
});

test('private image and audio previews verify bytes and use authenticated same-origin Blob URLs',async t=>{
 for(const [kind,data] of [['image',new Uint8Array([255,216,255,217])],['audio',new TextEncoder().encode('ID3synthetic')]]){
  const record=mediaTemplate(kind,asset(kind,data));const h=await makeTemplates({data:listing([record.template]),record,respondBinary:()=>binary(data,record.template.asset.mime)});t.after(()=>h.close());await preview(h);const element=h.element(kind==='audio'?'[data-st-media-content] audio':'[data-st-media-content] img');assert.match(element.src,/^blob:https:\/\/orion.test\//);assert.equal(element.hasAttribute('autoplay'),false);if(kind==='audio')assert.equal(element.autoplay,false);assert.equal(h.binaryCalls.length,1);const options=h.binaryCalls[0].options;assert.equal(options.credentials,'same-origin');assert.equal(options.mode,'same-origin');assert.equal(options.cache,'no-store');assert.equal(options.redirect,'error');assert.equal(options.method,'GET');assert.equal(writes(h).length,0);h.click(form('preview')+' '+act('close-dialog'));assert.equal(h.objectURLs.size,0);assert.equal(h.revokedURLs.length,1);assert.deepEqual(h.errors,[]);
 }
});

test('documents are never embedded and download only after a successful validated binary read',async t=>{
 const data=new TextEncoder().encode('%PDF-synthetic'),metadata=asset('document',data,{mime:'application/pdf',file_name:'orientacoes.pdf'}),record=mediaTemplate('document',metadata);const h=await makeTemplates({data:listing([record.template]),record,respondBinary:()=>binary(data,'application/pdf')});t.after(()=>h.close());await preview(h);assert.equal(h.binaryCalls.length,0);assert.equal(h.document.querySelector('iframe,object,embed'),null);h.click(act('download'));await flush();assert.equal(h.downloads.length,1);assert.equal(h.downloads[0].name,'orientacoes.pdf');assert.equal(h.downloads[0].blob.type,'application/pdf');assert.deepEqual(new Uint8Array(await h.downloads[0].blob.arrayBuffer()),data);
});

test('foreign URLs, wrong tenants, stale revision URLs, HTTP errors and false media are not rendered',async t=>{
 for(const scenario of ['external','tenant','revision','http','mime','signature','length','overflow']){
  const record=mediaTemplate();let response=()=>binary();if(scenario==='external')record.template.asset.content_url='https://example.invalid/file.jpg';if(scenario==='tenant')record.template.asset.content_url=record.template.asset.content_url.replace(tenantA,tenantB);if(scenario==='revision')record.template.asset.content_url=record.template.asset.content_url.replace('revision=7','revision=8');if(scenario==='http')response=()=>new Response('{"detail":"denied"}',{status:403,headers:{'Content-Type':'application/json'}});if(scenario==='mime')response=()=>binary(new Uint8Array([255,216,255,217]),'text/html');if(scenario==='signature')response=()=>binary(new TextEncoder().encode('fake'));if(scenario==='length')response=()=>binary(new Uint8Array([255,216,255,217]),'image/jpeg',{headers:{'Content-Length':'999'}});if(scenario==='overflow')response=()=>new Response(new Uint8Array([255,216,255,217,1]),{headers:{'Content-Type':'image/jpeg'}});
  const h=await makeTemplates({data:listing([record.template]),record,respondBinary:response});t.after(()=>h.close());await preview(h);assert.equal(h.document.querySelector('[data-st-media-content] img'),null,scenario);assert.equal(h.objectURLs.size,0,scenario);assert.equal(h.downloads.length,0,scenario);assert.equal(h.binaryCalls.length,['external','tenant','revision'].includes(scenario)?0:1,scenario);assert.equal(writes(h).length,0);
 }
});

test('late media and mutations cannot revive a closed preview or a previous business context',async t=>{
 const delayed=deferred(),record=mediaTemplate();const h=await makeTemplates({data:listing([record.template]),record,respondBinary:()=>delayed.promise});t.after(()=>h.close());await preview(h);const signal=h.binaryCalls[0].options.signal;h.click(form('preview')+' '+act('close-dialog'));assert.equal(signal.aborted,true);delayed.resolve(binary());await flush();assert.equal(h.objectURLs.size,0);assert.equal(h.document.querySelector('[data-st-preview]'),null);
 const saving=deferred();const other=await makeTemplates({respond(path,method){if(method==='POST')return saving.promise;}});t.after(()=>other.close());createText(other);other.submit();await flush();const payload=writes(other)[0].data;other.controller.unmount();saving.resolve({...detail(),request_id:payload.request_id});await flush();assert.equal(other.document.querySelector('dialog'),null);assert.equal(other.element('#templates').children.length,0);assert.equal(other.objectURLs.size,0);assert.deepEqual(other.errors,[]);
});

test('revocation during a write preserves the draft but disables further edits and saves',async t=>{
 const h=await makeTemplates({respond(path,method){if(method==='POST')throw failure(403);}});t.after(()=>h.close());createText(h);h.submit();await flush();assert.equal(writes(h).length,1);assert.equal(h.element(field('text')).value,'Olá, {name}!');assert.equal(h.element(field('text')).disabled,true);assert.equal(h.element('[data-st-save]').disabled,true);h.submit();await flush();assert.equal(writes(h).length,1);assert.match(h.element('[data-st-dialog-error]').textContent,/acesso/);assert.deepEqual(h.errors,[]);
});
