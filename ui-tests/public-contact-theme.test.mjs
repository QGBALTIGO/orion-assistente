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

test('theme icon defaults to system (not panel preference), toggles opposite and back to auto', async t => {
  const h=await makePublic(); t.after(()=>h.close()); await flush();
  let dark=false, onChange=null;
  h.window.matchMedia=()=>({
    get matches(){return dark;},
    addEventListener:(name,listener)=>{if(name==='change')onChange=listener;}
  });
  h.window.localStorage.setItem('orion-theme','dark'); // Admin choice must not affect the public site.
  const script=await readFile(new URL('../app/static/orion-public-theme.js',import.meta.url),'utf8');
  h.window.eval(script); await flush();
  const root=h.document.documentElement;
  const toggle=h.document.getElementById('public-theme-toggle');
  assert.equal(toggle.textContent.trim(),'');
  assert.equal(toggle.querySelectorAll('svg').length,2);
  assert.equal(h.document.getElementById('public-theme-menu'),null);
  assert.equal(root.dataset.themePreference,'auto');
  assert.equal(root.dataset.theme,'light');
  assert.equal(h.window.localStorage.getItem('orion-public-theme'),null);
  assert.match(toggle.getAttribute('aria-label'),/automático.*modo escuro/);
  h.click('#public-theme-toggle');
  assert.equal(root.dataset.theme,'dark');
  assert.equal(root.dataset.themePreference,'dark');
  assert.equal(h.window.localStorage.getItem('orion-public-theme'),'dark');
  assert.match(toggle.getAttribute('aria-label'),/manual.*modo claro/);
  assert.equal(h.document.querySelector('meta[name=theme-color]').content,'#080d18');
  h.click('#public-theme-toggle');
  assert.equal(root.dataset.theme,'light');
  assert.equal(root.dataset.themePreference,'auto');
  assert.equal(h.window.localStorage.getItem('orion-public-theme'),'auto');
  dark=true;onChange();
  assert.equal(root.dataset.theme,'dark');
  assert.equal(root.dataset.themePreference,'auto');
  h.click('#public-theme-toggle');
  assert.equal(root.dataset.theme,'light');
  assert.equal(root.dataset.themePreference,'light');
  dark=false;onChange();
  assert.equal(root.dataset.theme,'light'); // Manual theme stays applied.
  h.click('#public-theme-toggle'); // Dark is now opposite to OS.
  assert.equal(root.dataset.theme,'dark');
  assert.equal(root.dataset.themePreference,'dark');
  h.click('#public-theme-toggle'); // Light matches OS again; re-enable auto.
  assert.equal(root.dataset.theme,'light');
  assert.equal(root.dataset.themePreference,'auto');
  assert.equal(h.window.localStorage.getItem('orion-theme'),'dark');
  assert.equal(h.errors.length,0);
});

test('dark-mode phone starts automatically dark and returns to system with two taps', async t => {
  const h=await makePublic(); t.after(()=>h.close()); await flush();
  h.window.matchMedia=()=>({matches:true,addEventListener:()=>{}});
  const script=await readFile(new URL('../app/static/orion-public-theme.js',import.meta.url),'utf8');
  h.window.eval(script); await flush();
  const root=h.document.documentElement;
  assert.equal(root.dataset.theme,'dark');
  assert.equal(root.dataset.themePreference,'auto');
  h.click('#public-theme-toggle');
  assert.equal(root.dataset.theme,'light');
  assert.equal(root.dataset.themePreference,'light');
  h.click('#public-theme-toggle');
  assert.equal(root.dataset.theme,'dark');
  assert.equal(root.dataset.themePreference,'auto');
  assert.equal(h.errors.length,0);
});
