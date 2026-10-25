import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {JSDOM,VirtualConsole} from 'jsdom';
import {flush} from './harness.mjs';

const tenant='41000000-0000-4000-8000-000000000001';
const channel='42000000-0000-4000-8000-000000000001';
const contact='43000000-0000-4000-8000-000000000001';
const account='44000000-0000-4000-8000-000000000001';
const tag='45000000-0000-4000-8000-000000000001';
const reply='46000000-0000-4000-8000-000000000001';
const phone='+5567999999901',prefix='/api/v1/tenants/'+tenant+'/management/inbox';
const active={channel_id:channel,contact_e164:phone,name:'Cliente de teste',provider:'qr',mode:'human',unread_count:6,
  preview:'Preciso de outro horário',message_count:22,updated_at:'2026-10-25T12:00:00Z'};
const history=Array.from({length:20},(_,i)=>({id:randomUUID(),start_at:'2026-10-'+String(25-Math.floor(i/2)).padStart(2,'0')+'T12:00:00Z',
  status:i%3?'completed':'cancelled',services:i%3?'Corte e barba':'Corte',professional:'Alex',protocol:'AG-'+i}));
async function fixture(t,{write=true,tools=true,central=true,accountEnabled=true,custom}={}){
  const virtualConsole=new VirtualConsole(),errors=[];virtualConsole.on('jsdomError',e=>errors.push(e.message));
  const dom=new JSDOM('<!doctype html><html lang="pt-BR" data-theme="light"><body><main id="host"></main></body></html>',
    {url:'https://orion.test/panel',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole});
  const {window}=dom,calls=[];Object.defineProperty(window.crypto,'randomUUID',{value:randomUUID,configurable:true});
  window.confirm=()=>true;
  window.navigator.clipboard={writeText:async()=>{}};
  for(const f of ['orion-conversations.js','orion-inbox.js'])
    window.eval(await readFile(new URL('../app/static/'+f,import.meta.url),'utf8'));
  let assigned=false;
  const request=async(path,method='GET',payload)=>{
    calls.push({path,method,payload});const override=await custom?.(path,method,payload);if(override!==undefined)return override;
    if(path===prefix+'/capabilities')return {enabled:tools,central_enabled:central};
    if(path===prefix+'/channels')return {account_id:accountEnabled?account:null,send_enabled:true,items:[{id:channel,provider:'qr',label:'WhatsApp QR',enabled:true}]};
    if(path.includes('/conversations?'))return {items:[active],has_more:false};
    if(path.includes('/channels/'+channel+'/messages?'))return {customer_id:contact,
      control:{mode:'human',revision:5,assigned_account_id:accountEnabled?account:null,pending_operation:null},
      items:[{id:randomUUID(),direction:'in',kind:'text',source:'customer',body:'Olá, quero agendar',status:'received',created_at:'2026-10-25T12:00:00Z'}],
      has_more:false,preserved_history_available:true};
    if(path.endsWith('/labels'))return {items:[{id:tag,name:'Cliente recorrente',version:1,color:'#3183dd'}]};
    if(path.endsWith('/quick-replies'))return {items:[{id:reply,title:'Boas-vindas',category:'Saudação',text:'Olá {{cliente.nome}}',version:3,visibility:'company'}]};
    if(path.includes('/customers?'))return {items:[{id:contact,name:'Cliente de teste',phone}],has_more:false};
    if(path.endsWith('/customers/'+contact))return {customer:{id:contact,name:'Cliente de teste',whatsapp_e164:phone,notes:'Preferência por corte de cabelo'},
      labels:assigned?[{id:tag}]:[],history,history_limit:20,timezone:'America/Campo_Grande',upcoming:null};
    if(path.includes('/communication/pending?'))return {items:[]};
    if(path.endsWith('/customers/'+contact+'/labels/'+tag)&&method==='PUT'){assigned=true;return {assigned:true};}
    if(path.endsWith('/quick-replies/'+reply+'/preview')&&method==='POST')return {text:'Olá Cliente de teste',sent:false};
    throw Error('Unexpected synthetic endpoint '+method+' '+path);
  };
  const host=window.document.querySelector('#host');
  await window.OrionInbox.mount({host,tenantId:tenant,request,canWrite:write});await flush();
  t.after(()=>{window.OrionInbox.unmount();dom.window.close();});
  return {window,host,calls,errors,click:s=>{const x=host.querySelector(s);assert.ok(x,'not found '+s);x.click();return x;},
    submit:s=>{const form=host.querySelector(s);assert.ok(form);form.dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));},
    fill:(s,v)=>{const x=host.querySelector(s);assert.ok(x);x.value=v;x.dispatchEvent(new window.Event('input',{bubbles:true}));return x;}};
}

test('central always exposes simple tabs, with no popover covering the conversations',async t=>{
  const h=await fixture(t);
  assert.deepEqual([...h.host.querySelectorAll('.os-inbox-primary-nav [role=tab]')].map(x=>x.textContent),
    ['Conversas','Clientes','Respostas rápidas','Simulações']);
  assert.equal(h.host.querySelector('.os-inbox-more'),null);
  assert.equal(h.host.querySelector('.os-central-filters').open,false);
  assert.ok(h.host.querySelector('#centralSearch'));
  assert.equal(h.host.querySelectorAll('.os-central-thread-item-button').length,1);
  assert.equal(h.host.querySelector('.os-inbox-topbar-title'),null);
  assert.ok(h.calls.every(x=>x.method==='GET'));
  assert.deepEqual(h.errors,[]);
});

