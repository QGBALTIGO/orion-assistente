import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {makeSuite,flush,defaultResponse} from './harness.mjs';

const ids={
  cut:'c0000000-0000-4000-8000-000000000001',
  beard:'c0000000-0000-4000-8000-000000000002',
  hair:'c0000000-0000-4000-8000-000000000003',
  fixed:'d0000000-0000-4000-8000-000000000001',
  variable:'d0000000-0000-4000-8000-000000000002',
  inactive:'d0000000-0000-4000-8000-000000000003',
};
const services=[
  {id:ids.cut,name:'Corte Masculino',catalog_kind:'base',duration_min:40,price_cents:4000,active:true},
  {id:ids.beard,name:'Barba Tradicional',catalog_kind:'base',duration_min:20,price_cents:2000,active:true},
  {id:ids.hair,name:'Alinhamento Capilar (Botox)',catalog_kind:'base',duration_min:90,price_cents:8000,price_mode:'from',active:true}
];
function pack({id,name,serviceIds,photo,price,minutes,mode='fixed',active=true,available=true,featured=false}){
  return {
    id,name,active,available,featured,price_mode:mode,price_cents:price,
    effective_price_cents:price,duration_min:minutes,image_url:photo,
    color:'#8b5cf6',version:1,service_ids:serviceIds,
    items:serviceIds.map((service_id,item_order)=>({
      service_id,item_order,name:services.find(s=>s.id===service_id).name
    }))
  };
}
function fixtures(){
  return [
    pack({
      id:ids.fixed,
      name:'Corte + Barba + Sobrancelha na Navalha',
      serviceIds:[ids.cut,ids.beard],
      photo:'https://images.example.invalid/combocorte.webp',
      price:6000,minutes:90,featured:true
    }),
    pack({
      id:ids.variable,
      name:'Corte + Alinhamento Térmico (Botox) + Hidratação Profunda, cabelo comprido',
      serviceIds:[ids.cut,ids.hair],
      photo:'javascript:alert(1)',price:14000,minutes:150,mode:'from'
    }),
    pack({
      id:ids.inactive,name:'Tratamento completo',
      serviceIds:[ids.cut,ids.beard],
      photo:null,price:4500,minutes:60,active:false,available:false
    })
  ];
}
function responder(packs) {
  return (path,method='GET')=>{
    if(path.endsWith('/services')&&method==='GET')return services;
    if(path.endsWith('/professionals')||path.endsWith('/professional_services'))return [];
    if(path.endsWith('/management/service-packages')&&method==='GET')
      return {items:packs};
    return defaultResponse(path);
  };
}
async function fixture(t,{packs=fixtures(),role='owner'}={}){
  const h=await makeSuite({initialRoute:'servicos',role,respond:responder(packs)});
  t.after(()=>h.close());await flush();
  assert.equal(h.document.querySelectorAll('.os-package-card').length,packs.length);
  return h;
}
function card(h,id){return h.document.querySelector('[data-os-package-card="'+id+'"]');}

test('combo usa estrutura, background e ações compactas equivalentes ao card de serviço',async t=>{
  const h=await fixture(t),fixed=card(h,ids.fixed);
  assert.ok(fixed.classList.contains('os-card'));
  const section=h.document.getElementById('osPackagesSection');
  assert.ok(section.classList.contains('os-packages-section'));
  assert.equal(section.classList.contains('os-card'),false,'remove card exterior para evitar cartões duplos');
  assert.ok(section.querySelector('.os-packages-heading h2'));
  assert.equal(section.querySelector('.os-packages-heading p').textContent,'Combine serviços com preço especial.');
  assert.ok(h.document.querySelector('.os-package-card .os-package-main'));
  assert.equal(fixed.querySelector('.os-package-main .os-package-photo img').getAttribute('src'),
    'https://images.example.invalid/combocorte.webp');
  assert.equal(fixed.querySelector('.os-package-info h3').textContent,
    'Corte + Barba + Sobrancelha na Navalha');
  assert.equal(fixed.querySelector('.os-package-components').textContent,
    'Corte Masculino + Barba Tradicional');
  assert.match(fixed.querySelector('.os-package-meta strong').textContent,/R\$\s*60,00/);
  assert.equal(fixed.querySelector('.os-package-meta-dot').textContent,'•');
  assert.equal(fixed.querySelector('.os-package-duration').textContent,'1h 30min');
  assert.equal(fixed.querySelector('.os-package-status .os-badge').textContent,'Ativo');
  assert.equal(fixed.querySelector('.os-package-featured').textContent,'Destaque');
  const footer=fixed.querySelector('.os-package-actions');
  assert.ok(footer.querySelector('button[data-os-package="'+ids.fixed+'"]'));
  assert.equal(fixed.querySelector('.os-card-header'),null,'sem cabeçalho genérico gigante');
  assert.equal(fixed.querySelector('.os-item-bottom'),null,'sem preço solto com margem automática');
  assert.deepEqual(h.errors,[]);
});

