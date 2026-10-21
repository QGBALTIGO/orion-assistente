import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM} from 'jsdom';
import {flush,tenantA} from './harness.mjs';
async function setup({write=true,fail=false}={}){
  const dom=new JSDOM('<main id="host"></main>',{url:'https://orion.test',runScripts:'outside-only'}),{window}=dom,calls=[];
  window.eval(await readFile(new URL('../app/static/orion-flows.js',import.meta.url),'utf8'));
  const host=window.document.querySelector('#host');
  const request=async(path,method='GET',body)=>{calls.push({path,method,body});if(method==='GET')return {items:[{id:'synthetic',name:'<script>Fluxo</script>',steps:[{kind:'handoff',label:'Equipe'}],version:2}]};
    if(fail)throw Error('Versão alterada por outra pessoa');if(path.endsWith('/simulate'))return {trace:[{description:'Solicitaria atendimento humano: Equipe'}]};return {id:'saved',...body,version:1};};
  await window.OrionFlows.mount({host,request,tenantId:tenantA,canWrite:write});
  return {window,host,calls,close(){window.OrionFlows.unmount();window.close();},click(a){host.querySelector('[data-flow-action='+a+']').click();}};
}
test('opening and selecting a flow never executes or simulates it',async t=>{const h=await setup();t.after(()=>h.close());h.click('select');assert.equal(h.host.querySelector('script'),null);assert.equal(h.calls.length,1);h.click('simulate');await flush();assert.match(h.host.textContent,/Nenhuma ação foi executada/);assert.equal(h.calls[1].body.expected_version,2);});
test('reader can simulate a saved draft but cannot edit or save it',async t=>{const h=await setup({write:false});t.after(()=>h.close());h.click('select');assert.equal(h.host.querySelector('[data-flow-action=new]').disabled,true);assert.equal(h.host.querySelector('[data-flow-form] button:not([type])').disabled,true);assert.equal(h.host.querySelector('[data-flow-action=simulate]').disabled,false);});
test('a failed flow save preserves ordered steps and draft text',async t=>{const h=await setup({fail:true});t.after(()=>h.close());h.click('new');const name=h.host.querySelector('[name=name]');name.value='Rascunho preservado';name.dispatchEvent(new h.window.Event('input',{bubbles:true}));const label=h.host.querySelector('[data-flow-label]');label.value='Revisão';label.dispatchEvent(new h.window.Event('input',{bubbles:true}));h.host.querySelector('form').dispatchEvent(new h.window.Event('submit',{bubbles:true,cancelable:true}));await flush();assert.equal(h.host.querySelector('[name=name]').value,'Rascunho preservado');assert.match(h.host.textContent,/Versão alterada/);assert.equal(h.calls.filter(x=>x.method==='POST').length,1);});
