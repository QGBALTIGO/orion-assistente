import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM,VirtualConsole} from 'jsdom';
import {flush,deferred,tenantA,tenantB} from './harness.mjs';

async function setup(t,{canWrite=true,enabled=true,central=true,respond,conversationModule=true}={}){
  const console=new VirtualConsole(),errors=[];console.on('jsdomError',e=>errors.push(e.message));
  const dom=new JSDOM('<main id="host"></main>',{url:'https://orion.test',runScripts:'outside-only',virtualConsole:console});
  const {window}=dom,calls=[],mounts=[];let unmounts=0;
  if(conversationModule)window.OrionConversations={
    async mount(options){mounts.push(options);options.host.innerHTML='<h2>Conversas</h2>';},
    unmount(){unmounts++;}
  };
  window.eval(await readFile(new URL('../app/static/orion-inbox.js',import.meta.url),'utf8'));
  const request=async(path,method='GET',body)=>{
    calls.push({path,method,body});const custom=await respond?.(path,method,body);if(custom!==undefined)return custom;
    assert.equal(method,'GET','the wrapper never writes');
    assert.match(path,/\/management\/inbox\/capabilities$/,'the wrapper never loads removed tools');
    return {enabled,central_enabled:central};
  };
  const host=window.document.querySelector('#host');
  const mount=(id=tenantA)=>window.OrionInbox.mount({host,request,tenantId:id,canWrite});
  t.after(()=>{window.OrionInbox.unmount();window.close();});
  await mount();
  return {window,host,calls,mount,mounts,errors,unmounts:()=>unmounts};
}

test('Central opens only Conversations without obsolete or hidden destinations',async t=>{
  const h=await setup(t);
  assert.ok(h.host.querySelector('.os-inbox-conversations-only [data-inbox-central]'));
  assert.equal(h.host.querySelector('nav,[role=tablist],[data-inbox-tab],[data-inbox-flows],[data-inbox-customer]'),null);
  assert.doesNotMatch(h.host.textContent,/Clientes|Respostas rápidas|Simulações/);
  assert.equal(h.mounts.length,1);
  const mounted=h.mounts[0];
  assert.equal(mounted.tenantId,tenantA);
  assert.equal(mounted.canWrite,true);
  assert.equal(mounted.toolsEnabled,false);
  assert.equal(mounted.onCustomer,undefined,'removed CRM is not an invisible navigation target');
  assert.equal(h.calls.length,1);
  assert.equal(h.host.hasAttribute('aria-busy'),false);
  assert.deepEqual(h.errors,[]);
});

for(const central of [false,undefined,null,1,'true']){
  test('only the explicit central capability enables Conversations: '+String(central),async t=>{
    const h=await setup(t,{enabled:true,central,respond:()=>({enabled:true,central_enabled:central})});
    assert.match(h.host.textContent,/Conversas indisponíveis/);
    assert.equal(h.mounts.length,0);
    assert.equal(h.calls.length,1);
    assert.equal(h.host.querySelector('[data-inbox-central]'),null);
    assert.equal(h.host.querySelector('[data-inbox-tab]'),null);
  });
}

test('old CRM flag does not gate Conversations or grant editing permissions',async t=>{
  const h=await setup(t,{enabled:false,canWrite:false});
  assert.equal(h.mounts.length,1);
  assert.equal(h.mounts[0].canWrite,false);
  assert.equal(h.mounts[0].toolsEnabled,false);
  assert.ok(h.calls.every(x=>x.method==='GET'));
});

test('capability failure is escaped and retry remains a single read',async t=>{
  let fail=true,slow=false;const pending=deferred();
  const h=await setup(t,{respond(){if(fail)throw Error('<img src=x onerror=alert(1)>');if(slow)return pending.promise;}});
  assert.match(h.host.querySelector('[role=alert]').textContent,/<img src=x/);
  assert.equal(h.host.querySelector('img'),null);
  fail=false;slow=true;
  const retry=h.host.querySelector('[data-inbox-action=retry]');retry.click();retry.click();await flush();
  assert.equal(h.calls.length,2,'retry cannot duplicate a pending capability request');
  assert.equal(h.host.getAttribute('aria-busy'),'true');
  pending.resolve({central_enabled:true});await flush();
  assert.equal(h.mounts.length,1);
  assert.equal(h.host.hasAttribute('aria-busy'),false);
  assert.ok(h.calls.every(x=>x.method==='GET'));
});

test('a missing conversations module never falls back to removed CRM tools',async t=>{
  const h=await setup(t,{conversationModule:false});
  assert.match(h.host.querySelector('[role=alert]').textContent,/abrir as conversas/);
  assert.ok(h.host.querySelector('[data-inbox-action=retry]'));
  assert.equal(h.calls.length,1);
  assert.equal(h.host.querySelector('[data-inbox-tab]'),null);
});

test('a late capability response cannot mount the previous company',async t=>{
  const pending=deferred();let block=false;
  const h=await setup(t,{respond(path){if(block&&path.includes(tenantA))return pending.promise;}});
  block=true;const old=h.mount(tenantA);await h.mount(tenantB);
  const count=h.mounts.length;
  pending.resolve({central_enabled:true,enabled:true});await old;await flush();
  assert.equal(h.mounts.length,count);
  assert.equal(h.mounts.at(-1).tenantId,tenantB);
  assert.equal(h.host.hasAttribute('aria-busy'),false);
  assert.ok(h.unmounts()>=2);
});

test('unmount prevents late mounting and releases the old busy state',async t=>{
  const pending=deferred();let block=false;
  const h=await setup(t,{respond(){if(block)return pending.promise;}});
  block=true;const loading=h.mount();assert.equal(h.host.getAttribute('aria-busy'),'true');
  h.window.OrionInbox.unmount();
  pending.resolve({central_enabled:true});await loading;await flush();
  assert.equal(h.mounts.length,1);
  assert.equal(h.host.hasAttribute('aria-busy'),false);
  assert.equal(h.host.inert,false);
});
