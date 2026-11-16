import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {makeSuite,planState,defaultResponse} from './harness.mjs';
import {makeChatbotTest,base,deviceId,ruleId,rowId,bootstrap,response,message,media,binary,copy,contract,source,failure,flush,deferred,tenantA,tenantB} from './subscription-chatbot-test-harness.mjs';

const action=name=>'[data-sct-action="'+name+'"]';
const posts=h=>h.calls.filter(call=>call.method==='POST');
const bodies=h=>[...h.document.querySelectorAll('[data-sct-message]>p')].map(node=>node.textContent);
const permissions=planState('none',{workspace_profile:'subscription_services',features:['account.manage','business.setup','devices.manage','chatbot.manage'],permissions:{'account.manage':true,'business.setup':true,'devices.manage':true,'chatbot.manage':true}});
const device2='20000000-0000-4000-8000-000000000002';
const devices=()=>[{...copy(contract.bootstrap_response.devices[0]),phone_e164:'+5511999999901'},{...copy(contract.bootstrap_response.devices[0]),id:device2,name:'Outro dispositivo sintético'}];

test('the simulator route is subscription-only and requires its operational feature before any request',async t=>{
 const h=await makeSuite({initialRoute:'testar-chatbot',permissions,bridge:{tenants:[{id:tenantA,name:'Empresa sintética',role:'owner',workspace_profile:'subscription_services'}]},configureWindow:window=>window.eval(source),respond:path=>path===base?bootstrap():defaultResponse(path)});t.after(()=>h.close());await flush();
 assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'testar-chatbot');assert.ok(h.document.querySelector('[data-sct-device]'));
 assert.deepEqual([...h.document.querySelectorAll('#osNav [data-os-route]')].map(node=>node.dataset.osRoute),['dashboard','dispositivos','chatbot','testar-chatbot','novidades','ajuda','member']);
 assert.deepEqual(h.calls.filter(call=>call.path.includes('/subscription-whatsapp/')).map(call=>[call.method,call.path]),[['GET',base]]);
 for(const [profile,allowed] of [['standard',true],['subscription_services',false]]){
  const p={...copy(permissions),workspace_profile:profile,permissions:{...permissions.permissions,'chatbot.manage':allowed}};
  const ordinary=await makeSuite({initialRoute:'testar-chatbot',permissions:p,bridge:{tenants:[{id:tenantA,name:'Outra empresa',role:'owner',workspace_profile:profile}]},configureWindow:window=>window.eval(source)});t.after(()=>ordinary.close());await flush();
  assert.equal(ordinary.document.querySelector('#osNav [data-os-route="testar-chatbot"]'),null);assert.equal(ordinary.calls.some(call=>call.path.includes('/subscription-whatsapp/chatbot-test')),false);assert.deepEqual(ordinary.errors,[]);
 }
 assert.deepEqual(h.errors,[]);
});

test('opening only reads the catalog; selection is required and stores only device_id in the URL',async t=>{
 const h=await makeChatbotTest({data:bootstrap({devices:devices()})});t.after(()=>h.close());
 assert.deepEqual(h.calls.map(call=>[call.method,call.path]),[['GET',base]]);assert.equal(h.element('[data-sct-input]').tagName,'INPUT');assert.equal(h.element('[data-sct-input]').maxLength,400);assert.equal(h.element('[data-sct-input]').disabled,true);assert.equal(h.document.querySelector('textarea'),null);
 h.fill('Antes da seleção');h.key('Enter');assert.equal(posts(h).length,0);assert.equal(h.controller.getState().messages,0);
 h.select(deviceId);assert.equal(h.element('[data-sct-input]').disabled,false);assert.match(h.element('[data-sct-heading]').textContent,/Atendimento de exemplo.*\+5511999999901/);assert.equal(new URL(h.window.location.href).searchParams.get('device_id'),deviceId);
 assert.equal(h.window.localStorage.length,0);assert.equal(h.window.sessionStorage.length,0);assert.equal(posts(h).length,0);assert.deepEqual(h.errors,[]);
});

