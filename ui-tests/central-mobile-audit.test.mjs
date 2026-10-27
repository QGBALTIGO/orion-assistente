import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {JSDOM,VirtualConsole} from 'jsdom';
import {flush} from './harness.mjs';

const tenant='41000000-0000-4000-8000-000000000001';
const channel='42000000-0000-4000-8000-000000000001';
const channelB='42000000-0000-4000-8000-000000000002';
const contact='43000000-0000-4000-8000-000000000001';
const account='44000000-0000-4000-8000-000000000001';
const phone='+5567999999901',prefix='/api/v1/tenants/'+tenant+'/management/inbox';
const active={channel_id:channel,contact_e164:phone,name:'Cliente de teste',provider:'qr',mode:'human',unread_count:6,
  preview:'Preciso de outro horário',message_count:22,updated_at:'2026-10-25T12:00:00Z'};

async function fixture(t,{write=true,tools=true,central=true,accountEnabled=true,mode='human',items=[active],custom}={}){
  const console=new VirtualConsole(),errors=[];console.on('jsdomError',e=>errors.push(e.message));
  const dom=new JSDOM('<!doctype html><html lang="pt-BR" data-theme="light"><body><main id="host"></main></body></html>',
    {url:'https://orion.test/panel',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:console});
  const {window}=dom,calls=[],controls=new Map();Object.defineProperty(window.crypto,'randomUUID',{value:randomUUID,configurable:true});
  for(const row of items)controls.set(row.channel_id+':'+row.contact_e164,
    {mode,revision:5,assigned_account_id:mode==='human'&&accountEnabled?account:null,pending_operation:null});
  for(const f of ['orion-conversations.js','orion-inbox.js'])
    window.eval(await readFile(new URL('../app/static/'+f,import.meta.url),'utf8'));
  const request=async(path,method='GET',payload)=>{
    calls.push({path,method,payload});const override=await custom?.(path,method,payload);if(override!==undefined)return override;
    if(path===prefix+'/capabilities')return {enabled:tools,central_enabled:central};
    if(path===prefix+'/channels')return {account_id:accountEnabled?account:null,send_enabled:true,
      items:[...new Set(items.map(x=>x.channel_id))].map(id=>({id,provider:'qr',label:'Canal de teste',enabled:true}))};
    if(path.includes('/conversations?'))return {items:items.map(row=>({...row,mode:controls.get(row.channel_id+':'+row.contact_e164).mode})),has_more:false};
    const conversationChannel=path.match(/\/channels\/([^/]+)\//)?.[1];
    if(path.includes('/messages?')){
      const selectedPhone=new URL(path,'https://orion.test').searchParams.get('phone');
      const key=conversationChannel+':'+selectedPhone;
      assert.ok(controls.has(key),'channel and contact identify the conversation');
      return {customer_id:contact,control:{...controls.get(key)},
        items:[{id:'message-'+conversationChannel,direction:'in',kind:'text',source:'customer',
          body:'Olá, quero agendar',status:'received',created_at:'2026-10-25T12:00:00Z'},
          {id:'reply-'+conversationChannel,direction:'out',kind:'text',source:'assistant',
          body:'Temos horários disponíveis amanhã.',status:'delivered',created_at:'2026-10-25T12:01:00Z'}],
        has_more:false,preserved_history_available:true};
    }
    if(path.endsWith('/control')&&method==='PUT'){
      const control=controls.get(conversationChannel+':'+payload.contact_e164);
      assert.ok(control);assert.equal(payload.expected_revision,control.revision);
      control.mode=payload.mode;control.revision++;
      control.assigned_account_id=['human','waiting'].includes(payload.mode)?account:null;
      return {...control};
    }
    if(path.includes('/preserved-history?'))return {items:[{direction:'in',body:'<img src=x> mensagem preservada',at:1000000}]};
    throw Error('Unexpected synthetic endpoint '+method+' '+path);
  };
  const host=window.document.querySelector('#host');
  await window.OrionInbox.mount({host,tenantId:tenant,request,canWrite:write});await flush();
  t.after(()=>{window.OrionInbox.unmount();dom.window.close();});
  return {window,host,calls,errors,click:s=>{const x=host.querySelector(s);assert.ok(x,'not found '+s);x.click();return x;},
    submit:s=>{const form=host.querySelector(s);assert.ok(form);form.dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));},
    fill:(s,v)=>{const x=host.querySelector(s);assert.ok(x);x.value=v;x.dispatchEvent(new window.Event('input',{bubbles:true}));return x;}};
}

test('the complete Central mounts only the conversation workspace and makes no ancillary requests',async t=>{
  const h=await fixture(t);
  assert.equal(h.host.querySelector('[data-inbox-tab],[role=tablist]'),null);
  assert.equal(h.host.querySelector('.os-central-filters').open,false);
  assert.ok(h.host.querySelector('#centralSearch'));
  assert.equal(h.host.querySelectorAll('.os-central-thread-item-button').length,1);
  h.click('[data-central-phone]');await flush();
  assert.equal(h.host.querySelector('[data-central-action=customer],.os-central-quick-reply'),null);
  assert.ok(h.calls.every(x=>x.method==='GET'));
  assert.equal(h.calls.some(x=>/\/labels|\/customers|\/quick-replies|\/workflows/.test(x.path)),false);
  assert.deepEqual(h.errors,[]);
});

