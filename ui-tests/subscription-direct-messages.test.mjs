import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash,webcrypto} from 'node:crypto';
import {JSDOM} from 'jsdom';
import {makeDirect,syntheticServer,contract,source,base,templateBase,sendId,uid,copy,flush,deferred,failure,writes,recovery,tenantA,tenantB,fillText,review,confirm,kinds} from './subscription-direct-messages-harness.mjs';

const action=name=>'[data-dm-action="'+name+'"]';
const field=name=>'[data-dm-field="'+name+'"]';
const dialog=h=>h.element('[data-dm-dialog]');
const saved=h=>h.window.history.state?.orionDirectRecovery;
async function setup(t,options){const h=await makeDirect(options);t.after(()=>{assert.deepEqual(h.external,[]);assert.deepEqual(h.server.effects,[]);assert.deepEqual(h.server.unexpected,[]);assert.deepEqual(h.errors,[]);h.close();});return h;}
async function media(h,name='orientacoes.txt',caption='Legenda'){h.click('[data-dm-tab="media"]');h.change(field('device_id'),h.server.options.devices[0].id);h.fill(field('phone'),'5567999999901');await h.upload(name);h.fill(field('caption'),caption);}
async function cancel(h){h.click(action('cancel'));await flush();assert.equal(dialog(h).dataset.dmView,'cancel');h.click(action('confirm-cancel'));await flush();}
function select(h,start,end){const node=h.element('#osDmText');node.focus();node.setSelectionRange(start,end);node.dispatchEvent(new h.window.Event('select',{bubbles:true}));}

test('three native tabs load options without writes, history duplication or sensitive URL fields',async t=>{
  const h=await setup(t);assert.equal(h.controller.getState().kind,'text');assert.equal(h.document.querySelectorAll('[role=tab]').length,3);
  assert.deepEqual([...h.document.querySelectorAll('[role=tab]')].map(n=>n.textContent),['Texto','Mídia','Template']);
  assert.equal(h.element(field('text')).maxLength,1000);assert.equal(h.element(field('phone')).type,'tel');assert.match(h.document.body.textContent,/Tags não são substituídas/);
  assert.equal(h.element(field('device_id')).multiple,false);assert.equal(h.document.querySelector('[data-dm-history]'),null);assert.equal(h.document.querySelector('table'),null);
  assert.equal(writes(h).length,0);assert.equal(h.calls.length,1);assert.equal(h.calls[0].path,base+'/options');assert.equal(h.window.location.search,'?aba=enviar-mensagem');
});

test('text payload preserves literal whitespace and brace expressions through review',async t=>{
  const h=await setup(t),text='  Olá, {name}. {"literal":true}\nFim.  ';await review(h,{text,phone:'+55 (67) 99999-9901'});
  assert.equal(dialog(h).dataset.dmView,'review');const message=writes(h)[0].data.message;
  assert.deepEqual(message,{kind:'text',device_id:h.server.options.devices[0].id,recipients:{mode:'phone',phone:'5567999999901',contact_keys:[]},text});
  assert.equal(dialog(h).querySelector('.os-dm-message p').textContent,text);assert.equal(h.server.confirmations.length,0);
});

for(const [name,mark] of [['bold','*'],['italic','_'],['mono',String.fromCharCode(96).repeat(3)],['strike','~']])test('toolbar '+name+' wraps only the selection and retains a useful selection',async t=>{
  const h=await setup(t);fillText(h,{text:'Antes meio depois'});select(h,6,10);h.click('[data-dm-format="'+name+'"]');
  const node=h.element('#osDmText');assert.equal(node.value,'Antes '+mark+'meio'+mark+' depois');assert.equal(node.selectionStart,6+mark.length);assert.equal(node.selectionEnd,10+mark.length);assert.equal(h.document.activeElement,node);assert.equal(writes(h).length,0);
});

test('formatting and emoji enforce UTF-16 length without truncating the draft',async t=>{
  const h=await setup(t);fillText(h,{text:'😀'.repeat(500)});assert.match(h.element('[data-dm-counter]').textContent,/1000/);select(h,0,2);h.click('[data-dm-format="bold"]');assert.equal(h.element('#osDmText').value,'😀'.repeat(500));assert.match(h.document.body.textContent,/ultrapassa o limite/);
  await review(h,{text:'😀'.repeat(501)});assert.equal(writes(h).length,0);assert.match(h.document.body.textContent,/até 1.000/);
  await review(h,{text:'😀'.repeat(500)});assert.equal(dialog(h).dataset.dmView,'review');assert.equal(writes(h)[0].data.message.text.length,1000);
});

