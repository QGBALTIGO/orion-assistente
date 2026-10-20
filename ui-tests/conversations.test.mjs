import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {JSDOM} from 'jsdom';
import {flush,deferred,tenantA,tenantB} from './harness.mjs';

const channel='20000000-0000-4000-8000-000000000001',account='30000000-0000-4000-8000-000000000001',phone='+5567999999901';
async function setup({write=true,send=true,respond}={}){
  const dom=new JSDOM('<main id="host"></main>',{url:'https://orion.test',runScripts:'outside-only'}),{window}=dom,calls=[];
  Object.defineProperty(window.crypto,'randomUUID',{value:randomUUID});
  window.eval(await readFile(new URL('../app/static/orion-conversations.js',import.meta.url),'utf8'));
  let mode='human',revision=2;
  const request=async(path,method='GET',body)=>{
    calls.push({path,method,body});const custom=await respond?.(path,method,body);if(custom!==undefined)return custom;
    if(path.endsWith('/channels'))return {items:[{id:channel,provider:'qr',label:'QR sintético',enabled:true}],account_id:account,send_enabled:send};
    if(method==='PUT'){mode=body.mode;revision++;return {};}
    if(method==='POST')return {status:'queued'};
    if(path.includes('/conversations?'))return {items:[{channel_id:channel,contact_e164:phone,name:'<img src=x onerror=alert(1)>',mode,revision,provider:'qr',preview:'Olá'}],has_more:false};
    if(path.includes('/messages?'))return {control:{mode,revision,assigned_account_id:mode==='human'?account:null,pending_operation:null},items:[{id:randomUUID(),direction:'in',source:'customer',kind:'text',body:'<script>cliente</script>',status:'received',created_at:'2026-10-20T12:00:00Z'}],has_more:false};
    throw Error('Unexpected request '+path);
  };
  const host=window.document.querySelector('#host'),mount=(id=tenantA)=>window.OrionConversations.mount({host,request,tenantId:id,canWrite:write});
  await mount();host.querySelector('[data-central-phone]').click();await flush();
  return {window,host,calls,mount,close(){window.OrionConversations.unmount();window.close();},compose(value){const t=host.querySelector('textarea');t.value=value;t.dispatchEvent(new window.Event('input',{bubbles:true}));host.querySelector('[data-central-compose]').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));}};
}

test('conversation text is escaped, reader controls are disabled and no send occurs on load',async t=>{
  const h=await setup({write:false});t.after(()=>h.close());
  assert.equal(h.host.querySelector('script,img'),null);
  assert.match(h.host.textContent,/<script>cliente/);
  assert.equal(h.host.querySelector('[data-central-action=mode-human]').disabled,true);
  assert.equal(h.calls.filter(x=>x.method!=='GET').length,0);
});

test('preserved device history is loaded explicitly, escaped and never marks or sends messages',async t=>{
  const h=await setup({respond(path){
    if(path.includes('/messages?'))return {preserved_history_available:true,
      control:{mode:'human',revision:2,assigned_account_id:account,pending_operation:null},items:[],has_more:false};
    if(path.includes('/preserved-history?'))return {items:[{direction:'out',body:'<img src=x onerror=alert(1)>',at:1000000,delivery:null}]};
  }});t.after(()=>h.close());
  assert.equal(h.calls.some(c=>c.path.includes('preserved-history')),false);
  h.host.querySelector('[data-central-action=preserved-history]').click();await flush();
  assert.match(h.host.textContent,/Histórico preservado no dispositivo/);
  assert.match(h.host.textContent,/Sem confirmação de entrega/);
  assert.equal(h.host.querySelector('img'),null);assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
});

test('a response lost after enqueue preserves the same operation for an explicit retry',async t=>{
  let n=0;const h=await setup({respond(path,method){if(method==='POST'&&n++===0)throw Error('Resposta perdida');}});t.after(()=>h.close());
  h.compose('Mensagem revisada');await flush();
  assert.equal(h.host.querySelector('textarea').value,'Mensagem revisada');assert.match(h.host.textContent,/Resposta perdida/);
  h.host.querySelector('[data-central-compose]').dispatchEvent(new h.window.Event('submit',{bubbles:true,cancelable:true}));await flush();
  const sends=h.calls.filter(x=>x.method==='POST');assert.equal(sends.length,2);assert.equal(sends[0].body.operation_id,sends[1].body.operation_id);
  assert.equal(h.host.querySelector('textarea').value,'');
});

test('resuming the AI updates authority and disables the human composer',async t=>{
  const h=await setup();t.after(()=>h.close());h.host.querySelector('[data-central-action=mode-ai]').click();await flush();
  const change=h.calls.find(x=>x.method==='PUT');assert.equal(change.body.expected_revision,2);assert.equal(change.body.mode,'ai');
  assert.equal(h.host.querySelector('[data-central-compose] button').disabled,true);
});

test('an old tenant response cannot repopulate the next tenant or send by itself',async t=>{
  const pending=deferred();let slow=false;
  const h=await setup({respond(path){if(slow&&path.includes(tenantA)&&path.includes('/messages?'))return pending.promise;}});t.after(()=>h.close());
  slow=true;h.host.querySelector('[data-central-action=refresh]').click();await flush();await h.mount(tenantB);
  pending.resolve({control:{mode:'human',revision:999},items:[]});await flush();
  assert.doesNotMatch(h.host.textContent,/999/);assert.equal(h.calls.filter(x=>x.method==='POST').length,0);
});

test('an uncertain slot blocks every operator transition and preserves the result distinction',async t=>{
  const h=await setup({respond(path){if(path.includes('/messages?'))return {control:{mode:'human',revision:2,assigned_account_id:account,pending_operation:randomUUID()},items:[{id:randomUUID(),direction:'out',source:'manual',kind:'text',body:'Mensagem sintética',status:'uncertain',created_at:'2026-10-20T12:00:00Z'}],has_more:false};}});t.after(()=>h.close());
  assert.match(h.host.textContent,/Resultado incerto/);assert.equal(h.host.querySelector('[data-central-action=mode-ai]').disabled,true);
  assert.equal(h.host.querySelector('[data-central-compose] button').disabled,true);
});
