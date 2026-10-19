import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM} from 'jsdom';
import {makeSuite,flush,defaultResponse} from './harness.mjs';

const serviceId='f3000000-0000-4000-8000-000000000001';
function responder(path){
  if(path.endsWith('/services'))return [{id:serviceId,name:'Corte',duration_min:30,price_cents:4000,sort_order:0,active:true,color:'#2e7bf6'}];
  if(path.endsWith('/professionals')||path.endsWith('/professional_services'))return [];
  if(path.endsWith('/management/service-packages'))return {items:[]};
  if(path.endsWith('/management/services/'+serviceId+'/addons'))return {version:0,items:[]};
  return defaultResponse(path);
}
async function fixture(t,route='servicos'){
  const h=await makeSuite({initialRoute:route,respond:responder});
  t.after(()=>h.close());await flush();return h;
}
test('Serviços: intervalos e adicionais aparecem como cartões sem triângulos, mantendo campos e funcionamento',async t=>{
  const h=await fixture(t);
  h.click('[data-os-service="'+serviceId+'"]');await flush();
  const details=[...h.document.querySelectorAll('#osDialog details.os-more-fields')];
  assert.equal(details.length,2);
  for(const [index,title,iconName] of [[0,'Intervalos de atendimento',''],[1,'Adicionais','']]){
    const item=details[index],summary=item.querySelector(':scope>summary');
    assert.equal(item.open,false);
    assert.equal(summary.querySelector('.os-disclosure-label').textContent,title);
    assert.equal(summary.querySelectorAll(':scope>svg.os-icon').length,1,'ícone decorativo');
    assert.equal(summary.querySelector('svg.os-icon').getAttribute('aria-hidden'),'true');
    assert.ok(item.querySelector(':scope>.os-disclosure-body'));
    assert.equal(summary.querySelector('button'),null,'summary nativo recebe toque/teclado');
    summary.click();
    assert.equal(item.open,true,title+' abre');
    summary.click();
    assert.equal(item.open,false,title+' fecha');
  }
  assert.equal(details[0].querySelector('[name=prep_min]').type,'number');
  assert.equal(details[0].querySelector('[name=cleanup_min]').type,'number');
  const prep=details[0].querySelector('[name=prep_min]');
  prep.value='15';
  details[0].querySelector('summary').click();
  assert.equal(prep.value,'15','a abertura não recria os campos preenchidos');
  details[0].querySelector('summary').click();
  assert.equal(prep.value,'15','recolher não apaga o conteúdo');
  assert.equal(h.calls.some(call=>call.method!=='GET'),false,'expandir não grava alterações');
  assert.equal(h.errors.length,0);
});

test('Financeiro: pagamento e observações continuam no disclosure acessível',async t=>{
  const h=await fixture(t,'financeiro');
  h.click('[data-os-action=new-expense]');await flush();
  const item=h.document.querySelector('#osDialog details.os-more-fields');
  assert.ok(item);
  assert.match(item.querySelector(':scope>summary').textContent,/Pagamento e observações/);
  assert.ok(item.querySelector('summary svg.os-icon'));
  assert.ok(item.querySelector(':scope>.os-disclosure-body'));
  item.querySelector('summary').click();
  assert.equal(item.open,true);
  assert.ok(item.querySelector('[name=payment_method]'));
  assert.ok(item.querySelector('[name=status]'));
  assert.ok(item.querySelector('[name=notes]'));
  item.querySelector('summary').click();
  assert.equal(item.open,false);
  assert.equal(h.calls.some(call=>call.method!=='GET'),false);
  assert.equal(h.errors.length,0);
});

test('Clientes mantém seu expansor Mais/Menos dados já padronizado',async t=>{
  const h=await fixture(t,'clientes');
  h.click('[data-os-action=new-customer]');await flush();
  const details=h.document.querySelector('details.os-client-more');
  assert.ok(details);
  assert.ok(details.querySelector('.os-client-more-icon'));
  assert.match(details.textContent,/Mais dados/);
  assert.match(details.textContent,/Menos dados/);
  assert.equal(details.open,false);
  details.querySelector('summary').click();
  assert.equal(details.open,true);
  assert.ok(details.querySelector('[name=email]'));
  assert.equal(h.errors.length,0);
});

