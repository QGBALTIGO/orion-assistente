import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {makePanel} from './panel-harness.mjs';
import {entry,fullState,tenantA,flush} from './onboarding-harness.mjs';

const decision=entry({create_allowed:false,tenant_id:tenantA});
const setup=(data=fullState(),decisionValue=decision)=>makePanel({
  initialRoute:'dashboard',
  respond:(path,method)=>{
    if(method==='GET'&&path==='/api/v1/onboarding/entry')return decisionValue;
    if(method==='GET'&&path==='/api/v1/tenants/'+tenantA+'/onboarding')return data;
  }
});

test('setup has a separate brand/theme header through each wizard step without writes',async t=>{
  const h=await setup();t.after(()=>h.close());await flush();
  const app=h.element('#appView'),host=h.element('#orionSetup');
  assert.equal(app.classList.contains('ob-gated'),true);
  assert.equal(host.hidden,false);
  assert.ok(host.querySelector('[data-ob-form=business]'));
  assert.equal(h.document.querySelectorAll('#setupHeader [data-orion-logo]').length,1);
  assert.equal(h.element('#setupThemeToggle').getAttribute('aria-expanded'),'false');
  assert.equal(h.element('#setupThemeMenu').classList.contains('hidden'),true);
  h.click('#setupThemeToggle');
  assert.equal(h.element('#setupThemeToggle').getAttribute('aria-expanded'),'true');
  h.click('#setupThemeMenu [data-theme-choice=dark]');
  assert.equal(h.document.documentElement.dataset.theme,'dark');
  assert.equal(h.window.localStorage.getItem('orion-theme'),'dark');
  assert.equal(h.element('#setupThemeMenu').classList.contains('hidden'),true);
  assert.equal(h.element('#setupHeader [data-orion-logo]').getAttribute('src'),'/brand/orion-brand-dark.svg');
  h.click('#setupThemeToggle');h.click('#setupThemeMenu [data-theme-choice=light]');
  assert.equal(h.document.documentElement.dataset.theme,'light');
  assert.equal(h.element('#setupHeader [data-orion-logo]').getAttribute('src'),'/brand/orion-brand-light.svg');
  h.click('#setupThemeToggle');h.click('#setupThemeMenu [data-theme-choice=auto]');
  assert.equal(h.document.documentElement.dataset.themePreference,'auto');
  assert.equal(h.element('#setupThemeMenu').classList.contains('hidden'),true);
  for(const step of ['location','professionals','catalog','hours','channels','review']){
    h.click('[data-ob-step='+step+']');
    assert.equal(app.classList.contains('ob-gated'),true,step);
    assert.equal(host.hidden,false,step);
    assert.equal(h.element('#setupThemeToggle').getAttribute('aria-expanded'),'false',step);
  }
  assert.equal(h.calls.filter(call=>call.method!=='GET').length,0);
  assert.deepEqual(h.errors,[]);
});

test('completed business restores the normal workspace and hides setup chrome',async t=>{
  const done=fullState({status:'complete',current_step:'review'});
  const h=await setup(done);t.after(()=>h.close());await flush();
  assert.equal(h.element('#appView').classList.contains('ob-gated'),true);
  h.click('[data-ob-action=open-dashboard]');await flush();
  assert.equal(h.element('#appView').classList.contains('ob-gated'),false);
  assert.equal(h.element('#orionSetup').hidden,true);
  assert.equal(h.element('#setupThemeMenu').classList.contains('hidden'),true);
  assert.ok(h.element('#osNav'));
  assert.equal(h.calls.filter(call=>call.method!=='GET').length,0);
  assert.deepEqual(h.errors,[]);
});

test('dashboard session and new-business entry use the correct gate, with no drawer leak',async t=>{
  const normal=await makePanel({initialRoute:'dashboard'});t.after(()=>normal.close());await flush();
  assert.equal(normal.element('#appView').classList.contains('ob-gated'),false);
  assert.ok(normal.element('#osHeader'));
  const creating=await makePanel({initialRoute:'dashboard',respond:(path,method)=>{
    if(method==='GET'&&path==='/api/v1/account/businesses')return [];
    if(method==='GET'&&path==='/api/v1/onboarding/entry')return entry({tenant_id:null,businesses:[],create_allowed:true});
  }});t.after(()=>creating.close());await flush();
  assert.equal(creating.element('#appView').classList.contains('ob-gated'),true);
  assert.ok(creating.element('#orionSetup').querySelector('[data-ob-form=create]'));
  creating.element('#appView').classList.add('os-menu-open');
  creating.click('#setupThemeToggle');
  assert.equal(creating.element('#setupThemeMenu').classList.contains('hidden'),false);
  // The existing logout path tears down the gate without exposing stale content.
  creating.click('#logout');await flush();
  assert.equal(creating.element('#appView').classList.contains('ob-gated'),false);
  assert.equal(creating.element('#setupThemeMenu').classList.contains('hidden'),true);
  assert.equal(creating.element('#appView').classList.contains('hidden'),true);
  assert.deepEqual(creating.errors,[]);
});

test('setup CSS excludes the rail and normal header but never unmasks the logged-out app',async()=>{
  const css=await readFile(new URL('../app/static/orion-onboarding.css',import.meta.url),'utf8');
  assert.match(css,/#appView\.ob-gated:not\(\.hidden\)\{display:block!important/);
  assert.match(css,/#appView\.ob-gated>\.sidebar/);
  assert.match(css,/#appView\.ob-gated>\.content>#osHeader/);
  assert.match(css,/#appView\.ob-gated>\.content>#setupHeader/);
  assert.match(css,/#appView\.ob-gated \.ob-heading \.ob-logo/);
});
