import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM} from 'jsdom';
import {flush,deferred,tenantA,tenantB} from './harness.mjs';

const customer='10000000-0000-4000-8000-000000000001',label='20000000-0000-4000-8000-000000000001',reply='30000000-0000-4000-8000-000000000001';
async function setup({canWrite=true,enabled=true,respond}={}){
  const dom=new JSDOM('<main id="host"></main>',{url:'https://orion.test',runScripts:'outside-only'}),{window}=dom,calls=[];
  window.eval(await readFile(new URL('../app/static/orion-inbox.js',import.meta.url),'utf8'));
  const request=async(path,method='GET',body)=>{
    calls.push({path,method,body});const custom=await respond?.(path,method,body);if(custom!==undefined)return custom;
    if(method!=='GET')throw Error('A mutation must have a synthetic response');
    if(path.endsWith('/capabilities'))return {enabled};
    if(path.endsWith('/labels'))return {items:[{id:label,name:'<img src=x onerror=alert(1)>',color:'#18836b',version:1}]};
    if(path.endsWith('/quick-replies'))return {items:[{id:reply,title:'Olá',text:'Olá {{cliente.nome}}',category:'Atendimento',visibility:'company',version:2}]};
    if(path.includes('/customers?'))return {items:[{id:customer,name:'Cliente sintético',phone:'+5567999999901'}],has_more:false};
    if(path.endsWith('/customers/'+customer))return {customer:{id:customer,name:'Cliente sintético',whatsapp_e164:'+5567999999901'},labels:[],history:[],history_limit:20,timezone:'America/Cuiaba'};
    throw Error('Unexpected request '+path);
  };
  const host=window.document.querySelector('#host');
  const mount=(id=tenantA)=>window.OrionInbox.mount({host,request,tenantId:id,canWrite});
  await mount();return {window,host,calls,mount,close(){window.OrionInbox.unmount();window.close();},click(action){host.querySelector('[data-inbox-action='+action+']').click();},tab(id){host.querySelector('[data-inbox-tab='+id+']').click();}};
}

test('disabled tools show an honest setup state and issue no mutation',async t=>{
  const h=await setup({enabled:false});t.after(()=>h.close());
  assert.match(h.host.textContent,/preparação/);assert.equal(h.calls.length,1);
});

test('CRM labels escape markup and selecting a customer never creates a duplicate record',async t=>{
  const h=await setup();t.after(()=>h.close());
  h.host.querySelector('[data-inbox-view=labels]').click();await flush();
  assert.equal(h.host.querySelector('img'),null);assert.match(h.host.textContent,/<img src=x/);
  h.host.querySelector('[data-inbox-view=customers]').click();await flush();
  h.host.querySelector('[data-inbox-customer]').click();await flush();
  assert.match(h.host.textContent,/Nenhum agendamento futuro confirmado/);
  assert.equal(h.calls.filter(x=>x.method!=='GET').length,0);
});

test('a reader cannot create, delete or label a customer after a refresh',async t=>{
  const h=await setup({canWrite:false});t.after(()=>h.close());
  h.host.querySelector('[data-inbox-view=labels]').click();await flush();
  assert.equal(h.host.querySelector('[data-inbox-action=new-label]').disabled,true);
  h.host.querySelector('[data-inbox-view=customers]').click();await flush();
  h.host.querySelector('[data-inbox-customer]').click();await flush();
  assert.equal(h.host.querySelector('[data-inbox-label]').disabled,true);
  assert.equal(h.host.querySelector('[data-inbox-action=previous]').disabled,true);
  h.tab('replies');assert.equal(h.host.querySelector('[data-inbox-action=new-reply]').disabled,true);
});

test('a failed save preserves the draft and never retries automatically',async t=>{
  const h=await setup({respond(path,method){if(method==='POST')throw Error('Salvar indisponível');}});t.after(()=>h.close());
  h.tab('replies');h.click('new-reply');
  h.host.querySelector('[name=title]').value='Rascunho preservado';h.host.querySelector('[name=text]').value='Conteúdo';
  h.host.querySelector('form[data-inbox-form=reply]').dispatchEvent(new h.window.Event('submit',{bubbles:true,cancelable:true}));await flush();
  assert.equal(h.host.querySelector('[name=title]').value,'Rascunho preservado');
  assert.equal(h.host.querySelector('[name=text]').value,'Conteúdo');
  assert.equal(h.calls.filter(x=>x.method==='POST').length,1);
  assert.match(h.host.textContent,/Salvar indisponível/);
});

test('a prepared reply explicitly remains unsent and uses the current stored revision',async t=>{
  const h=await setup({respond(path,method,body){if(path.endsWith('/preview')){assert.equal(body.expected_version,2);return {text:'Olá Cliente sintético',sent:false};}}});t.after(()=>h.close());
  h.host.querySelector('[data-inbox-customer]').click();await flush();
  h.host.querySelector('#inboxPreparedReply').value=reply;h.click('preview');await flush();
  assert.equal(h.host.querySelector('#inboxPreparedText').value,'Olá Cliente sintético');
  assert.match(h.host.textContent,/Nenhuma mensagem enviada/);
  assert.equal(h.calls.some(x=>/\/send/.test(x.path)),false);
});

test('a late response from a prior company cannot repopulate the current company',async t=>{
  const pending=deferred();let block=false;
  const h=await setup({respond(path){if(block&&path.includes(tenantA)&&path.endsWith('/capabilities'))return pending.promise;}});t.after(()=>h.close());
  block=true;const old=h.mount(tenantA);await h.mount(tenantB);
  pending.resolve({enabled:false});await old;await flush();
  assert.ok(h.host.querySelector('[data-inbox-tab=customers][aria-selected=true]'));
  assert.doesNotMatch(h.host.textContent,/preparação/);
});

test('changing company during a save releases the old screen and a late result cannot unlock the new save',async t=>{
  const first=deferred(),second=deferred();
  const h=await setup({respond(path,method){if(method==='POST')return path.includes(tenantA)?first.promise:second.promise;}});t.after(()=>h.close());
  const submit=()=>{h.tab('replies');h.click('new-reply');h.host.querySelector('[name=title]').value='Rascunho';h.host.querySelector('[name=text]').value='Texto';h.host.querySelector('[data-inbox-form=reply]').dispatchEvent(new h.window.Event('submit',{bubbles:true,cancelable:true}));};
  submit();await flush();assert.equal(h.host.inert,true);
  await h.mount(tenantB);assert.equal(h.host.inert,false);
  submit();await flush();assert.equal(h.host.inert,true);
  first.resolve({id:reply,version:1});await flush();assert.equal(h.host.inert,true);
  second.resolve({id:reply,version:1});await flush();assert.equal(h.host.inert,false);
  const writes=h.calls.filter(x=>x.method==='POST');assert.equal(writes.length,2);
  assert.ok(writes[0].path.includes(tenantA));assert.ok(writes[1].path.includes(tenantB));
});
