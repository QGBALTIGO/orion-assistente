import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {makeSuite,flush,defaultResponse} from './harness.mjs';

const professionalId='c1000000-0000-4000-8000-000000000001';
const cutId='c2000000-0000-4000-8000-000000000002';
const beardId='c3000000-0000-4000-8000-000000000003';
function fixtureData(){
  const staff=[{id:professionalId,name:'Barbeiro sintético',active:true,specialty:'Barbeiro',
    color:'#31ad52',image_url:null,commission_mode:'percent',
    commission_percent:10,commission_config_version:1,accepts_appointments:true}];
  const services=[
    {id:cutId,name:'Corte',catalog_kind:'base',duration_min:40,price_cents:3500,price_mode:'from',active:true},
    {id:beardId,name:'Barba',catalog_kind:'base',duration_min:20,price_cents:2000,price_mode:'fixed',active:true},
  ];
  const links=services.map((s,i)=>({id:'link-'+i,professional_id:professionalId,service_id:s.id}));
  const rules=[];
  const saves=[];
  const responder=(path,method='GET',payload)=>{
    if(path.endsWith('/professionals')&&method==='GET')return staff;
    if(path.endsWith('/services')&&method==='GET')return services;
    if(path.endsWith('/professional_services')&&method==='GET')return links;
    if(path.endsWith('/management/team'))return {can_manage:true,members:[],invitations:[]};
    if(path.endsWith('/management/professionals/commission-rules'))return {items:rules};
    if(path.endsWith('/management/professionals/'+professionalId+'/configure')&&method==='PUT'){
      saves.push({method,path,payload});
      const p=staff[0];Object.assign(p,payload,{commission_config_version:p.commission_config_version+1});
      rules.splice(0,rules.length,...payload.commission_rules.map(rule=>({...rule,professional_id:professionalId})));
      return {...p};
    }
    return defaultResponse(path);
  };
  return {staff,services,links,rules,saves,responder};
}
async function fixture(t,{role='owner'}={}){
  const data=fixtureData(),h=await makeSuite({initialRoute:'funcionarios',role,respond:data.responder});
  t.after(()=>h.close());await flush();return {h,...data};
}
function change(h,selector,value){
  const element=h.document.querySelector(selector);
  assert.ok(element,selector);
  element.value=value;
  element.dispatchEvent(new h.window.Event('change',{bubbles:true}));
  return element;
}
test('percentual antigo exibe apenas comissão geral e mantém catálogo compacto',async t=>{
  const {h}=await fixture(t);
  assert.match(h.document.querySelector('.os-staff-details').textContent,/Comissão 10%/);
  h.click('[data-os-professional="'+professionalId+'"]');await flush();
  const form=h.document.querySelector('[data-os-form=professional]');
  assert.ok(form);
  assert.equal(form.elements.commission_mode.value,'percent');
  assert.equal(form.querySelectorAll('[data-os-commission-row]').length,2);
  for(const row of form.querySelectorAll('[data-os-commission-controls]')){
    assert.ok(row.classList.contains('hidden'));
    assert.equal(row.querySelector('input').disabled,true);
  }
  assert.equal(h.errors.length,0);
});
test('mudança para comissão personalizada permite % e R$ e salva atomicamente',async t=>{
  const {h,saves}=await fixture(t);
  h.click('[data-os-professional="'+professionalId+'"]');await flush();
  const form=h.document.querySelector('[data-os-form=professional]');
  change(h,'[name=commission_mode]','per_service');await flush();
  const serviceRows=[...form.querySelectorAll('[data-os-commission-row]')];
  assert.equal(serviceRows.length,2);
  assert.ok(serviceRows.every(r=>!r.querySelector('[data-os-commission-controls]').classList.contains('hidden')));
  change(h,'[name="commission_kind_'+beardId+'"]','fixed');await flush();
  h.fill('[name="commission_value_'+cutId+'"]','10');
  h.fill('[name="commission_value_'+beardId+'"]','10,00');
  h.submit('[data-os-form=professional]');await flush();
  assert.equal(saves.length,1);
  assert.equal(saves[0].payload.commission_mode,'per_service');
  assert.equal(saves[0].payload.expected_version,1);
  assert.deepEqual(new Set(saves[0].payload.service_ids),new Set([cutId,beardId]));
  const rules=saves[0].payload.commission_rules;
  assert.equal(rules.find(r=>r.service_id===cutId).commission_percent,10);
  assert.equal(rules.find(r=>r.service_id===beardId).commission_fixed_cents,1000);
  assert.match(h.document.querySelector('.os-staff-details').textContent,/Comissão por serviço/);
  assert.equal(h.errors.length,0);
});
test('desmarcar serviço desliga input obrigatório, nunca envia regra órfã',async t=>{
  const {h,saves}=await fixture(t);
  h.click('[data-os-professional="'+professionalId+'"]');await flush();
  const form=h.document.querySelector('[data-os-form=professional]');
  change(h,'[name=commission_mode]','per_service');await flush();
  const checkbox=form.querySelector('[name=service_ids][value="'+beardId+'"]');
  checkbox.checked=false;
  checkbox.dispatchEvent(new h.window.Event('change',{bubbles:true}));await flush();
  const disabled=form.querySelector('[name="commission_value_'+beardId+'"]');
  assert.equal(disabled.required,false);
  assert.equal(disabled.disabled,true);
  h.submit('[data-os-form=professional]');await flush();
  assert.equal(saves.length,1);
  assert.deepEqual(saves[0].payload.service_ids,[cutId]);
  assert.deepEqual(saves[0].payload.commission_rules.map(x=>x.service_id),[cutId]);
  assert.equal(h.errors.length,0);
});
test('leitor não pode salvar remuneração de profissional',async t=>{
  const {h,saves}=await fixture(t,{role:'viewer'});
  h.click('[data-os-professional="'+professionalId+'"]');await flush();
  const form=h.document.querySelector('[data-os-form=professional]');
  assert.equal(form.elements.commission_mode.disabled,true);
  assert.equal(form.querySelector('button[type=submit]').disabled,true);
  h.submit('[data-os-form=professional]');await flush();
  assert.equal(saves.length,0);
});
test('controles compactos têm limites de largura e não usam inline styles para forçar telas',async()=>{
  const css=await readFile(new URL('../app/static/orion-suite.css',import.meta.url),'utf8');
  assert.match(css,/#osDialog \.os-commission-service-controls\{[^}]*display:flex/);
  assert.match(css,/#osDialog \.os-commission-service-controls input\{[^}]*max-width:145px/);
  assert.match(css,/@media\(max-width:420px\)/);
});
