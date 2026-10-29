import test from 'node:test';
import assert from 'node:assert/strict';
import {makePanel} from './panel-harness.mjs';
import {planState,tenantA,flush,deferred} from './harness.mjs';

const account={id:'90000000-0000-4000-8000-000000000009',company_id:tenantA,can_manage_plan:true,name:'Responsável sintético',company_name:'Empresa sintética',email:'plan@example.invalid',state:'active',auth_version:7,created_at:'2026-10-01T00:00:00Z'};
const path='/api/v1/admin/accounts/'+account.id+'/companies/'+tenantA+'/plan';
async function fixture({state=planState('essencial',{revision:'a'.repeat(64),version:0}),save}={}){
  const value={company:{id:tenantA,name:'Empresa sintética',active_professionals:2},effective_plan:state,history:[]};
  const h=await makePanel({role:'admin',initialRoute:'member',respond:async(url,method,data)=>{
    if(url===path){if(method==='GET')return value;if(save)return save(url,method,data,value);value.effective_plan=planState(method==='DELETE'?'essencial':data.plan_code,{revision:'b'.repeat(64),version:1});return value;}
    if(url==='/api/v1/admin/accounts/stats')return {total:1,active:1};
    if(url.startsWith('/api/v1/admin/accounts?'))return {items:[{...account,effective_plan:value.effective_plan}],total:1,page:1,page_size:20};
    if(url==='/api/v1/admin/accounts/'+account.id)return {account,history:[]};
  }});await flush();h.click('#osNav [data-os-route=member]');await flush();return {...h,value};
}
const mutations=h=>h.calls.filter(call=>['PUT','DELETE'].includes(call.method));
const open=async h=>{h.click('#adminClientCenter .os-table [data-admin-control=plan]');await flush();};

test('existing administration exposes plan filters and opens the shared modal with authoritative company state',async t=>{
  const h=await fixture();t.after(()=>h.close());await open(h);
  assert.ok(h.element('#osDialog').classList.contains('os-admin-dialog'));assert.equal(h.element('#adminManualPlan').value,'essencial');assert.match(h.element('#osDialog').textContent,/Assinatura comercial|Profissionais ativos|Histórico de planos/);assert.equal(mutations(h).length,0);
  h.click('#osDialog [data-admin-control=close]');const select=h.element('[data-admin-plan-filter=source]');select.value='manual';select.dispatchEvent(new h.window.Event('change',{bubbles:true}));await flush();assert.ok(h.calls.some(call=>call.path.includes('source=manual')));
});

test('manual assignment requires review and sends the company-scoped CAS without payments or account version changes',async t=>{
  const h=await fixture();t.after(()=>h.close());await open(h);h.element('#adminManualPlan').value='elite';h.fill('#adminManualReason','Liberação administrativa');h.submit('[data-admin-form=plan]');await flush();
  assert.equal(mutations(h).length,0);assert.match(h.element('#osDialog').textContent,/Confirmar alteração|Nenhuma cobrança/);h.submit('[data-admin-form=plan-confirm]');await flush();
  const [call]=mutations(h);assert.equal(mutations(h).length,1);assert.equal(call.path,path);assert.equal(call.data.expected_version,0);assert.equal(call.data.expected_revision,'a'.repeat(64));assert.equal(call.data.confirmation,'ALTERAR PLANO');assert.equal(call.data.plan_code,'elite');assert.equal(call.data.access_type,'indefinite');assert.equal(call.data.expires_at,null);assert.match(call.data.operation_id,/^[0-9a-f-]{36}$/);assert.equal('auth_version' in call.data,false);assert.equal(h.calls.some(call=>/checkout|charge|logout/.test(call.path)),false);assert.deepEqual(h.errors,[]);
});

