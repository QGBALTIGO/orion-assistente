import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { flush } from './harness.mjs';
import { catalogue, makePublic } from './public-harness.mjs';

test('contact icons use configured safe destinations with accessible labels, not text buttons', async t => {
  const data = catalogue();
  Object.assign(data.site, {
    contact_phone: '+551133334444',
    instagram_url: 'https://instagram.com/orion.test',
    tiktok_url: 'https://www.tiktok.com/@orion',
    facebook_url: 'https://facebook.com/orion',
    external_url: 'https://example.com'
  });
  const h = await makePublic({ data }); t.after(() => h.close()); await flush();
  const items = [...h.document.querySelectorAll('#contact-links a')];
  assert.equal(items.length, 6);
  assert.deepEqual(items.map(item => item.classList[1]), [
    'social-link--whatsapp','social-link--phone','social-link--instagram',
    'social-link--tiktok','social-link--facebook','social-link--website'
  ]);
  for (const item of items) {
    assert.equal(item.textContent.trim(), '');
    assert.ok(item.getAttribute('aria-label'));
    assert.equal(item.querySelectorAll('svg[aria-hidden=true]').length, 1);
  }
  assert.equal(items[0].href, 'https://wa.me/5511999999999');
  assert.equal(items[1].getAttribute('href'), 'tel:+551133334444');
  assert.equal(items[1].getAttribute('target'), null);
  assert.ok(items.slice(2).every(item => item.target === '_blank' && item.rel.includes('noopener')));
  assert.equal(h.errors.length, 0);
});

test('contact icon links cannot inject javascript: URLs or markup', async t => {
  const data = catalogue();
  data.site.instagram_url='javascript:alert(1)';
  data.site.facebook_url='data:text/html,evil';
  data.site.tiktok_url='https://x.invalid/?q=<img src=x onerror=alert(1)>';
  const h=await makePublic({data});t.after(()=>h.close());await flush();
  assert.equal(h.document.querySelectorAll('#contact-links a').length,2);
  assert.equal(h.document.querySelector('a[href^="javascript:"]'),null);
  assert.equal(h.document.querySelector('#contact-links img'),null);
});

test('light/dark/system uses the Orion setting, menu a11y and OS changes', async t => {
  const h=await makePublic();t.after(()=>h.close());await flush();
  let isDark=false, notify=null;
  h.window.matchMedia=()=>({
    get matches(){return isDark;},
    addEventListener:(name,callback)=>{if(name==='change')notify=callback;}
  });
  const theme=await readFile(new URL('../app/static/orion-public-theme.js',import.meta.url),'utf8');
  h.window.eval(theme);await flush();
  const doc=h.document,root=doc.documentElement,menu=doc.getElementById('public-theme-menu');
  assert.equal(root.dataset.theme,'light');
  h.click('#public-theme-toggle');
  assert.equal(menu.hidden,false);
  assert.equal(doc.getElementById('public-theme-toggle').getAttribute('aria-expanded'),'true');
  h.click('[data-public-theme=dark]');
  assert.equal(root.dataset.theme,'dark');
  assert.equal(root.dataset.themePreference,'dark');
  assert.equal(h.window.localStorage.getItem('orion-theme'),'dark');
  assert.equal(menu.hidden,true);
  assert.equal(doc.querySelector('[data-public-theme=dark]').getAttribute('aria-pressed'),'true');
  assert.equal(doc.querySelector('meta[name=theme-color]').content,'#080d18');
  isDark=false;notify();assert.equal(root.dataset.theme,'dark');
  h.click('#public-theme-toggle');h.click('[data-public-theme=auto]');
  assert.equal(root.dataset.theme,'light');
  isDark=true;notify();assert.equal(root.dataset.theme,'dark');
  h.click('#public-theme-toggle');
  doc.dispatchEvent(new h.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
  assert.equal(menu.hidden,true);
  assert.equal(doc.getElementById('public-theme-toggle').getAttribute('aria-expanded'),'false');
  assert.equal(h.errors.length,0);
});
