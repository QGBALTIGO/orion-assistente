import test from 'node:test';
import assert from 'node:assert/strict';
import {makePanel} from './panel-harness.mjs';
import {planState,tenantA,tenantB,flush,deferred} from './harness.mjs';

const unassigned='/api/v1/admin/accounts/companies/unassigned';
const companyPath=id=>'/api/v1/admin/accounts/companies/'+id+'/plan';
const account={id:'90000000-0000-4000-8000-000000000009',company_id:tenantA,can_manage_plan:true,name:'Responsável sintético',company_name:'Empresa com conta',email:'plan@example.invalid',state:'active',auth_version:7};
const row=(id=tenantB,overrides={})=>({id,name:'Empresa pendente sintética',status:'active',owner_status:'unassigned',can_manage_plan:true,active_professionals:0,effective_plan:planState('essencial',{revision:'a'.repeat(64),version:0,manual_assignment:{plan_code:'essencial',active:true,expires_at:null}}),...overrides});
const envelope=c=>({company:{id:c.id,name:c.name,status:c.status,account_id:null,owner_status:c.owner_status,can_manage_plan:c.can_manage_plan,active_professionals:c.active_professionals},effective_plan:c.effective_plan,history:[],replayed:false});
async function fixture({companies=[row()],accounts=[account],initialRoute='plano',enter=true,respond}={}){
  const values=new Map(companies.map(c=>[c.id,envelope(c)]));
  const server={companies,accounts,values};
  const h=await makePanel({role:'admin',initialRoute,respond:async(path,method,data)=>{
    const supplied=await respond?.(path,method,data,server);if(supplied!==undefined)return supplied;
    if(path===unassigned)return {companies:server.companies.map(c=>({...c,effective_plan:server.values.get(c.id).effective_plan}))};
    if(path.startsWith('/api/v1/admin/accounts?'))return {items:server.accounts,total:server.accounts.length,page:1,page_size:20};
    if(path==='/api/v1/admin/accounts/stats')return {total:server.accounts.length,active:server.accounts.length};
    const id=[...server.values.keys()].find(id=>path===companyPath(id));
    if(id){
      const value=server.values.get(id);if(method==='GET')return value;
      value.effective_plan=planState(method==='DELETE'?'none':data.plan_code,{version:value.effective_plan.version+1,revision:'b'.repeat(64),active:value.company.status==='active'&&method!=='DELETE',manual_assignment:method==='DELETE'?null:{plan_code:data.plan_code,active:true,expires_at:data.expires_at}});
      value.history.push({action:method==='DELETE'?'manual_remove':'manual_assign',occurred_at:'2026-10-29T12:00:00Z',actor_id:'50000000-0000-4000-8000-000000000005',reason:data.reason,new_plan:value.effective_plan});
      return value;
    }
  }});await flush();
  if(enter){h.click('#osNav [data-os-route=member]');await flush();}
  return {...h,server};
}
const writes=h=>h.calls.filter(c=>c.method!=='GET'&&c.path!=='/auth/logout');
const open=async(h,id=tenantB)=>{h.click('#adminUnassignedCompanies [data-admin-control=plan][data-company-id="'+id+'"]');await flush();};
const change=async(h,key,value)=>{const el=h.element('[data-admin-plan-filter='+key+']');el.value=value;el.dispatchEvent(new h.window.Event('change',{bubbles:true}));await flush();};

test('pending companies are separate business targets and only manageable owner accounts suppress duplicates',async t=>{
  const duplicate=row(tenantA),viewerCompany=row('30000000-0000-4000-8000-000000000003');
  const viewer={...account,id:'80000000-0000-4000-8000-000000000008',company_id:viewerCompany.id,can_manage_plan:false};
  const h=await fixture({companies:[row(),duplicate,viewerCompany,row()],accounts:[account,viewer]});t.after(()=>h.close());
  assert.match(h.element('#adminUnassignedCompanies').textContent,/Empresas com vínculo pendente|Alterar o plano não muda o responsável/);
  assert.equal(h.document.querySelectorAll('[data-admin-company-id]').length,2);
  assert.equal(h.document.querySelector('[data-admin-company-id="'+tenantA+'"]'),null);
  assert.ok(h.element('[data-admin-company-id="'+viewerCompany.id+'"]'));
  assert.equal(h.document.querySelectorAll('#adminUnassignedCompanies [data-account-id],#adminUnassignedCompanies [data-admin-control=maintenance],#adminUnassignedCompanies [data-account-action],#adminUnassignedCompanies .os-avatar').length,0);
  assert.match(h.element('#adminAccountsCount').textContent,/2 contas/);
  assert.equal(writes(h).length,0);
});

