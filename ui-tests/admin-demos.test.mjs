import test from 'node:test';
import assert from 'node:assert/strict';
import {makePanel,jsonResponse} from './panel-harness.mjs';
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

function revisedRecord(){
  const image='https://orionagenda.pro/demo-assets/'+'a'.repeat(64)+'.webp';
  return {source_id:record.source_id,status:'draft',expected_version:999,id:'ignored-id',token:'ignored-token',
    catalogue:{site:{title:'Barbearia atualizada',city:'Cidade sintética',state:'SP',logo_url:image,cover_url:image},
      services:[['combo','Cabelo + barba',8000],['hair','Cabelo',5000],['beard','Barba',4000]].map(([id,name,price_cents])=>
        ({id,name,price_cents,duration_min:null,image_url:image,professional_ids:['published-person']})),
      professionals:[{id:'published-person',name:'Profissional publicado',image_url:null}],opening_hours:[],timezone:null},
    source_urls:['https://example.com/agenda'],review_notes:'Preços conferidos na tabela publicada.',
    pending_fields:['Foto do profissional a confirmar'],reviewed_at:'2026-11-10T12:00:00Z',expires_at:record.expires_at};
}
function selectUpdateFile(h,value){
  const file=typeof value?.text==='function'?value:{name:'cadastros-atualizados.json',size:500,text:async()=>JSON.stringify(value)};
  const input=h.element(`[data-demo-update-file="${record.id}"]`);
  Object.defineProperty(input,'files',{value:[file],configurable:true});
  input.dispatchEvent(new h.window.Event('change',{bubbles:true}));
}
async function updateFixture(t,{status='published',extraRespond}={}){
  let current={...structuredClone(record),status,version:7,review_notes:'Observação anterior.',reviewed_at:'2026-11-09T12:00:00Z',
    source_urls:['https://example.com/original']};
  const h=await makePanel({role:'admin',initialRoute:'member',respond:async(path,method,data)=>{
    const extra=await extraRespond?.(path,method,data);if(extra!==undefined)return extra;
    if(path==='/api/v1/admin/demos'){
      const {catalogue,source_urls,review_notes,...summary}=current;
      return {items:[{...summary,title:catalogue.site.title}],total:1};
    }
    if(path===`/api/v1/admin/demos/${record.id}`){
      if(method==='PUT'){
        if(data.expected_version!==current.version)return jsonResponse({detail:'A demonstração mudou.'},409);
        current={...current,...data,version:current.version+1};return {item:current,replayed:false};
      }
      return current;
    }
    return respond(path);
  }});t.after(()=>h.close());await flush();
  h.click('[data-admin-control=demos]');await flush();h.click('[data-demo-action=inspect]');await flush();
  return Object.assign(h,{current:()=>current,setRecord:value=>{current=value;}});
}

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

test('reviewed bundle updates the matching published demo with prices, photos and the same link',async t=>{
  const h=await updateFixture(t),revised=revisedRecord();
  selectUpdateFile(h,{demos:[{...revised,source_id:'another-company'},revised]});await flush();
  const review=h.element('[data-demo-update-review]');
  assert.match(review.textContent,/Barbearia atualizada/);
  assert.match(review.textContent,/3 serviços\. 1 profissional/);
  assert.match(review.textContent,/3 de 3 serviços com foto\. 0 de 1 profissional com foto/);
  for(const price of ['80,00','50,00','40,00'])assert.ok(review.textContent.includes(price));
  assert.match(review.textContent,/Foto do profissional a confirmar/);
  assert.match(review.textContent,/Estado mantido: Disponível/);
  assert.equal(h.calls.some(call=>call.method==='PUT'),false);
  h.click('[data-demo-action=update]');await flush();
  const writes=h.calls.filter(call=>call.method!=='GET');
  assert.equal(writes.length,1);assert.equal(writes[0].method,'PUT');
  assert.equal(writes[0].path,`/api/v1/admin/demos/${record.id}`);
  const {id,token,expected_version,status,...fields}=revised;
  assert.deepEqual(writes[0].data,{...fields,expected_version:7,status:'published'});
  assert.equal(h.current().public_url,record.public_url);
  assert.equal(h.element('#demoResults .os-item-commands a').href,'https://orion.test'+record.public_url);
  assert.match(h.element('#demoFeedback').textContent,/Cadastro atualizado/);
  assert.equal(h.document.querySelector('[data-demo-update-review]'),null);
  assert.deepEqual(h.errors,[]);
});

test('updates preserve draft or archived state and omitted review metadata',async t=>{
  for(const status of ['draft','archived'])await t.test(status,async t=>{
    const h=await updateFixture(t,{status}),previous=structuredClone(h.current());
    selectUpdateFile(h,{source_id:record.source_id,catalogue:revisedRecord().catalogue,status:'published'});await flush();
    h.click('[data-demo-action=update]');await flush();
    const put=h.calls.find(call=>call.method==='PUT');
    assert.equal(put.data.status,status);assert.equal(put.data.expected_version,7);
    for(const key of ['source_urls','review_notes','pending_fields','reviewed_at','expires_at'])
      assert.deepEqual(put.data[key],previous[key]);
    assert.equal(h.current().status,status);
    assert.equal(h.calls.some(call=>call.method==='POST'||call.method==='PATCH'),false);
    assert.deepEqual(h.errors,[]);
  });
});

