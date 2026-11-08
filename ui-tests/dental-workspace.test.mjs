import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { makeSuite, planState, flush, tenantA } from './harness.mjs';

function dentalPlan(overrides={}) {
  return planState('elite',{workspace_profile:'standard',layout_modes:['classic'],
    business_niche:'odontologia',revision:'synthetic-dental-v1',...overrides});
}

test('a verified dentist business gets a grouped Orion menu with only existing modules', async t => {
  const h=await makeSuite({initialRoute:'dashboard',permissions:dentalPlan()});
  t.after(()=>h.close());await flush();
  const app=h.document.getElementById('appView');
  assert.ok(app.classList.contains('os-dental-workspace'));
  const rows=[...h.document.querySelectorAll('#osNav [data-os-route]')];
  const labels=Object.fromEntries(rows.map(el=>[el.dataset.osRoute,el.textContent.trim()]));
  assert.equal(labels.dashboard,'Início');
  assert.equal(labels.agenda,'Agenda clínica');
  assert.equal(labels.clientes,'Pacientes');
  assert.equal(labels.servicos,'Procedimentos');
  assert.equal(labels.funcionarios,'Equipe clínica');
  assert.equal(labels.inbox,'Conversas');
  assert.equal(rows.filter(el=>el.dataset.osRoute==='clientes').length,1);
  assert.equal(rows.filter(el=>el.dataset.osRoute==='servicos').length,1);
  assert.ok([...h.document.querySelectorAll('#osNav .os-nav-section')].some(el=>el.textContent==='Atendimento clínico'));
  assert.equal(h.document.querySelector('#osNav [data-os-route=odontograma]'),null);
  assert.equal(h.document.querySelector('#osNav [data-os-route=prontuarios]'),null);
  assert.equal(app.classList.contains('os-layout-app'),false);
  assert.equal(h.errors.length,0);
});

test('dashboard uses the live management data contract and has actual booking and patient actions',async t=>{
  const calls=[];
  const h=await makeSuite({initialRoute:'dashboard',permissions:dentalPlan(),
    respond:(path)=>{calls.push(path);if(path.includes('/management/dashboard'))
      return {summary:{appointments:7,completed:3,customers:12,cancelled:1,no_show:0}};
      return [];}});
  t.after(()=>h.close());await flush();
  const main=h.document.getElementById('osPageBody');
  assert.match(main.textContent,/Consultas de hoje/);
  assert.match(main.textContent,/Nova consulta/);
  assert.match(main.textContent,/Novo paciente/);
  assert.ok(calls.some(path=>path.includes('/management/dashboard')));
  assert.ok(!main.textContent.includes('Sorriso Prime'));
  h.click('#osNav [data-os-route=clientes]');await flush();
  assert.match(h.document.querySelector('.os-client-heading h1').textContent,/Pacientes/);
  assert.ok(h.document.querySelector('[data-os-action=new-customer]'));
  assert.equal(h.document.querySelector('#osNav [data-os-route=clientes]')?.getAttribute('aria-current'),'page');
  assert.equal(h.errors.length,0);
});

test('dental procedures remain the normal writable service catalogue, not a second booking engine',async t=>{
  const h=await makeSuite({initialRoute:'servicos',permissions:dentalPlan()});
  t.after(()=>h.close());await flush();
  assert.equal(h.document.querySelector('.os-title-row h1')?.textContent,'Procedimentos');
  assert.match(h.document.getElementById('osPageBody').textContent,/PROCEDIMENTOS CADASTRADOS/);
  assert.ok(h.document.querySelector('[data-os-action=new-service]'));
  assert.match(h.document.getElementById('osPageBody').textContent,/Modelos de procedimentos/);
  h.click('[data-os-action=toggle-service-models]');await flush();
  const presets=[...h.document.querySelectorAll('[data-os-service-model]')];
  assert.equal(presets.length,6);
  assert.ok(presets.some(el=>el.textContent.includes('Avaliação odontológica')));
  assert.ok(presets.some(el=>el.textContent.includes('Profilaxia e limpeza')));
  assert.ok(h.document.getElementById('appView').classList.contains('os-dental-workspace'));
  assert.equal(h.errors.length,0);
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

test('switching quickly between dental routes leaves a consistent single selection and no errors',async t=>{
  const h=await makeSuite({initialRoute:'dashboard',permissions:dentalPlan()});
  t.after(()=>h.close());
  await flush();
  const routes=['dashboard','agenda','clientes','servicos','funcionarios','financeiro'];
  for(let i=0;i<96;i++)h.click('#osNav [data-os-route='+routes[i%routes.length]+']');
  await flush();await flush();
  await h.window.OrionSuite.open('clientes');await flush();
  assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'clientes');
  assert.equal(h.document.querySelectorAll('#osNav [aria-current=page]').length,1);
  assert.equal(h.document.querySelector('#osNav [aria-current=page]').dataset.osRoute,'clientes');
  assert.equal(h.errors.length,0);
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
