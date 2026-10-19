import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM} from 'jsdom';
import {makeSuite,flush,defaultResponse} from './harness.mjs';

const serviceId='f0000000-0000-4000-8000-000000000010';
const service={id:serviceId,name:'Corte',duration_min:40,price_cents:3500,
  prep_min:5,cleanup_min:10,active:true,color:'#8b5cf6',sort_order:0};
const secondService={id:'f0000000-0000-4000-8000-000000000011',
  name:'Barba',duration_min:20,price_cents:2000,active:true,sort_order:1};
function responder(path,method='GET',payload){
  if(path.endsWith('/services')&&method==='GET')return [service,secondService];
  if(path.endsWith('/professionals'))return [];
  if(path.endsWith('/professional_services'))return [];
  if(path.endsWith('/management/service-packages'))return {items:[]};
  if(path.includes('/management/services/'+serviceId+'/addons'))
    return {version:0,items:[]};
  if(path.endsWith('/services/'+serviceId)&&method==='PUT')return {...service,...payload};
  return defaultResponse(path);
}
async function serviceDialog(t){
  const h=await makeSuite({initialRoute:'servicos',respond:responder});
  t.after(()=>h.close());await flush();
  h.click('[data-os-service="'+serviceId+'"]');await flush();
  return h;
}

test('Serviços: Intervalos e Adicionais usam detalhes nativos, sem seta automática ou JS extra',async t=>{
  const h=await serviceDialog(t);
  const dialog=h.document.getElementById('osDialog'),
        sections=[...dialog.querySelectorAll('details.os-more-fields')];
  assert.equal(sections.length,2);
  assert.ok(sections[0].classList.contains('os-disclosure-clock'));
  assert.ok(sections[1].classList.contains('os-disclosure-addons'));
  assert.deepEqual(sections.map(d=>d.querySelector(':scope > summary').textContent),
    ['Intervalos de atendimento','Adicionais']);
  for(const section of sections){
    const summary=section.querySelector(':scope > summary');
    assert.equal(summary.tagName,'SUMMARY');
    assert.equal(summary.parentElement,section);
    assert.equal(summary.querySelectorAll('button').length,0,'sumário mantém ação nativa única');
    assert.equal(section.open,false);
    summary.click();assert.equal(section.open,true);
    summary.click();assert.equal(section.open,false);
  }
  assert.equal(dialog.querySelector('[name=prep_min]').value,'5');
  assert.equal(dialog.querySelector('[name=cleanup_min]').value,'10');
  assert.ok(dialog.querySelector('[name=addon_ids][value="'+secondService.id+'"]'));
  assert.equal(dialog.querySelector('[data-os-form=service]').elements.name.value,'Corte');
  assert.equal(h.calls.filter(c=>c.method!=='GET').length,0,'abrir e fechar não faz gravações');
  assert.equal(h.errors.length,0);
});

test('Fechar e reabrir adicionais mantém checkbox e valor dos intervalos',async t=>{
  const h=await serviceDialog(t),dialog=h.document.getElementById('osDialog');
  const [intervals,addons]=dialog.querySelectorAll('details.os-more-fields');
  intervals.open=true;
  const prepare=dialog.querySelector('[name=prep_min]');
  prepare.value='12';prepare.dispatchEvent(new h.window.Event('input',{bubbles:true}));
  intervals.open=false;
  addons.querySelector('summary').click();
  const checkbox=addons.querySelector('[name=addon_ids][value="'+secondService.id+'"]');
  checkbox.checked=true;checkbox.dispatchEvent(new h.window.Event('change',{bubbles:true}));
  addons.querySelector('summary').click();
  assert.equal(prepare.value,'12');
  assert.equal(checkbox.checked,true);
  assert.equal(addons.open,false);
  addons.querySelector('summary').click();
  assert.equal(checkbox.checked,true,'dados permanecem no DOM entre alternâncias');
  assert.equal(h.errors.length,0);
});

