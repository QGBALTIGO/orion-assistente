import test from 'node:test';
import assert from 'node:assert/strict';
import { makePanel, jsonResponse } from './panel-harness.mjs';
import { tenantA, tenantB, flush, deferred } from './harness.mjs';
import { fullState } from './onboarding-harness.mjs';
import { listing, item, runtime } from './channels-harness.mjs';

test('the shipped panel enters setup after authentication without creating a company on GET',async t=>{
  const h=await makePanel({role:'member',initialRoute:'dashboard',respond(path){
    if(path==='/api/v1/account/businesses')return [];
    if(path.startsWith('/api/v1/onboarding/entry'))return {enabled:true,kind:'onboarding',create_allowed:true};
  }});t.after(()=>h.close());await flush();
  assert.ok(h.document.querySelector('#appView').classList.contains('ob-gated'));
  assert.match(h.document.querySelector('#orionSetup').textContent,/Vamos configurar seu negócio/);
  assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
  assert.equal(h.errors.length,0);
});

test('the dashboard stays covered while the authenticated entry decision is loading',async t=>{
  const waiting=deferred();const h=await makePanel({role:'member',initialRoute:'dashboard',respond(path){
    if(path.startsWith('/api/v1/onboarding/entry'))return waiting.promise;
  }});t.after(()=>h.close());
  assert.ok(h.document.querySelector('#appView').classList.contains('ob-gated'));
  waiting.resolve({enabled:true,kind:'dashboard',tenant_id:tenantA});await flush();
  assert.equal(h.document.querySelector('#appView').classList.contains('ob-gated'),false);
});

test('a failed entry request keeps a recoverable setup screen, without bypass to an empty dashboard',async t=>{
  const h=await makePanel({role:'member',respond(path){if(path.startsWith('/api/v1/onboarding/entry'))return jsonResponse({detail:'Consulta indisponível'},503);}});t.after(()=>h.close());await flush();
  assert.ok(h.document.querySelector('#appView').classList.contains('ob-gated'));
  assert.match(h.document.querySelector('#orionSetup').textContent,/Consulta indisponível/);
  assert.ok(h.document.querySelector('#orionSetup [data-ob-action=refresh]'));
});

test('an incomplete business resumes its own stored step and does not publish channels',async t=>{
  const h=await makePanel({role:'member',respond(path){
    if(path.startsWith('/api/v1/onboarding/entry'))return {enabled:true,kind:'onboarding',tenant_id:tenantA};
    if(path.endsWith('/onboarding'))return fullState({current_step:'hours'});
  }});t.after(()=>h.close());await flush();
  assert.equal(h.window.OrionOnboarding===undefined,false);
  assert.match(h.document.querySelector('#orionSetup').textContent,/Quando você atende/);
  assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
});

test('logout discards the wizard and a late decision cannot restore the prior account',async t=>{
  const waiting=deferred();const h=await makePanel({role:'member',respond(path){if(path.startsWith('/api/v1/onboarding/entry'))return waiting.promise;}});t.after(()=>h.close());
  h.click('#logout');await flush();waiting.resolve({enabled:true,kind:'onboarding',create_allowed:true});await flush();
  assert.ok(h.document.querySelector('#appView').classList.contains('hidden'));
  assert.equal(h.document.querySelector('#orionSetup').childElementCount,0);
  assert.equal(h.errors.length,0);
});

test('multiple companies block the panel without choosing a business or loading scoped data',async t=>{
  const h=await makePanel({role:'member',respond(path){
    if(path==='/api/v1/account/businesses')return [{id:tenantA,name:'Negócio A',role:'owner'},{id:tenantB,name:'Negócio B',role:'owner'}];
  }});t.after(()=>h.close());await flush();
  assert.match(h.element('#orionSetup').textContent,/mais de uma empresa/);assert.ok(h.element('#retryBusinessContext'));assert.equal(h.document.querySelector('#osTenant'),null);assert.equal(h.calls.filter(c=>c.path.startsWith('/api/v1/tenants/')).length,0);assert.equal(h.calls.filter(c=>c.path.startsWith('/api/v1/qr/')).length,0);
});

test('an existing owner has no additional business action or creation form',async t=>{
  const h=await makePanel({role:'member',respond(path){if(path.startsWith('/api/v1/onboarding/entry'))return {enabled:true,kind:'dashboard',tenant_id:tenantA,create_allowed:true};}});t.after(()=>h.close());await flush();
  assert.equal(h.document.querySelector('[data-os-action=business-new]'),null);assert.equal(h.document.querySelector('#orionSetup [data-ob-form=create]'),null);assert.equal(h.document.querySelector('#tenantForm'),null);assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
});

