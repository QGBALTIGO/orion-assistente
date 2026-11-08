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

test('patient detail reuses the Orion client record and exposes a read-only administrative journey',async t=>{
  const h=await makeSuite({initialRoute:'clientes',permissions:dental(),respond:response});
  t.after(()=>h.close());await flush();
  assert.equal(h.document.querySelector('.os-client-heading h1')?.textContent,'Pacientes');
  h.click('[data-os-customer="'+customerId+'"]');await flush();
  const dialog=h.document.getElementById('osDialog');
  assert.match(dialog.textContent,/Editar paciente/);
  assert.ok(dialog.querySelector('.os-dental-journey'));
  const journey=dialog.querySelector('details.os-dental-journey');
  journey.querySelector('summary').click();
  assert.equal(journey.open,true);
  assert.match(journey.textContent,/Agendamentos registrados/);
  assert.match(journey.textContent,/Atendimentos concluídos/);
  assert.match(journey.textContent,/Valor dos atendimentos/);
  assert.match(journey.textContent,/180,00/);
  assert.match(journey.textContent,/não de um prontuário clínico/);
  assert.equal(h.calls.some(x=>x.path.includes('/services?')),false);
  const source=dialog.querySelector('details#osCustomerServiceHistory');
  assert.ok(source,'histórico real continua disponível');
  source.querySelector('summary').click();await flush();
  assert.equal(h.calls.filter(x=>x.path.includes('/customers/'+customerId+'/services?')).length,1);
  assert.equal(h.calls.some(x=>x.method!=='GET'),false);
  assert.deepEqual(h.errors,[]);
});

test('new dental patient uses the existing form, with safe wording for observations and birth date visible',async t=>{
  const h=await makeSuite({initialRoute:'clientes',permissions:dental(),respond:response});
  t.after(()=>h.close());await flush();
  h.click('[data-os-action=new-customer]');await flush();
  const dialog=h.document.getElementById('osDialog');
  assert.match(dialog.textContent,/Novo paciente/);
  const input=dialog.querySelector('input[name=name]');
  assert.ok(input);
  assert.equal(input.closest('.os-client-field')?.querySelector('label')?.textContent,'Nome do paciente *');
  const extras=dialog.querySelector('details.os-client-more');
  assert.ok(extras.open,'campos de nascimento ficam expandidos para a clínica');
  assert.equal(extras.querySelector('[name=birth_date]').type,'date');
  const label=dialog.querySelector('label[for=osField-notes]');
  assert.equal(label.textContent,'Observações administrativas');
  assert.match(dialog.textContent,/Não registre sintomas, diagnósticos, tratamentos ou exames aqui/);
  assert.doesNotMatch(dialog.querySelector('textarea[name=notes]').placeholder,/alergias/i);
  assert.equal(dialog.querySelector('details.os-dental-journey'),null);
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
