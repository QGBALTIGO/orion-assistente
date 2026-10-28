import test from 'node:test';
import assert from 'node:assert/strict';
import {makePanel} from './panel-harness.mjs';
import {flush} from './harness.mjs';

const account={
 id:'10000000-0000-4000-8000-000000000001',name:'Cliente de teste',
 email:'cliente@example.invalid',state:'active',auth_version:3,
 company_name:'Barbearia Teste',created_at:'2026-10-01T00:00:00Z'
};
test('platform admin sees customer center and can inspect without changing data',async t=>{
 const h=await makePanel({role:'admin',initialRoute:'member',respond:path=>{
  if(path.startsWith('/api/v1/admin/accounts?'))return {items:[account],total:1,page:1,page_size:20};
  if(path==='/api/v1/admin/accounts/'+account.id)return {account,history:[]};
 }});t.after(()=>h.close());await flush();
 h.click('#osNav [data-os-route=member]');await flush();
 assert.equal(h.element('#adminClientCenter').classList.contains('hidden'),false);
 assert.equal(h.element('#page-member .member-settings').classList.contains('hidden'),true);
 assert.match(h.element('#adminClientResults').textContent,/Cliente de teste/);
 assert.ok(h.document.querySelector('#osNav [data-os-route=member]'));
 h.click('#adminClientResults button');await flush();
 assert.equal(h.element('#adminClientName').value,account.name);
 assert.ok(h.element('#adminClientActions').textContent.includes('Suspender'));
 assert.ok(h.element('#adminClientActions').textContent.includes('Excluir'));
 assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
 assert.deepEqual(h.errors,[]);
});
for(const role of ['member','support']){
 test(role+' cannot see admin account list or controls',async t=>{
  const h=await makePanel({role,initialRoute:'dashboard'});t.after(()=>h.close());await flush();
  // Navigation visibility is governed by the existing role router;
  // only the privileged client center must remain inaccessible here.
  assert.equal(h.element('#adminClientCenter').classList.contains('hidden'),true);
  assert.equal(h.calls.filter(c=>c.path.startsWith('/api/v1/admin/accounts')).length,0);
 });
}
