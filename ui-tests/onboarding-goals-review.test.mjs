import test from 'node:test';
import assert from 'node:assert/strict';
import {copy,fullState,makeOnboarding,flush} from './onboarding-harness.mjs';

const metrics={
  operating_age:'1_to_3_years',
  team_size:'3_5',
  monthly_revenue:'50k_100k',
  monthly_volume:'101_300',
};
const answers={
  main_challenge:'whatsapp',
  desired_outcome:'automate_whatsapp',
  respondent_role:'partner',
};
const fields=Object.keys(answers);

function quickState({step='goals',activity='services',goals=null,pending=[],status='incomplete'}={}){
  const state=fullState({current_step:step,pending,status});
  state.configuration.activity=activity;
  state.configuration.business_metrics=copy(metrics);
  if(goals)state.configuration.business_priorities=copy(goals);
  state.steps=state.steps.map(row=>{
    if(row.id==='catalog')return {...row,id:'profile',label:'Perfil da empresa'};
    if(row.id==='channels')return {...row,id:'goals',label:'Desafios e objetivos',required:true};
    return row;
  });
  return state;
}
const writes=h=>h.calls.filter(call=>call.method!=='GET');

test('quick company setup skips Quem atende and preserves existing professionals',async t=>{
  const state=quickState({step:'professionals'});
  const h=await makeOnboarding({data:state});t.after(()=>h.close());
  assert.equal(h.controller.getState().step,'business');
  assert.equal(h.document.querySelector('[data-ob-step=professionals]'),null);
  assert.equal(h.document.querySelector('[data-ob-action=add-professional]'),null);
  assert.match(h.element('.ob-progress').textContent,/Etapa 1 de 6/);
  h.click('[data-ob-step=profile]');
  assert.equal(h.element('#ob-step-title').textContent,'Perfil da empresa');
  assert.equal(h.document.querySelector('[data-ob-step=professionals]'),null);
  assert.equal(writes(h).length,0);
  assert.deepEqual(h.errors,[]);
});

test('three focused questions replace channels and placeholders never count as answers',async t=>{
  const h=await makeOnboarding({data:quickState()});t.after(()=>h.close());
  assert.equal(h.element('#ob-step-title').textContent,'Desafios e objetivos');
  const form=h.element('form[data-ob-form=goals]');
  assert.equal(form.querySelectorAll('select').length,3);
  assert.equal(form.querySelectorAll('input,textarea').length,0);
  assert.equal(h.document.querySelector('[data-ob-form=channels]'),null);
  assert.equal(h.document.querySelector('[data-ob-action=connect-whatsapp]'),null);
  assert.equal(h.document.querySelector('[name=assistant_name]'),null);
  assert.equal(h.document.querySelector('[name=style]'),null);
  const counts=[7,6,4];
  for(const [index,key] of fields.entries()){
    const select=h.element('[name='+key+']');
    assert.equal(select.options.length,counts[index]+1,key);
    assert.equal(select.required,true,key);
    assert.equal(select.labels.length,1,key);
    assert.equal(select.labels[0].htmlFor,select.id,key);
    assert.equal(select.options[0].value,'',key);
    assert.equal(select.options[0].disabled,true,key);
    assert.equal(select.options[0].hidden,true,key);
    assert.equal(select.checkValidity(),false,key);
  }
  h.submit();await flush();
  assert.equal(writes(h).length,0);
  assert.deepEqual(h.errors,[]);
});

test('goals save once with concurrency-safe metadata and preserve legacy channel settings',async t=>{
  const h=await makeOnboarding({data:quickState(),respond:(path,method,payload,server)=>{
    if(method==='PUT'){
      assert.ok(path.endsWith('/steps/goals'));
      const updated=copy(server.data);
      updated.configuration.business_priorities=copy(payload.data);
      updated.current_step='review';
      updated.version+=1;
      updated.last_operation_key=payload.idempotency_key;
      updated.operations=[{key:payload.idempotency_key,step:'goals',version:updated.version}];
      server.data=copy(updated);
      return updated;
    }
  }});t.after(()=>h.close());
  for(const [name,value] of Object.entries(answers))h.change('[name='+name+']',value);
  h.submit();h.submit();await flush();
  const sent=writes(h);
  assert.equal(sent.length,1);
  assert.equal(sent[0].data.expected_version,3);
  assert.deepEqual(sent[0].data.data,answers);
  assert.match(sent[0].data.idempotency_key,/^[a-f0-9-]{36}$/);
  assert.equal(h.controller.getState().step,'review');
  assert.deepEqual(copy(h.controller.getState().data.configuration.business_priorities),answers);
  assert.deepEqual(copy(h.controller.getState().data.configuration.business_metrics),metrics);
  assert.equal(writes(h).some(x=>/connect|publish|whatsapp|business\/profile/.test(x.path)),false);
  assert.deepEqual(h.errors,[]);
});