test('Enter trims the outgoing text, uses inactive replies, and counts every no-match bubble',async t=>{
 const h=await makeChatbotTest({respond(path,method,payload){if(method==='POST'&&payload.input.text==='sem resposta')return response(payload,{outcome:'no_match',matched_rule:null,messages:[message('text',{text:contract.no_match_response.messages[0].text,delay_seconds:0})]});}});t.after(()=>h.close());h.select(deviceId);
 h.fill('  /START  ');h.key('Enter');await flush();assert.deepEqual(bodies(h),['/START','Olá! Como posso ajudar?']);assert.equal(h.element('[data-sct-count]').textContent,'2 mensagens');assert.match(h.element('[data-direction="in"] .os-chatbot-test-message-meta').textContent,/Resposta inativa/);assert.equal(h.document.querySelectorAll('[data-sct-message] time').length,2);
 assert.equal(posts(h)[0].data.input.text,'/START');assert.deepEqual(Object.keys(posts(h)[0].data).sort(),['context_id','device_id','expected_chatbot_revision','input','request_id']);assert.ok(posts(h)[0].data.context_id);assert.equal(posts(h)[0].path,base+'/simulate');
 await h.send('sem resposta');assert.equal(h.element('[data-sct-count]').textContent,'4 mensagens');assert.equal(bodies(h).at(-1),'Nenhuma resposta correspondente ao seu texto para este dispositivo');assert.equal(h.element('[data-sct-character-count]').textContent,'0/400');assert.deepEqual(h.errors,[]);
});

test('blank input, 401 UTF-16 units, malformed Unicode and IME composition cannot send',async t=>{
 const h=await makeChatbotTest();t.after(()=>h.close());h.select(deviceId);
 for(const value of ['   ','a'.repeat(401),'😀'.repeat(200)+'a','\ud800','\u0000','a\tb']){h.fill(value);h.submit();await flush();assert.equal(posts(h).length,0);assert.equal(h.controller.getState().messages,0);assert.ok(h.element('[data-sct-input-error]').textContent);}
 h.fill('😀'.repeat(200));assert.equal(h.element('[data-sct-character-count]').textContent,'400/400');h.key('Enter',{isComposing:true});assert.equal(posts(h).length,0);h.key('Enter');await flush();assert.equal(posts(h).length,1);assert.equal(posts(h)[0].data.input.text.length,400);
 await h.send('a'.repeat(400));assert.equal(posts(h).length,2);assert.deepEqual(h.errors,[]);
});

test('keyword chips use Portuguese match labels and regular matching, while every bubble escapes HTML',async t=>{
 const h=await makeChatbotTest({respond(path,method,payload){if(method==='POST')return response(payload,{messages:[message('text',{text:'*literal* <img src=x onerror=alert(1)>'})],keywords:[{rule_id:ruleId,keyword:'<script>teste</script>',match_mode:'equal',enabled:false},{rule_id:randomUUID(),keyword:'menu',match_mode:'like',enabled:true},{rule_id:randomUUID(),keyword:'um, dois',match_mode:'array',enabled:true}]});}});t.after(()=>h.close());h.select(deviceId);
 assert.equal(h.element('[data-sct-keywords]').hidden,true);await h.send('<img src=unsafe>');assert.equal(h.document.querySelector('[data-sct-messages] img'),null);assert.equal(h.document.querySelector('[data-sct-messages] strong'),null);assert.match(bodies(h).at(-1),/^\*literal\*/);
 assert.deepEqual([...h.document.querySelectorAll('.os-chatbot-test-keyword small')].map(node=>node.textContent),['Exata','Contém','Lista']);assert.equal(h.document.querySelector('[data-sct-keywords] script'),null);
 h.click('[data-sct-keyword="0"]');await flush();assert.deepEqual(posts(h)[1].data.input,{type:'text',text:'<script>teste</script>'});assert.equal(h.controller.getState().messages,4);assert.equal(h.binaryCalls.length,0);assert.deepEqual(h.errors,[]);
});

