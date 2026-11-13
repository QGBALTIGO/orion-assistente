import test from 'node:test';
import assert from 'node:assert/strict';
import {copy,entry,fullState,makeOnboarding,flush} from './onboarding-harness.mjs';

const niche='servico_assinaturas';
const offerings=[
  ['subscriptions','Assinaturas'],
  ['one_time_sales','Vendas avulsas'],
  ['both','Assinaturas e vendas avulsas'],
];
const ordinary=[
  ['services','Serviços com agendamento'],['products','Produtos'],['both','Serviços e produtos'],
];
const writes=h=>h.calls.filter(call=>call.method!=='GET');
const choices=h=>[...h.element('[name=activity]').options]
  .filter(option=>option.value).map(option=>[option.value,option.textContent]);

function subscribedState(offering='subscriptions',step='business') {
  const state=fullState({current_step:step});
  Object.assign(state.configuration,{niche,activity:'products',subscription_offering:offering});
  return state;
}
function saveIdentity(path,method,payload,server) {
  if(!['POST','PUT'].includes(method))return;
  const {name,timezone,idempotency_key,...identity}=method==='POST'?payload:payload.data;
  const configuration={...server.data.configuration,...copy(identity)};
  if(!Object.hasOwn(identity,'subscription_offering'))delete configuration.subscription_offering;
  server.data={...server.data,name,timezone,configuration,version:server.data.version+1,
    current_step:'location',last_operation_key:payload.idempotency_key};
  server.decision=entry();
  return copy(server.data);
}

test('new niche uses one required native select with exactly the three requested offers',async t=>{
  const h=await makeOnboarding({decision:entry({tenant_id:null})});t.after(()=>h.close());
  assert.equal(h.element('[name=niche] option[value='+niche+']').textContent,'Serviço de assinaturas');
  assert.deepEqual(choices(h),ordinary);
  h.change('[name=niche]',niche);
  const select=h.element('[name=activity]');
  assert.equal(select.tagName,'SELECT');assert.equal(select.multiple,false);
  assert.equal(select.required,true);assert.equal(select.value,'');
  assert.equal(select.options[0].disabled,true);assert.equal(select.options[0].hidden,true);
  assert.equal(select.checkValidity(),false);
  assert.deepEqual(choices(h),offerings);
  assert.equal(select.labels[0].textContent,'O que você oferece');
  assert.equal(h.element('[data-ob-other-niche]').hidden,true);
  assert.equal(h.element('.ob-business-fields').querySelectorAll(':scope > .ob-field').length,5);
  assert.deepEqual(writes(h),[]);assert.deepEqual(h.errors,[]);
});

test('switching business families clears even the shared both value, while ordinary niches keep it',async t=>{
  const state=fullState();state.configuration.activity='both';
  const h=await makeOnboarding({data:state});t.after(()=>h.close());
  h.change('[name=niche]','salao');assert.equal(h.element('[name=activity]').value,'both');
  h.change('[name=niche]',niche);assert.equal(h.element('[name=activity]').value,'');
  h.change('[name=activity]','both');
  h.change('[name=niche]','barbearia');
  assert.deepEqual(choices(h),ordinary);assert.equal(h.element('[name=activity]').value,'');
  assert.equal(h.element('[name=activity]').checkValidity(),false);
  assert.deepEqual(writes(h),[]);
});

for(const [value,label] of offerings)test('create, reload and edit preserve the explicit offering '+label,async t=>{
  const h=await makeOnboarding({decision:entry({tenant_id:null}),respond:saveIdentity});
  t.after(()=>h.close());
  h.fill('[name=name]','Empresa de assinaturas');h.change('[name=niche]',niche);
  h.change('[name=activity]',value);h.fill('[name=contact_phone]','(67) 99999-9901');
  h.submit();await flush();
  assert.equal(writes(h).length,1);
  assert.equal(writes(h)[0].data.niche,niche);
  assert.equal(writes(h)[0].data.activity,'products');
  assert.equal(writes(h)[0].data.subscription_offering,value);
  assert.equal(h.controller.getState().step,'location');
  await h.controller.resolveEntry();h.click('[data-ob-step=business]');
  assert.equal(h.element('[name=niche]').value,niche);
  assert.equal(h.element('[name=activity]').value,value);
  assert.equal(h.element('[name=activity]').selectedOptions[0].textContent,label);
  h.submit();await flush();
  assert.equal(writes(h).length,2);assert.equal(writes(h)[1].method,'PUT');
  assert.equal(writes(h)[1].data.data.activity,'products');
  assert.equal(writes(h)[1].data.data.subscription_offering,value);
  assert.deepEqual(h.errors,[]);
});

