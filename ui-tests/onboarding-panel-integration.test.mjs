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

test('multiple companies require a choice before evaluating their setup',async t=>{
  const h=await makePanel({role:'member',respond(path){
    if(path==='/api/v1/account/businesses')return [{id:tenantA,name:'Negócio A',role:'owner'},{id:tenantB,name:'Negócio B',role:'owner'}];
    if(path.startsWith('/api/v1/onboarding/entry'))return {enabled:true,kind:'choose_business',create_allowed:true,businesses:[{id:tenantA,name:'Negócio A'},{id:tenantB,name:'Negócio B'}]};
  }});t.after(()=>h.close());await flush();
  assert.ok(h.calls.some(c=>c.path==='/api/v1/onboarding/entry'));
  assert.match(h.document.querySelector('#orionSetup').textContent,/Escolha a empresa/);
  assert.equal(h.calls.filter(c=>c.path.endsWith('/onboarding')).length,0);
});

test('the explicit new-business action opens a separate creation form for an existing owner',async t=>{
  const h=await makePanel({role:'member',respond(path){if(path.startsWith('/api/v1/onboarding/entry'))return {enabled:true,kind:'dashboard',tenant_id:tenantA,create_allowed:true};}});t.after(()=>h.close());await flush();
  h.click('[data-os-action=business-new]');await flush();
  assert.ok(h.document.querySelector('#orionSetup [data-ob-form=create]'));
  assert.ok(h.document.querySelector('#appView').classList.contains('ob-gated'));
  assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
});

test('a failed company menu read still resolves the authenticated entry into a recoverable wizard',async t=>{
 const h=await makePanel({role:'member',initialRoute:'dashboard',respond(path){
  if(path==='/api/v1/account/businesses')return jsonResponse({detail:'Lista indisponível'},503);
  if(path.startsWith('/api/v1/onboarding/entry'))return {enabled:true,kind:'onboarding',create_allowed:true};
 }});t.after(()=>h.close());await flush();assert.match(h.element('#orionSetup').textContent,/Vamos configurar seu negócio/);assert.equal(h.element('#appView').classList.contains('ob-gated'),true);assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
});

test('the wizard tenant callback refreshes the menu without restarting its stored hours step',async t=>{
 const h=await makePanel({role:'member',initialRoute:'dashboard',respond(path){
  if(path.startsWith('/api/v1/onboarding/entry'))return {enabled:true,kind:'onboarding',tenant_id:tenantA};
  if(path.endsWith('/onboarding'))return fullState({current_step:'hours'});
 }});t.after(()=>h.close());await flush();assert.match(h.element('#orionSetup').textContent,/Quando você atende/);
 assert.equal(h.calls.filter(c=>c.path.startsWith('/api/v1/onboarding/entry')).length,1);assert.equal(h.calls.filter(c=>c.path.endsWith('/onboarding')).length,1);assert.equal(h.calls.filter(c=>c.path==='/api/v1/account/businesses').length,2);assert.equal(h.element('#appView').classList.contains('ob-gated'),true);
});

test('creating a second business updates the navigation and preserves the returned next step',async t=>{
 let created=false;const h=await makePanel({role:'member',initialRoute:'dashboard',respond(path,method){
  if(path==='/api/v1/account/businesses')return [{id:tenantA,name:'Negócio A',role:'owner'},...(created?[{id:tenantB,name:'Negócio B',role:'owner'}]:[])];
  if(path.startsWith('/api/v1/onboarding/entry'))return {enabled:true,kind:'dashboard',tenant_id:tenantA,create_allowed:true};
  if(path==='/api/v1/onboarding/businesses'&&method==='POST'){created=true;return fullState({tenant_id:tenantB,name:'Negócio B',current_step:'location'});}
 }});t.after(()=>h.close());await flush();h.click('[data-os-action=business-new]');await flush();
 for(const [name,value] of Object.entries({name:'Negócio B',niche:'barbearia',activity:'services',timezone:'America/Cuiaba'}))h.fill('#orionSetup [name='+name+']',value);
 h.submit('[data-ob-form=create]');await flush();assert.match(h.element('#orionSetup').textContent,/Onde você atende/);assert.equal(h.element('#businessTenant').value,tenantB);assert.equal(h.element('#appView').classList.contains('ob-gated'),true);assert.equal(h.calls.filter(c=>c.path==='/api/v1/onboarding/businesses').length,1);assert.equal(h.calls.filter(c=>c.path.startsWith('/api/v1/onboarding/entry')).length,2);assert.equal(h.errors.length,0);
});

test('completion keeps real channel statuses visible until the user explicitly opens the panel',async t=>{
 const state=fullState({current_step:'review'});const h=await makePanel({role:'member',initialRoute:'dashboard',respond(path,method){
  if(path.startsWith('/api/v1/onboarding/entry'))return {enabled:true,kind:'onboarding',tenant_id:tenantA};
  if(path.endsWith('/onboarding'))return state;
  if(path.endsWith('/onboarding/complete')&&method==='POST')return {...state,status:'complete',version:4};
 }});t.after(()=>h.close());await flush();h.element('#orionSetup [name=acknowledged]').checked=true;h.submit('[data-ob-form=complete]');await flush();
 assert.equal(h.element('#appView').classList.contains('ob-gated'),true);assert.match(h.element('#orionSetup').textContent,/WhatsApp|Google/);assert.ok(h.element('[data-ob-action=open-dashboard]'));
 h.click('[data-ob-action=open-dashboard]');await flush();assert.equal(h.element('#appView').classList.contains('ob-gated'),false);assert.equal(h.element('#orionSetup').hidden,true);assert.equal(h.calls.filter(c=>c.method!=='GET').length,1);
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
