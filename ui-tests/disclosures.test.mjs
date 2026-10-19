import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM} from 'jsdom';
import {makeSuite,flush,defaultResponse} from './harness.mjs';

const sid='b1000000-0000-4000-8000-000000000001';
const sampleService={id:sid,name:'Corte',duration_min:30,price_cents:3500,sort_order:0,active:true,color:'#2e7bf6'};
async function services(t){
  const h=await makeSuite({initialRoute:'servicos',respond:(path,method='GET')=>{
    if(path.endsWith('/services'))return [sampleService];
    if(path.endsWith('/professionals')||path.endsWith('/professional_services'))return [];
    if(path.endsWith('/management/service-packages'))return {items:[]};
    if(path.endsWith('/management/services/'+sid+'/addons'))return {version:0,items:[]};
    return defaultResponse(path);
  }});
  t.after(()=>h.close());await flush();h.click('[data-os-service="'+sid+'"]');await flush();return h;
}

test('Serviços mostra cartões expansíveis e chevrons sem usar a seta preta do navegador',async t=>{
  const h=await services(t);
  const items=[...h.document.querySelectorAll('#osDialog details.os-more-fields')];
  assert.equal(items.length,2);
  assert.deepEqual(items.map(d=>d.querySelector(':scope > summary').textContent.trim()),[
    'Intervalos de atendimento','Adicionais'
  ]);
  assert.equal(items[0].querySelector('summary .os-disclosure-heading>.os-icon')?.getAttribute('aria-hidden'),'true');
  assert.equal(items[1].querySelector('summary .os-disclosure-heading>.os-icon')?.getAttribute('aria-hidden'),'true');
  for(const detail of items){
    const summary=detail.querySelector(':scope > summary');
    assert.ok(summary);
    assert.equal(detail.open,false);
    summary.click();assert.equal(detail.open,true,'clique expande o details nativo');
    summary.click();assert.equal(detail.open,false,'outro clique recolhe sem perder conteúdo');
  }
  items[0].querySelector('summary').click();
  const input=items[0].querySelector('input[name=prep_min]');
  assert.ok(input);input.value='15';
  items[0].querySelector('summary').click();
  items[0].querySelector('summary').click();
  assert.equal(items[0].querySelector('input[name=prep_min]').value,'15','não apaga dados ao recolher');
  assert.equal(h.calls.some(c=>c.method!=='GET'),false,'abrir e fechar não grava serviços');
  assert.equal(h.errors.length,0);
});

test('Os expansores nativos do Financeiro, IA e Marketing recebem o novo padrão sem alterar os fluxos',async()=>{
  const src=await readFile(new URL('../app/static/orion-suite.js',import.meta.url),'utf8');
  for(const [name,icon] of [
    ['Intervalos de atendimento','clock'],['Adicionais','sparkles'],
    ['Pagamento e observações','credit'],['Conferir vales desta quitação','wallet'],
    ['Dados usados nesta resposta','info']
  ]){
    assert.ok(src.includes("<summary><span class=\"os-disclosure-heading\">'+icon('"+icon+"')+'"+name+"</span></summary>"),
      'título '+name+' usa ícone existente do Orion');
  }
  assert.match(src,/<details class="os-automation-rule"><summary>/,
    'Marketing mantém estrutura details existente');
  assert.match(src,/class="os-site-request" data-site-request=/,
    'Solicitações mantêm visualização própria com metadados');
  assert.match(src,/<details class="os-client-more"/,
    'Clientes mantém o seu próprio seletor Mais/Menos dados');
});

test('Os painéis antigos do WhatsApp e Configurações ganham o mesmo acabamento e continuam nativos',async()=>{
  const html=await readFile(new URL('../app/static/panel.html',import.meta.url),'utf8');
  const dom=new JSDOM(html,{url:'https://orion.test/panel'});
  try{
    const doc=dom.window.document;
    const blocks=[...doc.querySelectorAll('#appView .orion-workspace details.workspace-details')];
    assert.ok(blocks.length>=5,'WhatsApp, equipe e testes de IA têm expansores próprios');
    const hours=blocks.find(d=>d.querySelector(':scope > summary')?.textContent.includes('Horários e informações'));
    assert.ok(hours,'o bloco antigo sem classe foi padronizado');
    for(const d of blocks){
      const summary=d.querySelector(':scope > summary');
      assert.ok(summary&&summary.textContent.trim(),'sumário mantém título legível');
      assert.equal(d.open,false);
      summary.click();assert.equal(d.open,true);
      summary.click();assert.equal(d.open,false);
    }
    assert.ok(doc.getElementById('deviceOptions').classList.contains('device-menu'),
      'menu próprio do dispositivo não foi refeito');
  }finally{dom.window.close();}
});

test('CSS oculta apenas marcadores nativos das seções antigas e preserva foco e mobilidade reduzida',async()=>{
  const suite=await readFile(new URL('../app/static/orion-suite.css',import.meta.url),'utf8');
  const panel=await readFile(new URL('../app/static/panel.html',import.meta.url),'utf8');
  for(const val of [
    'details.os-more-fields>summary::-webkit-details-marker',
    'details.os-more-fields>summary::marker',
    'details.os-more-fields>summary::after',
    'details.os-more-fields[open]>summary::after',
    'details.os-ai-sources>summary::after',
    'details.os-more-fields>summary:focus-visible',
    'details.os-ai-sources>summary:focus-visible',
    'details.os-automation-rule>summary::-webkit-details-marker',
    'details.os-automation-rule>summary:focus-visible',
    'details.os-automation-rule>.os-automation-rule-body{padding:14px 15px 16px}',
    '@media(prefers-reduced-motion:reduce)',
    '@media(max-width:767px)'
  ])assert.ok(suite.includes(val),val);
  for(const val of [
    'details.workspace-details>summary::-webkit-details-marker',
    'details.workspace-details>summary::marker',
    'details.workspace-details>summary::after',
    'details.workspace-details[open]>summary::after',
    'details.workspace-details>summary:focus-visible',
    'details.workspace-details[open]>summary',
    '@media(prefers-reduced-motion:reduce)'
  ])assert.ok(panel.includes(val),val);
  assert.doesNotMatch(suite,/summary\s*\{\s*outline\s*:\s*none/i,
    'nenhuma remoção global do indicador de teclado');
  assert.match(suite,/#osDialog\.os-customer-dialog \.os-client-more-toggle/,
    'estilo exclusivo de clientes permanece');
});
