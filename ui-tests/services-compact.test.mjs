import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {makeSuite,flush,defaultResponse} from './harness.mjs';

const ids=[
  'f0000000-0000-4000-8000-000000000001',
  'f0000000-0000-4000-8000-000000000002',
  'f0000000-0000-4000-8000-000000000003',
];
const sample=()=>[
  {id:ids[0],name:'Corte tradicional',duration_min:40,price_cents:4500,sort_order:0,active:true,color:'#8b5cf6',image_url:'https://example.invalid/corte.png'},
  {id:ids[1],name:'Barba',duration_min:25,price_cents:2500,sort_order:1,active:false,color:'#336699',image_url:'javascript:alert(1)'},
  {id:ids[2],name:'Corte + sobrancelha',duration_min:60,price_cents:6500,sort_order:2,active:true,color:'#8b5cf6'},
];
function makeResponder(items,writes=[]){
  return (path,method='GET',payload)=>{
    if(/\/services$/.test(path)&&method==='GET')return items;
    if(/\/services\/[^/]+$/.test(path)&&method==='PUT'){
      const id=decodeURIComponent(path.split('/').at(-1)),item=items.find(x=>x.id===id);
      if(!item)throw Error('Invalid test service');
      writes.push({id,method,payload});Object.assign(item,payload);return {...item};
    }
    if(path.endsWith('/management/service-packages'))return {items:[],total:0};
    if(path.endsWith('/professionals')||path.endsWith('/professional_services'))return [];
    if(/\/management\/services\/.+\/addons$/.test(path))return {version:0,items:[]};
    return defaultResponse(path);
  };
}
async function fixture(t,{items=sample(),writes=[],role='owner'}={}){
  const h=await makeSuite({initialRoute:'servicos',role,respond:makeResponder(items,writes)});
  t.after(()=>h.close());await flush();return {h,items,writes};
}
const cards=h=>[...h.document.querySelectorAll('.os-service-card')];

test('cartões compactos exibem thumbnail circular, dados/status juntos e ações em rodapé próprio',async t=>{
  const {h}=await fixture(t);
  assert.equal(cards(h).length,3);
  const first=cards(h)[0],second=cards(h)[1];
  assert.equal(first.dataset.osServiceCard,ids[0]);
  assert.equal(first.querySelectorAll('.os-service-photo img').length,1);
  assert.equal(first.querySelector('img').getAttribute('alt'),'');
  assert.equal(second.querySelector('img'),null,'URL não HTTPS vira ícone local seguro');
  assert.ok(second.querySelector('.os-service-photo svg.os-icon'));
  assert.equal(first.querySelector('.os-service-info h3').textContent,'Corte tradicional');
  assert.match(first.querySelector('.os-service-meta').textContent,/40 min.*R\$\s*45,00/);
  assert.equal(first.querySelector('.os-service-state .os-badge').textContent.trim(),'Ativo');
  assert.equal(second.querySelector('.os-service-state .os-badge').textContent.trim(),'Inativo');
  assert.equal(first.querySelector('.os-item-commands .os-service-state'),null,'estado não fica solto no rodapé');
  const footer=first.querySelector(':scope > .os-item-commands');
  assert.equal(footer.firstElementChild.dataset.osService,ids[0]);
  assert.equal(footer.querySelectorAll('.os-service-controls input[data-os-service-toggle]').length,1);
  assert.equal(footer.querySelectorAll('.os-service-controls button[data-os-service-delete]').length,1);
  assert.equal(cards(h)[0].querySelector('[data-direction="-1"]').disabled,true);
  assert.equal(cards(h)[2].querySelector('[data-direction="1"]').disabled,true);
  assert.equal(cards(h)[1].querySelector('[data-direction="-1"]').disabled,false);
  assert.equal(h.errors.length,0);
});

test('edição, alternância de ativo e exclusão preservam as permissões e os contratos de dados',async t=>{
  const {h,writes}=await fixture(t);
  h.click('[data-os-service="'+ids[0]+'"]');await flush();
  const dialog=h.document.getElementById('osDialog');
  assert.ok(dialog.open);
  assert.ok(dialog.querySelector('[data-os-form=service]'));
  h.click('[data-os-action=close-dialog]');await flush();
  const toggle=h.document.querySelector('[data-os-service-toggle="'+ids[1]+'"]');
  assert.ok(toggle);
  toggle.checked=true;toggle.dispatchEvent(new h.window.Event('change',{bubbles:true}));await flush();
  assert.equal(writes.length,1);
  assert.equal(writes[0].id,ids[1]);
  assert.equal(writes[0].payload.active,true);
  assert.equal(writes[0].payload.sort_order,1);
  const deleteButton=h.document.querySelector('[data-os-service-delete="'+ids[1]+'"]');
  deleteButton.click();await flush();
  assert.match(h.document.getElementById('osDialog').textContent,/Excluir serviço/);
  assert.equal(writes.length,1,'exclusão aguarda confirmação');
  assert.equal(h.errors.length,0);
});

test('reordenar por setas continua salvando os mesmos sort_order na API',async t=>{
  const {h,writes}=await fixture(t);
  h.click('[data-os-service-order="'+ids[0]+'"][data-direction="1"]');await flush();
  assert.deepEqual(cards(h).map(c=>c.dataset.osServiceCard),[ids[1],ids[0],ids[2]]);
  assert.ok(writes.length>=2);
  assert.ok(writes.every(r=>r.method==='PUT'));
  assert.deepEqual(writes.map(w=>w.payload.sort_order).sort((a,b)=>a-b),[0,1]);
  assert.equal(h.errors.length,0);
});

