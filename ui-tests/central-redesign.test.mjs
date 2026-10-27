import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {JSDOM} from 'jsdom';
import {flush} from './harness.mjs';

const channel='20000000-0000-4000-8000-000000000001';
const customer='10000000-0000-4000-8000-000000000001';
const account='30000000-0000-4000-8000-000000000001';
const phone='+5567999999901';
const tenant='40000000-0000-4000-8000-000000000001';
const reply='50000000-0000-4000-8000-000000000001';
const prefix='/api/v1/tenants/'+tenant+'/management/inbox';
const one={id:customer,name:'Cliente sintético',phone};
const list=[{channel_id:channel,contact_e164:phone,name:'Ana de Teste',provider:'qr',mode:'human',unread_count:2,preview:'Preciso de horário amanhã',message_count:3,updated_at:'2026-10-25T14:00:00Z'}];
async function setup({write=true,tools=true,mode='human',assigned=account}={}){
  const dom=new JSDOM('<main id="host"></main>',{url:'https://orion.test',runScripts:'outside-only'});
  const {window}=dom,calls=[];
  Object.defineProperty(window.crypto,'randomUUID',{value:randomUUID,configurable:true});
  for(const file of ['orion-conversations.js','orion-inbox.js'])
    window.eval(await readFile(new URL('../app/static/'+file,import.meta.url),'utf8'));
  const request=async(path,method='GET',body)=>{
    calls.push({path,method,body});
    if(method!=='GET')throw Error('Unexpected mutation from a synthetic UI');
    if(path===prefix+'/capabilities')return {enabled:tools,central_enabled:true};
    if(path===prefix+'/channels')return {account_id:account,send_enabled:true,items:[{id:channel,provider:'qr',label:'Canal de testes',enabled:true}]};
    if(path.startsWith(prefix+'/conversations?'))return {items:list,has_more:false};
    if(path.includes('/channels/'+channel+'/messages?'))return {customer_id:customer,control:{mode,revision:2,assigned_account_id:assigned,pending_operation:null},items:[{id:randomUUID(),direction:'in',source:'customer',kind:'text',body:'Olá! Gostaria de marcar.',status:'received',created_at:'2026-10-25T14:00:00Z'}],has_more:false,preserved_history_available:true};
    if(path===prefix+'/quick-replies')return {items:[{id:reply,title:'Bom dia',category:'Saudações',version:1,text:'Olá {{cliente.nome}}'}]};
    if(path===prefix+'/labels')return {items:[{id:'label',name:'Frequente',color:'#2e7bf6',version:1}]};
    if(path.includes('/customers?'))return {items:[one],has_more:false};
    if(path.endsWith('/customers/'+customer))return {customer:{id:customer,name:'Ana de Teste',whatsapp_e164:phone},labels:[],history:[],history_limit:20,timezone:'America/Campo_Grande'};
    if(path.includes('/communication/pending?'))return {items:[]};
    throw Error('Unexpected endpoint '+path);
  };
  const host=window.document.getElementById('host');
  await window.OrionInbox.mount({host,request,tenantId:tenant,canWrite:write});
  await flush();
  return {window,host,calls,close(){window.OrionInbox.unmount();window.close();}};
}
test('central launches with a tidy conversation-first workspace, not an administration dashboard',async t=>{
  const h=await setup();t.after(()=>h.close());
  assert.ok(h.host.querySelector('.os-central-shell'));
  assert.ok(h.host.querySelector('.os-central-list-panel'));
  assert.ok(h.host.querySelector('.os-central-thread-panel'));
  assert.ok(h.host.querySelector('.os-central-placeholder'));
  assert.ok(h.host.querySelector('.os-inbox-conversations-only'));
  assert.equal(h.host.querySelector('.os-inbox-more'),null,'menu sobreposto não existe');
  assert.equal(h.host.querySelector('[role=tablist],[data-inbox-tab]'),null,'one destination needs no tab bar');
  assert.equal(h.host.querySelector('.os-central-filters').open,false,'filtros começam recolhidos');
  assert.equal(h.host.querySelectorAll('.os-central-thread-item-button').length,1);
  assert.equal(h.calls.some(x=>x.method!=='GET'),false);
  assert.equal(h.calls.some(x=>/\/labels|\/quick-replies|\/customers|\/workflows/.test(x.path)),false,'removed tools do not load in the background');
});
test('selected conversation shows relevant actions while hiding irrelevant ones',async t=>{
  const h=await setup();t.after(()=>h.close());
  h.host.querySelector('[data-central-phone]').click();await flush();
  assert.equal(h.host.querySelector('.os-central-selected-copy h2').textContent,'Ana de Teste');
  assert.match(h.host.querySelector('.os-central-messages').textContent,/Gostaria de marcar/);
  assert.equal(h.host.querySelector('[data-central-action=mode-human]').hidden,true);
  assert.equal(h.host.querySelector('[data-central-action=mode-ai]').hidden,false);
  assert.equal(h.host.querySelector('[data-central-action=mode-waiting]').hidden,false);
  assert.equal(h.host.querySelector('[data-central-compose] button').disabled,false);
  assert.ok(h.host.querySelector('.os-central-history-tools'));
  assert.equal(h.calls.filter(x=>x.method!=='GET').length,0);
});
test('read-only operators never gain controls or send permissions from the redesign',async t=>{
  const h=await setup({write:false,assigned:null});t.after(()=>h.close());
  h.host.querySelector('[data-central-phone]').click();await flush();
  for(const mode of ['human','ai','paused','waiting','closed']){
    const b=h.host.querySelector('[data-central-action=mode-'+mode+']');
    assert.ok(b,mode);assert.equal(b.disabled,true,mode);
  }
  assert.equal(h.host.querySelector('[data-central-compose] button').disabled,true);
  assert.equal(h.calls.some(x=>x.method!=='GET'),false);
});
test('mobile back navigation returns directly to Conversations without extra destinations',async t=>{
  const h=await setup();t.after(()=>h.close());
  h.host.querySelector('[data-central-phone]').click();await flush();
  assert.ok(h.host.querySelector('.os-central-shell.os-central-show-thread'));
  h.host.querySelector('[data-central-action=back]').click();await flush();
  assert.equal(h.host.querySelector('.os-central-shell.os-central-show-thread'),null);
  assert.ok(h.host.querySelector('#centralSearch'));
  assert.equal(h.host.querySelector('[data-inbox-tab],[data-central-action=customer],.os-central-quick-reply'),null);
  assert.equal(h.calls.some(x=>/\/labels|\/quick-replies|\/customers|\/workflows/.test(x.path)),false);
  assert.equal(h.calls.some(x=>x.method!=='GET'),false);
});
// Actual geometry, touch targets and collapsed-panel visibility are checked by
// central-layout.py and central-mobile-browser.py in Chromium and WebKit.