test('clear is immediate, preserves selection and invalidates a late response without restoring history',async t=>{
 const late=deferred();let payload;
 const h=await makeChatbotTest({respond(path,method,value){if(method==='POST'){payload=copy(value);return late.promise;}}});t.after(()=>h.close());h.select(deviceId);const before=h.controller.getState().contextId;
 h.fill('Aguardando');h.submit();assert.equal(h.controller.getState().pending,true);assert.equal(h.controller.getState().messages,1);h.click(action('clear'));
 assert.equal(h.controller.getState().pending,false);assert.equal(h.controller.getState().messages,0);assert.equal(h.element('[data-sct-device]').value,deviceId);assert.notEqual(h.controller.getState().contextId,before);assert.equal(h.element('[data-sct-keywords]').hidden,true);
 late.resolve(response(payload));await flush();assert.equal(h.controller.getState().messages,0);assert.equal(h.element('[data-sct-feedback]').textContent,'');assert.equal(new URL(h.window.location.href).searchParams.get('device_id'),deviceId);assert.deepEqual(h.errors,[]);
});

test('changing or removing the device preserves bubbles and discards pending work from the earlier context',async t=>{
 const late=deferred();let payload;
 const h=await makeChatbotTest({data:bootstrap({devices:devices()}),respond(path,method,value){if(method==='POST'&&value.input.text==='pendente'){payload=copy(value);return late.promise;}}});t.after(()=>h.close());h.select(deviceId);await h.send('primeira');const context=h.controller.getState().contextId;
 h.fill('pendente');h.submit();h.select('');assert.equal(h.controller.getState().messages,3);assert.equal(h.element('[data-sct-input]').disabled,true);assert.equal(new URL(h.window.location.href).searchParams.has('device_id'),false);assert.notEqual(h.controller.getState().contextId,context);
 h.select(device2);await h.send('nova conversa');assert.equal(h.controller.getState().messages,5);late.resolve(response(payload));await flush();assert.equal(h.controller.getState().messages,5);assert.equal(posts(h).at(-1).data.device_id,device2);assert.notEqual(posts(h)[0].data.context_id,posts(h).at(-1).data.context_id);assert.deepEqual(h.errors,[]);
});

test('fullscreen uses the Orion CSS surface, traps focus and restores it with Escape without losing conversation',async t=>{
 const h=await makeChatbotTest();t.after(()=>h.close());h.select(deviceId);await h.send('teste');h.fill('Rascunho preservado');let native=0;h.element('#tester').requestFullscreen=()=>native++;
 h.click(action('fullscreen'));assert.equal(h.element('#tester').classList.contains('os-is-fullscreen'),true);assert.equal(h.element(action('fullscreen')).getAttribute('aria-pressed'),'true');assert.equal(h.element('#outside').inert,true);assert.equal(h.document.body.style.overflow,'hidden');assert.equal(native,0);
 h.key('Escape');assert.equal(h.element('#tester').classList.contains('os-is-fullscreen'),false);assert.equal(h.element(action('fullscreen')).textContent,'Entrar em tela cheia');assert.equal(h.element('[data-sct-input]').value,'Rascunho preservado');assert.equal(h.controller.getState().messages,2);assert.equal(h.document.body.style.overflow,'');assert.equal(h.document.activeElement,h.element(action('fullscreen')));assert.notEqual(h.element('#outside').inert,true);
 h.click(action('fullscreen'));h.window.OrionSubscriptionChatbotTest.unmount();assert.equal(h.document.body.style.overflow,'');assert.notEqual(h.element('#outside').inert,true);assert.deepEqual(h.errors,[]);
});

test('a rule deep link runs once, removes reply_id and uses ordinary matching for subsequent messages and reload',async t=>{
 const url='https://orion.test/painel?aba=testar-chatbot&device_id='+deviceId+'&reply_id='+ruleId;
 const h=await makeChatbotTest({url});t.after(()=>h.close());assert.equal(posts(h).length,1);assert.deepEqual(posts(h)[0].data.input,{type:'rule',rule_id:ruleId});assert.equal(h.controller.getState().messages,2);assert.equal(new URL(h.window.location.href).searchParams.has('reply_id'),false);
 await h.send('Próxima');assert.equal(posts(h).length,2);assert.deepEqual(posts(h)[1].data.input,{type:'text',text:'Próxima'});
 const controller=h.window.OrionSubscriptionChatbotTest.mount({...h.bridge,initialDeviceId:new URL(h.window.location.href).searchParams.get('device_id'),initialRuleId:null});await flush();assert.equal(controller.getState().messages,0);assert.equal(controller.getState().deviceId,deviceId);assert.equal(posts(h).length,2);assert.deepEqual(h.errors,[]);
});

