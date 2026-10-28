import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {makePanel} from './panel-harness.mjs';
import {flush} from './harness.mjs';

const account={
  id:'10000000-0000-4000-8000-000000000001',name:'Cliente de teste',
  email:'cliente@example.invalid',state:'active',auth_version:3,
  company_name:'Barbearia Teste',created_at:'2026-10-01T00:00:00Z',
  verified_at:'2026-10-02T00:00:00Z',registration_pending:false,
};
const list={items:[account],total:1,page:1,page_size:20};
const stats={total:1,active:1,suspended:0,archived:0,erased:0};
const read=(path)=>{
  if(path==='/api/v1/admin/accounts/stats')return stats;
  if(path.startsWith('/api/v1/admin/accounts?'))return list;
  if(path==='/api/v1/admin/accounts/'+account.id)return {account,history:[
    {action:'edit',reason:'Nome atualizado pelo proprietário',occurred_at:'2026-10-18T12:00:00Z'},
  ]};
};

test('platform admin uses Clientes design, list, mobile swipe and shared customer modal',async t=>{
  const h=await makePanel({role:'admin',initialRoute:'member',respond:read,width:390});
  t.after(()=>h.close());await flush();
  h.click('#osNav [data-os-route=member]');await flush();
  assert.equal(h.element('#adminClientCenter').classList.contains('hidden'),false);
  assert.equal(h.element('#page-member').classList.contains('os-admin-center-open'),true);
  assert.equal(h.element('#page-member .member-settings').classList.contains('hidden'),true);
  assert.equal(h.element('#page-member .member-overview').classList.contains('hidden'),true);
  assert.match(h.element('#adminAccountsResults').textContent,/Cliente de teste/);
  assert.equal(h.element('#adminClientCenter .os-table .os-person strong').textContent,account.name);
  assert.equal(h.element('#adminClientCenter .os-swipe-row>.os-client-mobile-front>.os-client-mobile-main strong').textContent,account.name);
  assert.ok(h.document.querySelector('#adminClientCenter .os-swipe-actions [data-admin-control=maintenance]'));
  assert.ok(h.document.querySelector('#adminClientCenter .os-swipe-actions [data-account-action=archive]'));
  assert.equal(h.element('#adminClientCenter [data-admin-stat=total]').textContent,'1');
  h.click('#adminClientCenter .os-table [data-admin-control=view]');await flush();
  assert.equal(h.element('#osDialog').open,true);
  assert.ok(h.element('#osDialog').classList.contains('os-customer-dialog'));
  assert.ok(h.element('#osDialog').classList.contains('os-admin-dialog'));
  assert.equal(h.element('#adminModalName').value,account.name);
  assert.match(h.element('#osDialog').textContent,/Histórico de ações/);
  assert.match(h.element('#osDialog').textContent,/Acessar conta/);
  assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
  assert.deepEqual(h.errors,[]);
});

test('admin archive confirmation is inside shared modal, not window.confirm or prompt',async t=>{
  const posts=[];
  const h=await makePanel({role:'admin',initialRoute:'member',respond:(path,method,data)=>{
    if(method==='POST'){
      posts.push({path,method,data});return {ok:true,state:'archived'};
    }
    return read(path);
  }});
  t.after(()=>h.close());await flush();
  h.click('#osNav [data-os-route=member]');await flush();
  h.click('#adminClientCenter .os-table [data-account-action=archive]');await flush();
  assert.equal(h.element('#osDialog').open,true);
  assert.equal(h.element('#osDialog [data-admin-form=action]').dataset.adminForm,'action');
  assert.match(h.element('#osDialog').textContent,/30 dias/);
  assert.equal(posts.length,0);
  h.fill('#adminModalReason','Exclusão solicitada pelo cliente');
  h.fill('#adminModalConfirm','EXCLUIR');
  h.submit('#osDialog [data-admin-form=action]');await flush();
  assert.equal(posts.length,1);
  assert.equal(posts[0].path,'/api/v1/admin/accounts/'+account.id+'/archive');
  assert.equal(posts[0].data.expected_version,3);
  assert.equal(posts[0].data.confirmation,'EXCLUIR');
  assert.deepEqual(h.errors,[]);
});

test('maintenance requires a reason and explicit checkbox, never obtains a customer password',async t=>{
  const calls=[],started=[];
  const h=await makePanel({role:'admin',initialRoute:'member',respond:read});
  t.after(()=>h.close());await flush();
  h.click('#osNav [data-os-route=member]');await flush();
  h.window.OrionAdminCenter.open({
    role:'admin',
    request:async(path,method='GET',data)=>{
      calls.push({path,method,data});
      if(method==='POST')return {ok:true,expires_at:1793200000};
      return read(path);
    },
    onMaintenanceStart:()=>started.push('entered'),
  });
  await flush();
  h.click('#adminClientCenter .os-table [data-admin-control=maintenance]');await flush();
  assert.equal(h.element('#osDialog').open,true);
  assert.match(h.element('#osDialog').textContent,/30 minutos/);
  assert.ok(h.element('#osDialog [name=confirmation]').required);
  assert.equal(calls.filter(x=>x.method==='POST').length,0);
  const checkbox=h.element('#osDialog [name=confirmation]');
  checkbox.checked=true;
  h.submit('#osDialog [data-admin-form=maintenance]');await flush();
  const posts=calls.filter(x=>x.method==='POST');
  assert.equal(posts.length,1);
  assert.equal(posts[0].path,'/api/v1/admin/accounts/'+account.id+'/maintenance');
  assert.equal(posts[0].data.confirmation,'ACESSAR');
  assert.equal(posts[0].data.expected_version,3);
  assert.equal(posts[0].data.reason,'Manutenção interna solicitada pelo administrador');
  assert.deepEqual(started,['entered']);
  assert.ok(!JSON.stringify(posts).includes('password'));
  assert.deepEqual(h.errors,[]);
});

for(const role of ['member','support']){
  test(role+' cannot view admin directory or call account APIs',async t=>{
    const h=await makePanel({role,initialRoute:'dashboard'});
    t.after(()=>h.close());await flush();
    assert.equal(h.element('#adminClientCenter').classList.contains('hidden'),true);
    assert.equal(h.calls.filter(c=>c.path.startsWith('/api/v1/admin/accounts')).length,0);
  });
}

test('responsive styles share Clientes cards, support dark theme and preserve fixed maintenance banner',async()=>{
  const css=await readFile(new URL('../app/static/orion-suite.css',import.meta.url),'utf8');
  assert.match(css,/#adminClientCenter \.os-admin-mobile\.os-only-mobile\{display:grid!important/);
  assert.match(css,/#adminClientCenter \.os-swipe-row\{display:flex/);
  assert.match(css,/#osDialog\.os-admin-dialog/);
  assert.match(css,/--os-warning/);
  assert.match(css,/#orionMaintenanceExit/);
});
