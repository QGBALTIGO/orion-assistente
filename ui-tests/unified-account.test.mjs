import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {makeSuite,flush,planState,tenantA} from './harness.mjs';

const panel=await readFile(new URL('../app/static/panel.html',import.meta.url),'utf8');
const start=panel.indexOf('<section id="page-member"');
const end=panel.indexOf('<section id="page-dispositivo"',start);
assert.ok(start>=0&&end>start);
const accountMarkup=panel.slice(start,end);
const sections=['profile','business','security','appearance'];
async function makeAccount(niche,profile,role='owner',userRole='member',initialRoute='member'){
  const perms=planState('elite',{business_niche:niche,workspace_profile:profile,layout_modes:niche==='odontologia'?['classic']:['classic','app'],dental_clinical_enabled:niche==='odontologia'});
  const tenants=[{id:tenantA,name:'Empresa sintética',slug:'empresa',timezone:'America/Campo_Grande',workspace_profile:profile,status:'active',role}];
  let window,originalBusiness,originalPassword;
  const h=await makeSuite({initialRoute,role,permissions:perms,
    bridge:{user:{role:userRole,name:'Conta sintética',email:'synthetic@example.invalid'},tenants,tenantId:tenantA,openLegacy(route){
      for(const node of window.document.querySelectorAll('.content>.page'))node.classList.toggle('hidden',node.id!=='page-'+route);
      if(route==='member'&&userRole==='member'){
        const page=window.document.querySelector('#page-member');
        page.classList.remove('os-admin-center-open');
        page.querySelector('.member-settings').classList.remove('hidden');
        page.querySelector('.member-overview').classList.remove('hidden');
      }
      window.OrionSuite.legacyOpened(route);
    }},configureWindow(win){
      window=win;win.document.querySelector('.content').insertAdjacentHTML('beforeend',accountMarkup);
      originalBusiness=win.document.getElementById('memberBusinessForm');
      originalPassword=win.document.getElementById('memberPasswordForm');
      originalBusiness.querySelector('input').value='Nome de teste';
    }});
  await flush();return {...h,originalBusiness,originalPassword};
}
for(const [niche,profile] of [['barbearia','standard'],['odontologia','standard'],['servicos_assinatura','subscription_services'],['afiliados','affiliate']]){
  test(niche+' — conta unificada, tema, navegação, formulários e acesso original',async t=>{
    const h=await makeAccount(niche,profile);t.after(()=>h.close());
    const d=h.document,query=s=>d.querySelector(s),click=async s=>{h.click(s);await flush();};
    assert.ok(query('#osMemberAccount'));
    assert.equal(query('#memberWelcome').textContent,'Minha conta');
    assert.equal(query('#osNav [data-os-app-settings=appearance]'),null);
    assert.equal(query('#osNav [data-os-route=member]').getAttribute('aria-current'),'page');
    assert.deepEqual([...d.querySelectorAll('#osMemberAccount [data-os-account-tab]')].map(el=>el.dataset.osAccountTab),sections);
    assert.equal(query('.member-overview').hidden,false);
    assert.equal(query('.member-settings').hidden,true);
    assert.ok(query('#appView').classList.contains('os-personal-account'));
    assert.equal(query('#memberBusinessForm'),h.originalBusiness);
    assert.equal(query('#memberPasswordForm'),h.originalPassword);
    await click('[data-os-account-tab=business]');
    assert.equal(query('.member-settings').hidden,false);
    assert.equal(query('#memberBusinessForm').closest('.member-settings-card').hidden,false);
    assert.equal(query('#memberPasswordForm').closest('.member-settings-card').hidden,true);
    assert.equal(query('#memberBusinessName').value,'Nome de teste');
    await click('[data-os-account-tab=security]');
    assert.equal(query('#memberPasswordForm').closest('.member-settings-card').hidden,false);
    assert.equal(query('#memberCurrentPassword').type,'password');
    await click('[data-os-account-tab=appearance]');
    assert.equal(query('.member-settings').hidden,true);
    assert.equal(query('#osMemberAppearance').hidden,false);
    assert.deepEqual([...d.querySelectorAll('#osMemberAppearance [data-os-theme]')].map(el=>el.dataset.osTheme),['light','dark','auto']);
    for(const theme of ['dark','light','auto']){
      await click('#osMemberAppearance [data-os-theme='+theme+']');
      assert.equal(d.documentElement.dataset.themePreference,theme);
      assert.equal(query('#osMemberAppearance [data-os-theme='+theme+']').getAttribute('aria-pressed'),'true');
    }
    if(niche==='odontologia'){
      assert.equal(query('#osMemberAppearance [data-os-layout]'),null);
    }else{
      await click('#osMemberAppearance [data-os-layout=app]');
      assert.equal(query('#osAppDock').hidden,false);
      assert.equal(query('#osMemberAppearance [data-os-layout=app]').getAttribute('aria-pressed'),'true');
      const options=[...query('#osAppShortcut0').options].map(el=>el.value);
      const choice=options.find(v=>v&&v!==query('#osAppShortcut0').value);
      query('#osAppShortcut0').value=choice;
      query('#osAppShortcut0').dispatchEvent(new h.window.Event('change',{bubbles:true}));
      await flush();
      await click('#osMemberAppearance [data-os-action=app-shortcuts-save]');
      assert.equal(query('#osAppDock [data-os-app-tab]').dataset.osAppTab,choice);
      await click('#osMemberAppearance [data-os-action=app-shortcuts-default]');
      await click('#osMemberAppearance [data-os-action=app-shortcuts-save]');
      await click('#osMemberAppearance [data-os-layout=classic]');
      assert.equal(query('#osAppDock').hidden,true);
    }
    await click('[data-os-account-tab=business]');
    assert.equal(query('#memberBusinessName').value,'Nome de teste');
    if(profile==='standard'){
      await h.window.OrionSuite.open('configuracoes');await flush();
      assert.equal(query('#osSettingsSection [value=appearance]'),null);
      assert.equal(query('#osSettingsSection [value=account]'),null);
      assert.ok(query('#osSettingsSection [value=general]'));
    }else{
      await h.window.OrionSuite.open('configuracoes');await flush();
      assert.equal(query('#osMemberAppearance').hidden,false);
      assert.equal(query('#osSettingsSection'),null);
    }
    assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
    assert.deepEqual(h.errors,[]);
  });
}
test('platform admin customer center is not replaced by personal account',async t=>{
  const h=await makeAccount('barbearia','standard','owner','admin');t.after(()=>h.close());
  assert.equal(h.document.querySelector('#osMemberAccount'),null);
  assert.equal(h.document.querySelector('#appView').classList.contains('os-personal-account'),false);
});
