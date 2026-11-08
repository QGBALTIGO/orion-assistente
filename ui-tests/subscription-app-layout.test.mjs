import test from 'node:test';
import assert from 'node:assert/strict';
import { makeSuite, planState, flush, tenantA } from './harness.mjs';

const defaultSubscriptionTabs=['chatbot','templates','enviar-mensagem','diagnosticos','perfil'];
const allowedFeatures=[
  'account.manage','business.setup','devices.manage','chatbot.manage',
  'templates.manage','sent_messages.manage','diagnostics.manage',
  'campaigns.manage','scheduled_messages.manage','bulk_messages.manage',
  'direct_messages.manage','group_contacts.manage','group_add.manage',
  'whatsapp_guidance.view'
];
const moduleNames=[
  'OrionSubscriptionDevices','OrionSubscriptionChatbot','OrionSubscriptionChatbotTest',
  'OrionSubscriptionTemplates','OrionSubscriptionSentMessages',
  'OrionSubscriptionDiagnostics','OrionSubscriptionDirectMessages',
  'OrionSubscriptionCampaigns','OrionSubscriptionScheduledMessages',
  'OrionSubscriptionBulkMessages','OrionSubscriptionGroupContacts','OrionSubscriptionGroupAdd'
];
const tabValues = h => [...h.document.querySelectorAll('#osAppDock [data-os-app-tab]')].map(el=>el.dataset.osAppTab);
const activeTab = h => h.document.querySelector('#osAppDock [aria-current=page]')?.dataset.osAppTab;
const tabStorage = 'orion-quick-tabs-v1:'+tenantA+':synthetic@example.invalid:subscription_services';
const layoutStorage = 'orion-navigation-v1:'+tenantA+':synthetic@example.invalid';

function subscriptionPermissions(overrides={}){
  const all=planState('elite');
  const permissions=Object.fromEntries(Object.keys(all.permissions).map(feature=>[feature,allowedFeatures.includes(feature)]));
  return planState('elite',{
    workspace_profile:'subscription_services',
    layout_modes:['classic','app'],
    features:allowedFeatures,
    permissions:{...permissions,...overrides},
    revision:'subscription-test-v1'
  });
}
function subscriptionHarness({role='owner',permissions=subscriptionPermissions(),initialRoute='novidades',configureWindow,respond}={}){
  const tenants=[{id:tenantA,name:'Empresa Assinaturas Sintética',slug:'assinaturas-sinteticas',
    timezone:'America/Campo_Grande',workspace_profile:'subscription_services',role,status:'active'}];
  return makeSuite({initialRoute,role,permissions,
    bridge:{tenants,tenantId:tenantA},
    respond,
    configureWindow:win=>{
      moduleNames.forEach(name=>{win[name]={
        mount:async ({host})=>{if(host)host.setAttribute('data-test-mounted',name);},
        unmount(){},clearContext(){},navigationState(){return {};}
      };});
      configureWindow?.(win);
    }});
}
async function enableSubscriptionApp(h){
  await h.window.OrionSuite.open('configuracoes');await flush();
  h.click('[data-os-settings=appearance]');await flush();
  h.click('[data-os-layout=app]');await flush();
}
async function editShortcut(h,index,value){
  const selector='[data-os-app-shortcut="'+index+'"]';
  const input=h.document.querySelector(selector);
  assert.ok(input,'Atalho '+index+' presente');
  input.value=value;
  input.dispatchEvent(new h.window.Event('change',{bubbles:true}));
  await flush();
}

test('subscription services keeps classic by default with an Appearance entry in its sidebar',async t=>{
  const h=await subscriptionHarness();t.after(()=>h.close());await flush();
  assert.equal(h.document.getElementById('osAppDock').hidden,true);
  assert.ok(h.document.querySelector('#osNav [data-os-app-settings=appearance]'));
  h.click('#osNav [data-os-app-settings=appearance]');await flush();
  assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'configuracoes');
  assert.equal(h.document.querySelector('select#osSettingsSection')?.value,'appearance');
  assert.ok(h.document.querySelector('[data-os-layout=app]'));
  assert.equal(h.document.querySelectorAll('[data-os-app-shortcut]').length,4);
  assert.equal(h.document.querySelector('[data-os-settings=general]'),null);
  assert.equal(h.errors.length,0);
});