test('changing steps preserves unsubmitted answers without writing them',async t=>{
  const h=await makeOnboarding({data:quickState()});t.after(()=>h.close());
  for(const [name,value] of Object.entries(answers))h.change('[name='+name+']',value);
  h.click('[data-ob-step=profile]');
  h.click('[data-ob-step=goals]');
  for(const [name,value] of Object.entries(answers))assert.equal(h.element('[name='+name+']').value,value);
  assert.equal(writes(h).length,0);
});

test('products-only companies see relevant choices without a second questionnaire',async t=>{
  const h=await makeOnboarding({data:quickState({activity:'products'})});t.after(()=>h.close());
  assert.match(h.element('[name=main_challenge] option[value=scheduling]').textContent,/pedidos/i);
  assert.match(h.element('[name=main_challenge] option[value=cancellations]').textContent,/pedidos/i);
  assert.match(h.element('[name=desired_outcome] option[value=more_bookings]').textContent,/pedidos/i);
  assert.equal(h.element('[name=respondent_role] option[value=partner]').textContent,'Sócio');
  assert.equal(h.element('[name=respondent_role] option[value=manager]').textContent,'Gerente / Administrador');
});

test('new review has four organized editable sections and no technical channel status',async t=>{
  const h=await makeOnboarding({data:quickState({step:'review',goals:answers})});t.after(()=>h.close());
  const root=h.element('.ob-review');
  const expected=['Empresa','Atendimento','Perfil da empresa','Desafios e objetivos'];
  assert.deepEqual([...root.querySelectorAll('.ob-review-section-head h3')].map(el=>el.textContent),expected);
  assert.equal(root.querySelectorAll('.ob-review-section').length,4);
  assert.equal(root.querySelectorAll('.ob-review-edit').length,4);
  assert.equal(root.querySelectorAll('.ob-review-section-head .ob-icon').length,4);
  for(const target of ['business','location','profile','goals']){
    assert.equal(root.querySelector('[data-ob-target='+target+']').getAttribute('aria-label').startsWith('Editar '),true);
  }
  assert.match(root.textContent,/Barbearia/i);
  assert.match(root.textContent,/\(67\) 99999-9901/);
  assert.match(root.textContent,/Rua sintética/);
  assert.match(root.textContent,/R\$ 50 mil a R\$ 100 mil/);
  assert.match(root.textContent,/Responder clientes no WhatsApp/);
  assert.match(root.textContent,/Automatizar o WhatsApp/);
  assert.match(root.textContent,/Sócio/);
  assert.equal(root.textContent.includes('Canais e publicação'),false);
  assert.equal(root.textContent.includes('Google Agenda'),false);
  assert.equal(root.textContent.includes('Conectar WhatsApp'),false);
  assert.equal(h.element('[data-ob-form=complete] [name=acknowledged]').checked,false);
  assert.equal(writes(h).length,0);
});

test('summary edits lead back to saved answer, and review returns with no changes lost',async t=>{
  const h=await makeOnboarding({data:quickState({step:'review',goals:answers})});t.after(()=>h.close());
  h.click('[data-ob-target=goals]');
  assert.equal(h.controller.getState().step,'goals');
  for(const [name,value] of Object.entries(answers))assert.equal(h.element('[name='+name+']').value,value);
  h.click('[data-ob-step=review]');
  h.click('[data-ob-target=profile]');
  assert.equal(h.element('[name=monthly_revenue]').value,'50k_100k');
  assert.equal(writes(h).length,0);
});