test('combo variável conserva A partir de e tempo em linha compacta',async t=>{
  const h=await fixture(t),variable=card(h,ids.variable);
  assert.match(variable.querySelector('.os-package-meta strong').textContent,/A partir de R\$\s*140,00/);
  assert.equal(variable.querySelector('.os-package-duration').textContent,'2h 30min');
  assert.equal(variable.querySelector('.os-package-info h3').textContent,
    'Corte + Alinhamento Térmico (Botox) + Hidratação Profunda, cabelo comprido');
  assert.equal(variable.querySelector('.os-package-photo img'),null,'URL JavaScript rejeitada');
  assert.ok(variable.querySelector('.os-package-photo svg.os-icon'),'ícone vetorial de fallback');
  assert.equal(variable.querySelector('.os-package-featured'),null);
});

test('combo inativo usa status correto e edição respeita os valores originais',async t=>{
  const h=await fixture(t);
  const inactive=card(h,ids.inactive);
  assert.equal(inactive.querySelector('.os-package-status .os-badge').textContent,'Inativo');
  assert.equal(inactive.querySelector('.os-package-duration').textContent,'1h');
  h.click('[data-os-package="'+ids.fixed+'"]');await flush();
  const form=h.document.querySelector('[data-os-form=package]');
  assert.ok(form);
  assert.equal(form.elements.name.value,'Corte + Barba + Sobrancelha na Navalha');
  assert.equal(form.elements.price.value,'60,00');
  assert.ok(form.querySelector('input[name=service_ids][value="'+ids.cut+'"]').checked);
  assert.ok(form.querySelector('input[name=service_ids][value="'+ids.beard+'"]').checked);
  assert.equal(h.errors.length,0);
});

test('usuário sem edição vê Detalhes e mantém combo sem habilitar mutações',async t=>{
  const h=await fixture(t,{role:'viewer'});
  const fixed=card(h,ids.fixed);
  assert.match(fixed.querySelector('[data-os-package]').textContent,/Detalhes/);
  h.click('[data-os-package="'+ids.fixed+'"]');await flush();
  const form=h.document.querySelector('[data-os-form=package]');
  assert.ok(form);
  assert.equal(form.querySelector('fieldset').disabled,true);
  assert.equal(form.querySelector('button[type=submit]').disabled,true);
  assert.equal(h.calls.filter(x=>x.method!=='GET').length,0);
});

test('paleta, foto circular, clamp, preço e footer têm o mesmo ritmo do card de serviço',async()=>{
  const css=await readFile(new URL('../app/static/orion-suite.css',import.meta.url),'utf8');
  for(const needle of [
    '.os-package-card.os-card{',
    'border-radius:18px;',
    'background:var(--os-card);box-shadow:var(--os-shadow);overflow:hidden',
    '.os-package-main{',
    'grid-template-columns:52px minmax(0,1fr)',
    '.os-package-photo{',
    'width:52px;height:52px;border-radius:50%',
    '.os-package-info>h3{',
    '-webkit-line-clamp:2',
    '.os-package-components{',
    '.os-package-card>.os-package-actions{',
    'min-height:52px;margin:0;padding:6px 16px',
    '@media(max-width:359px)',
  ])assert.ok(css.includes(needle),'missing design rule: '+needle);
});