test('a global rule waits for a real device choice when there is more than one, and invalid IDs never simulate',async t=>{
 const h=await makeChatbotTest({initialRuleId:ruleId,data:bootstrap({devices:devices()})});t.after(()=>h.close());assert.equal(posts(h).length,0);assert.equal(h.controller.getState().deviceId,'');h.select(device2);await flush();assert.equal(posts(h).length,1);assert.equal(posts(h)[0].data.device_id,device2);assert.equal(posts(h)[0].data.input.type,'rule');h.select(deviceId);await flush();assert.equal(posts(h).length,1);
 const bad=await makeChatbotTest({url:'https://orion.test/painel?aba=testar-chatbot&device_id=invalid&reply_id=invalid'});t.after(()=>bad.close());assert.equal(posts(bad).length,0);assert.equal(new URL(bad.window.location.href).searchParams.has('device_id'),false);assert.equal(new URL(bad.window.location.href).searchParams.has('reply_id'),false);assert.equal(bad.element('[data-sct-input]').disabled,true);assert.deepEqual(bad.errors,[]);
});

test('list choices send only their signed test token and row identity, and expire with the local context',async t=>{
 const menu=message('list'),h=await makeChatbotTest({data:bootstrap({devices:devices()}),respond(path,method,payload){if(method==='POST')return response(payload,{messages:[payload.input.type==='selection'?message('text',{text:'Opção selecionada'}):menu]});}});t.after(()=>h.close());h.select(deviceId);await h.send('menu');
 assert.equal(h.document.body.innerHTML.includes(menu.selection_token),false);h.click('[data-type="list"] summary');assert.equal(h.element('[data-sct-row]').disabled,false);h.click('[data-sct-row]');await flush();
 assert.deepEqual(posts(h)[1].data.input,{type:'selection',selection_token:menu.selection_token,row_id:rowId});assert.equal(posts(h)[1].data.context_id,posts(h)[0].data.context_id);assert.equal(h.controller.getState().messages,4);assert.equal(bodies(h).at(-1),'Opção selecionada');
 h.select(device2);assert.equal(h.element('[data-sct-row]').disabled,true);h.click('[data-sct-row]');await flush();assert.equal(posts(h).length,2);h.select(deviceId);assert.equal(h.element('[data-sct-row]').disabled,true);
 await h.send('menu');const rows=[...h.document.querySelectorAll('[data-sct-row]')];assert.equal(rows.at(-1).disabled,false);await h.tick(1800010);assert.equal(rows.at(-1).disabled,true);assert.match(h.document.querySelectorAll('[data-sct-menu-notice]')[1].textContent,/expirou/);assert.deepEqual(h.errors,[]);
});

test('a stale revision refreshes the catalog without replaying the message and keeps the draft for review',async t=>{
 let attempts=0;const h=await makeChatbotTest({respond(path,method,payload,server){if(method==='POST'&&++attempts===1){server.data.chatbot_revision=8;throw failure(409);}if(method==='POST')return response(payload);}});t.after(()=>h.close());h.select(deviceId);const context=h.controller.getState().contextId;await h.send('revisar');
 assert.equal(posts(h).length,1);assert.equal(h.calls.filter(call=>call.method==='GET').length,2);assert.equal(h.element('[data-sct-input]').value,'revisar');assert.notEqual(h.controller.getState().contextId,context);assert.match(h.element('[data-sct-feedback]').textContent,/mudaram/);assert.equal(h.controller.getState().messages,1);
 h.submit();await flush();assert.equal(posts(h).length,2);assert.equal(posts(h)[1].data.expected_chatbot_revision,8);assert.equal(h.controller.getState().messages,3);assert.deepEqual(h.errors,[]);
});

