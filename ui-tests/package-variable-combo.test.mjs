import test from 'node:test';
import assert from 'node:assert/strict';
import {makeSuite,flush,defaultResponse} from './harness.mjs';

const ids=[
  'b0000000-0000-4000-8000-000000000001',
  'b0000000-0000-4000-8000-000000000002',
  'b0000000-0000-4000-8000-000000000003',
];
const services=()=>[
  {id:ids[0],name:'Corte',catalog_kind:'base',price_cents:4000,price_mode:'fixed',duration_min:40,active:true},
  {id:ids[1],name:'Hidratação',catalog_kind:'base',price_cents:2000,price_mode:'from',duration_min:30,active:true},
  {id:ids[2],name:'Barba',catalog_kind:'base',price_cents:2500,price_mode:'fixed',duration_min:20,active:true},
];
function responder({writes=[],packs=[]}={}){
  return (path,method='GET',payload)=>{
    if(path.endsWith('/services')&&method==='GET')return services();
    if(path.endsWith('/professionals')&&method==='GET')return [];
    if(path.endsWith('/professional_services')&&method==='GET')return [];
    if(path.endsWith('/management/service-packages')&&method==='GET')return {items:packs};
    if(path.endsWith('/management/service-packages')&&method==='POST'){
      writes.push(payload);
      return {id:'new-combo-synthetic',version:1,price_mode:payload.service_ids.includes(ids[1])?'from':'fixed',...payload};
    }
    if(/\/management\/service-packages\/[^/]+$/.test(path)&&method==='PATCH'){
      writes.push(payload);
      return {id:path.split('/').at(-1),version:2,price_mode:payload.service_ids.includes(ids[1])?'from':'fixed',...payload};
    }
    return defaultResponse(path);
  };
}
async function fixture(t,opts={}){
  const writes=[],h=await makeSuite({initialRoute:'servicos',respond:responder({writes,...opts})});
  t.after(()=>h.close());await flush();return {h,writes};
}
function toggleService(h,id){
  const box=h.document.querySelector('[data-os-form=package] [name=service_ids][value="'+id+'"]');
  assert.ok(box,id);
  box.checked=!box.checked;
  box.dispatchEvent(new h.window.Event('change',{bubbles:true}));
}
function screen(h){return h.document.querySelector('[data-os-form=package]');}

test('seleção dinâmica muda para A partir de automaticamente, sem exigir seleção manual',async t=>{
  const {h,writes}=await fixture(t);
  h.click('[data-os-action=new-package]');await flush();
  const form=screen(h);
  assert.ok(form);
  toggleService(h,ids[0]);await flush();
  toggleService(h,ids[1]);await flush();
  assert.match(h.document.getElementById('osPackageSummary').textContent,/Tipo de preço do combo.*A partir de/);
  assert.match(h.document.getElementById('osPackageSummary').textContent,/A partir de R\$\s*60,00/);
  assert.equal(h.document.getElementById('osPackageVariableHint').classList.contains('hidden'),false);
  assert.match(form.elements.price.closest('.os-field').querySelector('label').textContent,/Preço mínimo do combo/);
  assert.equal(form.elements.price.value,'60,00');
  form.elements.name.value='Combo corte + hidratação';
  h.submit('[data-os-form=package]');await flush();
  assert.equal(writes.length,1);
  assert.deepEqual(writes[0].service_ids,ids.slice(0,2));
  assert.equal(writes[0].price_cents,6000);
  assert.equal(Object.hasOwn(writes[0],'price_mode'),false,'tipo de preço deriva do servidor');
  assert.equal(h.errors.length,0);
});

test('remover o componente variável restaura preço fixo e esconde explicação',async t=>{
  const {h}=await fixture(t);
  h.click('[data-os-action=new-package]');await flush();
  toggleService(h,ids[0]);toggleService(h,ids[1]);await flush();
  toggleService(h,ids[1]);toggleService(h,ids[2]);await flush();
  assert.match(h.document.getElementById('osPackageSummary').textContent,/Tipo de preço do combo.*Fixo/);
  assert.equal(h.document.getElementById('osPackageVariableHint').classList.contains('hidden'),true);
  assert.equal(screen(h).elements.price.closest('.os-field').querySelector('label').textContent,'Preço do combo (R$)');
  assert.equal(h.errors.length,0);
});

test('editar um combo já personalizado preserva o preço mínimo, imagem e resumo correto',async t=>{
  const pack={id:'a0000000-0000-4000-8000-000000000009',name:'Corte e hidratação',
    version:2,service_ids:ids.slice(0,2),price_cents:5000,effective_price_cents:5000,
    price_mode:'from',active:true,featured:true,color:'#8b5cf6',
    image_url:'https://example.invalid/photo.png',description:'Descrição sintética'};
  const {h,writes}=await fixture(t,{packs:[pack]});
  h.click('[data-os-package="'+pack.id+'"]');await flush();
  const form=screen(h);
  assert.equal(form.elements.price.value,'50,00');
  assert.match(h.document.getElementById('osPackageSummary').textContent,/Tipo de preço do combo.*A partir de/);
  assert.match(form.elements.price.closest('.os-field').querySelector('label').textContent,/Preço mínimo/);
  assert.equal(form.elements.name.value,pack.name);
  assert.equal(form.elements.image_url.value,pack.image_url);
  h.submit('[data-os-form=package]');await flush();
  assert.equal(writes.length,1);
  assert.equal(writes[0].expected_version,2);
  assert.equal(writes[0].price_cents,5000);
  assert.equal(writes[0].image_url,pack.image_url);
  assert.equal(h.errors.length,0);
});

test('listagem de combos anuncia mínimo real para modo A partir de',async t=>{
  const pack={id:'a0000000-0000-4000-8000-000000000008',name:'Combo variável',
    version:1,service_ids:ids.slice(0,2),price_cents:0,
    effective_price_cents:6000,price_mode:'from',active:true,featured:false};
  const {h}=await fixture(t,{packs:[pack]});
  const summary=h.document.querySelector('.os-package-card .os-package-meta strong');
  assert.ok(summary);
  assert.match(summary.textContent,/A partir de R\$\s*60,00/);
  assert.doesNotMatch(summary.textContent,/R\$\s*0,00/);
  assert.equal(h.errors.length,0);
});
