import test from 'node:test';
import assert from 'node:assert/strict';
import {makeSuite,planState,tenantA,tenantB,flush} from './harness.mjs';

const business=(profile='subscription_services',id=tenantA)=>({id,name:'Empresa sintética',role:'owner',slug:'empresa',timezone:'America/Sao_Paulo',workspace_profile:profile});
const compact=h=>h.document.getElementById('osSidebarContext').classList.contains('os-sidebar-context--subscription');
const site=h=>h.document.querySelector('#osSidebarContext .os-side-site');

test('compact spacing follows profile refresh and restores the standard sidebar',async t=>{
  let value=planState('elite',{workspace_profile:'standard',revision:'spacing-standard'});
  const h=await makeSuite({initialRoute:'novidades',permissions:()=>({effective_plan:value}),bridge:{tenants:[business('standard')]}});
  t.after(()=>h.close());await flush();
  assert.equal(compact(h),false);assert.ok(site(h));
  value=planState('elite',{workspace_profile:'subscription_services',revision:'spacing-subscription'});
  h.window.dispatchEvent(new h.window.Event('orion:plan-changed'));await flush();
  assert.equal(compact(h),true);assert.equal(site(h),null);
  value=planState('elite',{workspace_profile:'standard',revision:'spacing-standard-restored'});
  h.window.dispatchEvent(new h.window.Event('orion:plan-changed'));await flush();
  assert.equal(compact(h),false);assert.ok(site(h));
  assert.deepEqual(h.errors,[]);
});

test('compact spacing is removed when another ordinary account is loaded',async t=>{
  let value=planState('elite',{workspace_profile:'subscription_services',revision:'spacing-account-first'});
  const h=await makeSuite({initialRoute:'novidades',permissions:()=>({effective_plan:value}),bridge:{tenants:[business()]}});
  t.after(()=>h.close());await flush();
  assert.equal(compact(h),true);assert.equal(site(h),null);
  value=planState('elite',{workspace_profile:'standard',revision:'spacing-account-next'});
  h.window.OrionSuite.sync({user:{role:'member',email:'next@example.invalid'},tenantId:tenantB,tenants:[business('standard',tenantB)]});await flush();
  assert.equal(compact(h),false);assert.ok(site(h));
  assert.deepEqual(h.errors,[]);
});
