import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { makeSuite, flush, deferred, defaultResponse, planState, tenantA } from './harness.mjs';

const barberPlan=()=>planState('elite',{layout_modes:['classic','app'],workspace_profile:'standard'});
async function createBarber({respond=defaultResponse}={}){
  const h=await makeSuite({initialRoute:'dashboard',permissions:barberPlan(),respond});
  await flush();
  return h;
}
async function enableApp(h){
  await h.window.OrionSuite.open('configuracoes');
  await flush();
  h.click('[data-os-settings=appearance]');
  await flush();
  h.click('[data-os-layout=app]');
  await flush();
}
function currentTab(h){
  return h.document.querySelector('#osAppDock [aria-current=page]')?.dataset.osAppTab;
}

test('barbershop can choose app layout; current classic remains the default',async t=>{
  const h=await createBarber();t.after(()=>h.close());
  assert.ok(!h.document.querySelector('#appView').classList.contains('os-layout-app'));
  assert.equal(h.document.getElementById('osAppDock').hidden,true);
  await h.window.OrionSuite.open('configuracoes');await flush();
  h.click('[data-os-settings=appearance]');await flush();
  assert.deepEqual([...h.document.querySelectorAll('[data-os-layout]')].map(node=>node.dataset.osLayout),['classic','app']);
  h.click('[data-os-layout=app]');await flush();
  assert.ok(h.document.querySelector('#appView').classList.contains('os-layout-app'));
  assert.equal(h.document.getElementById('osAppDock').hidden,false);
  assert.equal(h.document.querySelectorAll('#osAppDock [data-os-app-tab]').length,5);
  assert.equal(h.window.localStorage.getItem('orion-navigation-v1:'+tenantA+':synthetic@example.invalid'),'app');
  h.click('[data-os-layout=classic]');await flush();
  assert.equal(h.document.getElementById('osAppDock').hidden,true);
  assert.equal(h.document.querySelector('#appView').classList.contains('os-layout-app'),false);
});

test('non-barbershop cannot enable the shell through preferences, stale storage or direct route',async t=>{
  const h=await makeSuite({initialRoute:'dashboard',permissions:planState('elite',{layout_modes:['classic']})});
  t.after(()=>h.close());await flush();
  h.window.localStorage.setItem('orion-navigation-v1:'+tenantA+':synthetic@example.invalid','app');
  await h.window.OrionSuite.open('configuracoes');await flush();
  h.click('[data-os-settings=appearance]');await flush();
  assert.equal(h.document.querySelectorAll('[data-os-layout]').length,0);
  assert.equal(h.document.getElementById('osAppDock').hidden,true);
  await h.window.OrionSuite.open('perfil');await flush();
  assert.notEqual(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'perfil');
});

test('dock opens all five destinations and profile exposes only permitted Orion links',async t=>{
  const h=await createBarber();t.after(()=>h.close());await enableApp(h);
  for(const [tab,route] of [['dashboard','dashboard'],['agenda','agenda'],['servicos','servicos'],['hours','configuracoes'],['perfil','perfil']]){
    h.click('#osAppDock [data-os-app-tab='+tab+']');await flush();
    assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,route);
    assert.equal(currentTab(h),tab);
    assert.equal(h.document.querySelectorAll('#osAppDock [aria-current=page]').length,1);
  }
  assert.match(h.document.querySelector('#osPageBody').textContent,/Gestão do negócio/);
  assert.ok(h.document.querySelector('[data-os-app-settings=appearance]'));
  h.click('[data-os-app-settings=appearance]');await flush();
  assert.equal(h.document.querySelector('select#osSettingsSection')?.value,'appearance');
  assert.equal(currentTab(h),'perfil');
  h.click('#osAppDock [data-os-app-tab=perfil]');await flush();
  h.click('[data-os-route=configuracoes]');await flush();
  assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'configuracoes');
  assert.equal(h.document.querySelector('select#osSettingsSection')?.value,'general');
  assert.equal(h.errors.length,0);
});

