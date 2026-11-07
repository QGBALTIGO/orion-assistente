import test from 'node:test';
import assert from 'node:assert/strict';
import {makeSuite,planState,tenantA,tenantB,flush} from './harness.mjs';

const business=(profile='subscription_services',id=tenantA)=>({id,name:'Empresa sintética',role:'owner',slug:'empresa',timezone:'America/Sao_Paulo',workspace_profile:profile});
const compact=h=>h.document.getElementById('osSidebarContext').classList.contains('os-sidebar-context--subscription');
const site=h=>h.document.querySelector('#osSidebarContext .os-side-site');

test('compact spacing follows profile refresh and restores the standard sidebar',async t=>{
  let value=planState('elite',{workspace_profile:'standard',revision:'spacing-standard'});
  const h=await makeSuite({initialRoute:'novidades',permissions:()=>({effective_plan:value}),bridge:{tenants:[business('standard')]}});
  t.after(()=>h.close());await flush();
  assert.equal(compact(h),false);assert.ok(site(h));
  value=planState('elite',{workspace_profile:'subscription_services',revision:'spacing-subscription'});
  h.window.dispatchEvent(new h.window.Event('orion:plan-changed'));await flush();
  assert.equal(compact(h),true);assert.equal(site(h),null);
  value=planState('elite',{workspace_profile:'standard',revision:'spacing-standard-restored'});
  h.window.dispatchEvent(new h.window.Event('orion:plan-changed'));await flush();
  assert.equal(compact(h),false);assert.ok(site(h));
  assert.deepEqual(h.errors,[]);
});

test('compact spacing is removed when another ordinary account is loaded',async t=>{
  let value=planState('elite',{workspace_profile:'subscription_services',revision:'spacing-account-first'});
  const h=await makeSuite({initialRoute:'novidades',permissions:()=>({effective_plan:value}),bridge:{tenants:[business()]}});
  t.after(()=>h.close());await flush();
  assert.equal(compact(h),true);assert.equal(site(h),null);
  value=planState('elite',{workspace_profile:'standard',revision:'spacing-account-next'});
  h.window.OrionSuite.sync({user:{role:'member',email:'next@example.invalid'},tenantId:tenantB,tenants:[business('standard',tenantB)]});await flush();
  assert.equal(compact(h),false);assert.ok(site(h));
  assert.deepEqual(h.errors,[]);
});

const subscriptionManagementRoutes=[
  'dispositivos','chatbot','testar-chatbot','templates','mensagens-enviadas','diagnosticos',
  'campanhas','programadas','massa','enviar-mensagem','extrator-contatos','adicionar-ao-grupo',
  'aquecimento','evitar-banimento','resolver-banimento',
];
const subscriptionManagementFeatures=[
  'account.manage','devices.manage','chatbot.manage','templates.manage','sent_messages.manage',
  'diagnostics.manage','campaigns.manage','scheduled_messages.manage','bulk_messages.manage',
  'direct_messages.manage','whatsapp_guidance.view','group_contacts.manage','group_add.manage',
];
const iconHTML=button=>{
  const svg=button.querySelector(':scope > svg.os-icon');
  assert.ok(svg,'missing SVG: '+button.dataset.osRoute);
  assert.equal(svg.getAttribute('viewBox'),'0 0 24 24');
  assert.equal(svg.getAttribute('aria-hidden'),'true');
  assert.equal(svg.getAttribute('focusable'),'false');
  assert.ok(svg.querySelector('path')?.getAttribute('d'),'missing SVG path');
  return svg.innerHTML;
};

test('subscription management shows distinct SVG geometry for every function',async t=>{
  const permissions=Object.fromEntries(subscriptionManagementFeatures.map(feature=>[feature,true]));
  const h=await makeSuite({
    initialRoute:'aquecimento',
    permissions:planState('elite',{
      workspace_profile:'subscription_services',revision:'unique-svg-icons',
      features:subscriptionManagementFeatures,permissions,
    }),
    bridge:{tenants:[{...business(),status:'active'}]},
  });
  t.after(()=>h.close());await flush();
  const links=[...h.document.querySelectorAll('#osNav .os-nav-link[data-os-route]')]
    .filter(node=>subscriptionManagementRoutes.includes(node.dataset.osRoute));
  assert.deepEqual(links.map(node=>node.dataset.osRoute),subscriptionManagementRoutes);
  assert.equal(new Set(links.map(iconHTML)).size,subscriptionManagementRoutes.length);
  assert.notEqual(iconHTML(links.find(node=>node.dataset.osRoute==='evitar-banimento')),
    iconHTML(links.find(node=>node.dataset.osRoute==='resolver-banimento')));
  assert.notEqual(iconHTML(links.find(node=>node.dataset.osRoute==='mensagens-enviadas')),
    iconHTML(links.find(node=>node.dataset.osRoute==='massa')));
  assert.notEqual(iconHTML(links.find(node=>node.dataset.osRoute==='massa')),
    iconHTML(links.find(node=>node.dataset.osRoute==='enviar-mensagem')));
  assert.deepEqual(h.errors,[]);
});