test('emoji search, category, caret insertion and Escape use only local content',async t=>{
  const h=await setup(t);fillText(h,{text:'Bom dia'});select(h,4,4);h.click(action('emoji'));h.fill(field('emoji_q'),'coracao');
  assert.ok(h.document.querySelectorAll('[data-dm-emoji]').length);h.change(field('emoji_category'),'simbolos');
  const chosen=h.document.querySelector('[data-dm-emoji]');assert.ok(chosen);const emoji=chosen.dataset.dmEmoji;chosen.click();assert.equal(h.element('#osDmText').value,'Bom '+emoji+'dia');assert.equal(h.document.activeElement,h.element('#osDmText'));
  h.click(action('emoji'));h.fill(field('emoji_q'),'zzzz inexistente');assert.match(h.document.body.textContent,/Nenhum emoji/);h.element(field('emoji_q')).dispatchEvent(new h.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert.equal(h.document.querySelector('[data-dm-emoji-grid]'),null);assert.equal(writes(h).length,0);
});

test('all three tab drafts persist in memory, with no hidden union fields in the selected request',async t=>{
  const h=await setup(t);fillText(h,{text:'Rascunho literal {name}'});await media(h,'orientacoes.txt','Legenda guardada');h.change('[data-dm-save]',true);h.fill(field('name'),'Documento guardado');const asset=h.server.uploads.values().next().value.result.asset.id;
  h.click('[data-dm-tab="template"]');h.change(field('device_id'),uid(30));h.fill(field('phone'),'5567999999902');h.change(field('template_id'),uid(101));
  h.click('[data-dm-tab="text"]');assert.equal(h.element(field('text')).value,'Rascunho literal {name}');assert.equal(h.element(field('phone')).value,'5567999999901');assert.equal(h.element('[data-dm-save]').checked,false);
  h.click('[data-dm-tab="media"]');assert.equal(h.element(field('caption')).value,'Legenda guardada');assert.equal(h.element(field('name')).value,'Documento guardado');assert.match(h.element('[data-dm-file-status]').textContent,/orientacoes.txt/);
  h.click('[data-dm-tab="template"]');assert.equal(h.element(field('template_id')).value,uid(101));assert.equal(h.element(field('device_id')).value,uid(30));
  h.click('[data-dm-tab="text"]');await review(h,false);const payload=writes(h).find(c=>c.path===base+'/review').data.message;assert.equal(payload.text,'Rascunho literal {name}');assert.equal(payload.asset_id,undefined);assert.equal(payload.template_id,undefined);assert.equal(payload.save_template,undefined);assert.ok(asset);
});

test('contact selection is exclusive, unique, bounded at ten and retained across pages',async t=>{
  const h=await setup(t);fillText(h);h.change('[data-dm-mode="contacts"]',true);await flush();assert.equal(h.document.querySelector(field('phone')),null);
  for(const contact of h.server.contacts.slice(0,9))h.change('[data-dm-contact="'+contact.key+'"]',true);
  h.click(action('contacts-next'));await flush();const last=h.server.contacts[30];h.change('[data-dm-contact="'+last.key+'"]',true);assert.equal(h.controller.getState().selectedContacts,10);assert.equal(h.element('[data-dm-contact="'+h.server.contacts[31].key+'"]').disabled,true);
  assert.equal(h.element('[data-dm-contact="'+h.server.contacts[34].key+'"]').disabled,true);assert.match(h.document.body.textContent,/retirou a autorização/);
  h.change('[data-dm-contact="'+h.server.contacts[31].key+'"]',true);assert.equal(h.controller.getState().selectedContacts,10);
  h.click(action('contacts-prev'));await flush();assert.equal(h.element('[data-dm-contact="'+h.server.contacts[0].key+'"]').checked,true);
  await review(h,false);const recipients=writes(h)[0].data.message.recipients;assert.equal(recipients.mode,'contacts');assert.equal(recipients.phone,undefined);assert.equal(new Set(recipients.contact_keys).size,10);assert.equal(dialog(h).querySelectorAll('[data-dm-review-row]').length,10);
});

test('contact search and mode changes do not leak earlier selection or phone',async t=>{
  const h=await setup(t);fillText(h);h.change('[data-dm-mode="contacts"]',true);await flush();h.fill(field('contact_q'),'31');h.click(action('contacts-search'));await flush();assert.equal(h.document.querySelectorAll('[data-dm-contact]').length,1);h.change('[data-dm-contact="'+h.server.contacts[30].key+'"]',true);
  h.change('[data-dm-mode="phone"]',true);assert.equal(h.controller.getState().selectedContacts,0);assert.equal(h.element(field('phone')).value,'');h.fill(field('phone'),'5567999999905');await review(h,false);assert.deepEqual(writes(h)[0].data.message.recipients,{mode:'phone',phone:'5567999999905',contact_keys:[]});
});

test('media uses one manual number and the existing upload endpoint before review',async t=>{
  const h=await setup(t);await media(h,'orientacoes.txt','Primeira\nSegunda\r\nTerceira');assert.equal(h.document.querySelector('[data-dm-mode]'),null);assert.equal(h.element('[data-dm-file]').multiple,false);
  assert.deepEqual(h.element('[data-dm-file]').accept.split(',').map(s=>s.slice(1)).sort(),contract.options.limits.media_extensions.slice().sort());
  assert.equal(writes(h)[0].path,templateBase+'/assets');assert.equal(writes(h)[0].data.kind,'document');assert.equal(h.server.confirmations.length,0);assert.equal(h.server.savedTemplates.length,0);
  await review(h,false);assert.equal(dialog(h).dataset.dmView,'review');assert.match(dialog(h).textContent,/Primeira Segunda Terceira/);assert.match(dialog(h).textContent,/quebras de linha/);const m=writes(h)[1].data.message;assert.equal(m.caption,'Primeira\nSegunda\nTerceira');assert.equal(m.text,undefined);assert.equal(m.template_id,undefined);assert.ok(m.asset_id); // A textarea canonicalizes CRLF to LF before submission.
});

for(const extension of ['jpg','jpeg','png','webp','pdf','docx','xlsx','csv','txt','mp3','mp4','ogg','wav','opus','apk'])test('accepted media extension '+extension+' maps into a supported private asset kind',async t=>{
  const h=await setup(t);h.click('[data-dm-tab="media"]');await h.upload('exemplo.'+extension);const kind=['jpg','jpeg','png','webp'].includes(extension)?'image':extension==='mp4'?'video':['mp3','ogg','wav','opus'].includes(extension)?'audio':'document';
  assert.equal(writes(h).length,1);assert.equal(writes(h)[0].data.kind,kind);assert.match(h.element('[data-dm-file-status]').textContent,/arquivo preparado/);assert.match(h.element('[data-dm-file-status]').textContent,new RegExp('exemplo\\.'+(kind==='image'?'jpg':kind==='audio'?'mp3':extension)));assert.equal(h.server.confirmations.length,0);
});

test('audio preview drops caption without creating a follow-up text',async t=>{
  const h=await setup(t);await media(h,'aviso.mp3','Nunca enviar esta legenda');await review(h,false);const op=[...h.server.requests.values()][0].operation,preview=op.preview.items[0].message_preview;
  assert.deepEqual(op.preview.warnings,['AUDIO_CAPTION_OMITTED']);assert.equal(preview.parts.length,1);assert.equal(preview.parts[0].type,'audio');assert.equal(preview.caption,null);assert.equal(preview.followup,null);assert.equal(dialog(h).textContent.includes('Nunca enviar esta legenda'),false);assert.match(dialog(h).textContent,/não acompanha arquivos de áudio/);
});

test('invalid, oversized and multiple files fail before any upload request',async t=>{
  const h=await setup(t);h.click('[data-dm-tab="media"]');await h.upload('programa.exe');assert.equal(writes(h).length,0);await h.upload('grande.txt','x',{size:10000001});assert.equal(writes(h).length,0);
  const file=new h.window.File(['one'],'one.txt');await h.upload('two.txt','two',{files:[file,file]});assert.equal(writes(h).length,0);assert.match(h.document.body.textContent,/somente um arquivo/);
});

test('an unknown upload result permits only an explicit retry with the same upload identity',async t=>{
  let lost=true;const h=await setup(t,{respond:async(path,method,payload,server)=>{if(lost&&path===templateBase+'/assets'){lost=false;await server.execute(path,method,payload);throw failure(504);}}});
  h.click('[data-dm-tab="media"]');await h.upload();assert.equal(h.controller.getState().uploadPending,true);assert.equal(writes(h).length,1);assert.equal(h.element(action('review')).disabled,true);h.click(action('retry-upload'));await flush();
  assert.deepEqual(writes(h)[0],writes(h)[1]);assert.equal(h.server.uploads.size,1);assert.equal(h.controller.getState().uploadPending,false);assert.equal(h.server.confirmations.length,0);
});

test('save-template needs a bounded name and creates one library entry only at confirmation',async t=>{
  const h=await setup(t);fillText(h,{text:'Texto para a biblioteca'});h.change('[data-dm-save]',true);await review(h,false);assert.equal(writes(h).length,0);h.fill(field('name'),'😀'.repeat(51));await review(h,false);assert.equal(writes(h).length,0);
  h.fill(field('name'),'  Nome escolhido  ');await review(h,false);assert.deepEqual(writes(h)[0].data.message.save_template,{name:'Nome escolhido'});assert.equal(h.server.savedTemplates.length,0);assert.match(dialog(h).textContent,/Também será salvo/);
  await confirm(h);assert.equal(h.server.savedTemplates.length,1);assert.match(h.element('[data-dm-saved-template]').textContent,/Nome escolhido/);h.click(action('recover'));await flush();assert.equal(h.server.savedTemplates.length,1);assert.equal(h.server.confirmations.length,1);
});

for(const [index,kind] of kinds.entries())test('selected library template '+kind+' keeps its existing parts without hidden text fields',async t=>{
  const h=await setup(t);h.click('[data-dm-tab="template"]');h.change(field('device_id'),uid(30));h.fill(field('phone'),'5567999999901');h.change(field('template_id'),uid(100+index));
  assert.equal(h.document.querySelector(field('text')),null);assert.equal(h.document.querySelector('[data-dm-save]'),null);assert.equal(h.document.querySelector('[data-dm-file]'),null);
  await review(h,false);assert.equal(dialog(h).dataset.dmView,'review');assert.deepEqual(Object.keys(writes(h)[0].data.message).sort(),['device_id','kind','recipients','template_id']);assert.equal([...h.server.requests.values()][0].operation.preview.items[0].message_preview.type,kind);
});

test('confirmation requires fresh consent and exact ENVIAR, then tracks registration separately from delivery',async t=>{
  const h=await setup(t);await review(h);assert.equal(h.element(action('confirm')).disabled,true);h.fill(field('confirmation'),'ENVIAR');assert.equal(h.element(action('confirm')).disabled,true);h.change('[data-dm-consent]',true);h.fill(field('confirmation'),'enviar');assert.equal(h.element(action('confirm')).disabled,true);
  h.fill(field('confirmation'),'ENVIAR');const trigger=h.click(action('confirm'));trigger.click();await flush();assert.equal(h.server.confirmations.length,1);assert.equal(writes(h).length,2);assert.equal(dialog(h).dataset.dmView,'result');assert.match(dialog(h).textContent,/Pedido registrado/);
  h.click(action('acknowledge'));await flush();assert.equal(h.controller.getState().view,'result');assert.match(h.document.body.textContent,/Na fila/);assert.match(h.document.body.textContent,/não confirma entrega ou leitura/);assert.equal(h.document.querySelectorAll('[data-dm-recipient]').length,1);assert.equal(new URL(h.window.location).searchParams.get('message_id'),h.server.confirmations[0].send_id);assert.equal(saved(h),undefined);
});

for(const endpoint of ['review','confirm'])test('lost '+endpoint+' response is recovered on reload with GET only and no duplicate admission',async t=>{
  let lost=true;const h=await setup(t,{respond:async(path,method,payload,server)=>{if(lost&&method==='POST'&&path===base+'/'+endpoint){lost=false;await server.execute(path,method,payload);throw failure(504);}}});await review(h);if(endpoint==='confirm')await confirm(h);assert.equal(dialog(h).dataset.dmView,'unresolved');const record=copy(saved(h));assert.equal(record.confirming,endpoint==='confirm');
  const reloaded=await setup(t,{server:h.server,historyState:copy(h.window.history.state)});assert.equal(writes(reloaded).length,0);assert.equal(dialog(reloaded).dataset.dmView,endpoint==='review'?'review':'result');assert.equal(saved(reloaded).idempotency_key,record.idempotency_key);assert.equal(h.server.confirmations.length,endpoint==='confirm'?1:0);assert.equal(h.server.requests.size,1);
  assert.deepEqual(Object.keys(record).sort(),['account_scope','confirming','idempotency_key','kind','send_id','tenant_id']);assert.ok(!JSON.stringify(record).includes('556799'));assert.equal(reloaded.window.localStorage.length,0);assert.equal(reloaded.window.sessionStorage.length,0);
});

test('unconfirmed upload and all three tab drafts disappear on reload without persistent content',async t=>{
  const h=await setup(t);fillText(h,{text:'Não guardar este rascunho'});await media(h);h.click('[data-dm-tab="template"]');h.change(field('template_id'),uid(101));assert.equal(saved(h),undefined);
  const next=await setup(t,{server:h.server,historyState:copy(h.window.history.state)});assert.equal(next.element(field('text')).value,'');next.click('[data-dm-tab="media"]');assert.match(next.element('[data-dm-file-status]').textContent,/Nenhum arquivo/);assert.equal(next.element(field('caption')).value,'');next.click('[data-dm-tab="template"]');assert.equal(next.element(field('template_id')).value,'');assert.equal(writes(next).length,0);
});

test('authenticated 404 allows an explicit same-key retry, never an automatic mutation',async t=>{
  let lost=true;const h=await setup(t,{respond:async(path,method)=>{if(lost&&method==='POST'&&path===base+'/review'){lost=false;throw failure(504);}}});await review(h);assert.equal(h.document.querySelector(action('retry-same')),null);h.click(action('recover'));await flush();assert.equal(writes(h).length,1);h.click(action('retry-same'));await flush();assert.deepEqual(writes(h)[0],writes(h)[1]);assert.equal(dialog(h).dataset.dmView,'review');assert.equal(h.server.requests.size,1);
});

test('correction after missing admission keeps the same key and respects an original late review',async t=>{
  let first=true,original;const h=await setup(t,{respond:async(path,method,payload)=>{if(first&&path===base+'/review'&&method==='POST'){first=false;original=copy(payload);throw failure(422);}}});await review(h);h.click(action('recover'));await flush();h.click(action('correct'));h.fill(field('text'),'Correção explícita');await h.server.execute(base+'/review','POST',original);await review(h,false);assert.equal(dialog(h).dataset.dmView,'unresolved');assert.equal(writes(h)[1].data.idempotency_key,original.idempotency_key);h.click(action('recover'));await flush();assert.equal(dialog(h).dataset.dmView,'review');assert.match(dialog(h).textContent,/Texto literal/);assert.equal(dialog(h).textContent.includes('Correção explícita'),false);
});

test('lost confirmation that still reads prepared cannot discard the admitted identity',async t=>{
  let first=true;const h=await setup(t,{respond:async(path,method)=>{if(first&&path===base+'/confirm'&&method==='POST'){first=false;throw failure(504);}}});await review(h);await confirm(h);const key=saved(h).idempotency_key;h.click(action('recover'));await flush();assert.equal(dialog(h).dataset.dmView,'review');assert.equal(h.document.querySelector(action('cancel-review')),null);assert.equal(h.element('[data-dm-consent]').checked,false);assert.equal(h.element(field('confirmation')).value,'');assert.equal(saved(h).confirming,true);await confirm(h);assert.equal(h.server.confirmations.length,1);assert.ok(writes(h).every(c=>c.data.idempotency_key===key));
});

test('reload of a missing request cannot invent a retry payload or release its key',async t=>{
  const h=await setup(t,{historyState:{orionDirectRecovery:recovery()}});assert.equal(dialog(h).dataset.dmView,'unresolved');assert.equal(h.document.querySelector(action('retry-same')),null);assert.equal(h.document.querySelector(action('correct')),null);assert.equal(h.element(action('review')).disabled,true);assert.equal(writes(h).length,0);assert.equal(saved(h).idempotency_key,recovery().idempotency_key);
});

test('expired review acknowledgement does not create another send or erase the editable draft',async t=>{
  const h=await setup(t);await review(h);const op=h.server.requests.get(saved(h).idempotency_key).operation;op.expires_at=new Date(Date.now()-1000).toISOString();op.preview.expires_at=op.expires_at;h.click(action('recover'));await flush();assert.equal(dialog(h).dataset.dmView,'expired');h.click(action('acknowledge'));await flush();assert.equal(h.controller.getState().pending,false);assert.equal(h.element(field('text')).value,'Olá, {name}. Texto literal.');assert.equal(writes(h).length,1);assert.equal(h.server.confirmations.length,0);
});

test('cancel preserves uncertain recipients and never offers automatic resend',async t=>{
  const server=syntheticServer(),detail=server.sends.get(sendId);detail.recipients.items.push({...copy(detail.recipients.items[0]),id:uid(400),row_key:uid(401),phone_e164:'+5567999999902',status:'uncertain',error_message:'O resultado não pôde ser confirmado.'});server.updateProgress(detail);
  const h=await setup(t,{server,url:'https://orion.test/panel?aba=enviar-mensagem&message_id='+sendId});await cancel(h);assert.equal(writes(h).length,1);assert.deepEqual(Object.keys(writes(h)[0].data).sort(),['action','confirmation','expected_version','idempotency_key']);assert.equal(writes(h)[0].data.expected_version,1);h.click(action('acknowledge'));await flush();assert.match(h.document.body.textContent,/Resultado incerto/);assert.equal(server.sends.get(sendId).recipients.items[1].status,'uncertain');assert.equal(h.document.querySelectorAll('[data-dm-recipient] button').length,0);
});

test('exact stale cancel proof releases only its rejected admission and refreshes state',async t=>{
  const h=await setup(t,{url:'https://orion.test/panel?aba=enviar-mensagem&message_id='+sendId});h.click(action('cancel'));await flush();h.server.sends.get(sendId).send.version=2;h.click(action('confirm-cancel'));await flush();assert.equal(h.controller.getState().pending,false);assert.equal(saved(h),undefined);assert.match(h.document.body.textContent,/cancelamento não foi registrado/);assert.equal(h.server.requests.size,0);assert.equal(writes(h).length,1);
});

for(const mismatch of ['idempotency_key','kind','send_id','expected_version','reason'])test('mismatched cancel proof '+mismatch+' retains unresolved identity',async t=>{
  const h=await setup(t,{url:'https://orion.test/panel?aba=enviar-mensagem&message_id='+sendId,respond:async(path,method,payload)=>{if(method==='POST'&&path.endsWith('/actions')){const proof={reason:'stale_version',idempotency_key:payload.idempotency_key,kind:'cancel',send_id:sendId,expected_version:payload.expected_version};proof[mismatch]=mismatch==='expected_version'?999:'incorrect';throw failure(409,{code:'DIRECT_CONFLICT',directRejection:proof});}}});await cancel(h);assert.equal(h.controller.getState().pending,true);assert.equal(dialog(h).dataset.dmView,'unresolved');assert.ok(saved(h));assert.equal(h.document.querySelector(action('correct')),null);
});

for(const invalid of ['idempotency_key','review_version','total','expiry','duplicate','kind'])test('malformed review '+invalid+' cannot be presented as confirmed or unlock the write',async t=>{
  const h=await setup(t,{respond:async(path,method,payload,server)=>{if(method==='POST'&&path===base+'/review'){const op=await server.execute(path,method,payload);if(invalid==='idempotency_key')op.idempotency_key=uid(999);if(invalid==='review_version')op.preview.review_version='bad';if(invalid==='total')op.preview.total=2;if(invalid==='expiry')op.preview.expires_at=new Date(Date.now()+999999).toISOString();if(invalid==='duplicate'){op.preview.items.push(copy(op.preview.items[0]));op.preview.total=2;op.preview.eligible=2;}if(invalid==='kind')op.kind='cancel';return op;}}});await review(h);assert.equal(dialog(h).dataset.dmView,'unresolved');assert.equal(h.document.querySelector(action('confirm')),null);assert.ok(saved(h));h.click(action('recover'));await flush();assert.equal(dialog(h).dataset.dmView,'review');
});

test('tenant and account mismatches remove recovery without querying another private operation',async t=>{
  for(const extra of [{tenant_id:tenantB},{account_scope:'f'.repeat(64)}]){const h=await setup(t,{historyState:{orionDirectRecovery:recovery(extra)}});assert.equal(saved(h),undefined);assert.equal(h.calls.some(c=>c.path.includes('/requests/')),false);assert.equal(h.controller.getState().pending,false);}
});

test('late writes and reads cannot repopulate an unmounted context; only reads are abortable',async t=>{
  const delayed=deferred();const h=await setup(t,{respond:async(path,method,payload,server)=>{if(method==='POST'&&path===base+'/review'){await delayed.promise;return server.execute(path,method,payload);}}});await review(h);assert.equal(h.controller.getState().busy,true);assert.equal(h.signals.find(c=>c.path===base+'/review').signal,undefined);assert.ok(h.signals.find(c=>c.path.endsWith('/options')).signal);h.controller.unmount();delayed.resolve();await flush();assert.equal(h.document.querySelector('[data-dm-dialog]'),null);assert.equal(h.element('#direct').children.length,0);assert.equal(h.server.confirmations.length,0);
});

test('permission revocation purges private drafts, assets, results and recovery before another request',async t=>{
  const h=await setup(t);fillText(h,{text:'Texto privado'});await media(h,'privado.txt','Legenda privada');h.click('[data-dm-tab="template"]');h.change(field('template_id'),uid(101));h.click('[data-dm-tab="text"]');await review(h,false);const count=h.calls.length;h.access.allowed=false;h.click(action('recover'));await flush();assert.equal(h.calls.length,count);assert.equal(h.document.querySelector('[data-dm-dialog]'),null);assert.equal(saved(h),undefined);assert.equal(h.controller.getState().selectedContacts,0);assert.ok(!h.document.body.textContent.includes('Texto privado'));assert.match(h.document.body.textContent,/acesso a este módulo/);
  h.access.allowed=true;const remounted=h.window.OrionSubscriptionDirectMessages.mount(h.bridge);await remounted.ready;assert.equal(h.element(field('text')).value,'');h.click('[data-dm-tab="media"]');assert.match(h.element('[data-dm-file-status]').textContent,/Nenhum arquivo/);h.click('[data-dm-tab="template"]');assert.equal(h.element(field('template_id')).value,'');
});

test('clearContext removes all three tab drafts and send query identity immediately',async t=>{
  const h=await setup(t);fillText(h);await media(h);h.window.OrionSubscriptionDirectMessages.clearContext();assert.equal(saved(h),undefined);assert.equal(h.document.querySelector('[data-dm-file]'),null);assert.equal(h.document.querySelector(field('text')),null);assert.equal(new URL(h.window.location).searchParams.has('message_id'),false);
});

for(const status of [401,403])test('authenticated '+status+' revocation removes private content and operation recovery',async t=>{
  let revoked=false;const h=await setup(t,{respond:async(path)=>{if(revoked&&path.includes('/requests/'))throw failure(status);}});await review(h);revoked=true;h.click(action('recover'));await flush();assert.equal(saved(h),undefined);assert.equal(h.document.querySelector('[data-dm-dialog]'),null);assert.equal(h.document.querySelector(field('text')),null);
});

test('read-only access, absent identity or unavailable account hash disable every write',async t=>{
  for(const options of [{canWrite:false},{identity:''},{cryptoEnabled:false}]){const h=await setup(t,options);assert.equal(h.element(action('review')).disabled,true);h.click(action('review'));assert.equal(writes(h).length,0);assert.equal(h.element('fieldset').disabled,true);}
});

test('labels and content are escaped in form, review and tracked result',async t=>{
  const server=syntheticServer();server.options.devices[0].name='<img src=x onerror=alert(1)>';const h=await setup(t,{server});await review(h,{text:'<script>window.pwned=true</script> & "literal"'});assert.equal(h.document.querySelector('script,img,iframe'),null);assert.match(dialog(h).textContent,/<script>window.pwned=true/);assert.equal(h.window.pwned,undefined);await confirm(h);h.click(action('acknowledge'));await flush();assert.equal(h.document.querySelector('script,img,iframe'),null);assert.match(h.document.body.textContent,/<script>window.pwned=true/);
});

test('review actions are outside its scroll body, with accessible consent and return focus',async t=>{
  const h=await setup(t);await review(h);const modal=dialog(h),body=modal.querySelector('.os-dialog-body'),footer=modal.querySelector('.os-dialog-footer');assert.equal(footer.parentElement,modal);assert.equal(body.contains(footer),false);assert.ok(body.querySelector('[data-dm-consent]'));assert.ok(footer.querySelector(action('confirm')));
  h.element('[data-dm-consent]').focus();h.change('[data-dm-consent]',true);assert.equal(h.document.activeElement,h.element('[data-dm-consent]'));h.click(action('cancel-review'));assert.equal(h.document.activeElement,h.element(action('review')));assert.equal(h.server.confirmations.length,0);assert.equal(h.controller.getState().pending,false);
});

test('Suite exposes enviar-mensagem only for its subscription feature without opening Central',async t=>{
  const {makeSuite,planState,defaultResponse}=await import('./harness.mjs');
  for(const [profile,allowed]of [['subscription_services',true],['subscription_services',false],['standard',true]]){
    const server=syntheticServer(),loaded=deferred(),permissions=planState('none',{workspace_profile:profile,features:['account.manage','business.setup',...(allowed?['direct_messages.manage']:[])],permissions:{'account.manage':true,'business.setup':true,'direct_messages.manage':allowed}});
    const h=await makeSuite({initialRoute:'enviar-mensagem',permissions,bridge:{tenants:[{id:tenantA,name:'Empresa sintética',role:'owner',workspace_profile:profile}]},configureWindow:window=>{Object.defineProperty(window.crypto,'subtle',{value:webcrypto.subtle,configurable:true});window.TextEncoder=TextEncoder;window.eval(source);},respond:path=>{if(path.startsWith(base)){loaded.resolve();return server.execute(path);}return defaultResponse(path);}});t.after(()=>h.close());
    if(profile==='subscription_services'&&allowed){await loaded.promise;await flush();assert.ok(h.document.querySelector('#osNav [data-os-route="enviar-mensagem"]'));assert.ok(h.document.querySelector('#osSubscriptionDirectMessages [data-dm-form]'));assert.equal(h.document.querySelector('#osNav [data-os-route="conversas"]'),null);}else{await flush();assert.equal(h.document.querySelector('#osNav [data-os-route="enviar-mensagem"]'),null);assert.equal(h.calls.filter(c=>c.path.startsWith(base)).length,0);}assert.deepEqual(h.errors,[]);
  }
});

test('real panel bootstrap sanitizes direct recovery and removes untrusted tenant URL selection',async t=>{
  const html=await readFile(new URL('../app/static/panel.html',import.meta.url),'utf8'),prefix=html.match(/async function showApp\([^)]*\)\{[\s\S]*?(?=\n suiteUser=)/)?.[0];assert.ok(prefix);
  const dom=new JSDOM('',{url:'https://orion.test/painel?aba=enviar-mensagem&empresa=untrusted',runScripts:'outside-only'});t.after(()=>dom.window.close());dom.window.eval(source);const record=recovery();dom.window.history.replaceState({orionDirectRecovery:{...record,private_text:'discard'},unrelated:'discard'},'');
  dom.window.eval('var authEpoch=1,suiteTenantId="",businessRows=[],businessContextReady=false,businessContextError="";function resetWizard(){};'+prefix+'\n}');await dom.window.showApp('member','Synthetic','synthetic@example.invalid');assert.deepEqual(copy(dom.window.history.state),{orionDirectRecovery:record});assert.equal(new URL(dom.window.location).searchParams.get('aba'),'enviar-mensagem');assert.equal(new URL(dom.window.location).searchParams.has('empresa'),false);
});

test('real panel adapter forwards read abort and accepts sanitized direct cancel proof only on its endpoint',async t=>{
  const html=await readFile(new URL('../app/static/panel.html',import.meta.url),'utf8'),actual=html.match(/async function api\(url,method="GET",data,options\)\{[\s\S]*?\n\}/)?.[0];assert.ok(actual);
  const dom=new JSDOM('',{url:'https://orion.test/painel',runScripts:'outside-only'});t.after(()=>dom.window.close());dom.window.eval('var authEpoch=1,csrf="test-csrf",suiteTenantId="test",businessContextError="";function accountBusiness(){return {id:"test"};}function errorMessage(){return "test error";}'+actual);
  const signal=new dom.window.AbortController().signal;dom.window.fetch=async(path,options)=>{assert.equal(options.signal,signal);return {ok:true,status:200,json:async()=>({items:[]})};};await dom.window.api('/api/v1/tenants/test/subscription-whatsapp/direct-messages/options','GET',undefined,{signal});
  const proof={reason:'stale_version',idempotency_key:'operation',kind:'cancel',send_id:'send',expected_version:1};dom.window.fetch=async(path,options)=>{assert.equal(options.signal,undefined);assert.equal(options.headers['X-Orion-CSRF'],'test-csrf');return {ok:false,status:409,json:async()=>({detail:{code:'DIRECT_CONFLICT',rejection:{...proof,private:'discard'}}})};};
  await assert.rejects(dom.window.api('/api/v1/tenants/test/subscription-whatsapp/direct-messages/send/actions','POST',{}),error=>{assert.equal(error.code,'DIRECT_CONFLICT');assert.deepEqual(copy(error.directRejection),proof);return true;});
  for(const suffix of ['/confirm','/review','/requests/key'])await assert.rejects(dom.window.api('/api/v1/tenants/test/subscription-whatsapp/direct-messages'+suffix,'POST',{}),error=>{assert.equal(error.directRejection,undefined);return true;});
});