test('hours shares existing work schedule and block APIs without a second booking engine',async t=>{
  const calls=[];
  const h=await createBarber({respond:(path,method,data)=>{calls.push(path);return defaultResponse(path,method,data);}});
  t.after(()=>h.close());await enableApp(h);
  h.click('#osAppDock [data-os-app-tab=hours]');await flush();
  assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'configuracoes');
  assert.ok(h.document.querySelector('[data-os-app-hours=hours][aria-selected=true]'));
  assert.ok(calls.some(path=>path.endsWith('/working_hours')));
  h.click('[data-os-app-hours=blocks]');await flush();
  assert.ok(h.document.querySelector('[data-os-app-hours=blocks][aria-selected=true]'));
  assert.ok(calls.some(path=>path.endsWith('/schedule_blocks')));
  assert.ok(calls.some(path=>path.endsWith('/time_off')));
  assert.ok(h.document.querySelector('[data-os-action=new-block]'));
  h.click('[data-os-action=new-block]');await flush();
  assert.ok(h.document.querySelector('[data-os-form=block]'));
  assert.equal(h.errors.length,0);
});

test('rapid navigation keeps only latest page and ignores a delayed older response',async t=>{
  const pending=deferred(),calls=[];
  const h=await createBarber({respond:(path,method,data)=>{
    calls.push(path);
    if(path.includes('/appointments?'))return pending.promise;
    return defaultResponse(path,method,data);
  }});
  t.after(()=>h.close());await enableApp(h);
  const tabs=['dashboard','agenda','servicos','hours','perfil'];
  for(let i=0;i<75;i++)h.click('#osAppDock [data-os-app-tab='+tabs[i%tabs.length]+']');
  h.click('#osAppDock [data-os-app-tab=perfil]');
  await flush();
  pending.resolve({items:[],total:0,offset:0,limit:50,has_more:false});
  await flush();await flush();
  assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'perfil');
  assert.equal(currentTab(h),'perfil');
  assert.equal(h.document.querySelectorAll('#osAppDock [data-os-app-tab]').length,5);
  assert.equal(h.document.querySelectorAll('#osAppDock [aria-current=page]').length,1);
  assert.match(h.document.querySelector('#osPageBody').textContent,/Gestão do negócio/);
  assert.equal(h.errors.length,0);
});

test('navigation preference is account-scoped and does not leak across logins',async t=>{
  const h=await createBarber();t.after(()=>h.close());await enableApp(h);
  h.window.OrionSuite.sync({user:{role:'member',email:'other@example.invalid'},tenants:h.bridge.tenants,tenantId:tenantA,businessContextReady:true});
  await flush();await flush();
  assert.equal(h.document.querySelector('#appView').classList.contains('os-layout-app'),false);
  h.window.OrionSuite.sync({user:h.bridge.user,tenants:h.bridge.tenants,tenantId:tenantA,businessContextReady:true});
  await flush();await flush();
  assert.ok(h.document.querySelector('#appView').classList.contains('os-layout-app'));
});

