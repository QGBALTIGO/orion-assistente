import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { makePublic, catalogue } from './public-harness.mjs';
import { flush } from './harness.mjs';

const fixture = async (t, options = {}) => {
  const h = await makePublic(options);
  t.after(() => h.close());
  await flush();
  return h;
};

test('ações do site usam SVGs sem setas ambíguas, com nomes claros e traçado consistente', async t => {
  const h = await fixture(t);
  const doc = h.document, map = doc.getElementById('hero-map'), share = doc.getElementById('share-site');
  const book = doc.getElementById('hero-book'), footerMap = doc.getElementById('maps-link');
  assert.equal(map.hidden, false);
  assert.equal(map.getAttribute('aria-label'), 'Ver localização no mapa');
  assert.equal(map.getAttribute('title'), 'Abrir localização no mapa');
  assert.equal(map.getAttribute('target'), '_blank');
  assert.equal(map.getAttribute('rel'), 'noopener noreferrer');
  const route = new URL(map.href);
  assert.equal(route.origin, 'https://www.google.com');
  assert.equal(route.pathname, '/maps/dir/');
  assert.equal(route.searchParams.get('api'), '1');
  assert.equal(route.searchParams.get('dir_action'), 'navigate');
  assert.equal(map.querySelector('.hero-action-label')?.textContent, 'Mapa');
  assert.equal(share.getAttribute('aria-label'), 'Compartilhar site');
  assert.equal(share.querySelector('.hero-action-label')?.textContent, 'Compartilhar');
  for (const action of [map, share]) {
    const svg = action.querySelector('svg.hero-action-icon');
    assert.ok(svg, 'cada botão utiliza um SVG próprio');
    assert.equal(svg.getAttribute('viewBox'), '0 0 24 24');
    assert.equal(svg.getAttribute('fill'), 'none');
    assert.equal(svg.getAttribute('stroke'), 'currentColor');
    assert.equal(svg.getAttribute('stroke-width'), '1.9');
    assert.equal(svg.getAttribute('aria-hidden'), 'true');
    assert.equal(svg.getAttribute('focusable'), 'false');
    assert.doesNotMatch(action.textContent, /[↗↥]/, 'remover glifos improvisados');
  }
  assert.equal(map.querySelectorAll('circle').length, 1, 'mapa usa pin reconhecível');
  assert.equal(share.querySelectorAll('circle').length, 3, 'compartilhar usa três nós');
  assert.equal(book.querySelector('svg.hero-book-icon')?.getAttribute('viewBox'), '0 0 24 24');
  assert.equal(footerMap.querySelector('svg.route-action-icon')?.getAttribute('viewBox'), '0 0 24 24');
  assert.equal(doc.querySelector('.location-heading svg.location-icon')?.getAttribute('aria-hidden'), 'true');
  assert.ok(!doc.querySelector('#hero-map [onload],#share-site [onclick]'));
  assert.deepEqual(h.errors, []);
});

test('mapa permanece um link externo seguro e desaparece quando não existe URL HTTPS válida', async t => {
  const data = catalogue();
  data.site.maps_url = 'javascript:alert(1)';
  // An invalid external URL can never reach an iframe. The known, safe
  // business address still enables independently generated Google directions.
  const h = await fixture(t, { data });
  assert.equal(h.document.getElementById('hero-map').hidden, false);
  assert.equal(h.document.getElementById('maps-link').hidden, false);
  assert.equal(new URL(h.document.getElementById('maps-link').href).origin,'https://www.google.com');
  assert.equal(new URL(h.document.getElementById('location-map').src).origin,'https://maps.google.com');
  assert.equal(h.document.getElementById('share-site').hidden, false);
  assert.equal(h.document.querySelector('a[href^="javascript:"]'), null);
  assert.equal(h.errors.length, 0);
});

test('compartilhamento público mantém a API nativa quando presente e não envia mensagens', async t => {
  const h = await fixture(t);
  let payload;
  Object.defineProperty(h.window.navigator, 'share', { configurable: true, value: async value => { payload = value; } });
  h.click('#share-site');
  await flush();
  assert.deepEqual(JSON.parse(JSON.stringify(payload)), {
    title: 'Empresa sintética', url: 'https://orion.test/s/empresa-sintetica'
  });
  assert.equal(h.calls.length, 1, 'clicar em compartilhar não chama API de agendamento');
  assert.equal(h.errors.length, 0);
});

test('compartilhamento sem Web Share API copia apenas o link publicado', async t => {
  const h = await fixture(t);
  let copied;
  Object.defineProperty(h.window.navigator, 'clipboard', {
    configurable: true, value: { writeText: async text => { copied = text; } }
  });
  h.click('#share-site');
  await flush();
  assert.equal(copied, 'https://orion.test/s/empresa-sintetica');
  assert.equal(h.document.getElementById('share-message').hidden, false);
  assert.match(h.document.getElementById('share-message').textContent, /Link copiado/);
});

test('prévia não divulga URL de edição e mantém aviso de publicação', async t => {
  const h = await fixture(t, { path: '/app/site-preview/10000000-0000-4000-8000-000000000001' });
  let attempts = 0;
  Object.defineProperty(h.window.navigator, 'share', {
    configurable: true, value: async () => { attempts++; }
  });
  h.click('#share-site');
  await flush();
  assert.equal(attempts, 0);
  assert.match(h.document.getElementById('share-message').textContent, /Publique o site/);
  assert.equal(h.document.getElementById('preview-banner').hidden, false);
});

test('estilos padronizados em botões discretos, com toque mínimo e responsividade mobile/desktop', async () => {
  const css = await readFile(new URL('../app/static/orion-public.css', import.meta.url), 'utf8');
  assert.match(css, /\.hero-actions \.hero-icon-action\{[^}]*width:46px[^}]*height:46px[^}]*border-radius:14px/);
  assert.match(css, /\.hero-actions \.hero-action-icon\{[^}]*width:20px;height:20px/);
  assert.match(css, /@media\(min-width:640px\)\{[^}]*\.hero-actions \.pill\{flex:none/);
  assert.match(css, /@media\(max-width:359px\)/);
  assert.match(css, /\.hero-actions \.hero-icon-action:focus-visible\{outline:3px/);
  assert.match(css, /@media\(prefers-reduced-motion:reduce\)/);
});
