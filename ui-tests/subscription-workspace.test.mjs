import test from 'node:test';
import assert from 'node:assert/strict';
import {makeSuite,defaultResponse,planState,tenantA,tenantB,flush,deferred} from './harness.mjs';
import {makePanel,jsonResponse} from './panel-harness.mjs';
import {fullState} from './onboarding-harness.mjs';

const profile='subscription_services';
const utility=['dispositivos','chatbot','novidades','ajuda','member'];
const subscriptionPlan=(code='none',extra={})=>{
  const value=planState(code,{workspace_profile:profile,revision:'subscription-'+code,...extra});
  return {...value,features:[...value.features,'devices.manage'],permissions:{...value.permissions,'devices.manage':true}};
};
const business=(extra={})=>({id:tenantA,name:'Empresa de assinaturas',role:'owner',
  slug:'assinaturas-sinteticas',timezone:'America/Sao_Paulo',workspace_profile:profile,...extra});
const scopedCalls=h=>h.calls.filter(call=>call.path.startsWith('/api/v1/tenants/'));
const operationalCalls=h=>scopedCalls(h).filter(call=>!call.path.endsWith('/management/permissions'));
const visible=node=>!node.hidden&&!node.classList.contains('hidden');

function assertSubscriptionShell(h,devices=true){
  assert.deepEqual([...h.document.querySelectorAll('#osNav [data-os-route]')]
    .map(node=>node.dataset.osRoute),devices?utility:utility.slice(2));
  assert.deepEqual([...h.document.querySelectorAll('#osNav .os-nav-section')]
    .map(node=>node.textContent),['Gestão','Ajuda e conta']);
  assert.equal(h.document.querySelector('#osSidebarContext .os-side-site'),null);
  assert.equal(h.document.querySelector('#osNav [data-os-action=managed-whatsapp]'),null);
  assert.equal(h.document.querySelector('#osNotificationsToggle').closest('.os-notifications').hidden,true);
  assert.equal(h.document.querySelector('#osConsultantFab').classList.contains('hidden'),true);
  assert.equal(visible(h.document.querySelector('#osThemeToggle')),true);
}

async function subscriptionPanel(options={}){
  const respond=options.respond;
  return makePanel({role:'member',initialRoute:'dashboard',permissions:subscriptionPlan(),...options,
    respond(path,method,payload,headers){
      const result=respond?.(path,method,payload,headers);
      if(result!==undefined)return result;
      if(path==='/api/v1/account/businesses')return [business()];
      if(path==='/api/v1/account/business-identity')return {
        name:'Empresa de assinaturas',contact_phone:'+5567999999901'
      };
    }});
}

for(const code of ['none','essencial','profissional','elite']){
  test('subscription profile keeps only utilities even with '+code+' plan metadata',async t=>{
    // Deliberately leave the normal tier's permission flags in this response:
    // the workspace restriction must also win while tier metadata is stale.
    const h=await makeSuite({initialRoute:'plano',permissions:subscriptionPlan(code),
      bridge:{tenants:[business()]}});t.after(()=>h.close());await flush();
    assertSubscriptionShell(h);
    assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'novidades');
    assert.equal(new URL(h.window.location.href).searchParams.get('aba'),'novidades');
    assert.equal(operationalCalls(h).length,0);
    assert.equal(h.calls.some(call=>call.method!=='GET'),false);
    assert.deepEqual(h.errors,[]);
  });
}

test('all operational deep links return to News before any module request',async t=>{
  const h=await subscriptionPanel({initialRoute:'site',permissions:subscriptionPlan('elite')});
  t.after(()=>h.close());
  const routes=['dashboard','agenda','clientes','inbox','servicos','funcionarios',
    'financeiro','desempenho','marketing','site','fidelizacao','consultor','plano',
    'configuracoes','dispositivo','conversas','ai','empresa','catalogo','cadastros','acelerar'];
  for(const route of routes){
    await h.window.OrionSuite.open(route,{force:true});await flush();
    assert.equal(h.element('#page-suite').dataset.osCurrentRoute,'novidades',route);
    assertSubscriptionShell(h);
  }
  assert.equal(operationalCalls(h).length,0);
  assert.equal(h.calls.some(call=>call.method!=='GET'),false);
  assert.equal(h.element('#logout').textContent.trim(),'Sair com segurança');
  assert.deepEqual(h.errors,[]);
});

test('standard News and Help remain usable without unavailable module shortcuts',async t=>{
  const h=await subscriptionPanel();t.after(()=>h.close());
  assert.ok(h.document.querySelectorAll('.os-news-card').length>0);
  assert.equal(h.document.querySelector('.os-news-card [data-os-route]'),null);
  h.click('#osNav [data-os-route=ajuda]');await flush();
  assert.match(h.element('.os-help-hero').textContent,/Base de conhecimento/);
  h.click('[data-os-help-article=publicar-site]');await flush();
  assert.match(h.element('.os-help-article h1').textContent,/Como revisar e publicar/);
  assert.equal(h.document.querySelector('.os-help-article [data-os-route]'),null);
  h.click('[data-os-action=help-back]');await flush();
  h.fill('#osHelpSearch','minha conta');await flush();
  assert.match(h.element('#osHelpResults').textContent,/Como acessar a empresa da minha conta/);
  h.click('#osNav [data-os-route=novidades]');await flush();
  assert.equal(h.element('#page-suite').dataset.osCurrentRoute,'novidades');
  assert.equal(operationalCalls(h).length,0);
  assert.deepEqual(h.errors,[]);
});

