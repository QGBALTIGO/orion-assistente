import test from 'node:test';
import assert from 'node:assert/strict';
import { flush } from './harness.mjs';
import { catalogue, makePublic } from './public-harness.mjs';

test('Sobre nós renders real lazy Instagram profile preview in the existing card', async t => {
  const data=catalogue();
  data.site.instagram_url='https://instagram.com/espacovip__barbearia/?igsh=tracking-123';
  const h=await makePublic({data});t.after(()=>h.close());await flush();
  const about=h.document.getElementById('about-card');
  const section=h.document.getElementById('about-instagram');
  const frame=h.document.getElementById('about-instagram-frame');
  assert.equal(about.hidden,false);
  assert.equal(about.querySelectorAll('section.about-instagram').length,1);
  assert.equal(section.closest('article'),about);
  assert.equal(section.hidden,false);
  assert.equal(h.document.getElementById('about-text').textContent,data.site.description);
  assert.equal(h.document.getElementById('about-instagram-handle').textContent,'@espacovip__barbearia');
  assert.equal(frame.getAttribute('src'),'https://www.instagram.com/espacovip__barbearia/embed/');
  assert.equal(frame.getAttribute('loading'),'lazy');
  assert.equal(frame.getAttribute('referrerpolicy'),'no-referrer');
  assert.match(frame.title,/espacovip__barbearia/);
  assert.ok(frame.getAttribute('sandbox').includes('allow-scripts'));
  assert.ok(frame.getAttribute('sandbox').includes('allow-popups'));
  const link=h.document.getElementById('about-instagram-open');
  assert.equal(link.href,'https://www.instagram.com/espacovip__barbearia/');
  assert.equal(link.target,'_blank');
  assert.ok(link.rel.includes('noopener'));
  assert.equal(h.calls.filter(x=>x.url.includes('instagram.com')).length,0,
    'Orion backend must not scrape or request Instagram to render the site');
  assert.equal(h.errors.length,0);
});

test('Instagram already registered by Barbearia Peixoto uses the public profile directly',async t=>{
  const data=catalogue();
  data.site.instagram_url='https://www.instagram.com/barbeariapeixoto.1';
  const h=await makePublic({data});t.after(()=>h.close());await flush();
  assert.equal(h.document.getElementById('about-instagram-frame').getAttribute('src'),
    'https://www.instagram.com/barbeariapeixoto.1/embed/');
  assert.equal(h.document.getElementById('about-instagram-handle').textContent,'@barbeariapeixoto.1');
});

test('No linked Instagram means no preview or extra whitespace inside About',async t=>{
  const h=await makePublic();t.after(()=>h.close());await flush();
  assert.equal(h.document.getElementById('about-card').hidden,false);
  assert.equal(h.document.getElementById('about-instagram').hidden,true);
  assert.equal(h.document.getElementById('about-instagram-frame').hasAttribute('src'),false);
  assert.equal(h.document.getElementById('about-instagram-open').hasAttribute('href'),false);
  assert.equal(h.errors.length,0);
});

test('Instagram-only site retains the same About card without a new section',async t=>{
  const data=catalogue();data.site.description='';
  data.site.instagram_url='https://www.instagram.com/espacovip__barbearia/';
  const h=await makePublic({data});t.after(()=>h.close());await flush();
  assert.equal(h.document.querySelectorAll('article#about-card').length,1);
  assert.equal(h.document.getElementById('about-card').hidden,false);
  assert.equal(h.document.getElementById('about-text').hidden,true);
  assert.ok(h.document.getElementById('about-card').classList.contains('about-card-instagram-only'));
  assert.equal(h.document.getElementById('about-instagram').hidden,false);
});

test('Unknown or malicious Instagram URLs cannot become iframe destinations',async t=>{
  const malicious=[
    'javascript:alert(1)',
    'https://instagram.com.evil.invalid/peixoto',
    'https://evil.invalid/instagram.com/peixoto',
    'https://attacker@www.instagram.com/peixoto',
    'http://www.instagram.com/peixoto',
    'https://www.instagram.com/p/ABCD/',
    'https://www.instagram.com/reel/ABCD',
    'https://www.instagram.com/stories/peixoto',
    'https://www.instagram.com/direct',
    'https://www.instagram.com/a/b',
    'https://www.instagram.com/'+'a'.repeat(31),
    'https://www.instagram.com/peixoto%2Fextra',
  ];
  for(const link of malicious){
    const data=catalogue();data.site.instagram_url=link;
    const h=await makePublic({data});
    await flush();
    assert.equal(h.document.getElementById('about-instagram').hidden,true,link);
    assert.equal(h.document.getElementById('about-instagram-frame').hasAttribute('src'),false,link);
    assert.equal(h.document.getElementById('about-card').hidden,false);
    assert.equal(h.errors.length,0);
    h.close();
  }
});

test('About remains hidden when description and valid Instagram are both missing',async t=>{
  const data=catalogue();data.site.description='';data.site.instagram_url='https://evil.invalid';
  const h=await makePublic({data});t.after(()=>h.close());await flush();
  assert.equal(h.document.getElementById('about-card').hidden,true);
  assert.equal(h.document.getElementById('about-instagram').hidden,true);
});
