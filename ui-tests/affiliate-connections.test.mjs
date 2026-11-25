import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {JSDOM} from 'jsdom';
import {deferred,flush,tenantA} from './harness.mjs';
const source=await readFile(new URL('../app/static/orion-affiliate-workspace.js',import.meta.url),'utf8');
const API='/api/v1/afiliados/'+tenantA+'/api/';
const WA='b'.repeat(32), TG='c'.repeat(32);
function workspace(){return {company:'Conta sintética',sending:false,offer:[],decision:[],rule:[],product_flag:[],niches:[],sorts:{},template:[],jobs:[],
  connection:[{id:'telegram',status:'connected',username:'bot_sintetico'}],
  destination:[{id:WA,name:'Canal de ofertas e promoções',mode:'whatsapp_channel',verified:true},{id:TG,name:'Ofertas Telegram',mode:'telegram',verified:true}]};}
async function harness(t,{data=workspace(),getState,write}={}){
  const dom=new JSDOM('<main id="host"></main>',{url:'https://orion.test/panel?aba=afiliados-conexoes',runScripts:'outside-only',pretendToBeVisual:true});
  const {window}=dom,{document}=window,host=document.querySelector('#host'),calls=[],errors=[],notices=[];
  window.CSS={escape:String};window.HTMLElement.prototype.scrollIntoView=()=>{};window.scrollTo=()=>{};
  Object.defineProperty(window.crypto,'randomUUID',{value:randomUUID});
  window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  window.HTMLDialogElement.prototype.close=function(value){this.returnValue=value||this.returnValue;this.open=false;this.dispatchEvent(new window.Event('close'));};
  window.addEventListener('error',event=>errors.push(event.error?.stack||event.message));
  window.eval(source.replace('export function mount(','function mountConnections(')+'\nwindow.mountConnections=mountConnections;');
  let mounted,reads=0;
  mounted=window.mountConnections({host,tenantId:tenantA,view:'conexoes',whatsappAvailable:true,
    icon:name=>'<svg class="os-icon" data-icon="'+name+'" aria-hidden="true"></svg>',
    open:async route=>mounted.setView(route),showNotice:(message,error)=>notices.push({message,error}),
    request:async(path,method,payload)=>{
      const route=path.slice(API.length);
      calls.push({path:route,method,payload});
      if(route==='state'&&method==='GET'){reads++;return JSON.parse(JSON.stringify(getState?await getState(data,reads):data));}
      if(route==='whatsapp/status'&&method==='GET')return {status:'connected'};
      if(route==='whatsapp/groups'&&method==='GET')return {groups:[{title:'Grupo sintético',address:'120363000001@g.us'}]};
      if(write)return await write(route,method,payload,data);
      throw Error('Unexpected request: '+method+' '+route);
    }});
  t.after(()=>{mounted.unmount();dom.window.close();assert.deepEqual(errors,[]);});
  await flush();
  return {window,document,host,data,calls,notices,mounted,get(selector){const node=host.querySelector(selector);assert.ok(node,'Missing '+selector);return node;},
    click(selector){this.get(selector).click();},confirm(value='confirm'){this.get('#osAffiliateDialog').close(value);},
    change(selector,value){const node=this.get(selector);node.value=value;node.dispatchEvent(new window.Event('change',{bubbles:true}));return node;},
    submit(selector){const form=this.get(selector);form.dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));},
    writes(){return calls.filter(call=>call.method!=='GET');}};
}
test('Contas, contadores e filtros usam apenas destinos reais',async t=>{
  const h=await harness(t);assert.equal(h.get('.af-connections-workspace h1').textContent,'Conexões e destinos');
  assert.deepEqual([...h.host.querySelectorAll('[data-connection-summary] strong')].map(n=>n.textContent),['2','2','0']);
  assert.match(h.get('[data-connection-account=telegram]').textContent,/@bot_sintetico/);
  h.click('[data-connection-filter=telegram]');
  assert.equal(h.host.querySelectorAll('[data-connection-dest]').length,1);
  assert.equal(h.get('[data-connection-dest]').dataset.connectionDest,TG);
  h.click('[data-connection-filter=all]');
  assert.equal(h.host.querySelectorAll('[data-connection-dest]').length,2);
  assert.equal(h.calls.length,1);assert.equal(h.writes().length,0);
});
test('Cadastro recolhido abre pelo botão e atualização preserva campos, tipo, filtro e foco',async t=>{
  const later=deferred();const h=await harness(t,{getState:(data,n)=>n===1?data:later.promise});
  assert.equal(h.get('#af-destination-editor').open,false);
  h.click('[data-act=new-destination]');assert.equal(h.get('#af-destination-editor').open,true);
  h.change('#af-mode','whatsapp_channel');const field=h.get('[data-form=destination] input[name=address]');
  field.value='https://whatsapp.com/channel/synthetic';field.focus();field.setSelectionRange(5,10);
  h.click('[data-connection-filter=whatsapp]');
  h.click('[data-act=refresh-connections]');await flush();
  h.click('[data-act=refresh-connections]');later.resolve(h.data);await flush();
  assert.equal(h.get('#af-destination-editor').open,true);assert.equal(h.get('[data-form=destination] input[name=address]'),field);
  assert.equal(field.value,'https://whatsapp.com/channel/synthetic');assert.equal(h.document.activeElement,field);assert.equal(field.selectionStart,5);
  assert.equal(h.get('#af-mode').value,'whatsapp_channel');
  assert.equal(h.get('[data-connection-filter=whatsapp]').getAttribute('aria-pressed'),'true');
  assert.equal(h.calls.filter(c=>c.path==='state').length,2);
});
test('Falha de atualização não desmonta credenciais nem rascunho',async t=>{
  const h=await harness(t,{getState:(data,n)=>{if(n===2)throw Error('Falha temporária');return data;}});
  const details=h.get('#af-telegram-credentials');details.open=true;const field=h.get('input[name=token]');
  field.value='synthetic-only';h.click('[data-act=refresh-connections]');await flush();
  assert.equal(h.get('input[name=token]'),field);assert.equal(field.value,'synthetic-only');assert.equal(details.open,true);
  assert.match(h.get('[data-connection-feedback]').textContent,/Falha temporária/);
  h.click('[data-act=refresh-connections]');await flush();assert.equal(h.get('[data-connection-feedback]').textContent,'');
});
test('Remoção exige confirmação e não permite verificar e remover ao mesmo tempo',async t=>{
  const later=deferred();const h=await harness(t,{write:async(route,method,payload,data)=>{
    assert.equal(route,'destinations/'+WA);assert.equal(method,'DELETE');await later.promise;data.destination=data.destination.filter(d=>d.id!==WA);return {ok:true};
  }});
  h.click('[data-act=remove-dest][data-value="'+WA+'"]');h.confirm('cancel');await flush();assert.equal(h.writes().length,0);
  h.click('[data-act=remove-dest][data-value="'+WA+'"]');h.confirm();await flush();
  h.click('[data-act=verify-wa-channel][data-value="'+WA+'"]');h.click('[data-act=remove-dest][data-value="'+WA+'"]');
  assert.equal(h.writes().length,1);later.resolve();await flush();
  assert.equal(h.host.querySelector('[data-connection-dest="'+WA+'"]'),null);assert.equal(h.writes().length,1);
});
test('Destinos com problema não são contados como verificados e exibem ocorrência',async t=>{
  const data=workspace();data.destination[0].issue='Conexão indisponível.';
  const h=await harness(t,{data});assert.deepEqual([...h.host.querySelectorAll('[data-connection-summary] strong')].map(n=>n.textContent),['2','1','1']);
  assert.match(h.get('[data-connection-dest="'+WA+'"]').textContent,/Atenção/);
  assert.match(h.get('[data-connection-dest="'+WA+'"]').textContent,/Conexão indisponível/);
});