test('an update file must identify exactly one matching company and review names remain text',async t=>{
  const h=await updateFixture(t),revised=revisedRecord();
  for(const demos of [[{...revised,source_id:'wrong-company'}],[revised,revised]]){
    selectUpdateFile(h,{demos});await flush();
    assert.match(h.element('[data-demo-update-review]').textContent,/único cadastro desta empresa/);
    assert.equal(h.document.querySelector('[data-demo-action=update]'),null);
  }
  revised.catalogue.site.title='<img src=x onerror="alert(1)">';
  revised.review_notes='<script>alert(1)</script>';
  selectUpdateFile(h,revised);await flush();
  assert.equal(h.document.querySelector('[data-demo-update-review] img, [data-demo-update-review] script'),null);
  assert.match(h.element('[data-demo-update-review]').textContent,/<img/);
  assert.equal(h.calls.some(call=>call.method==='PUT'),false);
  assert.deepEqual(h.errors,[]);
});

test('older update files and file reads completed after navigation cannot replace the review',async t=>{
  const h=await updateFixture(t),old=deferred(),leaving=deferred(),revised=revisedRecord();
  selectUpdateFile(h,{size:500,text:()=>old.promise});
  selectUpdateFile(h,revised);await flush();
  const stale={...revised,catalogue:{...revised.catalogue,site:{title:'Arquivo antigo'}}};
  old.resolve(JSON.stringify(stale));await flush();
  assert.match(h.element('[data-demo-update-review]').textContent,/Barbearia atualizada/);
  assert.doesNotMatch(h.element('[data-demo-update-review]').textContent,/Arquivo antigo/);
  selectUpdateFile(h,{size:500,text:()=>leaving.promise});
  await h.window.OrionSuite.open('dashboard',{force:true});await flush();
  leaving.resolve(JSON.stringify(revised));await flush();
  assert.equal(h.element('#page-member').classList.contains('hidden'),true);
  assert.doesNotMatch(h.element('[data-demo-update-review]').textContent,/Barbearia atualizada/);
  assert.equal(h.calls.some(call=>call.method==='PUT'),false);
  assert.deepEqual(h.errors,[]);
});

test('a 409 requires reloading and a new review before using the changed version and state',async t=>{
  const h=await updateFixture(t),revised=revisedRecord();
  selectUpdateFile(h,revised);await flush();
  h.setRecord({...h.current(),version:8,status:'archived'});
  h.click('[data-demo-action=update]');await flush();
  assert.match(h.element('[data-demo-update-review]').textContent,/O cadastro mudou/);
  assert.equal(h.document.querySelector('[data-demo-action=update]'),null);
  assert.equal(h.element('[data-demo-update-file]').disabled,true);
  assert.equal(h.calls.filter(call=>call.method==='PUT').length,1);
  assert.equal(h.calls.filter(call=>call.path===`/api/v1/admin/demos/${record.id}`&&call.method==='GET').length,1);
  selectUpdateFile(h,revised);await flush();
  assert.equal(h.document.querySelector('[data-demo-action=update]'),null);
  h.click('[data-demo-action=inspect]');await flush();
  selectUpdateFile(h,revised);await flush();
  assert.match(h.element('[data-demo-update-review]').textContent,/Estado mantido: Arquivada/);
  h.click('[data-demo-action=update]');await flush();
  const puts=h.calls.filter(call=>call.method==='PUT');
  assert.equal(puts.length,2);assert.equal(puts[1].data.expected_version,8);
  assert.equal(puts[1].data.status,'archived');assert.equal(h.current().version,9);
  assert.equal(h.calls.some(call=>call.method==='POST'||call.method==='PATCH'),false);
  assert.deepEqual(h.errors,[]);
});

test('an update response after leaving cannot repaint the hidden page or trigger follow-up requests',async t=>{
  const pending=deferred();
  const h=await updateFixture(t,{extraRespond:(path,method)=>method==='PUT'?pending.promise:undefined});
  selectUpdateFile(h,revisedRecord());await flush();h.click('[data-demo-action=update]');await flush();
  const calls=h.calls.length;
  await h.window.OrionSuite.open('dashboard',{force:true});await flush();
  const afterNavigation=h.calls.length;
  pending.resolve({item:{...h.current(),version:8},replayed:false});await flush();
  assert.equal(h.calls.length,afterNavigation);assert.ok(afterNavigation>=calls);
  assert.equal(h.calls.filter(call=>call.method==='PUT').length,1);
  assert.equal(h.element('#demoFeedback').textContent,'');
  assert.deepEqual(h.errors,[]);
});