test('search, optional filters and clear request exactly one conversation list at a time',async t=>{
  const h=await fixture(t);
  const listings=()=>h.calls.filter(x=>x.path.includes('/conversations?'));
  const before=listings().length;
  h.fill('#centralSearch','corte');h.submit('[data-central-search]');await flush();
  assert.equal(listings().length,before+1,'no duplicate list requests after search');
  assert.match(listings().at(-1).path,/q=corte/);
  h.click('.os-central-filters summary');assert.equal(h.host.querySelector('.os-central-filters').open,true);
  h.host.querySelector('[name=mode]').value='ai';h.submit('[data-central-search]');await flush();
  assert.match(listings().at(-1).path,/mode=ai/);
  h.click('[data-central-action=clear-filters]');await flush();
  assert.doesNotMatch(listings().at(-1).path,/mode=ai|q=corte/);
  assert.ok(h.calls.every(x=>x.method==='GET'));
  assert.deepEqual(h.errors,[]);
});

test('CRM opens one compact customer, keeps twenty history entries collapsed and allows returning',async t=>{
  const h=await fixture(t);
  h.click('[data-inbox-tab=customers]');await flush();
  assert.ok(h.host.querySelector('.os-inbox-subnav'));
  assert.equal(h.host.querySelector('.os-inbox-label-list'),null,'labels do not crowd the client list');
  h.click('[data-inbox-customer="'+contact+'"]');await flush();
  assert.ok(h.host.querySelector('.os-inbox-grid.os-inbox-show-customer'));
  const details=h.host.querySelector('details.os-inbox-appointments');
  assert.ok(details&&!details.open);
  assert.equal(details.querySelectorAll('ol li').length,20);
  details.querySelector('summary').click();assert.equal(details.open,true);
  assert.match(details.textContent,/Não compareceu|Cancelado|Concluído/);
  assert.ok(h.host.querySelector('[data-inbox-action=customer-back]'));
  h.click('[data-inbox-action=customer-back]');await flush();
  assert.equal(h.host.querySelector('.os-inbox-show-customer'),null);
  assert.equal(h.calls.filter(x=>x.method!=='GET').length,0);
  assert.deepEqual(h.errors,[]);
});

test('CRM tags are managed only after opening the separate tags view',async t=>{
  const h=await fixture(t);
  h.click('[data-inbox-tab=customers]');await flush();
  h.click('[data-inbox-view=labels]');await flush();
  assert.ok(h.host.querySelector('.os-inbox-label-list'));
  assert.ok(h.host.querySelector('[data-inbox-action=new-label]'));
  h.click('[data-inbox-view=customers]');await flush();
  h.click('[data-inbox-customer="'+contact+'"]');await flush();
  const toggle=h.host.querySelector('[data-inbox-label="'+tag+'"]');
  assert.equal(toggle.checked,false);toggle.checked=true;toggle.dispatchEvent(new h.window.Event('change',{bubbles:true}));
  await flush();
  assert.equal(h.calls.filter(x=>x.method==='PUT').length,1);
  assert.equal(h.host.querySelector('[data-inbox-label="'+tag+'"]').checked,true);
  assert.deepEqual(h.errors,[]);
});

test('quick reply preview prepares text without sending or unexpectedly creating customers',async t=>{
  const h=await fixture(t);
  h.click('[data-inbox-tab=customers]');await flush();
  h.click('[data-inbox-customer="'+contact+'"]');await flush();
  h.host.querySelector('#inboxPreparedReply').value=reply;
  h.click('[data-inbox-action=preview]');await flush();
  assert.equal(h.host.querySelector('#inboxPreparedText').value,'Olá Cliente de teste');
  const posts=h.calls.filter(x=>x.method==='POST');
  assert.equal(posts.length,1);
  assert.deepEqual(Object.keys(posts[0].payload).sort(),['customer_id','expected_version']);
  assert.match(posts[0].path,/preview$/);
  assert.equal(h.calls.filter(x=>x.method==='PUT'||x.method==='DELETE').length,0);
  assert.deepEqual(h.errors,[]);
});

test('reader retains browsing/history but never sees enabled editing or sending controls',async t=>{
  const h=await fixture(t,{write:false,accountEnabled:false});
  h.click('[data-inbox-tab=customers]');await flush();
  h.click('[data-inbox-customer="'+contact+'"]');await flush();
  assert.equal(h.host.querySelector('[data-inbox-label]').disabled,true);
  h.click('[data-inbox-action=customer-back]');await flush();
  h.click('[data-inbox-view=labels]');await flush();
  assert.equal(h.host.querySelector('[data-inbox-action=new-label]').disabled,true);
  h.click('[data-inbox-tab=replies]');await flush();
  assert.equal(h.host.querySelector('[data-inbox-action=new-reply]').disabled,true);
  h.click('[data-inbox-tab=conversations]');await flush();
  assert.match(h.host.textContent,/administrador precisa estar habilitado/i);
  assert.ok(h.calls.every(x=>x.method==='GET'));
  assert.deepEqual(h.errors,[]);
});

test('CSS has token-driven overflow-safe mobile states and native focus',async()=>{
  const css=await readFile(new URL('../app/static/orion-inbox.css',import.meta.url),'utf8');
  for(const name of ['os-inbox-nav-tab','os-inbox-subnav','os-inbox-show-customer','os-inbox-appointments','os-central-filters','os-central-search-top'])
    assert.ok(css.includes(name),name);
  assert.match(css,/@media\(max-width:767px\)/);
  assert.match(css,/@container inbox \(max-width:760px\)/);
  assert.match(css,/var\(--os-border\)/);
  assert.match(css,/var\(--os-tint\)/);
  assert.match(css,/focus-visible/);
});
