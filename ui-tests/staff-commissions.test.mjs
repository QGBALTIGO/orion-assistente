import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {makeSuite,flush,defaultResponse} from './harness.mjs';

const professionalId='b0000000-0000-4000-8000-000000000001';
const cut='b0000000-0000-4000-8000-000000000002';
const beard='b0000000-0000-4000-8000-000000000003';
const services=[
  {id:cut,name:'Corte tradicional',duration_min:40,price_cents:3500,catalog_kind:'base',price_mode:'from',active:true},
  {id:beard,name:'Barba',duration_min:20,price_cents:2000,catalog_kind:'base',price_mode:'fixed',active:true},
];
const fresh=()=>({
  id:professionalId,name:'Alex de teste',specialty:'Barbeiro',active:true,
  accepts_appointments:true,commission_mode:'percent',commission_percent:25,
  commission_rules:{},commission_plan_version:1,color:'#31ad52',image_url:null,
});
function responder(professional,events){
  let links=[{id:'10000000-0000-4000-8000-000000000002',professional_id:professionalId,service_id:cut},
    {id:'10000000-0000-4000-8000-000000000003',professional_id:professionalId,service_id:beard}];
  return (path,method='GET',data)=>{
    if(path.endsWith('/professionals')&&method==='GET')return [professional];
    if(path.endsWith('/services')&&method==='GET')return services;
    if(path.endsWith('/professional_services')&&method==='GET')return links;
    if(path.includes('/management/team'))return {can_manage:true,members:[],invitations:[]};
    if(path.endsWith('/professionals/'+professionalId)&&method==='PUT'){
      events.push({action:'profile',data});Object.assign(professional,data);
      return {id:professionalId,...data};
    }
    if(path.endsWith('/management/professionals/'+professionalId+'/commission-plan')&&method==='PUT'){
      events.push({action:'plan',data});
      assert.equal(data.expected_version,professional.commission_plan_version);
      professional.commission_mode=data.mode;
      professional.commission_plan_version++;
      for(const row of data.services){
        if(row.kind){
          professional.commission_rules[row.service_id]=row.kind==='fixed'?
            {kind:'fixed',fixed_cents:row.fixed_cents}:{kind:'percent',percent:row.percent};
        }
      }
      links=data.services.map((x,index)=>({
        id:'30000000-0000-4000-8000-'+String(index+1).padStart(12,'0'),
        professional_id:professionalId,service_id:x.service_id}));
      return {professional_id:professionalId,commission_mode:data.mode,
        commission_percent:professional.commission_percent,
        commission_plan_version:professional.commission_plan_version,
        commission_rules:professional.commission_rules,service_ids:data.services.map(x=>x.service_id)};
    }
    return defaultResponse(path);
  };
}
async function fixture(t,{role='owner',profile=fresh()}={}){
  const events=[];
  const h=await makeSuite({initialRoute:'funcionarios',role,respond:responder(profile,events)});
  t.after(()=>h.close());await flush();
  return {h,events,profile};
}
const form=h=>h.document.querySelector('[data-os-form=professional]');
function setRadio(h,mode){
  const radio=form(h).querySelector('[name=commission_mode][value='+mode+']');
  assert.ok(radio);radio.checked=true;
  radio.dispatchEvent(new h.window.Event('change',{bubbles:true}));
}
function ruleInput(h,service){return form(h).querySelector('[name="commission_value_'+service+'"]');}
function setRule(h,service,kind,value){
  const type=form(h).querySelector('[name="commission_kind_'+service+'"]');
  assert.ok(type);type.value=kind;
  type.dispatchEvent(new h.window.Event('change',{bubbles:true}));
  const input=ruleInput(h,service);
  input.value=value;input.dispatchEvent(new h.window.Event('input',{bubbles:true}));
}

test('nova regra de comissão é compacta, geral por padrão e não confunde preço do serviço',async t=>{
  const {h}=await fixture(t);
  assert.match(h.document.querySelector('.os-staff-details').textContent,/Comissão 25%/);
  h.click('[data-os-professional="'+professionalId+'"]');await flush();
  const dialog=form(h);
  assert.ok(dialog);
  assert.equal(dialog.elements.commission_mode.value,'percent');
  assert.ok(dialog.querySelector('#osStaffGeneralCommission'));
  assert.equal(dialog.querySelector('#osStaffGeneralCommission').hidden,false);
  assert.equal(dialog.querySelectorAll('.os-staff-commission-choices label').length,2);
  assert.equal(dialog.querySelectorAll('[data-commission-row]').length,2);
  assert.equal(dialog.querySelectorAll('.os-staff-service-rule:not([hidden])').length,0);
  assert.match(dialog.textContent,/Corte tradicional/);
  assert.match(dialog.textContent,/A partir de R\$\s*35,00/);
  assert.deepEqual(h.errors,[]);
});