test('bar and panels use Orion tokens, accessible motion fallback and responsive breakpoints',()=>{
  const css=readFileSync(new URL('../app/static/orion-suite.css',import.meta.url),'utf8');
  assert.match(css,/\.os-app-dock-indicator/);
  assert.match(css,/var\(--os-primary\)/);
  assert.match(css,/var\(--os-font\)/);
  assert.match(css,/@media\(max-width:767px\)/);
  assert.match(css,/@media\(max-width:359px\)/);
  assert.match(css,/@media\(prefers-reduced-motion:reduce\)/);
  assert.match(css,/env\(safe-area-inset-bottom/);
});


function shortcutRoutes(h){
  return [...h.document.querySelectorAll('#osAppDock [data-os-app-tab]')].map(el=>el.dataset.osAppTab);
}
async function chooseShortcut(h,index,route){
  const selector='select[data-os-app-shortcut="'+index+'"]';
  const field=h.document.querySelector(selector);
  assert.ok(field,'shortcut selector must exist: '+index);
  field.value=route;
  field.dispatchEvent(new h.window.Event('change',{bubbles:true}));
  await flush();
}

test('customizer starts with four Orion shortcuts and an immutable final Profile',async t=>{
  const h=await createBarber();t.after(()=>h.close());await enableApp(h);
  assert.equal(h.document.querySelectorAll('[data-os-app-shortcut]').length,4);
  assert.deepEqual(shortcutRoutes(h),['dashboard','agenda','servicos','hours','perfil']);
  assert.equal(h.document.querySelector('#osAppDock [data-os-app-tab=perfil]')?.getAttribute('aria-label'),'Perfil');
  const choices=[...h.document.querySelector('select[data-os-app-shortcut="0"]').options].map(option=>option.value);
  assert.ok(choices.includes('clientes')&&choices.includes('financeiro')&&choices.includes('inbox'));
  assert.ok(!choices.includes('perfil'));
  assert.equal(h.document.querySelector('[data-os-app-shortcut="4"]'),null);
});

test('three chosen shortcuts resize the dock and unselected pages stay accessible in Profile',async t=>{
  const h=await createBarber();t.after(()=>h.close());await enableApp(h);
  await chooseShortcut(h,0,'agenda');
  await chooseShortcut(h,1,'clientes');
  await chooseShortcut(h,2,'financeiro');
  await chooseShortcut(h,3,'');
  assert.deepEqual([...h.document.querySelectorAll('[data-os-app-shortcut]')].map(el=>el.value),
    ['agenda','clientes','financeiro','']);
  assert.equal(h.document.querySelectorAll('.os-app-shortcut-preview-item').length,4);
  h.click('[data-os-action=app-shortcuts-save]');await flush();
  assert.deepEqual(shortcutRoutes(h),['agenda','clientes','financeiro','perfil']);
  assert.equal(h.document.getElementById('osAppDock').style.getPropertyValue('--os-app-count'),'4');
  assert.equal(h.window.localStorage.getItem('orion-quick-tabs-v1:'+tenantA+':synthetic@example.invalid'),
    JSON.stringify(['agenda','clientes','financeiro']));
  h.click('#osAppDock [data-os-app-tab=clientes]');await flush();
  assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'clientes');
  assert.equal(currentTab(h),'clientes');
  h.click('#osAppDock [data-os-app-tab=financeiro]');await flush();
  assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'financeiro');
  assert.equal(currentTab(h),'financeiro');
  h.click('#osAppDock [data-os-app-tab=perfil]');await flush();
  assert.ok(h.document.querySelector('#osPageBody [data-os-route=dashboard]'));
  assert.ok(h.document.querySelector('#osPageBody [data-os-route=servicos]'));
  assert.ok(h.document.querySelector('#osPageBody [data-os-app-tab=hours]'));
  h.click('#osPageBody [data-os-app-tab=hours]');await flush();
  assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'configuracoes');
  assert.equal(currentTab(h),'perfil');
  assert.equal(h.errors.length,0);
});

test('choosing an existing shortcut swaps positions and restoring defaults is explicit',async t=>{
  const h=await createBarber();t.after(()=>h.close());await enableApp(h);
  await chooseShortcut(h,0,'agenda');
  assert.deepEqual([...h.document.querySelectorAll('[data-os-app-shortcut]')].map(el=>el.value),
    ['agenda','dashboard','servicos','hours']);
  await chooseShortcut(h,1,'financeiro');
  h.click('[data-os-action=app-shortcuts-save]');await flush();
  assert.deepEqual(shortcutRoutes(h),['agenda','financeiro','servicos','hours','perfil']);
  // Saving does not change classic mode or the theme.
  h.click('[data-os-layout=classic]');await flush();
  assert.equal(h.document.getElementById('osAppDock').hidden,true);
  h.click('[data-os-layout=app]');await flush();
  assert.deepEqual(shortcutRoutes(h),['agenda','financeiro','servicos','hours','perfil']);
  h.click('[data-os-action=app-shortcuts-default]');await flush();
  // Restore modifies the draft first; persistence occurs only after Save.
  assert.deepEqual(shortcutRoutes(h),['agenda','financeiro','servicos','hours','perfil']);
  h.click('[data-os-action=app-shortcuts-save]');await flush();
  assert.deepEqual(shortcutRoutes(h),['dashboard','agenda','servicos','hours','perfil']);
  assert.equal(new Set(shortcutRoutes(h)).size,5);
  assert.equal(h.errors.length,0);
});

