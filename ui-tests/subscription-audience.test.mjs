import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import test from 'node:test';
import {JSDOM,VirtualConsole} from 'jsdom';

const source=await readFile(new URL('../app/static/orion-subscription-audience.js',import.meta.url),'utf8');
const fields=await readFile(new URL('../app/static/orion-fields.js',import.meta.url),'utf8');
const fixture=JSON.parse(await readFile(new URL('../tests/fixtures/subscription_audience_contract.json',import.meta.url),'utf8'));
const tenant='20000000-0000-4000-8000-000000000001';
const group=fixture.groups.items[0].id;
const flush=async()=>{for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));};
const pending=()=>{let resolve;return {promise:new Promise(r=>{resolve=r;}),resolve:value=>resolve(value)};};
const copy=value=>JSON.parse(JSON.stringify(value));
const page=(items,options={})=>({items,total:items.length,page:1,page_size:30,pages:1,...options});
const receipt=payload=>({request_id:randomUUID(),idempotency_key:payload.idempotency_key,replayed:false,result:{}});

async function setup({view='contacts',respond,initialGroupId='',openContacts}={}){
  const errors=[],calls=[],downloads=[],fetches=[],access={allowed:true,writable:true};
  const console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error));
  const dom=new JSDOM('<!doctype html><html lang="pt-BR"><body><main id="host"></main></body></html>',{url:'https://orion.test/painel',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:console});
  const {window}=dom,{document}=window;
  Object.defineProperty(window.crypto,'randomUUID',{value:randomUUID});
  window.TextDecoder=TextDecoder;
  window.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};
  window.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};
  window.URL.createObjectURL=blob=>{downloads.push({blob});return 'blob:https://orion.test/'+downloads.length;};
  window.URL.revokeObjectURL=()=>{};
  document.addEventListener('click',event=>{const a=event.target.closest('a');if(a){event.preventDefault();Object.assign(downloads.at(-1),{name:a.download,href:a.href});}});
  window.fetch=async(path,options)=>{fetches.push({path,options});return {ok:true,headers:new Map([['content-type','text/csv']]),blob:async()=>new window.Blob(['Nome,Telefone\r\nAna,+5567999991234'],{type:'text/csv'})};};
  window.eval(fields);window.eval(source);
  const bridge={host:document.querySelector('#host'),tenantId:tenant,view,initialGroupId,openContacts,canAccess:()=>access.allowed,canWrite:()=>access.writable,
    async request(path,method='GET',payload,config){
      calls.push({path,method,payload:copy(payload??null),signal:config?.signal});
      const custom=respond&&await respond(path,method,payload);
      if(custom!==undefined)return copy(custom);
      if(method!=='GET')return receipt(payload);
      if(/\/groups\?/.test(path))return copy(fixture.groups);
      if(path.endsWith('/groups/'+group))return copy(fixture.group_detail);
      if(/\/contacts\?/.test(path))return copy(fixture.contacts);
      if(/\/contacts\//.test(path))return copy(fixture.contacts.items[0]);
      throw Error('Unexpected test request: '+path);
    }};
  const controller=await window.OrionSubscriptionAudience.mount(bridge);
  const el=selector=>{const node=document.querySelector(selector);assert.ok(node,'Missing '+selector);return node;};
  const click=async action=>{el('[data-pa-action="'+action+'"]').click();await flush();};
  const field=(name,value,event='input')=>{const node=el('[name="'+name+'"]');if(node.type==='checkbox')node.checked=value;else node.value=value;node.dispatchEvent(new window.Event(event,{bubbles:true}));return node;};
  const submit=async(kind='contact')=>{el('[data-pa-form="'+kind+'"]').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));await flush();};
  const file=(text,name='contatos.csv',size)=>{const data=typeof text==='string'?new TextEncoder().encode(text):text;const object={name,size:size??data.length,arrayBuffer:async()=>data.buffer.slice(data.byteOffset,data.byteOffset+data.byteLength)};Object.defineProperty(el('[name="file"]'),'files',{value:[object],configurable:true});el('[name="file"]').dispatchEvent(new window.Event('change',{bubbles:true}));return object;};
  return {dom,window,document,controller,calls,downloads,fetches,errors,access,el,click,field,submit,file,close(){controller.destroy();dom.window.close();}};
}