test('default subscription app bar contains Chatbot, Templates, Enviar mensagens, Diagnostico and Perfil',async t=>{
  const h=await subscriptionHarness();t.after(()=>h.close());await flush();
  await enableSubscriptionApp(h);
  assert.deepEqual(tabValues(h),defaultSubscriptionTabs);
  assert.equal(h.document.querySelector('#osAppDock [data-os-app-tab=enviar-mensagem]')?.getAttribute('aria-label'),'Enviar mensagens');
  assert.equal(h.document.querySelector('#osAppDock [data-os-app-tab=enviar-mensagem] .os-app-dock-label')?.textContent,'Enviar');
  assert.equal(h.document.querySelector('#osAppDock [data-os-app-tab=diagnosticos]')?.getAttribute('aria-label'),'Diagnóstico');
  assert.equal(h.document.querySelector('#osAppDock [data-os-app-tab=perfil]')?.getAttribute('aria-label'),'Perfil');
  assert.equal(h.document.querySelector('#osAppDock').style.getPropertyValue('--os-app-count'),'5');
  assert.equal(h.document.querySelectorAll('#osAppDock [aria-current=page]').length,1);
  assert.equal(h.errors.length,0);
});

test('four subscribed default destination buttons open real existing module mounts',async t=>{
  const h=await subscriptionHarness();t.after(()=>h.close());await flush();
  await enableSubscriptionApp(h);
  const expected=[
    ['chatbot','osSubscriptionChatbot','OrionSubscriptionChatbot'],
    ['templates','osSubscriptionTemplates','OrionSubscriptionTemplates'],
    ['enviar-mensagem','osSubscriptionDirectMessages','OrionSubscriptionDirectMessages'],
    ['diagnosticos','osSubscriptionDiagnostics','OrionSubscriptionDiagnostics'],
    ['perfil','osPageBody',null]
  ];
  for(const [tab,hostName,moduleName] of expected){
    h.click('#osAppDock [data-os-app-tab='+tab+']');await flush();
    assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,tab);
    assert.equal(activeTab(h),tab);
    assert.equal(h.document.querySelectorAll('#osAppDock [aria-current=page]').length,1);
    const host=h.document.getElementById(hostName);
    assert.ok(host,'existing host '+hostName);
    if(moduleName)assert.equal(host.dataset.testMounted,moduleName);
  }
  assert.match(h.document.querySelector('#osPageBody').textContent,/Atendimento e chatbot/);
  assert.equal(h.errors.length,0);
});

test('subscriber Profile retains all tools, account and the Appearance editor',async t=>{
  const h=await subscriptionHarness();t.after(()=>h.close());await flush();await enableSubscriptionApp(h);
  h.click('#osAppDock [data-os-app-tab=perfil]');await flush();
  for(const name of ['dispositivos','testar-chatbot','mensagens-enviadas','programadas','campanhas','massa','aquecimento','extrator-contatos','adicionar-ao-grupo','novidades','ajuda','member']){
    assert.ok(h.document.querySelector('#osPageBody [data-os-route="'+name+'"]'),'Perfil deve conter '+name);
  }
  assert.ok(h.document.querySelector('#osPageBody [data-os-app-settings=appearance]'));
  assert.equal(h.document.querySelector('#osPageBody [data-os-route=financeiro]'),null);
  assert.equal(h.document.querySelector('#osPageBody [data-os-route=agenda]'),null);
  h.click('#osPageBody [data-os-app-settings=appearance]');await flush();
  assert.equal(h.document.querySelector('#osSettingsSection').value,'appearance');
  assert.equal(activeTab(h),'perfil');
  assert.equal(h.errors.length,0);
});