test('search, optional filters and clear request exactly one conversation list at a time',async t=>{
  const h=await fixture(t);
  const listings=()=>h.calls.filter(x=>x.path.includes('/conversations?'));
  const before=listings().length;
  h.fill('#centralSearch','corte');h.submit('[data-central-search]');await flush();
  assert.equal(listings().length,before+1);
  assert.match(listings().at(-1).path,/q=corte/);
  h.click('.os-central-filters summary');assert.equal(h.host.querySelector('.os-central-filters').open,true);
  h.host.querySelector('[name=mode]').value='ai';h.submit('[data-central-search]');await flush();
  assert.match(listings().at(-1).path,/mode=ai/);
  h.click('[data-central-action=clear-filters]');await flush();
  assert.doesNotMatch(listings().at(-1).path,/mode=ai|q=corte/);
  assert.ok(h.calls.every(x=>x.method==='GET'));
  assert.deepEqual(h.errors,[]);
});

test('formatting the visible contact never changes the selected WhatsApp recipient',async t=>{
  const h=await fixture(t,{mode:'ai'});h.click('[data-central-phone]');await flush();
  assert.equal(h.host.querySelector('.os-central-selected-copy p').textContent,'(67) 99999-9901');
  assert.equal(h.host.querySelector('[data-central-phone]').dataset.centralPhone,phone);
  h.click('[data-central-action=mode-human]');await flush();
  const change=h.calls.find(x=>x.method==='PUT');
  assert.equal(change.payload.contact_e164,phone);
  assert.equal(change.payload.expected_revision,5);
  assert.equal(change.payload.mode,'human');
  assert.equal(h.host.querySelector('[data-central-compose] button').disabled,false);
  assert.equal(h.calls.some(x=>x.method==='POST'),false);
});