test('contacts consume the server DTO, escape names and paginate without inventing counts',async t=>{
  const h=await setup({respond(path,method){if(method==='GET'&&/\/contacts\?/.test(path)){const query=new URL(path,'https://orion.test').searchParams;return page([{...fixture.contacts.items[0],name:'<script>contato</script>'}],{total:4211,pages:141,page:Number(query.get('page')||1)});}}});t.after(()=>h.close());
  assert.equal(h.el('tbody strong').textContent,'<script>contato</script>');assert.equal(h.document.querySelector('script'),null);
  assert.match(h.el('caption').textContent,/4\.211/);assert.match(h.el('[aria-label="Paginação"]').textContent,/1 de 141/);
  await h.click('next');assert.match(h.calls.at(-2).path,/page=2/);assert.match(h.el('[aria-label="Paginação"]').textContent,/2 de 141/);
  assert.deepEqual(h.errors,[]);
});

test('create contact preserves international phone, optional data, preferences and Orion money parsing',async t=>{
  const h=await setup();t.after(()=>h.close());await h.click('create');
  h.field('name','José 💙');h.field('phone_e164','+351 912 345 678');h.field('telegram','@jose_demo');h.field('email','jose@example.test');
  h.field('notes','Cliente de Portugal');h.field('plan_name','Anual');h.field('price','1.234,56');h.field('due_date','2027-02-10');
  h.field('service_username','demo');h.field('service_password','synthetic-private');h.field('chatbot_enabled',false,'change');h.field('audio_transcription_enabled',false,'change');
  h.el('[data-pa-group]').checked=true;await h.submit();
  const call=h.calls.find(call=>call.method==='POST');assert.ok(call);
  assert.equal(call.payload.contact.phone_e164,'+351 912 345 678');assert.equal(call.payload.contact.price_cents,123456);
  assert.equal(call.payload.contact.chatbot_enabled,false);assert.equal(call.payload.contact.audio_transcription_enabled,false);
  assert.deepEqual(call.payload.contact.group_ids,[group]);assert.equal(call.payload.contact.service_password,'synthetic-private');
  assert.equal(h.document.querySelector('dialog'),null);assert.match(h.el('[data-pa-feedback]').textContent,/Contato salvo/);assert.deepEqual(h.errors,[]);
});

test('editing uses the current version and retains a saved password when its field is blank',async t=>{
  const h=await setup();t.after(()=>h.close());await h.click('edit');
  assert.equal(h.el('[name="service_password"]').value,'');assert.match(h.document.body.textContent,/Uma senha já está salva/);
  h.field('name','Ana atualizada');await h.submit();const call=h.calls.find(call=>call.method==='PATCH');
  assert.equal(call.payload.expected_version,fixture.contacts.items[0].version);assert.equal(Object.hasOwn(call.payload.contact,'service_password'),false);
  assert.equal(call.payload.contact.clear_password,false);assert.deepEqual(h.errors,[]);
});

test('a Telegram-only contact is accepted but absence of both channels is explained',async t=>{
  const h=await setup();t.after(()=>h.close());await h.click('create');h.field('name','Telegram demo');await h.submit();
  assert.equal(h.calls.filter(call=>call.method!=='GET').length,0);assert.match(h.el('[data-pa-dialog-error]').textContent,/WhatsApp ou/);
  h.field('telegram','123456789');await h.submit();const call=h.calls.find(call=>call.method==='POST');assert.equal(call.payload.contact.phone_e164,null);assert.equal(call.payload.contact.telegram,'123456789');
});