test('subscription settings are strictly restricted to Appearance and Minha conta in any layout',async t=>{
  const requests=[];
  const h=await subscriptionHarness({respond:(path)=>{requests.push(path);return [];}});
  t.after(()=>h.close());await flush();
  await h.window.OrionSuite.open('configuracoes');await flush();
  assert.deepEqual([...h.document.querySelector('#osSettingsSection').options].map(x=>x.value),['appearance','account']);
  assert.equal(h.document.querySelector('[data-os-settings=general]'),null);
  h.click('[data-os-settings=appearance]');await flush();
  assert.deepEqual([...h.document.querySelector('#osSettingsSection').options].map(x=>x.value),['appearance','account']);
  assert.ok(!requests.some(path=>path.includes('/management/preferences')));
  h.click('[data-os-layout=app]');await flush();
  assert.deepEqual([...h.document.querySelector('#osSettingsSection').options].map(x=>x.value),['appearance','account']);
  assert.ok(!requests.some(path=>path.includes('/management/finance')));
  assert.equal(h.errors.length,0);
});

test('subscription shortcuts can be customized, reordered, persisted and restored without losing Perfil',async t=>{
  const h=await subscriptionHarness();t.after(()=>h.close());await flush();await enableSubscriptionApp(h);
  const options=[...h.document.querySelector('[data-os-app-shortcut="0"]').options].map(x=>x.value);
  assert.ok(options.includes('campanhas')&&options.includes('dispositivos')&&options.includes('programadas'));
  assert.equal(options.includes('financeiro'),false);
  assert.equal(options.includes('dashboard'),false);
  assert.equal(options.includes('perfil'),false);
  await editShortcut(h,0,'diagnosticos'); // swaps with fourth
  await editShortcut(h,1,'campanhas');
  await editShortcut(h,2,'enviar-mensagem');
  await editShortcut(h,3,'');
  assert.deepEqual([...h.document.querySelectorAll('[data-os-app-shortcut]')].map(el=>el.value),['diagnosticos','campanhas','enviar-mensagem','']);
  assert.deepEqual(tabValues(h),defaultSubscriptionTabs); // draft only
  h.click('[data-os-action=app-shortcuts-save]');await flush();
  assert.deepEqual(tabValues(h),['diagnosticos','campanhas','enviar-mensagem','perfil']);
  assert.equal(h.document.querySelector('#osAppDock').style.getPropertyValue('--os-app-count'),'4');
  assert.equal(h.window.localStorage.getItem(tabStorage),JSON.stringify(['diagnosticos','campanhas','enviar-mensagem']));
  h.click('[data-os-action=app-shortcuts-default]');await flush();
  assert.deepEqual(tabValues(h),['diagnosticos','campanhas','enviar-mensagem','perfil']);
  h.click('[data-os-action=app-shortcuts-save]');await flush();
  assert.deepEqual(tabValues(h),defaultSubscriptionTabs);
  assert.equal(h.errors.length,0);
});

test('changing back to classic and returning to app keeps subscription custom shortcuts',async t=>{
  const h=await subscriptionHarness();t.after(()=>h.close());await flush();await enableSubscriptionApp(h);
  await editShortcut(h,0,'campanhas');
  h.click('[data-os-action=app-shortcuts-save]');await flush();
  assert.equal(tabValues(h)[0],'campanhas');
  h.click('[data-os-layout=classic]');await flush();
  assert.equal(h.document.getElementById('osAppDock').hidden,true);
  assert.ok(h.document.querySelector('#osNav [data-os-app-settings=appearance]'));
  h.click('#osNav [data-os-app-settings=appearance]');await flush();
  h.click('[data-os-layout=app]');await flush();
  assert.equal(tabValues(h)[0],'campanhas');
  assert.equal(h.document.querySelector('#osAppDock [data-os-app-tab=perfil]')?.disabled,false);
  assert.equal(h.errors.length,0);
});

