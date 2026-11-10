import test from 'node:test';
import assert from 'node:assert/strict';
import {makePanel} from './panel-harness.mjs';
import {flush,deferred} from './harness.mjs';

const record={id:'d1000000-0000-4000-8000-000000000001',source_id:'synthetic-demo-1',
  version:1,status:'draft',expires_at:'2099-12-01T12:00:00Z',
  public_url:'/demo/synthetic_token_only',
  catalogue:{site:{title:'Barbearia de teste',city:'Cidade sintética'},
             services:[],professionals:[]},pending_fields:['Equipe a confirmar'],source_urls:[]};

function respond(path){
  if(path==='/api/v1/admin/accounts/stats')return {};
  if(path.startsWith('/api/v1/admin/accounts?'))return {items:[],total:0};
  if(path.endsWith('/companies/unassigned'))return {companies:[]};
  if(path==='/api/v1/admin/demos')return {items:[record],total:1};
}

function selectFile(h,file){
  Object.defineProperty(h.element('#demoImportFile'),'files',{value:[file],configurable:true});
  h.element('#demoImportFile').dispatchEvent(new h.window.Event('change',{bubbles:true}));
}
const importFile=(source_id,title)=>JSON.stringify([{source_id,
  catalogue:{...record.catalogue,site:{...record.catalogue.site,title}}}]);

test('admin can enter demos, inspect pending fields and return to clients',async t=>{
  const h=await makePanel({role:'admin',initialRoute:'member',respond});
  t.after(()=>h.close());await flush();
  h.click('[data-admin-control=demos]');await flush();
  assert.match(h.element('#demoResults').textContent,/Barbearia de teste/);
  assert.match(h.element('#demoResults').textContent,/Equipe a confirmar/);
  assert.equal(h.document.querySelector('#demoResults a[href^="/demo/"]'),null);
  assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
  h.click('[data-demo-action=back]');await flush();
  assert.ok(h.document.querySelector('#adminAccountsResults'));
  assert.deepEqual(h.errors,[]);
});

test('a file only creates drafts after explicit import and publishing carries the version',async t=>{
  let items=[];
  const h=await makePanel({role:'admin',initialRoute:'member',respond:(path,method,data)=>{
    if(path==='/api/v1/admin/demos'){
      if(method==='POST'){items=[record];return {item:record,replayed:false};}
      return {items,total:items.length};
    }
    if(path===`/api/v1/admin/demos/${record.id}/status`){
      items=[{...record,status:data.status,version:2}];return {item:items[0],replayed:false};
    }
    return respond(path);
  }});t.after(()=>h.close());await flush();
  h.click('[data-admin-control=demos]');await flush();
  const file={size:120,text:async()=>JSON.stringify({demos:[{
    source_id:record.source_id,catalogue:record.catalogue,status:'published'}]})};
  Object.defineProperty(h.element('#demoImportFile'),'files',{value:[file],configurable:true});
  h.element('#demoImportFile').dispatchEvent(new h.window.Event('change',{bubbles:true}));await flush();
  assert.equal(h.calls.filter(c=>c.method==='POST').length,0);
  h.click('[data-demo-action=import]');await flush();
  const post=h.calls.find(c=>c.path==='/api/v1/admin/demos'&&c.method==='POST');
  assert.equal(post.data.status,'draft');
  h.click('[data-demo-action=publish]');await flush();
  const patch=h.calls.find(c=>c.method==='PATCH');
  assert.deepEqual(patch.data,{expected_version:1,status:'published'});
  assert.equal(h.element('#demoResults a').href,'https://orion.test/demo/synthetic_token_only');
  assert.deepEqual(h.errors,[]);
});

test('untrusted imported names are text and delayed reads cannot replace the next page',async t=>{
  const pending=deferred();
  const h=await makePanel({role:'admin',initialRoute:'member',respond:path=>{
    if(path==='/api/v1/admin/demos')return pending.promise;
    return respond(path);
  }});t.after(()=>h.close());await flush();
  h.click('[data-admin-control=demos]');await flush();
  const file={size:100,text:async()=>JSON.stringify([{source_id:'unsafe-name',catalogue:{
    site:{title:'<img src=x onerror="alert(1)">'}}}])};
  Object.defineProperty(h.element('#demoImportFile'),'files',{value:[file]});
  h.element('#demoImportFile').dispatchEvent(new h.window.Event('change',{bubbles:true}));await flush();
  assert.equal(h.document.querySelector('#demoImportReview img'),null);
  assert.match(h.element('#demoImportReview').textContent,/<img/);
  h.click('[data-demo-action=back]');await flush();
  pending.resolve({items:[record]});await flush();
  assert.ok(h.document.querySelector('#adminAccountsResults'));
  assert.equal(h.document.querySelector('#demoResults'),null);
  assert.deepEqual(h.errors,[]);
});

