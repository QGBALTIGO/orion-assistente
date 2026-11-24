import test from 'node:test';
import assert from 'node:assert/strict';
import { makeSuite, planState, flush, defaultResponse, customerId } from './harness.mjs';

const patient={
  id:customerId,name:'Paciente sintética',phone:null,active:true,
  version:3,created_at:'2026-11-01T16:00:00Z',
  birth_date:'2000-05-06',total_appointments:4,completed_appointments:2,
  total_spent_cents:18000,last_visit_at:'2026-11-06T14:00:00Z',loyalty_points:0
};
const dental=()=>planState('elite',{business_niche:'odontologia',workspace_profile:'standard',revision:'dental-patient-v1'});
const response=(path)=>{
  if(path.includes('/management/customers?'))return {items:[patient],total:1};
  if(path.includes('/customers/'+customerId+'/services?'))return {items:[],total:0};
  return defaultResponse(path);
};

test('o painel de odontologia não consulta nem abre cadastros de pacientes durante a reconstrução',async t=>{
  const h=await makeSuite({initialRoute:'clientes',permissions:dental(),respond:response});
  t.after(()=>h.close());await flush();
  assert.equal(h.document.getElementById('page-suite').dataset.osCurrentRoute,'novidades');
  assert.equal(h.document.querySelector('[data-os-action=new-customer],.os-client-heading'),null);
  assert.equal(h.calls.some(({path})=>path.includes('/customers')||path.includes('/dental/')),false);
  assert.deepEqual(h.errors,[]);
});

test('the original non-dental CRM is unchanged and has no dental journey',async t=>{
  const h=await makeSuite({initialRoute:'clientes',permissions:planState('elite',{business_niche:null}),respond:response});
  t.after(()=>h.close());await flush();
  assert.equal(h.document.querySelector('.os-client-heading h1')?.textContent,'Clientes');
  h.click('[data-os-action=new-customer]');await flush();
  const dialog=h.document.getElementById('osDialog');
  assert.match(dialog.textContent,/Novo cliente/);
  assert.equal(dialog.querySelector('details.os-client-more').open,false);
  assert.equal(dialog.querySelector('label[for=osField-notes]').textContent,'Observações');
  assert.match(dialog.querySelector('textarea[name=notes]').placeholder,/alergias/);
  assert.equal(dialog.querySelector('.os-dental-journey'),null);
  assert.deepEqual(h.errors,[]);
});
