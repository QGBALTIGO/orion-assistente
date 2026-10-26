import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {makePublic,catalogue} from './public-harness.mjs';
import {flush} from './harness.mjs';

async function fixture(t,{data=catalogue(),initialTheme='auto',prefersDark=false}={}){
  const h=await makePublic({data,initialTheme,prefersDark});
  t.after(()=>h.close());await flush();
  return h;
}
const contacts=h=>[...h.document.querySelectorAll('#contact-links a.contact-channel')];

test('contato mostra somente ícones SVG reconhecíveis, sem botões escritos',async t=>{
  const data=catalogue();
  Object.assign(data.site,{
    contact_phone:'+5511999887766',
    instagram_url:'https://www.instagram.com/barbearia_exemplo/',
    tiktok_url:'https://www.tiktok.com/@barbearia_exemplo',
    facebook_url:'https://www.facebook.com/barbearia.exemplo',
    external_url:'https://barbearia.example/'
  });
  const h=await fixture(t,{data});
  const items=contacts(h);
  assert.equal(h.document.getElementById('contact-section').hidden,false);
  assert.equal(items.length,6);
  assert.deepEqual(items.map(x=>x.dataset.channel),
    ['whatsapp','phone','instagram','tiktok','facebook','website']);
  assert.equal(h.document.getElementById('contact-links').textContent.trim(),'',
    'o canal é representado pelo ícone, não pelo nome');
  for(const a of items){
    assert.ok(a.querySelector('svg.contact-glyph'),a.dataset.channel);
    assert.equal(a.querySelector('svg').getAttribute('viewBox'),'0 0 24 24');
    assert.equal(a.querySelector('svg').getAttribute('aria-hidden'),'true');
    assert.ok(a.getAttribute('aria-label').length>8);
    assert.ok(a.getAttribute('title'));
  }
  assert.equal(items[0].href,'https://wa.me/5511999999999');
  assert.equal(items[1].href,'tel:+5511999887766');
  assert.equal(items[1].hasAttribute('target'),false);
  assert.equal(items[2].href,'https://www.instagram.com/barbearia_exemplo/');
  assert.equal(items[3].href,'https://www.tiktok.com/@barbearia_exemplo');
  assert.equal(items[4].href,'https://www.facebook.com/barbearia.exemplo');
  assert.equal(items[5].href,'https://barbearia.example/');
  for(const a of items.filter(a=>a.dataset.channel!=='phone')){
    assert.equal(a.target,'_blank');
    assert.match(a.rel,/noopener noreferrer/);
  }
  assert.equal(h.errors.length,0);
});

test('redes sociais ausentes ou maliciosas não geram controles falsos',async t=>{
  const data=catalogue();
  data.site.whatsapp_phone='not-a-valid-phone';
  data.site.contact_phone='1234';
  data.site.instagram_url='javascript:alert(document.cookie)';
  data.site.facebook_url='https://user:pass@facebook.com/path';
  data.site.tiktok_url='data:text/html,hello';
  data.site.external_url='http://insecure.example';
  const h=await fixture(t,{data});
  assert.equal(h.document.getElementById('contact-section').hidden,true);
  assert.equal(contacts(h).length,0);
  assert.equal(h.document.querySelector('[href^="javascript:"]'),null);
  assert.equal(h.errors.length,0);
});