test('a paused company uses the direct company route and CAS after review without creating or changing an owner',async t=>{
  const h=await fixture({companies:[row(tenantB,{status:'paused',effective_plan:planState('essencial',{active:false,revision:'a'.repeat(64),version:3,manual_assignment:{plan_code:'essencial',active:true}})})]});t.after(()=>h.close());
  await open(h);assert.match(h.element('#osDialog').textContent,/Pausada|Conta de proprietário indisponível/);
  h.element('#adminManualPlan').value='elite';h.fill('#adminManualReason','Liberação sintética');h.submit('[data-admin-form=plan]');await flush();assert.equal(writes(h).length,0);
  assert.match(h.element('#osDialog').textContent,/vínculo do proprietário/);
  h.submit('[data-admin-form=plan-confirm]');await flush();
  const [call]=writes(h);assert.equal(writes(h).length,1);assert.equal(call.path,companyPath(tenantB));assert.equal(call.method,'PUT');
  assert.equal(call.data.expected_version,3);assert.equal(call.data.expected_revision,'a'.repeat(64));assert.equal(call.data.confirmation,'ALTERAR PLANO');assert.match(call.data.operation_id,/^[a-f0-9-]{36}$/);
  assert.equal('account_id' in call.data,false);assert.equal('owner_id' in call.data,false);assert.equal('status' in call.data,false);
  assert.equal(h.server.values.get(tenantB).company.status,'paused');assert.equal(h.server.values.get(tenantB).company.account_id,null);assert.equal(h.server.values.get(tenantB).effective_plan.active,false);
  assert.equal(h.server.accounts[0].id,account.id);assert.equal(h.server.accounts[0].auth_version,7);
});

test('company history, removal and Escape reuse the shared modal and preserve focus on its company trigger',async t=>{
  const h=await fixture();t.after(()=>h.close());await open(h);h.submit('[data-admin-form=plan]');h.submit('[data-admin-form=plan-confirm]');await flush();
  const trigger=h.element('#adminUnassignedCompanies [data-admin-control=plan]');trigger.focus();trigger.click();await flush();
  assert.match(h.element('#osDialog .os-disclosure-body').textContent,/Administrador geral/);
  assert.equal(h.element('#osDialog .os-disclosure-body').textContent.includes('50000000-0000-4000-8000-000000000005'),false);
  h.element('#osDialog').dispatchEvent(new h.window.Event('cancel',{cancelable:true}));assert.equal(h.document.activeElement,trigger);
  trigger.click();await flush();h.click('[data-admin-control=remove-plan]');await flush();assert.equal(writes(h).length,1);
  h.submit('[data-admin-form=plan-confirm]');await flush();assert.equal(writes(h).length,2);assert.equal(writes(h)[1].method,'DELETE');assert.equal(writes(h)[1].path,companyPath(tenantB));assert.equal(writes(h)[1].data.confirmation,'REMOVER ATRIBUIÇÃO');
  assert.equal(h.server.values.get(tenantB).effective_plan.code,'none');assert.equal(h.server.values.get(tenantB).history.length,2);
});

test('company search and plan/source/subscription filters share scope while account status tabs do not reinterpret tenant state',async t=>{
  const commercial=planState('elite',{source:'cakto',commercial_subscription:{status:'active',name:'Orion Elite'}});
  const other=row('30000000-0000-4000-8000-000000000003',{name:'Empresa comercial',effective_plan:commercial});
  const h=await fixture({companies:[row(tenantB,{status:'paused'}),other]});t.after(()=>h.close());
  await change(h,'plan','elite');assert.equal(h.document.querySelectorAll('[data-admin-company-id]').length,1);assert.match(h.element('#adminUnassignedCompanies').textContent,/Empresa comercial/);
  await change(h,'plan','all');await change(h,'source','manual');assert.equal(h.document.querySelectorAll('[data-admin-company-id]').length,1);assert.match(h.element('#adminUnassignedCompanies').textContent,/Pausada/);
  await change(h,'source','all');await change(h,'subscriptionStatus','active');assert.equal(h.document.querySelectorAll('[data-admin-company-id]').length,1);
  await change(h,'subscriptionStatus','all');h.click('[data-admin-status=suspended]');await flush();assert.equal(h.document.querySelectorAll('[data-admin-company-id]').length,2);
  h.fill('#adminAccountsSearch','COMÉRCIAL');await new Promise(resolve=>setTimeout(resolve,330));await flush();assert.equal(h.document.querySelectorAll('[data-admin-company-id]').length,1);
});