test('My account keeps the existing company and password forms without plan navigation',async t=>{
  const h=await subscriptionPanel({initialRoute:'member',respond(path,method,payload){
    if(path==='/api/v1/account/business-identity'&&method==='PUT')return {
      name:payload.name,contact_phone:'+5567999999901'
    };
  }});t.after(()=>h.close());
  assertSubscriptionShell(h);
  assert.equal(h.element('#page-member').classList.contains('hidden'),false);
  assert.equal(h.element('#memberBusinessName').value,'Empresa de assinaturas');
  assert.ok(h.element('#memberPasswordForm'));
  assert.equal(h.element('#adminClientCenter').classList.contains('hidden'),true);
  h.fill('#memberBusinessName','Assinaturas atualizadas');h.submit('#memberBusinessForm');await flush();
  assert.match(h.element('#memberBusinessFeedback').textContent,/atualizados com sucesso/);
  assert.equal(h.element('.os-tenant-name').textContent,'Assinaturas atualizadas');
  assert.deepEqual(h.calls.filter(call=>call.method!=='GET').map(call=>[call.method,call.path]),
    [['PUT','/api/v1/account/business-identity']]);
  assert.equal(operationalCalls(h).length,0);
  assert.deepEqual(h.errors,[]);
});

function quickReview(){return fullState({current_step:'review',
  steps:['business','location','profile','goals','review'].map(id=>({id,required:true,complete:id!=='review'})),
  configuration:{niche:'servico_assinaturas',activity:'products',subscription_offering:'both',
    contact_phone:'+5567999999901',modality:'online',
    business_metrics:{operating_age:'1_to_3_years',team_size:'1_2',monthly_revenue:'none',monthly_volume:'none'},
    business_priorities:{main_challenge:'whatsapp',desired_outcome:'automate_whatsapp',respondent_role:'owner'}},
  catalog:{professionals:[],services:[],products:[],hours:[]}});}

test('onboarding hides plan access and completion opens News with saved subscription choice',async t=>{
  const state=quickReview();
  const h=await subscriptionPanel({respond(path,method){
    if(path==='/api/v1/onboarding/entry')return {enabled:true,kind:'onboarding',tenant_id:tenantA};
    if(path.endsWith('/onboarding')&&method==='GET')return state;
    if(path.endsWith('/onboarding/complete')&&method==='POST')return {...state,status:'complete',version:state.version+1};
  }});t.after(()=>h.close());
  assert.equal(h.element('#appView').classList.contains('ob-gated'),true);
  assert.equal(h.element('[data-setup-access=plano]').hidden,true);
  assert.equal(h.element('[data-setup-access=account]').hidden,false);
  assert.match(h.element('.ob-review').textContent,/Assinaturas e vendas avulsas/);
  h.element('[data-ob-form=complete] [name=acknowledged]').checked=true;
  h.submit('[data-ob-form=complete]');await flush();
  assert.equal(h.element('#orionSetup').hidden,true);
  assert.equal(h.element('#page-suite').dataset.osCurrentRoute,'novidades');
  assertSubscriptionShell(h);
  assert.equal(h.calls.some(call=>/\/management\/(?:plan-|dashboard)/.test(call.path)),false);
  assert.deepEqual(h.calls.filter(call=>call.method!=='GET').map(call=>call.path),
    ['/api/v1/tenants/'+tenantA+'/onboarding/complete']);
  assert.deepEqual(h.errors,[]);
});

test('early business profile suppresses plan shortcuts while permissions are unresolved or unavailable',async t=>{
  const permission=deferred(),state=quickReview();
  const h=await subscriptionPanel({permissions:()=>permission.promise,respond(path,method){
    if(path==='/api/v1/onboarding/entry')return {enabled:true,kind:'onboarding',tenant_id:tenantA};
    if(path.endsWith('/onboarding')&&method==='GET')return state;
  }});t.after(()=>h.close());
  assertSubscriptionShell(h,false);
  assert.equal(h.element('[data-setup-access=plano]').hidden,true);
  permission.resolve(jsonResponse({detail:'Consulta temporariamente indisponível'},503));await flush();
  assertSubscriptionShell(h,false);
  assert.equal(h.element('[data-setup-access=plano]').hidden,true);
  assert.equal(h.calls.some(call=>/\/management\/(?:plan-|dashboard)/.test(call.path)),false);
  assert.deepEqual(h.errors,[]);
});

test('permission refresh failure retains the known subscription workspace without a plan fallback',async t=>{
  let failed=false;
  const h=await subscriptionPanel({permissions:()=>failed
    ?jsonResponse({detail:'Sem conexão'},503):{effective_plan:subscriptionPlan('elite')}});
  t.after(()=>h.close());failed=true;
  h.window.dispatchEvent(new h.window.Event('focus'));await flush();
  assertSubscriptionShell(h,false);
  assert.equal(h.element('#page-suite').dataset.osCurrentRoute,'novidades');
  assert.equal(operationalCalls(h).length,0);
  assert.deepEqual(h.errors,[]);
});