test('profiles, invalid storage and unavailable routes cannot inject unauthorized shortcuts',async t=>{
  const key='orion-quick-tabs-v1:'+tenantA+':synthetic@example.invalid';
  const layout='orion-navigation-v1:'+tenantA+':synthetic@example.invalid';
  const p=barberPlan();p.permissions['finance.basic']=false;p.revision='synthetic-no-finance';
  const h=await makeSuite({initialRoute:'dashboard',permissions:p,configureWindow:win=>{
    win.localStorage.setItem(layout,'app');
    win.localStorage.setItem(key,JSON.stringify(['financeiro','clientes','agenda']));
  }});
  t.after(()=>h.close());await flush();
  assert.deepEqual(shortcutRoutes(h),['clientes','agenda','perfil']);
  assert.equal(h.document.querySelector('#osAppDock [data-os-app-tab=financeiro]'),null);
  await h.window.OrionSuite.open('configuracoes');await flush();
  h.click('[data-os-settings=appearance]');await flush();
  const options=[...h.document.querySelector('select[data-os-app-shortcut="0"]').options].map(o=>o.value);
  assert.ok(!options.includes('financeiro'));
  assert.ok(!options.includes('perfil'));
  h.window.localStorage.setItem(key,'["agenda","agenda","perfil","__proto__"]');
  // Re-evaluating from storage must ignore the bad saved value safely.
  h.window.OrionSuite.sync({user:{...h.bridge.user,email:'other@example.invalid'},
    tenants:h.bridge.tenants,tenantId:tenantA,businessContextReady:true});await flush();
  h.window.OrionSuite.sync({user:h.bridge.user,tenants:h.bridge.tenants,tenantId:tenantA,businessContextReady:true});await flush();
  assert.deepEqual(shortcutRoutes(h),['dashboard','agenda','servicos','hours','perfil']);
  assert.equal(h.errors.length,0);
});

test('personalized shortcuts are scoped to the account and do not leak after switching identities',async t=>{
  const h=await createBarber();t.after(()=>h.close());await enableApp(h);
  await chooseShortcut(h,0,'clientes');
  h.click('[data-os-action=app-shortcuts-save]');await flush();
  assert.equal(shortcutRoutes(h)[0],'clientes');
  h.window.OrionSuite.sync({user:{role:'member',email:'second@example.invalid'},
    tenants:h.bridge.tenants,tenantId:tenantA,businessContextReady:true});await flush();await flush();
  assert.equal(h.document.getElementById('osAppDock').hidden,true);
  h.window.OrionSuite.sync({user:h.bridge.user,tenants:h.bridge.tenants,tenantId:tenantA,businessContextReady:true});await flush();await flush();
  assert.equal(shortcutRoutes(h)[0],'clientes');
});

test('170 rapid changes between personalized destinations never leave stale content or duplicate controls',async t=>{
  const pending=deferred();
  const h=await createBarber({respond:path=>{
    if(path.includes('/appointments?'))return pending.promise;
    return defaultResponse(path);
  }});
  t.after(()=>h.close());await enableApp(h);
  await chooseShortcut(h,0,'agenda');
  await chooseShortcut(h,1,'clientes');
  await chooseShortcut(h,3,'dashboard');
  h.click('[data-os-action=app-shortcuts-save]');await flush();
  const tabs=['agenda','clientes','servicos','dashboard','perfil'];
  assert.deepEqual(shortcutRoutes(h),tabs);
  for(let i=0;i<170;i++)h.click('#osAppDock [data-os-app-tab='+tabs[i%tabs.length]+']');
  h.click('#osAppDock [data-os-app-tab=perfil]');await flush();
  pending.resolve({items:[],total:0,offset:0,limit:50,has_more:false});
  await flush();await flush();
  assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'perfil');
  assert.equal(currentTab(h),'perfil');
  assert.equal(h.document.querySelectorAll('#osAppDock [aria-current=page]').length,1);
  assert.equal(h.document.querySelectorAll('#osAppDock [data-os-app-tab]').length,5);
  assert.equal(h.errors.length,0);
});