test('uncertain mutation retries the same request, freezes edits and does not create a second idempotency key',async t=>{
  let attempts=0;
  const h=await setup({respond(path,method,payload){if(method==='POST'){if(++attempts===1)throw Object.assign(Error('temporarily unavailable'),{status:503});return {...receipt(payload),replayed:true};}}});t.after(()=>h.close());
  await h.click('create');h.field('name','Contato sintético');h.field('phone_e164','+5567999991234');await h.submit();
  assert.equal(h.controller.getState().pending,true);assert.equal(h.el('[name="name"]').disabled,true);assert.equal(h.el('[data-pa-confirm]').textContent,'Tentar novamente');
  await h.click('close');assert.ok(h.document.querySelector('dialog'));
  await h.submit();const calls=h.calls.filter(call=>call.method==='POST');assert.equal(calls.length,2);assert.deepEqual(calls[0].payload,calls[1].payload);
  assert.equal(h.controller.getState().pending,false);assert.equal(h.document.querySelector('dialog'),null);assert.deepEqual(h.errors,[]);
});

test('a definitive validation error allows corrections and a new request',async t=>{
  let attempts=0;
  const h=await setup({respond(path,method,payload){if(method==='POST'){if(++attempts===1)throw Object.assign(Error('Este WhatsApp já está cadastrado.'),{status:409});return receipt(payload);}}});t.after(()=>h.close());
  await h.click('create');h.field('name','Contato sintético');h.field('phone_e164','+5567999991234');await h.submit();
  assert.equal(h.controller.getState().pending,false);assert.equal(h.el('[name="phone_e164"]').disabled,false);
  h.field('phone_e164','+5567999991235');await h.submit();const calls=h.calls.filter(call=>call.method==='POST');assert.notEqual(calls[0].payload.idempotency_key,calls[1].payload.idempotency_key);
});

test('CSV preview reports all 4211 rows and requires explicit consent to skip invalid rows',async t=>{
  const preview={...copy(fixture.preview),expires_at:new Date(Date.now()+600000).toISOString()};
  const h=await setup({respond(path,method,payload){if(path.endsWith('/imports/preview'))return preview;if(path.endsWith('/imports/commit'))return {...receipt(payload),result:{counts:{added:4205,unchanged:3,duplicates:2,invalid_skipped:1}}};}});t.after(()=>h.close());
  await h.click('import');h.file('\ufeffTelefone;Nome\r\n+5567999991234;José 💙\r\n');await h.submit('import');
  assert.match(h.el('[data-pa-import-preview]').textContent,/4\.211/);assert.match(h.el('[data-pa-import-preview]').textContent,/4\.205/);
  assert.equal(h.el('[data-pa-confirm]').disabled,true);await h.submit('import');assert.equal(h.calls.some(call=>call.path.endsWith('/imports/commit')),false);
  h.field('skip_invalid',true,'change');assert.equal(h.el('[data-pa-confirm]').disabled,false);await h.submit('import');
  const call=h.calls.find(call=>call.path.endsWith('/imports/commit'));assert.equal(call.payload.skip_invalid,true);assert.equal(call.payload.preview_token,preview.preview_token);
  assert.match(h.el('[data-pa-feedback]').textContent,/4\.205 novo/);assert.match(h.el('[data-pa-feedback]').textContent,/1 linha\(s\) inválida/);assert.deepEqual(h.errors,[]);
});

test('CSV decoding accepts Windows-1252 and UTF-16 without corrupting names',async t=>{
  const h=await setup({respond:path=>path.endsWith('/imports/preview')?{...fixture.preview,counts:{total:1,valid:1,invalid:0,duplicates:0,existing:0,new:1},expires_at:new Date(Date.now()+600000).toISOString()}:undefined});t.after(()=>h.close());
  await h.click('import');h.file(new Uint8Array([...Buffer.from('Telefone;Nome\n+5567999991234;Jos'),0xe9]),'contatos.txt');await h.submit('import');
  assert.match(h.calls.find(call=>call.path.endsWith('/imports/preview')).payload.content,/José$/);
  h.file(new Uint8Array([0xff,0xfe,...Buffer.from('Telefone;Nome\n+5567999991234;João','utf16le')]));await h.submit('import');
  assert.match(h.calls.filter(call=>call.path.endsWith('/imports/preview')).at(-1).payload.content,/João$/);
});

