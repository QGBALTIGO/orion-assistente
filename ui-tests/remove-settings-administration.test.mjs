import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {makePanel} from './panel-harness.mjs';
import {flush} from './harness.mjs';

for(const role of ['admin','member','support']){
  test('Configurações does not show Administração for '+role,async t=>{
    const h=await makePanel({role,initialRoute:'configuracoes'});
    t.after(()=>h.close());
    await flush();
    if(role==='admin'){
      h.click('#osNav [data-os-route=configuracoes]');
      await flush();
    }
    const source=await readFile(new URL('../app/static/orion-suite.js',import.meta.url),'utf8');
    assert.doesNotMatch(source,/\['administration','shield','Administração'/);
    assert.doesNotMatch(source,/if\(tab==='administration'\)panel=/);
    assert.doesNotMatch(source,/routeButton\('Administração do atendimento','cadastros'/);
    assert.ok(!h.document.querySelector('[data-os-settings="administration"]'));
    assert.ok(!h.document.querySelector('#osSettingsSection option[value="administration"]'));
  });
}

test('legacy Administração deep links redirect to Configurações',async t=>{
  const h=await makePanel({role:'admin',initialRoute:'dashboard'});
  t.after(()=>h.close());await flush();
  await h.window.OrionSuite.open('cadastros',{force:true});
  await flush();
  assert.equal(h.element('#page-cadastros').classList.contains('hidden'),true);
  assert.equal(h.window.OrionSuite.currentRoute?.()||'configuracoes','configuracoes');
});