test('alça de arrastar realmente reordena no touch com Pointer Events',async t=>{
  const {h,writes}=await fixture(t);
  const handle=h.document.querySelector('[data-os-service-drag="'+ids[0]+'"]');
  const last=cards(h).at(-1);
  assert.ok(handle&&!handle.disabled);
  assert.match(handle.getAttribute('aria-label'),/Arrastar Corte tradicional/);
  h.document.elementFromPoint=()=>last.querySelector('.os-service-meta');
  const send=(target,type,x,y)=>{
    const ev=new h.window.MouseEvent(type,{bubbles:true,cancelable:true,clientX:x,clientY:y,button:0});
    Object.defineProperty(ev,'pointerId',{value:3});target.dispatchEvent(ev);
  };
  send(handle,'pointerdown',10,20);
  send(h.document,'pointermove',12,22);
  assert.equal(writes.length,0,'toques sem arraste não alteram dados');
  send(h.document,'pointermove',15,180);
  assert.ok(last.classList.contains('os-service-drop-target'),'destino é indicado visualmente');
  assert.equal(writes.length,0,'não grava até soltar');
  send(h.document,'pointerup',15,180);
  await flush();
  assert.deepEqual(cards(h).map(c=>c.dataset.osServiceCard),[ids[1],ids[2],ids[0]]);
  assert.ok(writes.length>=2,'ordem persistida na mesma API');
  assert.ok(!h.document.querySelector('.os-service-drop-target'),'destaque transitório é limpo');
  assert.equal(h.errors.length,0);
});

test('alça de arrastar tem equivalente de teclado pelas setas e mantém foco destacado',async t=>{
  const {h,writes}=await fixture(t);
  const handle=h.document.querySelector('[data-os-service-drag="'+ids[1]+'"]');
  handle.focus();
  handle.dispatchEvent(new h.window.KeyboardEvent('keydown',{bubbles:true,cancelable:true,key:'ArrowUp'}));
  await flush();
  assert.deepEqual(cards(h).map(c=>c.dataset.osServiceCard),[ids[1],ids[0],ids[2]]);
  assert.ok(writes.length>=2);
  assert.equal(h.errors.length,0);
});

test('leitor enxerga cartões e status mas não consegue alterar serviços',async t=>{
  const {h,writes}=await fixture(t,{role:'viewer'});
  for(const card of cards(h)){
    assert.ok(card.querySelector('.os-service-info h3'));
    assert.ok(card.querySelector('.os-service-state .os-badge'));
    assert.ok(card.querySelector('[data-os-service]'));
    assert.match(card.querySelector('[data-os-service]').textContent,/Detalhes/);
    assert.equal(card.querySelector('[data-os-service-drag]').disabled,true);
    assert.equal(card.querySelector('[data-os-service-toggle]').disabled,true);
    assert.equal(card.querySelector('[data-os-service-delete]').disabled,true);
    assert.equal(card.querySelector('[data-os-service-order]').disabled,true);
  }
  assert.equal(writes.length,0);
});

test('nomes longos são texto seguro e metadados sem overflow seguem proporções reais do CSS',async t=>{
  const items=sample();
  items[0].name='<img src=x onerror=alert(1)> Corte e sobrancelha extraordinariamente completo';
  items[0].price_cents=9876543219876;
  const {h}=await fixture(t,{items});
  const card=cards(h)[0];
  assert.match(card.querySelector('h3').textContent,/<img src=x onerror=alert\(1\)>/);
  assert.equal(card.querySelector('img[src=x]'),null);
  assert.match(card.querySelector('.os-service-meta').textContent,/R\$/);
  const css=await readFile(new URL('../app/static/orion-suite.css',import.meta.url),'utf8');
  for(const rule of [
    '.os-service-card>.os-item-top{display:grid;grid-template-columns:28px 52px minmax(0,1fr)',
    '.os-service-card>.os-item-top>.os-service-photo{box-sizing:border-box;display:grid;place-items:center;flex:0 0 52px;width:52px;height:52px;border-radius:50%',
    '.os-service-card>.os-item-top>.os-service-info{min-width:0;max-width:100%;flex:1 1 auto}',
    '.os-service-card .os-service-info>h3{margin:0;color:var(--os-text);font:600 15px/21px var(--os-display);overflow-wrap:anywhere}',
    '.os-service-card .os-service-meta{margin-top:3px;font:400 12px/18px var(--os-font);color:var(--os-muted);overflow-wrap:anywhere}',
    '.os-service-card .os-service-grip{box-sizing:border-box;display:grid;place-items:center;',
    '.os-service-card>.os-item-commands{display:flex;align-items:center;justify-content:space-between;'
  ])assert.ok(css.includes(rule),'layout compactado: '+rule);
  assert.match(css,/@media\(max-width:359px\)\{[\s\S]*?\.os-service-card>\.os-item-top\{grid-template-columns:26px 46px minmax\(0,1fr\)/);
  assert.match(css,/\.os-service-card\.os-service-drop-target\{outline:2px solid var\(--os-primary\)/);
  assert.match(css,/\.os-service-card \.os-service-grip:focus-visible/);
  assert.equal(h.errors.length,0);
});