test('Financeiro: pagamento e vales conservam a estrutura nativa em HTML',async ()=>{
  const js=await readFile(new URL('../app/static/orion-suite.js',import.meta.url),'utf8');
  assert.match(js,/<details class="os-more-fields os-disclosure-payment"[^>]*><summary>Pagamento e observações<\/summary>/);
  assert.match(js,/<details class="os-more-fields os-disclosure-payment"><summary>Conferir vales desta quitação<\/summary>/);
  assert.match(js,/<details class="os-ai-sources"><summary>Dados usados nesta resposta<\/summary>/);
  assert.match(js,/<details class="os-automation-rule"><summary>/);
  assert.match(js,/<details class="os-client-more"/,'o controle Mais/Menos dados continua independente');
});

test('WhatsApp, Empresa e IA usam o mesmo cartão expansível, sem alterar o menu do dispositivo',async ()=>{
  const panel=await readFile(new URL('../app/static/panel.html',import.meta.url),'utf8');
  const dom=new JSDOM(panel,{runScripts:'outside-only'});
  try{
    const document=dom.window.document,details=[...document.querySelectorAll('details.workspace-details')];
    assert.equal(details.length,5);
    assert.ok(document.querySelector('#chatDeliveryDetails.workspace-details'));
    assert.ok(document.querySelector('.workspace-details.business-info-details'));
    assert.ok(document.querySelector('#page-ai .workspace-details')||document.querySelector('[id^=page-] .workspace-details'));
    for(const d of details){
      const s=d.querySelector(':scope > summary');
      assert.ok(s&&s.textContent.trim(),'cada seção tem título');
      assert.equal(d.open,false);
      s.click();assert.equal(d.open,true,'expansão nativa');
      s.click();assert.equal(d.open,false,'recolhimento nativo');
    }
    const menu=document.querySelector('details.device-menu');
    assert.ok(menu);
    assert.equal(menu.classList.contains('workspace-details'),false,'menu do dispositivo preservado');
    assert.equal(menu.querySelector('summary').textContent.trim(),'Opções');
    assert.ok(document.querySelector('#page-conversas .workspace-details'), 'ajuda de conversas intacta');
  }finally{dom.window.close()}
});

test('CSS suprime triângulos nativos, usa chevrons rotativos e preserva foco por teclado',async ()=>{
  const css=await readFile(new URL('../app/static/orion-suite.css',import.meta.url),'utf8');
  const panels=['.os-more-fields','.os-ai-sources','.os-automation-rule',
    '#appView .orion-workspace .workspace-details'];
  for(const selector of panels){
    assert.ok(css.includes(selector+'>summary::-webkit-details-marker'),'remove seta nativa WebKit: '+selector);
    assert.ok(css.includes(selector+'>summary::marker'),'remove marcador Firefox: '+selector);
    assert.ok(css.includes(selector+'>summary:focus-visible'),'preserva acessibilidade: '+selector);
    assert.ok(css.includes(selector+'>summary::before'),'ícone de contexto alinhado: '+selector);
    assert.ok(css.includes(selector+'[open]>summary'),'realça expansão: '+selector);
  }
  for(const selector of ['.os-more-fields','.os-ai-sources','#appView .orion-workspace .workspace-details']){
    assert.ok(css.includes(selector+'>summary::after'),'chevron discreto: '+selector);
    assert.ok(css.includes(selector+'[open]>summary::after'),'rotação do chevron: '+selector);
  }
  assert.match(css,/border-right:2px solid var\(--os-muted\)/);
  assert.match(css,/transform:rotate\(225deg\)/);
  assert.match(css,/min-height:52px/);
  assert.match(css,/@media\(max-width:639px\)/);
  assert.match(css,/@media\(prefers-reduced-motion:reduce\)/);
  assert.match(css,/--os-disclosure-icon:url\("data:image\/svg\+xml/);
  assert.match(css,/\.os-site-request-list\{border-top:0;display:grid;gap:10px\}/);
  assert.ok(!css.includes('.os-client-more>summary::after'),'Mais/Menos dados não recebe chevron duplicado');
  assert.ok(!css.includes('.device-menu>summary::after'),'menu do WhatsApp não é afetado');
});
