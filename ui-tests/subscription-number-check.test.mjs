import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM,VirtualConsole} from 'jsdom';

const source=await readFile(new URL('../app/static/orion-subscription-number-check.js',import.meta.url),'utf8');
const tenant='00000000-0000-4000-8000-000000000118',device='00000000-0000-4000-8000-000000000119';
const base='/api/v1/tenants/'+tenant+'/subscription-whatsapp/number-check';
const options={devices:[{id:device,name:'Dispositivo de teste',available:true,status:'connected'}],limits:{per_request:5,per_hour:30}};
const flush=async()=>{await new Promise(resolve=>setImmediate(resolve));};
function change(window,node,value){
  if(node.type==='checkbox')node.checked=Boolean(value);else node.value=value;
  node.dispatchEvent(new window.Event(node.type==='checkbox'||node.tagName==='SELECT'?'change':'input',{bubbles:true}));
}
async function setup(t,{response,opts=options,canWrite=true,canAccess=()=>true}={}){
  const errors=[],calls=[],virtualConsole=new VirtualConsole();
  virtualConsole.on('jsdomError',error=>errors.push(error));
  const dom=new JSDOM('<!doctype html><html><body><main id="app"><section id="host"></section></main></body></html>',{
    url:'https://orion.test/painel?aba=verificador-numeros',runScripts:'outside-only',
    pretendToBeVisual:true,virtualConsole
  });
  t.after(()=>dom.window.close());
  const {window}=dom,host=window.document.querySelector('#host'),external=[];
  window.OrionFields={refresh:()=>{}};
  window.fetch=(...args)=>{external.push(args);throw Error('external forbidden');};
  window.eval(source);
  const request=async (path,method,data)=>{
    calls.push({path,method,data});
    if(method==='GET'&&path===base+'/options')return structuredClone(opts);
    if(method==='POST'&&path===base+'/check'){
      if(response)return response(data);
      return {device_id:device,total:data.phones.length,items:data.phones.map(phone=>({phone,status:'unknown'}))};
    }
    throw Error('unexpected_request');
  };
  await window.OrionSubscriptionNumberCheck.mount({
    host,request,tenantId:tenant,canWrite:()=>canWrite,canAccess
  });
  const field=name=>host.querySelector('[data-nc-'+name+']');
  return {window,host,field,calls,errors,external,flush};
}
function submit(h){
  h.host.querySelector('[data-nc-form]').dispatchEvent(new h.window.Event('submit',{bubbles:true,cancelable:true}));
}
test('initialization reads only existing device, uses Orion native pattern and does not contact provider',async t=>{
  const h=await setup(t);
  assert.deepEqual(h.calls.map(x=>[x.method,x.path]),[['GET',base+'/options']]);
  assert.equal(h.host.querySelector('.os-title-row h1').textContent,'Verificador de números WhatsApp');
  assert.equal(h.field('device').value,device);
  assert.ok(h.host.querySelector('.os-card.os-form'));
  assert.equal(h.field('check').disabled,true);
  assert.deepEqual(h.errors,[]);
  assert.deepEqual(h.external,[]);
});
test('single explicit consent, five E164 numbers max, and zero sends',async t=>{
  const h=await setup(t);
  change(h.window,h.field('phones'),'+5567999999901\n+595991234567');
  submit(h);await h.flush();assert.equal(h.calls.length,1);
  change(h.window,h.field('confirm'),true);
  assert.equal(h.field('check').disabled,false);
  submit(h);await h.flush();
  assert.deepEqual(h.calls.at(-1),{path:base+'/check',method:'POST',data:{
    device_id:device,phones:['+5567999999901','+595991234567'],confirmed_authorization:true}});
  assert.equal(h.host.querySelectorAll('[data-nc-results] tbody tr').length,2);
  assert.match(h.host.querySelector('[data-nc-results]').textContent,/Não confirmado/);
  assert.deepEqual(h.external,[]);assert.deepEqual(h.errors,[]);
});
test('duplicate, malformed, oversized and unauthorized lists never call endpoint',async t=>{
  const h=await setup(t);change(h.window,h.field('confirm'),true);
  for(const value of [
    '123','+0123456789','+5567999999901\n+5567999999901',
    '+5567999999901\n+5567999999902\n+5567999999903\n+5567999999904\n+5567999999905\n+5567999999906'
  ]){
    change(h.window,h.field('phones'),value);
    submit(h);await h.flush();
    assert.equal(h.calls.length,1);
    assert.ok(h.host.querySelector('[role="alert"]'));
  }
});
test('statuses returned by producer are rendered accurately, while bad shape is rejected',async t=>{
  const h=await setup(t,{response:async data=>({device_id:device,total:2,items:[
    {phone:data.phones[0],status:'registered'},
    {phone:data.phones[1],status:'not_registered'}
  ]})});
  change(h.window,h.field('phones'),'+5567999999901\n+595991234567');
  change(h.window,h.field('confirm'),true);submit(h);await h.flush();
  const badges=[...h.host.querySelectorAll('[data-nc-results] .os-badge')];
  assert.deepEqual(badges.map(n=>n.textContent),['Cadastrado','Não cadastrado']);
  assert.deepEqual(badges.map(n=>n.classList.contains('os-success')), [true,false]);
});
test('disconnected device and read-only owner cannot trigger request',async t=>{
  const h=await setup(t,{opts:{...options,devices:options.devices.map(row=>({...row,available:false,status:'disconnected'}))}});
  assert.equal(h.field('device').value,'');assert.equal(h.field('check').disabled,true);
  change(h.window,h.field('phones'),'+5567999999901');
  change(h.window,h.field('confirm'),true);submit(h);await h.flush();
  assert.equal(h.calls.length,1);
});
test('malicious device names cannot insert HTML and leaving view revokes operations',async t=>{
  let allowed=true;
  const h=await setup(t,{canAccess:()=>allowed,opts:{...options,devices:[
    {...options.devices[0],name:'<img src=x onerror=alert(1)>'}]}});
  assert.equal(h.host.querySelector('img'),null);
  assert.match(h.field('device').textContent,/<img/);
  allowed=false;change(h.window,h.field('phones'),'+5567999999901');
  submit(h);await h.flush();assert.equal(h.calls.length,1);
});