test('server revocation disables every send and a changed tenant cannot receive a late old response',async t=>{
 const forbidden=await makeChatbotTest({respond(path,method){if(method==='POST')throw failure(403);}});t.after(()=>forbidden.close());forbidden.select(deviceId);await forbidden.send('teste');assert.equal(forbidden.element('[data-sct-input]').disabled,true);forbidden.fill('não enviar');forbidden.submit();await flush();assert.equal(posts(forbidden).length,1);
 const late=deferred();let payload;const h=await makeChatbotTest({respond(path,method,value){if(method==='POST'){payload=copy(value);return late.promise;}}});t.after(()=>h.close());h.select(deviceId);h.fill('empresa anterior');h.submit();
 const replacement=h.window.OrionSubscriptionChatbotTest.mount({...h.bridge,tenantId:tenantB,initialDeviceId:null,initialRuleId:null,request:async path=>{assert.equal(path,base.replace(tenantA,tenantB));return bootstrap({devices:[]});}});await flush();late.resolve(response(payload,{messages:[message('text',{text:'DADO DA EMPRESA ANTERIOR'})]}));await flush();assert.equal(replacement.getState().messages,0);assert.equal(h.document.body.textContent.includes('DADO DA EMPRESA ANTERIOR'),false);assert.deepEqual(h.errors,[]);
});

test('an obsolete bootstrap cannot replace a newly mounted tenant catalog',async t=>{
 const late=deferred();const h=await makeChatbotTest({respond:()=>late.promise});t.after(()=>h.close());
 const next=h.window.OrionSubscriptionChatbotTest.mount({...h.bridge,tenantId:tenantB,initialDeviceId:null,initialRuleId:null,request:async()=>bootstrap({devices:[{...devices()[1],name:'Empresa atual'}]})});await flush();late.resolve(bootstrap());await flush();
 assert.equal(next.getState().loaded,true);assert.deepEqual([...h.element('[data-sct-device]').options].map(option=>option.textContent),['Escolha um dispositivo...','Empresa atual']);assert.equal(posts(h).length,0);assert.deepEqual(h.errors,[]);
});

test('URLs and carousels are declared safely, preserve the sequence, and never execute external requests',async t=>{
 const longTemplate='campo '.repeat(5000),messages=[message('text',{text:'Primeira'}),message('external_request',{custom_template:longTemplate,url:'https://example.invalid/private?token=synthetic'}),message('carousel',{body:'<svg onload=alert(1)>',cards:[{title:'<img src=x>',body:'Descrição',button_text:'Saiba mais',image_status:'not_requested'}]}),message('unavailable')];
 const h=await makeChatbotTest({respond(path,method,payload){if(method==='POST')return response(payload,{messages});}});t.after(()=>h.close());h.select(deviceId);await h.send('sequência');
 assert.deepEqual([...h.document.querySelectorAll('[data-direction="in"]')].map(node=>node.dataset.type),['text','external_request','carousel','unavailable']);assert.equal(h.controller.getState().messages,5);assert.match(h.element('[data-type="external_request"]').textContent,/retorno do servidor não foi consultado/);assert.ok(h.element('[data-type="external_request"]').textContent.includes(longTemplate));
 assert.equal(h.document.querySelector('[data-sct-messages] a'),null);assert.equal(h.document.querySelector('[data-sct-messages] img'),null);assert.equal(h.document.querySelector('[data-sct-messages] svg'),null);assert.equal(h.document.querySelector('.os-chatbot-test-carousel button'),null);assert.equal(h.binaryCalls.length,0);assert.equal(posts(h).length,1);assert.deepEqual(h.errors,[]);
});