test('Áreas antigas do painel exibem <details> nativos, inclusive informações da empresa',async ()=>{
  const html=await readFile(new URL('../app/static/panel.html',import.meta.url),'utf8');
  const dom=new JSDOM(html,{runScripts:'outside-only',url:'https://orion.test/panel'});
  try{
    const doc=dom.window.document;
    const labels=['Verificação do destinatário e da entrega','Sobre as conversas e respostas automáticas',
      'Horários e informações para responder dúvidas','Testar com arquivo de áudio','Revisar e exportar avaliações'];
    for(const label of labels){
      const item=[...doc.querySelectorAll('#appView .orion-workspace details.workspace-details')]
        .find(node=>node.querySelector(':scope>summary')?.textContent.includes(label));
      assert.ok(item,label+' recebeu o visual padrão');
      const summary=item.querySelector(':scope>summary');
      assert.ok(summary);
      assert.equal(item.open,false);
      // These legacy pages are inactive in the static DOM and some sit under
      // disabled fields; JSDOM suppresses synthetic clicks there.
      // Active-panel native click toggling is tested in Serviços/Financeiro.
      item.open=true;assert.equal(item.open,true);
      item.open=false;assert.equal(item.open,false);
    }
    assert.ok(doc.querySelector('details.device-menu>summary'),'menu de conexão conserva estilo de dropdown próprio');
  }finally{dom.window.close();}
});

test('Seções de Marketing, IA e solicitações continuam usando details com um chevron único',async ()=>{
  const js=await readFile(new URL('../app/static/orion-suite.js',import.meta.url),'utf8');
  assert.match(js,/<details class="os-automation-rule"><summary><span><strong>/);
  assert.match(js,/<details class="os-site-request" data-site-request=/);
  assert.match(js,/<details class="os-ai-sources"><summary>'\+icon\('info'\)/);
  assert.match(js,/<span class="os-disclosure-label">Dados usados nesta resposta<\/span>/);
  assert.match(js,/<span class="os-disclosure-label">Conferir vales desta quitação<\/span>/);
  assert.match(js,/<div class="os-disclosure-body">/);
  // Existing custom chevrons on cards are kept, not duplicated by ::after.
  const css=await readFile(new URL('../app/static/orion-suite.css',import.meta.url),'utf8');
  assert.match(css,/\.os-automation-rule\[open\]>summary>\.os-icon\{transform:rotate\(180deg\)\}/);
  assert.match(css,/\.os-site-request\[open\](?:>| )summary>\.os-icon\{transform:rotate\(180deg\)\}/);
  assert.doesNotMatch(css,/\.os-automation-rule>summary::after|\.os-site-request>summary::after/);
});

test('CSS padroniza os cards e oculta marcadores nativos sem remover foco ou movimento reduzido',async ()=>{
  const [css,html]=await Promise.all([
    readFile(new URL('../app/static/orion-suite.css',import.meta.url),'utf8'),
    readFile(new URL('../app/static/panel.html',import.meta.url),'utf8'),
  ]);
  for(const selector of ['.os-more-fields',' .os-ai-sources','.os-automation-rule','.os-site-request']){
    assert.ok(css.includes(selector.trim()+'{'),'visual de '+selector);
  }
  assert.match(css,/\.os-more-fields>summary::-webkit-details-marker/);
  assert.match(css,/\.os-more-fields>summary::marker/);
  assert.match(css,/\.os-more-fields>summary::after,\.os-ai-sources>summary::after/);
  assert.match(css,/\.os-more-fields\[open\]>summary::after,\.os-ai-sources\[open\]>summary::after/);
  assert.match(css,/\.os-more-fields>summary:focus-visible,\.os-ai-sources>summary:focus-visible/);
  assert.match(css,/@media\(prefers-reduced-motion:reduce\)/);
  assert.match(css,/\.os-site-request\{box-sizing:border-box;min-width:0;border:1px solid var\(--os-border\);border-radius:14px/);
  assert.match(html,/#appView \.orion-workspace \.workspace-details>summary::-webkit-details-marker/);
  assert.match(html,/#appView \.orion-workspace \.workspace-details>summary::after/);
  assert.match(html,/#appView \.orion-workspace \.workspace-details>summary:focus-visible/);
  assert.match(html,/#appView \.orion-workspace \.workspace-details\[open\]>summary::after/);
  assert.doesNotMatch(css,/\.os-client-more>summary::after/,'expansor de Clientes permanece com seus próprios + e −');
});
