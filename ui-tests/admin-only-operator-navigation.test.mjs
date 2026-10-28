import test from 'node:test';
import assert from 'node:assert/strict';
import {makeSuite,flush,tenantA} from './harness.mjs';
import {makePanel} from './panel-harness.mjs';

const operatorRoutes=['conversas','ai','empresa','catalogo','cadastros'];
const operatorActions=['conversas','ai','empresa','catalogo'];

for(const role of ['member','support','editor','viewer']){
  test('only platform admin has legacy Atendimento Orion navigation: '+role,async t=>{
    const visited=[];
    const h=await makeSuite({
      initialRoute:'dashboard',
      role:role==='viewer'?'viewer':'owner',
      bridge:{
        user:{role,name:'Conta sintética',email:'user@example.invalid'},
        openLegacy:route=>visited.push(route)
      }
    });
    t.after(()=>h.close());await flush();
    const nav=h.document.querySelector('#osNav');
    assert.ok(nav);
    assert.equal([...nav.querySelectorAll('.os-nav-section')]
      .some(node=>node.textContent==='Atendimento Orion'),false);
    assert.ok(nav.querySelector('[data-os-route=dashboard]'));
    assert.ok(nav.querySelector('[data-os-route=agenda]'));
    assert.ok(nav.querySelector('[data-os-route=configuracoes]'));
    assert.ok(nav.querySelector('[data-os-route=site]'));
    if(role==='support'){
      assert.ok(nav.querySelector('[data-os-route=dispositivo]'),
        'support still retains its independent connection shortcut');
    }else{
      assert.ok(nav.querySelector('[data-os-action=managed-whatsapp]'),
        'business accounts retain their own WhatsApp settings');
    }
    for(const route of operatorActions)assert.equal(nav.querySelector('[data-os-route='+route+']'),null);
    for(const route of operatorRoutes){
      await h.window.OrionSuite.open(route);await flush();
      assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'dashboard',route);
      assert.equal(new URL(h.window.location.href).searchParams.get('aba'),'dashboard',route);
      assert.equal(h.document.querySelector('[data-os-route='+route+']'),null,route);
    }
    assert.deepEqual(visited,[],'not even programmatic deep links open legacy resources');
    assert.equal(h.calls.some(call=>/\/business\/profile|\/commerce\/|\/qr\/chats/.test(call.path)),false);
    assert.equal(h.calls.some(call=>call.method!=='GET'),false);
    assert.deepEqual(h.errors,[]);
  });
}

test('admin retains all original operator routes while normal Dashboard remains available',async t=>{
  const visited=[];
  const h=await makeSuite({initialRoute:'dashboard',bridge:{
    user:{role:'admin',name:'Operador',email:'admin@example.invalid'},
    openLegacy:route=>visited.push(route),
  }});
  t.after(()=>h.close());await flush();
  const nav=h.document.querySelector('#osNav');
  assert.ok([...nav.querySelectorAll('.os-nav-section')].some(node=>node.textContent==='Atendimento Orion'));
  for(const route of operatorActions)assert.ok(nav.querySelector('[data-os-route='+route+']'),route);
  assert.equal(nav.querySelector('[data-os-route=cadastros]'),null);
  for(const route of operatorRoutes){
    await h.window.OrionSuite.open(route);await flush();
  }
  assert.deepEqual(visited,operatorRoutes);
  assert.ok(nav.querySelector('[data-os-route=dashboard]'));
  assert.equal(h.calls.some(c=>c.method!=='GET'),false);
  assert.deepEqual(h.errors,[]);
});

for(const role of ['member','support']){
  test(role+' bookmark /panel?aba=catalogo opens Dashboard, no operator screen',async t=>{
    const called=[];
    const h=await makeSuite({initialRoute:'catalogo',url:'https://orion.test/panel?aba=catalogo',
      bridge:{user:{role,name:'Conta sintética',email:'user@example.invalid'},
        openLegacy:route=>called.push(route)}});
    t.after(()=>h.close());await flush();
    assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'dashboard');
    assert.equal(new URL(h.window.location.href).searchParams.get('aba'),'dashboard');
    assert.deepEqual(called,[]);
  });
}

test('switching from platform admin to a regular account clears operator menu and legacy route',async t=>{
  const visited=[];
  const h=await makeSuite({initialRoute:'dashboard',bridge:{
    user:{role:'admin',name:'Operador',email:'admin@example.invalid'},
    openLegacy:route=>visited.push(route)
  }});
  t.after(()=>h.close());await flush();
  await h.window.OrionSuite.open('catalogo');await flush();
  assert.equal(visited.at(-1),'catalogo');
  h.window.OrionSuite.sync({
    user:{role:'member',name:'Proprietária',email:'owner@example.invalid'},
    tenants:[{id:tenantA,name:'Negócio A',role:'owner',timezone:'America/Sao_Paulo'}],
  });
  await flush();
  assert.equal(h.document.querySelector('#osNav [data-os-route=catalogo]'),null);
  assert.ok(!h.document.querySelector('#osNav').textContent.includes('Atendimento Orion'));
  assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'dashboard');
  assert.equal(new URL(h.window.location.href).searchParams.get('aba'),'dashboard');
  assert.deepEqual(visited,['catalogo']);
  assert.deepEqual(h.errors,[]);
});

for(const role of ['member','support']){
  test('shipped panel keeps legacy sections hidden for '+role+' even with direct button clicks',async t=>{
    const h=await makePanel({role,initialRoute:'catalogo'});t.after(()=>h.close());await flush();
    for(const id of ['aiNav','businessNav','commerceNav','catalogNav'])
      assert.equal(h.element('#'+id).classList.contains('hidden'),true,id);
    assert.equal(h.document.querySelector('#osNav [data-os-route=catalogo]'),null);
    assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'dashboard');
    assert.equal(new URL(h.window.location.href).searchParams.get('aba'),'dashboard');
    h.click('#commerceNav');await flush();
    assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'dashboard');
    assert.equal(h.calls.filter(c=>/\/commerce\/status/.test(c.path)).length,0);
    assert.equal(h.calls.filter(c=>/\/business\/profile$/.test(c.path)).length,0);
    assert.deepEqual(h.errors,[]);
  });
}
test('shipped platform-admin panel retains the three legacy options',async t=>{
  const h=await makePanel({role:'admin',initialRoute:'dashboard'});t.after(()=>h.close());await flush();
  for(const id of ['aiNav','businessNav','commerceNav'])
    assert.equal(h.element('#'+id).classList.contains('hidden'),false,id);
  for(const route of ['ai','empresa','catalogo'])
    assert.ok(h.document.querySelector('#osNav [data-os-route='+route+']'));
  assert.ok(h.document.querySelector('#osNav').textContent.includes('Atendimento Orion'));
  assert.deepEqual(h.errors,[]);
});