test('por serviço grava % e R$ em centavos, preserva seleção e não duplica PUTs',async t=>{
  const {h,events}=await fixture(t);
  h.click('[data-os-professional="'+professionalId+'"]');await flush();
  setRadio(h,'per_service');
  assert.equal(form(h).querySelector('#osStaffGeneralCommission').hidden,true);
  assert.equal(form(h).querySelectorAll('.os-staff-service-rule:not([hidden])').length,2);
  setRule(h,cut,'fixed','10,00');
  setRule(h,beard,'percent','12,50');
  assert.equal(ruleInput(h,cut).required,true);
  h.submit('[data-os-form=professional]');await flush();
  assert.equal(events.length,2);
  assert.equal(events[0].action,'profile');
  assert.equal(events[1].action,'plan');
  assert.equal(events[1].data.mode,'per_service');
  assert.equal(events[1].data.expected_version,1);
  assert.deepEqual(JSON.parse(JSON.stringify(events[1].data.services)),[
    {service_id:cut,kind:'fixed',fixed_cents:1000},
    {service_id:beard,kind:'percent',percent:'12.50'},
  ]);
  assert.match(h.document.querySelector('.os-staff-details').textContent,/Comissão por serviço/);
  assert.deepEqual(h.errors,[]);
});

test('editar profissional recarrega taxas atuais e alternar para geral preserva regras',async t=>{
  const profile=fresh();
  profile.commission_mode='per_service';
  profile.commission_rules={
    [cut]:{kind:'fixed',fixed_cents:1000},
    [beard]:{kind:'percent',percent:'12.50'},
  };
  profile.commission_plan_version=3;
  const {h,events}=await fixture(t,{profile});
  h.click('[data-os-professional="'+professionalId+'"]');await flush();
  assert.equal(form(h).elements.commission_mode.value,'per_service');
  assert.equal(form(h).querySelector('[name="commission_kind_'+cut+'"]').value,'fixed');
  assert.equal(ruleInput(h,cut).value,'10,00');
  assert.equal(form(h).querySelector('[name="commission_kind_'+beard+'"]').value,'percent');
  setRadio(h,'percent');
  assert.equal(form(h).querySelector('#osStaffGeneralCommission').hidden,false);
  assert.equal(ruleInput(h,cut).disabled,true);
  h.submit('[data-os-form=professional]');await flush();
  assert.equal(events.at(-1).data.mode,'percent');
  assert.equal(events.at(-1).data.expected_version,3);
  assert.ok(events.at(-1).data.services.every(x=>Object.keys(x).length===1));
  assert.deepEqual(profile.commission_rules[cut],{kind:'fixed',fixed_cents:1000});
  assert.deepEqual(h.errors,[]);
});

test('regras incompletas ou valores acima de 100% não são enviados',async t=>{
  const {h,events}=await fixture(t);
  h.click('[data-os-professional="'+professionalId+'"]');await flush();
  setRadio(h,'per_service');
  setRule(h,cut,'percent','101');
  setRule(h,beard,'fixed','10,00');
  h.submit('[data-os-form=professional]');await flush();
  assert.equal(events.length,0);
  assert.match(h.document.getElementById('osDialog').textContent,/100%/);
  assert.deepEqual(h.errors,[]);
});

test('viewer vê a regra mas não pode gravar alterações financeiras',async t=>{
  const profile=fresh();profile.commission_mode='per_service';
  profile.commission_rules={[cut]:{kind:'fixed',fixed_cents:1000}};
  const {h,events}=await fixture(t,{role:'viewer',profile});
  h.click('[data-os-professional="'+professionalId+'"]');await flush();
  assert.ok(form(h).querySelector('fieldset').disabled);
  assert.equal(events.length,0);
  assert.deepEqual(h.errors,[]);
});

test('CSS da comissão tem campos pequenos, suporte a 320px e foco acessível',async()=>{
  const css=await readFile(new URL('../app/static/orion-suite.css',import.meta.url),'utf8');
  assert.match(css,/\.os-staff-commission-choices>label\{[^}]*min-height:42px/);
  assert.match(css,/\.os-staff-service-rule\[hidden\]\{display:none!important\}/);
  assert.match(css,/@media\(max-width:420px\)/);
  assert.match(css,/os-staff-rule-kind/);
  assert.match(css,/focus-within/);
});
