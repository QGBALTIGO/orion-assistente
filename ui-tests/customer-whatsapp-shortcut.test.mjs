import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {makeSuite,flush,defaultResponse,customerId} from './harness.mjs';

const registered='2026-10-17T13:12:00Z';
const record=(overrides={})=>({
  id:customerId,name:'Pessoa sintética',phone:'+5521997557364',
  created_at:registered,active:true,version:2,
  total_appointments:0,total_spent_cents:0,...overrides
});
function responder(customers){
  return (path,method='GET')=>{
    if(path.includes('/management/customers')&&method==='GET')
      return {items:customers,total:customers.length};
    return defaultResponse(path);
  };
}
async function fixture(t,customers,role='owner'){
  const h=await makeSuite({initialRoute:'clientes',respond:responder(customers),role});
  t.after(()=>h.close());
  await flush();
  return h;
}
const card=(h,index=0)=>[...h.document.querySelectorAll('.os-mobile-customers .os-swipe-row')][index];

test('mobile: nome e Desde, sem telefone visível; WhatsApp ao lado da seta',async t=>{
  const h=await fixture(t,[record()]);
  const row=card(h),front=row.querySelector('.os-client-mobile-front');
  assert.ok(front,'a face do card permanece um item único para o gesto lateral');
  const button=front.querySelector('button[data-os-customer]');
  assert.ok(button);
  assert.equal(button.querySelector('strong').textContent,'Pessoa sintética');
  assert.match(button.textContent,/Desde 17\/10\/2026/);
  assert.doesNotMatch(button.textContent,/21997557364|\+55|Sem WhatsApp/);
  const wa=front.querySelector('a.os-client-mobile-whatsapp');
  assert.ok(wa,'atalho separado do botão dos detalhes');
  assert.equal(wa.getAttribute('href'),'https://wa.me/5521997557364');
  assert.equal(wa.getAttribute('target'),'_blank');
  assert.match(wa.getAttribute('rel'),/noopener/);
  assert.match(wa.getAttribute('aria-label'),/Abrir WhatsApp de Pessoa sintética/);
  assert.ok(wa.querySelector('svg.os-whatsapp-mark'));
  assert.ok(wa.previousElementSibling===button);
  assert.ok(button.lastElementChild.matches('svg.os-icon'),'seta permanece no card');
  assert.ok(row.querySelector('.os-swipe-actions'),'ações de deslizar permanecem');
  assert.equal(row.querySelector('a button'),null,'não há controles aninhados');
  assert.deepEqual(h.errors,[]);
});

test('mobile: ícone abre apenas WhatsApp e o card/seta continua abrindo o cadastro',async t=>{
  const h=await fixture(t,[record()]);
  const row=card(h),wa=row.querySelector('.os-client-mobile-whatsapp');
  let external=0;
  wa.addEventListener('click',event=>{event.preventDefault();external++;});
  wa.click();await flush();
  assert.equal(external,1);
  assert.equal(h.document.getElementById('osDialog').open,false);
  assert.equal(h.calls.filter(x=>x.method!=='GET').length,0);
  row.querySelector('[data-os-customer]').click();
  await flush();
  assert.equal(h.document.getElementById('osDialog').open,true);
  assert.ok(h.document.querySelector('[data-os-form=customer] [name=phone]'));
  assert.equal(h.document.querySelector('[data-os-form=customer] [name=phone]').value,'+5521997557364');
  assert.deepEqual(h.errors,[]);
});

test('mobile: só cria link seguro para número brasileiro válido, mesmo se vier formatado',async t=>{
  const customers=[
    record({id:'cliente-formatado',phone:'(11) 91234-5678'}),
    record({id:'cliente-sem-numero',name:'Sem número',phone:null}),
    record({id:'cliente-invalido',name:'Inválido',phone:'javascript:alert(1)'}),
    record({id:'cliente-curto',name:'Curto',phone:'+5511'}),
    record({id:'cliente-estrangeiro',name:'Estrangeiro',phone:'+12025550123'}),
  ];
  const h=await fixture(t,customers);
  const rows=[...h.document.querySelectorAll('.os-mobile-customers .os-swipe-row')];
  assert.equal(rows.length,customers.length);
  assert.equal(rows[0].querySelector('a.os-client-mobile-whatsapp').href,'https://wa.me/5511912345678');
  for(const row of rows.slice(1)){
    assert.equal(row.querySelector('a.os-client-mobile-whatsapp'),null,'número ausente ou inválido não cria link');
    assert.ok(row.querySelector('button[data-os-customer]'));
  }
  assert.deepEqual(h.errors,[]);
});

test('mobile: nome não informado não revela telefone como título do cartão',async t=>{
  const h=await fixture(t,[record({name:'',display_name:null,phone:'+5521997557364'})]);
  const row=card(h);
  assert.equal(row.querySelector('.os-client-mobile-main strong').textContent,'Cliente sem nome');
  assert.doesNotMatch(row.querySelector('.os-client-mobile-main').textContent,/5521997557364/);
  assert.match(row.querySelector('a.os-client-mobile-whatsapp').getAttribute('aria-label'),/Cliente sem nome/);
});

test('mobile: nomes potencialmente maliciosos são escapados, mesmo no aria-label',async t=>{
  const evil='<img src=x onerror=alert(1)>';
  const h=await fixture(t,[record({name:evil})]);
  const row=card(h);
  assert.equal(row.querySelector('img'),null);
  assert.equal(row.querySelector('svg[onload]'),null);
  assert.equal(row.querySelector('.os-client-mobile-main strong').textContent,evil);
  assert.equal(row.querySelector('a').getAttribute('aria-label'),'Abrir WhatsApp de '+evil);
  assert.deepEqual(h.errors,[]);
});

test('leitor também consegue consultar e abrir WhatsApp sem permissão de edição',async t=>{
  const h=await fixture(t,[record()],'viewer');
  const row=card(h);
  assert.ok(row.querySelector('a.os-client-mobile-whatsapp'));
  row.querySelector('button[data-os-customer]').click();await flush();
  const form=h.document.querySelector('[data-os-form=customer]');
  assert.ok(form);
  assert.equal(form.querySelector('fieldset').disabled,true);
  assert.deepEqual(h.calls.filter(x=>x.method!=='GET'),[]);
});

test('toque e layout acessíveis em celular estreito, sem quebrar swipe nem cores do Orion',async()=>{
  const css=await readFile(new URL('../app/static/orion-suite.css',import.meta.url),'utf8');
  assert.match(css,/\.os-client-mobile-front\{[^}]*scroll-snap-align:start/);
  assert.match(css,/\.os-client-mobile-front>\.os-client-mobile-whatsapp\{[^}]*width:44px;height:44px/);
  assert.match(css,/\.os-client-mobile-front>\.os-client-mobile-whatsapp\{[^}]*border:0;border-radius:0;background:transparent/);
  assert.match(css,/\.os-client-mobile-whatsapp>\.os-whatsapp-mark\{[^}]*width:19px;height:19px/);
  assert.doesNotMatch(css,/\.os-client-mobile-whatsapp:hover\{background:/);
  assert.match(css,/\.os-client-mobile-whatsapp:focus-visible\{[^}]*outline:/);
  assert.match(css,/@media\(max-width:359px\)/);
  assert.match(css,/html\[data-theme=dark\] \.os-client-mobile-whatsapp/);
});