test('secondary details begin closed, leave the timeline clear and close with Escape or outside click',async t=>{
  const h=await fixture(t);h.click('[data-central-phone]');await flush();
  const details=h.host.querySelector('.os-central-control-menu');
  assert.equal(details.open,false);
  assert.equal(h.host.querySelector('.os-central-thread-scroll .os-central-history-tools'),null);
  assert.ok(details.querySelector('.os-central-history-tools #centralFind'));
  const summary=details.querySelector('summary');
  assert.match(summary.textContent,/Detalhes/);
  summary.click();assert.equal(details.open,true);
  summary.dispatchEvent(new h.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
  assert.equal(details.open,false);
  assert.equal(h.window.document.activeElement,summary);
  summary.click();h.click('.os-central-thread-header');
  assert.equal(details.open,false);
  assert.ok(h.calls.every(x=>x.method==='GET'));
});

test('all management actions retain current revision and never enqueue a message',async t=>{
  const h=await fixture(t);h.click('[data-central-phone]');await flush();
  for(const [next,revision] of [['waiting',5],['paused',6],['ai',7],['closed',8]]){
    h.click('.os-central-control-menu summary');
    h.click('[data-central-action=mode-'+next+']');await flush();
    const changes=h.calls.filter(x=>x.method==='PUT');
    assert.equal(changes.at(-1).payload.mode,next);
    assert.equal(changes.at(-1).payload.expected_revision,revision);
    assert.equal(changes.at(-1).payload.contact_e164,phone);
    assert.equal(h.host.querySelector('.os-central-control-menu').open,false);
  }
  assert.equal(h.calls.filter(x=>x.method==='PUT').length,4);
  assert.equal(h.calls.some(x=>x.method==='POST'),false);
  assert.equal(h.host.querySelector('[data-central-compose] button').disabled,true);
  assert.deepEqual(h.errors,[]);
});

test('drafts remain separate for identical contacts on different channels and survive returning to the list',async t=>{
  const rows=[active,{...active,channel_id:channelB,name:'Cliente de teste'}];
  const h=await fixture(t,{items:rows});
  h.click('[data-central-channel="'+channel+'"]');await flush();
  h.fill('#centralText','Rascunho do primeiro canal');
  h.click('[data-central-action=back]');
  h.click('[data-central-channel="'+channelB+'"]');await flush();
  assert.equal(h.host.querySelector('#centralText').value,'');
  h.fill('#centralText','Rascunho do segundo canal');
  h.click('[data-central-action=back]');
  h.click('[data-central-channel="'+channel+'"]');await flush();
  assert.equal(h.host.querySelector('#centralText').value,'Rascunho do primeiro canal');
  assert.equal(h.window.OrionConversations.hasUnsentDraft(),true);
  assert.ok(h.calls.every(x=>x.method==='GET'));
  assert.deepEqual(h.errors,[]);
});

test('a search from one conversation never hides messages in the next conversation',async t=>{
  const otherPhone='+5567999999902';
  const h=await fixture(t,{items:[active,{...active,contact_e164:otherPhone,name:'Outra cliente'}]});
  h.click('[data-central-phone="'+phone+'"]');await flush();
  h.fill('#centralText','Rascunho da primeira conversa');
  h.click('.os-central-control-menu summary');
  h.fill('#centralFind','agendar');
  assert.equal([...h.host.querySelectorAll('.os-central-message')].filter(x=>!x.hidden).length,1);
  h.click('[data-central-phone="'+otherPhone+'"]');await flush();
  assert.equal(h.host.querySelector('#centralFind').value,'');
  assert.equal(h.host.querySelector('.os-central-search-active').hidden,true);
  assert.equal([...h.host.querySelectorAll('.os-central-message')].filter(x=>!x.hidden).length,2);
  assert.equal(h.host.querySelector('#centralText').value,'');
  h.click('[data-central-phone="'+phone+'"]');await flush();
  assert.equal(h.host.querySelector('#centralText').value,'Rascunho da primeira conversa');
  assert.ok(h.calls.every(x=>x.method==='GET'));
  assert.deepEqual(h.errors,[]);
});

test('refresh retains an unsent draft and the position of an older message',async t=>{
  const h=await fixture(t);h.click('[data-central-phone]');await flush();
  h.fill('#centralText','Rascunho que ainda estou escrevendo');
  // JSDOM has no layout; keep the same measurable viewport after each render.
  Object.defineProperties(h.window.HTMLElement.prototype,{
    scrollHeight:{configurable:true,get(){return this.classList.contains('os-central-thread-scroll')?1200:0;}},
    clientHeight:{configurable:true,get(){return this.classList.contains('os-central-thread-scroll')?300:0;}}
  });
  const timeline=h.host.querySelector('.os-central-thread-scroll');
  timeline.scrollTop=180;
  h.click('[data-central-action=refresh]');await flush();
  assert.equal(h.host.querySelector('#centralText').value,'Rascunho que ainda estou escrevendo');
  assert.equal(h.host.querySelector('.os-central-thread-scroll').scrollTop,180);
  assert.ok(h.calls.every(x=>x.method==='GET'));
});

test('a failed history load keeps an explicit retry and a working return to conversations',async t=>{
  let fail=true;
  const h=await fixture(t,{custom(path){if(fail&&path.includes('/messages?'))throw Error('Histórico temporariamente indisponível');}});
  h.click('[data-central-phone]');await flush();
  assert.match(h.host.querySelector('.os-central-loading').textContent,/temporariamente indisponível/);
  assert.ok(h.host.querySelector('.os-central-loading [data-central-action=refresh]'));
  fail=false;h.click('.os-central-loading [data-central-action=refresh]');await flush();
  assert.equal(h.host.querySelectorAll('.os-central-message').length,2);
  h.click('[data-central-action=back]');
  assert.equal(h.host.querySelector('.os-central-show-thread'),null);
  assert.ok(h.calls.every(x=>x.method==='GET'));
  assert.deepEqual(h.errors,[]);
});

test('reader retains conversation access without editable, sending or transition authority',async t=>{
  const h=await fixture(t,{write:false,accountEnabled:false});h.click('[data-central-phone]');await flush();
  assert.match(h.host.textContent,/administrador precisa estar habilitado/i);
  for(const next of ['human','waiting','ai','paused','closed'])
    assert.equal(h.host.querySelector('[data-central-action=mode-'+next+']').disabled,true);
  assert.equal(h.host.querySelector('#centralText').readOnly,true);
  assert.equal(h.host.querySelector('[data-central-compose] button').disabled,true);
  h.fill('#centralText','Não autorizado');h.submit('[data-central-compose]');await flush();
  assert.ok(h.calls.every(x=>x.method==='GET'));
  assert.deepEqual(h.errors,[]);
});

test('history search and explicit preserved-history access stay local/read-only and escape message text',async t=>{
  const h=await fixture(t);h.click('[data-central-phone]');await flush();
  h.click('.os-central-control-menu summary');
  h.fill('#centralFind','amanhã');
  assert.equal([...h.host.querySelectorAll('.os-central-message')].filter(x=>!x.hidden).length,1);
  h.fill('#centralFind','');
  assert.equal([...h.host.querySelectorAll('.os-central-message')].filter(x=>!x.hidden).length,2);
  assert.equal(h.calls.some(x=>x.path.includes('/preserved-history?')),false);
  h.click('[data-central-action=preserved-history]');await flush();
  assert.match(h.host.textContent,/<img src=x> mensagem preservada/);
  assert.equal(h.host.querySelector('img'),null);
  assert.ok(h.calls.every(x=>x.method==='GET'));
  assert.deepEqual(h.errors,[]);
});
