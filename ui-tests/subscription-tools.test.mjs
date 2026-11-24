import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import {JSDOM,VirtualConsole} from 'jsdom';

const tenant='00000000-0000-4000-8000-000000000001',device='00000000-0000-4000-8000-000000000002',group='00000000-0000-4000-8000-000000000003';
const numberSource=await readFile(new URL('../app/static/orion-subscription-number-check.js',import.meta.url),'utf8');
const linkSource=await readFile(new URL('../app/static/orion-subscription-whatsapp-link.js',import.meta.url),'utf8');
const flush=async()=>{for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));};
const options={devices:[{id:device,name:'WhatsApp principal',available:true,status:'connected'}],groups:[{id:group,name:'Clientes',member_count:2}],limits:{numbers_per_check:1000}};
const result=(number='+5567999999901',status='registered',reason=null)=>({device_id:device,group_id:null,items:[{number,status,reason}],total:1,input_total:1,duplicates:0,counts:{registered:status==='registered'?1:0,not_registered:status==='not_registered'?1:0,inconclusive:status==='inconclusive'?1:0,error:status==='error'?1:0}});
function linkResult(payload,{qr=true}={}){const phone=payload.phone.replace(/[ ().-]/g,'').replace(/^00/,'+');return {phone,url:'https://wa.me/'+phone.slice(1)+(payload.message?'?text='+encodeURIComponent(payload.message):''),qr:qr?{svg:'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>',png_base64:'iVBORw0KGgo=',size:165}:null,qr_notice:qr?null:'O link está pronto. Reduza a mensagem para gerar o QR Code.'};}
async function setup(kind,respond){
  const errors=[],calls=[],copies=[],downloads=[],access={allowed:true,writable:true};
  const virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',e=>errors.push(e));
  const dom=new JSDOM('<!doctype html><html lang="pt-BR"><body><main id="host"></main></body></html>',{url:'https://orion.test/painel',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole});
  const {window}=dom,{document}=window;window.OrionFields={refresh(){}};window.fetch=()=>{throw Error('No external request allowed');};
  Object.defineProperty(window.navigator,'clipboard',{value:{async writeText(text){copies.push(text);}}});
  window.URL.createObjectURL=()=> 'blob:https://orion.test/test';window.URL.revokeObjectURL=()=>{};
  document.addEventListener('click',e=>{const a=e.target.closest('a');if(a){e.preventDefault();downloads.push({href:a.href,name:a.download});}});
  window.eval(kind==='number'?numberSource:linkSource);
  const bridge={host:document.querySelector('#host'),tenantId:tenant,canAccess:()=>access.allowed,canWrite:()=>access.writable,async request(path,method='GET',payload,config){calls.push({path,method,payload,signal:config?.signal});if(respond){const value=await respond(path,method,payload);if(value!==undefined)return value;}if(method==='GET')return structuredClone(options);return kind==='number'?result():linkResult(payload);}};
  const controller=(kind==='number'?window.OrionSubscriptionNumberCheck:window.OrionSubscriptionWhatsappLink).mount(bridge);await controller.ready;
  const field=(id,value,event='input')=>{const el=document.querySelector('#'+id);assert.ok(el,id);el.value=value;el.dispatchEvent(new window.Event(event,{bubbles:true}));};
  const submit=async()=>{document.querySelector('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));await flush();};
  return {dom,window,document,controller,bridge,access,calls,copies,downloads,errors,field,submit,close:()=>{controller.destroy();dom.window.close();}};
}

test('checker reads options only on open and keeps formatted phone input together',async()=>{
  const h=await setup('number');try{
    assert.equal(h.calls.length,1);assert.equal(h.calls[0].method,'GET');
    h.field('osNcNumbers','+55 (67) 99999-9901');await h.submit();
    assert.deepEqual([...h.calls[1].payload.numbers],['+55 (67) 99999-9901']);
    assert.equal(h.document.querySelectorAll('tbody tr').length,1);
    assert.match(h.document.body.textContent,/Com WhatsApp/);
    h.document.querySelector('[data-nc-action="copy"]').click();await flush();assert.deepEqual(h.copies,['+5567999999901']);
    h.field('osNcNumbers','+595981234567');assert.equal(h.document.querySelector('[data-nc-results]'),null);assert.equal(h.controller.getState().hasResult,false);assert.equal(h.document.querySelector('.os-section-stack > .os-notice'),null);
    assert.deepEqual(h.errors,[]);
  }finally{h.close();}
});

test('checker accepts group as exclusive source and never submits manual text with it',async()=>{
  const h=await setup('number');try{
    h.field('osNcNumbers','+595981234567');h.field('osNcGroup',group,'change');
    assert.equal(h.document.querySelector('#osNcNumbers').disabled,true);await h.submit();
    assert.equal(h.calls[1].payload.group_id,group);assert.equal(Object.hasOwn(h.calls[1].payload,'numbers'),false);
  }finally{h.close();}
});

test('checker enforces 1000 lines and paginates without truncating copy or exports',async()=>{
  const h=await setup('number',(path,method,payload)=>method==='POST'?{...result(),items:payload.numbers.map(number=>({number,status:'registered',reason:null})),total:1000,input_total:1000,counts:{registered:1000,not_registered:0,inconclusive:0,error:0}}:undefined);try{
    h.field('osNcNumbers',Array(1001).fill('+5567999999901').join('\n'));assert.equal(h.document.querySelector('[data-nc-submit]').disabled,true);
    await h.submit();assert.equal(h.calls.length,1);assert.match(h.document.querySelector('#osNcCount').textContent,/1001/);
    const numbers=Array.from({length:1000},(_,i)=>'+5567'+(900000000+i));h.field('osNcNumbers',numbers.join('\n'));await h.submit();
    assert.equal(h.document.querySelectorAll('tbody tr').length,30);assert.match(h.document.querySelector('.os-pagination').textContent,/1–30 de 1000/);
    h.document.querySelector('[data-nc-action="next"]').click();assert.equal(h.document.querySelector('tbody td').textContent,numbers[30]);
    h.document.querySelector('[data-nc-action="copy"]').click();await flush();assert.deepEqual(h.copies[0].split('\n'),numbers);
    h.document.querySelector('[data-nc-action="export"]').click();assert.equal(h.downloads.at(-1).name,'orion-verificacao-whatsapp.csv');
  }finally{h.close();}
});

test('checker displays an inconclusive provider timeout distinctly from no WhatsApp',async()=>{
  const h=await setup('number',(path,method)=>method==='POST'?result('+5567999999901','inconclusive','provider_timeout'):undefined);try{
    h.field('osNcNumbers','+5567999999901');await h.submit();
    assert.match(h.document.querySelector('tbody').textContent,/Inconclusivo/);
    assert.match(h.document.querySelector('tbody').textContent,/demorou/);
    assert.equal(h.document.querySelector('[data-nc-action="copy"]').disabled,true);
  }finally{h.close();}
});

test('checker drops late responses after unmount',async()=>{
  let release;const waiting=new Promise(resolve=>{release=resolve;});
  const h=await setup('number',(path,method)=>method==='POST'?waiting:undefined);try{
    h.field('osNcNumbers','bad');void h.submit();await flush();h.controller.destroy();release(result());await flush();
    assert.equal(h.document.querySelector('#host').children.length,0);
  }finally{h.close();}
});

test('link generator uses Brazil default, preserves message and offers SVG and PNG',async()=>{
  const h=await setup('link');try{
    assert.equal(h.calls.length,0);assert.equal(h.document.querySelector('#osWlCountry').value,'55');
    h.field('osWlPhone','(67) 99999-9901');h.field('osWlMessage','Olá, José 💙\nValores & planos?');await h.submit();
    assert.equal(h.calls[0].payload.phone,'+55(67) 99999-9901');assert.equal(h.calls[0].payload.message,'Olá, José 💙\nValores & planos?');
    const link=h.document.querySelector('[data-wl-open]');assert.equal(new URL(link.href).searchParams.get('text'),'Olá, José 💙\nValores & planos?');
    assert.equal(link.rel,'noopener noreferrer');assert.ok(h.document.querySelector('[data-wl-action="svg"]'));assert.ok(h.document.querySelector('[data-wl-action="png"]'));
    h.document.querySelector('[data-wl-action="copy"]').click();await flush();assert.equal(h.copies[0],link.href);
    h.document.querySelector('[data-wl-action="svg"]').click();assert.equal(h.downloads.at(-1).name,'orion-whatsapp-qr.svg');
    assert.deepEqual(h.errors,[]);
  }finally{h.close();}
});

test('link generator supports another DDI and explicit international numbers',async()=>{
  const h=await setup('link');try{
    h.field('osWlCountry','other','change');h.field('osWlDdi','49');h.field('osWlPhone','15123456789');await h.submit();assert.equal(h.calls[0].payload.phone,'+4915123456789');
    h.field('osWlPhone','+595981234567');await h.submit();assert.equal(h.calls[1].payload.phone,'+595981234567');
  }finally{h.close();}
});

test('editing invalidates old preview and counts Unicode characters without truncation',async()=>{
  const h=await setup('link');try{
    h.field('osWlPhone','67999999901');await h.submit();assert.ok(h.document.querySelector('[data-wl-open]'));
    h.field('osWlMessage','💙'.repeat(1000));assert.equal(h.document.querySelector('[data-wl-open]'),null);assert.match(h.document.querySelector('#osWlCount').textContent,/1000/);assert.equal(h.document.querySelector('[data-wl-generate]').disabled,false);
    h.field('osWlMessage','a'.repeat(1001));assert.equal(h.document.querySelector('[data-wl-generate]').disabled,true);
  }finally{h.close();}
});

test('link remains usable when the exact message is too large for a QR',async()=>{
  const h=await setup('link',(path,method,payload)=>linkResult(payload,{qr:false}));try{
    h.field('osWlPhone','67999999901');h.field('osWlMessage','💙'.repeat(1000));await h.submit();
    assert.equal(new URL(h.document.querySelector('[data-wl-open]').href).searchParams.get('text'),'💙'.repeat(1000));
    assert.equal(h.document.querySelector('[data-wl-action="svg"]'),null);assert.match(h.document.body.textContent,/Reduza/);
  }finally{h.close();}
});

test('link rejects mismatched destinations and clears sensitive results on revoked access',async()=>{
  const h=await setup('link',(path,method,payload)=>({...linkResult(payload),url:'https://evil.example/'}));try{
    h.field('osWlPhone','67999999901');await h.submit();assert.equal(h.document.querySelector('[data-wl-open]'),null);assert.match(h.document.querySelector('[role="alert"]').textContent,/confirmar/);
    h.field('osWlPhone','67999999902');assert.equal(h.document.querySelector('[role="alert"]'),null);
    h.access.allowed=false;h.field('osWlPhone','new');assert.equal(h.document.querySelector('form'),null);assert.match(h.document.body.textContent,/acesso/);
  }finally{h.close();}
});
