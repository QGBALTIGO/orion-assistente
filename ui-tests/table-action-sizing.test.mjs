import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

test('table text actions keep intrinsic width without changing form-button wrapping', async () => {
  const css = await readFile(new URL('../app/static/orion-suite.css', import.meta.url), 'utf8');
  const scoped = css.match(/\.os-table \.os-button\{([^}]+)\}/)?.[1];
  assert.ok(scoped, 'Table actions require their own intrinsic sizing rule');
  const declarations = Object.fromEntries(scoped.split(';').filter(Boolean).map(item => item.split(':').map(value => value.trim())));
  assert.equal(declarations.width, 'max-content');
  assert.equal(declarations['min-width'], 'max-content');
  assert.equal(declarations['max-width'], 'none');
  assert.equal(declarations.flex, '0 0 auto');
  assert.equal(declarations['white-space'], 'nowrap');
  assert.equal(declarations['overflow-wrap'], 'normal');
  assert.equal(declarations['word-break'], 'normal');
  assert.match(css, /\.os-table-scroll\{[^}]*overflow:auto/);
  assert.match(css, /@media\(pointer:coarse\)\{\.os-table \.os-button\{min-height:44px\}\}/);
  assert.match(css, /(?:^|\n)\.os-button\{[^}]*white-space:normal/);
});
