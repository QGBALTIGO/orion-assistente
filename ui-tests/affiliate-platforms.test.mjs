import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM} from 'jsdom';
import {flush,tenantA,deferred} from './harness.mjs';
const source=await readFile(new URL('../app/static/orion-affiliate-workspace.js',import.meta.url),'utf8');
async function mount(t,{linked=true,write}={}){
 const dom=new JSDOM('<main id="host"></main>',{url:'https://orion.test/',runScripts:'outside-only',pretendToBeVisual:true});
 const {window}=dom,{document}=window,host=document.querySelector('#host'),calls=[],notices=[],errors=[];
 window.HTMLElement.prototype.scrollIntoView=()=>{};window.scrollTo=()=>{};
 window.addEventListener('error',event=>errors.push(event.message));
 window.eval(source.replace('export function mount(','function mountPlatforms(')+'\nwindow.mountPlatforms=mountPlatforms;');
 const data={company:'Conta teste',connection:linked?[{id:'shopee',status:'connected',account:'••••0783',verified:1795635390}]:[],phrase_ai:false,offer:[],decision:[],rule:[],product_flag:[],niches:[],sorts:{},template:[],destination:[],jobs:[]};
 const app=window.mountPlatforms({host,tenantId:tenantA,view:'plataformas',whatsappAvailable:true,icon:name=>'<svg class="os-icon" data-icon="'+name+'"></svg>',open:async route=>app.setView(route),showNotice:(message,error)=>notices.push({message,error}),request:async(path,method,payload)=>{
  calls.push({path,method,payload});if(method==='GET'&&path.endsWith('/state'))return JSON.parse(JSON.stringify(data));
  if(method==='PUT'&&path.endsWith('/platforms/groq')){if(write)await write();data.connection.push({id:'groq',status:'connected'});data.phrase_ai=true;return {ok:true};}
  throw Error('Unexpected call '+method+' '+path);
 }});
 t.after(()=>{app.unmount();dom.window.close();assert.deepEqual(errors,[]);});
 await flush();
 return {window,host,data,calls,notices,get:s=>{const n=host.querySelector(s);assert.ok(n,s);return n;}};
}
test('Mostra integração real e planejadas, links oficiais e credenciais recolhidas',async t=>{
 const h=await mount(t);
 assert.equal(h.get('.af-platforms-heading h1').textContent,'Plataformas');
 assert.match(h.get('[data-platform-card=shopee]').textContent,/Conectada/);
 assert.equal(h.get('[data-platform-card=shopee] details').open,false);
 for(const key of ['amazon','mercado-livre']){assert.match(h.get('[data-platform-card='+key+']').textContent,/Planejada/);assert.equal(h.get('[data-platform-card='+key+']').querySelector('form'),null);}
 const links=[...h.host.querySelectorAll('.af-platform-resource')];assert.equal(links.length,8);
 assert.equal(links[0].href,'https://affiliate.shopee.com.br/open_api');
 assert.ok(links.every(a=>a.target==='_blank'&&a.rel.includes('noopener')&&a.protocol==='https:'));
 assert.equal(h.get('[data-platform-card=groq] input').type,'password');
 assert.equal(h.calls.length,1);
});
test('Atualização preserva formulário e segredo digitado sem enviar credenciais',async t=>{
 const h=await mount(t);const detail=h.get('[data-platform-card=groq] details'),field=h.get('#af-groq-key');
 detail.open=true;field.value='synthetic-test-key';field.focus();
 h.get('[data-act=refresh]').click();await flush();
 assert.equal(h.get('#af-groq-key'),field);assert.equal(field.value,'synthetic-test-key');assert.equal(detail.open,true);
 assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
});
test('Erro na validação mantém campo e bloqueia envio duplicado',async t=>{
 const gate=deferred(),h=await mount(t,{write:()=>gate.promise});
 const form=h.get('[data-form=groq]'),field=h.get('#af-groq-key');field.value='synthetic-key';
 form.dispatchEvent(new h.window.Event('submit',{bubbles:true,cancelable:true}));form.dispatchEvent(new h.window.Event('submit',{bubbles:true,cancelable:true}));
 assert.equal(h.calls.filter(c=>c.method==='PUT').length,1);gate.reject(Error('Chave inválida'));await flush();
 assert.equal(field.value,'synthetic-key');assert.equal(form.querySelector('button').disabled,false);assert.ok(h.notices.some(n=>n.error));
});
test('Salvar chave atualiza só card afetado e não altera conexão Shopee',async t=>{
 const h=await mount(t),shopee=h.get('[data-platform-card=shopee]'),field=h.get('#af-groq-key');field.value='synthetic-key';
 h.get('[data-form=groq]').dispatchEvent(new h.window.Event('submit',{bubbles:true,cancelable:true}));await flush();await flush();
 assert.equal(h.get('[data-platform-card=shopee]'),shopee);
 assert.match(h.get('[data-platform-card=groq]').textContent,/Conectada/);
 assert.equal(h.get('#af-groq-key').value,'');
});
