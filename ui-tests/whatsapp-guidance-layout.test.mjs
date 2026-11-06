import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {makeSuite,planState,tenantA,flush} from './harness.mjs';

const guides=[
  ['aquecimento','warmup','Aquecimento','Guia de uso manual',['dispositivos']],
  ['evitar-banimento','avoid-ban','Evitar Banimento','Orientações de uso',
    ['dispositivos','aquecimento','diagnosticos']],
  ['resolver-banimento','resolve-ban','Resolver Banimento','Revisão e reconexão',['dispositivos']],
];
const features=['account.manage','whatsapp_guidance.view','devices.manage','diagnostics.manage'];
const permissions=planState('elite',{
  workspace_profile:'subscription_services',revision:'guidance-layout-20261106',
  features,permissions:Object.fromEntries(features.map(feature=>[feature,true])),
});
const business={id:tenantA,name:'Empresa sintética',slug:'empresa-sintetica',
  role:'owner',status:'active',workspace_profile:'subscription_services',
  timezone:'America/Sao_Paulo'};

for(const [route,slug,title,meta,actions] of guides){
  test(route+' mantém conteúdo, aviso alinhável e ações funcionais',async t=>{
    const h=await makeSuite({initialRoute:route,permissions,
      bridge:{tenants:[business]}});
    t.after(()=>h.close());await flush();
    const article=h.document.querySelector('article.os-help-article[data-os-guidance="'+slug+'"]');
    assert.ok(article,'artigo do guia não encontrado');
    assert.equal(article.querySelector('h1').textContent,title);
    const back=article.querySelector(':scope > button[data-os-route="ajuda"]');
    assert.ok(back,'atalho de retorno precisa continuar disponível');
    const metaRow=article.querySelector('header .os-help-article-meta');
    assert.ok(metaRow);
    assert.equal(metaRow.querySelectorAll(':scope > span').length,2,
      'os metadados não podem quebrar ícone e texto em itens separados');
    assert.equal(metaRow.querySelector('.os-guidance-meta-summary > span').textContent,meta);
    assert.equal(metaRow.querySelector('.os-guidance-meta-scope').textContent,
      'Serviços de assinaturas');
    assert.ok(metaRow.querySelector('.os-guidance-meta-summary > svg.os-icon'));
    assert.ok(article.querySelector('.os-help-prose > p')?.textContent.trim());
    const notices=[...article.querySelectorAll('.os-help-prose .os-notice')];
    assert.ok(notices.length>=1,'guia precisa preservar avisos');
    for(const box of notices){
      assert.equal(box.querySelectorAll(':scope > svg.os-icon').length,1);
      assert.equal(box.querySelectorAll(':scope > p').length,1);
      assert.ok(box.querySelector(':scope > p').textContent.trim());
    }
    const routeButtons=[...article.querySelectorAll('.os-form-actions > button[data-os-route]')]
      .map(button=>button.dataset.osRoute);
    assert.deepEqual(routeButtons,actions,'ações preservadas com as mesmas rotas');
    for(const link of article.querySelectorAll('a[target="_blank"]')){
      assert.match(link.rel,/noopener/);
      assert.match(link.rel,/noreferrer/);
    }
    assert.deepEqual(h.errors,[]);
  });
}

test('aquecimento mantém tabela compacta com legenda e aviso acessível',async t=>{
  const h=await makeSuite({initialRoute:'aquecimento',permissions,bridge:{tenants:[business]}});
  t.after(()=>h.close());await flush();
  const table=h.document.querySelector('[data-os-guidance="warmup"] [data-os-warmup-example]');
  assert.ok(table);
  assert.equal(table.querySelectorAll('tbody tr').length,5);
  assert.equal(table.getAttribute('aria-describedby'),'osWarmupExampleNote');
  assert.ok(h.document.getElementById('osWarmupExampleNote'));
  assert.deepEqual(h.errors,[]);
});

test('regras visuais são exclusivas dos três guias e incluem telas pequenas e Safari',async()=>{
  const css=await readFile(new URL('../app/static/orion-suite.css',import.meta.url),'utf8');
  const slice=css.split('/* Scoped WhatsApp guidance layout: Aquecimento / Evitar / Resolver Banimento. */');
  assert.equal(slice.length,2,'as regras dos guias devem ter escopo próprio');
  const rules=slice[1];
  assert.match(rules,/data-os-current-route=aquecimento/);
  assert.match(rules,/data-os-current-route=evitar-banimento/);
  assert.match(rules,/data-os-current-route=resolver-banimento/);
  assert.match(rules,/grid-template-columns:22px minmax\(0,1fr\)/);
  assert.match(rules,/\.os-notice>p\s*\{/);
  assert.match(rules,/min-height:44px/);
  assert.match(rules,/@media\(max-width:767px\)/);
  assert.match(rules,/@media\(max-width:359px\)/);
  assert.match(rules,/safe-area-inset-bottom/);
  assert.match(rules,/\.os-table caption/);
});
