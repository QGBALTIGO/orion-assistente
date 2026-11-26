import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFile} from 'node:fs/promises';
import {JSDOM} from 'jsdom';

const source=await readFile(new URL('../app/static/orion-affiliate-shortener.js',import.meta.url),'utf8');
const {mount}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const KEY='spoo_test_only_0123456789';
const row={id:'507f1f77bcf86cd799439011',short_url:'https://spoo.me/teste',long_url:'https://example.com/path?q=1&ref=2',status:'ACTIVE',total_clicks:0};
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
async function setup(enabled=true){
  const dom=new JSDOM('<div id="host"></div>',{url:'https://orion.test'});
  const previous={window:globalThis.window,AbortController:globalThis.AbortController,FormData:globalThis.FormData};
  globalThis.window=dom.window;globalThis.AbortController=dom.window.AbortController;globalThis.FormData=dom.window.FormData;
  const host=dom.window.document.getElementById('host'),calls=[],notices=[];
  const api=async(path,method,body)=>{
    const action=path.split('/').at(-1);calls.push({action,method,body});
    if(method==='GET')return {enabled};
    assert.equal(body.api_key,KEY);
    if(action==='create')return {short_url:row.short_url};
    if(action==='stats')return {total_clicks:3,unique_clicks:2};
    if(action==='status')return {id:row.id,status:body.status};
    return {items:[row],page:body.page||1,total:1,has_next:false,omitted:0};
  };
  const view=mount({host,tenantId:'tenant',request:api,icon:()=>'',showNotice:t=>notices.push(t)});
  await tick();
  const $=s=>host.querySelector(s);
  const submit=async form=>{form.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await tick();await tick();};
  return {host,$,calls,notices,view,submit,close(){view.unmount();Object.assign(globalThis,previous);dom.window.close();}};
}
test('disabled tool never contacts provider',async()=>{
  const c=await setup(false);
  try{assert.match(c.host.textContent,/ainda não foi liberada/);assert.equal(c.$('[data-shortener-form="connect"]'),null);assert.equal(c.calls.length,1);}
  finally{c.close();}
});
test('connect, create, render real count, search, stats, disconnect',async()=>{
  const c=await setup();
  try{
    c.$('#shortener-key').value=KEY;
    await c.submit(c.$('[data-shortener-form="connect"]'));
    assert.match(c.host.textContent,/0 cliques/);
    assert.equal(c.host.innerHTML.includes(KEY),false);
    const form=c.$('[data-shortener-form="create"]');
    form.elements.url.value=row.long_url;form.elements.alias.value='teste';form.elements.accepted.checked=true;
    await c.submit(form);
    assert.equal(c.calls.find(x=>x.action==='create').body.url,row.long_url);
    assert.equal(c.calls.find(x=>x.action==='create').body.alias,'teste');
    assert.match(c.$('[data-shortener-result]').textContent,/spoo.me\/teste/);
    c.$('[data-shortener="stats"]').click();await tick();await tick();
    assert.match(c.$('[data-shortener-stats]').textContent,/Únicos estimados: 2/);
    c.$('#shortener-search').value='teste';
    await c.submit(c.$('[data-shortener-form="search"]'));
    assert.equal(c.calls.at(-1).body.search,'teste');
    c.$('[data-shortener="disconnect"]').click();
    assert.equal(c.$('[data-shortener-history]'),null);
    assert.equal(c.$('#shortener-key').value,'');
  }finally{c.close();}
});