test('subscription persisted shortcut keys do not collide with barbershop shortcut preferences',async t=>{
  const h=await subscriptionHarness({configureWindow:win=>{
    win.localStorage.setItem(layoutStorage,'app');
    win.localStorage.setItem('orion-quick-tabs-v1:'+tenantA+':synthetic@example.invalid',JSON.stringify(['agenda','clientes','financeiro']));
  }});
  t.after(()=>h.close());await flush();
  assert.deepEqual(tabValues(h),defaultSubscriptionTabs);
  assert.equal(h.window.localStorage.getItem(tabStorage),null);
});

test('subscription staff without owner role cannot surface tools restricted to subscription owner',async t=>{
  const h=await subscriptionHarness({role:'editor'});t.after(()=>h.close());await flush();
  await enableSubscriptionApp(h);
  h.click('#osAppDock [data-os-app-tab=perfil]');await flush();
  assert.equal(h.document.querySelector('#osPageBody [data-os-route=extrator-contatos]'),null);
  assert.equal(h.document.querySelector('#osPageBody [data-os-route=adicionar-ao-grupo]'),null);
  assert.equal(h.document.querySelector('#osPageBody [data-os-route=evitar-banimento]'),null);
  assert.ok(h.document.querySelector('#osPageBody [data-os-route=templates]'));
  assert.equal(h.errors.length,0);
});

test('subscription revoked permission and malicious storage cannot force hidden routes',async t=>{
  const permissions=subscriptionPermissions({'direct_messages.manage':false,'diagnostics.manage':false});
  const h=await subscriptionHarness({permissions,configureWindow:win=>{
    win.localStorage.setItem(layoutStorage,'app');
    win.localStorage.setItem(tabStorage,JSON.stringify(['enviar-mensagem','diagnosticos','chatbot','templates']));
  }});
  t.after(()=>h.close());await flush();
  assert.deepEqual(tabValues(h),['chatbot','templates','perfil']);
  await h.window.OrionSuite.open('configuracoes');await flush();
  h.click('[data-os-settings=appearance]');await flush();
  const options=[...h.document.querySelector('[data-os-app-shortcut="0"]').options].map(x=>x.value);
  assert.ok(!options.includes('diagnosticos')&&!options.includes('enviar-mensagem'));
  h.window.localStorage.setItem(tabStorage,'["chatbot","chatbot","financeiro","perfil"]');
  const other={...h.bridge.user,email:'other@example.invalid'};
  h.window.OrionSuite.sync({user:other,tenants:h.bridge.tenants,tenantId:tenantA,businessContextReady:true});
  await flush();
  h.window.OrionSuite.sync({user:h.bridge.user,tenants:h.bridge.tenants,tenantId:tenantA,businessContextReady:true});
  await flush();
  assert.deepEqual(tabValues(h),['chatbot','templates','perfil']); // denied default routes pruned
  assert.equal(h.errors.length,0);
});

test('220 rapid subscription tab switches leave only last selected page and no duplicate dock',async t=>{
  const h=await subscriptionHarness();t.after(()=>h.close());await flush();await enableSubscriptionApp(h);
  const items=defaultSubscriptionTabs;
  for(let i=0;i<220;i++)h.click('#osAppDock [data-os-app-tab="'+items[i%items.length]+'"]');
  h.click('#osAppDock [data-os-app-tab=perfil]');await flush();await flush();
  assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'perfil');
  assert.equal(activeTab(h),'perfil');
  assert.equal(h.document.querySelectorAll('#osAppDock [aria-current=page]').length,1);
  assert.equal(h.document.querySelectorAll('#osAppDock').length,1);
  assert.equal(h.document.querySelectorAll('#osAppDock [data-os-app-tab]').length,5);
  assert.match(h.document.querySelector('#osPageBody').textContent,/Atendimento e chatbot/);
  assert.equal(h.errors.length,0);
});