test('a failed account-business read blocks access and first creation until verification succeeds',async t=>{
 const h=await makePanel({role:'member',initialRoute:'dashboard',respond(path){
  if(path==='/api/v1/account/businesses')return jsonResponse({detail:'Consulta indisponível'},503);
  if(path.startsWith('/api/v1/onboarding/entry'))return {enabled:true,kind:'onboarding',create_allowed:true};
 }});t.after(()=>h.close());await flush();assert.match(h.element('#orionSetup').textContent,/Consulta indisponível/);assert.ok(h.element('#retryBusinessContext'));assert.equal(h.document.querySelector('[data-ob-form=create]'),null);assert.equal(h.element('#appView').classList.contains('ob-gated'),true);assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);assert.equal(h.calls.some(c=>c.path.startsWith('/api/v1/tenants/')),false);
});

test('the wizard tenant callback refreshes the menu without restarting its stored hours step',async t=>{
 const h=await makePanel({role:'member',initialRoute:'dashboard',respond(path){
  if(path.startsWith('/api/v1/onboarding/entry'))return {enabled:true,kind:'onboarding',tenant_id:tenantA};
  if(path.endsWith('/onboarding'))return fullState({current_step:'hours'});
 }});t.after(()=>h.close());await flush();assert.match(h.element('#orionSetup').textContent,/Quando você atende/);
 assert.equal(h.calls.filter(c=>c.path.startsWith('/api/v1/onboarding/entry')).length,1);assert.equal(h.calls.filter(c=>c.path.endsWith('/onboarding')).length,1);assert.equal(h.calls.filter(c=>c.path==='/api/v1/account/businesses').length,2);assert.equal(h.element('#appView').classList.contains('ob-gated'),true);
});

test('creating the first business binds the verified account and preserves the returned next step',async t=>{
 let created=false;const h=await makePanel({role:'member',initialRoute:'dashboard',respond(path,method){
  if(path==='/api/v1/account/businesses')return created?[{id:tenantA,name:'Meu negócio',role:'owner'}]:[];
  if(path.startsWith('/api/v1/onboarding/entry'))return {enabled:true,kind:'onboarding',tenant_id:created?tenantA:null,create_allowed:!created,businesses:[]};
  if(path==='/api/v1/onboarding/businesses'&&method==='POST'){created=true;return fullState({tenant_id:tenantA,name:'Meu negócio',current_step:'location'});}
 }});t.after(()=>h.close());await flush();
 for(const [name,value] of Object.entries({name:'Meu negócio',niche:'barbearia',activity:'services',contact_phone:'(67) 99999-9901'}))h.fill('#orionSetup [name='+name+']',value);
 h.submit('[data-ob-form=create]');await flush();assert.match(h.element('#orionSetup').textContent,/Onde você atende/);assert.equal(h.element('#businessTenant').value,tenantA);assert.equal(h.element('#appView').classList.contains('ob-gated'),true);assert.equal(h.calls.filter(c=>c.path==='/api/v1/onboarding/businesses').length,1);assert.equal(h.calls.filter(c=>c.path.startsWith('/api/v1/onboarding/entry')).length,1);assert.equal(h.errors.length,0);
});

test('a lost first-creation response verifies the new account binding before recovering without another POST',async t=>{
 let created=false,saved;const h=await makePanel({role:'member',initialRoute:'dashboard',respond(path,method,payload){
  if(path==='/api/v1/account/businesses')return created?[{id:tenantA,name:'Meu negócio',role:'owner'}]:[];
  if(path.startsWith('/api/v1/onboarding/entry'))return {enabled:true,kind:'onboarding',tenant_id:created?tenantA:null,create_allowed:!created,businesses:[]};
  if(path==='/api/v1/onboarding/businesses'&&method==='POST'){created=true;saved=fullState({tenant_id:tenantA,name:'Meu negócio',current_step:'location',last_operation_key:payload.idempotency_key});return jsonResponse({detail:'Resposta perdida após salvar'},503);}
  if(path.endsWith('/onboarding'))return saved;
 }});t.after(()=>h.close());await flush();
 for(const [name,value] of Object.entries({name:'Meu negócio',niche:'barbearia',activity:'services',contact_phone:'(67) 99999-9901'}))h.fill('#orionSetup [name='+name+']',value);
 h.submit('[data-ob-form=create]');await flush();h.click('[data-ob-action=refresh]');await flush();
 assert.equal(h.element('#businessTenant').value,tenantA);assert.match(h.element('#orionSetup').textContent,/servidor confirmou/);assert.equal(h.document.querySelector('[data-ob-action=retry-operation]'),null);assert.equal(h.calls.filter(c=>c.path==='/api/v1/onboarding/businesses').length,1);
 const accountReads=h.calls.map((c,i)=>c.path==='/api/v1/account/businesses'?i:-1).filter(i=>i>=0),recoveryRead=h.calls.findIndex(c=>c.path.endsWith('/onboarding'));
 assert.equal(accountReads.length,2);assert.ok(recoveryRead>accountReads[1]);assert.equal(h.errors.length,0);
});

