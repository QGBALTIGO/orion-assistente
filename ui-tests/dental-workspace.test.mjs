import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { makeSuite, planState, flush, tenantA } from './harness.mjs';

function dentalPlan(overrides={}) {
  return planState('elite',{workspace_profile:'standard',layout_modes:['classic'],
    business_niche:'odontologia',revision:'synthetic-dental-v1',...overrides});
}

const retainedRoutes=['novidades','ajuda','member'];
const retiredRoutes=[
  'dashboard','acelerar','agenda','clientes','pacientes-odonto','clinica','servicos','funcionarios',
  'inbox','site','financeiro','estoque-odonto','desempenho','marketing','fidelizacao','consultor',
  'plano','configuracoes','perfil','dispositivo','conversas','ai','empresa','catalogo','cadastros'
];

test('odontologia abre em Novidades e apresenta apenas as três áreas mantidas',async t=>{
  for(const dental_clinical_enabled of [false,true]){
    const h=await makeSuite({initialRoute:'dashboard',
      permissions:dentalPlan({dental_clinical_enabled,layout_modes:['classic','app']}),
      configureWindow:window=>window.localStorage.setItem('orion-navigation-v1:'+tenantA+':synthetic@example.invalid','app')
    });
    t.after(()=>h.close());await flush();
    const app=h.document.getElementById('appView');
    assert.equal(app.classList.contains('os-dental-workspace'),true);
    assert.equal(app.classList.contains('os-layout-app'),false);
    assert.equal(h.document.getElementById('osAppDock').hidden,true);
    const rows=[...h.document.querySelectorAll('#osNav [data-os-route]')];
    assert.deepEqual(rows.map(row=>row.dataset.osRoute),retainedRoutes);
    assert.deepEqual(rows.map(row=>row.textContent.trim()),['Novidades','Base de conhecimento','Minha conta']);
    assert.deepEqual([...h.document.querySelectorAll('#osNav .os-nav-section')].map(row=>row.textContent),['Ajuda e conta']);
    assert.equal(h.document.querySelector('.os-side-site,[data-os-action=managed-whatsapp]'),null);
    assert.equal(h.document.getElementById('osNotificationsToggle').hidden,true);
    assert.equal(h.document.getElementById('osConsultantFab').classList.contains('hidden'),true);
    assert.equal(h.document.querySelector('#osNav [aria-current=page]').dataset.osRoute,'novidades');
    assert.match(h.document.getElementById('osPageBody').textContent,/Painel de odontologia em reconstrução/);
    assert.deepEqual(h.errors,[]);
  }
});

test('endereços antigos e navegação direta voltam a Novidades sem carregar módulos',async t=>{
  const h=await makeSuite({initialRoute:'agenda',url:'https://orion.test/painel?aba=agenda',
    permissions:dentalPlan({dental_clinical_enabled:true})});
  t.after(()=>h.close());await flush();
  for(const route of retiredRoutes){
    await h.window.OrionSuite.open(route);
    assert.equal(new URL(h.window.location.href).searchParams.get('aba'),'novidades',route);
    assert.equal(h.document.getElementById('page-suite').dataset.osCurrentRoute,'novidades',route);
  }
  h.window.OrionSuite.legacyOpened('dispositivo');await flush();
  h.document.getElementById('osNotificationsToggle').click();await flush();
  assert.deepEqual(h.calls.filter(({path})=>!path.endsWith('/management/permissions')),[]);
  assert.deepEqual(h.errors,[]);
});

test('a base de conhecimento abre seus guias e Minha conta usa a página de conta existente',async t=>{
  const opened=[];
  const h=await makeSuite({initialRoute:'novidades',permissions:dentalPlan(),
    bridge:{openLegacy:route=>opened.push(route)}});
  t.after(()=>h.close());await flush();
  h.click('#osPageBody [data-os-route=ajuda]');await flush();
  assert.match(h.document.getElementById('osPageBody').textContent,/Base de conhecimento/);
  assert.equal(h.document.querySelector('[data-os-help-article=agendar]'),null);
  h.click('[data-os-help-article=alterar-senha]');
  assert.match(h.document.querySelector('.os-help-article h1').textContent,/Como alterar sua senha/);
  h.click('#osPageBody [data-os-route=member]');await flush();
  assert.deepEqual(opened,['member']);
  assert.equal(h.document.querySelector('#osNav [aria-current=page]').dataset.osRoute,'member');
  h.click('#osNav [data-os-route=novidades]');await flush();
  assert.equal(h.document.querySelector('#osNav [aria-current=page]').dataset.osRoute,'novidades');
  assert.deepEqual(h.errors,[]);
});

test('uma falha de permissões mantém o painel enxuto depois que a odontologia foi confirmada',async t=>{
  let available=true;
  const h=await makeSuite({initialRoute:'ajuda',permissions:()=>{
    if(!available)throw new Error('Serviço indisponível');
    return {effective_plan:dentalPlan()};
  }});
  t.after(()=>h.close());await flush();
  available=false;
  h.window.dispatchEvent(new h.window.Event('focus'));await flush();
  assert.equal(h.document.getElementById('page-suite').dataset.osCurrentRoute,'novidades');
  assert.deepEqual([...h.document.querySelectorAll('#osNav [data-os-route]')].map(row=>row.dataset.osRoute),retainedRoutes);
  assert.deepEqual(h.errors,[]);
});

test('business fields from an unverified tenant cannot opt in to clinical presentation',async t=>{
  const h=await makeSuite({initialRoute:'dashboard',permissions:planState('elite',{business_niche:null}),
    bridge:{tenants:[{id:tenantA,name:'Empresa A',niche:'odontologia',role:'owner',timezone:'America/Sao_Paulo'}]}});
  t.after(()=>h.close());await flush();
  assert.equal(h.document.getElementById('appView').classList.contains('os-dental-workspace'),false);
  assert.equal(h.document.querySelector('#osNav [data-os-route=clientes]')?.textContent.trim(),'Clientes');
  assert.equal(h.errors.length,0);
});

test('non-dental workspace keeps original navigation and barbershop service preset layout',async t=>{
  const h=await makeSuite({initialRoute:'servicos',permissions:planState('elite',{business_niche:null})});
  t.after(()=>h.close());await flush();
  assert.equal(h.document.getElementById('appView').classList.contains('os-dental-workspace'),false);
  assert.equal(h.document.querySelector('#osNav [data-os-route=servicos]')?.textContent.trim(),'Serviços');
  assert.ok(h.document.querySelector('.os-service-models'));
  assert.match(h.document.querySelector('.os-title-row').textContent,/Serviços/);
});

test('dental styles use the existing Orion tokens and mobile breakpoints',()=>{
  const css=readFileSync(new URL('../app/static/orion-suite.css',import.meta.url),'utf8');
  assert.ok(!css.includes('.orion-suite-enabled.os-dental-workspace .os-service-models{display:none!important}'));
  assert.match(css,/\.os-service-models/);
  assert.match(css,/--os-primary:/);
  assert.match(css,/--os-font:/);
  assert.match(css,/@media\(max-width:767px\)/);
  assert.match(css,/@media\(prefers-reduced-motion:reduce\)/);
});
