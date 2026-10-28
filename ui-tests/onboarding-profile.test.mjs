import test from 'node:test';
import assert from 'node:assert/strict';
import {copy,entry,fullState,makeOnboarding,flush} from './onboarding-harness.mjs';

function profileState({activity='services',saved=false,step='profile'}={}){
  const state=fullState({current_step:step});
  state.configuration.activity=activity;
  state.steps=state.steps.map(row=>row.id==='catalog'
    ? {...row,id:'profile',label:'Perfil da empresa'}:row);
  if(saved)state.configuration.business_metrics={
    operating_age:'1_to_3_years',team_size:'3_5',
    monthly_revenue:'50k_100k',monthly_volume:'101_300',
  };
  return state;
}
const selects=['operating_age','team_size','monthly_revenue','monthly_volume'];
function fillProfile(h){
  const values=['under_6_months','1_2','50k_100k','51_100'];
  for(let i=0;i<selects.length;i++)h.change('[name='+selects[i]+']',values[i]);
  return Object.fromEntries(selects.map((key,i)=>[key,values[i]]));
}
function writes(h){return h.calls.filter(row=>row.method!=='GET');}

test('new business profile has exactly four labelled, required selects and no catalog editor',async t=>{
  const h=await makeOnboarding({data:profileState()});t.after(()=>h.close());
  assert.equal(h.element('#ob-step-title').textContent,'Perfil da empresa');
  assert.equal(h.document.querySelectorAll('[data-ob-form=profile] select').length,4);
  assert.equal(h.document.querySelector('[data-ob-form=catalog]'),null);
  assert.equal(h.document.querySelector('[data-ob-action=add-service]'),null);
  assert.equal(h.document.querySelector('[data-ob-action=add-product]'),null);
  for(const key of selects){
    const input=h.element('[name='+key+']'),prompt=input.options[0];
    assert.equal(input.required,true,key);
    assert.equal(input.labels[0].htmlFor,input.id,key);
    assert.equal(prompt.disabled,true,key);
    assert.equal(prompt.hidden,true,key);
    assert.equal(input.checkValidity(),false,key);
  }
  assert.match(h.element('[name=monthly_revenue]').outerHTML,/aria-describedby="ob-monthly_revenue-help"/);
  assert.match(h.element('#ob-monthly_revenue-help').textContent,/estimativa/i);
  assert.equal(h.element('[name=monthly_revenue]').querySelectorAll('[value="50k_100k"]').length,1);
  h.submit();await flush();
  assert.equal(writes(h).length,0);
  assert.deepEqual(h.errors,[]);
});

test('all estimated bands are saved through the tenant profile step, never as catalog entries',async t=>{
  const h=await makeOnboarding({data:profileState(),respond:(path,method,payload,server)=>{
    if(method==='PUT'){
      assert.ok(path.endsWith('/steps/profile'));
      const updated=copy(server.data);
      updated.configuration.business_metrics=copy(payload.data);
      updated.version+=1;
      updated.current_step='hours';
      server.data=copy(updated);
      return updated;
    }
  }});t.after(()=>h.close());
  const values=fillProfile(h);
  h.submit();await flush();
  assert.equal(writes(h).length,1);
  const call=writes(h)[0];
  assert.deepEqual(call.data.data,values);
  assert.ok(call.data.idempotency_key);
  assert.equal(call.data.expected_version,3);
  assert.equal(call.path.endsWith('/steps/profile'),true);
  assert.equal(h.controller.getState().step,'hours');
  h.click('[data-ob-step=profile]');
  for(const [key,value] of Object.entries(values))assert.equal(h.element('[name='+key+']').value,value);
  assert.deepEqual(h.errors,[]);
});

test('unsaved profile answers survive navigating through other setup steps',async t=>{
  const h=await makeOnboarding({data:profileState()});t.after(()=>h.close());
  fillProfile(h);
  h.click('[data-ob-step=hours]');
  h.click('[data-ob-step=profile]');
  assert.equal(h.element('[name=operating_age]').value,'under_6_months');
  assert.equal(h.element('[name=monthly_revenue]').value,'50k_100k');
  assert.equal(writes(h).length,0);
});

test('products-only companies see sensible questions about team and customer orders',async t=>{
  const h=await makeOnboarding({data:profileState({activity:'products'})});t.after(()=>h.close());
  assert.match(h.element('[name=team_size]').labels[0].textContent,/pessoas trabalham/);
  assert.match(h.element('[name=monthly_volume]').labels[0].textContent,/pedidos/);
  assert.match(h.element('[name=team_size]').options[1].textContent,/colaboradores/);
  assert.match(h.element('[name=monthly_volume]').options[1].textContent,/pedidos/);
  assert.ok(h.element('[name=monthly_revenue]').querySelector('[value="prefer_not_to_say"]'));
});

test('review shows estimated company profile with a real link back to its questions',async t=>{
  const h=await makeOnboarding({data:profileState({saved:true,step:'review'})});t.after(()=>h.close());
  const summary=h.element('.ob-summary');
  assert.match(summary.textContent,/Perfil da empresa/);
  assert.match(summary.textContent,/R\$ 50 mil a R\$ 100 mil/);
  assert.match(summary.textContent,/101 a 300/);
  assert.equal(summary.querySelector('[data-ob-target=catalog]'),null);
  h.click('[data-ob-target=profile]');
  assert.equal(h.element('[name=monthly_revenue]').value,'50k_100k');
  assert.equal(writes(h).length,0);
});

test('server errors preserve all choices and never auto retry a failed profile save',async t=>{
  const h=await makeOnboarding({data:profileState(),respond:(path,method)=>{
    if(method==='PUT')throw Object.assign(Error('Falha temporária'),{status:500});
  }});t.after(()=>h.close());
  const values=fillProfile(h);
  h.submit();await flush();
  assert.equal(writes(h).length,1);
  for(const [key,value] of Object.entries(values))assert.equal(h.element('[name='+key+']').value,value);
  assert.ok(h.document.querySelector('[data-ob-action=refresh]'));
  assert.ok(h.document.querySelector('[data-ob-action=retry-operation]'));
  assert.deepEqual(h.errors,[]);
});
