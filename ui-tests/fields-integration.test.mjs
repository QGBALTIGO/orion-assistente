import test from 'node:test';
import assert from 'node:assert/strict';
import {makeSuite,flush,defaultResponse,customer,customerId} from './harness.mjs';

async function customerForm(t,phone=null){
  const record={...customer("Ana D'Ávila-Souza"),phone};
  const saves=[];
  const h=await makeSuite({initialRoute:'clientes',respond:(path,method='GET',data)=>{
    if(path.endsWith('/management/customers/'+customerId)&&method==='PATCH'){saves.push(data);Object.assign(record,data);return record;}
    if(path.endsWith('/management/customers/'+customerId))return record;
    if(path.includes('/management/customers?'))return{items:[record],total:1};
    return defaultResponse(path);
  }});
  t.after(()=>h.close());await flush();
  h.click('[data-os-customer="'+customerId+'"]');await flush();
  return{h,saves,form:h.document.querySelector('[data-os-form=customer]')};
}

test('shared customer phone round-trip preserves DDD55, Unicode name and canonical identity',async t=>{
  const{h,form,saves}=await customerForm(t,'+5555991234567');
  assert.equal(form.elements.phone.value,'(55) 99123-4567');
  assert.equal(form.elements.phone.placeholder,'(00) 00000-0000');
  assert.equal(form.elements.phone.hasAttribute('maxlength'),false);
  h.fill('[data-os-form=customer] [name=phone]','+55 (67) 99999-9901');
  assert.equal(form.elements.phone.value,'(67) 99999-9901');
  h.submit('[data-os-form=customer]');await flush();
  assert.equal(saves.length,1);assert.equal(saves[0].phone,'+5567999999901');
  assert.equal(saves[0].name,"Ana D'Ávila-Souza");assert.deepEqual(h.errors,[]);
});

test('customer phone invalid paste remains visible with associated error and makes no write',async t=>{
  const{h,form,saves}=await customerForm(t);
  h.fill('[data-os-form=customer] [name=phone]','(55) 99123-45678');
  h.submit('[data-os-form=customer]');await flush();
  assert.equal(form.elements.phone.value,'(55) 99123-45678');assert.equal(saves.length,0);
  assert.equal(form.elements.phone.getAttribute('aria-invalid'),'true');
  const id=form.elements.phone.getAttribute('aria-describedby');assert.ok(h.document.getElementById(id));
  h.fill('[data-os-form=customer] [name=phone]','');h.submit('[data-os-form=customer]');await flush();
  assert.equal(saves.length,1);assert.equal(saves[0].phone,null);
});

async function preferencesForm(t,business={}){
  const record={version:2,can_save:true,business:{contact_phone:null,monthly_goal_cents:0,tax_document_kind:'none',tax_document:null,trade_name:'Empresa sintética',legal_name:'',approved_link_hosts:[],...business}},saves=[];
  const h=await makeSuite({initialRoute:'configuracoes',respond:(path,method='GET',data)=>{
    if(path.endsWith('/management/preferences')){if(method==='PUT'){saves.push(data);record.business=data.business;record.version++;}return record;}return defaultResponse(path);
  }});
  t.after(()=>{h.close();assert.deepEqual(h.errors,[]);});await flush();
  const form=h.document.querySelector('[data-os-form=business-preferences]');assert.ok(form);
  const change=(name,value)=>{form.elements[name].value=value;form.elements[name].dispatchEvent(new h.window.Event('change',{bubbles:true}));};
  return{h,form,saves,change};
}

test('CPF and alphanumeric CNPJ have independent drafts, keyboard and canonical save',async t=>{
  const {h,form,saves,change}=await preferencesForm(t);
  change('tax_document_kind','cpf');
  h.fill('[name=tax_document]','52998224725');
  assert.equal(form.elements.tax_document.value,'529.982.247-25');assert.equal(form.elements.tax_document.inputMode,'numeric');
  change('tax_document_kind','cnpj');assert.equal(form.elements.tax_document.value,'');assert.equal(form.elements.tax_document.inputMode,'text');
  h.fill('[name=tax_document]','12abc34501de35');assert.equal(form.elements.tax_document.value,'12.ABC.345/01DE-35');
  change('tax_document_kind','cpf');assert.equal(form.elements.tax_document.value,'529.982.247-25');
  change('tax_document_kind','cnpj');assert.equal(form.elements.tax_document.value,'12.ABC.345/01DE-35');
  h.fill('[name=monthly_goal]','1.234,56');h.submit('[data-os-form=business-preferences]');await flush();
  assert.equal(saves.length,1);assert.equal(saves[0].business.tax_document,'12ABC34501DE35');assert.equal(saves[0].business.monthly_goal_cents,123456);
});

test('historical document stays editable through unrelated preferences while changed invalid DV is blocked',async t=>{
  const {h,form,saves}=await preferencesForm(t,{tax_document_kind:'cnpj',tax_document:'12345678000190'});
  h.fill('[name=trade_name]','Nome legítimo atualizado');h.submit('[data-os-form=business-preferences]');await flush();
  assert.equal(saves.length,1);assert.equal(saves[0].business.tax_document,'12345678000190');
  h.fill('[name=tax_document]','12345678000191');h.submit('[data-os-form=business-preferences]');await flush();
  assert.equal(saves.length,1);const input=h.document.querySelector('[name=tax_document]');assert.equal(input.getAttribute('aria-invalid'),'true');
  assert.ok(input.getAttribute('aria-describedby').split(/\s+/).some(id=>h.document.getElementById(id)?.textContent.includes('CNPJ')));
});

test('clearing optional document type keeps its local draft and excludes it from save',async t=>{
  const {h,form,saves,change}=await preferencesForm(t,{tax_document_kind:'cpf',tax_document:'52998224725'});
  change('tax_document_kind','none');assert.equal(form.elements.tax_document.disabled,true);assert.equal(form.elements.tax_document.value,'');
  change('tax_document_kind','cpf');assert.equal(form.elements.tax_document.value,'529.982.247-25');
  change('tax_document_kind','none');h.submit('[data-os-form=business-preferences]');await flush();assert.equal(saves[0].business.tax_document,null);
});

test('historical selected document type with no value does not add a new required business rule',async t=>{
  const {h,form,saves}=await preferencesForm(t,{tax_document_kind:'cpf',tax_document:null});
  assert.equal(form.elements.tax_document.required,false);
  h.fill('[name=trade_name]','Somente o nome mudou');h.submit('[data-os-form=business-preferences]');await flush();
  assert.equal(saves.length,1);assert.equal(saves[0].business.tax_document,null);assert.equal(saves[0].business.tax_document_kind,'cpf');
});