test('Encerrada includes commercial expiry under an active manual plan and effective expiry in both filtered groups',async t=>{
  const manual=planState('elite',{source:'manual',status:'active',commercial_subscription:{status:'expired'}});
  const expired=planState('none',{source:'none',active:false,status:'expired'});
  const company2='30000000-0000-4000-8000-000000000003';
  const accounts=[{...account,effective_plan:manual},{...account,id:'80000000-0000-4000-8000-000000000008',company_id:'40000000-0000-4000-8000-000000000004',name:'Conta expirada',effective_plan:expired}];
  const h=await fixture({accounts,companies:[row(tenantB,{effective_plan:manual}),row(company2,{name:'Empresa expirada',effective_plan:expired})],respond:(path,method,data,server)=>{
    if(path.startsWith('/api/v1/admin/accounts?')&&path.includes('subscription_status=expired'))return {items:server.accounts.filter(a=>a.effective_plan.status==='expired'||a.effective_plan.commercial_subscription?.status==='expired'),total:2};
  }});t.after(()=>h.close());await change(h,'subscriptionStatus','expired');
  assert.equal(h.document.querySelectorAll('#adminAccountsResults .os-table tbody tr').length,2);
  assert.equal(h.document.querySelectorAll('[data-admin-company-id]').length,2);assert.equal(manual.active,true);
});

test('a confirmed company change invalidates permissions before an optional company-list refresh finishes',async t=>{
  const pending=deferred();let reads=0;
  const h=await fixture({respond:path=>{if(path===unassigned&&++reads>1)return pending.promise;}});t.after(()=>h.close());
  const events=[],broadcasts=[];h.window.addEventListener('orion:plan-changed',()=>events.push(true));
  h.window.BroadcastChannel=class{postMessage(value){broadcasts.push(value);}close(){}};
  await open(h);h.submit('[data-admin-form=plan]');h.submit('[data-admin-form=plan-confirm]');await flush();
  assert.equal(writes(h).length,1);assert.equal(events.length,1);assert.equal(broadcasts.length,1);assert.equal(broadcasts[0].tenantId,tenantB);
  pending.resolve({companies:[row()]});await flush();assert.equal(events.length,1);assert.equal(writes(h).length,1);
});

test('409 or unknown company writes never repeat automatically and Conferir estado keeps the direct target even after it leaves the list',async t=>{
  for(const status of [409,503]){
    const h=await fixture({respond:(path,method,data,server)=>{
      if(path===companyPath(tenantB)&&method==='PUT'){server.companies=[];throw Object.assign(Error('Conferir estado'),{status});}
    }});t.after(()=>h.close());await open(h);h.submit('[data-admin-form=plan]');h.submit('[data-admin-form=plan-confirm]');await flush();
    assert.equal(writes(h).length,1);h.submit('[data-admin-form=plan-confirm]');await flush();assert.equal(writes(h).length,1);
    h.click('#osDialog [data-admin-control=plan][data-plan-target=company]');await flush();assert.ok(h.element('#adminManualPlan'));
    assert.equal(h.calls.filter(c=>c.path===companyPath(tenantB)&&c.method==='GET').length,2);
    assert.equal(h.calls.some(c=>c.path.includes('/accounts/'+account.id+'/companies/'+tenantB)),false);
  }
});

test('late company reads cannot repopulate the directory after reset and a company-store error does not hide accounts',async t=>{
  const pending=deferred();let first=true;
  const h=await fixture({respond:path=>{if(path===unassigned&&first){first=false;return pending.promise;}}});t.after(()=>h.close());
  h.window.OrionAdminCenter.reset();pending.resolve({companies:[row()]});await flush();assert.equal(h.document.querySelector('[data-admin-company-id]'),null);
  const failed=await fixture({respond:path=>{if(path===unassigned)throw Error('Empresas temporariamente indisponíveis');}});t.after(()=>failed.close());
  assert.match(failed.element('#adminAccountsResults').textContent,/Responsável sintético/);assert.match(failed.element('#adminUnassignedCompanies').textContent,/Empresas temporariamente indisponíveis/);
});

test('a newly available owner updates the company summary without changing the explicit company target',async t=>{
  const h=await fixture({respond:(path,method,data,server)=>{if(path===companyPath(tenantB)&&method==='GET'){const value=server.values.get(tenantB);value.company.owner_status='assigned';return value;}}});t.after(()=>h.close());
  await open(h);assert.match(h.element('#osDialog').textContent,/Conta de proprietário disponível/);
  h.submit('[data-admin-form=plan]');h.submit('[data-admin-form=plan-confirm]');await flush();assert.equal(writes(h)[0].path,companyPath(tenantB));assert.equal(h.server.values.get(tenantB).company.account_id,null);
});

test('a foreign company envelope cannot open a plan form or create an intent',async t=>{
  const h=await fixture({respond:(path,method)=>path===companyPath(tenantB)&&method==='GET'?{...envelope(row()),company:{id:tenantA,account_id:null}}:undefined});t.after(()=>h.close());
  await open(h);assert.equal(h.document.querySelector('#adminManualPlan'),null);assert.equal(writes(h).length,0);assert.match(h.element('#adminAccountsCount').textContent,/confirmar o plano/);
});

test('a direct reload of member starts its directory without a second navigation click',async t=>{
  const h=await fixture({initialRoute:'member',enter:false});t.after(()=>h.close());await flush();
  assert.match(h.element('#adminAccountsResults').textContent,/Responsável sintético/);assert.ok(h.element('[data-admin-company-id="'+tenantB+'"]'));
});
