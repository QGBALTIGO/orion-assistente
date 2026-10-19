import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM} from 'jsdom';
import {makeSuite,flush,defaultResponse} from './harness.mjs';

const serviceId='ff000000-0000-4000-8000-000000000071';
const service={id:serviceId,name:'Atendimento sintético',description:'',duration_min:40,
  price_cents:3000,color:'#2e7bf6',prep_min:5,cleanup_min:10,sort_order:0,active:true};
function response(path,method='GET'){
  if(path.endsWith('/services'))return [service];
  if(path.endsWith('/professionals')||path.endsWith('/professional_services'))return [];
  if(path.includes('/management/service-packages'))return {items:[]};
  if(path.includes('/management/services/'+serviceId+'/addons'))return {items:[],version:0};
  return defaultResponse(path,method);
}
async function serviceForm(t,role='owner'){
  const h=await makeSuite({initialRoute:'servicos',role,respond:response});
  t.after(()=>h.close());await flush();
  h.click('[data-os-service="'+serviceId+'"]');await flush();
  assert.equal(h.document.getElementById('osDialog').open,true);
  return h;
}

test('Serviços apresenta Intervalos e Adicionais como dois disclosures nativos clicáveis',async t=>{
  const h=await serviceForm(t);
  const details=[...h.document.querySelectorAll('#osDialog details.os-more-fields')];
  assert.equal(details.length,2);
  assert.equal(details[0].querySelector(':scope > summary').textContent,'Intervalos de atendimento');
  assert.equal(details[1].querySelector(':scope > summary').textContent,'Adicionais');
  assert.equal(details[0].open,false);
  assert.equal(details[1].open,false);
  assert.equal(details[0].querySelector('button.os-disclosure-trigger'),null,'não cria botões JS concorrentes com summary');
  const summaries=details.map(d=>d.querySelector(':scope > summary'));
  summaries[0].click();await flush();
  assert.equal(details[0].open,true,'abre ao tocar na linha toda');
  assert.equal(details[1].open,false,'um disclosure não muda o estado do outro');
  const prep=details[0].querySelector('[name=prep_min]');
  assert.ok(prep);
  prep.value='17';prep.dispatchEvent(new h.window.Event('input',{bubbles:true}));
  summaries[0].click();await flush();
  assert.equal(details[0].open,false,'fecha com um segundo toque');
  summaries[0].click();await flush();
  assert.equal(prep.value,'17','os dados digitados permanecem no fechamento');
  summaries[1].click();await flush();
  assert.equal(details[1].open,true,'Adicionais expande independentemente');
  assert.equal(h.calls.filter(c=>c.method!=='GET').length,0,'só expandir não chama API de escrita');
  assert.equal(h.errors.length,0);
});

test('expansores permanecem acessíveis ao teclado e ao usuário de leitura',async t=>{
  const h=await serviceForm(t,'viewer');
  const summary=h.document.querySelector('#osDialog details.os-more-fields > summary');
  assert.ok(summary);
  assert.equal(summary.tagName,'SUMMARY');
  assert.equal(summary.parentElement.tagName,'DETAILS');
  assert.equal(summary.getAttribute('role'),null,'preserva semântica nativa');
  summary.focus();
  assert.equal(h.document.activeElement,summary);
  summary.click();
  assert.equal(summary.parentElement.open,true,'mesmo sem permissão de editar, pode consultar');
  assert.equal(h.document.querySelector('#osField-prep_min').matches(':disabled'),true);
  assert.equal(h.errors.length,0);
});