test('malformed CSV cannot offer a partial import when the server cannot issue a valid preview token',async t=>{
  const h=await setup({respond:path=>path.endsWith('/imports/preview')?{...fixture.preview,preview_token:null,can_commit:false,can_skip_invalid:false,counts:{total:2,valid:1,invalid:1,duplicates:0,existing:0,new:1},errors:[{line:3,message:'Aspas não fechadas no arquivo CSV.'}]}:undefined});t.after(()=>h.close());
  await h.click('import');h.file('Telefone,Nome\n+5567999991234,Ana\n"aspas abertas');await h.submit('import');
  assert.equal(h.el('[data-pa-confirm]').disabled,true);assert.equal(h.document.querySelector('[name="skip_invalid"]'),null);
  assert.match(h.el('[data-pa-import-preview]').textContent,/Corrija a estrutura/);await h.submit('import');
  assert.equal(h.calls.some(call=>call.path.endsWith('/imports/commit')),false);
});

test('a valid CSV commits the server token without skipping any row',async t=>{
  const h=await setup({respond:(path,method,payload)=>path.endsWith('/imports/preview')?{...fixture.preview,can_commit:true,can_skip_invalid:false,expires_at:new Date(Date.now()+600000).toISOString(),counts:{total:1,valid:1,invalid:0,duplicates:0,existing:0,new:1},errors:[]}:undefined});t.after(()=>h.close());
  await h.click('import');h.file('Telefone,Nome\n+5567999991234,Ana');await h.submit('import');assert.equal(h.el('[data-pa-confirm]').disabled,false);
  await h.submit('import');const call=h.calls.find(call=>call.path.endsWith('/imports/commit'));assert.equal(call.payload.skip_invalid,false);assert.equal(call.payload.preview_token,fixture.preview.preview_token);
});

test('changing import country or target groups invalidates preview, and oversized files never upload',async t=>{
  const h=await setup({respond:path=>path.endsWith('/imports/preview')?fixture.preview:undefined});t.after(()=>h.close());await h.click('import');
  h.file('a','grande.csv',8*1024*1024+1);await h.submit('import');assert.equal(h.calls.some(call=>call.method==='POST'),false);assert.match(h.el('[data-pa-dialog-error]').textContent,/8 MB/);
  h.file('Telefone,Nome\n+5567999991234,Ana');await h.submit('import');assert.match(h.el('[data-pa-import-preview]').textContent,/Conferência/);
  h.field('default_country','PY','change');assert.equal(h.el('[data-pa-import-preview]').children.length,0);assert.equal(h.el('[data-pa-confirm]').textContent,'Conferir arquivo');
  await h.submit('import');const call=h.calls.filter(call=>call.method==='POST').at(-1);assert.equal(call.payload.default_country,'PY');
});

test('group member selection persists across pages and submits all chosen keys',async t=>{
  const h=await setup({view:'groups',respond(path,method){if(method==='GET'&&/\/contacts\?/.test(path)){const p=Number(new URL(path,'https://orion.test').searchParams.get('page'));return page([{...fixture.contacts.items[0],key:'phone:556799999123'+p,name:'Pessoa '+p}],{total:51,page:p,page_size:50,pages:2});}}});t.after(()=>h.close());
  await h.click('create');h.field('name','Grupo sintético');
  let box=h.el('[data-pa-contact-key]');box.checked=true;box.dispatchEvent(new h.window.Event('change',{bubbles:true}));
  await h.click('picker-next');box=h.el('[data-pa-contact-key]');box.checked=true;box.dispatchEvent(new h.window.Event('change',{bubbles:true}));
  await h.click('picker-previous');assert.equal(h.el('[data-pa-contact-key]').checked,true);assert.match(h.el('[data-pa-picker-count]').textContent,/2 contato/);
  await h.submit('group');const call=h.calls.find(call=>call.method==='POST');assert.deepEqual(call.payload.contact_keys,['phone:5567999991231','phone:5567999991232']);assert.deepEqual(h.errors,[]);
});

