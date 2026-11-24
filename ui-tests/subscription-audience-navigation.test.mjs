import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import {makeSuite,planState,tenantA,flush,defaultResponse} from './harness.mjs';

const source=await readFile(new URL('../app/static/orion-subscription-audience.js',import.meta.url),'utf8');
const fixture=JSON.parse(await readFile(new URL('../tests/fixtures/subscription_audience_contract.json',import.meta.url),'utf8'));
const features=['account.manage','devices.manage','chatbot.manage','templates.manage','sent_messages.manage','diagnostics.manage',
  'campaigns.manage','scheduled_messages.manage','bulk_messages.manage','direct_messages.manage','whatsapp_guidance.view',
  'group_contacts.manage','group_add.manage','number_check.manage','whatsapp_link.manage','audience.manage'];
const plan=profile=>planState('elite',{workspace_profile:profile,features,permissions:Object.fromEntries(features.map(f=>[f,true]))});
const business=(profile='subscription_services',role='owner')=>({id:tenantA,name:'Empresa sintética',role,status:'active',slug:'sintetica',timezone:'America/Sao_Paulo',workspace_profile:profile});
const groups=h=>{
  const map=new Map();let current;
  for(const node of h.document.getElementById('osNav').children){
    if(node.classList.contains('os-nav-section')){current=node.textContent.trim();assert.equal(map.has(current),false);map.set(current,[]);}
    else if(current&&node.dataset.osRoute)map.get(current).push(node.dataset.osRoute);
  }return map;
};

test('subscription navigation places four working routes in Ferramentas and Público, with distinct existing icons',async t=>{
  const h=await makeSuite({initialRoute:'novidades',permissions:plan('subscription_services'),bridge:{tenants:[business()]}});t.after(()=>h.close());await flush();
  const sections=groups(h);assert.deepEqual([...sections.keys()],['Gestão','Ferramentas','Público','Ajuda e conta']);
  assert.deepEqual(sections.get('Público'),['contatos','grupos-contatos']);
  assert.deepEqual(sections.get('Ferramentas'),['extrator-contatos','adicionar-ao-grupo','verificador-numeros','link-whatsapp','aquecimento','evitar-banimento','resolver-banimento']);
  assert.equal(sections.get('Gestão').includes('contatos'),false);
  const links=[...h.document.querySelectorAll('#osNav [data-os-route]')];assert.equal(new Set(links.map(node=>node.dataset.osRoute)).size,links.length);
  assert.equal(new Set(links.map(node=>node.querySelector('svg')?.innerHTML)).size,links.length);
  assert.match(h.document.querySelector('[data-os-route="verificador-numeros"]').textContent,/Verificador de números WhatsApp/);assert.deepEqual(h.errors,[]);
});

test('Público is restricted to active subscription owners even if another profile advertises its feature',async t=>{
  for(const [profile,role] of [['standard','owner'],['subscription_services','viewer']]){
    const h=await makeSuite({initialRoute:'novidades',permissions:plan(profile),bridge:{tenants:[business(profile,role)]}});t.after(()=>h.close());await flush();
    assert.equal(groups(h).has('Público'),false);assert.equal(h.document.querySelector('#osNav [data-os-route="verificador-numeros"]'),null);
    await h.window.OrionSuite.open('contatos',{force:true});await flush();assert.equal(h.calls.some(call=>call.path.includes('/audience/')),false);assert.deepEqual(h.errors,[]);
  }
});

test('group Ver contatos switches the actual suite route and carries the group filter',async t=>{
  const h=await makeSuite({initialRoute:'grupos-contatos',permissions:plan('subscription_services'),bridge:{tenants:[business()]},
    configureWindow:window=>window.eval(source),respond(path){
      if(path.includes('/audience/groups?'))return fixture.groups;
      if(path.includes('/audience/contacts?'))return fixture.contacts;
      return defaultResponse(path);
    }});t.after(()=>h.close());await flush();
  assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'grupos-contatos');
  h.click('[data-pa-action="group-contacts"]');await flush();
  assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'contatos');
  assert.equal(h.document.querySelector('#pa-group-filter').value,fixture.groups.items[0].id);
  assert.ok(h.calls.some(call=>call.path.includes('/audience/contacts?')&&call.path.includes('group_id='+fixture.groups.items[0].id)));
  assert.deepEqual(h.errors,[]);
});