test('changing the workspace profile drops pending module data and restores ordinary navigation when reverted',async t=>{
  const late=deferred();let value=planState('elite',{workspace_profile:'standard'});
  const h=await makeSuite({initialRoute:'clientes',permissions:()=>({effective_plan:value}),
    bridge:{tenants:[business({workspace_profile:'standard'})]},
    respond:path=>path.includes('/management/customers')?late.promise:defaultResponse(path)});
  t.after(()=>h.close());await flush();
  value=subscriptionPlan('elite');h.window.dispatchEvent(new h.window.Event('orion:plan-changed'));await flush();
  assertSubscriptionShell(h);
  assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'novidades');
  late.resolve({items:[{id:'synthetic-old-customer',name:'DADO ANTIGO QUE NÃO PODE REAPARECER'}],total:1});await flush();
  assert.doesNotMatch(h.document.querySelector('#page-suite').textContent,/DADO ANTIGO/);
  value=planState('elite',{workspace_profile:'standard',revision:'standard-restored'});
  h.window.dispatchEvent(new h.window.Event('orion:plan-changed'));await flush();
  assert.ok(h.document.querySelector('#osNav [data-os-route=agenda]'));
  assert.ok(h.document.querySelector('#osNav [data-os-route=plano]'));
  assert.ok(h.document.querySelector('#osSidebarContext .os-side-site'));
  assert.deepEqual(h.errors,[]);
});

test('a stale permission response cannot replace a newly bound workspace profile',async t=>{
  const old=deferred();let reads=0;
  const h=await makeSuite({initialRoute:'dashboard',permissions:()=>++reads===1?old.promise:
    {effective_plan:subscriptionPlan()},bridge:{tenants:[business({workspace_profile:'standard'})]}});
  t.after(()=>h.close());await flush();
  h.window.OrionSuite.sync({tenants:[business()]});await flush();
  assertSubscriptionShell(h);
  old.resolve({effective_plan:planState('elite',{workspace_profile:'standard'})});await flush();
  assertSubscriptionShell(h);
  assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'novidades');
  assert.equal(operationalCalls(h).length,0);
  assert.deepEqual(h.errors,[]);
});

test('account changes discard the subscription profile and preserve the next normal business',async t=>{
  let value=subscriptionPlan();
  const h=await makeSuite({initialRoute:'novidades',permissions:()=>({effective_plan:value}),
    bridge:{tenants:[business()]}});t.after(()=>h.close());await flush();
  assertSubscriptionShell(h);
  value=planState('essencial',{workspace_profile:'standard'});
  h.window.OrionSuite.sync({user:{role:'member',email:'next@example.invalid'},tenantId:tenantB,
    tenants:[business({id:tenantB,name:'Barbearia sintética',workspace_profile:'standard'})]});await flush();
  assert.equal(h.window.OrionSuite.workspaceProfile(),'standard');
  assert.ok(h.document.querySelector('#osNav [data-os-route=agenda]'));
  assert.ok(h.document.querySelector('#osNav [data-os-route=plano]'));
  assert.ok(h.document.querySelector('#osSidebarContext .os-side-site'));
  assert.equal(h.document.querySelector('.os-tenant-name').textContent,'Barbearia sintética');
  assert.deepEqual(h.errors,[]);
});

test('platform administration retains its complete navigation independently of a tenant niche',async t=>{
  const h=await makeSuite({initialRoute:'novidades',permissions:subscriptionPlan(),
    bridge:{user:{role:'admin',email:'platform@example.invalid'},tenants:[business()]}});
  t.after(()=>h.close());await flush();
  assert.equal(h.window.OrionSuite.workspaceProfile(),'standard');
  assert.ok(h.document.querySelector('#osNav [data-os-route=plano]'));
  assert.ok(h.document.querySelector('#osNav [data-os-route=conversas]'));
  assert.match(h.document.querySelector('#osNav [data-os-route=member]').textContent,/Administração de clientes/);
  assert.ok(h.document.querySelector('#osSidebarContext .os-side-site'));
  assert.deepEqual(h.errors,[]);
});

test('customer maintenance keeps the subscription workspace and My account label',async t=>{
  // Maintenance authentication returns the customer's effective member role.
  const h=await makeSuite({initialRoute:'plano',permissions:subscriptionPlan('elite'),
    bridge:{user:{role:'member',email:'customer@example.invalid',maintenance:{active:true}},
      tenants:[business()]}});t.after(()=>h.close());await flush();
  assertSubscriptionShell(h);
  assert.match(h.document.querySelector('#osNav [data-os-route=member]').textContent,/Minha conta/);
  assert.doesNotMatch(h.document.querySelector('#osNav').textContent,/Administração de clientes/);
  assert.equal(operationalCalls(h).length,0);
  assert.deepEqual(h.errors,[]);
});