test('normalized private images are fetched with scoped authentication, kept across device changes and revoked on clear',async t=>{
 const asset=media();const h=await makeChatbotTest({data:bootstrap({devices:devices()}),respond(path,method,payload){if(method==='POST')return response(payload,{messages:[asset.value]});},respondBinary:()=>binary(asset.bytes)});t.after(()=>h.close());h.select(deviceId);await h.send('imagem');
 assert.equal(h.binaryCalls.length,1);const {options}=h.binaryCalls[0];assert.equal(options.method,'GET');assert.equal(options.credentials,'same-origin');assert.equal(options.mode,'same-origin');assert.equal(options.redirect,'error');assert.equal(options.cache,'no-store');assert.ok(options.signal);
 const image=h.element('[data-sct-media-content] img');assert.match(image.src,/^blob:https:\/\/orion\.test\//);const blob=h.objectURLs.get(image.src);assert.equal(blob.type,'image/jpeg');assert.deepEqual(new Uint8Array(await blob.arrayBuffer()),asset.bytes);assert.equal(h.objectURLs.size,1);
 h.select(device2);assert.equal(h.controller.getState().messages,2);assert.equal(h.objectURLs.size,1);assert.equal(h.element('[data-sct-media-content] img').src,image.src);h.click(action('clear'));assert.equal(h.objectURLs.size,0);assert.equal(h.revokedURLs.length,1);assert.deepEqual(h.errors,[]);
});

test('private video previews use local blobs and posters while text documents remain explicit downloads',async t=>{
 const videoBytes=Uint8Array.from([0,0,0,12,102,116,121,112,105,115,111,109]),thumb=Uint8Array.from([255,216,255,217]);
 const video=media('ptv',videoBytes,{mime:'video/mp4',file_name:'video.mp4',thumbnail_url:contract.message_examples.media.asset.thumbnail_url,thumbnail_mime:'image/jpeg',thumbnail_size:thumb.length});
 const textBytes=new TextEncoder().encode('<script>texto literal</script>'),documentMedia=media('document',textBytes,{mime:'text/plain; charset=utf-8',file_name:'../../arquivo.txt'});
 const h=await makeChatbotTest({respond(path,method,payload){if(method==='POST')return response(payload,{messages:[payload.input.text==='vídeo'?video.value:documentMedia.value]});},respondBinary(path){return path.includes('variant=thumbnail')?binary(thumb):posts(h).at(-1).data.input.text==='vídeo'?binary(videoBytes,'video/mp4'):binary(textBytes,'text/plain; charset=utf-8');}});t.after(()=>h.close());h.select(deviceId);await h.send('vídeo');
 const element=h.element('video');assert.equal(element.controls,true);assert.equal(element.autoplay,false);assert.equal(element.preload,'metadata');assert.match(element.src,/^blob:/);assert.match(element.poster,/^blob:/);assert.equal(element.classList.contains('os-chatbot-test-ptv'),true);
 await h.send('arquivo');const link=h.element('[data-type="media"] a');assert.equal(link.download,'arquivo.txt');assert.match(link.href,/^blob:/);assert.equal(h.document.querySelector('[data-sct-messages] script'),null);assert.equal(h.binaryCalls.length,3);assert.deepEqual(h.errors,[]);
});

test('external, cross-tenant, duplicate-query and unscoped media URLs are refused before fetch',async t=>{
 const path=contract.message_examples.media.asset.content_url;
 for(const url of ['https://example.invalid/image.jpg',path.replace(tenantA,tenantB),path+'&ticket=another',path.split('?')[0],path+'#fragment','//orion.test'+path]){
  const asset=media('image',undefined,{content_url:url}),h=await makeChatbotTest({respond(path,method,payload){if(method==='POST')return response(payload,{messages:[asset.value]});}});t.after(()=>h.close());h.select(deviceId);await h.send('imagem');assert.equal(h.binaryCalls.length,0,url);assert.equal(h.objectURLs.size,0);assert.equal(h.document.querySelector('[data-sct-media-content] img'),null);assert.match(h.element('[data-sct-media-content]').textContent,/Não foi possível carregar/);assert.deepEqual(h.errors,[]);
 }
});

test('HTTP errors, wrong MIME, dishonest lengths and overflowing binary streams never become media blobs',async t=>{
 const asset=media();let cancelled=0;
 const responses=[()=>new Response('{"detail":"synthetic"}',{status:403,headers:{'Content-Type':'application/json'}}),()=>binary(asset.bytes,'application/json'),()=>binary(asset.bytes,'image/jpeg',{headers:{'Content-Length':'5000001'}}),()=>binary(Uint8Array.from([1,2,3,4])),()=>new Response(new ReadableStream({start(controller){controller.enqueue(new Uint8Array(5));},cancel(){cancelled++;}}),{headers:{'Content-Type':'image/jpeg'}})];
 for(const next of responses){const h=await makeChatbotTest({respond(path,method,payload){if(method==='POST')return response(payload,{messages:[asset.value]});},respondBinary:next});t.after(()=>h.close());h.select(deviceId);await h.send('imagem');assert.equal(h.objectURLs.size,0);assert.equal(h.document.querySelector('[data-sct-media-content] img'),null);assert.match(h.element('[data-sct-media-content]').textContent,/Não foi possível carregar/);assert.deepEqual(h.errors,[]);}
 assert.equal(cancelled,1);
});

test('clearing an in-flight private media read aborts it and cannot create a late blob',async t=>{
 const late=deferred(),asset=media();const h=await makeChatbotTest({respond(path,method,payload){if(method==='POST')return response(payload,{messages:[asset.value]});},respondBinary:()=>late.promise});t.after(()=>h.close());h.select(deviceId);await h.send('imagem');assert.equal(h.binaryCalls.length,1);h.click(action('clear'));assert.equal(h.binaryCalls[0].options.signal.aborted,true);late.resolve(binary(asset.bytes));await flush();assert.equal(h.controller.getState().messages,0);assert.equal(h.objectURLs.size,0);assert.equal(h.document.querySelector('[data-sct-media-content]'),null);assert.deepEqual(h.errors,[]);
});

test('foreign device/context/request/revision echoes and oversized output sets are rejected without rendering their content',async t=>{
 const changes=[{request_id:randomUUID()},{context_id:randomUUID()},{chatbot_revision:999},{device:{...contract.simulate_response.device,id:device2}},{messages:Array.from({length:31},()=>message('text',{text:'OUTRA RESPOSTA'}))}];
 for(const change of changes){const h=await makeChatbotTest({respond(path,method,payload){if(method==='POST')return response(payload,{messages:[message('text',{text:'OUTRA RESPOSTA'})],...change});}});t.after(()=>h.close());h.select(deviceId);await h.send('entrada');assert.equal(h.controller.getState().messages,1);assert.equal(h.document.body.textContent.includes('OUTRA RESPOSTA'),false);assert.match(h.element('[data-sct-feedback]').textContent,/Não foi possível concluir/);assert.equal(h.binaryCalls.length,0);assert.deepEqual(h.errors,[]);}
});

test('a forced legacy keyword may echo 255 codepoints without bypassing the typed-message or keyword-chip limit',async t=>{
 const keyword='😀'.repeat(255);const h=await makeChatbotTest({initialRuleId:ruleId,respond(path,method,payload){if(method==='POST')return response(payload,{input:{type:'rule',text:keyword,rule_id:ruleId,row_id:null},keywords:[{rule_id:ruleId,keyword,match_mode:'equal',enabled:false}]});}});t.after(()=>h.close());assert.equal(posts(h).length,1);assert.equal(h.controller.getState().messages,2);assert.equal(bodies(h)[0].length,510);h.click('[data-sct-keyword="0"]');await flush();assert.equal(posts(h).length,1);assert.match(h.element('[data-sct-feedback]').textContent,/400 caracteres/);assert.equal(h.element('[data-sct-input]').maxLength,400);assert.deepEqual(h.errors,[]);
});

test('an empty or unavailable catalog never enables the composer and a read retry does not execute a rule',async t=>{
 const empty=await makeChatbotTest({data:bootstrap({devices:[]})});t.after(()=>empty.close());assert.equal(empty.element('[data-sct-input]').disabled,true);assert.match(empty.element('[data-sct-feedback]').textContent,/Nenhum dispositivo/);assert.equal(posts(empty).length,0);
 let unavailable=true;const h=await makeChatbotTest({respond(path,method){if(method==='GET'&&unavailable)throw failure(503);}});t.after(()=>h.close());assert.equal(h.element('[data-sct-input]').disabled,true);unavailable=false;h.click(action('reload'));await flush();assert.equal(h.controller.getState().loaded,true);assert.equal(h.element('[data-sct-input]').disabled,true);assert.equal(posts(h).length,0);assert.deepEqual(h.errors,[]);
});

test('new template audio uses a private player without autoplay and location never loads a map',async t=>{
 const bytes=Uint8Array.from([0xff,0xfb,0x90,0x64]),audio=media('image',bytes,{kind:'audio',mime:'audio/mpeg',file_name:'audio.mp3'});audio.value.kind='audio';audio.value.caption='';
 const location={id:randomUUID(),delay_seconds:0,type:'location',latitude:-22.9,longitude:-43.2};
 const h=await makeChatbotTest({respond(path,method,payload){if(method==='POST')return response(payload,{messages:[audio.value,message('text',{text:'Depois do áudio'}),location]});},respondBinary:()=>binary(bytes,'audio/mpeg')});t.after(()=>h.close());h.select(deviceId);await h.send('template');
 const player=h.element('audio');assert.equal(player.controls,true);assert.equal(player.autoplay,false);assert.equal(player.preload,'metadata');assert.match(player.src,/^blob:/);
 assert.equal(h.objectURLs.get(player.src).type,'audio/mpeg');assert.equal(h.binaryCalls.length,1);assert.match(h.element('[data-type="location"]').textContent,/Latitude: -22.9.*Longitude: -43.2/);
 assert.equal(h.document.querySelector('[data-type="location"] :is(iframe,img,a)'),null);assert.equal(h.controller.getState().messages,4);h.click(action('clear'));assert.equal(h.objectURLs.size,0);assert.deepEqual(h.errors,[]);
});

for(const [mime,name,bytes]of [['application/pdf','arquivo.pdf',new TextEncoder().encode('%PDF-1.4 synthetic')],
 ['application/vnd.openxmlformats-officedocument.wordprocessingml.document','arquivo.docx',Uint8Array.from([0x50,0x4b,3,4])],
 ['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','arquivo.xlsx',Uint8Array.from([0x50,0x4b,3,4])],
 ['application/vnd.android.package-archive','arquivo.apk',Uint8Array.from([0x50,0x4b,3,4])],
 ['text/plain; charset=utf-8','tabela.csv',new TextEncoder().encode('nome,valor\nTeste,2\n')]]){
 test(`private ${name} is an explicit download with its exact name, never embedded or executed`,async t=>{
  const file=media('document',bytes,{mime,file_name:name}),h=await makeChatbotTest({respond(path,method,payload){if(method==='POST')return response(payload,{messages:[file.value]});},respondBinary:()=>binary(bytes,mime)});t.after(()=>h.close());h.select(deviceId);await h.send('arquivo');
  const link=h.element('[data-sct-media-content] a');assert.equal(link.download,name);assert.match(link.href,/^blob:/);assert.equal(h.objectURLs.get(link.href).type,mime.split(';')[0]);assert.equal(h.document.querySelector('[data-sct-media-content] :is(iframe,object,embed,script)'),null);assert.equal(h.binaryCalls.length,1);assert.deepEqual(h.errors,[]);
 });
}

test('impossible location and forged audio MIME cannot create an incoming message or media request',async t=>{
 const fake=media('image');fake.value.kind='audio';fake.value.asset.kind='audio';fake.value.asset.mime='text/html';
 for(const value of [{id:randomUUID(),delay_seconds:0,type:'location',latitude:91,longitude:0},fake.value]){
  const h=await makeChatbotTest({respond(path,method,payload){if(method==='POST')return response(payload,{messages:[value]});}});t.after(()=>h.close());h.select(deviceId);await h.send('inválido');assert.equal(h.controller.getState().messages,1);assert.equal(h.binaryCalls.length,0);assert.equal(h.objectURLs.size,0);assert.deepEqual(h.errors,[]);
 }
});
