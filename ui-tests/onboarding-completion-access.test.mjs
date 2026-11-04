import test from 'node:test';
import assert from 'node:assert/strict';
import {makePanel,jsonResponse} from './panel-harness.mjs';
import {fullState,tenantA,flush} from './onboarding-harness.mjs';
import {planState} from './harness.mjs';

function quickReview() {
  return fullState({
    current_step:'review',
    steps:['business','location','profile','goals','review'].map(id=>({
      id,required:true,complete:id!=='review'
    })),
    configuration:{
      niche:'servico_assinaturas',activity:'products',subscription_offering:'both',
      contact_phone:'+5567999999901',modality:'online',
      business_metrics:{operating_age:'1_to_3_years',team_size:'1_2',monthly_revenue:'none',monthly_volume:'none'},
      business_priorities:{main_challenge:'whatsapp',desired_outcome:'automate_whatsapp',respondent_role:'owner'}
    },
    catalog:{professionals:[],services:[],products:[],hours:[]}
  });
}

async function reviewPanel(onComplete) {
  const state=quickReview();
  return makePanel({role:'member',permissions:planState('none',{
    workspace_profile:'subscription_services',features:['account.manage','business.setup'],
    permissions:{'account.manage':true,'business.setup':true}
  }),respond(path,method,payload){
    if(path==='/api/v1/onboarding/entry')return {enabled:true,kind:'onboarding',tenant_id:tenantA};
    if(path.endsWith('/onboarding')&&method==='GET')return state;
    if(path.endsWith('/onboarding/complete')&&method==='POST')return onComplete(state,payload);
  }});
}

test('structured completion denial keeps the review and permits an explicit retry without granting a plan',async t=>{
  const message='Este recurso não está disponível no plano da sua empresa.';
  let allowCompletion=false;
  const h=await reviewPanel(state=>allowCompletion
    ?{...state,status:'complete',version:state.version+1}
    :jsonResponse({detail:{code:'PLAN_FEATURE_UNAVAILABLE',feature:'professionals.manage',
      message,plan_code:'none',source:'none'}},403));
  t.after(()=>h.close());
  const before=h.calls.length;
  h.element('#orionSetup [name=acknowledged]').checked=true;
  h.submit('[data-ob-form=complete]');await flush();

  const feedback=h.element('#orionSetup [data-ob-feedback]');
  assert.equal(feedback.textContent,message);
  assert.equal(feedback.hidden,false);
  assert.equal(h.element('#appView').classList.contains('ob-gated'),true);
  assert.equal(h.element('#orionSetup').hidden,false);
  assert.equal(h.element('[data-ob-form=complete] [name=acknowledged]').checked,true);
  assert.equal(h.element('[data-ob-form=complete] [type=submit]').disabled,false);
  assert.equal(h.document.querySelector('.ob-review-complete'),null);
  assert.equal(h.document.querySelector('[data-ob-action=retry-operation]'),null);
  assert.match(h.element('.ob-review').textContent,/Serviço de assinaturas/);
  assert.match(h.element('.ob-review').textContent,/Assinaturas e vendas avulsas/);
  assert.deepEqual(h.calls.slice(before).map(call=>[call.method,call.path]),[
    ['POST','/api/v1/tenants/'+tenantA+'/onboarding/complete']
  ]);
  const first=h.calls[before];
  assert.equal(first.options.headers['X-Orion-CSRF'],'synthetic-mobile-csrf');
  assert.equal(first.data.expected_version,3);
  assert.equal(first.data.expected_snapshot,'e'.repeat(64));

  allowCompletion=true;
  h.submit('[data-ob-form=complete]');await flush();
  const writes=h.calls.slice(before).filter(call=>call.method!=='GET');
  assert.equal(writes.length,2);
  assert.equal(writes[1].path,first.path);
  assert.equal(writes[1].data.expected_version,first.data.expected_version);
  assert.equal(writes[1].data.expected_snapshot,first.data.expected_snapshot);
  assert.equal(writes[1].data.acknowledged,true);
  assert.equal(h.element('#appView').classList.contains('ob-gated'),false);
  assert.equal(h.element('#orionSetup').hidden,true);
  // A successful questionnaire does not confer paid operational permissions.
  assert.equal(h.element('#page-suite').dataset.osCurrentRoute,'novidades');
  assert.equal(h.calls.slice(before).some(call=>/\/management\/dashboard/.test(call.path)),false);
  assert.equal(h.calls.slice(before).some(call=>/\/management\/plan-/.test(call.path)),false);
  assert.deepEqual(h.errors,[]);
});

const errorCases=[
  ['legacy detail precedes error',{detail:'Acesso recusado pelo servidor.',error:'Outro erro'},'Acesso recusado pelo servidor.'],
  ['structured detail precedes error',{detail:{message:'Confira a autorização da conta.'},error:'Outro erro'},'Confira a autorização da conta.'],
  ['empty structured message retains legacy error',{detail:{message:''},error:'Erro legado'},'Erro legado'],
  ['blank structured message uses the HTTP fallback',{detail:{message:'  \n '}},'O servidor retornou HTTP 403.'],
  ['non-text structured message uses the HTTP fallback',{detail:{message:{code:'blocked'}}},'O servidor retornou HTTP 403.'],
  ['missing detail retains legacy error',{error:'Ação não autorizada'},'Ação não autorizada'],
  ['server markup remains literal text',{detail:{message:'Confira <img src=x onerror="alert(1)"> & tente novamente.'}},'Confira <img src=x onerror="alert(1)"> & tente novamente.']
];

for(const [name,body,expected] of errorCases)test('shipped panel error handling: '+name,async t=>{
  const h=await reviewPanel(()=>jsonResponse(body,403));t.after(()=>h.close());
  h.element('#orionSetup [name=acknowledged]').checked=true;
  h.submit('[data-ob-form=complete]');await flush();
  const feedback=h.element('#orionSetup [data-ob-feedback]');
  assert.equal(feedback.textContent,expected);
  assert.equal(feedback.children.length,0);
  assert.equal(h.element('[data-ob-form=complete] [name=acknowledged]').checked,true);
  assert.equal(h.calls.filter(call=>call.method!=='GET').length,1);
  assert.deepEqual(h.errors,[]);
});