test('unsaved subscription choices survive navigation before the server niche changes',async t=>{
  const h=await makeOnboarding();t.after(()=>h.close());
  h.fill('[name=name]','Rascunho preservado');h.change('[name=niche]',niche);
  h.change('[name=activity]','one_time_sales');
  h.click('[data-ob-step=location]');h.click('[data-ob-step=business]');
  assert.equal(h.element('[name=name]').value,'Rascunho preservado');
  assert.equal(h.element('[name=niche]').value,niche);
  assert.deepEqual(choices(h),offerings);
  assert.equal(h.element('[name=activity]').value,'one_time_sales');
  assert.equal(h.controller.getState().data.configuration.niche,'barbearia');
  assert.deepEqual(writes(h),[]);assert.deepEqual(h.errors,[]);
});

test('an uncertain save restores the chosen offer and retries the same exact request',async t=>{
  let attempts=0;
  const h=await makeOnboarding({respond:(path,method,payload,server)=>{
    if(method==='PUT'&&++attempts===1)throw Error('Resposta perdida');
    return saveIdentity(path,method,payload,server);
  }});t.after(()=>h.close());
  h.change('[name=niche]',niche);h.change('[name=activity]','both');
  h.submit();await flush();
  assert.equal(h.controller.getState().uncertain,true);
  assert.equal(h.element('[name=niche]').value,niche);
  assert.equal(h.element('[name=activity]').value,'both');assert.deepEqual(choices(h),offerings);
  h.click('[data-ob-action=retry-operation]');await flush();
  assert.equal(writes(h).length,2);assert.deepEqual(writes(h)[0],writes(h)[1]);
  assert.equal(h.controller.getState().uncertain,false);
  assert.equal(h.controller.getState().data.configuration.subscription_offering,'both');
  assert.deepEqual(h.errors,[]);
});

test('leaving the niche omits the extra field and restores the ordinary saved form',async t=>{
  const h=await makeOnboarding({data:subscribedState('both'),respond:saveIdentity});
  t.after(()=>h.close());h.change('[name=niche]','barbearia');
  assert.equal(h.element('[name=activity]').value,'');
  h.submit();await flush();assert.equal(writes(h).length,0);
  h.change('[name=activity]','services');h.submit();await flush();
  assert.equal(writes(h).length,1);
  assert.equal(writes(h)[0].data.data.activity,'services');
  assert.equal(Object.hasOwn(writes(h)[0].data.data,'subscription_offering'),false);
  await h.controller.resolveEntry();h.click('[data-ob-step=business]');
  assert.equal(h.element('[name=niche]').value,'barbearia');
  assert.equal(h.element('[name=activity]').value,'services');assert.deepEqual(choices(h),ordinary);
  assert.deepEqual(h.errors,[]);
});

for(const [value,label] of offerings)test('both review layouts show readable niche and '+label,async t=>{
  for(const quick of [false,true]){
    const state=subscribedState(value,'review');
    if(quick)state.steps=state.steps.map(step=>step.id==='catalog'
      ?{...step,id:'profile',label:'Perfil da empresa'}:step.id==='channels'
        ?{...step,id:'goals',label:'Desafios e objetivos'}:step);
    const h=await makeOnboarding({data:state});t.after(()=>h.close());
    const review=h.element(quick?'.ob-review':'.ob-summary');
    const values=[...review.querySelectorAll('dd')].map(element=>element.textContent);
    assert.ok(values.includes('Serviço de assinaturas'));assert.ok(values.includes(label));
    assert.ok(!values.includes(niche));
    h.click('[data-ob-target=business]');
    assert.equal(h.element('[name=activity]').value,value);
    assert.deepEqual(writes(h),[]);assert.deepEqual(h.errors,[]);
  }
});