test('review requires explicit confirmation before completing and keeps channel side effects off',async t=>{
  const h=await makeOnboarding({data:quickState({step:'review',goals:answers}),respond:(path,method,payload,server)=>{
    if(method==='POST'){
      assert.ok(path.endsWith('/onboarding/complete'));
      const updated=copy(server.data);
      updated.status='complete';
      updated.version+=1;
      updated.configuration.reviewed=true;
      server.data=updated;
      return updated;
    }
  }});t.after(()=>h.close());
  h.submit();await flush();
  assert.equal(writes(h).length,0);
  h.change('[name=acknowledged]',true);
  h.submit();await flush();
  const sent=writes(h);
  assert.equal(sent.length,1);
  assert.equal(sent[0].data.acknowledged,true);
  assert.equal(sent[0].data.activate_catalog,true);
  assert.equal(sent[0].data.expected_version,3);
  assert.equal(sent[0].data.expected_snapshot,'e'.repeat(64));
  assert.equal(sent[0].path.endsWith('/onboarding/complete'),true);
  assert.equal(sent.some(call=>/whatsapp|connect|publish|google/.test(call.path)),false);
  assert.equal(h.controller.getState().data.status,'complete');
  assert.match(h.element('.ob-review-complete').textContent,/Cadastro concluído/);
  assert.deepEqual(h.errors,[]);
});

test('pending quick review cannot submit and makes gaps actionable',async t=>{
  const pending=[
    {step:'goals',code:'business_priorities',message:'Responda às três perguntas sobre desafios e objetivos'},
  ];
  const h=await makeOnboarding({data:quickState({step:'review',pending})});t.after(()=>h.close());
  assert.match(h.element('.ob-review-pending').textContent,/três perguntas/);
  assert.equal(h.document.querySelector('[data-ob-form=complete]'),null);
  assert.equal(h.element('.ob-review-pending + .ob-actions button[disabled]').disabled,true);
  h.click('.ob-review-edit[data-ob-target=goals]');
  assert.equal(h.controller.getState().step,'goals');
  assert.equal(writes(h).length,0);
});

test('failed save retains three selections and recovery never retries automatically',async t=>{
  const h=await makeOnboarding({data:quickState(),respond:(path,method)=>{
    if(method==='PUT')throw Object.assign(Error('Falha de conexão simulada'),{status:500});
  }});t.after(()=>h.close());
  for(const [name,value] of Object.entries(answers))h.change('[name='+name+']',value);
  h.submit();await flush();
  assert.equal(writes(h).length,1);
  for(const [name,value] of Object.entries(answers))assert.equal(h.element('[name='+name+']').value,value);
  assert.ok(h.document.querySelector('[data-ob-action=refresh]'));
  assert.ok(h.document.querySelector('[data-ob-action=retry-operation]'));
  h.submit();await flush();
  assert.equal(writes(h).length,1);
});

test('legacy onboarding retains the original channel configuration and review',async t=>{
  const h=await makeOnboarding({data:fullState({current_step:'channels'})});t.after(()=>h.close());
  assert.ok(h.document.querySelector('[data-ob-form=channels]'));
  assert.equal(h.document.querySelector('[data-ob-form=goals]'),null);
  assert.ok(h.document.querySelector('[name=assistant_name]'));
  assert.equal(h.document.querySelector('[data-ob-step=goals]'),null);
  h.click('[data-ob-step=review]');
  assert.ok(h.document.querySelector('.ob-summary'));
  assert.match(h.element('.ob-summary').textContent,/Canais e publicação/);
});

test('review uses text escaping even if a stored field resembles HTML',async t=>{
  const state=quickState({step:'review',goals:answers});
  state.name='<img src=x onerror=alert(1)>';
  state.configuration.other_niche='<script>window.injected=true</script>';
  state.configuration.niche='outro';
  const h=await makeOnboarding({data:state});t.after(()=>h.close());
  assert.equal(h.document.querySelector('img[src=x]'),null);
  assert.equal(h.document.querySelector('.ob-review script'),null);
  assert.match(h.element('.ob-review').textContent,/<img src=x/);
  assert.deepEqual(h.errors,[]);
});
