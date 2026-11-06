import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM,VirtualConsole} from 'jsdom';

const source=await readFile(new URL('../app/static/orion-subscription-whatsapp-link.js',import.meta.url),'utf8');
const tenant='00000000-0000-4000-8000-000000000119';
const base='/api/v1/tenants/'+tenant+'/subscription-whatsapp/whatsapp-link';
const phone='+5567999999901';
const PNG='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9XQIyAAAAABJRU5ErkJggg==';
const flush=async()=>new Promise(resolve=>setImmediate(resolve));
async function setup(t,{response,canAccess=()=>true,canWrite=true}={}){
  const errors=[],calls=[],external=[],virtualConsole=new VirtualConsole();
  virtualConsole.on('jsdomError',error=>errors.push(error));
  const dom=new JSDOM('<!doctype html><html lang="pt-BR"><body><section id="host"></section></body></html>',{
    url:'https://orion.test/painel?aba=link-qr-whatsapp',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole});
  t.after(()=>dom.window.close());
  const {window}=dom,host=window.document.querySelector('#host');
  window.TextEncoder=TextEncoder;
  window.fetch=(...args)=>{external.push(args);throw Error('outbound forbidden');};
  window.OrionFields={refresh(){}};
  window.eval(source);
  const request=async(path,method,data)=>{
    calls.push({path,method,data});
    if(path!==base+'/generate'||method!=='POST')throw Error('unexpected_request');
    return response?response(data):{url:'https://wa.me/'+data.phone.slice(1)+
      (data.message?'?text='+encodeURIComponent(data.message):''),image_base64:PNG,
      format:'png',mime_type:'image/png'};
  };
  await window.OrionSubscriptionWhatsAppLink.mount({host,tenantId:tenant,request,canAccess,canWrite:()=>canWrite});
  return {window,host,calls,errors,external,
    field:name=>host.querySelector('[data-wl-'+name+']'),
    fill(name,value){const node=host.querySelector('[data-wl-'+name+']');node.value=value;
      node.dispatchEvent(new window.Event('input',{bubbles:true}));},
    submit(){host.querySelector('[data-wl-form]').dispatchEvent(
      new window.Event('submit',{bubbles:true,cancelable:true}));},flush};
}
test('mount uses Orion title, existing form styles, and no connection or HTTP call',async t=>{
  const h=await setup(t);
  assert.equal(h.host.querySelector('.os-title-row h1').textContent,'Link e QR do WhatsApp');
  assert.ok(h.host.querySelector('.os-card.os-form'));
  assert.deepEqual(h.calls,[]);
  assert.deepEqual(h.external,[]);
  assert.deepEqual(h.errors,[]);
});
test('explicit generate constructs exact request; QR is local PNG and copy action only on click',async t=>{
  const h=await setup(t);
  const copies=[];
  Object.defineProperty(h.window.navigator,'clipboard',{value:{writeText:async text=>{copies.push(text);}}});
  h.fill('phone',phone);h.fill('message','Olá, gostaria de informações');
  assert.equal(h.calls.length,0);
  h.submit();await h.flush();
  assert.deepEqual(h.calls,[{path:base+'/generate',method:'POST',data:{
    phone,message:'Olá, gostaria de informações'}}]);
  assert.match(h.field('output').value,/^https:\/\/wa\.me\/5567999999901/);
  assert.ok(h.field('qr').src.startsWith('data:image/png;base64,iVBORw0KGgo'));
  assert.equal(copies.length,0);
  h.field('copy').click();await h.flush();
  assert.deepEqual(copies,[h.field('output').value]);
  assert.match(h.host.textContent,/Link copiado/);
  assert.deepEqual(h.external,[]);assert.deepEqual(h.errors,[]);
});
test('invalid number or overly long message never sends any request',async t=>{
  const h=await setup(t);
  for(const bad of ['5567999999901','+1','+00012345678','https://wa.me/5567']){
    h.fill('phone',bad);h.submit();await h.flush();assert.equal(h.calls.length,0);
  }
  h.fill('phone',phone);
  h.fill('message','🎉'.repeat(301));h.submit();await h.flush();
  assert.equal(h.calls.length,0);
});
test('malicious URL, wrong PNG signature or response for other number is rejected',async t=>{
  for(const result of [
    {url:'javascript:alert(1)',image_base64:PNG,format:'png',mime_type:'image/png'},
    {url:'https://wa.me/5567999999902',image_base64:PNG,format:'png',mime_type:'image/png'},
    {url:'https://wa.me/5567999999901',image_base64:'PHN2Zy8+',format:'png',mime_type:'image/png'},
  ]){
    const h=await setup(t,{response:()=>result});
    h.fill('phone',phone);h.submit();await h.flush();
    assert.equal(h.field('qr'),null);
    assert.ok(h.host.querySelector('[data-wl-error]'));
  }
});
test('editing input removes stale QR and link, and does not store contact data',async t=>{
  const h=await setup(t);h.fill('phone',phone);h.submit();await h.flush();
  assert.ok(h.field('qr'));
  h.fill('message','Mensagem nova');
  assert.equal(h.field('qr'),null);assert.equal(h.field('output'),null);
  assert.equal(h.window.localStorage.length,0);assert.equal(h.window.sessionStorage.length,0);
  assert.equal(h.calls.length,1);
});
test('revocation or read-only role prevents generation',async t=>{
  let active=true;const h=await setup(t,{canAccess:()=>active});
  h.fill('phone',phone);active=false;h.submit();await h.flush();
  assert.equal(h.calls.length,0);
  const readonly=await setup(t,{canWrite:false});
  readonly.fill('phone',phone);readonly.submit();await readonly.flush();
  assert.equal(readonly.calls.length,0);
});
