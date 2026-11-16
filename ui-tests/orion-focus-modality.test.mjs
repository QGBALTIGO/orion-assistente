import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';

const script=readFileSync(new URL('../app/static/orion-focus.js',import.meta.url),'utf8');
const css=readFileSync(new URL('../app/static/orion-focus.css',import.meta.url),'utf8');

function context(){
  const dom=new JSDOM('<!doctype html><html><body><button id="close">Fechar</button><h2 id="heading" tabindex="-1">Detalhes do agendamento</h2><input id="input"></body></html>',{
    url:'https://orionagenda.pro/painel',runScripts:'outside-only'
  });
  const {window}=dom;
  window.eval(script);
  return {dom,window,root:window.document.documentElement};
}

test('touch, pointer and mouse remove only the unwanted pointer focus modality',()=>{
  const t=context();
  assert.equal(t.root.hasAttribute('data-orion-input'),false,'unknown modality keeps native accessible focus');
  for(const type of ['pointerdown','touchstart','mousedown']){
    t.root.dispatchEvent(new t.window.Event(type,{bubbles:true}));
    assert.equal(t.root.getAttribute('data-orion-input'),'pointer',type);
  }
  t.window.close();
});

test('keyboard Tab, arrows and Enter restore focus indicators after tap',()=>{
  const t=context();
  t.root.dispatchEvent(new t.window.Event('pointerdown',{bubbles:true}));
  for(const key of ['Tab','ArrowDown','Enter',' ']){
    t.window.document.body.dispatchEvent(new t.window.KeyboardEvent('keydown',{key,bubbles:true}));
    assert.equal(t.root.dataset.orionInput,'keyboard',key);
    t.root.dispatchEvent(new t.window.Event('touchstart',{bubbles:true}));
  }
  t.window.close();
});

test('modifier-only and composition events do not change pointer modality',()=>{
  const t=context();
  t.root.dispatchEvent(new t.window.Event('pointerdown',{bubbles:true}));
  for(const key of ['Shift','Control','Alt','Meta']){
    t.window.document.dispatchEvent(new t.window.KeyboardEvent('keydown',{key,bubbles:true}));
    assert.equal(t.root.dataset.orionInput,'pointer');
  }
  t.window.close();
});

test('focus bootstrap can be evaluated twice without throwing and never blurs a dialog',()=>{
  const t=context();
  const heading=t.window.document.getElementById('heading');
  heading.focus();
  assert.equal(t.window.document.activeElement,heading);
  t.window.eval(script);
  assert.equal(t.window.document.activeElement,heading);
  assert.equal(t.root.getAttribute('data-orion-focus-initialized'),'true');
  t.window.close();
});

test('touch-specific styles protect normal input focus and preserve keyboard focus',()=>{
  assert.match(css,/html\[data-orion-input="pointer"\]/);
  assert.match(css,/:not\(input\):not\(textarea\):not\(select\)/);
  assert.match(css,/:focus-visible/);
  assert.doesNotMatch(css,/html\s*\*\s*:\s*focus\s*\{\s*outline\s*:\s*(?:0|none)/);
  assert.match(css,/\.os-agenda-date-picker/);
  assert.match(css,/\.booking-date-control/);
});

test('public routes, admin dialog, invite, booking receipt and landing load the shared policy',()=>{
  const pages=[
    ['app/static/panel.html','panel-assets'],
    ['app/static/orion-public.html','public-assets'],
    ['app/static/orion-appointment.html','appointment-assets'],
    ['app/static/orion-invite.html','panel-assets'],
    ['app/static/qr_connect.html','panel-assets'],
    ['landing/index.html','landing-assets']
  ];
  for(const [path,route] of pages){
    const html=readFileSync(new URL('../'+path,import.meta.url),'utf8');
    for(const extension of ['css','js'])assert.ok(
      html.includes('/'+route+'/orion-focus.'+extension),
      path+' must load shared focus '+extension
    );
  }
});