test('temporary access requires a future date and preserves its draft through the review back button',async t=>{
  const h=await fixture();t.after(()=>h.close());await open(h);const type=h.element('#adminManualAccess');type.value='temporary';type.dispatchEvent(new h.window.Event('change',{bubbles:true}));const input=h.element('#adminManualExpires');assert.equal(input.required,true);assert.equal(input.disabled,false);
  input.value='2020-01-01T12:00';h.submit('[data-admin-form=plan]');await flush();assert.equal(h.document.querySelector('[data-admin-form=plan-confirm]'),null);assert.equal(mutations(h).length,0);
  input.value='2099-11-01T12:00';h.fill('#adminManualReason','Teste temporário');h.submit('[data-admin-form=plan]');await flush();h.click('[data-admin-control=plan-back]');assert.equal(h.element('#adminManualExpires').value,'2099-11-01T12:00');assert.equal(h.element('#adminManualReason').value,'Teste temporário');
  h.submit('[data-admin-form=plan]');h.submit('[data-admin-form=plan-confirm]');await flush();assert.equal(mutations(h)[0].data.access_type,'temporary');assert.ok(mutations(h)[0].data.expires_at.endsWith('Z'));
});

test('removing an assignment has separate confirmation and preserves billing identity',async t=>{
  const h=await fixture({state:planState('elite',{revision:'c'.repeat(64),version:4,manual_assignment:{plan_code:'elite',active:true,expires_at:null}})});t.after(()=>h.close());await open(h);h.click('[data-admin-control=remove-plan]');await flush();assert.equal(mutations(h).length,0);assert.match(h.element('#osDialog').textContent,/recalculado pela assinatura comercial/);h.submit('[data-admin-form=plan-confirm]');await flush();assert.equal(mutations(h).length,1);assert.equal(mutations(h)[0].method,'DELETE');assert.equal(mutations(h)[0].data.confirmation,'REMOVER ATRIBUIÇÃO');assert.equal(mutations(h)[0].data.expected_version,4);assert.equal('plan_code' in mutations(h)[0].data,false);
});

test('a conflict or unknown outcome never automatically repeats the manual mutation',async t=>{
  for(const status of [409,503]){
    const h=await fixture({save:()=>{throw Object.assign(Error('Estado mudou'),{status});}});t.after(()=>h.close());await open(h);h.submit('[data-admin-form=plan]');h.submit('[data-admin-form=plan-confirm]');await flush();assert.equal(mutations(h).length,1);assert.ok(h.element('[data-admin-form=plan-confirm] [type=submit]').disabled);assert.ok(h.element('[data-admin-control=plan][data-account-id]'));h.submit('[data-admin-form=plan-confirm]');await flush();assert.equal(mutations(h).length,1);
  }
});

test('Escape cannot close a pending save and reset discards a late administrative result',async t=>{
  const pending=deferred(),h=await fixture({save:()=>pending.promise});t.after(()=>h.close());await open(h);h.submit('[data-admin-form=plan]');h.submit('[data-admin-form=plan-confirm]');await flush();
  const event=new h.window.Event('cancel',{cancelable:true});h.element('#osDialog').dispatchEvent(event);assert.equal(event.defaultPrevented,true);assert.equal(h.element('#osDialog').open,true);
  h.window.OrionAdminCenter.reset();pending.resolve(h.value);await flush();assert.equal(h.element('#osDialog').open,false);assert.equal(mutations(h).length,1);
});

test('the plan modal opened from account details returns keyboard focus to the persistent account row',async t=>{
  const h=await fixture();t.after(()=>h.close());const origin=h.element('#adminClientCenter .os-table [data-admin-control=view]');origin.focus();h.click('#adminClientCenter .os-table [data-admin-control=view]');await flush();
  h.click('#osDialog [data-admin-control=plan]');await flush();h.element('#osDialog').dispatchEvent(new h.window.Event('cancel',{cancelable:true}));assert.equal(h.element('#osDialog').open,false);assert.equal(h.document.activeElement,origin);
});