for(const activity of ['services','products'])test('completed '+activity+' business goes straight to Dashboard without an extra tap',async t=>{
 const state=fullState({current_step:'review'});state.configuration.activity=activity;
 state.steps=state.steps.map(row=>row.id==='catalog'?{...row,id:'profile',label:'Perfil da empresa'}:row.id==='channels'?{...row,id:'goals',label:'Desafios e objetivos'}:row);
 state.configuration.business_metrics={operating_age:'not_started',team_size:'none',monthly_revenue:'none',monthly_volume:'none'};
 state.configuration.business_priorities={main_challenge:'whatsapp',desired_outcome:'save_time',respondent_role:'owner'};
 const h=await makePanel({role:'member',initialRoute:'catalogo',respond(path,method){
  if(path.startsWith('/api/v1/onboarding/entry'))return {enabled:true,kind:'onboarding',tenant_id:tenantA};
  if(path.endsWith('/onboarding'))return state;
  if(path.endsWith('/onboarding/complete')&&method==='POST')return {...state,status:'complete',version:4};
 }});t.after(()=>h.close());await flush();
 assert.equal(h.element('#appView').classList.contains('ob-gated'),true);
 h.element('#orionSetup [name=acknowledged]').checked=true;
 h.submit('[data-ob-form=complete]');await flush();
 assert.equal(h.element('#appView').classList.contains('ob-gated'),false);
 assert.equal(h.element('#orionSetup').hidden,true);
 assert.equal(h.document.querySelector('#page-suite')?.dataset.osCurrentRoute,'dashboard');
 assert.equal(h.document.querySelector('#page-suite h1')?.textContent,'Dashboard');
 assert.equal(new URL(h.window.location.href).searchParams.get('aba'),'dashboard');
 assert.equal(h.calls.filter(c=>c.method!=='GET').length,1);
 assert.equal(h.calls.filter(c=>/\/commerce\/status/.test(c.path)).length,0);
 assert.equal(h.calls.filter(c=>/\/business\/profile$/.test(c.path)).length,0);
 assert.deepEqual(h.errors,[]);
});

test('WhatsApp settings mounts managed public APIs and navigation discards a late channel status',async t=>{
 const waiting=deferred(),row=item({enabled:true});const h=await makePanel({role:'member',initialRoute:'dashboard',respond(path){
  if(path.endsWith('/qr-channels'))return listing([row]);
  if(path.endsWith('/qr-channels/'+row.id+'/status'))return waiting.promise;
 }});t.after(()=>h.close());await flush();const start=h.calls.length;
 await h.window.OrionSuite.open('configuracoes');h.click('[data-os-settings=whatsapp]');await flush();assert.ok(h.calls.slice(start).some(c=>c.path.endsWith('/qr-channels')));
 assert.equal(h.calls.slice(start).filter(c=>c.path.startsWith('/api/v1/qr/')).length,0);assert.equal(h.calls.slice(start).filter(c=>c.method!=='GET').length,0);
 await h.window.OrionSuite.open('clientes');waiting.resolve(runtime(row,'connected'));await flush();assert.equal(h.document.querySelector('#osManagedChannels'),null);assert.equal(h.document.querySelector('[data-qc-action=start]'),null);assert.equal(h.errors.length,0);
});

test('logout unmounts managed connections before an in-flight channel list returns',async t=>{
 const waiting=deferred();const h=await makePanel({role:'member',initialRoute:'dashboard',respond(path){if(path.endsWith('/qr-channels'))return waiting.promise;}});t.after(()=>h.close());await flush();
 await h.window.OrionSuite.open('configuracoes');h.click('[data-os-settings=whatsapp]');await flush();assert.ok(h.calls.some(c=>c.path.endsWith('/qr-channels')));h.click('#logout');await flush();waiting.resolve(listing());await flush();assert.equal(h.element('#appView').classList.contains('hidden'),true);assert.equal(h.document.querySelector('[data-qc-action=start]'),null);assert.equal(h.errors.length,0);
});

test('Google setup marks only explicit boolean ready as complete without connecting automatically',async t=>{
 const h=await makePanel({role:'member',initialRoute:'dashboard',respond(path){if(path.endsWith('/google/status'))return {configured:false,connected:false,capabilities:{can_connect:false},setup:{checks:[{label:'Credencial real',ready:true},{label:'Texto falso',ready:'false'},{label:'Campo ausente'},{label:'Pendente real',ready:false}]}};}});t.after(()=>h.close());await flush();
 await h.window.OrionSuite.open('configuracoes');h.click('[data-os-settings=calendar]');await flush();const text=h.element('#osGoogleStatus').textContent;assert.match(text,/Pronto: Credencial real/);for(const label of ['Texto falso','Campo ausente','Pendente real'])assert.match(text,new RegExp('Pendente: '+label));assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
});
