import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {makeSuite,flush,defaultResponse} from './harness.mjs';

const id='a0000000-0000-4000-8000-000000000010';
const sample=()=>[
  {id,name:'Corte Masculino',duration_min:40,price_cents:4000,price_mode:'fixed',
   sort_order:0,active:true,color:'#8b5cf6'}
];
const calls=[];
function responder(path,method='GET',data){
  if(path.endsWith('/services')&&method==='GET')return sample();
  if(path.endsWith('/professionals')||path.endsWith('/professional_services'))return [];
  if(path.endsWith('/management/service-packages'))return {items:[]};
  if(path.includes('/addons'))return {version:0,items:[]};
  if(/\/services\/[a-f0-9-]+$/.test(path)&&method==='PUT'){
    calls.push({method,data});return {...sample()[0],...data};
  }
  return defaultResponse(path);
}
async function editor(t){
  calls.length=0;
  const h=await makeSuite({initialRoute:'servicos',respond:responder});
  t.after(()=>h.close());
  await flush();
  h.click('[data-os-service="'+id+'"]');
  await flush();
  const form=h.document.querySelector('[data-os-form=service]');
  assert.ok(form);
  return {h,form,input:form.elements.price,duration:form.elements.duration_min};
}
function insert(h,input,value){
  const start=input.selectionStart??input.value.length,end=input.selectionEnd??start;
  input.setRangeText(value,start,end,'end');
  input.dispatchEvent(new h.window.InputEvent('input',{bubbles:true,inputType:'insertText',data:value}));
}
function backspace(h,input){
  const end=input.selectionEnd??input.value.length;
  const start=input.selectionStart??end;
  if(start===end&&start>0)input.setRangeText('',start-1,end,'end');
  else input.setRangeText('',start,end,'end');
  input.dispatchEvent(new h.window.InputEvent('input',{bubbles:true,inputType:'deleteContentBackward'}));
}
function emptyPrice(h,input){input.value='';input.dispatchEvent(new h.window.InputEvent('input',{bubbles:true}));input.setSelectionRange(0,0);}

test('atalhos ficam logo depois de Duração, antes do tipo de preço e campo Preço',async t=>{
  const {h,form,duration}=await editor(t);
  const inputBox=duration.closest('.os-field');
  const shortcuts=inputBox.nextElementSibling;
  assert.ok(shortcuts.classList.contains('os-service-duration-shortcuts'));
  assert.equal(shortcuts.getAttribute('role'),'group');
  assert.match(shortcuts.getAttribute('aria-label'),/duração/i);
  assert.deepEqual([...shortcuts.querySelectorAll('button')].map(x=>x.textContent),['15 min','30 min','45 min','60 min','90 min']);
  assert.equal(shortcuts.nextElementSibling.classList.contains('os-service-price-modes'),true);
  assert.equal(shortcuts.querySelector('[data-os-duration=40]'),null);
  assert.ok(form.querySelector('[for=osField-price]').closest('.os-field').previousElementSibling.classList.contains('os-service-price-modes'));
  assert.equal(form.querySelectorAll('[data-os-duration]').length,5);
  assert.equal(h.errors.length,0);
});

test('atalhos definem a duração correta e destacam a seleção, inclusive após digitar manualmente',async t=>{
  const {h,form,duration}=await editor(t);
  for(const n of [15,30,45,60,90]){
    h.click('[data-os-duration="'+n+'"]');
    assert.equal(duration.value,String(n));
    const pressed=[...form.querySelectorAll('[data-os-duration][aria-pressed=true]')];
    assert.equal(pressed.length,1);
    assert.equal(pressed[0].dataset.osDuration,String(n));
  }
  duration.value='40';
  duration.dispatchEvent(new h.window.Event('input',{bubbles:true}));
  assert.equal(form.querySelectorAll('[data-os-duration][aria-pressed=true]').length,0);
  assert.equal(h.errors.length,0);
});

test('preço usa reais e centavos fixos enquanto digita, com separador de milhar',async t=>{
  const {h,input}=await editor(t);
  assert.equal(input.value,'40,00');
  assert.equal(input.getAttribute('inputmode'),'decimal');
  assert.equal(input.dataset.osMoneyMask,'whole');
  emptyPrice(h,input);
  for(const [ch,want,caret] of [
    ['4','4,00',1],
    ['4','44,00',2],
    ['0','440,00',3],
    ['0','4.400,00',5],
  ]){
    insert(h,input,ch);
    assert.equal(input.value,want);
    assert.equal(input.selectionStart,caret);
  }
  emptyPrice(h,input);
  for(const char of '2000')insert(h,input,char);
  assert.equal(input.value,'2.000,00');
  assert.equal(input.selectionStart,5);
  assert.deepEqual(h.errors,[]);
});

test('backspace, apagar tudo, colar valor brasileiro e centavos editáveis não quebram máscara',async t=>{
  const {h,input}=await editor(t);
  input.setSelectionRange(2,2);
  backspace(h,input);
  assert.equal(input.value,'4,00');
  assert.equal(input.selectionStart,1);
  input.setSelectionRange(0,input.value.length);
  backspace(h,input);
  assert.equal(input.value,'');
  insert(h,input,'4');
  assert.equal(input.value,'4,00');
  input.setSelectionRange(2,4);
  insert(h,input,'2');
  insert(h,input,'5');
  assert.equal(input.value,'4,25');

  input.setSelectionRange(0,input.value.length);
  insert(h,input,'R$ 2.000,80');
  assert.equal(input.value,'2.000,80');
  input.setSelectionRange(0,input.value.length);
  insert(h,input,'20.000,00');
  assert.equal(input.value,'20.000,00');

  input.setSelectionRange(2,2);
  insert(h,input,'9');
  assert.equal(input.value,'209.000,00');
  assert.equal(h.errors.length,0);
});

test('a máscara não muda o preço confirmado: salva centavos corretos e mantém preço variável',async t=>{
  const {h,form,input}=await editor(t);
  emptyPrice(h,input);
  for(const ch of '2000')insert(h,input,ch);
  const variable=form.querySelector('[name=price_mode][value=from]');
  variable.checked=true;
  variable.dispatchEvent(new h.window.Event('change',{bubbles:true}));
  assert.match(form.querySelector('[for=osField-price]').textContent,/mínimo/i);
  h.submit('[data-os-form=service]');await flush();
  assert.equal(calls.length,1);
  assert.equal(calls[0].data.price_cents,200000);
  assert.equal(calls[0].data.price_mode,'from');
  assert.equal(calls[0].data.duration_min,40);
  assert.equal(h.errors.length,0);
});

test('UI obedece tokens do Orion e agrupa os botões no mobile',async()=>{
  const css=await readFile(new URL('../app/static/orion-suite.css',import.meta.url),'utf8');
  assert.match(css,/#osDialog \[data-os-form=service\] \.os-service-duration-shortcuts\{/);
  assert.match(css,/flex-wrap:wrap;align-items:center;gap:7px/);
  assert.match(css,/\.os-service-duration-shortcuts>\.os-button\[aria-pressed=true\]/);
  assert.match(css,/#osDialog \[data-os-money-mask=whole\]\{font-variant-numeric:tabular-nums/);
});