test('modo Sistema acompanha preferência do dispositivo e permite mudar sem recarregar',async t=>{
  const h=await fixture(t,{prefersDark:true,initialTheme:'auto'});
  const doc=h.document,root=doc.documentElement,menu=doc.getElementById('appearance-menu');
  assert.equal(root.dataset.publicTheme,'dark');
  assert.equal(root.dataset.publicThemePreference,'system');
  assert.equal(doc.querySelectorAll('[data-public-theme-choice="system"][aria-pressed="true"]').length,2);
  h.click('#appearance-trigger');
  assert.equal(menu.hidden,false);
  assert.equal(doc.getElementById('appearance-trigger').getAttribute('aria-expanded'),'true');
  h.click('#appearance-menu [data-public-theme-choice="light"]');
  assert.equal(menu.hidden,true);
  assert.equal(root.dataset.publicTheme,'light');
  assert.equal(h.window.localStorage.getItem('orion-theme'),'light');
  assert.equal(doc.querySelectorAll('[data-public-theme-choice="light"][aria-pressed="true"]').length,2);
  h.setSystemDark(false);
  h.setSystemDark(true);
  assert.equal(root.dataset.publicTheme,'light','manual theme should not follow system');
  h.click('.appearance-segmented [data-public-theme-choice="system"]');
  assert.equal(root.dataset.publicTheme,'dark');
  assert.equal(h.window.localStorage.getItem('orion-theme'),'auto');
  h.setSystemDark(false);
  assert.equal(root.dataset.publicTheme,'light');
  assert.equal(h.errors.length,0);
});

test('modo escuro sincroniza menu e rodapé, fecha com Escape e ao tocar fora',async t=>{
  const h=await fixture(t);
  h.click('.appearance-segmented [data-public-theme-choice="dark"]');
  assert.equal(h.document.documentElement.dataset.publicTheme,'dark');
  assert.equal(h.window.localStorage.getItem('orion-theme'),'dark');
  assert.equal(h.document.querySelectorAll('[data-public-theme-choice="dark"][aria-pressed="true"]').length,2);
  h.click('#appearance-trigger');
  assert.equal(h.document.getElementById('appearance-menu').hidden,false);
  h.document.dispatchEvent(new h.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
  assert.equal(h.document.getElementById('appearance-menu').hidden,true);
  assert.equal(h.document.getElementById('appearance-trigger').getAttribute('aria-expanded'),'false');
  h.click('#appearance-trigger');
  h.document.body.dispatchEvent(new h.window.Event('pointerdown',{bubbles:true}));
  assert.equal(h.document.getElementById('appearance-menu').hidden,true);
  assert.deepEqual(h.errors,[]);
});

test('menu não altera agendamento, e o tema continua quando o catálogo é carregado',async t=>{
  const h=await fixture(t,{initialTheme:'dark'});
  assert.equal(h.document.documentElement.dataset.publicTheme,'dark');
  const calls=h.calls.length;
  h.click('#appearance-trigger');
  h.click('#appearance-menu [data-public-theme-choice="light"]');
  assert.equal(h.calls.length,calls,'appearance requires no backend writes');
  h.click('#services-grid .service-card');
  assert.equal(h.document.getElementById('booking-dialog').open,true);
  assert.equal(h.document.documentElement.dataset.publicTheme,'light');
  assert.equal(h.errors.length,0);
});

test('CSS garante contraste em tema escuro, canais compactos e responsividade',async()=>{
  const css=await readFile(new URL('../app/static/orion-public.css',import.meta.url),'utf8');
  for(const fragment of [
    ':root[data-public-theme="dark"]{',
    '--bg:#0d1422;--card:#182235;--text:#f5f7fc;--muted:#b6c3d6;',
    'color-scheme:dark',
    '@media(prefers-color-scheme:dark)',
    '.contact-channel{',
    'width:50px;height:50px;min-width:50px;min-height:50px;',
    '.contact-glyph{display:block;width:24px;height:24px;',
    '.appearance-menu{',
    '.appearance-segmented{',
    '.appearance-segmented button[aria-pressed=true]{',
    '@media(max-width:359px)'
  ])assert.ok(css.includes(fragment),'ausente '+fragment);
  const html=await readFile(new URL('../app/static/orion-public.html',import.meta.url),'utf8');
  assert.doesNotMatch(html,/<span class="map-provider"/);
  assert.ok(html.includes('id="appearance-trigger"'));
  assert.ok(html.includes('id="contact-links"'));
});
