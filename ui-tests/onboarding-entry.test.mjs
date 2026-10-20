import test from 'node:test';
import assert from 'node:assert/strict';
import {makeOnboarding,entry,fullState,tenantA,tenantB,flush,deferred,copy} from './onboarding-harness.mjs';

test('unauthenticated mount makes no request and never creates a business',async t=>{
  const h=await makeOnboarding({user:null});t.after(()=>h.close());assert.equal(h.calls.length,0);assert.equal(h.document.getElementById('setup').hidden,true);
});
test('disabled rollout and legacy dashboard leave the existing panel intact',async t=>{
  for(const decision of [entry({enabled:false}),entry({kind:'dashboard'})]){const h=await makeOnboarding({decision});t.after(()=>h.close());assert.equal(h.document.getElementById('setup').hidden,true);assert.equal(h.calls.length,1);assert.ok(h.document.getElementById('existing-panel'));assert.deepEqual(h.calls.filter(c=>c.method!=='GET'),[]);}
});
test('new member sees empty real fields, explicit niche and timezone, without a write on page load',async t=>{
  const h=await makeOnboarding({decision:entry({tenant_id:null,businesses:[]})});t.after(()=>h.close());assert.equal(h.element('[name=name]').value,'');assert.equal(h.element('[name=niche]').value,'');assert.equal(h.element('[name=timezone]').value,'');assert.match(h.element('#setup').textContent,/não conecta o WhatsApp/);assert.deepEqual(h.calls.filter(c=>c.method!=='GET'),[]);assert.equal(h.document.activeElement,h.element('[data-ob-title]'));
});
test('pending invitation cannot silently create a business or grant owner access',async t=>{
  const h=await makeOnboarding({decision:entry({kind:'invitation',tenant_id:null,create_allowed:false,invitations:[{tenant_id:tenantA,business_name:'Empresa convidante',status:'pending',role:'editor'}]})});t.after(()=>h.close());assert.match(h.element('#setup').textContent,/Empresa convidante/);assert.equal(h.document.querySelector('[data-ob-form=create]'),null);assert.equal(h.document.querySelector('a[href="/convite"]').textContent,'Ver convite');assert.deepEqual(h.calls.filter(c=>c.method!=='GET'),[]);
});
test('business chooser resolves only an explicit authorized selection',async t=>{
  const h=await makeOnboarding({decision:entry({kind:'choose_business',tenant_id:null,businesses:[{id:tenantA,name:'Empresa A',role:'owner'},{id:tenantB,name:'Empresa B',role:'editor'}]}),respond:path=>path.includes('tenant_id='+tenantB)?entry({tenant_id:tenantB}):path.includes('/tenants/'+tenantB+'/')?fullState({tenant_id:tenantB,name:'Empresa B',can_edit:false}):undefined});t.after(()=>h.close());assert.equal(h.calls.length,1);h.click('[data-ob-tenant="'+tenantB+'"]');await flush();assert.equal(h.controller.getState().tenantId,tenantB);assert.match(h.element('[data-ob-title]').textContent,/Empresa B/);assert.equal(h.element('[data-ob-write]').disabled,true);assert.deepEqual(h.calls.filter(c=>c.method!=='GET'),[]);
});
test('late response from business A cannot replace business B or issue a stale completion callback',async t=>{
  const late=deferred();let waiting=true;
  const h=await makeOnboarding({auto:false,respond:path=>path==='/api/v1/tenants/'+tenantA+'/onboarding'&&waiting?late.promise:path.includes('tenant_id='+tenantB)?entry({tenant_id:tenantB}):path.includes('/tenants/'+tenantB+'/')?fullState({tenant_id:tenantB,name:'Empresa B'}):undefined});t.after(()=>h.close());
  const a=h.controller.resolveEntry({tenantId:tenantA});await flush();const b=h.controller.resolveEntry({tenantId:tenantB});await b;waiting=false;late.resolve(fullState({name:'Empresa A atrasada'}));assert.equal(await a,null);assert.equal(h.controller.getState().tenantId,tenantB);assert.doesNotMatch(h.element('#setup').textContent,/Empresa A atrasada/);assert.deepEqual(h.tenants,[tenantB]);assert.equal(h.completed.length,0);
});
test('unmount drops private forms and ignores an outstanding read',async t=>{
  const late=deferred();const h=await makeOnboarding({auto:false,respond:path=>path.includes('/tenants/')?late.promise:undefined});t.after(()=>h.close());const pending=h.controller.resolveEntry();await flush();h.controller.unmount();late.resolve(fullState());await pending;assert.equal(h.element('#setup').childElementCount,0);assert.equal(h.element('#setup').hidden,true);
});
test('business labels and backend messages are rendered as text',async t=>{
  const payload='<img src=x onerror=alert(1)>';const h=await makeOnboarding({data:fullState({name:payload,pending:[{step:'business',message:payload}]} )});t.after(()=>h.close());assert.match(h.element('[data-ob-title]').textContent,/<img/);assert.equal(h.document.querySelector('img[src=x]'),null);h.click('[data-ob-step=review]');assert.equal(h.document.querySelector('img[src=x]'),null);
});
test('products-only server steps omit professional scheduling requirements from progress',async t=>{
  const data=fullState();data.configuration.activity='products';data.steps.forEach(s=>{if(['professionals','hours'].includes(s.id))s.required=false;});data.current_step='catalog';
  const h=await makeOnboarding({data});t.after(()=>h.close());assert.equal(h.document.querySelector('[data-ob-step=hours]'),null);assert.equal(h.document.querySelector('[data-ob-step=professionals]'),null);assert.equal(h.document.querySelector('[data-ob-action=add-service]'),null);assert.ok(h.document.querySelector('[data-ob-action=add-product]'));assert.match(h.element('.ob-progress').textContent,/Etapa 3 de 5/);
});
test('mounting twice does not duplicate handlers or replace navigation',async t=>{
  const h=await makeOnboarding();t.after(()=>h.close());const next=h.window.OrionOnboarding.mount(h.bridge);await next.resolveEntry();assert.equal(h.document.querySelectorAll('.ob-root').length,1);assert.ok(h.document.getElementById('original-nav'));assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
});
test('entry failure stays visible and cannot pretend the setup is complete',async t=>{
  const h=await makeOnboarding({auto:false,respond:()=>{throw Object.assign(Error('Acesso temporariamente indisponível'),{status:503});}});t.after(()=>h.close());await assert.rejects(h.controller.resolveEntry(),/indisponível/);assert.match(h.element('[role=alert]').textContent,/indisponível/);assert.equal(h.completed.length,0);assert.equal(h.document.querySelector('[data-ob-form=create]'),null);
});
test('explicit new-business creation does not reuse the selected business facts',async t=>{
  const h=await makeOnboarding({decision:entry({kind:'choose_business',tenant_id:null,businesses:[{id:tenantA,name:'Empresa existente',role:'owner'}]})});t.after(()=>h.close());h.click('[data-ob-action=create-business]');assert.equal(h.element('[name=name]').value,'');assert.equal(h.element('[name=niche]').value,'');assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
});
test('explicit createNew opens an independent empty setup after checking server entitlement',async t=>{
  const h=await makeOnboarding({auto:false,decision:entry({kind:'dashboard',tenant_id:tenantA,create_allowed:true,businesses:[{id:tenantA,name:'Empresa existente',role:'owner'}]})});t.after(()=>h.close());const result=await h.controller.resolveEntry({createNew:true});assert.equal(result.kind,'onboarding');assert.equal(h.controller.getState().tenantId,null);assert.equal(h.element('[name=name]').value,'');assert.equal(h.element('[name=niche]').value,'');assert.equal(h.calls.length,1);assert.deepEqual(h.calls.filter(c=>c.method!=='GET'),[]);
});
test('revoked create entitlement cannot be bypassed by the createNew client option',async t=>{
  const h=await makeOnboarding({auto:false,decision:entry({kind:'invitation',tenant_id:null,create_allowed:false,invitations:[{tenant_id:tenantA,business_name:'Empresa com convite',status:'claimed'}]})});t.after(()=>h.close());const result=await h.controller.resolveEntry({createNew:true});assert.equal(result.kind,'invitation');assert.equal(h.document.querySelector('[data-ob-form=create]'),null);assert.match(h.element('#setup').textContent,/Aguardando aprovação/);assert.deepEqual(h.calls.filter(c=>c.method!=='GET'),[]);
});
test('disabled rollout takes priority over an explicit createNew request',async t=>{
  const h=await makeOnboarding({auto:false,decision:entry({enabled:false,kind:'dashboard',create_allowed:true})});t.after(()=>h.close());const result=await h.controller.resolveEntry({createNew:true});assert.equal(result.kind,'dashboard');assert.equal(h.element('#setup').hidden,true);assert.equal(h.document.querySelector('[data-ob-form=create]'),null);assert.equal(h.calls.length,1);
});