test('ordinary tenant members have no administration demo entry',async t=>{
  const h=await makePanel({role:'member',initialRoute:'member',respond});
  t.after(()=>h.close());await flush();
  assert.equal(h.document.querySelector('[data-admin-control=demos]'),null);
  assert.equal(h.calls.some(c=>c.path==='/api/v1/admin/demos'),false);
});

test('a delayed file read cannot replace the latest reviewed or imported file',async t=>{
  const oldFile=deferred();
  const h=await makePanel({role:'admin',initialRoute:'member',respond:(path,method)=>{
    if(path==='/api/v1/admin/demos'&&method==='POST')return {item:record,replayed:false};
    return respond(path);
  }});t.after(()=>h.close());await flush();
  h.click('[data-admin-control=demos]');await flush();
  selectFile(h,{size:120,text:()=>oldFile.promise});
  selectFile(h,{size:120,text:async()=>importFile('current-file','Arquivo atual')});await flush();
  assert.match(h.element('#demoImportReview').textContent,/Arquivo atual/);
  oldFile.resolve(importFile('old-file','Arquivo anterior'));await flush();
  assert.match(h.element('#demoImportReview').textContent,/Arquivo atual/);
  assert.doesNotMatch(h.element('#demoImportReview').textContent,/Arquivo anterior/);
  h.click('[data-demo-action=import]');await flush();
  const posts=h.calls.filter(call=>call.path==='/api/v1/admin/demos'&&call.method==='POST');
  assert.equal(posts.length,1);assert.equal(posts[0].data.source_id,'current-file');
  assert.deepEqual(h.errors,[]);
});

test('an older list response cannot replace an inspected full catalogue',async t=>{
  const pending=deferred(),{catalogue,source_urls,...summary}=record;
  summary.title=catalogue.site.title;
  let lists=0;
  const h=await makePanel({role:'admin',initialRoute:'member',respond:path=>{
    if(path==='/api/v1/admin/demos')return ++lists===2?pending.promise:{items:[summary],total:1};
    if(path===`/api/v1/admin/demos/${record.id}`)return record;
    return respond(path);
  }});t.after(()=>h.close());await flush();
  h.click('[data-admin-control=demos]');await flush();
  assert.equal(h.element('#demoResults h3').textContent,record.catalogue.site.title);
  h.click('[data-demo-action=refresh]');await flush();
  h.click('[data-demo-action=inspect]');await flush();
  assert.match(h.element('#demoResults').textContent,/Cidade sintética/);
  pending.resolve({items:[summary],total:1});await flush();
  assert.match(h.element('#demoResults').textContent,/Cidade sintética/);
  assert.match(h.element('#demoResults').textContent,/Dados da agenda/);
  assert.equal(h.document.querySelector('[data-demo-action=inspect]'),null);
  assert.deepEqual(h.errors,[]);
});

test('leaving for dashboard stops a draft import after its already pending request',async t=>{
  const pending=deferred();let posts=0;
  const h=await makePanel({role:'admin',initialRoute:'member',respond:(path,method)=>{
    if(path==='/api/v1/admin/demos'){
      if(method==='POST')return ++posts===1?pending.promise:{item:record,replayed:false};
      return {items:[],total:0};
    }
    return respond(path);
  }});t.after(()=>h.close());await flush();
  h.click('[data-admin-control=demos]');await flush();
  const rows=['first','second'].map(source_id=>({source_id,catalogue:record.catalogue}));
  selectFile(h,{size:240,text:async()=>JSON.stringify(rows)});await flush();
  h.click('[data-demo-action=import]');await flush();assert.equal(posts,1);
  await h.window.OrionSuite.open('dashboard',{force:true});await flush();
  assert.equal(h.element('#page-member').classList.contains('hidden'),true);
  assert.equal(h.element('#adminClientCenter').classList.contains('hidden'),false);
  pending.resolve({item:record,replayed:false});await flush();
  assert.equal(posts,1,'nenhum outro cadastro deve ser enviado depois da saída');
  assert.equal(h.element('#demoFeedback').textContent,'');
  assert.equal(h.calls.filter(call=>call.path==='/api/v1/admin/demos'&&call.method==='GET').length,1);
  assert.deepEqual(h.errors,[]);
});
