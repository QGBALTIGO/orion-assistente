import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {makeSuite,flush} from './harness.mjs';

const css=await readFile(new URL('../app/static/orion-suite.css',import.meta.url),'utf8');

test('Consultor IA mantém dimensões quadradas, ícone centrado e área de toque adequada',()=>{
  const rule=css.match(/\\.os-fab\\{([^}]*)\\}/)?.[1];
  assert.ok(rule,'estilo do botão flutuante deve existir');
  for(const value of ['box-sizing:border-box','justify-content:center','gap:0',
    'width:56px','min-width:56px','height:56px','min-height:56px','border-radius:50%']){
    assert.ok(rule.includes(value),value);
  }
  assert.match(css,/\\.os-fab>span:first-child\\{[^}]*flex:0 0 44px;[^}]*width:44px;height:44px/);
  assert.match(css,/\\.os-fab>span:first-child \\.os-icon\\{display:block;width:22px;height:22px\\}/);
  assert.match(css,/@media\\(max-width:767px\\)\\{[\\s\\S]*?\\.os-fab\\{width:52px;min-width:52px;height:52px;min-height:52px;/);
  assert.match(css,/\\.os-fab>span:first-child\\{flex-basis:40px;width:40px;height:40px\\}/);
});

test('rótulo continua acessível, sem alargar o botão por hover persistente no toque',()=>{
  assert.match(css,/\\.os-fab-label\\{[^}]*position:absolute;[^}]*clip-path:inset\\(50%\\)/);
  assert.doesNotMatch(css,/\\.os-fab:hover \\.os-fab-label/);
  assert.match(css,/@media\\(hover:hover\\) and \\(pointer:fine\\)\\{\\.os-fab:hover/);
  assert.match(css,/\\.os-fab:focus-visible\\{outline:3px solid var\\(--os-primary\\);outline-offset:3px\\}/);
  assert.match(css,/@media\\(prefers-reduced-motion:reduce\\)\\{\\.os-fab\\{transition:none\\}\\}/);
  assert.match(css,/env\\(safe-area-inset-bottom,0px\\)/);
  assert.match(css,/env\\(safe-area-inset-right,0px\\)/);
});

test('atalho flutuante segue acessível e mantém navegação para Consultor IA',async t=>{
  const h=await makeSuite({initialRoute:'dashboard'});
  t.after(()=>h.close());
  await flush();
  const fab=h.document.getElementById('osConsultantFab');
  assert.ok(fab);
  assert.equal(fab.tagName,'BUTTON');
  assert.equal(fab.type,'button');
  assert.equal(fab.getAttribute('aria-label'),'Abrir Consultor IA');
  assert.equal(fab.dataset.osRoute,'consultor');
  assert.equal(fab.classList.contains('hidden'),false);
  h.click('#osConsultantFab');
  await flush();
  assert.equal(h.document.getElementById('page-suite').dataset.osCurrentRoute,'consultor');
  await h.window.OrionSuite.open('agenda');
  await flush();
  assert.equal(fab.classList.contains('hidden'),true,'atalho não cobre a agenda');
  await h.window.OrionSuite.open('dashboard');
  await flush();
  assert.equal(fab.classList.contains('hidden'),false);
  assert.equal(h.errors.length,0);
});