test('existing group edits retain membership and route to its filtered contacts through the suite',async t=>{
  const opens=[];const h=await setup({view:'groups',openContacts:id=>opens.push(id)});t.after(()=>h.close());
  await h.click('edit');assert.equal(h.el('[data-pa-contact-key]').checked,true);h.field('name','Mensais atualizado');await h.submit('group');
  const call=h.calls.find(call=>call.method==='PATCH');assert.equal(call.payload.expected_version,fixture.group_detail.group.version);assert.deepEqual(call.payload.contact_keys,fixture.group_detail.contact_keys);
  await h.click('group-contacts');assert.deepEqual(opens,[group]);assert.deepEqual(h.errors,[]);
});

test('export covers all filtered contacts instead of the visible page and uses authenticated same-origin fetch',async t=>{
  const h=await setup({initialGroupId:group});t.after(()=>h.close());h.field('q','Ana');await h.submit('search');await h.click('export');
  assert.equal(h.fetches.length,1);const {path,options}=h.fetches[0],url=new URL(path,'https://orion.test');
  assert.equal(url.searchParams.get('q'),'Ana');assert.equal(url.searchParams.get('group_id'),group);assert.equal(url.searchParams.has('page'),false);assert.equal(url.searchParams.has('page_size'),false);
  assert.equal(options.credentials,'same-origin');assert.equal(options.cache,'no-store');assert.equal(h.downloads[0].name,'orion-contatos.csv');assert.deepEqual(h.errors,[]);
});

test('failed refresh disables writes and revoked context clears pending dialogs and late responses',async t=>{
  let fail=false;const h=await setup({respond(path,method){if(fail&&method==='GET')throw Error('Sem conexão');}});t.after(()=>h.close());
  fail=true;await h.click('refresh');assert.equal(h.el('[data-pa-action="create"]').disabled,true);assert.equal(h.el('[data-pa-action="import"]').disabled,true);assert.match(h.el('[data-pa-feedback]').textContent,/Sem conexão/);
  fail=false;await h.click('refresh');await h.click('edit');h.window.OrionSubscriptionAudience.clearContext();assert.equal(h.document.querySelector('dialog'),null);assert.equal(h.el('#host').children.length,0);
  assert.deepEqual(h.errors,[]);
});

test('late mutation response cannot reopen contacts after leaving the account',async t=>{
  const wait=pending();const h=await setup({respond:(path,method)=>method==='POST'?wait.promise:undefined});t.after(()=>h.close());
  await h.click('create');h.field('name','Conta anterior');h.field('phone_e164','+5567999991234');void h.submit();await flush();
  const call=h.calls.find(call=>call.method==='POST');h.window.OrionSubscriptionAudience.clearContext();wait.resolve(receipt(call.payload));await flush();
  assert.equal(h.el('#host').children.length,0);assert.equal(h.document.querySelector('dialog'),null);assert.deepEqual(h.errors,[]);
});

test('deletion requires a dialog and the version of the exact chosen record',async t=>{
  const h=await setup();t.after(()=>h.close());await h.click('delete');assert.equal(h.calls.some(call=>call.method==='DELETE'),false);
  assert.match(h.el('dialog').textContent,/histórico de envios permanece/);await h.submit('delete');const call=h.calls.find(call=>call.method==='DELETE');
  assert.equal(call.payload.expected_version,fixture.contacts.items[0].version);assert.match(call.path,/phone%3A5567999991234$/);assert.equal(h.document.querySelector('dialog'),null);
});