test('standard business also keeps unique menu SVGs for clients and staff',async t=>{
  const h=await makeSuite({initialRoute:'dashboard',
    permissions:planState('elite',{revision:'unique-svg-standard'}),
    bridge:{tenants:[business('standard')]}});
  t.after(()=>h.close());await flush();
  const routes=['dashboard','agenda','clientes','inbox','servicos','funcionarios','financeiro',
    'desempenho','marketing','site','fidelizacao','consultor','plano','configuracoes'];
  const links=[...h.document.querySelectorAll('#osNav .os-nav-link[data-os-route]')]
    .filter(node=>routes.includes(node.dataset.osRoute));
  assert.deepEqual(links.map(node=>node.dataset.osRoute),routes);
  assert.equal(new Set(links.map(iconHTML)).size,routes.length);
  assert.deepEqual(h.errors,[]);
});

const navigationGroups=h=>{
  const groups=new Map();
  let current=null;
  for(const node of h.document.getElementById('osNav').children){
    if(node.classList.contains('os-nav-section')){
      current=node.textContent.trim();
      assert.equal(groups.has(current),false,'duplicate menu section: '+current);
      groups.set(current,[]);
    }else if(current&&node.hasAttribute('data-os-route')){
      groups.get(current).push(node.getAttribute('data-os-route'));
    }
  }
  return groups;
};

test('subscription services groups five tools after management and before help',async t=>{
  const permissions=Object.fromEntries(subscriptionManagementFeatures.map(feature=>[feature,true]));
  const h=await makeSuite({
    initialRoute:'aquecimento',
    permissions:planState('elite',{
      workspace_profile:'subscription_services',revision:'tools-group-owner',
      features:subscriptionManagementFeatures,permissions,
    }),
    bridge:{tenants:[{...business(),status:'active'}]},
  });
  t.after(()=>h.close());await flush();
  const groups=navigationGroups(h);
  assert.deepEqual([...groups.keys()],['Gestão','Ferramentas','Ajuda e conta']);
  assert.deepEqual(groups.get('Ferramentas'),[
    'extrator-contatos','adicionar-ao-grupo','aquecimento',
    'evitar-banimento','resolver-banimento',
  ]);
  assert.deepEqual(groups.get('Gestão'),[
    'dispositivos','chatbot','testar-chatbot','templates',
    'mensagens-enviadas','diagnosticos','campanhas','programadas',
    'massa','enviar-mensagem',
  ]);
  const routes=[...h.document.querySelectorAll('#osNav [data-os-route]')]
    .map(node=>node.getAttribute('data-os-route'));
  assert.equal(new Set(routes).size,routes.length,'routes must not appear in both sections');
  assert.deepEqual(h.errors,[]);
});

test('Ferramentas is absent for non-owner subscriptions and standard businesses',async t=>{
  const permissions=Object.fromEntries(subscriptionManagementFeatures.map(feature=>[feature,true]));
  const viewer=await makeSuite({
    initialRoute:'novidades',
    permissions:planState('elite',{
      workspace_profile:'subscription_services',revision:'tools-group-viewer',
      features:subscriptionManagementFeatures,permissions,
    }),
    bridge:{tenants:[{...business(),role:'viewer',status:'active'}]},
  });
  t.after(()=>viewer.close());await flush();
  assert.equal(navigationGroups(viewer).has('Ferramentas'),false);
  assert.deepEqual(viewer.errors,[]);

  const standard=await makeSuite({
    initialRoute:'dashboard',
    permissions:planState('elite',{workspace_profile:'standard',revision:'tools-group-standard'}),
    bridge:{tenants:[business('standard')]},
  });
  t.after(()=>standard.close());await flush();
  assert.equal(navigationGroups(standard).has('Ferramentas'),false);
  assert.deepEqual(standard.errors,[]);
});
