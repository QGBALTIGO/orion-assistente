import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {makeSuite,flush,defaultResponse} from './harness.mjs';

const id='d1000000-0000-4000-8000-000000000001';
const service=()=>({id,name:'Corte',price_cents:3500,duration_min:30,sort_order:0,active:true,color:'#8b5cf6'});
const site=()=>({title:'Studio Orion',description:'',slug:'studio-orion',theme_color:'#2e7bf6',show_prices:true,hide_orion_brand:false,version:2,published:false});
async function setup(t,route='servicos',role='owner'){
  const services=[service()],siteData=site(),writes=[];
  const respond=(path,method='GET',payload)=>{
    if(path.endsWith('/services')&&method==='GET')return services;
    if(path.endsWith('/services/'+id)&&method==='PUT'){writes.push({path,method,payload});Object.assign(services[0],payload);return services[0];}
    if(path.endsWith('/management/services/'+id+'/addons')){if(method==='PUT'){writes.push({path,method,payload});return {version:1,items:[]};}return {version:0,items:[]};}
    if(path.endsWith('/professionals'))return [{id:'e1000000-0000-4000-8000-000000000001',name:'Alex',active:true,color:'#31ad52'}];
    if(path.endsWith('/professional_services'))return [];
    if(path.endsWith('/management/service-packages'))return {items:[]};
    if(path.endsWith('/management/site/metrics?days=30'))return {visits:0,daily:[]};
    if(path.endsWith('/management/site')){
      if(method==='PUT'){writes.push({path,method,payload});Object.assign(siteData,payload);siteData.version++;}return siteData;
    }
    return defaultResponse(path);
  };
  const h=await makeSuite({initialRoute:route,role,respond});
  t.after(()=>h.close());await flush();return {h,writes};
}
test('Serviços mostra círculo multicolorido integrado aos presets e input nativo sem retângulo',async t=>{
  const {h}=await setup(t);
  h.click('[data-os-service="'+id+'"]');await flush();
  const input=h.document.querySelector('#osDialog input.os-color-native');
  assert.ok(input);
  const field=input.closest('.os-color-field');
  assert.equal(input.type,'color');
  assert.equal(input.name,'color');
  assert.equal(input.getAttribute('aria-label'),'Personalizar cor');
  assert.equal(field.querySelector('label').getAttribute('for'),'osField-color');
  assert.equal(field.classList.contains('os-color-compact'),true);
  assert.ok(h.document.querySelector('.os-color-presets').contains(field));
  assert.match(field.querySelector('.os-color-custom-caption').textContent,/Personalizar.*Escolher outra cor/,'a roda fica em sua própria linha com descrição clara');
  assert.equal(input.value,'#8b5cf6');
  assert.equal(field.querySelector('.os-color-wheel').style.getPropertyValue('--os-selected-color'),'#8B5CF6');
  assert.equal(h.document.querySelector('[data-os-color="#8b5cf6"]').getAttribute('aria-pressed'),'true');
  assert.equal(h.errors.length,0);
});
test('Cores prontas e personalizadas sincronizam swatch, marcação e payload do serviço',async t=>{
  const {h,writes}=await setup(t);
  h.click('[data-os-service="'+id+'"]');await flush();
  const input=h.document.querySelector('#osDialog input.os-color-native');
  h.click('[data-os-color="#2e7bf6"]');
  assert.equal(input.value,'#2e7bf6');
  assert.equal(input.closest('.os-color-wheel').style.getPropertyValue('--os-selected-color'),'#2E7BF6');
  assert.equal(h.document.querySelector('[data-os-color="#2e7bf6"]').getAttribute('aria-pressed'),'true');
  input.value='#a1b2c3';
  input.dispatchEvent(new h.window.Event('input',{bubbles:true}));
  input.dispatchEvent(new h.window.Event('change',{bubbles:true}));
  assert.equal(input.closest('.os-color-wheel').style.getPropertyValue('--os-selected-color'),'#A1B2C3');
  assert.equal(h.document.querySelectorAll('[data-os-color][aria-pressed=true]').length,0);
  h.submit('[data-os-form=service]');await flush();
  assert.equal(writes.find(x=>x.path.endsWith('/services/'+id))?.payload.color,'#a1b2c3');
  assert.equal(h.errors.length,0);
});
test('Cor na agenda do profissional tem o mesmo seletor circular e valor visível',async t=>{
  const {h}=await setup(t,'funcionarios');
  h.click('[data-os-action=new-professional]');await flush();
  const input=h.document.querySelector('#osDialog input.os-color-native');
  assert.ok(input);
  assert.equal(input.getAttribute('aria-label'),'Cor na agenda');
  assert.equal(input.value,'#31ad52');
  assert.equal(input.closest('.os-color-field').querySelector('.os-color-value').textContent,'#31AD52');
  input.value='#ee1234';
  input.dispatchEvent(new h.window.Event('change',{bubbles:true}));
  assert.equal(input.closest('.os-color-field').querySelector('.os-color-value').textContent,'#EE1234');
  assert.equal(h.errors.length,0);
});
test('Cor principal do Meu Site usa o controle circular e só grava após Salvar',async t=>{
  const {h,writes}=await setup(t,'site');
  h.click('[data-os-site-tab=settings]');await flush();
  const form=h.document.getElementById('osSiteForm'),input=form?.querySelector('input.os-color-native[name=theme_color]');
  assert.ok(input);
  assert.equal(input.getAttribute('aria-label'),'Cor principal');
  assert.equal(input.value,'#2e7bf6');
  input.value='#4099aa';
  input.dispatchEvent(new h.window.Event('input',{bubbles:true}));
  input.dispatchEvent(new h.window.Event('change',{bubbles:true}));
  assert.equal(form.dataset.dirty,'true');
  assert.equal(form.querySelector('.os-color-value').textContent,'#4099AA');
  assert.equal(writes.length,0);
  h.submit('#osSiteForm');await flush();
  assert.equal(writes.find(x=>x.path.endsWith('/management/site'))?.payload.theme_color,'#4099aa');
  assert.equal(h.errors.length,0);
});
test('Pessoas sem permissão não podem abrir o controle de edição de cores',async t=>{
  const {h}=await setup(t,'servicos','viewer');
  h.click('[data-os-service="'+id+'"]');await flush();
  assert.ok(h.document.querySelector('input.os-color-native').matches(':disabled'));
  assert.ok([...h.document.querySelectorAll('[data-os-color]')].every(x=>x.matches(':disabled')));
});
test('CSS mantém roda em círculo, swatch visível e foco, sem estilo retangular antigo',async ()=>{
  const css=await readFile(new URL('../app/static/orion-suite.css',import.meta.url),'utf8');
  const js=await readFile(new URL('../app/static/orion-suite.js',import.meta.url),'utf8');
  assert.match(css,/\.os-color-wheel\{[^}]*border-radius:50%;background:conic-gradient\(/);
  assert.match(css,/\.os-color-selected\{[^}]*background:var\(--os-selected-color/);
  assert.match(css,/\.os-color-wheel>input\.os-color-native\{[^}]*opacity:0!important;cursor:pointer!important/);
  assert.match(css,/\.os-color-wheel:focus-within\{outline:3px/);
  assert.match(css,/@media\(max-width:359px\)\{\.os-color-presets/);
  assert.match(css,/\.os-color-compact\{flex:1 1 100%;width:100%;margin:2px 0 0\}/,'personalização não fica como opção órfã');
  assert.doesNotMatch(css,/\.os-site-config input\[type=color\]\{height:40px;width:72px/);
  assert.doesNotMatch(js,/field\('Personalizar','color','color'/);
  assert.doesNotMatch(js,/field\('Cor na agenda','color','color'/);
  assert.doesNotMatch(js,/field\('Cor principal','theme_color','color'/);
});