test('workspace: os cinco disclosures de WhatsApp, empresa e simulador têm o mesmo card sem alterar menu do dispositivo',async ()=>{
  const markup=await readFile(new URL('../app/static/panel.html',import.meta.url),'utf8');
  const dom=new JSDOM(markup,{url:'https://orion.test/panel',runScripts:'outside-only'});
  try{
    const d=dom.window.document;
    const details=[...d.querySelectorAll('#appView .orion-workspace details.workspace-details')];
    assert.equal(details.length,5,'seções antigas mapeadas');
    const names=details.map(node=>node.querySelector('summary')?.textContent.trim());
    for(const name of [
      'Verificação do destinatário e da entrega',
      'Sobre as conversas e respostas automáticas',
      'Horários e informações para responder dúvidas',
      'Testar com arquivo de áudio',
      'Revisar e exportar avaliações'
    ])assert.ok(names.includes(name),name);
    const business=details.find(node=>node.querySelector('summary').textContent.includes('Horários e informações'));
    assert.ok(business);
    assert.equal(business.open,false);
    business.querySelector('summary').click();
    assert.equal(business.open,true);
    business.querySelector('summary').click();
    assert.equal(business.open,false);
    const device=d.getElementById('deviceOptions');
    assert.ok(device.classList.contains('device-menu'));
    assert.equal(device.classList.contains('workspace-details'),false,
      'menu de opções do dispositivo não deve se tornar accordion');
  }finally{dom.window.close();}
});

test('CSS compartilha superfícies, chevron discreto, marcador oculto e foco acessível',async ()=>{
  const css=await readFile(new URL('../app/static/orion-suite.css',import.meta.url),'utf8');
  for(const selector of [
    '.os-more-fields>summary::-webkit-details-marker',
    '.os-more-fields>summary::marker',
    '.os-ai-sources>summary::-webkit-details-marker',
    '.os-automation-rule>summary::-webkit-details-marker',
    '.os-more-fields>summary::after,.os-ai-sources>summary::after',
    '.os-more-fields[open]>summary::after,.os-ai-sources[open]>summary::after',
    '.os-more-fields>summary:focus-visible,',
    '.os-automation-rule>summary:focus-visible',
    '#appView .orion-workspace details.workspace-details>summary::-webkit-details-marker',
    '#appView .orion-workspace details.workspace-details>summary::after',
    '#appView .orion-workspace details.workspace-details[open]>summary::after',
    '#appView .orion-workspace details.workspace-details>summary:focus-visible',
    '@media(prefers-reduced-motion:reduce)'
  ])assert.ok(css.includes(selector),'contrato de disclosure: '+selector);
  assert.match(css,/\.os-more-fields>summary,\s*\.os-ai-sources>summary,\s*\.os-automation-rule>summary\{[^}]*min-height:52px/);
  assert.match(css,/\.os-more-fields>summary::after,\.os-ai-sources>summary::after\{[^}]*border-right:1\.8px solid var\(--os-muted\)/);
  assert.match(css,/\.os-more-fields\[open\]>summary::after,\.os-ai-sources\[open\]>summary::after\{[^}]*rotate\(225deg\)/);
  assert.match(css,/\.os-site-request\{box-sizing:border-box;border:1px solid var\(--os-border\)/);
  assert.match(css,/\.os-site-request>summary::marker\{content:""\}/);
  assert.match(css,/#osDialog\.os-customer-dialog \.os-client-more-toggle::marker\{content:""\}/,
    'mais/menos dados já padronizado dos clientes não é modificado');
  assert.doesNotMatch(css,/\.device-menu\s*>\s*summary::after/,
    'não confundir menu de opções com disclosure');
});

test('IA, quitações e automações mantêm detalhes nativos sem modificar conteúdo ou ações',async ()=>{
  const source=await readFile(new URL('../app/static/orion-suite.js',import.meta.url),'utf8');
  for(const raw of [
    '<details class="os-more-fields"><summary>Conferir vales desta quitação</summary>',
    '<details class="os-ai-sources"><summary>Dados usados nesta resposta</summary>',
    '<details class="os-more-fields"><summary>Intervalos de atendimento</summary>',
    '<details class="os-more-fields"><summary>Adicionais</summary>',
    '<summary>Pagamento e observações</summary>'
  ])assert.ok(source.includes(raw),'nativo preservado: '+raw);
  assert.match(source,/<details class="os-automation-rule"><summary><span><strong>/);
  assert.match(source,/icon\('down'\)\+'<\/summary><div class="os-automation-rule-body"/);
  assert.match(source,/<details class="os-site-request" data-site-request=/);
  assert.match(source,/<details class="os-client-more"/);
  assert.doesNotMatch(source,/addEventListener\('click',.*os-disclosure-trigger/,
    'nenhum listener desnecessário introduzido');
});
