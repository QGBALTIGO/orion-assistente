import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';

// Inspect the actual public panel document: no test-only reimplementation of the UI.
const html=readFileSync('app/static/panel.html','utf8');
const bootstrapStyle=html.match(/<style>([\s\S]*?)<\/style>/)?.[1]||'';

test('Orion startup has exactly ONE progress indicator and complete branding',()=>{
  const dom=new JSDOM(html,{runScripts:'outside-only'});
  try{
    const boot=dom.window.document.getElementById('sessionBoot');
    assert.ok(boot);
    assert.equal(boot.getAttribute('aria-busy'),'true');
    const progress=boot.querySelectorAll('.session-boot-progress');
    assert.equal(progress.length,1,'only one indeterminate loading animation');
    assert.equal(boot.querySelectorAll('#sessionBootSpinner,.session-boot-symbol,.session-boot-spinner,.os-spinner').length,0);
    assert.equal(boot.querySelectorAll('.session-boot-brand').length,1);
    assert.equal(boot.querySelector('.session-boot-brand').getAttribute('src'),'/brand/orion-brand-light.svg');
    assert.equal(boot.querySelector('.session-boot-brand').getAttribute('data-orion-logo'),'');
    assert.match(boot.querySelector('.session-boot-brand').alt,/Orion/);
    assert.equal(boot.querySelector('h1').textContent,'Preparando seu painel');
    assert.ok(boot.querySelector('#sessionBootMessage[role="status"][aria-live="polite"]'));
    assert.ok(boot.querySelector('#sessionBootRetry.hidden'));
    assert.equal(progress[0].getAttribute('aria-hidden'),'true');
  }finally{dom.window.close();}
});
test('first paint and theme are designed without depending on authenticated assets',()=>{
  assert.match(bootstrapStyle,/#sessionBoot\{/);
  assert.match(bootstrapStyle,/html\[data-theme="dark"\] #sessionBoot/);
  assert.match(bootstrapStyle,/--boot-bg:#f5f8fc/);
  assert.match(bootstrapStyle,/--boot-text:#12203b/);
  assert.match(bootstrapStyle,/prefers-reduced-motion:reduce/);
  assert.match(bootstrapStyle,/#sessionBoot\.session-boot-failed \.session-boot-progress\{display:none\}/);
  assert.ok(!bootstrapStyle.includes('@keyframes session-boot-spin'));
  assert.ok(!html.includes('sessionBootSpinner'));
  assert.ok(!html.includes('session-boot-symbol'));
  assert.match(html,/sessionBootTitle"\)\.textContent="Não foi possível abrir o painel"/);
  assert.match(html,/const url=effective==="light"\?"\/brand\/orion-brand-light.svg"/);
});
test('loading screen has no fake progress percentage or decorative wait',()=>{
  assert.ok(!bootstrapStyle.includes('session-boot-spin'));
  assert.match(bootstrapStyle,/animation:session-boot-progress/);
  assert.match(html,/setTimeout\(\(\)=>attempt\.controller\.abort\(\),12000\)/);
  assert.match(html,/function dismissSessionBoot\(\)/);
  assert.match(html,/function showSessionBoot\(/);
});
